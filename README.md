# config-claim-audit

An offline, static Python CLI that compares documented configuration defaults
with defaults in Python source. It never imports or executes the audited code,
accesses the network, or reads `.env` files. Only `.env.example` assignment lines
are considered, for the example-value rule.

Requires Python 3.9 or later; no runtime dependencies.

## Usage

```sh
pip install config-claim-audit
config-claim-audit .
config-claim-audit ./project --json
config-claim-audit ./project --fail-on HIGH
config-claim-audit ./project --ignore example-env-default-mismatch
config-claim-audit ./project --verbose
config-claim-audit --version
python3 -m config_claim_audit ./project
```

The path defaults to the current directory. `--fail-on MEDIUM` is the default;
`HIGH` still prints MEDIUM findings but does not fail on them. `--ignore` accepts
a comma-separated list of rule names and removes those findings entirely.
`--verbose` adds verified claims to human output. `--version` prints `0.1.0`.

Exit codes: **0** PASS, **2** FAIL (a finding meets the severity threshold),
**1** usage or scan error. Human output gives one line per finding, followed by
a PASS/FAIL summary. JSON output always includes `status`, `findings` (sorted
by rule, file, line), and `verified_claims`. Each finding has `severity`, `rule`,
`file`, `line`, and `message`; each verified claim has `kind` (`env`, `flag`, or
`example`), `name`, and normalized `value`.

For example, documentation saying `TIMEOUT defaults to 30` agrees with
`os.getenv("TIMEOUT", "30")`. Documentation saying `--port defaults to 8080`
agrees with `parser.add_argument("--port", default=8080)`. A code default of
8000 instead produces a HIGH flag mismatch. Run the included fixtures:

```sh
config-claim-audit tests/fixtures/good --json
config-claim-audit tests/fixtures/bad --json
```

## Full rule table

| # | Rule | Severity | Trigger |
|---|---|---|---|
| 1 | `documented-env-default-mismatch` | HIGH | A documented env assignment or prose default differs from the first literal code default. Reports doc file:line, variable, documented value, and code default. |
| 2 | `documented-flag-default-mismatch` | HIGH | A prose flag default differs from a literal code default or implied argparse store_true/store_false default. Reports doc file:line, flag, documented value, and code default. |
| 3 | `documented-env-unknown` | MEDIUM | A documented env variable has no recognized code read in any scanned file. Reports doc file:line and variable; external wrappers may account for it. |
| 4 | `example-env-default-mismatch` | MEDIUM | A `.env.example` assignment differs from a literal code default. Reports example file:line, variable, example value, and code default. Unknown variables or defaults are skipped. |
| 5 | No reverse-direction rule | — | Undocumented code defaults, nonexistent documented flags, and computed defaults produce no findings. Flag existence belongs to readme-claim-audit; UNKNOWN is not a verified match. |

## What is scanned

All Python files are parsed with AST, skipping `.venv`, `__pycache__`, `dist`,
`.git`, `.egg-info` (including package `.egg-info` directories), and `tests`
directories. Code in `examples` is included. Docs are top-level `*.md` files
(including README.md) and recursive `docs/**/*.md` files.

Recognized env reads are `os.getenv`, `os.environ.get`, read access to
`os.environ["NAME"]`, and `getenv` imported from `os` (including aliases).
Subscript reads establish presence but have no default. Omitted getenv/get
defaults mean None. Variable names must be string literals. Literal strings,
numbers, booleans, and None are comparable. Calls, names, attributes, and
computed defaults are unknown. CLI evidence is `add_argument` and
`@click.option`, including `--flag/--no-flag`. The longest long option is used;
short-only options are ignored. Without an explicit default, `store_true`
implies False and `store_false` implies True.

Env claims are uppercase names of at least three characters assigned a
non-whitespace value in inline code or fenced blocks, optionally prefixed
with `export`. Trailing commas and semicolons are removed. Prose patterns are
`NAME defaults to VALUE`, `NAME default is VALUE`, and `NAME (default VALUE)`;
the same patterns work for `--flag`. Wording is case-insensitive but env names
are case-sensitive. Values are single tokens, optionally backticked.
`.env.example` accepts `NAME=value` and optional `export`, ignoring blank and
comment lines. No other env files are opened.

Surrounding quotes/backticks are stripped. True/true/1 and False/false/0
normalize to booleans; strings and literal numbers compare as plain strings.
None matches explicit None/null/empty documentation. Files and lines are
processed in sorted order; the first doc claim and first literal code default
for each name win. Matching claims appear in JSON as verified claims.

## Differentiation

- **env-drift** audits env variable PRESENCE across code, `.env.example`, CI,
  and Dockerfile. This tool audits documented DEFAULT VALUES and example-vs-code
  values: presence versus value, with no overlap in rules.
- **readme-claim-audit** checks whether a documented flag EXISTS in argparse or
  click. This tool checks its documented DEFAULT against the source default.
- **dep-claim-audit / api-claim-audit / entry-claim-audit** audit dependency,
  API-symbol, and entry-point promises. This tool audits configuration-default
  promises, using the same family approach for a different promise source.
- **dotenv-linter / python-dotenv** validate env-file syntax or load values at
  runtime. This tool checks documentation promises statically and offline;
  it does not validate env-file syntax or load configuration.

## Honest limitations

Static scans miss dynamic defaults, runtime configuration files, importlib,
and computed values. First-literal-default-wins hides later conflicting
defaults. Narrow prose patterns miss unusual phrasing rather than flagging it.
Code-block `--flag value` invocations are usage, deliberately not default
claims. Env vars read only by wrappers outside the repository are flagged
MEDIUM; use `--ignore documented-env-unknown` where appropriate.
`.env.example` values are templates, so example mismatches are MEDIUM, not HIGH.

This is a syntactic scan, not name resolution or runtime type inference.
It does not track renamed os/click module imports, shadowing, argument-parser
inheritance, set_defaults, or configuration framework behavior. Syntax errors
and unreadable scanned files produce exit 1. Only supported claims can be
verified; PASS does not certify all configuration or documentation as correct.

## Development

```sh
PYTHONPATH=src python3 -m unittest discover -s tests -v
python3 -m build
```

Tests use local temporary fixtures only. Packaging uses setuptools build-meta;
the CLI itself uses only the Python standard library. Licensed under MIT.
