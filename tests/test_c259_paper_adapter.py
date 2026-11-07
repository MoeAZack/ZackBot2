"""Synthetic in-memory C259 proposals only; no data/economic files or venue use."""
from dataclasses import FrozenInstanceError,replace
from decimal import Decimal
from datetime import datetime,timezone
import math
import socket

import numpy as np
import pytest
from tools.research import c259_paper_adapter as A
from newcore.runtime.config import RuntimeConfig,RuntimeEnvironment,AccountSpec

D=Decimal
H=A.E.HOUR_MS


def fixture(side=1):
    start=int(datetime(2025,1,1,tzinfo=timezone.utc).timestamp()*1000)
    decision=start+722*H;entry=decision+H
    btc_returns=np.random.Generator(np.random.PCG64(25911)).normal(0,.0008,723)
    btc_returns[721]=side*.012
    def bars(returns):
        price=100.0;out=[]
        for hour,rate in enumerate(returns):
            open_=round(price,8);price*=math.exp(float(rate));close=round(price,8)
            out.append(A.E.Bar(start+hour*H,open_,round(max(open_,close)*1.002,8),
                round(min(open_,close)*.998,8),close,100_000_000))
        return tuple(out)
    benchmark=bars(btc_returns)
    symbols=[]
    for index,symbol in enumerate(("AAAUSDT","BBBUSDT","CCCUSDT","DDDUSDT")):
        noise=np.random.Generator(np.random.PCG64(index+17)).normal(0,.00004,723)
        tape=bars(.2*btc_returns+(.9-index*.1)*np.roll(btc_returns,1)+noise)
        quote=D(str(tape[-1].close))
        mark=A.Mark(entry-1,quote,A.digest(("mark",symbol)))
        symbols.append(A.SymbolSnapshot(symbol,tape,A.E.Filters(D("0.00000001"),D("0.001"),D("0.001"),D("100000"),D(1)),
            entry,A.digest(("filters",symbol)),quote,entry,entry,mark,A.digest(("market",symbol)),
            A.QuoteObservation(entry,entry,123,quote if side==-1 else quote-D('0.00000001'),
                               quote if side==1 else quote+D('0.00000001'),quote,A.digest(('actual quote',symbol)))))
    stamp=datetime.fromtimestamp(decision/1000,tz=timezone.utc)
    monday=int(stamp.replace(hour=0,minute=0,second=0).timestamp()*1000)-stamp.weekday()*24*H
    cell=A.PITCell(monday,monday,("DDDUSDT","BBBUSDT","AAAUSDT","CCCUSDT","BTCUSDT"),A.digest("PIT"))
    cohort=A.CohortSnapshot(decision,entry,entry,3,start,benchmark,tuple(reversed(symbols)),(cell,),
        A.digest("BTC"),A.digest("funding"),entry,True,123)
    account=A.digest("test account identity only")
    environment=A.PaperEnvironment(account,entry,3,confirmed_testnet=True,reconciliation_complete=True,observed_mono=123)
    book=A.SideBook(side,A.side_book_id(side),account,entry,3,D(2000),reconciled=True,actual_costs_complete=True,
        source_sha256=A.digest("isolated book state"),observed_mono=123)
    shared=A.SharedCapacity(account,entry,3,D(10000),D(10000),8,True,
        (A.C259SideCapacity(side,A.digest(book),D(2000),0,D(0)),
         A.C259SideCapacity(-side,A.digest(("opposite book",-side)),D(2000),0,D(0))),True,123)
    accepted=A.EconomicAcceptance(side,A.digest("synthetic historical PASS attestation"),True,True,True,True,True)
    runtime=RuntimeConfig(environment=RuntimeEnvironment.TESTNET,
        accounts=(AccountSpec(account_id='acct_'+format(1,'032x'),aggregate_id='pf_'+format(1,'032x'),
                              account_dir='synthetic-unused-c259',egress_id='synthetic'),))
    return dict(cohort=cohort,book=book,acceptance=accepted,environment=environment,shared=shared,
                now_ms=entry,now_mono_ms=123,config=A.AdapterConfig(enabled=True,runtime_config=runtime,
                    protected_runtime_config_sha256=A.digest(runtime)))


