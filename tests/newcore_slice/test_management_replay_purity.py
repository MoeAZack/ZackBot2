"""Codex P1 (review of 7f35c5c): a restart folds the management drivers from JOURNAL BYTES ONLY - no bars, no venue read.

P1-a: a tick journals the whole candle ('mg tick <open> <o> <h> <l> <c> <request>'); P1-b: the entry fee and every
fill a driver books are journaled first ('mg start ...', one 'mg fill ...' per venue trade), and an unreadable fill is
never booked as a zero (the input stays pending; an unreadable ENTRY fee leaves the lot to the runner under HOLD).
Every restart below builds the runner over a venue and a bar source that RAISE on any call during the boot (or answer
hostile, changed data) and must land on the identical driver state."""

import pytest

from newcore.domain import Action, EntriesMode, Purpose
from newcore.ports.bars import Bar
from newcore.ports.venue import ReadKind, ReadOutcome, VenueFill
from newcore.runner.intrabar import play_candle
from newcore.runner.managed import ManagedRunner, SyntheticPlans
from mg_helpers import ZERO_COSTS, MgWorld, intents_of, path, signals

pytestmark = pytest.mark.usefixtures('journal_kind')
SIDES = ('LONG', 'SHORT')
FLOW = {6: (100, 100.2, 98.9, 99), 7: (99, 99.2, 98.9, 99), 8: (99, 102.2, 98.9, 102)}
PLANS = SyntheticPlans(costs=ZERO_COSTS, time_exit_candles=8)


class Raises:
    """A port that must not be touched (the boot fold reads nothing)."""

    def __init__(self, name):
        self.name = name

    def __getattr__(self, attr):
        if attr in ('window', 'name', 'tf_label', 'version'):
            raise AttributeError(attr)

        def boom(*a, **k):
            raise AssertionError(f'{self.name}.{attr} called during the boot fold')
        return boom


class Hostile:
    """A venue whose fills / bars now say something else (a changed exchange answer)."""

    def __init__(self, inner, *, fills=None, bars=None):
        self.inner, self._fills, self._bars = inner, fills, bars

    def __getattr__(self, attr):
        return getattr(self.inner, attr)

    def fills(self, symbol, eoid):
        r = self.inner.fills(symbol, eoid)
        if self._fills == 'unknown':
            return ReadOutcome(kind=ReadKind.UNKNOWN, observed_at_ms=r.observed_at_ms, detail='timeout')
        if self._fills == 'changed':
            return ReadOutcome(kind=ReadKind.OK, observed_at_ms=r.observed_at_ms, value=tuple(
                VenueFill(trade_id=f'x{f.trade_id}', exchange_order_id=f.exchange_order_id, symbol=f.symbol,
                          position_side=f.position_side, qty=f.qty, price=f.price * 2, fee=f.fee * 9,
                          fee_asset=f.fee_asset, realized_pnl=f.realized_pnl, maker=f.maker, at_ms=f.at_ms)
                for f in r.value))
        return r

    def closed_bars(self, symbol, tf_ms, *, as_of_ms, limit):
        r = self.inner.closed_bars(symbol, tf_ms, as_of_ms=as_of_ms, limit=limit)
        if self._bars == 'unknown':
            return ReadOutcome(kind=ReadKind.UNKNOWN, observed_at_ms=as_of_ms, detail='timeout')
        if self._bars == 'changed' and r.kind is ReadKind.OK:
            return ReadOutcome(kind=ReadKind.OK, observed_at_ms=r.observed_at_ms, value=tuple(
                Bar(open_ms=b.open_ms, close_ms=b.close_ms, open=b.open * 3, high=b.high * 3, low=b.low * 3,
                    close=b.close * 3, volume=b.volume) for b in r.value))
        return r


def boot(w, venue, bars):
    w.journal = w.journal.reopen()
    return ManagedRunner(w.config, journal=w.journal, venue=venue, bars=bars, signals=w.signals,
                         account_reads=venue, management=w.management)


def world(side, **kw):
    return MgWorld(path(24, FLOW, side), signals(side), plans=PLANS, **kw)


