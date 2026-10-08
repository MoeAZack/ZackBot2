"""Cowork attacks routed late from PR #37 (cw37), the MEDs and LOWs - each a failing repro first:

NEW-3  a re-send answered REJECTED with a code other than -4116 was booked 'refused, nothing executed' although the
       first send had landed: T5 (stop) left an orphan reduce-only stop / a second stop, T4 (close) a phantom open lot.
       Fix: after ANY rejected re-send, read the order by its client id before booking.
NEW-4  a stop endpoint that times out without landing (every attempt UNKNOWN) left the lot naked cycle after cycle.
       Fix: N consecutive cycles with no confirmed stop -> a reduce-only market close; an incident every naked cycle.
F3     hard HOLD: an emergency stop refused as 'would trigger' (the market is already through the level) left the
       position naked. Ruling proposed + default implemented: a feasible fallback level beyond the last close.
F4     resume() during a hard HOLD returned True.
LOW    strict mode raised InvariantBreach for a lost stop answer although the HOLD was durable and the stop in flight.
M2     tracebacks for bad config paths / symbols, --cycles <= 0 accepted, a zero-byte segment accepted,
       parity.py main() always 0 and a raw traceback without --root."""
from decimal import Decimal as D

import pytest

from newcore.domain import EntriesMode, IntentState, Purpose, ReasonCode
from newcore.ports.venue import OrderOutcome, OutcomeKind
from newcore.runner import InjectedSignals, ids
from newcore.runner import app as A
from slice_helpers import H4, World, flat_bars
from test_run_cli import cfg_file, run

pytestmark = pytest.mark.usefixtures('journal_kind')
SYM = 'SOLUSDT'
T0 = flat_bars(1)[0].open_ms
SIDES = ('LONG', 'SHORT')


def sig(side, enter=5, close=None):
    s = {(SYM, T0 + (enter + 1) * H4): (('enter', side),)}
    if close is not None:
        s[(SYM, T0 + (close + 1) * H4)] = (('close', side),)
    return InjectedSignals(s, stop_atr=D('2'))


def position(w, side):
    return next(p.qty for p in w.venue.positions().value if p.side == side)


def stops_resting(w, side):
    return [o for o in w.venue.open_orders().value if o.order_type == 'STOP_MARKET' and o.position_side == side]


class Shape:
    """Per-client-id answer shaping on top of the World's ScriptedVenue."""

    def __init__(self, w):
        self.w, self.inner = w, w.port
        self.lose_after = set()             # cid: the submit lands, its answer is lost (UNKNOWN)
        self.refuse_resend = {}             # cid -> code: the 2nd submit of that cid is REJECTED with code
        self.never_lands = set()            # kinds whose every submit times out without landing
        self.seen = {}
        w.port = self
        w.runner.venue = self

    def __getattr__(self, name):
        return getattr(self.inner, name)

    def _shape(self, ref, call, kind):
        n = self.seen[ref.client_id] = self.seen.get(ref.client_id, 0) + 1
        if kind in self.never_lands:
            return OrderOutcome(kind=OutcomeKind.UNKNOWN, ref=ref, observed_at_ms=self.w.venue.now_ms, detail='timeout')
        if n >= 2 and ref.client_id in self.refuse_resend:
            return OrderOutcome(kind=OutcomeKind.REJECTED, ref=ref, observed_at_ms=self.w.venue.now_ms,
                                error_code=self.refuse_resend[ref.client_id], detail='scripted')
        out = call()
        if ref.client_id in self.lose_after and n == 1:
            return OrderOutcome(kind=OutcomeKind.UNKNOWN, ref=ref, observed_at_ms=self.w.venue.now_ms, detail='timeout')
        return out

    def submit_stop(self, order):
        return self._shape(order.ref, lambda: self.inner.submit_stop(order), 'stop')

    def submit_market(self, order):
        return self._shape(order.ref, lambda: self.inner.submit_market(order), 'close' if order.reduce else 'entry')


def _cids(w, side):
    acct = w.config.account.account_id
    key = ids.decision_key('injected', 'v1', '4h', SYM, side, w.close_ms(5), Purpose.ENTRY)
    lot = ids.derive_lot_id(acct, ids.derive_intent_id(acct, key))
    stop = ids.client_id_for(ids.derive_child_intent_id(acct, lot, Purpose.PROTECT, 0), 'classic')
    ckey = ids.decision_key('injected', 'v1', '4h', SYM, side, w.close_ms(8), Purpose.CLOSE)
    close = ids.client_id_for(ids.derive_intent_id(acct, ckey), 'classic')
    return stop, close


