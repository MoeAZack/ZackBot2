"""Synthetic authority-boundary checks; no actual C260 market data is read."""
from dataclasses import replace
import hashlib
import json
import subprocess
import sys

import pytest

from tools.research import c260_authority as A


@pytest.fixture
def issued_metadata(tmp_path, monkeypatch):
    store = tmp_path / 'synthetic-store'
    store.mkdir()
    monkeypatch.setattr(A, 'ROOT', tmp_path)
    monkeypatch.setattr(A, '_git', lambda *args: '.git' if args == ('rev-parse', '--git-common-dir') else pytest.fail(args))
    monkeypatch.setattr(A, '_authenticate_metadata', lambda: ('a' * 40, 'b' * 64, store, (), 1, 2))
    return tmp_path


def test_durable_claim_precedes_capability_and_a_failure_never_refunds(issued_metadata):
    with pytest.raises(RuntimeError, match='synthetic failure'):
        with A.development_authority() as authority:
            A.validate_issued(authority)
            assert json.loads(authority.claim_path.read_bytes())['state'] == 'CONSUMED_BEFORE_SOURCE'
            assert hashlib.sha256(authority.claim_path.read_bytes()).hexdigest() == authority.claim_sha256
            raise RuntimeError('synthetic failure')
    with pytest.raises(A.C.C260Locked, match='ISSUED'):
        A.validate_issued(authority)
    with pytest.raises(A.C.C260Locked, match='ALREADY_SPENT'):
        with A.development_authority():
            pytest.fail('spent candidate reopened')


def test_completed_read_does_not_refund_and_copy_cannot_authorize_source(issued_metadata):
    with A.development_authority() as authority:
        with pytest.raises(A.C.C260Locked, match='ISSUED'):
            A.validate_issued(replace(authority))
        A.validate_issued(authority)
    with pytest.raises(A.C.C260Locked, match='ALREADY_SPENT'):
        with A.development_authority():
            pytest.fail('completed candidate reopened')


@pytest.mark.parametrize('attack', ['corrupt', 'missing'])
def test_claim_tampering_revokes_even_the_actual_issued_object(issued_metadata, attack):
    with A.development_authority() as authority:
        if attack == 'corrupt':
            authority.claim_path.write_bytes(b'changed')
        else:
            authority.claim_path.unlink()
        with pytest.raises(A.C.C260Locked, match='SPEND'):
            A.validate_issued(authority)


@pytest.mark.parametrize('value', [True, False, None, {}, 'development'])
def test_caller_flags_and_docs_are_not_issued_authority(value):
    with pytest.raises(A.C.C260Locked, match='ISSUED'):
        A.validate_issued(value)


def test_authentication_failure_precedes_claim_directory_creation(tmp_path, monkeypatch):
    def refused():
        raise A.C.C260Locked('missing protected receipt')
    monkeypatch.setattr(A, '_authenticate_metadata', refused)
    monkeypatch.setattr(A, '_git', lambda *args: pytest.fail('claim path accessed'))
    with pytest.raises(A.C.C260Locked, match='missing protected receipt'):
        with A.development_authority():
            pytest.fail('unauthenticated source opened')
    assert list(tmp_path.iterdir()) == []


def test_missing_protected_receipt_reads_no_price_file_or_identity(monkeypatch):
    monkeypatch.setattr(A, '_git', lambda *args: '' if args == ('status', '--porcelain') else 'a' * 40)
    monkeypatch.setattr(A, '_tree', lambda path, head='HEAD': (_ for _ in ()).throw(A.C.C260Locked('PROTECTED_DOCUMENT_ABSENT')))
    with pytest.raises(A.C.C260Locked, match='PROTECTED_DOCUMENT_ABSENT'):
        A._authenticate_metadata()


