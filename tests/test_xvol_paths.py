"""WINPATH-01 (#345): research paths across Windows drives (checkout on F:, pytest temp root on C:).

Different drives are never containment: the reserved ledger / verdict / registry denials and the authorization
checks still run and still refuse; an authorized write to another drive is allowed; an external rules file on another
drive gets an explicit absolute identity while same-volume identity bytes stay exactly as before.
Drive semantics are exercised two ways: ntpath directly (any OS) and tests/_xvol_drive_sim.two_drives (the real call
sites with os.path behaving like a C:/F: split).
"""
import hashlib
import json
import ntpath
import os
import posixpath
import sys
import tempfile
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'tools' / 'research'))
sys.path.insert(0, str(ROOT / 'tests'))

import xvol_paths as XP                                                                     # noqa: E402
from _xvol_drive_sim import two_drives                                                      # noqa: E402


# ----------------------------------------------------------------------------------------------- helper (ntpath)

LEDGER = r'F:\Dev\ZackBot2\research_evidence\ledger'


@pytest.mark.parametrize('path, inside', [
    (LEDGER, True),
    (LEDGER + r'\x.json', True),
    (r'f:\dev\zackbot2\RESEARCH_EVIDENCE\Ledger\x.json', True),         # case spelling cannot step around the denial
    ('F:/Dev/ZackBot2/research_evidence/ledger/x.json', True),           # '/' spelling neither
    (LEDGER + r'\..\ledger\x.json', True),
    (r'F:\Dev\ZackBot2\research_evidence\ledger2\x.json', False),        # sibling prefix is not containment
    (r'F:\Dev\ZackBot2\research_evidence\x.json', False),
    (r'C:\Dev\ZackBot2\research_evidence\ledger\x.json', False),         # another drive is never containment
    (r'C:\Users\u\AppData\Local\Temp\pytest-0\r.json', False),
    (r'\\srv\share\research_evidence\ledger\x.json', False),             # UNC share vs drive
])
def test_is_within_ntpath(path, inside):
    assert XP.is_within(path, LEDGER, pathmod=ntpath) is inside


def test_is_within_posix_matches_the_previous_commonpath_rule():
    for p, root in [('/r/ledger/x', '/r/ledger'), ('/r/ledger', '/r/ledger'), ('/r/ledger2/x', '/r/ledger'),
                    ('/q/x', '/r/ledger'), ('/r/ledger/../x', '/r/ledger')]:
        old = posixpath.commonpath([posixpath.abspath(p), root]) == root
        assert XP.is_within(p, root, pathmod=posixpath) is old


def test_is_within_reraises_a_same_drive_value_error():
    class Mod:                                          # a ValueError that is NOT a drive mismatch must not become False
        abspath = staticmethod(ntpath.abspath)
        splitdrive = staticmethod(ntpath.splitdrive)
        normcase = staticmethod(ntpath.normcase)

        @staticmethod
        def commonpath(paths):
            raise ValueError('something else')
    with pytest.raises(ValueError, match='something else'):
        XP.is_within(r'F:\a\b', r'F:\a', pathmod=Mod)


def test_path_identity_same_volume_bytes_unchanged_and_cross_volume_absolute():
    root = r'F:\Dev\ZackBot2'
    for p in (r'F:\Dev\ZackBot2\data\exchange_rules_mainnet.json', r'F:\tmp\rules.json', r'F:\Dev\ZackBot2'):
        assert XP.path_identity(p, root, pathmod=ntpath) == \
            ntpath.relpath(ntpath.abspath(p), root).replace('\\', '/')        # the previous bytes exactly
    assert XP.path_identity(r'F:\Dev\ZackBot2\data\exchange_rules_mainnet.json', root, pathmod=ntpath) == \
        'data/exchange_rules_mainnet.json'
    x = XP.path_identity(r'C:\Users\u\Temp\pytest-0\rules_mainnet.json', root, pathmod=ntpath)
    assert x == 'C:/Users/u/Temp/pytest-0/rules_mainnet.json'
    assert ntpath.splitdrive(x)[0]                     # drive-qualified: never collides with a relpath identity
    for p in ('/r/data/x.json', '/q/x.json'):
        assert XP.path_identity(p, '/r', pathmod=posixpath) == posixpath.relpath(p, '/r')


# ------------------------------------------------------------------------------------- bpyr_runner call sites

@pytest.fixture
def cdrive(tmp_path):
    """tmp_path is simulated drive C:, the checkout (and everything else) drive F:."""
    with two_drives(tempfile.gettempdir()):
        yield tmp_path


def test_simulation_really_splits_the_drives(cdrive):
    with pytest.raises(ValueError):
        os.path.commonpath([str(cdrive / 'x'), str(ROOT)])
    with pytest.raises(ValueError):
        os.path.relpath(str(cdrive / 'x'), str(ROOT))
    assert XP.is_within(str(cdrive / 'x'), str(ROOT)) is False
    assert XP.is_within(str(ROOT / 'tools'), str(ROOT)) is True


