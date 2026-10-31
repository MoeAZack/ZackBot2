import hashlib
import io
import json
import zipfile

import pytest

from tools.research import carry01_history as H


def contract():
    return H.load_signed_contract()


def test_signed_contract_digest_is_required():
    p = contract()
    assert p["contract_digest_sha256"] == H.canonical_digest(p)
    p["signed"] = False
    with pytest.raises(H.HistoryDataError, match="not signed"):
        H.build_manifest(p)
    p = contract()
    p["data"]["sealed_development_end_exclusive"] = "2026-09-29T00:00:00Z"
    p["contract_digest_sha256"] = H.canonical_digest(p)
    with pytest.raises(H.HistoryDataError, match="unexpected.*seal"):
        H.build_manifest(p)


def test_manifest_uses_months_then_only_preseal_days():
    specs = H.build_manifest(contract())
    assert len(specs) == 996
    assert len({s.url for s in specs}) == len(specs)
    assert all(H.safe_archive_url(s.url) and H.safe_archive_url(s.checksum_url) for s in specs)
    assert not any("-2026-09.zip" in s.url for s in specs)
    assert any("-2026-09-27.zip" in s.url for s in specs)
    assert not any("-2026-09-28.zip" in s.url for s in specs)


def test_manifest_has_all_three_series_for_every_symbol_period():
    specs = H.build_manifest(contract())
    grouped = {}
    for spec in specs:
        grouped.setdefault((spec.symbol, spec.frequency, spec.period), set()).add(spec.series)
    assert all(series == {"klines", "markPriceKlines", "fundingRate"}
               for series in grouped.values())
    assert min(p for (s, f, p) in grouped if s == "BTCUSDT" and f == "monthly") == "2020-01"
    assert min(p for (s, f, p) in grouped if s == "BTCUSDC" and f == "monthly") == "2024-01"


def test_checksum_parser_and_verifier_fail_closed():
    payload = b"frozen archive bytes"
    digest = hashlib.sha256(payload).hexdigest().encode()
    assert H.verify_bytes(payload, digest + b"  file.zip\n") == digest.decode()
    with pytest.raises(H.HistoryDataError, match="mismatch"):
        H.verify_bytes(payload + b"x", digest)
    for bad in (b"", b"xyz", b"g" * 64):
        with pytest.raises(H.HistoryDataError):
            H.parse_checksum(bad)
    with pytest.raises(H.HistoryDataError, match="different file"):
        H.parse_checksum(digest + b"  **file.zip\n", "file.zip")
    assert H.parse_checksum(digest + b"  *file.zip\n", "file.zip") == digest.decode()


def test_manifest_document_binds_contract_and_has_no_results():
    doc = H.manifest_document(contract())
    assert doc["contract_digest_sha256"] == H.canonical_digest(contract())
    assert doc["archive_count"] == len(doc["archives"]) == 996
    assert doc["result_fields_pending_fetch"] == ["sha256", "bytes"]
    json.dumps(doc, sort_keys=True)


class Response:
    def __init__(self, content, *, redirect=False):
        self.content = content
        self.is_redirect = redirect
        self.is_permanent_redirect = False
        self.closed = False

    def raise_for_status(self):
        return None

    def close(self):
        self.closed = True


class Session:
    def __init__(self, replies):
        self.replies = list(replies)
        self.calls = []

    def get(self, url, **kwargs):
        self.calls.append((url, kwargs))
        return self.replies.pop(0)


