"""TestnetHttpSender: the real `http` callable for BinanceTestnetTransport (stdlib http.client over TLS).

This is the ONLY module in newcore.venue that may open a socket. It is not imported by the transport; the S5 harness
wires it in explicitly.

Contract (wire.py): return an HttpResponse, or raise WireTimeout / WireConnectionError / WireResponseTooLarge. Exception
messages are fixed strings, never the URL (the query holds the signature) and never a header.
- Host pin (defence in depth): https only, host exactly testnet.binancefuture.com, default port, no user-info, and the
  request URL must also pass guard.check_request_url. Anything else raises VenueGuardError BEFORE any connection.
- TLS verification is mandatory: the default context is ssl.create_default_context() (CERT_REQUIRED + hostname check);
  an injected context that does not verify is refused at construction.
- Name resolution is bounded by the same deadline (Codex #13): a daemon thread only calls getaddrinfo and stores the
  result; the caller waits with the remaining time. A resolution that does not finish in time, or fails, raises
  WireNotSent (dns_timeout / dns_failed): nothing was written; the late result is discarded. Connect, TLS and send
  happen ONLY in the caller thread, to the resolved IP, with server_hostname = the pinned host and full certificate +
  hostname verification.
- Timeouts (connect or read) -> WireTimeout. Refused / reset / aborted connection, TLS failure, a truncated body or a
  malformed status line -> WireConnectionError. A body over max_body_bytes -> WireResponseTooLarge.
  The transport turns every one of these into UNKNOWN (WireNotSent into UNKNOWN 'not_sent_<reason>').
- One request per call, a fresh connection per call, no retry, no redirect following (a 3xx is returned as is and the
  transport maps it to UNKNOWN).
"""
import http.client
import re
import socket
import ssl
import threading
import time
import urllib.parse

from .guard import TESTNET_BASE_URL, VenueGuardError, check_request_url
from .wire import HttpResponse, WireConnectionError, WireNotSent, WireResponseTooLarge, WireTimeout

TESTNET_HOST = urllib.parse.urlsplit(TESTNET_BASE_URL).hostname
DEFAULT_MAX_BODY = 8 * 1024 * 1024        # exchangeInfo is the largest slice answer (a few MB on mainnet)
_METHODS = ('GET', 'POST', 'DELETE')
USER_AGENT = 'zackbot-newcore-venue/1'
_PATH = re.compile(r'/fapi/v[0-9]{1,2}(/[A-Za-z]{1,40}){1,3}')
_QUERY = re.compile(r'(?:[A-Za-z0-9._~*+=&-]|%[0-9A-Fa-f]{2})*')     # urlencode() output only: no raw control chars
_CALLER_HEADERS = ('x-mbx-apikey',)
_HEADER_VALUE = re.compile(r'[\x21-\x7e]{1,256}')


def _verifying(ctx):
    return isinstance(ctx, ssl.SSLContext) and ctx.verify_mode == ssl.CERT_REQUIRED and ctx.check_hostname


def default_connect(host, timeout, context):
    """A plain http.client HTTPS connection (kept for callers / tests that inject a connection factory). The sender's
    own production path does NOT use it: it resolves under the deadline first (see _resolve_within)."""
    return http.client.HTTPSConnection(host, 443, timeout=timeout, context=context)


def _system_getaddrinfo(host, port, family=0, type_=0):
    return socket.getaddrinfo(host, port, family, type_)


def _resolve_within(getaddrinfo, host, port, deadline):
    """Name resolution bounded by the deadline (Codex #13). A daemon thread ONLY calls getaddrinfo and stores the
    result in a box the caller owns; it never connects or sends. The caller waits with the remaining time; on timeout
    the box is abandoned (a late result is discarded) and WireNotSent('dns_timeout') is raised: nothing was sent."""
    box, done = {}, threading.Event()

    def work():
        try:
            box['addrs'] = getaddrinfo(host, port, 0, socket.SOCK_STREAM)
        except BaseException:              # stored as a fact only; never re-raised in this thread
            box['failed'] = True
        finally:
            done.set()
    threading.Thread(target=work, name='newcore-dns', daemon=True).start()
    if not done.wait(max(0.0, deadline - time.monotonic())):
        raise WireNotSent('name resolution timed out', 'dns_timeout')
    if box.get('failed') or not box.get('addrs'):
        raise WireNotSent('name resolution failed', 'dns_failed')
    addrs = [(fam, sa) for fam, _, _, _, sa in box['addrs'] if fam in (socket.AF_INET, socket.AF_INET6)]
    if not addrs:
        raise WireNotSent('name resolution failed', 'dns_failed')
    return addrs


