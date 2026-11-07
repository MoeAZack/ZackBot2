"""Own C267 offline-feature receipt and durable one-shot development scope.

This does not grant PAPER/merge authority or relax C260's protected gate. No
receipt is supplied by this implementation. Missing scope fails before prices
or any evaluation identity. No C260 authority/claim is consumed or accepted.
"""
from contextlib import contextmanager
from dataclasses import dataclass
import hashlib
import json
import os
from pathlib import Path
import re
import subprocess
import sys

from tools.research import c267_metadata as C

ROOT = Path(__file__).resolve().parents[2]
IMPORTED_ROOT = ROOT
RECEIPT = 'contracts/c267_btc_residual_leadlag/c267_development_authority.v1.json'
SCOPE = 'C267_ONE_SHOT_DEVELOPMENT_ONLY_NO_FORWARD_HOLDOUT_OR_PAPER'
POLICY = 'INDEPENDENTLY_REVIEWED_EXACT_COMMITTED_FEATURE_OFFLINE_ONLY'
DOCUMENTS = dict(C.DOCUMENTS)
_ISSUED = {}


def sha(raw):
    return hashlib.sha256(raw).hexdigest()


def canonical(value):
    return (json.dumps(value, sort_keys=True, separators=(',', ':'), allow_nan=False) + '\n').encode()


def _strict(raw):
    def pairs(rows):
        result = {}
        for key, value in rows:
            if key in result: raise C.C267Locked('C267_DUPLICATE_RECEIPT_KEY')
            result[key] = value
        return result
    try:
        doc = json.loads(raw, object_pairs_hook=pairs,
                         parse_constant=lambda _: (_ for _ in ()).throw(ValueError()))
    except (ValueError, UnicodeError) as exc:
        raise C.C267Locked('C267_INVALID_RECEIPT_JSON') from exc
    if not isinstance(doc, dict): raise C.C267Locked('C267_RECEIPT_OBJECT_REQUIRED')
    return doc


def _git(*args):
    p = subprocess.run(['git', '-C', str(ROOT), *args], capture_output=True)
    if p.returncode: raise C.C267Locked('C267_REQUIRED_GIT_METADATA_UNAVAILABLE')
    return p.stdout.decode().strip()


def _tree(path, head='HEAD'):
    p = subprocess.run(['git', '-C', str(ROOT), 'show', f'{head}:{path}'], capture_output=True)
    if p.returncode: raise C.C267Locked('C267_REVIEWED_SCOPE_ABSENT')
    return p.stdout


@dataclass(frozen=True)
class DevelopmentAuthority:
    repository_root: Path
    store_root: Path
    side: int
    implementation_head: str
    receipt_sha256: str
    artifacts: tuple[tuple[str, str, str, bytes], ...]
    start_ms: int
    end_ms: int
    signal_end_ms: int
    funding_tail_exclusive_end_ms: int
    claim_path: Path
    claim_sha256: str


