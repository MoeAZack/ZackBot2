"""Static freeze for STRATEGY-CAUSAL-PACK-01. No data or backtest is read."""
import json
import os
from datetime import datetime, timezone

import strategies as S

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DIR = os.path.join(ROOT, 'contracts', 'strategy_causal_pack_01')
JS = os.path.join(DIR, 'strategy_causal_pack_01_contract.v1.json')
MD = os.path.join(DIR, 'STRATEGY_CAUSAL_PACK_01.md')


def _load():
    with open(JS, encoding='utf-8') as fh:
        contract = json.load(fh)
    with open(MD, encoding='utf-8') as fh:
        markdown = fh.read()
    return contract, markdown


def _ts(value):
    return datetime.fromisoformat(value.replace('Z', '+00:00')).astimezone(timezone.utc)


def test_contract_is_frozen_signed_and_has_no_live_authority():
    c, md = _load()
    assert (c['contract'], c['version'], c['status'], c['signed_by']) == (
        'STRATEGY-CAUSAL-PACK-01', 1, 'FROZEN', 'codex')
    assert c['frozen_at'] == '2026-11-06T10:52:47+02:00'
    assert c['scope']['paper_testnet_only'] is True
    assert c['scope']['default_enabled'] is False
    assert c['scope']['mainnet_authority'] is False
    assert c['scope']['parameter_search'] is False
    assert c['scope']['runtime_risk_before_independent_pass'] is False
    assert md.splitlines()[2] == '**Status: FROZEN — signed by Codex, 6 November 2026 09:55 Cairo.**'


def test_three_new_unique_identities_preserve_old_rejections():
    c, _ = _load()
    rows = c['candidates']
    assert [x['candidate_id'] for x in rows] == [
        'pullback_rsi_short_h1_causal_v2',
        'exp_mom_short_c27_causal_v2',
        'volume_pressure_inventory_short_1h_causal_v2',
    ]
    assert len({x['candidate_id'] for x in rows}) == len({x['old_id'] for x in rows}) == 3
    assert all(x['candidate_id'] != x['old_id'] and x['signal_parameters_unchanged'] for x in rows)
    assert rows[1]['management_parameters_unchanged'] is False
    assert c['candidate_selection_disclosure']['selection_is_not_evidence'] is True
    assert len(c['candidate_selection_disclosure']['prior_results']) == 3
    disclosure = c['candidate_selection_disclosure']
    assert 'Holm-adjusted' in disclosure['multiplicity_rule']
    assert disclosure['inference_role'] == 'diagnostic_only'
    assert disclosure['promotion_uses_inference'] is False


def test_source_strategy_facts_and_frozen_horizons_match_code():
    c, md = _load()
    by_id = {x['candidate_id']: x for x in c['candidates']}
    pull = S.STRATEGIES['pullback_rsi_short_h1']
    exp = S.STRATEGIES['exp_mom_short_c27']
    vpi = S.STRATEGIES['volume_pressure_inventory_1h']
    assert pull['research_only'] and pull['sides'] == 'short' and pull['tf'] == '4h'
    assert pull['mgmt']['max_bars'] == by_id['pullback_rsi_short_h1_causal_v2']['max_holding_bars'] == 60
    assert by_id['pullback_rsi_short_h1_causal_v2']['management'] == pull['mgmt']
    assert exp['research_only'] and exp['sides'] == 'short' and exp['tf'] == '4h'
    assert exp['mgmt'].get('max_bars') is None  # adding the preregistered cap creates the new identity
    assert by_id['exp_mom_short_c27_causal_v2']['max_holding_bars'] == 180
    assert by_id['exp_mom_short_c27_causal_v2']['management'] == {
        **exp['mgmt'], 'max_bars': 180}
    with open(os.path.join(ROOT, 'tools', 'research', 'trend_ema_mom.py'), encoding='utf-8') as fh:
        assert 'TIME_CAP_BARS = 180' in fh.read()
    assert vpi['research_only'] and vpi['sides'] == 'both' and vpi['tf'] == '1h'
    assert vpi['mgmt']['max_bars'] == by_id['volume_pressure_inventory_short_1h_causal_v2']['max_holding_bars'] == 24
    assert by_id['volume_pressure_inventory_short_1h_causal_v2']['management'] == {
        'stop_atr': 2.0, 'tp_r': 2.0, 'max_bars': 24}
    for row in by_id.values():
        assert f"`{row['candidate_id']}`" in md


def test_all_discretionary_exits_are_next_open_and_missing_open_is_incomplete():
    c, md = _load()
    e = c['execution']
    assert e['entry_fill'].startswith('next_tradable_open')
    assert e['signal_exit_fill'].startswith('next_tradable_open')
    assert e['time_exit_fill'].startswith('next_tradable_open')
    assert e['decision_time_strictly_before_signal_or_time_fill'] is True
    assert e['same_candle_close_fill_for_signal_or_time'] is False
    assert e['missing_next_open'] == 'INCOMPLETE_CANDIDATE'
    assert e['last_mark_or_synthetic_close'] is False
    assert 'next tradable open' in md and 'never a last-mark or synthetic close' in md


