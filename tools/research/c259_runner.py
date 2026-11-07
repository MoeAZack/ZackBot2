"""Fixed local C259 archive -> isolated causal books -> evidence runner.

No module import, contract validation, or absent-pin CLI attempt opens market
rows or an evaluation identity. There is no caller-supplied loader/metric seam.
"""
from __future__ import annotations

import hashlib
import json
import math
import os
import statistics
from collections import Counter
from dataclasses import asdict, dataclass, replace
from datetime import datetime, timezone
from decimal import Decimal
from pathlib import Path
from typing import Mapping
from types import MappingProxyType

from tools.research import btc_residual_leadlag_continuation_c259_stagea as E

ROOT=Path(__file__).resolve().parents[2]
RECEIPT_PATH="contracts/strategy_causal_pack_01/c259_authority_pins.v1.json"
BOUND_CODE=("tools/research/c259_runner.py","tools/research/c259_reporting.py",
    "tools/research/btc_residual_leadlag_continuation_c259_stagea.py",
    "tools/research/run_btc_residual_leadlag_continuation_c259_stagea.py")


def _strict(raw: bytes, label: str) -> dict:
    def pairs(rows):
        result={}
        for key,value in rows:
            if key in result: raise E.IncompleteAuthority(f"{label}: duplicate JSON key")
            result[key]=value
        return result
    try: value=json.loads(raw,object_pairs_hook=pairs,parse_constant=lambda _:(_ for _ in ()).throw(ValueError()))
    except (ValueError,UnicodeError) as exc: raise E.IncompleteAuthority(f"{label}: invalid JSON") from exc
    if not isinstance(value,dict): raise E.IncompleteAuthority(f"{label}: object required")
    return value


@dataclass(frozen=True)
class ProtectedRun:
    head: str
    pins: E.AuthorityPins
    authority: E.ProtectedAuthority
    code_blobs: Mapping[str,str]


def authenticate() -> ProtectedRun:
    E.load_frozen_contract()
    head=str(E._git(ROOT,"rev-parse","HEAD"))
    protected=str(E._git(ROOT,"rev-parse","--verify","refs/remotes/origin/master^{commit}"))
    if head!=protected or E._git(ROOT,"status","--porcelain"):
        raise E.EvaluationLocked("C259 requires clean HEAD equal to the protected ref")
    try: raw=E._tree_bytes(ROOT,head,RECEIPT_PATH)
    except E.IncompleteAuthority as exc:
        raise E.EvaluationLocked("C259_REAL_EVALUATION_LOCKED: protected authority pin amendment is absent; no source or identity opened") from exc
    receipt=_strict(raw,"C259 authority amendment")
    if (set(receipt)!={"format","candidate_id","contract_sha256","status","default_off","authority_pins"}
            or receipt["format"]!="zb-c259-protected-authority-amendment/1"
            or receipt["candidate_id"]!=259 or receipt["contract_sha256"]!=E.CONTRACT_SHA256
            or receipt["status"]!="FROZEN_AUTHORITY_PINNED_NOT_EVALUATED" or receipt["default_off"] is not True):
        raise E.IncompleteAuthority("C259 authority amendment changes frozen semantics")
    value=receipt["authority_pins"]
    if not isinstance(value,dict) or set(value)!={"protected_head",*E.EXPECTED_PATHS}:
        raise E.IncompleteAuthority("C259 exact authority pin set is required")
    try:
        pins=E.AuthorityPins(value["protected_head"],**{key:E.ArtifactPin(**value[key]) for key in E.EXPECTED_PATHS})
    except (TypeError,KeyError) as exc: raise E.IncompleteAuthority("malformed C259 authority pins") from exc
    blobs={}
    for path in (*BOUND_CODE,RECEIPT_PATH):
        oid=str(E._git(ROOT,"rev-parse",f"{head}:{path}"))
        if E._git(ROOT,"hash-object","--",path)!=oid:
            raise E.IncompleteAuthority("protected implementation bytes differ from working tree")
        blobs[path]=oid
        if path in BOUND_CODE:
            imported=Path(__file__).resolve().parents[2]/path
            if imported.read_bytes()!=E._tree_bytes(ROOT,head,path):
                raise E.IncompleteAuthority("imported runtime dependency differs from protected implementation")
    if E.np.__version__!="2.5.3":
        raise E.IncompleteAuthority("frozen C259 bootstrap requires numpy 2.5.3")
    authority=E.validate_authority_pins(pins,repo=ROOT)
    return ProtectedRun(head,pins,authority,blobs)


