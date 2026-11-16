import hashlib
import json
from zipfile import ZipFile

import pytest

from tools.research import lane2_mark_sources as m


def archive(tmp_path, row="60000,10,12,9,11,0,119999,0,0,0,0,0"):
    p = tmp_path / "ABCUSDT-1m-2023-01.zip"
    with ZipFile(p, "w") as z:
        z.writestr("candles.csv", row + "\n")
    return p


def test_checksum_requires_correct_digest_and_filename(tmp_path):
    p = archive(tmp_path)
    sha = hashlib.sha256(p.read_bytes()).hexdigest()
    assert m.verify_checksum(p, f"{sha}  {p.name}\n") == sha
    with pytest.raises(ValueError, match="mismatch"):
        m.verify_checksum(p, f"{'0'*64}  {p.name}")
    with pytest.raises(ValueError, match="filename"):
        m.verify_checksum(p, f"{sha}  other.zip")
    with pytest.raises(ValueError, match="format"):
        m.verify_checksum(p, "not a checksum")


def test_containing_candle_flags_gap_and_provenance(tmp_path):
    p = archive(tmp_path)
    meta = {"source_url": "https://example/markPriceKlines.zip", "source_sha256": "a" * 64}
    rows, missing = m.extract(p, "ABCUSDT", [60001, 120000], meta)
    assert missing == [120000]
    r = rows[0]
    assert r["source_kind"] == "CANDLE_PROXY_SOURCE_ONLY"
    assert (r["value"], r["low"], r["high"]) == ("10", "9", "12")
    assert (r["candle_open_ms"], r["candle_close_ms"], r["timestamp_gap_ms"]) == (60000, 119999, 1)
    assert r["source_sha256"] == "a" * 64


def test_invalid_interval_and_price_bounds_are_rejected(tmp_path):
    p = archive(tmp_path, "60000,13,12,9,11,0,119999")
    with pytest.raises(ValueError, match="bounds"):
        m.extract(p, "ABCUSDT", [60000], {})
    p = archive(tmp_path, "60000,10,12,9,11,0,120000")
    with pytest.raises(ValueError, match="interval"):
        m.extract(p, "ABCUSDT", [60000], {})


def test_only_missing_marks_within_windows_and_complete_files(tmp_path):
    events = [{"fundingTime": 60001, "markPrice": ""}, {"fundingTime": 60002, "markPrice": "10"},
              {"fundingTime": 120000, "markPrice": None}]
    (tmp_path / "ABCUSDT.json").write_text(json.dumps({"symbol": "ABCUSDT", "status": "PAGINATED_REST_RANGE_COMPLETE", "events": events}))
    result = m.needed_events({"ABCUSDT": [[60000, 119999]]}, tmp_path)
    assert list(result.values()) == [{60001, 120000}]


def test_existing_archive_without_checksum_is_verified_against_download(tmp_path, monkeypatch):
    folder = tmp_path / "ABCUSDT"
    folder.mkdir()
    p = archive(folder)
    sha = hashlib.sha256(p.read_bytes()).hexdigest()
    calls = []
    def request(url):
        calls.append(url)
        return f"{sha}  {p.name}\n".encode()
    monkeypatch.setattr(m, "request", request)
    _, metadata = m.acquire("ABCUSDT", "2023-01", tmp_path)
    assert len(calls) == 1 and calls[0].endswith(".CHECKSUM")
    assert metadata["source_sha256"] == sha and metadata["checksum_url"] == calls[0]


def test_404_is_recorded_and_429_stops(tmp_path, monkeypatch):
    from urllib.error import HTTPError
    windows = tmp_path / "windows.json"
    windows.write_text("{}")
    monkeypatch.setattr(m, "needed_events", lambda *args: {("A", "2023-01"): {1}, ("B", "2023-01"): {2}, ("C", "2023-01"): {3}})
    calls = []
    def acquire(symbol, month, cache):
        calls.append(symbol)
        raise HTTPError("https://example", 404 if symbol == "A" else 429, "error", {}, None)
    monkeypatch.setattr(m, "acquire", acquire)
    monkeypatch.setattr(m, "rest_source", lambda cache, symbol, event: {"symbol": symbol})
    result = m.run(windows, tmp_path, tmp_path / "cache", delay=0, jobs=1)
    assert calls == ["A", "B"]
    assert [x["reason"] for x in result["gaps"]] == ["ARCHIVE_OR_CHECKSUM_HTTP_404", "HTTP_429"]
    assert result["stopped_rate_limit"] and result["incomplete"]