def test_default_off_precedes_any_signal_computation(monkeypatch):
    inputs=fixture()
    inputs["config"]=A.AdapterConfig()
    monkeypatch.setattr(A.E,"fit_signal",lambda *a,**k:pytest.fail("disabled code evaluated signals"))
    result=A.build_intents(**inputs)
    assert result.rejection=="DEFAULT_OFF" and not result.intents
    assert not result.activation_performed and not result.runtime_installed


@pytest.mark.parametrize("side",[1,-1])
def test_causal_intents_reuse_fixed_model_and_bound_paper_risk(side):
    inputs=fixture(side)
    result=A.build_intents(**inputs)
    assert result.rejection is None and len(result.intents)==2
    assert all(intent.side==side for intent in result.intents)
    assert result.intents[0].symbol=="AAAUSDT"
    assert all(intent.scheduled_exit_ms==intent.entry_due_ms+12*H for intent in result.intents)
    assert all(intent.decision_candle_close_ms==inputs["cohort"].decision_ms-1 for intent in result.intents)
    assert all(intent.modeled_risk_cash<=intent.side_book_equity*D("0.0025") for intent in result.intents)
    assert sum((intent.modeled_risk_cash for intent in result.intents),D(0))<=result.intents[-1].side_book_equity*D("0.005")
    assert result.intents[-1].side_book_equity<result.intents[0].side_book_equity
    assert all((intent.stop<intent.entry_reference<intent.target if side==1 else intent.target<intent.entry_reference<intent.stop)
               for intent in result.intents)
    assert all(intent.quantity%inputs["cohort"].symbols[0].filters.step==0 for intent in result.intents)
    assert all(not intent.execution_authorized and intent.requires_atomic_shared_reservation for intent in result.intents)
    assert result.intents[0].source_digests
    assert A.build_intents(**inputs).intents==result.intents
    with pytest.raises(FrozenInstanceError):result.intents[0].quantity=D(999)


@pytest.mark.parametrize("field",["development_pass","forward_pass","holdout_pass","independent_exact_head_pass","report_acceptance_pass"])
def test_unqualified_side_is_rejected(field):
    inputs=fixture();inputs["acceptance"]=replace(inputs["acceptance"],**{field:False})
    result=A.build_intents(**inputs)
    assert not result.intents and result.rejection=="UNQUALIFIED_SIDE_ECONOMICS"


@pytest.mark.parametrize("changes",[{"live":True},{"endpoint":"https://fapi.binance.com"},{"confirmed_testnet":False},
                                    {"mode":"LIVE"},{"reconciliation_complete":False}])
def test_unconfirmed_or_mainnet_environment_is_rejected(changes):
    inputs=fixture();inputs["environment"]=replace(inputs["environment"],**changes)
    assert A.build_intents(**inputs).rejection=="UNCONFIRMED_PAPER_TESTNET_ENVIRONMENT"


def test_stale_snapshot_and_mixed_book_generation_are_rejected():
    inputs=fixture();cohort=inputs["cohort"]
    assert A.build_intents(**{**inputs,"cohort":replace(cohort,observed_ms=cohort.observed_ms-H)}).rejection=="STALE_OR_MIXED_EVIDENCE_GENERATION"
    assert A.build_intents(**{**inputs,"book":replace(inputs["book"],generation=2)}).rejection=="STALE_OR_MIXED_EVIDENCE_GENERATION"
    assert A.build_intents(**{**inputs,"now_ms":inputs["now_ms"]+H}).rejection=="STALE_FUTURE_OR_BACKDATED_EXECUTION_TIMING"


def test_missing_or_future_closed_bar_never_reaches_ranking():
    inputs=fixture();cohort=inputs["cohort"]
    bad=replace(cohort.symbols[0],bars=cohort.symbols[0].bars[:-2]+cohort.symbols[0].bars[-1:])
    assert A.build_intents(**{**inputs,"cohort":replace(cohort,symbols=(bad,*cohort.symbols[1:]))}).rejection=="INCOMPLETE_OR_NONCAUSAL_CLOSED_BAR_PREFIX"
    future=replace(cohort.btc_bars[-1],open_ms=cohort.entry_open_ms)
    assert A.build_intents(**{**inputs,"cohort":replace(cohort,btc_bars=(*cohort.btc_bars,future))}).rejection=="INCOMPLETE_OR_NONCAUSAL_CLOSED_BAR_PREFIX"


