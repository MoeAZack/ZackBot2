"""Bounded research runner for the RUNTIME VWAPREV strategy (`exp_vwaprev`, strategies.py) - candidate evidence only.

Why: the generic app backtest (app.run_backtest_job) has no end-date control, so a VWAPREV candidate run from the UI read
candles after the sealed 2026-09-28 holdout boundary and was invalidated (Codex 6184268429). This runner drives the SAME
machinery the app backtest job uses (backtest.Book + backtest.run with the app's sleeve / account / cost defaults) on the
app's real-qv candle cache, but only inside an explicit, end-exclusive development window:

  1. --window START/END is REQUIRED (no default) and validated by the frozen REX guard pit_run.rex_window (explicit,
     4h grid, inside the frozen development window, no calibration overlap, nothing on / after the sealed cutoff), plus
     a hard END <= 2026-09-28T00:00:00Z check and the warm-up guard: [START - warm-up, START) must lie inside the
     development window too (warm-up = backtest.run's default `warmup` bars, the app's own warm-up).
  2. symbols: --tier CORE-10 / CORE-20 or --symbols, both from the frozen static universe the REX runner uses
     (run_r4.static_universe of the committed short gate, re-validated). BTCUSDT always joins the book (the app does).
  3. a CLEAN git tree (tracked changes AND untracked files refuse; pit_run.rex_clean_commit) - before any cache read.
  4. cache read: <cache>/<SYMBOL>_4h.csv as written by app._get_candles (columns t[ms],o,h,l,c,v,qv; a pre-qv file loads
     with qv NaN exactly like the app). Every file read is SHA-256 digested.
  5. each frame is sliced to [START - warm-up, END) BEFORE any indicator is computed, then asserted: a candle opening at
     or after END (or before the warm-up start) aborts the run and no packet is written.
  6. backtest.Book(sliced) + backtest.run(..., t0=START, t1=END) with legacy exchange rules (BT02 off).
  7. packet (JSON, never overwritten): commit, window, warm-up, symbols, cache digests, strategy params + mgmt, costs,
     NaN-qv counts, per-symbol and aggregate trade stats, every trade; non_promotable true, evidence_class
     'candidate-runtime'.

VWAPREV fails closed on NaN quote volume (strategies.vwaprev_eval); this runner only COUNTS such candles, it never fills
them. Usage (PAPER research only, no network, no app settings or credentials):

  py -3.14 tools/research/run_vwaprev_candidate.py --cache <DATA>/candles --window 2025-03-03T00:00:00Z/2025-09-01T00:00:00Z \
     --tier CORE-10 --out <packet.json>
"""
import argparse
import hashlib
import inspect
import io
import json
import math
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(os.path.dirname(HERE))
for _p in (ROOT, HERE):
    if _p not in sys.path:
        sys.path.insert(0, _p)

import numpy as np                                                                          # noqa: E402
import pandas as pd                                                                         # noqa: E402

import backtest as BT                                                                       # noqa: E402
import strategies as ST                                                                     # noqa: E402
import pit_run as PR                                                                        # noqa: E402
import run_r4 as G                                                                          # noqa: E402
import splits as SP                                                                         # noqa: E402

FORMAT = 'zb-vwaprev-candidate/1'
EVIDENCE_CLASS = 'candidate-runtime'
KEY = 'exp_vwaprev'
TF = '4h'
TF_MS = 4 * 3_600_000
TF_SEC = TF_MS // 1000
BTC = 'BTCUSDT'
COLS = ['t', 'o', 'h', 'l', 'c', 'v', 'qv']
SEALED_CUTOFF = '2026-09-28T00:00:00Z'                  # first sealed instant: END may equal it (end-exclusive), never pass it
WARMUP_BARS = int(inspect.signature(BT.run).parameters['warmup'].default)    # the app backtest's own positional warm-up
# The app defaults a UI backtest of one VWAPREV slot runs with: panel.html addSleeve (share / risk / max_pos / sides) and
# app.run_backtest_job (start / max_lev / daily_halt; fund_per_bar scaled to the timeframe).
APP_DEFAULTS = dict(start=500.0, max_lev=10.0, daily_halt=0.08, risk=0.01, max_pos=4)


