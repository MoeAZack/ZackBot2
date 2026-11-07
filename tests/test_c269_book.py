"""Own269 causal side composition on private synthetic source component state."""
from dataclasses import replace
from decimal import Decimal as D
from types import MappingProxyType
import math
import numpy as np

import pytest
from tests.test_c269_source import native_fixture,issue,START,END,H
from tools.research import c269_source as S
from tools.research import c269_authority as A
from tools.research import c269_signals as SIGNAL
from tools.research import c269_funding as F
from tools.research import c269_book as B

DECISION=START+24*H
ENTRY=DECISION


def cache(source,symbol,series,rows):
    frozen=MappingProxyType(dict(sorted(rows.items())))
    source._cache[symbol,series]=source._issued_streams[symbol,series]=frozen
    return frozen


@pytest.fixture
def source(native_fixture,tmp_path,monkeypatch):
    issue(monkeypatch,native_fixture,tmp_path/'SYNTHETIC-own-claims')
    with A.development_authority(-1) as cap:
        source=S.DevelopmentSource(cap)
        members=source.membership_and_filters(decision_ms=ENTRY)[0]
        for symbol in members:
            bars={}
            for i,t in enumerate(range(ENTRY-241*H,ENTRY,H)):
                if symbol=='BTCUSDT':
                    price=D(str(100.+math.sin(i/5)));bars[t]=S.NativeBar(t,D(100),D(102),D(98),price,D('1000000000'))
                else:bars[t]=S.NativeBar(t,D(100),D(102),D(100),D(101),D('1000000000'))
            bars[ENTRY-H]=S.NativeBar(ENTRY-H,D(100),D('100.5'),D('99.5'),D(100),D('1000000000'))
            for t in range(ENTRY,ENTRY+55*H,H):bars[t]=S.NativeBar(t,D(100),D('100.5'),D('99.5'),D(100),D('1000000000'))
            cache(source,symbol,'klines',bars)
            cache(source,symbol,'markPriceKlines',{t:S.NativeBar(t,D(100),D(100),D(100),D(100),D(0)) for t in bars})
            cache(source,symbol,'fundingRate',{ENTRY-8*H:S.NativeFunding(ENTRY-8*H,8,D(0))})
        yield source


def setup(source):
    provider=SIGNAL.DecisionProvider(source);frame=provider.frame(ENTRY)
    assert len(frame.fits)>=16 and len(frame.indexed)==3
    return B.SideBook(provider),frame


def test_exact168_bar_mass_current_open_source_identity_and_full_wilder(source):
    provider=SIGNAL.DecisionProvider(source);frame=provider.frame(ENTRY)
    assert frame.entry_ms==frame.decision_ms==ENTRY and frame.side==-1
    symbol=frame.indexed[0].fit.symbol;fit=dict(frame.fits)[symbol]
    tape=tuple(provider.closes(symbol)[t] for t in range(ENTRY-168*H,ENTRY,H))
    atr=S.P.atr24_wilder(provider.atr_prefix(symbol,ENTRY),ENTRY)
    assert fit==SIGNAL.PURE.fit_signal(symbol,tape,atr=atr,entry_ms=ENTRY,window_start_ms=source.start_ms,window_end_ms=source.end_ms)
    assert fit.score==167/168 and len(provider.atr_prefix(symbol,ENTRY))==241
    with pytest.raises(S.C.C269Locked,match='FRAME_IDENTITY'):provider.validate_frame(replace(frame))
    with pytest.raises(TypeError):provider.closes(symbol)[ENTRY]=None
    with pytest.raises(S.C.C269Locked,match='SIX_HOUR'):provider.frame(ENTRY+H)


def test_ranked_entry_known_debit_then_next_equity_is_causal(source):
    book,frame=setup(source);ranked=[p.fit.symbol for p in frame.indexed]
    for symbol in ranked:
        cache(source,symbol,'fundingRate',{ENTRY-8*H:S.NativeFunding(ENTRY-8*H,8,D(0)),ENTRY-1:S.NativeFunding(ENTRY-1,4,D('-.02'))})
    first,reason=book.admit(frame,ranked[0],ENTRY);assert first and reason is None
    after=book.equity(ENTRY)
    second,reason=book.admit(frame,ranked[1],ENTRY);assert second and reason is None
    assert first.admission_ordinal==0 and second.admission_ordinal==1
    assert first.sizing.side_book_equity==2000 and second.sizing.side_book_equity==after<2000
    assert [(e.symbol,e.sequence,e.raw_time_ms is not None) for e in book.cash.events]==[
        (ranked[0],0,False),(ranked[0],1,True),(ranked[1],2,False),(ranked[1],3,True)]
    assert all(l.funding[0]['cash']<0 for l in(first,second))
    assert book.cash.reconciliation()['component_identity_reconciled']


