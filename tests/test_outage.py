"""T05b (local draft): shared bounded outage circuit for transient Binance failures. Reads share one cooldown; orders
and an order's own confirmation lookups are never skipped; one probe per window; recovery is a single transition."""
import os, sys, tempfile, threading, types
import requests
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.environ.setdefault('LOCALAPPDATA', tempfile.mkdtemp())
import binance_client as BC                                                # noqa: E402


class Resp:
    def __init__(self, code, data, headers=None): self.status_code, self._d, self.headers = code, data, headers or {}; self.content = b'x'
    def json(self): return self._d


class Clock:
    def __init__(self): self.t = 1000.0
    def __call__(self): return self.t


BUSY = lambda: Resp(503, {'code': -1007, 'msg': 'Timeout waiting for response from backend server.'})


def client(answer, clock):
    """answer(method, path, params) -> Resp | Exception. Every network call is recorded."""
    c = BC.Futures.__new__(BC.Futures)
    c.key, c.secret, c.base, c.rw, c.offset, c.last_ok = 'k', b's', BC.TESTNET, 6000, 0, 0
    c.__dict__['_health'] = BC.ExchangeHealth(clock=clock)
    calls = []
    def request(method, url, params=None, timeout=None):
        path = url.split('/fapi')[1]; calls.append((method, path, dict(params or {})))
        r = answer(method, path, params or {})
        if isinstance(r, Exception): raise r
        return r
    c.s = types.SimpleNamespace(request=request, headers={}, get=lambda *a, **k: Resp(200, {'serverTime': 0}))
    c.sync_time = lambda: None
    return c, calls


def _nosleep(monkeypatch, clock=None):
    def sl(x):
        if clock is not None: clock.t += x
    monkeypatch.setattr(BC.time, 'sleep', sl)


def test_a_single_transient_failure_does_not_open_the_circuit(monkeypatch):
    _nosleep(monkeypatch); clk = Clock(); seq = [BUSY(), Resp(200, [1])]
    c, calls = client(lambda *a: seq.pop(0), clk)
    assert c._req('GET', '/fapi/v1/klines') == [1] and len(calls) == 2
    assert c.health.state == 'ok' and c.health.recoveries == 0


def test_outage_reads_share_one_cooldown_and_probe_once_per_window(monkeypatch):
    clk = Clock(); _nosleep(monkeypatch, clk)
    c, calls = client(lambda *a: BUSY(), clk)
    try: c._req('GET', '/fapi/v2/positionRisk', signed=True)
    except BC.BinanceError: pass
    assert c.health.state == 'outage'
    n = len(calls); assert n == 3                                # opened after 3 consecutive transient failures
    for _ in range(20):                                          # 20 more status checks inside the cooldown: no network
        try: c._req('GET', '/fapi/v1/openOrders', signed=True); raise AssertionError('must fail fast')
        except BC.ExchangeUnavailable as e: assert e.kind == 'read' and e.retry_in > 0
    assert len(calls) == n and c.health.fail_fast == 20
    clk.t += 60                                                  # cooldown over: exactly one probe goes out
    try: c._req('GET', '/fapi/v1/openOrders', signed=True)
    except BC.BinanceError: pass
    assert len(calls) == n + 1 and c.health.probes == 1
    try: c._req('GET', '/fapi/v1/openOrders', signed=True)
    except BC.ExchangeUnavailable: pass
    assert len(calls) == n + 1                                   # failed probe -> back to waiting (longer cooldown)
    assert c.health.cool > BC.ExchangeHealth.COOL_MIN


def test_cooldown_is_bounded_and_respects_retry_after(monkeypatch):
    clk = Clock(); h = BC.ExchangeHealth(clock=clk)
    for _ in range(40): h.fail('x'); clk.t += 1
    assert h.state == 'outage' and h.cool == BC.ExchangeHealth.COOL_MAX
    h2 = BC.ExchangeHealth(clock=clk)
    for _ in range(3): h2.fail('x', retry_after=45)
    assert h2.next_probe - clk.t >= 45


def test_recovery_is_one_transition_and_flags_a_reconcile(monkeypatch):
    clk = Clock(); _nosleep(monkeypatch, clk); up = {'v': False}
    c, calls = client(lambda *a: Resp(200, []) if up['v'] else BUSY(), clk)
    try: c._req('GET', '/fapi/v1/openOrders', signed=True)
    except BC.BinanceError: pass
    assert c.health.state == 'outage' and not c.health.recovered
    up['v'] = True; clk.t += 60
    assert c._req('GET', '/fapi/v1/openOrders', signed=True) == []
    s = c.health.snapshot()
    assert s['state'] == 'ok' and s['recoveries'] == 1 and c.health.recovered and s['consecutive_fail'] == 0
    c._req('GET', '/fapi/v1/openOrders', signed=True)
    assert c.health.recoveries == 1                              # further successes are not new recoveries


