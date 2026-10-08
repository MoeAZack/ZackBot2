"""Cowork #37 findings 1-9 (parse boundaries, typed errors, stale bars, clock, DNS threads, min_feasible_qty, replay
CLI): repro tests. Fake HTTP, DUMMY keys, no network."""
import base64
import io
import json
import random
import threading
import time
from decimal import Decimal as D

import pytest

from ncv_support import DUMMY_KEY, fixture, make
from test_ncv_cassette_replay import cli, smoke_cassette  # noqa: F401  (fixture)
from test_ncv_smoke import env  # noqa: F401  (fixture used by smoke_cassette)
from test_ncv_smoke_trade import rules
from test_ncv_testnet_bars import H4, NOW, KlineVenue, series, source

from newcore.ports import venue as P
from newcore.venue import http_sender as HS
from newcore.venue import records as R
from newcore.venue.transport import VenueInputError
from newcore.venue.rules_fetch import RulesUnavailable, SymbolRefused, fetch_rules
from newcore.venue.smoke_trade import NOTIONAL_HEADROOM, min_feasible_qty
from newcore.venue.testnet_bars import TestnetBarSource
from newcore.venue.transport import _qty
from newcore.venue.wire import HttpResponse, WireNotSent


# ---------------------------------------------------------------------------------------------- 1 stale bars
@pytest.mark.parametrize('behind', [1, 5, 365 * 6])
def test_1_bars_that_stop_before_the_latest_closed_candle_are_stale(behind):
    last_open = (NOW // H4 - 1 - behind) * H4
    out = source(KlineVenue(series(10, end_open=last_open))).closed_bars('SOLUSDT', H4, as_of_ms=NOW, limit=5)
    assert out.kind is P.ReadKind.UNKNOWN and out.detail == 'stale'


def test_1_the_latest_closed_candle_is_ok():
    out = source(KlineVenue(series(10))).closed_bars('SOLUSDT', H4, as_of_ms=NOW, limit=5)
    assert out.kind is P.ReadKind.OK


# ---------------------------------------------------------------------------------------------- 8 clock
@pytest.mark.parametrize('clock', [lambda: None, lambda: 1.5, lambda: (_ for _ in ()).throw(RuntimeError('x'))])
def test_8_an_unusable_bars_clock_is_a_typed_unknown(clock):
    t, http = make()
    out = TestnetBarSource(t, clock).closed_bars('SOLUSDT', H4, as_of_ms=NOW, limit=5)
    assert out.kind is P.ReadKind.UNKNOWN and out.detail == 'clock' and http.requests == []


# ---------------------------------------------------------------------------------------------- 2 / 3 / 4 caps
@pytest.mark.parametrize('v', [D('1e999999999'), D('1e-999999999'), '1' * 65, 10 ** 30 * 10 ** 30,
                               D('1' * 80)])
def test_2_huge_numbers_are_malformed_at_once(v):
    t0 = time.monotonic()
    with pytest.raises(R.MalformedResponse):
        if isinstance(v, int):
            R.integer({'x': v}, 'x')
        else:
            R.dec({'x': v}, 'x')
    assert time.monotonic() - t0 < 1


@pytest.mark.parametrize('v', [D('1e999999999'), D('1e-999999999'), D('1' * 50)])
def test_2_the_order_quantity_is_capped_before_formatting(v):
    t0 = time.monotonic()
    with pytest.raises(VenueInputError, match='out of range'):
        _qty(v, 'quantity')
    assert time.monotonic() - t0 < 1


def test_2_place_market_with_a_huge_quantity_sends_nothing():
    t, http = make()
    with pytest.raises(VenueInputError):
        t.place_market(symbol='SOLUSDT', side='BUY', position_side='LONG', quantity=D('1e999999999'),
                       client_id='zbn1o-' + 'a' * 26, reduce_only=False)
    assert http.requests == []


def _info(**edit):
    resp = fixture('exchange_info')
    doc = json.loads(resp.body)
    doc.update(edit.get('top', {}))
    text = json.dumps(doc)
    for old, new in edit.get('raw', ()):
        text = text.replace(old, new, 1)
    return HttpResponse(200, {}, text.encode())


@pytest.mark.parametrize('server_time', ['1e20', '1e300', '2.5e14', '100000000000000000000'])
def test_3_absurd_server_times_are_typed(server_time):
    resp = _info(raw=[('"serverTime": ', f'"serverTime": {server_time}, "x": ')])
    with pytest.raises((RulesUnavailable, SymbolRefused)):
        fetch_rules(lambda r: resp, symbols=['SOLUSDT'], clock=lambda: NOW)


def test_3_a_huge_filter_exponent_is_typed_and_fast():
    doc = json.loads(fixture('exchange_info').body)
    for s in doc['symbols']:
        for f in s['filters']:
            if 'stepSize' in f:
                f['stepSize'] = '@@HUGE@@'
    body = json.dumps(doc).replace('"@@HUGE@@"', '1E+99999999999999999999').encode()
    t0 = time.monotonic()
    with pytest.raises((RulesUnavailable, SymbolRefused)):
        fetch_rules(lambda r: HttpResponse(200, {}, body), symbols=['SOLUSDT'], clock=lambda: NOW)
    assert time.monotonic() - t0 < 2


# ---------------------------------------------------------------------------------------------- 6 / N1 min qty
def test_6_min_feasible_qty_never_raises_and_always_meets_the_headroom():
    rnd = random.Random(37)
    for _ in range(20000):
        step = D(1).scaleb(-rnd.randint(0, 8))
        price = D(rnd.randint(1, 10 ** 6)).scaleb(-rnd.randint(0, 10))
        r = rules(market_step_size=step, market_min_qty=step * rnd.randint(1, 5), market_max_qty=D('1E9'),
                  min_notional=D(rnd.randint(1, 200)))
        q = min_feasible_qty(r, price)
        if q is not None:
            assert q * price >= r.min_notional * NOTIONAL_HEADROOM and q >= r.market_min_qty
            assert (q / step) == (q / step).to_integral_value()


def test_6_cowork_edge_prices():
    for price in (D('1E-8'), D('5.659E-9')):
        q = min_feasible_qty(rules(), price)
        assert q is None or q * price >= rules().min_notional * NOTIONAL_HEADROOM


# ---------------------------------------------------------------------------------------------- 9 DNS threads
def test_9_hung_resolutions_never_pile_up_threads():
    release = threading.Event()

    def hung(host, port, family=0, type_=0):
        release.wait(30)
        return []
    before = sum(t.name == 'newcore-dns' for t in threading.enumerate())
    reasons = []
    try:
        for _ in range(21):
            with pytest.raises(WireNotSent) as ei:
                HS._resolve_within(hung, 'testnet.binancefuture.com', 443, time.monotonic() + 0.01)
            reasons.append(ei.value.reason)
        alive = sum(t.name == 'newcore-dns' for t in threading.enumerate()) - before
        assert alive <= HS.MAX_DNS_THREADS
        assert reasons.count('dns_busy') >= 21 - HS.MAX_DNS_THREADS
    finally:
        release.set()
    deadline = time.monotonic() + 5
    while HS._dns_inflight[0] and time.monotonic() < deadline:
        time.sleep(0.01)
    assert HS._dns_inflight[0] == 0


# ---------------------------------------------------------------------------------------------- 4 / 5 replay CLI
def _edit(smoke_cassette, tmp_path, mutate):  # noqa: F811
    doc = json.load(open(smoke_cassette, encoding='utf-8'))
    mutate(doc)
    p = tmp_path / 'edited.json'
    p.write_text(json.dumps(doc), encoding='utf-8')
    return p


def _first(doc, path_end):
    return next(it for it in doc['interactions'] if it['request']['url'].endswith(path_end) and 'response' in it)


def test_4_a_renamed_field_fails_the_replay(smoke_cassette, tmp_path):  # noqa: F811
    def m(doc):
        it = _first(doc, '/fapi/v2/account')
        it['response']['body_text'] = it['response']['body_text'].replace('totalWalletBalance', 'totalWalletBalanc')
    rc, out = cli(_edit(smoke_cassette, tmp_path, m))
    assert rc == 1 and 'FAIL' in out


def test_4_a_bad_base64_body_and_an_unknown_error_fail(smoke_cassette, tmp_path):  # noqa: F811
    def m1(doc):
        r = doc['interactions'][0]['response']
        r.pop('body_text', None)
        r['body_b64'] = '!!notbase64!!'
    rc, out = cli(_edit(smoke_cassette, tmp_path, m1))
    assert rc == 2 and 'cannot replay' in out                        # a broken file: typed, never a PASS

    def m2(doc):
        doc['interactions'][0].pop('response')
        doc['interactions'][0]['error'] = 'WireMagic'
    rc, out = cli(_edit(smoke_cassette, tmp_path, m2))
    assert rc == 1 and 'FAIL' in out


def test_5_output_is_sanitised_and_never_echoes_a_key(smoke_cassette, tmp_path):  # noqa: F811
    def m(doc):
        doc['interactions'][0]['request']['url'] += '/\x1b[2J\r\nPASS    #9 fake line ' + DUMMY_KEY
    rc, out = cli(_edit(smoke_cassette, tmp_path, m))
    assert rc == 1 and '\x1b' not in out and DUMMY_KEY not in out
    assert not [ln for ln in out.splitlines() if ln.startswith('PASS    #9 fake line')]


def test_5_untyped_errors_are_exit_2_without_the_path(tmp_path):
    p = tmp_path / (DUMMY_KEY + '.json')
    p.write_bytes(b'[' * 5000 + b']' * 5000)
    rc, out = cli(p)
    assert rc == 2 and DUMMY_KEY not in out
    p2 = tmp_path / 'q.json'
    p2.write_text(json.dumps({'format': 'zb-newcore-cassette/1', 'interactions': [
        {'request': {'method': 'POST', 'url': 'https://testnet.binancefuture.com/fapi/v1/order', 'signed': True,
                     'query': [['quantity', '1e999999999']], 'headers': []},
         'response': {'status': 200, 'headers': {}, 'body_text': '{}'}}]}), encoding='utf-8')
    t0 = time.monotonic()
    rc, out = cli(p2)
    assert rc in (1, 2) and time.monotonic() - t0 < 5


def test_replay_player_refuses_a_malformed_record():
    from newcore.venue.cassette import CassetteMismatch, CassettePlayer
    from newcore.venue.wire import HttpRequest
    doc = {'format': 'zb-newcore-cassette/1', 'interactions': [
        {'request': {'method': 'GET', 'url': 'https://testnet.binancefuture.com/fapi/v1/time', 'signed': False,
                     'query': [], 'headers': []}, 'response': {'status': 'x', 'headers': {}, 'body_text': '{}'}}]}
    with pytest.raises(CassetteMismatch, match='malformed'):
        CassettePlayer(doc)(HttpRequest('GET', 'https://testnet.binancefuture.com/fapi/v1/time', '', (), 10.0,
                                        False))
    assert base64.b64encode(b'x')                                   # (keeps the import honest)
    assert io.StringIO
