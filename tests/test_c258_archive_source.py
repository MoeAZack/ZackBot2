from pathlib import Path
from types import SimpleNamespace
import hashlib
import json
import io
import zipfile

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


def test_checksum_archive_clips_sealed_values_before_schema_parse(tmp_path):
    lo = S._ms("2026-09-27T23:00:00Z")
    hi = lo+S.C.HOUR_MS
    rel = "um/monthly/klines/BTCUSDT/1h/BTCUSDT-1h-2026-09.zip"
    row = f"{lo},1,2,0.5,1,1,{hi-1},1,1,1,1,0\n"
    # Deliberately unparseable economic field after the sealed boundary proves
    # only its timestamp is examined; it cannot affect returned economic rows.
    sealed = f"{hi},1,2,0.5,1,1,{hi+S.C.HOUR_MS-1},SEALED,1,1,1,0\n"
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as archive:
        archive.writestr("BTCUSDT-1h-2026-09.csv", row+sealed)
    raw = buffer.getvalue()
    target = tmp_path / rel
    target.parent.mkdir(parents=True)
    target.write_bytes(raw)
    digest = hashlib.sha256(raw).hexdigest()
    Path(str(target)+".ok").write_text(digest, encoding="ascii")
    record = {"path": rel, "sha256": digest, "source": {"kind": "binance-vision-archive"}}
    _, rows = S._read_window_rows(tmp_path, record, new=False, start_ms=lo, end_ms=hi)
    assert len(rows) == 1 and int(rows[0][0]) == lo
    target.write_bytes(raw+b"changed")
    with pytest.raises(Exception, match="SHA-256"):
        S._read_window_rows(tmp_path, record, new=False, start_ms=lo, end_ms=hi)


def test_typed_source_preserves_mark_close_and_declared_funding_cadence(monkeypatch):
    archive = source(monkeypatch, [])
    lo, _ = S.frozen_window("wf1")
    kline = (lo, "10", "11", "9", "10.5", "1", lo+S.C.HOUR_MS-1, "1000", 1, "1", "1", "0")
    tapes = {"klines": (kline,), "markPriceKlines": (kline,),
             "fundingRate": ((lo, "4", "0.001"), (lo+4*S.C.HOUR_MS, "-0.002"))}
    def load(**kwargs):
        return S.SourceRows("BTCUSDT", kwargs["stream"], "wf1", lo,
                            lo+8*S.C.HOUR_MS, tapes[kwargs["stream"]], (("bound", "a"*64),))
    monkeypatch.setattr(archive, "load", load)
    kwargs = dict(symbol="BTCUSDT", fold="wf1", start_ms=lo, end_ms=lo+8*S.C.HOUR_MS)
    assert archive.candles(**kwargs)[0].quote_volume == 1000
    assert archive.marks(**kwargs)[0].close_ms == lo+S.C.HOUR_MS-1
    assert archive.funding(**kwargs) == (
        S.FundingObservation(lo, .001, 4), S.FundingObservation(lo+4*S.C.HOUR_MS, -.002, None))
    tapes["fundingRate"] = ((lo, "0", "0.001"),)
    with pytest.raises(S.C.IncompleteAuthority, match="interval"):
        archive.funding(**kwargs)
    tapes["markPriceKlines"] = ((lo, "10", "11", "9", "nan", "1", lo+S.C.HOUR_MS-1, "0", 1, "1", "1", "0"),)
    with pytest.raises(S.C.IncompleteAuthority, match="mark price"):
        archive.marks(**kwargs)


def test_real_v5_path_supplies_omitted_stream_symbol_interval(monkeypatch):
    lo, _ = S.frozen_window("wf1")
    path = "um/monthly/klines/BTCUSDT/1h/BTCUSDT-1h-2025-04.zip"
    archive = source(monkeypatch, [{"path": path, "sha256": "a"*64,
        "first_open_ms": lo, "last_open_ms": lo}])
    def read(store, record, **kwargs):
        assert (record["symbol"], record["series"], record["interval"]) == (
            "BTCUSDT", "klines", "1h")
        return {}, [(lo, "1")]
    monkeypatch.setattr(S, "_read_window_rows", read)
    assert archive.load(symbol="BTCUSDT", stream="klines", fold="wf1",
                        start_ms=lo, end_ms=lo+S.C.HOUR_MS).rows == ((lo, "1"),)


