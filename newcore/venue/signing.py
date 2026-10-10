"""HMAC-SHA256 request signing for Binance USD-M Futures (SIGNED endpoints).

Binance signs the exact query string that is sent: `signature = HMAC_SHA256(secret, totalParams)`. Everything is sent
in the query string (as the legacy client does, POST included), so totalParams == the query string before
`&signature=`. timestamp and recvWindow come from an injected clock in integer milliseconds; this module never reads
the wall clock.
"""
import hashlib
import hmac
import urllib.parse

BINANCE_RECV_WINDOW_MAX_MS = 60000  # Binance's documented upper bound for recvWindow
RECV_WINDOW_MAX_MS = 10000          # NEWCORE policy cap (Cowork): a stale signed request must not stay valid for a minute
_RESERVED = ('timestamp', 'recvwindow', 'signature')


def hmac_sha256_hex(secret, payload):
    """Lower-case hex HMAC-SHA256 of payload under secret (both bytes)."""
    if not isinstance(secret, (bytes, bytearray)) or not isinstance(payload, (bytes, bytearray)):
        raise TypeError('secret and payload must be bytes')
    return hmac.new(bytes(secret), bytes(payload), hashlib.sha256).hexdigest()


def encode_params(pairs):
    """Deterministic query string from an ordered sequence of (name, str value) pairs. Insertion order is kept."""
    for name, value in pairs:
        if type(name) is not str or type(value) is not str:
            raise TypeError('query parameters must be (str, str) pairs')
    return urllib.parse.urlencode(list(pairs))


def check_ms(value, what):
    if type(value) is not int or value <= 0:
        raise ValueError(f'{what} must be a positive int of milliseconds')
    return value


def check_recv_window(value):
    if type(value) is not int or not 0 < value <= RECV_WINDOW_MAX_MS:
        raise ValueError(f'recv_window_ms must be an int in 1..{RECV_WINDOW_MAX_MS}')
    return value


def signed_query(pairs, now_ms, recv_window_ms, sign):
    """Return (query_without_signature, signature) for a SIGNED request.
    pairs: ordered (name, value) business params; timestamp and recvWindow are appended last (in that order).
    sign: callable(bytes) -> hex str (the CredentialSource's sign; the secret never passes through here)."""
    check_ms(now_ms, 'timestamp')
    check_recv_window(recv_window_ms)
    for n, _ in pairs:                 # case-insensitive and percent-decoded: Signature, SIGNATURE, TimeStamp, ...
        if isinstance(n, str) and urllib.parse.unquote(n).strip().lower() in _RESERVED:
            raise ValueError('timestamp / recvWindow / signature are set by the signer only')
    full = list(pairs) + [('timestamp', str(now_ms)), ('recvWindow', str(recv_window_ms))]
    query = encode_params(full)
    signature = sign(query.encode('ascii'))
    if type(signature) is not str or len(signature) != 64 or any(c not in '0123456789abcdef' for c in signature):
        raise ValueError('credential source returned a malformed signature')
    return query, signature
