"""TNET-01 RUNNER-driven scenarios (T01-T04, T09-T12): the NEWCORE Runner itself, through ScriptedSignals on 1m candles.

    python tools/newcore_tnet_runner.py                                   every bundled spec on FakeVenue (no network)
    python tools/newcore_tnet_runner.py --only T01-long --only T09 -v
    python tools/newcore_tnet_runner.py --target testnet --dry-run      (reads %LOCALAPPDATA%\\ZackBotNC\\config\\testnet.toml)
    python tools/newcore_tnet_runner.py --target testnet --config <testnet.toml> [--symbol BTCUSDT] [--gate]
    python tools/newcore_tnet_runner.py --target testnet --account-id <acct_..> --key-digest <16 hex> --dry-run
    python tools/newcore_tnet_runner.py --target testnet --account-id <acct_..> --key-digest <16 hex> [--gate]

--target testnet builds the Runner's parts with newcore.venue.factory:build_testnet (stored DPAPI key of the account,
binding digest checked, hedge mode, exchange rules) and runs the same specs on the real 1m candles. The OWNER runs it.
Preflight refuses a non-flat account or foreign orders (--adopt-foreign to accept one). Cleanup ALWAYS runs per
scenario (also on Ctrl+C). The redacted report (JSON + Markdown) goes to %LOCALAPPDATA%\\ZackBotNC\\reports.

Exit codes (plan 6.6): 0 all PASS, cleanup clean | 1 an INCONCLUSIVE (a bounded wait elapsed) | 2 usage / config
refused (also a dirty tree with --gate) | 3 no usable credentials / binding mismatch | 4 preflight or factory refused |
5 report not written / leak | 6 a scenario deadline | 7 a scenario FAILED | 8 cleanup NOT clean (exposure MAY be left).
"""
import argparse
import json
import os
import re
import secrets
import sys
import time
from decimal import Decimal, InvalidOperation

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, REPO)

from newcore.tnet.driver import EXIT_FAIL, EXIT_PASS, EXIT_PREFLIGHT, EXIT_RESIDUE, run_suite  # noqa: E402
from newcore.tnet.rspec import (MAX_SETTLE_MS, SpecError, bundled, load_rspec, rspec_digest,  # noqa: E402
                                validate_rspec)
from newcore.venue.cli_args import ACCOUNT_REFUSAL, argv_refusal  # noqa: E402
from newcore.venue.redact import scrub_path, scrub_tokens  # noqa: E402
from newcore.venue.tnet import (CleanupResult, ScenarioOutcome, _audit, adopt_refusal, git_build,  # noqa: E402
                                tnet_report)

EXIT_USAGE, EXIT_CREDS, EXIT_REPORT = 2, 3, 5
DIGEST_RE = re.compile(r'[0-9a-f]{16}')
FACTORY = 'newcore.venue.factory:build_testnet'


def _parser():
    p = argparse.ArgumentParser(prog='newcore_tnet_runner', description='TNET-01 runner-driven scenarios.')
    p.add_argument('--target', choices=('fake', 'testnet'), default='fake')
    p.add_argument('--spec', action='append', default=[], help='a zb-newcore-tnet-runner/1 JSON (default: bundled)')
    p.add_argument('--only', action='append', default=[], help='run only these scenario ids')
    p.add_argument('--run-nonce', default=None, help='default: 8 random hex (fresh client ids per run)')
    p.add_argument('--account-id', default=None)
    p.add_argument('--key-digest', default=None, help='the 16-hex binding digest tools/newcore_keys.py prints')
    p.add_argument('--config', default=None,
                   help='a TESTNET run config (newcore.runner.config; READ only, never written). --target testnet '
                        'defaults to %%LOCALAPPDATA%%\\ZackBotNC\\config\\testnet.toml when it exists and no '
                        '--account-id is given')
    p.add_argument('--symbol', default=None, help='run every spec on this symbol (with --config: one of its symbols; '
                                                  'default: specs on a symbol outside the config move to its first)')
    p.add_argument('--root', default=None, help='credential folder (default %%LOCALAPPDATA%%\\ZackBotNC\\secrets)')
    p.add_argument('--report-dir', default=None, help='testnet default %%LOCALAPPDATA%%\\ZackBotNC\\reports; fake: '
                                                         'no report unless given')
    p.add_argument('--min-balance', default=None)
    p.add_argument('--adopt-foreign', action='append', default=[])
    p.add_argument('--adopt-file', default=None, help='one --adopt-foreign item per line (long client ids)')
    p.add_argument('--settle-ms', type=int, default=1500)
    p.add_argument('--dry-run', action='store_true', help='print the plan only: no network call, no key decrypted')
    p.add_argument('--cassette-dir', default=None,
                   help='testnet: where each scenario cassette + replay sidecar goes (default '
                        '%%LOCALAPPDATA%%\\ZackBotNC\\cassettes)')
    p.add_argument('--replay', action='append', default=[],
                   help='re-run a recorded scenario OFFLINE through the Runner against its cassette; it must reach '
                        'the recorded verdict (exit 0 same, 7 different)')
    p.add_argument('--gate', action='store_true', help='refuse a dirty working tree (exact-build report)')
    p.add_argument('-v', '--verbose', action='store_true')
    return p


