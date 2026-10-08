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
from newcore.venue.redact import scrub_path, scrub_tokens  # noqa: E402
from newcore.venue.run_config import RunConfigError, default_testnet_config_path, load_testnet_config  # noqa: E402
from newcore.venue.smoke import CORE8  # noqa: E402
from newcore.venue.testnet_venue import TestnetAccountReader, TestnetVenue  # noqa: E402
from newcore.venue.tnet import (ReportLeak, adopt_refusal, adopted_positions, default_report_dir,  # noqa: E402
                                format_cleanup, git_build, guarded, tnet_cleanup, tnet_preflight, tnet_report)
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
    p.add_argument('--adopt-file', default=None,
                   help='a text file with one --adopt-foreign item per line (for long client ids, which are '
                        'refused on the command line as key-shaped)')
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
    p.add_argument('--cleanup', action='store_true',
                   help='after a crash / kill -9: cancel every open NEWCORE order (zbn1o- / zbn1a- / zbn1e-) on the '
                        'symbols, list every position; never touches a foreign order')
    p.add_argument('--close-positions', action='store_true',
                   help='with --cleanup: also close every position above its --adopt-foreign baseline with a '
                        'reduce-only market order (you assert they are NEWCORE exposure)')
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
             f'  symbols {", ".join(symbols)}; cassette dir {scrub_path(cassette_dir)}; report dir '
             f'{scrub_path(report_dir)}', '',
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
        lines.append(f'{n}. scenario {spec["name"]} ({scrub_path(path)}, digest {spec_digest(spec)[:12]}):')
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


def main(argv=None, *, out=None, **kw):
    """Typed exit codes only: an unexpected exception is EXIT_FAIL (7) with its type, never a traceback with exit 1
    (= INCONCLUSIVE). Sends happen only inside the guarded body, whose teardown has run by then."""
    out = out or sys.stdout
    try:
        return _main(argv, out=out, **kw)
    except KeyboardInterrupt:                                        # N2: preflight / report phase
        out.write('INTERRUPTED (Ctrl+C). Nothing more will be sent. Check the lines above: if orders were sent, the '
                  'CLEANUP line shows what the teardown did; if none is there, run: python tools\\newcore_tnet.py '
                  '--cleanup\n')
        return EXIT_DEADLINE
    except Exception as ex:                                          # noqa: BLE001
        out.write(f'ERROR: unexpected {type(ex).__name__} (details withheld: they may echo input). The guarded '
                  f'cleanup has run if anything was sent.\n')
        return EXIT_FAIL


