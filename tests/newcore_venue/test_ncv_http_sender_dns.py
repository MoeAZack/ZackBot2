"""Codex #13: name resolution is bounded by the deadline and can never lead to a late send; connect / TLS / send happen
only in the caller thread, to the resolved IP, with hostname verification against the PINNED host. Local stubs only."""
import json
import socket
import ssl
import subprocess
import threading
import time
from decimal import Decimal as D

import pytest

from newcore.ports import keys as K
from newcore.ports import venue as P
from newcore.venue import http_sender as HS
from newcore.venue.cassette import CassettePlayer, CassetteRecorder
from newcore.venue.credentials import StaticCredentials
from newcore.venue.guard import TESTNET_BASE_URL
from newcore.venue.outcomes import ReadKind
from newcore.venue.testnet_venue import TestnetVenue
from newcore.venue.transport import BinanceTestnetTransport, PositionMode
from newcore.venue.wire import HttpRequest, WireConnectionError, WireNotSent, WireTimeout

from ncv_support import DUMMY_KEY, DUMMY_SECRET, NOW_MS, FakeHttp
from test_ncv_http_sender import Handler, Stub, _openssl

_real_getaddrinfo = socket.getaddrinfo


@pytest.fixture(autouse=True)
def no_internet(monkeypatch):
    def guarded(host, *args, **kwargs):
        if host not in ('127.0.0.1', 'localhost'):
            raise socket.gaierror(socket.EAI_NONAME, 'blocked in tests: no real internet')
        return _real_getaddrinfo(host, *args, **kwargs)
    monkeypatch.setattr(socket, 'getaddrinfo', guarded)


def make_tls_stub(tmp_path, san, mode='ok'):
    exe = _openssl()
    if exe is None:
        pytest.skip('openssl not available to make a throw-away certificate')
    cert, key = str(tmp_path / f'{abs(hash(san))}.pem'), str(tmp_path / f'{abs(hash(san))}.key')
    r = subprocess.run([exe, 'req', '-x509', '-newkey', 'rsa:2048', '-nodes', '-keyout', key, '-out', cert, '-days',
                        '2', '-subj', '/CN=zb-test', '-addext', f'subjectAltName={san}'],
                       capture_output=True, text=True, timeout=60)
    if r.returncode != 0:
        pytest.skip('openssl could not make a test certificate')
    srv = Stub(('127.0.0.1', 0), Handler)
    srv.mode = mode
    sctx = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
    sctx.load_cert_chain(cert, key)
    srv.socket = sctx.wrap_socket(srv.socket, server_side=True)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    return srv, cert


@pytest.fixture
def stubs():
    made = []
    yield made
    for srv in made:
        srv.shutdown()
        srv.server_close()


def trusting(cert):
    ctx = ssl.create_default_context(cafile=cert)
    ctx.verify_flags &= ~getattr(ssl, 'VERIFY_X509_STRICT', 0)
    return ctx


class Resolver:
    """A fake getaddrinfo that answers the stub address, optionally only after `delay` s or after `release`."""

    def __init__(self, port, delay=0.0, release=None, fail=None, answer=None):
        self.port, self.delay, self.release, self.fail = port, delay, release, fail
        self.answer, self.calls, self.returned = answer, [], threading.Event()

    def __call__(self, host, port, family=0, type_=0):
        self.calls.append((host, port))
        try:
            if self.release is not None:
                self.release.wait(5)
            time.sleep(self.delay)
            if self.fail is not None:
                raise self.fail
            if self.answer is not None:
                return self.answer
            return [(socket.AF_INET, socket.SOCK_STREAM, 6, '', ('127.0.0.1', self.port))]
        finally:
            self.returned.set()


class CountingDial:
    def __init__(self):
        self.calls, self.threads = 0, []

    def __call__(self, family, sockaddr, timeout, track):
        self.calls += 1
        self.threads.append(threading.get_ident())
        return HS._dial_tcp(family, sockaddr, timeout, track)


