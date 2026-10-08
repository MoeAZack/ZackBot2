"""TNET-01 venue harness CLI (REC02_TNET01_BUILD_PLAN 6.x): preflight -> probes / scenario specs -> guarded cleanup ->
redacted exact-build report. Testnet only (the transport is hard-pinned). The OWNER runs it with the key stored by
tools/newcore_keys.py.

    python tools/newcore_tnet.py --probe P1 --dry-run       account / binding / symbols from the run config (READ only):
                                                            --config, default %LOCALAPPDATA%\\ZackBotNC\\config\\testnet.toml
    python tools/newcore_tnet.py --account-id <id> --probe P1 --dry-run        print the plan: NO network, no key read
    python tools/newcore_tnet.py --account-id <id> --probe P1 [--probe P2]
    python tools/newcore_tnet.py --account-id <id> --scenario spec.json [--adopt-foreign web_x --adopt-foreign SOLUSDT:SHORT]

Every order is minimum size. The cleanup (cancel NEWCORE orders, flatten, re-read) ALWAYS runs: after success, after a
failure, and on Ctrl+C. Report + sanitized cassette: %LOCALAPPDATA%\\ZackBotNC\\reports and \\cassettes.

Exit codes (plan 6.6): 0 all PASS, cleanup clean, report written | 1 INCONCLUSIVE (a probe did not converge), cleanup
clean | 2 usage / config refused (also a dirty tree with --gate) | 3 no usable credentials | 4 preflight refused (not flat
/ not hedge / foreign orders / reads not OK) | 5 report or cassette not written, or a leak | 6 run deadline exceeded or
Ctrl+C (cleanup ran, clean) | 7 a scenario FAILED or a probe aborted (cleanup clean) | 8 cleanup NOT clean: exposure or an
order of this run MAY be left (client ids printed).
"""
import argparse
import hashlib
import os
import sys
from decimal import Decimal, InvalidOperation

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, REPO)

from newcore.ports import venue as P  # noqa: E402
from newcore.venue.cassette import CassetteLeak, CassetteRecorder  # noqa: E402
from newcore.venue.cli_args import ACCOUNT_REFUSAL, argv_refusal  # noqa: E402
from newcore.venue.clock import OffsetClock, system_clock_ms  # noqa: E402
from newcore.venue.credentials import (CredentialsUnavailable, CredentialStore, CredentialStoreError,  # noqa: E402
                                       SecretScrubber, binding_digest, check_root)
from newcore.venue.outcomes import ReadKind as TR  # noqa: E402  (transport-level reads)
from newcore.venue.run_config import RunConfigError, default_testnet_config_path, load_testnet_config  # noqa: E402
from newcore.venue.smoke import CORE8  # noqa: E402
from newcore.venue.testnet_venue import TestnetAccountReader, TestnetVenue  # noqa: E402
from newcore.venue.tnet import (ReportLeak, default_report_dir, format_cleanup, git_build, guarded,  # noqa: E402
                                tnet_cleanup, tnet_preflight, tnet_report)
from newcore.venue.tnet_exec import run_spec  # noqa: E402
from newcore.venue.tnet_probes import ProbeAborted, probe_p1, probe_p2  # noqa: E402
from newcore.venue.tnet_seams import DeadlineExceeded, DeadlinePort, FaultHttp, RunDeadline  # noqa: E402
from newcore.venue.tnet_spec import SpecError, load_spec, spec_digest  # noqa: E402
from newcore.venue.transport import BinanceTestnetTransport, PositionMode  # noqa: E402

EXIT_PASS, EXIT_INCONCLUSIVE, EXIT_USAGE, EXIT_CREDS, EXIT_PREFLIGHT = 0, 1, 2, 3, 4
EXIT_REPORT, EXIT_DEADLINE, EXIT_FAIL, EXIT_RESIDUE = 5, 6, 7, 8


def _cassette_dir():
    base = os.environ.get('LOCALAPPDATA')
    if not base:
        raise CredentialStoreError('LOCALAPPDATA is not set: pass --cassette-dir')
    return os.path.join(base, 'ZackBotNC', 'cassettes')


