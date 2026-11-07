"""Pure original259 parity and preread single-leg MODEL hostile witnesses."""
from dataclasses import replace
from decimal import Decimal as D
import json
import math
from pathlib import Path

import pytest
from tools.research import c267_execution_model as M

B=1735776000000;H=M.H


def rows():return tuple(M.E.VolumePoint(B-(24-i)*H,1e6/24) for i in range(24))
def filters(**kwargs):return M.E.Filters(D('.01'),D('.1'),D('.1'),D(kwargs.get('max_qty','100')),D(kwargs.get('min_notional','5')))
def reserve(side=1,amount='0'):
    rate=D(amount)/D(100)*D(side)
    event=M.KnownFunding('SYNTHETIC',B-1,8,rate,B-2*H,D(100))
    return M.known_entry_reserve('SYNTHETIC',side,B,(event,))
def solve(side=1,amount='0',**kwargs):
    return M.size_reserved(symbol='SYNTHETIC',side=side,entry=D(100),stop=D(90 if side==1 else 110),
        filters=filters(**kwargs),side_book_equity=D(2000),entry_impact_asof_ms=B,entry_volume_rows=rows(),reserve=reserve(side,amount))


@pytest.mark.parametrize('side',[1,-1])
def test_zero_reserve_matches_original_single_leg_solver_exactly(side):
    result=solve(side)
    original=M.E.size_position(side=side,entry=D(100),stop=D(90 if side==1 else 110),filters=filters(),
        side_book_equity=D(2000),entry_impact_asof_ms=B,entry_volume_rows=rows())
    assert result.quantity==original.quantity and result.initial_risk_cash==original.initial_risk_cash
    assert result.raw_notional==original.raw_notional and result.quote_volume_24h==original.quote_volume_24h


@pytest.mark.parametrize('side,amount,qty,risk',[(1,'0','1.9','19.5900603442093350779'),
    (1,'2','1.6','19.693526577025077624'),(1,'4','1.3','18.59798426550316739513'),
    (-1,'2','1.6','19.74759561554917963824')])
def test_root_independent_execution_price_fee_impact_and_known_debit_gold(side,amount,qty,risk):
    result=solve(side,amount)
    assert result.quantity==D(qty) and abs(result.initial_risk_cash-D(risk))<=D('1e-18')
    assert result.initial_risk_cash<=20


def test_original_maxqty_minnotional_and_no_favorable_upsize():
    result=solve(amount='2',max_qty='1')
    assert result.quantity==1 and abs(result.initial_risk_cash-D('12.3035431496824546251'))<D('1e-18')
    assert solve(min_notional='500') is None


def test_valid_zero_hour_positive_total_exact_all_positive_parity_and_zero_median():
    original=rows();mixed=list(original)
    mixed[0]=replace(mixed[0],quote_volume=0.)
    mixed[1]=replace(mixed[1],quote_volume=original[1].quote_volume*2)
    assert M.causal_volume_total(mixed,B)==M.causal_volume_total(original,B)
    result=M.size_reserved(symbol='SYNTHETIC',side=1,entry=D(100),stop=D(90),filters=filters(),
        side_book_equity=D(2000),entry_impact_asof_ms=B,entry_volume_rows=mixed,reserve=reserve(amount='2'))
    assert result.quantity==solve(amount='2').quantity and result.initial_risk_cash==solve(amount='2').initial_risk_cash
    assert M.liquidity_ratio_diagnostic(1.,[0.]*24)=={'eligible_original_gate':True,'ratio':None,'status':'ZERO_BASELINE'}
    with pytest.raises(M.ModelIncomplete,match='ZERO_OR_INVALID'):M.causal_volume_total([replace(r,quote_volume=0.) for r in rows()],B)


