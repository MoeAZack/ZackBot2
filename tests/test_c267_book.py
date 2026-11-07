"""Own267 causal side composition on private synthetic source component state."""
from dataclasses import replace
from decimal import Decimal as D
from types import MappingProxyType
import math
import numpy as np

import pytest
from tests.test_c267_source import native_fixture,issue,START,END,H
from tools.research import c267_source as S
from tools.research import c267_authority as A
from tools.research import c267_signals as SIGNAL
from tools.research import c267_funding as F
from tools.research import c267_book as B

DECISION=START+24*H
ENTRY=DECISION+H


def cache(source,symbol,series,rows):
    frozen=MappingProxyType(dict(sorted(rows.items())))
    source._cache[symbol,series]=source._issued_streams[symbol,series]=frozen
    return frozen


@pytest.fixture
def source(native_fixture,tmp_path,monkeypatch):
    issue(monkeypatch,native_fixture,tmp_path/'SYNTHETIC-own-claims')
    with A.development_authority(1) as cap:
        source=S.DevelopmentSource(cap)
        rng=np.random.Generator(np.random.PCG64(259))
        btc=list(rng.normal(0,.001,720));noise=list(rng.normal(0,.00001,720))
        series={'BTCUSDT':btc,'ALT00USDT':[1.3*btc[i]+(.35*btc[i-1] if i else 0)+noise[i] for i in range(720)],
            'ALT01USDT':[1.3*btc[i]+(.45*btc[i-1] if i else 0)+noise[i] for i in range(720)]}
        for symbol,returns in series.items():
            prices=[100.]
            for value in returns:prices.append(prices[-1]*math.exp(value))
            closes={DECISION-(721-i)*H:D(str(price)) for i,price in enumerate(prices)}
            closes[DECISION]=D(str(prices[-1]*math.exp(.01 if symbol=='BTCUSDT' else .013)))
            bars={t-H:S.NativeBar(t-H,D(100),max(D(100),price)+D('.5'),min(D(100),price)-D('.5'),price,D('1000000000')) for t,price in closes.items()}
            for t in range(DECISION,DECISION+20*H,H):bars[t]=S.NativeBar(t,D(100),D('100.5'),D('99.5'),D(100),D('1000000000'))
            cache(source,symbol,'klines',bars)
            cache(source,symbol,'markPriceKlines',{t:S.NativeBar(t,D(100),D(100),D(100),D(100),D(0)) for t in bars})
            cache(source,symbol,'fundingRate',{ENTRY-8*H:S.NativeFunding(ENTRY-8*H,8,D(0))})
        yield source


def setup(source):
    provider=SIGNAL.DecisionProvider(source);frame=provider.frame(DECISION)
    assert dict(frame.fits)['ALT00USDT'].qualifies and dict(frame.fits)['ALT01USDT'].qualifies
    return B.SideBook(provider),frame


def test_original_fit_window_decision_and_one_hour_delayed_entry_source_identity(source):
    provider=SIGNAL.DecisionProvider(source);frame=provider.frame(DECISION)
    assert frame.entry_ms==DECISION+H
    fit=dict(frame.fits)['ALT00USDT']
    assert fit==S.P.fit_signal(fit.prior_btc_closes,fit.prior_alt_closes,btc_decision=fit.btc_decision,alt_decision=fit.alt_decision)
    assert len(fit.prior_btc_closes)==721 and fit.window_end_ms==DECISION-H
    assert len(provider.atr_prefix('ALT00USDT',DECISION))==722
    with pytest.raises(S.C.C267Locked,match='FRAME_IDENTITY'):provider.validate_frame(replace(frame))
    with pytest.raises(TypeError):provider.closes('ALT00USDT')[DECISION]=None


def test_ranked_lexically_later_entry_known_debit_then_next_equity_is_causal(source):
    for symbol in('ALT00USDT','ALT01USDT'):
        cache(source,symbol,'fundingRate',{ENTRY-8*H:S.NativeFunding(ENTRY-8*H,8,D(0)),ENTRY-1:S.NativeFunding(ENTRY-1,4,D('.02'))})
    book,frame=setup(source)
    ranked=S.P.select_candidates(frame.fits,1,decision_close_ms=DECISION,limit=None)
    assert ranked[:2]==('ALT01USDT','ALT00USDT')
    first,reason=book.admit(frame,ranked[0],ENTRY);assert first and reason is None
    after=book.equity(ENTRY)
    second,reason=book.admit(frame,ranked[1],ENTRY);assert second and reason is None
    assert first.admission_ordinal==0 and second.admission_ordinal==1
    assert first.sizing.side_book_equity==2000 and second.sizing.side_book_equity==after<2000
    events=book.cash.events
    assert [(e.symbol,e.sequence,e.raw_time_ms is not None) for e in events]==[
        ('ALT01USDT',0,False),('ALT01USDT',1,True),('ALT00USDT',2,False),('ALT00USDT',3,True)]
    assert all(l.funding[0]['cash']<0 for l in(first,second))
    assert book.cash.reconciliation()['component_identity_reconciled']


