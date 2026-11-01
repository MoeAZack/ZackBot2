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