def test_a_business_error_proves_binance_is_up_and_is_not_transient(monkeypatch):
    _nosleep(monkeypatch); clk = Clock()
    c, _ = client(lambda *a: Resp(400, {'code': -1102, 'msg': 'bad param'}), clk)
    for _ in range(5):
        try: c._req('GET', '/fapi/v1/order', signed=True)
        except BC.BinanceError as e: assert e.code == -1102 and not isinstance(e, BC.ExchangeUnavailable)
    assert c.health.state == 'ok'


def test_orders_and_their_confirmation_are_never_skipped_during_an_outage(monkeypatch):
    """No duplicate order: the POST is attempted, its lost answer is looked up by client id (never a blind resend),
    and that lookup is not refused by the circuit."""
    clk = Clock(); _nosleep(monkeypatch, clk)
    h = BC.ExchangeHealth(clock=clk)
    for _ in range(3): h.fail('x')
    assert h.state == 'outage'
    script = [BUSY(), Resp(200, {'orderId': 5, 'status': 'FILLED', 'avgPrice': '100'})]
    c, calls = client(lambda *a: script.pop(0), clk); c.__dict__['_health'] = h
    o = c.open('BTCUSDT', 'LONG', '0.01')
    assert o['orderId'] == 5
    assert [x[0] for x in calls] == ['POST', 'GET'] and calls[1][2]['origClientOrderId'] == calls[0][2]['newClientOrderId']
    assert h.fail_fast == 0                                      # the confirmation lookup was never refused
    assert h.state == 'ok'                                       # the lookup answered: recovered


def test_order_lookup_failures_stay_ambiguous_not_unavailable(monkeypatch):
    clk = Clock(); _nosleep(monkeypatch, clk)
    c, calls = client(lambda *a: BUSY(), clk)
    try: c.open('BTCUSDT', 'LONG', '0.01'); raise AssertionError('must raise')
    except BC.AmbiguousOrder as e: assert e.tag and e.tag.startswith('c:')
    assert sum(1 for x in calls if x[0] == 'POST') == 1          # never a second POST while unconfirmed
    assert sum(1 for x in calls if x[0] == 'GET') >= 4           # every confirmation lookup actually went out
    assert c.health.fail_fast == 0


def test_network_exceptions_count_like_transient_codes(monkeypatch):
    clk = Clock(); _nosleep(monkeypatch, clk)
    c, calls = client(lambda *a: requests.ConnectionError('down'), clk)
    try: c._req('GET', '/fapi/v1/klines')
    except requests.RequestException: pass
    assert c.health.state == 'outage' and len(calls) == 3
    try: c._req('GET', '/fapi/v1/klines'); raise AssertionError
    except BC.ExchangeUnavailable: pass
    assert len(calls) == 3


def test_circuit_is_thread_safe():
    clk = Clock(); h = BC.ExchangeHealth(clock=clk)
    for _ in range(3): h.fail('x')
    clk.t += 100; admitted = []
    def worker():
        for _ in range(200):
            if h.admit_read() is None: admitted.append(1)
    ts = [threading.Thread(target=worker) for _ in range(4)]
    for t in ts: t.start()
    for t in ts: t.join()
    assert len(admitted) == 1 and h.probes == 1                  # exactly one probe per window across threads


# ------------------------------------------------------------------ engine: incidents, recovery, last confirmed, entries
from test_safety import mk_engine, SL, SG, opened          # noqa: E402
import engine as E                                          # noqa: E402


def _outage_engine():
    """Engine whose trading fake carries a real ExchangeHealth; positions() raises like the -1007 incident."""
    e, _ = mk_engine()
    clk = Clock(); h = BC.ExchangeHealth(clock=clk); e.trade.__dict__['_health'] = h
    real = e.trade.positions; down = {'v': False}
    def positions():
        if down['v']:
            h.fail('GET /fapi/v2/positionRisk: HTTP 503 code -1007')
            raise BC.BinanceError(-1007, 'Timeout waiting for response from backend server.')
        h.ok(); return real()
    e.trade.positions = positions
    return e, h, clk, down


