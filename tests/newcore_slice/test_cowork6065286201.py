"""Cowork's run on 85e2550 (PR #37, issuecomment 6065286201) - repros first:

NEW A  (verify on 9c81738, Cowork's scenario) a restart 9+ candles after the entry with the journal tail lost - partly
       or totally - in a NON-empty journal never ends naked: the entry is owned and protected.
1 MED  hard-HOLD sizing on a journaled side: the emergency stop and the emergency close were sized to the WHOLE venue
       position (ours 5 + foreign 2 -> a refused stop closed all 7). Now sized to our owned quantity: the journaled
       lots of that side plus the venue-confirmed fills of our own live opening intents; foreign quantity is never
       stopped or closed (A22).
2      an external FLAT close left the lot open with our reduce-only stop resting. With the external-close owner item
       + HOLD, our resting stop of that lot is now cancelled once the venue side is confirmed flat (the NEW-3 rule).
3      a PARTIAL external close raised nothing. Now: venue qty < our lot qty with no fill of ours in flight -> a durable
       'external partial close suspected' owner item, HOLD, and the lot's protection re-sized DOWN to the venue qty
       (the smaller stop is confirmed before the old one is cancelled; never above the lot).
4 INFO the guard with our lot mixed with a foreign add since the entry protects NOTHING on that side: a loud HOLD
       (disclosed in cmd_guard / _guard_proven and docs/newcore/slice/GUARD.md), pinned here."""
from decimal import Decimal as D

import pytest

from newcore.adapters import MemoryJournal
from newcore.domain import EntriesMode, IntentState
from newcore.runner import InjectedSignals, ids
from slice_helpers import ACCOUNT_ID, H4, PORTFOLIO_ID, Crash, World, flat_bars

pytestmark = pytest.mark.usefixtures('journal_kind')
SYM = 'SOLUSDT'
T0 = flat_bars(1)[0].open_ms
SIDES = ('LONG', 'SHORT')


def at(bar):
    return T0 + (bar + 1) * H4


def position(w, side):
    return sum((p.qty for p in w.venue.positions().value if p.side == side), D(0))


def stops(w, side):
    return [o for o in w.venue.open_orders().value if o.reduce and o.position_side == side
            and o.order_type == 'STOP_MARKET']


def covered(w, side):
    return sum((o.qty for o in stops(w, side)), D(0))


def closes(w, side):
    return [o for o in w.venue.orders_submitted() if o.order_type == 'MARKET' and o.reduce and o.position_side == side]


def external_reduce(w, side, qty):
    """A close done by hand on the venue (not one of our orders)."""
    w.venue._apply_fill(SYM, side, qty, D('100'), reduce=True, eoid='manual-close', at_ms=w.venue.now_ms, fee=D('0'))


# ------------------------------------------------------------------------------------------------------- NEW A
@pytest.mark.parametrize('side', SIDES)
@pytest.mark.parametrize('lost', (1, 2, 3))
@pytest.mark.parametrize('restart', (15, 20))
def test_new_a_a_restart_9_plus_candles_after_the_entry_with_a_lost_tail_is_never_naked(side, lost, restart):
    sig = InjectedSignals({(SYM, at(1)): (('enter', side),), (SYM, at(3)): (('close', side),),
                           (SYM, at(6)): (('enter', side),)}, stop_atr=D('2'))
    w = World(flat_bars(30), sig, strict=False)
    w.run(5)
    w.port.crash(len(w.port.effects) + 1, 'after')                       # the entry fills, the process dies
    w.venue.advance_to(w.close_ms(6))
    with pytest.raises(Crash):
        w.runner.cycle(w.close_ms(6))
    w.port.disarm()
    evs = w.journal.read()
    w.journal = MemoryJournal(ACCOUNT_ID, PORTFOLIO_ID)
    for e in evs[:len(evs) - lost]:
        w.journal.append(e)
    w.venue.advance_to(w.close_ms(restart - 1))
    w.runner = w.new_runner()
    w.run(restart + 2, from_bar=restart)
    r, pos = w.runner, position(w, side)
    lot, = r.fold.open_lots()
    assert pos > 0 and lot.qty == pos and lot.live_stop is not None and lot.live_stop.state is IntentState.WORKING
    assert covered(w, side) == pos and r.counters.unprotected_cycles == 0


# ------------------------------------------------------------------------------------------------------- 1
def hard_hold_with_foreign(side):
    w = World(flat_bars(20), InjectedSignals({(SYM, at(5)): (('enter', side),)}, stop_atr=D('2')), strict=False)
    w.run(6)
    lot, = w.runner.fold.open_lots()
    assert lot.qty == D('5')
    w.venue.inject_position(SYM, side, D('2'), D('100'))                 # a foreign add on the same side
    w.venue.external_cancel(lot.live_stop.intent.client_order_id)       # our stop is gone ...
    w.journal.fail_writes(10 ** 9)
    w.runner.store_unavailable('test: ENOSPC')                           # ... and the store is down
    return w


