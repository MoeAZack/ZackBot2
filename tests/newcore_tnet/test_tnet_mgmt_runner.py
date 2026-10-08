"""T05-T08 through the ManagedRunner (management.enabled) on FakeVenue: target exit, partial close, one bounded DCA,
cancel / replace around TP1 - and the runner-level end check that NO stop or order is left (T08)."""
import copy

import pytest

from newcore.tnet.driver import FAIL, PASS, run_scenario
from newcore.tnet.rspec import SpecError, bundled, validate_rspec
from newcore.tnet.targets import FakeTarget

MG = [s for s in bundled() if s['id'][:3] in ('T05', 'T06', 'T07', 'T08') and 'fake' in s['targets']]


def spec(id_):
    return copy.deepcopy(next(s for s in bundled() if s['id'] == id_))


@pytest.mark.parametrize('s', MG, ids=[s['id'] for s in MG])
def test_management_scenarios_pass_with_the_algo_route_like_testnet(s):
    r = run_scenario(copy.deepcopy(s), FakeTarget(), run_nonce='mg1')
    assert r.verdict == PASS, [a for a in r.assertions if not a[1]] + [r.error]
    stops = [o for o in r.orders if o['purpose'] == 'protect']
    assert stops and all(o['route'] in ('classic', 'algo') for o in stops)
    assert [o['state'] for o in stops if o['route'] == 'classic'] == ['rejected'] * sum(
        1 for o in stops if o['route'] == 'classic')                          # every classic stop refused -> algo
    assert r.cleanup['clean'] and r.cleanup['closes'] == [] and r.cleanup['cancelled'] == []


@pytest.mark.parametrize('side', ['long', 'short'])
def test_t06_reduces_half_then_the_rest(side):
    r = run_scenario(spec(f'T06-{side}'), FakeTarget(), run_nonce='mg2')
    reduces = [o for o in r.orders if o['purpose'] == 'reduce']
    assert [o['executed'] for o in reduces] == ['1.25', '1.25']


@pytest.mark.parametrize('side', ['long', 'short'])
def test_t07_adds_exactly_once(side):
    r = run_scenario(spec(f'T07-{side}'), FakeTarget(), run_nonce='mg3')
    assert len([o for o in r.orders if o['purpose'] == 'add']) == 1
    last_stop = [o for o in r.orders if o['purpose'] == 'protect'][-1]
    assert last_stop['qty'] == '5' and last_stop['state'] == 'filled'      # the basket stop covers entry + add


def test_t08_end_check_fails_when_any_order_is_left(monkeypatch):
    """The bug a0acf99 fixed (a flat lot left a stop working) must be caught by the runner-level end check."""
    from newcore.adapters import FakeVenue
    real = FakeVenue.open_orders

    def with_leftover(self, symbol=None):
        r = real(self, symbol)
        if r.value or not self.orders_submitted():
            return r
        stop = next(o for o in self.orders_submitted() if o.order_type == 'STOP_MARKET')
        import dataclasses

        from newcore.ports import venue as P
        left = P.VenueOrder(ref=stop.ref, exchange_order_id=stop.exchange_order_id, position_side=stop.position_side,
                            reduce=True, order_type='STOP_MARKET', status='NEW', qty=stop.qty, close_position=False,
                            stop_price=stop.stop_price)
        return dataclasses.replace(r, value=(left,))
    s = spec('T08-long')
    run_ok = run_scenario(copy.deepcopy(s), FakeTarget(), run_nonce='mg4')
    assert run_ok.verdict == PASS
    monkeypatch.setattr(FakeVenue, 'open_orders', with_leftover)
    r = run_scenario(s, FakeTarget(), run_nonce='mg5')
    assert r.verdict == FAIL
    assert any(name.startswith('open orders at the end') and not ok for name, ok, _ in r.assertions)


def test_move_is_fake_only_and_the_plan_is_validated():
    s = spec('T05-long')
    s['targets'] = ['fake', 'testnet']
    with pytest.raises(SpecError, match='FakeVenue only'):
        validate_rspec(s)
    for bad in ({'tp2_r': '0'}, {'tp2_r': 2}, {'be_after_tp1': 'yes'}, {'time_exit_candles': 0}, {'nope': '1'}):
        s = spec('T05-long')
        s['management']['plan'].update(bad)
        with pytest.raises(SpecError):
            validate_rspec(s)
    s = spec('T05-long')
    s['management'] = {'enabled': 'yes', 'plan': {}}
    with pytest.raises(SpecError):
        validate_rspec(s)


def test_management_disabled_runs_the_plain_runner():
    s = spec('T05-long')
    s['management']['enabled'] = False
    s['expect'] = {'final': 'flat'}
    s['steps'].append({'op': 'close'})
    s['bound']['max_ticks'] += 1
    r = run_scenario(s, FakeTarget(), run_nonce='mg6')
    assert r.verdict == PASS and not [o for o in r.orders if o['purpose'] in ('reduce', 'add')]


@pytest.mark.parametrize('n', ['T05', 'T06', 'T07'])
def test_the_testnet_bracket_order_cap_covers_per_attempt_routing(n):
    """Driver 83c39da: every resize / break-even stop pays one refused classic request on testnet. The FakeVenue run
    with classic stops refused is the same order sequence; plus one plan time-exit close it must fit the bracket cap."""
    fake = run_scenario(spec(f'{n}-long'), FakeTarget(), run_nonce='cap')
    worst = len(fake.ledger) + 1
    for side in ('long', 'short'):
        assert worst <= spec(f'{n}-tn-{side}')['bound']['max_orders'], (n, worst)
