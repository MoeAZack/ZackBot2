"""T05b runtime-canary finding (Codex, 2026-10-07): a process that STARTS during a Binance read outage never retried the
initial connect, so it could not recover without another restart. These tests start the REAL App.start_engine() and drive
the REAL App.loop() through the real Futures client + shared outage circuit + the exact-TESTNET read_outage injector,
advance past the injected window, and prove automatic recovery in the same process: one coalesced exchange-down incident,
nothing inferred from failed reads, no order/cancel sent, recovery only after positions and stops were re-read."""
import inspect, os, sys, tempfile, types
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.environ.setdefault('LOCALAPPDATA', tempfile.mkdtemp())
import pytest
import binance_client as BC                                                # noqa: E402
import engine as E                                                         # noqa: E402
from test_outage import Resp, Clock                                        # noqa: E402
from test_safety import mk_engine, opened, FakeX                          # noqa: E402

T0 = 14400.0 * 2000 + 30          # 30 s after a 4h (and 15m/1h) boundary: no candle closes inside the simulated window
INFO = FakeX().exchange_info()


class Stop(BaseException): pass


def _world(monkeypatch, lot=None, account_err=None):
    """A transport for the real client. Every network call is recorded; anything unexpected answers HTTP 500."""
    calls = []
    def answer(m, p, q):
        if account_err is not None and p == '/v2/account': return account_err
        if p == '/v1/exchangeInfo': return Resp(200, INFO)
        if p == '/v1/positionSide/dual': return Resp(200, {'dualSidePosition': True})
        if p == '/v2/account': return Resp(200, {'totalMarginBalance': '1000', 'totalUnrealizedProfit': '0'})
        if p == '/v2/positionRisk':
            return Resp(200, [{'symbol': lot['symbol'], 'positionSide': lot['side'], 'positionAmt': str(lot['qty'])}] if lot else [])
        if p == '/v1/openOrders':
            tag = (lot or {}).get('stop_id') or ''
            return Resp(200, [{'orderId': int(tag[2:])}] if lot and lot['symbol'] == q.get('symbol') and tag.startswith('o:') else [])
        if p == '/v1/openAlgoOrders': return Resp(200, {'orders': []})
        if p == '/v1/premiumIndex': return Resp(200, [{'symbol': 'BTCUSDT', 'markPrice': '100'}, {'symbol': 'ETHUSDT', 'markPrice': '50'}])
        return Resp(500, {'code': -1, 'msg': f'unexpected {m} {p}'})
    return calls, answer


def _start(monkeypatch, lot=None, outage='read_outage:180', account_err=None):
    """Real App.start_engine() with the real client on an injected transport + clock (no network, no installer)."""
    import app as A
    clk = Clock(); clk.t = T0
    calls, answer = _world(monkeypatch, lot, account_err)

    class Wired(BC.Futures):
        def __init__(self, key='', secret='', base=BC.TESTNET, recv_window=6000):
            self.key, self.secret, self.base, self.rw, self.offset, self.last_ok = key, (secret or '').encode(), base, recv_window, 0, 0
            self.__dict__['_health'] = BC.ExchangeHealth(clock=clk)
            def request(method, url, params=None, timeout=None):
                path = '/' + url.split('/fapi/', 1)[1]; calls.append((method, path, base))
                r = answer(method, path, params or {})
                if isinstance(r, Exception): raise r
                return r
            self.s = types.SimpleNamespace(request=request, headers={}, get=lambda *a, **k: Resp(200, {'serverTime': 0}))
        def sync_time(self): pass

    if outage: monkeypatch.setenv('ZB_TESTNET_FAULTS', outage)
    else: monkeypatch.delenv('ZB_TESTNET_FAULTS', raising=False)
    monkeypatch.setattr(E, 'Futures', Wired)
    tmp = tempfile.mkdtemp()
    real_engine = A.Engine
    def make(cfg, data):
        eng = real_engine(cfg, data)
        if lot: eng.state['lots'][lot['key']] = dict(lot)
        return eng
    monkeypatch.setattr(A, 'Engine', make)
    monkeypatch.setattr(A, 'DATA', tmp)
    monkeypatch.setattr(A, 'load_cfg', lambda: dict(MODE='paper', API_KEY='k' * 16, API_SECRET='s' * 16))
    monkeypatch.setattr(A.time, 'time', clk)                                 # the loop's clock = the circuit's clock
    monkeypatch.setattr(A.random, 'uniform', lambda a, b: 1.0)
    app = A.App.__new__(A.App); app.engine = None; app.lock = A.threading.RLock(); app.preview = lambda: None
    app.start_engine()
    return A, app, app.engine, clk, calls


