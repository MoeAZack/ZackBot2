"""Teeth for tests/breadth_registered.py: the SAME `registered_commit` / `assert_pinned` the freeze guards use, run
against throwaway repos with their own ids (never the real tag). Every refusal is an AssertionError - fail closed."""
import hashlib
import os
import subprocess

import pytest

import breadth_registered as RG

TAG = RG.TAG
DATE = '2026-10-16T10:00:00Z'


def g(repo, *a):
    env = dict(os.environ, GIT_AUTHOR_DATE=DATE, GIT_COMMITTER_DATE=DATE, GIT_AUTHOR_NAME='t', GIT_AUTHOR_EMAIL='t@t',
               GIT_COMMITTER_NAME='t', GIT_COMMITTER_EMAIL='t@t')
    return subprocess.run(['git', '-C', str(repo), *a], capture_output=True, check=True, env=env).stdout.decode().strip()


@pytest.fixture
def chain(tmp_path):
    """root -> X (code) -> Y (ledger only) + annotated TAG on Y; returns (repo, ids)."""
    r = tmp_path / 'r'
    r.mkdir()
    g(r, 'init', '-q')
    g(r, 'config', 'core.autocrlf', 'false')
    (r / 'engine.py').write_bytes(b'# v0\n')
    g(r, 'add', '-A'); g(r, 'commit', '-qm', 'root')
    (r / 'engine.py').write_bytes(b'# frozen\n')
    g(r, 'commit', '-qam', 'X')
    x = g(r, 'rev-parse', 'HEAD')
    (r / 'ledger.jsonl').write_bytes(b'{"family":"f"}\n')
    g(r, 'add', '-A'); g(r, 'commit', '-qm', 'Y')
    y = g(r, 'rev-parse', 'HEAD')
    g(r, 'tag', '-a', TAG, '-m', 'registration', y)
    ids = dict(tag_object=g(r, 'rev-parse', f'refs/tags/{TAG}'), y=y, y_tree=g(r, 'rev-parse', f'{y}^{{tree}}'),
               x=x, x_tree=g(r, 'rev-parse', f'{x}^{{tree}}'))
    return r, ids


def test_genuine_chain_accepted_and_blob_is_y_bytes(chain):
    r, ids = chain
    assert RG.registered_commit(r, **ids) == ids['y']
    (r / 'engine.py').write_bytes(b'# edited after registration\n')       # working-tree drift is irrelevant
    g(r, 'commit', '-qam', 'later master edit')
    assert RG.registered_blob(r, 'engine.py', **ids) == b'# frozen\n'
    RG.assert_pinned(r, 'engine.py', hashlib.sha256(b'# frozen\n').hexdigest(), **ids)


def test_depth2_shallow_clone_accepted_depth1_refused(chain, tmp_path):
    r, ids = chain
    for depth, ok in ((2, True), (1, False)):
        c = tmp_path / f'c{depth}'
        g(tmp_path, 'init', '-q', str(c))
        g(c, 'fetch', '-q', '--no-tags', f'--depth={depth}', r.as_uri(), f'+refs/tags/{TAG}:refs/tags/{TAG}')
        assert g(c, 'rev-parse', '--is-shallow-repository') == 'true'
        if ok:
            assert RG.registered_commit(c, **ids) == ids['y']             # X present, X's own parents absent: fine
        else:
            with pytest.raises(AssertionError, match='parent'):
                RG.registered_commit(c, **ids)


def test_lightweight_tag_refused(chain):
    r, ids = chain
    g(r, 'tag', '-d', TAG)
    g(r, 'tag', TAG, ids['y'])                                             # same name, same target, not annotated
    with pytest.raises(AssertionError, match='not an annotated tag'):
        RG.registered_commit(r, **ids)


def test_annotated_tag_on_another_commit_refused(chain):
    r, ids = chain
    (r / 'engine.py').write_bytes(b'# retuned\n')
    g(r, 'commit', '-qam', 'retune')
    g(r, 'tag', '-d', TAG)
    g(r, 'tag', '-a', TAG, '-m', 'registration', 'HEAD')                  # re-created on a different commit
    with pytest.raises(AssertionError, match='tag object'):
        RG.registered_commit(r, **ids)
    moved = dict(ids, tag_object=g(r, 'rev-parse', f'refs/tags/{TAG}'))   # even with the new tag object id pinned
    with pytest.raises(AssertionError, match='header|registration commit Y'):
        RG.registered_commit(r, **moved)


def test_missing_tag_refused(chain):
    r, ids = chain
    g(r, 'tag', '-d', TAG)
    with pytest.raises(AssertionError, match='failed'):
        RG.registered_commit(r, **ids)
    with pytest.raises(AssertionError):
        RG.assert_pinned(r, 'engine.py', hashlib.sha256(b'# frozen\n').hexdigest(), **ids)


def test_tampered_blob_digest_refused(chain):
    r, ids = chain
    with pytest.raises(AssertionError, match='re-freeze needed'):
        RG.assert_pinned(r, 'engine.py', hashlib.sha256(b'# frozen \n').hexdigest(), **ids)


@pytest.mark.parametrize('field', ['y_tree', 'x', 'x_tree'])
def test_wrong_tree_or_parent_refused(chain, field):
    r, ids = chain
    with pytest.raises(AssertionError):
        RG.registered_commit(r, **dict(ids, **{field: 'f' * 40}))


def test_two_parent_y_refused(chain):
    r, ids = chain
    g(r, 'checkout', '-q', '-b', 'side', f"{ids['x']}~1")
    (r / 'side.txt').write_bytes(b's\n')
    g(r, 'add', '-A'); g(r, 'commit', '-qm', 'side')
    g(r, 'checkout', '-q', ids['x'])
    g(r, 'merge', '-q', '--no-ff', '-m', 'merge', 'side')
    (r / 'ledger.jsonl').write_bytes(b'{"family":"f"}\n')
    g(r, 'add', '-A'); g(r, 'commit', '-q', '--amend', '--no-edit')
    m = g(r, 'rev-parse', 'HEAD')
    g(r, 'tag', '-d', TAG)
    g(r, 'tag', '-a', TAG, '-m', 'registration', m)
    fake = dict(ids, tag_object=g(r, 'rev-parse', f'refs/tags/{TAG}'), y=m, y_tree=g(r, 'rev-parse', f'{m}^{{tree}}'))
    with pytest.raises(AssertionError, match='expected exactly'):
        RG.registered_commit(r, **fake)
