"""T04: verify.py must report truthfully - a failed step fails the run, metrics are parsed from the real outputs,
and every summary carries the provenance fields the review asked for."""
import json, os, re, subprocess, sys, tempfile

import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
import verify  # noqa: E402

REPLAY_OUT = """ENGINE  500 -> 1189 (+137.8%)  maxDD -12.3%  trades 133
BACKTEST 500 -> 1175 (+135.0%)  maxDD -12.3%  trades 133
TRADES matched 133 (100.0%)  median |dR| 0.017  p95 |dR| 0.118  return gap 2.81 pp  DD gap 0.03 pp  position mismatches {}  stops 1 lots 1
STRICT targets {'matched_pct': 98.0, 'med_dr': 0.05, 'p95_dr': 0.25, 'ret_gap': 3.0, 'dd_gap': 2.0}: all met
GATE hard (return gap <= 15.0 pp, positions/stops reconcile) + strict -> PASS
"""


def _rep(tmp):
    return verify.Report('fast', tmp)


def test_replay_metrics_are_parsed_from_the_real_output(monkeypatch, tmp_path):
    monkeypatch.setattr(verify, 'run', lambda *a, **k: (0, REPLAY_OUT, 1.0))
    rep = _rep(str(tmp_path))
    assert verify.replay_step(rep, 'replay 1: x', [])
    m = rep.data['replays']['replay 1: x']
    assert m == dict(matched=133, matched_pct=100.0, median_abs_dR=0.017, p95_abs_dR=0.118, return_gap_pp=2.81,
                     dd_gap_pp=0.03, position_mismatches='{}', gate='PASS', steps=24, strict=True)


def test_replay_gate_fail_or_missing_metrics_fails_the_step(monkeypatch, tmp_path):
    rep = _rep(str(tmp_path))
    monkeypatch.setattr(verify, 'run', lambda *a, **k: (0, REPLAY_OUT.replace('-> PASS', '-> FAIL'), 1.0))
    assert not verify.replay_step(rep, 'replay gate fail', [])
    monkeypatch.setattr(verify, 'run', lambda *a, **k: (0, 'GATE hard -> PASS\n', 1.0))      # PASS line but no metrics
    assert not verify.replay_step(rep, 'replay no metrics', [])
    monkeypatch.setattr(verify, 'run', lambda *a, **k: (1, REPLAY_OUT, 1.0))                  # non-zero exit
    assert not verify.replay_step(rep, 'replay exit 1', [])


def test_pytest_counts_come_from_junit_and_zero_tests_is_a_failure(monkeypatch, tmp_path):
    rep = _rep(str(tmp_path))
    def fake_run(cmd, *a, **k):
        junit = next(c.split('=', 1)[1] for c in cmd if c.startswith('--junitxml='))
        open(junit, 'w').write('<testsuites><testsuite tests="5" failures="1" errors="0" skipped="1"/></testsuites>')
        return 1, 'FAILED tests/test_x.py::test_y - boom\n', 2.0
    monkeypatch.setattr(verify, 'run', fake_run)
    assert not verify.pytest_step(rep, 'tests', ['tests'], 10)
    assert rep.data['tests']['tests'] == dict(tests=5, failures=1, errors=0, skipped=1, passed=3)
    def empty_run(cmd, *a, **k):
        junit = next(c.split('=', 1)[1] for c in cmd if c.startswith('--junitxml='))
        open(junit, 'w').write('<testsuites><testsuite tests="0" failures="0" errors="0" skipped="0"/></testsuites>')
        return 0, '', 1.0
    monkeypatch.setattr(verify, 'run', empty_run)
    assert not verify.pytest_step(rep, 'tests none', ['tests'], 10), 'a run that collected nothing must not pass'


def test_one_failed_step_fails_the_run_and_the_summary_says_so(tmp_path):
    rep = _rep(str(tmp_path))
    rep.step('a', True, 1.0)
    rep.step('b', False, 1.0, why='planted')
    rep.step('c', True, skip=True, why='Windows only')
    assert rep.finish() == 1
    d = json.load(open(tmp_path / 'latest_fast.json'))
    assert d['passed'] is False and d['failed_steps'] == ['b']
    assert [s['status'] for s in d['steps']] == ['passed', 'FAILED', 'skipped']
    for k in ('started', 'finished', 'timezone', 'host'):
        assert k in d
    assert d['timezone'] == 'Africa/Cairo' and re.search(r'\+0[23]:00$', d['started']), 'Cairo time with offset'