def test_a_five_minute_outage_is_one_incident_then_one_recovery_and_changes_nothing():
    e, h, clk, down = _outage_engine()
    assert opened(e)
    lots0 = {k: dict(v) for k, v in e.state['lots'].items()}; calls0 = list(e.trade.calls)
    n0 = len(e.health['errors'])
    down['v'] = True
    for _ in range(36):                                     # 36 manage ticks (~5 min at 8 s)
        e.manage(e.trade.marks()); clk.t += 8
    assert h.state == 'outage'
    new = list(e.health['errors'])[n0:]
    assert len(new) == 1, new                               # ONE activity line, updated in place
    assert 'no order was sent' in new[0][1] and 'x36' in new[0][1]
    assert e.health['incidents']['exchange-down']['count'] == 36
    assert {k: dict(v) for k, v in e.state['lots'].items()} == lots0        # no resize / close guessed while blind
    assert [c for c in e.trade.calls[len(calls0):] if c in ('open', 'close', 'stop', 'cancel')] == []
    down['v'] = False; e.manage(e.trade.marks())
    new = list(e.health['errors'])[n0:]
    assert len(new) == 2 and 'answering again' in new[1][1] and '36 failed checks' in new[1][1]
    assert h.state == 'ok' and not h.recovered and not e.health['incidents']['exchange-down']['open']
    e.manage(e.trade.marks())
    assert len(list(e.health['errors'])[n0:]) == 2          # recovery is reported once


def test_entries_are_blocked_during_an_outage_with_a_plain_reason():
    e, h, clk, down = _outage_engine()
    for _ in range(3): h.fail('x')
    assert e.entry_block(SL, 'BTCUSDT', 'LONG') == 'Binance outage - no new entries until it answers again'
    h.ok()
    assert e.entry_block(SL, 'BTCUSDT', 'LONG') is None


def test_last_confirmed_advances_only_on_success():
    e, h, clk, down = _outage_engine()
    assert opened(e)
    e.manage(e.trade.marks()); t1 = e.health['confirmed']['positions']
    e.health['confirmed']['positions'] = '2000-01-01T00:00:00+00:00'
    down['v'] = True; e.manage(e.trade.marks())
    assert e.health['confirmed']['positions'] == '2000-01-01T00:00:00+00:00'
    down['v'] = False; e.manage(e.trade.marks())
    assert e.health['confirmed']['positions'] >= t1


def test_non_transient_failures_keep_their_own_messages():
    e, h, clk, down = _outage_engine()
    assert opened(e)
    def bad(): raise BC.BinanceError(-2015, 'Invalid API-key, IP, or permissions for action.')
    e.trade.positions = bad; n0 = len(e.health['errors'])
    e.manage(e.trade.marks())
    line = list(e.health['errors'])[n0][1]
    assert '-2015' in line and 'no order was sent' not in line and 'exchange-down' not in e.health['incidents']


def test_identical_repeats_coalesce_but_different_messages_do_not():
    e, _ = mk_engine(); n0 = len(e.health['errors'])
    for i in range(10): e.err('grid: order 123450 rejected at 101.0')
    e.err('grid: order 123451 rejected at 101.0')
    new = list(e.health['errors'])[n0:]
    assert len(new) == 2 and 'x10' in new[0][1]


def test_a_keyed_incident_shows_its_latest_text():
    e, _ = mk_engine(); n0 = len(e.health['errors'])
    e.err('open orders BTCUSDT: -1007 a', key='k'); e.err('open orders BTCUSDT: -1003 b', key='k')
    new = list(e.health['errors'])[n0:]
    assert len(new) == 1 and '-1003 b' in new[0][1] and 'x2' in new[0][1]


def test_an_old_repeat_after_a_quiet_gap_is_a_new_entry(monkeypatch):
    e, _ = mk_engine(); n0 = len(e.health['errors'])
    e.err('grid: order 1 rejected')
    inc = next(iter(v for v in e.health['incidents'].values() if v['msg'] == 'grid: order 1 rejected'))
    inc['last'] = '2000-01-01T00:00:00+00:00'
    e.err('grid: order 2 rejected')
    assert len(list(e.health['errors'])[n0:]) == 2


def test_status_health_shows_the_circuit_the_open_incident_and_last_confirmed():
    import app as A
    e, h, clk, down = _outage_engine()
    assert opened(e); e.manage(e.trade.marks())
    down['v'] = True
    for _ in range(4): e.manage(e.trade.marks())
    srv = A.App.__new__(A.App); srv.loop_ok = 0.0
    out = A.App.health(srv, e, [])
    assert out['exchange_circuit']['state'] == 'outage' and out['confirmed']['positions']
    assert [i['key'] for i in out['incidents']] == ['exchange-down'] and out['incidents'][0]['count'] == 4
    assert 'entry' not in out['incidents'][0]


# ------------------------------------------------------------------ review round (adversarial repros, now fixed)


