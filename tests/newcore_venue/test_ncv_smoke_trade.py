"""S5 owner smoke: --dry-run plan (no network, no key decrypted) and the --trade round trip over fake HTTP.
Dummy credentials only; no network."""
import io
import json
import os
from decimal import Decimal as D

import pytest

from newcore.ports import keys as K
from newcore.venue import scenarios as S
from newcore.venue import smoke_trade as ST
from newcore.venue.records import SymbolRules
from newcore.venue.wire import WireTimeout

from ncv_support import DUMMY_KEY, DUMMY_SECRET, FakeHttp, fixture, raw
from test_ncv_smoke import ACCOUNT, NOW, REPO, XorProtector, cassette_files, env, flat_script, load_tool  # noqa: F401

IDS = ST.smoke_ids(ACCOUNT, NOW, 'SOLUSDT', 'algo')


def klines():
    rows = [[NOW - 180_000 + 60_000 * i, '219.0', '221.0', '218.5', '220.0', '10', NOW - 120_001 + 60_000 * i,
             '2200', 10, '5', '1100', '0'] for i in range(2)]
    return raw(200, json.dumps(rows).encode())


def trades(order_id, qty, price, fee, pnl='0'):
    return raw(200, json.dumps([{'buyer': True, 'commission': fee, 'commissionAsset': 'USDT', 'id': order_id * 10,
                                 'maker': False, 'orderId': order_id, 'price': price, 'qty': qty,
                                 'quoteQty': str(D(qty) * D(price)), 'realizedPnl': pnl, 'side': 'BUY',
                                 'positionSide': 'LONG', 'symbol': 'SOLUSDT', 'time': NOW + 1}]).encode())


def trade_script(**override):
    s = dict(
        boot=S.dual(True), price=klines(),
        entry=S.order(IDS.entry, status='FILLED', side='BUY', qty='1', executed='1', avg='220.5', order_id=8001),
        stop=S.algo(IDS.stop, status='NEW', qty='1', trigger='209.47'),
        verify=S.algo(IDS.stop, status='NEW', qty='1', trigger='209.47'),
        cancel=S.algo_ack(IDS.stop), confirm=S.algo(IDS.stop, status='CANCELED', qty='1', trigger='209.47'),
        close=S.order(IDS.close, status='FILLED', side='SELL', qty='1', executed='1', avg='221', order_id=8002),
        positions=raw(200, b'[]'), oo=raw(200, b'[]'), algo=raw(200, b'[]'),
        fills_entry=trades(8001, '1', '220.5', '0.11'), fills_close=trades(8002, '1', '221', '0.11', '0.5'))
    s.update(override)
    return [v for v in s.values() if v is not None]


def run(env, script, extra=()):
    http = FakeHttp(*script)
    out = io.StringIO()
    rc = load_tool().main(['--account-id', ACCOUNT, '--root', env['root'], '--cassette-dir', env['cassettes'],
                           *extra], http=http, local_clock=lambda: NOW, protector=XorProtector(), out=out)
    return rc, out.getvalue(), http


def sent(http, method, path):
    return [r for r in http.requests if r.method == method and r.url.endswith(path)]


# ---------- dry run ----------

class NoDecrypt(XorProtector):
    def unprotect(self, data, entropy):
        raise AssertionError('the dry run must not decrypt the key')


@pytest.mark.parametrize('trade', [False, True])
def test_dry_run_prints_the_plan_without_network_or_key(env, monkeypatch, trade):
    monkeypatch.setenv('LOCALAPPDATA', str(env['tmp']))

    def no_http(request):
        raise AssertionError('the dry run made a request')
    out = io.StringIO()
    rc = load_tool().main(['--account-id', ACCOUNT, '--root', env['root'], '--dry-run'] + (['--trade'] if trade else []),
                          http=no_http, local_clock=lambda: NOW, protector=NoDecrypt(), out=out)
    text = out.getvalue()
    assert rc == 0 and 'no network call is made' in text and 'stored testnet key: yes' in text
    assert os.path.join(str(env['tmp']), 'ZackBotNC', 'cassettes', f'smoke-{NOW}.json') in text
    assert ('TRADE phase (--trade)' in text) is trade and ('not requested' in text) is (not trade)
    assert DUMMY_KEY not in text and not os.path.exists(env['tmp'] / 'ZackBotNC' / 'cassettes')


