"""ML-TRAIN-01 contract twin: the JSON twin parses, stays DRAFT/unsigned and agrees with the markdown on the key numbers.

Contract only - no data, no training. Both twins live under contracts/ml_train_01/ (Codex 6453098718), not in
the documentation tree, so every edit runs normal verification. Numbers and names the contract takes from code are
pinned against the code too.
"""
import json
import math
import os

import pytest

from ml_shadow import trainer
from ml_shadow.features import FEATURE_NAMES

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
CONTRACT_DIR = os.path.join(ROOT, 'contracts', 'ml_train_01')
MD = os.path.join(CONTRACT_DIR, 'ML_TRAIN_01_CONTRACT.md')
JS = os.path.join(CONTRACT_DIR, 'ml_train_01_contract.v1.json')


@pytest.fixture(scope='module')
def c():
    with open(JS, encoding='utf-8') as fh:
        return json.load(fh)


@pytest.fixture(scope='module')
def md():
    with open(MD, encoding='utf-8') as fh:
        return fh.read()


def _get(c, path):
    for k in path:
        c = c[k]
    return c


def test_status_is_draft_and_unsigned(c, md):
    assert c['contract'] == 'ML-TRAIN-01' and c['version'] == 1
    assert c['status'] == 'DRAFT' and c['signed_by'] is None and c['frozen_at'] is None
    assert '**Status: DRAFT — unsigned.**' in md


def test_frozen_order_pairing_first(c, md):
    assert c['order'] == ['PAIRED_LEDGER', 'DIAGNOSTICS', 'TRAINING', 'SHADOW_ONLY']
    assert '**(1) paired ledger → (2) diagnostics → (3) training → (4) shadow-only authority.**' in md
    heads = [md.index(h) for h in ('## 1. Paired training ledger (ML-PAIR-01) — first', '## 2. Diagnostics — second',
                                   '## 3. Training — only then', '## 4. Shadow-only authority')]
    assert heads == sorted(heads)
    assert c['tickets'][0] == 'ML-PAIR-01'
    pos = [md.index(f'**{t}') for t in c['tickets']]
    assert pos == sorted(pos)


def test_authority_none_reduce_only_skipped_never_labels(c, md):
    s = c['scope']
    assert s['authority'] == 'none' and s['runtime_authority'] == 'DEFERRED'
    assert s['future_authority_direction'] == 'reduce_only' and 'never increase' in md
    assert s['paper_only'] is True and s['default_on'] is False and s['engine_imports_trainer'] is False
    assert s['skipped_are_labels'] is False and '**Skipped opportunities are not supervised outcomes.**' in md
    assert c['ladder']['deferred'] == ['VETO_CONSIDERATION'] and '**DEFERRED / out of scope**' in md


@pytest.mark.parametrize('key,text', [
    ('unique_decisions', '| Unique decisions | {:,} |'),
    ('measured_close_labels', '| Measured close labels | {} |'),
    ('surviving_joins', '| Feature-label joins that survive | **{}** |'),
    ('orphaned_labels', '| Orphaned labels (decision rotated away) | **{}** |'),
    ('joined_decision_days', '| Joined history | {} decision days |'),
])
def test_codex_reported_figures_parity(c, md, key, text):
    assert c['codex_reported']['read_by_build'] is False and '**Build did not read live data.**' in md
    assert text.format(c['codex_reported'][key]) in md
    cr = c['codex_reported']
    assert f"| Taken / pending decisions | {cr['taken_decisions']} / {cr['pending_decisions']} |" in md


