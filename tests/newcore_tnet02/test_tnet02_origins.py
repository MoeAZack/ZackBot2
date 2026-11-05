"""TNET-02 testnet origins: two exact approved origins, one chosen per binding and run, no fallback or reuse.

Codex ruling on #219 (6660693481): support https://demo-fapi.binance.com beside the original
https://testnet.binancefuture.com.  The chosen origin must reach URL validation, DNS, TLS and request construction
consistently; mainnet, lookalike and redirect targets are refused.
"""
from __future__ import annotations

import json
import time

import pytest

from newcore.domain.account import confirmation_phrase
from newcore.testnet_harness import SuiteStatus, run_suite
from newcore.tnet02.journal import FileJournalOpener
from newcore.tnet02.live import LiveAccountPort, LiveError, LiveFiltersPort, SignedReader, read_limit_payloads
from newcore.tnet02.wiring import build_adapter, key_digest
from newcore.venue import TestnetHttpSender
from newcore.venue import http_sender as transport
from newcore.venue.binance_usdm_testnet import (DEMO_TESTNET_BASE_URL, TESTNET_BASE_URL, TESTNET_ORIGINS,
                                                BinanceUsdMTestnetVenue, VenueBindingError)
from tools import tnet02_run as cli
from tests.newcore_venue.test_nc03b_http_sender import QUERY, SECRET, FakeSocket, install_local_stack, request

from .fake_binance import FakeBinance, FakeClock
from .support import ACCOUNT_ID, BUILD_SHA, T0, account, manifest, wave
from .test_tnet02_live import DOC_KEY, _creds

ORIGINS = {"legacy": TESTNET_BASE_URL, "demo": DEMO_TESTNET_BASE_URL}
HOSTS = {TESTNET_BASE_URL: "testnet.binancefuture.com", DEMO_TESTNET_BASE_URL: "demo-fapi.binance.com"}
HOSTILE = [
    "https://fapi.binance.com",                         # mainnet USD-M
    "https://api.binance.com",                          # mainnet spot
    "https://demo-fapi.binance.com.evil.invalid",       # suffix lookalike
    "https://demo-fapi-binance.com",                    # punctuation lookalike
    "https://demo.fapi.binance.com",                    # wildcard-style subdomain
    "https://DEMO-FAPI.binance.com",                    # case variant is not the exact string
    "http://demo-fapi.binance.com",                     # downgrade
    "https://demo-fapi.binance.com:443",                # explicit port
    "https://demo-fapi.binance.com/",                   # trailing slash
    "https://user@demo-fapi.binance.com",               # userinfo
]
HOSTILE_IDS = ["mainnet_fapi", "mainnet_spot", "suffix", "dash", "subdomain", "case", "http", "port", "slash",
               "userinfo"]


def test_exactly_two_origins_are_approved():
    assert TESTNET_ORIGINS == frozenset(HOSTS)
    assert not any("fapi.binance.com" == o.split("//")[1] for o in TESTNET_ORIGINS)


# ------------------------------------------------------------------------------------------------- venue
@pytest.mark.parametrize("origin", list(ORIGINS.values()), ids=list(ORIGINS))
def test_venue_builds_requests_on_its_one_origin(origin):
    seen = []
    venue = BinanceUsdMTestnetVenue(environment="testnet", base_url=origin, sender=lambda r: seen.append(r),
                                    credentials=_creds(), clock=lambda: T0)
    try:
        venue._request("GET", "/fapi/v2/positionRisk", [])
    except Exception:
        pass
    assert seen and seen[0].url == origin + "/fapi/v2/positionRisk"


@pytest.mark.parametrize("hostile", HOSTILE, ids=HOSTILE_IDS)
def test_venue_refuses_mainnet_and_lookalike_origins(hostile):
    with pytest.raises(VenueBindingError):
        BinanceUsdMTestnetVenue(environment="testnet", base_url=hostile, sender=lambda r: None,
                                credentials=_creds(), clock=lambda: T0)


@pytest.mark.parametrize("swap_to", [DEMO_TESTNET_BASE_URL, "https://fapi.binance.com"], ids=["other_testnet",
                                                                                               "mainnet"])
def test_venue_refuses_a_swapped_origin_after_construction(swap_to):
    # Even the other approved origin is refused mid-run: one origin per binding and run, no migration.
    seen = []
    venue = BinanceUsdMTestnetVenue(environment="testnet", base_url=TESTNET_BASE_URL,
                                    sender=lambda r: seen.append(r), credentials=_creds(), clock=lambda: T0)
    venue._base_url = swap_to
    with pytest.raises(Exception) as caught:
        venue._request("GET", "/fapi/v2/positionRisk", [])
    assert "binding_changed" in repr(caught.value) and seen == []


