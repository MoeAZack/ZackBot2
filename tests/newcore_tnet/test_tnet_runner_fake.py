"""The runner-driven scenarios on FakeVenue (CI): every bundled spec PASSES through the real Runner, and the harness
itself fails closed - a wrong expectation FAILs, a bound breach FAILs with a clean teardown, a missing exit is
INCONCLUSIVE, residue is exit 8."""
import copy
from decimal import Decimal as D

import pytest

from newcore.tnet import driver as DR
from newcore.tnet.driver import (FAIL, INCONCLUSIVE, PASS, SKIPPED, run_scenario, run_suite, scenario_nonce,
                                 suite_exit_code)
from newcore.tnet.rspec import bundled
from newcore.tnet.targets import WARMUP, FakeTarget, fake_candles, plan_ticks
from newcore.venue.tnet import CleanupResult

FAKE = [s for s in bundled() if 'fake' in s['targets']]


def spec(id_):
    return copy.deepcopy(next(s for s in bundled() if s['id'] == id_))


def run(s, nonce='t1', **kw):
    return run_scenario(s, FakeTarget(), run_nonce=nonce, **kw)


@pytest.mark.parametrize('s', FAKE, ids=[s['id'] for s in FAKE])
def test_every_bundled_spec_passes_on_fakevenue(s):
    r = run(copy.deepcopy(s))
    assert r.verdict == PASS, [a for a in r.assertions if not a[1]] + [r.error]
    assert r.cleanup['clean'] and r.counters['unprotected_cycles'] == 0
    assert all(a[1] for a in r.assertions)
    assert ('safety: unprotected_cycles == 0', True, '0') in r.assertions


def test_t01_books_entry_stop_and_close_with_fills():
    r = run(spec('T01-long'))
    assert [(o['purpose'], o['state']) for o in r.orders] == [('entry', 'filled'), ('protect', 'cancelled'),
                                                              ('close', 'filled')]
    t, = r.trades
    assert (t['side'], t['exit_code'], t['qty']) == ('LONG', 'SIGNAL_EXIT', '10')      # 10 USDT risk / 1.0 distance
    assert [x['kind'] for x in r.ledger] == ['entry', 'stop', 'close']
    assert r.final_truth['outcome'] == 'flat' and r.final_truth['reconciled']


def test_t02_short_mirrors_t01():
    r = run(spec('T02-short'))
    assert r.trades[0]['side'] == 'SHORT' and [x['side'] for x in r.ledger] == ['SHORT'] * 3


def test_t04_routes():
    a, c = run(spec('T04-algo')), run(spec('T04-classic'))
    assert [(o['purpose'], o['route'], o['state']) for o in a.orders] == [
        ('entry', 'classic', 'filled'), ('protect', 'classic', 'rejected'), ('protect', 'algo', 'filled')]
    assert [(o['purpose'], o['route'], o['state']) for o in c.orders] == [
        ('entry', 'classic', 'filled'), ('protect', 'classic', 'filled')]
    assert a.trades[0]['exit_code'] == c.trades[0]['exit_code'] == 'STOP_HIT'


def test_t09_lost_answer_is_resolved_by_query_and_resumed():
    r = run(spec('T09'))
    assert r.orders[0]['phases'] == ['unknown', 'final'] and r.counters['holds'] == 1
    assert r.injected[0][:2] == ['entry', 'lost_response']
    assert ('resume_succeeds', True, 'mode active') in r.assertions


def test_t10_floor_sends_nothing_and_refusals_end_flat():
    f = run(spec('T10-floor'))
    assert f.ledger == [] and f.orders == [] and f.counters['skips'] == 1
    rf = run(spec('T10-refused'))
    assert [(o['purpose'], o['state']) for o in rf.orders] == [('entry', 'rejected')]
    sr = run(spec('T10-stop-refused'))
    assert [(o['purpose'], o['state']) for o in sr.orders] == [('entry', 'filled'), ('protect', 'rejected'),
                                                               ('close', 'filled')]


def test_t11_restart_does_not_duplicate_entry_or_stop():
    r = run(spec('T11'))
    assert [x['kind'] for x in r.ledger] == ['entry', 'stop', 'close']
    assert ('check_protected_reconciled@cycle2', True, 'observed protected_reconciled') in r.assertions


def test_t12_protected_is_flattened_by_the_cleanup():
    r = run(spec('T12-protected'))
    assert r.final_truth['outcome'] == 'protected_reconciled'
    assert r.cleanup['clean'] and len(r.cleanup['cancelled']) == 1 and len(r.cleanup['closes']) == 1


# ------------------------------------------------------------------------------------------------ the harness itself
def test_a_wrong_expectation_fails():
    s = spec('T01-long')
    s['expect']['trades'] = ['STOP_HIT']
    r = run(s)
    assert r.verdict == FAIL and ("trades ['STOP_HIT']", False, "['SIGNAL_EXIT']") in r.assertions


@pytest.mark.parametrize('key,value', [('entries', 2), ('skips', 1), ('entry_phases', ['unknown', 'final']),
                                       ('entry_state', 'rejected'), ('hold_seen', True), ('mode_end', 'hold'),
                                       ('max_orders', 2), ('fills_match', False), ('stop_route', 'algo'),
                                       ('final', 'protected_reconciled'), ('incidents', 1),
                                       ('skip_reason', 'execution.size_min')])
