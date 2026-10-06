"""T03c exceptional-path canary: the testnet-only leverage-refusal injector is inert on mainnet and without the flag."""
import os, sys, tempfile
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.environ.setdefault('LOCALAPPDATA', tempfile.mkdtemp())
import pytest
import binance_client as BC                                                # noqa: E402


def _client(base, monkeypatch, calls):
    c = BC.Futures.__new__(BC.Futures)
    c.key, c.secret, c.base, c.rw, c.offset, c.last_ok = 'k', b's', base, 6000, 0, 0
    monkeypatch.setattr(c, '_req', lambda *a, **k: calls.append((a, k)) or {'leverage': a[2]['leverage']}, raising=False)
    return c


def test_flag_refuses_only_the_listed_symbol_on_testnet(monkeypatch):
    monkeypatch.setenv('ZB_TESTNET_FAULTS', 'lev_refuse:SOLUSDT')
    calls = []; c = _client(BC.TESTNET, monkeypatch, calls)
    with pytest.raises(BC.BinanceError) as e: c.set_leverage('SOLUSDT', 10)
    assert e.value.code == -1000 and 'injected' in e.value.msg and calls == []     # nothing sent
    c.set_leverage('BTCUSDT', 10)
    assert len(calls) == 1


@pytest.mark.parametrize('base', [BC.MAINNET, 'https://fapi.binance.com/', '', None, 'https://testnet.binancefuture.com.evil'])
def test_flag_is_inert_unless_the_base_is_exactly_testnet(monkeypatch, base):
    monkeypatch.setenv('ZB_TESTNET_FAULTS', 'lev_refuse:SOLUSDT')
    calls = []; c = _client(base, monkeypatch, calls)
    c.set_leverage('SOLUSDT', 10)
    assert len(calls) == 1 and BC.testnet_faults(base, 'lev_refuse') == frozenset()


@pytest.mark.parametrize('val', [None, '', 'lev_refuse', 'lev_refuse:', 'lev_refuse:sol', 'other:SOLUSDT', 'lev_refuse:SOL USDT'])
def test_absent_or_malformed_flag_injects_nothing(monkeypatch, val):
    if val is None: monkeypatch.delenv('ZB_TESTNET_FAULTS', raising=False)
    else: monkeypatch.setenv('ZB_TESTNET_FAULTS', val)
    calls = []; c = _client(BC.TESTNET, monkeypatch, calls)
    c.set_leverage('SOLUSDT', 10)
    assert len(calls) == 1 and BC.testnet_faults(BC.TESTNET, 'lev_refuse') == frozenset()


def test_several_symbols_and_whitespace(monkeypatch):
    monkeypatch.setenv('ZB_TESTNET_FAULTS', ' lev_refuse:SOLUSDT , lev_refuse:ETHUSDT,junk')
    assert BC.testnet_faults(BC.TESTNET, 'lev_refuse') == frozenset({'SOLUSDT', 'ETHUSDT'})


# ---------------- T05b runtime canary: read_outage:<seconds>[:<path-prefix>] ----------------
import engine as E                                                          # noqa: E402
from test_outage import client, Clock, Resp, _real_client_engine, _marks, _orders   # noqa: E402
from test_safety import SL                                                  # noqa: E402

def _ok(m, p, q):
    if p == '/v1/order' and m == 'POST': return Resp(200, {'status': 'NEW', 'orderId': 1, 'clientOrderId': q.get('newClientOrderId')})
    if p == '/v1/order' and m == 'GET': return Resp(200, {'status': 'NEW', 'orderId': 1})
    if p == '/v1/premiumIndex': return Resp(200, [{'symbol': 'BTCUSDT', 'markPrice': '100'}])
    if m == 'DELETE': return Resp(200, {'status': 'CANCELED'})
    return Resp(200, [])


def _ro_client(monkeypatch, base=BC.TESTNET):
    monkeypatch.setattr(BC.time, 'sleep', lambda x: None)
    clk = Clock(); c, calls = client(_ok, clk); c.base = base
    return c, calls, clk


def _drive_reads(c, n=4):
    out = []
    for _ in range(n):
        try: c.positions(); out.append('ok')
        except BC.BinanceError as ex: out.append(type(ex).__name__)
    return out


@pytest.mark.parametrize('val', ['read_outage:90', 'read_outage:600', 'read_outage:1', ' read_outage:30 ',
                                 'junk,read_outage:45:/fapi/v2/positionRisk'])
