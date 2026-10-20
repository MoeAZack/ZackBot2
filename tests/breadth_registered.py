"""The IMMUTABLE registered breadth_static40.v1 artifact (Codex ruling: freeze guards bind to it, not the working tree).

The registration is the annotated tag TAG (tag object TAG_OBJECT) -> Y (tree Y_TREE), whose only parent is the accepted
commit X (tree X_TREE). Every guard that pins frozen bytes reads them from Y through `registered_commit`, so a later
edit to engine.py / strategies.py on master no longer moves what the guards check, while any missing, moved,
lightweight or re-pointed tag, or any id mismatch, FAILS (AssertionError) - there is no skip path.

X's own ancestry is never read: CI fetches the tag with --depth=2 (Y and X present, X's parents absent), which must
pass. The ids are parameters so the negative tests can run the very same checks against their own throwaway repos.
"""
import atexit
import functools
import hashlib
import io
import json
import os
import shutil
import subprocess
import sys
import tarfile
import tempfile

TAG = 'zb-breadth-static40-v1-registration-20261016'
TAG_OBJECT = 'e12f493157709aef2ebedf88706ccf0f50834973'
Y = 'ec8249e31239e0f1c3f897f91946b0fe875e37d5'
Y_TREE = '7c19bafc6250173446684e7506deb09cc8421109'
X = '8cb73872ff267ad94f977a00a253d9b275fff3ff'
X_TREE = 'a354c165ec9c9408cc04d23ccb9e282d558a8621'
IDS = dict(tag_object=TAG_OBJECT, y=Y, y_tree=Y_TREE, x=X, x_tree=X_TREE)


def _git(root, *a) -> bytes:
    r = subprocess.run(['git', '-C', str(root), *a], capture_output=True)
    if r.returncode != 0:
        raise AssertionError(f'registered artifact: `git {" ".join(a)}` failed in {root}: '
                             f'{r.stderr.decode(errors="replace").strip()[:300]}')
    return r.stdout


def _one(root, *a) -> str:
    return _git(root, *a).decode().strip()


def registered_commit(root, tag=TAG, *, tag_object=TAG_OBJECT, y=Y, y_tree=Y_TREE, x=X, x_tree=X_TREE) -> str:
    """Y, after proving the annotated tag -> Y -> (only parent) X chain and both trees. AssertionError otherwise."""
    ref = f'refs/tags/{tag}'
    kind = _one(root, 'cat-file', '-t', ref)
    assert kind == 'tag', f'{ref} is a {kind!r}, not an annotated tag (lightweight / replaced tag refused)'
    got = _one(root, 'rev-parse', '--verify', ref)
    assert got == tag_object, f'{ref} is tag object {got}, expected {tag_object} (moved / re-created tag refused)'
    head = _git(root, 'cat-file', 'tag', tag_object).decode().split('\n\n', 1)[0].splitlines()
    assert head[:3] == [f'object {y}', 'type commit', f'tag {tag}'], f'tag object {tag_object} header {head[:3]}'
    peeled = _one(root, 'rev-parse', '--verify', f'{ref}^{{commit}}')
    assert peeled == y, f'{ref} peels to {peeled}, expected the registration commit Y {y}'
    assert _one(root, 'rev-parse', '--verify', f'{y}^{{tree}}') == y_tree, f'Y {y} tree is not {y_tree}'
    raw = _git(root, 'cat-file', 'commit', y).decode().split('\n\n', 1)[0].splitlines()
    parents = [ln.split()[1] for ln in raw if ln.startswith('parent ')]
    assert parents == [x], f'Y {y} parents {parents}, expected exactly [X {x}]'
    seen = _one(root, 'rev-list', '--parents', '-n', '1', y).split()
    assert seen == [y, x], f'Y {y} parent X not present in this checkout (rev-list sees {seen[1:]}; fetch depth >= 2)'
    assert _one(root, 'cat-file', '-t', x) == 'commit', f'X {x} is not a commit here'
    assert _one(root, 'rev-parse', '--verify', f'{x}^{{tree}}') == x_tree, f'X {x} tree is not {x_tree}'
    return y


