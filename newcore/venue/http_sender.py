"""One-shot HTTPS transport for the Binance USD-M testnet adapter.

The adapter owns request construction and response interpretation.  This
module is the only NEWCORE component that opens a network socket.  It is
deliberately bound to the exact testnet origin and never retries or follows a
redirect.
"""
from __future__ import annotations

import concurrent.futures
import http.client
import re
import socket
import ssl
import threading
import time
from urllib.parse import urlsplit

from .binance_usdm_testnet import HttpRequest, HttpResponse, TESTNET_BASE_URL

_HOST = 'testnet.binancefuture.com'
_PORT = 443
_MAX_RESPONSE_BYTES = 8 * 1024 * 1024
_MAX_QUERY_BYTES = 16 * 1024
_RESOLVER_WORKERS = 4
_METHODS = frozenset({'GET', 'POST', 'DELETE'})
_PATHS = frozenset({
    '/fapi/v1/algoOrder',
    '/fapi/v1/order',
    '/fapi/v1/userTrades',
    '/fapi/v2/positionRisk',
})
_QUERY_KEY = re.compile(r'^[A-Za-z][A-Za-z0-9]*$')
_QUERY_VALUE = re.compile(r'^[A-Za-z0-9._~+%\-]*$')
_API_KEY = re.compile(r'^[A-Za-z0-9_-]{1,256}$')

# getaddrinfo has no portable cancellation API.  A small shared pool bounds
# resolver threads; after a caller times out, only the resolver may finish in
# the background.  Socket creation remains solely on the calling thread.
_RESOLVER = concurrent.futures.ThreadPoolExecutor(
    max_workers=_RESOLVER_WORKERS,
    thread_name_prefix='zackbot-testnet-dns',
)


def _remaining(deadline: float) -> float:
    remaining = deadline - time.monotonic()
    if remaining <= 0:
        raise TimeoutError('testnet HTTP deadline exceeded')
    return remaining


def _validate(request: HttpRequest) -> tuple[str, str]:
    if not isinstance(request, HttpRequest):
        raise ValueError('invalid testnet HTTP request')
    if request.method not in _METHODS:
        raise ValueError('invalid testnet HTTP request')
    if isinstance(request.timeout_s, bool) or not isinstance(request.timeout_s, (int, float)):
        raise ValueError('invalid testnet HTTP request')
    if not 0 < request.timeout_s <= 60:
        raise ValueError('invalid testnet HTTP request')

    if type(request.url) is not str or not request.url.startswith(TESTNET_BASE_URL + '/'):
        raise ValueError('invalid testnet HTTP request')
    parts = urlsplit(request.url)
    if (parts.scheme, parts.hostname, parts.port) != ('https', _HOST, None):
        raise ValueError('invalid testnet HTTP request')
    if parts.username is not None or parts.password is not None or parts.query or parts.fragment:
        raise ValueError('invalid testnet HTTP request')
    if parts.path not in _PATHS or request.url != TESTNET_BASE_URL + parts.path:
        raise ValueError('invalid testnet HTTP request')

    if type(request.headers) is not tuple or len(request.headers) != 1:
        raise ValueError('invalid testnet HTTP request')
    header = request.headers[0]
    if type(header) is not tuple or len(header) != 2 or header[0] != 'X-MBX-APIKEY':
        raise ValueError('invalid testnet HTTP request')
    api_key = header[1]
    if type(api_key) is not str or _API_KEY.fullmatch(api_key) is None:
        raise ValueError('invalid testnet HTTP request')

    query = request.query
    if type(query) is not str or not query or len(query.encode('ascii', errors='ignore')) > _MAX_QUERY_BYTES:
        raise ValueError('invalid testnet HTTP request')
    try:
        query.encode('ascii')
    except UnicodeEncodeError:
        raise ValueError('invalid testnet HTTP request') from None
    seen = set()
    for field in query.split('&'):
        if field.count('=') != 1:
            raise ValueError('invalid testnet HTTP request')
        key, value = field.split('=', 1)
        if _QUERY_KEY.fullmatch(key) is None or _QUERY_VALUE.fullmatch(value) is None or key in seen:
            raise ValueError('invalid testnet HTTP request')
        for index, char in enumerate(value):
            if char == '%' and (index + 2 >= len(value) or
                                not all(c in '0123456789abcdefABCDEF' for c in value[index + 1:index + 3])):
                raise ValueError('invalid testnet HTTP request')
        seen.add(key)
    if 'signature' not in seen or 'timestamp' not in seen or 'recvWindow' not in seen:
        raise ValueError('invalid testnet HTTP request')
    return parts.path, api_key