class _TnetConfig:
    """The RunConfig fields the testnet factory reads."""
    mode, venue_kind, factory = 'TESTNET', 'testnet', 'newcore.venue.factory:build_testnet'

    def __init__(self, account_id, key_digest, symbols):
        self.account_id, self.key_digest, self.symbols = account_id, key_digest, tuple(symbols)


def default_config_path():
    return os.path.join(os.environ.get('LOCALAPPDATA') or os.path.expanduser('~'), 'ZackBotNC', 'config',
                        'testnet.toml')


def _apply_config(args, out):
    """Load the run config READ-ONLY (newcore.runner.config.load) and take the account, binding digest and symbols from
    it. Explicit --account-id / --key-digest must agree with it. Returns an exit code on refusal, else None."""
    from newcore.runner.config import ConfigError, load
    try:
        cfg = load(args.config)
    except ConfigError as ex:                        # messages may quote values: key-like runs are redacted
        out.write(f'REFUSED: --config: {scrub_tokens(str(ex))[:200]}\n')
        return EXIT_USAGE
    except Exception as ex:                                          # noqa: BLE001 - OSError / nesting / types
        out.write(f'REFUSED: --config: not a usable config ({type(ex).__name__})\n')
        return EXIT_USAGE
    if cfg.mode != 'TESTNET' or cfg.venue_kind != 'testnet' or cfg.factory != FACTORY:
        out.write(f'REFUSED: --config is not a TESTNET config with venue.factory {FACTORY}.\n')
        return EXIT_USAGE
    for flag, mine in (('account_id', cfg.account_id), ('key_digest', cfg.key_digest)):
        given = getattr(args, flag)
        if given is not None and given != mine:
            out.write(f'REFUSED: --{flag.replace("_", "-")} disagrees with the config ({mine}).\n')
            return EXIT_USAGE
    args.account_id, args.key_digest, args.config_symbols = cfg.account_id, cfg.key_digest, tuple(cfg.symbols)
    out.write(f'config {scrub_path(args.config)}: account {cfg.account_id}, binding {cfg.key_digest}, symbols '
              f'{", ".join(cfg.symbols)}\n')
    return None


def retarget(specs, symbol, allowed, out):
    """--symbol moves every spec to that symbol; with a config (allowed symbols), a spec on another symbol moves to the
    config's first symbol. Every moved spec is re-validated."""
    if symbol is not None:
        if not re.fullmatch(r'[A-Z0-9]{2,30}', symbol):
            raise ValueError(f'--symbol {symbol!r} is not a symbol')
        if allowed is not None and symbol not in allowed:
            raise ValueError(f'--symbol {symbol} is not in the config symbols {list(allowed)}')
    out_specs = []
    for s in specs:
        want = symbol or (allowed[0] if allowed is not None and s['symbol'] not in allowed else None)
        if want is not None and want != s['symbol']:
            s = dict(s, symbol=want)
            validate_rspec(s)
            out.write(f'{s["id"]}: symbol -> {want}\n')
        out_specs.append(s)
    return out_specs


