from pathlib import Path
from types import SimpleNamespace
import hashlib
import json

import pytest

from tools.research import c258_archive_source as S


def source(monkeypatch, records):
    monkeypatch.setattr(S.C, "validate_authority_pins", lambda *a, **k: {
        "manifest_v6": {"active_base_files_v5": records, "files_v6": []},
        "pit_v9": {}, "filter_v4": {}})
    return S.C258ArchiveSource(SimpleNamespace(protected_head="a"*40), Path("unused"))


def test_validation_precedes_any_source_read(monkeypatch):
    def refuse(*args, **kwargs):
        raise S.C.IncompleteAuthority("bad protected pin")
    monkeypatch.setattr(S.C, "validate_authority_pins", refuse)
    monkeypatch.setattr(S, "_read_record_rows", lambda *a, **k: pytest.fail("source opened"))
    with pytest.raises(S.C.IncompleteAuthority, match="bad protected pin"):
        S.C258ArchiveSource(None, Path("unused"))


@pytest.mark.parametrize("fold", ["holdout", "sealed_holdout", "wf4", "arbitrary"])
def test_unknown_or_sealed_fold_refuses_before_archive_open(monkeypatch, fold):
    archive = source(monkeypatch, [])
    monkeypatch.setattr(S, "_read_record_rows", lambda *a, **k: pytest.fail("source opened"))
    with pytest.raises(S.C.IncompleteAuthority):
        archive.load(symbol="BTCUSDT", stream="klines", fold=fold, start_ms=0, end_ms=3600000)


def test_exact_contiguous_rows_and_overlap_refusal(monkeypatch):
    lo, _ = S.frozen_window("wf1")
    record = {"path": "bound", "sha256": "a"*64, "symbol": "BTCUSDT",
              "series": "klines", "interval": "1h", "first_open_ms": lo,
              "last_open_ms": lo+S.C.HOUR_MS}
    archive = source(monkeypatch, [record])
    tape = [(lo, 1), (lo+S.C.HOUR_MS, 2)]
    monkeypatch.setattr(S, "_read_record_rows", lambda *a, **k: ({}, tape))
    result = archive.load(symbol="BTCUSDT", stream="klines", fold="wf1",
                          start_ms=lo, end_ms=lo+2*S.C.HOUR_MS)
    assert result.rows == tuple(tape)
    assert result.records == (("bound", "a"*64),)
    tape.pop()
    with pytest.raises(S.C.IncompleteAuthority, match="missing canonical"):
        archive.load(symbol="BTCUSDT", stream="klines", fold="wf1",
                     start_ms=lo, end_ms=lo+2*S.C.HOUR_MS)
    tape.append(tape[0])
    with pytest.raises(S.C.IncompleteAuthority, match="overlapping"):
        archive.load(symbol="BTCUSDT", stream="klines", fold="wf1",
                     start_ms=lo, end_ms=lo+2*S.C.HOUR_MS)


def test_duplicate_manifest_path_refused(monkeypatch):
    with pytest.raises(S.C.IncompleteAuthority, match="duplicate active"):
        source(monkeypatch, [{"path": "same"}, {"path": "same"}])


def test_protected_filter_snapshot_preserves_full_pit_order_without_core20_cut(monkeypatch):
    archive = source(monkeypatch, [])
    lo, _ = S.frozen_window("wf1")
    members = [f"S{i}USDT" for i in range(24)]
    payload = {"symbols": [{"symbol": symbol, "status": "TRADING",
        "contractType": "PERPETUAL", "quoteAsset": "USDT", "underlyingType": "COIN",
        "filters": [{"filterType": "PRICE_FILTER", "tickSize": "0.01"},
                    {"filterType": "MARKET_LOT_SIZE", "stepSize": "0.1", "minQty": "0.1", "maxQty": "1000"},
                    {"filterType": "MIN_NOTIONAL", "notional": "5"}]} for symbol in reversed(members)]}
    raw = json.dumps(payload).encode()
    archive._pit = {"weeks": [{"monday_ms": lo-S.C.HOUR_MS,
                               "books": {"crypto": {"members": members}}}]}
    archive._filters = {"fold_bindings": {"wf1": "2024-12-13T04:44:48Z"}, "snapshots": [{
        "captured_at": "2024-12-13T04:44:48Z", "server_time": "2024-12-13T04:00:07Z",
        "path": "public-snapshot.json", "sha256": hashlib.sha256(raw).hexdigest()}]}
    monkeypatch.setattr(S.C, "_git", lambda *a, **k: raw)
    observed, filters = archive.membership_and_filters(fold="wf1", decision_ms=lo)
    assert observed == tuple(members)
    assert len(filters) == 24
    assert filters[members[0]][1] == S.Decimal("0.01")
    monkeypatch.setattr(S.C, "_git", lambda *a, **k: raw+b" ")
    with pytest.raises(S.C.IncompleteAuthority, match="bytes changed"):
        archive.membership_and_filters(fold="wf1", decision_ms=lo)
    with pytest.raises(S.C.IncompleteAuthority, match="escapes"):
        archive.membership_and_filters(fold="wf1", decision_ms=lo-S.C.HOUR_MS)