def _first_address(records):
    for family, socktype, proto, _canonname, address in records:
        if family in (socket.AF_INET, socket.AF_INET6) and socktype == socket.SOCK_STREAM:
            return family, socktype, proto, address
    raise OSError('testnet HTTP request failed')


class TestnetHttpSender:
    """Callable one-attempt HTTPS sender consumed by ``BinanceUsdMTestnetVenue``."""

    __test__ = False

    def __call__(self, request: HttpRequest) -> HttpResponse:
        path, api_key = _validate(request)
        deadline = time.monotonic() + float(request.timeout_s)
        future = _RESOLVER.submit(socket.getaddrinfo, _HOST, _PORT, 0, socket.SOCK_STREAM)
        try:
            records = future.result(timeout=_remaining(deadline))
        except concurrent.futures.TimeoutError:
            future.cancel()
            raise TimeoutError('testnet HTTP deadline exceeded') from None
        except Exception:
            raise OSError('testnet HTTP request failed') from None

        raw = tls_sock = response = None
        holder = [None]
        deadline_aborted = threading.Event()

        def abort_at_deadline():
            deadline_aborted.set()
            sock = holder[0]
            if sock is not None:
                try:
                    sock.shutdown(socket.SHUT_RDWR)
                except Exception:
                    pass
                try:
                    sock.close()
                except Exception:
                    pass

        timer = threading.Timer(_remaining(deadline), abort_at_deadline)
        timer.daemon = True
        timer.start()
        try:
            family, socktype, proto, address = _first_address(records)
            raw = socket.socket(family, socktype, proto)
            holder[0] = raw
            raw.settimeout(_remaining(deadline))
            raw.connect(address)

            context = ssl.create_default_context(purpose=ssl.Purpose.SERVER_AUTH)
            # Fail closed even if process-wide SSL defaults have been modified.
            context.check_hostname = True
            context.verify_mode = ssl.CERT_REQUIRED
            raw.settimeout(_remaining(deadline))
            tls_sock = context.wrap_socket(raw, server_hostname=_HOST)
            holder[0] = tls_sock

            target = path + '?' + request.query
            wire = (
                f'{request.method} {target} HTTP/1.1\r\n'
                f'Host: {_HOST}\r\n'
                f'X-MBX-APIKEY: {api_key}\r\n'
                'Accept: application/json\r\n'
                'Content-Length: 0\r\n'
                'Connection: close\r\n\r\n'
            ).encode('ascii')
            tls_sock.settimeout(_remaining(deadline))
            tls_sock.sendall(wire)

            tls_sock.settimeout(_remaining(deadline))
            response = http.client.HTTPResponse(tls_sock, method=request.method)
            response.begin()
            declared_length = response.length
            if declared_length is not None and declared_length > _MAX_RESPONSE_BYTES:
                raise OSError('testnet HTTP response too large')
            chunks, size = [], 0
            while True:
                tls_sock.settimeout(_remaining(deadline))
                chunk = response.read1(min(64 * 1024, _MAX_RESPONSE_BYTES + 1 - size))
                if not chunk:
                    break
                size += len(chunk)
                if size > _MAX_RESPONSE_BYTES:
                    raise OSError('testnet HTTP response too large')
                chunks.append(chunk)
            if declared_length is not None and size != declared_length:
                raise OSError('testnet HTTP response incomplete')
            # Disarm the watchdog before the final absolute-time check.  This
            # closes the race where the timer could fire between a successful
            # check and returning an otherwise authoritative response.
            timer.cancel()
            if deadline_aborted.is_set():
                raise TimeoutError('testnet HTTP deadline exceeded')
            _remaining(deadline)
            return HttpResponse(status=response.status, body=b''.join(chunks))
        except TimeoutError:
            raise TimeoutError('testnet HTTP deadline exceeded') from None
        except (OSError, ssl.SSLError, http.client.HTTPException):
            # Never expose the URL, query string, API key, peer text, or an
            # exception chain that may contain them.
            raise OSError('testnet HTTP request failed') from None
        except Exception:
            raise OSError('testnet HTTP request failed') from None
        finally:
            timer.cancel()
            if response is not None:
                try:
                    response.close()
                except Exception:
                    pass
            for sock in (tls_sock, raw):
                if sock is not None:
                    try:
                        sock.close()
                    except Exception:
                        pass
            holder[0] = None


__all__ = ['TestnetHttpSender']