@pytest.mark.parametrize('dirty,head', [(True, 'a' * 40), (False, 'c' * 40)])
def test_unprotected_or_dirty_head_refuses_before_authority_document(dirty, head, monkeypatch):
    def git(*args):
        if args == ('rev-parse', 'HEAD'): return head
        if args == ('status', '--porcelain'): return ' M code.py' if dirty else ''
        return 'a' * 40
    monkeypatch.setattr(A, '_git', git)
    monkeypatch.setattr(A, '_tree', lambda *args: pytest.fail('unprotected document read'))
    with pytest.raises(A.C.C260Locked, match='CLEAN_EXACT_PROTECTED'):
        A._authenticate_metadata()


def test_authority_parser_rejects_ambiguous_and_nonfinite_json():
    for raw in (b'{"candidate_id":260,"candidate_id":261}', b'{"number":NaN}', b'[]'):
        with pytest.raises(A.C.C260Locked):
            A._strict(raw)


ORIGINAL_ROOT = A.ROOT                                                 # the checkout under test (before any monkeypatch)
C260_FROZEN_TREE = '42ef787f6b6e8c9363705b76df13cdb487f840f0'   # immutable master the v1 census/receipt rules were proved on


def _code_differs_from_frozen():
    """The C260 authority binds tools/research and feasibility.py to the frozen tree: a checkout whose code changed (the
    accepted LANE3 F6 feasibility.round_step) can never pass it in-process - by design."""
    r = subprocess.run(['git', '-C', str(ORIGINAL_ROOT), 'diff', '--quiet', C260_FROZEN_TREE, '--', 'tools/research', 'feasibility.py'],
                       capture_output=True)
    assert r.returncode in (0, 1), r.stderr.decode(errors='replace')   # 128: the frozen commit is missing - fail, never skip
    return r.returncode == 1


@pytest.fixture(scope='session')
def frozen_checkout(tmp_path_factory):
    """A full checkout of the exact frozen tree (no change to this repository's refs or worktrees)."""
    root = tmp_path_factory.mktemp('c260-frozen') / 'repo'
    subprocess.run(['git', 'clone', '-q', '--shared', '--no-checkout', str(ORIGINAL_ROOT), str(root)], check=True, capture_output=True)
    subprocess.run(['git', '-C', str(root), 'checkout', '-q', '--detach', C260_FROZEN_TREE], check=True, capture_output=True)
    return root


def _run_on_frozen_tree(frozen, nodeid):
    """Positive / attack coverage of the protected path, run by the frozen tree's own test on the frozen tree's own code."""
    out = subprocess.run([sys.executable, '-m', 'pytest', '-q', '-p', 'no:cacheprovider', nodeid], cwd=str(frozen),
                         capture_output=True, timeout=600)
    text = out.stdout.decode('utf-8', errors='replace')
    assert out.returncode == 0 and ' passed' in text and 'skipped' not in text, text[-3000:]


