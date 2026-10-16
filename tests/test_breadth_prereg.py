"""zb-breadth-prereg/1 (Codex 6200896386): validator, registration guards and the fixed statistics.

Registration tests run in a throwaway git repo holding copies of the frozen files and the real ledger directory, with
pinned commit dates and an injected CLOCK, so they stay valid after the real holdout start.

Every frozen byte comes from the IMMUTABLE registered commit Y behind the annotated tag (tests/breadth_registered.py,
Codex ruling), never the mutable working tree: the committed prereg is rebuilt by Y's own validator on Y's own modules
in an isolated interpreter, and the throwaway repo is materialized from Y. In-process validate / register calls take
the module-derived parts of the build (_frozen_config / _hypotheses) from that isolated Y build, because this process
has already imported backtest / strategies / run_breadth_candidate from the working tree by bare name.
"""
import ast
import copy
import json
import os
import subprocess
import sys

import pytest

import breadth_registered as RG

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, 'tools', 'research'))
import breadth_prereg as BP                                                                 # noqa: E402
import ledger as L                                                                          # noqa: E402

ACCEPT_DATE = '2026-10-16T10:00:00Z'
ACCEPT_MS = BP._ms(ACCEPT_DATE)


def _registered_modules(monkeypatch):
    """In-process builds take the module-derived config / hypotheses from Y (computed by Y's own code, isolated)."""
    reg = RG.registered_build(ROOT)
    monkeypatch.setattr(BP, '_frozen_config', lambda repo: copy.deepcopy(reg['frozen_config']))
    monkeypatch.setattr(BP, '_hypotheses', lambda repo: copy.deepcopy(reg['hypotheses']))
    return reg


# ------------------------------------------------------------------ the committed prereg
def test_committed_prereg_is_the_frozen_build(monkeypatch):
    reg = _registered_modules(monkeypatch)
    raw = RG.registered_blob(ROOT, BP.PREREG)
    assert reg['validate'] == []                            # Y's validator on Y's modules accepts Y's prereg
    assert raw == reg['build'].encode()                     # and Y's prereg is byte-for-byte BP.build(Y)
    pre = json.loads(raw)
    assert BP.validate(pre, reg['tree']) == []              # the current validator accepts it on the Y tree too
    assert BP.dumps(BP.build(reg['tree'])) == raw
    assert [h['id'] for h in pre['hypotheses']] == [BP.hypothesis_id(k, s) for k, s in BP.HYPOTHESES]
    assert len(pre['hypotheses']) == 12 and pre['statistics']['multiplicity']['m'] == 12
    assert ('breakout_pyramid', 'short') not in BP.HYPOTHESES
    assert (pre['holdout']['start'], pre['holdout']['end']) == ('2026-10-19T00:00:00Z', '2027-10-18T00:00:00Z')
    assert pre['gate']['min_closed_trades'] == 30 and pre['gate']['pf_gt'] == 1.05 and pre['gate']['max_dd_r_lt'] == 60.0
    assert pre['config']['mgmt_mode'] == 'defaults' and pre['config']['mgmt_override'] == {} and pre['config']['tf'] == '4h'
    assert pre['statistics']['method'] == 'monte_carlo' and pre['statistics']['n_flips'] == 100_000


