import json
import re
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def load(name):
    return json.loads((ROOT / 'contracts' / name).read_text(encoding='utf-8'))


def test_signal_intent_contract_is_strict_versioned_and_sender_cannot_choose_authority():
    s = load('signal_intent_v1.schema.json')
    assert s['$schema'].endswith('2020-12/schema')
    assert s['additionalProperties'] is False
    assert s['properties']['contract_version'] == {'const': 1}
    assert {'signal_id', 'strategy_id', 'strategy_version', 'generated_at_ms', 'expires_at_ms', 'dry_run'} <= set(s['required'])
    assert not ({'secret', 'environment', 'mode', 'live', 'authority'} & set(s['properties']))


def test_signal_intent_uses_explicit_units_and_canonical_decimal_text():
    s = load('signal_intent_v1.schema.json')
    assert s['$defs']['value']['properties']['unit']['enum'] == ['price', 'percent', 'ticks', 'atr']
    assert s['$defs']['value']['properties']['value'] == {'$ref': '#/$defs/positiveDecimal'}
    assert s['$defs']['decimal']['type'] == 'string'
    positive = re.compile(s['$defs']['positiveDecimal']['pattern'])
    assert all(positive.fullmatch(value) for value in ('0.01', '1', '1.250'))
    assert not any(positive.fullmatch(value) for value in ('0', '-0', '-1', '00.1', '1.'))
    assert s['$defs']['order']['additionalProperties'] is False
    assert s['$defs']['risk']['additionalProperties'] is False


def test_signal_intent_requires_protection_for_entry_and_target_for_exit_actions():
    s = load('signal_intent_v1.schema.json')
    entry, exit_ = s['allOf']
    assert set(entry['then']['required']) == {'side', 'order', 'risk', 'stop'}
    assert set(exit_['if']['properties']['action']['enum']) == {'reduce', 'close', 'modify'}
    assert exit_['then']['required'] == ['target_ref']
    assert s['properties']['target_ref']['type'] == 'string'


def test_signal_result_has_stable_machine_outcome_and_no_open_ended_fields():
    s = load('signal_result_v1.schema.json')
    assert s['additionalProperties'] is False
    assert set(s['properties']['status']['enum']) == {'validated', 'accepted', 'rejected', 'duplicate', 'held'}
    assert {'signal_id', 'status', 'reason_code', 'retryable', 'recorded_at_ms'} <= set(s['required'])
    assert s['properties']['reason_code']['pattern'].startswith('^')


def test_candidate_score_keeps_decision_dimensions_separate_and_has_stand_down():
    s = load('candidate_score_v1.schema.json')
    scores = s['properties']['scores']
    assert scores['additionalProperties'] is False
    assert set(scores['required']) == {
        'opportunity', 'entry_quality', 'hold_quality', 'regime_fit',
        'evidence_readiness', 'operational_readiness',
    }
    assert all(scores['properties'][name] == {'type': 'integer', 'minimum': 0, 'maximum': 100}
               for name in scores['required'])
    assert set(s['properties']['entry_state']['enum']) == {
        'READY', 'SETUP', 'WAIT', 'EXTENDED', 'BREAKING', 'STAND_DOWN',
    }
    assert 'risk_multiplier' not in s['properties']
    stop_value = re.compile(s['properties']['structural_stop']['properties']['value']['pattern'])
    assert stop_value.fullmatch('0.5')
    assert not stop_value.fullmatch('0')


def test_scorecard_distinguishes_safety_from_profile_activity_controls():
    text = (ROOT / 'docs' / 'newcore' / 'SCANNER_SCORECARD_CONTRACT.md').read_text(encoding='utf-8')
    assert 'Ranking is not a blanket veto' in text
    assert 'unconditional new-entry blocks' in text
    assert 'expected opportunity-frequency band' in text
    assert 'starvation diagnostic' in text
    assert 'never silently loosens thresholds or forces a trade' in text