@pytest.mark.parametrize('side', SIDES)
def test_1_the_hard_hold_emergency_stop_covers_only_our_quantity(side):
    w = hard_hold_with_foreign(side)
    w.run(7)
    em = [o for o in stops(w, side) if ids.is_emergency_client_id(o.ref.client_id)]
    assert [o.qty for o in em] == [D('5')] and position(w, side) == D('7')


@pytest.mark.parametrize('side', SIDES)
@pytest.mark.parametrize('code', (-2010, -2021))
def test_1_a_refused_emergency_stop_closes_only_our_quantity(side, code):
    w = hard_hold_with_foreign(side)
    w.port.refuse('stop', code)
    w.run(7)
    assert [o.qty for o in closes(w, side)] == [D('5')]
    assert position(w, side) == D('2')                                   # the foreign 2 is never closed (A22)


# ------------------------------------------------------------------------------------------------------- 2
@pytest.mark.parametrize('side', SIDES)
def test_2_an_external_flat_close_cancels_our_resting_stop_with_the_owner_item(side):
    w = World(flat_bars(20), InjectedSignals({(SYM, at(5)): (('enter', side),)}, stop_atr=D('2')), strict=False)
    w.run(6)
    lot, = w.runner.fold.open_lots()
    external_reduce(w, side, lot.qty)                                    # closed by hand, our stop still resting
    assert position(w, side) == 0 and len(stops(w, side)) == 1
    w.run(9)
    r = w.runner
    assert stops(w, side) == []                                          # our stop no longer rests on a flat side
    assert r.fold.mode is EntriesMode.HOLD
    assert r.journal.find_decision(ids.marker_decision_id('external_close', lot.lot_id)) is not None
    assert any('external close suspected' in t for _, t in r.incidents)
    assert not closes(w, side)                                           # nothing else sent for the lot


# ------------------------------------------------------------------------------------------------------- 3
@pytest.mark.parametrize('side', SIDES)
def test_3_a_partial_external_close_is_ownership_unknown_and_protection_is_kept(side):
    """Cowork 6073089838 supersedes the resize-down here: a reduce-only close whose client id is not ours is not a
    PROVEN foreign order (it may be the child of our own triggered algo stop carrying a system id) - ownership
    UNKNOWN: the resting reduce-only stop is kept (it cannot over-close), nothing sent, HOLD, loud."""
    w = World(flat_bars(20), InjectedSignals({(SYM, at(5)): (('enter', side),)}, stop_atr=D('2')), strict=False)
    w.run(6)
    lot, = w.runner.fold.open_lots()
    external_reduce(w, side, D('2'))                                     # 5 -> 3 by hand
    w.run(9)
    r = w.runner
    assert r.fold.mode is EntriesMode.HOLD
    assert any('not a proven opening order' in t for _, t in r.incidents)
    assert lot.lot_id in r._own_unknown
    assert [o.qty for o in stops(w, side)] == [D('5')]                   # resting protection kept, never resized
    assert position(w, side) == D('3') and not closes(w, side)
    n = len(w.venue.orders_submitted())
    w.run(11)
    assert len(w.venue.orders_submitted()) == n                          # settled: nothing more sent


# ------------------------------------------------------------------------------------------------------- 4
def test_4_the_guard_protects_nothing_when_our_lot_is_mixed_with_a_foreign_add(tmp_path):
    import json
    from newcore.runner import app as A
    from newcore.runner import config as C
    from test_run_cli import cfg_file, run
    cfg = cfg_file(tmp_path)
    assert run(['run', '--config', cfg, '--cycles', '27', '--enable-candidate'])[0] == 0      # an open BTC lot
    d = tmp_path / 'nc' / C.load(cfg).account_id
    st = json.loads((d / A.STATE_FILE).read_text())
    for o in st['orders']:
        if o['type'] == 'STOP_MARKET':
            o['status'] = 'CANCELED'                                    # our stop is gone
    st['positions'][0][2] = str(D(st['positions'][0][2]) + D('0.5'))
    st['fills'].append(['999999', 'manual-eoid', 'BTCUSDT', 'LONG', '0.5', '40000', '0', 'USDT', '0', False,
                        st['fills'][-1][10] + 1])                       # a foreign add AFTER our entry
    (d / A.STATE_FILE).write_text(json.dumps(st))
    seg = sorted(p for p in (d / 'journal').iterdir() if p.name.endswith('.seg'))[-1]
    raw = bytearray(seg.read_bytes())
    raw[len(raw) // 2] ^= 0xFF
    seg.write_bytes(bytes(raw))
    code, out = run(['run', '--config', cfg, '--cycles', '5', '--enable-candidate'])
    assert code == A.EXIT_STORE_HOLD and 'GUARD' in out and 'ambiguous' in out                # loud
    st = json.loads((d / A.STATE_FILE).read_text())
    assert not [o for o in st['orders'] if o['status'] == 'NEW']                              # nothing placed
