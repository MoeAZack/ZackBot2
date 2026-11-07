"""Synthetic in-memory C259 proposals only; no data/economic files or venue use."""
from dataclasses import FrozenInstanceError,replace
from decimal import Decimal
from datetime import datetime,timezone
import math
import socket

import numpy as np
import pytest
from tools.research import c259_paper_adapter as A

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
            entry,A.digest(("filters",symbol)),quote,entry,entry,mark,A.digest(("market",symbol))))
    stamp=datetime.fromtimestamp(decision/1000,tz=timezone.utc)
    monday=int(stamp.replace(hour=0,minute=0,second=0).timestamp()*1000)-stamp.weekday()*24*H
    cell=A.PITCell(monday,monday,("DDDUSDT","BBBUSDT","AAAUSDT","CCCUSDT","BTCUSDT"),A.digest("PIT"))
    cohort=A.CohortSnapshot(decision,entry,entry,3,start,benchmark,tuple(reversed(symbols)),(cell,),
        A.digest("BTC"),A.digest("funding"),entry,True)
    account=A.digest("test account identity only")
    environment=A.PaperEnvironment(account,entry,3,confirmed_testnet=True,reconciliation_complete=True)
    book=A.SideBook(side,A.side_book_id(side),account,entry,3,D(2000),reconciled=True,actual_costs_complete=True,
        source_sha256=A.digest("isolated book state"))
    shared=A.SharedCapacity(account,entry,3,D(10000),D(10000),8,True)
    accepted=A.EconomicAcceptance(side,A.digest("synthetic historical PASS attestation"),True,True,True,True,True)
    return dict(cohort=cohort,book=book,acceptance=accepted,environment=environment,shared=shared,
                now_ms=entry,config=A.AdapterConfig(enabled=True))


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
    assert A.build_intents(**{**inputs,"now_ms":inputs["now_ms"]+H}).rejection=="WRONG_CANONICAL_ENTRY_CLOCK"


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
    result=A.build_intents(**{**inputs,"book":book})
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
