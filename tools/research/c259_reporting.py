"""C259 lifecycle-derived reports; undefined or unavailable evidence stays explicit."""
from __future__ import annotations

import math
import statistics
from collections import Counter
from dataclasses import asdict
from datetime import datetime,timezone
from decimal import Decimal
from typing import Sequence
from tools.research import btc_residual_leadlag_continuation_c259_stagea as E


def _finite(value):
    if isinstance(value,Decimal):return str(value)
    if isinstance(value,float) and not math.isfinite(value): return None
    if isinstance(value,dict): return {str(key):_finite(item) for key,item in value.items()}
    if isinstance(value,(tuple,list)): return [_finite(item) for item in value]
    return value


def _distribution(values: Sequence[float]) -> dict:
    return {"n":len(values),"min":min(values) if values else None,
        "median":statistics.median(values) if values else None,"max":max(values) if values else None,
        "mean":math.fsum(values)/len(values) if values else None}


def _correlation(left,right):
    if len(left)!=len(right) or len(left)<2:return None
    lm=math.fsum(left)/len(left);rm=math.fsum(right)/len(right)
    ll=math.fsum((v-lm)**2 for v in left);rr=math.fsum((v-rm)**2 for v in right)
    if ll<=0 or rr<=0:return None
    return math.fsum((l-lm)*(r-rm) for l,r in zip(left,right))/math.sqrt(ll*rr)


def _group(trades,key):
    grouped={}
    for trade in trades: grouped.setdefault(key(trade),[]).append(trade)
    return {name:{"closed":len(rows),"net_R":math.fsum(row.trade_R for row in rows),
        "mean_R":math.fsum(row.trade_R for row in rows)/len(rows)} for name,rows in sorted(grouped.items())}


def _period(trade,kind):
    stamp=datetime.fromtimestamp(trade.close_ms/1000,tz=timezone.utc)
    return stamp.strftime("%Y-%m") if kind=="month" else f"{stamp.isocalendar().year}-W{stamp.isocalendar().week:02d}"


def _canonical_hourly(points,start,end):
    ordered=sorted(points,key=lambda item:(item[0],item[1]))
    index=0;equity=ordered[0][2]
    out={}
    for timestamp in range(start,end+E.HOUR_MS,E.HOUR_MS):
        while index<len(ordered) and ordered[index][0]<=timestamp:
            equity=ordered[index][2];index+=1
        out[timestamp]=equity
    return out


def _recovery(points,capital):
    peak=capital;peak_time=points[0][0];worst=0
    for timestamp,_,value in points:
        if value>=peak: peak=value;peak_time=timestamp
        else: worst=max(worst,timestamp-peak_time)
    return worst/E.HOUR_MS


def _ledger(trades):
    rows=[]
    for trade in sorted(trades,key=lambda item:(item.entry_ms,item.symbol,item.attempt_id)):
        cashflows=[]
        for fill in trade.fills:
            phase=4 if fill.phase=="ENTRY" else 0 if fill.phase in {"STOP_RANGE","TARGET_RANGE"} else 3 if fill.phase=="PROTECTION_FAILURE_CLEANUP" else 2
            for kind,value in (("movement",fill.movement_cash),("fee",-fill.fee_drag),
                               ("slippage",-fill.slippage_drag),("impact",-fill.impact_drag)):
                cashflows.append({"timestamp_ms":fill.timestamp_ms,"phase":phase,"kind":kind,"cash":value})
        for funding in trade.funding_events:
            cashflows.append({"timestamp_ms":funding.event.timestamp_ms,"phase":1,"kind":"funding",
                "cash":E.funding_cash(trade.side,trade.fills[0].quantity,funding.mark.price,funding.event.rate)})
        cashflows.sort(key=lambda item:(item["timestamp_ms"],item["phase"],item["kind"]))
        rows.append({"attempt_id":trade.attempt_id,"side":trade.side,"symbol":trade.symbol,"entry_ms":trade.entry_ms,
            "close_ms":trade.close_ms,"regime":trade.regime,"initial_risk_cash":trade.initial_risk_cash,
            "actual_net_cash":trade.net_cash,"trade_R":trade.trade_R,"incomplete":trade.incomplete,
            "sizing":asdict(trade.sizing),
            "fills":[asdict(fill) for fill in trade.fills],"funding":[asdict(item) for item in trade.funding_events],
            "cashflows":cashflows,"cashflow_net":math.fsum(item["cash"] for item in cashflows),
            "source_digest":trade.authority_source_digest,"source_seal":{
                name:getattr(trade.source_seal,name) for name in ("atr_digest","funding_digest","lifecycle_digest",
                    "fill_volume_digests","funding_mark_digest","lifecycle_mark_digest")}})
    return rows