# ------------------------------------------------------------------------------------------------- sender
@pytest.mark.parametrize("origin", list(ORIGINS.values()), ids=list(ORIGINS))
def test_sender_uses_one_host_for_dns_tls_and_host_header(monkeypatch, origin):
    host = HOSTS[origin]
    resolved = []
    sock = FakeSocket()
    context = install_local_stack(monkeypatch, sock)
    real = transport.socket.getaddrinfo

    def resolver(name, *rest):
        resolved.append(name)
        return real(name, *rest)
    monkeypatch.setattr(transport.socket, "getaddrinfo", resolver)
    sender = TestnetHttpSender(origin)
    assert sender.origin == origin
    out = sender(request(url=origin + "/fapi/v1/order"))
    assert out.status == 200
    assert resolved == [host] and context.server_names == [host]
    assert f"Host: {host}\r\n".encode() in sock.sent[0]


def test_sender_default_keeps_the_original_origin(monkeypatch):
    sock = FakeSocket()
    context = install_local_stack(monkeypatch, sock)
    assert TestnetHttpSender().origin == TESTNET_BASE_URL
    TestnetHttpSender()(request())
    assert context.server_names == ["testnet.binancefuture.com"]


@pytest.mark.parametrize("bound, target", [(TESTNET_BASE_URL, DEMO_TESTNET_BASE_URL),
                                           (DEMO_TESTNET_BASE_URL, TESTNET_BASE_URL)], ids=["legacy", "demo"])
def test_sender_refuses_the_other_approved_origin_before_dns(monkeypatch, bound, target):
    dns = []
    monkeypatch.setattr(transport.socket, "getaddrinfo", lambda *args: dns.append(args))
    with pytest.raises(ValueError, match="^invalid testnet HTTP request$"):
        TestnetHttpSender(bound)(request(url=target + "/fapi/v1/order"))
    assert dns == []


@pytest.mark.parametrize("hostile", HOSTILE, ids=HOSTILE_IDS)
def test_sender_refuses_hostile_origins_at_construction_and_per_request(monkeypatch, hostile):
    with pytest.raises(ValueError, match="^invalid testnet origin$"):
        TestnetHttpSender(hostile)
    dns = []
    monkeypatch.setattr(transport.socket, "getaddrinfo", lambda *args: dns.append(args))
    with pytest.raises(ValueError, match="^invalid testnet HTTP request$"):
        TestnetHttpSender(DEMO_TESTNET_BASE_URL)(request(url=hostile + "/fapi/v1/order"))
    assert dns == []


def test_sender_refuses_a_tampered_origin_host_pair(monkeypatch):
    dns = []
    monkeypatch.setattr(transport.socket, "getaddrinfo", lambda *args: dns.append(args))
    sender = TestnetHttpSender(DEMO_TESTNET_BASE_URL)
    object.__setattr__(sender, "_host", "fapi.binance.com")
    with pytest.raises(ValueError):
        sender(request(url=DEMO_TESTNET_BASE_URL + "/fapi/v1/order"))
    assert dns == []
    with pytest.raises(AttributeError):
        sender.extra = 1                               # slots: no stray attributes to smuggle a host in


@pytest.mark.parametrize("origin, host", [("https://fapi.binance.com", "fapi.binance.com"),
                                          (DEMO_TESTNET_BASE_URL, "testnet.binancefuture.com")],
                         ids=["mainnet_pair", "crossed_pair"])
def test_validate_refuses_any_origin_host_pair_that_is_not_approved(origin, host):
    # Each sender layer is checked on its own: a request that matches a forged (origin, host) pair is refused.
    with pytest.raises(ValueError, match="^invalid testnet HTTP request$"):
        transport._validate(request(url=origin + "/fapi/v1/order"), origin, host)


def test_sender_returns_a_redirect_unchanged_and_never_follows_it(monkeypatch):
    reply = (b"HTTP/1.1 307 Temporary Redirect\r\nLocation: https://fapi.binance.com/fapi/v1/order\r\n"
             b"Content-Length: 0\r\n\r\n")
    sock = FakeSocket(reply=reply)
    other = FakeSocket()
    context = install_local_stack(monkeypatch, sock, other)
    out = TestnetHttpSender(DEMO_TESTNET_BASE_URL)(request(url=DEMO_TESTNET_BASE_URL + "/fapi/v1/order"))
    assert out.status == 307 and len(sock.sent) == 1 and other.sent == []
    assert context.server_names == ["demo-fapi.binance.com"]


