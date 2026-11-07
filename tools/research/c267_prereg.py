"""Private C267 metadata only. No loader, source, claim, receipt or execution."""
import copy
import hashlib
import json
from pathlib import Path

ROOT=Path(__file__).resolve().parents[2]
PARENT_PATH='research_evidence/prereg/btc_residual_leadlag_continuation_c259_stagea.v1.json'
PARENT_SHA256='1aad55f092c0c308cb901701e2ee8bd3062d1eee9a07e13729673efbf5b17719'
PROPOSAL_PATH='contracts/c267_btc_residual_leadlag/c267_prereg_proposal.v4.json'
PROPOSAL_SHA256='baab7e0431eb5cd71c9a6425ac23be6ff1d7a3b18a58f8f087085666ace39866'
MODEL_PATH='contracts/c267_btc_residual_leadlag/c267_funding_liability_model.v3.json'
MODEL_SHA256='e333fc5b328bca928eec6e6bfaa496cad1658669d11ad164e66d98dee9433ed9'
CATALOG_PATH='contracts/c267_btc_residual_leadlag/c267_lifecycle_catalog.v2.json'
CATALOG_SHA256='c3287d28e774b6494aad828c1676ee98c0313423f1916fff940833840974751b'
PREFIX_PATH='contracts/c267_btc_residual_leadlag/c267_initial_source_prefix_clarification.v1.json'
PREFIX_SHA256='ade5921021d9cc3b740eb5b189954b5592a6b3c26f20a8008144218b910e7e58'
EXECUTION_AUTHORIZED=False
SEEDS={'long_null_seed':267202611071,'long_ci_seed':267202611072,
       'short_null_seed':267202611073,'short_ci_seed':267202611074}
INHERITED=('hypothesis','signal','execution','sizing','risk','costs','accounting','regime','paper_admission')


class C267Locked(RuntimeError):pass


def validate_metadata(proposal,model,parent):
    """Validate draft metadata consistency, never issue an economic authority."""
    if (proposal['candidate_id']!=267 or model['candidate_scope']!=267
        or proposal['lineage']['parent_candidate']!=259
        or proposal['lineage']['parent_contract_sha256']!=PARENT_SHA256
        or proposal['lineage']['new_primary_side_trials']!=2):
        raise C267Locked('C267_OWN_TWO_SIDE_LINEAGE_REQUIRED')
    if proposal['inherited_verbatim']!={k:parent[k] for k in INHERITED}:
        raise C267Locked('C267_ORIGINAL_259_SIGNAL_COST_RISK_DRIFT')
    expected=copy.deepcopy(parent['statistics']);expected.update(SEEDS)
    expected['p_value']=expected['p_value'].replace('259*2','267*2')
    gates=copy.deepcopy(parent['gates'])
    gates['forward_pass_all_required_per_side']=[s.replace('259*2','267*2') for s in gates['forward_pass_all_required_per_side']]
    if proposal['statistics']!=expected or proposal['gates']!=gates:
        raise C267Locked('C267_ORIGINAL_PER_SIDE_GATES_20K_SCIENCE_DRIFT')
    if (proposal['multiplicity']['promotable_primary_count']!=2
        or proposal['multiplicity']['side_cells']!=parent['signal']['side_cells']
        or proposal['multiplicity']['cell_alpha_ceiling']!=.05/(267*2)
        or proposal['multiplicity']['historical_global_tested_variant_total']!='UNKNOWN'
        or proposal['multiplicity']['global_FWER_claimed'] is not False):
        raise C267Locked('C267_TWO_SIDES_NON_GLOBAL_SCREEN_REQUIRED')
    if (model['known_entry_risk']['adverse_reserve_multiplier']!=1.0
        or model['stress']['funding_multiplier']!=1.0):
        raise C267Locked('C267_NO_PAIRED_RESERVE_OR_FUNDING_STRESS_TRANSPLANT')
    if (model['StageA_tail']['funding_tail_exclusive_end_offset_ms']!=15001
        or model['StageA_tail']['cash_applied_offset_ms']!=15000
        or model['StageB_boundary']['start_UTC']!='2026-09-28T01:00:00Z'
        or model['StageB_boundary']['end_exclusive_UTC']!='2026-11-05T00:00:00Z'
        or model['StageB_boundary']['hours']!=911):
        raise C267Locked('C267_FIXED_BOUNDARY_SCOPE_DRIFT')
    if (proposal['splits']['development']!=parent['splits']['development']
        or proposal['splits']['development_filter_eligible']!=parent['splits']['development_filter_eligible']
        or proposal['splits']['forward_folds']!=parent['splits']['forward_folds']
        or proposal['splits']['sealed_holdout']!='2026-09-28T01:00:00Z/2026-11-05T00:00:00Z'):
        raise C267Locked('C267_NO_WINDOW_RESCUE_OR_WARMUP_CHANGE')
    for doc in(proposal,model):
        if any(doc.get(k)is not False for k in('execution_authorized','source_authority_bound','paper_qualified')) or doc.get('default_off')is not True:
            raise C267Locked('C267_METADATA_MUST_STAY_LOCKED')
    lifecycle=model['lifecycle_tradability_authority']
    if (lifecycle['required_before_first_row']is not True or lifecycle['authorization_ready']is not False
        or lifecycle['metadata_and_pins']!={'path':CATALOG_PATH,'sha256':CATALOG_SHA256}
        or proposal['lifecycle_source_blocker']['bars_present_implies_tradable']is not False):
        raise C267Locked('C267_LIFECYCLE_AUTHORITY_UNBOUND_NOT_BARS_PRESENT_PERMISSION')


