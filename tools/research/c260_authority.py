"""Fixed C260 development authority and durable single-use boundary.

Authenticates repository metadata only. No market row, network request, exchange
or caller-supplied loader is used. An issued source capability exists only after
the independently accepted protected receipt and a durable claim both exist.
"""
from __future__ import annotations

from contextlib import contextmanager
from dataclasses import dataclass
import gzip
import hashlib
import json
import os
from pathlib import Path
import re
import subprocess
import sys

from tools.research import c260_low_idio_variance as C

ROOT = Path(__file__).resolve().parents[2]
IMPORTED_ROOT = ROOT
RECEIPT = 'contracts/c260_low_idio_variance/c260_development_authority.v1.json'
DOCUMENTS = {
    'contracts/c260_low_idio_variance/c260_registration_amendment.v1.json': 'b00f4657610f8d458c41b636afe3f4e78da0deae5760c94fd354f596f39d8a27',
    'contracts/c260_low_idio_variance/c260_prior_metadata_census.v1.json': 'cdf4c2e4b2ef23ed626abfc78ade7efdcc99f087e18b59c2f8d4fcfe5e560a34',
    'contracts/c260_low_idio_variance/c260_execution_clarification.v1.json': 'c260dc85e7e55a87eadca89c299e5c928dffdf7232341f3f81d8aa50bcc5d788',
}
_ISSUED = {}


def _sha(raw):
    return hashlib.sha256(raw).hexdigest()


def _canonical(value):
    return (json.dumps(value, sort_keys=True, separators=(',', ':'), allow_nan=False) + '\n').encode()


def _strict(raw):
    def pairs(items):
        result = {}
        for key, value in items:
            if key in result:
                raise C.C260Locked('DUPLICATE_AUTHORITY_KEY')
            result[key] = value
        return result
    try:
        value = json.loads(raw, object_pairs_hook=pairs,
                          parse_constant=lambda _: (_ for _ in ()).throw(ValueError()))
    except (ValueError, UnicodeError) as exc:
        raise C.C260Locked('INVALID_AUTHORITY_JSON') from exc
    if not isinstance(value, dict):
        raise C.C260Locked('AUTHORITY_OBJECT_REQUIRED')
    return value


def _git(*args):
    result = subprocess.run(['git', '-C', str(ROOT), *args], capture_output=True)
    if result.returncode:
        raise C.C260Locked('C260_REQUIRED_GIT_AUTHORITY_UNAVAILABLE')
    return result.stdout.decode('utf-8').strip()


def _tree(path, head='HEAD'):
    result = subprocess.run(['git', '-C', str(ROOT), 'show', f'{head}:{path}'], capture_output=True)
    if result.returncode:
        raise C.C260Locked('C260_PROTECTED_DOCUMENT_ABSENT')
    return result.stdout


@dataclass(frozen=True)
class DevelopmentAuthority:
    repository_root: Path
    store_root: Path
    implementation_head: str
    receipt_sha256: str
    # name, repository-relative path, exact byte digest, authenticated raw bytes
    artifacts: tuple[tuple[str, str, str, bytes], ...]
    start_ms: int
    end_ms: int
    claim_path: Path
    claim_sha256: str


