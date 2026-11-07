"""Exact accepted270 metadata; never a source, receipt or execution grant."""
import hashlib
import json
from pathlib import Path
ROOT=Path(__file__).resolve().parents[2]
DIRECTORY='contracts/c270_denominator_expiry/'
CONTRACT_PATH=DIRECTORY+'preregistration.v1.json'
CONTRACT_SHA256='ba66e41a758695b78da8bd4d322623bd12a205cded96a2963a09c4cd414c7d16'
NOMINATION_SHA256='771ccd907bd26d0d65467e907f1fc1a4df23891bc25ee676fb11efd80d6cbc21'
REVIEW='https://github.com/MoeAZack/ZackBot2/issues/219#issuecomment-6709659078'
EXECUTION_AUTHORIZED=False
class C270Locked(RuntimeError):pass

def _read(path,digest):
 raw=(ROOT/path).read_bytes()
 if hashlib.sha256(raw).hexdigest()!=digest:raise C270Locked('C270_FIXED_METADATA_BYTE_DRIFT')
 return json.loads(raw)

def load_metadata():
 contract=_read(CONTRACT_PATH,CONTRACT_SHA256)
 nomination=_read(DIRECTORY+'nomination.v1.json',NOMINATION_SHA256)
 if (contract['candidate_id']!=270 or contract['primary_side']!=1 or contract['new_primary_trials']!=1
  or contract['root_decision']!=REVIEW or contract['nomination']!={'path':DIRECTORY+'nomination.v1.json','sha256':NOMINATION_SHA256}
  or not contract['default_off'] or any(contract[k]is not False for k in('execution_authorized','source_authority_bound','paper_qualified','forward_authorized','holdout_authorized'))
  or contract['own_receipt']is not None or contract['own_claim']is not None):raise C270Locked('C270_OWN_ONE_LONG_PREREAD_SCOPE_REQUIRED')
 if contract['root_preread_clarifications']!={'null_seed': 27020261108, 'ci_seed': 27020261107, 'feature_ATR_EMA_reset': 'WINDOW_START_NO_PREVIOUS_FOLD_WARMUP', 'filter_eligible_development_start': '2024-12-14T00:00:00Z', 'actual_first_eligible_development_clock': '2024-12-14T00:00:00Z', 'conservative_opportunity_table_is_not_an_extra_filter_warmup': True}:raise C270Locked('C270_FIXED_SEED_ORDER_AND_RESET_SCOPE_REQUIRED')
 return contract,nomination

def assert_execution_authorized():raise C270Locked('C270_OWN_CODE_SOURCE_RECEIPT_UNBOUND')
