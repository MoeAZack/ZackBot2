"""Signing in server time.  Real run on the owner PC: the PC clock was 1-2 s behind Binance testnet and a signed
startup read came back HTTP 400 / -1021 (outside recvWindow), so the CLI refused with reader_http_status."""
from __future__ import annotations

import time
from urllib.parse import parse_qsl

import pytest

from newcore.tnet02.live import (SIGNED_RECV_WINDOW_MS, SYNC_MARGIN_MS, LiveError, SignedReader, SyncedClock,
                                 server_offset_ms)
from newcore.venue import TestnetHttpSender
from newcore.venue import http_sender as transport
from newcore.venue.binance_usdm_testnet import HttpResponse
from tests.newcore_venue.test_nc03b_http_sender import FakeSocket, install_local_stack, request

from .fake_binance import FakeBinance, FakeClock
from .support import T0
from .test_tnet02_live import _creds

BASE = "https://testnet.binancefuture.com"


class Local:
    """A local clock that is `behind_ms` behind the fake server and advances `step_ms` per read."""

    def __init__(self, fake, behind_ms, step_ms=0):
        self.fake, self.behind, self.step = fake, behind_ms, step_ms

    def __call__(self):
        self.fake.clock.now += self.step
        return self.fake.clock.now - self.behind


def _reader(fake, clock):
    return SignedReader(sender=fake, credentials=_creds(), clock=clock)


def test_offset_is_measured_from_server_time_and_corrected_for_round_trip():
    fake = FakeBinance(FakeClock(T0))
    local = Local(fake, behind_ms=1800, step_ms=40)
    offset = server_offset_ms(_reader(fake, local), local)
    # each sample: server read happens after one 40 ms step; the midpoint estimate lands within the round trip
    assert 1800 - 40 <= offset <= 1800 + 40
    assert [p for _m, p, _q in fake.requests] == ["/fapi/v1/time"] * 3
    assert all(q == {} for _m, _p, q in fake.requests)            # unsigned: no key, no signature


def test_synced_clock_signs_just_behind_server_time_with_the_wide_window():
    fake = FakeBinance(FakeClock(T0))
    local = Local(fake, behind_ms=1800)
    clock = SyncedClock(local, server_offset_ms(_reader(fake, local), local))
    reader = SignedReader(sender=fake, credentials=_creds(), clock=clock, recv_window_ms=SIGNED_RECV_WINDOW_MS)
    reader.signed("/fapi/v2/balance")
    params = fake.requests[-1][2]
    assert int(params["timestamp"]) == fake.clock.now - SYNC_MARGIN_MS
    assert params["recvWindow"] == str(SIGNED_RECV_WINDOW_MS) == "10000"


@pytest.mark.parametrize("body, code", [
    ({"serverTime": "123"}, "clock.server_time"),
    ({"serverTime": 0}, "clock.server_time"),
    ([], "clock.server_time"),
    ({"serverTime": True}, "clock.server_time"),
], ids=["string", "zero", "list", "bool"])
def test_malformed_server_time_is_refused(body, code):
    fake = FakeBinance(FakeClock(T0))
    fake._get_fapi_v1_time = lambda p: HttpResponse(status=200, body=__import__("json").dumps(body).encode())
    with pytest.raises(LiveError) as caught:
        server_offset_ms(_reader(fake, fake.clock), fake.clock)
    assert caught.value.code == code


def test_slow_round_trip_and_huge_offset_are_refused():
    fake = FakeBinance(FakeClock(T0))
    slow = Local(fake, behind_ms=0, step_ms=6000)                  # 6 s between before and after
    with pytest.raises(LiveError) as caught:
        server_offset_ms(_reader(fake, slow), slow)
    assert caught.value.code == "clock.round_trip"
    far = Local(fake, behind_ms=61_000)
    with pytest.raises(LiveError) as caught:
        server_offset_ms(_reader(fake, far), far)
    assert caught.value.code == "clock.offset_too_large"


def test_median_ignores_one_outlier_sample():
    fake = FakeBinance(FakeClock(T0))
    answers = iter([T0 + 1000, T0 + 50_000, T0 + 1100])
    fake._get_fapi_v1_time = lambda p: HttpResponse(status=200, body=b'{"serverTime": %d}' % next(answers))
    assert server_offset_ms(_reader(fake, fake.clock), fake.clock) == 1100


