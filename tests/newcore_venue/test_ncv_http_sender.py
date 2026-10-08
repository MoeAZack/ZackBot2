"""NC-03 S5: the real stdlib HTTP sender, exercised ONLY against a local stub server on 127.0.0.1.

An autouse fixture makes name resolution of anything except localhost/127.0.0.1 fail, so no test here can reach the
internet even by mistake (that blocked lookup is also the DNS-failure test)."""
import http.client
import http.server
import json
import os
import shutil
import socket
import ssl
import struct
import subprocess
import threading
import time
from decimal import Decimal as D

import pytest

from newcore.venue.guard import TESTNET_BASE_URL, VenueGuardError
from newcore.venue.http_sender import TESTNET_HOST, TestnetHttpSender, default_connect
from newcore.venue.outcomes import OrderOutcomeKind as K, ReadKind
from newcore.venue.transport import BinanceTestnetTransport, PositionMode
from newcore.venue.wire import (HttpRequest, HttpResponse, WireConnectionError, WireResponseTooLarge, WireTimeout)

from newcore.venue.credentials import StaticCredentials

from ncv_support import DUMMY_KEY, DUMMY_SECRET, NOW_MS

_real_getaddrinfo = socket.getaddrinfo


@pytest.fixture(autouse=True)
def no_internet(monkeypatch):
    def guarded(host, *args, **kwargs):
        if host not in ('127.0.0.1', 'localhost'):
            raise socket.gaierror(socket.EAI_NONAME, 'blocked in tests: no real internet')
        return _real_getaddrinfo(host, *args, **kwargs)
    monkeypatch.setattr(socket, 'getaddrinfo', guarded)


class Stub(http.server.ThreadingHTTPServer):
    daemon_threads = True
    mode = 'ok'
    hits = 0

    def handle_error(self, request, client_address):     # resets / aborted clients are expected here
        pass


