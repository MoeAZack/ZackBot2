"""The run watchdog: kill switch, restart on crash or hang, never restart on a held or unknown account."""
from __future__ import annotations

import json
import os
import sys
import textwrap
import threading
import time

import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.join(ROOT, 'tools'))

import newcore_watchdog as wd  # noqa: E402


def test_status_publication_retries_a_transient_windows_reader_lock(tmp_path, monkeypatch):
    target = tmp_path / 'watchdog.json'
    target.write_text('{"old":true}', encoding='utf-8')
    replace = wd.os.replace
    attempts = []

    def reader_lock(source, destination):
        attempts.append((source, destination))
        if len(attempts) <= 2:
            assert json.loads(target.read_text(encoding='utf-8')) == {'old': True}
            raise PermissionError('temporary Windows reader lock')
        return replace(source, destination)

    monkeypatch.setattr(wd.os, 'replace', reader_lock)
    wd._write_json(str(target), {'new': True})
    assert len(attempts) == 3
    assert json.loads(target.read_text(encoding='utf-8')) == {'new': True}
    assert not (tmp_path / 'watchdog.json.tmp').exists()


def test_status_publication_does_not_hide_a_persistent_permission_failure(tmp_path, monkeypatch):
    target = tmp_path / 'watchdog.json'
    target.write_text('{"old":true}', encoding='utf-8')
    attempts = []

    def denied(source, destination):
        attempts.append((source, destination))
        raise PermissionError('persistent Windows reader lock')

    monkeypatch.setattr(wd.os, 'replace', denied)
    with pytest.raises(PermissionError):
        wd._write_json(str(target), {'new': True})
    assert len(attempts) == 10
    assert json.loads(target.read_text(encoding='utf-8')) == {'old': True}

CHILD = textwrap.dedent('''
    import json, os, sys, time
    plan_file, stop = sys.argv[1], sys.argv[sys.argv.index('--stop-file') + 1]
    disabled = sys.argv[sys.argv.index('--entry-disable-file') + 1]
    with open(plan_file, encoding='utf-8') as h:
        plans = json.load(h)
    count_file = plan_file + '.count'
    n = int(open(count_file, encoding='utf-8').read()) if os.path.exists(count_file) else 0
    with open(count_file, 'w', encoding='utf-8') as h:
        h.write(str(n + 1))
    plan = plans[min(n, len(plans) - 1)]
    step = 0
    def line(status, holds=()):
        global step
        if status != 'booted':
            step = 1 if plan['mode'] == 'cached_status' else step+1
        print(json.dumps({'schema': 'zackbot.newcore.runtime.paper_status.v1', 'status': status,
            'step': step, 'environment': 'paper_sim', 'entries_enabled': not os.path.exists(disabled),
            'accounts': [{'account_id': 'a1', 'holds': list(holds)}]}), flush=True)
    if plan['mode'] == 'garbage_before_boot':
        deadline = time.monotonic()+1.5
        while time.monotonic() < deadline:
            print(json.dumps({'status': 'running'}), flush=True)
            time.sleep(0.03)
        sys.exit(0)
    if plan['mode'] == 'slow_start':
        print(json.dumps({'event': 'starting'}), flush=True)
        time.sleep(plan['delay'])
    line('booted')
    if plan['mode'] in ('exit', 'slow_start'):
        line('running', plan.get('holds', ()))
        sys.exit(plan.get('code', 0))
    if plan['mode'] == 'hang':
        time.sleep(60)
    if plan['mode'] == 'garbage_after_boot':
        deadline = time.monotonic()+1.5
        while time.monotonic() < deadline:
            print('garbage is not a heartbeat', flush=True)
            time.sleep(0.03)
        sys.exit(0)
    deadline = time.monotonic()+1.5 if plan['mode'] == 'cached_status' else float('inf')
    while not os.path.exists(stop):                       # 'serve': run until the stop file
        if time.monotonic() >= deadline:
            sys.exit(0)
        line('running')
        time.sleep(0.05)
    line('stopped')
''')


@pytest.fixture
def child(tmp_path, monkeypatch):
    monkeypatch.setattr(wd, 'BACKOFF_FIRST_S', 0.05)
    script = tmp_path / 'child.py'
    script.write_text(CHILD, encoding='utf-8')

    def make(*plans):
        plan = tmp_path / 'plan.json'
        plan.write_text(json.dumps(list(plans)), encoding='utf-8')
        return [sys.executable, str(script), str(plan)]

    def count():
        path = tmp_path / 'plan.json.count'
        return int(path.read_text(encoding='utf-8')) if path.exists() else 0
    make.count = count
    return make


def dog(tmp_path, command, **kw):
    kw.setdefault('poll_s', 0.02)
    kw.setdefault('terminate_grace_s', 5.0)
    return wd.Watchdog(str(tmp_path / 'root'), command, **kw)


