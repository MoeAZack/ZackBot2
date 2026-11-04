"""RT-01 entrypoint CLI.

``python -m newcore.runtime check --config <file>`` validates a runtime configuration and prints the boot plan as
JSON.  It is read-only: it opens no journal, takes no lock, reads no credential and touches no network.  RT-01 has no
``run`` command; the PAPER/testnet install ticket adds it with concrete venue and evidence adapters.
"""
from __future__ import annotations

import argparse
import json
import os
import sys

from newcore.runtime.config import RuntimeConfigError, config_from_mapping

EXIT_OK, EXIT_REFUSED, EXIT_USAGE = 0, 2, 64
REFUSED_ENVIRONMENT = ('LIVE_CONFIRM', 'ZACKBOT_LIVE', 'ZACKBOT_MAINNET')


def _plan(config) -> dict:
    return {
        'schema': 'zackbot.newcore.runtime.boot_plan.v1',
        'environment': config.environment.value,
        'entries_enabled': config.entries_enabled,
        'accounts': [{'account_id': row.account_id, 'aggregate_id': row.aggregate_id,
                      'account_dir': row.account_dir, 'egress_id': row.egress_id,
                      'account_dir_exists': os.path.isdir(row.account_dir)} for row in config.accounts],
        'boot_steps': ['nc02_recover_journal', 'durable_state_projection', 'nc04_read_only_evidence',
                       'rec02_plan_reconciliation', 'nc05_scheduler'],
        'order_route': 'none (RefusingActionSink)',
    }


def main(argv=None, *, environ=None, out=None) -> int:
    environ = os.environ if environ is None else environ
    out = sys.stdout if out is None else out
    parser = argparse.ArgumentParser(prog='python -m newcore.runtime')
    sub = parser.add_subparsers(dest='command')
    check = sub.add_parser('check', help='validate a runtime config and print the boot plan (read-only)')
    check.add_argument('--config', required=True)
    try:
        args = parser.parse_args(argv)
    except SystemExit:
        return EXIT_USAGE
    if args.command != 'check':
        parser.print_usage(out)
        return EXIT_USAGE
    present = sorted(name for name in REFUSED_ENVIRONMENT if environ.get(name))
    if present:
        out.write(json.dumps({'status': 'refused', 'code': 'live_switch_present', 'variables': present}) + '\n')
        return EXIT_REFUSED
    try:
        with open(args.config, encoding='utf-8') as handle:
            raw = json.load(handle)
        config = config_from_mapping(raw)
    except RuntimeConfigError as ex:
        out.write(json.dumps({'status': 'refused', 'code': ex.code}) + '\n')
        return EXIT_REFUSED
    except (OSError, ValueError):
        out.write(json.dumps({'status': 'refused', 'code': 'config_unreadable'}) + '\n')
        return EXIT_REFUSED
    out.write(json.dumps({'status': 'ok', 'plan': _plan(config)}, sort_keys=True) + '\n')
    return EXIT_OK


if __name__ == '__main__':  # pragma: no cover
    raise SystemExit(main())
