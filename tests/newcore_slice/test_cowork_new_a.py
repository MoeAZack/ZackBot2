"""Cowork 6064553738 NEW A (MED): runtime recovery looked back only LOST_ENTRY_LOOKBACK=3 candles. With a NON-empty
journal whose tail was lost (an earlier closed trade survives), a restart 4+ candles after the entry left the position
naked in HOLD. Now the runtime recovery searches the guard's bounded window (GUARD_ENTRY_LOOKBACK) for the strategy's
deterministic entry ids and adopts a found entry only under the Codex P1-1 provenance rule: every venue trade of that
side since the entry fill is ours and the surviving net is the WHOLE entry. Anything else (a foreign trade, the entry
already exited, a partial own exit whose records were lost too) -> nothing adopted, an 'ambiguous' incident, the
reconciliation's untracked-position HOLD (owner item)."""
from decimal import Decimal as D

import pytest

from newcore.adapters import MemoryJournal
from newcore.domain import EntriesMode, IntentState
from newcore.ports.venue import VenueFill  # noqa: F401
from newcore.runner import InjectedSignals
from slice_helpers import ACCOUNT_ID, H4, PORTFOLIO_ID, Crash, World, flat_bars

pytestmark = pytest.mark.usefixtures('journal_kind')
SYM = 'SOLUSDT'
T0 = flat_bars(1)[0].open_ms
SIDES = ('LONG', 'SHORT')


def at(bar):
    return T0 + (bar + 1) * H4


def position(w, side):
    return sum((p.qty for p in w.venue.positions().value if p.side == side), D(0))


def covered(w, side):
    return sum((o.qty for o in w.venue.open_orders().value if o.reduce and o.position_side == side), D(0))


def lost_after_entry(side, entry_bar=6, lost=3, effect=1):
    """An earlier trade (enter 1, close 3) survives in the journal; the entry at entry_bar fills, the process dies
    right after it, and the store loses the last `lost` events."""
    sig = InjectedSignals({(SYM, at(1)): (('enter', side),), (SYM, at(3)): (('close', side),),
                           (SYM, at(entry_bar)): (('enter', side),)}, stop_atr=D('2'))
    w = World(flat_bars(30), sig, strict=False)
    w.run(entry_bar - 1)
    assert position(w, side) == 0 and w.journal.read()                 # the earlier trade is closed and journaled
    w.port.crash(len(w.port.effects) + effect, 'after')
    t = w.close_ms(entry_bar)
    w.venue.advance_to(t)
    with pytest.raises(Crash):
        w.runner.cycle(t)
    w.port.disarm()
    evs = w.journal.read()
    w.journal = MemoryJournal(ACCOUNT_ID, PORTFOLIO_ID)
    for e in evs[:len(evs) - lost]:
        w.journal.append(e)
    return w


@pytest.mark.parametrize('side', SIDES)
@pytest.mark.parametrize('restart', (9, 10, 14))
def test_new_a_a_restart_4_or_more_candles_later_owns_and_protects_the_lost_entry(side, restart):
    w = lost_after_entry(side)
    w.venue.advance_to(w.close_ms(restart - 1))
    w.runner = w.new_runner()
    w.run(restart + 3, from_bar=restart)
    r = w.runner
    pos = position(w, side)
    lot, = r.fold.open_lots()
    assert pos > 0 and lot.qty == pos and lot.live_stop is not None and lot.live_stop.state is IntentState.WORKING
    assert covered(w, side) == pos and r.fold.mode is EntriesMode.ACTIVE


@pytest.mark.parametrize('side', SIDES)
def test_new_a_a_foreign_trade_since_the_entry_is_not_adopted(side):
    """Same, but a manual same-side trade landed after our entry: provenance is ambiguous - nothing adopted, the
    untracked-position HOLD with an owner item, an 'ambiguous' incident, nothing sent."""
    w = lost_after_entry(side)
    w.venue.inject_position(SYM, side, D('2'), D('100'))                 # a foreign fill after our entry
    w.venue.advance_to(w.close_ms(9))
    w.runner = w.new_runner()
    n = len(w.venue.orders_submitted())
    w.run(12, from_bar=10)
    r = w.runner
    assert not r.fold.open_lots() and r.fold.mode is EntriesMode.HOLD
    assert any('ambiguous' in t for _, t in r.incidents)
    assert len(w.venue.orders_submitted()) == n


@pytest.mark.parametrize('side', SIDES)
def test_new_a_an_entry_already_exited_never_proves_a_later_position(side):
    """NEW B / Codex P1-1 at runtime: our lost entry was closed by its own stop (records lost too); a later foreign
    position on that side is never adopted from the old entry's id."""
    w = lost_after_entry(side, lost=3)
    stop = next(o for o in w.venue.open_orders().value if o.reduce and o.position_side == side) \
        if covered(w, side) else None
    assert stop is None                                                  # crash after the entry: no stop yet
    w.venue._positions.pop((SYM, side), None)                            # flattened by an exit of ours we cannot see
    w.venue._fills.append(VenueFill(trade_id='x-1', exchange_order_id='unknown-exit', symbol=SYM, position_side=side,
                                    qty=D('5'), price=D('100'), fee=D('0'), fee_asset='USDT', realized_pnl=D('0'),
                                    maker=False, at_ms=w.close_ms(7)))
    w.venue.inject_position(SYM, side, D('3'), D('100'))
    w.venue.advance_to(w.close_ms(9))
    w.runner = w.new_runner()
    n = len(w.venue.orders_submitted())
    w.run(12, from_bar=10)
    r = w.runner
    assert not r.fold.open_lots() and r.fold.mode is EntriesMode.HOLD
    assert len(w.venue.orders_submitted()) == n


@pytest.mark.parametrize('side', SIDES)
@pytest.mark.parametrize('restart', (70, 90))
def test_new_a_beyond_60_candles_the_search_is_anchored_to_the_last_durable_event(side, restart):
    """Cowork (c4eb46d run): a restart 70+ candles after a lost 3-event tail (crash after the entry) left the position
    naked in HOLD - the search stopped at GUARD_ENTRY_LOOKBACK=60 from now. It now reaches back to the journal's last
    durable event at boot (the lost tail is a suffix), so the entry is found, owned and protected."""
    sig = InjectedSignals({(SYM, at(1)): (('enter', side),), (SYM, at(3)): (('close', side),),
                           (SYM, at(6)): (('enter', side),)}, stop_atr=D('2'))
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
    w.runner = w.new_runner()
    w.run(restart + 2, from_bar=restart)
    r, pos = w.runner, position(w, side)
    lot, = r.fold.open_lots()
    assert pos > 0 and lot.qty == pos and lot.live_stop is not None and lot.live_stop.state is IntentState.WORKING
    assert covered(w, side) == pos and r.fold.mode is EntriesMode.ACTIVE
