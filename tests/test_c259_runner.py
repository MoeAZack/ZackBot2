"""Actual CLI on synthetic checksum archives and protected Git metadata only."""
from __future__ import annotations

import gzip
import hashlib
import io
import json
import math
import subprocess
import zipfile
from dataclasses import FrozenInstanceError,asdict,replace
from datetime import datetime,timezone
from pathlib import Path

import numpy as np
import pytest
from tools.research import btc_residual_leadlag_continuation_c259_stagea as E
from tools.research import c259_runner as R
from tools.research import c259_reporting as Q
from tools.research import run_btc_residual_leadlag_continuation_c259_stagea as CLI

SOURCE_ROOT=R.ROOT
FROZEN_BASE=E.load_frozen_contract()["base_commit"]


def _git(repo,*args):
    return subprocess.check_output(["git","-C",str(repo),*args],stderr=subprocess.PIPE).decode().strip()


def _document(value):
    value["digest"]=E._computed_digest(value)
    return E._canon(value)


def _shallow_ci_source(directory):
    """Only CI HEAD plus the two exact workflow-fetched fixture objects.

    No deep refs or history from the developer checkout enter the source.
    Fetched objects are not assumed to survive a clone of this shallow repo.
    """
    directory.mkdir()
    _git(directory,"init","-q")
    _git(directory,"fetch","--no-tags","--depth=1",str(SOURCE_ROOT),"HEAD")
    _git(directory,"update-ref","refs/heads/ci-head",_git(directory,"rev-parse","FETCH_HEAD"))
    _git(directory,"symbolic-ref","HEAD","refs/heads/ci-head")
    _git(directory,"fetch","--no-tags","--depth=1",str(SOURCE_ROOT),FROZEN_BASE,E.CONTRACT_HEAD)
    assert _git(directory,"rev-parse","--is-shallow-repository")=="true"
    return directory


def _initialize_fixture_clone(repo,source):
    subprocess.run(["git","clone","--shared","--no-checkout",str(source),str(repo)],
                   check=True,capture_output=True)
    # A shared clone of a shallow source need not retain unreferenced fetched
    # trees. Fetch both exact objects in the fixture itself, before checkout.
    _git(repo,"fetch","--no-tags","--depth=1",str(source),FROZEN_BASE,E.CONTRACT_HEAD)
    _git(repo,"config","core.autocrlf","false")
    sparse=[*R.BOUND_CODE,R.RECEIPT_PATH,*E.EXPECTED_PATHS.values(),
            E.CONTRACT_PATH.relative_to(E.ROOT).as_posix(),E.PLAN_PATH.relative_to(E.ROOT).as_posix(),"snapshot.json"]
    _git(repo,"config","core.sparseCheckout","true")
    (repo/".git/info/sparse-checkout").write_text("\n".join("/"+path for path in sparse)+"\n",encoding="utf-8")
    _git(repo,"checkout","--detach",E.CONTRACT_HEAD)
    # The contract's tree is immutable, but its pre-squash ancestry is not
    # present in CI. Generated authority commits descend directly from the
    # exact protected base while retaining that contract tree/index.
    _git(repo,"update-ref","--no-deref","HEAD",FROZEN_BASE)


