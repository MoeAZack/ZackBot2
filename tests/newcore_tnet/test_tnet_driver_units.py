"""Driver units: the T12 final-truth verdict (fresh reconciliation AND a direct venue read must agree), the always-on
safety counter, the cycle bound and the restart path."""
import copy
from decimal import Decimal as D
from types import SimpleNamespace

import pytest

from newcore.domain import IntentState
from newcore.ports import venue as P
from newcore.tnet import driver as DR
from newcore.tnet.driver import FAIL, final_truth, run_scenario
from newcore.tnet.rspec import bundled
from newcore.tnet.seams import BoundExceeded
from newcore.tnet.targets import FakeTarget

CID = 'zbn1o-' + 'a' * 26
STOP = 'zbn1a-' + 'b' * 26


def spec(id_):
    return copy.deepcopy(next(s for s in bundled() if s['id'] == id_))


def ok(value):
    return P.ReadOutcome(kind=P.ReadKind.OK, observed_at_ms=1_759_917_600_000, value=value)


class Venue:
    def __init__(self, positions=(), orders=()):
        self.p, self.o = positions, orders

    def positions(self, symbol=None):
        return ok(tuple(self.p))

    def open_orders(self, symbol=None):
        return ok(tuple(self.o))


def runner(rec_ok=True, lots=()):
    rec = SimpleNamespace(ok=rec_ok, reconciliation_id='rec_' + '0' * 32, items=() if rec_ok else (('x', 'y'),))
    return SimpleNamespace(reconcile=lambda: rec, fold=SimpleNamespace(open_lots=lambda: list(lots)))


def pos(qty):
    return P.VenuePosition(symbol='SOLUSDT', side='LONG', qty=D(qty), entry_price=D('100'))


def order(cid):
    return P.VenueOrder(ref=P.OrderRef(symbol='SOLUSDT', client_id=cid, route='algo' if cid.startswith('zbn1a')
                                       else 'classic'), exchange_order_id='9', position_side='LONG', reduce=True,
                        order_type='STOP_MARKET', status='NEW', qty=D('1'), close_position=False, stop_price=D('99'))


def lot(state=IntentState.WORKING, cid=STOP):
    return SimpleNamespace(live_stop=SimpleNamespace(state=state, intent=SimpleNamespace(client_order_id=cid)))


def test_flat_needs_the_reconciliation_and_the_direct_read():
    assert final_truth(runner(), Venue(), 'SOLUSDT')['outcome'] == 'flat'
    assert final_truth(runner(), Venue(positions=[pos('1')]), 'SOLUSDT')['outcome'] == 'unreconciled'
    assert final_truth(runner(), Venue(orders=[order(CID)]), 'SOLUSDT')['outcome'] == 'unreconciled'
    assert final_truth(runner(rec_ok=False), Venue(), 'SOLUSDT')['outcome'] == 'unreconciled'
    foreign = final_truth(runner(), Venue(positions=[pos('0')], orders=[order('web_x')]), 'SOLUSDT')
    assert foreign['outcome'] == 'flat' and foreign['newcore_orders'] == []          # not NEWCORE: the rec decides


def test_protected_needs_reconciled_working_and_listed_stops():
    listed = Venue(positions=[pos('1')], orders=[order(STOP)])
    assert final_truth(runner(lots=[lot()]), listed, 'SOLUSDT')['outcome'] == 'protected_reconciled'
    assert final_truth(runner(rec_ok=False, lots=[lot()]), listed, 'SOLUSDT')['outcome'] == 'unreconciled'
    assert final_truth(runner(lots=[lot()]), Venue(positions=[pos('1')]), 'SOLUSDT')['outcome'] == 'unreconciled'
    assert final_truth(runner(lots=[lot(IntentState.UNKNOWN)]), listed, 'SOLUSDT')['outcome'] == 'unreconciled'
    assert final_truth(runner(lots=[SimpleNamespace(live_stop=None)]), listed, 'SOLUSDT')['outcome'] == 'unreconciled'


def test_an_unreadable_venue_is_unreconciled():
    class Blind(Venue):
        def positions(self, symbol=None):
            return P.ReadOutcome(kind=P.ReadKind.UNKNOWN, observed_at_ms=1_759_917_600_000, detail='timeout')
    assert final_truth(runner(), Blind(), 'SOLUSDT')['outcome'] == 'unreconciled'


def test_an_unprotected_cycle_always_fails_whatever_the_spec_says(monkeypatch):
    from newcore.runner import runner as RR
    real = RR.Runner.cycle

    def cycle(self, now_ms, *, decide=True):
        out = real(self, now_ms, decide=decide)
        self.counters.unprotected_cycles += 1
        return out
    monkeypatch.setattr(RR.Runner, 'cycle', cycle)
    r = run_scenario(spec('T01-long'), FakeTarget(), run_nonce='u1')
    assert r.verdict == FAIL and ('safety: unprotected_cycles == 0', False, '5') in r.assertions


def test_the_cycle_bound_holds_even_past_validation():
    run = DR._Run(spec('T10-floor'), FakeTarget(), 'abcdef01', lambda: 0)
    run.build()
    run.bound = dict(run.bound, max_ticks=2)
    run.tick()
    run.tick()
    with pytest.raises(BoundExceeded, match='more than 2 cycles'):
        run.tick()


def test_restart_reopens_the_journal(monkeypatch):
    calls = []
    real = FakeTarget.reopen_journal

    def reopen(self):
        calls.append(1)
        return real(self)
    monkeypatch.setattr(FakeTarget, 'reopen_journal', reopen)
    r = run_scenario(spec('T11'), FakeTarget(), run_nonce='u2')
    assert r.verdict == 'PASS' and calls == [1]


def test_a_bracket_retries_while_inconclusive_with_fresh_ids(monkeypatch):
    calls = []
    real = DR._unobserved

    def flaky(run, observe):
        calls.append(1)
        return ['synthetic: not yet'] if len(calls) == 1 else real(run, observe)
    monkeypatch.setattr(DR, '_unobserved', flaky)
    s = spec('T01-long')
    s['attempts'] = 3
    s['expect']['observe'] = {'trades_in': ['SIGNAL_EXIT']}
    res = DR.run_suite([s], FakeTarget(), run_nonce='att')
    r, = res.scenarios
    assert r.verdict == 'PASS' and r.attempts == 2 and len(calls) == 2


def test_an_unobserved_event_is_inconclusive_never_a_fail_and_safety_still_fails():
    s = spec('T01-long')
    s['expect']['observe'] = {'trades_in': ['TP_FULL']}
    r = run_scenario(s, FakeTarget(), run_nonce='obs')
    assert r.verdict == DR.INCONCLUSIVE and "not in ['TP_FULL']" in r.error
    s['expect']['entries'] = 9                                          # a real assertion failure stays a FAIL
    assert run_scenario(s, FakeTarget(), run_nonce='obs2').verdict == FAIL


@pytest.mark.parametrize('bad', [{'attempts': 0}, {'attempts': 7}, {'expect_observe': {}},
                                 {'expect_observe': {'trades_in': []}}, {'expect_observe': {'adds_min': 0}},
                                 {'expect_observe': {'nope': 1}}])
def test_attempts_and_observe_are_validated(bad):
    from newcore.tnet.rspec import SpecError, validate_rspec
    s = spec('T01-long')
    if 'attempts' in bad:
        s['attempts'] = bad['attempts']
    else:
        s['expect']['observe'] = bad['expect_observe']
    with pytest.raises(SpecError):
        validate_rspec(s)
