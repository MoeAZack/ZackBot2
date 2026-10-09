"""Cowork on de0532c (PR #37, issuecomment 6067083903) - repros first:

1 adv6 E: the store down (a write fails mid-cycle - nobody calls store_unavailable) + our stop vanished, every refusal
  code: the runner IS in hard HOLD, but `fold.mode` cannot record it (no ModeChanged can be journaled) and read ACTIVE.
  Runner.mode / Runner.hold are the EFFECTIVE mode (HOLD / DURABILITY_UNAVAILABLE in a hard HOLD), and the central table
  (_permits) and the entry gate use them. The exposure is emergency-protected or emergency-closed (A24 + P1-3); with the
  close refused too it is loud: an I1 incident every cycle, protected=NO, never ACTIVE.
2 adv6 M: the venue raises during a trailing move -> strict BREACH I3 (two unconfirmed replacement stops: the older one
  was carried by no record). A trail move never stacks on an unconfirmed replacement: the old stop keeps carrying (never
  loosened, never unprotected), an incident, and the move goes on once the venue answers.
3 the raising-adapter guard catches Exception only: KeyboardInterrupt / SystemExit / GeneratorExit (and a test's
  simulated process death) always propagate."""
from decimal import Decimal as D

import pytest

from newcore.domain import EntriesMode, HoldKind
from newcore.runner import InjectedSignals
from newcore.runner.managed import SyntheticPlans
from newcore.runner.reports import health_line
from mg_helpers import ZERO_COSTS, MgWorld, path, signals
from slice_helpers import H4, ScriptedVenue, World, flat_bars

pytestmark = pytest.mark.usefixtures('journal_kind')
SYM = 'SOLUSDT'
T0 = flat_bars(1)[0].open_ms
SIDES = ('LONG', 'SHORT')


def position(w, side):
    return sum((p.qty for p in w.venue.positions().value if p.side == side), D(0))


def cover(w, side):
    return sum((o.qty for o in w.venue.open_orders().value if o.reduce and o.position_side == side), D(0))


class Raising(ScriptedVenue):
    raising = frozenset()
    exc = ConnectionError

    def _effect(self, name, call, ref, kind):
        if kind in self.raising:
            self.effects.append(name)
            raise self.exc('venue adapter raised')
        return super()._effect(name, call, ref, kind)

    def query(self, ref):
        if 'query' in self.raising:
            raise self.exc('venue adapter raised (query)')
        return super().query(ref)


# ------------------------------------------------------------------------------------------------------- 1
@pytest.mark.parametrize('side', SIDES)
@pytest.mark.parametrize('code', (None, -2010, -2021, -2022, -4131, -1013))
@pytest.mark.parametrize('close_refused', (False, True))
def test_1_store_down_mid_cycle_and_stop_vanished_is_hard_hold_never_active(side, code, close_refused):
    w = World(flat_bars(20), InjectedSignals({(SYM, T0 + 6 * H4): (('enter', side),)}, stop_atr=D('2')), strict=False)
    w.run(6)
    lot, = w.runner.fold.open_lots()
    w.journal.fail_writes(10 ** 9)                                       # the store fails; nobody tells the runner
    w.venue.external_cancel(lot.live_stop.intent.client_order_id)
    if code is not None:
        w.port.refuse('stop', code)
        if close_refused:
            w.port.refuse('reduce', code)
    w.run(9)
    r = w.runner
    assert r.hard_hold and r.mode is EntriesMode.HOLD and r.hold is HoldKind.DURABILITY_UNAVAILABLE
    assert 'mode=HOLD' in health_line(r)
    assert [o for o in w.venue.orders_submitted() if not o.reduce].__len__() == 1    # no new entry
    pos = position(w, side)
    if pos > cover(w, side):                                             # naked only when stop AND close refused
        assert close_refused and code is not None
        assert 'protected=NO' in health_line(r)
        assert sum(1 for _, t in r.incidents if t.startswith('I1')) >= 2    # every cycle, loud
    else:
        assert pos == 0 or cover(w, side) >= pos


# ------------------------------------------------------------------------------------------------------- 2
TRAIL = SyntheticPlans(costs=ZERO_COSTS, add_r=None, tp1_r=None, tp2_r=None, be_after_tp1=False,
                       time_exit_candles=None, trail_r=D('0.5'))
TRAIL_BARS = {6: (100, 101.2, 99.9, 101), 7: (101, 102.2, 100.9, 102), 8: (102, 103.2, 101.9, 103)}


@pytest.mark.parametrize('side', SIDES)
@pytest.mark.parametrize('what', (('stop',), ('cancel',), ('stop', 'cancel'), ('query',)))
def test_2_a_venue_raising_during_a_trail_move_keeps_the_old_stop_and_never_breaches(side, what):
    w = MgWorld(path(20, TRAIL_BARS, side), signals(side), plans=TRAIL, strict=True)
    w.port = Raising(w.venue)
    w.runner = w.new_runner()
    w.run(6)
    old = [o.stop_price for o in w.venue.open_orders().value if o.reduce]
    w.port.raising = frozenset(what)
    w.run(8)                                                             # strict: no InvariantBreach
    levels = [o.stop_price for o in w.venue.open_orders().value if o.reduce]
    assert cover(w, side) >= position(w, side) == D('5')                 # never unprotected
    better = (lambda a, b: a >= b) if side == 'LONG' else (lambda a, b: a <= b)
    assert all(better(x, old[0]) for x in levels)                       # never loosened
    if 'stop' in what:
        assert any('deferred' in t or 'raised' in t for _, t in w.runner.incidents)
    w.port.raising = frozenset()
    w.run(11)
    final = [o.stop_price for o in w.venue.open_orders().value if o.reduce]
    assert len(final) == 1 and better(final[0], old[0]) and final[0] != old[0]   # the trail went on, one stop


# ------------------------------------------------------------------------------------------------------- 3
@pytest.mark.parametrize('exc', (KeyboardInterrupt, SystemExit, GeneratorExit))
@pytest.mark.parametrize('where', ('stop', 'query'))
def test_3_the_raising_guard_never_swallows_a_base_exception(exc, where):
    w = World(flat_bars(20), InjectedSignals({(SYM, T0 + 6 * H4): (('enter', 'LONG'),)}, stop_atr=D('2')),
              strict=False)
    w.port = Raising(w.venue)
    w.port.exc = exc
    w.runner = w.new_runner()
    w.port.raising = frozenset((where,))
    with pytest.raises(exc):
        w.run(8)


def test_3_the_guard_catches_exception_only():
    import inspect
    from newcore.runner import runner as RUN
    src = inspect.getsource(RUN._SafeVenue)
    assert 'except Exception' in src and 'BaseException' not in src.split('except', 1)[1].split('\n', 1)[0]