def _iso(value: str) -> int:
    stamp=datetime.fromisoformat(value.replace("Z","+00:00"))
    if stamp.tzinfo is None: raise E.IncompleteAuthority("filter timestamp lacks timezone")
    return int(stamp.timestamp()*1000)


class FixedFoldSource:
    """Read-only typed source, frozen PIT membership and exact historical filters."""
    def __init__(self, run: ProtectedRun, store: Path):
        self.run=run
        self.archive=run.authority.open_source(store)
        self._streams={}
        self._filters={}

    def streams(self,symbol: str,fold: str,*,boundary_open: bool=False) -> E.ReadOnlySymbolStreams:
        if fold not in E.FOLD_RANGES: raise E.EvaluationLocked("sealed holdout source is unavailable")
        key=symbol,fold,boundary_open
        if key not in self._streams:
            self._streams[key]=self.archive.read_fold(symbol,fold,boundary_open=boundary_open)
        return self._streams[key]

    def filters(self,fold: str) -> Mapping[str,E.Filters]:
        if fold in self._filters: return self._filters[fold]
        document=self.run.authority["filter_v4"]
        key="development_eligible_segment" if fold=="development" else fold
        capture=document["fold_bindings"][key]
        effective=max(E.FOLD_RANGES[fold][0],1734134400000) if fold=="development" else E.FOLD_RANGES[fold][0]
        snapshots=[row for row in document["snapshots"]
                   if _iso(row["captured_at"])<effective and _iso(row["server_time"])<effective]
        if not snapshots: raise E.IncompleteAuthority("no strictly prior filter snapshot")
        selected=max(snapshots,key=lambda row:(_iso(row["captured_at"]),_iso(row["server_time"])))
        if selected["captured_at"]!=capture: raise E.IncompleteAuthority("filter binding is not the latest prior snapshot")
        raw=E._tree_bytes(ROOT,self.run.pins.protected_head,selected["path"])
        if E._sha(raw)!=selected["sha256"]: raise E.IncompleteAuthority("filter snapshot byte mismatch")
        payload=_strict(raw,"historical filter payload")
        if payload.get("serverTime")!=_iso(selected["server_time"]):
            raise E.IncompleteAuthority("filter payload server time mismatch")
        filters={}
        for symbol in payload["symbols"]:
            if (symbol.get("status"),symbol.get("contractType"),symbol.get("quoteAsset"),symbol.get("underlyingType"))!=(
                    "TRADING","PERPETUAL","USDT","COIN"): continue
            name=symbol["symbol"]
            rows=symbol["filters"]
            indexed={row["filterType"]:row for row in rows}
            if len(indexed)!=len(rows) or name in filters:
                raise E.IncompleteAuthority("duplicate historical filter authority")
            try:
                lot=indexed["MARKET_LOT_SIZE"]; minimum=indexed["MIN_NOTIONAL"]
                value=E.Filters(Decimal(indexed["PRICE_FILTER"]["tickSize"]),Decimal(lot["stepSize"]),
                    Decimal(lot["minQty"]),Decimal(lot["maxQty"]),Decimal(minimum.get("notional",minimum.get("minNotional"))))
            except (KeyError,TypeError,ValueError): continue
            if any(not item.is_finite() or item<=0 for item in asdict(value).values()): continue
            if value.min_qty>value.max_qty: raise E.IncompleteAuthority("historical quantity bounds inverted")
            filters[name]=value
        self._verify_memberships(fold,filters)
        self._filters[fold]=MappingProxyType(filters)
        return self._filters[fold]

    def _verify_memberships(self,fold: str,filters) -> None:
        document=self.run.authority["filter_v4"]
        key="development_eligible_segment" if fold=="development" else fold
        start,end=E.FOLD_RANGES[fold]
        if fold=="development": start=max(start,1734134400000)
        weeks=self.run.authority["pit_v9"]["weeks"]
        active=max((week for week in weeks if week["monday_ms"]<=start),key=lambda week:week["monday_ms"])
        chosen=[active]+[week for week in weeks if start<week["monday_ms"]<end]
        cells=[]
        for week in chosen:
            members=[symbol for symbol in week["books"]["crypto"]["members"] if symbol in filters][:20]
            if len(members)!=20: raise E.IncompleteAuthority("historical CORE-20 cell incomplete")
            cells.append({"monday_utc":week["monday_utc"],"members":members})
        if (cells!=document["weekly_membership"][key]
                or E._sha(E._canon(cells))!=document["weekly_membership_sha256"][key]):
            raise E.IncompleteAuthority("derived weekly membership differs from protected filter authority")

    def members(self,fold: str,decision_ms: int) -> tuple[str,...]:
        filters=self.filters(fold)
        week=max((week for week in self.run.authority["pit_v9"]["weeks"] if week["monday_ms"]<=decision_ms),
                 key=lambda week:week["monday_ms"])
        return tuple(symbol for symbol in week["books"]["crypto"]["members"] if symbol in filters)


