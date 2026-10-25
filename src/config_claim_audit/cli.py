"""Command line interface."""
import argparse
import json
import sys
from . import __version__
from .audit import RULES, audit


class Parser(argparse.ArgumentParser):
    def error(self, message):
        self.print_usage(sys.stderr)
        self.exit(1, '{}: error: {}\n'.format(self.prog, message))


def main(argv=None):
    parser = Parser(prog='config-claim-audit')
    parser.add_argument('path', nargs='?', default='.')
    parser.add_argument('--json', action='store_true')
    parser.add_argument('--fail-on', choices=['HIGH', 'MEDIUM'], default='MEDIUM')
    parser.add_argument('--ignore', default='', metavar='RULE,...')
    parser.add_argument('--verbose', action='store_true')
    parser.add_argument('--version', action='version', version=__version__)
    args = parser.parse_args(argv)
    ignored = set(filter(None, args.ignore.split(',')))
    if ignored - RULES.keys():
        parser.error('unknown ignored rule: ' + ', '.join(sorted(ignored - RULES.keys())))
    try:
        result = audit(args.path, args.fail_on, ignored)
    except (OSError, ValueError, SyntaxError) as exc:
        # Avoid echoing source lines (which can contain unrelated private data).
        print('config-claim-audit: unable to scan project ({})'.format(type(exc).__name__), file=sys.stderr)
        return 1
    if args.json:
        print(json.dumps(result))
    else:
        for finding in result['findings']:
            print('{severity} {rule} {file}:{line} {message}'.format(**finding))
        if args.verbose:
            for claim in result['verified_claims']:
                print('VERIFIED {kind} {name}={value}'.format(**claim))
        print('{}: {} finding(s), {} verified claim(s)'.format(result['status'], len(result['findings']), len(result['verified_claims'])))
    return 2 if result['status'] == 'FAIL' else 0