class RunnerError(RuntimeError):
    pass


def _utc(ms) -> str:
    return SP.utc(int(ms))


def _canon(obj) -> bytes:
    return json.dumps(obj, sort_keys=True, separators=(',', ':'), allow_nan=False).encode()


# ------------------------------------------------------------------ guards
def check_window(spec, plan=None) -> tuple[int, int, int]:
    """(lo, hi, warm_lo) in ms for an explicit `START/END` (end-exclusive). The frozen REX guard decides the window;
    this adds the hard sealed-cutoff check and the warm-up guard (warm-up inside the development window)."""
    plan = plan or PR.rex_plan(ROOT)
    try:
        lo, hi = PR.rex_window(spec, plan)
        dlo, dhi = PR.rex_dev_window(plan)
        cal = PR.rex_calibration(plan)
    except PR.PitRunError as e:
        raise RunnerError(f'VWAPREV candidate: {e}') from None
    if hi > SP.parse_utc(SEALED_CUTOFF) or hi > dhi:
        raise RunnerError(f'VWAPREV candidate: END {_utc(hi)} is after the sealed cutoff {SEALED_CUTOFF}')
    warm_lo = lo - WARMUP_BARS * TF_MS
    if warm_lo < dlo:
        raise RunnerError(f'VWAPREV candidate: the {WARMUP_BARS}-bar warm-up starts {_utc(warm_lo)}, before the frozen '
                          f'development window ({_utc(dlo)}); move START to {_utc(dlo + WARMUP_BARS * TF_MS)} or later')
    if any(warm_lo < c_hi and c_lo < hi for c_lo, c_hi in cal):
        raise RunnerError('VWAPREV candidate: the warm-up overlaps the calibration split')
    return lo, hi, warm_lo


def frozen_universe() -> dict:
    """The owner-approved static universe of the committed short gate (the REX runner's tiers), re-validated."""
    g = json.loads(G._load(ROOT, G.SHORT_GATE))
    try:
        return G.static_universe(ROOT, g['universe']['path'], g['universe']['snapshot'])
    except G.GateError as e:
        raise RunnerError(f'VWAPREV candidate: frozen universe invalid: {e}') from None


def pick_symbols(static: dict, tier=None, symbols=None) -> list[str]:
    if (tier is None) == (not symbols):
        raise RunnerError('VWAPREV candidate: give exactly one of --tier or --symbols')
    if tier is not None:
        k = {'CORE-10': 'core_10', 'CORE-20': 'core_20'}.get(tier)
        if k is None:
            raise RunnerError('VWAPREV candidate: --tier must be CORE-10 or CORE-20')
        return list(static[k])
    out = []
    for s in symbols:
        s = s.strip().upper()
        if s and s not in out:
            out.append(s)
    known = {x['symbol'] for x in static['symbols']}
    bad = [s for s in out if s not in known]
    if bad or not out:
        raise RunnerError(f'VWAPREV candidate: symbols outside the frozen universe: {bad or "none given"}')
    return out


def clean_commit(repo: str) -> str:
    try:
        return PR.rex_clean_commit(repo)
    except PR.PitRunError as e:
        raise RunnerError(f'VWAPREV candidate: {e}') from None


# ------------------------------------------------------------------ cache
def read_cache(cache: str, sym: str) -> tuple[pd.DataFrame, dict]:
    """One app cache file <cache>/<SYM>_4h.csv (app._get_candles format) -> (frame with t in ms, file record)."""
    name = f'{sym}_{TF}.csv'
    path = os.path.join(cache, name)
    try:
        with open(path, 'rb') as f:
            raw = f.read()
    except OSError as e:
        raise RunnerError(f'VWAPREV candidate: cache file {name} unreadable ({type(e).__name__})') from None
    df = pd.read_csv(io.BytesIO(raw))
    if 'qv' not in df:
        df['qv'] = float('nan')                       # pre-qv cache, exactly as the app loads it (VWAPREV fails closed)
    miss = [c for c in COLS if c not in df]
    if miss:
        raise RunnerError(f'VWAPREV candidate: cache file {name} lacks columns {miss}')
    df = df[COLS].astype(float)
    return df, {'file': name, 'sha256': hashlib.sha256(raw).hexdigest(), 'bytes': len(raw), 'rows': int(len(df))}


