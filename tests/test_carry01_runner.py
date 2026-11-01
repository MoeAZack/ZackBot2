from types import SimpleNamespace

import pytest

from tools.research import carry01_runner as R


def rows(*returns):
    return [R.DailyReturn(100 + index, value, 0.0)
            for index, value in enumerate(returns)]


def bar(opened, close=100.0, high=101.0):
    return R.HourlyBar(opened, opened + R.HOUR_MS - 1, close, high)


def observation(opened, btc=100.0, eth=50.0, btc_high=None, eth_high=None):
    return R.BasketObservation(
        opened,
        {"BTC": btc, "ETH": eth},
        {"BTC": btc, "ETH": eth},
        {"BTC": btc if btc_high is None else btc_high,
         "ETH": eth if eth_high is None else eth_high},
    )


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


def test_basket_is_equal_weight_and_one_sleeve_triggers_atomic_rebalance():
    zero = R.CostModel(spot_fee_bps=0, perpetual_fee_bps=0,
                       spot_slippage_bps=0, perpetual_slippage_bps=0)
    start = {"BTC": (100.0, 100.0), "ETH": (50.0, 50.0)}
    basket = R.BasketState.open(10_000, start, zero)
    assert basket.equity(start) == pytest.approx(10_000)
    moved = {"BTC": (130.0, 130.0), "ETH": (50.0, 50.0)}
    assert basket.rebalances_required(moved) is True
    before = basket.equity(moved)
    after = basket.rebalance(moved)
    assert after == pytest.approx(before)
    assert basket.rebalances == 1
    assert all(sleeve.leverage(moved[symbol][1]) == pytest.approx(3)
               for symbol, sleeve in basket.sleeves.items())


def test_basket_rebalance_charges_four_fills_per_sleeve():
    costs = R.CostModel()
    prices = {"BTC": (100.0, 100.0), "ETH": (50.0, 50.0)}
    basket = R.BasketState.open(10_000, prices, costs)
    opening_cost = basket.trading_cost
    before = basket.equity(prices)
    after = basket.rebalance(prices)
    assert basket.trading_cost > opening_cost * 2.9
    assert basket.turnover > 0
    assert after < before
    assert basket.rebalances == 1


def test_basket_funding_and_liquidation_are_symbol_specific():
    zero = R.CostModel(spot_fee_bps=0, perpetual_fee_bps=0,
                       spot_slippage_bps=0, perpetual_slippage_bps=0)
    prices = {"BTC": (100.0, 100.0), "ETH": (50.0, 50.0)}
    basket = R.BasketState.open(10_000, prices, zero)
    amount = basket.credit_funding("BTC", 0.001, 100)
    assert amount > 0
    assert basket.funding_events == 1
    assert basket.funding_pnl == pytest.approx(amount)
    highs = {"BTC": 140.0, "ETH": 50.0}
    assert basket.liquidated_symbols(highs) == ["BTC"]
    with pytest.raises(R.CarryRunnerError, match="unknown"):
        basket.credit_funding("SOL", 0.001, 1)


def test_funding_uses_exact_strictly_earlier_mark_and_never_older_substitute():
    nominal = 10 * R.HOUR_MS
    required = 9 * R.HOUR_MS
    event = R.FundingEvent("BTC", nominal, 0.001)
    assert R.required_funding_mark_open(nominal) == required
    assert R.funding_settlement_mark(event, {required: bar(required, close=123, high=124)}) == 123
    with pytest.raises(R.CarryRunnerError, match="exact prior"):
        R.funding_settlement_mark(event, {required - R.HOUR_MS: bar(required - R.HOUR_MS)})


def test_causal_ledger_delays_bars_until_close_and_funding_by_five_minutes():
    opened = 8 * R.HOUR_MS
    funding = R.FundingEvent("BTC", 9 * R.HOUR_MS, 0.001)
    ledger = R.causal_event_order(
        {"BTC": [bar(opened)]}, {"BTC": [bar(opened)]}, [funding])
    assert [(item.kind, item.available_time) for item in ledger] == [
        ("spot", 9 * R.HOUR_MS),
        ("mark", 9 * R.HOUR_MS),
        ("funding", 9 * R.HOUR_MS + R.FUNDING_DELAY_MS),
    ]