def req(timeout=1.0, path='/fapi/v1/time'):
    return HttpRequest('GET', TESTNET_BASE_URL + path, '', (), timeout, False)


PINNED_SAN = 'DNS:testnet.binancefuture.com'


# ---------- the deadline bounds DNS, and a late resolution never sends ----------

def test_blocked_resolver_returns_within_deadline_and_never_sends_later(tmp_path, stubs):
    srv, cert = make_tls_stub(tmp_path, PINNED_SAN)
    stubs.append(srv)
    release = threading.Event()
    resolver, dial = Resolver(srv.server_address[1], release=release), CountingDial()
    s = HS.TestnetHttpSender(ssl_context=trusting(cert), getaddrinfo=resolver, dial=dial)
    t0 = time.monotonic()
    with pytest.raises(WireNotSent) as ei:
        s(req(timeout=0.3))
    assert time.monotonic() - t0 < 0.3 + 0.25 and ei.value.reason == 'dns_timeout'
    release.set()                                   # the resolver now finishes WITH a valid address...
    assert resolver.returned.wait(5)
    time.sleep(0.3)                                 # ...and has every chance to (wrongly) continue
    assert dial.calls == 0 and srv.hits == 0        # zero connects, zero sends after the timeout


def test_resolver_answering_after_the_timeout_still_never_sends(tmp_path, stubs):
    srv, cert = make_tls_stub(tmp_path, PINNED_SAN)
    stubs.append(srv)
    resolver, dial = Resolver(srv.server_address[1], delay=0.6), CountingDial()
    s = HS.TestnetHttpSender(ssl_context=trusting(cert), getaddrinfo=resolver, dial=dial)
    with pytest.raises(WireNotSent):
        s(req(timeout=0.2))
    assert resolver.returned.wait(5)
    time.sleep(0.3)
    assert dial.calls == 0 and srv.hits == 0


def test_dns_timeout_through_the_transport_and_the_port_is_not_sent(tmp_path, stubs):
    srv, cert = make_tls_stub(tmp_path, PINNED_SAN)
    stubs.append(srv)
    resolver = Resolver(srv.server_address[1], delay=0.6)
    sender = HS.TestnetHttpSender(ssl_context=trusting(cert), getaddrinfo=resolver)
    t = BinanceTestnetTransport(environment='testnet', http=sender, clock=lambda: NOW_MS,
                                position_mode=PositionMode.HEDGE, timeout_s=0.2,
                                credentials=StaticCredentials(DUMMY_KEY, DUMMY_SECRET))
    ref = P.OrderRef(symbol='SOLUSDT', client_id=K.client_id_for('int_' + '3' * 32))
    out = TestnetVenue(t, lambda: NOW_MS).submit_market(P.MarketOrder(ref=ref, position_side='LONG', qty=D('1'),
                                                                        reduce=False))
    assert out.kind is P.OutcomeKind.UNKNOWN and out.detail == 'not_sent_dns_timeout'
    assert resolver.returned.wait(5)
    time.sleep(0.3)
    assert srv.hits == 0


@pytest.mark.parametrize('kw,reason', [(dict(fail=socket.gaierror(socket.EAI_NONAME, 'x')), 'dns_failed'),
                                       (dict(answer=[]), 'dns_failed'),
                                       (dict(answer=[(getattr(socket, 'AF_UNIX', 1), socket.SOCK_STREAM, 0, '',
                                                      '/x')]), 'dns_failed')])
def test_resolution_failures_are_not_sent(kw, reason):
    dial = CountingDial()
    s = HS.TestnetHttpSender(getaddrinfo=Resolver(1, **kw), dial=dial)
    with pytest.raises(WireNotSent) as ei:
        s(req())
    assert ei.value.reason == reason and dial.calls == 0