@dataclass(frozen=True)
class Replay:
    fold: E.FoldLifecycle
    side: int
    capital: int
    decisions: tuple[dict,...]
    fitted: tuple[dict,...]
    failures: tuple[dict,...]
    benchmark: tuple[E.Bar,...]


def _stream_maps(stream: E.ReadOnlySymbolStreams):
    return ({bar.open_ms:bar for bar in stream.bars},
            {mark.timestamp_ms:mark for mark in stream.marks})


def _equity_at(source: FixedFoldSource,name: str,capital: int,trades,entry_ms: int) -> Decimal:
    cashflows=[]
    unrealized=[]
    for trade in trades:
        for fill in trade.fills:
            if fill.timestamp_ms<=entry_ms:
                cashflows.extend((fill.movement_cash,-fill.fee_drag,-fill.slippage_drag,-fill.impact_drag))
        for funding in trade.funding_events:
            if funding.event.timestamp_ms<=entry_ms:
                cashflows.append(E.funding_cash(trade.side,trade.fills[0].quantity,funding.mark.price,funding.event.rate))
        if trade.entry_ms<=entry_ms<trade.close_ms:
            _,marks=_stream_maps(source.streams(trade.symbol,name))
            mark=marks.get(entry_ms-E.HOUR_MS)
            if mark is None: raise E.IncompleteAuthority("admission equity lacks exact causal prior mark")
            unrealized.append(trade.side*trade.fills[0].quantity*(mark.price-trade.fills[0].reference))
    equity=math.fsum((float(capital),*cashflows,*unrealized))
    if not math.isfinite(equity) or equity<=0: raise E.IncompleteEpisode("side-book equity is non-positive")
    return Decimal(str(equity))


class ExposedEpisodeFailure(E.IncompleteEpisode):
    def __init__(self,message: str,record: dict):
        super().__init__(message); self.record=record


