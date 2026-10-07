"""T05b final adversarial pass (on top of T03c): repros for the defects found, kept as regression tests.
R1 signed URL in a logged traceback; R2 Telegram alert per outage flap; R3 adds during an open circuit; R4 a false recovery
claim when the same pass re-opens the circuit; R5 incident store race (loop + HTTP thread); R6/R7 the real main loop
(mark-price and guard steps) during an outage; R8 the leverage-cooldown skip message is stable (coalesces)."""
import os, sys, threading, traceback, time as _time
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from test_outage import client, Clock, BUSY, Resp, _nosleep, _outage_engine, _real_client_engine, _marks   # noqa: E402
from test_safety import mk_engine, SL, SG as SG_, opened                     # noqa: E402
import binance_client as BC                                       # noqa: E402
import engine as E                                                # noqa: E402


def test_r1_signed_query_never_reaches_a_logged_traceback(monkeypatch):
    monkeypatch.setattr(BC.time, 'sleep', lambda x: None)
    c = BC.Futures('KEY', 'SECRET', 'http://127.0.0.1:9')
    try: c._req('GET', '/fapi/v2/account', signed=True); raise AssertionError('must fail')
    except Exception:
        tb = traceback.format_exc()
    assert 'signature=' not in tb and 'timestamp=' not in tb, tb


def test_r2_a_flapping_outage_sends_one_telegram_alert_not_one_per_flap():
    e, h, clk, down = _outage_engine(); assert opened(e)
    sent = []; e.notify = lambda t: sent.append(t)
    def flap():
        down['v'] = True
        for _ in range(6): e.manage(e.trade.marks()); clk.t += 8
        down['v'] = False; e.manage(e.trade.marks())
    for _ in range(5): flap()
    assert len(sent) == 1 and 'no order was sent' in sent[0], sent
    for _ in range(6): e._manage_failed('reconcile: -2015 Invalid API-key')     # a DIFFERENT cause still alerts at once
    assert len(sent) == 2 and '-2015' in sent[1]
    e.manage(e.trade.marks())                                                   # good pass re-arms
    e.health['alert_last'] = ('exchange-down', _time.time() - E.MANAGE_ALERT_REARM_S - 1)
    flap(); assert len(sent) == 3                                               # the same cause again after 15 min


def test_r3_dca_and_pyramid_adds_are_refused_while_the_circuit_is_open():
    e, h, clk, down = _outage_engine(); e.S['SLEEVES'] = [SL]; k = opened(e); lot = e.state['lots'][k]
    assert e._add_block(lot, 0.001, 100.0) is None
    for _ in range(3): h.fail('x')
    assert e._add_block(lot, 0.001, 100.0) == E.OUTAGE_ADD
    assert e._add_gate(lot, 0.001, 100.0, 'DCA') and lot['add_blocked'] == E.OUTAGE_ADD


def test_r4_a_pass_that_reopens_the_circuit_does_not_claim_recovery_or_add(monkeypatch):
    e, k, c, calls, clk, mode = _real_client_engine(monkeypatch)
    e.manage(_marks()); mode['down'] = True
    for _ in range(10): e.manage(_marks()); clk.t += 8
    assert e.health['incidents']['exchange-down']['open']
    mode['down'] = False; clk.t += 120
    real, lot = c.s.request, e.state['lots'][k]; lots0 = {x: dict(v) for x, v in e.state['lots'].items()}
    def req(method, url, params=None, timeout=None):     # positions answer (less than the lot: maybe a stop fill)...
        if url.endswith('/v2/positionRisk'):
            real(method, url, params, timeout)
            return Resp(200, [{'symbol': 'BTCUSDT', 'positionSide': 'LONG', 'positionAmt': str(lot['qty'] / 2)}])
        if '/openOrders' in url: return BUSY()           # ...but the open-orders read hits the outage again
        return real(method, url, params, timeout)
    c.s.request = req
    e.manage(_marks())
    assert c.health.state == 'outage'
    assert {x: dict(v) for x, v in e.state['lots'].items()} == lots0     # nothing guessed
    assert e.health['incidents']['exchange-down']['open']
    assert not any('answering again' in x[1] for x in e.health['errors'])
    assert e._add_block(lot, 0.001, 100.0) == E.OUTAGE_ADD