def test_default_resolver_is_bounded_too(monkeypatch):
    release = threading.Event()

    def slow(host, *a, **k):
        release.wait(5)
        raise socket.gaierror(socket.EAI_NONAME, 'late')
    monkeypatch.setattr(socket, 'getaddrinfo', slow)
    t0 = time.monotonic()
    with pytest.raises(WireNotSent):
        HS.TestnetHttpSender()(req(timeout=0.2))
    assert time.monotonic() - t0 < 0.45
    release.set()


# ---------- connect by IP, verify the PINNED hostname ----------

def test_connects_to_the_resolved_ip_and_verifies_the_pinned_host(tmp_path, stubs):
    srv, cert = make_tls_stub(tmp_path, PINNED_SAN)
    stubs.append(srv)
    resolver, dial = Resolver(srv.server_address[1]), CountingDial()
    r = HS.TestnetHttpSender(ssl_context=trusting(cert), getaddrinfo=resolver, dial=dial)(req())
    assert r.status == 200 and json.loads(r.body)['method'] == 'GET'
    assert resolver.calls == [('testnet.binancefuture.com', 443)]           # only the pinned host is ever resolved
    assert dial.calls == 1 and dial.threads == [threading.get_ident()]       # connect happens in the caller thread
    assert srv.hits == 1


@pytest.mark.parametrize('san', ['DNS:localhost,IP:127.0.0.1', 'DNS:fapi.binance.com', 'DNS:*.binancefuture.org'])
def test_hostname_verification_is_against_the_pinned_host_even_by_ip(tmp_path, stubs, san):
    srv, cert = make_tls_stub(tmp_path, san)            # trusted CA, but the name is not the pinned host
    stubs.append(srv)
    s = HS.TestnetHttpSender(ssl_context=trusting(cert), getaddrinfo=Resolver(srv.server_address[1]))
    with pytest.raises(WireConnectionError) as ei:
        s(req())
    assert str(ei.value) == 'TLS failure' and not isinstance(ei.value, WireNotSent) and srv.hits == 0


def test_untrusted_certificate_by_ip_is_refused(tmp_path, stubs):
    srv, _ = make_tls_stub(tmp_path, PINNED_SAN)
    stubs.append(srv)
    s = HS.TestnetHttpSender(getaddrinfo=Resolver(srv.server_address[1]))          # system trust store only
    with pytest.raises(WireConnectionError):
        s(req())
    assert srv.hits == 0


def test_watchdog_still_cuts_a_slow_answer_on_the_by_ip_path(tmp_path, stubs):
    srv, cert = make_tls_stub(tmp_path, PINNED_SAN, mode='drip_body')
    stubs.append(srv)
    s = HS.TestnetHttpSender(ssl_context=trusting(cert), getaddrinfo=Resolver(srv.server_address[1]))
    t0 = time.monotonic()
    with pytest.raises(WireTimeout):
        s(req(timeout=1.0))
    assert time.monotonic() - t0 < 2.0


# ---------- cassette + transport keep the not-sent fact ----------

def test_cassette_records_and_replays_not_sent():
    rec = CassetteRecorder(FakeHttp(WireNotSent('name resolution timed out', 'dns_timeout')))
    t = BinanceTestnetTransport(environment='testnet', http=rec, clock=lambda: NOW_MS, position_mode=PositionMode.HEDGE)
    assert t.server_time().unknown_reason == 'not_sent_dns_timeout'
    doc = json.loads(rec.to_json())
    assert doc['interactions'][0]['error'] == 'WireNotSent' and doc['interactions'][0]['reason'] == 'dns_timeout'
    t2 = BinanceTestnetTransport(environment='testnet', http=CassettePlayer(doc), clock=lambda: NOW_MS,
                                 position_mode=PositionMode.HEDGE)
    out = t2.server_time()
    assert out.kind is ReadKind.UNKNOWN and out.unknown_reason == 'not_sent_dns_timeout'