@pytest.mark.parametrize('path,fmt', [
    (('thresholds', 'min_paired_labels'), '| Paired labels | **≥ {:,}** |'),
    (('thresholds', 'min_family_side_cell'), '| Per family-side cell | **≥ {}** |'),
    (('thresholds', 'min_future_folds'), '| Future folds | **≥ {}**'),
    (('thresholds', 'min_fold_labels'), 'each **≥ {}** labels |'),
    (('thresholds', 'min_cairo_days'), '| Cairo days | **≥ {}**'),
    (('thresholds', 'min_regimes'), 'multiple regimes (**≥ {}**)'),
    (('thresholds', 'min_oof_auc'), '| Out-of-fold AUC | **≥ {}**'),
    (('thresholds', 'tranche_day_block_lower_ci_gt'), 'lower CI **> {}**'),
    (('thresholds', 'min_positive_folds'), 'positive in **≥ {} of 4** folds'),
    (('thresholds', 'cost_stress_mult'), 'non-negative at **{}×** variable cost'),
    (('thresholds', 'min_activity_retained'), '| Activity retained | **≥ {:.0%}** of trades |'),
    (('thresholds', 'max_concentration'), '| Concentration | **≤ {:.0%}** |'),
    (('thresholds', 'prospective_min_closes'), '**≥ {}** new cost-complete shadow closes'),
    (('thresholds', 'prospective_min_cairo_days'), 'over **≥ {}** Cairo days'),
    (('thresholds', 'bootstrap_reps'), 'uses {:,} reps'),
    (('dataset', 'max_censored_frac'), 'censored > **{:.0%}**'),
    (('split', 'sealed_holdout_frac'), 'The **sealed holdout** is the last **{:.0%}** of Cairo days'),
    (('calibration', 'isotonic_min_labels'), 'Isotonic is not allowed in v1 (it needs ≥ {:,}'),
    (('leakage', 'shuffle_perms'), '**{}** permutations'),
    (('leakage', 'null_skill_max'), 'mean null skill must be ≤ **{}**'),
    (('leakage', 'canary_auc_flag'), 'AUC > **{:.2f}**'),
    (('model', 'l2'), '`l2 = {}`'),
])
def test_md_json_parity(c, md, path, fmt):
    assert fmt.format(_get(c, path)) in md


def test_thresholds_tighten_only(c, md):
    t = c['thresholds']
    assert t['source'] == '6452728797' and t['may_tighten'] is True and t['may_weaken_silently'] is False
    assert 'may **tighten** during independent review but **never weaken silently**' in md
    assert t['min_positive_folds'] <= t['of_folds'] == t['min_future_folds']
    assert c['current_verdict'] == 'INSUFFICIENT_DATA'
    assert c['codex_reported']['surviving_joins'] < t['min_paired_labels']


def test_pair_ledger_spec_named_in_md(c, md):
    p = c['pair_ledger']
    assert p['rotated'] is False and p['append_only'] is True and p['require_costs_complete'] is True
    assert p['pair_key'] == ['decision_id', 'cost_rev_id'] and '`(decision_id, cost_rev_id)`' in md
    assert f"`{p['schema']}`" in md and p['net_basis_paired'] == ['measured']
    for step in ('engine._measured_done', 'engine._ml_label', "LedgerWriter.submit('label', row)", 'LedgerWriter._do_write'):
        assert step in md and step.split('(')[0] in p['write_point'], step
    for f in p['fields']:
        assert f'`{f}`' in md, f
    for f in p['cost_fields']:
        assert f'`{f}`' in md, f
    assert '`labelable_decisions.jsonl`' in md and '`<data>/ml_shadow/pairs/pairs.jsonl`' in md
    for f in c['diagnostics']['must_be_zero_since_epoch'] + c['diagnostics']['historical_diagnostic']:
        assert f'`{f}`' in md, f
    for s in c['failure_statuses'] + c['artifact']['statuses'] + c['ladder']['rungs']:
        assert f'`{s}`' in md, s


def test_code_facts_the_contract_relies_on():
    import engine
    assert (engine.Engine.ML_MAX_BYTES, engine.Engine.ML_MAX_FILES) == (2_000_000, 5)   # the rotation that orphans labels
    for name in ('_measured_done', '_ml_label', '_measured_followup', '_ml_decision'):
        assert callable(getattr(engine.Engine, name)), name
    assert (trainer.MIN_TOTAL, trainer.MIN_BUCKET) == (1000, 100)
    assert len(FEATURE_NAMES) == 15          # 13 numeric + regime + session (section 3.3)
    for f in ('atr_pct', 'ema_slope', 'regime', 'session'):
        assert f in FEATURE_NAMES