def test_each_expectation_can_fail(key, value):
    s = spec('T01-long')
    s['expect'][key] = value
    assert run(s).verdict == FAIL


def test_a_bound_breach_fails_and_the_teardown_flattens():
    s = spec('T01-long')
    s['bound']['max_orders'] = 2                                      # the close is the 3rd order
    s['expect'].pop('max_orders')
    r = run(s)
    assert r.verdict == FAIL and r.error.startswith('BoundExceeded') and 'more than 2' in r.error
    # the Runner had cancelled the stop just before the refused close: the teardown closes the position at once
    assert r.cleanup['clean'] and r.cleanup['cancelled'] == [] and len(r.cleanup['closes']) == 1


def test_notional_bound_fails_before_the_entry_is_sent():
    s = spec('T01-long')
    s['bound']['max_notional_usdt'] = '999'
    r = run(s)
    assert r.verdict == FAIL and 'notional' in r.error and r.ledger == []


def test_no_exit_within_the_wait_is_inconclusive_and_cleaned():
    s = spec('T04-classic')
    del s['steps'][1]['fake_gap_pct']
    r = run(s)
    assert r.verdict == INCONCLUSIVE and 'no exit within 10 cycles' in r.error
    assert r.cleanup['clean'] and len(r.cleanup['closes']) == 1


def test_a_deadline_fails():
    t = iter(range(0, 10 ** 6, 400))
    r = run(spec('T01-long'), monotonic=lambda: next(t))
    assert r.verdict == FAIL and r.error.startswith('DeadlineExceeded')
    assert suite_exit_code([r]) == DR.EXIT_DEADLINE


def test_an_invariant_breach_fails(monkeypatch):
    from newcore.runner import runner as RR

    def boom(self, rec):
        raise RR.InvariantBreach('synthetic')
    monkeypatch.setattr(RR.Runner, 'check_invariants', boom)
    r = run(spec('T01-long'))
    assert r.verdict == FAIL and r.error == 'InvariantBreach: synthetic' and r.cleanup['clean']


def test_a_testnet_only_spec_is_skipped_on_fake():
    s = spec('T01-long')
    s['targets'] = ['testnet']
    s.pop('expect_testnet')
    s['expect_testnet'] = {}
    r = run(s)
    assert r.verdict == SKIPPED and r.ledger == [] and r.cleanup == {}


def test_client_ids_differ_per_run_and_per_scenario():
    a, b = run(spec('T01-long'), 'run_a'), run(spec('T01-long'), 'run_b')
    c = run(spec('T12-flat'), 'run_a')
    ids = [{x['client_id'] for x in r.ledger} for r in (a, b, c)]
    assert not (ids[0] & ids[1]) and not (ids[0] & ids[2])
    assert scenario_nonce('run_a', 'T01-long') != scenario_nonce('run_a', 'T12-flat')
    with pytest.raises(ValueError):
        scenario_nonce('', 'T01')


def test_the_fake_market_gaps_after_the_first_await_cycle():
    s = spec('T04-classic')
    total, gaps = plan_ticks(s)
    assert total == 11 and gaps == {WARMUP + 1: D('-3')}
    c = fake_candles(s, '100')
    assert c[WARMUP].open == D('100') and c[WARMUP + 1].open == D('97.00')


def test_suite_exit_codes_and_residue_stops_the_suite(monkeypatch):
    res = run_suite([spec('T01-long'), spec('T04-classic')], FakeTarget(), run_nonce='s1')
    assert res.exit_code == DR.EXIT_PASS and [r.verdict for r in res.scenarios] == [PASS, PASS]
    s = spec('T04-classic')
    del s['steps'][1]['fake_gap_pct']
    assert run_suite([spec('T01-long'), s], FakeTarget(), run_nonce='s2').exit_code == DR.EXIT_INCONCLUSIVE
    bad = spec('T01-long')
    bad['expect']['entries'] = 3
    assert run_suite([bad, s], FakeTarget(), run_nonce='s3').exit_code == DR.EXIT_FAIL
    dirty = CleanupResult(clean=False, attempts=3, remaining_positions=[('SOLUSDT', 'LONG', '10')])
    monkeypatch.setattr(FakeTarget, 'cleanup', lambda self, *a, **k: dirty)
    res = run_suite([spec('T12-protected'), spec('T01-long')], FakeTarget(), run_nonce='s4')
    assert res.exit_code == DR.EXIT_RESIDUE and len(res.scenarios) == 1               # nothing more ran
    assert res.scenarios[0].verdict == FAIL and ('cleanup clean', False, 'attempts 3') in res.scenarios[0].assertions


def test_preflight_refusal_runs_nothing():
    class Refusing(FakeTarget):
        def preflight(self, symbols, **kw):
            from newcore.venue.tnet import PreflightResult
            return PreflightResult(False, ('not_flat:SOLUSDT:LONG',), None, (), (), {})
    res = run_suite([spec('T01-long')], Refusing(), run_nonce='p1')
    assert res.exit_code == DR.EXIT_PREFLIGHT and res.scenarios == []


def test_results_serialise():
    d = run(spec('T09')).as_dict()
    assert d['verdict'] == PASS and d['assertions'][0]['ok'] in (True, False) and d['orders'] and d['ledger']