def events(tmp_path):
    with open(tmp_path / 'root' / 'watchdog.jsonl', encoding='utf-8') as h:
        return [json.loads(line)['event'] for line in h]


def test_a_crash_is_restarted_and_a_clean_stop_ends_the_watch(tmp_path, child):
    cmd = child({'mode': 'exit', 'code': 1}, {'mode': 'exit', 'code': 0})
    out = dog(tmp_path, cmd).watch()
    assert out['status'] == 'stopped' and out['runs'] == 2 and child.count() == 2
    assert events(tmp_path).count('restarting') == 1


@pytest.mark.parametrize('code,status', [(2, 'refused'), (3, 'held'), (64, 'usage')])
def test_refused_held_and_usage_exits_are_never_restarted(tmp_path, child, code, status):
    out = dog(tmp_path, child({'mode': 'exit', 'code': code}, {'mode': 'exit', 'code': 0})).watch()
    assert out['status'] == status and child.count() == 1


def test_a_crash_with_a_sticky_hold_stops_for_a_human(tmp_path, child):
    cmd = child({'mode': 'exit', 'code': 1, 'holds': ['order_unresolved']}, {'mode': 'exit', 'code': 0})
    out = dog(tmp_path, cmd).watch()
    assert out['status'] == 'held_needs_human' and out['holds'] == ['a1:order_unresolved'] and child.count() == 1


def test_a_crash_with_only_a_clearable_hold_is_restarted(tmp_path, child):
    cmd = child({'mode': 'exit', 'code': 1, 'holds': ['evidence_timeout']}, {'mode': 'exit', 'code': 0})
    assert dog(tmp_path, cmd).watch()['status'] == 'stopped' and child.count() == 2


def test_restarts_are_capped(tmp_path, child):
    out = dog(tmp_path, child({'mode': 'exit', 'code': 1}), max_restarts=2).watch()
    assert out['status'] == 'gave_up' and child.count() == 3


def test_a_hung_run_is_terminated_and_restarted(tmp_path, child):
    cmd = child({'mode': 'hang'}, {'mode': 'exit', 'code': 0})
    started = time.monotonic()
    out = dog(tmp_path, cmd, stall_s=0.5).watch()
    assert out['status'] == 'stopped' and child.count() == 2 and time.monotonic() - started < 30
    assert 'stalled' in [json.loads(line).get('how') for line in
                         open(tmp_path / 'root' / 'watchdog.jsonl', encoding='utf-8')]


def test_explicit_full_stop_ends_the_run_and_nothing_restarts_it(tmp_path, child):
    cmd = child({'mode': 'serve'})
    dg = dog(tmp_path, cmd)
    result = {}
    thread = threading.Thread(target=lambda: result.update(dg.watch()))
    thread.start()
    for _ in range(400):
        if wd.status(dg.root)['watchdog'] and wd.status(dg.root)['watchdog'].get('status') == 'running':
            break
        time.sleep(0.01)
    killed = wd.stop(dg.root, timeout_s=20, sleep=time.sleep)
    thread.join(20)
    assert killed['status'] == 'killed' and result['status'] == 'killed' and child.count() == 1
    assert result['last']['status'] == 'stopped'               # the run stopped itself through the stop file
    with pytest.raises(wd.WatchdogError) as ex:                 # a kill is never undone silently
        dog(tmp_path, cmd).watch()
    assert ex.value.code == 'stop_file_present'


def test_clear_stop_starts_again(tmp_path, child):
    os.makedirs(tmp_path / 'root')
    (tmp_path / 'root' / 'STOP').write_text('stop\n', encoding='utf-8')
    disabled = tmp_path / 'root' / 'DISABLE_ENTRIES'
    disabled.write_text('disable\n', encoding='utf-8')
    out = dog(tmp_path, child({'mode': 'exit', 'code': 0})).watch(clear_stop=True)
    assert out['status'] == 'stopped' and 'stop_cleared' in events(tmp_path)
    assert disabled.exists() and out['last']['entries_enabled'] is False


def test_a_full_stop_during_backoff_does_not_restart(tmp_path, child, monkeypatch):
    monkeypatch.setattr(wd, 'BACKOFF_FIRST_S', 5.0)
    dg = dog(tmp_path, child({'mode': 'exit', 'code': 1}))
    result = {}
    thread = threading.Thread(target=lambda: result.update(dg.watch()))
    thread.start()
    for _ in range(400):
        st = wd.status(dg.root)['watchdog']
        if st and st.get('status') == 'backoff':
            break
        time.sleep(0.01)
    wd.stop(dg.root, timeout_s=20, sleep=time.sleep)
    thread.join(20)
    assert result['status'] == 'killed' and child.count() == 1