def test_read_outage_flag_parses_bounded_seconds(monkeypatch, val):
    monkeypatch.setenv('ZB_TESTNET_FAULTS', val)
    sec, prefix = BC.testnet_read_outage(BC.TESTNET)
    assert 1 <= sec <= BC.READ_OUTAGE_MAX_S == 600 and prefix.startswith('/fapi/')


@pytest.mark.parametrize('val', [None, '', 'read_outage', 'read_outage:', 'read_outage:0', 'read_outage:601', 'read_outage:9999',
                                 'read_outage:-5', 'read_outage:1.5', 'read_outage:1e2', 'read_outage:ALL', 'read_outage: 30',
                                 'read_outage:30:fapi', 'read_outage:30:/sapi/v1', 'read_outage:30:/fapi/?x=1', 'lev_refuse:SOLUSDT',
                                 'read_outage:١٢'])
def test_read_outage_absent_or_malformed_is_inert(monkeypatch, val):
    if val is None: monkeypatch.delenv('ZB_TESTNET_FAULTS', raising=False)
    else: monkeypatch.setenv('ZB_TESTNET_FAULTS', val)
    assert BC.testnet_read_outage(BC.TESTNET) is None
    c, calls, clk = _ro_client(monkeypatch)
    assert _drive_reads(c) == ['ok'] * 4 and len(calls) == 4 and c.health.state == 'ok'


@pytest.mark.parametrize('base', [BC.MAINNET, 'https://fapi.binance.com/', '', None, 'https://testnet.binancefuture.com.evil',
                                  'https://testnet.binancefuture.com/'])
def test_read_outage_is_inert_unless_the_base_is_exactly_testnet(monkeypatch, base):
    monkeypatch.setenv('ZB_TESTNET_FAULTS', 'read_outage:600')
    assert BC.testnet_read_outage(base) is None
    if base is None: return
    c, calls, clk = _ro_client(monkeypatch, base)
    assert _drive_reads(c) == ['ok'] * 4 and len(calls) == 4 and c.health.state == 'ok'


def test_read_outage_window_opens_the_real_circuit_then_recovers_after_the_window(monkeypatch):
    monkeypatch.setenv('ZB_TESTNET_FAULTS', 'read_outage:60')
    c, calls, clk = _ro_client(monkeypatch)
    clk.t += 1000                                                           # the window starts at the first read, not at import
    with pytest.raises(BC.ExchangeUnavailable): c.positions()               # 3 injected 503s -> outage, fail fast
    assert c.health.state == 'outage' and calls == []                       # no network call for an injected read
    with pytest.raises(BC.ExchangeUnavailable): c.account()                 # inside the cooldown: fail fast
    clk.t += 59.9
    with pytest.raises(BC.BinanceError): c.positions()                      # the probe at 59.9 s is still injected
    assert calls == [] and c.health.state == 'outage' and c.health.probes >= 1
    clk.t += 120                                                            # window over + past the next probe
    assert c.positions() == {} and c.health.state == 'ok' and c.health.recovered and c.health.recoveries == 1
    assert [x[1] for x in calls] == ['/v2/positionRisk']
    clk.t += 1000
    assert _drive_reads(c) == ['ok'] * 4 and c.health.state == 'ok'         # one-shot: never re-arms in this process


def test_read_outage_never_touches_orders_cancels_or_critical_reads(monkeypatch):
    monkeypatch.setenv('ZB_TESTNET_FAULTS', 'read_outage:600')
    c, calls, clk = _ro_client(monkeypatch)
    with pytest.raises(BC.ExchangeUnavailable): c.positions()
    assert c.health.state == 'outage' and calls == []
    c._order(dict(symbol='BTCUSDT', side='SELL', positionSide='LONG', type='STOP_MARKET', stopPrice=90, quantity=0.001))
    assert c.get_order('BTCUSDT', 'zbX')['status'] == 'NEW'
    assert c.positions(critical=True) == {} and c.marks(critical=True) == {'BTCUSDT': 100.0}
    c._req('DELETE', '/fapi/v1/order', dict(symbol='BTCUSDT', origClientOrderId='zbX'), signed=True)
    c._req('POST', '/fapi/v1/leverage', dict(symbol='BTCUSDT', leverage=5), signed=True)
    assert [(m, p) for m, p, _ in calls] == [('POST', '/v1/order'), ('GET', '/v1/order'), ('GET', '/v2/positionRisk'),
                                             ('GET', '/v1/premiumIndex'), ('DELETE', '/v1/order'), ('POST', '/v1/leverage')]
    assert not c.health.recovered or c.health.recoveries == 1               # a real answer closes it (T05b rule), once


