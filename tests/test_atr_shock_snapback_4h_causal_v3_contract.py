"""Static contract freeze for candidate 13 causal-v3. No archive or market rows are read."""
import hashlib
import json
import os


ROOT = os.path.dirname(os.path.dirname(__file__))
DIR = os.path.join(ROOT, 'contracts', 'atr_shock_snapback_4h_causal_v3')
CONTRACT = os.path.join(DIR, 'atr_shock_snapback_4h_causal_v3_contract.v1.json')
MD = os.path.join(DIR, 'ATR_SHOCK_SNAPBACK_4H_CAUSAL_V3.md')
UNIVERSE = os.path.join(ROOT, 'research_evidence', 'universe', 'pit-top40-qv30d-v4.json')
MANIFEST = os.path.join(ROOT, 'research_evidence', 'manifests', 'binance-um-archive-v1.json.gz')
CLASSES = os.path.join(ROOT, 'research_evidence', 'universe', 'instrument-classes-v1.json')
FILTERS = os.path.join(ROOT, 'contracts', 'strategy_causal_pack_01', 'exchange_filter_authority.v2.json')


def _load():
    with open(CONTRACT, encoding='utf-8') as fh:
        contract = json.load(fh)
    with open(MD, encoding='utf-8') as fh:
        md = fh.read()
    return contract, md


def _sha(path):
    with open(path, 'rb') as fh:
        return hashlib.sha256(fh.read()).hexdigest()


def test_identity_is_frozen_to_stage_a_without_parameter_changes():
    c, md = _load()
    assert (c['contract'], c['version'], c['status'], c['signed_by']) == (
        'ATR-SHOCK-SNAPBACK-4H-CAUSAL-V3-SPARSE-AVAILABILITY', 3, 'FROZEN', 'codex')
    s = c['frozen_source']
    assert s['source_head'] == '1a24edf49ef6ccaa40867ed930603737364d28ce'
    assert s['signal_parameters'] == {'shock_atr': 1.5, 'volume_floor': 0.8, 'cost_multiple': 5.0}
    assert s['management'] == {
        'stop_atr': 1.5, 'tp1_r': 0.8, 'tp1_frac': 0.5,
        'be_r': 0.8, 'tp_r': 1.5, 'max_bars': 12}
    assert s['signal_parameters_unchanged'] and s['management_parameters_unchanged'] and s['reentry_lock']
    assert 'exact `1a24edf4` Stage-A hypothesis' in md


def test_two_side_cells_are_separate_and_multiplicity_is_not_erased():
    c, _ = _load()
    assert c['candidate_cells'] == [
        {'candidate_id': 'atr_shock_snapback_4h_long_causal_v3', 'side': 'long'},
        {'candidate_id': 'atr_shock_snapback_4h_short_causal_v3', 'side': 'short'}]
    m = c['identity_and_multiplicity']
    assert m['registered_cell_count'] == m['holm_family_size'] == 2
    assert m['cells_judged_separately'] and m['cross_side_pooling'] is False
    assert m['prior_results_credit'] is False and m['promotion_uses_inference'] is False
    assert 'retain both and every prior tested candidate or rejection' in m['rule']
    assert 'missing prior p-values equal 1.0' in m['inference']


def test_signal_and_exit_rules_are_exact_mirrors_and_causal():
    c, _ = _load()
    s = c['signal']
    assert 'EMA50 > EMA200 for long' in s['trend'] and 'EMA50 < EMA200 for short' in s['trend']
    assert 'body[d-1] <= -1.5*ATR14[d-1]' in s['long_entry']
    assert 'body[d-1] >= +1.5*ATR14[d-1]' in s['short_entry']
    assert s['volume'].startswith('volume[d] >= 0.80') and 'current bar is excluded' in s['volume']
    assert s['cost_distance'].startswith('1.5*ATR14[d]/close[d] >= 5*2*(0.0005+0.0002)')
    assert 'close[d] >= EMA20[d]' in s['long_signal_exit']
    assert 'close[d] <= EMA20[d]' in s['short_signal_exit']
    assert 'completed causal 4h candles' in s['indicators'] and s['no_cross_sectional_signal']


def test_exact_authority_bytes_are_bound_without_reading_archive_rows():
    c, _ = _load()
    a = c['pit_and_archive_authority']
    assert _sha(UNIVERSE) == a['universe_byte_sha256']
    assert _sha(MANIFEST) == a['manifest_gzip_byte_sha256']
    assert _sha(CLASSES) == a['classes_byte_sha256']
    assert a['manifest_source_class'] == 'archive-verified' and a['manifest_survivor_only'] is False
    assert 'Before opening any candle, mark, funding or outcome row' in a['pre_market_validation']
    assert 'first 20 filter-eligible crypto members' in a['core20_derivation']
    assert 'No current, static, union, later-week or survivor-only membership' in a['core20_derivation']


