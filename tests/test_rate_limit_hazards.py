"""TRATE: rate-limit hazards. H1 - a 429 inside _req must not sleep for long (it runs under the engine lock: one orphan
cancel could block stop restoration / flatten / panel actions for ~90 s). H2 - an HTTP 418 IP ban must never be retried
(retrying while banned extends the ban): it is recorded, and every request fails fast, unsent, until it ends."""
import math, os, sys
import email.utils as eu
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from test_outage import client, Clock, Resp                               # noqa: E402
from test_safety import mk_engine, opened                                 # noqa: E402
import binance_client as BC                                               # noqa: E402
import engine as E                                                        # noqa: E402

BAN = lambda ra='7200': Resp(418, {'code': -1003, 'msg': 'Way too many requests; IP banned until 1760000000000.'},
                             {'Retry-After': ra})
LIMIT = lambda ra='30': Resp(429, {'code': -1003, 'msg': 'Too many requests; current limit is 2400 per minute.'},
                             {'Retry-After': ra} if ra is not None else {})


def _sleeps(monkeypatch, clock=None):
    """Patched time.sleep: records every sleep (and advances the fake clock)."""
    slept = []
    def sl(x):
        slept.append(x)
        if clock is not None: clock.t += x
    monkeypatch.setattr(BC.time, 'sleep', sl)
    return slept


# ------------------------------------------------------------------ H2: 418 IP ban
def test_418_is_never_retried_and_nothing_is_sent_until_the_ban_ends(monkeypatch):
    clk = Clock(); slept = _sleeps(monkeypatch, clk); up = {'v': False}
    c, calls = client(lambda m, p, q: Resp(200, []) if up['v'] else BAN('7200'), clk)
    try: c._req('GET', '/fapi/v1/openOrders', signed=True); raise AssertionError('must raise')
    except BC.RateLimitBan as e: assert e.code == -418 and e.retry_in == 7200 and BC.is_transient(e)
    assert len(calls) == 1 and slept == []                                  # one answer, no retry, no sleep
    n = len(calls)
    for t in (1, 600, 3600, 7199):                                          # reads, critical reads, cancels, orders
        clk.t = 1000.0 + t
        for f in (lambda: c._req('GET', '/fapi/v1/openOrders', signed=True), lambda: c.positions(critical=True),
                  lambda: c.marks(critical=True), lambda: c.get_order('BTCUSDT', 'zbX'), lambda: c.cancel('BTCUSDT', 'o:1'),
                  lambda: c.open_stop_tags('BTCUSDT'), lambda: c.set_leverage('BTCUSDT', 5)):
            try: f(); raise AssertionError('must fail fast')
            except BC.RateLimitBan as e: assert 'not sent' in str(e) and 0 < e.retry_in <= 7200 - t + 0.1
    assert len(calls) == n and slept == []                                  # zero network calls during the ban
    s = c.health.snapshot(); assert 0 < s['ban_s'] <= 1.0 and s['bans'] == 1 and s['ban_refused'] == 28
    clk.t = 1000.0 + 7201; up['v'] = True                                   # ban over: requests resume
    assert c._req('GET', '/fapi/v1/openOrders', signed=True) == [] and len(calls) == n + 1
    assert c.health.snapshot()['ban_s'] == 0.0


def test_an_order_during_the_ban_is_a_definite_failure_never_ambiguous_and_never_sent(monkeypatch):
    clk = Clock(); slept = _sleeps(monkeypatch, clk)
    c, calls = client(lambda *a: BAN('7200'), clk)
    try: c.positions()
    except BC.RateLimitBan: pass
    n = len(calls)
    for f in (lambda: c.open('BTCUSDT', 'LONG', '0.01'), lambda: c.close('BTCUSDT', 'LONG', '0.01'),
              lambda: c.stop('BTCUSDT', 'LONG', '0.01', '90')):
        try: f(); raise AssertionError('must raise')
        except BC.AmbiguousOrder: raise AssertionError('an unsent order must not be ambiguous')
        except BC.BinanceError as e: assert isinstance(e, BC.RateLimitBan) and e.code == -418
    assert len(calls) == n and slept == []                                  # no POST, no confirmation lookup


