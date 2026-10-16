"""Bounded research kline collector: public Binance USD-M futures /fapi/v1/klines for a fixed development window only.

Why not tools/collect_market_data.py directly: that collector (market_data.Collector) back-fills open interest / ratios /
funding / MARK-price klines (1h, 4h) up to now. The strategies and the app backtest run on TRADE klines with quote volume
(app._get_candles: /fapi/v1/klines, columns t,o,h,l,c,v,qv), and this research cache must never hold a candle on or after
the sealed cutoff. So this tool reuses the collector's own request layer - market_data.RequestsTransport (public, never
sends an API key) and market_data.Collector.get (pacing, 429 / -1003 back-off honouring Retry-After, 418 = stop at once
and write ban.json) - and only adds the bounded kline walk:

  - --window START/END is required and validated by the same frozen guard as the breadth runner (4h grid, inside the
    frozen development window, END <= 2026-09-28T00:00:00Z). Requests run from START - warm-up bars x interval to END
    with endTime = END - 1 ms on EVERY request, so the exchange never returns a candle opening at / after END; rows at /
    after END are also dropped defensively and counted.
  - --out must be OUTSIDE the git repo (the cache is never committed). Files: <out>/<SYMBOL>_<interval>.csv in the
    app._get_candles format. An existing file is never overwritten (re-run = digest only).
  - the manifest (--manifest, committed) records the source, request bounds, row counts, coverage / missing candles on the
    interval grid, per-file sha256, request count and any error. A symbol that cannot be fully collected is INCOMPLETE;
    nothing is ever filled or substituted.

  py -3.14 tools/research/collect_klines_research.py --interval 15m --tier CORE-10 \
     --window 2024-10-14T00:00:00Z/2026-09-28T00:00:00Z --out "%LOCALAPPDATA%/ZackBot/candles_15m_research" \
     --manifest research_evidence/manifests/klines_15m_core10_research.json
"""
import argparse
import hashlib
import json
import os
import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(os.path.dirname(HERE))
for _p in (ROOT, HERE):
    if _p not in sys.path:
        sys.path.insert(0, _p)

import numpy as np                                                                          # noqa: E402
import pandas as pd                                                                         # noqa: E402

import market_data as MD                                                                    # noqa: E402
import run_vwaprev_candidate as VC                                                          # noqa: E402

FORMAT = 'zb-klines-research/1'
PATH = '/fapi/v1/klines'
INTERVALS = {'15m': 15 * 60_000, '1h': 3_600_000, '4h': 4 * 3_600_000}
LIMIT = 1500
COLS = ['t', 'o', 'h', 'l', 'c', 'v', 'qv']


class CollectError(RuntimeError):
    pass


def _utc(ms):
    return VC._utc(ms)


def bounds(window, interval, warmup_bars, plan=None):
    """(fetch_lo, lo, hi) in ms: the frozen guard decides the window; the fetch starts warmup_bars x interval earlier."""
    if interval not in INTERVALS:
        raise CollectError(f'interval must be one of {sorted(INTERVALS)}')
    try:
        lo, hi, warm4 = VC.check_window(window, plan)
    except VC.RunnerError as e:
        raise CollectError(str(e).replace('VWAPREV candidate', 'kline collector')) from None
    if hi > VC.SP.parse_utc(VC.SEALED_CUTOFF):
        raise CollectError('END after the sealed cutoff')
    flo = lo - int(warmup_bars) * INTERVALS[interval]
    if flo < warm4:
        raise CollectError('the warm-up reaches before the validated 4h warm-up span')
    return flo, lo, hi


def inside_repo(path, repo=ROOT) -> bool:
    a, r = os.path.normcase(os.path.abspath(path)), os.path.normcase(os.path.abspath(repo))
    return a == r or a.startswith(r + os.sep)


def fetch(col, symbol, interval, flo, hi):
    """Forward walk [flo, hi) with endTime = hi - 1 on every request. Returns (rows, requests, dropped_after_end)."""
    step = INTERVALS[interval]
    rows, n, dropped, start = [], 0, 0, flo
    while start < hi:
        params = dict(symbol=symbol, interval=interval, startTime=int(start), endTime=int(hi - 1), limit=LIMIT)
        assert params['endTime'] < hi
        k = col.get(PATH, params)
        n += 1
        if not k:
            break
        for r in k:
            t = int(r[0])
            if t >= hi or t < flo:
                dropped += 1
                continue
            rows.append([t, r[1], r[2], r[3], r[4], r[5], r[7] if len(r) > 7 else 'nan'])
        nxt = int(k[-1][0]) + step
        if nxt <= start:
            break
        start = nxt
        if len(k) < LIMIT:
            break
    return rows, n, dropped


