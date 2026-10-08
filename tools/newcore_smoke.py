"""NEWCORE S5 READ-ONLY testnet smoke. The OWNER runs this on their PC after entering the testnet key with
tools/newcore_keys.py. It places NO orders, cancels nothing and changes no setting: it only reads.

    python tools/newcore_smoke.py --account-id <NEWCORE AccountId>

Reads: server time (clock calibration), exchangeInfo (core-8 filters), account balances, positions, position mode,
open classic + algo orders, and a 7-day income window. Prints a short summary and writes a sanitized cassette to
%LOCALAPPDATA%\\ZackBotNC\\cassettes\\smoke-<utc ms>.json (key header, signature, key and secret redacted; the file is
re-checked after writing and removed if any of them is found).

Exit codes: 0 all reads OK, flat, hedge mode; 1 all reads OK but with warnings (not flat / not hedge / missing
symbols); 2 refused usage; 3 no usable credentials; 4 a read failed (UNKNOWN or REJECTED) - nothing is retried;
5 the cassette could not be written or failed the leak check.
"""
import argparse
import os
import re
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from newcore.venue.cassette import CassetteLeak, CassetteRecorder  # noqa: E402
from newcore.venue.clock import OffsetClock, system_clock_ms  # noqa: E402
from newcore.venue.credentials import (CredentialsUnavailable, CredentialStore, CredentialStoreError,  # noqa: E402
                                       SecretScrubber, check_root)
from newcore.venue.guard import VenueGuardError  # noqa: E402
from newcore.venue.smoke import SmokeReadFailed, format_report, run_smoke  # noqa: E402
from newcore.venue.transport import BinanceTestnetTransport, PositionMode  # noqa: E402
from newcore.venue.wire import WireSeamError  # noqa: E402

_SECRET_FLAG = re.compile(r'^-{1,2}(api[-_]?key|key|apikey|secret|api[-_]?secret|password|passwd|token)(=.*)?$', re.I)
_TOKEN_LIKE = re.compile(r'[A-Za-z0-9]{24,}')
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
        out.write(f'ERROR: cassette not written ({type(ex).__name__}: {ex}).\n')
        return None, 5
    with open(path, 'rb') as fh:
        data = fh.read()
    if any(v.encode('utf-8') in data or v.encode('utf-16-le') in data for v in values):
        os.remove(path)
        out.write('ERROR: the written cassette contained a secret value and was removed.\n')
        return None, 5
    return path, 0


def main(argv=None, *, http=None, local_clock=None, protector=None, out=None):
    argv = list(sys.argv[1:] if argv is None else argv)
    out = out or sys.stdout
    if any(isinstance(a, str) and (_SECRET_FLAG.match(a) or (not any(c in a for c in '\\/:')
                                                             and _TOKEN_LIKE.search(a))) for a in argv):
        out.write('REFUSED: keys are never passed on the command line. Enter them with tools/newcore_keys.py.\n')
        return 2
    try:
        args = _parser().parse_args(argv)
    except SystemExit as ex:
        return 2 if ex.code else 0
    clock = local_clock or system_clock_ms
    scrubber = SecretScrubber()
    try:
        try:
            store = CredentialStore(args.account_id, root=args.root, protector=protector, harden_acl=False)
            creds = store.load(scrubber=scrubber)
            info = store.info()
        except CredentialsUnavailable as ex:
            out.write(f'NO USABLE TESTNET KEY ({ex.reason}): {ex}\n'
                      f'Enter it first:  python tools/newcore_keys.py set --env testnet --account-id {args.account_id}\n')
            return 3
        except CredentialStoreError as ex:
            out.write(f'REFUSED: {ex}\n')
            return 2
        scrubber.install()
        try:
            cassette_dir = check_root(args.cassette_dir or default_cassette_dir())   # legacy folder / repo refused
        except CredentialStoreError as ex:
            out.write(f'REFUSED: {ex}\n')
            return 2
        if http is None:
            from newcore.venue.http_sender import TestnetHttpSender      # the only network-capable module
            http = TestnetHttpSender()
        values = scrubber.redaction_values()
        recorder = CassetteRecorder(http, redact=values, note='S5 read-only smoke')
        oc = OffsetClock(clock)
        transport = BinanceTestnetTransport(environment='testnet', http=recorder, clock=oc,
                                            position_mode=PositionMode.HEDGE, credentials=creds)
        rc, report = 0, None
        try:
            report = run_smoke(transport, oc)
        except SmokeReadFailed as ex:
            out.write(f'READ FAILED - {ex.describe()}\nNothing was retried. {READ_ONLY_NOTE}\n')
            rc = 4
        except (VenueGuardError, WireSeamError) as ex:
            out.write(f'READ FAILED - seam refused: {type(ex).__name__}: {scrubber.scrub(ex)}\n')
            rc = 4
        path, crc = _write_cassette(recorder, cassette_dir, clock(), values, out)
        if report is not None:
            out.write(format_report(report, environment=info.environment, masked_key=info.masked_key,
                                    key_digest=info.key_digest) + '\n')
            rc = 1 if report.warnings else 0
        if path:
            out.write(f'cassette      : {path} ({len(recorder.interactions)} interactions, leak-checked)\n')
        out.write(READ_ONLY_NOTE + '\n')
        return crc or rc
    finally:
        scrubber.uninstall()


if __name__ == '__main__':
    sys.exit(main())