def _plan(args, specs):
    lines = [f'TNET-01 runner scenarios - PLAN (target {args.target}; dry run: no network call, no key decrypted)']
    if args.target == 'testnet':
        lines.append(f'account {args.account_id} binding {args.key_digest} (config {args.config or "none"})')
    for s in specs:
        on = 'runs' if args.target in s['targets'] else 'SKIPPED (not a ' + args.target + ' scenario)'
        lines.append(f'- {s["id"]} {s["name"]} [{on}] {s["side"]} {s["symbol"]} stop {s["stop"]} '
                     f'bound {s["bound"]} digest {rspec_digest(s)[:12]}')
        lines += [f'    {st["op"]} ' + ', '.join(f'{k}={v}' for k, v in st.items() if k != 'op') for st in s['steps']]
    lines.append('- after each scenario: final exchange truth (flat / protected + reconciled), then cleanup (always)')
    return '\n'.join(lines)


def _print(r, out, verbose):
    out.write(f'{r.verdict:<12} {r.id:<18} {r.name}' + (f'  ({r.error})' if r.error else '')
              + (' degraded=' + ','.join(f'{k}:{v}' for k, v in r.degraded.items()) if r.degraded else '') + '\n')
    for name, ok, detail in r.assertions:
        if verbose or not ok:
            out.write(f'    [{"x" if ok else " "}] {name}: {detail}\n')


def _report(args, res, run_id, build, values, out, cassettes=None):
    scenarios = [ScenarioOutcome(f'{r.id} {r.name} [{r.verdict}]', r.verdict == 'PASS',
                                 tuple((f'{n}: {d}', ok) for n, ok, d in r.assertions)) for r in res.scenarios]
    agg = CleanupResult(clean=all(not r.residue for r in res.scenarios), attempts=sum(r.cleanup.get('attempts', 0)
                                                                                         for r in res.scenarios))
    for r in res.scenarios:
        agg.remaining_positions += [tuple(x) for x in r.cleanup.get('remaining_positions', [])]
        agg.remaining_orders += [tuple(x) for x in r.cleanup.get('remaining_orders', [])]
    fees, pnl = Decimal(0), Decimal(0)
    for r in res.scenarios:
        for t in r.trades:
            fees += Decimal(t['fees'])
            pnl += Decimal(t['pnl'])
    config = {'target': args.target, 'config': args.config, 'account_id': args.account_id,
              'key_digest': args.key_digest, 'specs': sorted({r.id for r in res.scenarios}), 'run_nonce': run_id,
              'adopt_foreign': list(args.adopt_foreign)}
    detail = json.dumps([r.as_dict() for r in res.scenarios], indent=1, sort_keys=True) + '\n'
    try:
        _audit((detail,), values)                     # the per-scenario detail is leak-checked like the report
        paths = tnet_report(run_id=run_id, scenarios=scenarios, cleanup=agg, config=config,
                            cassette_path=cassettes or None,
                            fees=fees, pnl=pnl, build=build, now_ms=int(time.time() * 1000), out_dir=args.report_dir,
                            redact=values)
        with open(paths[0][:-len('.json')] + '.scenarios.json', 'w', encoding='utf-8', newline='\n') as fh:
            fh.write(detail)                          # orders, ledger, injected faults, final truth, cleanup
    except Exception as ex:                                              # noqa: BLE001 - reported as exit 5
        out.write(f'ERROR: report not written ({type(ex).__name__}: {ex}).\n')
        return EXIT_REPORT
    out.write(f'report: {scrub_path(paths[0])}\n')
    return EXIT_PASS


def main(argv=None, *, out=None, **kw):
    """Typed exit codes only: an unexpected exception is exit 7 with its type (never exit 1 = INCONCLUSIVE, never
    its message: it may echo input). Ctrl+C inside a scenario is handled there (teardown, report, exit 6)."""
    out = out or sys.stdout
    try:
        return _main(argv, out=out, **kw)
    except KeyboardInterrupt:                                        # N2: preflight / boot / report phase
        out.write('INTERRUPTED (Ctrl+C). A scenario that had sent orders ran its teardown.\n')
        return 6
    except Exception as ex:                                          # noqa: BLE001
        out.write(f'ERROR: unexpected {type(ex).__name__} (details withheld). Any scenario that sent orders ran '
                  f'its teardown.\n')
        return 7


