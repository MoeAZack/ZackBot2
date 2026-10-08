"""NEWCORE S5 testnet smoke. The OWNER runs this on their PC after entering the testnet key with tools/newcore_keys.py.

    python tools/newcore_smoke.py --account-id <AccountId> --dry-run [--trade]   print the plan; NO network, no key read
    python tools/newcore_smoke.py --account-id <AccountId>                       READ-ONLY (default)
    python tools/newcore_smoke.py --account-id <AccountId> --trade               reads, then ONE minimum-size round trip

(<AccountId> = the NEWCORE AccountId, a dashed UUID.)

READ-ONLY (default): server time (clock calibration), exchangeInfo (core-8 filters), account balances, positions,
position mode, open classic + algo orders, and a 7-day income window. No order, cancel or setting change.
--trade (TESTNET only; the transport is hard-pinned): only if every read was OK, hedge mode, flat and the symbol's rules
are known: price -> MARKET BUY LONG min size -> reduce-only STOP_MARKET (--stop-route algo|classic, default algo) ->
verify -> cancel + confirm -> MARKET SELL LONG (reduce) -> flat check -> fees. See newcore/venue/smoke_trade.py.
A sanitized cassette of everything sent is written to %LOCALAPPDATA%\\ZackBotNC\\cassettes\\smoke-<utc ms>.json (never
the repo, never the legacy ZackBot folder), re-checked after writing and removed if a secret is found.

Exit codes: 0 all reads OK (and the trade round trip done), flat, hedge mode; 1 reads OK with warnings; 2 refused usage;
3 no usable credentials; 4 a read failed (UNKNOWN / REJECTED) - nothing is retried; 5 cassette not written / leak;
6 the overall read deadline (--deadline-s, default 60 s) was exceeded; 7 the trade phase stopped with NO exposure left;
8 the trade phase stopped and a position MAY be left: check testnet by the printed client ids.
"""
import argparse
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from newcore.venue.cassette import CassetteLeak, CassetteRecorder  # noqa: E402
from newcore.venue.cli_args import ACCOUNT_REFUSAL, argv_refusal  # noqa: E402
from newcore.venue.clock import OffsetClock, system_clock_ms  # noqa: E402
from newcore.venue.credentials import (CredentialsUnavailable, CredentialStore, CredentialStoreError,  # noqa: E402
                                       SecretScrubber, check_root)
from newcore.venue.guard import VenueGuardError  # noqa: E402
from newcore.venue.smoke import (CORE8, DEFAULT_DEADLINE_S, SmokeDeadlineExceeded, SmokeReadFailed,  # noqa: E402
                                 format_report, run_smoke)
from newcore.venue.smoke_trade import ROUTES as TRADE_ROUTES  # noqa: E402
from newcore.venue.smoke_trade import (TradeAborted, format_plan, format_trade_report, run_trade_smoke,  # noqa: E402
                                       smoke_ids)
from newcore.venue.testnet_venue import HedgeModeRequired, TestnetVenue, VenueBootUnknown  # noqa: E402
from newcore.venue.transport import BinanceTestnetTransport, PositionMode  # noqa: E402
from newcore.venue.wire import WireSeamError  # noqa: E402
from newcore.venue.safe_text import exc_msg, exc_text  # noqa: E402

READ_ONLY_NOTE = 'READ-ONLY smoke: no order was placed or cancelled and no setting was changed.'


def default_cassette_dir():
    base = os.environ.get('LOCALAPPDATA')
    if not base:
        raise CredentialStoreError('LOCALAPPDATA is not set: pass --cassette-dir')
    return os.path.join(base, 'ZackBotNC', 'cassettes')


def _parser():
    p = argparse.ArgumentParser(prog='newcore_smoke', description='NEWCORE S5 read-only testnet smoke.')
    p.add_argument('--account-id', required=True, help='the NEWCORE AccountId whose stored testnet key to use')
    p.add_argument('--root', default=None, help='credential folder (default %%LOCALAPPDATA%%\\ZackBotNC\\secrets)')
    p.add_argument('--cassette-dir', default=None,
                   help='cassette folder (default %%LOCALAPPDATA%%\\ZackBotNC\\cassettes)')
    p.add_argument('--deadline-s', type=float, default=DEFAULT_DEADLINE_S,
                   help='overall wall-clock cap for the read phase in seconds (default 60, max 3600)')
    p.add_argument('--dry-run', action='store_true', help='print the plan only: no network call, no key decrypted')
    p.add_argument('--trade', action='store_true', help='after the reads, ONE minimum-size testnet round trip')
    p.add_argument('--symbol', default='SOLUSDT', choices=CORE8, help='symbol for --trade (default SOLUSDT)')
    p.add_argument('--stop-route', default='algo', choices=TRADE_ROUTES, help='protective stop endpoint (default algo)')
    return p


def _write_cassette(recorder, directory, stamp_ms, values, out):
    """Save, then re-read and re-check for every registered value. Returns (path or None, exit code or 0)."""
    try:
        check_root(directory)
        os.makedirs(directory, exist_ok=True)
        path = recorder.save(os.path.join(directory, f'smoke-{stamp_ms}.json'))
    except CassetteLeak:
        out.write('ERROR: the cassette failed the leak check before writing; nothing was written.\n')
        return None, 5
    except (CredentialStoreError, OSError) as ex:
        out.write(f'ERROR: cassette not written ({exc_text(ex)}).\n')
        return None, 5
    with open(path, 'rb') as fh:
        data = fh.read()
    if any(v.encode('utf-8') in data or v.encode('utf-16-le') in data for v in values):
        os.remove(path)
        out.write('ERROR: the written cassette contained a secret value and was removed.\n')
        return None, 5
    return path, 0