def test_raw_boundary_signs_no_credit_netting_and_no_future_field_access():
    assert reserve(1,'2').adverse_per_unit==reserve(-1,'2').adverse_per_unit==2
    credit=M.KnownFunding('SYNTHETIC',B-1,8,D('-.02'),B-2*H,D(100))
    assert M.known_entry_reserve('SYNTHETIC',1,B,(credit,)).adverse_per_unit==0
    class Trap:
        def __getattribute__(self,name):pytest.fail('future rate/mark accessed')
    future=M.KnownFunding('SYNTHETIC',B+1,Trap(),Trap(),Trap(),Trap())
    with pytest.raises(M.ModelIncomplete,match='FUTURE_RAW'):M.known_entry_reserve('SYNTHETIC',1,B,(future,))
    bad=replace(credit,mark_open_ms=B-H)
    with pytest.raises(M.ModelIncomplete,match='CAUSAL_MARK'):M.known_entry_reserve('SYNTHETIC',1,B,(bad,))


@pytest.mark.parametrize('entry_offset,expected',[(-1,False),(0,True),(1800000,True)])
def test_derived_current_effective_event_only_no_advance_horizon_veto(entry_offset,expected):
    event=M.C.load_catalog_metadata()[0][1];ban=event['effective_no_new_entry_ms']
    result=M.assess_current_lifecycle(symbol=event['symbol'],contract_identity=event['contract_identity'],
        entry_ms=ban+entry_offset,scheduled_due_ms=ban+12*H)
    assert result.veto is expected and result.entry_authorized is False and result.coverage_unknown
    if entry_offset<0:assert result.reason=='NO_PRE_EFFECTIVE_HORIZON_VETO_WITHOUT_ASOF_PROOF'


def test_settlement_is_incomplete_no_last_bar_fill_and_unrelated_contract_not_covered():
    event=M.C.load_catalog_metadata()[0][1]
    assert 'DATA_INCOMPLETE' in M.exposed_settlement_requirement(symbol=event['symbol'],contract_identity=event['contract_identity'],asof_ms=event['effective_settlement_ms'])
    result=M.assess_current_lifecycle(symbol='UNRELATED',contract_identity='OTHER_CONTRACT',entry_ms=B,scheduled_due_ms=B+12*H)
    assert result.coverage_unknown and result.entry_authorized is False and not result.veto
    with pytest.raises(TypeError):M.assess_current_lifecycle(symbol='FTMUSDT',contract_identity=event['contract_identity'],entry_ms=B,scheduled_due_ms=B+12*H,original_asof_version_verified=True)


def test_derived_record_hash_drift_cannot_upgrade_original_publication_or_settlement(tmp_path,monkeypatch):
    root=M.C.ROOT
    for path in(M.C.CATALOG_PATH,*[p['path'] for p,e in M.C.load_catalog_metadata()]):
        target=tmp_path/path;target.parent.mkdir(parents=True,exist_ok=True);target.write_bytes((root/path).read_bytes())
    pin,event=M.C.load_catalog_metadata()[0];event['advance_information_available_ms']=B-H
    (tmp_path/pin['path']).write_text(json.dumps(event))
    monkeypatch.setattr(M.C,'ROOT',tmp_path)
    with pytest.raises(M.C.C267Locked,match='BYTE_DRIFT'):M.assess_current_lifecycle(symbol='FTMUSDT',contract_identity='BINANCE_USDM_PERPETUAL_FTMUSDT',entry_ms=B,scheduled_due_ms=B+12*H)


def cash_event(attempt='a',time=B,phase='ENTRY',sequence=0,components=(-.05,-.02,-.001),raw=None):
    return M.CashEvent(time,phase,'SYNTHETIC',attempt,sequence,tuple(map(float,components)),f'SYNTHETIC:{raw}' if raw is not None else 'fill',raw)


