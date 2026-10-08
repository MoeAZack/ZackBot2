"""NC-03 wire layer: HMAC-SHA256 signing vectors, signed-query layout, credentials and key_digest."""
import copy
import hashlib
import pickle

import pytest

from newcore.venue.credentials import CredentialSource, StaticCredentials, key_digest
from newcore.venue.signing import encode_params, hmac_sha256_hex, signed_query

# Binance's own published signing example (USD-M Futures API docs, "SIGNED Endpoint Examples"). These are public
# documentation values, not credentials. The signature was cross-checked outside Python with
# `openssl dgst -sha256 -hmac <secret>` on 2026-10-08.
DOC_SECRET = '2b5eb11e18796d12d88f13dc27dbbd02c2cc51ff7059765ed9821957d82bb4d9'
DOC_KEY = 'dbefbc809e3e83c283a984c3a1459732ea7db1360ca80c5c2c8867408d28cc83'
DOC_QUERY = ('symbol=BTCUSDT&side=BUY&type=LIMIT&quantity=1&price=9000&timeInForce=GTC'
             '&recvWindow=5000&timestamp=1591702613943')
DOC_SIG = '3c661234138461fcc7a7d8746c6558c9842d4e10870d2ecbedf7777cad694af9'


def test_binance_doc_vector():
    assert hmac_sha256_hex(DOC_SECRET.encode(), DOC_QUERY.encode()) == DOC_SIG


def test_static_credentials_sign_doc_vector():
    creds = StaticCredentials(DOC_KEY, DOC_SECRET)
    assert creds.sign(DOC_QUERY.encode()) == DOC_SIG
    assert creds.api_key() == DOC_KEY
    assert isinstance(creds, CredentialSource)


def test_rfc4231_case_2_vector():
    # RFC 4231 test case 2 (HMAC-SHA-256): key "Jefe", data "what do ya want for nothing?"
    assert hmac_sha256_hex(b'Jefe', b'what do ya want for nothing?') == \
        '5bdcc146bf60754e6a042426089575c75a003f089d2739839dec58b964ec3843'


def test_hmac_requires_bytes():
    with pytest.raises(TypeError):
        hmac_sha256_hex('secret', b'x')
    with pytest.raises(TypeError):
        hmac_sha256_hex(b'secret', 'x')


def test_signed_query_layout_and_signature():
    creds = StaticCredentials(DOC_KEY, DOC_SECRET)
    pairs = [('symbol', 'BTCUSDT'), ('side', 'BUY'), ('type', 'LIMIT'), ('quantity', '1'), ('price', '9000'),
             ('timeInForce', 'GTC')]
    query, sig = signed_query(pairs, 1591702613943, 5000, creds.sign)
    # Order: business params as given, then timestamp, then recvWindow.
    assert query == ('symbol=BTCUSDT&side=BUY&type=LIMIT&quantity=1&price=9000&timeInForce=GTC'
                     '&timestamp=1591702613943&recvWindow=5000')
    # Fixed vector for OUR layout (computed with openssl, independent of the code under test).
    assert sig == '60812c3140e642134fd0b25627abf025774afc1075d87a704aeeca45cadae4aa'


def test_signed_query_is_deterministic_for_same_clock():
    creds = StaticCredentials(DOC_KEY, DOC_SECRET)
    a = signed_query([('symbol', 'SOLUSDT')], 1700000000000, 6000, creds.sign)
    b = signed_query([('symbol', 'SOLUSDT')], 1700000000000, 6000, creds.sign)
    c = signed_query([('symbol', 'SOLUSDT')], 1700000000001, 6000, creds.sign)
    assert a == b and a != c


def test_param_encoding_escapes_reserved_characters():
    assert encode_params([('newClientOrderId', 'zb-a:b/c.d_e'), ('x', 'a b&c=d')]) == \
        'newClientOrderId=zb-a%3Ab%2Fc.d_e&x=a+b%26c%3Dd'


@pytest.mark.parametrize('now', [0, -1, 1.5, True, '1700000000000', None])
def test_bad_clock_value_refused(now):
    with pytest.raises(ValueError):
        signed_query([('symbol', 'SOLUSDT')], now, 5000, lambda b: '0' * 64)