def test_rest_exact_and_nearest_include_source_and_flags():
    class FakeCache:
        def __init__(self, responses):
            self.responses = iter(responses)
            self.calls = []
        def get(self, endpoint, params):
            self.calls.append((endpoint, params))
            return next(self.responses), {"url": "https://fapi.binance.com/mark", "sha256": "b" * 64, "fetched_cairo": "2023-01-01T00:00:00+02:00"}
    row = [60000, "10", "12", "9", "11", "0", 119999]
    exact = FakeCache([[row]])
    result = m.rest_source(exact, "ABCUSDT", 60015)
    assert result["source_kind"] == m.LABEL and result["source_type"] == "BINANCE_MARK_REST"
    assert result["containing_candle"] and result["timestamp_gap_ms"] == 15
    assert result["close"] == "11" and result["source_sha256"] == "b" * 64
    assert exact.calls[0][1]["startTime"] == exact.calls[0][1]["endTime"] == 60000
    nearest = FakeCache([[], [row]])
    result = m.rest_source(nearest, "ABCUSDT", 120001)
    assert not result["containing_candle"] and result["timestamp_gap_ms"] == 60001
    assert nearest.calls[1][1]["limit"] == 1000
    assert m.rest_source(FakeCache([[], []]), "ABCUSDT", 120001) is None


def test_rest_rejects_invalid_ohlc():
    class BadCache:
        def get(self, endpoint, params):
            return [[60000, "10", "12", "9", "13", "0", 119999]], {}
    with pytest.raises(ValueError, match="bounds"):
        m.rest_source(BadCache(), "ABCUSDT", 60001)


def test_concurrency_is_bounded_and_forbids_excess(tmp_path, monkeypatch):
    import threading
    import time
    windows = tmp_path / "windows.json"
    windows.write_text("{}")
    monkeypatch.setattr(m, "needed_events", lambda *args: {(f"S{i}", "2023-01"): {i} for i in range(8)})
    lock, state = threading.Lock(), {"active": 0, "maximum": 0}
    def acquire(symbol, month, cache):
        with lock:
            state["active"] += 1
            state["maximum"] = max(state["maximum"], state["active"])
        time.sleep(0.03)
        with lock:
            state["active"] -= 1
        return None, {}
    monkeypatch.setattr(m, "acquire", acquire)
    monkeypatch.setattr(m, "extract", lambda archive, symbol, events, meta: ([], []))
    result = m.run(windows, tmp_path, tmp_path / "cache", delay=0, jobs=4)
    assert 1 < state["maximum"] <= 4 and not result["incomplete"]
    with pytest.raises(ValueError, match="between"):
        m.run(windows, tmp_path, tmp_path / "cache", delay=0, jobs=5)


def test_418_stops_without_submitting_remaining_work(tmp_path, monkeypatch):
    from urllib.error import HTTPError
    windows = tmp_path / "windows.json"
    windows.write_text("{}")
    monkeypatch.setattr(m, "needed_events", lambda *args: {(f"S{i}", "2023-01"): {i} for i in range(8)})
    calls = []
    def acquire(symbol, month, cache):
        calls.append(symbol)
        raise HTTPError("https://example", 418, "error", {}, None)
    monkeypatch.setattr(m, "acquire", acquire)
    result = m.run(windows, tmp_path, tmp_path / "cache", delay=0, jobs=1)
    assert calls == ["S0"] and result["stopped_rate_limit"] and result["incomplete"]
    assert result["gaps"][0]["reason"] == "HTTP_418"