def _interleaved_client(algo_answer=None):
    """openOrders answers; another thread's reads then trip the shared circuit before openAlgoOrders is sent."""
    clk = Clock()
    def answer(m, path, p):
        if path == '/v1/openOrders': return Resp(200, [])
        return algo_answer() if algo_answer else Resp(200, {'orders': [{'algoId': 7}]})
    c, calls = client(answer, clk)
    h = c.health; real_ok = h.ok
    def ok_then_other_thread_fails():
        real_ok()
        if calls and calls[-1][1] == '/v1/openOrders':
            for _ in range(3): h.fail('GET /fapi/v2/account (other thread): HTTP 503 code -1007')
    h.ok = ok_then_other_thread_fails
    return c, calls


def test_unknown_algo_state_is_never_reported_as_no_algo_stops(monkeypatch):
    _nosleep(monkeypatch)
    c, calls = _interleaved_client()
    try: tags = c.open_stop_tags('BTCUSDT')
    except BC.BinanceError as e: assert BC.is_transient(e); return          # unknown -> raises, reconcile retries
    assert 'a:7' in tags, f'algo stop silently missing: {tags}, calls={[x[1] for x in calls]}'


def test_busy_algo_read_raises_instead_of_an_incomplete_tag_set(monkeypatch):
    _nosleep(monkeypatch); clk = Clock()
    c, calls = client(lambda m, path, p: Resp(200, []) if path == '/v1/openOrders' else BUSY(), clk)
    try:
        tags = c.open_stop_tags('BTCUSDT')
    except BC.BinanceError:
        return
    raise AssertionError(f'unknown algo state returned as a complete tag set: {tags}')


def test_engine_never_books_a_live_lot_as_stopped_on_unknown_algo_state(monkeypatch):
    _nosleep(monkeypatch)
    e, _ = mk_engine(); k = opened(e)
    lot = e.state['lots'][k]; lot['stop_id'] = 'a:7'                # Binance now routes stops to the algo API
    e.trade.pos[('BTCUSDT', 'LONG')] = lot['qty'] * 0.5             # e.g. a partial close not yet reflected
    rc, _ = _interleaved_client()
    e.trade.open_stop_tags = lambda s: rc.open_stop_tags(s)
    e.reconcile(1000)
    assert k in e.state['lots'], 'lot finished as STOPPED although its algo stop is still open on Binance'


def test_incidents_are_bounded():
    e, _ = mk_engine()
    for i in range(300): e.err(f'manage lot-{chr(65 + i % 26)}{chr(65 + i // 26)}: boom')
    assert len(e.health['incidents']) <= 200, len(e.health['incidents'])


def test_the_live_outage_is_listed_first_in_status():
    import app as A
    e, h, clk, down = _outage_engine()
    assert opened(e)
    for i in range(25): e.err(f'old thing {chr(65 + i)} happened')
    down['v'] = True
    for _ in range(4): e.manage(e.trade.marks())
    srv = A.App.__new__(A.App); srv.loop_ok = 0.0
    out = A.App.health(srv, e, [])
    assert 'exchange-down' in [i['key'] for i in out['incidents']], [i['key'] for i in out['incidents']][:3]


def test_different_lots_are_never_merged_into_one_line():
    e, _ = mk_engine(); n0 = len(e.health['errors'])
    e.err('manage T|BTCUSDT|LONG|1759740000: -2021: Order would immediately trigger.')
    e.err('manage T|BTCUSDT|LONG|1759749999: -2021: Order would immediately trigger.')
    new = list(e.health['errors'])[n0:]
    assert any('1759749999' in x[1] for x in new), new


def test_a_cancel_keeps_its_retries_during_an_outage(monkeypatch):
    clk = Clock(); _nosleep(monkeypatch, clk)
    seq = [requests.ConnectionError('reset'), Resp(200, {'status': 'CANCELED'})]
    c, calls = client(lambda *a: seq.pop(0), clk)
    for _ in range(3): c.health.fail('x')
    assert c.health.state == 'outage'
    try:
        c.cancel('BTCUSDT', 'a:7')
    except requests.RequestException:
        raise AssertionError(f'cancel gave up after {len(calls)} attempt(s); master retries up to 4')


