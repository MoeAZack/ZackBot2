"""C268 source-bound cash/R reporting; no old259 evidence-seal borrowing."""
from datetime import datetime,timedelta,timezone
from decimal import Decimal as D
from fractions import Fraction
import math
import numpy as np

from tools.research import c268_book as B


def drawdown(values):
    peak=worst=0.
    for value in values:peak=max(peak,float(value));worst=max(worst,peak-float(value))
    return worst


def correlation(left,right):
    if len(left)<2 or len(left)!=len(right) or np.var(left)<=0 or np.var(right)<=0:return None
    return float(np.corrcoef(left,right)[0,1])


def cash_profit_factor(values):
    positive=math.fsum(v for v in values if v>0);negative=-math.fsum(v for v in values if v<0)
    return positive/negative if negative>0 else None


def calendar_time(close,start,end):
    if not start<=close<=end:raise B.K.ModelIncomplete('C268_CLOSE_OUTSIDE_FIXED_WINDOW')
    return min(close,end-1)


def cash_components(lot):
    expected=[]
    for fill in lot.fills:
        if fill.phase!='ENTRY':expected.append(fill.movement_cash)
        expected.extend((-fill.fee_drag,-fill.slippage_drag,-fill.impact_drag))
    expected.extend(event['cash'] for event in lot.funding)
    return tuple(expected)


