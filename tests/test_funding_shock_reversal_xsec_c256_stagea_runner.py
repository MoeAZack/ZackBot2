import math

import pytest

from tools.research import funding_shock_reversal_xsec_c256_stagea as C
from tools.research import run_funding_shock_reversal_xsec_c256_stagea as CLI


def funding_tape(symbol="A", *, start=0, interval_hours=8, rates=None):
    rates = rates if rates is not None else [(-1) ** i * (1 + (i % 5)) * 1e-5 for i in range(31)]
    return [
        C.FundingRow(symbol, start + i * interval_hours * C.HOUR_MS,
                     start + i * interval_hours * C.HOUR_MS + 60_000, rate, interval_hours)
        for i, rate in enumerate(rates)
    ]


def bar(hour, *, open_price=100, high=102, low=98, close=101, volume=1_000_000):
    return C.HourBar(hour * C.HOUR_MS, open_price, high, low, close, volume)


def shock(symbol, z, nominal=0):
    return C.FundingShock(symbol, nominal, z * 1e-5, 0, 1e-5, z)


def test_contract_is_byte_bound_and_real_evaluation_has_no_boolean_escape_hatch():
    contract = C.load_frozen_contract()
    assert contract["candidate_id"] == 256
    assert contract["base_commit"] == C.CONTRACT_BASE
    with pytest.raises(C.EvaluationLocked, match="market, outcome, identity"):
        C.assert_real_evaluation_authorized(gate={"enabled": True, "approved": True})


def test_contract_tamper_fails_before_any_evaluation_authority(monkeypatch, tmp_path):
    hostile = tmp_path / "contract.json"
    hostile.write_text("{}", encoding="utf-8")
    monkeypatch.setattr(C, "CONTRACT_PATH", hostile)
    with pytest.raises(C.IncompleteAuthority, match="reviewed head"):
        C.load_frozen_contract()


def test_robust_surprise_uses_exactly_30_strictly_prior_normalized_observations():
    history = funding_tape()
    nominal = history[-1].nominal_ms + 8 * C.HOUR_MS
    current = C.FundingRow("A", nominal, nominal + 60_000, 0.001, 8)
    result = C.robust_funding_surprise(current, history, decision_ms=nominal + C.FUNDING_DELAY_MS)
    assert result.symbol == "A" and result.z > 2
    with pytest.raises(C.IncompleteAuthority, match="31 rows"):
        C.robust_funding_surprise(current, history[1:], decision_ms=nominal + C.FUNDING_DELAY_MS)
    future = list(history)
    future[-1] = C.FundingRow("A", future[-1].nominal_ms, nominal + C.FUNDING_DELAY_MS + 1, future[-1].rate, 8)
    with pytest.raises(C.IncompleteAuthority, match="future or unavailable"):
        C.robust_funding_surprise(current, future, decision_ms=nominal + C.FUNDING_DELAY_MS)


@pytest.mark.parametrize("hours", [3, 9])
def test_funding_cadence_ambiguity_fails_closed(hours):
    history = funding_tape()
    nominal = history[-1].nominal_ms + hours * C.HOUR_MS
    current = C.FundingRow("A", nominal, nominal + 60_000, 0.001, hours)
    with pytest.raises(C.IncompleteAuthority, match="1/2/4/8"):
        C.robust_funding_surprise(current, history, decision_ms=nominal + C.FUNDING_DELAY_MS)


def test_verified_cadence_transition_is_rejected_as_ambiguous_even_when_both_intervals_are_allowed():
    history = funding_tape(interval_hours=8)
    nominal = history[-1].nominal_ms + 4 * C.HOUR_MS
    current = C.FundingRow("A", nominal, nominal + 60_000, 0.001, 4)
    with pytest.raises(C.IncompleteAuthority, match="changed funding cadence"):
        C.robust_funding_surprise(current, history, decision_ms=nominal + C.FUNDING_DELAY_MS)