def slice_frame(df: pd.DataFrame, warm_lo: int, hi: int) -> pd.DataFrame:
    """[warm_lo, hi) on the candle OPEN time, done on the raw rows before any indicator exists; t -> datetime as the app."""
    out = df[(df.t >= warm_lo) & (df.t < hi)].copy()
    out['t'] = pd.to_datetime(out.t, unit='ms')
    return out.reset_index(drop=True)


def _ms(t) -> np.ndarray:
    return pd.to_datetime(t).astype('datetime64[ns]').astype('int64').to_numpy() // 1_000_000


def assert_bounded(raw: dict, warm_lo: int, hi: int) -> None:
    """Abort (no packet) when any loaded candle opens at / after END or before the warm-up start."""
    for s, d in raw.items():
        t = _ms(d.t)
        if len(t) and (t.max() >= hi or t.min() < warm_lo):
            raise RunnerError(f'VWAPREV candidate: {s} holds a candle outside [{_utc(warm_lo)}, {_utc(hi)}) after '
                              f'loading (latest {_utc(t.max())}); aborted, nothing written')


# ------------------------------------------------------------------ simulation (the app backtest machinery)
def sleeve(symbols, risk, max_pos) -> dict:
    """The sleeve cfg app.run_backtest_job builds for one enabled UI VWAPREV slot."""
    return dict(key=KEY, share=1.0, risk=float(risk), max_pos=int(max_pos), id='S1', sides='both', mgmt={},
                symbols=list(symbols), hours=None, vol_max_pct=None, kelly=None)


def simulate(raw: dict, symbols, lo: int, hi: int, *, start, max_lev, daily_halt, risk, max_pos):
    """(trades, curve, book) from backtest.Book + backtest.run exactly as the app job calls them, bounded to [lo, hi)."""
    book = BT.Book(raw)
    if len(book.t) and _ms(book.t).max() >= hi:
        raise RunnerError('VWAPREV candidate: the book holds a candle at / after END; aborted')
    n_before = int((_ms(book.t) < lo).sum())
    cfg = [sleeve([s for s in symbols if s in raw], risk, max_pos)]
    tr, cv = BT.run(book, cfg, start=float(start), max_lev=float(max_lev), daily_halt=float(daily_halt),
                    warmup=n_before, t0=np.datetime64(lo, 'ms'), t1=np.datetime64(hi, 'ms'),
                    fund_per_bar=BT.FUND_PER_BAR * TF_SEC / 14400, exchange_rules=None)
    return tr, cv, book


def _num(x, nd=6):
    if x is None:
        return None
    x = float(x)
    return round(x, nd) if math.isfinite(x) else None


def trade_stats(tr: pd.DataFrame) -> dict:
    n = int(len(tr))
    if not n:
        return dict(trades=0, longs=0, shorts=0, wins=0, win_rate=None, pnl=0.0, gross_win=0.0, gross_loss=0.0, pf=None,
                    r_sum=0.0, r_mean=None, exits={})
    gw, gl = float(tr.pnl[tr.pnl > 0].sum()), float(-tr.pnl[tr.pnl < 0].sum())
    return dict(trades=n, longs=int((tr.side == 1).sum()), shorts=int((tr.side == -1).sum()),
                wins=int((tr.pnl > 0).sum()), win_rate=_num((tr.pnl > 0).mean()), pnl=_num(tr.pnl.sum()),
                gross_win=_num(gw), gross_loss=_num(gl), pf=_num(gw / gl) if gl > 0 else None,
                r_sum=_num(tr.R.sum()), r_mean=_num(tr.R.mean()),
                exits={str(k): int(v) for k, v in sorted(tr.why.value_counts().items())})


