"""The SAME driver entry point on the testnet target: newcore.venue.factory:build_testnet over the stateful fake Binance
(real transport, real TestnetVenue / TestnetBarSource / TestnetAccountReader, fake HTTP, DUMMY keys, no network)."""
import pytest

from newcore.tnet import driver as DR
from newcore.tnet.driver import INCONCLUSIVE, PASS, SKIPPED, run_scenario, run_suite
from newcore.tnet.rspec import bundled
from newcore.tnet.targets import TF_MS
from newcore.venue.factory import BindingMismatch

from tnet_support import Cfg, World, spec

TESTNET = [s for s in bundled() if 'testnet' in s['targets'] and s['id'] != 'T04-algo']


@pytest.mark.parametrize('s', TESTNET, ids=[s['id'] for s in TESTNET])
def test_bundled_specs_pass_on_the_testnet_factory_over_fake_http(s):
    w = World()
    r = run_scenario(s, w.target(), run_nonce='tn1', monotonic=w.monotonic)
    assert r.verdict == PASS, [a for a in r.assertions if not a[1]] + [r.error]
    assert w.fb.flat() and not w.fb.open_cids()


def test_the_whole_suite_is_pass_plus_one_inconclusive_bracket():
    w = World()
    res = run_suite(bundled(), w.target(), run_nonce='suite1', monotonic=w.monotonic)
    v = {r.id: r.verdict for r in res.scenarios}
    assert v.pop('T04-algo') == INCONCLUSIVE                          # the fake never triggers a stop: bounded wait
    assert v.pop('T04-classic') == v.pop('T10-stop-refused') == SKIPPED
    for k in [k for k in v if k[:3] in ('T05', 'T06', 'T07', 'T08')]:
        assert v.pop(k) == SKIPPED                                    # management scenarios: FakeVenue only
    assert set(v.values()) == {PASS}
    assert res.exit_code == DR.EXIT_INCONCLUSIVE
    assert w.fb.flat() and not w.fb.open_cids()


def test_cycles_wait_for_the_next_candle_close_plus_settle():
    w = World()
    t = w.target(settle_ms=1500)
    start = w.t
    now = t.next_close()
    assert now % TF_MS == 0 and now > start and w.t == now + 1500
    assert t.next_close() == now + TF_MS


def test_testnet_stops_go_algo_after_the_classic_refusal():
    w = World()
    r = run_scenario(spec('T01-long'), w.target(), run_nonce='tn2', monotonic=w.monotonic)
    assert [(x['kind'], x['route']) for x in r.ledger] == [('entry', 'classic'), ('stop', 'classic'),
                                                           ('stop', 'algo'), ('close', 'classic')]
    stops = [q for q in w.fb.requests if q.method == 'POST' and q.url.endswith('/fapi/v1/algoOrder')]
    assert len(stops) == 1


def test_lost_answer_is_injected_on_the_entry_request_only():
    w = World()
    r = run_scenario(spec('T09'), w.target(), run_nonce='tn3', monotonic=w.monotonic)
    assert r.verdict == PASS and r.injected == [['entry', 'lost_response', '/fapi/v1/order']]
    entry_cid = r.ledger[0]['client_id']
    assert entry_cid in w.fb.orders and w.fb.orders[entry_cid]['status'] == 'FILLED'    # it DID execute
    queries = [q for q in w.fb.requests if q.method == 'GET' and q.url.endswith('/fapi/v1/order')
               and entry_cid in q.query]
    assert queries                                                                        # resolved by query


def test_synthetic_refusal_never_reaches_the_venue():
    w = World()
    r = run_scenario(spec('T10-refused'), w.target(), run_nonce='tn4', monotonic=w.monotonic)
    assert r.verdict == PASS
    cid = r.ledger[0]['client_id']
    assert cid not in w.fb.used and not any(cid in q.query for q in w.fb.requests if q.method == 'POST')


def test_size_floor_sends_nothing():
    w = World()
    r = run_scenario(spec('T10-floor'), w.target(), run_nonce='tn5', monotonic=w.monotonic)
    assert r.verdict == PASS and not [q for q in w.fb.requests if q.method in ('POST', 'DELETE')]


def test_preflight_refuses_a_foreign_order_and_runs_nothing():
    w = World(foreign_orders=('web_manual1',))
    res = run_suite(bundled(), w.target(), run_nonce='pf1', monotonic=w.monotonic)
    assert res.exit_code == DR.EXIT_PREFLIGHT and res.scenarios == []
    assert any(r.startswith('foreign_order:SOLUSDT:web_manual1') for r in res.preflight.refusals)
    assert not [q for q in w.fb.requests if q.method in ('POST', 'DELETE')]


def test_an_adopted_foreign_order_is_scoped_out_of_the_runner_and_never_touched():
    """Cowork #37 N4: adopted foreign exposure is the baseline the Runner is shown (AdoptedView) until REC-02
    adoption is wired: the scenario runs normally, the adopted order is never touched."""
    w = World(foreign_orders=('web_manual1',))
    res = run_suite([spec('T01-long')], w.target(), run_nonce='pf2', monotonic=w.monotonic,
                    adopt_foreign=('web_manual1',))
    r, = res.scenarios
    assert res.exit_code == DR.EXIT_PASS and r.verdict == 'PASS' and r.counters['holds'] == 0
    assert r.final_truth['outcome'] == 'flat'
    assert r.cleanup['clean'] and w.fb.orders['web_manual1']['status'] == 'NEW'


def test_binding_mismatch_refuses_before_any_trade():
    w = World()
    with pytest.raises(BindingMismatch):
        w.target(config=Cfg(key_digest='f' * 16))
    assert not [q for q in w.fb.requests if q.method in ('POST', 'DELETE')]


def test_a_symbol_outside_the_factory_rules_fails_the_scenario():
    w = World()
    s = spec('T01-long')
    s['symbol'] = 'ETHUSDT'
    r = run_scenario(s, w.target(), run_nonce='tn6', monotonic=w.monotonic)
    assert r.verdict == DR.FAIL and 'not in the factory rules' in r.error and r.ledger == []


def test_testnet_account_is_the_configured_testnet_binding():
    w = World()
    t = w.target()
    r = run_scenario(spec('T12-protected'), t, run_nonce='tn7', monotonic=w.monotonic)
    assert r.verdict == PASS and t.journal.read()                     # the scenario's journal
    from newcore.domain import Environment
    assert t.environment is Environment.TESTNET
