"""Cowork #37 recorder R1-R4: allow-list recording, encoded secrets, Unicode names, typed depth / size limits, no
exception text stored. DUMMY values only."""
import base64
import json
import time

import pytest

from ncv_support import DUMMY_KEY, DUMMY_SECRET, FakeHttp
from test_ncv_cassette_leaks import ok, req

from newcore.venue.cassette import CassetteLeak, CassettePlayer, CassetteRecorder
from newcore.venue.wire import HttpRequest, WireNotSent

def record(*answers, requests=None, redact=(DUMMY_KEY, DUMMY_SECRET)):
    rec = CassetteRecorder(FakeHttp(*answers), redact=redact)
    for r in (requests or [req()] * len(answers)):
        rec(r)
    return rec


def body_of(rec, i=0):
    return json.loads(rec.to_json())['interactions'][i]['response']['body_text']


# ---------------------------------------------------------------------------------------------- R1 allow-list
def test_r1_unknown_json_fields_keep_their_name_never_their_value():
    rec = record(ok(json.dumps({'symbol': 'SOLUSDT', 'mysteryField': 'x' * 40, 'nested': {'a': [1, 2]},
                                'orderId': 7})))
    doc = json.loads(body_of(rec))
    assert doc == {'symbol': 'SOLUSDT', 'mysteryField': '<redacted>', 'nested': '<redacted>', 'orderId': 7}


def test_r1_numbers_keep_their_exact_text():
    rec = record(ok('{"price": 220.1000, "executedQty": "0.010", "orderId": 12345678901234567890}'))
    assert body_of(rec) == '{"price":220.1000,"executedQty":"0.010","orderId":12345678901234567890}'


@pytest.mark.parametrize('name', ['jwt', 'bearer', 'session', 'credential', 'auth', 'pwd', 'sig', 'key', 'hmac',
                                  'passphrase', 'otp', 'mnemonic', 'seed', 'X-Session', 'X-Auth', 'X-Jwt', 'sid'])
def test_r1_verbatim_secret_names_never_keep_a_value(name):
    v = 'v' * 24
    rec = record(ok(json.dumps({name: v}), {name: v}),
                 requests=[req(f'{name}={v}&symbol=SOLUSDT', ((name, v),))])
    text = rec.to_json()
    assert v not in text


@pytest.mark.parametrize('name', ['jwt', 'session', 'auth', 'pwd', 'key', 'passphrase'])
def test_r1_verbatim_names_in_a_text_body_are_redacted(name):
    v = 'w' * 24
    rec = record(ok(f'<html>{name}={v}; other</html>'))
    assert v not in rec.to_json()


def test_r1_unknown_headers_and_params_are_redacted_and_replay_by_presence():
    rec = record(ok('{}', {'X-New-Header': 'z' * 20, 'X-MBX-USED-WEIGHT-1M': '3'}),
                 requests=[req('symbol=SOLUSDT&newParam=abcdefghij', (('X-Thing', 'y' * 20),))])
    doc = json.loads(rec.to_json())
    it = doc['interactions'][0]
    assert it['response']['headers'] == {'X-New-Header': '<redacted>', 'X-MBX-USED-WEIGHT-1M': '3'}
    assert ['newParam', '<redacted>'] in it['request']['query'] and ['X-Thing', '<redacted>'] in it['request']['headers']
    player = CassettePlayer(doc)
    out = player(HttpRequest('POST', it['request']['url'], 'symbol=SOLUSDT&newParam=OTHERVALUE', (), 5.0, False))
    assert out.status == 200


# ---------------------------------------------------------------------------------------------- R2 encoded secrets
@pytest.mark.parametrize('encode', [
    lambda s: base64.b64encode(s.encode()).decode(),
    lambda s: base64.urlsafe_b64encode(s.encode()).decode().rstrip('='),
    lambda s: s.encode().hex(),
    lambda s: s.encode().hex().upper(),
    lambda s: ''.join(f'\\u{ord(c):04x}' for c in s),
    lambda s: ''.join(chr(0xFF00 + ord(c) - 0x20) if 0x21 <= ord(c) <= 0x7E else c for c in s),   # fullwidth
])
def test_r2_an_encoded_secret_echo_fails_closed(encode):
    rec = record(ok(json.dumps({'msg': 'echo ' + encode(DUMMY_SECRET)})))
    with pytest.raises(CassetteLeak):
        rec.to_json()


def test_r2_ordinary_base64_and_hex_do_not_trip_the_audit():
    rec = record(ok(json.dumps({'msg': base64.b64encode(b'nothing secret here at all').decode() + ' ' +
                                'deadbeef' * 4})))
    assert 'deadbeef' in rec.to_json()


# ---------------------------------------------------------------------------------------------- R3 Unicode names
@pytest.mark.parametrize('name', ['ｓｉｇｎａｔｕｒｅ', 'api​key',
                                  'sig­nature', 'X–MBX–APIKEY'])
def test_r3_unicode_disguised_names_are_redacted_in_json_form_and_params(name):
    v = 'q' * 24
    rec = record(ok(json.dumps({name: v})), ok(f'{name}={v}'), requests=[req(), req(f'{name}={v}')])
    assert v not in rec.to_json()


# ---------------------------------------------------------------------------------------------- R4 limits
def test_r4_a_deeply_nested_answer_is_recorded_without_crashing_and_fails_closed_at_output():
    deep = '[' * 1600 + ']' * 1600
    rec = record(ok(deep))
    assert rec.interactions[0]['response']['body_omitted'] == 'too_deep'
    with pytest.raises(CassetteLeak, match='nested deeper'):
        rec.to_json()


def test_r4_a_huge_answer_is_not_stored_and_is_fast():
    big = ('{"symbol":"SOLUSDT","rows":[' + ','.join('{"a":"%d"}' % i for i in range(900_000)) + ']}').encode()
    assert len(big) > 8 * 1024 * 1024
    t0 = time.monotonic()
    rec = record(ok(big))
    assert time.monotonic() - t0 < 5
    with pytest.raises(CassetteLeak, match='larger than'):
        rec.to_json()


def test_r4_a_large_recordable_answer_is_linear_enough():
    body = ('{"rows":[' + ','.join('{"income":"%d","asset":"USDT","info":"x"}' % i for i in range(60_000)) + ']}')
    t0 = time.monotonic()
    rec = record(ok(body))
    rec.to_json()
    assert time.monotonic() - t0 < 20


def test_r4_an_exception_reason_carrying_a_key_is_not_stored():
    class Boom:
        def __call__(self, r):
            raise WireNotSent('x', 'reason ' + DUMMY_KEY)
    rec = CassetteRecorder(Boom(), redact=(DUMMY_KEY, DUMMY_SECRET))
    with pytest.raises(WireNotSent):
        rec(req())
    assert rec.interactions[0]['reason'] == 'unspecified' and DUMMY_KEY not in rec.to_json()
