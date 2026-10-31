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
