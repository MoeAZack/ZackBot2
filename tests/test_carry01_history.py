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
    with pytest.raises(H.HistoryDataError, match="changed after signing"):
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


def test_manifest_document_binds_contract_and_has_no_results():
    doc = H.manifest_document(contract())
    assert doc["contract_digest_sha256"].startswith("bf3daee2")
    assert doc["archive_count"] == len(doc["archives"]) == 996
    assert doc["result_fields_pending_fetch"] == ["sha256", "bytes"]
    json.dumps(doc, sort_keys=True)


class Response:
    def __init__(self, content, *, redirect=False):
        self.content = content
        self.is_redirect = redirect
        self.is_permanent_redirect = False

    def raise_for_status(self):
        return None


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
    checksum = hashlib.sha256(payload).hexdigest().encode() + b"  x.zip\n"
    session = Session([Response(checksum), Response(payload)])
    result = H.fetch_verified(spec, tmp_path, session)
    kept = tmp_path / H.archive_relative_path(spec.url)
    assert kept.read_bytes() == payload
    assert kept.with_name(kept.name + ".CHECKSUM").read_bytes() == checksum
    assert result["sha256"] == hashlib.sha256(payload).hexdigest()
    assert all(call[1]["allow_redirects"] is False for call in session.calls)
    assert not kept.with_name(kept.name + ".part").exists()


def test_fetch_verified_refuses_redirect_and_offsite(tmp_path):
    spec = H.build_manifest(contract())[0]
    with pytest.raises(H.HistoryDataError, match="redirect"):
        H.fetch_verified(spec, tmp_path, Session([Response(b"", redirect=True)]))
    with pytest.raises(H.HistoryDataError, match="outside"):
        H.archive_relative_path("https://example.com/data/x.zip")


def zipped(name, text):
    out = io.BytesIO()
    with zipfile.ZipFile(out, "w", zipfile.ZIP_DEFLATED) as archive:
        archive.writestr(name, text)
    return out.getvalue()


def kline(opened, open_price="100", high="110", low="90", close="105"):
    return f"{opened},{open_price},{high},{low},{close},1,{opened + 3599999},1,1,1,1,0\n"


def test_kline_parser_pins_schema_grid_ohlc_and_seal():
    start = 1_700_000_000_000 // 3_600_000 * 3_600_000
    rows = H.parse_kline_zip(zipped("BTCUSDT.csv", kline(start) + kline(start + 3_600_000)))
    assert rows[0]["high"] == 110 and rows[-1]["close"] == 105
    H.validate_hourly_contiguous(rows)
    with pytest.raises(H.HistoryDataError, match="OHLC"):
        H.parse_kline_zip(zipped("x.csv", kline(start, high="99")))
    with pytest.raises(H.HistoryDataError, match="signed seal"):
        H.parse_kline_zip(zipped("x.csv", kline(H.SEAL_MS)))


def test_hourly_gap_is_refused():
    start = 1_700_000_000_000 // 3_600_000 * 3_600_000
    rows = H.parse_kline_zip(zipped("x.csv", kline(start) + kline(start + 7_200_000)))
    with pytest.raises(H.HistoryDataError, match="gap or overlap"):
        H.validate_hourly_contiguous(rows)


def test_funding_parser_accepts_jitter_but_refuses_schema_order_and_seal():
    header = "calc_time,funding_interval_hours,last_funding_rate\n"
    good = header + "1700000000002,8,0.0001\n1700028800000,8,-0.0002\n"
    rows = H.parse_funding_zip(zipped("funding.csv", good))
    assert rows[0]["funding_time"] % 1000 == 2 and rows[1]["funding_rate"] < 0
    with pytest.raises(H.HistoryDataError, match="frozen schema"):
        H.parse_funding_zip(zipped("x.csv", "time,rate\n1,0.1\n"))
    bad_order = header + "1700000000002,8,0.1\n1700000000001,8,0.1\n"
    with pytest.raises(H.HistoryDataError, match="ordering"):
        H.parse_funding_zip(zipped("x.csv", bad_order))
    sealed = header + f"{H.SEAL_MS},8,0.1\n"
    with pytest.raises(H.HistoryDataError, match="signed seal"):
        H.parse_funding_zip(zipped("x.csv", sealed))


def test_zip_reader_refuses_multiple_members_and_traversal():
    out = io.BytesIO()
    with zipfile.ZipFile(out, "w") as archive:
        archive.writestr("a.csv", "x")
        archive.writestr("b.csv", "x")
    with pytest.raises(H.HistoryDataError, match="exactly one"):
        H.parse_kline_zip(out.getvalue())
    with pytest.raises(H.HistoryDataError, match="unsafe archive member"):
        H.parse_kline_zip(zipped("../x.csv", "x"))