def test_current_pit_cell_is_required_and_future_members_are_not_promoted():
    inputs=fixture();cohort=inputs["cohort"];cell=cohort.pit_cells[0]
    stale=replace(cell,monday_ms=cell.monday_ms-7*24*H,available_ms=cell.available_ms-7*24*H)
    assert A.build_intents(**{**inputs,"cohort":replace(cohort,pit_cells=(stale,))}).rejection=="STALE_INCOMPLETE_OR_FUTURE_PIT_AUTHORITY"
    late=replace(cell,available_ms=cohort.decision_ms+1)
    assert A.build_intents(**{**inputs,"cohort":replace(cohort,pit_cells=(late,))}).rejection=="STALE_INCOMPLETE_OR_FUTURE_PIT_AUTHORITY"
    future=replace(cell,monday_ms=cell.monday_ms+7*24*H,available_ms=cell.available_ms+7*24*H,members=("FUTUREUSDT",))
    baseline=A.build_intents(**inputs)
    changed=A.build_intents(**{**inputs,"cohort":replace(cohort,pit_cells=(cell,future))})
    assert [(i.symbol,i.quantity,i.stop,i.target) for i in changed.intents]==[(i.symbol,i.quantity,i.stop,i.target) for i in baseline.intents]


def test_post_decision_close_does_not_enter_signal_or_atr():
    inputs=fixture();cohort=inputs["cohort"]
    def change(tape):
        return (*tape[:-1],replace(tape[-1],high=tape[-1].high*2,low=tape[-1].low/2,close=tape[-1].close*1.5))
    changed=replace(cohort,btc_bars=change(cohort.btc_bars),symbols=tuple(replace(row,bars=change(row.bars)) for row in cohort.symbols))
    left=A.build_intents(**inputs);right=A.build_intents(**{**inputs,"cohort":changed})
    assert [(i.symbol,i.quantity,i.stop,i.target,i.regime) for i in left.intents]==[(i.symbol,i.quantity,i.stop,i.target,i.regime) for i in right.intents]


def test_ranked_infeasible_symbol_skips_without_reordering_or_other_side():
    inputs=fixture();cohort=inputs["cohort"]
    changed=tuple(replace(row,filters=replace(row.filters,min_notional=D("1000000"))) if row.symbol=="AAAUSDT" else row for row in cohort.symbols)
    result=A.build_intents(**{**inputs,"cohort":replace(cohort,symbols=changed)})
    assert result.rejection is None and len(result.intents)==2
    assert [i.symbol for i in result.intents]==["BBBUSDT","CCCUSDT"]
    assert [i.original_rank for i in result.intents]==[2,3]
    assert result.skips[0][0]=="AAAUSDT"


def test_capacity_and_side_books_remain_isolated_from_wallet_or_opposite_side():
    inputs=fixture();base=A.build_intents(**inputs)
    richer=replace(inputs["shared"],available_collateral_cash=D("1000000"),available_gross_notional=D("1000000"))
    assert [(i.symbol,i.quantity) for i in A.build_intents(**{**inputs,"shared":richer}).intents]==[(i.symbol,i.quantity) for i in base.intents]
    badbook=replace(inputs["book"],settled_cash=D("1000000"))
    assert A.build_intents(**{**inputs,"book":badbook}).rejection=="UNVERIFIED_ISOLATED_PRIMARY_CAPITAL"
    opposite=replace(inputs["book"],side=-1,book_id=A.side_book_id(-1))
    assert A.build_intents(**{**inputs,"book":opposite}).rejection=="UNQUALIFIED_SIDE_ECONOMICS"
    poor=replace(inputs["shared"],available_collateral_cash=D(1))
    result=A.build_intents(**{**inputs,"shared":poor})
    assert not result.intents and all(reason=="INSUFFICIENT_SHARED_CAPACITY" for _,reason in result.skips)


