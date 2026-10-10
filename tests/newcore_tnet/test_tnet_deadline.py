"""Codex P1 (nc-tnet01 e76d99c): the absolute scenario deadline is enforced around EVERY candle wait and before every
send. Expiry during the wait sends nothing; the wait never sleeps past the deadline; past the deadline nothing opens
exposure, and the teardown still runs and leaves the account flat."""
import copy
import io
from decimal import Decimal as D

import pytest

from newcore.ports import venue as P
from newcore.ports.keys import client_id_for
from newcore.tnet import driver as DR
from newcore.tnet.driver import FAIL, run_scenario, run_suite
from newcore.tnet.rspec import SpecError, bundled, validate_rspec
from newcore.tnet.seams import BoundedPort, DeadlineExceeded
from newcore.tnet.targets import FakeTarget, check_settle_ms

from tnet_support import World


def spec(id_, **bound):
    s = copy.deepcopy(next(x for x in bundled() if x['id'] == id_))
    s['bound'].update(bound)
    return s


class Clock:
    def __init__(self):
        self.t = 0.0

    def __call__(self):
        return self.t


class SlowCandles(FakeTarget):
    """Codex's repro: every candle wait takes `wait_s` of wall time."""

    def __init__(self, clock, wait_s):
        self.clock_, self.wait_s = clock, wait_s

    def next_close(self, deadline_s=None):
        self.clock_.t += self.wait_s
        return super().next_close(deadline_s)


def test_codex_repro_expiry_during_the_candle_wait_sends_nothing():
    clock = Clock()
    r = run_scenario(spec('T01-long', max_wall_s=1), SlowCandles(clock, 2.0), run_nonce='dl1', monotonic=clock)
    assert r.verdict == FAIL and r.error.startswith('DeadlineExceeded') and 'during the candle wait' in r.error
    assert r.ledger == [] and r.orders == [] and r.counters['cycles'] == 0
    assert r.cleanup['clean'] and DR.suite_exit_code([r]) == DR.EXIT_DEADLINE


def test_codex_repro_through_the_suite_exits_6():
    clock = Clock()
    res = run_suite([spec('T01-long', max_wall_s=1)], SlowCandles(clock, 2.0), run_nonce='dl2', monotonic=clock)
    assert res.exit_code == DR.EXIT_DEADLINE and res.scenarios[0].ledger == []


def test_a_wait_that_fits_still_runs():
    clock = Clock()
    r = run_scenario(spec('T01-long', max_wall_s=100), SlowCandles(clock, 2.0), run_nonce='dl3', monotonic=clock)
    assert r.verdict == 'PASS' and len(r.cycle_times) == 5


def test_testnet_wait_is_never_slept_past_the_deadline_and_nothing_is_sent():
    w = World()
    r = run_scenario(spec('T01-long', max_wall_s=30), w.target(), run_nonce='dl4', monotonic=w.monotonic)
    assert r.verdict == FAIL and 'only 30.0 s of the scenario deadline are left' in r.error
    assert w.slept == [2.0] and r.ledger == []            # no candle wait; only the teardown's confirmation re-read
    assert not [q for q in w.fb.requests if q.method in ('POST', 'DELETE')]
    assert r.cleanup['clean']


def test_testnet_deadline_mid_scenario_stops_before_the_next_wait_and_the_teardown_flattens():
    w = World()
    r = run_scenario(spec('T01-long', max_wall_s=100), w.target(), run_nonce='dl5', monotonic=w.monotonic)
    assert r.verdict == FAIL and r.error.startswith('DeadlineExceeded')
    assert w.slept[0] == 61.5 and len(r.cycle_times) == 1                          # one candle wait, no second
    assert [x['kind'] for x in r.ledger] == ['entry', 'stop', 'stop']               # classic refused -> algo
    assert r.cleanup['clean'] and len(r.cleanup['closes']) == 1 and w.fb.flat() and not w.fb.open_cids()


class JumpAfterEntry(FakeTarget):
    """The deadline passes right after the entry is accepted, i.e. between the entry and its stop."""

    def __init__(self, clock, jump_on):
        self.clock_, self.jump_on = clock, jump_on

    def prepare(self, spec_, account, portfolio_id):
        super().prepare(spec_, account, portfolio_id)
        inner, clock, jump_on = self.port, self.clock_, self.jump_on

        class Port:
            def __getattr__(self, n):
                return getattr(inner, n)

            def submit_market(self, order):
                out = inner.submit_market(order)
                if jump_on == 'after_entry' and not order.reduce:
                    clock.t += 10_000
                return out
        self.port = Port()