def load_metadata():
    docs=[]
    for path,digest in((PROPOSAL_PATH,PROPOSAL_SHA256),(MODEL_PATH,MODEL_SHA256),(PARENT_PATH,PARENT_SHA256)):
        raw=(ROOT/path).read_bytes()
        if hashlib.sha256(raw).hexdigest()!=digest:raise C267Locked('C267_METADATA_BYTE_DRIFT')
        docs.append(json.loads(raw))
    proposal,model,parent=docs
    if proposal['funding_model']!={'path':MODEL_PATH,'sha256':MODEL_SHA256}:
        raise C267Locked('C267_OWN_MODEL_BINDING_DRIFT')
    validate_metadata(proposal,model,parent)
    raw=(ROOT/PREFIX_PATH).read_bytes()
    if (hashlib.sha256(raw).hexdigest()!=PREFIX_SHA256
        or proposal['source_plan']['initial_prefix_clarification']!={'path':PREFIX_PATH,'sha256':PREFIX_SHA256}):
        raise C267Locked('C267_OWN_PREFIX_CLARIFICATION_DRIFT')
    load_catalog_metadata()
    return proposal,model


def load_catalog_metadata():
    raw=(ROOT/CATALOG_PATH).read_bytes()
    if hashlib.sha256(raw).hexdigest()!=CATALOG_SHA256:raise C267Locked('C267_CATALOG_BYTE_DRIFT')
    catalog=json.loads(raw);records=[]
    if catalog['candidate_scope']!=267 or catalog['coverage']!='KNOWN_OBSERVED_EVENTS_NOT_COMPLETE_WORLD':
        raise C267Locked('C267_NO_COMPLETE_WORLD_OR_OTHER_CANDIDATE_GRANT')
    for pin in catalog['records']:
        raw=(ROOT/pin['path']).read_bytes()
        if hashlib.sha256(raw).hexdigest()!=pin['sha256']:raise C267Locked('C267_DERIVED_EVENT_BYTE_DRIFT')
        event=json.loads(raw)
        if (pin['kind']!='DERIVED_CURRENT_OFFICIAL_OBSERVATION' or event['symbol']!=pin['symbol']
            or event['contract_identity']!=pin['contract_identity']
            or event['raw_http_bytes_preserved']is not False or event['raw_http_sha256'] is not None
            or event['original_asof_version_verified']is not False or event['advance_information_available_ms'] is not None
            or event['advance_horizon_veto_authorized']is not False or event['settlement_price_or_cost_proof'] is not None
            or event['execution_authorized']is not False or event['paper_qualified']is not False):
            raise C267Locked('C267_DERIVED_EVENT_CANNOT_UPGRADE_ASOF_OR_SETTLEMENT')
        records.append((pin,event))
    return tuple(records)


def assert_execution_authorized():
    raise C267Locked('C267_LIFECYCLE_CODE_SOURCE_REVIEW_AND_OWN_RECEIPT_UNBOUND')


if __name__=='__main__':assert_execution_authorized()