def _trade(args, transport, oc, report, clock, out):
    """The --trade phase through the TestnetVenue port. Returns 0, 7 (stopped, no exposure) or 8 (MAY be exposed)."""
    venue = TestnetVenue(transport, oc)
    ids = smoke_ids(args.account_id, clock(), args.symbol, args.stop_route)
    try:
        venue.check_hedge_mode()
        rep = run_trade_smoke(venue, transport, report, symbol=args.symbol, ids=ids, clock=oc)
    except (HedgeModeRequired, VenueBootUnknown) as ex:
        out.write(f'TRADE REFUSED: {exc_msg(ex)}. No order was placed.\n')
        return 7
    except TradeAborted as ex:
        if ex.exposure_possible:
            out.write(f'TRADE STOPPED - POSITION MAY BE OPEN ON TESTNET: {exc_msg(ex)}\n'
                      f'Check the testnet UI for these client ids and close manually if needed.\n')
            return 8
        out.write(f'TRADE STOPPED (no exposure left): {exc_msg(ex)}\n')
        return 7
    out.write(format_trade_report(rep) + '\n')
    out.write('TRADE phase done on TESTNET: one minimum-size round trip, protective stop placed, verified and '
              'cancelled, position closed, account flat.\n')
    return 0


def main(argv=None, *, http=None, local_clock=None, protector=None, out=None, monotonic=None):
    argv = list(sys.argv[1:] if argv is None else argv)
    out = out or sys.stdout
    refusal = argv_refusal(argv)
    if refusal == ACCOUNT_REFUSAL:
        out.write(f'REFUSED: {ACCOUNT_REFUSAL}.\n')
        return 2
    if refusal is not None:
        out.write('REFUSED: keys are never passed on the command line. Enter them with tools/newcore_keys.py.\n')
        return 2
    try:
        args = _parser().parse_args(argv)
    except SystemExit as ex:
        return 2 if ex.code else 0
    if not 0 < args.deadline_s <= 3600:
        out.write('REFUSED: --deadline-s must be in (0, 3600].\n')
        return 2
    clock = local_clock or system_clock_ms
    try:
        cassette_dir = check_root(args.cassette_dir or default_cassette_dir())       # legacy folder / repo refused
        store = CredentialStore(args.account_id, root=args.root, protector=protector, harden_acl=False)
    except (CredentialStoreError, CredentialsUnavailable) as ex:
        out.write(f'REFUSED: {exc_msg(ex)}\n')
        return 2
    if args.dry_run:                       # the plan only: no sender is built, nothing is decrypted or sent
        stamp = clock()
        out.write(format_plan(trade=args.trade, symbol=args.symbol, stop_route=args.stop_route,
                              cassette_path=os.path.join(cassette_dir, f'smoke-{stamp}.json'),
                              account_id=args.account_id, credentials_stored=store.exists()) + '\n')
        return 0
    scrubber = SecretScrubber()
    try:
        try:
            creds = store.load(scrubber=scrubber)
            info = store.info()
        except CredentialsUnavailable as ex:
            out.write(f'NO USABLE TESTNET KEY ({ex.reason}): {exc_msg(ex)}\n'
                      f'Enter it first:  python tools/newcore_keys.py set --env testnet --account-id {args.account_id}\n')
            return 3
        except CredentialStoreError as ex:
            out.write(f'REFUSED: {exc_msg(ex)}\n')
            return 2
        scrubber.install()
        if http is None:
            from newcore.venue.http_sender import TestnetHttpSender      # the only network-capable module
            http = TestnetHttpSender()
        values = scrubber.redaction_values()
        recorder = CassetteRecorder(http, redact=values,
                                    note='S5 smoke ' + ('with --trade' if args.trade else 'read-only'))
        oc = OffsetClock(clock)
        transport = BinanceTestnetTransport(environment='testnet', http=recorder, clock=oc,
                                            position_mode=PositionMode.HEDGE, credentials=creds, scrubber=scrubber,
                                            timeout_s=min(10.0, args.deadline_s))
        rc, report = 0, None
        try:
            report = run_smoke(transport, oc, deadline_s=args.deadline_s, monotonic=monotonic)
        except SmokeDeadlineExceeded as ex:
            out.write(f'SMOKE DEADLINE - {ex.describe()}. The run was stopped; nothing was retried. '
                      f'The sanitized cassette is saved as evidence. {READ_ONLY_NOTE}\n')
            rc = 6
        except SmokeReadFailed as ex:
            out.write(f'READ FAILED - {ex.describe()}\nNothing was retried. {READ_ONLY_NOTE}\n')
            rc = 4
        except (VenueGuardError, WireSeamError) as ex:
            out.write(f'READ FAILED - seam refused: {exc_text(ex)}\n')
            rc = 4
        if report is not None:
            out.write(format_report(report, environment=info.environment, masked_key=info.masked_key,
                                    key_digest=info.key_digest) + '\n')
            rc = 1 if report.warnings else 0
        traded = False
        if args.trade and report is None:
            out.write('TRADE SKIPPED: the read phase did not complete; no order was placed.\n')
        elif args.trade:
            traded = True
            rc = _trade(args, transport, oc, report, clock, out) or rc
        path, crc = _write_cassette(recorder, cassette_dir, clock(), values, out)
        if path:
            out.write(f'cassette      : {path} ({len(recorder.interactions)} interactions, leak-checked)\n')
        if not traded:
            out.write(READ_ONLY_NOTE + '\n')
        return rc if rc in (7, 8) else (crc or rc)
    finally:
        scrubber.uninstall()


if __name__ == '__main__':
    sys.exit(main())