def _parser():
    p = argparse.ArgumentParser(prog='newcore_tnet', description='TNET-01 venue harness (testnet only).')
    p.add_argument('--account-id', default=None, help='default: the --config account')
    p.add_argument('--config', default=None,
                   help='TESTNET run config, READ only (never written): account, binding digest (checked against the '
                        'stored key) and symbols. Default %%LOCALAPPDATA%%\\ZackBotNC\\config\\testnet.toml when it '
                        'exists and no --account-id is given')
    p.add_argument('--root', default=None, help='credential folder (default %%LOCALAPPDATA%%\\ZackBotNC\\secrets)')
    p.add_argument('--cassette-dir', default=None)
    p.add_argument('--report-dir', default=None)
    p.add_argument('--dry-run', action='store_true', help='print the plan only: no network call, no key decrypted')
    p.add_argument('--probe', action='append', default=[], choices=('P1', 'P2'))
    p.add_argument('--scenario', action='append', default=[], help='a zb-newcore-tnet-scenario/1 JSON file')
    p.add_argument('--adopt-foreign', action='append', default=[],
                   help='a foreign client id or SYMBOL:SIDE position the preflight may accept (cleanup keeps it)')
    p.add_argument('--symbol', default=None, choices=CORE8,
                   help='probe symbol (default: the first config symbol, else SOLUSDT)')
    p.add_argument('--stop-route', default='algo', choices=('algo', 'classic'))
    p.add_argument('--min-balance', default='100')
    p.add_argument('--p2-samples', type=int, default=3)
    p.add_argument('--deadline-s', type=float, default=900.0)
    p.add_argument('--cleanup-attempts', type=int, default=3)
    p.add_argument('--settle-s', type=float, default=2.0,
                   help='the cleanup confirms a clean read this long later (venue read lag); 0..10')
    p.add_argument('--gate', action='store_true', help='refuse a dirty working tree (exact-build report)')
    return p


class _Recording:
    """Port proxy that remembers every FINAL fill's (symbol, exchange order id) for the fee / PnL truth."""

    def __init__(self, venue):
        self._v, self.filled = venue, []

    def __getattr__(self, name):
        return getattr(self._v, name)

    def _note(self, out):
        if out.kind is P.OutcomeKind.FINAL and out.executed_qty:
            self.filled.append((out.ref.symbol, out.exchange_order_id))
        return out

    def submit_market(self, order):
        return self._note(self._v.submit_market(order))

    def query(self, ref):
        return self._note(self._v.query(ref))


def _plan(args, specs, symbols, cassette_dir, report_dir, stored):
    lines = ['TNET-01 venue harness - PLAN (dry run: no network call, no key decrypted)',
             f'  account {args.account_id} (stored testnet key: {"yes" if stored else "NO"}); testnet host only',
             f'  symbols {", ".join(symbols)}; cassette dir {cassette_dir}; report dir {report_dir}', '',
             '1. preflight: testnet host, hedge mode, available balance >= ' + args.min_balance +
             ' USDT, flat + no open orders on the symbols' + (f' (adopting {args.adopt_foreign})' if args.adopt_foreign
                                                              else '')]
    n = 2
    for pr in args.probe:
        if pr == 'P1':
            lines.append(f'{n}. P1 client-id reuse on {args.symbol}: open min LONG (id Y), far {args.stop_route} stop '
                         f'(id X), cancel X, resubmit X, resubmit Y as a reduce-only close, close leftovers')
        else:
            lines.append(f'{n}. P2 read lag on {args.symbol}: {args.p2_samples} x (open min LONG, poll position, far '
                         f'stop, poll open orders, cancel, close, poll flat)')
        n += 1
    for path, spec in specs:
        lines.append(f'{n}. scenario {spec["name"]} ({path}, digest {spec_digest(spec)[:12]}):')
        lines += [f'     - {st["id"]}: {st["op"]} ' + ', '.join(f'{k}={v}' for k, v in st.items()
                                                                 if k not in ('id', 'op')) for st in spec['steps']]
        lines += [f'     ! fault {f["kind"]} x{f.get("times", 1)} at {f["at"]}' for f in spec.get('faults', [])]
        n += 1
    lines += [f'{n}. cleanup (always; also on Ctrl+C): cancel NEWCORE orders, flatten reduce-only, re-read, up to '
              f'{args.cleanup_attempts} attempts', f'{n + 1}. redacted exact-build report (JSON + Markdown) + '
              'sanitized cassette, leak-checked']
    return '\n'.join(lines)