def test_causal_ledger_refuses_mismatched_symbols_and_noncanonical_bars():
    with pytest.raises(R.CarryRunnerError, match="symbol sets"):
        R.causal_event_order({"BTC": [bar(0)]}, {"ETH": [bar(0)]}, [])
    bad = R.HourlyBar(1, R.HOUR_MS, 100, 101)
    with pytest.raises(R.CarryRunnerError, match="canonical"):
        R.causal_event_order({"BTC": [bad]}, {"BTC": [bad]}, [])


def test_fold_replay_is_fresh_start_cost_and_funding_aware():
    obs = [observation(0), observation(R.HOUR_MS), observation(2 * R.HOUR_MS)]
    event = R.FundingEvent("BTC", R.HOUR_MS, 0.001)
    result = R.replay_fold(obs, [R.FundingCredit(event, 100)])
    assert result.status == "COMPLETE"
    assert result.funding_events == 1
    assert result.funding_pnl > 0
    assert result.trading_cost > result.funding_pnl
    assert result.ending_equity < result.starting_equity
    assert sum(value for _, value in result.asset_net_pnl) == pytest.approx(
        result.ending_equity - result.starting_equity)
    assert result.turnover > 0
    assert result.minimum_margin_headroom > 0


def test_fold_replay_supports_one_observation_rebalance_latency():
    obs = [observation(0), observation(R.HOUR_MS, btc=130),
           observation(2 * R.HOUR_MS, btc=130)]
    immediate = R.replay_fold(obs, rebalance_latency_hours=0, funding=[])
    delayed = R.replay_fold(obs, rebalance_latency_hours=1, funding=[])
    assert immediate.rebalances == delayed.rebalances == 1
    assert immediate.equity_curve[1][1] != delayed.equity_curve[1][1]


def test_fold_replay_fails_on_hourly_high_liquidation_before_close():
    obs = [observation(0), observation(R.HOUR_MS, btc_high=140)]
    result = R.replay_fold(obs, [])
    assert result.status == "LIQUIDATED"
    assert result.ending_equity is None
    assert result.liquidated_symbols == ("BTC",)


def test_fold_replay_refuses_boundary_and_order_errors():
    with pytest.raises(R.CarryRunnerError, match="at least two"):
        R.replay_fold([observation(0)], [])
    with pytest.raises(R.CarryRunnerError, match="chronological"):
        R.replay_fold([observation(R.HOUR_MS), observation(0)], [])


def test_daily_returns_include_open_and_exit_costs_without_duplicate_endpoint():
    obs = [observation(0), observation(R.HOUR_MS), observation(2 * R.HOUR_MS)]
    replay = R.replay_fold(obs, [])
    assert len(replay.equity_curve) == len(obs)
    assert len({timestamp for timestamp, _ in replay.equity_curve}) == len(obs)
    daily = R.daily_returns_from_replay(replay)
    assert len(daily) == 1
    assert 1 + daily[0].portfolio == pytest.approx(
        replay.ending_equity / replay.starting_equity)
    assert daily[0].cash > 0


def test_daily_returns_never_bridge_missing_hour():
    replay = R.FoldReplay(
        "COMPLETE", 100.0, 99.0,
        ((R.HOUR_MS, 100.0), (2 * R.HOUR_MS, 101.0), (4 * R.HOUR_MS, 99.0)),
        0, 0, 1, 0, 0,
    )
    daily = R.daily_returns_from_replay(replay, cash_apr=0)
    assert len(daily) == 1
    assert daily[0].portfolio == pytest.approx(0.01)


