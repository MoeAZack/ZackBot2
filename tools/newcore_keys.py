"""NEWCORE testnet key entry (console). The OWNER runs this; keys are typed at hidden prompts, never passed as arguments.

    python tools/newcore_keys.py set    --env testnet --account-id <NEWCORE AccountId, a dashed UUID>
    python tools/newcore_keys.py status --account-id <NEWCORE AccountId, a dashed UUID>
    python tools/newcore_keys.py clear  --account-id <NEWCORE AccountId, a dashed UUID> --yes

The key and secret are stored DPAPI-encrypted (CurrentUser) at %LOCALAPPDATA%\\ZackBotNC\\secrets\\<account_id>.bin
(--root overrides the folder; the legacy %LOCALAPPDATA%\\ZackBot folder and the repository are refused).
Storing a key NEVER enables trading or live mode. Mainnet keys are refused. A different key for the same AccountId is a
rotation: NEWCORE must treat the binding as unconfirmed (HOLD + reconcile + confirm) before trading on it.
A GUI entry screen is deferred until NEWCORE has an app shell.

Exit codes: 0 ok, 2 refused usage (incl. secrets on the command line), 3 mainnet refused, 4 no usable credentials,
5 store write/remove error.
"""
import argparse
import getpass
import os
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from newcore.venue.cli_args import ACCOUNT_REFUSAL, argv_refusal  # noqa: E402
from newcore.venue.credentials import (CredentialsUnavailable, CredentialStore, CredentialStoreError,  # noqa: E402
                                       MainnetCredentialRefused, SecretScrubber)

NOT_TRADING = 'This does NOT enable trading or live mode.'


def argv_carries_secret(argv):
    """True if argv is refused (a key/secret flag, a key-like token, or an --account-id that is not a dashed UUID)."""
    return argv_refusal(argv) is not None


def fmt_time(ms):
    """Cairo local time first (owner preference), UTC in parentheses. Falls back to UTC only without tz data."""
    import datetime as dt
    utc = dt.datetime.fromtimestamp(ms / 1000, tz=dt.timezone.utc)
    try:
        from zoneinfo import ZoneInfo
        cairo = utc.astimezone(ZoneInfo('Africa/Cairo'))
        return f'{cairo:%Y-%m-%d %H:%M} Cairo ({utc:%H:%M} UTC)'
    except Exception:
        return f'{utc:%Y-%m-%d %H:%M} UTC'


def _parser():
    p = argparse.ArgumentParser(prog='newcore_keys', description='NEWCORE testnet key entry (keys are prompted, '
                                                                 'never given as arguments).')
    sub = p.add_subparsers(dest='cmd', required=True)
    for name in ('set', 'status', 'clear'):
        s = sub.add_parser(name)
        s.add_argument('--account-id', required=True, help='the NEWCORE AccountId this key belongs to')
        s.add_argument('--root', default=None, help='credential folder (default %%LOCALAPPDATA%%\\ZackBotNC\\secrets)')
        if name == 'set':
            s.add_argument('--env', required=True, help='must be "testnet"')
        if name == 'clear':
            s.add_argument('--yes', action='store_true', help='confirm removal')
    return p


def _print_info(info, out):
    out.write(f'environment : {info.environment}\n')
    out.write(f'account_id  : {info.account_id}\n')
    out.write(f'api key     : {info.masked_key}\n')
    out.write(f'key digest  : {info.key_digest}\n')
    out.write(f'created     : {fmt_time(info.created_ms)}\n')
    if info.rotated:
        out.write(f'ROTATED     : replaced key {info.previous_key_digest} - the binding is UNCONFIRMED until '
                  f'NEWCORE reconciles and the owner confirms.\n')


def main(argv=None, *, prompt=None, out=None, now_ms=None, scrubber=None, protector=None, harden_acl=True):
    argv = list(sys.argv[1:] if argv is None else argv)
    out = out or sys.stdout
    refusal = argv_refusal(argv)
    if refusal == ACCOUNT_REFUSAL:
        out.write(f'REFUSED: {ACCOUNT_REFUSAL}.\n')
        return 2
    if refusal is not None:
        out.write('REFUSED: never pass an API key or secret on the command line (shell history, process list).\n'
                  '         Run "set" and type them at the hidden prompts.\n')
        return 2
    try:
        args = _parser().parse_args(argv)
    except SystemExit as ex:
        return 2 if ex.code else 0
    try:
        store = CredentialStore(args.account_id, root=args.root, protector=protector, harden_acl=harden_acl)
    except (CredentialStoreError, CredentialsUnavailable) as ex:
        out.write(f'REFUSED: {ex}\n')
        return 2

    if args.cmd == 'status':
        try:
            info = store.info()
        except CredentialsUnavailable as ex:
            out.write(f'NO USABLE CREDENTIALS ({ex.reason}): {ex}\n')
            return 4
        _print_info(info, out)
        out.write(NOT_TRADING + '\n')
        return 0

    if args.cmd == 'clear':
        if not args.yes:
            out.write('REFUSED: add --yes to remove the stored credential.\n')
            return 2
        try:
            removed = store.clear()
        except CredentialStoreError as ex:
            out.write(f'ERROR: {ex}\n')
            return 5
        out.write('removed\n' if removed else 'nothing stored\n')
        return 0

    # set
    if args.env == 'mainnet':
        out.write('REFUSED: NEWCORE is testnet-only; mainnet keys are not stored.\n')
        return 3
    if args.env != 'testnet':
        out.write('REFUSED: --env must be "testnet".\n')
        return 2
    ask = prompt or getpass.getpass
    scrub = scrubber or SecretScrubber()
    key = ask('Testnet API key (input hidden): ').strip()
    secret = ask('Testnet API secret (input hidden): ').strip()
    if len(key) < 8 or len(secret) < 8:
        key = secret = None
        out.write('REFUSED: the key and the secret must each be at least 8 characters (Binance keys are 64). '
                  'Nothing was stored.\n')
        return 2
    scrub.register(key, secret)
    scrub.install()
    try:
        info = store.save('testnet', key, secret, now_ms=now_ms if now_ms is not None else int(time.time() * 1000))
    except MainnetCredentialRefused as ex:
        out.write(f'REFUSED: {ex}\n')
        return 3
    except CredentialStoreError as ex:
        out.write(f'ERROR: {scrub.scrub(ex)}\n')
        return 5
    finally:
        key = secret = None
    out.write('stored\n')
    _print_info(info, out)
    if store.acl_restricted is False:
        out.write('note        : folder ACL could not be restricted; the file is still DPAPI-encrypted for this '
                  'Windows user.\n')
    out.write(NOT_TRADING + '\n')
    return 0


if __name__ == '__main__':
    sys.exit(main())