@pytest.fixture(scope="module")
def checksum_cli(tmp_path_factory,request):
    fixture=tmp_path_factory.mktemp("c259-checksum-cli")
    repo,store=fixture/"repo",fixture/"store"
    source=_shallow_ci_source(fixture/"shallow-ci-source")
    _initialize_fixture_clone(repo,source)
    _git(repo,"config","user.name","C259 synthetic")
    _git(repo,"config","user.email","c259@example.invalid")
    for path in R.BOUND_CODE:
        target=repo/path;target.parent.mkdir(parents=True,exist_ok=True)
        target.write_bytes((R.ROOT/path).read_bytes())
    start=int(datetime(2025,1,1,tzinfo=timezone.utc).timestamp()*1000)
    end=start+739*E.HOUR_MS
    members=[f"ALT{i:02d}USDT" for i in range(24)]+["BTCUSDT"]
    rng=np.random.Generator(np.random.PCG64(25911))
    btc_returns=rng.normal(0,.0008,744)
    btc_returns[721]=.012;btc_returns[724]=-.012
    records=[]
    for index,symbol in enumerate(members):
        if symbol=="BTCUSDT": returns=btc_returns
        else:
            noise=np.random.Generator(np.random.PCG64(index+17)).normal(0,.00004,744)
            returns=.2*btc_returns+.7*np.roll(btc_returns,1)+noise
        closes=[];price=100.0
        for rate in returns:
            price*=math.exp(float(rate));closes.append(round(price,8))
        for series in ("klines","markPriceKlines","fundingRate"):
            token="fundingRate" if series=="fundingRate" else "1h"
            rel=f"um/monthly/{series}/{symbol}/"+("" if token=="fundingRate" else "1h/")+f"{symbol}-{token}-2025-01.zip"
            lines=[]
            for hour in range(744):
                if getattr(request,"param",None)=="exposed_gap" and series=="klines" and symbol!="BTCUSDT" and hour==724:
                    continue
                timestamp=start+hour*E.HOUR_MS
                if series=="fundingRate":
                    if hour%8:continue
                    lines.append(f"{timestamp},8,{'0.0001' if hour%16 else '-0.0001'}")
                else:
                    open_=100.0 if hour==0 else closes[hour-1]
                    high=round(max(open_,closes[hour])*1.002,8)
                    low=round(min(open_,closes[hour])*.998,8)
                    # Poisoned future ranges inside the same calendar archive
                    # may be checksum-hashed but must never be interpreted.
                    close=str(closes[hour]) if hour<739 else "FORBIDDEN_FORWARD_VALUE"
                    lines.append(f"{timestamp},{open_},{high},{low},{close},1,{timestamp+E.HOUR_MS-1},100000000,1,1,1,0")
            output=io.BytesIO()
            with zipfile.ZipFile(output,"w",compression=zipfile.ZIP_DEFLATED) as archive:
                archive.writestr(f"{symbol}-{token}-2025-01.csv","\n".join(lines)+"\n")
            raw=output.getvalue();sha=hashlib.sha256(raw).hexdigest()
            path=store/rel;path.parent.mkdir(parents=True,exist_ok=True);path.write_bytes(raw)
            path.with_name(path.name+".ok").write_text(sha,encoding="ascii")
            path.with_name(path.name+".CHECKSUM").write_text(f"{sha}  {path.name}\n",encoding="ascii")
            records.append({"path":rel,"sha256":sha,"bytes":len(raw),"rows":len(lines),
                "first_open_ms":start,"last_open_ms":start+(736 if series=="fundingRate" else 743)*E.HOUR_MS,
                "source":{"kind":"binance-vision-archive","url":"https://data.binance.vision/data/futures/"+rel,"archive_checksum":sha}})
    manifest={"format":"zb-binance-um-archive-manifest/6","authority_id":E.AUTHORITY_ID,
        "cutoff_exclusive_ms":1793836800000,"active_base_files_v5":records,"files_v6":[]}
    manifest_raw=gzip.compress(_document(manifest),mtime=0)
    weeks=[]
    for monday in range(1580688000000,1793577600000+1,7*24*E.HOUR_MS):
        weeks.append({"monday_ms":monday,"monday_utc":datetime.fromtimestamp(monday/1000,tz=timezone.utc).strftime("%Y-%m-%dT00:00:00Z"),
            "books":{"crypto":{"eligible":len(members),"members":members,"qv30d_usdt":["1000000"]*len(members)}}})
    pit={"format":"zb-pit-universe/2","universe_id":"PIT-TOP40-QV30D-V9","manifest_digest":manifest["digest"],"weeks":weeks}
    pit_raw=_document(pit)
    snapshot={"serverTime":R._iso("2024-12-13T04:00:07.467Z"),"symbols":[{
        "symbol":symbol,"status":"TRADING","contractType":"PERPETUAL","quoteAsset":"USDT","underlyingType":"COIN",
        "filters":[{"filterType":"PRICE_FILTER","tickSize":"0.00000001"},
            {"filterType":"MARKET_LOT_SIZE","stepSize":"0.01","minQty":"0.01","maxQty":"100000"},
            {"filterType":"MIN_NOTIONAL","notional":"1"}]} for symbol in members]}
    snapshot_raw=E._canon(snapshot);(repo/"snapshot.json").write_bytes(snapshot_raw)
    active=max((week for week in weeks if week["monday_ms"]<=start),key=lambda week:week["monday_ms"])
    cells=[{"monday_utc":week["monday_utc"],"members":members[:20]} for week in
        [active]+[week for week in weeks if start<week["monday_ms"]<end]]
    fold_names=("development_eligible_segment","wf1","wf2","wf3","sealed_holdout")
    filters={"authority":"STRATEGY-CAUSAL-PACK-01-EXCHANGE-FILTER-AUTHORITY","version":4,"status":"FROZEN",
        "pit_universe_sha256":E._sha(pit_raw),"pit_universe_digest":pit["digest"],
        "fold_bindings":{key:"2026-02-17T04:13:28Z" if key in ("wf3","sealed_holdout") else "2024-12-13T04:44:48Z" for key in fold_names},
        "snapshots":[{"captured_at":"2024-12-13T04:44:48Z","server_time":"2024-12-13T04:00:07.467Z",
            "path":"snapshot.json","sha256":E._sha(snapshot_raw)}],
        "weekly_membership":{key:cells for key in fold_names},
        "weekly_membership_sha256":{key:E._sha(E._canon(cells)) for key in fold_names}}
    filter_raw=_document(filters)
    streams={stream:0 for stream in ("klines:1d","klines:1h","markPriceKlines:1h","fundingRate:null")}
    coverage={"format":"zb-strategy-causal-pack-01-authority-coverage-preflight/1","authority_version":4,
        "required_result":"ZERO_GUARANTEED_PERIOD_GAPS","cutoff_exclusive_ms":1793836800000,
        "manifest_digest":manifest["digest"],"pit_universe_digest":pit["digest"],"filter_authority_digest":filters["digest"],
        "required_streams":list(streams),"guaranteed_period_gaps_by_stream":streams,"guaranteed_period_gaps":0,"gaps":[]}
    raws={"manifest_v6":manifest_raw,"pit_v9":pit_raw,"filter_v4":filter_raw,"coverage_v4":_document(coverage)}
    docs={"manifest_v6":manifest,"pit_v9":pit,"filter_v4":filters,"coverage_v4":coverage}
    for name,raw in raws.items():
        path=repo/E.EXPECTED_PATHS[name];path.parent.mkdir(parents=True,exist_ok=True);path.write_bytes(raw)
    _git(repo,"add","--",*R.BOUND_CODE,*E.EXPECTED_PATHS.values(),"snapshot.json")
    _git(repo,"commit","-m","synthetic checksum authority and exact runner bytes")
    authority_head=_git(repo,"rev-parse","HEAD")
    pins=E.AuthorityPins(authority_head,**{name:E.ArtifactPin(E.EXPECTED_PATHS[name],E._sha(raw),docs[name]["digest"]) for name,raw in raws.items()})
    receipt={"format":"zb-c259-protected-authority-amendment/1","candidate_id":259,"contract_sha256":E.CONTRACT_SHA256,
        "status":"FROZEN_AUTHORITY_PINNED_NOT_EVALUATED","default_off":True,"authority_pins":asdict(pins)}
    path=repo/R.RECEIPT_PATH;path.parent.mkdir(parents=True,exist_ok=True);path.write_bytes(E._canon(receipt))
    _git(repo,"add","--",R.RECEIPT_PATH);_git(repo,"commit","-m","synthetic protected pin amendment")
    _git(repo,"update-ref","refs/remotes/origin/master",_git(repo,"rev-parse","HEAD"))
    patch=pytest.MonkeyPatch()
    patch.setattr(R,"ROOT",repo);patch.setattr(E,"ROOT",repo)
    patch.setattr(E,"CONTRACT_PATH",repo/"research_evidence/prereg/btc_residual_leadlag_continuation_c259_stagea.v1.json")
    patch.setattr(E,"PLAN_PATH",repo/"contracts/strategy_causal_pack_01/authority_extension_plan.v6.json")
    ranges={**E.ALL_SPLIT_RANGES,"development":(start,end)}
    patch.setattr(E,"ALL_SPLIT_RANGES",ranges)
    patch.setattr(E,"FOLD_RANGES",{key:value for key,value in ranges.items() if key!="sealed_holdout"})
    yield repo,store,start,end
    patch.undo()


