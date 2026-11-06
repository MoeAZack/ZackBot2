"""Static freeze for RBR short causal v3. This test reads no market or archive data."""
import json
import hashlib
import os
import sys
from decimal import Decimal
from datetime import datetime, timezone

import strategies as S


ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DIR = os.path.join(ROOT, 'contracts', 'rank_breadth_reversal_4h_short_causal_v3')
JS = os.path.join(DIR, 'rank_breadth_reversal_4h_short_causal_v3_contract.v1.json')
MD = os.path.join(DIR, 'RANK_BREADTH_REVERSAL_4H_SHORT_CAUSAL_V3.md')
FAMILY = os.path.join(DIR, 'rank_breadth_reversal_4h_family.v1.json')
UNIVERSE = os.path.join(ROOT, 'research_evidence', 'universe', 'pit-top40-qv30d-v4.json')
MANIFEST = os.path.join(ROOT, 'research_evidence', 'manifests', 'binance-um-archive-v1.json.gz')
CLASSES = os.path.join(ROOT, 'research_evidence', 'universe', 'instrument-classes-v1.json')
FILTER_AUTHORITY = os.path.join(
    ROOT, 'contracts', 'strategy_causal_pack_01', 'exchange_filter_authority.v2.json')
sys.path.insert(0, os.path.join(ROOT, 'tools', 'research'))
import manifest as M  # noqa: E402


def _load():
    with open(JS, encoding='utf-8') as fh:
        contract = json.load(fh)
    with open(MD, encoding='utf-8') as fh:
        markdown = fh.read()
    return contract, markdown


def _ts(value):
    return datetime.fromisoformat(value.replace('Z', '+00:00')).astimezone(timezone.utc)


def _sha(path):
    h = hashlib.sha256()
    with open(path, 'rb') as fh:
        for block in iter(lambda: fh.read(1024 * 1024), b''):
            h.update(block)
    return h.hexdigest()


def _canonical_digest(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, separators=(',', ':'),
                                     allow_nan=False).encode()).hexdigest()


def test_contract_is_frozen_default_off_and_has_no_live_authority():
    c, md = _load()
    assert (c['contract'], c['version'], c['status'], c['signed_by']) == (
        'RANK-BREADTH-REVERSAL-4H-SHORT-CAUSAL-V3', 2, 'FROZEN', 'codex')
    assert c['frozen_at'] == '2026-11-06T11:50:43+02:00'
    assert c['scope']['research_only'] and c['scope']['paper_testnet_only']
    assert c['scope']['default_enabled'] is False
    assert c['scope']['mainnet_authority'] is False
    assert c['scope']['parameter_search'] is False
    assert c['scope']['runtime_risk_before_independent_pass'] is False
    assert md.splitlines()[2] == '**Status: FROZEN — signed by Codex, 6 November 2026 11:50 Cairo.**'


def test_source_signal_parameters_and_management_are_byte_for_byte_frozen():
    c, _ = _load()
    row = c['candidate']
    source = S.STRATEGIES['rank_breadth_reversal_4h']
    assert row['candidate_id'] == 'rank_breadth_reversal_4h_short_causal_v3'
    assert row['old_id'] == 'rank_breadth_reversal_4h:short'
    assert row['side'] == 'short' and row['timeframe'] == source['tf'] == '4h'
    assert source['research_only'] and source['sides'] == 'both'
    assert source['fn'] is S.sig_rank_breadth_reversal_4h
    assert row['reentry_lock'] is source['reentry_lock'] is True
    assert row['signal_parameters'] == source['params'] == S.RBR4H
    assert row['management'] == source['mgmt']
    assert row['signal_parameters_unchanged'] and row['management_parameters_unchanged']
    assert row['max_lookback_bars'] == 200 and row['max_holding_bars'] == 18


def test_prior_positive_result_is_explicitly_contaminated_not_evidence():
    c, md = _load()
    old = c['contaminated_context']
    assert old['selection_is_not_evidence'] is True
    assert old['fresh_evidence_credit'] is False
    assert old['old_result'] == {
        'closed_trades': 76, 'symbols': 8, 'net_r': 7.33, 'mean_r': 0.096,
        'profit_factor': 1.239, 'max_dd_r': 7.96, 'verdict': 'rejected: regime sample'}
    assert 'cannot satisfy any gate' in old['rule']
    assert old['inference_role'] == 'diagnostic_only'
    assert old['promotion_uses_inference'] is False
    assert 'not fresh evidence' in ' '.join(md.split())