def test_variable_exhaustive_funding_uses_actual_mark_close_and_leg_exit():
    C = S.C
    points = [C.FundingPoint("L", C.HOUR_MS, .001),
              C.FundingPoint("S", C.HOUR_MS, -.001),
              C.FundingPoint("S", 4*C.HOUR_MS, .002)]
    marks = {symbol: [C.MarkClose(t*C.HOUR_MS-1, 100) for t in (1, 4)]
             for symbol in ("L", "S")}
    kwargs = dict(funding_points=points, funding_authority=points, symbol_marks=marks,
        position_sides={"L": 1, "S": -1}, quantities={"L": S.Decimal(1), "S": S.Decimal(1)},
        entry_ms=C.HOUR_MS, close_ms=6*C.HOUR_MS, stress=False,
        exhaustive_source_tape=True, actual_mark_closes=True,
        leg_exit_times={"L": 2*C.HOUR_MS, "S": 6*C.HOUR_MS})
    events = C.derive_funding_events(**kwargs)
    assert [(event.symbol, event.timestamp_ms) for event in events] == [
        ("L", C.HOUR_MS), ("S", C.HOUR_MS), ("S", 4*C.HOUR_MS)]
    with pytest.raises(C.IncompleteEpisode, match="exactly cover"):
        C.derive_funding_events(**{**kwargs, "funding_points": points[:-1]})
    with pytest.raises(C.IncompleteAuthority, match="stale"):
        C.derive_funding_events(**{**kwargs, "symbol_marks": {
            **marks, "L": [C.MarkClose(0, 100)]}})


def test_api_clips_sealed_rate_before_interpreting_economic_value(tmp_path):
    from tools.research import manifest as M
    lo = S._ms("2026-09-27T23:00:00Z")
    hi = lo+S.C.HOUR_MS
    relative = "um/api/fundingRate/BTCUSDT/BTCUSDT-fundingRate-2026-09-27-2026-09-28-p000.json"
    raw = json.dumps([{"fundingTime": lo, "fundingRate": ".001"},
                      {"fundingTime": hi, "fundingRate": "SEALED_RATE_MUST_NOT_PARSE"}]).encode()
    target = tmp_path/relative
    target.parent.mkdir(parents=True)
    target.write_bytes(raw)
    record = {"path": relative, "sha256": hashlib.sha256(raw).hexdigest(),
              "source": {"kind": M.FUNDING_API_KIND}}
    _, rows = S._read_window_rows(tmp_path, record, new=False, start_ms=lo, end_ms=hi)
    assert len(rows) == 1 and float(rows[0]["fundingRate"]) == .001


def test_actual_boundary_zip_opens_only_time_and_open_not_sealed_range(tmp_path, monkeypatch):
    _, boundary = S.frozen_window("wf3")
    relative = "um/monthly/klines/BTCUSDT/1h/BTCUSDT-1h-2026-09.zip"
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as archive:
        archive.writestr("boundary.csv", f"{boundary},100,SEALED_HIGH,SEALED_LOW,SEALED_CLOSE,SEALED_VOLUME\n"
                         f"{boundary+S.C.HOUR_MS},SEALED_OPEN,SEALED_HIGH\n")
    raw = buffer.getvalue()
    target = tmp_path/relative
    target.parent.mkdir(parents=True)
    target.write_bytes(raw)
    archive = source(monkeypatch, [{"path": relative, "sha256": hashlib.sha256(raw).hexdigest()}])
    archive._store = tmp_path
    opened = archive.boundary_open(symbol="BTCUSDT", fold="wf3")
    assert opened == S.C.OpenBar(boundary, 100)
    assert not hasattr(opened, "high") and not hasattr(opened, "quote_volume")