def test_future_funding_fields_do_not_enter_entry_and_ambiguous_credit_withheld(source):
    class Trap:
        @property
        def interval_hours(self):pytest.fail('future funding interval read')
        @property
        def rate(self):pytest.fail('future funding rate read')
    cache(source,SIGNAL.DecisionProvider(source).frame(ENTRY).indexed[0].fit.symbol,'fundingRate',{ENTRY-8*H:S.NativeFunding(ENTRY-8*H,8,D(0)),ENTRY-1:S.NativeFunding(ENTRY-1,4,D('.02')),ENTRY+1:Trap()})
    book,frame=setup(source);lot,reason=book.admit(frame,frame.indexed[0].fit.symbol,ENTRY)
    assert lot and lot.sizing.reserve.adverse_per_unit==0 and not lot.funding and book.withheld
    assert book.cash.settled_cash<2000


def test_due_open_before_current_range_and_target_gap_caps_reference_at_target(source):
    book,frame=setup(source);lot,_=book.admit(frame,frame.indexed[0].fit.symbol,ENTRY)
    bars=dict(source.native_candles(lot.symbol));due=ENTRY+24*H
    bars[due]=replace(bars[due],low=D(1))
    cache(source,lot.symbol,'klines',bars)
    book.open_exits(due)
    assert lot.exit_kind=='SCHEDULED' and lot.fills[-1].reference==100.


def test_stop_target_same_prior_range_stop_wins_and_late_closed_debit_retained(source):
    cache(source,SIGNAL.DecisionProvider(source).frame(ENTRY).indexed[0].fit.symbol,'fundingRate',{ENTRY-8*H:S.NativeFunding(ENTRY-8*H,8,D(0)),ENTRY+H+1:S.NativeFunding(ENTRY+H+1,4,D('-.01'))})
    book,frame=setup(source);lot,_=book.admit(frame,frame.indexed[0].fit.symbol,ENTRY)
    bars=dict(source.native_candles(lot.symbol));bars[ENTRY]=replace(bars[ENTRY],low=lot.target-D(1),high=lot.sizing.stop+D(1))
    cache(source,lot.symbol,'klines',bars)
    book.prior_ranges(ENTRY+H);book.reconcile(ENTRY+H)
    assert lot.exit_kind=='STOP_RANGE' and lot.earliest_exit_ms==ENTRY and not lot.funding
    before=book.cash.settled_cash;book.reconcile(ENTRY+2*H)
    assert lot.funding[0]['raw_time_ms']==ENTRY+H+1 and book.cash.settled_cash<before
    exact=book.cash.exact_components(lot.attempt_id);book.reconcile(ENTRY+3*H)
    assert book.cash.exact_components(lot.attempt_id)==exact and lot.funding_complete


def test_stress_preserves_base_admitted_quantity_risk_and_natural_funding(source):
    cache(source,SIGNAL.DecisionProvider(source).frame(ENTRY).indexed[0].fit.symbol,'fundingRate',{ENTRY-8*H:S.NativeFunding(ENTRY-8*H,8,D(0)),ENTRY-1:S.NativeFunding(ENTRY-1,4,D('-.01'))})
    base,frame=setup(source);lot,_=base.admit(frame,frame.indexed[0].fit.symbol,ENTRY)
    stress=B.SideBook(base.provider,stress=True);stress.replay_admission(lot,ENTRY);mirrored=stress.lots[0]
    assert mirrored.sizing is lot.sizing and mirrored.funding[0]['cash']==lot.funding[0]['cash']
    assert stress.cash.settled_cash<base.cash.settled_cash


def test_exposed_zero_mandatory_volume_is_incomplete_with_entry_costs_and_position_retained(source):
    book,frame=setup(source);lot,_=book.admit(frame,frame.indexed[0].fit.symbol,ENTRY)
    bars=dict(source.native_candles(lot.symbol));due=ENTRY+24*H
    for t in range(due-24*H,due,H):bars[t]=replace(bars[t],quote_volume=D(0))
    cache(source,lot.symbol,'klines',bars);before=book.cash.settled_cash
    with pytest.raises(B.K.ModelIncomplete,match='ZERO_OR_INVALID'):book.open_exits(due)
    assert lot.active and book.active[lot.symbol] is lot and book.cash.settled_cash==before<2000