# ------------------------------------------------------------------------------------------------------- NEW-3
@pytest.mark.parametrize('side', SIDES)
def test_new3_t5_a_landed_stop_whose_resend_is_refused_is_read_not_booked_refused(side):
    w = World(flat_bars(20), sig(side), strict=False)
    w.run(4)
    s = Shape(w)
    stop, _ = _cids(w, side)
    s.lose_after.add(stop)
    s.refuse_resend[stop] = -2021
    w.venue.not_found(stop, 1)                                     # the first read lags: the runner re-sends
    w.run(7)
    r = w.runner
    lot, = r.fold.open_lots()
    assert lot.live_stop is not None and lot.live_stop.state is IntentState.WORKING
    assert len(stops_resting(w, side)) == 1 and r.counters.unprotected_cycles == 0


@pytest.mark.parametrize('side', SIDES)
def test_new3_t4_a_landed_close_whose_resend_is_refused_is_read_not_booked_refused(side):
    w = World(flat_bars(20), sig(side, close=8), strict=False)
    w.run(7)
    s = Shape(w)
    _, close = _cids(w, side)
    s.lose_after.add(close)
    s.refuse_resend[close] = -2022
    w.venue.not_found(close, 1)
    w.run(11)
    r = w.runner
    assert not r.fold.open_lots() and position(w, side) == 0 and not stops_resting(w, side)


# ------------------------------------------------------------------------------------------------------- NEW-4
@pytest.mark.parametrize('side', SIDES)
def test_new4_a_stop_that_never_lands_escalates_to_a_reduce_only_close(side):
    w = World(flat_bars(20), sig(side), strict=False)
    w.run(4)
    s = Shape(w)
    s.never_lands.add('stop')
    w.run(9)
    r = w.runner
    assert position(w, side) == 0 and not r.fold.open_lots()
    close, = [iv for iv in r.fold.intents.values() if iv.purpose is Purpose.CLOSE]
    assert close.intent.reason is ReasonCode.EXIT_STOP_FAILED
    assert r.counters.unprotected_cycles <= 3                       # bounded, not 'every cycle'
    naked = [t for _, t in r.incidents if t.startswith('I1')]
    assert len(naked) == r.counters.unprotected_cycles               # an incident on every naked cycle


# ------------------------------------------------------------------------------------------------------- F3
@pytest.mark.parametrize('side', SIDES)
def test_f3_a_would_trigger_emergency_stop_gets_a_feasible_fallback_level(side):
    crash = ('80', '80.2', '79.8', '80') if side == 'LONG' else ('120', '120.2', '119.8', '120')
    w = World(flat_bars(20, overrides={7: crash}), sig(side), strict=False)
    w.run(6)
    lot, = w.runner.fold.open_lots()
    w.venue.external_cancel(lot.live_stop.intent.client_order_id)  # the stop is gone ...
    w.journal.fail_writes(10 ** 9)
    w.runner.store_unavailable('test: ENOSPC')                     # ... the store is down ...
    w.run(8)                                                       # ... and the market is through the old level
    cover = sum((o.qty for o in stops_resting(w, side)), D(0))
    assert position(w, side) == 0 or cover >= position(w, side)


# ------------------------------------------------------------------------------------------------------- LOWs
def test_f4_resume_during_a_hard_hold_is_refused():
    w = World(flat_bars(20), sig('LONG'))
    w.run(6)
    w.journal.fail_writes(10 ** 9)
    w.runner.store_unavailable('test: ENOSPC')
    assert w.runner.resume(w.close_ms(7)) is False
    assert w.runner.hard_hold is not None


@pytest.mark.parametrize('side', SIDES)
def test_strict_mode_does_not_raise_while_a_lost_stop_answer_is_in_flight_under_a_durable_hold(side):
    w = World(flat_bars(20), sig(side))                            # strict
    w.run(4)
    s = Shape(w)
    stop, _ = _cids(w, side)
    s.lose_after.add(stop)
    w.port.inner.blind(stop)                                       # its answer cannot be read this cycle
    w.run(5)                                                       # no InvariantBreach: HOLD + in-flight stop
    assert w.runner.fold.mode is EntriesMode.HOLD
    w.port.inner._blind.discard(stop)
    w.run(7)
    lot, = w.runner.fold.open_lots()
    assert lot.live_stop.state is IntentState.WORKING


def test_m2_bad_paths_and_symbols_are_typed_refusals_not_tracebacks(tmp_path):
    f = tmp_path / 'afile'
    f.write_text('x')
    for over in ({'venue': {'data_root': str(tmp_path / 'nope')}},
                 {'strategy': {'symbols': ['NOPEUSDT']}},
                 {'journal': {'dir': str(f)}},
                 {'journal': {'dir': str(f / 'under')}}):
        code, out = run(['run', '--config', cfg_file(tmp_path, **over), '--once'])
        assert code in (A.EXIT_CONFIG, A.EXIT_STORE_DOWN) and 'Traceback' not in out and out.strip()


