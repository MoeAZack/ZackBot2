"""Byte-bound C268 preregistration metadata. Never a source/execution grant."""
import hashlib
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
DIRECTORY = 'contracts/c268_downside_beta_premium/'
CONTRACT_PATH = DIRECTORY + 'preregistration.v1.json'
CONTRACT_SHA256 = 'beb9d11a0ef9f0b676fad5af3c1cf78566cd4baf7d6c06b083d7962f06ca5f15'
NOMINATION_SHA256 = '749bb0dd1b7c1b438e38ce3cb158947fc6b868decf34da9aec31914e87e7f080'
DRAFT_SHA256 = 'a76ee5f52b3a697be64ae08b0f117eaf926eb7abac7ce356c6051d3cfeb97dd1'
REVIEW = 'https://github.com/MoeAZack/ZackBot2/issues/219#issuecomment-6708026573'
EXECUTION_AUTHORIZED = False


class C268Locked(RuntimeError):
    pass


def _read(path, digest):
    raw = (ROOT / path).read_bytes()
    if hashlib.sha256(raw).hexdigest() != digest:
        raise C268Locked('C268_IMMUTABLE_METADATA_BYTE_DRIFT')
    return json.loads(raw)


def load_metadata():
    contract = _read(CONTRACT_PATH, CONTRACT_SHA256)
    nomination = _read(DIRECTORY + 'nomination.v1.json', NOMINATION_SHA256)
    draft = _read(DIRECTORY + 'prereg_review_draft.v1.json', DRAFT_SHA256)
    expected = {
        'nomination.v1.json': {'path': DIRECTORY + 'nomination.v1.json', 'sha256': NOMINATION_SHA256},
        'prereg_review_draft.v1.json': {'path': DIRECTORY + 'prereg_review_draft.v1.json', 'sha256': DRAFT_SHA256},
    }
    if (contract['candidate_id'] != 268 or contract['accepted_documents'] != expected
            or contract['specification'] != nomination
            or draft['preregistered_specification'] != nomination
            or contract['root_review']['reference'] != REVIEW
            or contract['mechanism_risk'] != draft['mechanism_risk']
            or contract['statistics_clarification'] != draft['fixed_science_clarification']):
        raise C268Locked('C268_ACCEPTED_FIXED_SPECIFICATION_DRIFT')
    for key in ('execution_authorized', 'source_authority_bound', 'paper_qualified',
                'forward_authorized', 'holdout_authorized'):
        if contract[key] is not False:
            raise C268Locked('C268_METADATA_IS_NOT_AN_EXECUTION_GRANT')
    if (contract['default_off'] is not True or contract['evaluation_locked'] is not True
            or any(contract[key] is not None for key in ('own_source_pins', 'own_lifecycle_catalog',
                'independent_executable_receipt', 'own_identity_claim'))
            or contract['pre_data_clarifications']['minimum_fit_count'] != 16
            or contract['pre_data_clarifications']['minimum_fit_count_scope']
                != 'STATISTICALLY_DEFINED_BEFORE_POSITIVE_ELIGIBILITY'):
        raise C268Locked('C268_PREREAD_SCOPE_MUST_REMAIN_LOCKED')
    return contract


def assert_execution_authorized():
    raise C268Locked('C268_PURE_KERNEL_ONLY_OWN_SOURCE_CODE_RECEIPT_UNBOUND')