def test_fixture_fetches_exact_trees_and_binds_base_without_incidental_deep_refs(tmp_path):
    source=_shallow_ci_source(tmp_path/"source")
    assert _git(source,"for-each-ref","--format=%(refname)")=="refs/heads/ci-head"
    _git(source,"cat-file","-e",f"{E.CONTRACT_HEAD}^{{commit}}")
    _git(source,"cat-file","-e",f"{FROZEN_BASE}^{{commit}}")
    # Knowing both objects is insufficient to prove an ancestry path through
    # missing shallow parents. This is the actual CI graph, not a deep clone.
    lineage=subprocess.run(["git","-C",str(source),"merge-base","--is-ancestor",FROZEN_BASE,"HEAD"],capture_output=True)
    assert lineage.returncode==1
    repo=tmp_path/"fixture"
    _initialize_fixture_clone(repo,source)
    assert _git(repo,"rev-parse","--is-shallow-repository")=="true"
    assert _git(repo,"rev-parse","HEAD")==FROZEN_BASE
    contract=E.CONTRACT_PATH.relative_to(E.ROOT).as_posix()
    assert E._sha((repo/contract).read_bytes())==E.CONTRACT_SHA256
    assert _git(repo,"rev-parse",f":{contract}")==_git(repo,"rev-parse",f"{E.CONTRACT_HEAD}:{contract}")
    _git(repo,"config","user.name","C259 shallow regression")
    _git(repo,"config","user.email","c259-shallow@example.invalid")
    _git(repo,"commit","--allow-empty","-m","synthetic contract tree on exact protected base")
    assert _git(repo,"rev-parse","HEAD^")==FROZEN_BASE
    _git(repo,"merge-base","--is-ancestor",FROZEN_BASE,"HEAD")


