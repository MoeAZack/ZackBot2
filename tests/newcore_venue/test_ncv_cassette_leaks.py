"""Cowork finding 1: the cassette must redact by NAME and by VALUE in every disguise, learn values it blanks, and
fail closed (CassetteLeak) when anything sensitive is left. Dummy values only; no network."""
import base64
import json
import urllib.parse

import pytest

from newcore.venue.cassette import CassetteLeak, CassetteMismatch, CassettePlayer, CassetteRecorder
from newcore.venue.credentials import StaticCredentials
from newcore.venue.redact import contains_values
from newcore.venue.transport import BinanceTestnetTransport, PositionMode
from newcore.venue.wire import HttpRequest, HttpResponse

from ncv_support import DUMMY_KEY, DUMMY_SECRET, NOW_MS, FakeHttp, raw

URL = 'https://testnet.binancefuture.com/fapi/v1/listenKey'
LK = 'pqrsListenKeyDUMMYzz0123456789abcdefghijklmnopqrstuvwxyzABCDEFGH'
ODD_KEY = 'Ab+Cd/Ef' * 8
SIG = 'ab12' * 16


def req(query='', headers=(), url=URL, method='POST', signed=False):
    return HttpRequest(method, url, query, tuple(headers), 5.0, signed)


def ok(body, headers=None):
    return HttpResponse(200, dict(headers or {}), body if isinstance(body, bytes) else body.encode())


def recorded(*answers, requests=None, redact=()):
    rec = CassetteRecorder(FakeHttp(*answers), redact=redact)
    for r in (requests or [req()] * len(answers)):
        rec(r)
    return rec, rec.to_json()


# ---------- the Cowork repros ----------

def test_listenkey_in_response_body_is_redacted_and_learned():
    rec, text = recorded(ok(json.dumps({'listenKey': LK})))
    assert LK not in text and json.loads(json.loads(text)['interactions'][0]['response']['body_text']) == \
        {'listenKey': '<redacted>'}


def test_listenkey_in_request_query_is_redacted():
    _, text = recorded(ok('{}'), requests=[req(f'listenKey={LK}&symbol=SOLUSDT')])
    q = json.loads(text)['interactions'][0]['request']['query']
    assert ['listenKey', '<redacted>'] in q and ['symbol', 'SOLUSDT'] in q and LK not in text


@pytest.mark.parametrize('echo', [DUMMY_KEY.lower(), DUMMY_KEY.upper(), '-'.join(DUMMY_KEY[i:i + 4] for i in
                                                                               range(0, 64, 4))],
                         ids=['lower', 'upper', 'dash-split'])               # no secret in a node id (#44)
def test_key_echoed_in_another_case_or_split(echo):
    _, text = recorded(raw(400, ('{"code": -2015, "msg": "bad key %s"}' % echo).encode()),
                       requests=[req('a=1', (('X-MBX-APIKEY', DUMMY_KEY),), signed=True)])
    assert not contains_values(text, [DUMMY_KEY])


@pytest.mark.parametrize('enc', [urllib.parse.quote(ODD_KEY, safe=''), urllib.parse.quote(ODD_KEY, safe='').lower(),
                                 urllib.parse.quote_plus(ODD_KEY)],
                         ids=['quote', 'quote-lower', 'quote-plus'])         # no secret in a node id (#44)
def test_key_echoed_percent_encoded(enc):
    assert '%2' in enc.upper()
    _, text = recorded(raw(400, ('{"code": -2015, "msg": "key=%s"}' % enc).encode()),
                       requests=[req('a=1', (('X-MBX-APIKEY', ODD_KEY),), signed=True)])
    assert not contains_values(text, [ODD_KEY]) and enc not in text


@pytest.mark.parametrize('name', ['Signature', 'SIGNATURE', '%73ignature', 'signature'])
def test_signature_query_names_in_any_spelling(name):
    _, text = recorded(ok('{}'), requests=[req(f'symbol=SOLUSDT&{name}={SIG}')])
    assert SIG not in text and SIG.upper() not in text


def test_uppercase_signature_echoed_in_error_body():
    body = ('{"code": -1022, "msg": "Signature %s is not valid."}' % SIG.upper()).encode()
    _, text = recorded(raw(400, body), requests=[req(f'symbol=SOLUSDT&signature={SIG}')])
    assert not contains_values(text, [SIG])


