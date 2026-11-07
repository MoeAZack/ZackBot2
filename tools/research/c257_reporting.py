"""Reporting-only C257 diagnostics. Never selects or rescues a strategy."""
from __future__ import annotations

import math
import hashlib
import json
from collections import Counter
from datetime import datetime, timezone
from decimal import Decimal
from typing import Mapping, Sequence
from tools.research import funding_shock_reversal_xsec_c257_stagea as S


REGIME_SPECIFICATION = {
    "authority":"Codex reporting-only ruling, 2026-11-07; inherited C258 BTC 1h EMA200 convention",
    "clock":"Last completed checksum-bound BTCUSDT 1h close available by nominal funding T+300 seconds",
    "reset":"Reset at each evaluable fold/segment start; a missing hourly row resets diagnostic warmup",
    "warmup":"First 199 contiguous closes are UNCLASSIFIED; no pre-fold warmup",
    "seed":"math.fsum(first200)/200 with binary64 closes in chronological order",
    "recurrence":"Binary64 ema=(2/201)*close+(199/201)*previous_ema in this written order",
    "labels":"BTC_ABOVE_EMA200 when close>=ema, BTC_BELOW_EMA200 otherwise; missing/warmup UNCLASSIFIED",
    "effect":"Reporting only: no signal, pairing, sizing, admission, cost, exit or numeric gate changes; no rescue",
    "limitation":"Original qualitative single-regime-reliance criterion has no frozen numeric threshold; no regime-robustness claim",
}

BASELINE_SEED = int.from_bytes(hashlib.sha256(
    b"C257_REPORT_BASELINE_V1:"+S.CONTRACT_SHA256.encode("ascii")).digest()[:8],"big")


def diagnostic_pairs(shocks, mode: str, rng):
    primary=S.fixed_index_pairs(shocks)
    if mode=="sign_flipped":
        return tuple(S.IndexedPair(pair.index,pair.short_symbol,pair.long_symbol,pair.short_z,pair.long_z)
                     for pair in primary)
    if mode!="random_opposite_tail":
        raise S.C257Error("unknown fixed reporting baseline")
    if len(shocks)<12:
        return ()
    longs=sorted((item for item in shocks if item.z<=-2),key=lambda item:(item.z,item.symbol))
    shorts=sorted((item for item in shocks if item.z>=2),key=lambda item:(-item.z,item.symbol))
    count=min(len(longs),len(shorts),3)
    if not count: return ()
    left=rng.choice(len(longs),size=count,replace=False)
    right=rng.choice(len(shorts),size=count,replace=False)
    return tuple(S.IndexedPair(index,longs[int(l)].symbol,shorts[int(r)].symbol,
                              longs[int(l)].z,shorts[int(r)].z)
                 for index,(l,r) in enumerate(zip(left,right),1))


def diagnostic_summary(input_, mode: str) -> dict:
    rows=input_.attempts
    zero=Decimal(0)
    ledger=[{"attempt_id":row.attempt_id,"entry_ms":row.entry_ms,"close_ms":row.close_ms,
        "symbols":row.symbols,"base_net_cash":str(row.base_net_cash),"base_R":str(row.base_R),
        "stress_net_cash":str(row.stress_net_cash),"stress_R":str(row.stress_R),
        "funding":[{"nominal_ms":item.point.nominal_ms,"symbol":item.point.symbol,
                    "rate":str(item.point.rate),"cash":str(item.cash)} for item in row.funding],
        "fills":[{"timestamp_ms":cash.fill.timestamp_ms,"symbol":cash.fill.symbol,
                   "side":cash.fill.transaction_side,"phase":cash.fill.phase,
                   "quantity":str(cash.fill.quantity),"reference":str(cash.fill.reference),
                   "execution":str(cash.execution),"fee":str(cash.fee),
                   "slippage":str(cash.slippage),"impact":str(cash.impact),
                   "impact_failure":cash.impact_failure} for cash in row.fills]}
        for row in rows]
    return {"case":mode,"status":"REPORT_ONLY","promotable":False,"rescue_permitted":False,
        "seed":BASELINE_SEED if mode=="random_opposite_tail" else None,
        "attempts":len(rows),"net_R":str(sum((row.base_R for row in rows),zero)),
        "stress_net_R":str(sum((row.stress_R for row in rows),zero)),
        "impact_failures":sum(cash.impact_failure is not None for row in rows for cash in row.fills),
        "ledger":ledger,"ledger_digest":hashlib.sha256(json.dumps(ledger,sort_keys=True,separators=(",", ":")).encode()).hexdigest(),
        "cohort_decisions":input_.disclosure["cohort_decisions"]}