def test_old_flat_liability_finalization_avoids_unneeded_terminal_source(source,monkeypatch):
    book,frame=setup(source);lot,_=book.admit(frame,frame.indexed[0].fit.symbol,ENTRY)
    book.open_exits(ENTRY+24*H);book.reconcile(ENTRY+25*H)
    assert not lot.active and lot.funding_complete
    monkeypatch.setattr(source,'funding_tail',lambda *args,**kwargs:pytest.fail('flat unrelated tail read'))
    before=book.cash.settled_cash;book.reconcile(END+15000,terminal=True)
    assert book.cash.settled_cash==before


def test_actual_hour_loop_freezes_three_indices_and_never_backfills(source,monkeypatch):
    book,frame=setup(source)
    first=frame.indexed[0].fit.symbol
    bars=dict(source.native_candles(first));bars[ENTRY]=replace(bars[ENTRY],open=D('100.00001'),high=D(101))
    cache(source,first,'klines',bars)
    def fixed_frame(t):
        if t==ENTRY:return frame
        empty=SIGNAL.DecisionFrame(t,t,-1,frame.members,'UNCLASSIFIED',(),(),(),(),'SYNTHETIC_NO_PROPOSALS',())
        book.provider._frames[t]=book.provider._issued[t]=empty
        return empty
    monkeypatch.setattr(book.provider,'frame',fixed_frame)
    book.run()
    assert book.complete and not book.active
    row=next(r for r in book.decisions if r['entry_ms']==ENTRY)
    assert row['skipped']==[{'symbol':first,'original_rank':1,'reason':'OFF_GRID_RAW_ENTRY'}]
    assert [(r['original_rank'],r['symbol']) for r in row['admitted']]==[(p.index,p.fit.symbol) for p in frame.indexed[1:]]
    assert len(book.lots)==2 and all(l.entry_ms==ENTRY and l.latest_exit_ms==ENTRY+24*H for l in book.lots)
    stress=B.SideBook(book.provider,stress=True).run(base=book)
    assert stress.complete and [(l.sizing.quantity,l.sizing.initial_risk_cash) for l in stress.lots]==[(l.sizing.quantity,l.sizing.initial_risk_cash) for l in book.lots]


def test_tiny_positive_dimensionless_mass_has_no_forecast_return_gate(source,monkeypatch):
    book,frame=setup(source);fit=frame.indexed[0].fit;small=replace(fit,score=.000001)
    fixed=replace(frame,fits=tuple((s,small if s==fit.symbol else f) for s,f in frame.fits),indexed=(SIGNAL.PURE.IndexedSignal(1,small),))
    book.provider._frames[ENTRY]=book.provider._issued[ENTRY]=fixed
    monkeypatch.setattr(S.P,'cost_admission',lambda **kw:pytest.fail('forecast-return predicate is not269'))
    lot,reason=book.admit(fixed,fit.symbol,ENTRY)
    assert lot is not None and reason is None


def test_one_defined_positive_symbol_has_index_no_beta_min16(source,monkeypatch):
    members,rules=source.membership_and_filters(decision_ms=ENTRY);alt=next(s for s in members if s!='BTCUSDT')
    monkeypatch.setattr(source,'membership_and_filters',lambda **kw:(('BTCUSDT',alt),rules))
    frame=SIGNAL.DecisionProvider(source).frame(ENTRY)
    assert len(frame.fits)==1 and len(frame.indexed)==1 and frame.indexed[0].fit.symbol==alt


def test_observed_zero_168h_quote_total_is_infeasible_not_source_failure(source):
    symbol=source.membership_and_filters(decision_ms=ENTRY)[0][0]
    if symbol=='BTCUSDT':symbol=source.membership_and_filters(decision_ms=ENTRY)[0][1]
    bars={t:replace(b,quote_volume=D(0)) for t,b in source.native_candles(symbol).items()}
    cache(source,symbol,'klines',bars)
    frame=SIGNAL.DecisionProvider(source).frame(ENTRY)
    assert dict(frame.fits)[symbol].status=='ZERO_TOTAL_LIQUIDITY_INFEASIBLE'
    assert symbol not in [p.fit.symbol for p in frame.indexed]