def test_existing_position_and_pending_recovery_are_not_duplicated():
    inputs=fixture();first=A.build_intents(**inputs).intents[0]
    row=next(row for row in inputs["cohort"].symbols if row.symbol==first.symbol)
    position=A.OpenPosition(first.intent_id,first.symbol,1,first.quantity,first.entry_reference,first.modeled_risk_cash,row.entry_mark,True)
    book=replace(inputs["book"],positions=(position,))
    shared=replace(inputs["shared"],c259_side_capacity=tuple(
        replace(row,book_sha256=A.digest(book),position_and_reservation_count=1,
                open_and_reserved_risk_cash=first.modeled_risk_cash) if row.side==book.side else row
        for row in inputs["shared"].c259_side_capacity))
    result=A.build_intents(**{**inputs,"book":book,"shared":shared})
    assert len(result.intents)==1 and result.intents[0].symbol!=first.symbol
    assert (first.symbol,"ALREADY_OCCUPIED") in result.skips
    pending=replace(inputs["book"],pending_intent_ids=(first.intent_id,))
    assert A.build_intents(**{**inputs,"book":pending}).rejection=="SIDE_BOOK_RECONCILIATION_REQUIRED"
    unsafe=replace(book,positions=(replace(position,protection_confirmed=False),))
    assert A.build_intents(**{**inputs,"book":unsafe}).rejection=="UNKNOWN_OR_UNPROTECTED_SIDE_POSITION"


def test_stale_mark_and_incomplete_funding_are_rejected():
    inputs=fixture();cohort=inputs["cohort"];row=cohort.symbols[0]
    stale=replace(row,entry_mark=replace(row.entry_mark,close_time_ms=cohort.entry_open_ms))
    assert A.build_intents(**{**inputs,"cohort":replace(cohort,symbols=(stale,*cohort.symbols[1:]))}).rejection=="STALE_OR_UNVERIFIED_MARK"
    assert A.build_intents(**{**inputs,"cohort":replace(cohort,funding_complete=False)}).rejection=="INCOMPLETE_FUNDING_OR_BENCHMARK_AUTHORITY"


def test_no_file_or_network_reads_and_native_mapping_is_honest(monkeypatch):
    inputs=fixture()
    with monkeypatch.context() as patch:
        patch.setattr(socket,"create_connection",lambda *a,**k:pytest.fail("network requested"))
        patch.setattr("builtins.open",lambda *a,**k:pytest.fail("file requested"))
        result=A.build_intents(**inputs)
    assert result.intents
    gap=A.native_integration_gap()
    assert gap["installed"] is False and "EntrySource.candidates" in gap["entry_seam"]
    assert "NC-07" in gap["management_gap"] and "DecisionKey" in gap["durability_seam"]


def test_native_key_mapping_uses_canonical_boundary_not_raw_binance_close():
    inputs=fixture();intent=A.build_intents(**inputs).intents[0]
    fields=A.native_decision_key_fields(intent)
    assert fields["candle_close_ms"]==inputs["cohort"].decision_ms
    assert fields["candle_close_ms"]%H==0 and intent.decision_candle_close_ms==fields["candle_close_ms"]-1
    assert fields["side"]=="LONG" and fields["purpose"]=="entry" and fields["tf"]=="1h"


def test_all_c259_reservations_are_mandatory_complete_and_same_generation():
    inputs=fixture();shared=inputs["shared"]
    for bad in (replace(shared,c259_reservations_complete=False),
                replace(shared,c259_side_capacity=()),
                replace(shared,c259_side_capacity=shared.c259_side_capacity[:1])):
        assert A.build_intents(**{**inputs,"shared":bad}).rejection=="INCOMPLETE_ALL_C259_RESERVATIONS"
    assert A.build_intents(**{**inputs,"shared":replace(shared,generation=2)}).rejection=="STALE_OR_MIXED_EVIDENCE_GENERATION"
    mismatched=replace(shared,c259_side_capacity=tuple(
        replace(row,book_sha256=A.digest("other snapshot")) if row.side==1 else row
        for row in shared.c259_side_capacity))
    assert A.build_intents(**{**inputs,"shared":mismatched}).rejection=="C259_RESERVATION_BOOK_MISMATCH"


@pytest.mark.parametrize("side",[1,-1])
def test_opposite_side_open_or_pending_reservations_consume_candidate_count(side):
    inputs=fixture(side);shared=inputs["shared"]
    rows=tuple(replace(row,position_and_reservation_count=2,open_and_reserved_risk_cash=D(8))
               if row.side!=side else row for row in shared.c259_side_capacity)
    result=A.build_intents(**{**inputs,"shared":replace(shared,c259_side_capacity=rows)})
    assert not result.intents and result.rejection is None
    assert all(reason=="PAPER_POSITION_CAP" for _,reason in result.skips)
    rows=tuple(replace(row,position_and_reservation_count=1,open_and_reserved_risk_cash=D(4))
               if row.side!=side else row for row in shared.c259_side_capacity)
    result=A.build_intents(**{**inputs,"shared":replace(shared,c259_side_capacity=rows)})
    assert len(result.intents)==1
    assert result.intents[0].side==side and result.intents[0].side_book_equity==D(2000)