@pytest.mark.parametrize('mut, msg', [
    (lambda p: p['gate'].update(pf_gt=1.0), 'gate'),
    (lambda p: p['config']['costs'].update(fee_taker=0.0004), 'config'),
    (lambda p: p['hypotheses'][3]['params'].update(x=1), 'hypotheses'),
    (lambda p: p['hypotheses'].append(dict(p['hypotheses'][0])), 'duplicate hypothesis id'),
    (lambda p: p['hypotheses'].__setitem__(11, dict(p['hypotheses'][0])), 'duplicate hypothesis id'),
    (lambda p: p['holdout'].update(start='2026-10-12T00:00:00Z'), 'holdout'),
    (lambda p: p['code']['sha256'].update({'strategies.py': '0' * 64}), 'code'),
    (lambda p: p['universe'].update(dev_incomplete=[]), 'universe'),
    (lambda p: p['statistics'].update(n_flips=1000), 'statistics'),
    (lambda p: p.update(extra=1), 'keys'),
])
def test_tamper_refused(mut, msg, monkeypatch):
    tree = _registered_modules(monkeypatch)['tree']
    pre, _ = BP.load_prereg(tree)
    assert BP.validate(pre, tree) == []                     # untampered is clean, so each refusal is the mutation's
    mut(pre)
    bad = BP.validate(pre, tree)
    assert bad and any(msg in b for b in bad), bad


# ------------------------------------------------------------------ throwaway repo
def _git(repo, *a, date=ACCEPT_DATE):
    env = dict(os.environ, GIT_AUTHOR_DATE=date, GIT_COMMITTER_DATE=date, GIT_AUTHOR_NAME='t', GIT_AUTHOR_EMAIL='t@t',
               GIT_COMMITTER_NAME='t', GIT_COMMITTER_EMAIL='t@t')
    return subprocess.run(['git', '-C', repo, *a], capture_output=True, check=True, env=env).stdout.decode().strip()


def _unregister(r):
    """Return the copied ledger to its pre-registration state (the real family is registered from Y onward).

    The family's ledger is removed and REGISTRY.jsonl is cut before the family's first line; it is appended last,
    so the hash-chained prefix stays valid."""
    led = os.path.join(r, BP.LEDGER)
    if os.path.exists(led): os.remove(led)
    reg = os.path.join(r, BP.REGISTRATION_FILES[1])
    with open(reg, 'rb') as f: lines = f.read().splitlines(keepends=True)
    keep = []
    for ln in lines:
        if json.loads(ln).get('family') == BP.FAMILY: break
        keep.append(ln)
    with open(reg, 'wb') as f: f.write(b''.join(keep))


@pytest.fixture
def repo(tmp_path, monkeypatch):
    r = str(tmp_path / 'repo')
    os.makedirs(r)
    RG.materialize(ROOT, r, BP.CODE_FILES + (BP.UNIVERSE, BP.PREREG, BP.VALIDATOR, 'tools/research/ledger.py',
                                             'research_evidence/ledger'))       # registered Y bytes, not ROOT
    _unregister(r)
    _registered_modules(monkeypatch)
    _git(r, 'init', '-q')
    _git(r, 'config', 'core.autocrlf', 'false')
    _git(r, 'add', '-A')
    _git(r, 'commit', '-q', '-m', 'accepted head')
    monkeypatch.setattr(BP, 'CLOCK', lambda: ACCEPT_MS + 60_000)
    return r


def _head(r):
    return _git(r, 'rev-parse', 'HEAD')


def _commit_ledger(r, date='2026-10-16T10:05:00Z'):
    _git(r, 'add', '-A')
    _git(r, 'commit', '-q', '-m', 'register', date=date)


def test_register_then_verify(repo):
    acc = _head(repo)
    recs = BP.register(repo, accepted_commit=acc)
    assert len(recs) == 12 and len({r['candidate_id'] for r in recs}) == 12
    d = recs[0]['detail']
    assert d['registered_at_utc'] == '2026-10-16T10:01:00.000Z' and d['accepted_commit'] == acc
    assert d['prereg_sha256'] == BP.load_prereg(repo)[1]
    assert d['accepted_tree'] == _git(repo, 'rev-parse', 'HEAD^{tree}')
    assert BP.verify_registration(repo, require_committed=False) == []
    assert BP.verify_registration(repo) == ['the registration records are not committed']
    _commit_ledger(repo)
    assert BP.verify_registration(repo) == []
    fams = L.verify(os.path.join(repo, BP.LEDGER))
    assert L.n_trials(fams[BP.FAMILY]) == 12