@pytest.fixture
def protected_metadata_repo(tmp_path, monkeypatch, request):
    """Real isolated Git/ref/receipt path; only existing authority metadata.

    The archive store is empty. No market file, economic packet or original
    repository identity directory is accessed by this fixture.
    """
    if _code_differs_from_frozen() and not request.node.get_closest_marker('integration_tree'):
        _run_on_frozen_tree(request.getfixturevalue('frozen_checkout'), request.node.nodeid)
        return None                                                   # covered on the exact frozen tree
    original = A.ROOT
    root, store = tmp_path / 'repo', tmp_path / 'empty-store'
    store.mkdir()
    def git(*args):
        result = subprocess.run(['git', '-C', str(root), *args], capture_output=True, check=True)
        return result.stdout.decode().strip()
    subprocess.run(['git', 'clone', '--shared', '--sparse', str(original), str(root)], check=True, capture_output=True)
    git('sparse-checkout', 'set', '--no-cone', '/contracts/c260_low_idio_variance/')
    # LANE3 F5+F6 integration (Codex #363 c6928954473): positive C260 coverage runs on the EXACT frozen tree the census was
    # issued for (immutable master), never on a later tree whose census metadata files legitimately changed
    git('checkout', '-q', '--detach', C260_FROZEN_TREE)
    git('config', 'user.name', 'Synthetic C260 authority test')
    git('config', 'user.email', 'synthetic@example.invalid')
    accepted = git('rev-parse', 'HEAD')
    proposal = A.C.load_proposal_metadata()
    receipt = {'format': 'zb-c260-development-authority/1', 'candidate_id': 260,
        'proposal_sha256': A.C.PREREG_SHA256, 'documents': A.DOCUMENTS,
        'authority': {name: proposal['stage_a_authority_proposal'][name]
                      for name in ('manifest', 'pit', 'filters', 'coverage')},
        'accepted_implementation_head': accepted,
        'research_tree_oid': git('rev-parse', f'{accepted}:tools/research'),
        'feasibility_blob_oid': git('rev-parse', f'{accepted}:feasibility.py'),
        'store_root': str(store), 'independent_review': {'reviewer': 'SYNTHETIC_ONLY',
            'reference': 'synthetic metadata fixture; no real run or acceptance',
            'verdict': 'PASS_EXACT_CODE_SOURCE_AND_PREREAD_RULES'},
        'scope': 'ONE_SHOT_DEVELOPMENT_ONLY_NO_FORWARD_OR_HOLDOUT', 'default_off': True}
    path = root / A.RECEIPT
    path.write_bytes(A._canonical(receipt))
    git('add', A.RECEIPT)
    git('commit', '-qm', 'Synthetic metadata-only authorization')
    git('update-ref', 'refs/remotes/origin/master', git('rev-parse', 'HEAD'))
    monkeypatch.setattr(A, 'ROOT', root)
    return root, store, receipt, git


def test_actual_protected_metadata_authentication_then_one_spend_without_price_reads(protected_metadata_repo):
    if protected_metadata_repo is None: return                      # ran on the exact frozen tree (fixture)
    root, store, _, _ = protected_metadata_repo
    with A.development_authority() as authority:
        A.validate_issued(authority)
        assert authority.repository_root == root.resolve() and authority.store_root == store.resolve()
        assert {item[0] for item in authority.artifacts} == {'manifest', 'pit', 'filters', 'coverage'}
        assert all(hashlib.sha256(item[3]).hexdigest() == item[2] for item in authority.artifacts)
        assert authority.end_ms < 1790553600000
        assert list(store.iterdir()) == []
    with pytest.raises(A.C.C260Locked, match='ALREADY_SPENT'):
        with A.development_authority():
            pytest.fail('same real Git capability consumed twice')


@pytest.mark.parametrize('attack', ['scope', 'code', 'source', 'review'])
def test_actual_protected_bad_receipts_fail_before_claim(protected_metadata_repo, attack):
    if protected_metadata_repo is None: return                      # ran on the exact frozen tree (fixture)
    root, _, receipt, git = protected_metadata_repo
    if attack == 'scope': receipt['scope'] = 'FORWARD_AND_HOLDOUT'
    if attack == 'code': receipt['research_tree_oid'] = '0' * 40
    if attack == 'source': receipt['authority']['manifest']['byte_sha256'] = '0' * 64
    if attack == 'review': receipt['independent_review']['verdict'] = 'SELF_ACCEPTED'
    (root / A.RECEIPT).write_bytes(A._canonical(receipt))
    git('add', A.RECEIPT)
    git('commit', '-qm', 'Synthetic invalid authorization')
    git('update-ref', 'refs/remotes/origin/master', git('rev-parse', 'HEAD'))
    with pytest.raises(A.C.C260Locked):
        with A.development_authority():
            pytest.fail('invalid authority opened source')
    assert not (root / '.git/c260-evaluation').exists()