def test_manifest_check_catches_missing_and_changed_files(monkeypatch, tmp_path):
    """Self-contained (runs in the installer's staging copy too): a fake dataset with one changed and one missing file."""
    import hashlib
    (tmp_path / 'data').mkdir()
    (tmp_path / 'data' / 'ok.csv').write_bytes(b'a,b\n1,2\n')         # exact bytes: write_text() turns \n into CRLF on Windows
    (tmp_path / 'data' / 'changed.csv').write_bytes(b'tampered\n')
    good = hashlib.sha256(b'a,b\n1,2\n').hexdigest()
    man = {'files': {'data/ok.csv': {'sha256': good}, 'data/changed.csv': {'sha256': good}, 'data/missing.csv': {'sha256': good}}}
    (tmp_path / 'DATA_MANIFEST.json').write_text(json.dumps(man))
    monkeypatch.setattr(verify, 'ROOT', str(tmp_path))
    rep = verify.Report('fast', str(tmp_path / 'out'))
    assert not verify.manifest_check(rep)
    assert rep.data['dataset']['missing'] == ['data/missing.csv'] and rep.data['dataset']['changed'] == ['data/changed.csv']
    (tmp_path / 'data' / 'changed.csv').write_bytes(b'a,b\n1,2\n'); (tmp_path / 'data' / 'missing.csv').write_bytes(b'a,b\n1,2\n')
    assert verify.manifest_check(verify.Report('fast', str(tmp_path / 'out2')))


@pytest.mark.skipif(not os.path.isdir(os.path.join(ROOT, 'data')),
                    reason='installer staging copy: the data folders are not copied there (the manifest check needs them)')
def test_summary_has_the_required_provenance_fields(tmp_path):
    """Real run of the cheap part (static + manifest) through main(): commit, dependencies, dataset hash, Cairo times."""
    out = tmp_path / 'v'
    code = ('import sys, verify\n'
            'verify.pytest_step = lambda rep, name, *a, **k: rep.step(name, True, 0.0, counts={"tests": 1})\n'
            'verify.installer_mode = lambda rep, mode, t: rep.step("installer " + mode, True, skip=True)\n'
            f'sys.argv = ["verify.py", "fast", "--out", r"{out}"]\n'
            'sys.exit(verify.main())\n')
    r = subprocess.run([sys.executable, '-c', code], cwd=ROOT, capture_output=True, text=True, timeout=300)
    assert r.returncode == 0, r.stdout[-800:] + r.stderr[-800:]
    d = json.load(open(out / 'latest_fast.json'))
    assert d['dependencies'].get('pandas') and d['dependencies'].get('pytest')
    assert re.fullmatch(r'[0-9a-f]{64}', d['dataset']['manifest_sha256']) and d['dataset']['files'] >= 60
    assert 'commit' in d['git'] and d['build_id']
    assert d['passed'] is True


def test_release_refuses_off_windows():
    if os.name == 'nt':
        return
    r = subprocess.run([sys.executable, 'verify.py', 'release'], cwd=ROOT, capture_output=True, text=True, timeout=60)
    assert r.returncode == 2 and 'Windows PC only' in r.stdout

def test_ui_harness_gets_seed_history_on_a_clean_checkout(monkeypatch, tmp_path):
    """GitHub run #6 (T04): dev_out/ is not in Git, so the harness had no closed trades and its calendar-day flow timed out.
    verify full now hands the harness the seeds replay 1 wrote in the same run; dev_out/ is only the fallback."""
    root, out = tmp_path / 'repo', tmp_path / 'out'
    (root / 'dev_out').mkdir(parents=True)
    monkeypatch.setattr(verify, 'ROOT', str(root))
    rep = verify.Report('full', str(out))
    assert verify.ui_seeds(rep) == ([], 'none')
    (root / 'dev_out' / 'seed_history.json').write_text('[]')
    files, src = verify.ui_seeds(rep)
    assert src.startswith('dev_out') and [os.path.basename(f) for f in files] == ['seed_history.json']
    run1 = out / ('replay_' + verify.REPLAYS[0][0].split(':')[0].replace(' ', ''))
    run1.mkdir(parents=True)
    for n in ('seed_history.json', 'seed_missed.json'): (run1 / n).write_text('[]')
    files, src = verify.ui_seeds(rep)
    assert src == 'replay 1 (this run)' and sorted(os.path.basename(f) for f in files) == ['seed_history.json', 'seed_missed.json']
    seen = {}
    def fake_run(cmd, timeout, env=None):
        seen['files'] = sorted(os.listdir(env['ZB_OUT']))
        return 0, 'UI HARNESS: PASS - 171/171 checks passed\n', 1.0
    monkeypatch.setattr(verify, 'run', fake_run)
    assert verify.ui_step(rep)
    assert seen['files'] == ['seed_history.json', 'seed_missed.json'], 'the harness must find the seeds in its ZB_OUT folder'
    assert rep.data['ui']['seeds'] == 'replay 1 (this run)'


# ---------------------------------------------------------------- T04 review round 1
def _status(mode='PAPER', engine='ok', lots=({'protected': True},), **h):
    return dict(mode=mode, build='b', lots=list(lots), health=dict(dict(engine=engine, exchange='ok', unprotected=[], untracked={}, orphans=0), **h))


