"""Standalone market-data collector for ZackBot (public Binance USD-M futures endpoints, no API key).

Back-fills, then appends (idempotent: rows are de-duplicated by timestamp) open interest / long-short ratios / taker
ratio (Binance serves only the last ~30 days of these - collect them continuously), funding-rate history, funding
intervals, mark-price klines and exchangeInfo snapshots. Same code as the in-app collector (market_data.py).

  python tools/collect_market_data.py --once                      back-fill + append once, then exit
  python tools/collect_market_data.py --loop --every 4h           keep running (jittered), Ctrl+C to stop
  python tools/collect_market_data.py --once --symbols BTCUSDT,ETHUSDT --out D:/zb_market
  python tools/collect_market_data.py --testnet                   TESTNET exchangeInfo snapshot only (for BT02
                                                                  exchange_rules.py; builds data/exchange_rules_testnet.json
                                                                  when exchange_rules.py is present in the repo)

Universe (first that applies): --symbols, the installed app's settings.json UNIVERSE (%LOCALAPPDATA%/ZackBot), engine.py
TOP40, the built-in core 8. Output: one CSV per dataset/symbol/period under --out (default data_market/ in the repo),
plus manifest.json (rows, first/last timestamp, last run, source) and collector.log.
Exit code: 0 ok, 1 finished with errors, 2 bad arguments, 3 stopped (IP ban / rate limit / another run holds the lock).
"""
import argparse, ast, json, os, random, sys, time
from datetime import datetime, timezone

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path: sys.path.insert(0, ROOT)
import market_data as MD  # noqa: E402


def parse_every(v):
    """'4h' / '30m' / '90s' / '3600' -> seconds (minimum 15 min: Binance publishes these series per period)."""
    v = str(v).strip().lower()
    mult = {'h': 3600, 'm': 60, 's': 1}.get(v[-1:], None)
    n = float(v[:-1] if mult else v) * (mult or 1)
    if not (900 <= n <= 7 * 86400): raise argparse.ArgumentTypeError('--every must be between 15m and 7 days')
    return n


def top40_from_engine(root=ROOT):
    """TOP40 read from engine.py without importing it (no engine side effects / heavy imports)."""
    try:
        with open(os.path.join(root, 'engine.py'), encoding='utf-8') as f: tree = ast.parse(f.read())
        for node in tree.body:
            if isinstance(node, ast.Assign) and any(getattr(t, 'id', None) == 'TOP40' for t in node.targets):
                v = ast.literal_eval(node.value)
                if isinstance(v, list) and all(isinstance(x, str) for x in v): return v
    except (OSError, SyntaxError, ValueError):
        pass
    return None


def settings_universe():
    p = os.path.join(os.environ.get('LOCALAPPDATA', os.path.expanduser('~')), 'ZackBot', 'settings.json')
    u = (MD.read_json(p, {}) or {}).get('UNIVERSE')
    return [s for s in u if isinstance(s, str)] if isinstance(u, list) and u else None


def resolve_symbols(arg):
    if arg:
        syms = [s.strip().upper() for s in arg.split(',') if s.strip()]
        return [s if s.endswith('USDT') else s + 'USDT' for s in syms], '--symbols'
    for fn, why in ((settings_universe, 'app settings.json UNIVERSE'), (top40_from_engine, 'engine.py TOP40')):
        u = fn()
        if u: return u, why
    return list(MD.CORE8), 'built-in core 8'


def make_logger(out):
    path = os.path.join(out, 'collector.log')
    def log(msg):
        line = f"{datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M:%SZ')} {msg}"
        print(line, flush=True)
        try:
            os.makedirs(out, exist_ok=True)
            with open(path, 'a', encoding='utf-8') as f: f.write(line + '\n')
        except OSError:
            pass
    return log


