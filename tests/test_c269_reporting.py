"""Source-expected monetary audit, exact side drawdown and failure retention."""
from dataclasses import replace
from decimal import Decimal as D

import pytest
from tests.test_c269_book import source,setup,ENTRY,H
from tests.test_c269_source import native_fixture
from tools.research import c269_reporting as R
from tools.research import c269_runner as CLI


def test_report_audits_all_actual_fill_and_funding_components_not_its_own_cash_events(source):
    book,frame=setup(source);lot,_=book.admit(frame,frame.indexed[0].fit.symbol,ENTRY);book.last_boundary=ENTRY
    before=R.report(book)
    assert before['cash_reconciled'] and before['unresolved_exposure'][0]['attempt_id']==lot.attempt_id
    assert before['known_current_cash']<2000 and before['unresolved_exposure'][0]['fee_drag']>0
    event=book.cash._events[0]
    # Mimic a broken writer that omits1e-9 while keeping its own caches coherent.
    changed=replace(event,components=(*event.components,1e-9));book.cash._events[0]=changed
    delta=book.cash._exact((1e-9,));book.cash._cumulative+=delta;book.cash._per_attempt[lot.attempt_id]+=delta
    assert book.cash.reconciliation()['component_identity_reconciled']
    after=R.report(book)
    assert after['cash_reconciled'] is False and after['data_complete'] is False
    assert after['cash_audit']['expected_component_total_reconciled'] is False


def test_failure_packet_retains_open_fills_cash_and_explicit_unknown_marks(source,monkeypatch):
    book,frame=setup(source);lot,_=book.admit(frame,frame.indexed[0].fit.symbol,ENTRY);book.last_boundary=ENTRY+H
    book.complete=False;book.failures.append('SYNTHETIC_MARK_FAILURE')
    monkeypatch.setattr(source,'native_marks',lambda *args:(_ for _ in ()).throw(R.B.S.SourceIncomplete('SYNTHETIC missing mark')))
    report=R.report(book)
    assert not report['data_complete'] and report['current_marked_equity'] is None
    assert report['unknown_current_marks']==[lot.symbol] and report['unresolved_exposure'][0]['fills']
    assert report['known_current_cash']<2000 and report['cash_reconciled']
    assert CLI.jsonable(report)['all_known_attempts'][0]['fills'][0]['volume_rows']


def test_cash_pf_and_dev_worse_drawdown_remain_original259_not266_r_pf_or_closed_only():
    assert R.cash_profit_factor([1.,-2.])==.5
    assert R.cash_profit_factor([1.,2.]) is None
    base={'monthly_including_zero':{'a':{'closed':8}},'closed_count':120,'symbols':8,'gross_R':1.,'mean_R':.1,
        'profit_factor':1.2,'gate_drawdown_R':70.,'data_complete':True}
    assert R.development_checks(base,base)['gate_drawdown_R_lte_60'] is False


def test_coherent_unowned_zero_total_cash_attempts_cannot_pass_source_audit(source):
    book,frame=setup(source);lot,_=book.admit(frame,frame.indexed[0].fit.symbol,ENTRY);book.last_boundary=ENTRY
    book.cash.append(R.B.K.CashEvent(ENTRY+H,'MARK','A','ghost-a',0,(1e-9,),'ghost-a'))
    book.cash.append(R.B.K.CashEvent(ENTRY+H,'MARK','Z','ghost-b',0,(-1e-9,),'ghost-b'))
    report=R.report(book)
    assert book.cash.reconciliation()['component_identity_reconciled']
    assert report['cash_reconciled'] is False and not report['exact_cash_attempt_identity_set_matches_lots']


def test_missing_benchmark_required_report_cannot_pass_dev_complete():
    base={'monthly_including_zero':{'a':{'closed':8}},'closed_count':120,'symbols':8,'gross_R':1.,'mean_R':.1,
        'profit_factor':1.2,'gate_drawdown_R':1.,'data_complete':True,'required_report_complete':False}
    assert R.development_checks(base,base)['complete'] is False