@pytest.mark.parametrize('side', SIDES)
@pytest.mark.parametrize('intrabar', (False, True))
def test_restart_after_every_cycle_reads_nothing(side, intrabar):
    """Ticks, fills, marks, the start: a boot over ports that raise on ANY call folds the identical driver state."""
    w = world(side)
    for i in range(17):
        t = w.close_ms(i)
        if intrabar:
            play_candle(w.venue, w.runner, t)
        else:
            w.venue.advance_to(t)
        w.runner.cycle(t)
        old = w.runner
        new = boot(w, Raises('venue'), Raises('bars'))
        assert new.mg == old.mg and new.plans == old.plans and new.unmanaged == old.unmanaged
        w.runner = ManagedRunner(w.config, journal=w.journal, venue=w.port, bars=w.bars, signals=w.signals,
                                 account_reads=w.venue, management=w.management)
    r = w.runner
    assert not r.fold.open_lots() and r.trades() and r.counters.unprotected_cycles == 0


@pytest.mark.parametrize('side', SIDES)
@pytest.mark.parametrize('hostile', ('bars_unknown', 'bars_changed', 'fills_unknown', 'fills_changed'))
def test_codex_repro_restart_with_offline_or_changed_ports_is_identical(side, hostile):
    """Two managed ticks (and the add / resize fills), then a restart whose bars / fills are unknown or now say
    something else: the boot neither raises nor changes the state (it never asks)."""
    w = world(side)
    w.run(8)
    old = w.runner
    kind, what = hostile.split('_')
    h = Hostile(w.port, **{kind: what}) if kind == 'fills' else w.port
    bars = Hostile(w.bars, bars=what) if kind == 'bars' else w.bars
    new = boot(w, h, bars)
    assert new.mg == old.mg and new.plans == old.plans
    ticks = [d for d in new.fold.decisions.values() if d.action is Action.WAIT and d.detail.startswith('mg tick ')]
    assert len(ticks) >= 2 and all(len(d.detail.split(' ')) == 9 for d in ticks)     # the whole candle is recorded


@pytest.mark.parametrize('side', SIDES)
def test_unreadable_fills_stay_pending_then_book_when_readable(side):
    """The TP1 reduce fills but its fills cannot be read: nothing is booked (no 'mg fill', the driver waits); a restart
    in between changes nothing; once readable, the rows are journaled and booked - never as a zero."""
    w = world(side)
    w.run(7)
    w.port = Hostile(w.port.inner if hasattr(w.port, 'inner') else w.port, fills='unknown')
    w.runner.venue = w.port
    w.run(8)
    r = w.runner
    tp1, = [iv for iv in intents_of(r, Purpose.REDUCE)]
    assert tp1.executed > 0
    lot_id, = r.mg
    ds = r.mg[lot_id]
    b = next(x for x in ds.bindings if x.intent_id == tp1.intent_id)
    assert b.filled == 0 and b.executed == tp1.executed                  # executed at the venue, not booked
    assert r.fold.mode is EntriesMode.HOLD                               # fail closed while it is pending
    fills = [d for d in r.fold.decisions.values() if d.detail.startswith('mg fill ') and tp1.intent_id in d.evidence]
    assert not fills
    old = r
    again = boot(w, Raises('venue'), Raises('bars'))
    assert again.mg == old.mg
    w.port = w.port.inner                                                 # readable again
    w.runner = ManagedRunner(w.config, journal=w.journal, venue=w.port, bars=w.bars, signals=w.signals,
                             account_reads=w.venue, management=w.management)
    w.run(9)
    r = w.runner
    fills = [d for d in r.fold.decisions.values() if d.detail.startswith('mg fill ') and tp1.intent_id in d.evidence]
    assert len(fills) == 1
    assert all(b.intent_id != tp1.intent_id for b in r.mg[lot_id].bindings)   # booked and retired
    assert r.counters.unprotected_cycles == 0


@pytest.mark.parametrize('side', SIDES)
def test_an_unreadable_entry_fee_leaves_the_lot_to_the_runner_under_hold(side):
    w = world(side)
    w.run(4)
    inner = w.port
    w.port = Hostile(inner, fills='unknown')
    w.runner.venue = w.port
    w.run(5)                                                              # the entry fills; its fee is unreadable
    r = w.runner
    lot, = r.fold.open_lots()
    assert lot.lot_id not in r.mg and lot.lot_id in r.unmanaged
    assert not [d for d in r.fold.decisions.values() if d.detail.startswith('mg start ')]
    assert lot.live_stop is not None and not r.fold.decisions[lot.live_stop.intent.decision_id].detail.startswith('mg ')
    assert r.fold.mode is EntriesMode.HOLD and r.counters.unprotected_cycles == 0
    again = boot(w, Raises('venue'), Raises('bars'))
    assert lot.lot_id not in again.mg and not again._pending_start          # the runner's lot, durably
