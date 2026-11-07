"""Own268 DEVELOPMENT-only CLI. Receipt absent; forward/holdout/PAPER locked."""
import argparse
from dataclasses import asdict,is_dataclass
from datetime import datetime
from decimal import Decimal
from fractions import Fraction
import json
import math
import os
from pathlib import Path
from zoneinfo import ZoneInfo
import statistics

from tools.research import c268_authority as A
from tools.research import c268_source as S
from tools.research import c268_signals as SIGNAL
from tools.research import c268_book as B
from tools.research import c268_reporting as R
from tools.research import c268_statistics as SCI


def evaluate(source):
    provider=SIGNAL.DecisionProvider(source)
    return evaluate_books(provider)


def evaluate_books(provider):
    source=provider.source
    primary_book=B.SideBook(provider).run();stress_book=B.SideBook(provider,stress=True).run(base=primary_book)
    primary,stress=R.report(primary_book),R.report(stress_book)
    diagnostics=[]
    for capital in(1000,3000,4000):
        book=B.SideBook(provider,capital).run();stressed=B.SideBook(provider,capital,stress=True).run(base=book)
        diagnostics.append({'capital':capital,'base':R.report(book),'stress':R.report(stressed)})
    values=[row['trade_R'] for row in sorted(primary['closed'],key=lambda r:(r['entry_ms'],r['symbol']))]
    inference=SCI.statistics(values,side=source.side) if values else None
    checks=R.development_checks(primary,stress)
    incomplete=not primary['data_complete'] or not stress['data_complete']
    verdict='DATA_INCOMPLETE' if incomplete else 'DEVELOPMENT_SURVIVOR_FORWARD_STILL_LOCKED' if all(checks.values()) else 'REJECT'
    fit_table=[];coverage=[]
    for t,frame in sorted(provider._frames.items()):
        coverage.append({'decision_ms':t,'entry_ms':t,'status':frame.status,'statistically_fitted_count':len(frame.fits),
            'positive_eligible_count':sum(f.positive_eligible for _,f in frame.fits),'indexed_proposals':[(p.index,p.fit.symbol) for p in frame.indexed],
            'history_ineligible':frame.history_ineligible,'undefined_fit_symbols':frame.undefined_symbols})
        for symbol,fit in frame.fits:
            native=source.native_candles(symbol)
            prior=tuple(float(native[u].quote_volume) for u in range(t-25*B.H,t-B.H,B.H))
            median=statistics.median(prior);decision_qv=float(native[t-B.H].quote_volume)
            fit_table.append({'decision_ms':t,'entry_ms':t,'symbol':symbol,'side':1,'beta_all':fit.beta_all,'beta_down':fit.beta_down,
                'score_beta_excess':fit.score,'downside_count':fit.downside_count,'positive_eligible':fit.positive_eligible,
                'index':next((p.index for p in frame.indexed if p.fit.symbol==symbol),None),
                'decision_quote_volume':decision_qv,'preceding_24h_median_quote_volume':median,
                'decision_liquidity_ratio':decision_qv/median if median else None,'liquidity_diagnostic_status':'ZERO_BASELINE' if not median else 'DEFINED',
                'decision_liquidity_gate':decision_qv>0 and decision_qv>=median,'window_identity_sha256':dict(frame.window_digests)[symbol]})
    return {'candidate_id':268,'side':source.side,'alpha_lineage':'INDEPENDENT_CONDITIONAL_DOWNSIDE_BETA_PREMIUM_LONG','proposal_sha256':S.C.PREREG_SHA256,
        'model':{'path':S.C.MODEL_PATH,'sha256':S.C.MODEL_SHA256,'conditional_research_model':True,'actual_testnet_fee_and_eligibility_ledger_required_before_final_qualification':True},
        'source_digests':dict(source.digests),'source_reads':source.source_reads,'source_gaps_unavailable_prefixes':source.known_gaps,
        'ordinary_window':[source.start_ms,source.end_ms],
        'window_cairo':[datetime.fromtimestamp(t/1000,ZoneInfo('Africa/Cairo')).isoformat() for t in(source.start_ms,source.end_ms)],
        'funding_only_old_liability_tail':{'exclusive_end_ms':source.funding_tail_exclusive_end_ms,
            'fixed_cash_observation_ms':source.end_ms+15000,'no_new_trade_price_mark_or_nextfold_cash':True},
        'economic_development_verdict':verdict,'development_gates':checks,'primary':primary,'stress':stress,
        'all_statistically_fit_proposals_once':fit_table,'source_fit_coverage_once':coverage,
        'fit_table_scope':'ALL statistically defined168return fits once, including nonpositive/nonindexed/skipped; no169price tapes. Minimum16 BEFORE positive eligibility. First3 indexed only, no replacement. Provider shared across isolated diagnostic capitals.',
        'stress_same_admitted_primary_quantity_frozen_risk_and_natural_funding':True,'diagnostic_isolated_capitals_nonselecting':diagnostics,
        'bootstrap':inference,'statistics_specification':{'draws':20000,'block_expected_trades':21,'side_seeds':SCI.SEEDS[source.side],
            'p_threshold':'.05/268','historical_global_variant_total':'UNKNOWN','global_FWER_claimed':False,
            'development_inference_is_diagnostic':True},
        'lifecycle_catalog':{'path':S.C.META.CATALOG_PATH,'sha256':S.C.META.CATALOG_SHA256,'complete_world':False,
            'kind':'DERIVED_CURRENT_OFFICIAL_OBSERVATION_EFFECTIVE_EVENTS_ONLY','original_announcement_asof_proven':False,
            'advance_horizon_veto_authorized':False,'settlement_execution_proof':None},
        'published_funding_completeness_model':[vars(p) for p in dict.fromkeys(source.published_funding_proofs)],
        'trial_ledger':{'candidate_id':268,'promotable_primary_trials':1,'current_scope_side':source.side,'primary_capital':2000,
            'diagnostic_capitals':[1000,3000,4000],'diagnostics_select_or_rescue_primary':False,'global_variant_total':'UNKNOWN'},
        'execution_authorized':False,'paper_qualified':False,'forward_authorized':False,'holdout_authorized':False,
        'report_readiness':{'own_DEV_source_and_economics_reported':True,'other_strategy_packets':'UNAVAILABLE_NO_AUTHORIZED_PACKET',
            'final_cohort_comparison_scope_and_actual_testnet_fee_lifecycle_evidence_pending':True},
        'ROADMAP FIT':'Own268 fixed conditional-downside-beta LONG economic development',
        'DEFERRED':'Forward/holdout grants, all PAPER/runtime protection/restart/rollback and live qualification'}


