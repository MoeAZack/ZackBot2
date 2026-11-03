import hashlib
import hmac
import io
import socket
import ssl

import pytest

from newcore.ports.keys import client_id_for
from newcore.ports.venue import OrderRef, OutcomeKind
from newcore.venue import (
    TESTNET_BASE_URL,
    BinanceUsdMTestnetVenue,
    HttpRequest,
    HttpResponse,
    RateEvidence,
    TestnetHttpSender,
)
from newcore.venue import http_sender as transport

NOW = 1_799_000_000_000
CID = client_id_for('int_' + '12' * 16)
REF = OrderRef(symbol='BTCUSDT', client_id=CID)
QUERY = 'symbol=BTCUSDT&recvWindow=5000&timestamp=1799000000000&signature=' + 'a' * 64


class Credentials:
    def api_key(self):
        return 'test-key'

    def sign(self, payload):
        return hmac.new(b'test-secret', payload, hashlib.sha256).hexdigest()


class Sender:
    def __init__(self, answer):
        self.answer = answer
        self.requests = []

    def __call__(self, request):
        self.requests.append(request)
        if isinstance(self.answer, BaseException):
            raise self.answer
        return self.answer


def venue(sender, observer=None):
    return BinanceUsdMTestnetVenue(environment='testnet', base_url=TESTNET_BASE_URL, sender=sender,
                                   credentials=Credentials(), clock=lambda: NOW, rate_observer=observer)


def record():
    return b'{"symbol":"BTCUSDT","clientOrderId":"' + CID.encode() + b'","orderId":123,"status":"NEW"}'


class FakeSocket:
    def __init__(self, reply):
        self.reply = reply

    def settimeout(self, _value):
        pass

    def connect(self, _address):
        pass

    def sendall(self, _data):
        pass

    def makefile(self, _mode):
        return io.BytesIO(self.reply)

    def shutdown(self, _how):
        pass

    def close(self):
        pass


class FakeContext:
    def wrap_socket(self, sock, *, server_hostname):
        assert server_hostname == 'testnet.binancefuture.com'
        return sock


def local_response(monkeypatch, headers):
    body = b'{}'
    wire = (b'HTTP/1.1 200 OK\r\n' + headers + b'Content-Length: 2\r\n\r\n' + body)
    sock = FakeSocket(wire)
    address = [(socket.AF_INET, socket.SOCK_STREAM, socket.IPPROTO_TCP, '', ('127.0.0.1', 443))]
    monkeypatch.setattr(transport.socket, 'getaddrinfo', lambda *args: address)
    monkeypatch.setattr(transport.socket, 'socket', lambda *args: sock)
    monkeypatch.setattr(transport.ssl, 'create_default_context', lambda **kwargs: FakeContext())
    return TestnetHttpSender()(HttpRequest('GET', TESTNET_BASE_URL + '/fapi/v1/order', QUERY,
                                           (('X-MBX-APIKEY', 'test-key'),), 2.0))


def test_sender_exposes_only_sanitized_numeric_rate_fields(monkeypatch):
    response = local_response(monkeypatch, b'rEtRy-AfTeR: 7\r\nX-MbX-UsEd-WeIgHt-1m: 123\r\nX-Secret: leak\r\n')
    assert response == HttpResponse(200, b'{}', retry_after_ms=7000, used_weight_1m=123)
    assert not hasattr(response, 'headers') and 'leak' not in repr(response)


@pytest.mark.parametrize('headers,expected_retry,expected_weight', [
    (b'Retry-After: 5\r\nRetry-After: 6\r\nX-MBX-USED-WEIGHT-1M: 4\r\n', None, 4),
    (b'Retry-After: +5\r\nX-MBX-USED-WEIGHT-1M: -1\r\n', None, None),
    (b'Retry-After: Wed, 21 Oct 2015 07:28:00 GMT\r\nX-MBX-USED-WEIGHT-1M: 1.5\r\n', None, None),
    (b'Retry-After: 9223372036854776\r\nX-MBX-USED-WEIGHT-1M: 9223372036854775808\r\n', None, None),
    (b'Retry-After: \r\nX-MBX-USED-WEIGHT-1M: ' + '１２'.encode() + b'\r\n', None, None),
])
def test_malformed_duplicate_or_overflow_headers_are_unknown_not_zero(monkeypatch, headers,
                                                                       expected_retry, expected_weight):
    response = local_response(monkeypatch, headers)
    assert response.retry_after_ms == expected_retry
    assert response.used_weight_1m == expected_weight


@pytest.mark.parametrize('status,body,detail', [
    (307, b'{}', 'unexpected_status'),
    (418, b'{"code":-1003}', 'venue_rejected'),
    (429, b'{"code":-1003}', 'venue_rejected'),
    (503, b'{}', 'http_5xx'),
    (200, b'{', 'malformed'),
])
def test_observer_runs_once_before_every_real_response_is_interpreted(status, body, detail):
    seen = []
    sender = Sender(HttpResponse(status, body, retry_after_ms=3000, used_weight_1m=55))
    out = venue(sender, seen.append).query(REF)
    assert seen == [RateEvidence(status, 3000, 55, NOW)]
    assert len(sender.requests) == 1
    if status in (418, 429):
        assert out.kind is OutcomeKind.REJECTED and out.detail == detail
    else:
        assert out.kind is OutcomeKind.UNKNOWN and out.detail == detail


def test_transport_failure_emits_no_rate_evidence_and_never_retries():
    seen = []
    sender = Sender(TimeoutError('no complete response'))
    out = venue(sender, seen.append).query(REF)
    assert out.kind is OutcomeKind.UNKNOWN and out.detail == 'timeout'
    assert seen == [] and len(sender.requests) == 1


def test_observer_failure_is_unknown_and_does_not_retry_or_trust_answer():
    calls = []

    def broken(evidence):
        calls.append(evidence)
        raise RuntimeError('budget unavailable')

    sender = Sender(HttpResponse(200, record(), used_weight_1m=1))
    out = venue(sender, broken).query(REF)
    assert out.kind is OutcomeKind.UNKNOWN and out.detail == 'rate_observer'
    assert len(calls) == len(sender.requests) == 1


@pytest.mark.parametrize('response', [
    HttpResponse(99, b'{}'),
    HttpResponse(200, b'{}', retry_after_ms=True),
    HttpResponse(200, b'{}', used_weight_1m=-1),
])
def test_invalid_fake_metadata_is_bad_response_and_not_observed(response):
    seen = []
    out = venue(Sender(response), seen.append).query(REF)
    assert out.kind is OutcomeKind.UNKNOWN and out.detail == 'bad_http_response'
    assert seen == []