def _authenticate_metadata(side):
    if type(side)is not int or side not in(-1,1):raise C.C267Locked('C267_EXACT_SIDE_REQUIRED')
    if not C.SOURCE_MODEL_BOUND: raise C.C267Locked('C267_PUBLISHED_LEDGER_TAIL_MODEL_UNBOUND')
    proposal = C.load_proposal_metadata()
    head = _git('rev-parse', 'HEAD')
    if not re.fullmatch('[0-9a-f]{40}', head) or _git('status', '--porcelain'):
        raise C.C267Locked('C267_CLEAN_EXACT_COMMITTED_FEATURE_REQUIRED')
    raw = _tree(RECEIPT)
    receipt = _strict(raw)
    keys = {'format', 'candidate_id', 'proposal_sha256', 'scope', 'head_policy', 'default_off',
            'accepted_implementation_head', 'research_tree_oid', 'feasibility_blob_oid',
            'independent_review', 'authority', 'store_root', 'registration', 'documents', 'side_cells', 'lifecycle_model_scope'}
    if (set(receipt) != keys or receipt['format'] != 'zb-c267-offline-development-authority/1'
            or type(receipt['candidate_id']) is not int or receipt['candidate_id'] != 267
            or receipt['proposal_sha256'] != C.PREREG_SHA256 or receipt['scope'] != SCOPE
            or receipt['head_policy'] != POLICY or receipt['default_off'] is not True
            or receipt['documents'] != DOCUMENTS):
        raise C.C267Locked('C267_EXACT_OWN_RESEARCH_RECEIPT_REQUIRED')
    if (not isinstance(receipt['side_cells'],list) or not receipt['side_cells']
        or any(type(v)is not int or v not in(-1,1) for v in receipt['side_cells'])
        or len(receipt['side_cells'])!=len(set(receipt['side_cells'])) or side not in receipt['side_cells']
        or receipt['lifecycle_model_scope']!=C.LIFECYCLE_SCOPE):
        raise C.C267Locked('C267_OWN_SIDE_AND_LIMITED_LIFECYCLE_SCOPE_REQUIRED')
    accepted = receipt['accepted_implementation_head']
    if (type(accepted) is not str or not re.fullmatch('[0-9a-f]{40}', accepted)
            or _git('rev-parse', '--verify', f'{accepted}^{{commit}}') != accepted):
        raise C.C267Locked('C267_EXACT_REVIEWED_IMPLEMENTATION_COMMIT_REQUIRED')
    review = receipt['independent_review']
    if (not isinstance(review, dict) or set(review) != {'reviewer', 'reference', 'reviewed_head', 'verdict'}
            or not all(type(v) is str and v for v in review.values()) or review['reviewed_head'] != accepted
            or review['verdict'] != 'PASS_EXACT_C267_CODE_SOURCE_COST_AND_PREREAD_RULES'):
        raise C.C267Locked('C267_INDEPENDENT_EXACT_SCOPE_REVIEW_REQUIRED')
    if receipt['registration'] != {'candidate_id': 267, 'new_primary_trials': 2,
            'proposal_sha256': C.PREREG_SHA256, 'historical_global_variant_total': 'UNKNOWN',
            'numbered_family_screen_only': True, 'global_FWER_claimed': False}:
        raise C.C267Locked('C267_EXACT_OWN_REGISTRATION_REQUIRED')
    for path, key in (('tools/research', 'research_tree_oid'), ('feasibility.py', 'feasibility_blob_oid')):
        if (_git('rev-parse', f'{accepted}:{path}') != receipt[key]
                or _git('rev-parse', f'{head}:{path}') != receipt[key]):
            raise C.C267Locked('C267_REVIEWED_CODE_CLOSURE_DRIFT')
    if sha(_tree(C.PREREG.relative_to(IMPORTED_ROOT).as_posix())) != C.PREREG_SHA256:
        raise C.C267Locked('C267_COMMITTED_PROPOSAL_DRIFT')
    for path, digest in DOCUMENTS.items():
        if sha(_tree(path)) != digest: raise C.C267Locked('C267_PREREAD_CLARIFICATION_DRIFT')
    for line in _git('ls-tree', '-r', head, '--', 'tools/research', 'feasibility.py').splitlines():
        _, path = line.split('\t', 1)
        if path.endswith('.py') and (IMPORTED_ROOT / path).read_bytes() != _tree(path):
            raise C.C267Locked('C267_IMPORTED_CODE_DIFFERS_FROM_REVIEWED_TREE')
    for module in tuple(sys.modules.values()):
        location = getattr(module, '__file__', None)
        if not isinstance(location, str): continue
        normalized = location.replace('\\', '/')
        path = ('tools/research/' + normalized.split('/tools/research/', 1)[1]
                if '/tools/research/' in normalized and normalized.endswith('.py') else
                'feasibility.py' if normalized.endswith('/feasibility.py') else None)
        if path and Path(location).read_bytes() != _tree(path):
            raise C.C267Locked('C267_LOADED_DEPENDENCY_DRIFT')
    import numpy as np
    if np.__version__ != '2.5.3': raise C.C267Locked('C267_FIXED_SCIENCE_VERSION_REQUIRED')
    authority = C.fixed_source_pins()
    pins = {name: authority[name] for name in ('manifest', 'pit', 'filters', 'coverage')}
    if receipt['authority'] != pins: raise C.C267Locked('C267_FIXED_V5_SOURCE_PINS_REQUIRED')
    artifacts = tuple((name, pin['path'], pin['byte_sha256'], _tree(pin['path'])) for name, pin in pins.items())
    if any(sha(content) != digest for _, _, digest, content in artifacts):
        raise C.C267Locked('C267_SOURCE_METADATA_BYTE_DRIFT')
    store = Path(receipt['store_root'])
    if not store.is_absolute() or not store.is_dir(): raise C.C267Locked('C267_EXPLICIT_SOURCE_STORE_REQUIRED')
    from tools.research.c267_source import prepare_metadata
    view = prepare_metadata(ROOT.resolve(), store.resolve(), artifacts)
    view.preflight_grid()  # metadata/stat only, before any identity or raw byte
    return head, sha(raw), store.resolve(), artifacts, view.start_ms, view.end_ms


def validate_issued(cap):
    if type(cap) is not DevelopmentAuthority or _ISSUED.get(id(cap)) is not cap:
        raise C.C267Locked('C267_OWN_ISSUED_DEVELOPMENT_SCOPE_REQUIRED')
    try: content = cap.claim_path.read_bytes()
    except OSError as exc: raise C.C267Locked('C267_DURABLE_SPEND_MISSING') from exc
    if sha(content) != cap.claim_sha256: raise C.C267Locked('C267_DURABLE_SPEND_CHANGED')


@contextmanager
def development_authority(side):
    head, digest, store, artifacts, start, end = _authenticate_metadata(side)
    common = Path(_git('rev-parse', '--git-common-dir'))
    common = common if common.is_absolute() else ROOT / common
    claim = common.resolve() / f'c267-evaluation/candidate-267-{side}-development.claim.json'
    claim.parent.mkdir(parents=True, exist_ok=True)
    content = canonical({'candidate_id': 267, 'side':side, 'stage': 'development', 'state': 'CONSUMED_BEFORE_SOURCE',
                         'head': head, 'receipt_sha256': digest, 'scope': SCOPE})
    try:
        with claim.open('xb') as handle:
            handle.write(content); handle.flush(); os.fsync(handle.fileno())
    except FileExistsError as exc:
        raise C.C267Locked('C267_ALREADY_SPENT_ACROSS_HEADS_AND_WORKTREES') from exc
    cap = DevelopmentAuthority(ROOT.resolve(), store, side, head, digest, artifacts, start, end, end,
                               end+C.TAIL_MS+1, claim, sha(content))
    _ISSUED[id(cap)] = cap
    try: yield cap
    finally: _ISSUED.pop(id(cap), None)