def test_pit_core_rank_and_breadth_are_causal_and_not_union_based():
    c, md = _load()
    p = c['point_in_time_signal']
    assert 'weekly PIT CORE-20 membership snapshot available at d' in p['universe']
    assert 'union-of-fold membership are forbidden' in p['universe']
    assert 'causal 42-bar return' in p['rank'] and 'excluded from rank7_n' in p['rank']
    assert 'same PIT membership and data-eligible set' in p['breadth']
    assert p['minimum_ranked_members'] == 8
    assert p['missing_input'] == 'INCOMPLETE_CANDIDATE'
    assert 'available_at <= decision candle close' in p['availability']
    assert 'weekly point-in-time CORE-20' in md


def test_exact_pit_authority_bytes_content_and_core20_derivation_are_valid():
    c, _ = _load()
    a = c['pit_authority']
    assert a['universe_path'] == 'research_evidence/universe/pit-top40-qv30d-v4.json'
    assert _sha(UNIVERSE) == a['universe_byte_sha256']
    assert _sha(MANIFEST) == a['manifest_gzip_byte_sha256']
    assert _sha(CLASSES) == a['classes_byte_sha256']
    with open(UNIVERSE, encoding='utf-8') as fh:
        universe = json.load(fh)
    core = {k: universe[k] for k in universe if k != 'digest'}
    assert _canonical_digest(core) == universe['digest'] == a['universe_content_digest']
    assert (universe['format'], universe['universe_id'], universe['manifest_digest'],
            universe['classes_id'], universe['classes_digest'], universe['books']) == (
        a['universe_format'], a['universe_id'], a['manifest_content_digest'],
        a['classes_id'], a['classes_content_digest'], a['artifact_books_exact'])
    weeks = universe['weeks']
    assert len(weeks) == 348
    assert (weeks[0]['monday_utc'], weeks[-1]['monday_utc']) == ('2020-02-03', '2026-09-28')
    assert all(b['monday_ms'] - a_['monday_ms'] == 7 * 86_400_000
               for a_, b in zip(weeks, weeks[1:]))
    for week in weeks:
        assert set(week['books']) == set(a['artifact_books_exact'])
        for book in a['artifact_books_exact']:
            row = week['books'][book]
            members, raw_qv = row['members'], row['qv30d_usdt']
            assert len(members) == len(raw_qv) <= 40
            assert len(set(members)) == len(members) and all(x.isascii() for x in members)
            pairs = [(symbol, Decimal(qv)) for symbol, qv in zip(members, raw_qv)]
            assert all(qv.is_finite() and qv >= 0 for _, qv in pairs)
            assert all(aq > bq or (aq == bq and asym < bsym)
                       for (asym, aq), (bsym, bq) in zip(pairs, pairs[1:]))
        if week['monday_utc'] >= '2025-03-31':
            assert len(week['books']['crypto']['members'][:20]) == 20
    manifest = M.load(MANIFEST)
    assert (manifest['format'], manifest['loader_version'], manifest['source_class'],
            manifest['survivor_only'], manifest['digest']) == (
        a['manifest_format'], a['manifest_loader_version'], a['manifest_source_class'],
        a['manifest_survivor_only'], a['manifest_content_digest'])
    with open(CLASSES, encoding='utf-8') as fh:
        classes = json.load(fh)
    class_core = {k: classes[k] for k in classes if k != 'digest'}
    assert _canonical_digest(class_core) == classes['digest'] == a['classes_content_digest']
    assert 'members[0:20]' in a['core20_derivation']
    assert 'Before any candle' in a['pre_market_row_validation']


def test_causal_filter_authority_is_exactly_bound_before_market_rows():
    c, md = _load()
    a = c['pit_authority']
    assert _sha(FILTER_AUTHORITY) == a['exchange_filter_authority_sha256']
    with open(FILTER_AUTHORITY, encoding='utf-8') as fh:
        authority = json.load(fh)
    assert (authority['authority'], authority['version'], authority['status']) == (
        'STRATEGY-CAUSAL-PACK-01-EXCHANGE-FILTER-AUTHORITY', 2, 'FROZEN')
    assert authority['fold_bindings'] == a['exchange_filter_fold_bindings']
    assert authority['weekly_membership_sha256'] == a['filtered_weekly_membership_sha256']
    assert 'strictly before the fold start' in a['exchange_filter_fold_policy']
    assert 'active at fold start' in a['exchange_filter_fold_policy']
    assert 'first 20' in a['exchange_filter_fold_policy']
    assert 'causal exchange-filter authority' in md