def _main(argv=None, *, http=None, local_clock=None, sleep=None, store=None, out=None, git_run=None,
          monotonic=None):
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
    if args.replay:                                  # offline: no key, no network, the recorded answers only
        others = [f for f in ('spec', 'only', 'config', 'account_id', 'key_digest', 'symbol', 'adopt_foreign')
                  if getattr(args, f)]
        if others or args.target != 'fake' or args.dry_run:
            out.write('REFUSED: --replay runs alone (no target / spec / account options).\n')
            return EXIT_USAGE
        return _replay(args.replay, out, args.verbose)
    specs = []
    for i, p in enumerate(args.spec):
        try:
            specs.append(load_rspec(p))
        except SpecError as ex:
            out.write(f'REFUSED: --spec #{i + 1}: {scrub_tokens(str(ex))[:200]}\n')
            return EXIT_USAGE
        except Exception as ex:                                      # noqa: BLE001 - bad UTF-8 / nesting / types
            out.write(f'REFUSED: --spec #{i + 1}: not a usable spec ({type(ex).__name__})\n')
            return EXIT_USAGE
    if not args.spec:
        specs = bundled()
    if args.adopt_file is not None:                  # X1: long foreign client ids never travel in argv
        try:
            with open(args.adopt_file, encoding='utf-8') as fh:
                args.adopt_foreign += [ln.strip() for ln in fh.read(64 * 1024).splitlines() if ln.strip()]
        except (OSError, UnicodeDecodeError) as ex:
            out.write(f'REFUSED: --adopt-file cannot be read ({type(ex).__name__}).\n')
            return EXIT_USAGE
    for a in args.adopt_foreign:
        why = adopt_refusal(a)
        if why is not None:
            out.write(f'REFUSED: --adopt-foreign item: {why}.\n')
            return EXIT_USAGE
    for d, flag in ((args.report_dir, '--report-dir'), (args.cassette_dir, '--cassette-dir')):
        if d is not None and (not d.strip() or (os.path.exists(d) and not os.path.isdir(d))):
            out.write(f'REFUSED: {flag} must be a directory.\n')
            return EXIT_USAGE
    if args.only:
        unknown = set(args.only) - {s['id'] for s in specs}
        if unknown:
            out.write(f'REFUSED: unknown scenario id(s) {sorted(unknown)}\n')
            return EXIT_USAGE
        specs = [s for s in specs if s['id'] in args.only]
        skipped = [s['id'] for s in specs if args.target not in s['targets']]
        if skipped:
            out.write(f'REFUSED: not {args.target} scenario(s): {skipped} (SKIPPED is never a pass)\n')
            return EXIT_USAGE
    if not 0 <= args.settle_ms <= MAX_SETTLE_MS:
        out.write(f'REFUSED: --settle-ms must be in 0..{MAX_SETTLE_MS}.\n')
        return EXIT_USAGE
    nonce = args.run_nonce or secrets.token_hex(4)
    if not re.fullmatch(r'[0-9a-z_]{1,32}', nonce):
        out.write('REFUSED: --run-nonce must be [0-9a-z_]{1,32}\n')
        return EXIT_USAGE
    allowed = None
    if args.target == 'testnet':
        if args.config is None and not args.account_id and os.path.isfile(default_config_path()):
            args.config = default_config_path()
        if args.config is not None:
            refused = _apply_config(args, out)
            if refused:
                return refused
            allowed = args.config_symbols
        if not args.account_id or not args.key_digest or DIGEST_RE.fullmatch(args.key_digest) is None:
            out.write('REFUSED: --target testnet needs --config, or --account-id and --key-digest (16 hex binding '
                      'digest).\n')
            return EXIT_USAGE
    elif args.config is not None:
        out.write('REFUSED: --config is for --target testnet.\n')
        return EXIT_USAGE
    try:
        specs = retarget(specs, args.symbol, allowed, out)
    except (SpecError, ValueError) as ex:
        out.write(f'REFUSED: {ex}\n')
        return EXIT_USAGE
    build = git_build(REPO, run=git_run)
    if args.gate and build['dirty']:
        out.write('REFUSED: --gate needs a clean, committed tree (exact-build report).\n')
        return EXIT_USAGE
    if args.dry_run:
        out.write(_plan(args, specs) + '\n')
        return EXIT_PASS
    min_balance = None
    if args.min_balance is not None:
        try:
            min_balance = Decimal(args.min_balance)
            if not min_balance.is_finite() or min_balance < 0:
                raise InvalidOperation
        except InvalidOperation:
            out.write('REFUSED: --min-balance must be a finite decimal >= 0\n')
            return EXIT_USAGE

    values = []
    scrubber = None
    cassettes, cassette_errors = {}, []
    if args.target == 'fake':
        from newcore.tnet.targets import FakeTarget
        target = FakeTarget()
    else:
        from newcore.venue.credentials import CredentialStoreError, check_root
        try:
            cassette_dir = check_root(args.cassette_dir or default_cassette_dir())
            os.makedirs(cassette_dir, exist_ok=True)
        except (CredentialStoreError, OSError) as ex:
            out.write(f'REFUSED: cassette dir ({type(ex).__name__})\n')
            return EXIT_USAGE
        clash = [n for n in os.listdir(cassette_dir) if n.startswith(f'tnet-{nonce}-')]
        if clash:                                    # a rerun with the same nonce would overwrite evidence
            out.write(f'REFUSED: cassettes of run {nonce} already exist; use another --run-nonce\n')
            return EXIT_USAGE
        rc, make_target, scrubber = _testnet_target(args, specs, http, local_clock, sleep, store, out)
        if make_target is None:
            return rc
        values = [v for v in scrubber.redaction_values() if isinstance(v, str)]
        scrubber.install()
    try:
        out.write(f'run {nonce}: {len(specs)} spec(s) on {args.target}\n')
        if args.target == 'fake':
            res = run_suite(specs, target, run_nonce=nonce, min_balance=min_balance,
                            adopt_foreign=args.adopt_foreign, on_result=lambda r: _print(r, out, args.verbose))
        else:
            from newcore.tnet.recording import run_recorded_suite
            try:
                res, pre_path, cassette_errors = run_recorded_suite(
                    specs, make_target, run_nonce=nonce, cassette_dir=cassette_dir, redact=values,
                    monotonic=monotonic or time.monotonic, min_balance=min_balance, adopt_foreign=args.adopt_foreign,
                    on_result=lambda r: _print(r, out, args.verbose))
            except _BootRefused as ex:
                out.write(ex.text)
                return ex.code
            cassettes = {'preflight': pre_path, **{r.id: r.cassette for r in res.scenarios if r.cassette}}
            for sid, why in cassette_errors:
                out.write(f'ERROR: cassette of {sid} not written ({why}).\n')
        if res.exit_code == 2 and res.scenarios is not None and not any(r.verdict != 'SKIPPED'
                                                                         for r in res.scenarios):
            out.write('NOTHING RAN: every selected scenario was SKIPPED on this target (not a pass).\n')
            return EXIT_USAGE
        if res.exit_code == EXIT_PREFLIGHT:
            out.write('PREFLIGHT REFUSED:\n' + ''.join(f'  - {x}\n' for x in res.preflight.refusals))
            return EXIT_PREFLIGHT
        rc_report = EXIT_PASS
        if args.target == 'testnet' or args.report_dir:
            rc_report = _report(args, res, 'tnet_' + nonce, build, values, out, cassettes)
        if res.exit_code == EXIT_RESIDUE:
            out.write('CLEANUP NOT CLEAN - check these on the testnet UI:\n')
            for r in res.scenarios:
                for x in r.cleanup.get('remaining_positions', []) + r.cleanup.get('remaining_orders', []):
                    out.write(f'  {r.id}: {x}\n')
            return EXIT_RESIDUE
        if rc_report:
            return rc_report
        if cassette_errors:
            return EXIT_REPORT
        summary = {k: sum(1 for r in res.scenarios if r.verdict == k) for k in ('PASS', 'FAIL', 'INCONCLUSIVE',
                                                                                 'SKIPPED')}
        out.write(f'summary: {summary} -> exit {res.exit_code}\n')
        return res.exit_code
    finally:
        if scrubber is not None:
            scrubber.uninstall()