def test_backfill_and_non_training_closes(c, md):
    b = c['backfill']
    assert b['tag'] == 'backfill_v1' and '**`source = backfill_v1`**' in md
    assert b['counts_toward_prospective'] is False and '**never count toward the prospective gate**' in md
    assert b['counts_toward_training'] == 'only_if_all_live_rules_pass' and b['else'] == 'diagnostics_only'
    assert b['synthesize_orphans'] is False and '**180 orphaned labels are never synthesized**' in md
    assert c['thresholds']['prospective_sources'] == ['live'] and '(`source = live` only)' in md
    n = c['non_training_closes']
    assert n['bases'] == ['acknowledged', 'booked'] and n['training_pairs'] is False
    assert '**Acknowledged and booked closes are never training pairs.**' in md and '`non_training_closes`' in md
    assert c['pair_ledger']['net_basis_paired'] == ['measured'] and c['pair_ledger']['sources'] == ['live', 'backfill_v1']


def test_epoch_relative_counters(c, md):
    d = c['diagnostics']
    assert d['epoch'] == 'pair_epoch_start' and '**ML-PAIR-01 epoch**' in md
    assert 'They never block training.' in md and 'historical_orphans' in d['historical_diagnostic']


def test_pooled_model_with_family_side(c, md):
    m = c['model']
    assert m['pooled'] is True and m['per_cell_models'] is False and '**There are no per-cell models in v1**' in md
    assert m['extra_categorical'] == ['family', 'side'] and m['feature_hash_covers_family_side'] is True
    assert f"`{m['feature_schema']}`" in md and '**plus explicit categorical `family` and `side`**' in md
    assert m['report_by'] == ['family', 'side', 'regime']
    for f in ('family', 'side'):
        assert f in c['diagnostics']['critical_features'] and f not in FEATURE_NAMES   # trainer must add them (ticket 5)


def test_code_id_binds_build_commit_and_bundle(c, md):
    ci = c['pair_ledger']['code_id']
    assert ci['build_id'] == 'build_info.BUILD_ID' and ci['source_commit'] == 'build.json source_commit'
    assert ci['dev_build_training_eligible'] is False and "`BUILD_ID == 'dev'`" in md
    for m in ci['bundle_hash_modules']:
        assert f'`{m}`' in md, m
    with open(os.path.join(ROOT, 'installer.ps1'), encoding='utf-8') as fh:
        inst = fh.read()
    assert "'build_info.py'" in inst and 'BUILD_ID' in inst and 'source_commit' in inst
    with open(os.path.join(ROOT, 'app.py'), encoding='utf-8') as fh:
        assert 'from build_info import BUILD_ID' in fh.read()


def test_frozen_horizons_match_strategy_code(c, md):
    import strategies as S
    h = c['horizons']
    assert h['rule'] == 'ex_ante_max_bars_x_tf' and h['observed_holding_used'] is False
    assert 'max observed holding' not in md
    tf_h = {'4h': 4, '1h': 1, '15m': 0.25}
    with open(os.path.join(ROOT, 'strategies.py'), encoding='utf-8') as fh:
        lines = fh.read().splitlines()
    fam = {}
    for e in h['eligible']:
        st = S.STRATEGIES[e['strategy']]
        assert st['mgmt'].get('max_bars') == e['max_bars'], e['strategy']
        assert st.get('tf') in (None, e['tf']), e['strategy']
        assert e['hours'] == e['max_bars'] * tf_h[e['tf']], e['strategy']
        ln = int(e['source'].split(':')[1])
        assert f"max_bars={e['max_bars']}" in lines[ln - 1], e['source']
        assert f"| `{e['strategy']}` | {e['tf']}" in md and f"| {e['hours']} h | `{e['source']}`" in md, e['strategy']
        fam[e['family']] = max(fam.get(e['family'], 0), e['hours'])
        assert e['strategy'] not in ('dca_dip', 'exp_rip_sell_dca')
    assert fam == h['family_max_hours']
    for k in h['excluded_uncapped']:
        assert S.STRATEGIES[k]['mgmt'].get('max_bars') is None, k
        assert f'`{k}`' in md, k
    for k in h['excluded_always']:
        assert f'`{k}`' in md
    covered = {e['strategy'] for e in h['eligible']} | set(h['excluded_uncapped']) | set(h['excluded_always'])
    assert covered == set(S.STRATEGIES)                         # every registered strategy is classified


