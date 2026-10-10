"""The M3 evidence tool (newcore/runner/evidence.py) end to end on a short window: run -> part JSON + trades.csv ->
report. The full core-8 pack is docs/newcore/slice/M3_long_candidate_evidence.md."""
import json
import os

from newcore.runner import evidence as E

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


def test_evidence_run_and_report_on_a_window(tmp_path):
    part, trades, md = str(tmp_path / 'p.json'), str(tmp_path / 't.csv'), str(tmp_path / 'r.md')
    assert E.main(['run', '--root', ROOT, '--costs', '2x', '--kill', 'on', '--upto', '700', '--work',
                   str(tmp_path / 'w'), '--out', part, '--trades', trades]) == 0
    p = json.load(open(part, encoding='utf-8'))
    assert p['costs'] == '2x' and p['kill'] == 'on' and p['unprotected'] == 0
    assert open(trades, encoding='utf-8').readline().startswith('symbol,side,lot_id')
    assert len(p['curve']) == 700 and all(t['side'] == 'LONG' for t in p['trades'])
    assert E.main(['report', '--parts', part, '--out', md]) == 0
    text = open(md, encoding='utf-8').read()
    assert 'NOT a promotion' in text and 'episode CI' in text and '2x, kill ON (canary as configured)' in text