def test_fetch_verified_is_exact_host_no_redirect_and_atomic(tmp_path):
    spec = H.build_manifest(contract())[0]
    payload = b"zip bytes"
    filename = spec.url.rsplit("/", 1)[-1]
    checksum = hashlib.sha256(payload).hexdigest().encode() + b"  " + filename.encode() + b"\n"
    responses = [Response(checksum), Response(payload)]
    session = Session(responses)
    result = H.fetch_verified(spec, tmp_path, session)
    kept = tmp_path / H.archive_relative_path(spec.url)
    assert kept.read_bytes() == payload
    assert kept.with_name(kept.name + ".CHECKSUM").read_bytes() == checksum
    assert result["sha256"] == hashlib.sha256(payload).hexdigest()
    assert all(call[1]["allow_redirects"] is False for call in session.calls)
    assert all(call[1]["stream"] is True for call in session.calls)
    assert all(response.closed for response in responses)
    assert not kept.with_name(kept.name + ".part").exists()
    assert H.fetch_verified(spec, tmp_path, Session([Response(checksum), Response(payload)]))["reused"] is True
    other = b"different zip bytes"
    other_sum = hashlib.sha256(other).hexdigest().encode() + b"  " + filename.encode() + b"\n"
    with pytest.raises(H.HistoryDataError, match="overwrite"):
        H.fetch_verified(spec, tmp_path, Session([Response(other_sum), Response(other)]))


def test_fetch_verified_refuses_redirect_and_offsite(tmp_path):
    spec = H.build_manifest(contract())[0]
    with pytest.raises(H.HistoryDataError, match="redirect"):
        H.fetch_verified(spec, tmp_path, Session([Response(b"", redirect=True)]))
    with pytest.raises(H.HistoryDataError, match="outside"):
        H.archive_relative_path("https://example.com/data/x.zip")
    assert H.safe_archive_url("https://data.binance.vision:abc/data/x.zip") is False


def zipped(name, text):
    out = io.BytesIO()
    with zipfile.ZipFile(out, "w", zipfile.ZIP_DEFLATED) as archive:
        archive.writestr(name, text)
    return out.getvalue()


def kline(opened, open_price="100", high="110", low="90", close="105"):
    return f"{opened},{open_price},{high},{low},{close},1,{opened + 3599999},1,1,1,1,0\n"


def spec(period="2025-01-01", *, series="klines", frequency="daily"):
    return H.ArchiveSpec("BTCUSDT", "spot" if series == "klines" else "futures",
                         series, frequency, period, "x", "y")


def day_klines(start, *, micros=False):
    lines = []
    for hour in range(24):
        opened = start + hour * 3_600_000
        if micros:
            lines.append(f"{opened * 1000},100,110,90,105,1,{(opened + 3_599_999) * 1000 + 999},1,1,1,1,0\n")
        else:
            lines.append(kline(opened))
    return "".join(lines)


def test_kline_parser_pins_schema_grid_ohlc_and_seal():
    start = 1_735_689_600_000
    rows = H.parse_kline_zip(zipped("BTCUSDT.csv", day_klines(start)), spec())
    assert rows[0]["high"] == 110 and rows[-1]["close"] == 105
    H.validate_hourly_contiguous(rows)
    with pytest.raises(H.HistoryDataError, match="OHLC"):
        H.parse_kline_zip(zipped("x.csv", kline(start, high="99")), spec())
    with pytest.raises(H.HistoryDataError, match="signed seal"):
        H.parse_kline_zip(zipped("x.csv", kline(H.SEAL_MS)), spec("2026-09-28"))
    with pytest.raises(H.HistoryDataError, match="ASCII unsigned"):
        H.parse_kline_zip(zipped("x.csv", " " + kline(start)), spec())


def test_kline_parser_normalizes_official_microsecond_transition():
    start = 1_735_689_600_000
    row = H.parse_kline_zip(zipped("BTCUSDT.csv", day_klines(start, micros=True)), spec())[0]
    assert row["open_time"] == start
    assert row["close_time"] == start + 3_599_999


def test_period_coverage_is_pinned_when_spec_is_provided():
    start = 1_735_689_600_000
    text = "".join(kline(start + hour * 3_600_000) for hour in range(24))
    archive_spec = spec()
    assert len(H.parse_kline_zip(zipped("x.csv", text), archive_spec)) == 24
    with pytest.raises(H.HistoryDataError, match="exactly cover"):
        H.parse_kline_zip(zipped("x.csv", text.splitlines(True)[1]), archive_spec)


