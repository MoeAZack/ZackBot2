"""Cowork 6061109158 (PR #37), the remaining items - each a failing repro first:

MED-2  HOLD + a trailing plan: every bar placed another full-size stop (the protection cancel is held in HOLD), 18 stops
       by bar 23. Now: in HOLD a lot already covered by a confirmed stop keeps it - a trail move creates no stop; the
       newest move is sent on resume (and the old stop released once it is confirmed); never more than one live stop
       per lot while HOLD lasts.
MED-3  a position closed OUTSIDE the bot together with its stop: a phantom lot, a refused PROTECT and a refused CLOSE
       every cycle (90 intents in 21 cycles). Now: after REDUCE_REFUSALS refused reduce-only sends of a lot the runner
       reads the venue position; flat on that side -> a durable owner item ('external close suspected') + HOLD, and no
       more sends for that lot.
LOW-6  two NOT_FOUND answers latched HOLD on a working stop. A stop the venue LISTS in its open orders is working,
       whatever a by-id lookup answers: re-queried, never booked unknown, no HOLD."""
from decimal import Decimal as D

import pytest

from newcore.domain import Action, EntriesMode, IntentState, ReasonCode
from newcore.runner import InjectedSignals
from newcore.runner.managed import SyntheticPlans
from mg_helpers import ZERO_COSTS, MgWorld, path, signals as mg_signals
from slice_helpers import H4, World, flat_bars

pytestmark = pytest.mark.usefixtures('journal_kind')
SYM = 'SOLUSDT'
T0 = flat_bars(1)[0].open_ms
SIDES = ('LONG', 'SHORT')


def stops_resting(w, side):
    return [o for o in w.venue.open_orders().value if o.order_type == 'STOP_MARKET' and o.position_side == side]


# ------------------------------------------------------------------------------------------------------- MED-2
@pytest.mark.parametrize('side', SIDES)
def test_med2_a_trailing_plan_in_hold_piles_no_stops(side):
    plans = SyntheticPlans(costs=ZERO_COSTS, add_r=None, tp1_r=None, tp2_r=None, be_after_tp1=False,
                           time_exit_candles=None, trail_r=D('0.5'))
    up = {i: (100 + (i - 6), 101 + (i - 6) + 0.2, 99.9 + (i - 6), 101 + (i - 6)) for i in range(6, 16)}
    w = MgWorld(path(24, up, side), mg_signals(side), plans=plans)
    w.run(5)
    w.runner._hold([ReasonCode.RECONCILE_UNRECONCILED])
    for i in range(6, 15):
        w.run(i)
        assert len(stops_resting(w, side)) == 1                    # never a second stop while HOLD lasts
    r = w.runner
    assert r.counters.unprotected_cycles == 0
    assert r.resume(w.close_ms(14))
    w.run(16)
    assert len(stops_resting(w, side)) == 1                        # the newest trail level, the old one released
    lot, = w.runner.fold.open_lots()
    first = lot.protects[0].intent.stop_price
    assert (lot.carrier.intent.stop_price > first) if side == 'LONG' else (lot.carrier.intent.stop_price < first)


# ------------------------------------------------------------------------------------------------------- MED-3
def _flatten_outside(w, side):
    lot, = w.runner.fold.open_lots()
    w.venue.external_cancel(lot.live_stop.intent.client_order_id)
    w.venue._positions.pop((SYM, side), None)                      # closed on the exchange UI, stop included


def _reduce_sends(w):
    return [o for o in w.venue.orders_submitted() if o.reduce]


@pytest.mark.parametrize('side', SIDES)
@pytest.mark.parametrize('managed', (False, True))
def test_med3_an_external_close_is_an_owner_item_not_a_send_loop(side, managed):
    if managed:
        w = MgWorld(path(30, {}, side), mg_signals(side), strict=False)
    else:
        sig = InjectedSignals({(SYM, T0 + 6 * H4): (('enter', side),)}, stop_atr=D('2'))
        w = World(flat_bars(30), sig, strict=False)
    w.run(7)
    n0 = len(_reduce_sends(w))
    _flatten_outside(w, side)
    w.run(28)                                                      # 21 cycles
    r = w.runner
    sends = len(_reduce_sends(w)) - n0
    assert sends <= 4                                              # bounded, never a loop (was ~2 per cycle)
    assert r.fold.mode is EntriesMode.HOLD
    assert any('external close suspected' in t for _, t in r.incidents)
    items = [d for d in r.fold.decisions.values() if d.action is Action.WAIT and 'external close' in d.detail]
    assert len(items) == 1                                         # one durable owner item
    w2 = w.restart()                                               # durable: a restart does not start sending again
    n1 = len(_reduce_sends(w))
    w.run(29)
    assert len(_reduce_sends(w)) == n1 and w2.fold.mode is EntriesMode.HOLD


# ------------------------------------------------------------------------------------------------------- LOW-6
@pytest.mark.parametrize('side', SIDES)
def test_low6_a_listed_stop_is_working_whatever_a_not_found_lookup_says(side):
    sig = InjectedSignals({(SYM, T0 + 6 * H4): (('enter', side),)}, stop_atr=D('2'))
    w = World(flat_bars(20), sig)
    w.run(6)
    lot, = w.runner.fold.open_lots()
    w.venue.not_found(lot.live_stop.intent.client_order_id, 2)      # two lagging lookups
    w.run(9)
    r = w.runner
    lot, = r.fold.open_lots()
    assert lot.live_stop.state is IntentState.WORKING and r.fold.mode is EntriesMode.ACTIVE
    assert r.counters.unprotected_cycles == 0 and len(stops_resting(w, side)) == 1
