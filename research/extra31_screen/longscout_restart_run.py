"""Run one published LongScout restart version and preserve test evidence."""
from contextlib import ExitStack
from datetime import datetime
import importlib
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import time
from zoneinfo import ZoneInfo

from longscout_register_version import HERE, ROOT, RESTART_BRANCH, RESTART_DEADLINE

CACHE = Path('C:/Dev/ZackBot_research_cache')
FRESH = CACHE / 'longscout_fresh_20261114'
EXTRA = CACHE / 'h5_wide_d01_20261112' / 'clean'
PINNED_DATA = CACHE / 'longscout_codex_v91_20261120' / '_data'


def git(*args):
    return subprocess.check_output(['git', *args], cwd=ROOT, text=True).strip()


def run_checks(version, out):
    name = f'longscout_v{version}'
    test_path = f'tests/test_{name}.py'
    with (out / 'tests.log').open('wb') as log:
        subprocess.run([sys.executable, '-B', '-m', 'pytest', '-q', test_path],
                       cwd=ROOT, stdout=log, stderr=subprocess.STDOUT, check=True)
    mutation_dir = out / 'mutations'
    mutation_dir.mkdir()
    source = (ROOT / 'research' / f'{name}.py').read_text(encoding='utf-8')
    test = importlib.import_module(f'test_{name}')
    supplemental = HERE / f'{name}_mutation_updates.json'
    updates = json.loads(supplemental.read_text(encoding='utf-8')) if supplemental.exists() else {}
    for spec in test.PLANTED_MUTANTS:
        label, before, after, test_name = updates.get(spec[0], spec)
        assert source.count(before) == 1, label
        (mutation_dir / f'{label}.txt').write_text(
            f'BEFORE\n{before}\nAFTER\n{after}\nDETECTED BY\n{test_name}\n', encoding='utf-8')
        script = (
            'import sys, types, pytest\n'
            'name, research, test = sys.argv[1:]\n'
            'sys.path.insert(0, research)\n'
            'module = sys.modules[name] = types.ModuleType(name)\n'
            'exec(compile(sys.stdin.read(), name, "exec"), module.__dict__)\n'
            'raise SystemExit(pytest.main(["-q", test]))\n'
        )
        check = subprocess.run([sys.executable, '-B', '-c', script, name,
                                str(ROOT / 'research'), f'{test_path}::{test_name}'], cwd=ROOT,
                               input=source.replace(before, after, 1).encode('utf-8'),
                               stdout=subprocess.PIPE, stderr=subprocess.STDOUT)
        (mutation_dir / f'{label}.log').write_bytes(check.stdout)
        assert check.returncode == 1 and b'AssertionError' in check.stdout and b'1 failed' in check.stdout, label


def main():
    version = int(sys.argv[1])
    assert version >= 105 and git('branch', '--show-current') == RESTART_BRANCH
    assert datetime.now(ZoneInfo('Africa/Cairo')) < RESTART_DEADLINE
    head = git('rev-parse', 'HEAD')
    remote = git('ls-remote', '--heads', 'origin', f'refs/heads/{RESTART_BRANCH}').split()
    assert remote and remote[0] == head, 'Push the case registration before execution'
    case_file = HERE / f'longscout_v{version}_cases.json'
    cases = json.loads(case_file.read_text(encoding='utf-8'))
    ledger = json.loads((HERE / 'longscout_resume_ledger.json').read_text())
    item = ledger['versions'][str(version)]
    assert item['status'] == 'PREREGISTERED'
    source = item['source_freeze']
    runner = cases.get('runner', 'longscout_v91_run.py')
    assert runner in ('longscout_v91_run.py', 'longscout_flow_run.py')
    git('merge-base', '--is-ancestor', source, head)
    git('diff', '--exit-code', source, '--', f'research/longscout_v{version}.py',
        f'tests/test_longscout_v{version}.py', f'research/extra31_screen/{runner}',
        'research/extra31_screen/longscout_version_report.py')
    git('diff', '--exit-code', head, '--', 'research/extra31_screen')

    out = CACHE / f'longscout_codex_v{version}_20261124'
    out.mkdir()
    sys.path.insert(0, str(ROOT / 'tests'))
    run_checks(version, out)
    # Work in a private copy. The frozen runner validates every CSV.
    shutil.copytree(PINNED_DATA, out / '_data')
    state_path = ROOT / 'research' / 'longscout_control_state.json'
    state = json.loads(state_path.read_text(encoding='utf-8'))
    state['running'] = dict(version=version, source=source, registration=head,
                            output=str(out), start_cairo=datetime.now(ZoneInfo('Africa/Cairo')).isoformat())
    state_path.write_text(json.dumps(state, indent=1) + '\n', encoding='utf-8', newline='\n')
    env = dict(os.environ, LONGSCOUT_CASES=case_file.name, PYTHONUTF8='1', OPENBLAS_NUM_THREADS='1')
    workers = []
    with ExitStack() as streams:
        try:
            for key in cases['cases']:
                log = streams.enter_context((out / f'{key}.log').open('wb'))
                command = [sys.executable, '-B', str(HERE / runner),
                           str(FRESH), str(EXTRA), str(out), '--case', key, '--orders', '40']
                workers.append(subprocess.Popen(command, cwd=ROOT, env=env, stdout=log, stderr=subprocess.STDOUT))
            while any(worker.poll() is None for worker in workers):
                if datetime.now(ZoneInfo('Africa/Cairo')) >= RESTART_DEADLINE:
                    raise RuntimeError('Cairo cutoff reached. Preserve partial trial outputs.')
                time.sleep(3)
            if any(worker.returncode for worker in workers):
                raise RuntimeError(f'At least one V{version} economic worker failed')
        finally:
            for worker in workers:
                if worker.poll() is None:
                    subprocess.run(['taskkill', '/PID', str(worker.pid), '/T', '/F'], capture_output=True)
    subprocess.run([sys.executable, '-B', str(HERE / 'longscout_finish_version.py'),
                    str(version), str(out), str(out / 'tests.log')], cwd=ROOT, env=env, check=True)


if __name__ == '__main__':
    main()