def test_duplicate_registration_refused(repo):
    BP.register(repo, accepted_commit=_head(repo))
    _commit_ledger(repo)
    with pytest.raises(BP.BreadthPreregError, match='already registered'):
        BP.register(repo, accepted_commit=_git(repo, 'rev-parse', 'HEAD~1'))


@pytest.mark.parametrize('now, ok', [(BP.HOLDOUT_START_MS - 1, True), (BP.HOLDOUT_START_MS, False),
                                     (BP.HOLDOUT_START_MS + 3_600_000, False)])
def test_late_registration_refused(repo, monkeypatch, now, ok):
    monkeypatch.setattr(BP, 'CLOCK', lambda: now)
    if ok:
        assert len(BP.register(repo, accepted_commit=_head(repo))) == 12
    else:
        with pytest.raises(BP.BreadthPreregError, match='at or after the holdout start'):
            BP.register(repo, accepted_commit=_head(repo))
        assert not os.path.exists(os.path.join(repo, BP.LEDGER))


@pytest.mark.parametrize('kw', [{'registered_at_utc': '2026-10-16T09:00:00.000Z'}, {'now_ms': 1}, {'clock': lambda: 1}])
def test_caller_supplied_time_rejected(repo, kw):
    with pytest.raises(BP.BreadthPreregError, match='taken internally'):
        BP.register(repo, accepted_commit=_head(repo), **kw)
    with pytest.raises(SystemExit):
        BP.main(['register', '--accepted-commit', _head(repo), '--registered-at-utc', '2026-10-16T09:00:00.000Z'])


def test_backdated_registration_refused(repo, monkeypatch):
    monkeypatch.setattr(BP, 'CLOCK', lambda: ACCEPT_MS - 1)
    with pytest.raises(BP.BreadthPreregError, match='backdated'):
        BP.register(repo, accepted_commit=_head(repo))


def _forge(repo, at: str, acc=None):
    """Hand-written records bypassing register()'s clock, to prove verify_registration catches them."""
    acc = acc or _head(repo)
    recs = BP.registration_records(BP.load_prereg(repo)[1], acc, _git(repo, 'rev-parse', acc + '^{tree}'),
                                   BP.parse_ts(at))
    for i, r in enumerate(recs):
        L.append(os.path.join(repo, BP.LEDGER), **r, lineage='root' if i == 0 else None)


@pytest.mark.parametrize('at, commit_date, msg', [
    ('2026-10-16T09:59:59.999Z', '2026-10-16T10:05:00Z', 'backdated'),
    ('2026-10-19T00:00:00.000Z', '2026-10-19T00:10:00Z', 'late registration'),
    ('2026-10-16T10:01:00.000Z', '2026-10-19T00:00:00Z', 'before the holdout start'),
    ('2026-10-16T11:00:00.000Z', '2026-10-16T10:05:00Z', 'at/after registered_at_utc'),
])
def test_verify_refuses_bad_timestamps(repo, at, commit_date, msg):
    _forge(repo, at)
    _commit_ledger(repo, date=commit_date)
    bad = BP.verify_registration(repo)
    assert any(msg in b for b in bad), bad


def test_verify_refuses_retune_and_second_reveal_free_ledger(repo):
    BP.register(repo, accepted_commit=_head(repo))
    L.append(os.path.join(repo, BP.LEDGER), kind='grid_point', candidate_id=BP.hypothesis_id('ema_mom', 'long'),
             split='development', window=dict(BP.DEV_WINDOW), author='claude-code', cairo_date='2026-10-16',
             detail={'config': 'stop_atr+0.5'})
    _commit_ledger(repo)
    assert any('no retune' in b for b in BP.verify_registration(repo))