def build_rules(latest_path, fetched_ms, log):
    """BT02: turn the raw testnet exchangeInfo into data/exchange_rules_testnet.json when exchange_rules.py exists."""
    try:
        import exchange_rules as ER
    except ImportError:
        log(f'exchange_rules.py not in this checkout - build the BT02 snapshot later with:  python exchange_rules.py build '
            f'"{latest_path}" --env testnet --fetched-at {MD.utc_iso(fetched_ms)} --source "{MD.TESTNET}/fapi/v1/exchangeInfo"')
        return None
    snap, _old = ER.build_file(latest_path, 'testnet', source=f'{MD.TESTNET}/fapi/v1/exchangeInfo', fetched_at=MD.utc_iso(fetched_ms))
    log(f"exchange_rules_testnet.json: {len(snap.get('symbols', {}))} symbols, version {snap.get('version')}")
    return snap


def run_once(a, log, transport=None, sleep=time.sleep, clock=time.time):
    out = os.path.abspath(a.out)
    if not MD.acquire_lock(out, clock=clock):
        log(f'another collector run holds {os.path.join(out, MD.LOCK_FILE)} - skipped'); return 3
    try:
        if a.testnet:
            c = MD.Collector(transport or MD.RequestsTransport(MD.TESTNET), out, source=MD.TESTNET, sleep=sleep, clock=clock, log=log)
            c.t0 = clock()
            try:
                info, latest, fetched = c.snapshot_exchange_info('testnet')
            except (MD.StopRun, MD.Transient, MD.Refused) as ex:
                log(f'testnet exchangeInfo failed: {ex}'); MD.atomic_write_json(os.path.join(out, 'manifest.json'), c.manifest); return 1
            MD.atomic_write_json(os.path.join(out, 'manifest.json'), c.manifest)
            log(f"testnet exchangeInfo: {len(info['symbols'])} symbols -> {latest}")
            if not a.no_rules: build_rules(latest, fetched, log)
            return 0
        syms, why = resolve_symbols(a.symbols)
        log(f'collecting {len(syms)} symbols ({why}) -> {out}')
        c = MD.Collector(transport or MD.RequestsTransport(MD.MAINNET), out, source=MD.MAINNET, sleep=sleep, clock=clock,
                         weight_per_min=a.weight_per_min, log=log)
        s = c.run(syms, periods=a.periods, run_kind='standalone')
        log(f"run done: +{s['rows_added']} rows, {s['requests']} requests, {s['errors']} errors, {s['seconds']}s"
            + (f" - STOPPED: {s['stopped']}" if s['stopped'] else ''))
        return 3 if s['stopped'] else (1 if s['errors'] else 0)
    finally:
        MD.release_lock(out)


def main(argv=None, transport=None, sleep=time.sleep, clock=time.time):
    ap = argparse.ArgumentParser(description='ZackBot public market-data collector (no API key).')
    mode = ap.add_mutually_exclusive_group()
    mode.add_argument('--once', action='store_true', help='back-fill + append once and exit (default)')
    mode.add_argument('--loop', action='store_true', help='keep collecting every --every (jittered)')
    ap.add_argument('--every', type=parse_every, default=4 * 3600, help='loop interval, e.g. 4h (default), 1h, 30m')
    ap.add_argument('--symbols', default='', help='comma list, e.g. BTCUSDT,ETHUSDT (default: the app universe)')
    ap.add_argument('--out', default=os.path.join(ROOT, 'data_market'), help='data directory (default data_market/)')
    ap.add_argument('--periods', default='1h,4h', help='periods for the period series (default 1h,4h)')
    ap.add_argument('--weight-per-min', type=float, default=1200, help='request weight budget per minute (Binance IP limit 2400)')
    ap.add_argument('--testnet', action='store_true', help='only snapshot the TESTNET exchangeInfo (BT02 exchange rules)')
    ap.add_argument('--no-rules', action='store_true', help='with --testnet: do not build data/exchange_rules_testnet.json')
    a = ap.parse_args(argv)
    a.periods = tuple(p for p in a.periods.split(',') if p in MD.PERIOD_MS) or ('1h', '4h')
    log = make_logger(os.path.abspath(a.out))
    if not a.loop: return run_once(a, log, transport, sleep, clock)
    try:
        while True:
            run_once(a, log, transport, sleep, clock)
            wait = a.every * random.uniform(0.95, 1.05)
            log(f'next run in {wait / 3600:.2f} h (Ctrl+C to stop)')
            sleep(wait)
    except KeyboardInterrupt:
        log('stopped by Ctrl+C'); return 0


if __name__ == '__main__':
    sys.exit(main())