def registered_blob(root, path: str, tag=TAG, **ids) -> bytes:
    """The committed (LF) bytes of `path` in the registered commit Y."""
    y = registered_commit(root, tag, **ids)
    return _git(root, 'cat-file', 'blob', f'{y}:{path}')


def registered_sha256(root, path: str, tag=TAG, **ids) -> str:
    return hashlib.sha256(registered_blob(root, path, tag, **ids).replace(b'\r\n', b'\n')).hexdigest()


def assert_pinned(root, path: str, want: str, tag=TAG, **ids) -> None:
    """The one pin comparison: SHA-256 of Y's bytes of `path` must equal `want`."""
    got = registered_sha256(root, path, tag, **ids)
    assert got == want, f'{path}: registered bytes sha256 {got} != pin {want}; re-freeze needed'


def materialize(root, dest: str, paths, tag=TAG, **ids) -> str:
    """Extract `paths` (files or directories) from Y into `dest` via `git archive` (registered bytes, no filters)."""
    y = registered_commit(root, tag, **ids)
    with tarfile.open(fileobj=io.BytesIO(_git(root, 'archive', '--format=tar', y, '--', *paths))) as t:
        t.extractall(dest, filter='data')
    return dest


def _python_sources(root, y) -> list[str]:
    """Every tracked non-test .py file of Y: the import closure of the build (backtest -> feasibility, run_vwaprev ->
    pit_run / run_r4 / splits, ...). Source only; no market data."""
    names = _git(root, 'ls-tree', '-r', '--name-only', y).decode().splitlines()
    return [n for n in names if n.endswith('.py') and not n.startswith('tests/')]


@functools.lru_cache(maxsize=None)
def registered_tree(root) -> str:
    """Y materialized once per session: the breadth code, universe, prereg, validator, ledger and the Python modules
    the build imports. Removed at interpreter exit."""
    here = os.path.join(root, 'tools', 'research')
    if here not in sys.path:
        sys.path.insert(0, here)
    import breadth_prereg as BP
    y = registered_commit(root)
    dest = tempfile.mkdtemp(prefix='zb-breadth-Y-')
    atexit.register(shutil.rmtree, dest, True)
    paths = sorted(set(BP.CODE_FILES + (BP.UNIVERSE, BP.PREREG, BP.VALIDATOR, 'tools/research/ledger.py',
                                        'research_evidence/ledger') + tuple(_python_sources(root, y))))
    return materialize(root, dest, paths)


_BUILD = r'''
import json, os, sys
tree = os.getcwd()
sys.path.insert(0, os.path.join(tree, 'tools', 'research'))
import breadth_prereg as BP
pre, _ = BP.load_prereg(tree)
built = BP.build(tree)
mods = {m: os.path.abspath(sys.modules[m].__file__) for m in
        ('breadth_prereg', 'backtest', 'strategies', 'run_breadth_candidate', 'ledger')}
sys.stdout.write(json.dumps({'build': BP.dumps(built).decode(), 'validate': BP.validate(pre, tree),
                             'frozen_config': BP._frozen_config(tree), 'hypotheses': BP._hypotheses(tree),
                             'bp_root': os.path.abspath(BP.ROOT), 'modules': mods}))
'''


@functools.lru_cache(maxsize=None)
def registered_build(root) -> dict:
    """BP.build / BP.validate run by Y's OWN validator on Y's OWN modules, in a fresh isolated interpreter
    (`python -I`, cwd = the materialized Y tree). A subprocess, not a sys.modules purge: the pytest process has
    already imported backtest / strategies / run_breadth_candidate from the working tree by bare name, and
    BP._frozen_config / _hypotheses import them by bare name, so an in-process build would silently reuse them."""
    tree = registered_tree(root)
    r = subprocess.run([sys.executable, '-I', '-c', _BUILD], cwd=tree, capture_output=True)
    if r.returncode != 0:
        raise AssertionError(f'registered build failed: {r.stderr.decode(errors="replace")[-2000:]}')
    out = json.loads(r.stdout)
    real = os.path.realpath(tree)
    for name, f in dict(out['modules'], BP_ROOT=out['bp_root']).items():
        assert os.path.realpath(f).startswith(real + os.sep) or os.path.realpath(f) == real, \
            f'{name} was loaded from {f}, not the registered tree {tree}'
    return dict(out, tree=tree)
