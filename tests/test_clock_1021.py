"""CLOCK-1021: a Binance -1021 (timestamp / recvWindow) refusal must not leave a position unprotected.

13 Nov 2026, testnet account 4: -1021 refused EX01 ADA's protective stop AND its safety close (each got one resync
retry), and the long sat with no stop for ~2m14s. These tests pin the client fix (midpoint server-time sync, periodic
refresh, an own -1021 retry budget with a widened recvWindow for every request that does not add risk, one plain retry
for an entry) and the engine's clock alert."""
import threading, types

import pytest

import binance_client as BC
import engine as E
from tests.test_safety import mk_engine, opened, SL, SG


class Resp:
    def __init__(self, code, data): self.status_code, self._d, self.headers, self.content = code, data, {}, b'x'
    def json(self): return self._d


R1021_WINDOW = Resp(400, {'code': -1021, 'msg': 'Timestamp for this request is outside of the recvWindow.'})
R1021_AHEAD = Resp(400, {'code': -1021, 'msg': "Timestamp for this request was 1000ms ahead of the server's time."})


def client(script, server_time=None, sync_fail=False):
    """A real Futures (its own _req / _order / sync_time) on a scripted session. server_time(): the /time answer."""
    c = BC.Futures.__new__(BC.Futures)
    c.key, c.secret, c.base, c.rw, c.offset, c.last_ok = 'k', b's', BC.TESTNET, 6000, 0, 0
    calls, syncs = [], []
    def request(method, url, params=None, timeout=None):
        calls.append((method, url.split('/fapi')[1], dict(params or {})))
        r = script.pop(0)
        if isinstance(r, Exception): raise r
        return r
    def get(url, timeout=None):
        syncs.append(timeout)
        if sync_fail: raise BC.requests.ConnectionError('time endpoint down')
        return Resp(200, {'serverTime': server_time() if server_time else int(BC.time.time() * 1000)})
    c.s = types.SimpleNamespace(request=request, headers={}, get=get)
    c._audit = dict(lock=threading.Lock(), s=c.s, until=0.0)
    return c, calls, syncs


NOW = lambda: int(BC.time.time() * 1000)


@pytest.fixture(autouse=True)
def _nosleep(monkeypatch):
    monkeypatch.setattr(BC.time, 'sleep', lambda x: None)


# ------------------------------------------------------------------ the incident: stop + safety close both refused
def test_stop_survives_repeated_1021_with_resync_and_wider_window():
    c, calls, syncs = client([R1021_WINDOW, R1021_WINDOW, Resp(200, {'orderId': 5})])
    assert c.stop('ADAUSDT', 'LONG', '1232', '0.1637') == 'o:5'
    posts = [x for x in calls if x[0] == 'POST']
    assert len(posts) == 3 and len(syncs) == 2 * 2                     # resynced (2 samples) before each re-send
    assert [p[2]['recvWindow'] for p in posts] == [6000, 10000, 20000]
    assert len({p[2]['newClientOrderId'] for p in posts}) == 1          # same client id: a refused order is never a 2nd one
    assert len({p[2]['signature'] for p in posts}) == 3                 # re-signed every time
    assert c.clock_status()['recovered_1021'] == 1


def test_safety_close_survives_three_1021_refusals():
    c, calls, _ = client([R1021_WINDOW] * 3 + [Resp(200, {'orderId': 8, 'status': 'FILLED', 'avgPrice': '0.16'})])
    assert c.close('ADAUSDT', 'LONG', '1232')['orderId'] == 8
    assert [x[2]['recvWindow'] for x in calls] == [6000, 10000, 20000, 30000]


def test_1021_left_after_the_budget_raises_clock_rejected_not_ambiguous():
    c, calls, _ = client([R1021_WINDOW] * 4)
    with pytest.raises(BC.ClockRejected) as ex:
        c.close('ADAUSDT', 'LONG', '1232')
    assert ex.value.code == -1021 and isinstance(ex.value, BC.BinanceError)
    assert 'after 3 resync retries' in str(ex.value)
    assert len(calls) == 1 + BC.CLOCK_RETRIES                          # never an AmbiguousOrder lookup / blind resend
    st = c.clock_status()
    assert st['rejects_1021'] == 4 and st['exhausted_1021'] == 1


