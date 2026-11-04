"""TNET-02 live pieces against the fake exchange: signer, signed reader, account and filter ports, limit payloads."""
from __future__ import annotations

import copy
import pickle
from decimal import Decimal

import pytest

from newcore.testnet_harness import SuiteStatus, run_suite
from newcore.tnet02.journal import FileJournalOpener
from newcore.tnet02.live import (API_KEY_ENV, API_SECRET_ENV, EnvCredentialSource, LiveAccountPort, LiveError,
                                 LiveFiltersPort, SignedReader, read_limit_payloads)
from newcore.tnet02.wiring import build_adapter
from newcore.venue.binance_usdm_testnet import HttpResponse

from .fake_binance import FakeBinance, FakeClock
from .support import ACCOUNT_ID, T0, account, manifest, wave
from newcore.domain.account import confirmation_phrase

# Binance's published HMAC-SHA256 example (API docs, "SIGNED endpoint examples")
DOC_SECRET = "NhqPtmdSJYdKjVHjA7PZj4Mge3R5YNiP1e3UZjInClVN65XAbvqqM6A7H5fATj0j"
DOC_KEY = "vmPUZE6mv9SD5VNHk4HlWFsOr6aKE2zvsw0MuIgwCIPy6utIco14y7Ju91duEh8A"
DOC_PAYLOAD = (b"symbol=LTCBTC&side=BUY&type=LIMIT&timeInForce=GTC&quantity=1&price=0.1&recvWindow=5000"
               b"&timestamp=1499827319559")
DOC_SIGNATURE = "c8db56825ae71d6d79447849e617115f4a920fa2acdcab2b053c4b2838bd6b71"


def _creds():
    return EnvCredentialSource({API_KEY_ENV: DOC_KEY, API_SECRET_ENV: DOC_SECRET})


# ------------------------------------------------------------------------------------------------- credentials
def test_signer_matches_binance_documented_vector_and_never_shows_the_secret():
    creds = _creds()
    assert creds.sign(DOC_PAYLOAD) == DOC_SIGNATURE
    assert creds.api_key() == DOC_KEY
    for text in (repr(creds), str(creds), str(vars(type(creds)))):
        assert DOC_SECRET not in text
    with pytest.raises(LiveError):
        pickle.dumps(creds)
    with pytest.raises(LiveError):
        copy.copy(creds)
    assert not hasattr(creds, "__dict__")


@pytest.mark.parametrize("env, code", [
    ({}, "credentials.api_key_missing"),
    ({API_KEY_ENV: DOC_KEY}, "credentials.api_secret_missing"),
    ({API_KEY_ENV: "short", API_SECRET_ENV: DOC_SECRET}, "credentials.api_key_missing"),
    ({API_KEY_ENV: DOC_KEY, API_SECRET_ENV: DOC_SECRET + "\n"}, "credentials.api_secret_missing"),
    ({API_KEY_ENV: DOC_KEY, API_SECRET_ENV: DOC_KEY}, "credentials.key_equals_secret"),
], ids=["empty", "no_secret", "short_key", "secret_newline", "key_equals_secret"])
def test_credentials_refuse_missing_or_malformed_values_without_echoing_them(env, code):
    with pytest.raises(LiveError) as caught:
        EnvCredentialSource(env)
    assert caught.value.code == code and DOC_SECRET not in str(caught.value) and DOC_KEY not in str(caught.value)


# ------------------------------------------------------------------------------------------------- reader
def _reader(fake, creds=None):
    return SignedReader(sender=fake, credentials=creds or _creds(), clock=fake.clock)


def test_signed_read_carries_signature_and_key_header_only():
    fake = FakeBinance(FakeClock(T0))
    seen = []
    real = fake.__class__.__call__

    def spy(request):
        seen.append(request)
        return real(fake, request)
    reader = SignedReader(sender=spy, credentials=_creds(), clock=fake.clock)
    reader.signed("/fapi/v2/balance")
    request = seen[0]
    assert request.method == "GET" and request.headers == (("X-MBX-APIKEY", DOC_KEY),)
    assert request.query.startswith("recvWindow=5000&timestamp=") and "&signature=" in request.query
    assert DOC_SECRET not in request.query
    reader.public("/fapi/v1/exchangeInfo")
    assert seen[1].headers == () and seen[1].query == ""


@pytest.mark.parametrize("status, code", [(418, "reader.http_418"), (429, "reader.http_429"),
                                          (401, "reader.http_status"), (500, "reader.http_status")])
def test_reader_errors_are_codes_without_venue_text(status, code):
    def sender(_request):
        return HttpResponse(status=status, body=b'{"code":-2015,"msg":"Invalid API-key ' + DOC_KEY.encode() + b'"}')
    with pytest.raises(LiveError) as caught:
        SignedReader(sender=sender, credentials=_creds(), clock=lambda: T0).signed("/fapi/v2/balance")
    assert caught.value.code == code and DOC_KEY not in str(caught.value) and "Invalid" not in str(caught.value)


