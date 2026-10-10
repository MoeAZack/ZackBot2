"""M4 intra-candle marks (newcore/runner/intrabar.py + Runner.mark / intrabar_sync + FakeVenue intra-candle play).

A bot-side target triggers AT its level inside the candle (zb-path/1), and the venue fills the market order at that
mark; a gap through the level fills at the open; a stop inside the candle's range blocks the targets of that candle
(stop first); a stop triggered mid-candle is read at once, so a later level sees the flat position. Marks that fire
are journaled first ('mg mark <price>'), so a crash at any journal boundary and a restart after every candle fold to
the same driver state and the same trade. Testnet: Runner.mark from a polled mark price (Session.poll_marks)."""
from decimal import Decimal as D

import pytest

from newcore.domain import Action, EntriesMode, Purpose, ReasonCode
from newcore.runner.intrabar import play_candle
from newcore.runner.managed import SyntheticPlans
from mg_helpers import ZERO_COSTS, MgWorld, intents_of, path, signals
from slice_helpers import T0, Crash

pytestmark = pytest.mark.usefixtures('journal_kind')
SIDES = ('LONG', 'SHORT')
PLANS = SyntheticPlans(costs=ZERO_COSTS, add_r=None, tp1_r=None, tp2_r=D('1'), be_after_tp1=False,
                       time_exit_candles=8)
# long frame, entry 100.02 (fills at bar 6's open), stop 98.02, target avg + 1R = 102.02
TOUCH = {7: (100, 102.5, 99.9, 101)}          # the high touches the target, the close does not
GAP = {7: (103, 103.5, 102.8, 103)}           # opens through the target: fills at the open
BOTH = {7: (100, 102.5, 97.5, 101)}           # the stop lies inside the range: stop first, no target


def mirror(side, p):
    p = D(str(p))
    return p if side == 'LONG' else D(200) - p


def world(side, bars, **kw):
    return MgWorld(path(24, bars, side), signals(side), plans=PLANS, **kw)


