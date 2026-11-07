"""C267 fixed development metadata seam, never a source/economic grant."""
from tools.research import c267_prereg as META
from tools.research import btc_residual_leadlag_continuation_c259_stagea as P

ROOT=META.ROOT
PREREG=ROOT/META.PROPOSAL_PATH
PREREG_SHA256=META.PROPOSAL_SHA256
C267Locked=META.C267Locked
HOUR_MS=P.HOUR_MS
TAIL_MS=15000
SOURCE_MODEL_BOUND=True  # Exact reviewed metadata only, receipt remains absent.
MODEL_PATH=META.MODEL_PATH
MODEL_SHA256=META.MODEL_SHA256
HOURLY_PATH='contracts/strategy_causal_pack_01/c257_259_hourly_source_correction.v2.json'
HOURLY_SHA256='648f5fdf17217297fd5737f50f2d2f2707f79d02c1211b57646478f56bb3a74c'
LIFECYCLE_SCOPE={'kind':'DERIVED_CURRENT_OFFICIAL_EFFECTIVE_EVENTS_ONLY',
    'catalog_sha256':META.CATALOG_SHA256,'model_sha256':MODEL_SHA256,
    'complete_world_claimed':False,'advance_asof_available':False,'settlement_execution_proof_available':False}
DOCUMENTS={META.PROPOSAL_PATH:META.PROPOSAL_SHA256,META.MODEL_PATH:META.MODEL_SHA256,
    META.PARENT_PATH:META.PARENT_SHA256,META.CATALOG_PATH:META.CATALOG_SHA256,HOURLY_PATH:HOURLY_SHA256,
    META.PREFIX_PATH:META.PREFIX_SHA256}
# The exact single derived notice is metadata, not a raw HTTP/market payload.
DOCUMENTS['contracts/c267_btc_residual_leadlag/lifecycle/root.browser-derived.current-observation.v1.json']='d8144e94ec63b5e89d8cda7af04338d4b5106aa2bdabe6be591c537037f439e6'
DOCUMENTS['contracts/c267_btc_residual_leadlag/lifecycle/root.web-derived.bnx-current-observation.v1.json']='11d288a05f9466e9d33dc7c7612993bf11de0309a438b851b519938f88487586'


def load_proposal_metadata():return META.load_metadata()[0]


def fixed_source_pins():
    import copy
    return copy.deepcopy(load_proposal_metadata()['source_plan']['development_only_proposed_pins'])