def test_sender_errors_never_name_the_query_or_key(monkeypatch):
    sock = FakeSocket(connect_error=OSError("leak " + SECRET + " " + QUERY))
    install_local_stack(monkeypatch, sock)
    with pytest.raises(OSError) as caught:
        TestnetHttpSender(DEMO_TESTNET_BASE_URL)(request(url=DEMO_TESTNET_BASE_URL + "/fapi/v1/order"))
    assert SECRET not in str(caught.value) and caught.value.__cause__ is None


# ------------------------------------------------------------------------------------------------- reader and wiring
def test_reader_refuses_unapproved_origins():
    fake = FakeBinance(FakeClock(T0))
    for hostile in HOSTILE:
        with pytest.raises(LiveError) as caught:
            SignedReader(sender=fake, credentials=_creds(), clock=fake.clock, base_url=hostile)
        assert caught.value.code == "reader.origin"
    reader = SignedReader(sender=fake, credentials=_creds(), clock=fake.clock, base_url=DEMO_TESTNET_BASE_URL)
    reader._origin = "https://fapi.binance.com"
    with pytest.raises(LiveError) as caught:
        reader.signed("/fapi/v2/balance")
    assert caught.value.code == "reader.origin" and fake.requests == []


def _wire(tmp_path, fake, clock, origin, *, reader_origin=None, wiring_origin=None):
    creds = _creds()
    reader = SignedReader(sender=fake, credentials=creds, clock=clock, base_url=reader_origin or origin)
    exchange, limits, info, raw = read_limit_payloads(reader)
    (tmp_path / "j").mkdir(exist_ok=True)
    return build_adapter(sender=fake, credentials=creds, account_id=ACCOUNT_ID, egress_id="egress-test",
                         exchange_info=exchange, account_order_limits=limits,
                         account=LiveAccountPort(reader, clock), filters=LiveFiltersPort(info, raw),
                         journals=FileJournalOpener(tmp_path / "j"), clock=clock, monotonic=clock,
                         sleeper=fake.advance, base_url=wiring_origin or origin)


def test_full_matrix_passes_on_the_demo_origin_and_every_request_uses_it(tmp_path):
    clock = FakeClock(T0)
    fake = FakeBinance(clock)
    fake.origin = DEMO_TESTNET_BASE_URL                # the fake asserts every request URL is on this origin
    fake.price_path = wave()
    fake.tick()
    wired = _wire(tmp_path, fake, clock, DEMO_TESTNET_BASE_URL)
    assert wired.venue._base_url == DEMO_TESTNET_BASE_URL
    acct = account(digest=key_digest(DOC_KEY))
    report = run_suite(manifest(acct=acct), wired.adapter, [].append)
    assert report.status is SuiteStatus.PASSED, [(c.kind.value, c.failure) for c in report.cases]
    assert len(fake.requests) > 50


def test_wiring_refuses_a_sender_bound_to_another_origin(tmp_path):
    clock = FakeClock(T0)
    fake = FakeBinance(clock)
    fake.origin = DEMO_TESTNET_BASE_URL
    with pytest.raises(ValueError, match="wiring.origin_mismatch"):
        _wire(tmp_path, fake, clock, DEMO_TESTNET_BASE_URL, wiring_origin=TESTNET_BASE_URL)
    plain = FakeBinance(clock)                         # a sender that declares no origin of its own
    with pytest.raises(ValueError, match="^wiring.origin$"):
        _wire(tmp_path, plain, clock, TESTNET_BASE_URL, wiring_origin="https://fapi.binance.com")


# ------------------------------------------------------------------------------------------------- CLI
LIVE_KEY = "TNET02LIVEKEYMARKER0000000000000a"
LIVE_SECRET = "TNET02LIVESECRETMARKER00000000000b"


@pytest.fixture
def identity(monkeypatch):
    monkeypatch.setattr(cli, "_git", lambda *args: BUILD_SHA if args[0] == "rev-parse" else "")


def _account_file(tmp_path, origin=None, name="account.json"):
    digest = key_digest(LIVE_KEY)
    raw = {"account_id": ACCOUNT_ID, "label": "Dedicated TNET-02", "key_digest": digest, "hedge_mode": True,
           "settlement_asset": "USDT", "confirmed_at_ms": 1_900_000_000_000,
           "typed_phrase": confirmation_phrase(ACCOUNT_ID, digest)}
    if origin is not None:
        raw["origin"] = origin
    path = tmp_path / name
    path.write_text(json.dumps(raw), encoding="utf-8")
    return path