class Handler(http.server.BaseHTTPRequestHandler):
    def log_message(self, *a):
        pass

    def _reply(self, status, body, headers=None, length=True):
        self.send_response(status)
        for k, v in (headers or {}).items():
            self.send_header(k, v)
        if length:
            self.send_header('Content-Length', str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _handle(self):
        srv = self.server
        srv.hits += 1
        mode = srv.mode
        if mode == 'ok':
            echo = {'method': self.command, 'path': self.path, 'apikey': self.headers.get('X-MBX-APIKEY') is not None,
                    'ua': self.headers.get('User-Agent'), 'serverTime': 1759917600000}
            self._reply(200, json.dumps(echo).encode(), {'Content-Type': 'application/json',
                                                         'X-MBX-USED-WEIGHT-1M': '3'})
        elif mode == 'slow':
            time.sleep(1.0)
            self._reply(200, b'{"serverTime": 1}')
        elif mode == 'reset':
            self.connection.setsockopt(socket.SOL_SOCKET, socket.SO_LINGER, struct.pack('ii', 1, 0))
            self.connection.close()
            self.close_connection = True
        elif mode == 'err500':
            self._reply(500, b'{"code": -1000, "msg": "internal"}')
        elif mode == 'truncated':
            self.send_response(200)
            self.send_header('Content-Length', '100')
            self.end_headers()
            self.wfile.write(b'{"serverTi')
            self.wfile.flush()
            self.connection.shutdown(socket.SHUT_RDWR)
            self.close_connection = True
        elif mode == 'big':
            self._reply(200, b'[' + b'0,' * 2000 + b'0]')
        elif mode == 'big_unsized':
            self.send_response(200)
            self.end_headers()                                # HTTP/1.0, no length: body runs to close
            self.wfile.write(b'[' + b'0,' * 2000 + b'0]')
            self.close_connection = True
        elif mode == 'lying_length':
            self.send_response(200)
            self.send_header('Content-Length', str(50 * 1024 * 1024))   # announces 50 MiB, sends a few bytes
            self.end_headers()
            self.wfile.write(b'{}')
            self.close_connection = True
        elif mode == 'redirect':
            self._reply(302, b'', {'Location': 'https://fapi.binance.com/fapi/v1/time'})

    do_GET = do_POST = do_DELETE = _handle


@pytest.fixture
def stub():
    srv = Stub(('127.0.0.1', 0), Handler)
    th = threading.Thread(target=srv.serve_forever, daemon=True)
    th.start()
    yield srv
    srv.shutdown()
    srv.server_close()


def plain_connect(srv):
    port = srv.server_address[1]
    return lambda host, timeout, ctx: http.client.HTTPConnection('127.0.0.1', port, timeout=timeout)


def req(path='/fapi/v1/time', query='', method='GET', timeout=2.0, headers=(), url=None):
    return HttpRequest(method, url or TESTNET_BASE_URL + path, query, tuple(headers), timeout, False)


def transport_over(sender, timeout=2.0):
    return BinanceTestnetTransport(environment='testnet', http=sender, clock=lambda: NOW_MS,
                                   position_mode=PositionMode.HEDGE, timeout_s=timeout,
                                   credentials=StaticCredentials(DUMMY_KEY, DUMMY_SECRET))


# ---------- happy path ----------

def test_ok_roundtrip_sends_path_query_and_headers(stub):
    s = TestnetHttpSender(connect=plain_connect(stub))
    r = s(req('/fapi/v1/order', 'symbol=SOLUSDT&x=1', 'POST', headers=(('X-MBX-APIKEY', 'k'),)))
    assert isinstance(r, HttpResponse) and r.status == 200
    echo = json.loads(r.body)
    assert echo == {'method': 'POST', 'path': '/fapi/v1/order?symbol=SOLUSDT&x=1', 'apikey': True,
                    'ua': 'zackbot-newcore-venue/1', 'serverTime': 1759917600000}
    assert r.headers['X-MBX-USED-WEIGHT-1M'] == '3'


def test_transport_over_real_sender(stub):
    t = transport_over(TestnetHttpSender(connect=plain_connect(stub)))
    out = t.server_time()
    assert out.kind is ReadKind.OK and out.value == 1759917600000 and out.rate.weight('1m') == 3


# ---------- failure mapping ----------

def test_slow_response_times_out_to_unknown(stub):
    stub.mode = 'slow'
    s = TestnetHttpSender(connect=plain_connect(stub))
    with pytest.raises(WireTimeout) as ei:
        s(req(timeout=0.2))
    assert str(ei.value) == 'timed out'
    out = transport_over(s, timeout=0.2).place_market('SOLUSDT', 'BUY', 'LONG', D('1'), 'zb-a', reduce_only=False)
    assert out.kind is K.UNKNOWN and out.unknown_reason == 'timeout'


def test_connection_reset_to_unknown(stub):
    stub.mode = 'reset'
    s = TestnetHttpSender(connect=plain_connect(stub))
    with pytest.raises(WireConnectionError):
        s(req())
    out = transport_over(s).cancel_order('SOLUSDT', 'zb-a')
    assert out.kind is K.UNKNOWN and out.unknown_reason == 'connection'


def test_5xx_is_returned_and_transport_maps_unknown(stub):
    stub.mode = 'err500'
    s = TestnetHttpSender(connect=plain_connect(stub))
    assert s(req()).status == 500
    out = transport_over(s).place_market('SOLUSDT', 'BUY', 'LONG', D('1'), 'zb-a', reduce_only=False)
    assert out.kind is K.UNKNOWN and out.unknown_reason == 'http_5xx' and out.http_status == 500


def test_truncated_body_to_unknown(stub):
    stub.mode = 'truncated'
    s = TestnetHttpSender(connect=plain_connect(stub))
    with pytest.raises(WireConnectionError) as ei:
        s(req())
    assert str(ei.value) == 'truncated response body'
    assert transport_over(s).positions().kind is ReadKind.UNKNOWN


@pytest.mark.parametrize('mode', ['big', 'big_unsized'])
def test_body_size_is_bounded(stub, mode):
    stub.mode = mode
    s = TestnetHttpSender(connect=plain_connect(stub), max_body_bytes=1024)
    with pytest.raises(WireResponseTooLarge):
        s(req())
    out = transport_over(s).exchange_info()
    assert out.kind is ReadKind.UNKNOWN and out.unknown_reason == 'response_too_large'


def test_announced_oversize_is_refused_before_reading(stub):
    stub.mode = 'lying_length'
    s = TestnetHttpSender(connect=plain_connect(stub), max_body_bytes=1024)
    with pytest.raises(WireResponseTooLarge):           # not "truncated": the header alone exceeds the bound
        s(req())


def test_second_host_check_holds_on_its_own(monkeypatch):
    import newcore.venue.http_sender as hs
    monkeypatch.setattr(hs, 'check_request_url', lambda url: url)      # disable the first layer
    calls = []
    s = TestnetHttpSender(connect=lambda *a: calls.append(a))
    for url in ('https://fapi.binance.com/fapi/v1/time', 'http://testnet.binancefuture.com/fapi/v1/time',
                'https://testnet.binancefuture.com:8443/fapi/v1/time'):
        with pytest.raises(VenueGuardError):
            s(req(url=url))
    assert calls == []


def test_redirect_is_not_followed(stub):
    stub.mode = 'redirect'
    s = TestnetHttpSender(connect=plain_connect(stub))
    r = s(req())
    assert r.status == 302 and stub.hits == 1
    out = transport_over(s).server_time()
    assert out.kind is ReadKind.UNKNOWN and out.unknown_reason == 'http_status_unexpected'


def test_dns_failure_on_default_connect_is_unknown():
    s = TestnetHttpSender()                       # the production connect; the lookup is blocked by the fixture
    with pytest.raises(WireConnectionError) as ei:
        s(req())
    assert str(ei.value) == 'name resolution failed'
    out = transport_over(s).account()
    assert out.kind is ReadKind.UNKNOWN and out.unknown_reason == 'connection'


def test_refused_connection_is_unknown():
    with socket.socket() as sock:                  # a port with nothing listening
        sock.bind(('127.0.0.1', 0))
        port = sock.getsockname()[1]
    s = TestnetHttpSender(connect=lambda h, t, c: http.client.HTTPConnection('127.0.0.1', port, timeout=t))
    # Windows retries a refused SYN for ~2 s, so a short timeout may fire first. Either way: UNKNOWN, never "failed".
    with pytest.raises((WireConnectionError, WireTimeout)):
        s(req(timeout=0.5))
    out = transport_over(s, timeout=0.5).place_market('SOLUSDT', 'BUY', 'LONG', D('1'), 'zb-a', reduce_only=False)
    assert out.kind is K.UNKNOWN and out.unknown_reason in ('connection', 'timeout')


def test_error_messages_never_carry_url_or_headers(stub):
    stub.mode = 'reset'
    s = TestnetHttpSender(connect=plain_connect(stub))
    with pytest.raises(WireConnectionError) as ei:
        s(req('/fapi/v1/order', 'symbol=SOLUSDT&signature=' + 'ab' * 32, 'POST',
              headers=(('X-MBX-APIKEY', DUMMY_KEY),)))
    text = str(ei.value)
    assert 'signature' not in text and DUMMY_KEY not in text and ei.value.__cause__ is None


# ---------- host pin + TLS required ----------

@pytest.mark.parametrize('url', ['https://fapi.binance.com/fapi/v1/time', 'http://testnet.binancefuture.com/fapi/v1/time',
                                 'https://testnet.binancefuture.com:8443/fapi/v1/time',
                                 'https://user@testnet.binancefuture.com/fapi/v1/time',
                                 'https://127.0.0.1/fapi/v1/time', TESTNET_BASE_URL + '/sapi/v1/x'])
def test_sender_refuses_other_targets_before_connecting(url):
    calls = []
    s = TestnetHttpSender(connect=lambda *a: calls.append(a))
    with pytest.raises(VenueGuardError):
        s(req(url=url))
    assert calls == []


@pytest.mark.parametrize('method,timeout', [('PUT', 1.0), ('GET', 0), ('GET', 61), ('GET', True)])
def test_sender_refuses_bad_method_or_timeout(method, timeout):
    calls = []
    s = TestnetHttpSender(connect=lambda *a: calls.append(a))
    with pytest.raises(VenueGuardError):
        s(req(method=method, timeout=timeout))
    assert calls == []


def test_guard_refusal_propagates_through_transport():
    calls = []
    s = TestnetHttpSender(connect=lambda *a: calls.append(a))
    t = transport_over(s)
    object.__setattr__(t, '_timeout', 99.0)               # an out-of-range timeout only the sender catches
    with pytest.raises(VenueGuardError):
        t.server_time()
    assert calls == []


def test_non_verifying_tls_context_refused():
    off = ssl.create_default_context()
    off.check_hostname = False
    with pytest.raises(VenueGuardError):
        TestnetHttpSender(ssl_context=off)
    off.verify_mode = ssl.CERT_NONE
    with pytest.raises(VenueGuardError):
        TestnetHttpSender(ssl_context=off)
    with pytest.raises(VenueGuardError):
        TestnetHttpSender(ssl_context=ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER))


