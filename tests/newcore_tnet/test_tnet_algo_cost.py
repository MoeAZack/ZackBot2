"""The 'classic stop refused -> algo' path on testnet costs EXACTLY one extra order per stop, and every bundled order
cap covers it. Proven on the faulted FakeVenue (classic STOP_MARKET refused with -4120, as Binance testnet does) against
the same spec with classic stops accepted, and cross-checked on the testnet factory over the fake Binance."""
import copy

import pytest

from newcore.tnet.driver import PASS, run_scenario
from newcore.tnet.rspec import bundled, expectations
from newcore.tnet.targets import FakeTarget

from tnet_support import World

FAKE = [s for s in bundled() if 'fake' in s['targets'] and s['id'] != 'T04-classic']   # T04-classic pins 'classic'


def variant(s, classic_stops):
    v = copy.deepcopy(s)
    v.setdefault('fake', {})['classic_stops'] = classic_stops
    v['expect'].pop('stop_route', None)                    # the route is what differs; everything else must hold
    v['expect'].pop('max_orders', None)                    # measured below against the bound instead
    return v


def stops(ledger):
    return [x for x in ledger if x['kind'] == 'stop']


@pytest.mark.parametrize('s', FAKE, ids=[s['id'] for s in FAKE])
def test_each_refused_classic_stop_costs_exactly_one_extra_order_and_fits_the_cap(s):
    acc = run_scenario(variant(s, 'accept'), FakeTarget(), run_nonce='cost')
    ref = run_scenario(variant(s, 'refuse'), FakeTarget(), run_nonce='cost')
    assert acc.verdict == ref.verdict == PASS, (acc.error, ref.error, [a for a in ref.assertions if not a[1]])
    placed = [x for x in stops(ref.ledger) if x['route'] == 'algo']
    # every algo stop is preceded by exactly one refused classic attempt of the same side, and nothing else changed
    assert len(ref.ledger) - len(acc.ledger) == len(placed)
    if s['id'] != 'T10-stop-refused':                      # there the stop is refused for another reason: no fallback
        assert len(placed) == len(stops(acc.ledger))            # 0 when no stop is ever placed (T10a, T10b)
    else:
        assert placed == [] and len(ref.ledger) == len(acc.ledger)
    for i, x in enumerate(ref.ledger):
        if x['kind'] == 'stop' and x['route'] == 'algo':
            prev = ref.ledger[i - 1]
            assert (prev['kind'], prev['route'], prev['side']) == ('stop', 'classic', x['side'])
    assert [x['kind'] for x in ref.ledger if x['kind'] != 'stop'] == [x['kind'] for x in acc.ledger if x['kind'] != 'stop']
    assert len(ref.ledger) <= s['bound']['max_orders']                       # the cap covers the testnet route
    want = expectations(s, 'testnet').get('max_orders') if 'testnet' in s['targets'] else None
    if want is not None:
        assert len(ref.ledger) <= want


def test_the_fallback_is_bounded_a_refused_algo_stop_is_not_retried_forever():
    """Classic refused AND the algo stop refused: one classic + one algo attempt, then the lot is closed (STOP_FAILED);
    never a loop of stop attempts."""
    s = variant(next(x for x in bundled() if x['id'] == 'T01-long'), 'refuse')
    s['steps'] = [{'op': 'enter'}, {'op': 'tick', 'n': 1}]
    s['expect'] = {'final': 'flat'}

    class AlgoRefused(FakeTarget):
        def prepare(self, spec_, account, portfolio_id):
            super().prepare(spec_, account, portfolio_id)
            real = self.venue.submit_stop

            def submit_stop(order):
                if order.ref.route == 'algo':
                    return self.venue._rejected(order.ref, -2021, 'would_trigger')
                return real(order)
            self.venue.submit_stop = submit_stop
    r = run_scenario(s, AlgoRefused(), run_nonce='cost2')
    assert r.verdict == PASS
    assert [(x['kind'], x['route']) for x in r.ledger] == [('entry', 'classic'), ('stop', 'classic'),
                                                           ('stop', 'algo'), ('close', 'classic')]
    assert r.trades[0]['exit_code'] == 'STOP_FAILED'


@pytest.mark.parametrize('sid', ['T01-long', 'T02-short', 'T09', 'T11', 'T12-protected'])
def test_testnet_factory_over_fake_binance_pays_the_same_single_extra_order(sid):
    w = World()                                            # FakeBinance refuses classic stops with -4120 by default
    s = next(x for x in bundled() if x['id'] == sid)
    r = run_scenario(s, w.target(), run_nonce='cost3', monotonic=w.monotonic)
    assert r.verdict == PASS
    st = stops(r.ledger)
    assert [x['route'] for x in st] == ['classic', 'algo'] and len(r.ledger) <= expectations(s, 'testnet').get(
        'max_orders', s['bound']['max_orders'])
    refused = [q for q in w.fb.requests if q.method == 'POST' and q.url.endswith('/fapi/v1/order')
               and 'type=STOP_MARKET' in q.query]
    assert len(refused) == 1                               # one classic STOP_MARKET request, answered -4120
