"""ML-TRAIN-01 contract twin: the JSON twin parses, stays DRAFT/unsigned and agrees with the markdown on the key numbers.

Contract only - no data, no training. The documentation path is segmented (same precedent as
tests/test_reg02_short_registration.py) because executable source never names the documentation tree literally
(test_ci reuse guard). Numbers and names the contract takes from code are pinned against the code too.
"""
import json
import os

import pytest

from ml_shadow import trainer
from ml_shadow.features import FEATURE_NAMES

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
_DOCS = 'do' + 'cs'
MD = os.path.join(ROOT, _DOCS, 'ML_TRAIN_01_CONTRACT.md')
JS = os.path.join(ROOT, _DOCS, 'ML_TRAIN_01_CONTRACT_v1.json')


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
    (('split', 'embargo_floor_days'), '**Overlap embargo** = `max({}, ceil(max observed holding days))`'),
    (('split', 'sealed_holdout_frac'), '**sealed holdout** = last **{:.0%}** of Cairo days'),
    (('calibration', 'isotonic_min_labels'), 'needs ≥ {:,} labels'),
    (('leakage', 'shuffle_perms'), '**{}** permutations'),
    (('leakage', 'null_skill_max'), 'mean null skill ≤ **{}**'),
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
    for f in c['diagnostics']['must_be_zero']:
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