def _known_exposure_prefix(replay, *, stress=False):
    """Keep all accepted lots through the first unpriced-exit failure.

    This is an explicitly labelled observed prefix, never complete P&L or a
    replacement gate drawdown. Missing marks terminate it; they are not filled.
    """
    partials=[row for row in replay.failures if row.get("accepted_quantity")]
    if not partials:return None
    end=min(row["failure_detected_ms"] for row in partials)
    start=E.FOLD_RANGES[replay.fold.name][0]
    prices={(mark.timestamp_ms,mark.symbol):mark.price for mark in replay.fold.marks}
    events={};lots={}
    for trade in replay.fold.stress if stress else replay.fold.base:
        lots[trade.attempt_id]={"symbol":trade.symbol,"side":trade.side,"quantity":trade.fills[0].quantity,
            "entry":trade.fills[0].reference}
        for fill in trade.fills:
            phase=4 if fill.phase=="ENTRY" else 0 if fill.phase in {"STOP_RANGE","TARGET_RANGE"} else 3 if trade.incomplete else 2
            events.setdefault((fill.timestamp_ms,phase),[]).append((trade.attempt_id,
                (fill.movement_cash,-fill.fee_drag,-fill.slippage_drag,-fill.impact_drag),fill.phase=="ENTRY",fill.phase!="ENTRY"))
        for applied in trade.funding_events:
            cash=E.funding_cash(trade.side,trade.fills[0].quantity,applied.mark.price,applied.event.rate)
            events.setdefault((applied.event.timestamp_ms,1),[]).append((trade.attempt_id,(cash,),False,False))
    for row in partials:
        identity=row["attempt_id"]
        fill=row["known_stress_entry_fill"] if stress else row["known_entry_fill"]
        lots[identity]={"symbol":row["symbol"],"side":row["side"],"quantity":fill["quantity"],"entry":fill["reference"]}
        events.setdefault((row["entry_ms"],4),[]).append((identity,
            (-fill["fee_drag"],-fill["slippage_drag"],-fill["impact_drag"]),True,False))
        for funding in row["known_funding"]:
            events.setdefault((funding["timestamp_ms"],1),[]).append((identity,(funding["cash"],),False,False))
        for mark in row["known_exposure_marks"]:prices[(mark["timestamp_ms"],mark["symbol"])]=mark["price"]
    inventory={};cashflows=[];points=[(start,0,float(replay.capital))]
    times=sorted({timestamp for timestamp,_ in events if timestamp<=end}|
                 {timestamp+E.HOUR_MS for timestamp,_ in prices if start<=timestamp+E.HOUR_MS<=end})
    def append(timestamp,phase):
        movement=[]
        for identity in sorted(inventory,key=lambda key:(inventory[key]["symbol"],key)):
            lot=inventory[identity];mark=prices.get((timestamp-E.HOUR_MS,lot["symbol"]))
            if mark is None:return False
            movement.append(lot["side"]*lot["quantity"]*(mark-lot["entry"]))
        points.append((timestamp,phase,math.fsum((float(replay.capital),*cashflows,*movement))))
        return True
    complete=True
    for timestamp in times:
        for phase in range(5):
            rows=events.get((timestamp,phase),())
            if not rows:continue
            for identity,deltas,entry,exit_ in sorted(rows,key=lambda row:(lots[row[0]]["symbol"],row[0])):
                cashflows.extend(deltas)
                if entry:inventory[identity]=lots[identity]
                if exit_:inventory.pop(identity,None)
            if not append(timestamp,phase):complete=False;break
        if not complete:break
        if not append(timestamp,5):complete=False;break
    return {"points":points,"observed_through_ms":points[-1][0],"failure_detected_ms":end,
        "prefix_marks_complete":complete,"complete_episode_economics":False,
        "marked_drawdown_R_lower_bound":E.marked_equity_drawdown_R(points,replay.capital),
        "gate_substitution_permitted":False}