def test_the_recovery_line_never_claims_stops_were_read():
    e, h, clk, down = _outage_engine()
    k = opened(e)
    down['v'] = True
    for _ in range(4): e.manage(e.trade.marks())
    down['v'] = False
    e.trade.pos[('BTCUSDT', 'LONG')] = e.state['lots'][k]['qty'] * 0.5
    e.trade.fail.add('tags')
    n0 = len(e.health['errors'])
    e.manage(e.trade.marks())
    lines = [x[1] for x in list(e.health['errors'])[n0 - 1:]]
    # T05b canary finding: recovery is declared only after the protective orders were re-read too
    assert not any('answering again' in l for l in lines), lines
    assert e.health['incidents']['exchange-down']['open']
    assert any('open orders' in l for l in lines)                          # its own incident says what failed
    e.trade.fail.discard('tags')
    e.trade.pos[('BTCUSDT', 'LONG')] = e.state['lots'][k]['qty']           # full position, stop still open
    n1 = len(e.health['errors'])
    e.manage(e.trade.marks())
    lines = [x[1] for x in list(e.health['errors'])[n1:]]
    assert any('answering again' in l and 'stops re-confirmed' in l for l in lines), lines
    assert not e.health['incidents']['exchange-down']['open']


# ------------------------------------------------------------------ second adversarial pass (verifier repros + gaps)
import time as _time                                              # noqa: E402
from datetime import timedelta                                    # noqa: E402


def test_a_cancel_failing_every_pass_never_starves_position_reads(monkeypatch):
    """One orphan cancel answering 503 every pass (reads healthy): positions must still be re-read."""
    clk = Clock(); _nosleep(monkeypatch, clk)
    c, calls = client(lambda m, p, q: BUSY() if m == 'DELETE' else Resp(200, []), clk)
    sent = 0
    for _ in range(40):                                          # 40 manage passes, 8 s apart (~5 min)
        try: c.cancel('BTCUSDT', 'a:123')
        except BC.BinanceError: pass
        try: c.positions(); sent += 1
        except BC.ExchangeUnavailable: pass
        clk.t += 8
    assert sent == 40 and c.health.state == 'ok', (sent, c.health.state)
    assert c.health.other_fails >= 40                            # still visible as telemetry


def test_order_cancel_and_confirmation_failures_never_open_the_circuit_or_move_the_probe(monkeypatch):
    clk = Clock(); _nosleep(monkeypatch, clk)
    c, calls = client(lambda m, p, q: BUSY(), clk)
    for _ in range(5):
        try: c.cancel('BTCUSDT', 'a:1')
        except BC.BinanceError: pass
        try: c.get_order('BTCUSDT', 'zbX')                       # critical confirmation lookup
        except BC.BinanceError: pass
    assert c.health.state == 'ok' and c.health.fails == 0
    for _ in range(3): c.health.fail('GET x')                     # a real read outage
    probe = c.health.next_probe; cool = c.health.cool
    for _ in range(3):
        try: c.cancel('BTCUSDT', 'a:1')
        except BC.BinanceError: pass
        try: c.open('BTCUSDT', 'LONG', '1')
        except (BC.BinanceError, BC.AmbiguousOrder): pass
    assert (c.health.next_probe, c.health.cool) == (probe, cool)


def test_one_failed_probe_doubles_the_cooldown_once(monkeypatch):
    clk = Clock(); _nosleep(monkeypatch, clk)
    c, calls = client(lambda m, p, q: BUSY(), clk)
    try: c.positions()
    except BC.BinanceError: pass
    assert c.health.state == 'outage' and c.health.cool == BC.ExchangeHealth.COOL_MIN
    clk.t += 100
    try: c.positions()                                           # the probe: one call, one failure
    except BC.BinanceError: pass
    assert c.health.cool == 2 * BC.ExchangeHealth.COOL_MIN


def test_an_http_date_retry_after_on_an_order_post_is_still_looked_up_by_client_id(monkeypatch):
    clk = Clock(); _nosleep(monkeypatch, clk)
    def answer(m, p, q):
        if m == 'POST': return Resp(503, {'code': -1007, 'msg': 'timeout'}, {'Retry-After': 'Wed, 21 Oct 2026 07:28:00 GMT'})
        return Resp(200, {'status': 'FILLED', 'orderId': 1, 'avgPrice': '100', 'executedQty': '1'})
    c, calls = client(answer, clk)
    o = c.open('BTCUSDT', 'LONG', '1')
    assert o['status'] == 'FILLED' and [x[0] for x in calls] == ['POST', 'GET']
    assert calls[1][2]['origClientOrderId'] == calls[0][2]['newClientOrderId']