def coverage(t: np.ndarray, flo, hi, step) -> dict:
    grid = np.arange(flo, hi, step, dtype=np.int64)
    u = np.unique(t)
    miss = np.setdiff1d(grid, u)
    off = np.setdiff1d(u, grid)
    out = {'expected': int(len(grid)), 'rows': int(len(t)), 'missing': int(len(miss)), 'off_grid': int(len(off)),
           'duplicates': int(len(t) - len(u)), 'first': _utc(u.min()) if len(u) else None,
           'last': _utc(u.max()) if len(u) else None}
    if len(miss):
        # contiguous missing runs (first 20), never filled
        br = np.flatnonzero(np.diff(miss) != step)
        starts, ends = np.r_[miss[0], miss[br + 1]], np.r_[miss[br], miss[-1]]
        out['missing_runs'] = [[_utc(a), _utc(b), int((b - a) // step + 1)] for a, b in zip(starts[:20], ends[:20])]
    out['status'] = 'complete' if not (len(miss) or len(off) or out['duplicates']) else 'INCOMPLETE'
    return out


def collect(*, symbols, interval, window, out, manifest, warmup_bars=220, transport=None, sleep=time.sleep,
            clock=time.time, min_gap=1.0, weight_per_min=600, plan=None) -> dict:
    flo, lo, hi = bounds(window, interval, warmup_bars, plan)
    if inside_repo(out):
        raise CollectError('--out is inside the git repo; the research cache must live outside it (never committed)')
    os.makedirs(out, exist_ok=True)
    step = INTERVALS[interval]
    col = MD.Collector(transport or MD.RequestsTransport(MD.MAINNET), out, source=MD.MAINNET, sleep=sleep, clock=clock,
                       weight_per_min=weight_per_min, min_gap=min_gap)
    files, stopped = {}, None
    for s in symbols:
        name = f'{s}_{interval}.csv'
        path = os.path.join(out, name)
        rec = {'file': name, 'request': {'path': PATH, 'interval': interval, 'startTime': _utc(flo),
                                         'endTime_exclusive': _utc(hi), 'limit': LIMIT}}
        if os.path.exists(path):
            rec['fetched'] = 'existing file (not re-fetched, never overwritten)'
        elif stopped:
            rec.update(status='INCOMPLETE', error=f'not collected: {stopped}')
            files[s] = rec
            continue
        else:
            try:
                rows, n, dropped = fetch(col, s, interval, flo, hi)
            except (MD.StopRun, MD.Transient, MD.Refused) as e:
                stopped = f'{type(e).__name__}: {str(e)[:160]}'
                rec.update(status='INCOMPLETE', error=stopped)
                files[s] = rec
                continue
            df = pd.DataFrame(rows, columns=COLS).astype(float).drop_duplicates('t').sort_values('t')
            tmp = path + '.tmp'
            df.to_csv(tmp, index=False)
            os.replace(tmp, path)
            rec.update(fetched='this run', requests=n, dropped_at_or_after_end=dropped)
        with open(path, 'rb') as f:
            raw = f.read()
        df = pd.read_csv(path)
        t = df.t.to_numpy(np.int64) if len(df) else np.array([], dtype=np.int64)
        if len(t) and (t.max() >= hi or t.min() < flo):
            raise CollectError(f'{name} holds a candle outside [{_utc(flo)}, {_utc(hi)}); refused')
        rec.update(sha256=hashlib.sha256(raw).hexdigest(), bytes=len(raw),
                   nan_qv=int((~np.isfinite(df.qv.to_numpy(float))).sum()) if len(df) else 0,
                   **coverage(t, flo, hi, step))
        files[s] = rec
    with open(os.path.abspath(__file__), 'rb') as f:
        tool_sha = hashlib.sha256(f.read()).hexdigest()
    man = {'format': FORMAT, 'source': f'{MD.MAINNET}{PATH} (public Binance USD-M futures trade klines, no API key)',
           'tool': {'collect_klines_research.py': tool_sha}, 'interval': interval,
           'window': {'start': _utc(lo), 'end': _utc(hi), 'end_exclusive': True, 'sealed_cutoff': VC.SEALED_CUTOFF},
           'fetch': {'start': _utc(flo), 'end_exclusive': _utc(hi), 'warmup_bars': int(warmup_bars),
                     'requests': int(col.requests), 'pacing': {'min_gap_s': min_gap, 'weight_per_min': weight_per_min}},
           'cache_dir': 'outside the repo (not committed); files named <SYMBOL>_<interval>.csv',
           'format_cols': 'app._get_candles CSV: t(ms),o,h,l,c,v,qv', 'stopped': stopped,
           'incomplete': sorted(s for s, r in files.items() if r.get('status') == 'INCOMPLETE'),
           'files': files}
    if manifest:
        if os.path.exists(manifest):
            raise CollectError(f'{manifest} exists; a manifest is never overwritten')
        os.makedirs(os.path.dirname(os.path.abspath(manifest)), exist_ok=True)
        with open(manifest, 'w', encoding='utf-8', newline='\n') as f:
            f.write(json.dumps(man, sort_keys=True, indent=1) + '\n')
    return man


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split('\n')[0])
    ap.add_argument('--interval', required=True, choices=sorted(INTERVALS))
    ap.add_argument('--window', required=True)
    g = ap.add_mutually_exclusive_group(required=True)
    g.add_argument('--tier', choices=('CORE-10', 'CORE-20'))
    g.add_argument('--symbols')
    ap.add_argument('--out', required=True)
    ap.add_argument('--manifest', required=True)
    ap.add_argument('--warmup-bars', type=int, default=220)
    a = ap.parse_args(argv)
    try:
        syms = VC.pick_symbols(VC.frozen_universe(), a.tier, a.symbols.split(',') if a.symbols else None)
        man = collect(symbols=sorted(set(syms) | {VC.BTC}), interval=a.interval, window=a.window, out=a.out,
                      manifest=a.manifest, warmup_bars=a.warmup_bars)
    except (CollectError, VC.RunnerError) as e:
        print(f'REFUSED: {e}', file=sys.stderr)
        return 2
    for s, r in man['files'].items():
        print(f"{s}: {r.get('status')} rows {r.get('rows')} missing {r.get('missing')} {r.get('error', '')}")
    return 1 if man['incomplete'] else 0


if __name__ == '__main__':
    sys.exit(main())