def test_default_connection_is_https_443_with_verifying_context():
    ctx = ssl.create_default_context()
    conn = default_connect(TESTNET_HOST, 5.0, ctx)                 # constructed only; nothing is connected
    assert isinstance(conn, http.client.HTTPSConnection) and conn.host == 'testnet.binancefuture.com'
    assert conn.port == 443 and conn.timeout == 5.0
    assert TestnetHttpSender()._ctx.verify_mode == ssl.CERT_REQUIRED and TestnetHttpSender()._ctx.check_hostname


# ---------- real TLS handshakes against a throw-away local certificate ----------

def _openssl():
    exe = shutil.which('openssl')
    for cand in (r'C:\Program Files\Git\mingw64\bin\openssl.exe', r'C:\Program Files\Git\usr\bin\openssl.exe'):
        if exe is None and os.path.exists(cand):
            exe = cand
    return exe


@pytest.fixture
def tls_stub(tmp_path):
    exe = _openssl()
    if exe is None:
        pytest.skip('openssl not available to make a throw-away localhost certificate')
    cert, key = str(tmp_path / 'cert.pem'), str(tmp_path / 'key.pem')
    r = subprocess.run([exe, 'req', '-x509', '-newkey', 'rsa:2048', '-nodes', '-keyout', key, '-out', cert,
                        '-days', '2', '-subj', '/CN=localhost', '-addext', 'subjectAltName=DNS:localhost,IP:127.0.0.1'],
                       capture_output=True, text=True, timeout=60)
    if r.returncode != 0:
        pytest.skip('openssl could not make a test certificate')
    srv = Stub(('127.0.0.1', 0), Handler)
    sctx = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
    sctx.load_cert_chain(cert, key)
    srv.socket = sctx.wrap_socket(srv.socket, server_side=True)
    th = threading.Thread(target=srv.serve_forever, daemon=True)
    th.start()
    yield srv, cert
    srv.shutdown()
    srv.server_close()


