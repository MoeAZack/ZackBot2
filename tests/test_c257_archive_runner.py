"""Synthetic checksum archives only; no real market data or identity is read."""
from __future__ import annotations

import gzip
import hashlib
import io
import json
import zipfile
from datetime import datetime, timedelta, timezone
from decimal import Decimal

import pytest
from tools.research import authority_v6 as V
from tools.research import c257_archive_loader as L
from tools.research import c257_successor_guard as G
from tools.research import funding_shock_reversal_xsec_c257_evaluator as E
from tools.research import funding_shock_reversal_xsec_c257_protected_adapter as A
from tools.research import funding_shock_reversal_xsec_c257_stagea as S
from tools.research import run_funding_shock_reversal_xsec_c257_protected as CLI


def digest(value):
    value["digest"] = V.content_digest(value)
    return value


@pytest.fixture
def synthetic_archive(tmp_path, monkeypatch):
    monkeypatch.setattr(A,"REPORT_REQUIREMENTS_BOUND",True)
    root, store = tmp_path/"repo", tmp_path/"store"
    start = S._iso_ms("2024-12-14T00:00:00Z")
    end = start+24*S.HOUR_MS
    symbols = [f"S{i:02d}USDT" for i in range(20)]
    records = []
    for symbol in symbols:
        i = symbols.index(symbol)
        for series in ("klines","markPriceKlines","fundingRate"):
            token = "fundingRate" if series == "fundingRate" else "1h"
            rel = f"um/monthly/{series}/{symbol}/"+("" if token == "fundingRate" else "1h/")+f"{symbol}-{token}-2024-12.zip"
            rows = []
            for hour in range(-40,24):
                timestamp = start+hour*S.HOUR_MS
                if series == "fundingRate":
                    rate = (Decimal("0.01")*(-1 if i < 3 else 1)) if hour == 0 and i < 6 else Decimal((hour%4)*2-3)/Decimal(100000)
                    row = f"{timestamp},1,{rate}"
                else:
                    row = f"{timestamp},100,101,99,100,100,{timestamp+S.HOUR_MS-1},1000000,100,1,1,0"
                rows.append(row)
            # A sealed-boundary row deliberately contains invalid economic data.
            # It must never reach the archive parser, even from a monthly ZIP.
            rows.append(f"{end},SEALED_INVALID_VALUES")
            buffer = io.BytesIO()
            with zipfile.ZipFile(buffer,"w") as archive:
                archive.writestr(f"{symbol}-{token}-2024-12.csv", "\n".join(rows)+"\n")
            raw = buffer.getvalue()
            path = store/rel
            path.parent.mkdir(parents=True,exist_ok=True)
            path.write_bytes(raw)
            checksum = hashlib.sha256(raw).hexdigest()
            path.with_name(path.name+".ok").write_text(checksum,encoding="ascii")
            records.append({"path":rel,"series":series,"symbol":symbol,
                "interval":None if token=="fundingRate" else "1h", "sha256":checksum,
                "bytes":len(raw),"rows":len(rows),"first_open_ms":start-40*S.HOUR_MS,
                "last_open_ms":end,"source":{"kind":"binance-vision-archive",
                    "url":"https://data.binance.vision/data/futures/"+rel,"archive_checksum":checksum}})
    manifest = digest({"format":"zb-binance-um-archive-manifest/6",
        "authority_id":"BINANCE-UM-PRECUTOFF-AUTHORITY-V6","cutoff_exclusive_ms":V.CUTOFF_MS,
        "active_base_files_v5":records,"files_v6":[]})
    weeks=[]
    cursor=datetime(2024,10,14,tzinfo=timezone.utc)
    while cursor <= datetime(2026,11,2,tzinfo=timezone.utc):
        weeks.append({"monday_ms":int(cursor.timestamp()*1000),
            "monday_utc":cursor.strftime("%Y-%m-%dT00:00:00Z"),
            "books":{"crypto":{"members":symbols}}})
        cursor+=timedelta(days=7)
    pit = digest({"format":"zb-pit-universe/2","universe_id":"PIT-TOP40-QV30D-V9",
                  "manifest_digest":manifest["digest"],"weeks":weeks})
    snapshot = {"serverTime":S._iso_ms("2024-12-13T04:00:07.467Z"),"symbols":[{
        "symbol":symbol,"status":"TRADING","contractType":"PERPETUAL","quoteAsset":"USDT",
        "underlyingType":"COIN","filters":[{"filterType":"PRICE_FILTER","tickSize":"0.1"},
          {"filterType":"MARKET_LOT_SIZE","stepSize":"0.000001","minQty":"0.000001","maxQty":"10000"},
          {"filterType":"MIN_NOTIONAL","notional":"1"}]} for symbol in symbols]}
    snapshot_raw = V.canonical(snapshot)
    snapshot_path = root/"snapshot.json"
    root.mkdir()
    snapshot_path.write_bytes(snapshot_raw)
    cells=V._weekly_membership_cells(pit,frozenset(symbols),start,end)
    filter_ = digest({"authority":"STRATEGY-CAUSAL-PACK-01-EXCHANGE-FILTER-AUTHORITY",
        "version":4,"status":"FROZEN","pit_universe_path":G.OUTPUT_PATHS["pit_v9"],
        "pit_universe_sha256":V.sha256(V.canonical(pit)),"pit_universe_digest":pit["digest"],
        "snapshots":[{"captured_at":"2024-12-13T04:44:48Z","server_time":"2024-12-13T04:00:07.467Z",
            "path":"snapshot.json","sha256":V.sha256(snapshot_raw)}],
        "fold_bindings":{"development_eligible_segment":"2024-12-13T04:44:48Z",
            "wf1":"2024-12-13T04:44:48Z","wf2":"2024-12-13T04:44:48Z",
            "wf3":"2026-02-17T04:13:28Z","sealed_holdout":"2026-02-17T04:13:28Z"},
        "weekly_membership":{"development_eligible_segment":cells},
        "weekly_membership_sha256":{"development_eligible_segment":V.sha256(V.canonical(cells))}})
    streams={name:0 for name in ("klines:1d","klines:1h","markPriceKlines:1h","fundingRate:null")}
    coverage=digest({"format":"zb-strategy-causal-pack-01-authority-coverage-preflight/1",
        "authority_version":4,"cutoff_exclusive_ms":V.CUTOFF_MS,"manifest_digest":manifest["digest"],
        "pit_universe_digest":pit["digest"],"filter_authority_digest":filter_["digest"],
        "required_streams":list(streams),"guaranteed_period_gaps_by_stream":streams,
        "guaranteed_period_gaps":0,"gaps":[],"required_result":"ZERO_GUARANTEED_PERIOD_GAPS"})
    docs={"manifest_v6":manifest,"pit_v9":pit,"filter_v4":filter_,"coverage_v4":coverage}
    pins={}
    for name,doc in docs.items():
        raw=V.canonical(doc)
        if name=="manifest_v6": raw=gzip.compress(raw,mtime=0)
        path=root/G.OUTPUT_PATHS[name]
        path.parent.mkdir(parents=True,exist_ok=True)
        path.write_bytes(raw)
        pins[name]={"path":G.OUTPUT_PATHS[name],"byte_sha256":V.sha256(raw),"content_digest":doc["digest"]}
    original_contract=S.load_frozen_contract()
    original_contract["walk_forward"]["development_filter_eligible_segment"]="2024-12-14T00:00:00Z/2024-12-15T00:00:00Z"
    monkeypatch.setattr(S,"load_frozen_contract",lambda:original_contract)
    monkeypatch.setattr(S,"frozen_splits",lambda:{"development":(start,end,False)})
    return root,store,pins,start,end