def test_all_discretionary_fills_are_next_open_and_fbl_is_frozen():
    c, md = _load()
    e, rule = c['execution'], c['execution']['intrabar_rule']
    assert e['entry_fill'].startswith('next_tradable_open')
    assert e['signal_exit_fill'].startswith('next_tradable_open')
    assert e['time_exit_fill'] == 'next_tradable_open_at_index_e_plus_H where H is max_holding_bars'
    assert 'fold-start exchangeInfo snapshot' in e['exchange_filter_rule']
    assert 'engine parser' in e['exchange_filter_rule']
    assert e['decision_time_strictly_before_signal_or_time_fill']
    assert e['same_candle_close_fill_for_signal_or_time'] is False
    assert e['missing_next_open'] == 'INCOMPLETE_CANDIDATE'
    assert e['last_mark_or_synthetic_close'] is False
    assert rule['id'] == 'STRATEGY_CAUSAL_FBL_BT01_V1'
    assert rule['green_path'] == ['open', 'low', 'high', 'close']
    assert rule['red_path'] == ['open', 'high', 'low', 'close']
    assert rule['doji_short_path'] == ['open', 'low', 'high', 'close']
    assert rule['entry_bar_participates'] is True
    assert rule['unresolved_or_missing_bar'] == 'INCOMPLETE_CANDIDATE'
    assert 'next tradable open' in md and 'synthetic final mark' in md


def test_exit_order_and_source_reentry_lock_semantics_are_unambiguous():
    c, md = _load()
    e = c['execution']
    assert e['same_timestamp_open_order'] == [
        'funding_time_lt_t', 'causal_marks_and_cairo_roll', 'due_signal_time_exits',
        'reentry_lock_checks', 'core20_ordered_entry_attempts', 'funding_time_eq_t',
        'fill_bar_intrabar_management']
    assert 'complete its entire intrabar path' in e['event_timeline']
    assert 'due signal/time exit at t wins over a fill-bar gap-stop classification' in e['event_timeline']
    lock = e['reentry_lock_semantics']
    assert lock['enabled'] is True
    assert lock['timeframe_ms'] == 4 * 60 * 60 * 1000
    tf = lock['timeframe_ms']
    cycle_fill, intrabar_fill = 10 * tf, 10 * tf + 1234
    cycle_associated = cycle_fill // tf * tf - tf
    intrabar_associated = intrabar_fill // tf * tf
    assert cycle_associated == 9 * tf and intrabar_associated == 10 * tf
    assert 9 * tf <= cycle_associated and 10 * tf > cycle_associated
    assert 10 * tf <= intrabar_associated and 11 * tf > intrabar_associated
    assert 'entry_decision_open_ms <=' in lock['block_predicate']
    assert 'exact same decision candle' in lock['signal_or_time_exit']
    assert 'following completed candle is eligible' in lock['signal_or_time_exit']
    assert 'candle whose path filled it' in lock['intrabar_exit']
    assert 'INCOMPLETE_CANDIDATE' in lock['determinism']
    assert 'Engine.reentry_lock' in lock['source_parity']
    assert 'source `reentry_lock=True` is binding' in md


def test_unchanged_short_management_formulas_and_step_floors_are_explicit():
    c, _ = _load()
    m = c['execution']['management_semantics']
    assert m['side_sign'] == 'short=-1'
    assert m['initial_r'] == 'R_price = 1.5 * ATR_entry'
    assert m['initial_stop'] == 'stop = entry_exec + R_price'
    assert m['tp1'].startswith('tp1 = entry_exec - 1.0 * R_price')
    assert m['breakeven'].startswith('after a completed favourable leg')
    assert m['full_target'].startswith('target = entry_exec - 2.0 * R_price')
    assert 'MARKET_LOT_SIZE' in m['initial_quantity'] and 'minQty/minNotional' in m['initial_quantity']
    assert 'floor the requested half close' in m['partial_quantity']
    assert set(m['forbidden']) == {
        'DCA', 'pyramiding', 'take-profit ladder', 'same-leg retroactive breakeven'}


def test_isolated_book_sizing_and_cairo_halt_match_corrected_pack():
    c, md = _load()
    b = c['book_and_sizing']
    assert b['reference_start_equity_usdt'] == 500.0
    assert b['risk_fraction_of_current_candidate_book_equity'] == 0.01
    assert b['maximum_open_positions'] == 4
    assert b['gross_leverage_multiple'] == 10.0
    assert b['candidate_book_isolated'] and b['equity_compounding']
    assert 'current marked candidate-book equity' in b['risk_cash_formula']
    assert 'last checksum-verified 1h mark close' in b['mark_timestamp_basis']
    assert 'PIT CORE-20 order' in b['entry_priority']
    assert b['daily_halt_fraction'] == 0.08
    assert b['daily_halt_timezone'].startswith('Africa/Cairo')
    assert 'before the first entry' in b['daily_halt_rule']
    assert 'exits and management never stop' in b['daily_halt_rule']
    assert b['caller_override'].startswith('forbidden')
    assert 'isolated 500 USDT marked-equity book' in md