def test_interval_authority_must_match_timestamp_gap():
    history = funding_tape(interval_hours=8)
    hostile = list(history)
    row = hostile[-1]
    hostile[-1] = C.FundingRow(row.symbol, row.nominal_ms, row.available_ms, row.rate, 4)
    nominal = history[-1].nominal_ms + 8 * C.HOUR_MS
    current = C.FundingRow("A", nominal, nominal + 60_000, 0.001, 8)
    with pytest.raises(C.IncompleteAuthority, match="interval_hours authority|changed funding cadence"):
        C.robust_funding_surprise(current, hostile, decision_ms=nominal + C.FUNDING_DELAY_MS)


def test_bool_cadence_is_not_accepted_as_integer_one_hour():
    history = funding_tape(interval_hours=1)
    row = history[10]
    history[10] = C.FundingRow(row.symbol, row.nominal_ms, row.available_ms, row.rate, True)
    nominal = history[-1].nominal_ms + C.HOUR_MS
    current = C.FundingRow("A", nominal, nominal + 60_000, 0.001, 1)
    with pytest.raises(C.IncompleteAuthority, match="interval authority"):
        C.robust_funding_surprise(current, history, decision_ms=nominal + C.FUNDING_DELAY_MS)


def test_pairing_is_ranked_deterministic_and_never_repairs_a_skipped_index():
    values = [shock(f"N{i:02}", 0.0) for i in range(6)] + [
        shock("L2", -3), shock("L1", -4), shock("L3", -2.5),
        shock("S2", 3), shock("S1", 4), shock("S3", 2.5),
    ]
    pairs = C.fixed_index_pairs(values)
    assert [(p.long_symbol, p.short_symbol) for p in pairs] == [
        ("L1", "S1"), ("L2", "S2"), ("L3", "S3")
    ]
    decisions = C.decide_indexed_pairs(pairs, infeasible_symbols={"S2"})
    assert [(d.pair.index, d.accepted) for d in decisions] == [(1, True), (2, False), (3, True)]
    assert decisions[2].pair.long_symbol == "L3"  # no S3/L2 repair


def test_small_cohort_and_one_sided_tail_are_zero_trade_without_backfill():
    assert C.fixed_index_pairs([shock(f"X{i}", -3) for i in range(11)]) == ()
    assert C.fixed_index_pairs([shock(f"X{i}", -3) for i in range(12)]) == ()


def test_entry_is_next_open_after_t_plus_300_and_fixed_exit_is_eight_hours_later():
    bars = [bar(i) for i in range(12)]
    entry = C.entry_bar(bars, 0)
    assert entry.open_ms == C.HOUR_MS
    assert C.fixed_exit_bar(bars, entry.open_ms).open_ms == 9 * C.HOUR_MS
    with pytest.raises(C.IncompleteAuthority, match="required next-open"):
        C.fixed_exit_bar(bars[:9], entry.open_ms)


def test_stops_use_raw_worse_gap_and_range_close_timestamps():
    gap = bar(4, open_price=90, high=95, low=88, close=92)
    event = C.protective_stop_event(side="long", stop_price=94, bar=gap)
    assert (event.timestamp_ms, event.reference_price, event.kind, event.funding_inclusive_at_exit) == (
        4 * C.HOUR_MS, 90, "GAP_STOP", False
    )
    touched = bar(5, open_price=100, high=102, low=93, close=95)
    event = C.protective_stop_event(side="long", stop_price=94, bar=touched)
    assert event.timestamp_ms == 6 * C.HOUR_MS and event.reference_price == 94
    assert event.kind == "RANGE_STOP" and event.funding_inclusive_at_exit
    short_gap = bar(6, open_price=110, high=112, low=108, close=109)
    assert C.protective_stop_event(side="short", stop_price=106, bar=short_gap).reference_price == 110
    with pytest.raises(C.C256Error, match="finite and positive"):
        C.protective_stop_event(side="long", stop_price=math.nan, bar=gap)


def test_funding_boundaries_follow_entry_open_exit_open_and_range_stop_order():
    entry, exit_ = C.HOUR_MS, 9 * C.HOUR_MS
    assert C.settlement_is_in_position(nominal_ms=entry, entry_ms=entry, exit_ms=exit_)
    assert not C.settlement_is_in_position(nominal_ms=exit_, entry_ms=entry, exit_ms=exit_)
    assert C.settlement_is_in_position(
        nominal_ms=exit_, entry_ms=entry, exit_ms=exit_, range_stop_exit=True
    )


