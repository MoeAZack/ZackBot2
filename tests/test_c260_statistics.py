"""Fixed numerical examples only; these are not C260 market outcomes."""
from dataclasses import replace
import math

import numpy as np
import pytest

from tools.research import c260_statistics as S


def daily(name, value=1.):
    windows = S.C.load_proposal_metadata()["windows_half_open_utc"]
    start, end = map(S._day_ms, windows[name])
    n = (end - start) // S.C.DAY_MS
    return S.DailyPairs(name, (value,) * n, (1,) * n)


def test_constant_positive_calendar_has_exact_mean_ci_and_minimum_attainable_p():
    result = S.inference((daily("development"),), scope="development")
    assert result["closed"] == 90
    assert result["mean_net_R"] == result["ci_low"] == result["ci_high"] == 1.
    assert result["p_value"] == 1 / 10001 and result["inference_gate_pass"]
    assert not result["paper_qualified"] and not result["global_FWER_claimed"]


def test_forward_draws_preserve_each_fold_and_weight_actual_trade_counts():
    pairs = (daily("wf1", 1.), daily("wf2", 2.), daily("wf3", 3.))
    result = S.inference(pairs, scope="aggregate_forward")
    counts = [len(item.net_r) for item in pairs]
    expected = math.fsum((index + 1) * count for index, count in enumerate(counts)) / sum(counts)
    assert result["mean_net_R"] == result["ci_low"] == result["ci_high"] == expected
    assert result["closed"] == sum(counts)
    assert result["inference_gate_pass"]
    with pytest.raises(S.C.C260Locked):
        S.inference(tuple(reversed(pairs)), scope="aggregate_forward")


def test_null_upper_tail_is_inclusive_and_diagnostics_cannot_select_primary():
    result = S.inference((daily("wf1", 0.),), scope="wf1_diagnostic")
    assert result["defined"] and result["p_value"] == 1.
    assert not result["inference_gate_applicable"] and not result["inference_gate_pass"]


def test_flat_days_are_retained_and_any_zero_count_resample_fails_closed():
    template = daily("development")
    n = len(template.net_r)
    sparse = replace(template, net_r=(1.,) + (0.,) * (n - 1), closed_count=(1,) + (0,) * (n - 1))
    result = S.inference((sparse,), scope="development")
    assert result["closed"] == 1 and result["mean_net_R"] == 1.
    assert not result["defined"] and result["p_value"] is None and result["ci_low"] is None
    assert not result["inference_gate_pass"]


def test_pcg64_block_draw_order_matches_an_independent_index_oracle():
    pairs = (S.DailyPairs("toy", (1., -2., 4., 0., 8., -1., 2., 6., 3.), (1, 2, 1, 0, 3, 1, 1, 2, 1)),)
    seed, center = 26017, .3
    actual = S._distribution(pairs, seed=seed, center=center)
    random = np.random.Generator(np.random.PCG64(seed))
    draws = random.integers(0, 9, size=(10000, 2), dtype=np.int64)
    for replicate in (0, 1, 37, 9999):
        indices = [(int(draws[replicate, block]) + day) % 9 for block in range(2) for day in range(7)][:9]
        expected = math.fsum(pairs[0].net_r[i] - center * pairs[0].closed_count[i] for i in indices)
        expected /= sum(pairs[0].closed_count[i] for i in indices)
        assert actual[replicate] == expected


@pytest.mark.parametrize("attack", ["missing_day", "bool_count", "zero_count_nonzero_r", "nonfinite", "holdout"])
def test_malformed_or_sealed_calendar_cannot_generate_inference(attack):
    item = daily("development")
    if attack == "missing_day": item = replace(item, net_r=item.net_r[:-1])
    if attack == "bool_count": item = replace(item, closed_count=(True,) + item.closed_count[1:])
    if attack == "zero_count_nonzero_r": item = replace(item, closed_count=(0,) + item.closed_count[1:])
    if attack == "nonfinite": item = replace(item, net_r=(float("nan"),) + item.net_r[1:])
    if attack == "holdout": item = replace(item, fold="sealed_holdout")
    with pytest.raises((ValueError, S.C.C260Locked)):
        S.inference((item,), scope="development")


def test_empty_calendar_and_wrong_numpy_do_not_emit_success(monkeypatch):
    item = daily("development", 0.)
    item = replace(item, closed_count=(0,) * len(item.closed_count))
    result = S.inference((item,), scope="development")
    assert result["mean_net_R"] is None and not result["inference_gate_pass"]
    monkeypatch.setattr(S.np, "__version__", "different")
    with pytest.raises(S.C.C260Locked, match="NUMPY"):
        S.inference((item,), scope="development")


@pytest.mark.parametrize("net,dd,stress,stress_dd,tier,passed,multiplier", [
    (20., 15., 1., 10., "standard", True, 1.),
    (45., 30., 1., 10., "elevated", True, .5),
    (44.99, 30., 1., 10., "reject", False, 0.),
    (120., 60., 50., 50., "high", True, .25),
    (119.99, 60., 50., 50., "reject", False, 0.),
    (120., 60., 49.99, 50., "reject", False, 0.),
    (120., 60., 50., 0., "reject", False, 0.),
    (1000., 60.001, 1000., 1., "reject", False, 0.),
])
def test_frozen_high_drawdown_acceptance_and_reduced_paper_risk(net, dd, stress, stress_dd, tier, passed, multiplier):
    result = S.drawdown_tier(net, dd, stress, stress_dd)
    assert (result["tier"], result["pass"], result["future_paper_multiplier_max"]) == (tier, passed, multiplier)
