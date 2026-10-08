"""M4: the S2-owned golden cases (target / tp1 / time_exit / trail) through the ManagedRunner (golden_adapter
run_case(case, management=True): the candle is played along zb-path/1 with intra-candle marks, newcore/runner/
intrabar.py). 7 / 10 pass exactly (codes, bars, sides, R / pnl to 1e-9): every time exit including the restart
fault, and both gap targets. The other 3 are pinned to their KNOWN deltas, so any change shows:

- G-TP1-ZERO-L-01: the runner books the EXACT value (pnl 19.38593201, R 1.938593201 - the NC-07 core and the golden
  newcore_sim adapter agree to the digit); the case prints it rounded (19.385932 / 1.9385932), 1e-8 beyond EPS.
  Pending the pack correction (docs/newcore/NC08_GOLDEN_ADAPTER_PROPOSALS.md section 1, rule A).
- G-STOP-CROSSED-L/S-01: the NC-07 trail is `close -/+ trail_offset` at candle close (offset fixed at entry); the legacy
  trail ratchets from the highest high with the CURRENT ATR, so its level is crossed at bar 286 and the core's is not.
  Pending a Codex ruling on the trail definition (management lane)."""
import os

import pytest

from golden_adapter import diff, load, run_case

GOLDEN = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), 'golden', 'cases')
PASS = ('G-TIME-L-01', 'G-TIME-L-02', 'G-TIME-S-01', 'G-TIME-S-02', 'G-RESTART-TIME-L-01', 'G-GAP-TP-L-01',
        'G-GAP-TP-S-01')
KNOWN_DELTA = {
    'G-TP1-ZERO-L-01': [('trades[0].R', '1.9385932', 1.938593201), ('trades[0].pnl', '19.385932', 19.38593201)],
    'G-STOP-CROSSED-L-01': [('trades', 1, 0)],
    'G-STOP-CROSSED-S-01': [('trades', 1, 0)],
}


@pytest.mark.parametrize('case_id', PASS)
def test_s2_golden_case_passes_through_the_managed_runner(case_id):
    case = load(case_id, GOLDEN)
    trace, runner = run_case(case, management=True)
    assert diff(case, trace) == []
    s = runner.summary()
    assert s.counters['unprotected_cycles'] == 0 and s.mode == 'active' and s.ownership == 'known_empty'


@pytest.mark.parametrize('case_id', sorted(KNOWN_DELTA))
def test_s2_golden_case_known_delta(case_id):
    case = load(case_id, GOLDEN)
    trace, runner = run_case(case, management=True)
    got = [(k, e, round(a, 9) if isinstance(a, float) else a) for k, e, a in diff(case, trace)]
    assert got == KNOWN_DELTA[case_id]
    assert runner.summary().counters['unprotected_cycles'] == 0