def test_fill_ledger_charges_slippage_and_fee_once_from_raw_reference_pnl():
    entry = C.make_fill(timestamp_ms=1, symbol="A", side="buy", quantity=2,
                        reference_price=100, slippage_bps=2, impact_ratio=0.0001)
    exit_ = C.make_fill(timestamp_ms=2, symbol="A", side="sell", quantity=2,
                        reference_price=110, slippage_bps=2, impact_ratio=0.0001)
    raw = C.raw_move_pnl(position_side="long", quantity=2, entry_reference=100, exit_reference=110)
    expected = raw - entry.slippage_dollars - exit_.slippage_dollars - entry.fee_dollars - exit_.fee_dollars
    assert C.net_pnl(raw_movement=raw, fills=[entry, exit_], funding_pnl=0) == pytest.approx(expected)
    assert entry.execution_price == pytest.approx(100.02)
    assert exit_.execution_price == pytest.approx(109.978)


def test_signed_funding_and_stress_never_flip_or_improve_debits():
    assert C.signed_funding_pnl(position_side="long", quantity=2, mark_price=100, funding_rate=.001) == -0.2
    assert C.signed_funding_pnl(position_side="short", quantity=2, mark_price=100, funding_rate=.001) == 0.2
    assert C.stressed_funding(-0.2) == pytest.approx(-0.3)
    assert C.stressed_funding(0.2) == pytest.approx(0.2 / 1.5)


def test_impact_uses_only_strictly_prior_volume_and_required_exits_always_book():
    volumes = [C.VolumeBar(C.HOUR_MS, 100_000), C.VolumeBar(2 * C.HOUR_MS, 1)]
    ratio, bps, state = C.impact_terms(
        quantity=.5, reference_price=100, fill_ms=2 * C.HOUR_MS,
        volumes=volumes, required_exit=False,
    )
    assert ratio == .0005 and bps == 2 and state is None
    with pytest.raises(C.IncompleteAuthority, match="impact cap"):
        C.impact_terms(quantity=2, reference_price=100, fill_ms=2 * C.HOUR_MS,
                       volumes=volumes, required_exit=False)
    ratio, bps, state = C.impact_terms(
        quantity=2, reference_price=100, fill_ms=2 * C.HOUR_MS,
        volumes=volumes, required_exit=True,
    )
    assert ratio == .002 and bps == 4 and state == "INCOMPLETE_IMPACT_LIMIT"
    _, bps, state = C.impact_terms(
        quantity=2, reference_price=100, fill_ms=0, volumes=[], required_exit=True,
    )
    assert bps == 2 and state == "INCOMPLETE_IMPACT_AUTHORITY"
    with pytest.raises(C.C256Error, match="finite and positive"):
        C.impact_terms(quantity=math.nan, reference_price=100, fill_ms=0,
                       volumes=volumes, required_exit=False)
    with pytest.raises(C.C256Error, match="finite and positive"):
        C.impact_terms(quantity=-1, reference_price=100, fill_ms=0,
                       volumes=volumes, required_exit=False)


def test_aggregate_dual_stop_risk_and_portfolio_caps_are_enforced():
    loss = C.modeled_pair_loss(
        long_quantity=2, long_entry=100, long_stop=94,
        short_quantity=1, short_entry=100, short_stop=106,
    )
    assert loss > 18  # both raw stop moves plus stressed four-fill costs
    risk = C.pair_risk_r(loss, 4_000)
    assert risk < 1
    assert C.frozen_risk_dollars(4_000) == 40
    with pytest.raises(TypeError):
        C.pair_risk_r(loss, 4_000, risk_fraction=1)
    assert C.admit_pair(proposed_modeled_loss=loss, open_modeled_loss=80, open_pairs=2,
                        proposed_gross=1_000, open_gross=4_000, equity=4_000)
    assert not C.admit_pair(proposed_modeled_loss=40.01, open_modeled_loss=0, open_pairs=0,
                            proposed_gross=1_000, open_gross=0, equity=4_000)
    assert not C.admit_pair(proposed_modeled_loss=20, open_modeled_loss=108, open_pairs=2,
                            proposed_gross=1_000, open_gross=4_000, equity=4_000)