def _write_cassette(recorder, directory, stamp, values, out):
    try:
        os.makedirs(check_root(directory), exist_ok=True)
        path = recorder.save(os.path.join(directory, f'tnet-{stamp}.json'))
    except (CassetteLeak, CredentialStoreError, OSError) as ex:
        out.write(f'ERROR: cassette not written ({type(ex).__name__}).\n')
        return None
    data = open(path, 'rb').read()
    if any(v.encode() in data for v in values):
        os.remove(path)
        out.write('ERROR: the cassette contained a secret value and was removed.\n')
        return None
    return path


def main(argv=None, *, http=None, local_clock=None, protector=None, out=None, monotonic=None, sleep=None,
         git_run=None):
    argv = list(sys.argv[1:] if argv is None else argv)
    out = out or sys.stdout
    refusal = argv_refusal(argv)
    if refusal is not None:
        why = ACCOUNT_REFUSAL if refusal == ACCOUNT_REFUSAL else 'keys are never passed on the command line'
        out.write(f'REFUSED: {why}.\n')
        return EXIT_USAGE
    try:
        args = _parser().parse_args(argv)
    except SystemExit as ex:
        return EXIT_USAGE if ex.code else EXIT_PASS
    if not 0 <= args.settle_s <= 10:
        out.write('REFUSED: --settle-s must be in 0..10.\n')
        return EXIT_USAGE
    if not 0 < args.deadline_s <= 7200:
        out.write('REFUSED: --deadline-s must be in (0, 7200].\n')
        return EXIT_USAGE
    if not args.probe and not args.scenario:
        out.write('REFUSED: give at least one --probe or --scenario.\n')
        return EXIT_USAGE
    try:
        min_balance = Decimal(args.min_balance)
        specs = [(p, load_spec(p)) for p in args.scenario]
    except (InvalidOperation, SpecError, OSError) as ex:
        out.write(f'REFUSED: {type(ex).__name__}: {ex}\n')
        return EXIT_USAGE
    run_cfg = None
    if args.config is None and args.account_id is None and os.path.isfile(default_testnet_config_path()):
        args.config = default_testnet_config_path()
    if args.config is not None:
        try:
            run_cfg = load_testnet_config(args.config)          # READ only: the file is never written
        except RunConfigError as ex:
            out.write(f'REFUSED: config {args.config}: {ex}\n')
            return EXIT_USAGE
        if args.account_id is not None and args.account_id != run_cfg.account_id:
            out.write(f'REFUSED: --account-id {args.account_id} disagrees with the config ({run_cfg.account_id}).\n')
            return EXIT_USAGE
        if args.symbol is not None and args.symbol not in run_cfg.symbols:
            out.write(f'REFUSED: --symbol {args.symbol} is not in the config symbols {list(run_cfg.symbols)}.\n')
            return EXIT_USAGE
        args.account_id = run_cfg.account_id
        args.symbol = args.symbol or run_cfg.symbols[0]
        out.write(f'config {args.config}: account {run_cfg.account_id}, binding {run_cfg.key_digest}, symbols '
                  f'{", ".join(run_cfg.symbols)}\n')
    if args.account_id is None:
        out.write('REFUSED: give --config (default %LOCALAPPDATA%\\ZackBotNC\\config\\testnet.toml) or --account-id.\n')
        return EXIT_USAGE
    args.symbol = args.symbol or 'SOLUSDT'
    symbols = sorted(({args.symbol} if args.probe else set()) | {s for _, sp in specs for s in sp['symbols']})
    import time as _time
    clock = local_clock or system_clock_ms
    mono = monotonic or _time.monotonic
    snooze = sleep or _time.sleep
    try:
        cassette_dir = check_root(args.cassette_dir or _cassette_dir())
        report_dir = check_root(args.report_dir or default_report_dir())
        store = CredentialStore(args.account_id, root=args.root, protector=protector, harden_acl=False)
    except (CredentialStoreError, CredentialsUnavailable) as ex:
        out.write(f'REFUSED: {ex}\n')
        return EXIT_USAGE
    build = git_build(REPO, run=git_run)
    if args.gate and build['dirty']:
        out.write('REFUSED: --gate needs a clean, committed tree (exact-build report).\n')
        return EXIT_USAGE
    if args.dry_run:
        out.write(_plan(args, specs, symbols, cassette_dir, report_dir, store.exists()) + '\n')
        return EXIT_PASS

    scrubber = SecretScrubber()
    try:
        creds = store.load(scrubber=scrubber)
    except CredentialsUnavailable as ex:
        out.write(f'NO USABLE TESTNET KEY ({ex.reason}). Store it: python tools/newcore_keys.py set --env testnet '
                  f'--account-id {args.account_id}\n')
        return EXIT_CREDS
    if run_cfg is not None:
        try:
            digest = binding_digest(creds.api_key())
        except ValueError:
            out.write('NO USABLE TESTNET KEY (corrupt).\n')
            return EXIT_CREDS
        if digest != run_cfg.key_digest:
            out.write(f'BINDING MISMATCH: the stored key of {args.account_id} has binding {digest}, the config says '
                      f'{run_cfg.key_digest}: confirm the binding (rotation) first.\n')
            return EXIT_CREDS
    scrubber.install()
    try:
        return _run(args, specs, symbols, min_balance, creds, scrubber, http, clock, mono, snooze, build, cassette_dir,
                    report_dir, out)
    finally:
        scrubber.uninstall()