def test_cli_checksum_archives_to_isolated_primary_and_diagnostic_reports(checksum_cli,monkeypatch,capsys):
    repo,store,start,end=checksum_cli
    opened=[]
    original=E.ProtectedSourceArchive.read_fold
    def watch(self,symbol,fold,**kwargs):
        opened.append(fold)
        return original(self,symbol,fold,**kwargs)
    monkeypatch.setattr(E.ProtectedSourceArchive,"read_fold",watch)
    assert CLI.main(["--evaluate","--store",str(store)])==0
    packet=json.loads(capsys.readouterr().out)
    assert opened and set(opened)=={"development"}
    assert packet["sealed_holdout_opened"] is False
    for side in E.SIDES.values():
        result=packet["result"]["sides"][side]
        assert result["verdict"]=="REJECT_DEVELOPMENT" and result["forward_opened"] is False
        report=result["primary_development"]
        assert report["capital_usd"]==2000 and report["scheduled"]==739
        assert report["closed"]>=1 and report["failed"]==0
        assert report["fee_drag"]>0 and report["slippage_drag"]>0 and report["impact_drag"]>0
        assert set(result["capital_diagnostics"])=={"1000","3000","4000"}
        assert all(row["capital_usd"]!=2000 and not row["gate"].get("applicable",True)
                   for row in result["capital_diagnostics"].values())
        assert report["book_benchmark_hourly_observations"]==738
        assert report["signal_distributions"]["btc_impulse"]["n"]>0
        assert report["signal_distributions"]["decision_liquidity_ratio"]["min"]==1
        assert report["entry_participation_distribution"]["n"]==report["closed"]
        assert isinstance(report["cashflow_and_trade_ledger"][0]["sizing"]["quantity"],str)
        assert all(item["cashflow_net"]==item["actual_net_cash"] for item in report["cashflow_and_trade_ledger"])
    before=len(opened)
    with pytest.raises(E.EvaluationLocked,match="already consumed"):
        R.evaluate_once(store=store)
    assert len(opened)==before


def test_readonly_source_rejects_holdout_and_unbound_loader(checksum_cli):
    _,store,_,_=checksum_cli
    run=R.authenticate()
    source=R.FixedFoldSource(run,store)
    stream=source.streams("BTCUSDT","development")
    assert isinstance(stream.bars,tuple)
    assert source.members("development",E.FOLD_RANGES["development"][0]+722*E.HOUR_MS)==tuple(
        [f"ALT{i:02d}USDT" for i in range(24)]+["BTCUSDT"])
    with pytest.raises(FrozenInstanceError):stream.symbol="FAKE"
    with pytest.raises(TypeError):source.filters("development")["BTCUSDT"]=E.Filters(*([E.Decimal(1)]*5))
    with pytest.raises(E.EvaluationLocked,match="holdout"):
        source.streams("BTCUSDT","sealed_holdout")