def test_future_funding_fields_do_not_enter_entry_and_ambiguous_credit_withheld(source):
    class Trap:
        @property
        def interval_hours(self):pytest.fail('future funding interval read')
        @property
        def rate(self):pytest.fail('future funding rate read')
    cache(source,'ALT00USDT','fundingRate',{ENTRY-8*H:S.NativeFunding(ENTRY-8*H,8,D(0)),ENTRY-1:S.NativeFunding(ENTRY-1,4,D('-.02')),ENTRY+1:Trap()})
    book,frame=setup(source);lot,reason=book.admit(frame,'ALT00USDT',ENTRY)
    assert lot and lot.sizing.reserve.adverse_per_unit==0 and not lot.funding and book.withheld
    assert book.cash.settled_cash<2000


def test_due_open_before_current_range_and_target_gap_caps_reference_at_target(source):
    book,frame=setup(source);lot,_=book.admit(frame,'ALT00USDT',ENTRY)
    bars=dict(source.native_candles(lot.symbol));due=ENTRY+12*H
    bars[due]=replace(bars[due],low=D(1))
    cache(source,lot.symbol,'klines',bars)
    book.open_exits(due)
    assert lot.exit_kind=='SCHEDULED' and lot.fills[-1].reference==100.


def test_stop_target_same_prior_range_stop_wins_and_late_closed_debit_retained(source):
    cache(source,'ALT00USDT','fundingRate',{ENTRY-8*H:S.NativeFunding(ENTRY-8*H,8,D(0)),ENTRY+H+1:S.NativeFunding(ENTRY+H+1,4,D('.01'))})
    book,frame=setup(source);lot,_=book.admit(frame,'ALT00USDT',ENTRY)
    bars=dict(source.native_candles(lot.symbol));bars[ENTRY]=replace(bars[ENTRY],low=lot.sizing.stop-D(1),high=lot.target+D(1))
    cache(source,lot.symbol,'klines',bars)
    book.prior_ranges(ENTRY+H);book.reconcile(ENTRY+H)
    assert lot.exit_kind=='STOP_RANGE' and lot.earliest_exit_ms==ENTRY and not lot.funding
    before=book.cash.settled_cash;book.reconcile(ENTRY+2*H)
    assert lot.funding[0]['raw_time_ms']==ENTRY+H+1 and book.cash.settled_cash<before
    exact=book.cash.exact_components(lot.attempt_id);book.reconcile(ENTRY+3*H)
    assert book.cash.exact_components(lot.attempt_id)==exact and lot.funding_complete


def test_stress_preserves_base_admitted_quantity_risk_and_natural_funding(source):
    cache(source,'ALT00USDT','fundingRate',{ENTRY-8*H:S.NativeFunding(ENTRY-8*H,8,D(0)),ENTRY-1:S.NativeFunding(ENTRY-1,4,D('.01'))})
    base,frame=setup(source);lot,_=base.admit(frame,'ALT00USDT',ENTRY)
    stress=B.SideBook(base.provider,stress=True);stress.replay_admission(lot,ENTRY);mirrored=stress.lots[0]
    assert mirrored.sizing is lot.sizing and mirrored.funding[0]['cash']==lot.funding[0]['cash']
    assert stress.cash.settled_cash<base.cash.settled_cash


def test_exposed_zero_mandatory_volume_is_incomplete_with_entry_costs_and_position_retained(source):
    book,frame=setup(source);lot,_=book.admit(frame,'ALT00USDT',ENTRY)
    bars=dict(source.native_candles(lot.symbol));due=ENTRY+12*H
    for t in range(due-24*H,due,H):bars[t]=replace(bars[t],quote_volume=D(0))
    cache(source,lot.symbol,'klines',bars);before=book.cash.settled_cash
    with pytest.raises(B.K.ModelIncomplete,match='ZERO_OR_INVALID'):book.open_exits(due)
    assert lot.active and book.active[lot.symbol] is lot and book.cash.settled_cash==before<2000


def test_old_flat_liability_finalization_avoids_unneeded_terminal_source(source,monkeypatch):
    book,frame=setup(source);lot,_=book.admit(frame,'ALT00USDT',ENTRY)
    book.open_exits(ENTRY+12*H);book.reconcile(ENTRY+13*H)
    assert not lot.active and lot.funding_complete
    monkeypatch.setattr(source,'funding_tail',lambda *args,**kwargs:pytest.fail('flat unrelated tail read'))
    before=book.cash.settled_cash;book.reconcile(END+15000,terminal=True)
    assert book.cash.settled_cash==before