def _testnet_target(args, specs, http, local_clock, sleep, store, out):
    from newcore.tnet.targets import TestnetTarget
    from newcore.venue.credentials import CredentialsUnavailable, CredentialStore, CredentialStoreError, SecretScrubber
    from newcore.venue.factory import BindingMismatch, FactoryRefused
    symbols = sorted({s['symbol'] for s in specs if 'testnet' in s['targets']})
    if not symbols:
        out.write('REFUSED: none of the selected specs targets testnet.\n')
        return EXIT_USAGE, None, None
    try:
        store = store or CredentialStore(args.account_id, root=args.root)
    except (CredentialStoreError, CredentialsUnavailable) as ex:
        out.write(f'REFUSED: {ex}\n')
        return EXIT_USAGE, None, None
    scrubber = SecretScrubber()
    try:
        store.load(scrubber=scrubber)                 # the redaction values, before any request is recorded
    except CredentialsUnavailable as ex:
        out.write(f'NO USABLE TESTNET KEY ({ex.reason}). Store it: python tools/newcore_keys.py set --env testnet '
                  f'--account-id {args.account_id}\n')
        return EXIT_CREDS, None, None
    if http is None:
        from newcore.venue.http_sender import TestnetHttpSender
        http = TestnetHttpSender()
    cfg = _TnetConfig(args.account_id, args.key_digest, symbols)
    booted = []

    def make_target(recorder):
        """A fresh factory boot per scenario (each cassette replays on its own). Only the FIRST boot's refusals are
        CLI exit codes; a later one is that scenario's FAIL."""
        try:
            t = TestnetTarget(cfg, http=http, sleep=sleep or time.sleep, local_clock=local_clock, store=store,
                              scrubber=scrubber, settle_ms=args.settle_ms, recorder=recorder)
        except Exception as ex:                                          # noqa: BLE001
            if booted:
                raise
            if isinstance(ex, BindingMismatch):
                raise _BootRefused(EXIT_CREDS, f'BINDING MISMATCH: {ex}\n') from None
            if isinstance(ex, CredentialsUnavailable):
                raise _BootRefused(EXIT_CREDS, f'NO USABLE TESTNET KEY ({ex.reason}).\n') from None
            if isinstance(ex, BOOT_REFUSALS):
                raise _BootRefused(EXIT_PREFLIGHT, f'FACTORY REFUSED: {type(ex).__name__}\n') from None
            raise                                                    # a bug: the typed top level (exit 7)
        booted.append(1)
        return t
    return EXIT_PASS, make_target, scrubber