def test_cross_side_risk_is_normalized_to_each_isolated_book_and_costs():
    inputs=fixture();shared=inputs["shared"]
    # Opposite book has lost equity: 3 cash risk / (1000 * 1%) = 0.30R.
    rows=tuple(replace(row,marked_equity=D(1000),position_and_reservation_count=1,
                       open_and_reserved_risk_cash=D(3)) if row.side==-1 else row
               for row in shared.c259_side_capacity)
    result=A.build_intents(**{**inputs,"shared":replace(shared,c259_side_capacity=rows)})
    assert len(result.intents)==1
    intent=result.intents[0]
    assert intent.quantity<A.build_intents(**inputs).intents[0].quantity
    assert D("0.30")+intent.modeled_risk_cash/((intent.side_book_equity-intent.estimated_entry_drag_reserve)*D("0.01"))<=D("0.50")
    full=tuple(replace(row,open_and_reserved_risk_cash=D(5)) if row.side==-1 else row for row in rows)
    result=A.build_intents(**{**inputs,"shared":replace(shared,c259_side_capacity=full)})
    assert not result.intents and all(reason=="PAPER_OPEN_RISK_CAP" for _,reason in result.skips)


def test_first_side_proposals_reserved_before_other_side_block_four_position_escape():
    long_inputs=fixture(1);long_batch=A.build_intents(**long_inputs)
    assert len(long_batch.intents)==2
    short_inputs=fixture(-1)
    # Native atomic reservation must publish this refreshed all-C259 snapshot
    # before asking the opposite source for proposals. This helper sends none.
    reserved=replace(short_inputs["shared"],c259_side_capacity=tuple(
        replace(row,book_sha256=A.digest(long_inputs["book"]),
                marked_equity=D(2000)-sum((intent.estimated_entry_drag_reserve for intent in long_batch.intents),D(0)),
                position_and_reservation_count=2,
                open_and_reserved_risk_cash=sum((intent.modeled_risk_cash for intent in long_batch.intents),D(0)))
        if row.side==1 else row for row in short_inputs["shared"].c259_side_capacity))
    assert not A.build_intents(**{**short_inputs,"shared":reserved}).intents


def test_reservation_cannot_omit_known_local_exposure():
    inputs=fixture();intent=A.build_intents(**inputs).intents[0]
    snapshot=next(row for row in inputs["cohort"].symbols if row.symbol==intent.symbol)
    position=A.OpenPosition(intent.intent_id,intent.symbol,1,intent.quantity,intent.entry_reference,
                            intent.modeled_risk_cash,snapshot.entry_mark,True)
    book=replace(inputs["book"],positions=(position,))
    # The collector's zero-risk/count snapshot no longer matches this local book.
    shared=replace(inputs["shared"],c259_side_capacity=tuple(
        replace(row,book_sha256=A.digest(book)) if row.side==1 else row
        for row in inputs["shared"].c259_side_capacity))
    assert A.build_intents(**{**inputs,"book":book,"shared":shared}).rejection=="C259_RESERVATION_BOOK_MISMATCH"


@pytest.mark.parametrize("origin",A.APPROVED_TESTNET_ORIGINS)
def test_one_approved_origin_is_pinned_without_fallback(origin):
    inputs=fixture()
    inputs["config"]=replace(inputs["config"],testnet_origin=origin)
    inputs["environment"]=replace(inputs["environment"],endpoint=origin)
    assert A.build_intents(**inputs).intents
    other=next(item for item in A.APPROVED_TESTNET_ORIGINS if item!=origin)
    inputs["environment"]=replace(inputs["environment"],endpoint=other)
    assert A.build_intents(**inputs).rejection=="UNCONFIRMED_PAPER_TESTNET_ENVIRONMENT"
    inputs["config"]=replace(inputs["config"],testnet_origin="https://fapi.binance.com")
    inputs["environment"]=replace(inputs["environment"],endpoint="https://fapi.binance.com")
    assert A.build_intents(**inputs).rejection=="UNCONFIRMED_PAPER_TESTNET_ENVIRONMENT"