def test_live_bot_blocks_the_drill_before_any_stop(monkeypatch, tmp_path):
    """T04 review P1: the drill stops/restarts ZackBot, so a LIVE (or unhealthy) bot must stop the release run BEFORE it."""
    calls = []
    monkeypatch.setattr(verify, 'installer_mode', lambda rep, mode, t: calls.append(mode) or rep.step('installer ' + mode, True))
    monkeypatch.setattr(verify.time, 'sleep', lambda s: None)
    for st in (_status(mode='LIVE'), _status(engine='degraded'), _status(lots=({'protected': False},)), _status(orphans=2)):
        monkeypatch.setattr(verify, 'bot_status', lambda st=st: st)
        rep = verify.Report('release', str(tmp_path / 'r'))
        assert not verify.release_steps(rep)
        assert calls == [], f'drill must not start for {st}'
        assert rep.finish() == 1 and 'installer drill' in rep.data['failed_steps']
    monkeypatch.setattr(verify, 'bot_status', lambda: (_ for _ in ()).throw(OSError('bot not running')))
    rep = verify.Report('release', str(tmp_path / 'r2'))
    assert not verify.release_steps(rep) and calls == []


def test_paper_bot_is_drilled_then_checked_again(monkeypatch, tmp_path):
    seq, calls = [_status(), _status(mode='LIVE')], []
    monkeypatch.setattr(verify, 'installer_mode', lambda rep, mode, t: calls.append(mode) or rep.step('installer ' + mode, True))
    monkeypatch.setattr(verify.time, 'sleep', lambda s: None)
    monkeypatch.setattr(verify, 'bot_status', lambda: seq[0] if not calls else seq[1])
    rep = verify.Report('release', str(tmp_path / 'r'))
    assert not verify.release_steps(rep), 'a bot that comes back LIVE after the drill must fail the run'
    assert calls == ['drill'] and set(rep.data['reconciliation']) == {'before drill', 'after drill'}
    calls.clear(); seq[1] = _status()
    rep = verify.Report('release', str(tmp_path / 'r2'))
    assert verify.release_steps(rep) and calls == ['drill'] and rep.finish() == 0


def test_runtime_gate_requires_paper_mode():
    assert verify.runtime_ok(_status())
    assert not verify.runtime_ok(_status(mode='LIVE')) and not verify.runtime_ok(_status(mode=None))


def test_full_and_release_have_no_skip_switches(tmp_path):
    """T04 review P1: a named full/release run can no longer omit the replay or UI gate and still say PASS."""
    for level in ('full', 'release'):
        for sw in ('--skip-ui', '--skip-replays'):
            out = tmp_path / f'{level}{sw}'
            r = subprocess.run([sys.executable, 'verify.py', level, sw, '--out', str(out)], cwd=ROOT, capture_output=True, text=True, timeout=60)
            assert r.returncode == 2 and 'unrecognized arguments' in r.stderr, (level, sw, r.stdout[-300:], r.stderr[-300:])
            assert not (out / f'latest_{level}.json').exists()


def test_secret_scan_planted_files(tmp_path):
    """T04 review P2: private files at any depth and secrets inside JSON are caught; market data, checksums and fixtures are not."""
    (tmp_path / 'sub' / 'deeper').mkdir(parents=True); (tmp_path / 'data').mkdir(); (tmp_path / 'dev_out').mkdir()
    real_key = 'Zq7' + 'x9Lm2Np4Rt6Vb8Kd0Fh1Jg3Sa5Wc7Ye9Ui2Oo4Pq6Ar8Ts0Dv1Bn3Mk5Lj7Hg9F'[:61]
    assert len(real_key) == 64
    (tmp_path / 'sub' / 'deeper' / 'config.env').write_text('MODE=paper\n')
    (tmp_path / 'sub' / 'creds.json').write_text(json.dumps({'api_secret': real_key}))
    (tmp_path / 'sub' / 'k.txt').write_text('-----BEGIN OPENSSH ' + 'PRIVATE KEY-----\nabc\n')
    (tmp_path / 'sub' / 'tg.md').write_text('token 987654321:AA' + 'b' * 16 + 'C' * 17)
    (tmp_path / 'data' / 'x.csv').write_text(real_key)                         # market-data folder: not scanned
    (tmp_path / 'dev_out' / 'config.env').write_text('x')                      # local-only, gitignored: not scanned
    (tmp_path / 'ok.json').write_text(json.dumps({'sha256': 'ab' * 32, 'fixture': 'C' * 64, 'tg': '123456789:AA' + 'x' * 33}))
    forbidden, hits = verify.secret_scan(str(tmp_path))
    assert forbidden == [os.path.join('sub', 'deeper', 'config.env')]
    kinds = sorted(h.split(': ', 1)[1].rsplit(' ', 1)[0] for h in hits)
    assert kinds == ['Telegram bot token', 'key-like 64-char string', 'private key block'], hits
    assert not any(h.startswith(('data', 'dev_out', 'ok.json')) for h in hits)
