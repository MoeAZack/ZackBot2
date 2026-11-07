"""Hostile synthetic-only C257 evaluator tests; no real authority rows are opened."""
from __future__ import annotations
from dataclasses import replace
from decimal import Decimal
from pathlib import Path
import pytest
from tools.research import funding_shock_reversal_xsec_c257_stagea as S
from tools.research import funding_shock_reversal_xsec_c257_evaluator as E
from tools.research import funding_shock_reversal_xsec_c257_protected_adapter as A
from tools.research import run_funding_shock_reversal_xsec_c257_protected as CLI

D = Decimal

def bars(*, touch_long_at=None, gap_short_at=None):
    rows=[]
    for hour in range(0, 36):
        low = 96 if hour == touch_long_at else 99
        open_ = 104 if hour == gap_short_at else 100
        rows.append(S.HourBar(hour*S.HOUR_MS, open_, max(open_+1,101), low, 100, 1_000_000))
    return tuple(rows)

def legs(entry_hour=25):
    t=entry_hour*S.HOUR_MS
    return (E.LegPlan("L",1,D(1),t,D(100),D(97)),
            E.LegPlan("S",-1,D(1),t,D(100),D(103)))

def test_exact_c256_inheritance_is_git_bound_and_default_locked():
    contract=S.load_frozen_contract()
    assert contract["candidate_id"] == 256
    assert contract["multiplicity"]["candidate_familywise_alpha_ceiling"] == 0.0001953125
    with pytest.raises(S.EvaluationLocked, match="C257_REAL_EVALUATION_LOCKED"):
        S.assert_real_evaluation_authorized(gate={"approved":True})

def test_t_plus_300_exact_31_row_surprise_and_tail_pairing():
    history=[S.FundingRow("A",i*8*S.HOUR_MS,i*8*S.HOUR_MS+1,D("0.00001") if False else ((-1)**i)*(1+i%4)*1e-5,8) for i in range(31)]
    nominal=31*8*S.HOUR_MS
    current=S.FundingRow("A",nominal,nominal+1,0.001,8)
    result=S.robust_funding_surprise(current,history,decision_ms=nominal+S.FUNDING_DELAY_MS)
    assert result.z >= 2
    with pytest.raises(S.IncompleteAuthority,match="31 rows"):
        S.robust_funding_surprise(current,history[1:],decision_ms=nominal+S.FUNDING_DELAY_MS)
    shocks=[S.FundingShock(f"N{i}",nominal,0,0,1,0) for i in range(6)]
    shocks += [S.FundingShock("L1",nominal,0,0,1,-4),S.FundingShock("L2",nominal,0,0,1,-3),
               S.FundingShock("L3",nominal,0,0,1,-2),S.FundingShock("S1",nominal,0,0,1,4),
               S.FundingShock("S2",nominal,0,0,1,3),S.FundingShock("S3",nominal,0,0,1,2)]
    assert [(p.long_symbol,p.short_symbol) for p in S.fixed_index_pairs(shocks)] == [("L1","S1"),("L2","S2"),("L3","S3")]

def test_exact_decimal_sizing_rounds_down_and_binds_three_atr_risk():
    f=S.ExactFilters(D("0.1"),D("0.01"),D("0.01"),D("100"),D("5"))
    sized=S.size_exact_pair(equity=D("2000"),long=S.ExactLeg("L",1,D("100"),D("1.01"),f),
                            short=S.ExactLeg("S",-1,D("50"),D("0.51"),f))
    assert sized.long_stop == D("96.9") and sized.short_stop == D("51.6")
    assert sized.modeled_loss <= D("20") and sized.gross <= D("1000") and sized.risk_r <= 1
    assert sized.long_qty % D("0.01") == 0 and sized.short_qty % D("0.01") == 0

def test_complete_lifecycle_derives_8h_exit_and_rejects_missing_bar():
    authority={"L":bars(),"S":bars()}
    finals=E.derive_lifecycle(legs(),authority)
    assert {(f.phase,f.timestamp_ms) for f in finals} == {("exit",33*S.HOUR_MS)}
    broken={"L":tuple(b for b in bars() if b.open_ms != 30*S.HOUR_MS),"S":bars()}
    with pytest.raises(S.IncompleteAuthority,match="complete|contiguous"):
        E.derive_lifecycle(legs(),broken)

def test_first_range_stop_forces_mate_cleanup_and_gap_wins_at_cleanup_open():
    authority={"L":bars(touch_long_at=27),"S":bars(gap_short_at=28)}
    finals=E.derive_lifecycle(legs(),authority)
    assert [(f.symbol,f.phase,f.timestamp_ms,f.reference) for f in finals] == [
        ("L","stop",28*S.HOUR_MS,D(97)),("S","stop",28*S.HOUR_MS,D(104))]
    later=replace(finals[1],timestamp_ms=33*S.HOUR_MS,phase="exit")
    assert later not in finals