def test_intrabar_rule_is_fully_encoded_and_candidate_bounded():
    c, md = _load()
    rule = c['execution']['intrabar_rule']
    assert rule['id'] == 'STRATEGY_CAUSAL_FBL_BT01_V1'
    assert rule['green_path'] == ['open', 'low', 'high', 'close']
    assert rule['red_path'] == ['open', 'high', 'low', 'close']
    assert rule['doji_short_path'] == ['open', 'low', 'high', 'close']
    assert rule['doji_long_path'] == ['open', 'high', 'low', 'close']
    assert rule['entry_bar_participates'] is True
    assert rule['gap_stop'].startswith('if the bar opens at or beyond the stop')
    assert rule['both_stop_and_favourable_level'].startswith('if the stop as frozen at the bar open')
    assert rule['monotonic_leg_order'].startswith('otherwise fire reached levels in price order')
    assert rule['target_and_partial_reference_fill'].startswith('fill at the frozen trigger level')
    assert rule['partial_then_management'].startswith('after a partial target')
    assert rule['trail_update'].startswith('ratchet only after each completed path leg')
    assert rule['signal_and_time_order'].startswith('process the full intrabar path first')
    assert rule['dca_or_pyramid'].startswith('forbidden')
    assert rule['unresolved_or_missing_bar'] == 'INCOMPLETE_CANDIDATE'
    assert 'STRATEGY_CAUSAL_FBL_BT01_V1' in md


def test_management_formulas_and_exchange_rounding_are_frozen():
    c, md = _load()
    m = c['execution']['management_semantics']
    assert m['side_sign'] == 'long=+1, short=-1'
    assert m['entry_atr'].startswith('ATR_entry is the candidate signal ATR at completed decision bar d')
    assert m['entry_price_basis'].startswith('entry_exec is the adverse-slipped')
    assert m['initial_r'] == 'R_price = management.stop_atr * ATR_entry'
    assert m['initial_stop'] == 'stop = entry_exec - side_sign * R_price'
    assert m['initial_quantity'].startswith('qty_raw = risk_cash / R_price')
    assert m['level_precision'].startswith('management trigger levels remain the exact formula values')
    assert m['full_target'].startswith('when tp_r exists, target = entry_exec + side_sign * tp_r * R_price')
    assert m['tp1'].startswith('when tp1_r exists, tp1 = entry_exec + side_sign * tp1_r * R_price')
    assert m['partial_quantity'].startswith('for a partial only, floor requested close quantity')
    assert m['partial_dust'].startswith('tp1 has no automatic dust-to-full-close conversion')
    assert m['breakeven'].startswith('when be_r exists and best_price reaches')
    assert 'ATR_prior_closed_bar' in m['trail'] and 'never loosen' in m['trail']
    assert m['best_price'].startswith('initialize to entry_exec')
    assert set(m['forbidden']) == {
        'DCA', 'pyramiding', 'take-profit ladder',
        'same-leg retroactive breakeven', 'same-leg retroactive trail'}
    assert 'Management uses the adverse executable entry' in md


def test_costs_are_realistic_and_stressed():
    c, md = _load()
    costs = c['costs']
    assert costs['fee_per_fill_bps'] == 5 and costs['adverse_slippage_per_fill_bps'] == 2
    assert 'actual Binance USD-M funding' in costs['funding']
    assert costs['funding_gap'] == costs['mark_gap'] == 'INCOMPLETE_CANDIDATE'
    assert costs['cost_stress_multipliers_to_report'] == [1.0, 1.5, 2.0]
    assert 'positive rate is a receipt for a short' in costs['funding_sign']
    assert costs['price_pnl_basis'].startswith('gross_reference_price_pnl uses unslipped reference prices only')
    assert costs['adverse_slippage_cash'].endswith('exactly once')
    assert costs['net_pnl_formula'] == (
        'gross_reference_price_pnl + signed_funding_cash - '
        'fee_cash_on_executable_notional - adverse_slippage_cash')
    assert 'positive funding credits remain at 1.0x' in costs['cost_stress_rule']
    assert '5 bp taker fee plus 2 bp adverse slippage' in md


def test_book_sizing_and_cairo_halt_are_frozen_not_caller_choices():
    c, md = _load()
    b = c['book_and_sizing']
    assert b['reference_start_equity_usdt'] == 500.0
    assert b['risk_fraction_of_current_candidate_book_equity'] == 0.01
    assert b['maximum_open_positions'] == 4
    assert b['gross_leverage_multiple'] == 10.0
    assert 'current candidate-book equity' in b['risk_cash_formula']
    assert 'current gross open notional' in b['leverage_notional_cap_formula']
    assert 'frozen CORE-20 order' in b['entry_priority']
    assert b['daily_halt_fraction'] == 0.08
    assert b['daily_halt_timezone'].startswith('Africa/Cairo')
    assert b['equity_compounding'] is True
    assert b['candidate_books_isolated'] is True
    assert b['cross_candidate_capital_sharing'] is False
    assert b['caller_override'].startswith('forbidden')
    assert '500 USDT starting equity' in md and 'Cairo calendar days' in md