def play(w, upto, hooks=None):
    hooks = hooks or {}
    start = max(0, (w.venue.now_ms - w.t0) // w.config.tf_ms)
    for i in range(start, upto + 1):
        t = w.close_ms(i)
        if i in hooks:
            hooks[i](w)
        try:
            play_candle(w.venue, w.runner, t)
            w.runner.cycle(t)
        except Crash:
            w.port.disarm()
            w.restart()
            if w.venue.open_candle():                   # the crash fell inside the walk: the new process resumes it
                play_candle(w.venue, w.runner, t)
            w.runner.cycle(t)
    return w.runner


@pytest.mark.parametrize('side', SIDES)
def test_a_target_touched_inside_the_candle_fills_at_its_level(side):
    w = world(side, TOUCH)
    r = play(w, 9)
    t, = r.trades()
    assert t.exit_reason is ReasonCode.EXIT_TAKE_PROFIT
    plan, = r.plans.values()
    level = plan.entry_price + (1 if side == 'LONG' else -1) * plan.tp2_offset
    slip = w.venue.costs.slip
    assert t.exit_price == (level * (1 - slip) if side == 'LONG' else level * (1 + slip))
    assert t.exit_ms == w.close_ms(6)                                   # filled inside candle 7 (its open time)
    marks = [d for d in r.fold.decisions.values() if d.action is Action.WAIT and d.detail.startswith('mg mark ')]
    assert len(marks) == 1 and D(marks[0].detail.split(' ')[2]) == level
    assert r.counters.unprotected_cycles == 0 and not w.venue.open_orders().value


@pytest.mark.parametrize('side', SIDES)
def test_the_close_only_clock_misses_the_touch(side):
    """The same candle on the candle-close clock (advance_to + cycle): no trigger, the trade stays open."""
    w = world(side, TOUCH)
    w.run(9)
    assert w.runner.fold.open_lots() and not intents_of(w.runner, Purpose.REDUCE)


@pytest.mark.parametrize('side', SIDES)
def test_a_gap_through_the_target_fills_at_the_open(side):
    w = world(side, GAP)
    r = play(w, 9)
    t, = r.trades()
    slip = w.venue.costs.slip
    o = mirror(side, 103)
    assert t.exit_reason is ReasonCode.EXIT_TAKE_PROFIT
    assert t.exit_price == (o * (1 - slip) if side == 'LONG' else o * (1 + slip))


@pytest.mark.parametrize('side', SIDES)
def test_a_stop_inside_the_range_blocks_the_target_of_that_candle(side):
    w = world(side, BOTH)
    r = play(w, 9)
    t, = r.trades()
    assert t.exit_reason is ReasonCode.EXIT_STOP and not intents_of(r, Purpose.REDUCE)
    assert r.counters.unprotected_cycles == 0 and not w.venue.open_orders().value


@pytest.mark.parametrize('side', SIDES)
def test_restart_after_every_candle_folds_the_same_driver_state(side):
    w = world(side, TOUCH)
    for i in range(10):
        t = w.close_ms(i)
        play_candle(w.venue, w.runner, t)
        w.runner.cycle(t)
        old = w.runner
        new = w.restart()
        assert new.mg == old.mg


def _ref(side):
    w = world(side, TOUCH)
    play(w, 9)
    return w


REF = {s: _ref(s) for s in SIDES}


@pytest.mark.parametrize('side,k', [(s, k) for s in SIDES for k in range(len(REF[s].journal.read()))])
def test_crash_at_every_journal_boundary_with_marks(side, k):
    w = world(side, TOUCH)
    w.journal.fail_writes(1, after=k, error=Crash)
    r = play(w, 9)
    ref = REF[side].runner
    assert r.counters.unprotected_cycles == 0 and r.fold.mode is EntriesMode.ACTIVE
    assert [(t.exit_reason, t.exit_price) for t in r.trades()] == [(t.exit_reason, t.exit_price) for t in ref.trades()]
    assert all(p.qty == 0 for p in w.venue.positions().value) and not w.venue.open_orders().value


@pytest.mark.parametrize('side', SIDES)
def test_runner_mark_from_a_polled_price_fires_the_target(side):
    """Testnet style: no candle walk, a mark price between closes (Session.poll_marks calls this)."""
    w = world(side, {})
    w.run(6)
    r = w.runner
    plan, = r.plans.values()
    level = plan.entry_price + (1 if side == 'LONG' else -1) * plan.tp2_offset
    w.venue.begin_candle(w.close_ms(7))                                # the venue between two closes
    w.venue.set_mark('SOLUSDT', level)
    assert not r.mark('SOLUSDT', level - (1 if side == 'LONG' else -1), w.close_ms(6))   # not crossed: nothing
    assert r.mark('SOLUSDT', level, w.close_ms(6))
    w.venue.end_candle(w.close_ms(7))
    r.cycle(w.close_ms(7))
    assert not r.fold.open_lots() and r.trades()[0].exit_reason is ReasonCode.EXIT_TAKE_PROFIT


def test_the_venue_refuses_a_candle_out_of_turn():
    w = world('LONG', {})
    with pytest.raises(ValueError):
        w.venue.begin_candle(w.close_ms(3))
    w.venue.begin_candle(w.close_ms(0))
    with pytest.raises(ValueError):
        w.venue.advance_to(w.close_ms(1))
    w.venue.end_candle(w.close_ms(0))
    assert w.venue.now_ms == w.close_ms(0)


# ------------------------------------------------------------------------------------------- testnet polling / config
def test_session_poll_marks_offers_each_symbol_mark_to_the_runner():
    from types import SimpleNamespace

    from newcore.ports.venue import ReadKind, ReadOutcome
    from newcore.runner.app import Session
    calls = []
    runner = SimpleNamespace(intrabar_sync=lambda at: calls.append(('sync', at)),
                             mark=lambda s, p, at: calls.append(('mark', s, p, at)) or True)
    reads = SimpleNamespace(mark_price=lambda s: ReadOutcome(kind=ReadKind.OK, observed_at_ms=T0, value=(D("101.5"),)))
    cfg = SimpleNamespace(mg_enabled=True, symbols=('SOLUSDT', 'BTCUSDT'))
    s = SimpleNamespace(cfg=cfg, venue=None, reads=reads, runner=runner)
    assert Session.poll_marks(s, wall_ms=5) == 2
    assert calls == [('sync', 5), ('mark', 'SOLUSDT', D('101.5'), 5), ('mark', 'BTCUSDT', D('101.5'), 5)]
    s.cfg = SimpleNamespace(mg_enabled=False, symbols=('SOLUSDT',))
    assert Session.poll_marks(s, wall_ms=6) == 0                      # management off: no polling


def test_mark_poll_config(tmp_path):
    from newcore.runner import config as C
    base = {'journal': {'dir': str(tmp_path / 'j')}}
    assert C.validate(base, environ={}).mark_poll_s == 10.0
    assert C.validate({**base, 'cycle': {'mark_poll_s': 0}}, environ={}).mark_poll_s == 0.0
    with pytest.raises(C.ConfigError):
        C.validate({**base, 'cycle': {'mark_poll_s': -1}}, environ={})