@pytest.mark.parametrize('n', ('0', '-3'))
def test_m2_non_positive_cycles_are_refused(tmp_path, n):
    code, out = run(['run', '--config', cfg_file(tmp_path), '--cycles', n])
    assert code == A.EXIT_CONFIG


def test_m2_a_zero_byte_segment_is_a_store_hold_not_a_fresh_journal(tmp_path):
    cfg = cfg_file(tmp_path)
    assert run(['run', '--config', cfg, '--cycles', '3', '--enable-candidate'])[0] == 0
    from newcore.runner import config as C
    d = tmp_path / 'nc' / C.load(cfg).account_id / 'journal'
    seg = sorted(d.iterdir())[-1]
    seg.write_bytes(b'')
    code, out = run(['run', '--config', cfg, '--cycles', '3', '--enable-candidate'])
    assert code == A.EXIT_STORE_HOLD and 'zero-byte' in out
    assert sorted(p.name for p in d.iterdir() if p.name.endswith('.seg')) == [seg.name]   # nothing new beside it


def test_parity_main_gates_on_a_mismatch_and_refuses_a_missing_root(tmp_path, monkeypatch):
    from newcore.runner import parity as P
    out = tmp_path / 'p.json'
    assert P.main(['single', '--root', str(tmp_path / 'nope'), '--symbols', 'BTCUSDT', '--out', str(out)]) == 2
    monkeypatch.setattr(P, 'cmd_single', lambda a: {'kind': 'single', 'costs': 'zero', 'symbols': {'BTCUSDT': dict(
        longs=2, shorts=1, paired=1, same_exit_qty=1, r_equal=1, only_long=[3], only_short=[], diffs=[])}})
    assert P.main(['single', '--root', '.', '--symbols', 'BTCUSDT', '--out', str(out)]) == 1
    monkeypatch.setattr(P, 'cmd_single', lambda a: {'kind': 'single', 'costs': 'zero', 'symbols': {'BTCUSDT': dict(
        longs=1, shorts=1, paired=1, same_exit_qty=1, r_equal=1, only_long=[], only_short=[], diffs=[])}})
    assert P.main(['single', '--root', '.', '--symbols', 'BTCUSDT', '--out', str(out)]) == 0


@pytest.mark.parametrize('side', SIDES)
def test_f3_ruling_the_fallback_is_beyond_the_current_mark_and_marked_degraded(side):
    from newcore.runner.reports import health_line
    crash = ('80', '80.2', '79.8', '80') if side == 'LONG' else ('120', '120.2', '119.8', '120')
    w = World(flat_bars(20, overrides={7: crash}), sig(side), strict=False)
    w.run(6)
    lot, = w.runner.fold.open_lots()
    w.venue.external_cancel(lot.live_stop.intent.client_order_id)
    w.journal.fail_writes(10 ** 9)
    w.runner.store_unavailable('test: ENOSPC')
    w.run(7)
    mark = w.venue.mark_price(SYM).value[0]                        # the mark the fallback is placed against
    w.run(8)
    em = [o for o in w.venue.orders_submitted() if ids.is_emergency_client_id(o.ref.client_id)
          and o.order_type == 'STOP_MARKET']
    assert em and all((o.stop_price < mark) if side == 'LONG' else (o.stop_price > mark) for o in em)
    assert position(w, side) == 0 or sum((o.qty for o in stops_resting(w, side)), D(0)) >= position(w, side)
    assert f'degraded={SYM}:{side}:fallback_stop' in health_line(w.runner)
    assert any('DEGRADED protection' in t for _, t in w.runner.incidents)


@pytest.mark.parametrize('side', SIDES)
def test_f3_ruling_no_safe_stop_escalates_to_a_reduce_only_close(side):
    from newcore.runner.reports import health_line
    crash = ('80', '80.2', '79.8', '80') if side == 'LONG' else ('120', '120.2', '119.8', '120')
    w = World(flat_bars(20, overrides={7: crash}), sig(side), strict=False)
    w.run(6)
    lot, = w.runner.fold.open_lots()
    w.venue.external_cancel(lot.live_stop.intent.client_order_id)
    w.journal.fail_writes(10 ** 9)
    w.runner.store_unavailable('test: ENOSPC')
    w.port.refuse('stop', -2021)                                   # every stop would trigger: no safe stop exists
    w.run(9)
    assert position(w, side) == 0                                  # the reduce-only emergency close took it
    assert f'degraded={SYM}:{side}:emergency_close' in health_line(w.runner)
