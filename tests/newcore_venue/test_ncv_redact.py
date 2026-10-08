"""Cowork findings 1 + 3: shared redaction rules (by name, by value in disguise), VenueError message scrubbing,
SecretScrubber strictness. Dummy values only."""
import io
import importlib.util
import os
import urllib.parse
from decimal import Decimal as D

import pytest

from newcore.venue.credentials import SecretScrubber, StaticCredentials
from newcore.venue.errors import scrub_text
from newcore.venue.outcomes import OrderOutcomeKind as K
from newcore.venue.redact import (REDACTED, contains_values, is_sensitive_name, redact_values, scrub_tokens,
                                  value_pattern)
from newcore.venue.transport import BinanceTestnetTransport, PositionMode, VenueInputError

from ncv_support import DUMMY_KEY, DUMMY_SECRET, NOW_MS, FakeHttp, raw

SPLIT_SECRET = 'AbCdEfGh' * 8                    # 64 chars
ODD_KEY = 'Ab+Cd/Ef' * 8                         # a key with '+' and '/' (percent-encodes to %2B / %2F)


def chunks(s, n=8):
    return [s[i:i + n] for i in range(0, len(s), n)]


# ---------- names ----------

@pytest.mark.parametrize('name', ['listenKey', 'LISTEN_KEY', 'listen-key', 'signature', 'Signature', '%73ignature',
                                  'X-MBX-APIKEY', 'apiKey', 'api_key', 'secretKey', 'Set-Cookie', 'cookie', 'token',
                                  'accessToken', 'Authorization', 'password', 'private_key'])
def test_sensitive_names(name):
    assert is_sensitive_name(name)


@pytest.mark.parametrize('name', ['symbol', 'orderId', 'clientOrderId', 'newClientOrderId', 'priceProtect',
                                  'selfTradePreventionMode', 'X-MBX-USED-WEIGHT-1M', 'timestamp', 'recvWindow',
                                  'algoId', 'clientAlgoId', 'positionSide', 'Content-Type'])
def test_ordinary_names_are_not_sensitive(name):
    assert not is_sensitive_name(name)


# ---------- values in disguise ----------

@pytest.mark.parametrize('disguise', [
    DUMMY_SECRET, DUMMY_SECRET.lower(), DUMMY_SECRET.upper(),
    '-'.join(chunks(DUMMY_SECRET, 4)), '_'.join(chunks(DUMMY_SECRET)), ' '.join(chunks(DUMMY_SECRET, 2)),
])
def test_value_found_case_and_separator_insensitive(disguise):
    text = f'error: bad {disguise} here'
    assert contains_values(text, [DUMMY_SECRET])
    out = redact_values(text, [DUMMY_SECRET])
    assert not contains_values(out, [DUMMY_SECRET]) and REDACTED in out


@pytest.mark.parametrize('enc', [urllib.parse.quote(ODD_KEY, safe=''), urllib.parse.quote(ODD_KEY, safe='').lower(),
                                 urllib.parse.quote_plus(ODD_KEY), urllib.parse.quote(ODD_KEY, safe='/')])
def test_value_found_percent_encoded(enc):
    assert '%2' in enc.upper()
    out = redact_values(f'key={enc}&x=1', [ODD_KEY])
    assert not contains_values(out, [ODD_KEY]) and out.endswith('&x=1')


@pytest.mark.parametrize('short', ['', 'abc', '1234567', None, 12345678])
def test_short_or_non_text_values_refused_not_dropped(short):
    with pytest.raises(ValueError):
        value_pattern(short)
    with pytest.raises(ValueError):
        SecretScrubber().register(DUMMY_SECRET, short)


def test_scrubber_register_is_all_or_nothing():
    s = SecretScrubber()
    with pytest.raises(ValueError):
        s.register(DUMMY_SECRET, 'short')
    assert s.redaction_values() == ()


def test_scrubber_scrubs_disguised_values():
    s = SecretScrubber()
    s.register(DUMMY_SECRET)
    for d in (DUMMY_SECRET.lower(), '-'.join(chunks(DUMMY_SECRET, 4)), urllib.parse.quote(DUMMY_SECRET)):
        assert not contains_values(s.scrub(f'[{d}]'), [DUMMY_SECRET])


# ---------- finding 3: scrub_text ----------

