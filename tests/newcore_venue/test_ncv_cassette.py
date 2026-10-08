"""NC-03 S5: cassette record (sanitized) / replay seam. Dummy credentials only; no network."""
import base64
import json
from decimal import Decimal as D

import pytest

from newcore.venue.cassette import CASSETTE_FORMAT, CassetteLeak, CassetteMismatch, CassettePlayer, CassetteRecorder
from newcore.venue.credentials import StaticCredentials
from newcore.venue.transport import BinanceTestnetTransport, PositionMode, StopRoute
from newcore.venue.wire import HttpRequest, HttpResponse, WireTimeout

from ncv_support import DUMMY_KEY, DUMMY_SECRET, NOW_MS, FakeHttp, raw

OTHER_KEY = 'REPLAYKEY' + 'z' * 55
OTHER_SECRET = 'REPLAYSECRET' + 'y' * 52
CID, SCID, ACID = 'zb-ABCDEFGHIJKLMNOPQRSTUVWX', 'zb-es-AAAABBBBCCCCDDDDEEEE', 'zb-es-QQQQRRRRSSSSTTTTUUUU'


def transport(http, key=DUMMY_KEY, secret=DUMMY_SECRET, now=NOW_MS):
    return BinanceTestnetTransport(environment='testnet', http=http, clock=lambda: now,
                                   position_mode=PositionMode.HEDGE, credentials=StaticCredentials(key, secret))


def session(t):
    """A small S5-like session: reads, an entry, a protective stop, a query, a cancel, a timeout."""
    return [
        t.server_time(), t.exchange_info(), t.klines('SOLUSDT', '4h', limit=3), t.account(), t.positions('SOLUSDT'),
        t.place_market('SOLUSDT', 'BUY', 'LONG', D('10'), CID, reduce_only=False),
        t.place_stop_market('SOLUSDT', 'LONG', D('10'), D('211.40'), ACID, route=StopRoute.ALGO),
        t.query_order('SOLUSDT', CID), t.cancel_algo_order(ACID), t.user_trades('SOLUSDT', order_id=4000000100),
        t.open_orders('SOLUSDT'), t.account(),
    ]


SCRIPT = ['server_time', 'exchange_info', 'klines_4h', 'account_v2', 'position_risk_hedge', 'order_market_filled',
          'algo_order_new', 'order_query_filled', 'algo_cancel_ack', 'user_trades',
          raw(400, ('{"code": -2015, "msg": "Invalid API-key %s"}' % DUMMY_KEY).encode()),     # venue echoes the key
          WireTimeout()]


def record(tmp_path, redact=(DUMMY_SECRET,)):
    inner = FakeHttp(*SCRIPT)
    rec = CassetteRecorder(inner, redact=redact, note='unit-test session')
    outs = session(transport(rec))
    path = rec.save(str(tmp_path / 'session.json'))
    return path, outs, inner


def test_recorded_cassette_contains_no_key_secret_or_signature(tmp_path):
    path, _, inner = record(tmp_path)
    text = open(path, encoding='utf-8').read()
    sigs = [r.query.rsplit('&signature=', 1)[1] for r in inner.requests if r.signed]
    assert len(sigs) == 9                                    # the signed calls really were signed with the dummy key
    assert DUMMY_KEY not in text and DUMMY_SECRET not in text
    assert not any(s in text for s in sigs)
    doc = json.loads(text)
    assert doc['format'] == CASSETTE_FORMAT and len(doc['interactions']) == 12
    signed = [i for i in doc['interactions'] if i['request']['signed']]
    assert all(['X-MBX-APIKEY', '<redacted>'] in i['request']['headers'] for i in signed)
    assert all(['signature', '<redacted>'] in i['request']['query'] for i in signed)
    assert 'Invalid API-key <redacted>' in doc['interactions'][10]['response']['body_text']
    assert doc['interactions'][11]['error'] == 'WireTimeout'


def test_secret_is_absent_even_without_registering_it(tmp_path):
    path, _, _ = record(tmp_path, redact=())
    assert DUMMY_SECRET not in open(path, encoding='utf-8').read()      # the secret never travels, only its HMAC