def report(book):
    start,end=book.source.start_ms,book.source.end_ms
    expected_by_attempt={lot.attempt_id:cash_components(lot) for lot in book.lots}
    expected=tuple(v for components in expected_by_attempt.values() for v in components)
    audit=book.cash.reconciliation(expected_components=expected)
    individual=all(book.cash._exact(components)==book.cash.exact_components(identifier) for identifier,components in expected_by_attempt.items())
    cash_ids={event.attempt_id for event in book.cash.events}
    owned_ids=set(expected_by_attempt)
    reconciled=(audit['component_identity_reconciled'] and audit['expected_component_total_reconciled'] and individual
        and cash_ids==owned_ids and len(owned_ids)==len(book.lots))
    all_rows=[];closed=[];integrity=[]
    for lot in book.lots:
        sizing=lot.sizing
        recomputed=B.K.size_reserved(symbol=lot.symbol,side=lot.side,entry=sizing.entry,stop=sizing.stop,filters=sizing.filters,
            side_book_equity=sizing.side_book_equity,entry_impact_asof_ms=sizing.entry_impact_asof_ms,
            entry_volume_rows=sizing.entry_volume_rows,reserve=sizing.reserve)
        if recomputed!=sizing:integrity.append(f'{lot.attempt_id}:sizing-not-frozen-firstpassing')
        for event in lot.funding:
            value=B.E.funding_cash(lot.side,float(sizing.quantity),float(event['mark_price']),float(event['raw_rate']))
            if (event['cash']!=value or event['raw_time_ms']>event['cash_applied_ms']
                or event['mark_open_ms']!=event['raw_time_ms']//B.H*B.H-B.H):integrity.append(f'{lot.attempt_id}:funding-source-clock-cash')
        net=float(book.cash.exact_components(lot.attempt_id));risk=float(sizing.initial_risk_cash)
        gross=math.fsum(fill.movement_cash for fill in lot.fills)
        row={'attempt_id':lot.attempt_id,'symbol':lot.symbol,'side':lot.side,'decision_ms':lot.decision_ms,'entry_ms':lot.entry_ms,
            'admission_ordinal':lot.admission_ordinal,'close_ms':lot.latest_exit_ms,'earliest_exit_ms':lot.earliest_exit_ms,
            'exit_kind':lot.exit_kind,'regime':lot.regime,'quantity':sizing.quantity,'entry_reference':sizing.entry,
            'stop_reference':sizing.stop,'target_reference':lot.target,'admission_equity':sizing.side_book_equity,
            'initial_risk_cash':sizing.initial_risk_cash,'requested_risk_cash':sizing.requested_risk_cash,
            'known_entry_adverse_per_unit':sizing.reserve.adverse_per_unit,'actual_net_cash':net,'trade_R':net/risk,
            'gross_cash':gross,'gross_R':gross/risk,'protection_failure':lot.protection_failure,
            'fee_drag':math.fsum(f.fee_drag for f in lot.fills),'slippage_drag':math.fsum(f.slippage_drag for f in lot.fills),
            'impact_drag':math.fsum(f.impact_drag for f in lot.fills),'funding_cash':math.fsum(v['cash'] for v in lot.funding),
            'fills':[vars(fill) for fill in lot.fills],'funding':lot.funding,
            'coefficients':{'beta_all':lot.fit.beta_all,'beta_down':lot.fit.beta_down,'score_beta_excess':lot.fit.score,
                'downside_count':lot.fit.downside_count,'window_identity':dict(book.provider._frames[lot.decision_ms].window_digests)[lot.symbol]}}
        all_rows.append(row)
        if not lot.active:
            row['calendar_ownership_ms']=calendar_time(lot.latest_exit_ms,start,end)
            row['recorded_holding_ms']=lot.latest_exit_ms-lot.entry_ms;closed.append(row)
    closed.sort(key=lambda r:(r['close_ms'],r['symbol'],r['attempt_id']))
    cash=[r['actual_net_cash'] for r in closed];rs=[r['trade_R'] for r in closed]
    pf=cash_profit_factor(cash)
    total=0.;cumulative=[]
    for value in rs:total=math.fsum((total,value));cumulative.append(total)
    closed_dd=drawdown(cumulative)
    marked_dd=drawdown(v['equity']-book.capital for v in book.snapshots)/(.01*book.capital)
    gate_dd=max(closed_dd,marked_dd)
    start_dt=datetime.fromtimestamp(start/1000,timezone.utc);end_dt=datetime.fromtimestamp(end/1000,timezone.utc)
    month=start_dt.replace(day=1);months={};week=(start_dt-timedelta(days=start_dt.weekday())).replace(hour=0,minute=0,second=0,microsecond=0);weeks={}
    while month<end_dt:
        months[month.strftime('%Y-%m')]={'closed':0,'net_R':0.,'net_cash':0.}
        month=month.replace(year=month.year+1,month=1) if month.month==12 else month.replace(month=month.month+1)
    while week<end_dt:weeks[week.isoformat()]={'closed':0,'net_R':0.};week+=timedelta(days=7)
    by_symbol={};regimes={label:[] for label in('BTC_ABOVE_OR_ON_EMA200','BTC_BELOW_EMA200','UNCLASSIFIED')}
    for row in closed:
        timestamp=datetime.fromtimestamp(row['calendar_ownership_ms']/1000,timezone.utc)
        key=timestamp.strftime('%Y-%m');months[key]['closed']+=1;months[key]['net_R']+=row['trade_R'];months[key]['net_cash']+=row['actual_net_cash']
        key=(timestamp-timedelta(days=timestamp.weekday())).replace(hour=0,minute=0,second=0,microsecond=0).isoformat()
        weeks[key]['closed']+=1;weeks[key]['net_R']+=row['trade_R']
        by_symbol[row['symbol']]=by_symbol.get(row['symbol'],0.)+row['trade_R'];regimes.setdefault(row['regime'],[]).append(row)
    regime_table=[]
    for label,rows in regimes.items():
        regime_table.append({'label':label,'closed':len(rows),'net_R':math.fsum(r['trade_R'] for r in rows),
            'net_cash':math.fsum(r['actual_net_cash'] for r in rows),'fee':math.fsum(r['fee_drag'] for r in rows),
            'slippage':math.fsum(r['slippage_drag'] for r in rows),'impact':math.fsum(r['impact_drag'] for r in rows),
            'funding':math.fsum(r['funding_cash'] for r in rows)})
    hours={p['timestamp_ms']:p for p in book.snapshots if p['phase']=='HOUR_END'}
    returns=[];btc_returns=[];gaps=[]
    try:benchmark=book.source.native_candles('BTCUSDT')
    except (B.S.SourceIncomplete,B.S.C.C268Locked,B.E.C259Error,OSError,ValueError):benchmark={}
    last=min(end,book.last_boundary or start)
    for t in range(start+B.H,last+B.H,B.H):
        if (t-B.H not in hours or t not in hours or t-B.H not in benchmark
            or t>start+B.H and t-2*B.H not in benchmark or hours[t-B.H]['equity']<=0):
            gaps.append(t);continue
        reference=benchmark[t-B.H].open if t==start+B.H else benchmark[t-2*B.H].close
        returns.append(hours[t]['equity']/hours[t-B.H]['equity']-1)
        btc_returns.append(float(benchmark[t-B.H].close/reference-1))
    corr=correlation(returns,btc_returns) if not gaps else None
    beta=float(np.cov(returns,btc_returns,ddof=0)[0,1]/np.var(btc_returns)) if corr is not None else None
    unknown_marks=[];current_marks={}
    for symbol in book.active:
        try:point=book.source.native_marks(symbol).get((book.last_boundary or start)//B.H*B.H-B.H)
        except (B.S.SourceIncomplete,B.S.C.C268Locked,B.E.C259Error,OSError,ValueError):point=None
        if point is None:unknown_marks.append(symbol)
        else:current_marks[symbol]=point.close
    current_equity=None if unknown_marks else book.cash.mark_equity(current_marks)
    peak=float(book.capital);peak_time=start;recovery=0
    for sample in book.snapshots:
        if sample['equity']>=peak:peak=sample['equity'];peak_time=sample['timestamp_ms']
        else:recovery=max(recovery,sample['timestamp_ms']-peak_time)
    return {'side':book.side,'capital':book.capital,'stress':book.stress,'closed':closed,'all_known_attempts':all_rows,
        'closed_count':len(closed),'symbols':len(by_symbol),'net_cash':math.fsum(cash),'net_R':math.fsum(rs),
        'mean_R':math.fsum(rs)/len(rs) if rs else None,'profit_factor':pf,'profit_factor_basis':'SUM_POSITIVE_CASH/ABS_SUM_NEGATIVE_CASH; undefined_without_losses',
        'gross_R':math.fsum(r['gross_R'] for r in closed),'win_rate':sum(v>0 for v in cash)/len(cash) if cash else None,
        'fee_drag':math.fsum(r['fee_drag'] for r in closed),'slippage_drag':math.fsum(r['slippage_drag'] for r in closed),
        'impact_drag':math.fsum(r['impact_drag'] for r in closed),'funding_cash':math.fsum(r['funding_cash'] for r in closed),
        'closed_realized_R_drawdown_R':closed_dd,'chronological_marked_equity_drawdown_R':marked_dd,'gate_drawdown_R':gate_dd,
        'drawdown_recovery_ms':recovery,'return_drawdown_ratio':math.fsum(rs)/gate_dd if gate_dd>0 else None,
        'monthly_including_zero':months,'weekly_including_zero':weeks,'symbol_net_R':by_symbol,'regime_table':regime_table,
        'cash_reconciled':reconciled,'cash_audit':audit,'expected_cash_components_from':'ALL actual lot.fills and lot.funding, including OPEN/PROTECTION_FAILURE; independently compared per attempt and global',
        'exact_cash_attempt_identity_set_matches_lots':cash_ids==owned_ids and len(owned_ids)==len(book.lots),
        'known_current_cash':book.cash.settled_cash,'known_current_cash_exact':book.cash.settled_cash_exact,
        'current_marked_equity':current_equity,'current_mark_prices':current_marks,'unknown_current_marks':unknown_marks,
        'unresolved_exposure':[r for r in all_rows if r['close_ms'] is None],
        'data_complete':book.complete and not book.active and reconciled and not integrity,'failures':book.failures,'integrity_failures':integrity,
        'decisions':book.decisions,'scheduled':len(book.decisions),'eligible':sum(v['eligible']>0 for v in book.decisions),
        'admitted':len(book.lots),'skipped':sum(len(v['skipped']) for v in book.decisions),'zero_trade':sum(not v['admitted'] for v in book.decisions),
        'hourly_equity':list(hours.values()),'phase_equity':book.snapshots,'cash_events':[vars(e) for e in book.cash.events],
        'book_BTC_correlation':corr,'book_BTC_beta':beta,'BTC_alignment_complete':not gaps,'BTC_interval_gaps':gaps,
        'required_report_complete':not gaps and corr is not None and beta is not None,
        'full_book_hourly_return_count':len(returns),'fraction_hours_exposed':sum(v['active_count']>0 for v in hours.values())/len(hours) if hours else None,
        'max_concurrent_positions':max((v['active_count'] for v in hours.values()),default=0),
        'cadence_diagnostics':book.observer.cadence_diagnostics,
        'source_coefficient_coverage':'SEE_TOP_LEVEL_ONCE_PER_SOURCE_PROVIDER_TABLE',
        'comparison_packets':{'BOL_DSR_C258':'UNAVAILABLE_NO_AUTHORIZED_OTHER_STRATEGY_PACKET; not zero/no invented comparison'}}


def development_checks(base,stress):
    counts=tuple(v['closed'] for v in base['monthly_including_zero'].values())
    checks={'pre_cost_gross_expectancy_positive':base['closed_count']>0 and base['gross_R']/base['closed_count']>0,
        'mean_after_cost_R_positive':base['mean_R'] is not None and base['mean_R']>0,
        'profit_factor_gt_1_05':base['profit_factor'] is not None and base['profit_factor']>1.05,
        'closed_trades_gte_120':base['closed_count']>=120,'symbols_gte_8':base['symbols']>=8,
        'median_monthly_closed_gte_8':bool(counts) and B.E.statistics.median(counts)>=8,
        'stress_mean_R_positive':stress['mean_R'] is not None and stress['mean_R']>0,
        'gate_drawdown_R_lte_60':base['gate_drawdown_R']<=60,
        'complete':base['data_complete'] and stress['data_complete']
            and base.get('required_report_complete',False) and stress.get('required_report_complete',False)}
    return checks