@pytest.mark.parametrize('sep', ['_', '-', '+', '/', '='])
def test_scrub_text_blanks_token_runs_split_by_separators(sep):
    out = scrub_text('bad secret ' + sep.join(chunks(SPLIT_SECRET)) + ' end')
    assert 'AbCdEfGh' not in out and REDACTED in out and out.endswith(' end')


def test_scrub_text_redacts_registered_values_even_with_spaces():
    spaced = ' '.join(chunks(DUMMY_SECRET, 4))            # spaces defeat any token-shape rule
    assert DUMMY_SECRET[:4] in scrub_tokens(spaced)
    out = scrub_text(f'msg {spaced} tail', [DUMMY_SECRET])
    assert not contains_values(out, [DUMMY_SECRET]) and DUMMY_SECRET[:4] not in out


def test_scrub_text_keeps_ordinary_messages():
    for msg in ('Order would immediately trigger.', 'Timestamp for this request is outside of the recvWindow.',
                'Path /fapi/v1/openAlgoOrders, Method GET is invalid'):
        assert scrub_text(msg) == msg


def test_scrub_happens_before_truncation():
    out = scrub_text('x' * 230 + DUMMY_SECRET, [DUMMY_SECRET])
    assert DUMMY_SECRET[:10] not in out and len(out) <= 240


# ---------- finding 3 through the transport ----------

def transport(http, scrubber=None, key=DUMMY_KEY):
    return BinanceTestnetTransport(environment='testnet', http=http, clock=lambda: NOW_MS,
                                   position_mode=PositionMode.HEDGE, credentials=StaticCredentials(key, DUMMY_SECRET),
                                   scrubber=scrubber)


@pytest.mark.parametrize('echo', [DUMMY_KEY.lower(), '-'.join(chunks(DUMMY_KEY, 4)), ' '.join(chunks(DUMMY_KEY, 4)),
                                  urllib.parse.quote(DUMMY_KEY)])
def test_venue_error_never_carries_the_key_in_any_disguise(echo):
    body = ('{"code": -2015, "msg": "Invalid API-key %s"}' % echo).encode()
    out = transport(FakeHttp(raw(400, body))).place_market('SOLUSDT', 'BUY', 'LONG', D('1'), 'zb-a',
                                                             reduce_only=False)
    assert out.kind is K.REJECTED and out.error.code == -2015
    assert not contains_values(out.error.msg, [DUMMY_KEY]) and REDACTED in out.error.msg


def test_venue_error_redacts_scrubber_values():
    s = SecretScrubber()
    s.register(DUMMY_SECRET)
    spaced = ' '.join(chunks(DUMMY_SECRET, 4))
    body = ('{"code": -3999, "msg": "weird %s"}' % spaced).encode()
    out = transport(FakeHttp(raw(400, body)), scrubber=s).account()
    assert not contains_values(out.error.msg, [DUMMY_SECRET])


def test_odd_key_percent_encoded_echo_is_redacted():
    body = ('{"code": -2015, "msg": "key %s"}' % urllib.parse.quote(ODD_KEY, safe='')).encode()
    out = transport(FakeHttp(raw(400, body)), key=ODD_KEY).account()
    assert not contains_values(out.error.msg, [ODD_KEY])


def test_transport_rejects_a_scrubber_without_scrub():
    with pytest.raises(VenueInputError):
        transport(FakeHttp(), scrubber=object())


# ---------- the key-entry tool refuses short values instead of dropping them ----------

def test_keys_tool_refuses_short_key_or_secret(tmp_path):
    spec = importlib.util.spec_from_file_location(
        'newcore_keys_tool_r', os.path.join(os.path.dirname(os.path.dirname(os.path.dirname(
            os.path.abspath(__file__)))), 'tools', 'newcore_keys.py'))
    tool = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(tool)
    answers = iter(['shortk', DUMMY_SECRET])
    out = io.StringIO()
    rc = tool.main(['set', '--env', 'testnet', '--account-id', '11111111-2222-4333-8444-555555555555',
                    '--root', str(tmp_path / 's')],
                   prompt=lambda _: next(answers), out=out, harden_acl=False)
    assert rc == 2 and 'at least 8 characters' in out.getvalue() and not os.path.exists(tmp_path / 's')
