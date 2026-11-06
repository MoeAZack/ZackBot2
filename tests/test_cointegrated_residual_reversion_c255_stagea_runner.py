import math
from pathlib import Path

import numpy as np
import pytest

from tools.research import cointegrated_residual_reversion_c255_stagea as C


def bars(start=0, count=30, base=100.0):
    return [
        C.Bar(start + i * C.HOUR_MS, base + i, base + i + 2, base + i - 2, base + i + 1, 1_000_000)
        for i in range(count)
    ]


def pair_sample(seed, beta=1.2, n=4320):
    rng = np.random.default_rng(seed)
    log_x = 4.0 + np.cumsum(rng.normal(0, 0.002, n))
    residual = np.empty(n)
    residual[0] = 0
    for i in range(1, n):
        residual[i] = 0.95 * residual[i - 1] + rng.normal(0, 0.002)
    log_y = 0.3 + beta * log_x + residual
    x, y = np.exp(log_x), np.exp(log_y)
    def tape(values):
        return [C.Bar(i * C.HOUR_MS, value, value * 1.01, value * 0.99, value, 1_000_000)
                for i, value in enumerate(values)]
    return C.build_training_sample(tape(x), tape(y), selection_ms=n * C.HOUR_MS)


def frozen_adf(values, **kwargs):
    assert kwargs == {"regression": "n", "maxlag": 24, "autolag": "BIC"}
    assert len(values) >= 3
    return (-5.0, 0.001, 24, len(values) - 25)


def test_contract_validates_but_every_real_evaluation_request_is_locked():
    assert C.load_frozen_contract()["candidate_id"] == 255
    with pytest.raises(C.EvaluationLocked, match="no market, outcome"):
        C.assert_real_evaluation_authorized(gate={"enabled": True, "protected": True})


def test_contract_byte_tamper_fails_before_authority_or_outcome_access(tmp_path, monkeypatch):
    tampered = tmp_path / "contract.json"
    tampered.write_text("{}", encoding="utf-8")
    monkeypatch.setattr(C, "CONTRACT_PATH", tampered)
    with pytest.raises(C.IncompleteAuthority, match="reviewed head"):
        C.load_frozen_contract()


def test_membership_uses_latest_monday_ms_not_label_or_future_cell():
    cells = [
        {"monday_ms": 100, "monday_utc": "wrong-label", "symbols": ["A"]},
        {"monday_ms": 200, "monday_utc": "older-looking-label", "symbols": ["B"]},
        {"monday_ms": 300, "monday_utc": "future", "symbols": ["C"]},
    ]
    assert C.latest_membership_cell(cells, 250)["symbols"] == ["B"]
    with pytest.raises(C.IncompleteAuthority, match="duplicate"):
        C.latest_membership_cell(cells + [{"monday_ms": 200}], 250)


def test_exact_intersection_never_fills_gaps_and_refuses_duplicates_or_off_grid():
    x = bars(count=4)
    y = [bars(count=4)[i] for i in (0, 2, 3)]
    joined = C.exact_bar_intersection(x, y, start_ms=0, end_ms=4 * C.HOUR_MS)
    assert [left.open_ms for left, _ in joined] == [0, 2 * C.HOUR_MS, 3 * C.HOUR_MS]
    with pytest.raises(C.IncompleteAuthority, match="duplicate"):
        C.exact_bar_intersection(x + [x[0]], y, start_ms=0, end_ms=4 * C.HOUR_MS)
    bad = C.Bar(1, 1, 2, 0.5, 1.5)
    with pytest.raises(C.IncompleteAuthority, match="non-hourly"):
        C.exact_bar_intersection([bad], [bad], start_ms=0, end_ms=C.HOUR_MS)


def test_pair_enumeration_orientation_adf_interface_and_holm_are_deterministic():
    assert C.deterministic_pairs(["Z", "A", "B", "A"]) == (("A", "B"), ("A", "Z"), ("B", "Z"))
    assert C.adf_pvalue([0, 1, 0, -1], frozen_adf) == 0.001
    adjusted = C.holm_adjust({("A", "B"): 0.01, ("C", "D"): 0.02, ("E", "F"): 0.20})
    assert adjusted == {("A", "B"): 0.03, ("C", "D"): 0.04, ("E", "F"): 0.20}
    with pytest.raises(C.C255Error, match="orientation"):
        C.select_monthly_pairs({("Z", "A"): pair_sample(1)}, frozen_adf)