def test_retry_after_is_parsed_defensively_and_bounded():
    ra = BC.retry_after
    assert ra({'Retry-After': '5'}) == 5.0 and ra({}) == 0.0 and ra(None) == 0.0
    for bad in ('nan', 'inf', '-inf', '-5', 'soon', '', 'Wed, 99 Foo'):
        v = ra({'Retry-After': bad}); assert v == 0.0, (bad, v)
    assert ra({'Retry-After': '9999'}) == BC.RETRY_AFTER_MAX
    now = 1_800_000_000
    import email.utils as eu
    assert 29 <= ra({'Retry-After': eu.formatdate(now + 30, usegmt=True)}, now=now) <= 31
    assert ra({'Retry-After': eu.formatdate(now - 30, usegmt=True)}, now=now) == 0.0
    clk = Clock(); h = BC.ExchangeHealth(clock=clk)
    for x in (float('nan'), float('inf'), 'x', 1e9):
        h.fail('GET', retry_after=x)
    assert h.state == 'outage' and h.next_probe - clk.t <= BC.RETRY_AFTER_MAX + 1


def _fresh_cache(e):
    e.marks, e.marks_t = e.trade.marks(), _time.time()          # the 8 s manage loop keeps this fresh


def test_a_manual_stop_move_is_attempted_during_the_read_cooldown():
    e, _ = mk_engine(); k = opened(e); _fresh_cache(e)
    clk = Clock(); h = BC.ExchangeHealth(clock=clk); e.trade.__dict__['_health'] = h
    for _ in range(3): h.fail('x')
    def marks():
        w = h.admit_read()
        if w is not None: raise BC.ExchangeUnavailable('Binance outage: status check /fapi/v1/premiumIndex not sent', w)
        return {}
    e.trade.marks = marks
    n = e.trade.calls.count('stop')
    e.move_stop(k, 90.0)
    assert e.trade.calls.count('stop') == n + 1 and e.state['lots'][k]['stop'] == 90.0


def test_a_manual_stop_move_with_no_known_price_is_refused_and_nothing_is_closed():
    e, _ = mk_engine(); k = opened(e)
    e.marks_t = _time.time() - 3600                              # stale cache
    def down(*a, **kw): raise BC.ExchangeUnavailable('Binance outage', 2.0)
    e.trade.marks = down
    lot0 = dict(e.state['lots'][k]); n = len(e.trade.calls)
    try: e.move_stop(k, 90.0); raise AssertionError('must refuse')
    except ValueError as ex: assert 'previous stop is still active' in str(ex)
    assert e.trade.calls[n:] == [] and e.state['lots'][k] == lot0


def test_a_critical_mark_read_is_sent_during_the_cooldown(monkeypatch):
    clk = Clock(); _nosleep(monkeypatch, clk)
    up = {'v': False}
    c, calls = client(lambda m, p, q: Resp(200, [{'symbol': 'BTCUSDT', 'markPrice': '99'}]) if up['v'] else BUSY(), clk)
    try: c.positions()
    except BC.BinanceError: pass
    up['v'] = True; n = len(calls)
    try: c.marks(); raise AssertionError('plain read must fail fast')
    except BC.ExchangeUnavailable: pass
    assert len(calls) == n and c.marks(critical=True) == {'BTCUSDT': 99.0} and len(calls) == n + 1


def test_fail_fast_messages_coalesce_for_unkeyed_callers(monkeypatch):
    clk = Clock(); _nosleep(monkeypatch, clk)
    c, calls = client(lambda m, p, q: BUSY(), clk)
    for _ in range(3):
        try: c.positions()
        except BC.BinanceError: pass
    msgs = []
    for dt in (0.3, 0.7, 0.4):
        clk.t += dt
        try: c._req('GET', '/fapi/v1/ticker/bookTicker', dict(symbol='BTCUSDT'))
        except BC.ExchangeUnavailable as ex: msgs.append(str(ex)); assert ex.retry_in > 0
    e, _ = mk_engine(); n0 = len(e.health['errors'])
    for m in msgs: e.err(f'maker entry ME|T|BTCUSDT|LONG: {m}')
    assert len(msgs) == 3 and len(list(e.health['errors'])[n0:]) == 1, list(e.health['errors'])[n0:]


def test_signed_query_strings_never_reach_activity_lines_and_repeats_coalesce(monkeypatch):
    clk = Clock(); _nosleep(monkeypatch, clk); ts = iter(range(10**6))
    def answer(m, p, q):
        return requests.ConnectionError(f"HTTPSConnectionPool(host='testnet.binancefuture.com', port=443): Max retries "
                                        f"exceeded with url: /fapi/v2/positionRisk?timestamp={next(ts)}&signature=deadbeef{next(ts)} "
                                        f"(Caused by NewConnectionError('x'))")
    c, calls = client(answer, clk)
    e, _ = mk_engine(); n0 = len(e.health['errors'])
    for _ in range(2):
        try: c._req('POST', '/fapi/v1/order', {}, signed=True, retry=False)
        except requests.RequestException as ex:
            assert 'signature' not in str(ex) and 'timestamp' not in str(ex) and '/fapi/v2/positionRisk' in str(ex)
            e.err(f'exit failed: {ex}')
    new = list(e.health['errors'])[n0:]
    assert len(new) == 1 and 'x2' in new[0][1], new
    e.err('raw: url /fapi/v1/order?symbol=X&timestamp=1&signature=abc')
    assert 'signature' not in list(e.health['errors'])[-1][1]


