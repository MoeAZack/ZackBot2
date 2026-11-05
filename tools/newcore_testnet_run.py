"""NEWCORE runtime on Binance USD-M TESTNET: ``init`` and ``run`` for one dedicated testnet account.

``python tools/newcore_testnet_run.py init --config <cfg> --account-file <file>``
    Journals the account file's CONFIRMED testnet binding (the owner typed ``BIND <account_id> <key_digest>`` when
    the file was made, see TNET-02 ``tools/tnet02_run.py``) into the account's NC-02 journal.  Re-running with the
    same binding is a no-op; a different binding is refused.  No network, no credentials.

``python tools/newcore_testnet_run.py run --config <cfg> --account-file <file> --symbols BTCUSDT[,ETHUSDT...]``
    Builds the real testnet venue (``newcore.tnet02.live.make_testnet_venue`` over ``TestnetHttpSender``; keys only
    from ``ZACKBOT_TNET02_API_KEY`` / ``ZACKBOT_TNET02_API_SECRET`` on the owner's machine), checks the key digest
    against the journaled binding, pins the key to its origin, reads the NC-04 limit payloads, and then drives the
    NEWCORE runtime (journal projection, venue evidence, journaling sink, REC-02) exactly like the PAPER run.

Testnet only: the runtime config must say ``testnet``; a live/mainnet switch variable, more than one account,
``entries_enabled`` without an entries factory (Python API only), a digest that does not match the binding, or an
origin outside the two approved testnet origins is refused before any request is sent.

This tool lives outside ``newcore/runtime`` on purpose: the runtime package may not import ``newcore.tnet02`` or the
HTTP sender.  It needs TNET-02 (PR #294, which stacks on #291).
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import time
import uuid
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from newcore.runtime.__main__ import REFUSED_ENVIRONMENT  # noqa: E402
from newcore.runtime.config import RuntimeConfigError, RuntimeEnvironment, config_from_mapping  # noqa: E402

EXIT_OK, EXIT_REFUSED, EXIT_HELD, EXIT_USAGE = 0, 2, 3, 64


class TestnetRunError(Exception):
    __test__ = False

    def __init__(self, code: str):
        self.code = code
        super().__init__(code)


def _tnet02():
    """TNET-02 helpers (PR #294).  Imported late so this file can be read and its refusals tested without it."""
    try:
        if str(HERE) not in sys.path:
            sys.path.insert(0, str(HERE))
        import tnet02_run
        from newcore.tnet02 import live
        from newcore.venue import TestnetHttpSender
    except ImportError:
        raise TestnetRunError('tnet02_unavailable') from None
    return tnet02_run, live, TestnetHttpSender


def _mono_ms() -> int:
    return time.monotonic_ns() // 1_000_000


def _wall_ms() -> int:
    return time.time_ns() // 1_000_000


def _one_account(config):
    if config.environment is not RuntimeEnvironment.TESTNET:
        raise TestnetRunError('testnet_only')
    if len(config.accounts) != 1:
        raise TestnetRunError('one_account_only')     # one dedicated testnet account per run
    return config.accounts[0]


def _load_account(path, spec):
    tnet02_run, _live, _sender = _tnet02()
    try:
        account = tnet02_run.load_account(Path(path))
        origin = tnet02_run.account_origin(Path(path))
    except tnet02_run.Refused as ex:
        raise TestnetRunError(ex.code) from None
    if account.account_id != spec.account_id:
        raise TestnetRunError('account_file_account_mismatch')
    return account, origin


# ---------------------------------------------------------------------------------------------------- init
def init_testnet(config, account_file: str, *, event_n: int | None = None) -> str:
    """Journal the confirmed testnet binding.  Returns 'bound' or 'already_bound'; raises TestnetRunError."""
    from newcore.domain import BindingChanged, ReasonCode, make_id
    from newcore.domain.account import BindingState
    from newcore.store import create_journal, recover_journal
    from newcore.store.errors import JournalExists

    spec = _one_account(config)
    account, _origin = _load_account(account_file, spec)
    try:
        create_journal(spec.account_dir, spec.account_id, spec.aggregate_id).close()
    except JournalExists:
        pass
    journal = recover_journal(spec.account_dir, spec.account_id, spec.aggregate_id).journal
    if journal is None:
        raise TestnetRunError('journal_unusable')
    try:
        bound = [ev for ev in journal.read() if isinstance(ev, BindingChanged)]
        if bound:
            last = bound[-1]
            if last.to_state is BindingState.CONFIRMED and last.binding == account.binding:
                return 'already_bound'
            raise TestnetRunError('already_bound_differently')
        journal.append(BindingChanged(
            event_id=make_id('evt', uuid.uuid4().int if event_n is None else event_n), account_id=spec.account_id,
            aggregate_id=spec.aggregate_id, sequence=journal.last_sequence() + 1,
            at_ms=max(_wall_ms(), account.confirmation.confirmed_at_ms), reason=ReasonCode.BINDING_UNCONFIRMED,
            from_state=BindingState.UNCONFIRMED, to_state=BindingState.CONFIRMED, binding=account.binding,
            confirmation=account.confirmation, reconciliation_id=None))
        return 'bound'
    finally:
        journal.close()


# ---------------------------------------------------------------------------------------------------- run
def run_testnet(config, account_file: str, *, symbols: tuple[str, ...], emit, step_ms: int = 500,
                max_steps: int | None = None, stop_file: str | None = None, status_every: int = 20,
                entries_factory=None, environ=None, sender_factory=None, sleep=time.sleep,
                monotonic_clock=_mono_ms, wall_clock=_wall_ms, boot_id: str | None = None) -> dict:
    from newcore.account_context import AccountBudget, AccountContext
    from newcore.runtime import JournalingActionSink, JournalProjection, VenueEvidence
    from newcore.runtime.paper_run import JournalView, _entries, attach_views, drive
    from newcore.runtime.ports import AccountPorts
    from newcore.runtime.runtime import boot

    spec = _one_account(config)
    if config.entries_enabled and entries_factory is None:
        raise TestnetRunError('entries_source_missing')
    if entries_factory is not None and not callable(entries_factory):
        raise TestnetRunError('entries_factory_invalid')
    if type(step_ms) is not int or not 100 <= step_ms <= 60_000:
        raise TestnetRunError('step_ms_invalid')
    if max_steps is not None and (type(max_steps) is not int or max_steps < 1):
        raise TestnetRunError('max_steps_invalid')
    if not os.path.isdir(spec.account_dir):
        raise TestnetRunError('account_not_initialised')
    try:
        evidence = VenueEvidence(symbols=tuple(symbols))
    except Exception:
        raise TestnetRunError('symbols_invalid') from None
    tnet02_run, live, sender_type = _tnet02()
    account, origin = _load_account(account_file, spec)
    sender = (sender_factory or sender_type)(origin)
    observers = []

    def observe(evidence_row):
        for fn in observers:
            fn(evidence_row)

    try:
        venue, digest = live.make_testnet_venue(origin=origin, sender=sender, clock=wall_clock, environ=environ,
                                                rate_observer=observe)
    except Exception as ex:
        raise TestnetRunError(getattr(ex, 'code', None) or 'venue_unavailable') from None
    if digest != account.binding.key_digest:
        raise TestnetRunError('key_digest_mismatch')       # the keys in the environment are not the bound account
    try:
        tnet02_run.pin_origin(Path(spec.account_dir), digest, origin)   # before any network use
    except tnet02_run.Refused as ex:
        raise TestnetRunError(ex.code) from None
    reader = live.SignedReader(sender=sender, credentials=live.EnvCredentialSource(environ), clock=wall_clock,
                               base_url=origin)
    try:
        exchange, limits, _info, _raw = live.read_limit_payloads(reader)
    except Exception as ex:
        raise TestnetRunError(getattr(ex, 'code', None) or 'limits_unavailable') from None
    budget = AccountBudget(account_id=spec.account_id, egress_id=spec.egress_id, exchange_info=exchange,
                           account_order_limits=limits, monotonic_clock=monotonic_clock, wall_clock=wall_clock)
    observers.append(budget.observe)
    context = AccountContext(account_id=spec.account_id, egress_id=spec.egress_id, venue=venue, budget=budget,
                             monotonic_clock=monotonic_clock)
    views: dict[str, JournalView] = {}
    extra = {} if entries_factory is None else {'entries': _entries(entries_factory, spec, wall_clock, views)}
    ports = {spec.account_id: AccountPorts(context=context, state_source=JournalProjection(label=account.label),
                                           evidence=evidence, actions=JournalingActionSink(), **extra)}
    runtime = boot(config, ports, boot_id=boot_id or f'testnet-{uuid.uuid4().hex[:12]}',
                   monotonic_clock=monotonic_clock, wall_clock=wall_clock)
    attach_views(runtime, views)
    return drive(runtime, config, emit=emit, step_ms=step_ms, max_steps=max_steps, stop_file=stop_file,
                 status_every=status_every, sleep=sleep, monotonic_clock=monotonic_clock)


# ---------------------------------------------------------------------------------------------------- CLI
def main(argv=None, *, environ=None, out=None, run_hooks=None) -> int:
    environ = os.environ if environ is None else environ
    out = sys.stdout if out is None else out
    parser = argparse.ArgumentParser(prog='tools/newcore_testnet_run.py')
    sub = parser.add_subparsers(dest='command')
    for name in ('init', 'run'):
        cmd = sub.add_parser(name)
        cmd.add_argument('--config', required=True)
        cmd.add_argument('--account-file', required=True)
        if name == 'run':
            cmd.add_argument('--symbols', required=True)
            cmd.add_argument('--step-ms', type=int, default=500)
            cmd.add_argument('--max-steps', type=int, default=None)
            cmd.add_argument('--stop-file', default=None)
            cmd.add_argument('--status-every', type=int, default=20)
    try:
        args = parser.parse_args(argv)
    except SystemExit:
        return EXIT_USAGE
    if args.command not in ('init', 'run'):
        return EXIT_USAGE

    def refuse(code, **extra):
        out.write(json.dumps({'status': 'refused', 'code': code, **extra}) + '\n')
        return EXIT_REFUSED

    refused = {name.casefold() for name in REFUSED_ENVIRONMENT}
    present = sorted(name for name in environ if name.casefold() in refused)
    if present:
        return refuse('live_switch_present', variables=present)
    try:
        with open(args.config, encoding='utf-8') as handle:
            config = config_from_mapping(json.load(handle))
    except RuntimeConfigError as ex:
        return refuse(ex.code)
    except Exception:
        return refuse('config_unreadable')
    try:
        if args.command == 'init':
            result = init_testnet(config, args.account_file)
            out.write(json.dumps({'status': 'ok', 'result': result}) + '\n')
            return EXIT_OK
        if args.status_every < 1:
            return EXIT_USAGE

        def emit(line):
            out.write(json.dumps(line, sort_keys=True) + '\n')
            out.flush()

        final = run_testnet(config, args.account_file, symbols=tuple(s for s in args.symbols.split(',') if s),
                            emit=emit, step_ms=args.step_ms, max_steps=args.max_steps, stop_file=args.stop_file,
                            status_every=args.status_every, environ=environ, **(run_hooks or {}))
    except TestnetRunError as ex:
        return refuse(ex.code)
    return EXIT_OK if all(not a['holds'] for a in final['accounts']) else EXIT_HELD


if __name__ == '__main__':  # pragma: no cover
    raise SystemExit(main())


__all__ = ['TestnetRunError', 'init_testnet', 'main', 'run_testnet']