def test_clock_retries_do_not_use_up_the_network_retry_budget():
    """A GET that meets -1021 three times and then a 503 still gets its normal busy retries."""
    c, calls, _ = client([R1021_WINDOW] * 3 + [Resp(503, {'code': -1001, 'msg': 'busy'}), Resp(200, [1])])
    assert c._req('GET', '/fapi/v2/account', signed=True) == [1]
    assert len(calls) == 5


def test_ahead_answer_moves_the_timestamp_back():
    c, calls, _ = client([R1021_AHEAD, R1021_AHEAD, Resp(200, {'orderId': 1})], server_time=NOW)
    c.stop('ADAUSDT', 'LONG', '1', '0.1')
    t = [x[2]['timestamp'] for x in calls]
    # (wall clock moves a few ms between sends; the back-off is 1 s, then 2 s)
    assert t[0] - t[1] >= BC.CLOCK_AHEAD_BACK_MS - 200 and t[0] - t[2] >= 2 * BC.CLOCK_AHEAD_BACK_MS - 200


def test_window_answer_does_not_move_the_timestamp_back():
    c, calls, _ = client([R1021_WINDOW, Resp(200, {'orderId': 1})], server_time=NOW)
    c.stop('ADAUSDT', 'LONG', '1', '0.1')
    t = [x[2]['timestamp'] for x in calls]
    assert abs(t[0] - t[1]) < 500


def test_resync_failure_does_not_abort_the_protective_retry():
    c, calls, _ = client([R1021_WINDOW, Resp(200, {'orderId': 2})], sync_fail=True)
    assert c.stop('ADAUSDT', 'LONG', '1', '0.1') == 'o:2'
    assert calls[1][2]['recvWindow'] == 10000
    assert 'ConnectionError' in c.clock_status()['last_sync_error']


# ------------------------------------------------------------------ entries add risk: one plain retry only
def test_entry_gets_one_plain_retry_and_no_wider_window():
    c, calls, _ = client([R1021_WINDOW, R1021_WINDOW])
    with pytest.raises(BC.ClockRejected):
        c.open('ADAUSDT', 'LONG', '10')
    assert [x[2]['recvWindow'] for x in calls] == [6000, 6000]


def test_entry_one_retry_can_still_succeed():
    c, calls, _ = client([R1021_WINDOW, Resp(200, {'orderId': 3, 'status': 'FILLED', 'avgPrice': '1'})])
    assert c.open('ADAUSDT', 'LONG', '10')['orderId'] == 3 and len(calls) == 2


def test_client_with_a_wider_own_window_never_narrows_it():
    c, calls, _ = client([R1021_WINDOW, Resp(200, {'orderId': 1})])
    c.rw = 15000
    c.stop('ADAUSDT', 'LONG', '1', '0.1')
    assert [x[2]['recvWindow'] for x in calls] == [15000, 15000]


# ------------------------------------------------------------------ sync quality and freshness
def test_sync_uses_the_round_trip_midpoint(monkeypatch):
    clock = iter([1000.000, 1000.400])                                    # 400 ms round trip
    monkeypatch.setattr(BC.time, 'time', lambda: next(clock, 1000.4))
    c, _, _ = client([], server_time=lambda: 1_000_200)                  # Binance stamped it at the midpoint, exactly
    c.sync_time()
    assert c.offset == 0                                                  # old formula gave -200
    assert c.__dict__['_sync_rtt_ms'] == 400


def test_sync_keeps_the_fastest_sample(monkeypatch):
    ts = iter([0.0, 2.0, 10.0, 10.1])                                     # sample 1: 2000 ms rtt, sample 2: 100 ms
    monkeypatch.setattr(BC.time, 'time', lambda: next(ts, 10.1))
    answers = iter([5000, 10_050 + 7])                                    # 2nd: true offset +7 ms
    c, _, _ = client([], server_time=lambda: next(answers))
    c.sync_time(samples=2)
    assert c.offset == 7


@pytest.mark.parametrize('bad', [None, 'x', float('nan'), -5, 0, True])
def test_sync_rejects_an_unusable_server_time_and_keeps_the_offset(bad):
    c, _, _ = client([], server_time=lambda: bad)
    c.offset = 123
    with pytest.raises((ValueError, TypeError)):
        c.sync_time()
    assert c.offset == 123