def test_training_sample_uses_exact_180_day_grid_and_995_percent_completeness():
    sample = pair_sample(8)
    assert len(sample.aligned) == 4320
    x = [row[0] for row in sample.aligned]
    y = [row[1] for row in sample.aligned]
    # Removing 22 rows drops below ceil(4320 * .995) = 4299.
    with pytest.raises(C.IncompleteAuthority, match="completeness"):
        C.build_training_sample(x[:-22], y[:-22], selection_ms=sample.selection_ms)


def test_monthly_selection_freezes_models_and_greedily_prevents_shared_symbols():
    samples = {
        ("A", "B"): pair_sample(1),
        ("A", "C"): pair_sample(2),
        ("D", "E"): pair_sample(3),
    }
    selected = C.select_monthly_pairs(samples, frozen_adf)
    assert len(selected) == 2
    symbols = [symbol for model in selected for symbol in (model.x, model.y)]
    assert len(symbols) == len(set(symbols))
    assert all(0.2 < model.beta < 5 and 6 <= model.half_life_hours <= 72 for model in selected)


def test_signal_cost_floor_stop_guard_and_next_open_are_causal():
    assert C.entry_signal(-2.6, displacement_log_units=0.008) == "long_spread"
    assert C.entry_signal(2.6, displacement_log_units=0.008) == "short_spread"
    assert C.entry_signal(3.0, displacement_log_units=0.0069) is None
    assert C.entry_signal(4.5, displacement_log_units=0.02) is None
    tape = bars(count=3)
    assert C.next_open_fill(tape, 0).open_ms == C.HOUR_MS
    with pytest.raises(C.IncompleteAuthority, match="next 1h open"):
        C.next_open_fill([tape[0], tape[2]], 0)


def test_atr24_uses_true_range_seed_then_wilder_updates_and_requires_previous_close():
    tape = bars(count=25)
    # Every true range is 4, so both seed and final ATR are exactly 4.
    assert C.atr24_wilder(tape) == pytest.approx(4.0)
    with pytest.raises(C.IncompleteAuthority, match="24 true ranges"):
        C.atr24_wilder(tape[:24])


def test_catastrophic_stops_use_worse_of_stop_or_gap_then_adverse_slippage():
    long_gap = C.Bar(0, 90, 95, 88, 92)
    # Entry 100, ATR 2 => stop 94; gap opened at 90, never grant the stop price.
    assert C.catastrophic_stop_fill(side="long", entry_price=100, frozen_atr24=2, bar=long_gap) == pytest.approx(90 * 0.9998)
    short_gap = C.Bar(0, 110, 112, 108, 109)
    assert C.catastrophic_stop_fill(side="short", entry_price=100, frozen_atr24=2, bar=short_gap) == pytest.approx(110 * 1.0002)
    untouched = C.Bar(0, 100, 103, 97, 101)
    assert C.catastrophic_stop_fill(side="long", entry_price=100, frozen_atr24=2, bar=untouched) is None


def test_actual_fill_cost_uses_each_fill_notional_and_atomic_failure_is_a_closed_attempt():
    fills = [
        C.Fill("A", 600, price_move_pnl=-3, funding_pnl=-0.2),
        C.Fill("B", 400, price_move_pnl=1, funding_pnl=0.1),
        C.Fill("A", 570, price_move_pnl=-2),
    ]
    expected = (600 + 400 + 570) * 7 / 10_000
    assert C.actual_fill_cost(fills) == pytest.approx(expected)
    attempt = C.account_atomic_attempt("A/B", fills, both_legs_placed=False)
    assert attempt.atomicity_failure and attempt.closed_attempts == 1
    assert attempt.costs == pytest.approx(expected)
    assert attempt.net_pnl == pytest.approx(-4.1 - expected)


def test_risk_model_and_portfolio_caps_fail_closed_without_mutating_on_refusal():
    loss = C.modeled_loss_fraction(
        beta=1.2, z_entry=2.6, residual_std=0.02,
        y_atr_fraction=0.01, x_atr_fraction=0.015,
        y_other_leg_flatten_fraction=0.005, x_other_leg_flatten_fraction=0.006,
    )
    assert math.isfinite(loss) and loss > 0.003
    state = C.PortfolioState(equity=4_000)
    assert state.admit(x="A", y="B", proposed_gross=2_000, proposed_risk_r=1)
    state.commit(x="A", y="B", gross=2_000, risk_r=1)
    before = (state.open_pair_gross, state.open_risk_r, state.open_pairs, set(state.symbols))
    assert not state.admit(x="A", y="C", proposed_gross=500, proposed_risk_r=1)
    assert not state.admit(x="C", y="D", proposed_gross=2_001, proposed_risk_r=1)
    assert before == (state.open_pair_gross, state.open_risk_r, state.open_pairs, state.symbols)