def test_secret_in_set_cookie_and_cookie_headers():
    _, text = recorded(ok('{}', {'Set-Cookie': f'session={DUMMY_SECRET}; Path=/; HttpOnly', 'X-Other': 'fine'}),
                       requests=[req('a=1', (('Cookie', f'session={DUMMY_SECRET}'),))])
    assert DUMMY_SECRET not in text
    it = json.loads(text)['interactions'][0]
    # allow-list (R1): an unknown header keeps its name, never its value
    assert it['response']['headers']['Set-Cookie'] == '<redacted>' and \
        it['response']['headers']['X-Other'] == '<redacted>'
    assert ['Cookie', '<redacted>'] in it['request']['headers']


@pytest.mark.parametrize('short', ['', 'abc', 'seven77', 1234])
def test_short_redact_value_is_refused_not_dropped(short):
    with pytest.raises(ValueError):
        CassetteRecorder(FakeHttp(), redact=(DUMMY_SECRET, short))


def test_short_api_key_on_the_wire_is_refused():
    rec = CassetteRecorder(FakeHttp(ok('{}')))
    with pytest.raises(CassetteLeak):
        rec(req('a=1', (('X-MBX-APIKEY', 'short'),), signed=True))


# ---------- Cowork round 2: UNREGISTERED values under sensitive NAMES are blanked (fixed list, any case) ----------

UNREG = 'unregisteredValue0123456789XYZ'
BODY_QUERY_NAMES = ['apiKey', 'listenKey', 'secretKey', 'token', 'Authorization', 'api_secret', 'password',
                    'X-MBX-APIKEY', 'APIKEY', 'Listen_Key', 'SECRETKEY', 'Password']
HEADER_NAMES = ['Set-Cookie', 'Authorization', 'X-Api-Key', 'Proxy-Authorization', 'set-cookie', 'X-MBX-APIKEY',
                'Cookie']


@pytest.mark.parametrize('name', BODY_QUERY_NAMES)
def test_unregistered_value_under_sensitive_json_key_any_depth(name):
    body = json.dumps({'a': 1, 'outer': [{'inner': {name: UNREG}}], name: UNREG})
    _, text = recorded(ok(body))
    assert UNREG not in text


@pytest.mark.parametrize('name', BODY_QUERY_NAMES)
def test_unregistered_value_under_sensitive_query_name(name):
    q = urllib.parse.urlencode([('symbol', 'SOLUSDT'), (name, UNREG)])
    _, text = recorded(ok('{}'), requests=[req(q)])
    assert UNREG not in text


@pytest.mark.parametrize('name', BODY_QUERY_NAMES)
def test_unregistered_value_under_sensitive_form_name(name):
    _, text = recorded(ok(f'ok=1&{urllib.parse.quote(name)}={UNREG}&x=2'))
    assert UNREG not in text


@pytest.mark.parametrize('name', HEADER_NAMES)
def test_unregistered_value_under_sensitive_response_header(name):
    _, text = recorded(ok('{}', {name: f'Bearer {UNREG}', 'X-MBX-USED-WEIGHT-1M': '3'}))
    assert UNREG not in text and '"X-MBX-USED-WEIGHT-1M": "3"' in text


def test_a_sensitive_key_holding_a_structure_is_blanked_whole():
    # JSON bodies are walked structurally (R1): the whole value of a sensitive / unknown key becomes <redacted>.
    rec = CassetteRecorder(FakeHttp(ok(json.dumps({'token': [UNREG, {'x': UNREG}]}))))
    rec(req())
    text = rec.to_json()
    assert UNREG not in text
    assert json.loads(json.loads(text)['interactions'][0]['response']['body_text']) == {'token': '<redacted>'}


# ---------- learning + back-propagation ----------

def test_value_learned_later_is_removed_from_earlier_interactions():
    _, text = recorded(ok(json.dumps({'note': f'hello {LK}'})), ok(json.dumps({'listenKey': LK})))
    assert LK not in text


def test_learned_value_redacted_in_unnamed_places_of_later_requests():
    _, text = recorded(ok(json.dumps({'listenKey': LK})), ok('{}'),
                       requests=[req(), req(f'symbol=SOLUSDT&x={LK}', url=URL)])
    assert LK not in text


def test_form_and_text_bodies():
    _, text = recorded(ok(f'listenKey={LK}&ok=1'), ok(f'token: x; api_key={DUMMY_KEY}'))
    assert LK not in text and DUMMY_KEY not in text


def test_binary_body_by_name_and_value():
    blob = b'\xff\xfe' + f'"listenKey":"{LK}"'.encode() + b'\x00' + DUMMY_SECRET.encode()
    _, text = recorded(HttpResponse(200, {}, blob), redact=(DUMMY_SECRET,))
    body = base64.b64decode(json.loads(text)['interactions'][0]['response']['body_b64'])
    assert LK.encode() not in body and DUMMY_SECRET.encode() not in body


# ---------- fail closed ----------