def _boot_refusals():
    from newcore.venue.factory import FactoryRefused
    from newcore.venue.guard import VenueGuardError
    from newcore.venue.rules_fetch import RulesUnavailable, SymbolRefused
    from newcore.venue.testnet_venue import HedgeModeRequired, VenueBootUnknown
    return (FactoryRefused, VenueGuardError, RulesUnavailable, SymbolRefused, HedgeModeRequired, VenueBootUnknown)


BOOT_REFUSALS = _boot_refusals()


class _BootRefused(Exception):
    def __init__(self, code, text):
        super().__init__(text)
        self.code, self.text = code, text


def _clean(text, limit=200):
    """One printable line (ESC / CR / LF -> '?'), key-like runs redacted: no forged SAME / PASS lines."""
    return scrub_tokens(''.join(ch if ch.isprintable() else '?' for ch in str(text)))[:limit]


def default_cassette_dir():
    return os.path.join(os.environ.get('LOCALAPPDATA') or os.path.expanduser('~'), 'ZackBotNC', 'cassettes')


def _replay(paths, out, verbose):
    from newcore.tnet.replay import ReplayError, describe, replay
    rc = EXIT_PASS
    for i, p in enumerate(paths):
        try:
            rr = replay(p)
        except (ReplayError, SpecError) as ex:
            out.write(f'REFUSED: --replay #{i + 1}: {_clean(ex)}\n')
            return EXIT_USAGE
        except Exception as ex:                                      # noqa: BLE001 - a malformed file
            out.write(f'REFUSED: --replay #{i + 1}: not a usable recording ({type(ex).__name__})\n')
            return EXIT_USAGE
        out.write(_clean(describe(rr, p), 400) + '\n')
        if verbose:
            for name, ok, detail in rr.replayed.assertions:
                out.write(f'    [{"x" if ok else " "}] {_clean(name, 80)}: {_clean(detail)}\n')
        if not rr.same:
            rc = EXIT_FAIL
    return rc


if __name__ == '__main__':
    sys.exit(main())