def test_exchange_filter_authority_and_fold_bindings_are_exact():
    c, _ = _load()
    a = c['exchange_filter_authority']
    assert _sha(FILTERS) == a['byte_sha256']
    assert a['identity'] == 'STRATEGY-CAUSAL-PACK-01-EXCHANGE-FILTER-AUTHORITY/v2'
    with open(FILTERS, encoding='utf-8') as fh:
        filters = json.load(fh)
    assert a['fold_bindings'] == filters['fold_bindings']
    assert a['weekly_membership_sha256'] == filters['weekly_membership_sha256']
    assert 'strictly before fold start' in a['selection']
    assert all(x in a['rules'] for x in ('PRICE_FILTER.tickSize', 'MARKET_LOT_SIZE', 'MIN_NOTIONAL.notional'))
    assert 'LOT_SIZE is not a substitute' in a['rules']
    assert a['future_or_current_snapshot_forbidden']


def test_sparse_availability_is_flat_tolerant_and_exposure_fail_closed():
    c, md = _load()
    a = c['sparse_availability']
    assert a['identity'] == 'ASS4H-SPARSE-CAUSAL-AVAILABILITY/v1'
    assert all(x in a['no_imputation'] for x in (
        'forward-fill', 'backfill', 'interpolate', 'synthesize', 'compress time', 'bridge'))
    assert all(x in a['segment_indicators'] for x in ('EMA20/50/200', 'Wilder ATR14', 'prior-42-volume'))
    assert 'do not make the cell incomplete' in a['flat_missing_is_harmless']
    assert 'may not veto, approve or reprioritize' in a['no_future_veto']
    assert all(x in a['exposure_fail_closed'] for x in (
        'next open', 'filter', 'fill', 'bar', 'causal mark', 'funding settlement', 'protection'))
    assert 'entire side cell INCOMPLETE_CANDIDATE' in a['exposure_fail_closed']
    assert 'This contract supplies none' in a['terminal_series']
    assert 'never a skipped trade or reduced denominator' in md


def test_event_order_next_open_intrabar_and_reentry_are_frozen():
    c, md = _load()
    e = c['execution']
    assert e['entry_fill'].startswith('next_tradable_open')
    assert e['signal_exit_fill'].startswith('next_tradable_open')
    assert e['time_exit_fill'] == 'next_tradable_open_at_index_e_plus_12'
    assert e['same_candle_close_fill'] is False and e['entry_bar_participates']
    assert e['same_timestamp_open_order'] == [
        'funding_time_lt_t', 'causal_marks_and_cairo_roll', 'due_signal_time_exits',
        'reentry_lock_checks', 'pit_ordered_entry_attempts', 'funding_time_eq_t',
        'fill_bar_intrabar_management']
    assert 'due open exit wins over fill-bar gap-stop' in e['event_timeline']
    assert 'following completed candle is eligible' in e['reentry_lock']
    r = e['intrabar_rule']
    assert r['id'] == 'STRATEGY_CAUSAL_FBL_BT01_V1'
    assert r['green_path'] == ['open', 'low', 'high', 'close']
    assert r['red_path'] == ['open', 'high', 'low', 'close']
    assert r['dca_or_pyramid'] == 'forbidden' and r['missing_exposure_bar'] == 'INCOMPLETE_CANDIDATE'
    assert 'fixed order is earlier funding' in md


def test_management_book_and_cairo_halt_are_explicit():
    c, _ = _load()
    m, b = c['execution']['management'], c['book_and_sizing']
    assert m['r_price'] == '1.5 * ATR_entry'
    assert all(x in m['long_levels'] for x in ('+0.8R', '+1.5R'))
    assert all(x in m['short_levels'] for x in ('-0.8R', '-1.5R'))
    assert m['max_bars'] == 12 and 'MARKET_LOT_SIZE' in m['quantity']
    assert b['one_isolated_book_per_side'] and b['reference_start_equity_usdt'] == 500.0
    assert b['risk_fraction_of_current_marked_equity'] == 0.01
    assert b['maximum_open_positions'] == 4 and b['gross_leverage_multiple'] == 10.0
    assert 'weekly PIT CORE-20 order' in b['entry_priority']
    assert b['daily_halt_fraction'] == 0.08 and b['daily_halt_timezone'].startswith('Africa/Cairo')
    assert 'block later new entries only, never exits or protection' in b['daily_halt']


