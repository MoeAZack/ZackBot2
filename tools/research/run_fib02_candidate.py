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
     E2 is a PROSPECTIVE / CURRENT-RULE feasibility check, not point-in-time historical exchange rules; it cannot by
     itself make a packet promotable (the packet stays non_promotable).

The runner writes a sibling staging file; the final --out path is published atomically only after the wrapped packet
is complete (see run()). The `fib02.breadth` block reports closed_trades, symbols_with_trades and breadth_ok
(>= 100 closed trades across >= 8 symbols with trades) and the preregistered development verdict.

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


BREADTH_MIN_TRADES, BREADTH_MIN_SYMBOLS = 100, 8          # Codex 6487245023: the next gate's breadth floor
E2_LABEL = ('PROSPECTIVE / CURRENT-RULE FEASIBILITY CHECK: the current mainnet snapshot applied to the whole window; '
            'not point-in-time historical exchange rules, and it cannot by itself make this packet promotable')


def staging_paths(out) -> tuple[str, str]:
    """(staging, staging tmp): the sibling the shared runner writes, and the runner's own .tmp of it."""
    st = out + '.staging'
    return st, st + '.tmp'


def breadth(pkt) -> dict:
    agg = pkt['stats']['aggregate']
    n = int(agg.get('trades') or 0)
    syms = sorted(s for s, x in (pkt['stats'].get('per_symbol') or {}).items() if int(x.get('trades') or 0) > 0)
    ok = n >= BREADTH_MIN_TRADES and len(syms) >= BREADTH_MIN_SYMBOLS
    r = agg.get('r_mean')
    verdict = ('REJECT (mean net R <= 0 or no trades)' if r is None or r <= 0 else
               'DEVELOPMENT SURVIVOR (breadth ok; next: independent review + anchored walk-forward ruling)' if ok else
               'INCONCLUSIVE / INSUFFICIENT BREADTH')
    return {'closed_trades': n, 'symbols_with_trades': len(syms), 'symbols': syms,
            'rule': f'>= {BREADTH_MIN_TRADES} closed trades across >= {BREADTH_MIN_SYMBOLS} symbols with trades',
            'breadth_ok': ok, 'mean_net_r': r, 'development_verdict': verdict}


def wrap(staged: dict, ident: dict, feas) -> dict:
    """The staged runner packet -> the FIB-02 packet (envelope added, packet_sha256 recomputed the runner's way)."""
    pkt = dict(staged)
    want = pkt.pop('packet_sha256', None)
    if want != hashlib.sha256(B._canon(pkt)).hexdigest():
        raise Fib02Error('fib02: staged runner packet fails its own packet_sha256; not published')
    if pkt.get('strategy', {}).get('key') != KEY or pkt.get('window', {}).get('tf') != TF:
        raise Fib02Error('fib02: staged packet is not a fib_pullback_02 1h packet; not published')
    pkt['costs'] = dict(pkt['costs'], exchange_rules=(
        f'{E2_LABEL}. Mainnet snapshot {ident["path"]} (fetched {ident["fetched_at"]}): step / minQty / minNotional; '
        'entries below the floor skipped, never rounded up (backtest BT02)'))
    prereg = os.path.join(ROOT, PREREG)
    pkt['fib02'] = {
        'format': FORMAT, 'prereg': PREREG,
        'prereg_sha256_lf': hashlib.sha256(open(prereg, 'rb').read().replace(b'\r\n', b'\n')).hexdigest()
        if os.path.exists(prereg) else None,
        'wrapper_sha256': _sha_file(os.path.abspath(__file__)),
        'equivalents': {'E1_timeframe': f'{TF} added to the frozen runner TFS for this call',
                        'E2_exchange_floors': f'{E2_LABEL} (mainnet snapshot passed to backtest.run for this call)'},
        'e2_label': 'prospective/current-rule feasibility, not point-in-time; not promotable by itself',
        'exchange_rules': ident, 'feasibility': _feas_summary(feas),
        'breadth': breadth(pkt), 'runner_packet_sha256': want,
    }
    pkt['packet_sha256'] = hashlib.sha256(B._canon(pkt)).hexdigest()
    return pkt


def publish(pkt: dict, out: str) -> None:
    """Durable atomic publish: temp file, flush + fsync, os.replace onto the final path."""
    tmp = out + '.tmp'
    with open(tmp, 'wb') as f:
        f.write(json.dumps(pkt, sort_keys=True, indent=1, allow_nan=False).encode())
        f.flush()
        os.fsync(f.fileno())
    os.replace(tmp, out)


def _rm(path) -> None:
    with contextlib.suppress(FileNotFoundError):
        os.remove(path)


def run(*, cache, regime_cache, window, out, side, tier=None, symbols=None, repo=B.VC.ROOT, plan=None,
        rules_path=None, **acct) -> dict:
    """One FIB-02 development cell. `symbols` is for synthetic tests only (the CLI exposes --tier only).

    Evidence atomicity (Codex 6487245023 F1): the shared runner writes a sibling STAGING file (<out>.staging); the
    wrapper validates, wraps and re-hashes it and only then publishes <out> atomically (temp + fsync + os.replace).
    The final path therefore holds either nothing or the complete wrapped packet. Any failure after the runner starts
    removes the staging files (never published, so not evidence). Stale staging policy: REFUSE - a staging / temp
    file left by a hard crash (power loss, kill) is never reused or silently deleted; the run refuses and names it,
    and the operator removes it before rerunning."""
    staging, staging_tmp = staging_paths(out)
    if os.path.exists(out):
        raise Fib02Error(f'fib02: {out} exists; a packet is never overwritten')
    stale = [p for p in (staging, staging_tmp, out + '.tmp') if os.path.exists(p)]
    if stale:
        raise Fib02Error(f'fib02: stale staging from an interrupted run: {stale}; remove it and rerun (never reused)')
    if B.ST.STRATEGIES[KEY].get('tf') != TF or not B.ST.STRATEGIES[KEY].get('research_only'):
        raise Fib02Error('fib02: fib_pullback_02 must be the research-only 1h key')
    snap, ident = load_rules(rules_path or os.path.join(ROOT, RULES))
    try:
        with scoped_equivalents(snap) as seen:
            B.run(cache=cache, window=window, out=staging, key=KEY, side=side, mgmt_mode='defaults', custom=None,
                  tier=tier, symbols=symbols, regime_cache=regime_cache, repo=repo, plan=plan, **acct)
        if len(seen) != 1:
            raise Fib02Error(f'fib02: expected one backtest.run call, saw {len(seen)}')
        with open(staging, 'rb') as f:
            pkt = wrap(json.loads(f.read()), ident, seen[0])
        publish(pkt, out)
    except BaseException:
        for p in (staging, staging_tmp, out + '.tmp'):
            _rm(p)
        raise
    _rm(staging)
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
    br = pkt['fib02']['breadth']
    print(f"{KEY} {a.side} {a.tier}: {pkt['status']} {agg['trades']} trades on {br['symbols_with_trades']} symbols, "
          f"R sum {agg['r_sum']}, mean R {agg['r_mean']}, PF {agg['pf']}, breadth_ok {br['breadth_ok']}: "
          f"{br['development_verdict']}; packet {pkt['packet_sha256'][:12]} -> {a.out}")
    return 0


if __name__ == '__main__':
    sys.exit(main())