def test_hourly_gap_is_refused():
    start = 1_735_689_600_000
    with pytest.raises(H.HistoryDataError, match="gap or overlap"):
        H.parse_kline_zip(zipped("x.csv", kline(start) + kline(start + 7_200_000)), spec())


def test_funding_parser_accepts_jitter_but_refuses_schema_order_and_seal():
    header = "calc_time,funding_interval_hours,last_funding_rate\n"
    good = header + "1700000000002,8,0.0001\n1700028800000,8,-0.0002\n"
    funding_spec = spec("2023-11", series="fundingRate", frequency="monthly")
    rows = H.parse_funding_zip(zipped("funding.csv", good), funding_spec)
    assert rows[0]["funding_time"] % 1000 == 2 and rows[1]["funding_rate"] < 0
    with pytest.raises(H.HistoryDataError, match="frozen schema"):
        H.parse_funding_zip(zipped("x.csv", "time,rate\n1,0.1\n"), funding_spec)
    bad_order = header + "1700000000002,8,0.1\n1700000000001,8,0.1\n"
    with pytest.raises(H.HistoryDataError, match="ordering"):
        H.parse_funding_zip(zipped("x.csv", bad_order), funding_spec)
    sealed = header + f"{H.SEAL_MS},8,0.1\n"
    with pytest.raises(H.HistoryDataError, match="signed seal"):
        H.parse_funding_zip(zipped("x.csv", sealed), spec("2026-09-28", series="fundingRate"))
    with pytest.raises(H.HistoryDataError, match="ASCII unsigned"):
        H.parse_funding_zip(zipped("x.csv", header + "+1700000000002,8,0.1\n"), funding_spec)
    with pytest.raises(H.HistoryDataError, match="strict ASCII"):
        H.parse_funding_zip(zipped("x.csv", header + "1700000000002,8,1_0\n"), funding_spec)
    with pytest.raises(H.HistoryDataError, match="magnitude"):
        H.parse_funding_zip(zipped("x.csv", header + "1700000000002,8,99\n"), funding_spec)


def test_funding_events_must_stay_inside_declared_period():
    header = "calc_time,funding_interval_hours,last_funding_rate\n"
    funding_spec = spec("2023-11", series="fundingRate", frequency="monthly")
    start, end = H._period_bounds(funding_spec)
    with pytest.raises(H.HistoryDataError, match="outside its declared period"):
        H.parse_funding_zip(zipped("x.csv", header + f"{start - 1},8,0.0001\n"), funding_spec)
    with pytest.raises(H.HistoryDataError, match="outside its declared period"):
        H.parse_funding_zip(zipped("x.csv", header + f"{end},8,0.0001\n"), funding_spec)


def test_zip_reader_refuses_multiple_members_and_traversal():
    out = io.BytesIO()
    with zipfile.ZipFile(out, "w") as archive:
        archive.writestr("a.csv", "x")
        archive.writestr("b.csv", "x")
    with pytest.raises(H.HistoryDataError, match="exactly one"):
        H.parse_kline_zip(out.getvalue(), spec())
    with pytest.raises(H.HistoryDataError, match="unsafe archive member"):
        H.parse_kline_zip(zipped("../x.csv", "x"), spec())


def test_mixed_units_leading_zero_and_noncanonical_micro_residue_refuse():
    start = 1_735_689_600_000
    mixed = kline(start) + day_klines(start + 3_600_000, micros=True)
    with pytest.raises(H.HistoryDataError, match="mixes timestamp units"):
        H.parse_kline_zip(zipped("x.csv", mixed), spec())
    leading = "0" + kline(start)
    with pytest.raises(H.HistoryDataError, match="ASCII unsigned"):
        H.parse_kline_zip(zipped("x.csv", leading), spec())
    bad_close = f"{start * 1000},100,110,90,105,1,{(start + 3_599_999) * 1000 + 998},1,1,1,1,0\n"
    with pytest.raises(H.HistoryDataError, match="residue"):
        H.parse_kline_zip(zipped("x.csv", bad_close), spec())