def _run(args, specs, symbols, min_balance, creds, scrubber, http, clock, mono, snooze, build, cassette_dir,
         report_dir, out):
    if http is None:
        from newcore.venue.http_sender import TestnetHttpSender
        http = TestnetHttpSender()
    seam = FaultHttp(http)
    values = scrubber.redaction_values()
    recorder = CassetteRecorder(seam, redact=values, note='TNET-01 venue harness')
    oc = OffsetClock(clock)
    transport = BinanceTestnetTransport(environment='testnet', http=recorder, clock=oc,
                                        position_mode=PositionMode.HEDGE, credentials=creds, scrubber=scrubber)
    stamp = clock()
    run_id = 'tnet_' + hashlib.sha256(f'{args.account_id}\x00{stamp}'.encode()).hexdigest()[:8]
    if not oc.resync(transport).ok:
        out.write('PREFLIGHT REFUSED: server time not readable.\n')
        _write_cassette(recorder, cassette_dir, stamp, values, out)
        return EXIT_PREFLIGHT
    venue = _Recording(TestnetVenue(transport, oc))
    reader = TestnetAccountReader(transport, oc)
    pre = tnet_preflight(venue, reader, symbols, min_balance=min_balance, adopt_foreign=args.adopt_foreign)
    info = transport.exchange_info()
    rules, prices, refusals = {}, {}, list(pre.refusals)
    if info.kind is not TR.OK:
        refusals.append('exchange_info_unknown')
    else:
        for s in symbols:
            if s not in info.value.symbols or info.value.symbols[s].status != 'TRADING':
                refusals.append(f'no_trading_rules:{s}')
                continue
            rules[s] = info.value.symbols[s]
            k = transport.klines(s, '1m', limit=3)
            closed = [c for c in (k.value if k.kind is TR.OK else ()) if c.is_closed_at(oc())]
            if not closed:
                refusals.append(f'no_price:{s}')
            else:
                prices[s] = closed[-1].close
    if refusals:
        out.write('PREFLIGHT REFUSED:\n' + ''.join(f'  - {r}\n' for r in refusals))
        _write_cassette(recorder, cassette_dir, stamp, values, out)
        return EXIT_PREFLIGHT
    out.write(f'preflight OK (run {run_id}, available {pre.available_balance} USDT)\n')

    dl = RunDeadline(args.deadline_s, mono)
    pv, bounded_sleep = DeadlinePort(venue, dl), dl.bounded(snooze)       # every send and every wait is bounded
    state = dict(probes={}, scenarios=[], aborted=None, exposure=False, deadline=False)

    def body():
        try:
            _body()
        except DeadlineExceeded as ex:
            state['deadline'] = True
            out.write(f'DEADLINE: {ex}\n')

    def _body():
        for pr in args.probe:
            if dl.expired():
                state['deadline'] = True
                return
            try:
                if pr == 'P1':
                    r = probe_p1(pv, symbol=args.symbol, rules=rules[args.symbol], price=prices[args.symbol],
                                 run_id=run_id, route=args.stop_route)
                    state['probes']['P1_client_id_reuse'] = r.as_dict()
                else:
                    r = probe_p2(pv, symbol=args.symbol, rules=rules[args.symbol], price=prices[args.symbol],
                                 run_id=run_id, route=args.stop_route, samples=args.p2_samples, monotonic=mono,
                                 sleep=bounded_sleep)
                    state['probes']['P2_read_lag'] = r.as_dict()
                out.write(f'{pr}: {"conclusive" if r.conclusive else "INCONCLUSIVE"}\n')
            except ProbeAborted as ex:
                state['aborted'], state['exposure'] = f'{pr}: {ex}', ex.exposure_possible
                return
        for _, spec in specs:
            if dl.expired():
                state['deadline'] = True
                return
            outcome, _ = run_spec(spec, pv, rules_by_symbol=rules, price_by_symbol=prices, run_id=run_id,
                                  seam=seam, sleep=bounded_sleep)
            state['scenarios'].append(outcome)
            out.write(f'scenario {outcome.name}: {"PASS" if outcome.passed else "FAIL"}\n')

    interrupted = False
    try:
        _, cleanup = guarded(body, lambda: tnet_cleanup(venue, symbols, run_id=run_id,
                                                        max_attempts=args.cleanup_attempts, baseline=pre.baseline,
                                                        confirm_reads=2, settle_s=args.settle_s, sleep=snooze))
    except KeyboardInterrupt:
        interrupted = True
        cleanup = tnet_cleanup(venue, symbols, run_id=run_id, max_attempts=1, baseline=pre.baseline,
                               confirm_reads=2, settle_s=args.settle_s, sleep=snooze)
    out.write(format_cleanup(cleanup) + '\n')

    fees = pnl = Decimal(0)
    for sym, eoid in venue.filled:
        f = venue.fills(sym, eoid)
        if f.kind is P.ReadKind.OK:
            fees += sum((x.fee for x in f.value), Decimal(0))
            pnl += sum((x.realized_pnl for x in f.value), Decimal(0))
    cassette = _write_cassette(recorder, cassette_dir, stamp, values, out)
    rc_report = EXIT_PASS
    try:
        config = {'config': args.config, 'account_id': args.account_id, 'symbols': symbols, 'probes': args.probe,
                  'stop_route': args.stop_route,
                  'min_balance': str(min_balance), 'specs': [spec_digest(s) for _, s in specs],
                  'adopt_foreign': list(args.adopt_foreign)}
        paths = tnet_report(run_id=run_id, scenarios=state['scenarios'], cleanup=cleanup, config=config,
                            cassette_path=cassette, fees=fees, pnl=pnl, build=build, now_ms=clock(), out_dir=report_dir,
                            redact=values, probes=state['probes'])
        out.write(f'report: {paths[0]}\n')
    except (ReportLeak, CredentialStoreError, OSError) as ex:
        out.write(f'ERROR: report not written ({type(ex).__name__}: {ex}).\n')
        rc_report = EXIT_REPORT
    if not cleanup.clean:
        return EXIT_RESIDUE
    if cassette is None or rc_report:
        return EXIT_REPORT
    if interrupted or state['deadline']:
        return EXIT_DEADLINE
    if state['aborted'] or any(not s.passed for s in state['scenarios']):
        if state['aborted']:
            out.write(f'ABORTED: {state["aborted"]}\n')
        return EXIT_FAIL
    if any(not p.get('conclusive') for p in state['probes'].values()):
        return EXIT_INCONCLUSIVE
    return EXIT_PASS


if __name__ == '__main__':
    sys.exit(main())