def test_register_refuses_prereg_drift_and_unaccepted_bytes(repo):
    acc = _head(repo)
    with pytest.raises(BP.BreadthPreregError, match='40-hex'):
        BP.register(repo, accepted_commit=acc[:12])
    with open(os.path.join(repo, 'strategies.py'), 'ab') as f:
        f.write(b'\n# drift\n')
    with pytest.raises(BP.BreadthPreregError, match='prereg invalid'):
        BP.register(repo, accepted_commit=acc)
    _git(repo, 'checkout', '--', 'strategies.py')
    p = os.path.join(repo, BP.PREREG)
    pre = json.load(open(p, encoding='utf-8'))
    pre['gate']['pf_gt'] = 1.0
    open(p, 'wb').write(BP.dumps(pre))
    _git(repo, 'commit', '-qam', 'tamper')
    with pytest.raises(BP.BreadthPreregError):
        BP.register(repo, accepted_commit=acc)


def test_register_refuses_other_dirt(repo):
    open(os.path.join(repo, 'stray.txt'), 'w').write('x')
    with pytest.raises(BP.BreadthPreregError, match='uncommitted'):
        BP.register(repo, accepted_commit=_head(repo))


# ------------------------------------------------------------------ no holdout read path
def test_no_data_read_paths_in_the_validator():
    src = open(os.path.join(ROOT, BP.VALIDATOR), encoding='utf-8').read()
    tree = ast.parse(src)
    calls = {n.func.attr if isinstance(n.func, ast.Attribute) else getattr(n.func, 'id', None)
             for n in ast.walk(tree) if isinstance(n, ast.Call)}
    assert not calls & {'read_cache', 'read_csv', 'collect', 'urlopen', 'check_window', 'fetch', 'Collector'}
    runs = [n for n in ast.walk(tree) if isinstance(n, ast.Call) and isinstance(n.func, ast.Attribute)
            and n.func.attr == 'run']
    assert runs and all(isinstance(n.args[0], ast.List) and n.args[0].elts[0].value == 'git' for n in runs)
    for tok in ('fapi.binance.com/fapi/v1/klines\'', 'candles', '.csv', 'requests', 'urllib', 'LOCALAPPDATA'):
        assert tok not in src.replace("'https://fapi.binance.com/fapi/v1/klines (public", '')


def test_frozen_runner_refuses_the_holdout_window():
    import run_vwaprev_candidate as VC
    with pytest.raises(VC.RunnerError):
        VC.check_window(f"{BP.HOLDOUT['start']}/{BP.HOLDOUT['end']}")


# ------------------------------------------------------------------ statistics
def test_sign_flip_deterministic_and_calibrated():
    x = [5, -3, 8, 2, -1, 7, 4, -2, 6, 3]
    p1, p2 = BP.sign_flip_p(x, 'seed-a', 4000), BP.sign_flip_p(x, 'seed-a', 4000)
    assert p1 == p2 and 0 < p1 < 0.1
    assert BP.sign_flip_p([1] * 12, 's', 20000) < 0.002              # all-positive: ~2**-12
    assert BP.sign_flip_p([0, 0, 0], 's', 100) == 1.0
    assert BP.sign_flip_p([-1] * 8, 's', 1000) > 0.99
    assert BP._sign_bits('s', 3, 300) == BP._sign_bits('s', 3, 300) and len(BP._sign_bits('s', 3, 300)) == 300


def test_daily_pnl_micro():
    tr = [{'t_out': '2026-11-02T04:00:00Z', 'pnl': 1.5}, {'t_out': '2026-11-02T20:00:00Z', 'pnl': -0.25},
          {'t_out': '2026-11-01T08:00:00Z', 'pnl': 0.000001}]
    assert BP.daily_pnl_micro(tr) == [1, 1_250_000]


