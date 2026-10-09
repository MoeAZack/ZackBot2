"""Cowork on ab65e21 (PR #37, issuecomment 6066645198), NEW (MED): the request cost of the lost-tail search.

It read `bars.closed_bars(limit=window)` once PER CANDLE (a klines read of limit 1500 weighs 10) and the survival walk
re-read entry -> now the same way: up to ~4000 requests / 20-40k weight against Binance's 2400 / min IP limit. And the
deep window anchored at boot was never retired: a manual position after N candles of uptime made one cycle read N bars.
Now: ONE bars read per (symbol, side) search, evaluated locally per candle (Runner._signal_windows) - the boot search,
the guard search and the survival walk alike; and the deep journal-anchored search runs only until one boot search has
completed - a mid-run mismatch searches LOST_ENTRY_LOOKBACK candles."""
from decimal import Decimal as D

import pytest

from newcore.adapters import MemoryJournal
from newcore.domain import IntentState
from newcore.runner import InjectedSignals
from newcore.runner.runner import LOST_ENTRY_LOOKBACK
from slice_helpers import ACCOUNT_ID, H4, PORTFOLIO_ID, Crash, World, flat_bars

pytestmark = pytest.mark.usefixtures('journal_kind')
SYM = 'SOLUSDT'
T0 = flat_bars(1)[0].open_ms
SIDES = ('LONG', 'SHORT')


def at(bar):
    return T0 + (bar + 1) * H4


class CountingBars:
    def __init__(self, inner):
        self.inner, self.calls = inner, []

    def closed_bars(self, symbol, tf_ms, *, as_of_ms, limit):
        self.calls.append(limit)
        return self.inner.closed_bars(symbol, tf_ms, as_of_ms=as_of_ms, limit=limit)

    def __getattr__(self, name):
        return getattr(self.inner, name)


def boot_after_lost_tail(side, restart):
    sig = InjectedSignals({(SYM, at(1)): (('enter', side),), (SYM, at(3)): (('close', side),),
                           (SYM, at(6)): (('enter', side),)}, stop_atr=D('2'), window=50)
    w = World(flat_bars(restart + 10), sig, strict=False)
    w.run(5)
    w.port.crash(len(w.port.effects) + 1, 'after')
    w.venue.advance_to(w.close_ms(6))
    with pytest.raises(Crash):
        w.runner.cycle(w.close_ms(6))
    w.port.disarm()
    evs = w.journal.read()
    w.journal = MemoryJournal(ACCOUNT_ID, PORTFOLIO_ID)
    for e in evs[:len(evs) - 3]:
        w.journal.append(e)
    w.venue.advance_to(w.close_ms(restart - 1))
    w.bars = CountingBars(w.bars)
    w.runner = w.new_runner()
    w.run(restart, from_bar=restart)                                     # the boot cycle
    return w


@pytest.mark.parametrize('side', SIDES)
def test_the_boot_search_reads_the_bars_a_bounded_number_of_times_whatever_the_depth(side):
    near, far = boot_after_lost_tail(side, 20), boot_after_lost_tail(side, 200)
    for w in (near, far):
        lot, = w.runner.fold.open_lots()                                 # found, owned and protected
        assert lot.live_stop is not None and lot.live_stop.state is IntentState.WORKING
    assert len(far.bars.calls) == len(near.bars.calls) <= 8              # O(1) reads, never O(candles)
    assert max(far.bars.calls) > max(near.bars.calls)                    # the depth is in ONE read's limit


@pytest.mark.parametrize('side', SIDES)
def test_a_mid_run_foreign_position_triggers_no_deep_search(side):
    w = boot_after_lost_tail(side, 120)
    w.run(122)
    other = 'SHORT' if side == 'LONG' else 'LONG'
    w.venue.inject_position(SYM, other, D('1'), D('100'))               # a manual position, 116 candles into uptime
    w.bars.calls.clear()
    w.run(124)
    window = w.runner.signals.window
    assert w.bars.calls and max(w.bars.calls) <= window + LOST_ENTRY_LOOKBACK   # shallow: never the boot depth
    assert len(w.bars.calls) <= 12


@pytest.mark.parametrize('side', SIDES)
def test_a_search_deeper_than_one_bars_page_reads_bounded_pages(side):
    near, deep = boot_after_lost_tail(side, 20), boot_after_lost_tail(side, 1700)
    lot, = deep.runner.fold.open_lots()                                   # found 1694 candles back
    assert lot.live_stop is not None and lot.live_stop.state is IntentState.WORKING
    assert max(deep.bars.calls) == 1500                                    # the port's page limit
    assert len(deep.bars.calls) <= len(near.bars.calls) + 2                # one more page per deep read
