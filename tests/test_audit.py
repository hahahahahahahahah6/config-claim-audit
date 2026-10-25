import contextlib
import io
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from config_claim_audit.audit import audit
from config_claim_audit.cli import main


class AuditTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)

    def project(self, docs='', code='', extra=None):
        files = {'README.md': docs, 'app.py': code}
        files.update(extra or {})
        for name, content in files.items():
            path = self.root / name
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(content, encoding='utf-8')
        return self.root

    def result(self, docs='', code='', extra=None, **kwargs):
        return audit(self.project(docs, code, extra), **kwargs)

    def rule(self, result, expected):
        self.assertEqual([f['rule'] for f in result['findings']], [expected])

    def cli(self, *args):
        out, err = io.StringIO(), io.StringIO()
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            try:
                status = main([str(self.root), *args])
            except SystemExit as exc:
                status = exc.code
        return status, out.getvalue(), err.getvalue()

    def test_good(self):
        r = self.result('TIMEOUT defaults to 30\n--port defaults to 8080', 'import os\na=os.getenv("TIMEOUT", "30")\np.add_argument("--port", default=8080)')
        self.assertEqual(r['status'], 'PASS')
        self.assertEqual({x['kind'] for x in r['verified_claims']}, {'env', 'flag'})

    def test_env_mismatch(self):
        r = self.result('```sh\nTIMEOUT=30\n```', 'os.getenv("TIMEOUT", "60")')
        self.rule(r, 'documented-env-default-mismatch')
        self.assertEqual(r['findings'][0]['severity'], 'HIGH')
        self.assertEqual(r['findings'][0]['line'], 2)

    def test_flag_mismatch(self):
        self.rule(self.result('--port defaults to 8080', 'p.add_argument("--port", default=8000)'), 'documented-flag-default-mismatch')

    def test_unknown(self):
        r = self.result('`MISSING_VAR=1`')
        self.rule(r, 'documented-env-unknown')
        self.assertEqual(r['status'], 'FAIL')

    def test_unknown_high_threshold(self):
        r = self.result('`MISSING_VAR=1`', fail_on='HIGH')
        self.assertEqual(r['status'], 'PASS')
        self.assertEqual(len(r['findings']), 1)

    def test_example_mismatch(self):
        self.rule(self.result(code='os.getenv("TIMEOUT", "60")', extra={'.env.example': 'TIMEOUT=30\n'}), 'example-env-default-mismatch')

    def test_example_agree(self):
        r = self.result(code='os.getenv("TIMEOUT", "30")', extra={'.env.example': 'TIMEOUT=30'})
        self.assertEqual(r['verified_claims'], [dict(kind='example', name='TIMEOUT', value='30')])

    def test_bool_false(self):
        self.assertEqual(self.result('--verbose defaults to false', 'p.add_argument("--verbose", action="store_true")')['status'], 'PASS')

    def test_bool_mismatch(self):
        self.rule(self.result('--verbose defaults to true', 'p.add_argument("--verbose", action="store_true")'), 'documented-flag-default-mismatch')

    def test_store_false(self):
        self.assertEqual(len(self.result('--cache defaults to true', 'p.add_argument("--cache", action="store_false")')['verified_claims']), 1)

    def test_bool_explicit_overrides_action(self):
        self.rule(self.result('--cache defaults to true', 'p.add_argument("--cache", action="store_false", default=False)'), 'documented-flag-default-mismatch')

    def test_name_unknown(self):
        self.assertEqual(self.result('TIMEOUT defaults to 30', 'os.getenv("TIMEOUT", DEFAULT)')['findings'], [])

    def test_call_unknown(self):
        self.assertEqual(self.result('TIMEOUT defaults to 30', 'os.getenv("TIMEOUT", compute())')['findings'], [])

    def test_attribute_unknown(self):
        self.assertEqual(self.result('--port defaults to 30', 'p.add_argument("--port", default=config.port)')['verified_claims'], [])

    def test_binop_unknown(self):
        self.assertEqual(self.result('TIMEOUT defaults to 30', 'os.getenv("TIMEOUT", 10+20)')['findings'], [])

    def test_tests_only(self):
        self.rule(self.result('TIMEOUT defaults to 30', extra={'tests/app.py': 'os.getenv("TIMEOUT", "30")'}), 'documented-env-unknown')

    def test_click_match(self):
        r = self.result('--retries defaults to 3', '@click.option("--retries", default=3)\ndef run(): pass')
        self.assertEqual(len(r['verified_claims']), 1)

    def test_click_mismatch(self):
        self.rule(self.result('--retries defaults to 3', '@click.option("--retries", default=5)\ndef run(): pass'), 'documented-flag-default-mismatch')

    def test_click_dual(self):
        self.assertEqual(len(self.result('--cache defaults to true', '@click.option("--cache/--no-cache", default=True)\ndef run(): pass')['verified_claims']), 1)

    def test_environ_get(self):
        self.assertEqual(len(self.result('KEY defaults to abc', 'os.environ.get("KEY", "abc")')['verified_claims']), 1)

    def test_environ_subscript(self):
        r = self.result('KEY defaults to abc', 'os.environ["KEY"]')
        self.assertEqual(r['findings'], [])
        self.assertEqual(r['verified_claims'], [])

    def test_environ_write_is_not_read(self):
        self.rule(self.result('KEY defaults to abc', 'os.environ["KEY"] = "abc"'), 'documented-env-unknown')

    def test_no_claims(self):
        self.assertEqual(self.result('Welcome to the project')['status'], 'PASS')

    def test_no_code(self):
        self.project('TIMEOUT defaults to 30')
        (self.root / 'app.py').unlink()
        self.rule(audit(self.root), 'documented-env-unknown')

    def test_usage_example(self):
        self.assertEqual(self.result('```\nmytool --port 9000\n```', 'p.add_argument("--port", default=8080)')['findings'], [])

    def test_flag_prose_inside_fence_ignored(self):
        self.assertEqual(self.result('```\n--port defaults to 9000\n```', 'p.add_argument("--port", default=8080)')['findings'], [])

    def test_inline_assignment(self):
        self.rule(self.result('Set `export TIMEOUT=30;` please', 'os.getenv("TIMEOUT", "60")'), 'documented-env-default-mismatch')

    def test_bare_assignment_not_claim(self):
        self.assertEqual(self.result('TIMEOUT=30', 'os.getenv("TIMEOUT", "60")')['findings'], [])

    def test_prose_default_is(self):
        self.assertEqual(len(self.result('TIMEOUT DEFAULT IS `30`', 'os.getenv("TIMEOUT", 30)')['verified_claims']), 1)

    def test_parenthesized(self):
        self.assertEqual(len(self.result('TIMEOUT (default 30)', 'os.getenv("TIMEOUT", 30)')['verified_claims']), 1)

    def test_quoted_name(self):
        self.assertEqual(len(self.result('`TIMEOUT` defaults to `30`', 'os.getenv("TIMEOUT", 30)')['verified_claims']), 1)

    def test_imported_getenv(self):
        self.assertEqual(len(self.result('TIMEOUT defaults to 30', 'from os import getenv\ngetenv("TIMEOUT", "30")')['verified_claims']), 1)

    def test_imported_alias(self):
        self.assertEqual(len(self.result('TIMEOUT defaults to 30', 'from os import getenv as env\nenv("TIMEOUT", "30")')['verified_claims']), 1)

    def test_implicit_none_mismatch(self):
        self.rule(self.result('TIMEOUT defaults to 30', 'os.getenv("TIMEOUT")'), 'documented-env-default-mismatch')

    def test_none_match(self):
        self.assertEqual(len(self.result('TIMEOUT defaults to null', 'os.getenv("TIMEOUT", None)')['verified_claims']), 1)

    def test_empty_none(self):
        self.assertEqual(len(self.result('TIMEOUT defaults to empty', 'os.getenv("TIMEOUT")')['verified_claims']), 1)

    def test_float(self):
        self.assertEqual(len(self.result('TIMEOUT defaults to 30.5', 'os.getenv("TIMEOUT", 30.5)')['verified_claims']), 1)

    def test_negative(self):
        self.assertEqual(len(self.result('TIMEOUT defaults to -30', 'os.getenv("TIMEOUT", -30)')['verified_claims']), 1)

    def test_numeric_boolean(self):
        self.assertEqual(len(self.result('ENABLED defaults to 1', 'os.getenv("ENABLED", True)')['verified_claims']), 1)

    def test_first_literal_wins(self):
        r = self.result('TIMEOUT defaults to 30', 'os.getenv("TIMEOUT", dynamic())\nos.getenv("TIMEOUT", 30)\nos.getenv("TIMEOUT", 60)')
        self.assertEqual(len(r['verified_claims']), 1)

    def test_files_sorted(self):
        r = self.result('TIMEOUT defaults to 30', 'os.getenv("TIMEOUT", 60)', {'aaa.py': 'os.getenv("TIMEOUT", 30)'})
        self.assertEqual(len(r['verified_claims']), 1)

    def test_first_doc_claim(self):
        r = self.result('TIMEOUT defaults to 30\nTIMEOUT defaults to 60', 'os.getenv("TIMEOUT", 30)')
        self.assertEqual(len(r['verified_claims']), 1)

    def test_first_inline_claim_on_same_line(self):
        r = self.result('`TIMEOUT=60` then `TIMEOUT=30`', 'os.getenv("TIMEOUT", 60)')
        self.assertEqual(len(r['verified_claims']), 1)

    def test_embedded_assignment_not_token(self):
        self.assertEqual(self.result('`otherTIMEOUT=30`')['findings'], [])

    def test_embedded_prose_name_not_token(self):
        self.assertEqual(self.result('otherTIMEOUT defaults to 30')['findings'], [])

    def test_recursive_docs(self):
        self.rule(self.result(extra={'docs/nested/config.md': 'TIMEOUT defaults to 30'}), 'documented-env-unknown')

    def test_top_level_markdown(self):
        self.rule(self.result(extra={'CONFIG.md': 'TIMEOUT defaults to 30'}), 'documented-env-unknown')

    def test_arbitrary_nested_markdown_ignored(self):
        self.assertEqual(self.result(extra={'other/config.md': 'TIMEOUT defaults to 30'})['findings'], [])

    def test_skip_directories(self):
        for directory in ('.venv', '__pycache__', 'dist', '.git', '.egg-info', 'tests', 'pkg.egg-info'):
            with self.subTest(directory=directory):
                self.rule(self.result('TIMEOUT defaults to 30', extra={directory+'/app.py': 'os.getenv("TIMEOUT", 30)'}), 'documented-env-unknown')

    def test_examples_code_scanned(self):
        self.assertEqual(len(self.result('TIMEOUT defaults to 30', extra={'examples/app.py': 'os.getenv("TIMEOUT", 30)'})['verified_claims']), 1)

    def test_example_unknown_ignored(self):
        self.assertEqual(self.result(extra={'.env.example': 'MISSING_VAR=1'})['findings'], [])

    def test_example_export_comments_quotes(self):
        r = self.result(code='os.getenv("TIMEOUT", 30)', extra={'.env.example': '# comment\n\nexport TIMEOUT="30"\n'})
        self.assertEqual(len(r['verified_claims']), 1)

    def test_env_files_never_opened(self):
        self.project(extra={'.env': 'PRIVATE_PLACEHOLDER', '.env.local': 'PRIVATE_PLACEHOLDER'})
        original = Path.open
        def guarded(path, *args, **kwargs):
            if path.name.startswith('.env') and path.name != '.env.example':
                raise AssertionError('Forbidden read')
            return original(path, *args, **kwargs)
        with patch.object(Path, 'open', guarded):
            self.assertEqual(audit(self.root)['status'], 'PASS')

    def test_reverse_direction_ignored(self):
        self.assertEqual(self.result(code='os.getenv("TIMEOUT", 30)')['findings'], [])

    def test_missing_flag_ignored(self):
        self.assertEqual(self.result('--port defaults to 30')['findings'], [])

    def test_short_only_ignored(self):
        self.assertEqual(self.result('--port defaults to 30', 'p.add_argument("-p", default=60)')['findings'], [])

    def test_longest_flag(self):
        self.assertEqual(len(self.result('--timeout defaults to 30', 'p.add_argument("--time", "--timeout", default=30)')['verified_claims']), 1)

    def test_json_deterministic(self):
        self.project('ZZZ defaults to 1\nAAA defaults to 1\n--port defaults to 30', 'p.add_argument("--port", default=60)')
        status, out, _ = self.cli('--json')
        result = json.loads(out)
        self.assertEqual(status, 2)
        fs = result['findings']
        self.assertEqual(fs, sorted(fs, key=lambda f: (f['rule'], f['file'], f['line'])))
        self.assertEqual(out, self.cli('--json')[1])

    def test_cli_pass(self):
        self.project()
        self.assertEqual(self.cli()[0], 0)
        self.assertIn('PASS', self.cli()[1])

    def test_cli_fail(self):
        self.project('TIMEOUT defaults to 30')
        self.assertEqual(self.cli()[0], 2)

    def test_cli_threshold(self):
        self.project('TIMEOUT defaults to 30')
        status, out, _ = self.cli('--fail-on', 'HIGH')
        self.assertEqual(status, 0)
        self.assertIn('MEDIUM documented-env-unknown', out)

    def test_ignore(self):
        self.project('TIMEOUT defaults to 30')
        status, out, _ = self.cli('--ignore', 'documented-env-unknown', '--json')
        self.assertEqual(status, 0)
        self.assertEqual(json.loads(out)['findings'], [])

    def test_ignore_multiple(self):
        self.project('TIMEOUT defaults to 30\n--port defaults to 30', 'p.add_argument("--port", default=60)')
        self.assertEqual(self.cli('--ignore', 'documented-env-unknown,documented-flag-default-mismatch')[0], 0)

    def test_version(self):
        self.assertEqual(self.cli('--version')[:2], (0, '0.1.0\n'))

    def test_bad_argument(self):
        self.assertEqual(self.cli('--bogus')[0], 1)

    def test_bad_threshold(self):
        self.assertEqual(self.cli('--fail-on', 'LOW')[0], 1)

    def test_bad_ignore(self):
        self.assertEqual(self.cli('--ignore', 'typo')[0], 1)

    def test_missing_path(self):
        self.assertEqual(self.cli(str(self.root / 'absent'))[0], 1)

    def test_verbose(self):
        self.project('TIMEOUT defaults to 30', 'os.getenv("TIMEOUT", 30)')
        self.assertIn('VERIFIED env TIMEOUT=30', self.cli('--verbose')[1])

    def test_syntax_error_no_source_leak(self):
        self.project(code='PRIVATE_PLACEHOLDER = (')
        status, out, err = self.cli()
        self.assertEqual(status, 1)
        self.assertNotIn('PRIVATE_PLACEHOLDER', out + err)

    def test_no_execution(self):
        self.assertEqual(self.result(code='raise RuntimeError("must not execute")')['status'], 'PASS')


if __name__ == '__main__':
    unittest.main()