def test_cost_funding_exchange_and_full_stress_replays_are_single_count():
    c, _ = _load()
    x = c['costs_and_funding']
    assert x['fee_per_fill_bps'] == 5 and x['adverse_slippage_per_fill_bps'] == 2
    assert 'entry_fill_time <= t < exit_fill_time' in x['funding']
    assert 'positive rate debits long and credits short' in x['funding_sign']
    assert x['net_pnl_formula'] == (
        'gross_reference_price_pnl + signed_funding_cash - '
        'fee_cash_on_executable_notional - adverse_slippage_cash')
    assert 'subtract slippage cash exactly once' in x['adverse_fill']
    assert x['funding_or_mark_gap_during_exposure'] == 'INCOMPLETE_CANDIDATE'
    assert x['cost_stress_multipliers'] == [1.0, 1.5, 2.0]
    assert 'independent full causal books' in x['stress_rule']
    assert 'Repricing a fixed trade list is forbidden' in x['stress_rule']


def test_folds_embargo_and_holdout_cutoff_are_hard():
    c, _ = _load()
    assert [(x['name'], x['start'], x['end']) for x in c['folds']] == [
        ('wf1', '2025-04-01T00:00:00Z', '2025-09-30T00:00:00Z'),
        ('wf2', '2025-10-01T00:00:00Z', '2026-03-31T00:00:00Z'),
        ('wf3', '2026-04-01T00:00:00Z', '2026-09-28T00:00:00Z')]
    assert c['pit_and_archive_authority']['forbid_read_at_or_after'] == '2026-09-28T00:00:00Z'
    s = c['split_rules']
    assert (s['max_lookback_bars'], s['max_holding_bars']) == (200, 12)
    assert (s['first_eligible_decision_index'], s['first_eligible_entry_index']) == (199, 200)
    assert s['entry_eligibility'] == 'e >= 200 and e + 12 < N'
    assert s['last_eligible_entry_index'] == 'N-13' and s['next_open_embargo_bars_at_end'] == 12
    assert s['episode_must_close_inside_fold'] and s['fold_selection'] is False


def test_exact_stage_a_promotion_gates_apply_per_side_without_inference():
    c, md = _load()
    g = c['promotion_gates_per_side']
    assert g['aggregate_net_r_gt'] == g['aggregate_mean_r_gt'] == 0
    assert g['aggregate_pf_gt'] == 1.05 and g['positive_pre_cost_gross_expectancy']
    assert (g['minimum_closed_trades'], g['minimum_symbols']) == (160, 8)
    assert (g['minimum_positive_folds'], g['of_folds']) == (2, 3)
    assert g['monthly_bucket_timezone'] == 'UTC' and 'including zero-close months' in g['monthly_bucket_rule']
    assert g['minimum_monthly_trade_median'] == 5 and g['max_dd_r_lte'] == 15
    assert g['minimum_closes_per_regime'] == 15 and g['minimum_regime_mean_r'] == -0.05
    assert g['max_symbol_absolute_contribution_fraction'] == 0.35
    assert g['positive_after_1_5x_cost_stress']
    assert g['causal_parity_required'] and g['independent_review_required']
    assert g['maximum_incomplete_rows'] == 0 and g['missing_or_incomplete_is_pass'] is False
    assert g['inference_required_for_promotion'] is False and g['cross_side_substitution'] is False
    assert 'at least 160 closes over 8 symbols' in ' '.join(md.split())


def test_reports_integrity_activation_and_stop_conditions_fail_closed():
    c, md = _load()
    required = set(c['required_report'])
    assert {'trade_count', 'net_r', 'mean_r', 'profit_factor', 'max_dd_r'} <= required
    assert {'fee_drag_r', 'slippage_drag_r', 'funding_drag_r', 'gross_r'} <= required
    assert {'sparse_availability_audit', 'full_candidate_ledger', 'survivorship_disclosure'} <= required
    assert all(c['runner_integrity'].values())
    a = c['activation']
    assert a['default_enabled'] is False and a['paper_testnet_only']
    assert a['requires_separate_exact_head_acceptance'] and a['requires_protected_execution']
    assert a['mainnet_authority'] is False
    assert c['stop_conditions'][1].startswith('Any market/archive/outcome read before')
    assert 'grants no runtime or mainnet authority' in md