def test_dry_run_never_builds_the_network_sender(env, monkeypatch):
    import newcore.venue.http_sender as hs

    def boom(*a, **k):
        raise AssertionError('sender built during a dry run')
    monkeypatch.setattr(hs, 'TestnetHttpSender', boom)
    out = io.StringIO()
    rc = load_tool().main(['--account-id', ACCOUNT, '--root', env['root'], '--cassette-dir', env['cassettes'],
                           '--dry-run', '--trade'], local_clock=lambda: NOW, protector=NoDecrypt(), out=out)
    assert rc == 0


@pytest.mark.parametrize('where', ['repo', 'legacy'])
def test_cassette_dir_in_repo_or_legacy_refused_before_anything(env, monkeypatch, where):
    monkeypatch.setenv('LOCALAPPDATA', str(env['tmp']))
    d = os.path.join(REPO, 'cassettes') if where == 'repo' else str(env['tmp'] / 'ZackBot' / 'cassettes')
    http = FakeHttp()
    out = io.StringIO()
    rc = load_tool().main(['--account-id', ACCOUNT, '--root', env['root'], '--cassette-dir', d, '--trade'],
                          http=http, local_clock=lambda: NOW, protector=XorProtector(), out=out)
    assert rc == 2 and http.requests == [] and not os.path.exists(d)


# ---------- the --trade round trip ----------

def test_trade_round_trip_happy_path(env):
    rc, out, http = run(env, flat_script() + trade_script(), ['--trade'])
    assert rc == 0, out
    assert 'TRADE phase done on TESTNET' in out and 'qty 1' in out and 'fees 0.22' in out
    assert len(sent(http, 'POST', '/fapi/v1/order')) == 2                 # entry + close only
    assert len(sent(http, 'POST', '/fapi/v1/algoOrder')) == 1             # one protective stop
    assert len(sent(http, 'DELETE', '/fapi/v1/algoOrder')) == 1           # one cancel
    assert not sent(http, 'DELETE', '/fapi/v1/order') and http.script == []
    entry, close = sent(http, 'POST', '/fapi/v1/order')
    e, c = dict(_q(entry)), dict(_q(close))
    assert (e['side'], e['positionSide'], e['newClientOrderId'], e['quantity']) == ('BUY', 'LONG', IDS.entry.client_id,
                                                                                    '1')
    assert (c['side'], c['positionSide'], c['newClientOrderId']) == ('SELL', 'LONG', IDS.close.client_id)
    stop = dict(_q(sent(http, 'POST', '/fapi/v1/algoOrder')[0]))
    assert (stop['side'], stop['positionSide'], stop['type'], stop['clientAlgoId'], stop['workingType']) == \
        ('SELL', 'LONG', 'STOP_MARKET', IDS.stop.client_id, 'MARK_PRICE')
    assert stop['triggerPrice'] == '209.47'                               # 5 % below 220.5, floored to the tick
    (path,) = cassette_files(env)
    text = open(path, encoding='utf-8').read()
    assert DUMMY_KEY not in text and DUMMY_SECRET not in text and 'with --trade' in text


def _q(request):
    from urllib.parse import parse_qsl
    return parse_qsl(request.query)


def test_read_only_default_places_nothing(env):
    rc, out, http = run(env, flat_script())
    assert rc == 0 and not [r for r in http.requests if r.method != 'GET'] and 'READ-ONLY smoke' in out


def test_not_flat_refuses_the_trade(env):
    rc, out, http = run(env, flat_script(positions='position_risk_hedge') + [S.dual(True)], ['--trade'])
    assert rc == 7 and 'not flat' in out and not [r for r in http.requests if r.method != 'GET']


def test_one_way_account_refuses_the_trade(env):
    rc, out, http = run(env, flat_script(dual=raw(200, b'{"dualSidePosition": false}')) + [S.dual(False)],
                        ['--trade'])
    assert rc == 7 and 'TRADE REFUSED' in out and not [r for r in http.requests if r.method != 'GET']


def test_read_failure_skips_the_trade(env):
    rc, out, http = run(env, flat_script(account=WireTimeout())[:5], ['--trade'])
    assert rc == 4 and 'TRADE SKIPPED' in out and not [r for r in http.requests if r.method != 'GET']


def test_entry_refused_stops_clean(env):
    rc, out, http = run(env, flat_script() + trade_script(
        entry=S.error(-2019, 'Margin is insufficient.'), stop=None, verify=None, cancel=None, confirm=None,
        close=None, positions=None, oo=None, algo=None, fills_entry=None, fills_close=None), ['--trade'])
    assert rc == 7 and 'nothing executed' in out and len(sent(http, 'POST', '/fapi/v1/order')) == 1


