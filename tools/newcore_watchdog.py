"""Watchdog, kill switch and restart for a long NEWCORE run (PAPER or TESTNET).

``python tools/newcore_watchdog.py watch --root <dir> -- <run command ...>``

The run command is any NEWCORE run that writes JSON status lines and takes ``--stop-file`` and
``--entry-disable-file``, for example
``python tools/newcore_testnet_run.py run --config ... --account-file ... --symbols BTCUSDT`` or
``python -m newcore.runtime run --config ... --marks ...``.  The watchdog appends ``--stop-file <root>/STOP`` itself.

Policy:

* **Entry kill switch.** ``kill`` creates ``<root>/DISABLE_ENTRIES``. The runtime latches entries off and keeps
  guards, management, action polling and stop-fill reconciliation running in the same process. Removing the file
  cannot clear an in-process latch; restarts also inherit a marker that remains present.
* **Explicit full stop.** ``stop`` or ``<root>/STOP`` shuts the process down. ``watch --clear-stop`` clears only
  this full-stop marker, never the entry-disable latch.
* **Restart on crash or hang.** A run that dies with any exit code other than the ones below, or makes no valid,
  advancing runtime-status progress
  line for ``--stall-s`` seconds (``--startup-grace-s`` before its first status line; it is then terminated), is restarted after a backoff (5 s, doubling, at most
  300 s).  Boot recovery and the REC-02 restore proof make a restart safe; a run that cannot prove its account
  refuses or holds.  At most ``--max-restarts`` restarts per hour; past that the watchdog gives up.
* **Never restart on a held or unknown account.** Exit 0 (clean stop), 2 (refused), 3 (held) and 64 (usage) end
  the watch.  A crash whose last status line showed a hold that only a restart clears (anything outside the
  runtime's clearable evidence holds) also ends the watch: unknown account state stops for a human.

Files under ``<root>``: ``logs/run-<n>.jsonl`` (the run's own output), ``watchdog.jsonl`` (one line per watchdog
event) and ``watchdog.json`` (the heartbeat: run number, restarts, steps since the last boot, last status, time of
the last line).  The run inherits the environment unchanged (testnet keys come only from it); nothing here writes
the command line or any environment value to a file.  This module never reads a credential, opens a socket
or touches the venue.  All text I/O is utf-8.
"""
from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import time

SCHEMA = 'zackbot.newcore.watchdog.v1'
RUNTIME_STATUS_SCHEMA = 'zackbot.newcore.runtime.paper_status.v1'
EXIT_OK, EXIT_GAVE_UP, EXIT_HELD, EXIT_USAGE = 0, 1, 3, 64
NO_RESTART_EXITS = {0: 'stopped', 2: 'refused', 3: 'held', 64: 'usage'}
# Mirror of newcore.runtime.runtime.CLEARABLE: holds a later confirmed proof clears without a restart.
CLEARABLE_HOLDS = frozenset({'evidence_unavailable', 'evidence_timeout', 'evidence_invalid', 'reconcile_error',
                             'reconcile_hold'})
BACKOFF_FIRST_S, BACKOFF_MAX_S, RESTART_WINDOW_S = 5.0, 300.0, 3600.0


class WatchdogError(RuntimeError):
    def __init__(self, code: str):
        super().__init__(code)
        self.code = code


def _write_json(path: str, value) -> None:
    tmp = path + '.tmp'
    with open(tmp, 'w', encoding='utf-8') as handle:
        json.dump(value, handle, sort_keys=True)
    os.replace(tmp, path)


def _valid_status(value) -> bool:
    return (type(value) is dict and value.get('schema') == RUNTIME_STATUS_SCHEMA
            and value.get('status') in ('booted', 'running', 'stopped')
            and type(value.get('step')) is int and value['step'] >= 0
            and value.get('environment') in ('paper_sim', 'testnet')
            and type(value.get('entries_enabled')) is bool
            and type(value.get('accounts')) is list
            and all(type(account) is dict and type(account.get('account_id')) is str
                    and bool(account['account_id']) and type(account.get('holds')) is list
                    and all(type(hold) is str for hold in account['holds']) for account in value['accounts']))


def _last_status(log: str) -> dict | None:
    last = None
    try:
        with open(log, encoding='utf-8', errors='replace') as handle:
            for line in handle:
                line = line.strip()
                if line.startswith('{'):
                    try:
                        value = json.loads(line)
                    except ValueError:
                        continue
                    if _valid_status(value):
                        last = value
    except OSError:
        return None
    return last