def test_stale_offset_is_refreshed_before_a_signed_request(monkeypatch):
    c, calls, syncs = client([Resp(200, {'ok': 1}), Resp(200, {'ok': 1})])
    c.__dict__['_synced_at'] = BC.time.time() - BC.SYNC_EVERY_S - 1
    c._req('GET', '/fapi/v2/account', signed=True)
    assert syncs == [3]                                                   # short timeout, one sample
    c._req('GET', '/fapi/v2/account', signed=True)
    assert len(syncs) == 1                                                # fresh now: no second sync


def test_failed_refresh_is_retried_after_the_gap_not_on_every_call():
    c, calls, syncs = client([Resp(200, {'ok': 1})] * 3, sync_fail=True)
    c.__dict__['_synced_at'] = 0.0
    for _ in range(3): c._req('GET', '/fapi/v2/account', signed=True)
    assert len(syncs) == 1


def test_unsigned_request_never_syncs():
    c, _, syncs = client([Resp(200, [1])])
    c.__dict__['_synced_at'] = 0.0
    c._req('GET', '/fapi/v1/klines')
    assert syncs == []


# ------------------------------------------------------------------ engine: the clock alert
def test_engine_raises_one_keyed_clock_alert_when_stop_and_close_are_refused():
    e, _ = mk_engine()
    sent = []; e.notify = sent.append
    e.trade.clock_status = lambda: dict(offset_ms=-2500, rtt_ms=7100, sync_age_s=4, recv_window_ms=6000)
    real_stop, real_close = e.trade.stop, e.trade.close
    def stop(*a, **k): raise BC.ClockRejected(-1021, 'outside of the recvWindow')
    def close(*a, **k): raise BC.ClockRejected(-1021, 'outside of the recvWindow')
    e.trade.stop, e.trade.close = stop, close
    assert e.open_lot(SL, 'BTCUSDT', 'LONG', SG, None, e.equity())       # kept (unprotected) - as before
    inc = e.health['incidents'].get('clock-1021')
    assert inc and inc['open'] and inc['count'] == 2                      # stop + safety close, one incident
    assert 'rtt_ms 7100' in inc['msg'] and 'offset_ms -2500' in inc['msg']
    assert sum('-1021' in m for m in sent) == 1                           # one phone alert (rate limited)
    assert any('NO stop' in m for m in sent)                              # the existing unprotected alert still fires
    e.trade.stop, e.trade.close = real_stop, real_close
    lot = next(iter(e.state['lots'].values()))
    e.manage(e.trade.marks())
    assert lot['stop_id'] and not lot['stop_dirty']                       # restored on the next pass


def test_engine_clock_alert_ignores_other_errors():
    e, _ = mk_engine()
    sent = []; e.notify = sent.append
    assert e._clock_alert('x', BC.BinanceError(-2021, 'would trigger')) is False
    assert e._clock_alert('x', RuntimeError('boom')) is False
    assert 'clock-1021' not in e.health.get('incidents', {}) and not sent


def test_engine_clock_alert_survives_a_fake_without_clock_status():
    e, _ = mk_engine()
    e.notify = lambda m: None
    assert e._clock_alert('stop X', BC.BinanceError(-1021, 'ts')) is True
    assert 'clock-1021' in e.health['incidents']


def test_maker_entry_is_sent_as_risk_adding():
    from tests.test_v31_engine import maker_engine
    e, bk = maker_engine()
    seen = []; orig = bk.order
    def order(p, adds_risk=False): seen.append(adds_risk); return orig(p, adds_risk)
    e.trade._order = order
    e.open_lot(SL, 'BTCUSDT', 'LONG', SG, None, e.equity())
    assert seen == [True]                                                  # one plain -1021 retry only, no wider window


def test_persistent_network_error_still_ends_after_the_normal_retries():
    c, calls, _ = client([BC.requests.ConnectionError('down')] * 10)
    with pytest.raises(BC.requests.ConnectionError):
        c._req('GET', '/fapi/v2/account', signed=True, critical=True)   # no outage circuit: full budget
    assert len(calls) == 4


def test_persistent_busy_answer_still_ends_after_the_normal_retries():
    c, calls, _ = client([Resp(503, {'code': -1001, 'msg': 'busy'})] * 10)
    with pytest.raises(BC.BinanceError):
        c._req('GET', '/fapi/v2/account', signed=True, critical=True)   # no outage circuit: full budget
    assert len(calls) == 4
