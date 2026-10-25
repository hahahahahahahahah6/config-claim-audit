"""Offline configuration claim extraction and comparison. Never imports project code."""
import ast
import re
from pathlib import Path

RULES = {
    'documented-env-default-mismatch': 'HIGH',
    'documented-flag-default-mismatch': 'HIGH',
    'documented-env-unknown': 'MEDIUM',
    'example-env-default-mismatch': 'MEDIUM',
}
SKIP = {'.venv', '__pycache__', 'dist', '.git', '.egg-info', 'tests'}
UNKNOWN = object()
NAME = r'[A-Z][A-Z0-9_]{2,}'
VALUE = r'`[^`\s]+`|[^\s)`,;]+'
PROSE = re.compile(r'(?<![\w-])(?P<name>--[\w-]+|'+NAME+r')\s+(?:(?i:defaults to|default is)\s+|\((?i:default)\s+)(?P<value>'+VALUE+r')')
ASSIGN = re.compile(r'(?<!\S)('+NAME+r')=(\S+)')


def normalize(value):
    if value is None:
        return 'None'
    value = str(value).strip().strip('`\'"')
    if value.lower() in ('true', '1'):
        return 'true'
    if value.lower() in ('false', '0'):
        return 'false'
    if value.lower() in ('none', 'null', 'empty'):
        return 'None'
    return value


def literal(node):
    if isinstance(node, ast.Constant) and (node.value is None or isinstance(node.value, (str, int, float, bool))):
        return node.value
    if isinstance(node, ast.UnaryOp) and isinstance(node.op, (ast.USub, ast.UAdd)):
        value = literal(node.operand)
        if type(value) in (int, float):
            return -value if isinstance(node.op, ast.USub) else value
    return UNKNOWN


def code_evidence(root):
    known, env, flags = set(), {}, {}
    for path in sorted(root.rglob('*.py')):
        if any(p in SKIP or p.endswith('.egg-info') for p in path.relative_to(root).parts[:-1]):
            continue
        tree = ast.parse(path.read_text(encoding='utf-8'), filename=str(path))
        imported = {a.asname or a.name for n in ast.walk(tree) if isinstance(n, ast.ImportFrom) and n.module == 'os' for a in n.names if a.name == 'getenv'}
        for node in sorted(ast.walk(tree), key=lambda n: (getattr(n, 'lineno', 0), getattr(n, 'col_offset', 0))):
            if isinstance(node, ast.Subscript) and isinstance(node.ctx, ast.Load) and ast.unparse(node.value) == 'os.environ':
                key = literal(node.slice)
                if isinstance(key, str):
                    known.add(key)
            if not isinstance(node, ast.Call):
                continue
            func = ast.unparse(node.func)
            kwargs = {k.arg: k.value for k in node.keywords}
            if func in ('os.getenv', 'os.environ.get') or func in imported:
                key = literal(node.args[0]) if node.args else literal(kwargs.get('key'))
                if isinstance(key, str):
                    known.add(key)
                    default = literal(node.args[1]) if len(node.args) > 1 else literal(kwargs['default']) if 'default' in kwargs else None
                    if default is not UNKNOWN:
                        env.setdefault(key, normalize(default))
            if func.endswith('.add_argument') or func == 'add_argument' or func == 'click.option':
                names = [a.value.split('/')[0] for a in node.args if isinstance(a, ast.Constant) and isinstance(a.value, str) and a.value.startswith('--')]
                if not names:
                    continue
                default = literal(kwargs['default']) if 'default' in kwargs else UNKNOWN
                if 'default' not in kwargs and 'action' in kwargs:
                    action = literal(kwargs['action'])
                    if action in ('store_true', 'store_false'):
                        default = action == 'store_false'
                if default is not UNKNOWN:
                    flags.setdefault(max(names, key=len), normalize(default))
    return known, env, flags


def doc_claims(root):
    paths = set(root.glob('*.md'))
    if (root / 'docs').is_dir():
        paths.update((root / 'docs').rglob('*.md'))
    claims = {}
    for path in sorted(paths):
        fence = None
        for line_no, line in enumerate(path.read_text(encoding='utf-8').splitlines(), 1):
            marker = re.match(r'^\s*(`{3,}|~{3,})', line)
            if marker:
                if fence is None:
                    fence = marker[1][0]
                elif marker[1][0] == fence:
                    fence = None
                continue
            candidates = []
            if fence:
                spans = [(0, line)]
            else:
                spans = [(m.start(1), m[1]) for m in re.finditer(r'`([^`]+)`', line)]
                # Backticks around names are common in prose as well.
                prose = re.sub(r'`(--[\w-]+|'+NAME+r')`', r'\1', line)
                candidates.extend((m.start(), 'flag' if m['name'].startswith('--') else 'env', m['name'], m['value']) for m in PROSE.finditer(prose))
            for offset, span in spans:
                candidates.extend((offset + m.start(), 'env', m[1], m[2].rstrip(',;')) for m in ASSIGN.finditer(span))
            for _, kind, name, value in sorted(candidates):
                claims.setdefault((kind, name), (normalize(value), path.relative_to(root).as_posix(), line_no))
    return claims


def audit(path, fail_on='MEDIUM', ignore=()):
    root = Path(path)
    if not root.is_dir():
        raise ValueError('project path must be a directory')
    known, env, flags = code_evidence(root)
    findings, verified = [], []

    def compare(kind, name, value, file, line):
        defaults = flags if kind == 'flag' else env
        rule = None
        if kind == 'env' and name not in known:
            rule = 'documented-env-unknown'
            message = '{}:{} {} has no code read'.format(file, line, name)
        elif name in defaults:
            actual = defaults[name]
            if value == actual:
                verified.append(dict(kind=kind, name=name, value=value))
            else:
                rule = {'env': 'documented-env-default-mismatch', 'flag': 'documented-flag-default-mismatch', 'example': 'example-env-default-mismatch'}[kind]
                message = '{}:{} {} documented value {!r}; code default {!r}'.format(file, line, name, value, actual)
        if rule and rule not in ignore:
            findings.append(dict(severity=RULES[rule], rule=rule, file=file, line=line, message=message))

    for (kind, name), (value, file, line) in doc_claims(root).items():
        compare(kind, name, value, file, line)
    example = root / '.env.example'
    if example.is_file():
        for number, line in enumerate(example.read_text(encoding='utf-8').splitlines(), 1):
            match = re.match(r'^\s*(?:export\s+)?('+NAME+r')=(.*)$', line)
            if match:
                compare('example', match[1], normalize(match[2].strip()), '.env.example', number)
    findings.sort(key=lambda f: (f['rule'], f['file'], f['line']))
    failed = any(f['severity'] == 'HIGH' or fail_on == 'MEDIUM' for f in findings)
    return dict(status='FAIL' if failed else 'PASS', findings=findings, verified_claims=verified)
