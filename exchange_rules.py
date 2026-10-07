"""Exchange-rule snapshots (BT02, issue #14): build / load / compare the versioned JSON files under data/.

  data/exchange_rules_testnet.json   rules of the Binance USD-M futures TESTNET
  data/exchange_rules_mainnet.json   rules of MAINNET
NO rule file is shipped: a missing file is state 'unavailable' (shown as unknown, never a pass), and backtests then run
the legacy floor with "exchange rules NOT applied". Testnet and mainnet rules are kept apart and never assumed identical.

Capture the TRUSTED snapshot (the only way to get state 'ok' from a file): fetch it directly (public endpoint, no API key):
  python exchange_rules.py fetch --env testnet [--out PATH]
  (the panel reads PATH = %LOCALAPPDATA%\\ZackBot\\exchange_rules_testnet.json first, then data/ next to app.py)
Import an exchangeInfo JSON file (saved with a browser or curl) - for inspection / diffs only, it is NEVER trusted:
  python exchange_rules.py build exchangeInfo.json --env testnet [--source "..."] [--captured-at 2026-10-07T12:00:00Z]
  -> verified=false, provenance 'file'; capture time = the file's serverTime, else --captured-at, else none.
Show the rule changes between two snapshots:
  python exchange_rules.py diff old.json new.json

The pure parsing / checks live in feasibility.py (shared with the engine, the backtester and the panel).
"""
import json, os, sys, time
from datetime import datetime, timezone
import feasibility as F

HERE = os.path.dirname(os.path.abspath(__file__))
BASES = dict(testnet='https://testnet.binancefuture.com', mainnet='https://fapi.binance.com')


def path_for(environment, root=None):
    if environment not in F.ENVIRONMENTS: raise ValueError(f'environment must be one of {F.ENVIRONMENTS}')
    return os.path.join(root or os.path.join(HERE, 'data'), f'exchange_rules_{environment}.json')


def load(environment, root=None, path=None):
    """The snapshot dict for this environment, or None when the file is missing / unreadable (-> state 'unavailable').
    Nothing is shipped: a missing file is normal until `fetch` creates it."""
    p = path or path_for(environment, root)
    try:
        with open(p, encoding='utf-8') as f: return json.load(f)
    except (OSError, ValueError):
        return None


def save(snap, path):
    tmp = path + '.tmp'
    with open(tmp, 'w', encoding='utf-8') as f: json.dump(snap, f, indent=1, sort_keys=True); f.write('\n')
    os.replace(tmp, path)


def state(environment, now=None, root=None, max_age_days=F.MAX_AGE_DAYS):
    """(snapshot, state, detail) for the panel / backtests."""
    snap = load(environment, root)
    st, detail = F.snapshot_state(snap, time.time() if now is None else now, environment, max_age_days)
    return snap, st, detail


def _now_iso():
    return datetime.now(timezone.utc).isoformat(timespec='seconds').replace('+00:00', 'Z')


def _iso(epoch):
    return datetime.fromtimestamp(float(epoch), timezone.utc).isoformat(timespec='seconds').replace('+00:00', 'Z')


def capture_time(info, captured_at=None):
    """The real capture time of an exchangeInfo answer: its serverTime (ms), else the explicit captured_at, else None.
    Never the current clock (a file can be months old)."""
    st = info.get('serverTime') if isinstance(info, dict) else None
    if isinstance(st, (int, float)) and st > 0: return _iso(st / 1000.0)
    if captured_at is not None:
        if F._ts(captured_at) is None: raise ValueError(f'--captured-at {captured_at!r} is not an ISO-8601 time')
        return str(captured_at)
    return None


def build_file(info_path, environment, out=None, source=None, captured_at=None, version=None):
    """Import an exchangeInfo file. Always verified=False / provenance 'file': a file cannot prove which environment it
    came from or that it is current, so it is never state 'ok' (only `fetch` produces a trusted snapshot)."""
    with open(info_path, encoding='utf-8') as f: info = json.load(f)
    old = load(environment, path=out)
    snap = F.build_snapshot(info, environment, source or f'exchangeInfo file {os.path.basename(info_path)}',
                            fetched_at=capture_time(info, captured_at), verified=False, provenance='file',
                            version=version or ((old or {}).get('version', 0) + 1),
                            note='imported from a file - not trusted; run: python exchange_rules.py fetch --env ' + environment)
    save(snap, out or path_for(environment))
    return snap, old


def fetch(environment, out=None, get=None):
    """Direct GET of /fapi/v1/exchangeInfo from this environment's base URL -> the only trusted snapshot (verified,
    provenance 'fetch', capture time = the answer's serverTime, else now)."""
    if get is None:
        import requests
        get = requests.get
    url = BASES[environment] + '/fapi/v1/exchangeInfo'
    r = get(url, timeout=20)
    r.raise_for_status()
    info = r.json()
    old = load(environment, path=out)
    snap = F.build_snapshot(info, environment, url, fetched_at=capture_time(info) or _now_iso(), verified=True, provenance='fetch',
                            version=(old or {}).get('version', 0) + 1, note='fetched from exchangeInfo')
    save(snap, out or path_for(environment))
    return snap, old


def main(argv):
    if not argv or argv[0] not in ('build', 'fetch', 'diff'): print(__doc__); return 2
    opt = lambda k, d=None: argv[argv.index(k) + 1] if k in argv else d
    if argv[0] == 'diff':
        a, b = (json.load(open(x, encoding='utf-8')) for x in argv[1:3])
        print(json.dumps(F.diff_snapshots(a, b), indent=1)); return 0
    env = opt('--env', 'testnet')
    if argv[0] == 'build':
        if '--fetched-at' in argv: print('--fetched-at was renamed --captured-at', file=sys.stderr); return 2
        snap, old = build_file(argv[1], env, opt('--out'), opt('--source'), opt('--captured-at'))
        print('WARNING: an imported file is NOT trusted (verified=false, provenance file): backtests will not apply it. '
              f'Run: python exchange_rules.py fetch --env {env}', file=sys.stderr)
    else: snap, old = fetch(env, opt('--out'))
    print(f"{env}: {len(snap['symbols'])} symbols, version {snap['version']}, captured {snap['fetched_at']}, "
          f"provenance {snap['provenance']}, verified {snap['verified']}")
    if old: print('changes vs previous snapshot:', json.dumps(F.diff_snapshots(old, snap)))
    return 0


if __name__ == '__main__':
    sys.exit(main(sys.argv[1:]))
