"""Bounded fake HTTP pages only; no real acquisition or candidate data."""
import json
from urllib.parse import parse_qs, urlsplit

import pytest

from tools.research import authority_v6 as V
from tests.test_authority_v6_acquisition import _pit_v9


def capture(tmp_path, fetch, symbol="BTCUSDT"):
    return V.capture_funding_residual((symbol,), tmp_path / "store", tmp_path / "ledger.jsonl",
        fetch, max_total_bytes=1000000, pit_v9=_pit_v9((symbol,)))


def response(symbol="BTCUSDT"):
    return V.canonical([{"symbol": symbol, "fundingTime": V.API_START_MS,
                         "fundingRate": "0.0001"}])


def canonical_path(tmp_path, symbol="BTCUSDT"):
    return tmp_path / "store" / "um/api/fundingRate" / symbol / (
        f"{symbol}-fundingRate-2026-11-01-2026-11-04-p000.json")


def test_unicode_native_symbol_roundtrips_url_path_and_capture(tmp_path):
    symbol = "\u54c8\u57fa\u7c73USDT"
    url = V.funding_url(symbol, V.API_START_MS)
    assert url.isascii() and parse_qs(urlsplit(url).query)["symbol"] == [symbol]
    result = capture(tmp_path, lambda u: (200, response(symbol), u), symbol)
    assert result["completed"] == [symbol]
    path = canonical_path(tmp_path, symbol)
    meta = V.M.archive_meta(path.relative_to(tmp_path / "store").as_posix())
    assert meta["symbol"] == symbol
    assert path.read_bytes() == response(symbol)


@pytest.mark.parametrize("symbol", ["../BTCUSDT", "BTC/USDT", "BTC\\USDT", "btcUSDT", "BTC?USDT"])
def test_unsafe_or_noncanonical_symbol_remains_refused(symbol):
    with pytest.raises(V.AuthorityV6Error):
        V.funding_url(symbol, V.API_START_MS)


@pytest.mark.parametrize("symbol", ["BTCUSDT", "\u54c8\u57fa\u7c73USDT"])
def test_verified_positive_page_resumes_without_network_or_new_attempt(tmp_path, symbol):
    raw = response(symbol)
    first = capture(tmp_path, lambda u: (200, raw, u), symbol)
    before = sorted((tmp_path / "store").rglob("*.raw"))
    def forbidden(_):
        raise AssertionError("verified cache made a network request")
    assert capture(tmp_path, forbidden, symbol) == first
    assert sorted((tmp_path / "store").rglob("*.raw")) == before
    entries = [json.loads(x) for x in (tmp_path / "ledger.jsonl").read_text().splitlines()]
    assert entries[-1]["decision"] == "CACHE_REUSED_REVALIDATED"


@pytest.mark.parametrize("attack", ["canonical", "attempt", "sidecar", "provenance"])
def test_accepted_cache_tampering_fails_closed(tmp_path, attack):
    capture(tmp_path, lambda u: (200, response(), u))
    target = canonical_path(tmp_path)
    if attack == "canonical":
        target.write_bytes(b"[]")
    elif attack == "attempt":
        next((tmp_path / "store").rglob("*.raw")).write_bytes(b"[]")
    elif attack == "sidecar":
        target.with_name(target.name + ".ok").write_text("0" * 64)
    else:
        p = target.with_name(target.name + ".provenance.json")
        doc = json.loads(p.read_bytes()); doc["params"]["endTime"] += 1
        p.write_bytes(V.canonical(doc))
    with pytest.raises(V.AuthorityV6Error):
        capture(tmp_path, lambda _: (_ for _ in ()).throw(AssertionError("network after corrupt cache")))


def test_empty_response_is_observed_again_and_preserved(tmp_path):
    calls = []
    def fetch(url):
        calls.append(url); return 200, b"[]", url
    assert capture(tmp_path, fetch)["incomplete"] == ["BTCUSDT"]
    assert capture(tmp_path, fetch)["incomplete"] == ["BTCUSDT"]
    assert len(calls) == 2 and len(list((tmp_path / "store").rglob("*.raw"))) == 2


def test_newer_failed_observation_cannot_fall_back_to_old_cache(tmp_path):
    capture(tmp_path, lambda u: (200, response(), u))
    ledger = tmp_path / "ledger.jsonl"
    rel = canonical_path(tmp_path).relative_to(tmp_path / "store").as_posix()
    with ledger.open("ab") as out:
        out.write(V.canonical({"kind": "FUNDING_API", "artifact_path": rel,
            "path": rel + ".attempt-001.raw", "attempt": 1, "decision": "FETCH_FAILED"}))
    calls = []
    def fetch(url):
        calls.append(url); return 200, response(), url
    assert capture(tmp_path, fetch)["completed"] == ["BTCUSDT"]
    assert len(calls) == 1


def test_full_page_then_terminal_page_reuses_exact_cursor_chain(tmp_path):
    starts = []
    def fetch(url):
        start = int(parse_qs(urlsplit(url).query)["startTime"][0]); starts.append(start)
        count = 1000 if start == V.API_START_MS else 1
        rows = [{"symbol": "BTCUSDT", "fundingTime": start + i, "fundingRate": "0.0001"}
                for i in range(count)]
        return 200, V.canonical(rows), url
    first = capture(tmp_path, fetch)
    assert starts == [V.API_START_MS, V.API_START_MS + 1000]
    def forbidden(_):
        raise AssertionError("complete cached page chain queried network")
    assert capture(tmp_path, forbidden) == first


def test_cached_bytes_still_count_toward_original_budget(tmp_path):
    capture(tmp_path, lambda u: (200, response(), u))
    def forbidden(_):
        raise AssertionError("budget-rejected cache queried network")
    with pytest.raises(V.AuthorityV6Error, match="byte cap"):
        V.capture_funding_residual(("BTCUSDT",), tmp_path / "store", tmp_path / "ledger.jsonl",
            forbidden, max_total_bytes=len(response()) - 1, pit_v9=_pit_v9(("BTCUSDT",)))
