"""Native-hour hand offsets, salience direction, clocks and fixed thresholds."""
from dataclasses import replace
import math
import pytest
from tools.research import c270_signal as S
from tools.research import c270_prereg as C
H=S.HOUR_MS
T=30*H


def rows():return tuple(S.CompletedClose(t,t+H-1,100.) for t in range(T-25*H,T,H))


def fit(tape):return S.fit_signal('ALT',tape,entry_ms=T,window_start_ms=0,window_end_ms=T+100*H)


def test_exact_preread_nomination_and_reverse_seed_order_clarification():
    contract,nomination=C.load_metadata()
    assert contract['root_preread_clarifications']['null_seed']==27020261108
    assert contract['root_preread_clarifications']['ci_seed']==27020261107
    assert contract['root_preread_clarifications']['actual_first_eligible_development_clock']=='2024-12-14T00:00:00Z'
    with pytest.raises(C.C270Locked):C.assert_execution_authorized()


def test_outgoing_old_hour_is_indices_zero_one_not_last_hour_or_current_return():
    tape=list(rows());tape[1]=replace(tape[1],close=99.);tape[-1]=replace(tape[-1],close=40.)
    actual=fit(tape)
    assert actual.old_denominator_close==100. and actual.next_denominator_close==99.
    assert actual.score==-math.log(.99) and actual.positive_eligible
    assert actual.current_display_log_return==math.log(.4) # negative current display does not veto.
    tape[-1]=replace(tape[-1],close=400.)
    assert fit(tape).score==actual.score # no fresh momentum filter.
    tape[1]=replace(tape[1],close=101.)
    assert fit(tape).score==-math.log(1.01) and not fit(tape).positive_eligible


def test_threshold_inclusive_no_epsilon_and_first_three_unicode_no_min16():
    base=fit(rows())
    assert replace(base,score=.005).positive_eligible
    assert not replace(base,score=math.nextafter(.005,-math.inf)).positive_eligible
    fits=tuple(replace(base,symbol=s,score=.01) for s in('Z','猫','A','B'))
    assert [p.fit.symbol for p in S.rank_fits(fits)]==['A','B','Z']
    assert len(S.rank_fits(fits[:1]))==1


@pytest.mark.parametrize('attack',['missing','gap','duplicate','future_close','negative','nan'])
def test_observed_bad_coordinates_or_prices_cannot_be_warmup(attack):
    tape=list(rows())
    if attack=='missing':tape.pop()
    elif attack=='gap':tape[1]=replace(tape[1],open_ms=tape[1].open_ms+H)
    elif attack=='duplicate':tape[1]=tape[0]
    elif attack=='future_close':tape[-1]=replace(tape[-1],close_ms=T)
    elif attack=='negative':tape[1]=replace(tape[1],close=-1.)
    else:tape[1]=replace(tape[1],close=float('nan'))
    with pytest.raises(S.SignalIncomplete):fit(tape)


def test_no_prefold_warmup_or_wrong_current_utc_clock():
    with pytest.raises(S.SignalIncomplete):S.fit_signal('ALT',rows(),entry_ms=T,window_start_ms=T-24*H,window_end_ms=T+100*H)
    with pytest.raises(S.SignalIncomplete):S.fit_signal('ALT',rows(),entry_ms=T+H,window_start_ms=0,window_end_ms=T+100*H)
