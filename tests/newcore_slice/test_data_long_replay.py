"""Replay on real data_long (BTCUSDT 4h, 10,499 candles) through the S1 Runner with ema_mom, against the research
after-cost trade list (see data_long_replay.py for the fixture's provenance and the conventions).

Expected differences, all small and explained:
  1. size floored to the venue step (BTC 0.0001 on ~0.05-0.1 BTC) and the stop floored to the tick (0.1): R moves by
     < 1e-3 (the research sizes in floats with no filters);
  2. a signal exit fills at the NEXT candle's open (the runner decides at the close, the market order fills at the next
     open), the research at the signal candle's close: o[i+1] vs c[i];
  3. the research is the core-8 book (shared equity, 3x gross cap): R is equity-free unless the cap trims a size; on
     BTCUSDT no paired trade differs by more than the tolerance below.
The strategy sees a 1500-candle window (the live kline limit), the research the full history: no signal differs.
"""
from decimal import Decimal

import pytest

from data_long_replay import compare, replay, research_rows

pytestmark = pytest.mark.slow
SYM = 'BTCUSDT'


@pytest.fixture(scope='module')
def run():
    return replay(SYM)


def test_every_research_trade_is_reproduced_on_the_runner(run):
    runner, venue, journal, bars = run
    pairs, only_research, only_runner = compare(SYM, runner, bars)
    assert only_research == [] and only_runner == []
    assert len(pairs) == len(research_rows(SYM)) == 31
    for p in pairs:
        assert p['i_out'][0] == p['i_out'][1] and p['exit'][0] == p['exit'][1], p
        assert abs(p['px_in'][0] - p['px_in'][1]) <= p['px_in'][1] * Decimal('1e-9'), p     # o x (1 + slip), exact
        tol = Decimal('0.0005') if p['why'] != 'signal' else Decimal('0.01')
        assert abs(p['px_out'][0] - p['px_out'][1]) <= p['px_out'][1] * tol, p
        assert abs(p['dR']) < Decimal('0.001'), p
    total = sum(p['dR'] for p in pairs)
    assert abs(total) < Decimal('0.005')


def test_the_run_is_safe_and_ends_known_empty(run):
    runner, venue, journal, bars = run
    s = runner.summary()
    assert s.counters['unprotected_cycles'] == 0 and s.counters['holds'] == 0 and s.counters['mismatch_cycles'] == 0
    assert s.counters['cycles'] == len(bars) and s.open_lots == 0
    assert s.mode == 'active' and s.ownership == 'known_empty'
    cids = [o.ref.client_id for o in venue.orders_submitted()]
    assert len(cids) == len(set(cids)) == 3 * s.trades - sum(1 for t in runner.trades() if t.exit_code == 'STOP_HIT')
    assert s.equity_end == Decimal('10000') + s.pnl                       # the ledger equals the venue wallet
