import pytest

from tools.research import carry01_runner as R


def rows(*returns):
    return [R.DailyReturn(100 + index, value, 0.0)
            for index, value in enumerate(returns)]


def test_compound_and_calendar_span_annualization_are_deterministic():
    data = rows(0.01, -0.005, 0.002)
    assert R.compound(item.portfolio for item in data) == pytest.approx(
        1.01 * 0.995 * 1.002 - 1)
    expected = (1.01 * 0.995 * 1.002) ** (R.YEAR_DAYS / 3) - 1
    assert R.annualized_return(data) == pytest.approx(expected)


def test_annualization_keeps_calendar_gaps_in_the_span():
    data = [R.DailyReturn(1, 0.01, 0), R.DailyReturn(11, 0.01, 0)]
    assert R.annualized_return(data) == pytest.approx(1.0201 ** (R.YEAR_DAYS / 11) - 1)


def test_cash_excess_compares_compounded_wealth_not_summed_rates():
    data = [R.DailyReturn(1, 0.02, 0.01), R.DailyReturn(2, -0.01, 0.01)]
    assert R.cash_excess_return(data) == pytest.approx(1.02 * 0.99 - 1.01**2)


def test_drawdown_and_recovery_include_open_underwater_period():
    curve = [(1, 100.0), (2, 110.0), (4, 88.0), (7, 111.0), (8, 90.0), (12, 95.0)]
    assert R.maximum_drawdown([value for _, value in curve]) == pytest.approx(0.20)
    assert R.longest_recovery_days(curve) == 5


def test_circular_bootstrap_is_seeded_and_positive_for_uniform_edge():
    data = [R.DailyReturn(day, 0.002, 0.001) for day in range(30)]
    first = R.circular_block_bootstrap_lcb(
        data, block_days=7, resamples=250, seed=25701, lower_percentile=2.5)
    second = R.circular_block_bootstrap_lcb(
        data, block_days=7, resamples=250, seed=25701, lower_percentile=2.5)
    assert first == second == pytest.approx(0.001)


@pytest.mark.parametrize("data,match", [
    ([], "empty"),
    ([R.DailyReturn(1, -1, 0)], "all capital"),
    ([R.DailyReturn(2, 0, 0), R.DailyReturn(2, 0, 0)], "chronological"),
])
def test_daily_return_validation_fails_closed(data, match):
    with pytest.raises(R.CarryRunnerError, match=match):
        R.validate_daily_returns(data)


def test_bootstrap_refuses_invalid_controls():
    with pytest.raises(R.CarryRunnerError, match="positive"):
        R.circular_block_bootstrap_lcb(rows(0.01), block_days=0, resamples=1,
                                       seed=1, lower_percentile=2.5)
    with pytest.raises(R.CarryRunnerError, match="between"):
        R.circular_block_bootstrap_lcb(rows(0.01), block_days=1, resamples=1,
                                       seed=1, lower_percentile=101)


def test_sleeve_opens_at_three_x_and_equal_moves_are_delta_hedged():
    costs = R.CostModel(spot_fee_bps=0, perpetual_fee_bps=0,
                        spot_slippage_bps=0, perpetual_slippage_bps=0)
    sleeve = R.SleeveState.open(5_000, 100, 100, costs)
    assert sleeve.quantity == pytest.approx(37.5)
    assert sleeve.leverage(100) == pytest.approx(3.0)
    assert sleeve.equity(120, 120) == pytest.approx(5_000)


def test_sleeve_charges_each_leg_and_funding_uses_held_short_quantity():
    costs = R.CostModel()
    sleeve = R.SleeveState.open(5_000, 100, 100, costs)
    assert sleeve.trading_cost == pytest.approx(37.5 * (100 * 0.0015 + 100 * 0.001))
    credited = sleeve.credit_funding(0.001, 102)
    assert credited == pytest.approx(37.5 * 102 * 0.001)
    ending = sleeve.close(102, 102, costs)
    assert sleeve.funding_events == 1
    assert sleeve.price_pnl_realized == pytest.approx(0)
    assert ending == pytest.approx(5_000 + credited - sleeve.trading_cost)


def test_funding_stress_weakens_receipts_and_amplifies_payments():
    zero = R.CostModel(spot_fee_bps=0, perpetual_fee_bps=0,
                       spot_slippage_bps=0, perpetual_slippage_bps=0)
    sleeve = R.SleeveState.open(1_000, 100, 100, zero)
    assert sleeve.credit_funding(0.001, 100, received_divisor=2,
                                 paid_multiplier=2) == pytest.approx(0.375)
    assert sleeve.credit_funding(-0.001, 100, received_divisor=2,
                                 paid_multiplier=2) == pytest.approx(-1.5)


def test_hourly_high_liquidation_proxy_is_conservative_and_inclusive():
    zero = R.CostModel(spot_fee_bps=0, perpetual_fee_bps=0,
                       spot_slippage_bps=0, perpetual_slippage_bps=0)
    sleeve = R.SleeveState.open(1_000, 100, 100, zero)
    boundary = (sleeve.futures_collateral + sleeve.quantity * sleeve.entry_mark) / (
        sleeve.quantity * 1.005)
    assert sleeve.liquidates_at_high(boundary - 0.001) is False
    assert sleeve.liquidates_at_high(boundary) is True


def test_cost_model_and_sleeve_refuse_invalid_numbers():
    with pytest.raises(R.CarryRunnerError, match="negative"):
        R.CostModel(spot_fee_bps=-1).validate()
    with pytest.raises(R.CarryRunnerError, match="positive"):
        R.SleeveState.open(1_000, 0, 100, R.CostModel())