def test_r5_incident_store_is_thread_safe_between_loop_and_http_threads(monkeypatch):
    e, _ = mk_engine(); bad = []
    monkeypatch.setattr(E, 'INCIDENT_MAX', 20); monkeypatch.setattr(E.log, 'disabled', True); sw = sys.getswitchinterval(); sys.setswitchinterval(1e-6)
    def w(i):
        try:
            for j in range(3000): e.err(f'one-off {i}-{j} x')
        except Exception as ex: bad.append(repr(ex))
    ts = [threading.Thread(target=w, args=(i,)) for i in range(6)]
    [t.start() for t in ts]; [t.join() for t in ts]; sys.setswitchinterval(sw)
    assert not bad, bad[:3]


def test_r6_main_loop_mark_price_outage_goes_to_the_exchange_incident(monkeypatch):
    import app as A
    e, _ = mk_engine(); opened(e)
    e.state['last_cycle'] = {tf: E.now_utc().isoformat(timespec='seconds') for tf in A.TF_SEC}
    def marks(*a, **k): raise BC.ExchangeUnavailable('Binance outage: status check /fapi/v1/premiumIndex not sent (waiting for the next probe)', 2)
    e.data = type('D', (), {'marks': staticmethod(marks)})()
    app = A.App.__new__(A.App); app.engine = e; app.preview = lambda: None
    class Stop(BaseException): pass
    def sl(x): raise Stop()
    monkeypatch.setattr(A.time, 'sleep', sl)
    n0 = len(e.health['errors'])
    try: app.loop()
    except Stop: pass
    assert e.health['incidents'].get('exchange-down', {}).get('open'), list(e.health['errors'])[n0:]


def _one_loop_pass(monkeypatch, e):
    """Drive ONE real App.loop() pass (no cycle due); returns the sleep it ended with (2 = normal, 20 = 'main loop' error)."""
    import app as A
    e.state['last_cycle'] = {tf: E.now_utc().isoformat(timespec='seconds') for tf in A.TF_SEC}
    app = A.App.__new__(A.App); app.engine = e; app.preview = lambda: None
    class Stop(BaseException): pass
    slept = []
    def sl(x): slept.append(x); raise Stop()
    monkeypatch.setattr(A.time, 'sleep', sl)
    try: app.loop()
    except Stop: pass
    return slept[0]


def test_r7_main_loop_guard_and_equity_steps_route_an_outage_without_the_main_loop_error(monkeypatch):
    e, _ = mk_engine(); opened(e); n0 = len(e.health['errors'])
    def acct(): raise BC.ExchangeUnavailable('Binance outage: status check /fapi/v2/account not sent (waiting for the next probe)', 2)
    e.trade.account = acct; e.guard_eq = None                                  # the equity record must read too
    assert _one_loop_pass(monkeypatch, e) == 2                                 # no 'main loop' error / 20 s sleep
    inc = e.health['incidents']['exchange-down']
    assert inc['open'] and inc['count'] == 2                                   # guards + equity record, one line
    assert len(list(e.health['errors'])[n0:]) == 1
    def acct2(): raise BC.BinanceError(-2015, 'Invalid API-key')               # non-transient: still raises as before
    e.trade.account = acct2
    assert _one_loop_pass(monkeypatch, e) == 20


def test_r8_leverage_cooldown_skip_message_is_stable_and_seconds_stay_in_cooldown_left(monkeypatch):
    from test_leverage_auto import refusing
    e = refusing(mtype='ISOLATED')
    t = [1000.0]; monkeypatch.setattr(E.time, 'time', lambda: t[0])
    msgs, left = [], []
    for _ in range(3):
        assert not e.open_lot(SL, 'BTCUSDT', 'LONG', dict(SG_), None, e.equity())
        msgs.append(e.last_skip); left.append(e.lev_refusals['BTCUSDT'].get('cooldown_left')); t[0] += 60
    assert 'refused recently' in msgs[1] and msgs[1] == msgs[2], msgs           # identical text -> coalesces
    assert left == [None, E.LEV_REFUSAL_COOLDOWN_S - 60, E.LEV_REFUSAL_COOLDOWN_S - 120]