def test_two_side_cash_and_persistent_marked_exposure_isolation():
    long,short=M.SideCash(1),M.SideCash(-1)
    long.append(cash_event());long.positions['SYNTHETIC']=(D(2),D(100))
    assert long.mark_equity({'SYNTHETIC':D(105)})==math.fsum((2000.,-.05,-.02,-.001,10.))
    assert short.settled_cash==2000 and short.mark_equity({})==2000
    long.append(cash_event(phase='FUNDING',time=B+H,components=(-2,),raw=B+1))
    assert long.mark_equity({'SYNTHETIC':D(105)})==math.fsum((2000.,-.05,-.02,-.001,10.,-2.))
    assert short.settled_cash==2000 and long.reconciliation()['component_identity_reconciled']
    with pytest.raises(M.ModelIncomplete,match='OWN_SIDE_OPEN_MARKS'):long.mark_equity({})


def test_cash_phase_backdating_and_once_per_attempt_allocation_guards():
    ledger=M.SideCash(1);ledger.append(cash_event())
    event=cash_event(time=B+H,phase='FUNDING',components=(-1,),raw=B+1)
    ledger.append(event)
    with pytest.raises(M.ModelIncomplete,match='DUPLICATE'):ledger.append(replace(event,sequence=1))
    with pytest.raises(M.ModelIncomplete,match='BACKDATED'):ledger.append(cash_event(sequence=1))
    with pytest.raises(M.ModelIncomplete,match='FUTURE'):ledger.append(cash_event(time=B+2*H,phase='FUNDING',raw=B+3*H))
    # One shared real event may debit a different actually exposed attempt;
    # it never re-debits the same event/attempt allocation.
    ledger.append(replace(event,attempt_id='b'))
    assert ledger.reconciliation()['component_identity_reconciled']


def test_authoritative_cash_exact_finite_decimal_prefix_no_tolerance_or_dropped_small_fee():
    values=(D('.1'),D('1e-30'),D('-.1'))
    ledger=M.SideCash(1);ledger.append(M.CashEvent(B,'ENTRY','SYNTHETIC','a',0,values,'fill'))
    assert ledger.exact_components()==M.Fraction(D('1e-30'))
    assert ledger.settled_cash_exact==D('2000.000000000000000000000000000001')
    assert ledger.reconciliation(expected_components=values)['expected_component_total_reconciled']
    # The source-derived expected components include a small real fee omitted
    # by a broken writer. No arbitrary monetary epsilon can conceal it.
    assert ledger.reconciliation(expected_components=(*values,D('-1e-9')))['expected_component_total_reconciled'] is False
    assert ledger.reconciliation()['source_binding_checked'] is False


def test_alternate_trade_grouping_matches_exact_authoritative_component_sum():
    ledger=M.SideCash(-1)
    first=(D('12.3035431496824546251'),D('-.000000000000000000000006333'))
    second=(D('-12.3035431496824546251'),D('.000000000000000000000004'))
    ledger.append(M.CashEvent(B,'ENTRY','SYNTHETIC','a',0,first,'fill-a'))
    ledger.append(M.CashEvent(B+H,'SCHEDULED','SYNTHETIC','b',0,second,'fill-b'))
    exact=ledger._exact((*first,*second))
    assert ledger.exact_components('a')+ledger.exact_components('b')==exact==ledger.exact_components()
    assert ledger.reconciliation(expected_components=(*first,*second))['component_identity_reconciled']
    with pytest.raises(AttributeError):ledger.events.clear()


def test_cached_exact_prefix_is_constant_work_but_report_reaudits_missing_component(monkeypatch):
    ledger=M.SideCash(1);event=cash_event(components=(-.05,-1e-9));ledger.append(event)
    expected=ledger.exact_components();original=ledger._exact
    monkeypatch.setattr(ledger,'_exact',lambda *args:pytest.fail('cash prefix rescanned history'))
    assert ledger.exact_components()==ledger.exact_components('a')==expected
    assert ledger.settled_cash==float(M.Fraction(2000)+expected)
    monkeypatch.setattr(ledger,'_exact',original)
    ledger._events[0]=replace(event,components=(-.05,))
    assert ledger.reconciliation()['component_identity_reconciled'] is False
