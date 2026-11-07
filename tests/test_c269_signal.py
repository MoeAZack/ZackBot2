"""Hand arithmetic and hostile clocks for the sole preregistered269 signal."""
from dataclasses import replace
import math
import pytest
from tools.research import c269_signal as S
from tools.research import c269_prereg as C

H=S.HOUR_MS
T=174*H


def rows():
    return tuple(S.CompletedBar(t,t+H-1,100.,100.,100.,100.,1.) for t in range(T-168*H,T,H))


def fit(tape,atr=1.,symbol='ALT'):
    return S.fit_signal(symbol,tape,atr=atr,entry_ms=T,window_start_ms=0,window_end_ms=T+100*H)


def test_exact_frozen_nomination_and_root_one_short_scope():
    contract,nomination=C.load_metadata()
    assert contract['primary_side']==-1 and nomination['candidate_id_proposed']==269
    with pytest.raises(C.C269Locked):C.assert_execution_authorized()


def test_strict_lower_inclusive_upper_and_quote_weighted_hand_mass():
    tape=list(rows())
    tape[0]=replace(tape[0],high=103.,low=100.,close=100.,quote_volume=2.) # typical101 upper included.
    tape[1]=replace(tape[1],high=106.,low=100.,close=100.,quote_volume=3.) # typical102 excluded.
    tape[2]=replace(tape[2],quote_volume=0.)
    actual=fit(tape)
    assert actual.total_quote_volume==170. and actual.overhead_quote_volume==2.
    assert actual.score==2./170. and actual.positive_eligible
    assert fit(rows()).score==0. # exact lower equality excluded, not a short.


def test_adjacent_binary64_boundaries_no_epsilon_and_last_close_not_future_open():
    tape=list(rows());above=math.nextafter(101.,math.inf)
    tape[0]=replace(tape[0],open=above,high=above,low=above,close=above)
    assert fit(tape).score==0.
    tape[0]=replace(tape[0],open=101.,high=101.,low=101.,close=101.)
    assert fit(tape).score==1./168.


def test_zero_total_liquidity_and_zero_atr_are_infeasible_not_freefills():
    assert fit(tuple(replace(b,quote_volume=0.) for b in rows())).status=='ZERO_TOTAL_LIQUIDITY_INFEASIBLE'
    assert fit(rows(),atr=0.).status=='ZERO_ATR_PROTECTION_INFEASIBLE'
    assert not S.rank_fits((fit(rows(),atr=0.),))


@pytest.mark.parametrize('attack',['missing','duplicate','future','negative_quote','nan_quote','nonfinite_price','bad_range'])
def test_malformed_or_noncausal_observed_history_hard_fails(attack):
    tape=list(rows())
    if attack=='missing':tape.pop()
    elif attack=='duplicate':tape[1]=tape[0]
    elif attack=='future':tape[-1]=replace(tape[-1],close_ms=T)
    elif attack=='negative_quote':tape[0]=replace(tape[0],quote_volume=-1.)
    elif attack=='nan_quote':tape[0]=replace(tape[0],quote_volume=float('nan'))
    elif attack=='nonfinite_price':tape[0]=replace(tape[0],high=float('inf'))
    else:tape[0]=replace(tape[0],low=101.)
    with pytest.raises(S.SignalIncomplete):fit(tape)


def test_no_beta_min16_gate_ties_unicode_and_three_fixed_indices():
    base=fit(rows());fits=tuple(replace(base,symbol=s,score=.5,overhead_quote_volume=84.) for s in('Z','B','A','C'))
    assert [(p.index,p.fit.symbol) for p in S.rank_fits(fits)]==[(1,'A'),(2,'B'),(3,'C')]
    assert len(S.rank_fits(fits[:1]))==1 # no hypothesis268 fit-count transplant.
    assert S.rank_fits((replace(base,symbol='猫',score=.5),replace(base,symbol='A',score=.5)))[0].fit.symbol=='A'


def test_canonical_current_sixhour_clock_and_no_prefold_warmup():
    with pytest.raises(S.SignalIncomplete):S.fit_signal('ALT',rows(),atr=1.,entry_ms=T+H,window_start_ms=0,window_end_ms=T+100*H)
    with pytest.raises(S.SignalIncomplete):S.fit_signal('ALT',rows(),atr=1.,entry_ms=T,window_start_ms=T-167*H,window_end_ms=T+100*H)