def sticky_holds(status: dict | None) -> list[str]:
    """Holds in a status line that a restart would be needed to clear (and so must not be auto-restarted)."""
    if type(status) is not dict:
        return []
    out = []
    for account in status.get('accounts') or ():
        for hold in (account or {}).get('holds') or ():
            if hold not in CLEARABLE_HOLDS:
                out.append(f"{account.get('account_id')}:{hold}")
    return out


class Watchdog:
    def __init__(self, root: str, command: list[str], *, stall_s: float = 300.0, max_restarts: int = 5,
                 startup_grace_s: float = 300.0,
                 popen=subprocess.Popen, sleep=time.sleep, monotonic=time.monotonic, poll_s: float = 1.0,
                 terminate_grace_s: float = 30.0):
        if not command:
            raise WatchdogError('command_required')
        if '--stop-file' in command or '--entry-disable-file' in command:
            raise WatchdogError('stop_file_is_set_by_the_watchdog')
        if not stall_s > 0 or type(max_restarts) is not int or max_restarts < 0:
            raise WatchdogError('limits_invalid')
        self.root = os.path.abspath(root)
        self.command = list(command)
        self.stall_s, self.max_restarts = float(stall_s), max_restarts
        self.startup_grace_s = max(float(startup_grace_s), self.stall_s)
        self._popen, self._sleep, self._mono = popen, sleep, monotonic
        self.poll_s, self.terminate_grace_s = poll_s, terminate_grace_s

    @property
    def stop_file(self) -> str:
        return os.path.join(self.root, 'STOP')

    @property
    def entry_disable_file(self) -> str:
        return os.path.join(self.root, 'DISABLE_ENTRIES')

    @property
    def state_file(self) -> str:
        return os.path.join(self.root, 'watchdog.json')

    def _event(self, kind: str, **fields) -> None:
        line = {'schema': SCHEMA, 'event': kind, 'at_wall_ms': int(time.time() * 1000), **fields}
        with open(os.path.join(self.root, 'watchdog.jsonl'), 'a', encoding='utf-8') as handle:
            handle.write(json.dumps(line, sort_keys=True) + '\n')

    def _state(self, **fields) -> None:
        _write_json(self.state_file, {'schema': SCHEMA, 'pid': os.getpid(), **fields})

    def _next_log(self) -> str:
        logs = os.path.join(self.root, 'logs')
        os.makedirs(logs, exist_ok=True)
        n = 1 + max([int(name[4:-6]) for name in os.listdir(logs)
                     if name.startswith('run-') and name.endswith('.jsonl') and name[4:-6].isdigit()] or [0])
        return os.path.join(logs, f'run-{n:05d}.jsonl')

    def _spawn(self, log: str):
        env = dict(os.environ)
        env['PYTHONIOENCODING'] = 'utf-8'
        kw = {}
        if os.name == 'nt':  # pragma: no cover - Windows: own process group, so a console Ctrl-C hits the watchdog
            kw['creationflags'] = 0x00000200                   # CREATE_NEW_PROCESS_GROUP
        handle = open(log, 'w', encoding='utf-8')
        try:
            return self._popen([*self.command, '--stop-file', self.stop_file,
                                '--entry-disable-file', self.entry_disable_file], stdout=handle,
                               stderr=subprocess.STDOUT, stdin=subprocess.DEVNULL, env=env, **kw)
        finally:
            handle.close()

    def _end(self, proc) -> int:
        """Ask the run to stop through the stop file's semantics, then terminate it if it will not."""
        deadline = self._mono() + self.terminate_grace_s
        while proc.poll() is None and self._mono() < deadline:
            self._sleep(self.poll_s)
        if proc.poll() is None:
            proc.terminate()
            try:
                proc.wait(timeout=self.terminate_grace_s)
            except subprocess.TimeoutExpired:
                proc.kill()
                proc.wait()
        return proc.returncode

    def _supervise(self, proc, log: str, beat: dict) -> tuple[str, int | None]:
        """Wait for one run.  Returns (how it ended, exit code).  Until its first status line a run gets the
        startup grace (it may fetch candles first); after that, ``stall_s`` of silence is a hang."""
        size, quiet_since, booted = -1, self._mono(), False
        highest_step, observed = -1, None
        while True:
            code = proc.poll()
            if code is not None:
                return 'exited', code
            if os.path.exists(self.stop_file):
                return 'killed', self._end(proc)
            try:
                now_size = os.path.getsize(log)
            except OSError:
                now_size = size
            if now_size != size:
                size = now_size
                last = _last_status(log)
                if last is not None:
                    if last['step'] > highest_step:
                        highest_step, quiet_since, booted = last['step'], self._mono(), True
                    if last != observed:
                        observed = last
                        self._state(**beat, status='running', steps_since_boot=last['step'],
                                    entries_enabled=last['entries_enabled'], last_status=last['status'],
                                    last_line_wall_ms=int(time.time() * 1000))
            if self._mono() - quiet_since >= (self.stall_s if booted else self.startup_grace_s):
                # Hung: write the stop file's effect without leaving it behind, then force it.
                proc.terminate()
                try:
                    proc.wait(timeout=self.terminate_grace_s)
                except subprocess.TimeoutExpired:
                    proc.kill()
                    proc.wait()
                return 'stalled', proc.returncode
            self._sleep(self.poll_s)

    def watch(self, *, clear_stop: bool = False) -> dict:
        os.makedirs(self.root, exist_ok=True)
        if os.path.exists(self.stop_file):
            if not clear_stop:
                raise WatchdogError('stop_file_present')
            os.remove(self.stop_file)
            self._event('stop_cleared')
        restarts: list[float] = []
        backoff = BACKOFF_FIRST_S
        run = 0
        while True:
            run += 1
            log = self._next_log()
            proc = self._spawn(log)
            self._event('started', run=run, child_pid=proc.pid, log=log)
            beat = dict(run=run, child_pid=proc.pid, log=log, restarts=len(restarts))
            self._state(**beat, status='running', steps_since_boot=None)
            started = self._mono()
            how, code = self._supervise(proc, log, beat)
            last = _last_status(log)
            self._event('ended', run=run, how=how, exit_code=code, last=last)
            if how == 'killed' or os.path.exists(self.stop_file):
                return self._finish('killed', run, code, last)
            if how == 'exited' and code in NO_RESTART_EXITS:
                return self._finish(NO_RESTART_EXITS[code], run, code, last)
            sticky = sticky_holds(last)
            if sticky:
                return self._finish('held_needs_human', run, code, last, holds=sticky)
            now = self._mono()
            if now - started >= RESTART_WINDOW_S:
                backoff = BACKOFF_FIRST_S                          # a long healthy run resets the backoff
            restarts = [t for t in restarts if now - t < RESTART_WINDOW_S]
            if len(restarts) >= self.max_restarts:
                return self._finish('gave_up', run, code, last, restarts=len(restarts))
            restarts.append(now)
            self._event('restarting', run=run, how=how, exit_code=code, backoff_s=backoff)
            self._state(status='backoff', run=run, backoff_s=backoff, restarts=len(restarts))
            deadline = self._mono() + backoff
            while self._mono() < deadline:
                if os.path.exists(self.stop_file):
                    return self._finish('killed', run, code, last)
                self._sleep(min(self.poll_s, max(0.0, deadline - self._mono())))
            backoff = min(backoff * 2, BACKOFF_MAX_S)

    def _finish(self, status: str, run: int, code, last, **extra) -> dict:
        out = {'status': status, 'runs': run, 'exit_code': code, 'last': last, **extra}
        self._event('finished', **{k: v for k, v in out.items() if k != 'last'})
        self._state(**{k: v for k, v in out.items() if k != 'last'})
        return out