@pytest.fixture(scope='module')
def bp():
    from tests.test_bpyr_runner import AUTH, R, CLEAN, NO_MANIFEST
    pf = R.preflight(git=CLEAN, manifest_check=NO_MANIFEST)
    res = {'candidate_id': R.CANDIDATE, 'prereg_sha256': pf.prereg_sha256, 'decided': False, 'evaluated': False}
    return R, pf, AUTH(pf), res


def test_write_result_cross_drive_authorized_write_is_allowed(bp, cdrive):
    R, pf, ok, res = bp
    p = R.write_result(res, str(cdrive / 'result.json'), authorization=ok)
    assert Path(p).read_bytes() == R.canon(R._json_safe(res))
    with pytest.raises(FileExistsError):                                       # still exclusive-create
        R.write_result(res, p, authorization=ok)


@pytest.mark.parametrize('name, tok, extra', [
    ('verdict.json', 'ok', {}),
    ('My_Verdict_x.json', 'ok', {}),
    ('REGISTRY.json', 'ok', {}),
    ('r.json', 'ok', {'verdict': 'x'}),
    ('r.json', 'ok', {'decided': True}),
    ('r.json', None, {}),
    ('r.json', 'wrong', {}),
])
def test_write_result_cross_drive_denials_still_run(bp, cdrive, name, tok, extra):
    R, pf, ok, res = bp
    auth = {'ok': ok, None: None, 'wrong': R.EvalAuthorization('cowork', '1', pf.prereg_sha256)}[tok]
    with pytest.raises(R.WriteRefused):
        R.write_result(dict(res, **extra), str(cdrive / name), authorization=auth)
    assert not (cdrive / name).exists()


def test_write_result_same_drive_ledger_denied_under_split(bp, cdrive):
    R, pf, ok, res = bp
    target = ROOT.joinpath(*R.LEDGER_REL[:-1], 'winpath01_never.json')
    with pytest.raises(R.WriteRefused):
        R.write_result(res, str(target), authorization=ok)
    assert not target.exists()


def test_execute_refuses_the_ledger_dir_and_passes_the_check_for_another_drive(bp, cdrive):
    R, pf, ok, res = bp
    from tests.test_bpyr_runner import _execute
    with pytest.raises(R.WriteRefused, match='ledger'):
        _execute(pf, R.os.path.join(str(ROOT), *R.LEDGER_REL[:-1]))
    with pytest.raises(R.WriteRefused):                                        # authorization still first
        _execute(pf, cdrive / 'a', authorization=None)
    assert _execute(pf, cdrive / 'r', stop_after=1)['status'] == 'PARTIAL'     # cross-drive run dir is allowed


def test_prospective_rules_on_another_drive_is_outside_the_repo(bp, cdrive):
    R = bp[0]
    p = cdrive / 'snap.json'
    p.write_bytes(b'{}')
    with pytest.raises(R.RunnerError, match='inside the repo'):
        R.ProspectiveRules.load(str(p), hashlib.sha256(b'{}').hexdigest(), ['BTCUSDT'], repo=str(ROOT))


# ------------------------------------------------------------------------------------ run_fib02 load_rules

def _write_rules(d, env='mainnet'):
    snap = dict(environment=env, verified=True, provenance='direct_fetch', fetched_at='2026-10-11T15:16:58Z',
                schema='zackbot.exchange_rules/1', symbols={'BTCUSDT': dict(step=0.001)})
    p = d / f'rules_{env}.json'
    p.write_text(json.dumps(snap), encoding='utf-8')
    return p


@pytest.fixture(scope='module')
def W():
    import run_fib02_candidate as W
    return W


def test_fib02_external_rules_on_another_drive(W, cdrive):
    p = _write_rules(cdrive)
    snap, ident = W.load_rules(str(p))
    assert ident['path'] == os.path.abspath(str(p)).replace(os.sep, '/')
    assert ident['sha256'] == hashlib.sha256(p.read_bytes()).hexdigest()        # hash unaffected
    with pytest.raises(W.Fib02Error, match='mainnet'):                           # content checks still run
        W.load_rules(str(_write_rules(cdrive, env='testnet')))


def test_fib02_same_volume_identity_bytes_unchanged(W, tmp_path):
    p = _write_rules(tmp_path)
    assert W.load_rules(str(p))[1]['path'] == os.path.relpath(os.path.abspath(str(p)), W.ROOT).replace(os.sep, '/')
    committed = os.path.join(W.ROOT, W.RULES)
    if os.path.exists(committed):
        assert W.load_rules(committed)[1]['path'] == W.RULES                    # the frozen default identity