def _main(argv=None, *, http=None, local_clock=None, protector=None, out=None, monotonic=None, sleep=None,
          git_run=None):
    argv = list(sys.argv[1:] if argv is None else argv)
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
    if args.cleanup and (args.probe or args.scenario):
        out.write('REFUSED: --cleanup runs alone (no --probe / --scenario).\n')
        return EXIT_USAGE
    if args.close_positions and not args.cleanup:
        out.write('REFUSED: --close-positions goes with --cleanup.\n')
        return EXIT_USAGE
    if not args.probe and not args.scenario and not args.cleanup:
        out.write('REFUSED: give at least one --probe or --scenario (or --cleanup).\n')
        return EXIT_USAGE
    for flag, v, lo, hi in (('--p2-samples', args.p2_samples, 1, 10),
                            ('--cleanup-attempts', args.cleanup_attempts, 1, 10)):
        if not lo <= v <= hi:
            out.write(f'REFUSED: {flag} must be in {lo}..{hi}.\n')
            return EXIT_USAGE
    try:
        min_balance = Decimal(args.min_balance)
        if not min_balance.is_finite() or min_balance < 0:
            raise InvalidOperation
    except InvalidOperation:
        out.write('REFUSED: --min-balance must be a finite decimal >= 0.\n')
        return EXIT_USAGE
    for d, flag in ((args.report_dir, '--report-dir'), (args.cassette_dir, '--cassette-dir')):
        if d is not None and os.path.exists(d) and not os.path.isdir(d):
            out.write(f'REFUSED: {flag} is not a directory.\n')
            return EXIT_USAGE
    specs = []
    for i, p in enumerate(args.scenario):
        try:
            specs.append((p, load_spec(p)))
        except SpecError as ex:
            out.write(f'REFUSED: --scenario #{i + 1}: {scrub_tokens(str(ex))[:200]}\n')   # no path, no raw keys
            return EXIT_USAGE
        except OSError as ex:
            out.write(f'REFUSED: --scenario #{i + 1}: cannot read it ({type(ex).__name__})\n')
            return EXIT_USAGE
        if 'testnet' not in specs[-1][1].get('targets', ['testnet']):
            out.write(f'REFUSED: --scenario #{i + 1} ({specs[-1][1]["name"]}) is not a testnet scenario '
                      f'(targets {specs[-1][1]["targets"]}).\n')
            return EXIT_USAGE
    adopt = list(args.adopt_foreign)
    if args.adopt_file is not None:                   # X1: long foreign client ids never travel in argv
        try:
            with open(args.adopt_file, encoding='utf-8') as fh:
                adopt += [ln.strip() for ln in fh.read(64 * 1024).splitlines() if ln.strip()]
        except (OSError, UnicodeDecodeError) as ex:
            out.write(f'REFUSED: --adopt-file cannot be read ({type(ex).__name__}).\n')
            return EXIT_USAGE
    for _, sp in specs:                              # the spec's own account section is honoured (T3)
        acc = sp.get('account', {})
        if 'min_balance' in acc:
            min_balance = max(min_balance, Decimal(acc['min_balance']))
        adopt += [a for a in acc.get('adopt_foreign', []) if a not in adopt]
    for a in adopt:
        why = adopt_refusal(a)
        if why is not None:
            out.write(f'REFUSED: --adopt-foreign item: {why}.\n')
            return EXIT_USAGE
    args.adopt_foreign = adopt
    if args.cleanup and args.close_positions and any(
            q is None for q in adopted_positions(args.adopt_foreign).values()):
        out.write('REFUSED: with --cleanup --close-positions an adopted position must state its FOREIGN quantity: '
                  '--adopt-foreign SYMBOL:SIDE:QTY (a NEWCORE leftover is never kept as foreign).\n')
        return EXIT_USAGE
    run_cfg = None
    if args.config is None and args.account_id is None and os.path.isfile(default_testnet_config_path()):
        args.config = default_testnet_config_path()
    if args.config is not None:
        try:
            run_cfg = load_testnet_config(args.config)          # READ only: the file is never written
        except RunConfigError as ex:
            out.write(f'REFUSED: --config: {ex}\n')
            return EXIT_USAGE
        if args.account_id is not None and args.account_id != run_cfg.account_id:
            out.write(f'REFUSED: --account-id {args.account_id} disagrees with the config ({run_cfg.account_id}).\n')
            return EXIT_USAGE
        if args.symbol is not None and args.symbol not in run_cfg.symbols:
            out.write(f'REFUSED: --symbol {args.symbol} is not in the config symbols {list(run_cfg.symbols)}.\n')
            return EXIT_USAGE
        args.account_id = run_cfg.account_id
        args.symbol = args.symbol or run_cfg.symbols[0]
        out.write(f'config {scrub_path(args.config)}: account {run_cfg.account_id}, binding {run_cfg.key_digest}, '
                  f'symbols '
                  f'{", ".join(run_cfg.symbols)}\n')
    if args.account_id is None:
        out.write('REFUSED: give --config (default %LOCALAPPDATA%\\ZackBotNC\\config\\testnet.toml) or --account-id.\n')
        return EXIT_USAGE
    args.symbol = args.symbol or 'SOLUSDT'
    symbols = sorted(({args.symbol} if args.probe else set()) | {s for _, sp in specs for s in sp['symbols']})
    if args.cleanup:
        symbols = sorted(set(run_cfg.symbols if run_cfg is not None else ()) | {args.symbol})
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
        if args.cleanup:
            out.write('TNET-01 cleanup - PLAN (dry run: no network call, no key decrypted)\n'
                      f'  account {args.account_id}; symbols {", ".join(symbols)}\n'
                      '  1. read positions + open orders (classic and algo) on the symbols\n'
                      '  2. cancel every open NEWCORE order (foreign orders are never touched)\n'
                      + ('  3. close every position above its --adopt-foreign baseline (reduce-only market)\n'
                         if args.close_positions else '  3. list positions only (add --close-positions to close)\n')
                      + f'  4. confirm with a second read {args.settle_s:g} s later; report\n')
            return EXIT_PASS
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