def stop(root: str, *, timeout_s: float = 120.0, sleep=time.sleep, monotonic=time.monotonic) -> dict:
    """Explicit process shutdown, separate from the entry-only kill latch."""
    root = os.path.abspath(root)
    os.makedirs(root, exist_ok=True)
    with open(os.path.join(root, 'STOP'), 'w', encoding='utf-8') as handle:
        handle.write('stop\n')
    state_file = os.path.join(root, 'watchdog.json')
    deadline = monotonic() + timeout_s
    while monotonic() < deadline:
        try:
            with open(state_file, encoding='utf-8') as handle:
                state = json.load(handle)
        except (OSError, ValueError):
            state = None
        if state is None or state.get('status') not in ('running', 'backoff'):
            return {'status': 'killed', 'watchdog': state}
        sleep(0.5)
    return {'status': 'kill_pending', 'detail': 'stop file written; the run stops on its next step'}


def kill(root: str, *, timeout_s: float = 120.0, sleep=time.sleep, monotonic=time.monotonic) -> dict:
    """Latch new entries off; the child keeps managing and reconciling exposure."""
    root = os.path.abspath(root)
    os.makedirs(root, exist_ok=True)
    with open(os.path.join(root, 'DISABLE_ENTRIES'), 'w', encoding='utf-8') as handle:
        handle.write('disable entries\n')
        handle.flush()
        os.fsync(handle.fileno())
    deadline = monotonic()+timeout_s
    while monotonic() < deadline:
        try:
            with open(os.path.join(root, 'watchdog.json'), encoding='utf-8') as handle:
                state = json.load(handle)
        except (OSError, ValueError):
            state = None
        if state is None:
            return {'status': 'entry_disable_pending', 'detail': 'latch written; no runtime acknowledgement'}
        if state.get('status') not in ('running', 'backoff'):
            return {'status': 'entries_disabled', 'watchdog': state}
        if state.get('entries_enabled') is False:
            return {'status': 'entries_disabled', 'watchdog': state}
        sleep(min(0.5, max(0, deadline-monotonic())))
    return {'status': 'entry_disable_pending', 'detail': 'entry-disable latch written; protection continues'}