def _dial_tcp(family, sockaddr, timeout, track):
    """TCP connect to a resolved address in the CALLER thread; the socket is handed to the watchdog before it blocks."""
    sock = socket.socket(family, socket.SOCK_STREAM)
    track(sock)
    sock.settimeout(timeout)
    try:
        sock.connect(sockaddr)
    except BaseException:
        sock.close()
        raise
    return sock


class TestnetHttpSender:
    __test__ = False                     # not a pytest test class despite the name

    def __init__(self, *, ssl_context=None, max_body_bytes=DEFAULT_MAX_BODY, connect=None, getaddrinfo=None,
                 dial=None):
        """connect: optional factory (host, timeout, ctx) -> an http.client connection (local-stub tests only).
        getaddrinfo / dial: seams for the production path (resolve under the deadline, then TCP to the resolved IP)."""
        ctx = ssl_context if ssl_context is not None else ssl.create_default_context()
        if not _verifying(ctx):
            raise VenueGuardError('TLS verification must be on (CERT_REQUIRED and check_hostname)')
        if type(max_body_bytes) is not int or not 1024 <= max_body_bytes <= 64 * 1024 * 1024:
            raise ValueError('max_body_bytes must be an int in 1 KiB .. 64 MiB')
        self._ctx, self._max, self._connect = ctx, max_body_bytes, connect
        self._getaddrinfo = getaddrinfo or _system_getaddrinfo
        self._dial = dial or _dial_tcp

    def _open(self, deadline, dog):
        """Production path: resolve within the deadline, then (caller thread only) TCP to the resolved IP, then TLS
        with server_hostname = the PINNED host and full certificate + hostname verification."""
        addrs = _resolve_within(self._getaddrinfo, TESTNET_HOST, 443, deadline)
        sock, last = None, None
        for family, sockaddr in addrs:
            if dog.fired or time.monotonic() >= deadline:
                raise WireTimeout('timed out')
            try:
                sock = self._dial(family, sockaddr, max(0.001, deadline - time.monotonic()), dog.track)
                break
            except OSError as ex:
                last = ex
        if sock is None:
            raise last if last is not None else WireConnectionError('connection failed')
        _set_timeout(sock, deadline)
        tls = self._ctx.wrap_socket(sock, server_hostname=TESTNET_HOST)     # handshake + hostname check now
        dog.track(tls)
        conn = http.client.HTTPSConnection(TESTNET_HOST, 443, timeout=max(0.001, deadline - time.monotonic()),
                                           context=self._ctx)
        conn.sock = tls                       # http.client sends on this socket; it never re-resolves or reconnects
        return conn

    def __repr__(self):
        return f'TestnetHttpSender(host={TESTNET_HOST!r}, max_body_bytes={self._max})'

    @staticmethod
    def _target(request):
        check_request_url(request.url)
        parts = urllib.parse.urlsplit(request.url)
        if (parts.scheme != 'https' or parts.hostname != TESTNET_HOST or parts.port not in (None, 443)
                or parts.username is not None or parts.password is not None or parts.query or parts.fragment):
            raise VenueGuardError('sender refuses any target other than the https testnet host')
        if not _PATH.fullmatch(parts.path):
            raise VenueGuardError('sender refuses a non-canonical path')
        if request.method not in _METHODS:
            raise VenueGuardError('sender refuses this HTTP method')
        if not isinstance(request.query, str) or not _QUERY.fullmatch(request.query):
            raise VenueGuardError('sender refuses a query with characters outside the urlencoded set')
        return parts.path + ('?' + request.query if request.query else '')

    @staticmethod
    def _headers(request):
        """Only the API-key header may come from the caller (Host, cookies, anything else is refused), and header
        values must be printable ASCII without spaces, CR or LF (Cowork finding 4)."""
        headers = {'User-Agent': USER_AGENT, 'Accept': 'application/json'}
        for k, v in request.wire_headers():
            if not isinstance(k, str) or k.lower() not in _CALLER_HEADERS:
                raise VenueGuardError('sender refuses a caller-supplied header other than the API key')
            if not isinstance(v, str) or not _HEADER_VALUE.fullmatch(v):
                raise VenueGuardError('sender refuses a header value with control or non-printable characters')
            headers[k] = v
        return headers

    def __call__(self, request):
        target = self._target(request)
        timeout = request.timeout_s
        if isinstance(timeout, bool) or not isinstance(timeout, (int, float)) or not 0 < timeout <= 60:
            raise VenueGuardError('timeout_s must be in (0, 60]')
        headers = self._headers(request)
        deadline = time.monotonic() + timeout
        dog = _Watchdog()
        conn = None
        try:
            dog.arm(deadline)
            if self._connect is not None:      # injected connection factory (local-stub tests)
                conn = self._connect(TESTNET_HOST, timeout, self._ctx)
                dog.watch_conn(conn)
                conn.connect()
                dog.track(conn.sock)
            else:
                conn = self._open(deadline, dog)
            result = self._exchange(conn, request.method, target, headers, deadline, dog)
            # A stream may LOOK complete after the deadline cut it. Judge by the clock too: under CPU load the
            # watchdog thread can run late, and its flag alone would then miss the overrun.
            if dog.fired or time.monotonic() > deadline:
                raise WireTimeout('timed out')
            return result
        except WireNotSent:
            raise                              # nothing was written, whatever the watchdog did meanwhile
        except (WireTimeout, WireConnectionError) as ex:
            if dog.fired and not isinstance(ex, WireTimeout):
                raise WireTimeout('timed out') from None
            raise
        except Exception as ex:
            if dog.fired or time.monotonic() >= deadline:
                raise WireTimeout('timed out') from None
            raise _map_error(ex) from None
        finally:
            dog.cancel()
            if conn is not None:
                try:
                    conn.close()
                except Exception:
                    pass

    def _exchange(self, conn, method, target, headers, deadline, dog):
        _set_timeout(conn.sock, deadline)
        conn.request(method, target, headers=headers)
        resp = conn.getresponse()              # a slow status line / header drip is cut by the watchdog
        length = resp.getheader('Content-Length')
        if length is not None and length.strip().isdigit() and int(length) > self._max:
            raise WireResponseTooLarge('response larger than the bound')
        chunks, total = [], 0
        while True:                            # TOTAL deadline: checked between chunks, and each read is bounded
            if dog.fired or time.monotonic() >= deadline:
                raise WireTimeout('timed out')
            _set_timeout(dog.sock, deadline)
            chunk = resp.read1(65536)
            if not chunk:
                break
            total += len(chunk)
            if total > self._max:
                raise WireResponseTooLarge('response larger than the bound')
            chunks.append(chunk)
        body = b''.join(chunks)
        if length is not None and length.strip().isdigit() and len(body) != int(length):
            raise WireConnectionError('truncated response body')
        hdrs = {}
        for k, v in resp.getheaders():
            hdrs[k] = v if k not in hdrs else hdrs[k] + ', ' + v
        return HttpResponse(resp.status, hdrs, body)