def _drive(A, app, monkeypatch, clk, until, max_passes=400):
    """Run the REAL App.loop(); each loop sleep advances the fake clock; stops when until(engine) or after max_passes."""
    n = [0]
    def sl(x):
        clk.t += x
        if inspect.stack()[1].function != 'loop': return                     # client back-off sleeps just pass time
        n[0] += 1
        if until(app.engine) or n[0] >= max_passes: raise Stop()
    monkeypatch.setattr(A.time, 'sleep', sl)
    try: app.loop()
    except Stop: pass
    return n[0]


def _lot(monkeypatch):
    monkeypatch.setattr(E, 'Futures', BC.Futures)                            # restore the real client after mk_engine's fake
    e0, _ = mk_engine(); k = opened(e0)
    return dict(e0.state['lots'][k], key=k)


def _prot(lots):
    """Lots without T05a's observe-only excursion record ('ex'), which the audit updates on every mark, and AUD-04's
    stop-verification timestamp ('stop_confirmed_t'), which a successful open-order read refreshes (no order involved)."""
    return {k: {f: v for f, v in l.items() if f not in ('ex', 'stop_confirmed_t')} for k, l in lots.items()}


def _writes(calls): return [c for c in calls if c[0] in ('POST', 'DELETE', 'PUT')]


@pytest.mark.parametrize('with_lot', [True, False])
def test_engine_started_during_a_read_outage_recovers_in_the_same_process(monkeypatch, with_lot):
    lot = _lot(monkeypatch) if with_lot else None
    A, app, e, clk, calls = _start(monkeypatch, lot)
    eng0 = e
    # start-up failed safely: not connected, one exchange-down incident, an automatic retry scheduled, nothing sent
    assert not e.connected and e.connect_retry and e.connect_retry['n'] == 1
    assert 'retrying automatically' in e.error and e.exchange_state()['state'] == 'outage'
    inc = e.health['incidents']['exchange-down']; assert inc['open'] and inc['count'] == 1
    assert calls == []                                                       # injected reads never reach the network
    lots0 = {k: dict(v) for k, v in e.state['lots'].items()}
    n0 = len(e.health['errors'])

    # inside the injected window: retries back off, the incident coalesces, nothing is inferred or sent
    _drive(A, app, monkeypatch, clk, until=lambda x: clk.t >= T0 + 170)
    assert app.engine is eng0 and not e.connected and e.exchange_state()['state'] == 'outage'
    assert 2 <= e.connect_retry['n'] <= 7                                  # 5,10,20,40,60 s ... not every 2 s pass
    assert _prot(e.state['lots']) == _prot(lots0) and _writes(calls) == []
    assert e.health['incidents']['exchange-down']['open'] and len(e.health['errors']) == n0   # one line, updated in place

    # past the window: the next retry connects, the loop re-reads positions + stops, THEN the incident closes
    resolved_at = {'n': None}; real_resolve = e.resolve
    def resolve(key, note):
        if key == 'exchange-down' and resolved_at['n'] is None: resolved_at['n'] = len(calls)
        return real_resolve(key, note)
    monkeypatch.setattr(e, 'resolve', resolve)
    done = lambda x: x.connected and not x.health['incidents']['exchange-down']['open']
    _drive(A, app, monkeypatch, clk, until=done)
    assert clk.t < T0 + 400, 'did not recover automatically'
    assert app.engine is eng0                                                # same process/engine: no restart
    assert e.connected and e.error is None and e.connect_retry is None and e.exchange_state()['state'] == 'ok'
    assert e.rules and 'BTCUSDT' in e.rules and e.hedge is True
    assert _prot(e.state['lots']) == _prot(lots0)                          # identical quantities and stop ids
    assert _writes(calls) == []                                              # no order, stop or cancel sent at any point
    paths = [c[1] for c in calls]
    assert '/v2/account' in paths
    lines = [x[1] for x in list(e.health['errors'])[n0:]]
    assert len(lines) == 1 and lines[0].startswith('Binance answering again'), lines
    if with_lot:
        assert '/v2/positionRisk' in paths and '/v1/openOrders' in paths     # positions and protective orders re-read
        assert 'positions re-read and reconciled' in lines[0]
        assert 0 <= max(i for i, c in enumerate(calls[:resolved_at['n']]) if c[1] == '/v1/openOrders')   # read BEFORE close
    else:
        assert 'account readable' in lines[0]