def status(root: str) -> dict:
    root = os.path.abspath(root)
    try:
        with open(os.path.join(root, 'watchdog.json'), encoding='utf-8') as handle:
            state = json.load(handle)
    except (OSError, ValueError):
        state = None
    log = (state or {}).get('log')
    return {'status': 'ok', 'watchdog': state, 'stop_file': os.path.exists(os.path.join(root, 'STOP')),
            'entries_disabled': os.path.lexists(os.path.join(root, 'DISABLE_ENTRIES')),
            'last': _last_status(log) if log else None}


def main(argv=None, *, out=None) -> int:
    out = sys.stdout if out is None else out
    argv = list(sys.argv[1:] if argv is None else argv)
    command = []
    if '--' in argv:
        cut = argv.index('--')
        argv, command = argv[:cut], argv[cut + 1:]
    parser = argparse.ArgumentParser(prog='tools/newcore_watchdog.py')
    parser.add_argument('action', choices=('watch', 'kill', 'stop', 'status'))
    parser.add_argument('--root', required=True)
    parser.add_argument('--stall-s', type=float, default=300.0)
    parser.add_argument('--max-restarts', type=int, default=5)
    parser.add_argument('--startup-grace-s', type=float, default=300.0)
    parser.add_argument('--clear-stop', action='store_true')
    parser.add_argument('--timeout-s', type=float, default=120.0)
    try:
        args = parser.parse_args(argv)
    except SystemExit:
        return EXIT_USAGE
    for name in ('ZACKBOT_LIVE', 'LIVE_CONFIRM', 'ZACKBOT_MAINNET'):
        if os.environ.get(name):
            out.write(json.dumps({'status': 'refused', 'code': 'live_switch_present'}) + '\n')
            return EXIT_USAGE
    try:
        if args.action == 'watch':
            result = Watchdog(args.root, command, stall_s=args.stall_s, max_restarts=args.max_restarts,
                              startup_grace_s=args.startup_grace_s).watch(clear_stop=args.clear_stop)
        elif args.action == 'kill':
            result = kill(args.root, timeout_s=args.timeout_s)
        elif args.action == 'stop':
            result = stop(args.root, timeout_s=args.timeout_s)
        else:
            result = status(args.root)
    except WatchdogError as ex:
        out.write(json.dumps({'status': 'refused', 'code': ex.code}) + '\n')
        return EXIT_USAGE
    out.write(json.dumps(result, sort_keys=True) + '\n')
    if args.action != 'watch':
        return EXIT_OK
    return {'stopped': EXIT_OK, 'killed': EXIT_OK, 'gave_up': EXIT_GAVE_UP}.get(result['status'], EXIT_HELD)


if __name__ == '__main__':  # pragma: no cover
    raise SystemExit(main())


__all__ = ['Watchdog', 'WatchdogError', 'kill', 'stop', 'main', 'status', 'sticky_holds', 'CLEARABLE_HOLDS']
