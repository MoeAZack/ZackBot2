"""C267 metadata-only contract proofs; no archive, claim or outcome access."""
import copy
from datetime import datetime,timedelta,timezone
import hashlib
import json

import pytest
from tools.research import c267_prereg as C


def test_byte_bound_parent_and_own_draft_model_remain_locked():
    p,m=C.load_metadata()
    assert p['candidate_id']==267 and p['lineage']['parent_candidate']==259
    assert hashlib.sha256((C.ROOT/C.PARENT_PATH).read_bytes()).hexdigest()==C.PARENT_SHA256
    assert p['lifecycle_source_blocker']['before_first_row'] and not m['lifecycle_tradability_authority']['authorization_ready']
    assert p['exposure_disclosure']['zero_prior_outcome_knowledge_claimed'] is False
    assert m['known_entry_risk']['adverse_reserve_multiplier']==m['stress']['funding_multiplier']==1
    with pytest.raises(C.C267Locked,match='LIFECYCLE_CODE_SOURCE_REVIEW'):C.assert_execution_authorized()


@pytest.mark.parametrize('attack',['signal','impact','DD','holdout_sample','resamples','seed','pool_sides','reserve_1_5','fund_stress_1_5','source_grant','lifecycle_bypass'])
def test_scope_or_economics_drift_is_not_an_accepted_source_successor(attack):
    p,m=copy.deepcopy(C.load_metadata());parent=json.loads((C.ROOT/C.PARENT_PATH).read_bytes())
    if attack=='signal':p['inherited_verbatim']['signal']['btc_impulse']='tuned impulse'
    if attack=='impact':p['inherited_verbatim']['costs']['impact']='prior1h participation.001'
    if attack=='DD':p['gates']['development_reject_if_any_per_side'][-2]='closed DD only'
    if attack=='holdout_sample':p['gates']['holdout_pass_all_required_per_side'][0]='at least1 trade'
    if attack=='resamples':p['statistics']['resamples']=10000
    if attack=='seed':p['statistics']['long_ci_seed']=26620261108
    if attack=='pool_sides':p['multiplicity']['promotable_primary_count']=1
    if attack=='reserve_1_5':m['known_entry_risk']['adverse_reserve_multiplier']=1.5
    if attack=='fund_stress_1_5':m['stress']['funding_multiplier']=1.5
    if attack=='source_grant':p['source_authority_bound']=True
    if attack=='lifecycle_bypass':m['lifecycle_tradability_authority']['authorization_ready']=True
    with pytest.raises(C.C267Locked):C.validate_metadata(p,m,parent)


def test_own_911h_holdout_reset_original_723h_warmup_and_fixed_twoside_science():
    p,m=C.load_metadata();s=m['StageB_boundary']
    start=datetime.fromisoformat(s['start_UTC'].replace('Z','+00:00'))
    end=datetime.fromisoformat(s['end_exclusive_UTC'].replace('Z','+00:00'))
    assert (end-start).total_seconds()/3600==911
    earliest=start+timedelta(hours=723)
    assert earliest==datetime(2026,10,28,4,tzinfo=timezone.utc)
    assert (end-earliest).total_seconds()/3600==188
    assert 3*int((188-1e-6)//12)==45
    assert p['statistics']['resamples']==20000 and p['statistics']['expected_block_length_trades']==21
    assert p['statistics']['long_null_seed']==267202611071 and p['statistics']['short_ci_seed']==267202611074
    assert 1/20001<.05/(267*2)<2/20001
    assert 'due>=END' in s['entry_veto'] and 'No ENDopen' in s['entry_veto']
    assert 'first' not in p['gates']['holdout_pass_all_required_per_side'][0]


def test_three_lifecycle_clocks_do_not_fabricate_advance_notice_or_settlement():
    p,m=C.load_metadata();life=m['lifecycle_tradability_authority'];rules=life['three_clock_rules']
    assert set(rules)=={'entry_at_or_after_effective_restriction','pre_effective_horizon','existing_exposed_settlement'}
    assert 'entry>=' in rules['entry_at_or_after_effective_restriction']
    assert 'separately authenticated historical announcement-asof' in rules['pre_effective_horizon']
    assert 'DATA_INCOMPLETE' in rules['existing_exposed_settlement']
    assert life['metadata_and_pins']=={'path':C.CATALOG_PATH,'sha256':C.CATALOG_SHA256}
    assert p['lifecycle_source_blocker']['specific_symbol_exclusions'] is False
    assert p['future_PAPER_candidate_bounds']['maximum_all_C267_positions_across_both_sides']==2


def test_pinned_current_event_does_not_upgrade_publication_date_into_asof_or_fill():
    pin,event=C.load_catalog_metadata()[0]
    assert pin['kind']=='DERIVED_CURRENT_OFFICIAL_OBSERVATION'
    assert event['effective_no_new_entry_ms']==1736152200000 and event['effective_settlement_ms']==1736154000000
    assert event['advance_information_available_ms'] is None and not event['original_asof_version_verified']
    assert event['raw_http_sha256'] is None and event['settlement_price_or_cost_proof'] is None
    assert event['displayed_publication_timezone']=='UNSPECIFIED_DO_NOT_INFER'


def test_two_generic_effective_events_and_ranked_entry_override_do_not_change_science():
    p,m=C.load_metadata();records=C.load_catalog_metadata()
    assert {e['symbol'] for pin,e in records}=={'FTMUSDT','BNXUSDT'}
    assert all(pin['kind']=='DERIVED_CURRENT_OFFICIAL_OBSERVATION' and e['advance_information_available_ms'] is None for pin,e in records)
    override=m['accounting']['entry_phase_override']
    assert override['candidate_scope']==267 and override['preserve_ranked_causal_admission_sizing'] is True
    assert '2*admission_ordinal+0' in override['cash_sequence'] and '2*admission_ordinal+1' in override['cash_sequence']
    assert p['statistics']['resamples']==20000 and p['statistics']['short_null_seed']==267202611073