def _app_stats(tr, cv, start) -> dict:
    """backtest.stats with the app job's mid-period split (the numbers the app UI shows)."""
    if cv.empty:
        return {}
    mid = cv.index[0] + (cv.index[-1] - cv.index[0]) / 2
    st = BT.stats(tr, cv, start=float(start), split=str(mid))
    st['per_week'] = round(len(tr) / max(1, (cv.index[-1] - cv.index[0]).days) * 7, 1)
    return {k: (v.item() if hasattr(v, 'item') else v) for k, v in st.items()}


# ------------------------------------------------------------------ the run
def run(*, cache: str, window, out: str, tier=None, symbols=None, repo: str = ROOT, plan=None,
        start=APP_DEFAULTS['start'], max_lev=APP_DEFAULTS['max_lev'], daily_halt=APP_DEFAULTS['daily_halt'],
        risk=APP_DEFAULTS['risk'], max_pos=APP_DEFAULTS['max_pos']) -> dict:
    """window -> universe -> clean tree -> cache read + digest -> slice -> bounded assert -> Book/run -> packet."""
    lo, hi, warm_lo = check_window(window, plan)
    static = frozen_universe()
    syms = pick_symbols(static, tier, symbols)
    if os.path.exists(out):
        raise RunnerError(f'VWAPREV candidate: {out} exists; a packet is never overwritten')
    commit = clean_commit(repo)                          # before a single cache byte is read
    book_syms = sorted(set(syms) | {BTC})                 # the app always adds BTCUSDT (context / regime)
    raw, files, nan_qv, skipped, cover = {}, {}, {}, [], {}
    win_bars = (hi - lo) // TF_MS
    for s in book_syms:
        df, rec = read_cache(cache, s)
        files[s] = rec
        d = slice_frame(df, warm_lo, hi)
        t = _ms(d.t)
        q = ~np.isfinite(d.qv.to_numpy(float))
        nan_qv[s] = {'all': int(q.sum()), 'in_window': int(q[t >= lo].sum()), 'warmup': int(q[t < lo].sum())}
        cover[s] = {'rows': int(len(d)), 'first': _utc(t.min()) if len(t) else None,
                    'last': _utc(t.max()) if len(t) else None}
        if len(d) < 0.95 * win_bars + 200:              # the app job's coverage rule (days x 0.95 + 200 bars)
            skipped.append({'symbol': s, 'reason': f'insufficient coverage ({len(d)} rows in warm-up + window)'})
            continue
        raw[s] = d
    if BTC not in raw:
        raise RunnerError('VWAPREV candidate: no 4h BTCUSDT coverage for the window (the app book requires it)')
    assert_bounded(raw, warm_lo, hi)
    run_syms = [s for s in syms if s in raw]
    tr, cv, book = simulate(raw, run_syms, lo, hi, start=start, max_lev=max_lev, daily_halt=daily_halt, risk=risk,
                            max_pos=max_pos)
    bt = _ms(book.t)
    if len(tr):
        tr = tr.assign(t_in=[_utc(bt[i]) for i in tr.i_in], t_out=[_utc(bt[i]) for i in tr.i_out])
    per = {s: trade_stats(tr[tr.sym == s] if len(tr) else tr) for s in run_syms}
    trades = [] if not len(tr) else [
        {'symbol': r.sym, 'side': int(r.side), 't_in': r.t_in, 't_out': r.t_out, 'pnl': _num(r.pnl), 'R': _num(r.R),
         'why': r.why} for r in tr.itertuples()]
    strat = ST.STRATEGIES[KEY]
    pkt = {
        'format': FORMAT, 'evidence_class': EVIDENCE_CLASS, 'non_promotable': True,
        'purpose': 'bounded VWAPREV runtime candidate run (development window only; not evidence for promotion)',
        'commit': commit, 'tree': 'clean',
        'window': {'start': _utc(lo), 'end': _utc(hi), 'end_exclusive': True, 'bars': int(win_bars),
                   'guard': 'pit_run.rex_window', 'sealed_cutoff': SEALED_CUTOFF},
        'warmup': {'bars': WARMUP_BARS, 'start': _utc(warm_lo), 'book_bars_before_start': int((bt < lo).sum())},
        'universe': {'digest': static['digest'], 'id': static.get('universe_id'), 'tier': tier,
                     'requested': syms, 'run': run_syms, 'book': sorted(raw), 'skipped': skipped},
        'cache': {'format': 'app._get_candles CSV <SYMBOL>_4h.csv: t(ms),o,h,l,c,v,qv', 'files': files,
                  'coverage': cover},
        'nan_qv': nan_qv,
        'strategy': {'key': KEY, 'name': strat['name'], 'params': dict(strat.get('params') or {}),
                     'mgmt': ST.merge_mgmt(KEY, {}), 'reentry_lock': bool(strat.get('reentry_lock')),
                     'vwaprev': {k: v for k, v in ST.VWAPREV.items()}},
        'sleeve': sleeve(run_syms, risk, max_pos),
        'account': {'start': float(start), 'max_lev': float(max_lev), 'daily_halt': float(daily_halt)},
        'costs': {'fee': BT.FEE, 'slip': BT.SLIP, 'fund_per_bar': BT.FUND_PER_BAR * TF_SEC / 14400,
                  'exchange_rules': 'legacy floor (BT02 off: no step / minQty); not execution-realistic'},
        'engine': {'backtest': BT.VERSION, 'machinery': 'backtest.Book + backtest.run (app.run_backtest_job)'},
        'gaps': {k: int(v) for k, v in book.gaps.items()},
        'stats': {'per_symbol': per, 'aggregate': trade_stats(tr), 'app': _app_stats(tr, cv, start),
                  'curve_end': _num(cv.iloc[-1]) if len(cv) else None,
                  'liquidations': int(cv.attrs.get('liquidations', 0))},
        'trades': trades,
    }
    pkt['packet_sha256'] = hashlib.sha256(_canon(pkt)).hexdigest()
    os.makedirs(os.path.dirname(os.path.abspath(out)), exist_ok=True)
    tmp = out + '.tmp'
    with open(tmp, 'wb') as f:
        f.write(json.dumps(pkt, sort_keys=True, indent=1, allow_nan=False).encode())
    os.replace(tmp, out)
    return pkt


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split('\n')[0])
    ap.add_argument('--cache', required=True, help="the app's candle cache dir (<DATA>/candles, files <SYM>_4h.csv)")
    ap.add_argument('--window', required=True, help='START/END, UTC, 4h grid, end-exclusive (no default)')
    g = ap.add_mutually_exclusive_group(required=True)
    g.add_argument('--tier', choices=('CORE-10', 'CORE-20'))
    g.add_argument('--symbols', help='comma-separated, from the frozen static universe')
    ap.add_argument('--out', required=True, help='packet path (never overwritten)')
    for k, v in APP_DEFAULTS.items():
        ap.add_argument('--' + k.replace('_', '-'), type=type(v), default=v)
    a = ap.parse_args(argv)
    try:
        pkt = run(cache=a.cache, window=a.window, out=a.out, tier=a.tier,
                  symbols=a.symbols.split(',') if a.symbols else None,
                  **{k: getattr(a, k) for k in APP_DEFAULTS})
    except RunnerError as e:
        print(f'REFUSED: {e}', file=sys.stderr)
        return 2
    agg = pkt['stats']['aggregate']
    print(f"{pkt['window']['start']}..{pkt['window']['end']} {len(pkt['universe']['run'])} symbols: "
          f"{agg['trades']} trades, R sum {agg['r_sum']}, packet {pkt['packet_sha256'][:12]} -> {a.out}")
    return 0


if __name__ == '__main__':
    sys.exit(main())