def _close_episode(source: FixedFoldSource,name: str,side: int,symbol: str,decision: int,
                   regime: str,sizing: E.SizedPosition,stop: Decimal,target: Decimal):
    start,end=E.FOLD_RANGES[name]
    entry=decision+E.HOUR_MS
    stream=source.streams(symbol,name,boundary_open=entry+12*E.HOUR_MS==end)
    bars,marks=_stream_maps(stream)
    regular=tuple(bar for bar in stream.bars if isinstance(bar,E.Bar))
    atr=tuple(bars[t] for t in range(start,decision,E.HOUR_MS))
    entry_bar=bars[entry]
    entry_fill=E.make_fill(timestamp_ms=entry,phase="ENTRY",symbol=symbol,side=side,
        quantity=float(sizing.quantity),reference=float(sizing.entry),movement_from=None,volume_bars=regular)
    stress_entry_fill=E.make_fill(timestamp_ms=entry,phase="ENTRY",symbol=symbol,side=side,
        quantity=float(sizing.quantity),reference=float(sizing.entry),movement_from=None,volume_bars=regular,
        slippage_bps=E.STRESS_SLIPPAGE_BPS)
    attempt=f"{name}:{side}:{symbol}:{decision}"
    timestamp=entry
    exit_fill=None
    try:
        lifecycle=[]
        for timestamp in range(entry,entry+13*E.HOUR_MS,E.HOUR_MS):
            bar=bars.get(timestamp)
            if bar is None: raise E.IncompleteAuthority("exposed lifecycle lacks next canonical bar")
            lifecycle.append(bar)
            event=E.exit_reference(side=side,stop=float(stop),target=float(target),bar=bar,
                                   scheduled_due=timestamp>=entry+12*E.HOUR_MS)
            if event is not None: break
        else: raise E.IncompleteEpisode("mandatory exit was not derived")
        phase,reference,close=event
        exit_fill=E.make_fill(timestamp_ms=close,phase=phase,symbol=symbol,side=-side,
            quantity=float(sizing.quantity),reference=reference,movement_from=float(sizing.entry),volume_bars=regular)
        required_events=tuple(event for event in stream.funding if entry<event.timestamp_ms<close
            or phase not in {"STOP_RANGE","TARGET_RANGE"} and event.timestamp_ms==close)
        applied=tuple(E.AppliedFunding(event,marks[event.timestamp_ms-E.HOUR_MS]) for event in required_events)
        lifecycle_marks=tuple(marks[t] for t in range(entry-E.HOUR_MS,close,E.HOUR_MS))
        fills=(entry_fill,exit_fill)
        seal=source.archive.seal_trade(attempt_id=attempt,symbol=symbol,entry_ms=entry,close_ms=close,
            split_start_ms=start,split_end_ms=end,atr_bars=atr,funding_authority=stream.funding,
            lifecycle_bars=lifecycle,fills=fills,funding_marks=tuple(item.mark for item in applied),
            lifecycle_marks=lifecycle_marks)
        trade=E.ClosedTrade(attempt,side,symbol,entry,close,regime,fills,applied,sizing,decision,start,
            stream.source_digest,seal,seal.atr_digest,atr,stream.funding,seal.funding_digest,
            tuple(lifecycle),entry_bar,lifecycle[-1],float(stop),float(target))
        E.validate_closed_trade(trade)
        stress=replace(trade,fills=tuple(E.make_fill(timestamp_ms=fill.timestamp_ms,phase=fill.phase,
            symbol=symbol,side=fill.side,quantity=fill.quantity,reference=fill.reference,
            movement_from=None if fill.phase=="ENTRY" else float(sizing.entry),volume_bars=regular,
            slippage_bps=E.STRESS_SLIPPAGE_BPS) for fill in fills))
        E.stress_same_selection((trade,),(stress,))
        return trade,stress,lifecycle_marks
    except (E.C259Error,KeyError) as exc:
        known_funding=[]
        for funding in stream.funding:
            if entry<funding.timestamp_ms<=timestamp and funding.timestamp_ms-E.HOUR_MS in marks:
                mark=marks[funding.timestamp_ms-E.HOUR_MS]
                known_funding.append({"timestamp_ms":funding.timestamp_ms,"rate":funding.rate,
                    "mark_price":mark.price,"cash":E.funding_cash(side,float(sizing.quantity),mark.price,funding.rate)})
        cleanup=bars.get(timestamp+E.HOUR_MS)
        known_marks=[asdict(mark) for t,mark in sorted(marks.items()) if entry-E.HOUR_MS<=t<timestamp]
        raise ExposedEpisodeFailure(str(exc),{"attempt_id":attempt,"side":side,"symbol":symbol,
            "entry_ms":entry,"accepted_quantity":str(sizing.quantity),"status":"INCOMPLETE_EXPOSED_EPISODE",
            "known_entry_fill":asdict(entry_fill),"known_entry_cost_cash":-(entry_fill.fee_drag+entry_fill.slippage_drag+entry_fill.impact_drag),
            "known_stress_entry_fill":asdict(stress_entry_fill),
            "failure_detected_ms":timestamp,"known_funding":known_funding,"known_exposure_marks":known_marks,
            "frozen_stop":str(stop),"frozen_target":str(target),
            "known_exit_fill":asdict(exit_fill) if exit_fill is not None else None,
            "next_known_cleanup_open":cleanup.open if cleanup is not None else None,
            "cleanup_due_ms":timestamp+E.HOUR_MS,"unpriced_exit_components_retained":True,
            "actual_net_cash":None,"trade_R":None,
            "source_digest":stream.source_digest,"reason":str(exc),
            "economic_metrics_defined":False,"forward_qualified":False}) from exc