def test_checksum_archive_to_concurrent_cash_book_and_economic_report(synthetic_archive, monkeypatch):
    root,store,pins,start,end=synthetic_archive
    loader=L.ProtectedArchiveLoader(root=root,store=store,pins=pins)
    with pytest.raises(L.ArchiveLoaderError,match="sealed"):
        loader.load_fold("wf1",Decimal(1000))
    raw=loader.load_fold("development",Decimal(1000))
    derived=E.derive_evaluation(raw)
    assert len(derived.attempts)>=2
    assert all(row.entry_ms==start+S.HOUR_MS for row in derived.attempts)
    first,second=derived.attempts[:2]
    # Indexed admission 2 sees admission 1's actual entry costs immediately.
    entry_cost=sum(cash.fee+cash.slippage+cash.impact for cash in first.fills if cash.fill.phase=="entry")
    assert E._book_equity_at(Decimal(1000),(first,),raw.portfolio_marks,first.entry_ms)==Decimal(1000)-entry_cost
    assert second.initial_gross_cash < first.initial_gross_cash
    report=E.evaluate(raw)
    assert report["verdict"]=="REJECT_DEVELOPMENT"
    assert Decimal(report["profit_factor"])==0
    assert Decimal(report["marked_cashflow_drawdown_R"])>0
    assert all(row.close_ms<end for row in derived.attempts)


def test_runner_dev_veto_precedes_forward_reads_and_claim_is_single_use(synthetic_archive,monkeypatch,tmp_path,capsys):
    root,store,pins,_,_=synthetic_archive
    monkeypatch.setattr(A,"ROOT",root)
    monkeypatch.setattr(A,"authenticate_before_loader",lambda:A.Bindings("a"*40,"b"*64,{},
        {"authority_v6":{"output_pins":pins}}))
    ledger=tmp_path/"durable-identity"
    monkeypatch.setattr(A,"_identity_directory",lambda:ledger)
    opened=[]
    original=L.ProtectedArchiveLoader.load_fold
    def watch(self,fold,equity):
        opened.append(fold)
        return original(self,fold,equity)
    monkeypatch.setattr(L.ProtectedArchiveLoader,"load_fold",watch)
    assert CLI.main(["--evaluate-once","--store",str(store)])==0
    packet=json.loads(capsys.readouterr().out)
    assert packet["verdict"]=="REJECT_DEVELOPMENT" and packet["forward_opened"] is False
    assert opened==["development"]*4
    assert (ledger/"candidate-257.report.json").is_file()
    with pytest.raises(A.ProtectedAdapterError,match="already consumed"):
        A.evaluate_once(store=store)
    assert opened==["development"]*4


def test_checksum_failure_consumes_identity_without_forward_access(synthetic_archive,monkeypatch,tmp_path):
    root,store,pins,_,_=synthetic_archive
    first=next(store.rglob("*.zip"))
    first.write_bytes(first.read_bytes()+b"corrupt")
    monkeypatch.setattr(A,"ROOT",root)
    monkeypatch.setattr(A,"authenticate_before_loader",lambda:A.Bindings("a"*40,"b"*64,{},
        {"authority_v6":{"output_pins":pins}}))
    ledger=tmp_path/"durable-identity"
    monkeypatch.setattr(A,"_identity_directory",lambda:ledger)
    with pytest.raises(L.ArchiveLoaderError,match="checksum"):
        A.evaluate_once(store=store)
    assert (ledger/"candidate-257.claim.failed.json").is_file()
    with pytest.raises(A.ProtectedAdapterError,match="already consumed"):
        A.evaluate_once(store=store)