@pytest.mark.parametrize('rw', [0, -5, 60001, 5000.0, True, None])
def test_bad_recv_window_refused(rw):
    with pytest.raises(ValueError):
        signed_query([('symbol', 'SOLUSDT')], 1700000000000, rw, lambda b: '0' * 64)


@pytest.mark.parametrize('rw', [10001, 59999, 60000])
def test_recv_window_policy_cap(rw):
    # Cowork: Binance allows up to 60000; NEWCORE caps at 10000 so a stale signed request cannot linger.
    with pytest.raises(ValueError):
        signed_query([('symbol', 'SOLUSDT')], 1700000000000, rw, lambda b: '0' * 64)
    assert signed_query([('symbol', 'SOLUSDT')], 1700000000000, 10000, lambda b: '0' * 64)


@pytest.mark.parametrize('name', ['Signature', 'SIGNATURE', 'TimeStamp', 'TIMESTAMP', 'recvwindow', 'RECVWINDOW',
                                  '%73ignature', 'sign%61ture', ' signature'])
def test_reserved_names_refused_in_any_case(name):
    with pytest.raises(ValueError):
        signed_query([(name, '1')], 1700000000000, 5000, lambda b: '0' * 64)


@pytest.mark.parametrize('name', ['timestamp', 'recvWindow', 'signature'])
def test_caller_cannot_inject_signer_fields(name):
    with pytest.raises(ValueError):
        signed_query([(name, '1')], 1700000000000, 5000, lambda b: '0' * 64)


@pytest.mark.parametrize('bad', ['', 'ABC', '0' * 63, 'g' * 64, None, b'0' * 64])
def test_malformed_signature_from_source_refused(bad):
    with pytest.raises(ValueError):
        signed_query([('symbol', 'SOLUSDT')], 1700000000000, 5000, lambda b: bad)


def test_key_digest_is_one_way_stable_and_domain_separated():
    d = key_digest(DOC_KEY)
    assert d.startswith('zbk1:') and len(d) == 5 + 64
    assert DOC_KEY not in d
    assert d == key_digest(DOC_KEY)
    assert d != 'zbk1:' + hashlib.sha256(DOC_KEY.encode()).hexdigest()     # domain separated, not a bare sha256
    assert d != key_digest(DOC_KEY[:-1] + 'x')
    assert StaticCredentials(DOC_KEY, DOC_SECRET).key_digest() == d
    # Pinned value so a silent change of the digest scheme (which would orphan every recorded binding) is caught.
    # Computed with `openssl dgst -sha256` over the domain string, a NUL byte and the key (independent of this code).
    assert d == 'zbk1:3db119766b71f30301496b97bd7cb561e98d403114ae7ebac75ce7b95deda173'


def test_digest_does_not_depend_on_secret():
    assert StaticCredentials(DOC_KEY, 'a' * 64).key_digest() == StaticCredentials(DOC_KEY, 'b' * 64).key_digest()


@pytest.mark.parametrize('key,secret', [('', 's'), ('k', ''), (None, 's'), ('k', None), ('k k', 's'), ('k', 's\n'),
                                        ('kéy', 's'), (b'k', 's')])
def test_bad_credentials_refused_without_echo(key, secret):
    with pytest.raises(ValueError) as ei:
        StaticCredentials(key, secret)
    text = str(ei.value)
    for v in (key, secret):
        if isinstance(v, str) and len(v) > 1:
            assert v not in text


def test_credentials_redacted_and_not_copyable():
    creds = StaticCredentials('KEYSENTINEL0123456789', 'SECRETSENTINEL0123456789')
    for text in (repr(creds), str(creds), f'{creds}', f'{creds!r}', '%s' % (creds,)):
        assert 'KEYSENTINEL' not in text and 'SECRETSENTINEL' not in text
        assert '<redacted>' in text
    for fn in (lambda: pickle.dumps(creds), lambda: copy.copy(creds), lambda: copy.deepcopy(creds)):
        with pytest.raises(TypeError):
            fn()
    assert not hasattr(creds, '__dict__')