def test_atomic_one_leg_is_protected_and_next_open_only():
    leg=legs()[0]
    final=E.derive_lifecycle((leg,),{"L":bars()},atomic_failure=True)
    assert final[0].phase == "atomic_cleanup" and final[0].timestamp_ms == 26*S.HOUR_MS

def test_funding_is_unique_immediate_prior_and_boundary_ordered():
    plans=legs(); finals=E.derive_lifecycle(plans,{"L":bars(),"S":bars()})
    point=E.FundingPoint("L",plans[0].entry_ms,D("0.001"))
    mark=E.MarkClose("L",point.nominal_ms-S.HOUR_MS,D(100))
    result=E.derive_funding(plans,finals,(point,),(mark,))
    assert result[0].cash == D("-0.1")
    with pytest.raises(S.IncompleteAuthority,match="duplicate funding"):
        E.derive_funding(plans,finals,(point,point),(mark,))
    with pytest.raises(S.IncompleteAuthority,match="immediate strict-prior"):
        E.derive_funding(plans,finals,(point,),(replace(mark,close_ms=mark.close_ms-S.HOUR_MS),))

def test_fill_costs_are_derived_and_stress_cannot_improve():
    plan=legs()[0]; fill=E.DerivedFill(33*S.HOUR_MS,"L",-1,D(1),D(105),"exit",33*S.HOUR_MS)
    base=E.account_fill(fill,plan,bars(),stress=False); stress=E.account_fill(fill,plan,bars(),stress=True)
    assert base.movement == D(5) and base.fee > 0 and base.slippage > 0
    assert stress.slippage > base.slippage and stress.execution < base.execution

def test_close_derives_conserved_fills_and_risk_from_exact_sizing():
    sizing=S.ExactPairSizing(D(1),D(1),D(97),D(103),D(200),D(7),D("0.35"))
    closed=E.close_attempt(attempt_id="x",legs=legs(),bars={"L":bars(),"S":bars()},
                           points=(),marks=(),sizing=sizing)
    assert closed.initial_risk_cash == D(7) and len(closed.fills) == 4
    assert {(f.fill.symbol,f.fill.quantity) for f in closed.fills} == {("L",D(1)),("S",D(1))}
    hostile=replace(legs()[0],quantity=D("0.1"))
    with pytest.raises(E.EvaluatorError,match="accepted quantities"):
        E.close_attempt(attempt_id="bad",legs=(hostile,legs()[1]),bars={"L":bars(),"S":bars()},
                        points=(),marks=(),sizing=sizing)

def test_no_loss_profit_factor_and_missing_marks_fail_closed():
    with pytest.raises(E.EvaluatorError,match="undefined"):
        E.profit_factor((D(1),D(2)))
    entry=E.DerivedFill(0,"L",1,D(1),D(100),"entry",0)
    final=E.DerivedFill(S.HOUR_MS,"L",-1,D(1),D(101),"exit",S.HOUR_MS)
    fc=lambda f,m: E.FillCash(f,f.reference,m,D("0.01"),D("0.01"),D("0.0001"),None)
    row=E.ClosedAttempt("a",0,S.HOUR_MS,D(1),(fc(entry,D(0)),fc(final,D(1))),(),
                        D("0.96"),D("0.96"),D("0.9"),D("0.9"),False,("L",))
    loss=replace(row,attempt_id="b",entry_ms=2*S.HOUR_MS,close_ms=3*S.HOUR_MS,
                 base_net_cash=D(-1),base_R=D(-1),stress_net_cash=D(-1),stress_R=D(-1))
    with pytest.raises(S.IncompleteAuthority,match="marked exposure"):
        E.evaluate(E.EvaluationInput((row,loss),(),"development"))

def test_pcg64_bootstrap_is_reproducible_and_frozen():
    a=S.stationary_bootstrap_indices(3); b=S.stationary_bootstrap_indices(3)
    assert a == b and a[0][:5] == b[0][:5]
    with pytest.raises(S.C256Error,match="settings are frozen"):
        S.stationary_bootstrap_indices(3,seed=257)

def test_authority_failure_happens_before_loader_or_identity(monkeypatch,tmp_path):
    called=[]
    class Loader:
        def load(self,*,capability): called.append("loader")
    monkeypatch.setattr(A,"authenticate_before_loader",lambda: (_ for _ in ()).throw(A.ProtectedAdapterError("authority")))
    with pytest.raises(A.ProtectedAdapterError,match="authority"):
        A.evaluate_once(Loader(),identity_dir=tmp_path)
    assert called == [] and list(tmp_path.iterdir()) == []

def test_locked_cli_is_default_off(capsys):
    assert CLI.main(["--validate-locked"]) == 0
    assert '"candidate_id":257' in capsys.readouterr().out
    with pytest.raises(SystemExit): CLI.main([])
