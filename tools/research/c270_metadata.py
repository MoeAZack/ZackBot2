"""C270 own fixed source/model bytes; independent receipt absent."""
import copy
import hashlib
import json
from tools.research import c270_prereg as META
from tools.research import btc_residual_leadlag_continuation_c259_stagea as P
ROOT=META.ROOT
C270Locked=META.C270Locked
DIRECTORY=META.DIRECTORY
PREREG=ROOT/META.CONTRACT_PATH
PREREG_SHA256=META.CONTRACT_SHA256
BINDING_PATH=DIRECTORY+'source_model_binding.v1.json'
BINDING_SHA256='283c36a273b8c9b8a2a7df1bf621f67b11fdb0fee1b252209f6812c01fc8fa7c'
MODEL_PATH=DIRECTORY+'funding_lifecycle_model.v1.json'
MODEL_SHA256='6d9484cad229f25af7c49ce9efff63d4f8a5f5539e086634d865c1ba062394a4'
CATALOG_PATH=DIRECTORY+'lifecycle_catalog.v1.json'
CATALOG_SHA256='aa45528541f2830829a9224b6ebdb56412c329b1a60fdf0e0acd7ed426d6750a'
PREFIX_PATH=DIRECTORY+'initial_source_prefix.v1.json'
PREFIX_SHA256='14520505199bb9731c3c87042e24d20819d7e008d9e0a7469e271f34a0cec7b7'
HOUR_MS=P.HOUR_MS
TAIL_MS=15000
SOURCE_MODEL_BOUND=True # Fixed bytes only, never an execution grant.
HOURLY_PATH='contracts/strategy_causal_pack_01/c257_259_hourly_source_correction.v2.json'
HOURLY_SHA256='648f5fdf17217297fd5737f50f2d2f2707f79d02c1211b57646478f56bb3a74c'
LIFECYCLE_SCOPE={'kind':'DERIVED_CURRENT_OFFICIAL_EFFECTIVE_EVENTS_ONLY','catalog_sha256':CATALOG_SHA256,'model_sha256':MODEL_SHA256,'complete_world_claimed':False,'advance_asof_available':False,'settlement_execution_proof_available':False}
DOCUMENTS={META.CONTRACT_PATH:PREREG_SHA256,DIRECTORY+'nomination.v1.json':META.NOMINATION_SHA256,BINDING_PATH:BINDING_SHA256,MODEL_PATH:MODEL_SHA256,CATALOG_PATH:CATALOG_SHA256,PREFIX_PATH:PREFIX_SHA256,HOURLY_PATH:HOURLY_SHA256}
DOCUMENTS['contracts/c267_btc_residual_leadlag/lifecycle/root.browser-derived.current-observation.v1.json']='d8144e94ec63b5e89d8cda7af04338d4b5106aa2bdabe6be591c537037f439e6'
DOCUMENTS['contracts/c267_btc_residual_leadlag/lifecycle/root.web-derived.bnx-current-observation.v1.json']='11d288a05f9466e9d33dc7c7612993bf11de0309a438b851b519938f88487586'

def _read(path,digest):
 raw=(ROOT/path).read_bytes()
 if hashlib.sha256(raw).hexdigest()!=digest:raise C270Locked('C270_OWN_METADATA_BYTE_DRIFT')
 return json.loads(raw)

def load_proposal_metadata():
 contract,nomination=META.load_metadata();binding=_read(BINDING_PATH,BINDING_SHA256)
 for path,digest in((MODEL_PATH,MODEL_SHA256),(CATALOG_PATH,CATALOG_SHA256),(PREFIX_PATH,PREFIX_SHA256)):_read(path,digest)
 if binding['candidate_id']!=270 or binding['preregistration']!={'path':META.CONTRACT_PATH,'sha256':PREREG_SHA256}:raise C270Locked('C270_OWN_SOURCE_BINDING_REQUIRED')
 w=nomination['windows']
 windows={k:w[k] for k in('development','development_filter_eligible')}
 windows.update(forward_folds=copy.deepcopy(w['forward']),sealed_holdout=w['new_holdout'])
 return {'splits':windows,'source_plan':{'development_only_proposed_pins':binding['source_pins']}}


def fixed_source_pins():return copy.deepcopy(load_proposal_metadata()['source_plan']['development_only_proposed_pins'])


def load_catalog_metadata():
    raw=(ROOT/CATALOG_PATH).read_bytes()
    if hashlib.sha256(raw).hexdigest()!=CATALOG_SHA256:raise C270Locked('C270_CATALOG_BYTE_DRIFT')
    catalog=json.loads(raw);records=[]
    if catalog['candidate_scope']!=270 or catalog['coverage']!='KNOWN_OBSERVED_EVENTS_NOT_COMPLETE_WORLD':
        raise C270Locked('C270_NO_COMPLETE_WORLD_OR_OTHER_CANDIDATE_GRANT')
    for pin in catalog['records']:
        raw=(ROOT/pin['path']).read_bytes()
        if hashlib.sha256(raw).hexdigest()!=pin['sha256']:raise C270Locked('C270_DERIVED_EVENT_BYTE_DRIFT')
        event=json.loads(raw)
        if (pin['kind']!='DERIVED_CURRENT_OFFICIAL_OBSERVATION' or event['symbol']!=pin['symbol']
            or event['contract_identity']!=pin['contract_identity']
            or event['raw_http_bytes_preserved']is not False or event['raw_http_sha256'] is not None
            or event['original_asof_version_verified']is not False or event['advance_information_available_ms'] is not None
            or event['advance_horizon_veto_authorized']is not False or event['settlement_price_or_cost_proof'] is not None
            or event['execution_authorized']is not False or event['paper_qualified']is not False):
            raise C270Locked('C270_DERIVED_EVENT_CANNOT_UPGRADE_ASOF_OR_SETTLEMENT')
        records.append((pin,event))
    return tuple(records)