def _args(tmp_path, acct, origin):
    for name in ("journals", "out"):
        (tmp_path / name).mkdir(exist_ok=True)
    return ["run", "--expected-head", BUILD_SHA, "--account-file", str(acct), "--symbols", "BTCUSDT",
            "--origin", origin, "--journal-dir", str(tmp_path / "journals"), "--out-dir", str(tmp_path / "out"),
            "--host-id", "tnet_x"]


@pytest.fixture
def live_env(monkeypatch):
    monkeypatch.setenv("ZACKBOT_TNET02_API_KEY", LIVE_KEY)
    monkeypatch.setenv("ZACKBOT_TNET02_API_SECRET", LIVE_SECRET)


@pytest.fixture
def senders(monkeypatch):
    import newcore.venue as venue_pkg
    built = []

    def make(origin):
        fake = FakeBinance(FakeClock(time.time_ns() // 1_000_000))  # CLI signs in server time
        fake.origin = origin
        built.append(origin)
        return fake
    monkeypatch.setattr(venue_pkg, "TestnetHttpSender", make)
    return built


def test_cli_preflight_on_the_demo_origin_reads_only_there(tmp_path, identity, live_env, senders, capsys):
    acct = _account_file(tmp_path, DEMO_TESTNET_BASE_URL)
    assert cli.main(_args(tmp_path, acct, DEMO_TESTNET_BASE_URL)) == cli.EXIT_OK
    out = capsys.readouterr().out
    assert senders == [DEMO_TESTNET_BASE_URL]
    assert f"PREFLIGHT origin={DEMO_TESTNET_BASE_URL} " in out and "no order was sent" in out
    assert LIVE_KEY not in out and LIVE_SECRET not in out


def test_cli_account_file_without_origin_keeps_the_original_binding(tmp_path, identity, live_env, senders,
                                                                    capsys):
    acct = _account_file(tmp_path)
    assert cli.main(_args(tmp_path, acct, TESTNET_BASE_URL)) == cli.EXIT_OK
    assert senders == [TESTNET_BASE_URL]
    capsys.readouterr()
    assert cli.main(_args(tmp_path, acct, DEMO_TESTNET_BASE_URL)) == cli.EXIT_REFUSED
    assert capsys.readouterr().err.strip() == "REFUSED: origin_mismatch"
    assert senders == [TESTNET_BASE_URL]


@pytest.mark.parametrize("file_origin", HOSTILE[:3], ids=HOSTILE_IDS[:3])
def test_cli_refuses_an_account_file_with_an_unapproved_origin(tmp_path, identity, live_env, senders, capsys,
                                                               file_origin):
    acct = _account_file(tmp_path, file_origin)
    assert cli.main(_args(tmp_path, acct, file_origin)) == cli.EXIT_REFUSED
    assert capsys.readouterr().err.strip() == "REFUSED: account_file_origin" and senders == []


def test_cli_never_reuses_one_key_on_the_other_origin(tmp_path, identity, live_env, senders, capsys):
    demo = _account_file(tmp_path, DEMO_TESTNET_BASE_URL, "demo.json")
    legacy = _account_file(tmp_path, TESTNET_BASE_URL, "legacy.json")
    assert cli.main(_args(tmp_path, demo, DEMO_TESTNET_BASE_URL)) == cli.EXIT_OK
    capsys.readouterr()
    assert cli.main(_args(tmp_path, legacy, TESTNET_BASE_URL)) == cli.EXIT_REFUSED
    assert capsys.readouterr().err.strip() == "REFUSED: credentials_origin_reuse"
    assert senders == [DEMO_TESTNET_BASE_URL]
    assert cli.main(_args(tmp_path, demo, DEMO_TESTNET_BASE_URL)) == cli.EXIT_OK


@pytest.mark.parametrize("pins", ["[1]", "{bad", '{"0123456789abcdef": 1}'], ids=["list", "json", "value_type"])
def test_cli_refuses_corrupt_origin_pins_before_network(tmp_path, identity, live_env, senders, capsys, pins):
    acct = _account_file(tmp_path, DEMO_TESTNET_BASE_URL)
    args = _args(tmp_path, acct, DEMO_TESTNET_BASE_URL)
    (tmp_path / "journals" / "origin_pins.json").write_text(pins, encoding="utf-8")
    assert cli.main(args) == cli.EXIT_REFUSED
    assert capsys.readouterr().err.strip() == "REFUSED: origin_pins_unreadable" and senders == []


def test_cli_plan_names_the_bound_origin(tmp_path, identity, capsys):
    acct = _account_file(tmp_path, DEMO_TESTNET_BASE_URL)
    assert cli.main(["plan", "--expected-head", BUILD_SHA, "--account-file", str(acct), "--symbols",
                     "BTCUSDT"]) == cli.EXIT_OK
    assert f"PLAN origin={DEMO_TESTNET_BASE_URL}\n" in capsys.readouterr().out


def test_cli_refuses_an_account_file_with_unknown_keys(tmp_path, identity, live_env, senders, capsys):
    acct = _account_file(tmp_path, DEMO_TESTNET_BASE_URL)
    raw = json.loads(acct.read_text(encoding="utf-8"))
    raw["base_url"] = "https://fapi.binance.com"
    acct.write_text(json.dumps(raw), encoding="utf-8")
    assert cli.main(_args(tmp_path, acct, DEMO_TESTNET_BASE_URL)) == cli.EXIT_REFUSED
    assert capsys.readouterr().err.strip() == "REFUSED: account_file_keys" and senders == []


# ------------------------------------------------------------------------------------------------- review regressions
def test_cli_key_pin_survives_a_new_journal_dir_and_account_file(tmp_path, identity, live_env, senders, capsys):
    # Adversarial finding: the pin lived only in --journal-dir, so a fresh journal dir let one key reach both origins.
    demo = _account_file(tmp_path, DEMO_TESTNET_BASE_URL, "demo.json")
    legacy = _account_file(tmp_path, TESTNET_BASE_URL, "legacy.json")
    assert cli.main(_args(tmp_path, demo, DEMO_TESTNET_BASE_URL)) == cli.EXIT_OK
    capsys.readouterr()
    other = tmp_path / "second"
    other.mkdir()
    assert cli.main(_args(other, legacy, TESTNET_BASE_URL)) == cli.EXIT_REFUSED
    assert capsys.readouterr().err.strip() == "REFUSED: credentials_origin_reuse"
    assert senders == [DEMO_TESTNET_BASE_URL]
    assert not (other / "journals" / "origin_pins.json").exists()      # a refusal writes no pin anywhere


def test_cli_journal_pin_still_refuses_when_the_machine_store_is_fresh(tmp_path, identity, live_env, senders,
                                                                      capsys, monkeypatch):
    demo = _account_file(tmp_path, DEMO_TESTNET_BASE_URL, "demo.json")
    legacy = _account_file(tmp_path, TESTNET_BASE_URL, "legacy.json")
    assert cli.main(_args(tmp_path, demo, DEMO_TESTNET_BASE_URL)) == cli.EXIT_OK
    capsys.readouterr()
    monkeypatch.setattr(cli, "MACHINE_PIN_STORE", tmp_path / "fresh" / "pins.json")
    assert cli.main(_args(tmp_path, legacy, TESTNET_BASE_URL)) == cli.EXIT_REFUSED
    assert capsys.readouterr().err.strip() == "REFUSED: credentials_origin_reuse"


def test_cli_corrupt_machine_store_refuses_before_network(tmp_path, identity, live_env, senders, capsys):
    cli.MACHINE_PIN_STORE.parent.mkdir(parents=True, exist_ok=True)
    cli.MACHINE_PIN_STORE.write_text("{bad", encoding="utf-8")
    acct = _account_file(tmp_path, DEMO_TESTNET_BASE_URL)
    assert cli.main(_args(tmp_path, acct, DEMO_TESTNET_BASE_URL)) == cli.EXIT_REFUSED
    assert capsys.readouterr().err.strip() == "REFUSED: origin_pins_unreadable" and senders == []


def test_wiring_refuses_an_account_reader_on_another_origin(tmp_path):
    from .support import FakeFiltersPort, limit_payloads
    clock = FakeClock(T0)
    fake = FakeBinance(clock)
    exchange, limits = limit_payloads()
    port = LiveAccountPort(SignedReader(sender=fake, credentials=_creds(), clock=clock,
                                        base_url=DEMO_TESTNET_BASE_URL), clock)
    (tmp_path / "j").mkdir()
    with pytest.raises(ValueError, match="^wiring.origin_mismatch$"):
        build_adapter(sender=fake, credentials=_creds(), account_id=ACCOUNT_ID, egress_id="egress-test",
                      exchange_info=exchange, account_order_limits=limits, account=port,
                      filters=FakeFiltersPort(), journals=FileJournalOpener(tmp_path / "j"), clock=clock,
                      monotonic=clock, sleeper=fake.advance, base_url=TESTNET_BASE_URL)
    assert fake.requests == []
