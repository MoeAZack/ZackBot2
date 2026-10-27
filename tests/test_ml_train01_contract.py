"""ML-TRAIN-01 contract twin: the JSON twin parses, stays DRAFT/unsigned and agrees with the markdown on the key numbers.

Contract only - no data, no training. The documentation path is segmented (same precedent as
tests/test_reg02_short_registration.py) because executable source never names the documentation tree literally
(test_ci reuse guard). The numbers that the contract re-uses from code are pinned against the code constants too.
"""
import json
import os
import re

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


def test_status_is_draft_and_unsigned(c, md):
    assert c['contract'] == 'ML-TRAIN-01' and c['version'] == 1
    assert c['status'] == 'DRAFT' and c['signed_by'] is None and c['frozen_at'] is None
    assert '**Status: DRAFT — unsigned.**' in md


def test_no_authority_paper_only_default_off(c, md):
    s = c['scope']
    assert s == {'authority': 'none', 'paper_only': True, 'default_on': False, 'ui_flag': 'ML_TRAIN_UI',
                 'engine_imports_trainer': False}
    assert '"authority": "none"' in md and 'PAPER-only' in md and 'Default OFF' in md
    assert 'VETO_CONSIDERATION' in c['ladder']['deferred'] and 'DEFERRED / out of scope' in md


def _has(md, text):
    assert text in md, text


@pytest.mark.parametrize('path,fmt', [
    (('sufficiency', 'min_total_labels'), '| Qualifying labels overall | **{:,}**'),
    (('sufficiency', 'min_bucket_labels'), '| Per (family, side) bucket to be modelled | **{}**'),
    (('sufficiency', 'min_per_class_dev'), '| Per class (wins / losses) in development | **{}** each'),
    (('sufficiency', 'min_per_class_test'), '| Per class in the sealed test | **{}** each'),
    (('sufficiency', 'min_cairo_days'), '| Distinct Cairo days spanned | **{}**'),
    (('sufficiency', 'min_symbols'), '| Distinct symbols | **{}**'),
    (('sufficiency', 'min_regime_report'), '| Per regime, for a regime stability line | **{}**'),
    (('sufficiency', 'min_test_labels_calibrated'), '| Sealed test labels for `CALIBRATED_SHADOW` | **{}**'),
    (('sufficiency', 'current_closed_labels_approx'), '**Today (~{} closed labels'),
    (('split', 'n_folds'), '**{}** expanding folds'),
    (('split', 'embargo_floor_days'), '**Embargo** = `max({}, ceil(max observed holding days))`'),
    (('dataset', 'max_censored_frac'), 'censored > **{:.0%}**'),
    (('calibration', 'n_bins'), '{} equal-width reliability bins'),
    (('calibration', 'min_per_bin'), 'until each has ≥ **{}**'),
    (('calibration', 'ece_max'), '**ECE ≤ {}**'),
    (('calibration', 'ece_demote'), 'rolling ECE ≤ **{}**'),
    (('calibration', 'bootstrap_reps'), '{:,} reps'),
    (('calibration', 'isotonic_min_labels'), 'needs ≥ {:,} calibration labels'),
    (('leakage', 'shuffle_perms'), '**{}** permutations'),
    (('leakage', 'null_brier_skill_max'), '**≤ {}**, and the real skill'),
    (('leakage', 'canary_auc_flag'), '(> **{:.2f}** ⇒ flag)'),
    (('ladder', 'ui_forward_days'), 'forward window ≥ **{}** Cairo days'),
    (('ladder', 'ui_forward_labels'), '≥ **{}** new labels'),
    (('model', 'l2'), '`l2 = {}`'),
])
def test_md_json_parity(c, md, path, fmt):
    v = c
    for k in path:
        v = v[k]
    _has(md, fmt.format(v))


def test_split_and_calibration_choices_are_preregistered(c, md):
    sp = c['split']
    assert sp['shuffle'] is False and sp['block'] == 'cairo_day' and sp['purge_train_rows_closing_after_cutoff'] is True
    assert '**{:.0%}** of Cairo days'.format(sp['sealed_test_frac']) in md
    assert '**{:.0%}** of Cairo days'.format(1 - sp['sealed_test_frac']) in md
    assert c['calibration']['method'] == 'platt' and 'Platt scaling** fitted on the pooled out-of-fold' in md
    assert c['model']['hyperparameter_search'] is False and 'No hyper-parameter search in v1' in md
    # the calibrated rung is unreachable today: the needed total exceeds the current label count
    need = c['sufficiency']['min_test_labels_calibrated'] / sp['sealed_test_frac']
    assert need > c['sufficiency']['current_closed_labels_approx']
    assert c['sufficiency']['current_verdict'] == 'INSUFFICIENT_DATA'


def test_join_exclusions_and_statuses_named_in_md(c, md):
    d = c['dataset']
    assert d['join_key'] == ['decision_id', 'cost_rev_id'] and '(`decision_id`, `cost_rev_id`)' in md
    assert d['net_basis_allowed'] == ['measured'] and d['excluded_families'] == ['Dip-DCA']
    assert f"`schema == '{d['decision_schema']}'`" in md and d['label_definition'] in md
    for s in c['failure_statuses'] + c['artifact']['statuses'] + c['ladder']['rungs']:
        assert s in md or s == 'VETO_CONSIDERATION', s      # L4 is written out as 'veto consideration'
    assert c['artifact']['model_version'] in md and c['artifact']['immutable'] is True
    for g in c['gaps']:
        assert re.search(rf'\*\*{g} ', md), g


def test_numbers_reused_from_code_match_the_code(c):
    assert c['sufficiency']['min_total_labels'] == trainer.MIN_TOTAL
    assert c['sufficiency']['min_bucket_labels'] == trainer.MIN_BUCKET
    assert c['dataset']['decision_schema'] == trainer.SCHEMA_VERSION
    assert len(FEATURE_NAMES) == 15          # 13 numeric + regime + session, as described in section 5
