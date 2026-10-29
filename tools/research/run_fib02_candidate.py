"""FIB-PULLBACK-02 development runner (research only; preregistration research_evidence/prereg/fib_pullback_02.md).

The run IS the frozen shared cost-aware candidate runner, tools/research/run_breadth_candidate.run (byte-identical, pinned in
tests/test_breadth_freeze_pins.py; the same runner FIB-PULLBACK-01 used), for exactly one key, `fib_pullback_02`, with the
frozen strategy management (--mgmt defaults) and one side per call. Two runner constraints force a difference; both are
preregistered explicit equivalents, scoped to the call and restored in `finally`:

  E1 timeframe. The frozen runner's cached-timeframe table (TFS) holds 4h and 15m only. For the call, '1h' (3,600,000 ms)
     is added. Everything else is the runner's own tf-generic path: a 1h research cache written by
     tools/research/collect_klines_research.py --interval 1h (app CSV format <SYMBOL>_1h.csv), completeness on the 1h grid
     (a symbol with any missing 1h candle in [warm-up, END) is INCOMPLETE and excluded, never filled), the 220-candle
     warm-up inside the runner's 4h warm-up guard, funding FUND_PER_BAR x 3600 / 14400 per 1h candle, and the BTC regime
     split from a 4h cache (--regime-cache).
  E2 exchange floors. The frozen runner calls backtest.run(exchange_rules=None), the legacy notional-only floor. For the
     call, backtest.run receives the committed, directly fetched mainnet snapshot data/exchange_rules_mainnet.json
     (step / minQty / minNotional; an entry below the floor is skipped, never rounded up - backtest BT02). The packet's
     costs.exchange_rules text is replaced, a `fib02` block records the snapshot identity and the feasibility counts, and
     packet_sha256 is recomputed exactly the runner's way (sha256 of the canonical packet without the field).

No parameter grid, no params / mgmt override and no symbol list on the command line (--tier only). Never reads anything
at or after the sealed cutoff (the runner's window guard). Reads no settings, credentials or app data folder.

Usage (Phase B, separate go-ahead):
  py -3.14 tools/research/run_fib02_candidate.py --cache <1h cache> --regime-cache <4h cache> \
     --window 2024-10-14T00:00:00Z/2026-09-28T00:00:00Z --tier CORE-20 --side long --out <packet.json>
"""
import argparse
import contextlib
import hashlib
import json
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(os.path.dirname(HERE))
for _p in (ROOT, HERE):
    if _p not in sys.path:
        sys.path.insert(0, _p)

import backtest as BT                                                                       # noqa: E402
import run_breadth_candidate as B                                                           # noqa: E402

KEY = 'fib_pullback_02'
TF, TF_MS = '1h', 3_600_000
PREREG = 'research_evidence/prereg/fib_pullback_02.md'
RULES = 'data/exchange_rules_mainnet.json'
FORMAT = 'zb-fib02-envelope/1'


class Fib02Error(RuntimeError):
    pass


def _sha_file(path) -> str:
    with open(path, 'rb') as f:
        return hashlib.sha256(f.read()).hexdigest()


def load_rules(path) -> tuple[dict, dict]:
    """The committed mainnet exchange-rule snapshot, verified direct fetch only."""
    with open(path, 'rb') as f:
        raw = f.read()
    snap = json.loads(raw)
    if snap.get('environment') != 'mainnet' or snap.get('verified') is not True or snap.get('provenance') != 'direct_fetch':
        raise Fib02Error('fib02: exchange rules must be the verified direct-fetch mainnet snapshot')
    if not isinstance(snap.get('symbols'), dict) or not snap['symbols']:
        raise Fib02Error('fib02: exchange-rule snapshot holds no symbols')
    ident = {'path': os.path.relpath(os.path.abspath(path), ROOT).replace(os.sep, '/'),
             'sha256': hashlib.sha256(raw).hexdigest(), 'environment': snap['environment'],
             'fetched_at': snap.get('fetched_at'), 'raw_sha256': snap.get('raw_sha256'), 'schema': snap.get('schema')}
    return snap, ident


