"""RT-02 PAPER install / start / stop / restart / rollback (tools/newcore_release.py).  Real subprocesses, PAPER
only, no network, no credentials.  All text I/O is utf-8."""
from __future__ import annotations

import io
import json
import os
import shutil
import sys
import time

import pytest

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(os.path.dirname(HERE))
sys.path.insert(0, os.path.join(ROOT, 'tools'))

import newcore_release as R  # noqa: E402

from newcore.runtime.__main__ import main as runtime_main  # noqa: E402


def write_json(path, value):
    with open(path, 'w', encoding='utf-8') as handle:
        json.dump(value, handle)
    return str(path)


@pytest.fixture
def site(tmp_path):
    cfg = write_json(tmp_path / 'config.json', {
        'schema': 'zackbot.newcore.runtime.config.v1', 'environment': 'paper_sim',
        'accounts': [{'account_id': 'acct_' + '0' * 31 + '1', 'aggregate_id': 'pf_' + '0' * 31 + '1',
                      'account_dir': str(tmp_path / 'acct1'), 'egress_id': 'paper-1'}]})
    marks = write_json(tmp_path / 'marks.json', {'BTCUSDT': '100'})
    rel = R.Releases(str(tmp_path / 'root'))
    yield rel, cfg, marks, tmp_path
    try:
        rel.stop(30)
    except Exception:
        pass


def changed_source(tmp_path):
    """A copy of the repo's newcore package with one harmless comment added: a second, different release."""
    src = tmp_path / 'src2'
    shutil.copytree(os.path.join(ROOT, 'newcore'), src / 'newcore', ignore=shutil.ignore_patterns('__pycache__'))
    with open(src / 'newcore' / 'runtime' / 'paper.py', 'a', encoding='utf-8') as handle:
        handle.write('# release two\n')
    return str(src)


def wait_for(predicate, timeout=60):
    end = time.monotonic() + timeout
    while time.monotonic() < end:
        if predicate():
            return
        time.sleep(0.1)
    raise AssertionError('timed out')


def test_install_verifies_and_points_at_the_release(site):
    rel, cfg, marks, tmp = site
    out = rel.install(ROOT, cfg)
    assert out['status'] == 'installed' and out['previous'] is None
    rid = out['release']
    assert rel.current() == {'schema': R.SCHEMA, 'current': rid, 'previous': None}
    rel.verify(rid)
    assert rel.install(ROOT, cfg) == {'status': 'unchanged', 'release': rid}
    assert not any(n.endswith('.staging') for n in os.listdir(os.path.join(rel.root, 'releases')))


def test_a_failing_check_leaves_the_pointer_untouched(site):
    rel, cfg, marks, tmp = site
    first = rel.install(ROOT, cfg)['release']
    bad = write_json(tmp / 'bad.json', {'schema': 'zackbot.newcore.runtime.config.v1', 'environment': 'mainnet',
                                         'accounts': []})
    with pytest.raises(R.ReleaseError) as ex:
        rel.install(changed_source(tmp), bad)
    assert ex.value.code == 'release_check_failed'
    assert rel.current()['current'] == first


def test_install_two_releases_then_rollback(site):
    rel, cfg, marks, tmp = site
    first = rel.install(ROOT, cfg)['release']
    second = rel.install(changed_source(tmp), cfg)
    assert second['previous'] == first and second['release'] != first
    back = rel.rollback()
    assert back == {'status': 'rolled_back', 'release': first, 'replaced': second['release']}
    assert rel.current() == {'schema': R.SCHEMA, 'current': first, 'previous': second['release']}


def test_rollback_refuses_a_tampered_or_missing_previous_release(site):
    rel, cfg, marks, tmp = site
    with pytest.raises(R.ReleaseError, match='no_previous_release'):
        rel.rollback()
    first = rel.install(ROOT, cfg)['release']
    rel.install(changed_source(tmp), cfg)
    path = os.path.join(rel.release_dir(first), 'newcore', 'runtime', 'paper.py')
    with open(path, 'a', encoding='utf-8') as handle:
        handle.write('# tampered\n')
    pointer = rel.current()
    with pytest.raises(R.ReleaseError, match='release_file_mismatch'):
        rel.rollback()
    assert rel.current() == pointer
    os.remove(path)
    with pytest.raises(R.ReleaseError, match='release_file_mismatch'):
        rel.verify(first)


def test_an_extra_file_in_a_release_is_refused(site):
    rel, cfg, marks, tmp = site
    rid = rel.install(ROOT, cfg)['release']
    with open(os.path.join(rel.release_dir(rid), 'newcore', 'sneaky.py'), 'w', encoding='utf-8') as handle:
        handle.write('x = 1\n')
    with pytest.raises(R.ReleaseError, match='release_extra_files'):
        rel.verify(rid)


def test_start_refuses_without_an_install(site):
    rel, cfg, marks, tmp = site
    with pytest.raises(R.ReleaseError, match='not_installed'):
        rel.start(cfg, marks)


def test_start_stop_restart_and_rollback_of_a_running_paper_bot(site):
    rel, cfg, marks, tmp = site
    assert runtime_main(['init-paper', '--config', cfg], environ={}, out=io.StringIO()) == 0
    first = rel.install(ROOT, cfg)['release']
    started = rel.start(cfg, marks)
    assert started['release'] == first and rel.running()
    wait_for(lambda: (rel.last_line() or {}).get('status') == 'booted')
    with pytest.raises(R.ReleaseError, match='already_running'):
        rel.start(cfg, marks)
    stopped = rel.stop(60)
    assert stopped['status'] == 'stopped' and stopped['last']['reason'] == 'stop_file'
    assert stopped['last']['accounts'][0]['holds'] == [] and not rel.running()
    assert not os.path.exists(rel.stop_file)
    assert rel.stop() == {'status': 'not_running'}
    # restart reuses the recorded config and marks
    again = rel.restart()
    assert again['stop'] == 'not_running' and again['start']['release'] == first
    wait_for(lambda: (rel.last_line() or {}).get('status') == 'booted')
    # a new release does not touch the running bot; rollback while running restarts on the previous release
    second = rel.install(changed_source(tmp), cfg)['release']
    assert rel.running() and rel._run_state()['release'] == first
    rel.restart()
    wait_for(lambda: (rel.last_line() or {}).get('status') == 'booted')
    assert rel._run_state()['release'] == second
    back = rel.rollback()
    assert back['release'] == first and back['restart']['start']['release'] == first
    wait_for(lambda: (rel.last_line() or {}).get('status') == 'booted')
    final = rel.stop(60)
    assert final['last']['status'] == 'stopped' and final['last']['accounts'][0]['holds'] == []
    logs = sorted(os.listdir(os.path.join(rel.root, 'logs')))
    assert logs == ['run-00001.jsonl', 'run-00002.jsonl', 'run-00003.jsonl', 'run-00004.jsonl']


def test_cli_reports_failures_as_json(site):
    rel, cfg, marks, tmp = site
    out = io.StringIO()
    assert R.main(['rollback', '--root', rel.root], out=out) == R.EXIT_FAILED
    assert json.loads(out.getvalue()) == {'status': 'failed', 'code': 'no_previous_release'}
    assert R.main(['install', '--root', rel.root], out=io.StringIO()) == R.EXIT_USAGE
    assert R.main(['explode', '--root', rel.root], out=io.StringIO()) == R.EXIT_USAGE
    out = io.StringIO()
    assert R.main(['status', '--root', rel.root], out=out) == R.EXIT_OK
    assert json.loads(out.getvalue())['running'] is False
