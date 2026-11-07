"""Exact accepted269 metadata; never a source, receipt or execution grant."""
import hashlib
import json
from pathlib import Path
ROOT=Path(__file__).resolve().parents[2]
DIRECTORY='contracts/c269_overhead_notional/'
CONTRACT_PATH=DIRECTORY+'preregistration.v1.json'
CONTRACT_SHA256='d291916387ab2a7c5936de62debaa6eb491f1cf30ba16261a1aac06f7e55ea7d'
NOMINATION_SHA256='6d5dfc1890ccfd499cd5dbeb2b524cec92f358481ee4b828b0192c037a7f6a81'
REVIEW='https://github.com/MoeAZack/ZackBot2/issues/219#issuecomment-6709305742'
EXECUTION_AUTHORIZED=False
class C269Locked(RuntimeError):pass

def _read(path,digest):
 raw=(ROOT/path).read_bytes()
 if hashlib.sha256(raw).hexdigest()!=digest:raise C269Locked('C269_FIXED_METADATA_BYTE_DRIFT')
 return json.loads(raw)

def load_metadata():
 contract=_read(CONTRACT_PATH,CONTRACT_SHA256)
 nomination=_read(DIRECTORY+'nomination.v1.json',NOMINATION_SHA256)
 if (contract['candidate_id']!=269 or contract['primary_side']!=-1 or contract['new_primary_trials']!=1
  or contract['root_decision']!=REVIEW or contract['nomination']!={'path':DIRECTORY+'nomination.v1.json','sha256':NOMINATION_SHA256}
  or not contract['default_off'] or any(contract[k]is not False for k in('execution_authorized','source_authority_bound','paper_qualified','forward_authorized','holdout_authorized'))
  or contract['own_receipt']is not None or contract['own_claim']is not None):raise C269Locked('C269_OWN_ONE_SHORT_PREREAD_SCOPE_REQUIRED')
 return contract,nomination

def assert_execution_authorized():raise C269Locked('C269_OWN_CODE_SOURCE_RECEIPT_UNBOUND')