@pytest.mark.parametrize("attack",["receipt","implementation","dependency","numpy_version"])
def test_wrong_protected_receipt_or_runtime_dependency_blocks_before_identity(checksum_cli,monkeypatch,attack):
    repo,store,_,_=checksum_cli
    if attack=="receipt":
        original=E._tree_bytes
        def tree(root,head,path):
            raw=original(root,head,path)
            if path==R.RECEIPT_PATH:
                document=json.loads(raw);document["contract_sha256"]="0"*64
                return E._canon(document)
            return raw
        monkeypatch.setattr(E,"_tree_bytes",tree)
    elif attack=="numpy_version":monkeypatch.setattr(E.np,"__version__","0.0.0")
    else:
        original=E._git
        target="tools/research/c259_runner.py" if attack=="implementation" else "tools/research/btc_residual_leadlag_continuation_c259_stagea.py"
        def git(root,*args,**kwargs):
            if args==("hash-object","--",target):return "0"*40
            return original(root,*args,**kwargs)
        monkeypatch.setattr(E,"_git",git)
    monkeypatch.setattr(R,"_identity_directory",lambda:(_ for _ in ()).throw(AssertionError("identity reached")))
    with pytest.raises(E.C259Error):R.evaluate_once(store=store)


def test_ancestor_output_head_without_exact_current_receipt_is_rejected(checksum_cli,monkeypatch):
    repo,_,_,_=checksum_cli
    run=R.authenticate()
    assert run.pins.protected_head!=run.head
    original=E._tree_bytes
    def tree(root,head,path):
        raw=original(root,head,path)
        if path==R.RECEIPT_PATH:
            value=json.loads(raw);value["authority_pins"]["manifest_v6"]["byte_sha256"]="0"*64
            return E._canon(value)
        return raw
    monkeypatch.setattr(E,"_tree_bytes",tree)
    with pytest.raises(E.IncompleteAuthority,match="exact current protected mechanical receipt"):
        E.validate_authority_pins(run.pins,repo=repo)


def test_side_and_capital_books_do_not_change_each_others_admissions(checksum_cli):
    _,store,_,_=checksum_cli
    source=R.FixedFoldSource(R.authenticate(),store)
    original=R.replay_fold(source,"development",1,2000)
    R.replay_fold(source,"development",-1,4000)
    diagnostic=R.replay_fold(source,"development",1,1000)
    repeated=R.replay_fold(source,"development",1,2000)
    def identity(replay):
        return [(trade.attempt_id,trade.fills,trade.sizing.side_book_equity,trade.sizing.quantity) for trade in replay.fold.base]
    assert identity(original)==identity(repeated)
    assert original.fold.base and diagnostic.fold.base
    assert original.fold.base[0].sizing.side_book_equity==2000
    assert diagnostic.fold.base[0].sizing.side_book_equity==1000


def test_marked_funding_phase_retains_open_unrealized_exposure(checksum_cli):
    _,store,_,_=checksum_cli
    source=R.FixedFoldSource(R.authenticate(),store)
    replay=R.replay_fold(source,"development",-1,2000)
    points=E.derive_equity_points(replay.fold.base,replay.fold.marks,
        start_ms=E.FOLD_RANGES["development"][0],end_ms=E.FOLD_RANGES["development"][1])
    index={(mark.timestamp_ms,mark.symbol):mark for mark in replay.fold.marks}
    found=False
    for timestamp,phase,value in points:
        if phase!=1:continue
        deltas=[];unrealized=[]
        for trade in replay.fold.base:
            final_phase=0 if trade.fills[-1].phase in {"STOP_RANGE","TARGET_RANGE"} else 2
            for fill in trade.fills:
                fill_phase=4 if fill.phase=="ENTRY" else final_phase
                if fill.timestamp_ms<timestamp or fill.timestamp_ms==timestamp and fill_phase<=phase:
                    deltas.extend((fill.movement_cash,-fill.fee_drag,-fill.slippage_drag,-fill.impact_drag))
            deltas.extend(E.funding_cash(trade.side,trade.fills[0].quantity,item.mark.price,item.event.rate)
                          for item in trade.funding_events if item.event.timestamp_ms<=timestamp)
            if trade.entry_ms<timestamp and (trade.close_ms>timestamp or trade.close_ms==timestamp and final_phase>phase):
                unrealized.append(trade.side*trade.fills[0].quantity*(index[(timestamp-E.HOUR_MS,trade.symbol)].price-trade.fills[0].reference))
        if abs(math.fsum(unrealized))>1e-8:
            assert value==pytest.approx(math.fsum((2000,*deltas,*unrealized)),abs=1e-10)
            found=True
    assert found


