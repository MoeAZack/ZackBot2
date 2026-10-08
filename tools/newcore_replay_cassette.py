"""Replay a recorded NEWCORE cassette (e.g. an S5 smoke cassette) offline, interaction by interaction.

    python tools/newcore_replay_cassette.py <path to smoke-<utc ms>.json>

No network, no real key: the replay signs with dummy credentials and answers from the cassette. Prints PASS / FAIL /
SKIPPED per interaction (the rebuilt request must match the recorded one and the recorded answer must parse into a
typed outcome) and a name-based leak audit of the file.

Exit codes: 0 every interaction PASSES and the audit is clean; 1 a FAIL or an audit finding; 2 the file is missing or is
not a NEWCORE cassette.
"""
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from newcore.venue.cassette_replay import leak_audit, replay_report  # noqa: E402


def main(argv=None, *, out=None):
    argv = list(sys.argv[1:] if argv is None else argv)
    out = out or sys.stdout
    if len(argv) != 1 or argv[0].startswith('-'):
        out.write('usage: python tools/newcore_replay_cassette.py <cassette.json>\n')
        return 2
    path = argv[0]
    try:
        with open(path, encoding='utf-8') as fh:
            doc = json.load(fh)
        results = replay_report(doc)
    except (OSError, ValueError) as ex:
        out.write(f'ERROR: cannot replay {path}: {type(ex).__name__}: {ex}\n')
        return 2
    for r in results:
        out.write(f'{r.status:<7} {r.label:<52} {r.detail}\n')
    passed = sum(r.status == 'PASS' for r in results)
    audit = leak_audit(doc)
    out.write(f'interactions: {passed} PASS / {len(results)} total; leak audit: '
              f'{"clean" if audit is None else "FAIL - " + audit}\n')
    return 0 if passed == len(results) and audit is None else 1


if __name__ == '__main__':
    sys.exit(main())
