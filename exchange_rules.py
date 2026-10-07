"""Exchange-rule snapshots (BT02, issue #14): build / load / compare the versioned JSON files under data/.

  data/exchange_rules_testnet.json   rules of the Binance USD-M futures TESTNET
  data/exchange_rules_mainnet.json   rules of MAINNET (not shipped yet - build it before any mainnet decision)
Testnet and mainnet rules are kept apart and never assumed identical.

Fetch it directly (public endpoint, no API key) and build - the ONLY way to get a verified snapshot automatically:
  python exchange_rules.py fetch --env testnet
Import a saved exchangeInfo JSON file (always UNVERIFIED: a file cannot prove which environment it came from or when):
  python exchange_rules.py build exchangeInfo.json --env testnet [--source "..."] [--fetched-at 2026-10-07T12:00:00Z]
  The capture time is --fetched-at, else the file's own serverTime, else none (state 'stale'); it is never "now".
  --trust marks the import verified anyway: a loud warning, provenance 'file_import_trusted' (never 'direct_fetch'),
  and the environment must match a --source URL when one is given.
Show the rule changes between two snapshots:
  python exchange_rules.py diff old.json new.json

Provenance recorded in every snapshot: provenance ('direct_fetch' | 'file_import' | 'file_import_trusted'), source /
source_url, fetched_at, server_time, raw_sha256 (SHA-256 of the exact bytes received / read). The pure parsing / checks
live in feasibility.py (shared with the engine, the backtester and the panel).
"""
import hashlib, json, os, sys, time
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


def _iso_ms(ms):
    return datetime.fromtimestamp(int(ms) / 1000, timezone.utc).isoformat(timespec='seconds').replace('+00:00', 'Z')


def env_of_url(url):
    """'testnet' / 'mainnet' when the URL's host is one of BASES, else None."""
    for env, base in BASES.items():
        if str(url or '').startswith(base): return env
    return None


def _with_provenance(snap, provenance, raw, info, source_url=None):
    snap.update(provenance=provenance, raw_sha256=hashlib.sha256(raw).hexdigest(), source_url=source_url,
                server_time=_iso_ms(info['serverTime']) if isinstance(info.get('serverTime'), (int, float)) else None)
    return snap


def build_file(info_path, environment, out=None, source=None, fetched_at=None, version=None, trust=False, warn=None):
    """Import a saved exchangeInfo file. UNVERIFIED unless trust=True (which is loud and recorded as 'file_import_trusted').
    fetched_at: given, else the file's serverTime, else None - never the current time (BT02 review P2)."""
    with open(info_path, 'rb') as f: raw = f.read()
    info = json.loads(raw.decode('utf-8'))
    if not isinstance(info, dict) or not isinstance(info.get('symbols'), list): raise ValueError('not an exchangeInfo answer')
    if trust and env_of_url(source) not in (None, environment):
        raise ValueError(f'--source is a {env_of_url(source)} URL but --env is {environment}: refusing to trust it')
    st = info.get('serverTime')
    when = fetched_at or (_iso_ms(st) if isinstance(st, (int, float)) and not isinstance(st, bool) else None)
    old = load(environment, path=out)
    note = ('imported from a file and TRUSTED by hand (--trust) - not a direct fetch' if trust
            else 'imported from a file - unverified (only a direct fetch is verified); refresh with: exchange_rules.py fetch')
    if trust and warn: warn(f'WARNING: marking {os.path.basename(info_path)} as VERIFIED {environment} rules by hand. Only do this for '
                            f'a file you fetched yourself from {BASES[environment]}/fapi/v1/exchangeInfo. Recorded as file_import_trusted.')
    snap = F.build_snapshot(info, environment, source or f'exchangeInfo file {os.path.basename(info_path)}', fetched_at=when,
                            verified=bool(trust), version=version or ((old or {}).get('version', 0) + 1), note=note)
    _with_provenance(snap, 'file_import_trusted' if trust else 'file_import', raw, info,
                     source_url=source if env_of_url(source) else None)
    save(snap, out or path_for(environment))
    return snap, old


def fetch(environment, out=None, get=None):
    """Direct public fetch of this environment's exchangeInfo (no API key) -> a verified snapshot ('direct_fetch')."""
    url = f'{BASES[environment]}/fapi/v1/exchangeInfo'
    if get is None:
        import requests
        get = lambda u: requests.get(u, timeout=20)
    r = get(url)
    r.raise_for_status()
    raw = r.content
    info = json.loads(raw.decode('utf-8'))
    old = load(environment, path=out)
    snap = F.build_snapshot(info, environment, url, fetched_at=_now_iso(), verified=True,
                            version=(old or {}).get('version', 0) + 1, note='fetched directly from exchangeInfo')
    _with_provenance(snap, 'direct_fetch', raw, info, source_url=url)
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
        snap, old = build_file(argv[1], env, opt('--out'), opt('--source'), opt('--fetched-at'), trust='--trust' in argv,
                               warn=lambda m: print(m, file=sys.stderr))
    else: snap, old = fetch(env, opt('--out'))
    print(f"{env}: {len(snap['symbols'])} symbols, version {snap['version']}, fetched {snap['fetched_at']}, "
          f"{snap['provenance']}, verified={snap['verified']}, raw sha256 {snap['raw_sha256'][:12]}")
    if old: print('changes vs previous snapshot:', json.dumps(F.diff_snapshots(old, snap)))
    return 0


if __name__ == '__main__':
    sys.exit(main(sys.argv[1:]))
