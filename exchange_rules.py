"""Exchange-rule snapshots (BT02, issue #14): build / load / compare the versioned JSON files under data/.

  data/exchange_rules_testnet.json   rules of the Binance USD-M futures TESTNET
  data/exchange_rules_mainnet.json   rules of MAINNET (not shipped yet - build it before any mainnet decision)
Testnet and mainnet rules are kept apart and never assumed identical.

Build from an exchangeInfo JSON file (GET https://testnet.binancefuture.com/fapi/v1/exchangeInfo, saved with a browser or curl):
  python exchange_rules.py build exchangeInfo.json --env testnet [--source "..."] [--fetched-at 2026-10-07T12:00:00Z]
Fetch it directly (public endpoint, no API key) and build:
  python exchange_rules.py fetch --env testnet
Show the rule changes between two snapshots:
  python exchange_rules.py diff old.json new.json

A snapshot built from a real exchangeInfo answer is marked verified=true with its fetch time. The pure parsing / checks live
in feasibility.py (shared with the engine, the backtester and the panel).
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
    """The snapshot dict for this environment, or None when the file is missing / unreadable (-> state 'unavailable')."""
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


def build_file(info_path, environment, out=None, source=None, fetched_at=None, version=None):
    with open(info_path, encoding='utf-8') as f: info = json.load(f)
    old = load(environment, path=out)
    snap = F.build_snapshot(info, environment, source or f'exchangeInfo file {os.path.basename(info_path)}',
                            fetched_at=fetched_at or _now_iso(), verified=True,
                            version=version or ((old or {}).get('version', 0) + 1), note='built from exchangeInfo')
    save(snap, out or path_for(environment))
    return snap, old


def fetch(environment, out=None):
    import requests
    r = requests.get(BASES[environment] + '/fapi/v1/exchangeInfo', timeout=20)
    r.raise_for_status()
    info = r.json()
    old = load(environment, path=out)
    snap = F.build_snapshot(info, environment, f'{BASES[environment]}/fapi/v1/exchangeInfo', fetched_at=_now_iso(), verified=True,
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
    if argv[0] == 'build': snap, old = build_file(argv[1], env, opt('--out'), opt('--source'), opt('--fetched-at'))
    else: snap, old = fetch(env, opt('--out'))
    print(f"{env}: {len(snap['symbols'])} symbols, version {snap['version']}, fetched {snap['fetched_at']}")
    if old: print('changes vs previous snapshot:', json.dumps(F.diff_snapshots(old, snap)))
    return 0


if __name__ == '__main__':
    sys.exit(main(sys.argv[1:]))