def test_modeled_pair_loss_matches_exact_four_fill_stressed_ledger():
    loss = C.modeled_pair_loss(
        long_quantity=2, long_entry=100, long_stop=94,
        short_quantity=3, short_entry=50, short_stop=53,
    )
    fills = [
        C.make_fill(timestamp_ms=0, symbol="L", side="buy", quantity=2,
                    reference_price=100, slippage_bps=10, impact_ratio=0),
        C.make_fill(timestamp_ms=1, symbol="L", side="sell", quantity=2,
                    reference_price=94, slippage_bps=10, impact_ratio=0),
        C.make_fill(timestamp_ms=0, symbol="S", side="sell", quantity=3,
                    reference_price=50, slippage_bps=10, impact_ratio=0),
        C.make_fill(timestamp_ms=1, symbol="S", side="buy", quantity=3,
                    reference_price=53, slippage_bps=10, impact_ratio=0),
    ]
    expected = 2 * 6 + 3 * 3 + sum(fill.fee_dollars + fill.slippage_dollars for fill in fills)
    assert loss == pytest.approx(expected)
    with pytest.raises(C.C256Error, match="adverse"):
        C.modeled_pair_loss(long_quantity=1, long_entry=100, long_stop=101,
                            short_quantity=1, short_entry=100, short_stop=105)
    with pytest.raises(TypeError):
        C.modeled_pair_loss(long_quantity=1, long_entry=100, long_stop=95,
                            short_quantity=1, short_entry=100, short_stop=105,
                            slippage_bps=1)


def test_pair_sizing_floors_both_legs_and_shrinks_to_aggregate_one_r():
    sized = C.size_equal_gross_pair(
        equity=1_000, target_gross=500,
        long_entry=100, long_stop=80.07, long_tick=.1, long_step=.1, long_min_qty=.1, long_max_qty=100, long_min_notional=5,
        short_entry=50, short_stop=60.01, short_tick=.1, short_step=.1, short_min_qty=.1, short_max_qty=100, short_min_notional=5,
    )
    assert sized.risk_r <= 1
    assert sized.gross <= 500
    assert sized.long_quantity * 10 == pytest.approx(round(sized.long_quantity * 10))
    assert sized.short_quantity * 10 == pytest.approx(round(sized.short_quantity * 10))
    assert sized.long_stop == pytest.approx(80.0)
    assert sized.short_stop == pytest.approx(60.1)
    with pytest.raises(C.IncompleteAuthority, match="exchange feasibility"):
        C.size_equal_gross_pair(
            equity=1_000, target_gross=10,
            long_entry=100, long_stop=99, long_tick=.1, long_step=.1, long_min_qty=1, long_max_qty=100, long_min_notional=100,
            short_entry=100, short_stop=101, short_tick=.1, short_step=.1, short_min_qty=1, short_max_qty=100, short_min_notional=100,
        )
    with pytest.raises(TypeError):
        C.size_equal_gross_pair(
            equity=1_000, target_gross=100,
            long_entry=100, long_stop=99, long_tick=.1, long_step=.1, long_min_qty=.1, long_max_qty=100, long_min_notional=5,
            short_entry=100, short_stop=101, short_tick=.1, short_step=.1, short_min_qty=.1, short_max_qty=100, short_min_notional=5,
            risk_fraction=1,
        )


@pytest.mark.parametrize(
    ("side", "raw", "entry", "tick", "message"),
    [
        ("long", 101, 100, .1, "below entry"),
        ("short", 99, 100, .1, "above entry"),
        ("long", math.nan, 100, .1, "finite and positive"),
        ("short", 101, 100, 0, "finite and positive"),
    ],
)
def test_stop_rounding_rejects_wrong_orientation_and_invalid_tick(side, raw, entry, tick, message):
    with pytest.raises(C.C256Error, match=message):
        C.round_protective_stop(side=side, raw_stop=raw, entry=entry, tick_size=tick)