def test_entry_unknown_then_not_found_is_possible_exposure(env):
    rc, out, http = run(env, flat_script() + trade_script(
        entry=WireTimeout(), stop=S.error(-2013, 'Order does not exist.'), verify=None, cancel=None, confirm=None,
        close=None, positions=None, oo=None, algo=None, fills_entry=None, fills_close=None), ['--trade'])
    assert rc == 8 and 'POSITION MAY BE OPEN' in out and IDS.entry.client_id in out
    assert len(sent(http, 'POST', '/fapi/v1/order')) == 1 and not sent(http, 'POST', '/fapi/v1/algoOrder')


def test_entry_unknown_then_final_continues(env):
    script = flat_script() + trade_script()
    i = 10 + 2                                                            # the entry answer
    script[i:i + 1] = [WireTimeout(), S.order(IDS.entry, status='FILLED', side='BUY', qty='1', executed='1',
                                               avg='220.5', order_id=8001)]
    rc, out, _ = run(env, script, ['--trade'])
    assert rc == 0 and 'entry query by client id: final' in out


def test_stop_not_resting_triggers_an_immediate_close(env):
    rc, out, http = run(env, flat_script() + trade_script(
        stop=S.error(-2021, 'Order would immediately trigger.'),
        verify=S.order(IDS.close, status='FILLED', side='SELL', qty='1', executed='1', avg='219', order_id=8003),
        cancel=None, confirm=None, close=None, positions=None, oo=None, algo=None, fills_entry=None,
        fills_close=None), ['--trade'])
    assert rc == 7 and 'emergency reduce-only close DONE' in out
    assert [dict(_q(r))['newClientOrderId'] for r in sent(http, 'POST', '/fapi/v1/order')] == \
        [IDS.entry.client_id, IDS.close.client_id]


def test_close_not_confirmed_is_possible_exposure(env):
    rc, out, http = run(env, flat_script() + trade_script(
        close=WireTimeout(), positions=WireTimeout(), oo=None, algo=None, fills_entry=None, fills_close=None),
        ['--trade'])
    assert rc == 8 and 'CLOSE NOT CONFIRMED' in out and IDS.close.client_id in out


def test_not_flat_after_close_is_possible_exposure(env):
    rows = json.loads(fixture('position_risk_hedge').body)
    rc, out, _ = run(env, flat_script() + trade_script(positions=raw(200, json.dumps(rows).encode()),
                                                         fills_entry=None, fills_close=None), ['--trade'])
    assert rc == 8 and 'NOT FLAT after the close' in out


# ---------- ids and sizing ----------

def test_smoke_ids_are_runner_ids_unique_per_run():
    a, b = ST.smoke_ids(ACCOUNT, NOW, 'SOLUSDT', 'algo'), ST.smoke_ids(ACCOUNT, NOW + 1, 'SOLUSDT', 'algo')
    assert a.entry.client_id != b.entry.client_id
    assert a.stop.client_id == K.client_id_for(a.stop_intent, 'algo') and a.stop.route == 'algo'
    assert all(K.is_newcore_client_id(r.client_id) for r in (a.entry, a.stop, a.close))
    assert ST.smoke_ids(ACCOUNT, NOW, 'SOLUSDT', 'classic').stop.route == 'classic'


def rules(**kw):
    base = dict(symbol='BTCUSDT', status='TRADING', contract_type='PERPETUAL', quote_asset='USDT', margin_asset='USDT',
                price_precision=2, quantity_precision=3, tick_size=D('0.10'), min_price=D('1'), max_price=D('1000000'),
                step_size=D('0.001'), min_qty=D('0.001'), max_qty=D('1000'), market_step_size=D('0.001'),
                market_min_qty=D('0.001'), market_max_qty=D('120'), min_notional=D('100'), max_num_orders=None,
                max_num_algo_orders=None, order_types=())
    base.update(kw)
    return SymbolRules(**base)


def test_min_feasible_qty_meets_min_notional_with_headroom():
    assert ST.min_feasible_qty(rules(), D('60000')) == D('0.002')        # 110 / 60000 = 0.00183 -> 0.002
    assert ST.min_feasible_qty(rules(), D('200000')) == D('0.001')       # min qty dominates
    assert ST.min_feasible_qty(rules(market_max_qty=D('0.001')), D('60000')) is None


def test_stop_price_floors_to_the_tick():
    assert ST.stop_price_for(D('220.5'), rules(tick_size=D('0.01'))) == D('209.47')
    assert ST.stop_price_for(D('60123.4'), rules()) == D('57117.2')