def observed_inputs(delay_ms=1,prepare_delay_ms=0,quote_shift=D(0)):
    """Preserve canonical bars/open; stamp actual collector and preparation times."""
    inputs=fixture();event=inputs['cohort'].entry_open_ms
    observed=event+delay_ms;mono=123+delay_ms
    book=replace(inputs['book'],observed_ms=observed,observed_mono=mono)
    shared=replace(inputs['shared'],observed_ms=observed,observed_mono=mono,
        c259_side_capacity=tuple(replace(row,book_sha256=A.digest(book)) if row.side==book.side else row
                                for row in inputs['shared'].c259_side_capacity))
    symbols=tuple(replace(row,entry_available_ms=observed,filters_observed_ms=observed,
        quote=replace(row.quote,event_ms=observed,observed_ms=observed,observed_mono=mono,
                      bid=row.quote.bid+quote_shift,ask=row.quote.ask+quote_shift,mark=row.quote.mark+quote_shift,
                      source_sha256=A.digest(('actual delayed quote',row.symbol,observed,str(quote_shift)))))
        for row in inputs['cohort'].symbols)
    inputs.update(book=book,shared=shared,cohort=replace(inputs['cohort'],observed_ms=observed,
        observed_mono=mono,symbols=symbols),environment=replace(inputs['environment'],observed_ms=observed,
        observed_mono=mono),now_ms=observed+prepare_delay_ms,now_mono_ms=mono+prepare_delay_ms)
    return inputs


def test_real_plus_one_ms_clock_envelope_preserves_canonical_signal_and_actual_quote():
    inputs=observed_inputs(quote_shift=D('0.00000001'))
    result=A.build_intents(**inputs)
    assert result.intents and result.rejection is None
    p=result.intents[0];event=inputs['cohort'].entry_open_ms
    assert p.entry_due_ms==event and p.scheduled_exit_ms==event+12*H
    assert p.collector_observed_ms==p.prepared_ms==event+1
    assert p.collector_observed_mono==p.prepared_mono==124
    assert p.dispatch_entry_reference==p.entry_reference+D('0.00000001')
    assert p.gross_notional==p.quantity*p.dispatch_entry_reference
    assert p.runtime_config_sha256==inputs['config'].protected_runtime_config_sha256
    assert p.intent_id==A.build_intents(**fixture()).intents[0].intent_id


@pytest.mark.parametrize('mode',['snapshot_age','dispatch_deadline','future','mixed_mono','policy_digest'])
def test_verified_existing_runtime_bound_plus_one_or_bad_clocks_refuse(mode):
    base=fixture();runtime=base['config'].runtime_config
    if mode=='snapshot_age': inputs=observed_inputs(1,runtime.max_snapshot_age_s*1000+1)
    elif mode=='dispatch_deadline': inputs=observed_inputs(runtime.evidence_deadline_ms+1)
    else:
        inputs=observed_inputs()
        if mode=='future': inputs['now_ms']-=1;inputs['now_mono_ms']-=1
        elif mode=='mixed_mono': inputs['book']=replace(inputs['book'],observed_mono=123)
        else: inputs['config']=replace(inputs['config'],protected_runtime_config_sha256=A.digest('unbound config'))
    assert not A.build_intents(**inputs).intents
    assert A.build_intents(**inputs).rejection is not None
    if mode=='snapshot_age': assert A.build_intents(**observed_inputs(1,runtime.max_snapshot_age_s*1000)).intents
    if mode=='dispatch_deadline': assert A.build_intents(**observed_inputs(runtime.evidence_deadline_ms)).intents


def test_delayed_dispatch_does_not_promote_current_open_bar_into_closed_prefix():
    inputs=observed_inputs();cohort=inputs['cohort'];event=cohort.entry_open_ms
    poison=replace(cohort.btc_bars[-1],open_ms=event)
    result=A.build_intents(**{**inputs,'cohort':replace(cohort,btc_bars=(*cohort.btc_bars,poison))})
    assert result.rejection=='INCOMPLETE_OR_NONCAUSAL_CLOSED_BAR_PREFIX'


def test_actual_executable_quote_does_not_change_frozen_open_or_bypass_cost_protection():
    inputs=observed_inputs(quote_shift=D(100))
    result=A.build_intents(**inputs)
    assert not result.intents
    assert any(reason=='EXECUTABLE_QUOTE_OUTSIDE_FROZEN_PROTECTION' for _,reason in result.skips)