def test_squash_equivalent_reviewed_code_tree_does_not_require_feature_ancestry(protected_metadata_repo):
    if protected_metadata_repo is None: return                      # ran on the exact frozen tree (fixture)
    root, _, receipt, git = protected_metadata_repo
    # A genuinely disjoint reviewed commit models the feature source object
    # after a protected squash. It has exactly the accepted code, no parent.
    reviewed = subprocess.run(['git', '-C', str(root), 'commit-tree', git('rev-parse', 'HEAD^{tree}')],
                              input=b'Synthetic independent reviewed source\n', capture_output=True, check=True).stdout.decode().strip()
    lineage = subprocess.run(['git', '-C', str(root), 'merge-base', '--is-ancestor', reviewed, 'HEAD'], capture_output=True)
    assert lineage.returncode == 1
    receipt['accepted_implementation_head'] = reviewed
    (root / A.RECEIPT).write_bytes(A._canonical(receipt))
    git('add', A.RECEIPT)
    git('commit', '-qm', 'Synthetic protected exact-code acceptance after squash')
    git('update-ref', 'refs/remotes/origin/master', git('rev-parse', 'HEAD'))
    with A.development_authority() as authority:
        A.validate_issued(authority)
        assert authority.implementation_head == git('rev-parse', 'HEAD')


def test_annotated_tag_object_cannot_substitute_for_an_exact_reviewed_commit(protected_metadata_repo):
    if protected_metadata_repo is None: return                      # ran on the exact frozen tree (fixture)
    root, _, receipt, git = protected_metadata_repo
    git('tag', '-a', 'synthetic-reviewed-tag', '-m', 'not a commit identity')
    receipt['accepted_implementation_head'] = git('rev-parse', 'refs/tags/synthetic-reviewed-tag')
    (root / A.RECEIPT).write_bytes(A._canonical(receipt))
    git('add', A.RECEIPT)
    git('commit', '-qm', 'Synthetic tag identity attack')
    git('update-ref', 'refs/remotes/origin/master', git('rev-parse', 'HEAD'))
    with pytest.raises(A.C.C260Locked, match='EXACT_COMMIT'):
        with A.development_authority():
            pytest.fail('tag object accepted as reviewed commit')
    assert not (root / '.git/c260-evaluation').exists()


@pytest.mark.integration_tree
def test_old_c260_authority_rejects_the_changed_integration_tree(protected_metadata_repo):
    """LANE3 F5+F6 integration: the frozen v1 census never authorizes a tree whose census metadata changed (the F01
    cohort manifest carries the accepted F5/F6 pins) - the old authority fails closed on the changed code/manifest."""
    root, _, _, git = protected_metadata_repo
    rel = 'contracts/factory_f01_impl_01/cohort_manifest.v1.json'
    census = A._strict(A._tree('contracts/c260_low_idio_variance/c260_prior_metadata_census.v1.json'))
    assert rel in {e['path'] for e in census['metadata_files']}
    src = subprocess.run(['git', '-C', str(ORIGINAL_ROOT), 'show', f'HEAD:{rel}'], capture_output=True, check=True).stdout
    frozen = subprocess.run(['git', '-C', str(root), 'show', f'{C260_FROZEN_TREE}:{rel}'], capture_output=True, check=True).stdout
    if src == frozen:                                                    # this checkout is the frozen tree itself: change it
        src = frozen.replace(b'"pins_expected": {', b'"pins_expected": {\n  "_synthetic_change": "x",', 1)
    assert src != frozen
    blob = subprocess.run(['git', '-C', str(root), 'hash-object', '-w', '--stdin'], input=src, capture_output=True,
                          check=True).stdout.decode().strip()
    git('update-index', '--add', '--cacheinfo', f'100644,{blob},{rel}')
    git('update-index', '--skip-worktree', rel)                         # outside the sparse checkout: the tree stays clean
    git('commit', '-qm', 'Synthetic changed census metadata (integration tree manifest)')
    git('update-ref', 'refs/remotes/origin/master', git('rev-parse', 'HEAD'))
    want = ('C260_IMPORTED_SOURCE_DIFFERS_FROM_ACCEPTED_TREE' if _code_differs_from_frozen()   # changed code: refused first
            else 'C260_ACCEPTED_CENSUS_TREE_CHANGED')                                           # same code: the census refuses
    with pytest.raises(A.C.C260Locked, match=want):
        with A.development_authority():
            pytest.fail('old C260 authority accepted a changed census tree')