def test_a_one_off_error_is_closed_after_the_idle_window_but_the_outage_stays_open():
    import app as A
    e, _ = mk_engine()
    e.err('trailing entries: boom'); e.err('manage: down', key='exchange-down')
    old = (E.now_utc() - timedelta(days=3)).isoformat(timespec='seconds')
    for k in ('trailing entries: boom', 'exchange-down'):
        e.health['incidents'][k]['first'] = e.health['incidents'][k]['last'] = old
    srv = A.App.__new__(A.App); srv.loop_ok = 0.0
    out = A.App.health(srv, e, [])
    assert [i['key'] for i in out['incidents']] == ['exchange-down']             # only the keyed one is still open
    assert not e.health['incidents']['trailing entries: boom']['open']
    e2, _ = mk_engine(); e2.err('one off'); e2.health['incidents']['one off']['last'] = old
    e2.err('something else')                                                     # any later err() sweeps too
    assert not e2.health['incidents']['one off']['open']


# --- engine through the REAL client: _req fail-fast -> ExchangeUnavailable -> engine
def _real_client_engine(monkeypatch, resting=False, orphan=False):
    e, _ = mk_engine(); k = opened(e); lot = e.state['lots'][k]
    clk = Clock(); _nosleep(monkeypatch, clk); mode = {'down': False, 'delete_down': False}
    def answer(m, p, q):
        if mode['down'] or (m == 'DELETE' and mode['delete_down']): return BUSY()
        tag = lot['stop_id']
        if p == '/v2/positionRisk':
            return Resp(200, [{'symbol': 'BTCUSDT', 'positionSide': 'LONG', 'positionAmt': str(lot['qty'])}])
        if p == '/v1/openOrders': return Resp(200, [{'orderId': int(tag[2:])}] if tag.startswith('o:') else [])
        if p == '/v1/openAlgoOrders': return Resp(200, {'orders': []})
        if p == '/v1/premiumIndex': return Resp(200, [{'symbol': 'BTCUSDT', 'markPrice': '100'}, {'symbol': 'ETHUSDT', 'markPrice': '50'}])
        if p == '/v1/order' and m == 'GET': return Resp(200, {'status': 'NEW', 'orderId': 9, 'executedQty': '0'})
        if p == '/v1/ticker/bookTicker': return Resp(200, {'bidPrice': '99.9', 'askPrice': '100.1'})
        if m == 'DELETE': return Resp(200, {'status': 'CANCELED'})
        return Resp(500, {'code': -1, 'msg': f'unexpected {m} {p}'})
    c, calls = client(answer, clk)
    e.trade = c
    if orphan: e.state['orphans'] = [['BTCUSDT', 'a:123']]
    if resting:
        e.state['resting_entries']['ME|T|ETHUSDT|LONG'] = dict(
            key='ME|T|ETHUSDT|LONG', sleeve='T', symbol='ETHUSDT', side='LONG', qty=1.0, filled=0.0, cost=0.0, n=1,
            t0=_time.time(), status='open', cid='zmX', price=49.9, placed_t=_time.time(),
            plan=dict(sl=SL, sym='ETHUSDT', side='LONG', qty=1.0, px=50.0, sg=dict(SG, close=50.0)))
    return e, k, c, calls, clk, mode


def _marks(): return {'BTCUSDT': 100.0, 'ETHUSDT': 50.0}


def _orders(calls, n0): return [x for x in calls[n0:] if x[0] == 'POST']