def test_holm_known_values():
    order = [f'h{i}' for i in range(4)]
    p = {'h0': 0.01, 'h1': 0.04, 'h2': 0.03, 'h3': 0.005}
    out = BP.holm(p, order, alpha=0.05)
    assert out['h3'] == (0.02, True) and out['h0'] == (0.03, True)
    assert out['h2'][1] is False and out['h1'][1] is False                 # 2 x 0.03 = 0.06 > alpha: stop
    assert abs(out['h2'][0] - 0.06) < 1e-12 and abs(out['h1'][0] - 0.06) < 1e-12
    assert BP.holm(p, order) == out
    ties = BP.holm({'a': 0.01, 'b': 0.01}, ['a', 'b'])
    assert ties == {'a': (0.02, True), 'b': (0.02, True)}


def test_gate_rows():
    row = dict(status='ok', data_complete=True, busted=False, trades=30, pnl=0.01, pf=1.051, max_dd_r=59.9,
               holm_reject=True)
    assert all(BP.gate_rows(row).values())
    for k, v, g in [('trades', 29, 'min_closed_trades'), ('pnl', 0.0, 'net_cash'), ('pf', 1.05, 'pf'),
                    ('max_dd_r', 60.0, 'max_dd_r'), ('busted', True, 'no_bust'), ('data_complete', False, 'data'),
                    ('holm_reject', False, 'holm'), ('pf', None, 'pf')]:
        assert BP.gate_rows(dict(row, **{k: v}))[g] is False


# ------------------------------------------------------------------ evaluation guard (Codex 6201777892): run from Y
def _registered(repo):
    acc = _head(repo)
    BP.register(repo, accepted_commit=acc)
    _commit_ledger(repo)
    return acc, _head(repo)


def test_evaluation_guard_runs_only_from_y(repo):
    acc, y = _registered(repo)
    b = BP.evaluation_guard(repo, y)                        # HEAD == pinned Y, clean, parent X, ledger-only diff
    assert b['accepted_commit'] == acc and b['accepted_tree'] == _git(repo, 'rev-parse', acc + '^{tree}')
    assert b['y'] == y and b['spend'] is None and _git(repo, 'rev-parse', 'HEAD^') == acc
    with pytest.raises(BP.BreadthPreregError, match='does not resolve'):
        BP.evaluation_guard(repo, 'no-such-tag')
    with pytest.raises(BP.BreadthPreregError, match='no registration'):
        BP.evaluation_guard(repo, acc)                      # X itself holds no registration
    _git(repo, 'checkout', '-q', acc)
    with pytest.raises(BP.BreadthPreregError, match='not the registration commit Y'):
        BP.evaluation_guard(repo, y)                        # HEAD == X, not Y
    _git(repo, 'checkout', '-q', y)
    with open(os.path.join(repo, 'strategies.py'), 'ab') as f:
        f.write(b'# dirty\n')
    with pytest.raises(BP.BreadthPreregError, match='dirty worktree'):
        BP.evaluation_guard(repo, y)
    _git(repo, 'checkout', '--', 'strategies.py')
    with open(os.path.join(repo, 'untracked.py'), 'w') as f:
        f.write('x')
    with pytest.raises(BP.BreadthPreregError, match='dirty worktree'):
        BP.evaluation_guard(repo, y)
    os.remove(os.path.join(repo, 'untracked.py'))
    BP.evaluation_guard(repo, y)


@pytest.mark.parametrize('change', ['empty', 'code', 'ledger_only'])
@pytest.mark.parametrize('allow', [False, True])
def test_evaluation_guard_refuses_any_head_after_y(repo, change, allow):
    acc, y = _registered(repo)
    if change == 'code':
        with open(os.path.join(repo, 'strategies.py'), 'ab') as f:
            f.write(b'# later\n')
        _git(repo, 'commit', '-qam', 'later code change')
    elif change == 'ledger_only':                           # a ledger-only child that is not an evaluation spend
        L.append(os.path.join(repo, BP.LEDGER), kind='data_access', candidate_id=BP.CANDIDATE, split='development',
                 window=dict(BP.DEV_WINDOW), author='claude-code', cairo_date='2026-10-16')
        _commit_ledger(repo, date='2026-10-16T10:06:00Z')
    else:
        _git(repo, 'commit', '-q', '--allow-empty', '-m', 'same tree, new commit')
    with pytest.raises(BP.BreadthPreregError, match='not the registration commit Y'):
        BP.evaluation_guard(repo, y, allow_spend_child=allow)