def test_read_outage_path_prefix_limits_the_injection(monkeypatch):
    monkeypatch.setenv('ZB_TESTNET_FAULTS', 'read_outage:600:/fapi/v1/openOrders')
    c, calls, clk = _ro_client(monkeypatch)
    assert c.positions() == {} and len(calls) == 1 and c.health.state == 'ok'
    with pytest.raises(BC.BinanceError): c._req('GET', '/fapi/v1/openOrders', signed=True)
    assert len(calls) == 1 and c.health.state == 'outage'


def test_engine_logs_the_read_outage_once_at_startup_without_secrets(monkeypatch, tmp_path):
    monkeypatch.setenv('ZB_TESTNET_FAULTS', 'read_outage:90')
    monkeypatch.setattr(BC.Futures, 'sync_time', lambda self: None)
    monkeypatch.setattr(E, 'Futures', BC.Futures)                       # test_grid/test_safety replace it globally
    seen = []; monkeypatch.setattr(E.log, 'warning', lambda m, *a, **k: seen.append(str(m)))
    E.Engine(dict(MODE='paper', API_KEY='KEYSECRETVALUE', API_SECRET='SECRETVALUE'), str(tmp_path))
    lines = [m for m in seen if 'TESTNET FAULT INJECTION' in m]
    assert len(lines) == 1 and '90 s' in lines[0] and 'SECRETVALUE' not in lines[0], seen
    seen.clear()
    E.Engine(dict(MODE='live', API_KEY='k', API_SECRET='s'), str(tmp_path))     # mainnet: nothing logged, nothing armed
    assert not [m for m in seen if 'TESTNET FAULT INJECTION' in m], seen


def test_engine_canary_window_blocks_entries_and_adds_changes_no_lot_then_recovers_once(monkeypatch):
    e, k, c, calls, clk, mode = _real_client_engine(monkeypatch)
    e.manage(_marks()); assert c.health.state == 'ok'                       # healthy pass before the flag is read
    monkeypatch.setenv('ZB_TESTNET_FAULTS', 'read_outage:120')
    lots0 = {x: dict(v) for x, v in e.state['lots'].items()}; lot = e.state['lots'][k]
    stop0 = lot['stop_id']; n0 = len(e.health['errors']); c0 = len(calls)
    for _ in range(14): e.manage(_marks()); clk.t += 8                      # 112 s inside the 120 s window
    assert c.health.state == 'outage' and e.health['incidents']['exchange-down']['open']
    assert _orders(calls, c0) == [] and not [x for x in calls[c0:] if x[0] == 'DELETE']     # stops untouched
    assert not [x for x in calls[c0:] if x[0] == 'GET' and x[1] in ('/v2/positionRisk', '/v1/openOrders')]
    assert {x: dict(v) for x, v in e.state['lots'].items()} == lots0 and lot['stop_id'] == stop0
    assert e.entry_block(SL, 'ETHUSDT', 'LONG') == 'Binance outage - no new entries until it answers again'
    assert e._add_block(lot, 0.001, 100.0) == E.OUTAGE_ADD
    new = [x[1] for x in list(e.health['errors'])[n0:]]
    assert sum('no order was sent' in l for l in new) == 1 and not any('answering again' in l for l in new), new
    clk.t += 120                                                            # window over; next probe due
    e.manage(_marks()); e.manage(_marks()); clk.t += 8; e.manage(_marks())
    assert c.health.state == 'ok' and not e.health['incidents']['exchange-down']['open']
    new = [x[1] for x in list(e.health['errors'])[n0:]]
    assert sum('answering again' in l for l in new) == 1, new               # exactly one recovery line
    assert [x for x in calls[c0:] if x[0] == 'GET' and x[1] == '/v2/positionRisk']           # positions re-read
    assert {x: dict(v) for x, v in e.state['lots'].items()} == lots0 and _orders(calls, c0) == []
    assert e.entry_block(SL, 'ETHUSDT', 'LONG') != 'Binance outage - no new entries until it answers again'
