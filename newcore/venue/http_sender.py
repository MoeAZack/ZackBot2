"""TestnetHttpSender: the real `http` callable for BinanceTestnetTransport (stdlib http.client over TLS).

This is the ONLY module in newcore.venue that may open a socket. It is not imported by the transport; the S5 harness
wires it in explicitly.

Contract (wire.py): return an HttpResponse, or raise WireTimeout / WireConnectionError / WireResponseTooLarge. Exception
messages are fixed strings, never the URL (the query holds the signature) and never a header.
- Host pin (defence in depth): https only, host exactly testnet.binancefuture.com, default port, no user-info, and the
  request URL must also pass guard.check_request_url. Anything else raises VenueGuardError BEFORE any connection.
- TLS verification is mandatory: the default context is ssl.create_default_context() (CERT_REQUIRED + hostname check);
  an injected context that does not verify is refused at construction.
- Timeouts (connect or read) -> WireTimeout. DNS failure, refused / reset / aborted connection, TLS failure, a truncated
  body or a malformed status line -> WireConnectionError. A body over max_body_bytes -> WireResponseTooLarge.
  The transport turns every one of these into UNKNOWN.
- One request per call, a fresh connection per call, no retry, no redirect following (a 3xx is returned as is and the
  transport maps it to UNKNOWN).
"""
import http.client
import socket
import ssl
import urllib.parse

from .guard import TESTNET_BASE_URL, VenueGuardError, check_request_url
from .wire import HttpResponse, WireConnectionError, WireResponseTooLarge, WireTimeout

TESTNET_HOST = urllib.parse.urlsplit(TESTNET_BASE_URL).hostname
DEFAULT_MAX_BODY = 8 * 1024 * 1024        # exchangeInfo is the largest slice answer (a few MB on mainnet)
_METHODS = ('GET', 'POST', 'DELETE')
USER_AGENT = 'zackbot-newcore-venue/1'


def _verifying(ctx):
    return isinstance(ctx, ssl.SSLContext) and ctx.verify_mode == ssl.CERT_REQUIRED and ctx.check_hostname


def default_connect(host, timeout, context):
    """The production connection: HTTPS to the pinned host on 443 with a verifying context."""
    return http.client.HTTPSConnection(host, 443, timeout=timeout, context=context)


class TestnetHttpSender:
    __test__ = False                     # not a pytest test class despite the name

    def __init__(self, *, ssl_context=None, max_body_bytes=DEFAULT_MAX_BODY, connect=None):
        ctx = ssl_context if ssl_context is not None else ssl.create_default_context()
        if not _verifying(ctx):
            raise VenueGuardError('TLS verification must be on (CERT_REQUIRED and check_hostname)')
        if type(max_body_bytes) is not int or not 1024 <= max_body_bytes <= 64 * 1024 * 1024:
            raise ValueError('max_body_bytes must be an int in 1 KiB .. 64 MiB')
        self._ctx, self._max, self._connect = ctx, max_body_bytes, connect or default_connect

    def __repr__(self):
        return f'TestnetHttpSender(host={TESTNET_HOST!r}, max_body_bytes={self._max})'

    @staticmethod
    def _target(request):
        check_request_url(request.url)
        parts = urllib.parse.urlsplit(request.url)
        if (parts.scheme != 'https' or parts.hostname != TESTNET_HOST or parts.port not in (None, 443)
                or parts.username is not None or parts.password is not None or parts.query or parts.fragment):
            raise VenueGuardError('sender refuses any target other than the https testnet host')
        if request.method not in _METHODS:
            raise VenueGuardError('sender refuses this HTTP method')
        if not isinstance(request.query, str) or '#' in request.query:
            raise VenueGuardError('malformed query')
        return parts.path + ('?' + request.query if request.query else '')

    def __call__(self, request):
        target = self._target(request)
        timeout = request.timeout_s
        if isinstance(timeout, bool) or not isinstance(timeout, (int, float)) or not 0 < timeout <= 60:
            raise VenueGuardError('timeout_s must be in (0, 60]')
        headers = {'User-Agent': USER_AGENT, 'Accept': 'application/json'}
        for k, v in request.wire_headers():
            headers[k] = v
        conn = None
        try:
            conn = self._connect(TESTNET_HOST, timeout, self._ctx)
            conn.request(request.method, target, headers=headers)
            resp = conn.getresponse()
            length = resp.getheader('Content-Length')
            if length is not None and length.strip().isdigit() and int(length) > self._max:
                raise WireResponseTooLarge('response larger than the bound')
            body = resp.read(self._max + 1)
            if len(body) > self._max:
                raise WireResponseTooLarge('response larger than the bound')
            if length is not None and length.strip().isdigit() and len(body) != int(length):
                raise WireConnectionError('truncated response body')
            hdrs = {}
            for k, v in resp.getheaders():
                hdrs[k] = v if k not in hdrs else hdrs[k] + ', ' + v
            return HttpResponse(resp.status, hdrs, body)
        except (WireTimeout, WireConnectionError):
            raise
        except (TimeoutError, socket.timeout):
            raise WireTimeout('timed out') from None
        except socket.gaierror:
            raise WireConnectionError('name resolution failed') from None
        except ssl.SSLError:
            raise WireConnectionError('TLS failure') from None
        except http.client.IncompleteRead:
            raise WireConnectionError('truncated response body') from None
        except (http.client.HTTPException, ConnectionError, OSError):
            raise WireConnectionError('connection failed') from None
        finally:
            if conn is not None:
                try:
                    conn.close()
                except Exception:
                    pass