def replay_fold(source: FixedFoldSource,name: str,side: int,capital: int) -> Replay:
    if type(source) is not FixedFoldSource or side not in (-1,1) or capital not in E.CAPITALS:
        raise E.IncompleteAuthority("fixed source and frozen isolated replay identity required")
    if name not in E.FOLD_RANGES: raise E.EvaluationLocked("holdout and custom folds are unavailable")
    start,end=E.FOLD_RANGES[name]
    try: btc=source.streams("BTCUSDT",name)
    except E.IncompleteAuthority as exc:
        rows=tuple({"decision_ms":timestamp,"side":side,"capital":capital,"eligible":0,
            "admitted":[],"skipped":[],"status":"SOURCE_AUTHORITY_FAILURE","reason":str(exc)}
            for timestamp in range(start,end,E.HOUR_MS))
        return Replay(E.FoldLifecycle(name,(),(),()),side,capital,rows,(),
            ({"status":"BENCHMARK_SOURCE_INTEGRITY_FAILURE","reason":str(exc)},),())
    btc_bars={bar.open_ms:bar for bar in btc.bars if isinstance(bar,E.Bar)}
    btc_closes={t+E.HOUR_MS:E.ClosePrice(t+E.HOUR_MS,bar.close) for t,bar in btc_bars.items()}
    cache={}
    trades=[];stress=[];marks={};decisions=[];fitted=[];failures=[]
    ema=E.EMA200()
    for decision in range(start,end,E.HOUR_MS):
        row={"decision_ms":decision,"side":side,"capital":capital,"eligible":0,"admitted":[],"skipped":[],"status":"ZERO_TRADE"}
        decisions.append(row)
        btc_bar=btc_bars.get(decision-E.HOUR_MS)
        regime=ema.update(btc_bar) if btc_bar is not None else None
        if btc_bar is None: ema=E.EMA200()
        if name=="development" and not E.filter_authority_available(decision):
            row["reason"]="FILTER_AUTHORITY_UNAVAILABLE";continue
        if failures: row["reason"]="SIDE_REPLAY_INCOMPLETE";continue
        if decision<start+722*E.HOUR_MS or regime is None:
            row["reason"]="SPLIT_RESET_WARMUP";continue
        entry=decision+E.HOUR_MS
        if entry+12*E.HOUR_MS>end:
            row["reason"]="FLAT_SPLIT_BOUNDARY";continue
        fits=[]
        for symbol in source.members(name,decision):
            if symbol=="BTCUSDT": continue
            if symbol not in cache:
                try:
                    stream=source.streams(symbol,name)
                except E.IncompleteAuthority as exc:
                    failures.append({"status":"SYMBOL_SOURCE_INTEGRITY_FAILURE","symbol":symbol,
                        "decision_ms":decision,"reason":str(exc)})
                    row["status"]="SOURCE_AUTHORITY_FAILURE"
                    break
                indexed,_=_stream_maps(stream)
                closes={bar.open_ms+E.HOUR_MS:E.ClosePrice(bar.open_ms+E.HOUR_MS,bar.close)
                        for bar in stream.bars if isinstance(bar,E.Bar)}
                cache[symbol]=stream,indexed,closes
            try:
                stream,indexed,closes=cache[symbol]
                times=range(decision-721*E.HOUR_MS,decision,E.HOUR_MS)
                fit=E.fit_signal(tuple(btc_closes[t] for t in times),tuple(closes[t] for t in times),
                    btc_decision=btc_closes[decision],alt_decision=closes[decision])
            except (KeyError,E.IncompleteAuthority) as exc:
                row["skipped"].append({"symbol":symbol,"reason":"SIGNAL_AUTHORITY_OR_WARMUP","detail":str(exc)})
                continue
            fits.append((symbol,fit))
            fitted.append({"decision_ms":decision,"symbol":symbol,"beta0":fit.beta0,"gamma":fit.gamma,
                "t_gamma":fit.t_gamma,"forecast":fit.forecast,"residual_d":fit.residual_d,
                "impulse_threshold":fit.impulse_threshold,
                "btc_impulse":math.log(fit.btc_decision_price/fit.prior_btc_closes[-1].price),
                "residual_consumption_ratio":fit.residual_d/fit.forecast if fit.forecast else None,
                "decision_liquidity_ratio":indexed[decision-E.HOUR_MS].quote_volume/statistics.median(
                    indexed[t].quote_volume for t in range(decision-25*E.HOUR_MS,decision-E.HOUR_MS,E.HOUR_MS)),
                "qualifies":fit.qualifies,"side":fit.side})
        if failures:continue
        row["eligible"]=sum(fit.qualifies and fit.side==side for _,fit in fits)
        occupied={trade.symbol for trade in trades if trade.close_ms>entry}
        for symbol,fit in fits:
            if fit.qualifies and fit.side==side and symbol in occupied:
                row["skipped"].append({"symbol":symbol,"reason":"ALREADY_OCCUPIED"})
        selected=E.select_candidates(fits,side,decision_close_ms=decision,occupied=occupied,limit=None)
        row["ranked_same_side"]=list(selected)
        fit_map=dict(fits)
        for rank,symbol in enumerate(selected,1):
            if len(row["admitted"])>=3:break
            stream,indexed,_=cache[symbol]
            try:
                entry_bar=indexed[entry]
                if not isinstance(entry_bar,E.Bar): raise E.IncompleteAuthority("entry lacks full canonical authority")
                filters=source.filters(name)[symbol]
                reference=Decimal(str(entry_bar.open))
                if reference%filters.tick:
                    row["skipped"].append({"symbol":symbol,"rank":rank,"reason":"ENTRY_PRICE_OFF_TICK"});continue
                atr_bars=tuple(indexed[t] for t in range(start,decision,E.HOUR_MS))
                atr=E.atr24_wilder(atr_bars,decision)
                stop,target=E.protection_prices(reference,Decimal(str(atr)),side,filters.tick)
                if not E.cost_admission(entry=reference,stop=stop,forecast=fit_map[symbol].forecast):
                    row["skipped"].append({"symbol":symbol,"reason":"COST_INFEASIBLE"});continue
                equity=_equity_at(source,name,capital,trades,entry)
                volume_rows=tuple(E.VolumePoint(t,indexed[t].quote_volume) for t in range(entry-24*E.HOUR_MS,entry,E.HOUR_MS))
                sized=E.size_position(side=side,entry=reference,stop=stop,filters=filters,side_book_equity=equity,
                    entry_impact_asof_ms=entry,entry_volume_rows=volume_rows)
                if sized is None:
                    row["skipped"].append({"symbol":symbol,"reason":"EXCHANGE_OR_RISK_INFEASIBLE"});continue
                if min(sized.quantity*stop,sized.quantity*target)<filters.min_notional:
                    row["skipped"].append({"symbol":symbol,"reason":"PROTECTION_EXIT_MIN_NOTIONAL"});continue
                if not E.liquidity_admission(decision_quote_volume=indexed[decision-E.HOUR_MS].quote_volume,
                    preceding_quote_volumes=tuple(indexed[t].quote_volume for t in range(decision-25*E.HOUR_MS,decision-E.HOUR_MS,E.HOUR_MS)),
                    raw_entry_notional=sized.raw_notional,causal_quote_volume_24h=sized.quote_volume_24h):
                    row["skipped"].append({"symbol":symbol,"reason":"IMPACT_INFEASIBLE"});continue
                active=[trade for trade in trades if trade.close_ms>entry]
                if (len(active)>=3 or sum((trade.sizing.initial_risk_cash for trade in active),Decimal(0))+sized.initial_risk_cash>equity*Decimal("0.03")
                        or sum((trade.sizing.raw_notional for trade in active),Decimal(0))+sized.raw_notional>equity*Decimal("1.5")):
                    row["skipped"].append({"symbol":symbol,"reason":"SIDE_BOOK_CAP"});continue
                trade,stressed,lifecycle_marks=_close_episode(source,name,side,symbol,decision,regime,sized,stop,target)
            except ExposedEpisodeFailure as exc:
                failures.append(exc.record);row["status"]="INCOMPLETE_EXPOSED_EPISODE";break
            except (KeyError,E.C259Error) as exc:
                row["skipped"].append({"symbol":symbol,"reason":"CAUSAL_AUTHORITY_FAILURE","detail":str(exc)})
                failures.append({"status":"PRE_ENTRY_AUTHORITY_FAILURE","symbol":symbol,"decision_ms":decision,"reason":str(exc)})
                break
            trades.append(trade);stress.append(stressed)
            row.setdefault("admission_diagnostics",[]).append({"symbol":symbol,"original_rank":rank,
                "entry_ms":entry,"side_book_equity":str(equity),"raw_notional":str(sized.raw_notional),
                "initial_risk_cash":str(sized.initial_risk_cash),"causal_quote_volume_24h":sized.quote_volume_24h,
                "entry_participation":float(sized.raw_notional)/sized.quote_volume_24h})
            exit_fill=trade.fills[-1]
            if (Decimal(str(exit_fill.reference))%filters.tick
                    or Decimal(str(exit_fill.reference))*sized.quantity<filters.min_notional):
                failures.append({"status":"EXCHANGE_EXIT_RULE_FAILURE","attempt_id":trade.attempt_id,
                    "known_exit_fill":asdict(exit_fill),"retained_closed_trade":True})
            row["admitted"].append(trade.attempt_id);row["status"]="ADMITTED"
            for mark in lifecycle_marks:
                key=mark.timestamp_ms,mark.symbol
                if key in marks and marks[key]!=mark: raise E.IncompleteAuthority("conflicting exposure mark")
                marks[key]=mark
    fold=E.FoldLifecycle(name,tuple(trades),tuple(stress),tuple(marks[key] for key in sorted(marks)))
    return Replay(fold,side,capital,tuple(decisions),tuple(fitted),tuple(failures),tuple(btc_bars[t] for t in sorted(btc_bars)))