def _authenticate_metadata():
    proposal = C.load_proposal_metadata()
    head = _git('rev-parse', 'HEAD')
    if (head != _git('rev-parse', '--verify', 'refs/remotes/origin/master^{commit}')
            or _git('status', '--porcelain')):
        raise C.C260Locked('C260_CLEAN_EXACT_PROTECTED_HEAD_REQUIRED')
    raw = _tree(RECEIPT)
    receipt = _strict(raw)
    expected = {'format', 'candidate_id', 'proposal_sha256', 'documents', 'authority',
                'accepted_implementation_head', 'research_tree_oid', 'feasibility_blob_oid',
                'store_root', 'independent_review', 'scope', 'default_off'}
    if (set(receipt) != expected or receipt['format'] != 'zb-c260-development-authority/1'
            or type(receipt['candidate_id']) is not int or receipt['candidate_id'] != 260
            or receipt['proposal_sha256'] != C.PREREG_SHA256 or receipt['documents'] != DOCUMENTS
            or receipt['scope'] != 'ONE_SHOT_DEVELOPMENT_ONLY_NO_FORWARD_OR_HOLDOUT'
            or receipt['default_off'] is not True):
        raise C.C260Locked('C260_EXACT_RESEARCH_RECEIPT_REQUIRED')
    review = receipt['independent_review']
    if (not isinstance(review, dict) or set(review) != {'reviewer', 'reference', 'verdict'}
            or not all(type(value) is str and value for value in review.values())
            or review['verdict'] != 'PASS_EXACT_CODE_SOURCE_AND_PREREAD_RULES'):
        raise C.C260Locked('C260_INDEPENDENT_ACCEPTANCE_REQUIRED')
    accepted = receipt['accepted_implementation_head']
    if type(accepted) is not str or not re.fullmatch('[0-9a-f]{40}', accepted):
        raise C.C260Locked('C260_ACCEPTED_CODE_HEAD_REQUIRED')
    # Protected squash merges do not retain feature-head ancestry. The
    # protected receipt binds the independently reviewed source object, and
    # exact code-tree equality below proves the installed implementation is
    # identical. Current execution still requires clean protected HEAD.
    if _git('rev-parse', '--verify', f'{accepted}^{{commit}}') != accepted:
        raise C.C260Locked('C260_ACCEPTED_SOURCE_MUST_BE_AN_EXACT_COMMIT')
    for path in ('tools/research/c260_authority.py', 'tools/research/c260_archive.py',
                 'tools/research/c260_runner.py'):
        _tree(path, accepted)
    if _sha(_tree(str(C.PREREG.relative_to(IMPORTED_ROOT)).replace('\\', '/'))) != C.PREREG_SHA256:
        raise C.C260Locked('C260_PROTECTED_PROPOSAL_DRIFT')
    import numpy as np
    if np.__version__ != '2.5.3':
        raise C.C260Locked('C260_FROZEN_NUMPY_VERSION_REQUIRED')
    # One tree closes all local research imports, including the source/book
    # parser and nested helpers. It excludes the receipt to avoid self-reference.
    for path, key in (('tools/research', 'research_tree_oid'), ('feasibility.py', 'feasibility_blob_oid')):
        if (_git('rev-parse', f'{accepted}:{path}') != receipt[key]
                or _git('rev-parse', f'{head}:{path}') != receipt[key]):
            raise C.C260Locked('C260_ACCEPTED_IMPLEMENTATION_DRIFT')
    entries = _git('ls-tree', '-r', head, '--', 'tools/research', 'feasibility.py').splitlines()
    for entry in entries:
        _, path = entry.split('\t', 1)
        if path.endswith('.py') and (IMPORTED_ROOT / path).read_bytes() != _tree(path):
            raise C.C260Locked('C260_IMPORTED_SOURCE_DIFFERS_FROM_ACCEPTED_TREE')
    for module in tuple(sys.modules.values()):
        location = getattr(module, '__file__', None)
        if not isinstance(location, str):
            continue
        normalized = location.replace('\\', '/')
        path = ('tools/research/' + normalized.split('/tools/research/', 1)[1]
                if '/tools/research/' in normalized and normalized.endswith('.py') else
                'feasibility.py' if normalized.endswith('/feasibility.py') else None)
        if path and Path(location).read_bytes() != _tree(path):
            raise C.C260Locked('C260_LOADED_DEPENDENCY_DIFFERS_FROM_ACCEPTED_TREE')
    for path, digest in DOCUMENTS.items():
        if _sha(_tree(path)) != digest:
            raise C.C260Locked('C260_PREREAD_DOCUMENT_DRIFT')
    census = _strict(_tree('contracts/c260_low_idio_variance/c260_prior_metadata_census.v1.json'))
    if census['global_historical_trial_count'] != 'UNKNOWN' or census['global_trial_registry_complete'] is not False:
        raise C.C260Locked('C260_HISTORICAL_TRIAL_UNCERTAINTY_MUST_REMAIN_VISIBLE')
    for entry in census['metadata_files']:
        if _git('rev-parse', f"{head}:{entry['path']}") != entry['git_blob_sha1']:
            raise C.C260Locked('C260_ACCEPTED_CENSUS_TREE_CHANGED')
    authority = proposal['stage_a_authority_proposal']
    expected_pins = {name: authority[name] for name in ('manifest', 'pit', 'filters', 'coverage')}
    if receipt['authority'] != expected_pins:
        raise C.C260Locked('C260_FIXED_V5_AUTHORITY_REQUIRED')
    artifacts, parsed = [], {}
    for name, pin in expected_pins.items():
        content = _tree(pin['path'])
        if _sha(content) != pin['byte_sha256']:
            raise C.C260Locked('C260_SOURCE_AUTHORITY_BYTE_DRIFT')
        parsed[name] = _strict(gzip.decompress(content) if pin['path'].endswith('.gz') else content)
        artifacts.append((name, pin['path'], pin['byte_sha256'], content))
    manifest, pit, coverage = (parsed[name] for name in ('manifest', 'pit', 'coverage'))
    if (pit.get('manifest_digest') != manifest.get('digest')
            or coverage.get('manifest_digest') != manifest.get('digest')
            or coverage.get('pit_universe_digest') != pit.get('digest')
            or type(coverage.get('guaranteed_period_gaps')) is not int or coverage['guaranteed_period_gaps'] != 0):
        raise C.C260Locked('C260_CROSSBOUND_COMPLETE_SOURCE_AUTHORITY_REQUIRED')
    store = Path(receipt['store_root'])
    if not store.is_absolute() or not store.is_dir():
        raise C.C260Locked('C260_EXPLICIT_EXISTING_STORE_REQUIRED')
    from datetime import datetime
    start, end = (int(datetime.fromisoformat(value.replace('Z', '+00:00')).timestamp() * 1000)
                  for value in proposal['windows_half_open_utc']['development'])
    return head, _sha(raw), store.resolve(), tuple(artifacts), start, end