def test_folds_are_ordered_unspent_and_holdout_is_forbidden():
    c, md = _load()
    folds = c['folds']
    assert [(x['name'], x['start'], x['end']) for x in folds] == [
        ('wf1', '2025-04-01T00:00:00Z', '2025-09-30T00:00:00Z'),
        ('wf2', '2025-10-01T00:00:00Z', '2026-03-31T00:00:00Z'),
        ('wf3', '2026-04-01T00:00:00Z', '2026-09-28T00:00:00Z'),
    ]
    for previous, current in zip(folds, folds[1:]):
        assert _ts(previous['end']) < _ts(current['start'])
    cutoff = _ts(c['data']['forbid_read_at_or_after'])
    assert all(_ts(x['end']) <= cutoff for x in folds)
    assert c['scope']['old_holdout_reuse'] is False
    assert c['data']['viewed_holdout'] == '[2026-09-28,2026-11-05)'
    assert 'Nothing at or after 28 September 2026 may be read' in md


def test_split_purge_is_candidate_specific_and_finite():
    c, _ = _load()
    s = c['split_rules']
    assert s['bar_index_model'].startswith('Each fold has local bars 0..N-1')
    assert s['first_eligible_decision_index'] == 'L-1 where L=candidate.max_lookback_bars'
    assert s['first_eligible_entry_index'] == 'L'
    assert s['time_cap_exit_index'] == 'x=e+H where H=candidate.max_holding_bars'
    assert s['entry_eligibility_inequality'] == 'e >= L and e + H < N'
    assert s['last_eligible_entry_index'] == 'N-H-1'
    assert s['terminal_fill_inequality'].endswith('e <= x < N')
    assert s['next_open_embargo_bars_at_end'] == 'H'
    assert s['episode_must_close_inside_fold'] and s['folds_reported_separately']
    assert s['fold_selection'] is False and s['pool_before_per_fold_reporting'] is False
    for row in c['candidates']:
        lookback, hold, n = row['max_lookback_bars'], row['max_holding_bars'], 1000
        first_entry = lookback
        last_entry = n - hold - 1
        assert first_entry - 1 == lookback - 1
        assert first_entry + hold < n
        assert last_entry + hold == n - 1
        assert last_entry + 1 + hold == n


def test_runner_fails_closed_before_data_and_outputs_are_immutable():
    c, _ = _load()
    r = c['runner_integrity']
    assert all(r.values())
    assert c['stop_conditions'][1].startswith('Any read at or after 2026-09-28 refuses')


def test_promotion_is_economic_frequent_diversified_and_independent():
    c, md = _load()
    g = c['promotion_gates']
    assert g['aggregate_net_r_gt'] == g['aggregate_mean_r_gt'] == 0
    assert g['aggregate_pf_gt'] == 1.05 and g['minimum_closed_trades'] == 200
    assert g['minimum_symbols'] == 8 and (g['minimum_positive_folds'], g['of_folds']) == (2, 3)
    assert g['minimum_monthly_trade_median'] == 8 and g['max_dd_r_lte'] == 15
    assert g['minimum_regime_mean_r'] == -0.05 and g['max_symbol_absolute_contribution_fraction'] == 0.35
    assert g['positive_after_1_5x_cost_stress'] and g['causal_parity_required']
    assert g['independent_review_required'] and g['all_required_reports_present']
    assert g['maximum_incomplete_rows'] == 0
    assert g['incomplete_scope'].startswith('Any missing funding')
    assert g['missing_or_incomplete_is_pass'] is False
    assert g['inference_required_for_promotion'] is False
    assert 'at least 200 closes' in md and 'missing evidence never passes' in md


def test_required_reports_cover_significance_costs_regimes_and_survivorship():
    c, _ = _load()
    required = set(c['required_report'])
    assert {'trade_count', 'net_r', 'mean_r', 'profit_factor', 'max_dd_r', 'expectancy_r'} <= required
    assert {'raw_p_value', 'holm_adjusted_p_value', 'bootstrap_ci_95_mean_r'} <= required
    assert {'fee_drag_r', 'slippage_drag_r', 'funding_drag_r', 'gross_r'} <= required
    assert {'regime_breakdown', 'coin_breakdown', 'symbol_concentration'} <= required
    assert {'survivorship_disclosure', 'incomplete_rows', 'failure_reason'} <= required


def test_cohort_remains_default_off_bounded_and_protected():
    c, md = _load()
    cohort = c['cohort']
    assert cohort['target_qualified_candidates'] == [2, 3]
    assert cohort['slot_isolation'] and cohort['protected_execution_required'] and cohort['rollback_ready']
    assert cohort['activation'].startswith('default-OFF')
    assert cohort['initial_risk_multiplier_max'] == 'min(1, 15 / accepted_max_dd_r)'
    assert 'RISK-LOSS-01' in cohort['loss_controls_required']
    assert 'grants no mainnet authority' in md