def test_command_and_cli_refusals(tmp_path, monkeypatch):
    with pytest.raises(wd.WatchdogError):
        wd.Watchdog(str(tmp_path), [])
    with pytest.raises(wd.WatchdogError):
        wd.Watchdog(str(tmp_path), ['x', '--stop-file', 'y'])
    import io
    out = io.StringIO()
    monkeypatch.setenv('LIVE_CONFIRM', '1')
    assert wd.main(['watch', '--root', str(tmp_path), '--', 'x'], out=out) == wd.EXIT_USAGE
    assert 'live_switch_present' in out.getvalue()


def test_clearable_holds_mirror_the_runtime():
    from newcore.runtime.runtime import CLEARABLE
    assert wd.CLEARABLE_HOLDS == frozenset(code.value for code in CLEARABLE)


def test_a_slow_start_gets_the_startup_grace_not_the_stall_limit(tmp_path, child):
    out = dog(tmp_path, child({'mode': 'slow_start', 'delay': 1.0}), stall_s=0.3, startup_grace_s=5).watch()
    assert out['status'] == 'stopped' and child.count() == 1


def test_a_start_that_never_reports_is_a_hang_after_the_grace(tmp_path, child):
    cmd = child({'mode': 'slow_start', 'delay': 30}, {'mode': 'exit', 'code': 0})
    out = dog(tmp_path, cmd, stall_s=0.3, startup_grace_s=1.0).watch()
    assert out['status'] == 'stopped' and child.count() == 2


def test_the_heartbeat_records_restarts_and_steps_and_no_environment(tmp_path, child, monkeypatch):
    monkeypatch.setenv('ZACKBOT_TNET02_API_SECRET', 'NeverWriteThisValue0001')
    cmd = child({'mode': 'serve'})
    dg = dog(tmp_path, cmd)
    thread = threading.Thread(target=dg.watch)
    thread.start()
    beat = None
    for _ in range(500):
        beat = wd.status(dg.root)['watchdog']
        if beat and beat.get('last_status') == 'running':
            break
        time.sleep(0.01)
    wd.stop(dg.root, timeout_s=20, sleep=time.sleep)
    thread.join(20)
    assert beat['restarts'] == 0 and beat['run'] == 1 and 'steps_since_boot' in beat
    for name in os.listdir(dg.root):
        path = os.path.join(dg.root, name)
        if os.path.isfile(path):
            assert 'NeverWriteThisValue0001' not in open(path, encoding='utf-8').read()


def test_the_run_inherits_the_environment(tmp_path, monkeypatch):
    monkeypatch.setenv('ZACKBOT_TNET02_API_KEY', 'InheritedKey000000001')
    script = tmp_path / 'env_child.py'
    script.write_text("import os, sys, json\n"
                      "ok = os.environ.get('ZACKBOT_TNET02_API_KEY') == 'InheritedKey000000001'\n"
                      "print(json.dumps({'status': 'stopped', 'accounts': []}), flush=True)\n"
                      "sys.exit(0 if ok else 5)\n", encoding='utf-8')
    out = dog(tmp_path, [sys.executable, str(script)], max_restarts=0).watch()
    assert out['status'] == 'stopped'


@pytest.mark.parametrize('mode', ['garbage_before_boot', 'garbage_after_boot', 'cached_status'])
def test_growing_output_without_valid_progress_still_stalls(tmp_path, child, mode):
    started = time.monotonic()
    result = dog(tmp_path, child({'mode': mode}), stall_s=.3, startup_grace_s=.3,
                 max_restarts=0).watch()
    assert result['status'] == 'gave_up' and child.count() == 1
    assert time.monotonic()-started < 5
    with open(tmp_path/'root'/'watchdog.jsonl', encoding='utf-8') as handle:
        assert any(json.loads(line).get('how') == 'stalled' for line in handle)


def test_entry_kill_latches_without_exiting_or_restarting_the_child(tmp_path, child):
    dg = dog(tmp_path, child({'mode': 'serve'}))
    result = {}
    thread = threading.Thread(target=lambda: result.update(dg.watch()))
    thread.start()
    try:
        for _ in range(500):
            before = wd.status(dg.root)['watchdog']
            if before and before.get('last_status') == 'running':
                break
            time.sleep(.01)
        assert before and before.get('entries_enabled') is True
        disabled = wd.kill(dg.root, timeout_s=5)
        assert disabled['status'] == 'entries_disabled'
        time.sleep(.1)
        after = wd.status(dg.root)
        assert thread.is_alive() and child.count() == 1
        assert after['entries_disabled'] and not after['stop_file']
        assert after['watchdog']['child_pid'] == before['child_pid']
        assert after['watchdog']['steps_since_boot'] > before['steps_since_boot']
        assert after['watchdog']['entries_enabled'] is False
    finally:
        wd.stop(dg.root, timeout_s=5)
        thread.join(5)
    assert not thread.is_alive() and child.count() == 1