def test_deadline_between_entry_and_stop_the_stop_is_placed_and_the_teardown_flattens():
    clock = Clock()
    t = JumpAfterEntry(clock, 'after_entry')
    r = run_scenario(spec('T01-long', max_wall_s=100), t, run_nonce='dl6', monotonic=clock)
    assert r.verdict == FAIL and 'elapsed before cycle 2' in r.error                 # caught before the next wait
    assert [(x['kind'], x['past_deadline']) for x in r.ledger] == [('entry', False), ('stop', True)]
    assert r.cleanup['clean'] and len(r.cleanup['cancelled']) == 1 and len(r.cleanup['closes']) == 1
    assert all(p.qty == 0 for p in t.raw.positions().value)


def test_an_opening_order_past_the_deadline_is_refused_before_it_is_sent():
    class Inner:
        def submit_market(self, o):
            raise AssertionError('sent past the deadline')
    port = BoundedPort(Inner(), max_orders=5, max_notional='1000', price_of=lambda s: D('100'), expired=lambda: True)
    ref = P.OrderRef(symbol='SOLUSDT', client_id=client_id_for('int_' + '0' * 32, 'classic'), route='classic')
    with pytest.raises(DeadlineExceeded):
        port.submit_market(P.MarketOrder(ref=ref, position_side='LONG', qty=D('1'), reduce=False))
    assert port.ledger == [] and port.submits == 0


def test_bounded_port_lets_risk_reducing_sends_through_past_the_deadline_and_flags_them():
    sent = []

    class Inner:
        def submit_market(self, o):
            sent.append(o)

        def submit_stop(self, o):
            sent.append(o)
    port = BoundedPort(Inner(), max_orders=5, max_notional='1000', price_of=lambda s: D('100'), expired=lambda: True)
    ref = P.OrderRef(symbol='SOLUSDT', client_id=client_id_for('int_' + '1' * 32, 'classic'), route='classic')
    port.submit_stop(P.StopOrder(ref=ref, position_side='LONG', qty=D('1'), stop_price=D('99')))
    port.submit_market(P.MarketOrder(ref=ref, position_side='LONG', qty=D('1'), reduce=True))
    assert len(sent) == 2 and [x['past_deadline'] for x in port.ledger] == [True, True]


def test_deadline_inside_the_cycle_before_the_entry_refuses_it():
    clock = Clock()
    t = FakeTarget()
    real_prepare = t.prepare

    def prepare(spec_, account, portfolio_id):
        real_prepare(spec_, account, portfolio_id)
        v = t.venue
        real_positions = v.positions

        def positions(symbol=None):                     # the deadline passes during the cycle's first read
            clock.t += 10_000
            return real_positions(symbol)
        v.positions = positions
    t.prepare = prepare
    r = run_scenario(spec('T01-long', max_wall_s=100), t, run_nonce='dl7', monotonic=clock)
    assert r.verdict == FAIL and 'the scenario deadline has passed' in r.error
    assert r.ledger == [] and r.cleanup['clean']


# ------------------------------------------------------------------------------------------------ settle_ms bounds
@pytest.mark.parametrize('v', [-1, 10_001, 1.5, True, None])
def test_settle_ms_is_bounded(v):
    with pytest.raises(ValueError):
        check_settle_ms(v)
    with pytest.raises(ValueError):
        World().target(settle_ms=v)


@pytest.mark.parametrize('v', [-1, 10_001, True])
def test_spec_settle_ms_is_bounded(v):
    s = spec('T01-long')
    s['bound']['settle_ms'] = v
    with pytest.raises(SpecError, match='settle_ms'):
        validate_rspec(s)


def test_spec_settle_ms_drives_the_testnet_wait():
    w = World()
    r = run_scenario(spec('T10-floor', settle_ms=0), w.target(settle_ms=5000), run_nonce='dl8', monotonic=w.monotonic)
    assert r.verdict == 'PASS' and w.slept[0] == 60.0                                # boundary + 0 ms, not + 5 s


@pytest.mark.parametrize('v', ['-1', '10001'])
def test_cli_refuses_an_unbounded_settle(v):
    import importlib.util
    import os
    repo = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
    sp = importlib.util.spec_from_file_location('cli_dl', os.path.join(repo, 'tools', 'newcore_tnet_runner.py'))
    mod = importlib.util.module_from_spec(sp)
    sp.loader.exec_module(mod)
    out = io.StringIO()
    assert mod.main(['--settle-ms', v], out=out) == 2 and '--settle-ms must be in 0..10000' in out.getvalue()