def regime_labels(bars: Sequence[S.HourBar]) -> Mapping[int,str]:
    labels, seed = {}, []
    previous, ema = None, None
    for bar in sorted(bars,key=lambda row:row.open_ms):
        if previous is not None and bar.open_ms != previous+S.HOUR_MS:
            seed, ema = [], None
        previous = bar.open_ms
        close = float(bar.close)
        if not math.isfinite(close) or close<=0:
            seed, ema = [], None
            labels[bar.close_ms]="UNCLASSIFIED"
            continue
        if ema is None:
            seed.append(close)
            if len(seed)==200:
                ema=math.fsum(seed)/200
        else:
            ema=(2/201)*close+(199/201)*ema
        labels[bar.close_ms] = "UNCLASSIFIED" if ema is None else (
            "BTC_ABOVE_EMA200" if close>=ema else "BTC_BELOW_EMA200")
    return labels


def percentile(values: Sequence[Decimal], fraction: Decimal) -> Decimal:
    ordered=sorted(values)
    position=fraction*Decimal(len(ordered)-1)
    lower=int(position)
    upper=min(lower+1,len(ordered)-1)
    return ordered[lower]+(ordered[upper]-ordered[lower])*(position-lower)


def _pearson(left: Sequence[Decimal], right: Sequence[Decimal]) -> Decimal | None:
    if len(left)!=len(right) or len(left)<2:
        return None
    n=Decimal(len(left))
    lm,rm=sum(left,Decimal(0))/n,sum(right,Decimal(0))/n
    lv=sum(((v-lm)**2 for v in left),Decimal(0))
    rv=sum(((v-rm)**2 for v in right),Decimal(0))
    if lv<=0 or rv<=0:
        return None
    covariance=sum(((l-lm)*(r-rm) for l,r in zip(left,right)),Decimal(0))
    return covariance/(lv*rv).sqrt()


def _hourly_book_path(rows, marks, initial_equity: Decimal, *, timeline=None):
    prices={(mark.symbol,mark.close_ms):mark.price for mark in marks}
    events={}
    for row in rows:
        for cash in row.fills:
            events.setdefault(cash.fill.timestamp_ms,[]).append((row.attempt_id,cash))
        for funding in row.funding:
            events.setdefault(funding.point.nominal_ms,[]).append((row.attempt_id,funding))
    cash=initial_equity
    positions={}
    path=[]
    if timeline is None:
        timeline=sorted(set(events)|{timestamp for _,timestamp in prices})
    else:
        timeline=tuple(timeline)
        if any(right-left!=S.HOUR_MS for left,right in zip(timeline,timeline[1:])):
            raise S.IncompleteAuthority("report full-book timeline has a missing canonical hour")
        if set(events)-set(timeline):
            raise S.IncompleteAuthority("report events escape canonical full-book timeline")
    for timestamp in timeline:
        for attempt,item in events.get(timestamp,()):
            if hasattr(item,"fill"):
                fill=item.fill
                cash+=item.movement-item.fee-item.slippage-item.impact
                if fill.phase=="entry": positions[(attempt,fill.symbol)]=fill
                else: positions.pop((attempt,fill.symbol),None)
            else:
                cash+=item.cash
        equity=cash
        for (_,symbol),entry in positions.items():
            price=prices.get((symbol,timestamp))
            if price is None:
                raise S.IncompleteAuthority("report book correlation lacks exposure mark")
            equity+=Decimal(entry.transaction_side)*entry.quantity*(price-entry.reference)
        if equity<=0:
            raise S.IncompleteAuthority("report hourly book equity is non-positive")
        path.append((timestamp,equity))
    return path