def _set_timeout(sock, deadline):
    if sock is not None:
        try:
            sock.settimeout(max(0.001, deadline - time.monotonic()))
        except OSError:
            pass


def _map_error(ex):
    if isinstance(ex, (TimeoutError, socket.timeout)):
        return WireTimeout('timed out')
    if isinstance(ex, socket.gaierror):
        return WireConnectionError('name resolution failed')
    if isinstance(ex, ssl.SSLError):
        return WireConnectionError('TLS failure')
    if isinstance(ex, http.client.IncompleteRead):
        return WireConnectionError('truncated response body')
    if isinstance(ex, (http.client.HTTPException, ConnectionError, OSError)):
        return WireConnectionError('connection failed')
    return WireConnectionError('connection failed')


class _Watchdog:
    """Wall-clock deadline for the WHOLE request (Cowork finding 2). A per-read socket timeout alone lets a server
    that drips one byte per interval keep a call alive forever; at the deadline this shuts the socket down, which
    makes any blocked connect / TLS / send / read in the request thread fail at once. Name resolution is bounded
    separately (_resolve_within): it runs in its own thread and the caller stops waiting at the deadline."""

    def __init__(self):
        self.fired, self.sock, self._conn, self._timer = False, None, None, None
        self._lock = threading.Lock()

    def watch_conn(self, conn):
        self._conn = conn

    def arm(self, deadline):
        self._timer = threading.Timer(max(0.0, deadline - time.monotonic()), self._fire)
        self._timer.daemon = True
        self._timer.start()

    def track(self, sock):
        with self._lock:
            self.sock = sock
            if self.fired:
                _shutdown(sock)

    def _fire(self):
        with self._lock:
            self.fired = True
            _shutdown(self.sock if self.sock is not None else getattr(self._conn, 'sock', None))

    def cancel(self):
        if self._timer is not None:
            self._timer.cancel()


def _shutdown(sock):
    if sock is None:
        return
    try:
        sock.shutdown(socket.SHUT_RDWR)
    except OSError:
        pass