def fold_report(replay) -> dict:
    fold=replay.fold
    start,end=E.FOLD_RANGES[fold.name]
    trades=fold.base
    undefined=[]
    if not trades:
        base={"closed":0,"symbols":0,"net_R":0.0,"mean_R":None,"profit_factor":None,"incomplete":bool(replay.failures)}
        stress=dict(base);bootstrap=None
        undefined.extend(("mean_R","profit_factor","bootstrap","correlation","btc_beta"))
    else:
        base=_finite(E.metrics(trades));stress=_finite(E.metrics(fold.stress))
        bootstrap=E.bootstrap_stats([trade.trade_R for trade in sorted(trades,key=lambda item:(item.entry_ms,item.symbol))],side=replay.side) if replay.capital==E.PRIMARY_CAPITAL else None
        if base["profit_factor"] is None:undefined.append("profit_factor")
        if stress["profit_factor"] is None:undefined.append("stress_profit_factor")
    points=E.derive_equity_points(trades,fold.marks,start_ms=start,end_ms=end,capital=replay.capital)
    stressed=E.derive_equity_points(fold.stress,fold.marks,start_ms=start,end_ms=end,capital=replay.capital)
    marked=E.marked_equity_drawdown_R(points,replay.capital)
    realized=E.closed_realized_drawdown_R(trades) if trades else 0.0
    stress_marked=E.marked_equity_drawdown_R(stressed,replay.capital)
    stress_realized=E.closed_realized_drawdown_R(fold.stress) if trades else 0.0
    drawdown=max(marked,realized);stress_dd=max(stress_marked,stress_realized)
    tier=E.drawdown_tier(float(base["net_R"]),drawdown,float(stress["net_R"]),stress_dd)
    if replay.capital==E.PRIMARY_CAPITAL:
        try:
            gate=E.development_gate(fold) if fold.name=="development" else {"pass":False,"reason":"PER_FOLD_EVIDENCE_ONLY"}
        except E.C259Error as exc:gate={"pass":False,"failures":["UNDEFINED_OR_INCOMPLETE"],"reason":str(exc)}
        if replay.failures:gate={**gate,"pass":False,"authority_failures":len(replay.failures)}
    else:gate={"pass":False,"applicable":False,"reason":"DIAGNOSTIC_CAPITAL_NEVER_SELECTS_OR_VETOES_PRIMARY"}
    hourly=_canonical_hourly(points,start,end)
    btc={bar.open_ms+E.HOUR_MS:bar.close for bar in replay.benchmark}
    returns=[];benchmark=[]
    expected=tuple(range(start+E.HOUR_MS,end+E.HOUR_MS,E.HOUR_MS))
    if set(expected)-set(btc):undefined.append("canonical_BTC_hour_missing")
    else:
        for previous,timestamp in zip(expected,expected[1:]):
            returns.append(hourly[timestamp]/hourly[previous]-1)
            benchmark.append(btc[timestamp]/btc[previous]-1)
    correlation=_correlation(returns,benchmark)
    beta=None
    if benchmark:
        bm=math.fsum(benchmark)/len(benchmark);rm=math.fsum(returns)/len(returns)
        variance=math.fsum((v-bm)**2 for v in benchmark)
        if variance>0:beta=math.fsum((a-rm)*(b-bm) for a,b in zip(returns,benchmark))/variance
    if correlation is None:undefined.append("book_BTC_correlation")
    if beta is None:undefined.append("BTC_beta")
    pairwise={}
    series={}
    for symbol in sorted({trade.symbol for trade in trades}):
        subset=tuple(trade for trade in trades if trade.symbol==symbol)
        keys={(t,symbol) for trade in subset for t in range(trade.entry_ms-E.HOUR_MS,trade.close_ms,E.HOUR_MS)}
        marks=tuple(mark for mark in fold.marks if (mark.timestamp_ms,mark.symbol) in keys)
        path=E.derive_equity_points(subset,marks,start_ms=start,end_ms=end,capital=replay.capital)
        values=_canonical_hourly(path,start,end)
        series[symbol]=[values[t]/values[t-E.HOUR_MS]-1 for t in range(start+2*E.HOUR_MS,end+E.HOUR_MS,E.HOUR_MS)]
    names=sorted(series)
    for index,left in enumerate(names):
        for right in names[index+1:]:pairwise[f"{left}/{right}"]=_correlation(series[left],series[right])
    fees=math.fsum(fill.fee_drag for trade in trades for fill in trade.fills)
    slippage=math.fsum(fill.slippage_drag for trade in trades for fill in trade.fills)
    impact=math.fsum(fill.impact_drag for trade in trades for fill in trade.fills)
    funding=math.fsum(trade.funding for trade in trades)
    months=_group(trades,lambda trade:_period(trade,"month"))
    for year,month in E._month_keys(start,end):months.setdefault(f"{year:04d}-{month:02d}",{"closed":0,"net_R":0.0,"mean_R":None})
    ledger=_ledger(trades)
    output={"side":E.SIDES[replay.side],"fold":fold.name,"capital_usd":replay.capital,
        "primary":replay.capital==E.PRIMARY_CAPITAL,"gate":_finite(gate),"base":base,"stress":stress,
        "bootstrap":bootstrap,"scheduled":len(replay.decisions),
        "eligible":sum(row["eligible"] for row in replay.decisions),"admitted":len(trades),
        "skipped":sum(len(row["skipped"]) for row in replay.decisions),"failed":len(replay.failures),"closed":len(trades),
        "zero_trade_hours":sum(not row["admitted"] for row in replay.decisions),
        "pre_authority_zero_trade_hours":sum(row.get("reason")=="FILTER_AUTHORITY_UNAVAILABLE" for row in replay.decisions),
        "gross_R":math.fsum(math.fsum(fill.movement_cash for fill in trade.fills)/trade.initial_risk_cash for trade in trades),
        "signed_funding_R":math.fsum(trade.funding/trade.initial_risk_cash for trade in trades),
        "win_rate":sum(trade.net_cash>0 for trade in trades)/len(trades) if trades else None,
        "marked_equity_drawdown_R":marked,"closed_realized_drawdown_R":realized,"gate_drawdown_R":drawdown,
        "stress_marked_equity_drawdown_R":stress_marked,"stress_closed_realized_drawdown_R":stress_realized,
        "stress_gate_drawdown_R":stress_dd,"recovery_hours":_recovery(points,replay.capital),
        "return_drawdown_ratio":float(base["net_R"])/drawdown if drawdown>0 else None,"risk_tier":tier,
        "fee_drag":fees,"slippage_drag":slippage,"impact_drag":impact,"signed_funding":funding,
        "holding_hours":_distribution([(trade.close_ms-trade.entry_ms)/E.HOUR_MS for trade in trades]),
        "months":months,"weeks":_group(trades,lambda trade:_period(trade,"week")),
        "symbols":_group(trades,lambda trade:trade.symbol),"regimes":_group(trades,lambda trade:trade.regime),
        "same_hour_cluster_size":max(Counter(trade.entry_ms for trade in trades).values(),default=0),
        "impulse_clusters":dict(Counter(str(row["decision_ms"]) for row in replay.fitted if row["qualifies"] and row["side"]==replay.side)),
        "book_BTC_return_correlation":correlation,"BTC_beta":beta,"pairwise_return_correlations":pairwise,
        "book_benchmark_hourly_observations":len(returns),
        "benchmark_start_policy":"First completed close inside split starts return baseline; all following canonical hours, including flat exposure, are retained. No pre-split warmup.",
        "signal_distributions":{key:_distribution([row[key] for row in replay.fitted]) for key in (
            "beta0","gamma","t_gamma","forecast","residual_d","impulse_threshold","btc_impulse","decision_liquidity_ratio")},
        "residual_consumption_ratio_distribution":{**_distribution([row["residual_consumption_ratio"] for row in replay.fitted if row["residual_consumption_ratio"] is not None]),
            "undefined_zero_forecast_count":sum(row["residual_consumption_ratio"] is None for row in replay.fitted)},
        "entry_participation_distribution":_distribution([row["entry_participation"] for decision in replay.decisions for row in decision.get("admission_diagnostics",())]),
        "overlap_controls":{"BOL-1H":{"status":"UNAVAILABLE_REQUIRED_AGGRESSOR_FLOW_STREAM"},
            "DSR-1H":{"status":"UNAVAILABLE_PROTECTED_SIGNAL_SPECIFICATION"},
            "C258":{"status":"UNAVAILABLE_INDEPENDENT_ENTRY_EVIDENCE"}},
        "overlap_control_acceptance":"PENDING_INDEPENDENT_REVIEW; no outcome packets read or fabricated overlap",
        "authority_failures":list(replay.failures),"partial_exposed_episodes_retained":any(row.get("accepted_quantity") for row in replay.failures),
        "all_accepted_exposure_economics_defined":not any(row.get("accepted_quantity") for row in replay.failures),
        "all_accepted_exposure_actual_net_cash":None if any(row.get("accepted_quantity") for row in replay.failures) else math.fsum(trade.net_cash for trade in trades),
        "closed_trade_metrics_cover_all_accepted_exposure":not any(row.get("accepted_quantity") for row in replay.failures),
        "decisions":list(replay.decisions),"fits":list(replay.fitted),
        "cashflow_and_trade_ledger":ledger,"stress_trade_ledger":_ledger(fold.stress),"equity_points":points,
        "stress_equity_points":stressed,"undefined_statistics":sorted(set(undefined)),
        "promotable":False,"paper_testnet_only":True,"full_report_acceptance":"PENDING_INDEPENDENT_REVIEW_AND_OVERLAP_EVIDENCE"}
    output["ledger_sha256"]=E._sha(E._canon(_finite({"decisions":output["decisions"],"trades":ledger,"failures":list(replay.failures)})))
    if not output["all_accepted_exposure_economics_defined"]:
        output["base_scope"]="KNOWN_COMPLETE_CLOSED_TRADES_ONLY; accepted incomplete exposure is retained separately"
        output["closed_only_equity_points"]=output.pop("equity_points")
        output["closed_only_stress_equity_points"]=output.pop("stress_equity_points")
        output["known_exposure_prefix"]=_known_exposure_prefix(replay)
        output["known_stress_exposure_prefix"]=_known_exposure_prefix(replay,stress=True)
        output["all_accepted_exposure_gate_drawdown_R"]=None
        output["equity_points"]=None;output["stress_equity_points"]=None
        output["closed_only_marked_equity_drawdown_R"]=output["marked_equity_drawdown_R"]
        output["marked_equity_drawdown_R"]=None;output["gate_drawdown_R"]=None
        output["closed_only_stress_gate_drawdown_R"]=output["stress_gate_drawdown_R"]
        output["stress_gate_drawdown_R"]=None
        output["risk_tier"]={"tier":"UNDEFINED_INCOMPLETE_EXPOSURE","pass":False,"paper_multiplier":None}
    return _finite(output)


def forward_report(replays) -> dict:
    reports=[fold_report(replay) for replay in replays]
    if any(replay.failures or not replay.fold.base for replay in replays):
        gate={"pass":False,"failures":["EMPTY_OR_INCOMPLETE_FORWARD_FOLD"]}
    else:
        try:gate=E.forward_gate(tuple(replay.fold for replay in replays))
        except E.C259Error as exc:gate={"pass":False,"failures":["UNDEFINED_OR_INCOMPLETE"],"reason":str(exc)}
    # Missing overlap diagnostics cannot be presented as full-report acceptance.
    numeric_pass=bool(gate["pass"])
    gate={**gate,"pass":False,"report_acceptance_pending":True}
    return {"gate":_finite(gate),"folds":reports,"numeric_economic_gate_pass":numeric_pass,
            "numeric_economic_gate":_finite({key:value for key,value in gate.items() if key!="pass"}),
            "primary_capital_usd":E.PRIMARY_CAPITAL,"side_isolation":True,"promotable":False}