def build_disclosures(input_, *, drawdown: Decimal, stress_drawdown: Decimal) -> dict:
    rows=input_.attempts
    decisions=input_.disclosure.get("cohort_decisions",())
    coverage=input_.disclosure.get("coverage",{})
    benchmark=input_.disclosure.get("benchmark_bars",())
    undefined=[]
    if not rows: undefined.extend(("mean_net_R","profit_factor","bootstrap_mean_R_95pct_interval",
                                  "holding_time_hours","cost_bps_of_initial_gross"))
    zero=Decimal(0)
    totals={name:zero for name in ("fees","slippage","impact","funding","net_R","stress_net_R")}
    stress_costs={name:zero for name in ("fees","slippage","impact","funding")}
    symbols,sides,months,regimes={},{},{},{}
    long_values,short_values=[],[]
    holding=[]
    imbalances=[]
    for row in rows:
        entries={cash.fill.symbol:cash.fill for cash in row.fills if cash.fill.phase=="entry"}
        sides_for_row={1:zero,-1:zero}
        for cash in row.fills:
            totals["fees"]+=cash.fee; totals["slippage"]+=cash.slippage; totals["impact"]+=cash.impact
            side=entries[cash.fill.symbol].transaction_side
            value=(cash.movement-cash.fee-cash.slippage-cash.impact)/row.initial_risk_cash
            sides_for_row[side]+=value
            symbols[cash.fill.symbol]=symbols.get(cash.fill.symbol,zero)+value
        for funding in row.funding:
            totals["funding"]+=funding.cash
            side=entries[funding.point.symbol].transaction_side
            value=funding.cash/row.initial_risk_cash
            sides_for_row[side]+=value
            symbols[funding.point.symbol]=symbols.get(funding.point.symbol,zero)+value
            stress_costs["funding"]+=funding.cash*Decimal("1.5") if funding.cash<0 else funding.cash/Decimal("1.5")
        for cash in row.stress_fills:
            stress_costs["fees"]+=cash.fee
            stress_costs["slippage"]+=cash.slippage
            stress_costs["impact"]+=cash.impact
        for side in (1,-1): sides[side]=sides.get(side,zero)+sides_for_row[side]
        long_values.append(sides_for_row[1]); short_values.append(sides_for_row[-1])
        month=datetime.fromtimestamp(row.entry_ms/1000,tz=timezone.utc).strftime("%Y-%m")
        months[month]=months.get(month,zero)+row.base_R
        regime=regimes.setdefault(row.regime,{"attempts":0,"net_R":zero})
        regime["attempts"]+=1; regime["net_R"]+=row.base_R
        totals["net_R"]+=row.base_R; totals["stress_net_R"]+=row.stress_R
        holding.append(Decimal(row.close_ms-row.entry_ms)/S.HOUR_MS)
        notionals={side:sum((abs(fill.quantity*fill.reference) for fill in entries.values()
                            if fill.transaction_side==side),zero) for side in (1,-1)}
        imbalances.append(abs(notionals[1]-notionals[-1])/row.initial_gross_cash)
    # Include zero-trade months in every fold's denominator.
    spans=coverage.get("segments",(coverage,))
    for span in spans:
        if "effective_start_ms" not in span: continue
        cursor=datetime.fromtimestamp(span["effective_start_ms"]/1000,tz=timezone.utc).replace(day=1)
        end=datetime.fromtimestamp(span["end_exclusive_ms"]/1000,tz=timezone.utc)
        while cursor<end:
            months.setdefault(cursor.strftime("%Y-%m"),zero)
            cursor=cursor.replace(year=cursor.year+(cursor.month==12),month=1 if cursor.month==12 else cursor.month+1)
    paired_corr=_pearson(long_values,short_values)
    if paired_corr is None: undefined.append("long_short_pair_return_correlation")
    btc={bar.close_ms:Decimal(str(bar.close)) for bar in benchmark}
    book_returns,btc_returns=[],[]
    for part in input_.disclosure.get("fold_inputs",(input_,)):
        initial=part.disclosure.get("initial_equity")
        if initial is None: continue
        span=part.disclosure.get("coverage",{})
        timeline=tuple(range(span["effective_start_ms"],span["end_exclusive_ms"]+S.HOUR_MS,S.HOUR_MS)) if span else None
        btc={bar.close_ms:Decimal(str(bar.close)) for bar in part.disclosure.get("benchmark_bars",())}
        if timeline is None or set(timeline)-set(btc):
            undefined.append("missing_canonical_benchmark_hour")
            continue
        path=_hourly_book_path(part.attempts,part.marks,initial,timeline=timeline)
        for (previous,pe),(timestamp,equity) in zip(path,path[1:]):
            if btc[previous]<=0 or btc[timestamp]<=0:
                raise S.IncompleteAuthority("report BTC benchmark price is non-positive")
            book_returns.append(equity/pe-1); btc_returns.append(btc[timestamp]/btc[previous]-1)
    corr=_pearson(book_returns,btc_returns)
    beta=None
    if len(btc_returns)>=2:
        mean_btc=sum(btc_returns,zero)/len(btc_returns)
        mean_book=sum(book_returns,zero)/len(book_returns)
        variance=sum(((value-mean_btc)**2 for value in btc_returns),zero)
        if variance>0:
            beta=sum(((a-mean_book)*(b-mean_btc) for a,b in zip(book_returns,btc_returns)),zero)/variance
    if corr is None: undefined.append("book_btc_return_correlation")
    if beta is None: undefined.append("btc_beta")
    chronological=sorted(rows,key=lambda row:(row.close_ms,row.attempt_id))
    cumulative=peak=zero
    peak_time=chronological[0].entry_ms if chronological else 0
    recovery=zero
    for row in chronological:
        cumulative+=row.base_R
        if cumulative>=peak:
            recovery=max(recovery,Decimal(row.close_ms-peak_time)/S.HOUR_MS)
            peak,peak_time=cumulative,row.close_ms
        else:
            recovery=max(recovery,Decimal(row.close_ms-peak_time)/S.HOUR_MS)
    ratio=totals["net_R"]/drawdown if drawdown>0 else None
    if ratio is None: undefined.append("return_drawdown_ratio")
    risk_label=("standard" if rows and drawdown<=15 else "elevated" if drawdown<=30 and ratio is not None and ratio>=Decimal("1.5")
                else "high" if drawdown<=60 and ratio is not None and ratio>=2 and stress_drawdown>0
                and totals["stress_net_R"]/stress_drawdown>=1 else "reject")
    skipped=[item for decision in decisions for item in decision["skipped_pairs"]]
    exchange=sum(item["reason"]=="EXCHANGE_INFEASIBLE" for item in skipped)
    proposed=len(rows)+len(skipped)
    schedules=tuple(decision["nominal_ms"] for decision in decisions)
    ledger=[{"attempt":row.attempt_id,"fills":[{"timestamp_ms":cash.fill.timestamp_ms,"symbol":cash.fill.symbol,
        "side":cash.fill.transaction_side,"phase":cash.fill.phase,"quantity":str(cash.fill.quantity),
        "reference_price":str(cash.fill.reference),"execution_price":str(cash.execution),
        "adverse_slippage_bps":str(Decimal(10000)*(cash.slippage+cash.impact)/abs(cash.fill.quantity*cash.fill.reference)),
        "fee_dollars":str(cash.fee),"slippage_dollars":str(cash.slippage),"impact_dollars":str(cash.impact),
        "impact_ratio":str(cash.impact_ratio),"impact_failure":cash.impact_failure} for cash in row.fills],
        "stress_fills":[{"timestamp_ms":cash.fill.timestamp_ms,"symbol":cash.fill.symbol,
            "phase":cash.fill.phase,"execution_price":str(cash.execution),"fee_dollars":str(cash.fee),
            "slippage_dollars":str(cash.slippage),"impact_dollars":str(cash.impact),
            "adverse_slippage_bps":str(Decimal(10000)*(cash.slippage+cash.impact)/abs(cash.fill.quantity*cash.fill.reference))}
            for cash in row.stress_fills],
        "funding":[{"symbol":item.point.symbol,"nominal_ms":item.point.nominal_ms,
            "rate":str(item.point.rate),"mark_price":str(item.mark.price),"cash":str(item.cash)} for item in row.funding]}
        for row in rows]
    return {"contract_sha256":S.CONTRACT_SHA256,"base_commit":S.CONTRACT_BASE,
        "status":"RESEARCH_EVIDENCE_DEFAULT_OFF","promotable":False,
        "leg_count":sum(len(row.symbols) for row in rows),"closed_paired_attempts":len(rows),
        "scheduled_cohorts":len(decisions),"eligible_cohorts":sum(d["eligible_symbols"]>=12 and d["reason"] is None for d in decisions),
        "admitted_cohorts":sum(bool(d["admitted_attempts"]) for d in decisions),
        "skipped_pairs":len(skipped),"zero_trade_cohorts":sum(not d["admitted_attempts"] for d in decisions),
        "skipped_cohorts":sum(bool(d["skipped_pairs"]) or d["reason"] is not None for d in decisions),
        "skipped_pair_reasons":dict(Counter(d["reason"] for d in skipped)),"cohort_decisions":decisions,
        "scheduled_event_hours":schedules,"scheduled_union_digest":hashlib.sha256(json.dumps(schedules,separators=(",", ":")).encode()).hexdigest(),
        "coverage":coverage,"net_R":str(totals["net_R"]),"stress_net_R":str(totals["stress_net_R"]),
        "fees_dollars":str(totals["fees"]),"slippage_dollars":str(totals["slippage"]),
        "impact_dollars":str(totals["impact"]),"funding_dollars":str(totals["funding"]),
        "stress_costs_dollars":{key:str(value) for key,value in stress_costs.items()},
        "fill_ledger":ledger,"ledger_digest":hashlib.sha256(json.dumps(ledger,sort_keys=True,separators=(",", ":")).encode()).hexdigest(),
        "cost_bps_of_initial_gross":str(Decimal(10000)*(totals["fees"]+totals["slippage"]+totals["impact"])/sum((row.initial_gross_cash for row in rows),zero)) if rows else None,
        "impact_failures":sum(cash.impact_failure is not None for row in rows for cash in row.fills),
        "atomicity_failures":sum(row.atomic_failure for row in rows),"holding_time_hours":str(sum(holding,zero)/len(holding)) if holding else None,
        "holding_time_range_hours":[str(min(holding)),str(max(holding))] if holding else None,
        "exchange_rejection_rate":str(Decimal(exchange)/proposed) if proposed else None,
        "long_short_return_correlation":str(paired_corr) if paired_corr is not None else None,
        "book_btc_return_correlation":str(corr) if corr is not None else None,"btc_beta":str(beta) if beta is not None else None,
        "book_correlation_hourly_observations":len(book_returns),
        "same_candle_cluster":max(Counter(row.entry_ms for row in rows).values(),default=0),
        "gross_imbalance":str(max(imbalances,default=zero)),"recovery_duration_hours":str(recovery),
        "return_drawdown_ratio":str(ratio) if ratio is not None else None,"risk_label":risk_label,
        "paper_risk_multiplier":str(min(Decimal(1),Decimal(15)/drawdown)) if drawdown>0 else "1",
        "months":{key:str(value) for key,value in sorted(months.items())},
        "sides":{"long_net_R":str(sides.get(1,zero)),"short_net_R":str(sides.get(-1,zero))},
        "symbol_net_R":{key:str(value) for key,value in sorted(symbols.items())},
        "distinct_symbols":len(symbols),
        "regimes":{key:{"attempts":value["attempts"],"net_R":str(value["net_R"])} for key,value in regimes.items()},
        "regime_specification":REGIME_SPECIFICATION,"regime_robustness_claimed":False,
        "undefined_statistics":undefined,"report_statistics_defined":not undefined}
