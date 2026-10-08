"""S4 on real data_long: the core-8 BOOK through the BookRunner (shared equity, 1% risk, 3x cap) against the research
book (newcore/strategy/research/ema_mom_after_cost.py, same mechanics), on the first 4000 4h candles (2021-12-19 ->
2023-09-30). The research book is causal, so its trades that closed before candle 4000 are exactly those of a run that
ends there; the full 10,499-candle comparison is in the S4 report (s4_book_*.txt).

Expected differences (explained):
  1. size floored to the venue step / stop to the tick (R uses the actual stop distance), as in the single-symbol replay;
  2. a signal exit fills at the next open, the research at the signal close;
  3. the 3x cap: the runner sizes on the signal close (the next open is unknown at decision time), the research on the
     fill price o x (1 + slip); a capped trade's size differs by that ratio (its R does not).
"""
from decimal import Decimal

import pytest

from data_long_book import RESEARCH, RESEARCH_MAXPOS4, compare, research_rows, run

pytestmark = pytest.mark.slow
N = 4000


@pytest.mark.parametrize('policy,costs', [(RESEARCH, 'base'), (RESEARCH_MAXPOS4, 'base, max_pos 4')])
def test_book_matches_the_research_book(policy, costs):
    runner, venue, bars = run(policy, upto=N)
    pairs, only_research, only_runner = compare(runner, bars, costs, before_bar=N - 1)
    assert only_research == [] and only_runner == []
    assert len(pairs) == len(research_rows(costs, before_bar=N - 1)) > 50
    assert all(p['same_exit'] for p in pairs), [p for p in pairs if not p['same_exit']]
    assert max(abs(p['dR']) for p in pairs) < Decimal('0.03')
    s = runner.summary()
    assert s.counters['unprotected_cycles'] == 0 and s.counters['holds'] == 0
    if policy.max_positions is not None:
        assert max_open(runner) <= policy.max_positions


def max_open(runner):
    events, top = [], 0
    for t in runner.trades():
        events += [(t.entry_ms, 1), (t.exit_ms, -1)]
    n = 0
    for _, d in sorted(events, key=lambda e: (e[0], e[1])):
        n += d
        top = max(top, n)
    return top