def test_filter_binding_is_strictly_prior_and_frozen_per_fold():
    rows = [C.FilterSnapshot(100, 90, "old"), C.FilterSnapshot(200, 199, "new"), C.FilterSnapshot(199, 201, "late")]
    assert C.freeze_filter_snapshot(rows, 200).digest == "old"
    assert C.freeze_filter_snapshot(rows, 201).digest == "new"
    with pytest.raises(C.IncompleteAuthority, match="strictly before"):
        C.freeze_filter_snapshot(rows, 90)


@pytest.mark.parametrize("count", [-1, True, 1.5])
def test_portfolio_open_pair_count_requires_exact_nonnegative_integer(count):
    with pytest.raises(C.C256Error, match="exact non-negative integer"):
        C.admit_pair(proposed_modeled_loss=5, open_modeled_loss=0, open_pairs=count,
                     proposed_gross=100, open_gross=0, equity=1_000)


def test_split_enforcement_never_allows_sealed_holdout_even_with_caller_flag():
    splits = C.frozen_splits()
    wf_start, wf_end, _ = splits["wf1"]
    assert C.enforce_split(wf_start, "wf1") == "ELIGIBLE"
    with pytest.raises(C.IncompleteAuthority, match="boundary"):
        C.enforce_split(wf_end, "wf1")
    with pytest.raises(C.EvaluationLocked, match="sealed holdout"):
        C.enforce_split(splits["holdout"][0], "holdout")
    with pytest.raises(C.IncompleteAuthority, match="caller-defined"):
        C.enforce_split(wf_start, "hostile-custom-range")


def test_development_pre_filter_authority_rows_are_explicitly_ineligible():
    development_start, _, _ = C.frozen_splits()["development"]
    assert C.enforce_split(development_start, "development") == C.PRE_FILTER_AUTHORITY_INELIGIBLE
    eligible_start = C._iso_ms(C.load_frozen_contract()["walk_forward"]["development_filter_eligible_segment"].split("/", 1)[0])
    assert C.enforce_split(eligible_start - 1, "development") == C.PRE_FILTER_AUTHORITY_INELIGIBLE
    assert C.enforce_split(eligible_start, "development") == "ELIGIBLE"


def test_report_schema_is_complete_locked_and_multiplicity_bound():
    report = {key: 0 for key in C.REQUIRED_REPORT_KEYS}
    report.update(candidate_id=256, contract_sha256=C.CONTRACT_SHA256,
                  base_commit=C.CONTRACT_BASE, status="EVALUATION_LOCKED",
                  promotable=False, family_alpha=C.FAMILY_ALPHA,
                  folds={}, months={}, sides={}, symbols={}, regimes={}, capital_feasibility={},
                  risk_label="standard", ledger_digest="0" * 64)
    C.validate_report_schema(report)
    report["family_alpha"] = .05
    with pytest.raises(C.C256Error, match="multiplicity"):
        C.validate_report_schema(report)
    report["family_alpha"] = C.FAMILY_ALPHA
    report["recovery_duration_hours"] = math.nan
    with pytest.raises(C.C256Error, match="undefined or non-finite"):
        C.validate_report_schema(report)
    report["recovery_duration_hours"] = 0
    report["closed_paired_attempts"] = -1
    with pytest.raises(C.C256Error, match="non-negative integers"):
        C.validate_report_schema(report)


def test_stationary_bootstrap_indices_are_reproducible_and_settings_are_frozen():
    first = C.stationary_bootstrap_indices(2)
    second = C.stationary_bootstrap_indices(2)
    assert first == second and len(first) == 10_000 and all(len(row) == 2 for row in first)
    with pytest.raises(C.C256Error, match="settings are frozen"):
        C.stationary_bootstrap_indices(2, seed=1)


def test_cli_can_only_validate_and_evaluate_always_refuses(capsys):
    assert CLI.main(["--validate-contract"]) == 0
    assert "REAL_EVALUATION_LOCKED" in capsys.readouterr().out
    with pytest.raises(SystemExit) as exc:
        CLI.main(["--evaluate"])
    assert exc.value.code == 2
    with pytest.raises(SystemExit) as exc:
        CLI.main(["--evaluate", "--validate-contract"])
    assert exc.value.code == 2
