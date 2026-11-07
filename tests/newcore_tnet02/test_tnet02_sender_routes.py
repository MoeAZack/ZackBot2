"""TNET-02 additions to the testnet sender: two signed read routes and the one unsigned public route."""
from __future__ import annotations

import pytest

from newcore.venue import TestnetHttpSender
from newcore.venue import http_sender as transport
from tests.newcore_venue.test_nc03b_http_sender import QUERY, SECRET, FakeSocket, install_local_stack, request

BASE = "https://testnet.binancefuture.com"


def test_public_exchange_info_sends_no_key_and_no_query(monkeypatch):
    sock = FakeSocket()
    install_local_stack(monkeypatch, sock)
    out = TestnetHttpSender()(request(url=BASE + "/fapi/v1/exchangeInfo", query="", headers=()))
    assert out.status == 200
    wire = sock.sent[0]
    assert wire.startswith(b"GET /fapi/v1/exchangeInfo HTTP/1.1\r\n")
    assert b"X-MBX-APIKEY" not in wire and b"?" not in wire.split(b"\r\n")[0]


@pytest.mark.parametrize("path", ["/fapi/v1/rateLimit/order", "/fapi/v2/balance"])
def test_new_signed_read_routes_keep_the_signed_grammar(monkeypatch, path):
    sock = FakeSocket()
    install_local_stack(monkeypatch, sock)
    assert TestnetHttpSender()(request(url=BASE + path)).status == 200
    assert b"X-MBX-APIKEY: " + SECRET.encode() + b"\r\n" in sock.sent[0]
    dns = []
    monkeypatch.setattr(transport.socket, "getaddrinfo", lambda *args: dns.append(args))
    with pytest.raises(ValueError):
        TestnetHttpSender()(request(url=BASE + path, query="", headers=()))
    assert dns == []


@pytest.mark.parametrize("changes", [
    {"headers": (("X-MBX-APIKEY", SECRET),)},          # a key must never ride the public route
    {"query": QUERY},                                    # nor a signed query
    {"query": "symbol=BTCUSDT"},
    {"method": "POST"},
    {"method": "DELETE"},
    {"url": BASE + "/fapi/v1/exchangeInfo/"},
    {"url": "https://fapi.binance.com/fapi/v1/exchangeInfo"},
])
def test_public_route_refuses_anything_but_a_bare_get(monkeypatch, changes):
    values = {"url": BASE + "/fapi/v1/exchangeInfo", "query": "", "headers": ()}
    values.update(changes)
    dns = []
    monkeypatch.setattr(transport.socket, "getaddrinfo", lambda *args: dns.append(args))
    with pytest.raises(ValueError, match="^invalid testnet HTTP request$"):
        TestnetHttpSender()(request(**values))
    assert dns == []


def test_unsigned_request_on_a_signed_route_is_still_refused(monkeypatch):
    dns = []
    monkeypatch.setattr(transport.socket, "getaddrinfo", lambda *args: dns.append(args))
    for path in ("/fapi/v1/order", "/fapi/v2/positionRisk"):
        with pytest.raises(ValueError):
            TestnetHttpSender()(request(url=BASE + path, query="", headers=()))
    assert dns == []