def test_incident_is_not_closed_by_the_connect_alone(monkeypatch):
    """Connect succeeded but the reconcile read still fails: still connected-but-blind -> incident stays open."""
    lot = _lot(monkeypatch)
    A, app, e, clk, calls = _start(monkeypatch, lot, outage='read_outage:180:/fapi/v2/positionRisk')
    # only positionRisk is injected: connect (exchangeInfo/dual/account) succeeds at once, positions stay unreadable
    assert e.connected and e.connect_retry is None
    _drive(A, app, monkeypatch, clk, until=lambda x: clk.t >= T0 + 60)
    assert e.health['incidents']['exchange-down']['open']
    assert e.state['lots'][lot['key']]['qty'] == lot['qty'] and e.state['lots'][lot['key']]['stop_id'] == lot['stop_id']
    assert _writes(calls) == []


def test_non_transient_connect_failure_keeps_the_old_behaviour_and_is_not_retried(monkeypatch):
    A, app, e, clk, calls = _start(monkeypatch, outage=None,
                                   account_err=Resp(401, {'code': -2015, 'msg': 'Invalid API-key, IP, or permissions for action.'}))
    assert not e.connected and e.connect_retry is None and 'Invalid API-key' in e.error
    n_info = sum(1 for c in calls if c[1] == '/v1/exchangeInfo')
    _drive(A, app, monkeypatch, clk, until=lambda x: clk.t >= T0 + 300)
    assert sum(1 for c in calls if c[1] == '/v1/exchangeInfo') == n_info    # no reconnect hammering on a bad key
    assert not e.connected and not e.health['incidents'].get('exchange-down')


def test_retry_schedule_is_bounded_and_respects_the_circuit_probe(monkeypatch):
    import app as A
    app = A.App.__new__(A.App); app.preview = lambda: None
    monkeypatch.setattr(A.random, 'uniform', lambda a, b: 1.0)
    e = types.SimpleNamespace(connect_retry=None, error=None, _exchange_down=lambda ex: True,
                              exchange_down_incident=lambda where: None)
    delays = []
    for i in range(8):
        app._connect_failed(e, BC.BinanceError(-1007, 'Timeout'), now=1000.0); delays.append(e.connect_retry['next_t'] - 1000.0)
    assert delays == [5, 10, 20, 40, 60, 60, 60, 60]
    app._connect_failed(e, BC.ExchangeUnavailable('Binance outage: status check x not sent', 45), now=1000.0)
    assert e.connect_retry['next_t'] - 1000.0 == 60                          # cap
    e.connect_retry = None
    app._connect_failed(e, BC.ExchangeUnavailable('Binance outage: status check x not sent', 30), now=1000.0)
    assert e.connect_retry['next_t'] - 1000.0 == 30                          # never sooner than the circuit's next probe
    for bad in (float('nan'), float('inf'), 'x', None, -5):
        e.connect_retry = None
        ex = BC.ExchangeUnavailable('Binance outage: status check x not sent', 1); ex.retry_in = bad
        app._connect_failed(e, ex, now=1000.0)
        assert 0 < e.connect_retry['next_t'] - 1000.0 <= 72                   # finite and bounded whatever the floor is
    monkeypatch.setattr(A.random, 'uniform', lambda a, b: b)                 # jitter is bounded too
    e.connect_retry = dict(n=10, since=0)
    app._connect_failed(e, BC.BinanceError(-1007, 'Timeout'), now=1000.0)
    assert e.connect_retry['next_t'] - 1000.0 == pytest.approx(72)