def test_a_418_answer_to_an_order_post_is_definite_no_lookup_no_resend(monkeypatch):
    clk = Clock(); slept = _sleeps(monkeypatch, clk)
    c, calls = client(lambda *a: BAN('120'), clk)
    try: c.open('BTCUSDT', 'LONG', '0.01'); raise AssertionError('must raise')
    except BC.AmbiguousOrder: raise AssertionError('Binance refused it: not ambiguous')
    except BC.RateLimitBan: pass
    assert [x[0] for x in calls] == ['POST'] and slept == []


def test_ban_duration_is_parsed_defensively_and_bounded(monkeypatch):
    bs, now = BC.ban_seconds, 1_800_000_000.0
    assert bs({'Retry-After': '7200'}) == 7200.0 and bs({'Retry-After': '0'}) == 0.0
    for v in ('nan', 'NaN', '-inf', 'soon', '', '   ', None, '0x10', '1,5'):
        assert bs({'Retry-After': v} if v is not None else {}) == BC.BAN_DEFAULT_S, v
    for v in ('inf', 'Infinity', '1e400', '9e18', '999999999'):
        assert bs({'Retry-After': v}) == BC.BAN_MAX_S, v                     # huge -> 3 days, never overflow / forever
    assert bs({'Retry-After': '-5'}) == 0.0 and bs(None) == BC.BAN_DEFAULT_S and bs('junk') == BC.BAN_DEFAULT_S
    assert 3599 <= bs({'Retry-After': eu.formatdate(now + 3600, usegmt=True)}, now=now) <= 3601
    assert bs({'Retry-After': eu.formatdate(now - 30, usegmt=True)}, now=now) == 0.0
    clk = Clock(); _sleeps(monkeypatch, clk)                                 # through _req: always a finite, bounded ban
    for v, want in (('nan', BC.BAN_DEFAULT_S), ('inf', BC.BAN_MAX_S), ('garbage', BC.BAN_DEFAULT_S), ('1e300', BC.BAN_MAX_S)):
        c, _ = client(lambda *a, v=v: BAN(v), clk)
        try: c._req('GET', '/fapi/v1/klines')
        except BC.RateLimitBan as e: assert e.retry_in == want and math.isfinite(e.retry_in)
        assert math.isfinite(c.health.ban_until) and c.health.ban_until - clk.t == want


def test_a_later_shorter_ban_never_shortens_the_recorded_one():
    clk = Clock(); h = BC.ExchangeHealth(clock=clk)
    h.ban(7200, 'x'); h.ban(5, 'y'); h.ban(0, 'z')
    assert h.ban_until == clk.t + 7200
    h.ban(1e30, 'huge'); assert h.ban_until == clk.t + BC.BAN_MAX_S


# ------------------------------------------------------------------ H1: 429 under the engine lock
def test_a_429_on_a_cancel_fails_fast_without_a_long_sleep(monkeypatch):
    for ra in ('30', '3', None):
        clk = Clock(); slept = _sleeps(monkeypatch, clk)
        c, calls = client(lambda *a, ra=ra: LIMIT(ra), clk)
        try: c.cancel('BTCUSDT', 'o:1'); raise AssertionError('must raise')
        except BC.BinanceError as e: assert e.code == -1003 and BC.is_transient(e) and not isinstance(e, BC.RateLimitBan)
        assert sum(slept) <= BC.RATE_LIMIT_SLEEP_MAX, (ra, slept)
        if ra in ('30', '3'): assert slept == [] and len(calls) == 1, (ra, slept)    # Retry-After beyond the bound: no sleep
        assert c.health.state == 'ok' and c.health.other_fails == len(calls)  # a cancel never drives the circuit
        assert c.health.ban_until is None                                     # a 429 is not a ban