def test_realistic_costs_funding_and_three_stress_rows_are_single_count():
    c, md = _load()
    x = c['costs']
    assert x['fee_per_fill_bps'] == 5 and x['adverse_slippage_per_fill_bps'] == 2
    assert 'actual Binance USD-M funding' in x['funding']
    assert 'positive rate is a receipt for a short' in x['funding_sign']
    assert x['funding_gap'] == x['mark_gap'] == 'INCOMPLETE_CANDIDATE'
    assert x['cost_stress_multipliers_to_report'] == [1.0, 1.5, 2.0]
    assert 'positive funding credits remain at 1.0x' in x['cost_stress_rule']
    assert x['net_pnl_formula'] == (
        'gross_reference_price_pnl + signed_funding_cash - '
        'fee_cash_on_executable_notional - adverse_slippage_cash')
    assert '5 bp fee and 2 bp adverse slippage' in md


def test_verdict_math_and_full_cost_replays_are_frozen_not_posthoc():
    c, md = _load()
    v = c['verdict_math']
    assert 'initial_filled_quantity_after_leverage_cap_and_step_floor * R_price' in v['initial_risk_cash']
    assert v['trade_r'].startswith('episode_net_cash / initial_risk_cash')
    assert v['net_r'].startswith('math.fsum') and 'closed_episode_count' in v['mean_r']
    assert 'positive trade_r' in v['profit_factor']
    assert 'cashflows sharing an exact timestamp are math.fsum\'d atomically' in v['cashflow_drawdown_r']
    assert 'max of the three fold DDs' in v['cashflow_drawdown_r']
    assert 'sum_s abs' not in v['symbol_contribution']  # executable formula is fully spelt out, not shorthand
    assert 'math.fsum(abs(symbol_net_r)' in v['symbol_contribution']
    assert 'three independent full causal books' in v['stress_replay']
    assert 'Repricing a fixed base trade list post hoc is forbidden' in v['stress_replay']
    assert 'base formula' in v['cost_distance_screen_under_stress']
    assert '1.5x full replay aggregate net_r > 0' in v['promotion_cost_row']
    compact = ' '.join(md.split())
    assert 'actual initial stop risk after the leverage cap' in compact
    assert 'three full replays from 500 USDT, not post-hoc repricing' in compact


def test_statistics_and_exact_family_ledger_are_reproducibly_frozen():
    c, md = _load()
    stats = c['statistics']
    raw, boot, family = stats['raw_p_value'], stats['bootstrap_ci_95_mean_r'], stats['family_ledger']
    assert stats['role'].startswith('required selection-bias diagnostics only')
    assert raw['test'] == 'one-sided Monte Carlo sign-flip test of after-cost net R versus zero'
    assert raw['alternative'] == 'greater'
    assert raw['unit'].startswith('UTC calendar exit day')
    assert raw['statistic'] == 'T = math.fsum(day_net_r)'
    assert raw['resamples'] == 100000
    assert raw['rng'] == 'numpy.random.Generator(PCG64)' and raw['seed'] == 20261106115043
    assert raw['formula'].startswith('(1 + count(T_star >= T_observed)) / 100001')
    assert raw['empty_sample'].startswith('raw_p_value=1.0')
    assert boot['unit'].startswith('UTC calendar exit-day cluster')
    assert boot['resamples'] == 10000 and boot['seed'] == 20261106115044
    assert boot['rng'] == 'numpy.random.Generator(PCG64)'
    assert 'sample the observed exit-day clusters with replacement' in boot['procedure']
    assert boot['interval'].startswith('ordinary percentile')
    assert boot['empty_sample'].startswith('[null,null]')
    assert family['path'].endswith('rank_breadth_reversal_4h_family.v1.json')
    assert _sha(FAMILY) == family['byte_sha256']
    with open(FAMILY, encoding='utf-8') as fh:
        ledger = json.load(fh)
    ledger_core = {k: ledger[k] for k in ledger if k != 'digest'}
    assert _canonical_digest(ledger_core) == ledger['digest'] == family['content_digest']
    assert len(ledger['trials']) == ledger['holm_family_size'] == family['m'] == 3
    assert ledger['holm_missing_prior_p_value'] == 1.0
    assert [x['status'] for x in ledger['trials']] == [
        'REJECTED_CONTAMINATED_CONTEXT', 'REJECTED_CONTAMINATED_CONTEXT',
        'PREREGISTERED_UNEVALUATED']
    assert 'adjusted_j = max over k<=j' in stats['holm_adjustment']
    assert 'mandatory diagnostics but never promotion gates' in ' '.join(md.split())