def _identity_directory() -> Path:
    common=Path(str(E._git(ROOT,"rev-parse","--git-common-dir")))
    return ((ROOT/common) if not common.is_absolute() else common).resolve()/"c259-evaluation"


def _exclusive(path: Path,value: Mapping) -> None:
    path.parent.mkdir(parents=True,exist_ok=True)
    descriptor=os.open(path,os.O_WRONLY|os.O_CREAT|os.O_EXCL)
    with os.fdopen(descriptor,"wb") as stream:
        stream.write(E._canon(value)+b"\n");stream.flush();os.fsync(stream.fileno())


def evaluate_once(*,store: Path) -> Mapping:
    run=authenticate()  # before identities, snapshots, archive bytes or outcomes
    ledger=_identity_directory()
    claim=ledger/"candidate-259.claim.json"
    identity=E._sha(E._canon({"candidate":259,"contract":E.CONTRACT_SHA256,
        "head":run.head,"pins":asdict(run.pins),"code":run.code_blobs}))
    try: _exclusive(claim,{"state":"RESERVED","identity":identity,"head":run.head})
    except FileExistsError as exc: raise E.EvaluationLocked("C259 candidate identity already consumed across heads/worktrees") from exc
    try:
        from tools.research import c259_reporting as R
        source=FixedFoldSource(run,store)
        sides={}
        for side in (1,-1):
            development={capital:replay_fold(source,"development",side,capital) for capital in E.CAPITALS}
            primary=development[E.PRIMARY_CAPITAL]
            dev=R.fold_report(primary)
            diagnostics={str(capital):R.fold_report(replay) for capital,replay in development.items() if capital!=E.PRIMARY_CAPITAL}
            if not dev["gate"]["pass"]:
                sides[E.SIDES[side]]={"verdict":"DATA_INCOMPLETE_DEVELOPMENT" if primary.failures else "REJECT_DEVELOPMENT","forward_opened":False,
                    "primary_development":dev,"capital_diagnostics":diagnostics}
                continue
            forwards={capital:tuple(replay_fold(source,name,side,capital) for name in ("wf1","wf2","wf3")) for capital in E.CAPITALS}
            forward=R.forward_report(forwards[E.PRIMARY_CAPITAL])
            sides[E.SIDES[side]]={"verdict":"FORWARD_ECONOMIC_SURVIVOR_REQUIRES_INDEPENDENT_ACCEPTANCE" if forward["numeric_economic_gate_pass"] else "REJECT_FORWARD",
                "forward_opened":True,"primary_development":dev,"primary_forward":forward,
                "capital_diagnostics":{"development":diagnostics,"forward":{str(capital):[R.fold_report(item) for item in replays]
                    for capital,replays in forwards.items() if capital!=E.PRIMARY_CAPITAL}}}
        result=E.immutable_result_identity(pins=run.pins,source_digest=run.authority.source_digest,
            result={"sides":sides,"diagnostic_capitals_select_primary":False,"sealed_holdout_opened":False,
                    "promotable":False,"paper_qualified":False,"implementation_head":run.head,"code_blobs":dict(run.code_blobs)})
        _exclusive(ledger/"candidate-259.report.json",result)
        _exclusive(ledger/"candidate-259.completed.json",{"state":"COMPLETED","identity":identity,"packet_sha256":E._sha(E._canon(result))})
        return result
    except BaseException:
        _exclusive(ledger/"candidate-259.failed.json",{"state":"FAILED_CONSUMED","identity":identity})
        raise