def test_a_429_on_a_status_read_fails_fast_and_a_short_retry_after_is_still_honoured(monkeypatch):
    clk = Clock(); slept = _sleeps(monkeypatch, clk)
    c, calls = client(lambda *a: LIMIT('30'), clk)
    try: c._req('GET', '/fapi/v2/positionRisk', signed=True); raise AssertionError('must raise')
    except BC.BinanceError as e: assert e.code == -1003
    assert slept == [] and len(calls) == 1
    seq = [LIMIT('2'), Resp(200, [1])]                                        # within the bound: unchanged behaviour
    c, calls = client(lambda *a: seq.pop(0), clk); slept.clear()
    assert c._req('GET', '/fapi/v1/klines') == [1] and slept == [2.0]


def test_a_critical_order_confirmation_still_resolves_through_a_short_429(monkeypatch):
    """The POST's answer is lost; its confirmation lookup hits a short 429 and still resolves the order (one POST)."""
    clk = Clock(); slept = _sleeps(monkeypatch, clk)
    script = [Resp(503, {'code': -1007, 'msg': 'timeout'}), LIMIT('1'), Resp(200, {'orderId': 5, 'status': 'FILLED'})]
    c, calls = client(lambda *a: script.pop(0), clk)
    assert c.open('BTCUSDT', 'LONG', '0.01')['orderId'] == 5
    assert [x[0] for x in calls] == ['POST', 'GET', 'GET'] and 1.0 in slept
    script = [Resp(503, {'code': -1007, 'msg': 'timeout'})] + [LIMIT('30')] * 3 + [Resp(200, {'orderId': 6, 'status': 'FILLED'})]
    c, calls = client(lambda *a: script.pop(0), clk); slept.clear()
    assert c.open('BTCUSDT', 'LONG', '0.01')['orderId'] == 6                  # long 429s: lookups fail fast, loop retries
    assert sum(1 for x in calls if x[0] == 'POST') == 1 and max(slept) <= 4  # _order's own 1..4 s spacing, no 30 s sleep


# ------------------------------------------------------------------ engine through the real client
def _engine(monkeypatch, answer_extra):
    e, _ = mk_engine(); k = opened(e); lot = e.state['lots'][k]
    clk = Clock(); slept = _sleeps(monkeypatch, clk)
    def answer(m, p, q):
        r = answer_extra(m, p, q)
        if r is not None: return r
        if p == '/v2/positionRisk': return Resp(200, [{'symbol': 'BTCUSDT', 'positionSide': 'LONG', 'positionAmt': str(lot['qty'])}])
        if p == '/v1/openOrders': return Resp(200, [{'orderId': int(lot['stop_id'][2:])}] if lot['stop_id'].startswith('o:') else [])
        if p == '/v1/openAlgoOrders': return Resp(200, {'orders': []})
        if p == '/v1/order' and m == 'POST': return Resp(200, {'orderId': 777, 'status': 'NEW'})
        if m == 'DELETE': return Resp(200, {'status': 'CANCELED'})
        return Resp(500, {'code': -1, 'msg': f'unexpected {m} {p}'})
    c, calls = client(answer, clk); e.trade = c
    return e, k, lot, c, calls, clk, slept


MARKS = {'BTCUSDT': 100.0, 'ETHUSDT': 50.0}


def test_manage_a_429_on_an_orphan_cancel_neither_blocks_nor_stops_the_stop_restore(monkeypatch):
    e, k, lot, c, calls, clk, slept = _engine(monkeypatch, lambda m, p, q: LIMIT('30') if m == 'DELETE' else None)
    e.state['orphans'] = [['BTCUSDT', 'a:123']]
    lot['stop_dirty'] = True; old = lot['stop_id']
    e.manage(MARKS)
    assert sum(slept) <= BC.RATE_LIMIT_SLEEP_MAX, slept                     # the lock was never held for a long sleep
    stops = [x for x in calls if x[0] == 'POST' and x[2].get('type') == 'STOP_MARKET']
    assert len(stops) == 1 and lot['stop_id'] == 'o:777' and not lot['stop_dirty']   # protection restored this pass
    assert ['BTCUSDT', 'a:123'] in e.state['orphans'] and ['BTCUSDT', old] in e.state['orphans']   # kept for retry
    assert c.health.state == 'ok' and 'exchange-down' not in e.health['incidents']


