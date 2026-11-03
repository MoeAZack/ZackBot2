import io
import socket
import ssl
import threading

import pytest

from newcore.venue import HttpRequest, TestnetHttpSender
from newcore.venue import http_sender as transport


QUERY = 'symbol=BTCUSDT&recvWindow=5000&timestamp=1799000000000&signature=' + 'a' * 64
SECRET = 'test-api-key'


def request(**changes):
    values = dict(
        method='GET',
        url='https://testnet.binancefuture.com/fapi/v1/order',
        query=QUERY,
        headers=(('X-MBX-APIKEY', SECRET),),
        timeout_s=2.0,
    )
    values.update(changes)
    return HttpRequest(**values)


class FakeSocket:
    def __init__(self, reply=b'HTTP/1.1 200 OK\r\nContent-Length: 2\r\n\r\n{}', connect_error=None):
        self.reply = reply
        self.connect_error = connect_error
        self.connected = []
        self.timeouts = []
        self.sent = []
        self.closed = 0

    def settimeout(self, value):
        self.timeouts.append(value)

    def connect(self, address):
        self.connected.append(address)
        if self.connect_error:
            raise self.connect_error

    def sendall(self, data):
        self.sent.append(data)

    def makefile(self, _mode):
        return io.BytesIO(self.reply)

    def shutdown(self, _how):
        pass

    def close(self):
        self.closed += 1


class FakeContext:
    def __init__(self):
        self.check_hostname = False
        self.verify_mode = ssl.CERT_NONE
        self.server_names = []

    def wrap_socket(self, sock, *, server_hostname):
        self.server_names.append(server_hostname)
        return sock


def install_local_stack(monkeypatch, *sockets):
    addresses = [
        (socket.AF_INET, socket.SOCK_STREAM, socket.IPPROTO_TCP, '', ('127.0.0.1', 443)),
        (socket.AF_INET, socket.SOCK_STREAM, socket.IPPROTO_TCP, '', ('127.0.0.2', 443)),
    ]
    monkeypatch.setattr(transport.socket, 'getaddrinfo', lambda *args: addresses)
    pending = list(sockets)
    monkeypatch.setattr(transport.socket, 'socket', lambda *args: pending.pop(0))
    context = FakeContext()
    monkeypatch.setattr(transport.ssl, 'create_default_context', lambda **kwargs: context)
    return context


def test_one_local_hit_uses_exact_host_tls_and_closes(monkeypatch):
    sock = FakeSocket()
    context = install_local_stack(monkeypatch, sock)
    out = TestnetHttpSender()(request())
    assert out.status == 200 and out.body == b'{}'
    assert sock.connected == [('127.0.0.1', 443)]
    assert context.server_names == ['testnet.binancefuture.com']
    assert context.check_hostname is True and context.verify_mode == ssl.CERT_REQUIRED
    assert len(sock.sent) == 1 and b'Host: testnet.binancefuture.com\r\n' in sock.sent[0]
    assert b'X-MBX-APIKEY: test-api-key\r\n' in sock.sent[0]
    assert sock.closed >= 1


def test_redirect_is_returned_unchanged_without_second_hit(monkeypatch):
    reply = b'HTTP/1.1 307 Temporary Redirect\r\nLocation: https://evil.invalid/\r\nContent-Length: 4\r\n\r\nmove'
    sock = FakeSocket(reply)
    install_local_stack(monkeypatch, sock)
    out = TestnetHttpSender()(request(method='POST'))
    assert out.status == 307 and out.body == b'move'
    assert len(sock.connected) == len(sock.sent) == 1


def test_multiple_dns_answers_still_make_only_one_connection_attempt(monkeypatch):
    first = FakeSocket(connect_error=OSError('first address failed'))
    second = FakeSocket()
    install_local_stack(monkeypatch, first, second)
    with pytest.raises(OSError, match='^testnet HTTP request failed$'):
        TestnetHttpSender()(request())
    assert len(first.connected) == 1
    assert second.connected == []


def test_dns_timeout_never_creates_a_socket_later(monkeypatch):
    started, release = threading.Event(), threading.Event()

    def blocked_resolver(*_args):
        started.set()
        release.wait(1)
        return [(socket.AF_INET, socket.SOCK_STREAM, socket.IPPROTO_TCP, '', ('127.0.0.1', 443))]

    opened = []
    monkeypatch.setattr(transport.socket, 'getaddrinfo', blocked_resolver)
    monkeypatch.setattr(transport.socket, 'socket', lambda *args: opened.append(args))
    try:
        with pytest.raises(TimeoutError, match='^testnet HTTP deadline exceeded$'):
            TestnetHttpSender()(request(timeout_s=0.01))
        assert started.wait(0.5)
        release.set()
        assert opened == []
    finally:
        release.set()


def test_resolver_pool_is_capped():
    assert transport._RESOLVER._max_workers == 4


def test_response_is_bounded_before_body_read_and_socket_closes(monkeypatch):
    reply = b'HTTP/1.1 200 OK\r\nContent-Length: 8388609\r\n\r\n'
    sock = FakeSocket(reply)
    install_local_stack(monkeypatch, sock)
    with pytest.raises(OSError, match='^testnet HTTP request failed$'):
        TestnetHttpSender()(request())
    assert sock.closed >= 1


@pytest.mark.parametrize('changes', [
    {'method': 'get'},
    {'method': 'PUT'},
    {'url': 'https://fapi.binance.com/fapi/v1/order'},
    {'url': 'https://testnet.binancefuture.com:443/fapi/v1/order'},
    {'url': 'https://testnet.binancefuture.com//evil.invalid/'},
    {'url': 'https://testnet.binancefuture.com/fapi/v1/order?x=1'},
    {'url': 'https://testnet.binancefuture.com/fapi/v1/unknown'},
    {'headers': (('x-mbx-apikey', SECRET),)},
    {'headers': (('X-MBX-APIKEY', SECRET), ('Host', 'evil.invalid'))},
    {'headers': (('X-MBX-APIKEY', 'secret\r\nHost: evil.invalid'),)},
    {'query': 'recvWindow=5000&timestamp=1&signature=aa&bad'},
    {'query': 'recvWindow=5000&timestamp=1&signature=%zz'},
    {'query': 'recvWindow=5000&timestamp=1&timestamp=2&signature=aa'},
    {'query': 'recvWindow=5000&timestamp=1'},
    {'timeout_s': 0},
    {'timeout_s': True},
])
def test_strict_request_grammar_rejects_before_dns(monkeypatch, changes):
    dns = []
    monkeypatch.setattr(transport.socket, 'getaddrinfo', lambda *args: dns.append(args))
    with pytest.raises(ValueError, match='^invalid testnet HTTP request$'):
        TestnetHttpSender()(request(**changes))
    assert dns == []


def test_transport_errors_are_redacted_without_exception_chain(monkeypatch):
    sock = FakeSocket(connect_error=OSError('leaked ' + SECRET + ' ' + QUERY))
    install_local_stack(monkeypatch, sock)
    with pytest.raises(OSError) as caught:
        TestnetHttpSender()(request())
    rendered = repr(caught.value) + str(caught.value)
    assert SECRET not in rendered and 'signature=' not in rendered and QUERY not in rendered
    assert caught.value.__cause__ is None