def test_hedge_check_outage_fails_the_connect_instead_of_being_swallowed(monkeypatch):
    monkeypatch.setattr(E, 'Futures', BC.Futures)
    e, _ = mk_engine()
    def down(): raise BC.ExchangeUnavailable('Binance outage: status check /fapi/v1/positionSide/dual not sent', 5)
    e.trade.hedge_mode = down; e.connected = False
    with pytest.raises(BC.ExchangeUnavailable): e.connect()
    assert not e.connected
    def bad(): raise BC.BinanceError(-4059, 'No need to change position side.')
    e.trade.hedge_mode = bad
    e.connect()                                                              # a non-transient hedge answer is still only a warning
    assert e.connected


def test_recovery_reports_a_stop_not_seen_and_changes_nothing(monkeypatch):
    monkeypatch.setattr(E, 'Futures', BC.Futures)
    e, _ = mk_engine(); k = opened(e); lot0 = dict(e.state['lots'][k])
    e.exchange_down_incident('test')
    e.trade.stops.pop(lot0['stop_id'])                                       # its stop is not among the open orders
    calls0 = list(e.trade.calls)
    e._after_confirmed()
    inc = e.health['incidents']
    assert inc[f'stop-unseen|BTCUSDT|LONG']['open'] and not inc['exchange-down']['open']
    assert e.state['lots'][k] == lot0, {x: (lot0.get(x), v) for x, v in e.state['lots'][k].items() if lot0.get(x) != v}
    assert [c for c in e.trade.calls[len(calls0):] if c != 'tags'] == []      # only the open-orders read: no order sent
    e.exchange_down_incident('test again'); e.trade.fail.add('tags')
    e._after_confirmed()
    assert inc['exchange-down']['open']                                       # open orders unreadable -> not recovered


# ---------------- second internal adversarial pass on the fix (repros kept as regression tests)
def _world_with(monkeypatch, wrap):
    orig = _world
    def world(mp, lot=None, account_err=None):
        calls, answer = orig(mp, lot, account_err)
        return calls, wrap(calls, answer)
    monkeypatch.setattr(sys.modules[__name__], '_world', world)


def test_engine_replaced_during_the_retry_never_finishes_the_pass_on_the_old_engine(monkeypatch):
    """Settings saved (start_engine swaps self.engine) while the loop is inside _reconnect: the old engine must not go on
    to reconcile/manage/equity (orders or stop repairs with old keys, state written over the new engine's file)."""
    lot = _lot(monkeypatch); box = {}
    def wrap(calls, answer):
        def a2(m, p, q):
            app = box.get('app')
            if app is not None and p == '/v2/account' and 'swapped' not in box and app.engine.connect_retry:
                box['swapped'] = len(calls)
                app.engine = types.SimpleNamespace(connected=False, connect_retry=None, cfg={})
            return answer(m, p, q)
        return a2
    _world_with(monkeypatch, wrap)
    A, app, e, clk, calls = _start(monkeypatch, lot)
    box['app'] = app
    _drive(A, app, monkeypatch, clk, until=lambda x: clk.t > T0 + 400)
    assert 'swapped' in box
    assert [c[1] for c in calls[box['swapped'] + 1:]] == []                  # nothing more from the replaced engine