def test_evaluation_guard_refuses_a_pinned_y_that_did_not_introduce_the_registration(repo):
    acc, y = _registered(repo)
    _git(repo, 'commit', '-q', '--allow-empty', '-m', 'later commit carrying the same ledger')
    with pytest.raises(BP.BreadthPreregError, match='not the commit that introduced the registration'):
        BP.evaluation_guard(repo, _head(repo))


def test_evaluation_guard_refuses_a_fresh_chain_rewrite_after_y(repo):
    """Cowork 6201874565 #2: a later commit rewriting the ledger with a fresh hash chain is refused."""
    acc, y = _registered(repo)
    _git(repo, 'rm', '-q', BP.LEDGER)                                   # drop the family + its REGISTRY lines
    _git(repo, 'checkout', acc, '--', 'research_evidence/ledger')
    _forge(repo, '2026-10-16T10:03:00.000Z', acc)                       # fresh chain, new registered_at_utc
    _commit_ledger(repo, date='2026-10-16T10:07:00Z')
    z = _head(repo)
    for pin, allow in ((y, False), (y, True), (z, False), (z, True)):
        with pytest.raises(BP.BreadthPreregError, match='not the registration commit Y|only parent of Y must be X'):
            BP.evaluation_guard(repo, pin, allow_spend_child=allow)


def test_evaluation_guard_refuses_a_tree_mismatch_inside_y(repo):
    acc = _head(repo)
    BP.register(repo, accepted_commit=acc)
    with open(os.path.join(repo, 'backtest.py'), 'ab') as f:
        f.write(b'# smuggled into Y\n')
    _commit_ledger(repo)
    with pytest.raises(BP.BreadthPreregError, match='differs from X|not ledger-only'):
        BP.evaluation_guard(repo, _head(repo))


def test_registered_binding_requires_records(repo):
    with pytest.raises(BP.BreadthPreregError, match='no registration'):
        BP.registered_binding(repo, 'HEAD')


def test_register_refuses_a_head_other_than_x(repo):
    acc = _head(repo)
    _git(repo, 'commit', '-q', '--allow-empty', '-m', 'child of X')
    with pytest.raises(BP.BreadthPreregError, match='HEAD != X'):
        BP.register(repo, accepted_commit=acc)


def test_verify_refuses_a_smuggled_change_in_the_registration_commit(repo):
    BP.register(repo, accepted_commit=_head(repo))
    with open(os.path.join(repo, 'strategies.py'), 'ab') as f:
        f.write(b'# smuggled\n')
    _commit_ledger(repo)
    bad = BP.verify_registration(repo)
    assert any('not ledger-only' in b and 'strategies.py' in b for b in bad), bad


def test_verify_refuses_y_that_is_not_a_direct_child_of_x(repo):
    acc = _head(repo)
    _git(repo, 'commit', '-q', '--allow-empty', '-m', 'an intermediate master commit between X and Y')
    _forge(repo, '2026-10-16T10:01:00.000Z', acc)
    _commit_ledger(repo)
    bad = BP.verify_registration(repo)
    assert any('only, direct parent' in b for b in bad), bad


def test_verify_refuses_a_registration_on_an_unrelated_root(repo):
    acc = _head(repo)
    _git(repo, 'checkout', '-q', '--orphan', 'other')
    _git(repo, 'commit', '-q', '-m', 'unrelated root')
    _forge(repo, '2026-10-16T10:01:00.000Z', acc)
    _commit_ledger(repo)
    assert any('only, direct parent' in b for b in BP.verify_registration(repo))