def jsonable(value):
    if is_dataclass(value):return jsonable(asdict(value))
    if isinstance(value,Decimal):return str(value)
    if isinstance(value,Fraction):return {'numerator':str(value.numerator),'denominator':str(value.denominator)}
    if isinstance(value,float) and not math.isfinite(value):return None
    if isinstance(value,dict):return {str(k):jsonable(v) for k,v in value.items()}
    if isinstance(value,(tuple,list)):return [jsonable(v) for v in value]
    return value


def main(argv=None):
    parser=argparse.ArgumentParser(description='Own268 locked side-development only; no source override')
    parser.add_argument('stage',choices=('development',));parser.add_argument('--side',choices=('long',),default='long')
    parser.add_argument('--out',type=Path,required=True);args=parser.parse_args(argv)
    side=1;out=args.out.resolve()
    if out.exists():raise S.C.C268Locked('C268_EXPORT_ALREADY_EXISTS_BEFORE_SPEND')
    out.parent.mkdir(parents=True,exist_ok=True);probe=out.parent/(out.name+'.preflight');created=False
    try:
        with probe.open('xb') as handle:created=True;handle.write(b'');handle.flush();os.fsync(handle.fileno())
    finally:
        if created:probe.unlink()
    with A.development_authority(side) as cap:
        canonical=cap.claim_path.parent/f'development-{side}.canonical.json'
        with canonical.open('xb') as handle:
            try:source=S.DevelopmentSource(cap)
            except (S.SourceIncomplete,S.P.C259Error,OSError,ValueError,KeyError) as exc:
                report={'candidate_id':268,'side':side,'economic_development_verdict':'DATA_INCOMPLETE',
                    'failure_before_book':str(exc),'known_exposure':[],'known_execution_costs':0,
                    'execution_authorized':False,'paper_qualified':False}
            else:
                try:provider=SIGNAL.DecisionProvider(source)
                except (S.SourceIncomplete,S.P.C259Error,B.K.ModelIncomplete,OSError,ValueError,KeyError) as exc:
                    report={'candidate_id':268,'side':side,'economic_development_verdict':'DATA_INCOMPLETE',
                        'failure_before_any_side_book':str(exc),'known_execution_costs':0,'known_exposure':[],
                        'execution_authorized':False,'paper_qualified':False}
                else:report=evaluate_books(provider)
            report.update(implementation_head=cap.implementation_head,receipt_sha256=cap.receipt_sha256,
                canonical_report_path=str(canonical),research_only=True)
            raw=(json.dumps(jsonable(report),sort_keys=True,separators=(',',':'),allow_nan=False)+'\n').encode()
            handle.write(raw);handle.flush();os.fsync(handle.fileno())
    with out.open('xb') as handle:handle.write(raw);handle.flush();os.fsync(handle.fileno())
    return 0


if __name__=='__main__':raise SystemExit(main())