@pytest.mark.parametrize("args", [(None, 0), (lambda: 1, 60_001), (lambda: 1, "5"), (lambda: 1, 0, 1001),
                                  (lambda: 1, 0, -1)], ids=["no_clock", "offset_big", "offset_str", "margin_big",
                                                            "margin_negative"])
def test_synced_clock_refuses_bad_construction(args):
    with pytest.raises(LiveError) as caught:
        SyncedClock(*args)
    assert caught.value.code == "clock.synced"


def test_synced_clock_has_no_mutable_attributes():
    clock = SyncedClock(lambda: 1000, 500, 0)
    assert clock() == 1500 and clock.offset_ms == 500
    with pytest.raises(AttributeError):
        clock.extra = 1


def test_sender_allows_unsigned_server_time_only_as_a_bare_get(monkeypatch):
    sock = FakeSocket(reply=b'HTTP/1.1 200 OK\r\nContent-Length: 20\r\n\r\n{"serverTime":12345}')
    install_local_stack(monkeypatch, sock)
    out = TestnetHttpSender()(request(url=BASE + "/fapi/v1/time", query="", headers=()))
    assert out.status == 200 and sock.sent[0].startswith(b"GET /fapi/v1/time HTTP/1.1\r\n")
    assert b"X-MBX-APIKEY" not in sock.sent[0]
    for bad in ({"query": "x=1"}, {"method": "POST"}, {"headers": (("X-MBX-APIKEY", "k"),)}):
        values = {"url": BASE + "/fapi/v1/time", "query": "", "headers": ()}
        values.update(bad)
        dns = []
        monkeypatch.setattr(transport.socket, "getaddrinfo", lambda *a: dns.append(a))
        with pytest.raises(ValueError):
            TestnetHttpSender()(request(**values))
        assert dns == []


def test_offset_uses_the_round_trip_midpoint_exactly():
    fake = FakeBinance(FakeClock(T0))
    fake._get_fapi_v1_time = lambda p: HttpResponse(status=200, body=b'{"serverTime": 5000}')
    ticks = iter([1000, 1400])
    assert server_offset_ms(_reader(fake, lambda: next(ticks)), lambda: next(ticks), samples=1) == 3800


# ------------------------------------------------------------------------------------------------- CLI end to end
@pytest.fixture
def cli_env(tmp_path, monkeypatch):
    import newcore.venue as venue_pkg
    from tools import tnet02_run as cli

    from .test_tnet02_cli import LIVE_KEY, LIVE_SECRET, _live_account, _run_args

    monkeypatch.setattr(cli, "_git", lambda *a: __import__("tests.newcore_tnet02.support", fromlist=["x"]).BUILD_SHA
                        if a[0] == "rev-parse" else "")
    monkeypatch.setenv("ZACKBOT_TNET02_API_KEY", LIVE_KEY)
    monkeypatch.setenv("ZACKBOT_TNET02_API_SECRET", LIVE_SECRET)

    def run(*, server_ahead_ms=0, latency_ms=0):
        fake = FakeBinance(FakeClock(time.time_ns() // 1_000_000 + server_ahead_ms))
        fake.enforce_recv_window, fake.latency_ms = True, latency_ms
        monkeypatch.setattr(venue_pkg, "TestnetHttpSender", lambda origin: fake)
        code = cli.main(_run_args(tmp_path, _live_account(tmp_path)))
        return code, fake
    return run


def test_cli_preflight_passes_with_the_pc_clock_far_behind_the_server(cli_env, capsys):
    code, fake = cli_env(server_ahead_ms=12_000)              # plain local timestamps would be 12 s stale
    out = capsys.readouterr().out
    assert code == 0 and "isolated=true" in out, out
    offset = int(out.split("clock_offset_ms=")[1].split()[0])
    assert 11_500 <= offset <= 12_500
    signed = [q for _m, _p, q in fake.requests if "timestamp" in q]
    assert signed and all(q["recvWindow"] == "10000" for q in signed)


def test_cli_preflight_survives_six_seconds_of_network_delay(cli_env, capsys):
    code, _fake = cli_env(latency_ms=6_000)                   # passes only with the 10 s recvWindow
    out = capsys.readouterr().out
    assert code == 0 and "isolated=true" in out, out