def validate_issued(authority):
    if type(authority) is not DevelopmentAuthority or _ISSUED.get(id(authority)) is not authority:
        raise C.C260Locked('C260_ISSUED_DEVELOPMENT_AUTHORITY_REQUIRED')
    try:
        raw = authority.claim_path.read_bytes()
    except OSError as exc:
        raise C.C260Locked('C260_DURABLE_DEVELOPMENT_SPEND_MISSING') from exc
    if _sha(raw) != authority.claim_sha256:
        raise C.C260Locked('C260_DURABLE_DEVELOPMENT_SPEND_CHANGED')


@contextmanager
def development_authority():
    head, receipt_sha, store, artifacts, start, end = _authenticate_metadata()
    common = Path(_git('rev-parse', '--git-common-dir'))
    common = common if common.is_absolute() else ROOT / common
    claim = common.resolve() / 'c260-evaluation/candidate-260-development.claim.json'
    claim.parent.mkdir(parents=True, exist_ok=True)
    content = _canonical({'candidate_id': 260, 'stage': 'development', 'state': 'CONSUMED_BEFORE_SOURCE',
                          'head': head, 'receipt_sha256': receipt_sha})
    try:
        with claim.open('xb') as stream:
            stream.write(content)
            stream.flush()
            os.fsync(stream.fileno())
    except FileExistsError as exc:
        raise C.C260Locked('C260_DEVELOPMENT_ALREADY_SPENT_ACROSS_HEADS_AND_WORKTREES') from exc
    authority = DevelopmentAuthority(ROOT.resolve(), store, head, receipt_sha, artifacts,
                                     start, end, claim, _sha(content))
    _ISSUED[id(authority)] = authority
    try:
        yield authority
    finally:
        _ISSUED.pop(id(authority), None)  # Failure never refunds a read/spend.