def test_ranked_same_side_infeasible_candidate_advances_without_cross_side_substitution(checksum_cli,monkeypatch):
    _,store,_,_=checksum_cli
    source=R.FixedFoldSource(R.authenticate(),store)
    original=R.replay_fold(source,"development",1,2000)
    selected=next(row for row in original.decisions if len(row["admitted"])>=2)
    top=selected["ranked_same_side"][0]
    raw_filters=source.filters("development")
    source._filters["development"]={**raw_filters,top:replace(raw_filters[top],tick=E.Decimal("1000"))}
    replay=R.replay_fold(source,"development",1,2000)
    decision=next(row for row in replay.decisions if row["decision_ms"]==selected["decision_ms"])
    assert any(skip["symbol"]==top and skip["reason"]=="ENTRY_PRICE_OFF_TICK" for skip in decision["skipped"])
    accepted=[trade.symbol for trade in replay.fold.base if trade.decision_close_ms==selected["decision_ms"]]
    assert top not in accepted and len(accepted)>=2
    assert all(symbol in selected["ranked_same_side"] for symbol in accepted)
    assert any(symbol in selected["ranked_same_side"][3:] for symbol in accepted)


@pytest.mark.parametrize("checksum_cli",["exposed_gap"],indirect=True)
def test_real_checksum_gap_after_acceptance_retains_exposure_and_blocks_forward(checksum_cli,capsys):
    _,store,_,_=checksum_cli
    assert CLI.main(["--evaluate","--store",str(store)])==0
    packet=json.loads(capsys.readouterr().out)
    result=packet["result"]["sides"][E.SIDES[1]]
    assert result["verdict"]=="DATA_INCOMPLETE_DEVELOPMENT"
    assert not result["forward_opened"]
    report=result["primary_development"]
    assert report["partial_exposed_episodes_retained"]
    assert not report["all_accepted_exposure_economics_defined"]
    failure=next(row for row in report["authority_failures"] if row.get("accepted_quantity"))
    assert failure["known_entry_cost_cash"]<0 and failure["trade_R"] is None
    assert failure["frozen_stop"] and failure["known_exposure_marks"]
    assert report["known_exposure_prefix"]["marked_drawdown_R_lower_bound"]>0
    assert report["known_stress_exposure_prefix"]["marked_drawdown_R_lower_bound"]>report["known_exposure_prefix"]["marked_drawdown_R_lower_bound"]
    assert report["equity_points"] is None and report["all_accepted_exposure_gate_drawdown_R"] is None


def test_pin_failure_precedes_archive_or_identity_access(monkeypatch,tmp_path):
    monkeypatch.setattr(R,"authenticate",lambda:(_ for _ in ()).throw(E.EvaluationLocked("absent pins")))
    monkeypatch.setattr(R,"_identity_directory",lambda:(_ for _ in ()).throw(AssertionError("identity reached")))
    with pytest.raises(E.EvaluationLocked,match="absent pins"):R.evaluate_once(store=tmp_path)
    assert list(tmp_path.iterdir())==[]


def test_split_table_matches_all_frozen_contract_intervals():
    contract=E.load_frozen_contract()
    expected={"development":contract["splits"]["development"],
              **{f"wf{i}":span for i,span in enumerate(contract["splits"]["forward_folds"],1)},
              "sealed_holdout":contract["splits"]["sealed_holdout"]}
    original={"development":(1728864000000,1743465600000),"wf1":(1743465600000,1759276800000),
        "wf2":(1759276800000,1775001600000),"wf3":(1775001600000,1790553600000),"sealed_holdout":(1790553600000,1793836800000)}
    for name,span in expected.items():assert original[name]==tuple(R._iso(value) for value in span.split("/"))