def tls_connect(srv):
    port = srv.server_address[1]
    return lambda host, timeout, ctx: http.client.HTTPSConnection('127.0.0.1', port, timeout=timeout, context=ctx)


def test_tls_untrusted_certificate_is_refused(tls_stub):
    srv, _ = tls_stub
    s = TestnetHttpSender(connect=tls_connect(srv))               # system trust store: the test cert is unknown
    with pytest.raises(WireConnectionError) as ei:
        s(req())
    assert str(ei.value) == 'TLS failure'
    assert transport_over(s).server_time().kind is ReadKind.UNKNOWN


def test_tls_trusted_certificate_works(tls_stub):
    srv, cert = tls_stub
    ctx = ssl.create_default_context(cafile=cert)
    ctx.verify_flags &= ~getattr(ssl, 'VERIFY_X509_STRICT', 0)    # the throw-away self-signed cert is not a strict CA
    s = TestnetHttpSender(ssl_context=ctx, connect=tls_connect(srv))
    assert json.loads(s(req()).body)['method'] == 'GET'


def test_https_client_against_plain_http_fails(stub):
    port = stub.server_address[1]
    s = TestnetHttpSender(connect=lambda h, t, c: http.client.HTTPSConnection('127.0.0.1', port, timeout=t, context=c))
    with pytest.raises(WireConnectionError):
        s(req())