def test_replay_reproduces_outcomes_with_other_credentials_and_clock(tmp_path):
    path, recorded, _ = record(tmp_path)
    player = CassettePlayer(path)
    replayed = session(transport(player, key=OTHER_KEY, secret=OTHER_SECRET, now=NOW_MS + 12345))
    assert player.remaining == 0
    player.assert_exhausted()
    for a, b in zip(recorded, replayed):
        assert a.kind == b.kind
        assert getattr(a, 'value', None) == getattr(b, 'value', None)
        assert getattr(a, 'record', None) == getattr(b, 'record', None)
        assert a.rate == b.rate
    assert replayed[5].executed_qty == D('10') and replayed[-1].unknown_reason == 'timeout'


def test_replay_mismatch_propagates_and_is_not_venue_data(tmp_path):
    path, _, _ = record(tmp_path)
    t = transport(CassettePlayer(path))
    t.server_time(); t.exchange_info()
    with pytest.raises(CassetteMismatch) as ei:
        t.klines('BTCUSDT', '4h', limit=3)                   # recorded SOLUSDT
    assert 'symbol' in str(ei.value)


def test_replay_wrong_endpoint_and_exhaustion(tmp_path):
    path, _, _ = record(tmp_path)
    p = CassettePlayer(path)
    with pytest.raises(CassetteMismatch):
        transport(p).account()                               # first recorded request is server_time
    doc = json.loads(open(path, encoding='utf-8').read())
    doc['interactions'] = doc['interactions'][:1]
    p = CassettePlayer(doc)
    t = transport(p)
    t.server_time()
    with pytest.raises(CassetteMismatch):
        t.server_time()


def test_replay_signed_request_must_be_signed(tmp_path):
    path, _, _ = record(tmp_path)
    doc = json.loads(open(path, encoding='utf-8').read())
    item = doc['interactions'][3]                            # account (signed)
    p = CassettePlayer({'format': CASSETTE_FORMAT, 'interactions': [item]})
    q = '&'.join(f'{k}={v}' for k, v in item['request']['query'] if k != 'signature')
    with pytest.raises(CassetteMismatch):
        p(HttpRequest('GET', item['request']['url'], q, (), 5.0, True))


def test_not_a_cassette_refused():
    with pytest.raises(CassetteMismatch):
        CassettePlayer({'format': 'other', 'interactions': []})


def test_binary_body_is_scrubbed_before_encoding(tmp_path):
    blob = b'\xff\xfe' + DUMMY_KEY.encode() + b'\x00tail'
    rec = CassetteRecorder(FakeHttp(HttpResponse(200, {}, blob)))
    transport(rec).account()
    doc = json.loads(rec.to_json())
    body = base64.b64decode(doc['interactions'][0]['response']['body_b64'])
    assert DUMMY_KEY.encode() not in body and b'<redacted>' in body


def test_leak_check_refuses_to_produce_a_cassette(tmp_path):
    rec = CassetteRecorder(FakeHttp('server_time'), redact=(DUMMY_SECRET,))
    transport(rec).server_time()
    rec.interactions[0]['response']['body_text'] += DUMMY_SECRET           # simulate a seam bug
    with pytest.raises(CassetteLeak):
        rec.to_json()
    with pytest.raises(CassetteLeak):
        rec.save(str(tmp_path / 'never.json'))
    assert list(tmp_path.iterdir()) == []


def test_recorder_passes_the_live_answer_through_unmodified():
    leak = raw(400, ('{"code": -2015, "msg": "Invalid API-key %s"}' % DUMMY_KEY).encode())
    inner = FakeHttp(leak)
    rec = CassetteRecorder(inner)
    resp = rec(HttpRequest('GET', 'https://testnet.binancefuture.com/fapi/v2/account', 'timestamp=1&signature=ab',
                           (('X-MBX-APIKEY', DUMMY_KEY),), 5.0, True))
    assert resp is leak                                      # the live run sees the real answer; only the copy is clean
    assert DUMMY_KEY not in repr(rec)