def test_manage_418_is_one_rate_ban_incident_sends_nothing_then_recovers_once(monkeypatch):
    mode = {'ban': True}
    e, k, lot, c, calls, clk, slept = _engine(monkeypatch, lambda m, p, q: BAN('7200') if mode['ban'] else None)
    lots0 = {x: dict(v) for x, v in e.state['lots'].items()}; n0 = len(e.health['errors'])
    e.manage(MARKS); c0 = len(calls)
    assert c0 == 1 and e.health['incidents']['rate-ban']['open']
    for _ in range(50): e.manage(MARKS); clk.t += 8                            # 400 s of passes inside the ban
    assert len(calls) == c0 and slept == []                                    # zero network, zero sleeping
    assert {x: dict(v) for x, v in e.state['lots'].items()} == lots0          # nothing resized / closed / re-stopped
    new = [x[1] for x in list(e.health['errors'])[n0:]]
    assert sum('HTTP 418' in l for l in new) == 1 and len(new) == 1, new       # coalesced into one line
    assert e.health['incidents']['rate-ban']['count'] == 51 and 'exchange-down' not in e.health['incidents']
    assert e.exchange_state()['ban_s'] > 0
    clk.t = 1000.0 + 7201; mode['ban'] = False                                 # ban over
    e.manage(MARKS)
    assert not e.health['incidents']['rate-ban']['open']
    new = [x[1] for x in list(e.health['errors'])[n0:]]
    assert sum('IP ban over' in l for l in new) == 1 and 'stops re-confirmed' in new[-1], new
    assert [x for x in calls[c0:] if x[1] == '/v2/positionRisk'] and [x for x in calls[c0:] if x[1] == '/v1/openOrders']
    e.manage(MARKS)
    assert sum('IP ban over' in x[1] for x in list(e.health['errors'])[n0:]) == 1


def test_steps_that_do_not_pass_the_error_still_report_the_ban_under_its_own_key(monkeypatch):
    e, k, lot, c, calls, clk, slept = _engine(monkeypatch, lambda m, p, q: BAN('600'))
    try: c.account()
    except BC.RateLimitBan: pass
    e.exchange_down_incident('daily guards'); e.exchange_down_incident('equity record')
    assert e.health['incidents']['rate-ban']['count'] == 2 and 'exchange-down' not in e.health['incidents']
    clk.t += 601                                                               # ban over: generic outage key again
    e.exchange_down_incident('daily guards')
    assert e.health['incidents']['exchange-down']['open']
    assert e._down_why(BC.ExchangeUnavailable('x'))[1] == 'exchange-down'
    assert e._down_why(BC.RateLimitBan('x'))[1] == 'rate-ban'


def test_main_loop_mark_price_ban_goes_to_the_rate_ban_incident(monkeypatch):
    import app as A
    e, _ = mk_engine(); opened(e)
    e.state['last_cycle'] = {tf: E.now_utc().isoformat(timespec='seconds') for tf in A.TF_SEC}
    def marks(*a, **k): raise BC.RateLimitBan('Binance IP ban (HTTP 418): GET /fapi/v1/premiumIndex not sent', 60)
    e.data = type('D', (), {'marks': staticmethod(marks)})()
    app = A.App.__new__(A.App); app.engine = e; app.preview = lambda: None
    class Stop(BaseException): pass
    def sl(x): raise Stop()
    monkeypatch.setattr(A.time, 'sleep', sl)
    try: app.loop()
    except Stop: pass
    assert e.health['incidents'].get('rate-ban', {}).get('open') and 'exchange-down' not in e.health['incidents']