def test_per_row_horizon_purging_no_global_embargo(c, md):
    sp = c['split']
    def keys(o):
        if isinstance(o, dict):
            for k, v in o.items():
                yield k
                yield from keys(v)
        elif isinstance(o, list):
            for v in o:
                yield from keys(v)
    # the only embargo key left anywhere is the explicit "global_embargo": false - no embargo rule or day count
    assert [k for k in keys(c) if 'embargo' in k] == ['global_embargo']
    p = sp['purge']
    assert p['rule'] == 'per_row_strategy_horizon' and p['global_embargo'] is False
    assert 'There is no single global embargo.**' in md and 'Overlap embargo' not in md
    assert 'embargo_days' not in md and 'T_cut − embargo' not in md
    assert p['row_kept_if_before_boundary'] == ['candle_time_plus_frozen_horizon', 'closed_at']
    assert '`candle_time + horizon(strategy, tf)`' in md and 'its actual close, `closed_at`' in md
    assert p['applies_to'] == ['training', 'calibration'] and 'A training or\n  calibration row' in md
    assert p['purge_exact_overlapping_symbol_episodes'] is True and '**Exact overlapping symbol episodes are purged' in md
    assert p['short_rows_inherit_longest_horizon'] is False
    assert "**Shorter-horizon rows do not inherit `exp_trend`'s 30-day exclusion.**" in md
    assert p['horizon_from_future_or_holdout_duration'] is False
    assert '**No future or holdout duration is read to choose a horizon.**' in md


def test_too_few_rows_is_insufficient_data_never_relaxed(c, md):
    p = c['split']['purge']
    assert p['too_few_rows_outcome'] == 'INSUFFICIENT_DATA' and p['gates_relaxed'] is False
    assert ('**If per-row purging leaves too few rows, the outcome is\n  `INSUFFICIENT_DATA`**, and no gate is ever relaxed.'
            in md)
    assert c['thresholds']['min_future_folds'] == 4 and c['thresholds']['min_fold_labels'] == 50
    assert c['thresholds']['min_cairo_days'] == 30 and c['thresholds']['min_paired_labels'] == 1000


def test_v1_capped_only_and_ml_horizon_02_deferred(c, md):
    import strategies as S
    h = c['horizons']
    assert h['v1_scope'] == 'currently_capped_only' and h['uncapped_excluded_from_v1'] is True
    assert h['uncapped_blocks_strategy_or_paper_path'] is False and h['pair01_invents_uncapped_labels'] is False
    assert '**ML v1 = currently capped strategies only.**' in md and '**does not block their strategy or PAPER path**' in md
    assert '**not invent labels**' in md
    assert {'ema_mom', 'breakout_pyramid'} <= set(h['excluded_uncapped'])
    eligible = {e['strategy'] for e in h['eligible']}
    assert not eligible & set(h['excluded_uncapped'])
    assert all(S.STRATEGIES[e]['mgmt'].get('max_bars') for e in eligible)
    f = h['follow_up']
    assert f == {'contract': 'ML-HORIZON-02', 'status': 'DEFERRED', 'scope': 'deterministic_causal_label_only_horizons',
                 'runtime_trade_management_change': False}
    assert c['deferred_contracts'] == ['ML-HORIZON-02'] and 'ML-HORIZON-02' not in c['tickets']
    assert '**DEFERRED follow-up contract:** **`ML-HORIZON-02`**' in md and 'no runtime trade-management change' in md