def _cleanup_only(args, venue, symbols, run_id, snooze, recorder, cassette_dir, stamp, values, out, *, build,
                  report_dir, clock):
    """N3: the standalone teardown. Foreign orders are never cancelled; positions are closed only with
    --close-positions (above the --adopt-foreign baseline), else listed. Exit 0 clean / 4 unreadable / 8 left."""
    from newcore.venue.tnet import is_newcore_cid
    try:
        venue.check_hedge_mode()
    except Exception:                                            # noqa: BLE001 - one-way / unreadable
        out.write('CLEANUP REFUSED: the account is not readable in hedge mode.\n')
        _write_cassette(recorder, cassette_dir, stamp, values, out)
        return EXIT_PREFLIGHT
    baseline, positions, over = {}, [], []
    for sym in symbols:
        pos = venue.positions(sym)
        oo = venue.open_orders(sym)
        if pos.kind is not P.ReadKind.OK or oo.kind is not P.ReadKind.OK:
            out.write(f'CLEANUP REFUSED: {sym} positions / open orders unreadable; nothing sent.\n')
            _write_cassette(recorder, cassette_dir, stamp, values, out)
            return EXIT_PREFLIGHT
        declared = adopted_positions(args.adopt_foreign)
        for p in pos.value:
            if p.qty == 0:
                continue
            positions.append(p)
            if not args.close_positions:
                baseline[(p.symbol, p.side)] = p.qty         # listed only, never closed
            elif (p.symbol, p.side) in declared:            # keep ONLY the declared foreign quantity (C1)
                if declared[(p.symbol, p.side)] > p.qty:     # C1b: cannot be checked; it would hide a leftover
                    over.append(f'{p.symbol}:{p.side} declares {declared[(p.symbol, p.side)]} but holds {p.qty}')
                baseline[(p.symbol, p.side)] = min(p.qty, declared[(p.symbol, p.side)])
        for o in oo.value:
            tag = 'NEWCORE, will be cancelled' if is_newcore_cid(o.ref.client_id) else 'foreign, left alone'
            out.write(f'  order {sym} {o.ref.client_id} {o.order_type} {o.qty}: {tag}\n')
    if over:
        out.write('CLEANUP REFUSED (nothing sent): an adopted quantity is above the position, so a NEWCORE leftover '
                  'could not be told apart. Re-check the testnet UI and declare the foreign quantity exactly:\n'
                  + ''.join(f'  {x}\n' for x in over))
        _write_cassette(recorder, cassette_dir, stamp, values, out)
        return EXIT_PREFLIGHT
    for p in positions:
        keep = baseline.get((p.symbol, p.side), Decimal(0))
        what = 'kept (listed)' if keep >= p.qty else (f'{p.qty - keep} will be closed' + (f', {keep} kept as '
                                                                                       f'foreign' if keep else ''))
        out.write(f'  position {p.symbol} {p.side} {p.qty}: {what}\n')
    _, res = guarded(lambda: None, lambda: tnet_cleanup(                # C2: Ctrl+C cannot abort the teardown
        venue, symbols, run_id=run_id + '_cleanup', max_attempts=args.cleanup_attempts, baseline=baseline,
        confirm_reads=2, settle_s=args.settle_s, sleep=snooze))
    out.write(format_cleanup(res) + '\n')
    declared = adopted_positions(args.adopt_foreign)
    left = [p for p in positions if (p.symbol, p.side) in baseline and (p.symbol, p.side) not in declared]
    if left:
        out.write('POSITIONS LEFT (not adopted; add --close-positions if they are NEWCORE exposure):\n' +
                  ''.join(f'  {p.symbol} {p.side} {p.qty}\n' for p in left))
    cassette = _write_cassette(recorder, cassette_dir, stamp, values, out)   # every request of the teardown
    if cassette is not None:
        out.write(f'cassette: {scrub_path(cassette)}\n')
    rc_report = EXIT_PASS
    try:
        config = {'mode': 'cleanup', 'account_id': args.account_id, 'symbols': symbols,
                  'close_positions': bool(args.close_positions), 'adopt_foreign': list(args.adopt_foreign)}
        paths = tnet_report(run_id=run_id + '_cleanup', scenarios=[], cleanup=res, config=config,
                            cassette_path=cassette, fees=Decimal(0), pnl=Decimal(0), build=build, now_ms=clock(),
                            out_dir=report_dir, redact=values)
        out.write(f'report: {scrub_path(paths[0])}\n')
    except (ReportLeak, CredentialStoreError, OSError) as ex:
        out.write(f'ERROR: report not written ({type(ex).__name__}).\n')
        rc_report = EXIT_REPORT
    if not res.clean or left:
        return EXIT_RESIDUE
    if cassette is None or rc_report:
        return EXIT_REPORT
    return EXIT_PASS


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
    if args.cleanup:
        return _cleanup_only(args, venue, symbols, run_id, snooze, recorder, cassette_dir, stamp, values, out,
                             build=build, report_dir=report_dir, clock=clock)
    pre = tnet_preflight(venue, reader, symbols, min_balance=min_balance, adopt_foreign=args.adopt_foreign)
    state_baseline = pre.baseline
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
        except Exception as ex:                          # noqa: BLE001 - typed: a FAIL, the teardown runs
            state['aborted'], state['exposure'] = f'unexpected {type(ex).__name__}', True

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
            left = dl.remaining()                          # T3: the spec's own max_duration_s bounds it too
            sdl = RunDeadline(min(spec['expect'].get('max_duration_s', 3600), left), mono) if left > 0 else None
            if sdl is None:
                state['deadline'] = True
                return
            outcome, _ = run_spec(spec, DeadlinePort(venue, sdl), rules_by_symbol=rules, price_by_symbol=prices,
                                  run_id=run_id, seam=seam, sleep=sdl.bounded(snooze), baseline=state_baseline)
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
        config = {'config': scrub_path(args.config) if args.config else None, 'account_id': args.account_id,
                  'symbols': symbols, 'probes': args.probe, 'stop_route': args.stop_route,
                  'min_balance': str(min_balance), 'specs': [spec_digest(s) for _, s in specs],
                  'adopt_foreign': list(args.adopt_foreign)}
        paths = tnet_report(run_id=run_id, scenarios=state['scenarios'], cleanup=cleanup, config=config,
                            cassette_path=cassette, fees=fees, pnl=pnl, build=build, now_ms=clock(), out_dir=report_dir,
                            redact=values, probes=state['probes'])
        out.write(f'report: {scrub_path(paths[0])}\n')
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
