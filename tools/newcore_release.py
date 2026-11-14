"""RT-02 PAPER install, start, stop, restart and rollback for the NEWCORE runtime.

``python tools/newcore_release.py <command> --root <dir> ...``

* ``install --source <repo>``: copy the ``newcore`` package (``.py`` files only) into
  ``<root>/releases/<release id>/``, where the id is the first 12 hex of the SHA-256 over the file manifest.  Every
  copied file is hashed again against the manifest, then the new release must pass ``check --config``.  Only then
  does ``current.json`` move to it (atomically), remembering the release it replaced as ``previous``.  A failed
  install leaves ``current.json`` untouched.  A running bot is not touched; ``restart`` picks the release up.
* ``start --config <file> --marks <file>``: run ``python -m newcore.runtime run`` from the current release in the
  background, with ``<root>/STOP`` as its stop file and its JSON status lines in ``<root>/logs/run-<n>.jsonl``.
* ``stop``: create the stop file and wait for the run to write its final ``stopped`` line.
* ``restart``: stop (if running), then start.
* ``rollback``: re-verify the previous release's manifest, swap current and previous, and restart if it was running.
* ``status``: the current and previous release, whether a run is live, and its last status line.

PAPER only: ``run`` itself refuses a testnet config, entries and any live switch, and this module never reads a
credential or opens a socket.  All text I/O is utf-8.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import shutil
import subprocess
import sys
import time

SCHEMA = 'zackbot.newcore.release.v1'
EXIT_OK, EXIT_FAILED, EXIT_USAGE = 0, 2, 64
FINAL_STATES = ('stopped', 'refused')


class ReleaseError(RuntimeError):
    def __init__(self, code: str):
        self.code = code
        super().__init__(code)


def _sha(path: str) -> str:
    h = hashlib.sha256()
    with open(path, 'rb') as handle:
        for block in iter(lambda: handle.read(1 << 16), b''):
            h.update(block)
    return h.hexdigest()


def _write_json(path: str, value) -> None:
    tmp = path + '.tmp'
    with open(tmp, 'w', encoding='utf-8') as handle:
        json.dump(value, handle, sort_keys=True, indent=1)
        handle.flush()
        os.fsync(handle.fileno())
    os.replace(tmp, path)


def _read_json(path: str):
    with open(path, encoding='utf-8') as handle:
        return json.load(handle)


def manifest_of(package_dir: str) -> dict[str, str]:
    """Relative posix path -> SHA-256 for every .py file under ``package_dir`` (caches skipped)."""
    out = {}
    for base, dirs, files in os.walk(package_dir):
        dirs[:] = sorted(d for d in dirs if d != '__pycache__' and not d.startswith('.'))
        for name in sorted(files):
            if name.endswith('.py'):
                full = os.path.join(base, name)
                out[os.path.relpath(full, os.path.dirname(package_dir)).replace(os.sep, '/')] = _sha(full)
    if not out:
        raise ReleaseError('source_empty')
    return out


def release_id(manifest: dict[str, str]) -> str:
    return hashlib.sha256(json.dumps(manifest, sort_keys=True).encode('utf-8')).hexdigest()[:12]


class Releases:
    def __init__(self, root: str, *, python: str = sys.executable, popen=subprocess.Popen, run=subprocess.run,
                 sleep=time.sleep, monotonic=time.monotonic):
        self.root = os.path.abspath(root)
        self.python, self._popen, self._run, self._sleep, self._mono = python, popen, run, sleep, monotonic

    # ------------------------------------------------------------------------------------------------ paths
    @property
    def pointer(self) -> str:
        return os.path.join(self.root, 'current.json')

    @property
    def stop_file(self) -> str:
        return os.path.join(self.root, 'STOP')

    @property
    def run_file(self) -> str:
        return os.path.join(self.root, 'run.json')

    def release_dir(self, rid: str) -> str:
        return os.path.join(self.root, 'releases', rid)

    def current(self) -> dict | None:
        if not os.path.exists(self.pointer):
            return None
        raw = _read_json(self.pointer)
        if type(raw) is not dict or raw.get('schema') != SCHEMA:
            raise ReleaseError('pointer_invalid')
        return raw

    # ------------------------------------------------------------------------------------------------ verify
    def verify(self, rid: str) -> None:
        folder = self.release_dir(rid)
        try:
            manifest = _read_json(os.path.join(folder, 'manifest.json'))['files']
        except Exception:
            raise ReleaseError('manifest_unreadable') from None
        if release_id(manifest) != rid:
            raise ReleaseError('manifest_mismatch')
        for rel, digest in manifest.items():
            path = os.path.join(folder, *rel.split('/'))
            if not os.path.isfile(path) or _sha(path) != digest:
                raise ReleaseError('release_file_mismatch')
        if manifest_of(os.path.join(folder, 'newcore')) != manifest:
            raise ReleaseError('release_extra_files')

    def _cli(self, rid: str, *args: str, **kwargs):
        env = {k: v for k, v in os.environ.items() if k != 'PYTHONPATH'}
        env['PYTHONPATH'] = self.release_dir(rid)
        env['PYTHONIOENCODING'] = 'utf-8'
        return [self.python, '-m', 'newcore.runtime', *args], dict(env=env, cwd=self.release_dir(rid), **kwargs)

    # ------------------------------------------------------------------------------------------------ install
    def install(self, source: str, config: str) -> dict:
        package = os.path.join(os.path.abspath(source), 'newcore')
        if not os.path.isfile(os.path.join(package, 'runtime', '__main__.py')):
            raise ReleaseError('source_invalid')
        manifest = manifest_of(package)
        rid = release_id(manifest)
        folder = self.release_dir(rid)
        if not os.path.isdir(folder):
            staging = folder + '.staging'
            shutil.rmtree(staging, ignore_errors=True)
            for rel in manifest:
                dst = os.path.join(staging, *rel.split('/'))
                os.makedirs(os.path.dirname(dst), exist_ok=True)
                shutil.copyfile(os.path.join(os.path.dirname(package), *rel.split('/')), dst)
            _write_json(os.path.join(staging, 'manifest.json'), {'schema': SCHEMA, 'files': manifest})
            os.replace(staging, folder)
        self.verify(rid)
        argv, kw = self._cli(rid, 'check', '--config', os.path.abspath(config))
        done = self._run(argv, capture_output=True, text=True, encoding='utf-8', timeout=120, **kw)
        if done.returncode != 0:
            raise ReleaseError('release_check_failed')
        now = self.current()
        if now is not None and now['current'] == rid:
            return {'status': 'unchanged', 'release': rid}
        _write_json(self.pointer, {'schema': SCHEMA, 'current': rid,
                                   'previous': None if now is None else now['current']})
        return {'status': 'installed', 'release': rid, 'previous': None if now is None else now['current']}

    # ------------------------------------------------------------------------------------------------ run state
    def _run_state(self) -> dict | None:
        return _read_json(self.run_file) if os.path.exists(self.run_file) else None

    def last_line(self) -> dict | None:
        state = self._run_state()
        if state is None or not os.path.exists(state['log']):
            return None
        last = None
        with open(state['log'], encoding='utf-8') as handle:
            for line in handle:
                line = line.strip()
                if line:
                    try:
                        last = json.loads(line)
                    except ValueError:
                        pass
        return last

    def running(self) -> bool:
        state = self._run_state()
        if state is None or state.get('ended'):
            return False
        last = self.last_line()
        if last is not None and last.get('status') in FINAL_STATES:
            return False
        return _alive(state['pid'])

    # ------------------------------------------------------------------------------------------------ start/stop
    def start(self, config: str, marks: str) -> dict:
        if self.running():
            raise ReleaseError('already_running')
        now = self.current()
        if now is None:
            raise ReleaseError('not_installed')
        rid = now['current']
        self.verify(rid)
        if os.path.exists(self.stop_file):
            os.remove(self.stop_file)
        logs = os.path.join(self.root, 'logs')
        os.makedirs(logs, exist_ok=True)
        n = 1 + max([int(name[4:-6]) for name in os.listdir(logs)
                     if name.startswith('run-') and name.endswith('.jsonl') and name[4:-6].isdigit()] or [0])
        log = os.path.join(logs, f'run-{n:05d}.jsonl')
        argv, kw = self._cli(rid, 'run', '--config', os.path.abspath(config), '--marks', os.path.abspath(marks),
                             '--stop-file', self.stop_file)
        if os.name == 'nt':  # pragma: no cover - Windows: the run outlives the console that started it
            kw['creationflags'] = 0x00000008 | 0x00000200          # DETACHED_PROCESS | CREATE_NEW_PROCESS_GROUP
        handle = open(log, 'w', encoding='utf-8')
        try:
            proc = self._popen(argv, stdout=handle, stderr=subprocess.STDOUT, stdin=subprocess.DEVNULL, **kw)
        finally:
            handle.close()
        _write_json(self.run_file, {'schema': SCHEMA, 'pid': proc.pid, 'release': rid, 'log': log,
                                    'config': os.path.abspath(config), 'marks': os.path.abspath(marks)})
        self._proc = proc
        return {'status': 'started', 'release': rid, 'pid': proc.pid, 'log': log}

    def stop(self, timeout_s: float = 60.0) -> dict:
        state = self._run_state()
        if state is None or not self.running():
            return {'status': 'not_running'}
        with open(self.stop_file, 'w', encoding='utf-8') as handle:
            handle.write('stop\n')
        deadline = self._mono() + timeout_s
        while self._mono() < deadline:
            proc = getattr(self, '_proc', None)
            if proc is not None:
                proc.poll()
            if not self.running():
                state['ended'] = True
                _write_json(self.run_file, state)
                os.remove(self.stop_file)
                return {'status': 'stopped', 'last': self.last_line()}
            self._sleep(0.2)
        raise ReleaseError('stop_timeout')     # the stop file stays: the run still stops on its next step

    def restart(self, config: str | None = None, marks: str | None = None) -> dict:
        state = self._run_state()
        config = config or (state or {}).get('config')
        marks = marks or (state or {}).get('marks')
        if not config or not marks:
            raise ReleaseError('config_required')
        stopped = self.stop()
        started = self.start(config, marks)
        return {'status': 'restarted', 'stop': stopped['status'], 'start': started}

    def rollback(self) -> dict:
        now = self.current()
        if now is None or not now.get('previous'):
            raise ReleaseError('no_previous_release')
        self.verify(now['previous'])
        was_running = self.running()
        _write_json(self.pointer, {'schema': SCHEMA, 'current': now['previous'], 'previous': now['current']})
        out = {'status': 'rolled_back', 'release': now['previous'], 'replaced': now['current']}
        if was_running:
            out['restart'] = self.restart()
        return out

    def status(self) -> dict:
        return {'status': 'ok', 'pointer': self.current(), 'running': self.running(), 'last': self.last_line()}


def _alive(pid: int) -> bool:
    if type(pid) is not int or pid <= 0:
        return False
    if os.name == 'nt':  # pragma: no cover - Windows
        import ctypes
        kernel = ctypes.windll.kernel32
        handle = kernel.OpenProcess(0x1000, False, pid)        # PROCESS_QUERY_LIMITED_INFORMATION
        if not handle:
            return False
        code = ctypes.c_ulong()
        try:
            return bool(kernel.GetExitCodeProcess(handle, ctypes.byref(code))) and code.value == 259  # STILL_ACTIVE
        finally:
            kernel.CloseHandle(handle)
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    try:  # a zombie child we started counts as gone
        done, _status = os.waitpid(pid, os.WNOHANG)
        return done == 0
    except ChildProcessError:
        return True


def main(argv=None, *, out=None, releases=None) -> int:
    out = sys.stdout if out is None else out
    parser = argparse.ArgumentParser(prog="tools/newcore_release.py")
    parser.add_argument('command', choices=('install', 'start', 'stop', 'restart', 'rollback', 'status'))
    parser.add_argument('--root', required=True)
    parser.add_argument('--source')
    parser.add_argument('--config')
    parser.add_argument('--marks')
    parser.add_argument('--timeout-s', type=float, default=60.0)
    try:
        args = parser.parse_args(argv)
    except SystemExit:
        return EXIT_USAGE
    rel = releases or Releases(args.root)
    try:
        if args.command == 'install':
            if not args.source or not args.config:
                return EXIT_USAGE
            result = rel.install(args.source, args.config)
        elif args.command == 'start':
            if not args.config or not args.marks:
                return EXIT_USAGE
            result = rel.start(args.config, args.marks)
        elif args.command == 'stop':
            result = rel.stop(args.timeout_s)
        elif args.command == 'restart':
            result = rel.restart(args.config, args.marks)
        elif args.command == 'rollback':
            result = rel.rollback()
        else:
            result = rel.status()
    except ReleaseError as ex:
        out.write(json.dumps({'status': 'failed', 'code': ex.code}) + '\n')
        return EXIT_FAILED
    out.write(json.dumps(result, sort_keys=True) + '\n')
    return EXIT_OK


if __name__ == '__main__':  # pragma: no cover
    raise SystemExit(main())


__all__ = ['Releases', 'ReleaseError', 'main', 'manifest_of', 'release_id']