def test_only_three_unopened_folds_are_allowed_and_cutoff_is_hard():
    c, md = _load()
    folds = c['folds']
    assert [(x['name'], x['start'], x['end']) for x in folds] == [
        ('wf1', '2025-04-01T00:00:00Z', '2025-09-30T00:00:00Z'),
        ('wf2', '2025-10-01T00:00:00Z', '2026-03-31T00:00:00Z'),
        ('wf3', '2026-04-01T00:00:00Z', '2026-09-28T00:00:00Z')]
    cutoff = _ts(c['data']['forbid_read_at_or_after'])
    assert all(_ts(row['end']) <= cutoff for row in folds)
    assert c['data']['viewed_holdout'] == '[2026-09-28,2026-11-05)'
    assert c['scope']['old_holdout_reuse'] is False
    assert 'Nothing at or after' in md


def test_fold_local_warmup_and_embargo_close_every_episode_inside_fold():
    c, _ = _load()
    s = c['split_rules']
    assert s['first_eligible_decision_index'] == 'L-1 where L=200'
    assert s['first_eligible_entry_index'] == 'L'
    assert s['time_cap_exit_index'] == 'x=e+18'
    assert s['entry_eligibility_inequality'] == 'e >= 200 and e + 18 < N'
    assert s['last_eligible_entry_index'] == 'N-19'
    assert s['next_open_embargo_bars_at_end'] == 18
    assert s['episode_must_close_inside_fold'] and s['folds_reported_separately']
    assert s['fold_selection'] is False and s['pool_before_per_fold_reporting'] is False
    for n in (500, 1000):
        assert 200 + 18 < n
        assert (n - 19) + 18 == n - 1
        assert (n - 18) + 18 == n


def test_promotion_gates_freeze_frequency_profit_risk_regime_and_concentration():
    c, md = _load()
    g = c['promotion_gates']
    assert g['aggregate_net_r_gt'] == g['aggregate_mean_r_gt'] == 0
    assert g['aggregate_pf_gt'] == 1.05
    assert g['minimum_closed_trades'] == 120 and g['minimum_symbols'] == 8
    assert (g['minimum_positive_folds'], g['of_folds']) == (2, 3)
    assert g['monthly_bucket_timezone'] == 'UTC'
    assert 'include zero-close months' in g['monthly_bucket_rule']
    assert g['minimum_monthly_trade_median'] == 4
    assert g['max_dd_r_lte'] == 15
    assert g['minimum_closes_per_regime'] == 15
    assert g['minimum_regime_mean_r'] == -0.05
    assert g['max_symbol_absolute_contribution_fraction'] == 0.35
    assert g['positive_after_1_5x_cost_stress']
    assert g['causal_parity_required'] and g['independent_review_required']
    assert g['full_ledger_and_multiplicity_disclosure_required']
    assert g['maximum_incomplete_rows'] == 0 and g['missing_or_incomplete_is_pass'] is False
    compact = ' '.join(md.split())
    assert 'at least 120 closes' in compact and 'including zero-close months' in compact


def test_required_reports_and_runner_integrity_fail_closed_before_market_reads():
    c, _ = _load()
    required = set(c['required_report'])
    assert {'trade_count', 'net_r', 'mean_r', 'profit_factor', 'max_dd_r'} <= required
    assert {'raw_p_value', 'holm_adjusted_p_value', 'bootstrap_ci_95_mean_r'} <= required
    assert {'fee_drag_r', 'slippage_drag_r', 'funding_drag_r', 'gross_r'} <= required
    assert {'utc_monthly_close_counts_including_zeros', 'regime_breakdown',
            'symbol_concentration', 'full_candidate_ledger'} <= required
    assert all(c['runner_integrity'].values())
    assert c['stop_conditions'][1].startswith('Any market or archive read before contract merge')


def test_activation_remains_default_off_protected_and_non_mainnet():
    c, md = _load()
    a = c['activation']
    assert a['default_enabled'] is False and a['paper_testnet_only'] is True
    assert a['requires_separate_exact_head_acceptance']
    assert a['requires_protected_execution'] and a['requires_rollback_ready']
    assert a['mainnet_authority'] is False
    assert 'grants no runtime or mainnet authority' in md