def test_daily_returns_refuse_liquidated_or_duplicate_curve():
    liquidated = R.FoldReplay("LIQUIDATED", 100, None, ((1, 50),), 0, 0, 0, 0, 0)
    with pytest.raises(R.CarryRunnerError, match="complete"):
        R.daily_returns_from_replay(liquidated)
    duplicate = R.FoldReplay("COMPLETE", 100, 100, ((R.HOUR_MS, 100),
                              (R.HOUR_MS, 100)), 0, 0, 0, 0, 0)
    with pytest.raises(R.CarryRunnerError, match="chronological"):
        R.daily_returns_from_replay(duplicate)


def test_stress_suite_is_complete_fresh_start_and_directionally_conservative():
    obs = [observation(0), observation(R.HOUR_MS), observation(2 * R.HOUR_MS)]
    funding = [R.FundingCredit(R.FundingEvent("BTC", R.HOUR_MS, 0.01), 100)]
    suite = R.replay_stress_suite(obs, funding)
    assert set(suite) == {
        "base_x1", "cost_x1.5", "cost_x2", "funding_zero",
        "funding_sign_reversal", "rebalance_latency_1h",
    }
    assert suite["cost_x2"].trading_cost > suite["cost_x1.5"].trading_cost > suite["base_x1"].trading_cost
    assert suite["cost_x2"].funding_pnl < suite["cost_x1.5"].funding_pnl < suite["base_x1"].funding_pnl
    assert suite["funding_zero"].funding_pnl == 0
    assert suite["funding_sign_reversal"].funding_pnl < 0
    assert all(result.starting_equity == 10_000 for result in suite.values())


def test_fold_inputs_are_synchronous_and_do_not_cross_boundaries_or_gaps():
    def history(spot_missing=()):
        spot = [{"open_time": t, "close": 100.0, "high": 101.0}
                for t in range(0, 6 * R.HOUR_MS, R.HOUR_MS) if t not in spot_missing]
        mark = [{"open_time": t, "close": 100.0, "high": 101.0}
                for t in range(0, 6 * R.HOUR_MS, R.HOUR_MS)]
        funding = [
            {"funding_time": 0, "funding_rate": 0.001},
            {"funding_time": 2 * R.HOUR_MS, "funding_rate": 0.001},
            {"funding_time": 6 * R.HOUR_MS, "funding_rate": 0.001},
        ]
        return SimpleNamespace(start_ms=0, end_ms=6 * R.HOUR_MS,
                               spot=SimpleNamespace(rows=spot),
                               mark=SimpleNamespace(rows=mark), funding=funding)
    inputs = R.build_fold_inputs({"BTC": history(),
                                  "ETH": history((2 * R.HOUR_MS,))},
                                 0, 6 * R.HOUR_MS)
    assert [item.open_time for item in inputs.observations] == [
        0, R.HOUR_MS, 3 * R.HOUR_MS, 4 * R.HOUR_MS,
    ]
    assert [(item.event.available_time, item.event.symbol) for item in inputs.funding] == [
        (2 * R.HOUR_MS + R.FUNDING_DELAY_MS, "BTC"),
        (2 * R.HOUR_MS + R.FUNDING_DELAY_MS, "ETH"),
    ]
    assert inputs.incomplete_funding_events == 0


def test_fold_inputs_count_missing_exact_funding_mark():
    rows = [{"open_time": t, "close": 100.0, "high": 101.0}
            for t in range(0, 5 * R.HOUR_MS, R.HOUR_MS) if t != R.HOUR_MS]
    history = SimpleNamespace(
        start_ms=0, end_ms=5 * R.HOUR_MS,
        spot=SimpleNamespace(rows=rows), mark=SimpleNamespace(rows=rows),
        funding=[{"funding_time": 2 * R.HOUR_MS, "funding_rate": 0.001}],
    )
    inputs = R.build_fold_inputs({"BTC": history}, 0, 5 * R.HOUR_MS)
    assert inputs.funding == ()
    assert inputs.incomplete_funding_events == 1
