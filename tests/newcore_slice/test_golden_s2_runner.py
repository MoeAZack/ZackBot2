"""M4: the S2-owned golden cases (target / tp1 / time_exit / trail) through the ManagedRunner (golden_adapter
run_case(case, management=True)). 5 / 10 pass exactly (codes, bars, sides, R / pnl to 1e-9): every time exit, including
the restart fault. The other 5 are pinned to their KNOWN deltas, so any change shows:

- G-GAP-TP-L/S-01, G-TP1-ZERO-L-01: the runner fires bot-side targets on the candle-CLOSE mark and its market order
  fills at the next open (FakeVenue's candle clock); the golden fills a target AT its level on the zb-path (or at the
  gapped open). GAP-TP: same code / bars, R lower by the open-vs-close gap; TP1-ZERO: the target is touched intrabar
  only (high 104.02 = target, close 104), so the runner holds the trade (owner: a sub-candle mark cadence + venue).
- G-STOP-CROSSED-L/S-01: the NC-07 trail is `close -/+ trail_offset` at candle close (offset fixed at entry); the legacy
  trail ratchets from the highest high with the CURRENT ATR, so its level is crossed at bar 286 and the core's is not
  (owner: management lane, trail definition)."""
import os

import pytest

from golden_adapter import diff, load, run_case

GOLDEN = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), 'golden', 'cases')
PASS = ('G-TIME-L-01', 'G-TIME-L-02', 'G-TIME-S-01', 'G-TIME-S-02', 'G-RESTART-TIME-L-01')
KNOWN_DELTA = {
    'G-GAP-TP-L-01': [('trades[0].R', '2.9279003', 2.877935295), ('trades[0].pnl', '29.279003', 28.77935295)],
    'G-GAP-TP-S-01': [('trades[0].R', '2.9321003', 2.882065295), ('trades[0].pnl', '29.321003', 28.82065295)],
    'G-TP1-ZERO-L-01': [('trades', 1, 0)],
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