def test_open_orders_refused_non_transient_is_explained_and_recovery_waits(monkeypatch):
    lot = _lot(monkeypatch)
    def wrap(calls, answer):
        return lambda m, p, q: (Resp(400, {'code': -2015, 'msg': 'Invalid API-key, IP, or permissions for action.'})
                                if p == '/v1/openOrders' else answer(m, p, q))
    _world_with(monkeypatch, wrap)
    A, app, e, clk, calls = _start(monkeypatch, lot)
    _drive(A, app, monkeypatch, clk, until=lambda x: clk.t > T0 + 600)
    inc = e.health['incidents']
    assert e.connected and inc['exchange-down']['open']                       # stops never re-read -> not recovered
    assert inc[f"open-orders|{lot['symbol']}"]['open']                        # ...and it says why, in one keyed line
    assert _prot({1: e.state['lots'][lot['key']]}) == _prot({1: lot}) and _writes(calls) == []


def test_outage_that_ends_in_a_refused_key_closes_the_exchange_down_incident(monkeypatch):
    A, app, e, clk, calls = _start(monkeypatch, outage='read_outage:60',
                                   account_err=Resp(401, {'code': -2015, 'msg': 'Invalid API-key'}))
    _drive(A, app, monkeypatch, clk, until=lambda x: clk.t > T0 + 600)
    assert e.connect_retry is None and 'Invalid API-key' in e.error
    assert not e.health['incidents']['exchange-down']['open']                 # answered (refused): no longer "down"
    assert any('connect refused' in x[1] for x in e.health['errors'])


def test_recovery_line_never_claims_a_stop_for_a_lot_that_has_none(monkeypatch):
    lot = _lot(monkeypatch); lot['stop_id'] = None
    A, app, e, clk, calls = _start(monkeypatch, lot)
    n0 = len(e.health['errors'])
    _drive(A, app, monkeypatch, clk, until=lambda x: x.connected and not x.health['incidents']['exchange-down']['open'])
    rec = [x[1] for x in list(e.health['errors'])[n0:] if x[1].startswith('Binance answering again')]
    assert rec and '1 lot(s) still waiting for a stop' in rec[0], rec


def test_stop_unseen_closes_when_the_stop_is_seen_again(monkeypatch):
    monkeypatch.setattr(E, 'Futures', BC.Futures)
    e, _ = mk_engine(); k = opened(e); tag = e.state['lots'][k]['stop_id']
    e.exchange_down_incident('t'); saved = e.trade.stops.pop(tag)
    e._after_confirmed(); assert e.health['incidents']['stop-unseen|BTCUSDT|LONG']['open']
    e.trade.stops[tag] = saved; e.exchange_down_incident('t2')
    e._after_confirmed(); assert not e.health['incidents']['stop-unseen|BTCUSDT|LONG']['open']


def test_retry_schedule_survives_a_very_long_outage_without_overflow(monkeypatch):
    """Codex review P2 (ee1ba92): 5.0 * 2 ** (n - 1) overflowed at n=1025 (~17-20 h of start-up outage), so retry
    scheduling itself raised and the engine could no longer recover. The exponent is capped before exponentiation."""
    import app as A
    app = A.App.__new__(A.App); app.preview = lambda: None
    monkeypatch.setattr(A.random, 'uniform', lambda a, b: 1.0)
    e = types.SimpleNamespace(connect_retry=None, error=None, _exchange_down=lambda ex: True,
                              exchange_down_incident=lambda where: None)
    for n in (1024, 1025, 5000, 10 ** 9):
        e.connect_retry = dict(n=n, since=0.0)
        app._connect_failed(e, BC.BinanceError(-1007, 'Timeout'), now=1000.0)
        assert e.connect_retry['next_t'] - 1000.0 == 60 and e.connect_retry['n'] == n + 1
