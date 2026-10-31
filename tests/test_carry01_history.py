import hashlib
import json

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