def test_engine_outage_through_the_real_client_is_one_incident_and_sends_nothing(monkeypatch):
    for kw in (dict(), dict(resting=True), dict(orphan=True), dict(resting=True, orphan=True)):
        e, k, c, calls, clk, mode = _real_client_engine(monkeypatch, **kw)
        e.manage(_marks()); assert c.health.state == 'ok', kw
        if kw.get('orphan'): e.state['orphans'] = [['BTCUSDT', 'a:123']]          # its cancel failed earlier
        lots0 = {x: dict(v) for x, v in e.state['lots'].items()}; n0 = len(e.health['errors']); c0 = len(calls)
        mode['down'] = True
        for _ in range(36): e.manage(_marks()); clk.t += 8
        assert c.health.state == 'outage' and _orders(calls, c0) == [], kw           # no order / stop sent while blind
        reads = [x for x in calls[c0:] if x[0] == 'GET' and x[1] in ('/v2/positionRisk', '/v1/openOrders')]
        assert len(reads) < 36, (kw, len(reads))                                      # status checks share the cooldown
        assert {x: dict(v) for x, v in e.state['lots'].items()} == lots0, kw         # nothing resized / closed
        new = [x[1] for x in list(e.health['errors'])[n0:]]
        assert sum('no order was sent' in l for l in new) == 1, (kw, new)
        assert len(new) <= 3, (kw, new)                                              # every source coalesced
        assert not any('next try' in l or 'signature' in l for l in new), new
        if kw.get('orphan'): assert e.state['orphans'] == [['BTCUSDT', 'a:123']]     # kept for a later retry
        if kw.get('resting'): assert 'ME|T|ETHUSDT|LONG' in e.state['resting_entries']
        assert e.entry_block(SL, 'ETHUSDT', 'LONG') == 'Binance outage - no new entries until it answers again'
        mode['down'] = False; clk.t += 120
        e.manage(_marks())
        assert c.health.state == 'ok' and not e.health['incidents']['exchange-down']['open'], kw
        assert any('answering again' in x[1] for x in list(e.health['errors'])[n0:]), kw


def test_engine_with_an_orphan_cancel_failing_still_reconciles_and_allows_entries(monkeypatch):
    e, k, c, calls, clk, mode = _real_client_engine(monkeypatch, orphan=True, resting=True)
    mode['delete_down'] = True
    for _ in range(30): e.manage(_marks()); clk.t += 8
    assert c.health.state == 'ok' and c.health.fails == 0
    reads = [x for x in calls if x[0] == 'GET' and x[1] == '/v2/positionRisk']
    assert len(reads) >= 30                                       # positions re-read every pass
    assert e.state['orphans'] == [['BTCUSDT', 'a:123']]
    assert 'exchange-down' not in e.health['incidents']
    assert e.entry_block(SL, 'ETHUSDT', 'LONG') != 'Binance outage - no new entries until it answers again'


def test_grid_adds_are_refused_during_an_outage():
    e, h, clk, down = _outage_engine()
    g = dict(sym='BTCUSDT')
    assert e.grids._can_add(g, 'LONG')
    for _ in range(3): h.fail('x')
    assert not e.grids._can_add(g, 'LONG')


def test_algo_endpoint_errors_other_than_unsupported_mean_unknown(monkeypatch):
    _nosleep(monkeypatch); clk = Clock()
    for code, status, swallowed in ((-5000, 404, True), (-2015, 401, False), (-1102, 400, False), (-4164, 400, False)):
        c, _ = client(lambda m, p, q: Resp(200, []) if p == '/v1/openOrders' else Resp(status, {'code': code, 'msg': 'x'}), clk)
        try: tags = c.open_stop_tags('BTCUSDT'); assert swallowed and tags == set(), code
        except BC.BinanceError as ex: assert not swallowed and ex.code == code
    c, _ = client(lambda m, p, q: Resp(200, []) if p == '/v1/openOrders' else Resp(404, None), clk)
    c._once = lambda m, u, p, s: (Resp(200, []), []) if u.endswith('/openOrders') else \
        (Resp(404, None), {'code': -1404, 'msg': 'HTTP 404 (not JSON)'})
    assert c.open_stop_tags('BTCUSDT') == set()


def test_main_loop_guard_step_routes_an_outage_to_the_incident_without_raising():
    import app as A, inspect
    src = inspect.getsource(A.App)
    assert "e.exchange_down_incident('daily guards')" in src and "if not e._exchange_down(ex): raise" in src
    e, _ = mk_engine(); n0 = len(e.health['errors'])
    for _ in range(3): e.exchange_down_incident('daily guards')
    assert len(list(e.health['errors'])[n0:]) == 1 and e.health['incidents']['exchange-down']['count'] == 3


def test_a_manual_stop_move_asks_for_a_critical_read_when_the_cache_is_stale():
    e, _ = mk_engine(); k = opened(e)
    e.marks_t = _time.time() - 3600                              # stale cache: only a critical read can validate it
    real = e.trade.marks
    def marks(critical=False):
        if not critical: raise BC.ExchangeUnavailable('Binance outage: status check /fapi/v1/premiumIndex not sent', 2.0)
        return real()
    e.trade.marks = marks
    n = e.trade.calls.count('stop')
    e.move_stop(k, 90.0)
    assert e.trade.calls.count('stop') == n + 1 and e.state['lots'][k]['stop'] == 90.0