def test_nested_unknown_object_is_blanked_whole():
    rec = CassetteRecorder(FakeHttp(ok(json.dumps({'data': {'secret': {'value': 'xxxxxxxxxxxx'}}}))))
    rec(req())
    text = rec.to_json()
    assert json.loads(json.loads(text)['interactions'][0]['response']['body_text']) == {'data': '<redacted>'}


def test_post_hoc_injected_value_fails_closed(tmp_path):
    rec = CassetteRecorder(FakeHttp(ok('{}')), redact=(DUMMY_SECRET,))
    rec(req())
    rec.interactions[0]['response']['body_text'] += DUMMY_SECRET.lower()
    with pytest.raises(CassetteLeak):
        rec.to_json()
    with pytest.raises(CassetteLeak):
        rec.save(str(tmp_path / 'never.json'))
    assert list(tmp_path.iterdir()) == []


def test_post_hoc_injected_sensitive_header_or_param_fails_closed():
    rec = CassetteRecorder(FakeHttp(ok('{}'), ok('{}')))
    rec(req())
    rec(req())
    rec.interactions[0]['response']['headers']['Set-Cookie'] = 'a=b'
    with pytest.raises(CassetteLeak):
        rec.to_json()
    rec.interactions[0]['response']['headers']['Set-Cookie'] = '<redacted>'
    rec.interactions[1]['request']['query'].append(['listenKey', 'zzz'])
    with pytest.raises(CassetteLeak):
        rec.to_json()


def test_post_hoc_injected_sensitive_text_pair_fails_closed():
    # Not JSON, no registered value: only the raw-text name scan can see it.
    rec = CassetteRecorder(FakeHttp(ok('plain text')))
    rec(req())
    rec.interactions[0]['response']['body_text'] = 'note listenKey=abcdefghij end'
    with pytest.raises(CassetteLeak):
        rec.to_json()


def test_replay_signed_request_needs_the_key_header():
    _, text = recorded(ok('{}'), requests=[req('a=1&signature=' + SIG, (('X-MBX-APIKEY', DUMMY_KEY),),
                                               signed=True)])
    p = CassettePlayer(json.loads(text))
    with pytest.raises(CassetteMismatch):
        p(req('a=1&signature=' + SIG, (), signed=True))         # signature present, key header missing


def test_post_hoc_injected_value_in_binary_body_fails_closed():
    rec = CassetteRecorder(FakeHttp(HttpResponse(200, {}, b'\xff\x00ok')), redact=(DUMMY_SECRET,))
    rec(req())
    rec.interactions[0]['response']['body_b64'] = base64.b64encode(b'\xff' + DUMMY_SECRET.encode()).decode()
    with pytest.raises(CassetteLeak):
        rec.to_json()


def test_ordinary_answers_are_untouched():
    body = json.dumps({'symbol': 'SOLUSDT', 'clientOrderId': 'zb-ABCDEFGHIJKLMNOPQRSTUVWX', 'orderId': 1,
                       'msg': 'Signature for this request is not valid.'})
    _, text = recorded(ok(body, {'X-MBX-USED-WEIGHT-1M': '5'}))
    it = json.loads(text)['interactions'][0]
    assert json.loads(it['response']['body_text']) == json.loads(body)
    assert it['response']['headers'] == {'X-MBX-USED-WEIGHT-1M': '5'}


# ---------- replay compares sensitive params by presence ----------

def test_replay_sensitive_param_by_presence():
    _, text = recorded(ok('{}'), requests=[req(f'listenKey={LK}&symbol=SOLUSDT')])
    p = CassettePlayer(json.loads(text))
    p(req(f'listenKey={"other" * 4}&symbol=SOLUSDT'))
    p2 = CassettePlayer(json.loads(text))
    with pytest.raises(CassetteMismatch):
        p2(req('symbol=SOLUSDT'))


def test_full_transport_session_round_trips():
    rec = CassetteRecorder(FakeHttp('account_v2', 'server_time'), redact=(DUMMY_SECRET,))
    t = BinanceTestnetTransport(environment='testnet', http=rec, clock=lambda: NOW_MS,
                                position_mode=PositionMode.HEDGE, credentials=StaticCredentials(DUMMY_KEY, DUMMY_SECRET))
    a, b = t.account(), t.server_time()
    text = rec.to_json()
    assert DUMMY_KEY not in text and DUMMY_SECRET not in text
    t2 = BinanceTestnetTransport(environment='testnet', http=CassettePlayer(json.loads(text)), clock=lambda: NOW_MS + 5,
                                 position_mode=PositionMode.HEDGE,
                                 credentials=StaticCredentials('REPLAY' + 'k' * 58, 'REPLAY' + 's' * 58))
    assert t2.account() == a and t2.server_time() == b
