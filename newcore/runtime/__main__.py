"""RT-01 entrypoint CLI.

* ``check --config <file>`` validates a runtime configuration and prints the boot plan as JSON.  It is read-only: it
  opens no journal, takes no lock, reads no credential and touches no network.
* ``init-paper --config <file>`` creates each account's journal and journals a confirmed SIM (paper) binding.
* ``run --config <file> --marks <file>`` runs the PAPER runtime (see ``newcore.runtime.paper_run``) and prints one JSON
  status line per ``--status-every`` steps.  A testnet config and ``entries_enabled`` are refused for now.

Every command refuses to start while a live/mainnet switch variable is present in the environment.
"""
from __future__ import annotations

import argparse
import json
import os
import sys

from newcore.runtime.config import RuntimeConfigError, RuntimeEnvironment, config_from_mapping

EXIT_OK, EXIT_REFUSED, EXIT_USAGE = 0, 2, 64
COMMANDS = ('check', 'init-paper', 'run')
EXIT_HELD = 3
REFUSED_ENVIRONMENT = ('LIVE_CONFIRM', 'ZACKBOT_LIVE', 'ZACKBOT_MAINNET')


def _local_dir_exists(path: str):
    """None for a UNC/network path: probing it would leave this machine."""
    if path.startswith(('\\\\', '//')):
        return None
    return os.path.isdir(path)


def _plan(config) -> dict:
    return {
        'schema': 'zackbot.newcore.runtime.boot_plan.v1',
        'environment': config.environment.value,
        'entries_enabled': config.entries_enabled,
        'accounts': [{'account_id': row.account_id, 'aggregate_id': row.aggregate_id,
                      'account_dir': row.account_dir, 'egress_id': row.egress_id,
                      'account_dir_exists': _local_dir_exists(row.account_dir)} for row in config.accounts],
        'boot_steps': ['nc02_recover_journal', 'durable_state_projection', 'nc04_read_only_evidence',
                       'rec02_plan_reconciliation', 'nc05_scheduler'],
        'order_route': 'none (RefusingActionSink)',
    }


def _refuse(out, code, **extra) -> int:
    out.write(json.dumps({'status': 'refused', 'code': code, **extra}) + '\n')
    return EXIT_REFUSED


def main(argv=None, *, environ=None, out=None, run_hooks=None) -> int:
    environ = os.environ if environ is None else environ
    out = sys.stdout if out is None else out
    parser = argparse.ArgumentParser(prog='python -m newcore.runtime')
    sub = parser.add_subparsers(dest='command')
    check = sub.add_parser('check', help='validate a runtime config and print the boot plan (read-only)')
    check.add_argument('--config', required=True)
    init = sub.add_parser('init-paper', help='create journals and journal a confirmed paper (SIM) binding')
    init.add_argument('--config', required=True)
    run = sub.add_parser('run', help='run the PAPER runtime against a marks file')
    run.add_argument('--config', required=True)
    run.add_argument('--marks', required=True)
    run.add_argument('--step-ms', type=int, default=250)
    run.add_argument('--max-steps', type=int, default=None)
    run.add_argument('--stop-file', default=None)
    run.add_argument('--entry-disable-file', default=None)
    run.add_argument('--status-every', type=int, default=20)
    try:
        args = parser.parse_args(argv)
    except SystemExit:
        return EXIT_USAGE
    if args.command not in COMMANDS:
        parser.print_usage(out)
        return EXIT_USAGE
    refused = {name.casefold() for name in REFUSED_ENVIRONMENT}
    present = sorted(name for name in environ if name.casefold() in refused)  # presence, any case, even empty
    if present:
        return _refuse(out, 'live_switch_present', variables=present)
    try:
        with open(args.config, encoding='utf-8') as handle:
            raw = json.load(handle)
        config = config_from_mapping(raw)
    except RuntimeConfigError as ex:
        return _refuse(out, ex.code)
    except Exception:  # unreadable, malformed or pathologically nested JSON
        return _refuse(out, 'config_unreadable')
    if args.command == 'check':
        out.write(json.dumps({'status': 'ok', 'plan': _plan(config)}, sort_keys=True) + '\n')
        return EXIT_OK
    from newcore.runtime import paper_run
    if config.environment is not RuntimeEnvironment.PAPER_SIM:
        return _refuse(out, 'testnet_run_unavailable' if args.command == 'run' else 'paper_only')
    if args.command == 'init-paper':
        rows = []
        for spec in config.accounts:
            try:
                rows.append({'account_id': spec.account_id,
                             'result': paper_run.init_paper_account(spec, wall_ms=paper_run._wall_ms())})
            except paper_run.PaperRunError as ex:
                rows.append({'account_id': spec.account_id, 'result': ex.code})
        ok = all(r['result'] == 'bound' for r in rows)
        out.write(json.dumps({'status': 'ok' if ok else 'partial', 'accounts': rows}, sort_keys=True) + '\n')
        return EXIT_OK if ok else EXIT_REFUSED
    if args.status_every < 1:
        return EXIT_USAGE

    def emit(line):
        out.write(json.dumps(line, sort_keys=True) + '\n')
        out.flush()

    try:
        final = paper_run.run_paper(config, marks_path=args.marks, emit=emit, step_ms=args.step_ms,
                                    max_steps=args.max_steps, stop_file=args.stop_file,
                                    entry_disable_file=args.entry_disable_file,
                                    status_every=args.status_every, **(run_hooks or {}))
    except paper_run.PaperRunError as ex:
        return _refuse(out, ex.code)
    return EXIT_OK if all(not a['holds'] for a in final['accounts']) else EXIT_HELD


if __name__ == '__main__':  # pragma: no cover
    raise SystemExit(main())