@contextlib.contextmanager
def scoped_equivalents(rules):
    """E1 + E2 for the duration of one runner call; restored whatever happens."""
    old_tfs, old_run = dict(B.TFS), BT.run
    seen = []

    def run_with_rules(*a, **k):
        if k.get('exchange_rules') is None:
            k['exchange_rules'] = rules
        tr, cv = old_run(*a, **k)
        seen.append(cv.attrs.get('feasibility'))
        return tr, cv

    B.TFS[TF] = TF_MS
    BT.run = run_with_rules
    try:
        yield seen
    finally:
        B.TFS.clear()
        B.TFS.update(old_tfs)
        BT.run = old_run


def _feas_summary(f) -> dict:
    if not f:
        return {}
    out = {k: v for k, v in f.items() if k != 'skips'}
    out['skips_listed'] = len(f.get('skips') or [])
    return json.loads(json.dumps(out, default=str))


def run(*, cache, regime_cache, window, out, side, tier=None, symbols=None, repo=B.VC.ROOT, plan=None,
        rules_path=None, **acct) -> dict:
    """One FIB-02 development cell. `symbols` is for synthetic tests only (the CLI exposes --tier only)."""
    if os.path.exists(out):
        raise Fib02Error(f'fib02: {out} exists; a packet is never overwritten')
    if B.ST.STRATEGIES[KEY].get('tf') != TF or not B.ST.STRATEGIES[KEY].get('research_only'):
        raise Fib02Error('fib02: fib_pullback_02 must be the research-only 1h key')
    snap, ident = load_rules(rules_path or os.path.join(ROOT, RULES))
    with scoped_equivalents(snap) as seen:
        B.run(cache=cache, window=window, out=out, key=KEY, side=side, mgmt_mode='defaults', custom=None, tier=tier,
              symbols=symbols, regime_cache=regime_cache, repo=repo, plan=plan, **acct)
    if len(seen) != 1:
        raise Fib02Error(f'fib02: expected one backtest.run call, saw {len(seen)}')
    with open(out, 'rb') as f:
        pkt = json.loads(f.read())
    pkt.pop('packet_sha256', None)
    pkt['costs']['exchange_rules'] = (f'mainnet snapshot {ident["path"]} (fetched {ident["fetched_at"]}): step / minQty / '
                                      'minNotional; entries below the floor skipped, never rounded up (backtest BT02)')
    prereg = os.path.join(ROOT, PREREG)
    pkt['fib02'] = {
        'format': FORMAT, 'prereg': PREREG,
        'prereg_sha256_lf': hashlib.sha256(open(prereg, 'rb').read().replace(b'\r\n', b'\n')).hexdigest()
        if os.path.exists(prereg) else None,
        'wrapper_sha256': _sha_file(os.path.abspath(__file__)),
        'equivalents': {'E1_timeframe': f'{TF} added to the frozen runner TFS for this call',
                        'E2_exchange_floors': 'mainnet snapshot passed to backtest.run for this call'},
        'exchange_rules': ident, 'feasibility': _feas_summary(seen[0]),
    }
    pkt['packet_sha256'] = hashlib.sha256(B._canon(pkt)).hexdigest()
    tmp = out + '.tmp'
    with open(tmp, 'wb') as f:
        f.write(json.dumps(pkt, sort_keys=True, indent=1, allow_nan=False).encode())
    os.replace(tmp, out)
    return pkt


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split('\n')[0])
    ap.add_argument('--cache', required=True, help='1h research cache dir (<SYMBOL>_1h.csv)')
    ap.add_argument('--regime-cache', required=True, help='4h cache dir (BTCUSDT_4h.csv, BTC regime split)')
    ap.add_argument('--window', required=True, help='START/END, UTC, 4h grid, end-exclusive (no default)')
    ap.add_argument('--tier', required=True, choices=('CORE-10', 'CORE-20'))
    ap.add_argument('--side', required=True, choices=tuple(B.SIDES))
    ap.add_argument('--out', required=True)
    a = ap.parse_args(argv)
    try:
        pkt = run(cache=a.cache, regime_cache=a.regime_cache, window=a.window, out=a.out, side=a.side, tier=a.tier)
    except (B.RunnerError, Fib02Error) as e:
        print(f'REFUSED: {e}', file=sys.stderr)
        return 2
    agg = pkt['stats']['aggregate']
    print(f"{KEY} {a.side} {a.tier}: {pkt['status']} {agg['trades']} trades, R sum {agg['r_sum']}, PF {agg['pf']}, "
          f"packet {pkt['packet_sha256'][:12]} -> {a.out}")
    return 0


if __name__ == '__main__':
    sys.exit(main())