def test_reader_hides_transport_exception_text():
    def sender(_request):
        raise OSError("boom " + DOC_SECRET)
    with pytest.raises(LiveError) as caught:
        SignedReader(sender=sender, credentials=_creds(), clock=lambda: T0).signed("/fapi/v2/balance")
    assert caught.value.code == "reader.transport" and caught.value.__cause__ is None
    assert DOC_SECRET not in repr(caught.value)


# ------------------------------------------------------------------------------------------------- account port
def test_account_port_reads_marks_leverage_hedge_and_equity():
    fake = FakeBinance(FakeClock(T0))
    fake.leverage["BTCUSDT"] = 2
    facts = LiveAccountPort(_reader(fake), fake.clock).facts(("BTCUSDT", "ETHUSDT"))
    assert facts.hedge_mode is True and facts.settlement_asset == "USDT" and facts.equity == fake.equity
    assert facts.symbol("BTCUSDT").leverage == 2 and facts.symbol("BTCUSDT").mark_price == fake.marks["BTCUSDT"]
    assert {m for m, _p, _q in fake.requests} == {"GET"}


def test_account_port_reports_one_way_mode_and_refuses_unknown_symbols():
    fake = FakeBinance(FakeClock(T0))
    fake.hedge_mode = False
    assert LiveAccountPort(_reader(fake), fake.clock).facts(("BTCUSDT",)).hedge_mode is False
    with pytest.raises(LiveError) as caught:
        LiveAccountPort(_reader(fake), fake.clock).facts(("SOLUSDT",))
    assert caught.value.code == "account.symbol_missing"


def test_account_port_takes_the_higher_side_leverage():
    fake = FakeBinance(FakeClock(T0))
    real = fake._get_fapi_v2_positionRisk

    def rows(params):
        response = real(params)
        body = __import__("json").loads(response.body)
        body[1]["leverage"] = "20"
        return HttpResponse(status=200, body=__import__("json").dumps(body).encode())
    fake._get_fapi_v2_positionRisk = rows
    assert LiveAccountPort(_reader(fake), fake.clock).facts(("BTCUSDT",)).symbol("BTCUSDT").leverage == 20


# ------------------------------------------------------------------------------------------------- filters port
def _info():
    fake = FakeBinance(FakeClock(T0))
    return _reader(fake).public("/fapi/v1/exchangeInfo")


def test_filters_port_uses_the_stricter_lot_rules_and_pins_the_bytes():
    info, raw = _info()
    f = LiveFiltersPort(info, raw).filters("BTCUSDT")
    assert (f.tick_size, f.step_size, f.min_quantity, f.max_quantity, f.min_notional) == (
        Decimal("0.10"), Decimal("0.001"), Decimal("0.001"), Decimal("120"), Decimal("100"))
    assert f.source_sha256 == __import__("hashlib").sha256(raw).hexdigest()


@pytest.mark.parametrize("mutate, code", [
    (lambda s: s.__setitem__("status", "SETTLING"), "filters.symbol_not_tradable"),
    (lambda s: s.__setitem__("contractType", "CURRENT_QUARTER"), "filters.symbol_not_tradable"),
    (lambda s: s.__setitem__("filters", [f for f in s["filters"] if f["filterType"] != "MARKET_LOT_SIZE"]),
     "filters.missing"),
    (lambda s: s["filters"][0].__setitem__("tickSize", "NaN"), "filters.tick"),
])
def test_filters_port_refuses_untradable_or_incomplete_symbols(mutate, code):
    info, raw = _info()
    mutate(info["symbols"][0])
    with pytest.raises(LiveError) as caught:
        LiveFiltersPort(info, raw).filters("BTCUSDT")
    assert caught.value.code == code


# ------------------------------------------------------------------------------------------------- end to end
def test_full_matrix_passes_with_live_ports_over_the_fake_exchange(tmp_path):
    clock = FakeClock(T0)
    fake = FakeBinance(clock)
    fake.price_path = wave()
    fake.tick()
    creds = _creds()
    reader = SignedReader(sender=fake, credentials=creds, clock=clock)
    exchange, limits, info, raw = read_limit_payloads(reader)
    (tmp_path / "j").mkdir()
    wired = build_adapter(sender=fake, credentials=creds, account_id=ACCOUNT_ID, egress_id="egress-test",
                          exchange_info=exchange, account_order_limits=limits,
                          account=LiveAccountPort(reader, clock), filters=LiveFiltersPort(info, raw),
                          journals=FileJournalOpener(tmp_path / "j"), clock=clock, monotonic=clock,
                          sleeper=fake.advance)
    from newcore.tnet02.wiring import key_digest
    digest = key_digest(DOC_KEY)
    acct = account(digest=digest)
    assert acct.confirmation.typed_phrase == confirmation_phrase(ACCOUNT_ID, digest)
    out: list[bytes] = []
    report = run_suite(manifest(acct=acct), wired.adapter, out.append)
    assert report.status is SuiteStatus.PASSED, [(c.kind.value, c.failure) for c in report.cases]
    assert DOC_SECRET.encode() not in out[0] and DOC_KEY.encode() not in out[0]
