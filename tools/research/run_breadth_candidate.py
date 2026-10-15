"""Bounded breadth research runner: any 4h strategies.STRATEGIES key, one side, one management variant - candidate evidence only.

Generalises tools/research/run_vwaprev_candidate.py (unchanged; its guards and helpers are imported, not copied):
  - the same frozen development-window guard (pit_run.rex_window + sealed cutoff 2026-09-28T00:00:00Z + warm-up guard),
    the same frozen static universe tiers (CORE-10 / CORE-20), the same clean-tree check before any cache read, the same
    app cache format (<cache>/<SYMBOL>_4h.csv) with SHA-256 digests, the same slice-before-indicators + bounded assert,
    the same app machinery (backtest.Book + backtest.run, app costs / account / sleeve defaults, legacy exchange rules).
  - --key: any strategies.STRATEGIES key on a cached timeframe (4h: the app cache; 15m: a research cache collected by
    tools/research/collect_klines_research.py, passed as --cache, with --regime-cache = the 4h cache). Any other tf is
    refused ('BLOCKED: no <tf> cache'), never resampled. --cache-extra: a second (research) cache dir read only for a
    symbol whose file the primary --cache lacks; every file records which dir it came from and its sha256. The window guard is always the 4h one (grid, dev window, the
    220 x 4h warm-up); a 15m run's own 220-bar warm-up lies inside that validated span.
  - --side long|short: the sleeve 'sides' field (backtest.run zeroes the other side's entry signals). A side whose raw
    entry signal never fires in the window over the run symbols is recorded 'not emitted' (status in the packet).
  - --mgmt defaults (frozen strategy mgmt, empty override) | custom (explicit --tp1-r / --tp1-frac / --be-r / --max-bars
    overrides on top of the defaults) | profit_capture (the exact Codex overrides, PROFIT_CAPTURE, six keys only).
    The packet's mgmt_check proves every default field the override does not name (stop_atr, tp_r, untouched max_bars)
    is carried unchanged into the merged sleeve mgmt.
  - completeness: a symbol whose [warm-up start, END) slice has any missing 4h candle (gap, late listing, early end) is
    INCOMPLETE: it is never filled, never enters the book (a gap would drop that bar for every symbol through the Book's
    timestamp intersection), is reported separately and is excluded from every aggregate. BTCUSDT incomplete refuses.
  - per trade (read-only instrumentation of backtest.close, see FeeProbe): gross price P&L after slippage, exit fees,
    entry fees, funding (residual), MFE (path best price, in stop-distance R), MAE (bar-extreme approximation over
    [entry bar, exit bar], in stop-distance R) and giveback (MFE R - realised R). The probe does not change any number:
    BT.FEE is replaced for the run by an object that multiplies exactly like 0.0005 and only records the close() products.
  - regime split: BTCUSDT close vs its 4h EMA200 (strategies.ema, 200) on the last 4h candle CLOSED at or before the
    entry fill, i.e. causal (for a 4h run: bar i_in - 1, identical to the book's own e200).
  - packet JSON per (key, side, mgmt): commit, window, symbols, cache digests, runner sha256, params, costs, stats, every
    trade; non_promotable true; never overwritten.

Usage (PAPER research only, no network, no app settings or credentials):

  py -3.14 tools/research/run_breadth_candidate.py --cache <DATA>/candles --window 2024-10-14T00:00:00Z/2026-09-28T00:00:00Z \
     --tier CORE-10 --key exp_range --side long --mgmt defaults --out <packet.json>
"""
import argparse
import hashlib
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
import run_vwaprev_candidate as VC                                                          # noqa: E402

FORMAT = 'zb-breadth-candidate/1'
EVIDENCE_CLASS = 'candidate-runtime'
TF, TF_MS, TF_SEC, BTC = VC.TF, VC.TF_MS, VC.TF_SEC, VC.BTC        # the 4h reference timeframe (window grid, regime)
TFS = {'4h': 4 * 3_600_000, '15m': 15 * 60_000}                    # timeframes with a candle cache (one dir per tf)
SEALED_CUTOFF, WARMUP_BARS, APP_DEFAULTS = VC.SEALED_CUTOFF, VC.WARMUP_BARS, VC.APP_DEFAULTS
SIDES = {'long': 1, 'short': -1}
# custom mgmt fields and their allowed ranges (app.MG_NUM, the app's own validation for slot management)
CUSTOM = {'tp1_r': (0.1, 50.0, float), 'tp1_frac': (0.05, 1.0, float), 'be_r': (0.1, 50.0, float),
          'max_bars': (1, 5000, int)}
# The PAPER profit-capture candidate, exact sleeve overrides from Codex (ZackBot2 #13, comment 6198970276), merged onto
# each frozen default (stop_atr / tp_r / untouched max_bars stay). Only these six keys; every other key is defaults-only.
PROFIT_CAPTURE_SOURCE = 'https://github.com/MoeAZack/ZackBot2/issues/13#issuecomment-6198970276'
PROFIT_CAPTURE = {
    'exp_trend': dict(be_r=1.0, tp1_r=1.5, tp1_frac=0.33),
    'exp_range': dict(be_r=0.5, tp1_r=0.5, tp1_frac=0.5, max_bars=18),
    'exp_breakout': dict(be_r=1.0, tp1_r=1.25, tp1_frac=0.5),
    'exp_scalp': dict(be_r=0.75, tp1_r=0.75, tp1_frac=0.5),
    'exp_structure': dict(be_r=0.75, tp1_r=1.0, tp1_frac=0.5),
    'exp_vwaprev': dict(be_r=0.5, tp1_r=0.5, tp1_frac=0.5, max_bars=18),
}
FEE_VALUE = float(BT.FEE)


class RunnerError(RuntimeError):
    pass


_num, _utc, _ms, _canon = VC._num, VC._utc, VC._ms, VC._canon


def _sha(path) -> str:
    with open(path, 'rb') as f:
        return hashlib.sha256(f.read()).hexdigest()


def runner_digest() -> dict:
    return {'run_breadth_candidate.py': _sha(os.path.abspath(__file__)),
            'run_vwaprev_candidate.py': _sha(os.path.abspath(VC.__file__))}


# ------------------------------------------------------------------ arguments
def check_key(key) -> dict:
    if key not in ST.STRATEGIES:
        raise RunnerError(f'breadth candidate: unknown strategy key {key!r}')
    tf = ST.STRATEGIES[key].get('tf', '4h')
    if tf not in TFS:
        raise RunnerError(f'BLOCKED: no {tf} cache ({key} runs on {tf}; cached timeframes: {sorted(TFS)})')
    return ST.STRATEGIES[key]


def build_mgmt(mode, custom=None, key=None) -> dict:
    """defaults -> {} (frozen strategy mgmt); custom -> explicit overrides only, range-checked like app.MG_NUM;
    profit_capture -> the exact Codex overrides for the six keys in PROFIT_CAPTURE (refused for any other key)."""
    custom = {k: v for k, v in (custom or {}).items() if v is not None}
    if mode == 'defaults':
        if custom:
            raise RunnerError('breadth candidate: --mgmt defaults takes no overrides')
        return {}
    if mode == 'profit_capture':
        if custom:
            raise RunnerError('breadth candidate: --mgmt profit_capture takes no overrides (exact Codex values)')
        if key not in PROFIT_CAPTURE:
            raise RunnerError(f'breadth candidate: no profit_capture config for {key} (defaults-only key)')
        mode, custom = 'custom', dict(PROFIT_CAPTURE[key])
    if mode != 'custom':
        raise RunnerError('breadth candidate: --mgmt must be defaults, profit_capture or custom')
    if not custom:
        raise RunnerError('breadth candidate: --mgmt custom needs at least one of --tp1-r/--tp1-frac/--be-r/--max-bars')
    out = {}
    for k, v in custom.items():
        if k not in CUSTOM:
            raise RunnerError(f'breadth candidate: unknown mgmt field {k}')
        lo, hi, ty = CUSTOM[k]
        v = ty(v)
        if not (lo <= v <= hi):
            raise RunnerError(f'breadth candidate: {k}={v} outside [{lo}, {hi}]')
        out[k] = v
    if ('tp1_r' in out) != ('tp1_frac' in out):
        raise RunnerError('breadth candidate: a partial needs both --tp1-r and --tp1-frac')
    return out


def mgmt_check(key, mg) -> dict:
    """Every default mgmt field the override does not name is carried unchanged into the merged sleeve mgmt."""
    base, merged = ST.merge_mgmt(key, {}), ST.merge_mgmt(key, mg)
    kept = {k: base[k] for k in base if k not in mg}
    if any(merged.get(k) != v for k, v in kept.items()) or any(merged.get(k) != v for k, v in mg.items()):
        raise RunnerError('breadth candidate: merged mgmt lost a default or an override; aborted')
    return {'defaults': base, 'override': dict(mg), 'kept_from_defaults': kept, 'merged': merged, 'ok': True}


def read_cache(cache: str, sym: str, tf: str):
    """<cache>/<SYM>_<tf>.csv in the app._get_candles format. A missing file is reported (the symbol is INCOMPLETE),
    never substituted."""
    name = f'{sym}_{tf}.csv'
    path = os.path.join(cache, name)
    if not os.path.exists(path):
        return None, {'file': name, 'missing_file': True}
    if tf == TF:
        try:
            return VC.read_cache(cache, sym)
        except VC.RunnerError as e:
            raise RunnerError(str(e).replace('VWAPREV candidate', 'breadth candidate')) from None
    with open(path, 'rb') as f:
        raw = f.read()
    import io
    df = pd.read_csv(io.BytesIO(raw))
    if 'qv' not in df:
        df['qv'] = float('nan')
    miss = [c for c in VC.COLS if c not in df]
    if miss:
        raise RunnerError(f'breadth candidate: cache file {name} lacks columns {miss}')
    df = df[VC.COLS].astype(float)
    return df, {'file': name, 'sha256': hashlib.sha256(raw).hexdigest(), 'bytes': len(raw), 'rows': int(len(df))}


# ------------------------------------------------------------------ completeness
def completeness(d, warm_lo: int, hi: int, tf_ms: int = TF_MS) -> dict:
    """Missing candles in [warm_lo, hi) (open times) on the tf grid. Never filled."""
    exp = (hi - warm_lo) // tf_ms
    if d is None:
        return {'expected': int(exp), 'rows': 0, 'missing': int(exp), 'off_grid': 0, 'duplicates': 0,
                'missing_file': True, 'complete': False}
    t = np.unique(_ms(d.t)) if len(d) else np.array([], dtype=np.int64)
    grid = np.arange(warm_lo, hi, tf_ms, dtype=np.int64)
    miss = np.setdiff1d(grid, t)
    off = np.setdiff1d(t, grid)
    rec = {'expected': int(exp), 'rows': int(len(d)), 'missing': int(len(miss)), 'off_grid': int(len(off)),
           'duplicates': int(len(d) - len(t))}
    if len(miss):
        rec['first_missing'], rec['last_missing'] = _utc(miss.min()), _utc(miss.max())
    rec['complete'] = not (len(miss) or len(off) or rec['duplicates'])
    return rec


# ------------------------------------------------------------------ read-only instrumentation of backtest.close
class FeeProbe:
    """Stands in for backtest.FEE during one run. Multiplies exactly like the float FEE (same IEEE product), so every
    number the backtest computes is unchanged. numpy scalars defer to it (__array_ufunc__ = None), so x * FEE always
    reaches __rmul__. Only the products made inside backtest.run's nested close() are recorded (one per close call:
    the exit-fee term of the pnl line), with the position dict read from that frame."""
    __array_ufunc__ = None

    def __init__(self, value):
        self.value = float(value)
        self.pos = {}

    def __float__(self):
        return self.value

    def __rmul__(self, other):
        out = float(other) * self.value
        f = sys._getframe(1)
        if f.f_code.co_name == 'close' and f.f_code.co_filename == BT.__file__:
            L = f.f_locals
            p, q, px, s = L['p'], float(L['q']), float(L['px']), L['s']
            r = self.pos.setdefault((s, int(p['i'])), dict(gross=0.0, exit_fee=0.0, qty=0.0))
            r['gross'] += p['side'] * (px - p['avg']) * q
            r['exit_fee'] += q * px * self.value
            r['qty'] += q
            r.update(side=int(p['side']), e0=float(p['e0']), avg=float(p['avg']), R=float(p['R']),
                     best=float(p['best']), risk=float(p['risk']))
        return out

    __mul__ = __rmul__


def sleeve(key, side, symbols, mgmt, risk, max_pos) -> dict:
    return dict(key=key, share=1.0, risk=float(risk), max_pos=int(max_pos), id='S1', sides=side, mgmt=dict(mgmt),
                symbols=list(symbols), hours=None, vol_max_pct=None, kelly=None)


def simulate(raw, key, side, symbols, mgmt, lo, hi, *, start, max_lev, daily_halt, risk, max_pos, tf_sec=TF_SEC):
    book = BT.Book(raw)
    if len(book.t) and _ms(book.t).max() >= hi:
        raise RunnerError('breadth candidate: the book holds a candle at / after END; aborted')
    if book.gaps:
        raise RunnerError(f'breadth candidate: book gaps {book.gaps} after completeness screening; aborted')
    n_before = int((_ms(book.t) < lo).sum())
    cfg = [sleeve(key, side, [s for s in symbols if s in raw], mgmt, risk, max_pos)]
    probe = FeeProbe(FEE_VALUE)
    old = BT.FEE
    BT.FEE = probe
    try:
        tr, cv = BT.run(book, cfg, start=float(start), max_lev=float(max_lev), daily_halt=float(daily_halt),
                        warmup=n_before, t0=np.datetime64(lo, 'ms'), t1=np.datetime64(hi, 'ms'),
                        fund_per_bar=BT.FUND_PER_BAR * tf_sec / 14400, exchange_rules=None)
    finally:
        BT.FEE = old
    return tr, cv, book, probe.pos


def emitted(book, key, side, symbols, lo, hi) -> int:
    """Raw entry signals on this side inside [lo, hi) over the run symbols (strategy side mask off, like backtest.run)."""
    t = _ms(book.t)
    m = (t >= lo) & (t < hi)
    col = 'le' if side == 'long' else 'se'
    return int(sum(np.asarray(book.raw_sig(key, s)[col], bool)[m].sum() for s in symbols))


# ------------------------------------------------------------------ stats
def btc_regime_frame(btc4h: pd.DataFrame):
    """(4h open ms, close, EMA200) of the sliced 4h BTCUSDT frame - strategies.ema, the same e200 the 4h book computes."""
    return _ms(btc4h.t), btc4h.c.to_numpy(float), ST.ema(btc4h.c.astype(float), 200).to_numpy(float)


def regime_at(reg, t_fill_ms) -> str:
    """BTC close vs 4h EMA200 on the last 4h candle CLOSED at or before the entry fill (open + 4h <= fill time)."""
    t4, c4, e4 = reg
    j = int(np.searchsorted(t4, t_fill_ms - TF_MS, side='right')) - 1
    if j < 0 or not math.isfinite(e4[j]):
        return 'n/a'
    return 'btc_above_ema200' if c4[j] > e4[j] else 'btc_below_ema200'


def enrich(tr: pd.DataFrame, pos: dict, book, reg) -> pd.DataFrame:
    """Per trade: gross / fees / funding / MFE / MAE / giveback / BTC regime at entry (all causal or realised)."""
    if not len(tr):
        return tr.assign(gross=[], fees=[], funding=[], mfe_r=[], mae_r=[], giveback_r=[], regime=[])
    bt_ms = _ms(book.t)
    rows = []
    for r in tr.itertuples():
        x = pos[(r.sym, int(r.i_in))]
        entry_fee = x['avg'] * x['qty'] * FEE_VALUE          # every fill taker (market entries; legacy rules)
        fees = entry_fee + x['exit_fee']
        funding = x['gross'] - fees - float(r.pnl)           # residual: what the bar loop charged as funding
        a = book.arr[r.sym]
        seg = slice(int(r.i_in), int(r.i_out) + 1)
        sd, R = x['side'], x['R']
        if R > 0:
            mfe = sd * (x['best'] - x['e0']) / R
            worst = a['l'][seg].min() if sd == 1 else a['h'][seg].max()
            mae = max(0.0, sd * (x['e0'] - worst) / R)
        else:
            mfe = mae = float('nan')
        rows.append(dict(gross=x['gross'], fees=fees, funding=funding, mfe_r=max(0.0, mfe), mae_r=mae,
                         giveback_r=max(0.0, mfe) - float(r.R), regime=regime_at(reg, int(bt_ms[int(r.i_in)]))))
    return pd.concat([tr.reset_index(drop=True), pd.DataFrame(rows)], axis=1)


def stats(tr: pd.DataFrame) -> dict:
    out = VC.trade_stats(tr)
    if not len(tr):
        out.update(gross=0.0, fees=0.0, funding=0.0, fee_share=None, mfe_r_mean=None, mae_r_mean=None,
                   giveback_r_mean=None, max_dd_r=None)
        return out
    gross, fees = float(tr.gross.sum()), float(tr.fees.sum())
    cum = tr.sort_values(['i_out', 'i_in']).R.cumsum().to_numpy()
    dd_r = float((np.maximum.accumulate(np.r_[0.0, cum]) - np.r_[0.0, cum]).max())
    out.update(gross=_num(gross), fees=_num(fees), funding=_num(float(tr.funding.sum())),
               fee_share=_num(fees / gross) if gross > 0 else None,
               fee_share_note=None if gross > 0 else 'gross <= 0: fee share undefined',
               fees_over_abs_gross=_num(fees / float(tr.gross.abs().sum())) if float(tr.gross.abs().sum()) > 0 else None,
               mfe_r_mean=_num(tr.mfe_r.mean()), mae_r_mean=_num(tr.mae_r.mean()),
               giveback_r_mean=_num(tr.giveback_r.mean()), max_dd_r=_num(dd_r))
    return out


def curve_dd(cv) -> float | None:
    if cv is None or cv.empty:
        return None
    return _num(float((cv / cv.cummax() - 1).min()))


# ------------------------------------------------------------------ the run
def run(*, cache: str, window, out: str, key: str, side: str, mgmt_mode: str = 'defaults', custom=None, tier=None,
        symbols=None, regime_cache=None, cache_extra=None, repo: str = VC.ROOT, plan=None, start=APP_DEFAULTS['start'], max_lev=APP_DEFAULTS['max_lev'],
        daily_halt=APP_DEFAULTS['daily_halt'], risk=APP_DEFAULTS['risk'], max_pos=APP_DEFAULTS['max_pos']) -> dict:
    strat = check_key(key)
    if side not in SIDES:
        raise RunnerError('breadth candidate: --side must be long or short')
    mg = build_mgmt(mgmt_mode, custom, key)
    mcheck = mgmt_check(key, mg)
    tf = strat.get('tf', '4h')
    tf_ms = TFS[tf]
    if tf != TF and not regime_cache:
        raise RunnerError(f'breadth candidate: a {tf} run needs --regime-cache (the 4h cache for the BTC regime split)')
    regime_cache = regime_cache or cache
    try:
        lo, hi, warm4 = VC.check_window(window, plan)     # 4h grid + frozen dev window + 220 x 4h warm-up guard
        static = VC.frozen_universe()
        syms = VC.pick_symbols(static, tier, symbols)
    except VC.RunnerError as e:
        raise RunnerError(str(e).replace('VWAPREV candidate', 'breadth candidate')) from None
    if os.path.exists(out):
        raise RunnerError(f'breadth candidate: {out} exists; a packet is never overwritten')
    try:
        commit = VC.clean_commit(repo)                   # before a single cache byte is read
    except VC.RunnerError as e:
        raise RunnerError(str(e).replace('VWAPREV candidate', 'breadth candidate')) from None
    # the run tf's warm-up (220 bars of tf) lies inside the 4h warm-up span the guard already validated
    warm_lo = lo - WARMUP_BARS * tf_ms
    assert warm_lo >= warm4
    book_syms = sorted(set(syms) | {BTC})
    raw, files, comp, nan_qv, incomplete = {}, {}, {}, {}, []
    for s in book_syms:
        df, rec = read_cache(cache, s, tf)
        rec['dir'] = 'cache'
        if df is None and cache_extra:                   # a symbol the primary cache lacks: the research cache, if any
            df, rec = read_cache(cache_extra, s, tf)
            rec['dir'] = 'cache_extra'
        files[s] = rec
        d = VC.slice_frame(df, warm_lo, hi) if df is not None else None
        comp[s] = completeness(d, warm_lo, hi, tf_ms)
        if d is not None:
            t = _ms(d.t)
            q = ~np.isfinite(d.qv.to_numpy(float))
            nan_qv[s] = {'all': int(q.sum()), 'in_window': int(q[t >= lo].sum()), 'warmup': int(q[t < lo].sum())}
        if not comp[s]['complete']:
            incomplete.append(dict(symbol=s, status='INCOMPLETE', **comp[s]))
            continue
        raw[s] = d
    if BTC not in raw:
        raise RunnerError('breadth candidate: BTCUSDT is INCOMPLETE for the window (the book requires it); refused')
    VC.assert_bounded(raw, warm_lo, hi)
    b4, b4rec = read_cache(regime_cache, BTC, TF)
    if b4 is None:
        raise RunnerError('breadth candidate: no 4h BTCUSDT file for the regime split; refused')
    b4 = VC.slice_frame(b4, warm4, hi)
    if not completeness(b4, warm4, hi)['complete']:
        raise RunnerError('breadth candidate: the 4h BTCUSDT regime frame is INCOMPLETE; refused')
    VC.assert_bounded({BTC: b4}, warm4, hi)
    reg = btc_regime_frame(b4)
    run_syms = [s for s in syms if s in raw]
    tr, cv, book, pos = simulate(raw, key, side, run_syms, mg, lo, hi, start=start, max_lev=max_lev,
                                 daily_halt=daily_halt, risk=risk, max_pos=max_pos, tf_sec=tf_ms // 1000)
    n_sig = emitted(book, key, side, run_syms, lo, hi)
    if len(tr) and (tr.side != SIDES[side]).any():
        raise RunnerError('breadth candidate: a trade on the filtered-out side; aborted')
    tr = enrich(tr, pos, book, reg)
    bt = _ms(book.t)
    if len(tr):
        tr = tr.assign(t_in=[_utc(bt[i]) for i in tr.i_in], t_out=[_utc(bt[i]) for i in tr.i_out])
    per = {s: stats(tr[tr.sym == s] if len(tr) else tr) for s in run_syms}
    regimes = {g: stats(tr[tr.regime == g] if len(tr) else tr) for g in ('btc_above_ema200', 'btc_below_ema200')}
    agg = stats(tr)
    agg['max_dd_equity'] = curve_dd(cv)
    trades = [] if not len(tr) else [
        {'symbol': r.sym, 'side': int(r.side), 't_in': r.t_in, 't_out': r.t_out, 'pnl': _num(r.pnl), 'R': _num(r.R),
         'why': r.why, 'gross': _num(r.gross), 'fees': _num(r.fees), 'funding': _num(r.funding),
         'mfe_r': _num(r.mfe_r), 'mae_r': _num(r.mae_r), 'giveback_r': _num(r.giveback_r), 'regime': r.regime}
        for r in tr.itertuples()]
    status = 'ok' if n_sig else 'not emitted'
    pkt = {
        'format': FORMAT, 'evidence_class': EVIDENCE_CLASS, 'non_promotable': True,
        'purpose': 'bounded breadth candidate run (development window only; not evidence for promotion)',
        'status': status, 'raw_entry_signals': n_sig,
        'commit': commit, 'tree': 'clean', 'runner_sha256': runner_digest(),
        'window': {'start': _utc(lo), 'end': _utc(hi), 'end_exclusive': True, 'bars': int((hi - lo) // tf_ms), 'tf': tf,
                   'guard': 'pit_run.rex_window', 'sealed_cutoff': SEALED_CUTOFF},
        'warmup': {'bars': WARMUP_BARS, 'tf': tf, 'start': _utc(warm_lo), 'guard_4h_start': _utc(warm4), 'book_bars_before_start': int((bt < lo).sum())},
        'universe': {'digest': static['digest'], 'id': static.get('universe_id'), 'tier': tier, 'requested': syms,
                     'run': run_syms, 'book': sorted(raw), 'incomplete': incomplete},
        'cache': {'format': f'app._get_candles CSV <SYMBOL>_{tf}.csv: t(ms),o,h,l,c,v,qv', 'files': files,
                  'dirs': {'cache': 'primary (--cache)', 'cache_extra': 'research cache (--cache-extra), used only for '
                           'symbols the primary lacks' if cache_extra else None},
                  'completeness': comp, 'regime_btc_4h': b4rec},
        'nan_qv': nan_qv,
        'strategy': {'key': key, 'name': strat['name'], 'tf': strat.get('tf', '4h'), 'registry_sides': strat['sides'],
                     'params': dict(strat.get('params') or {}), 'mgmt_mode': mgmt_mode, 'mgmt_override': mg,
                     'mgmt_check': mcheck,
                     'mgmt_source': PROFIT_CAPTURE_SOURCE if mgmt_mode == 'profit_capture' else None,
                     'mgmt': ST.merge_mgmt(key, mg), 'reentry_lock': bool(strat.get('reentry_lock'))},
        'side': side,
        'sleeve': sleeve(key, side, run_syms, mg, risk, max_pos),
        'account': {'start': float(start), 'max_lev': float(max_lev), 'daily_halt': float(daily_halt)},
        'costs': {'fee_taker': FEE_VALUE, 'slip': BT.SLIP, 'fund_per_bar': BT.FUND_PER_BAR * (tf_ms // 1000) / 14400,
                  'entry_order': 'market (taker)',
                  'exchange_rules': 'legacy floor (BT02 off: no step / minQty); not execution-realistic'},
        'engine': {'backtest': BT.VERSION, 'machinery': 'backtest.Book + backtest.run (app.run_backtest_job)'},
        'definitions': {
            'gross': 'price P&L after slippage, before fees and funding', 'fee_share': 'fees / gross (None if gross <= 0)',
            'funding': 'gross - fees - net (the bar-loop funding charge)',
            'mfe_r': 'best price on the backtest path (p.best) vs entry, in stop-distance R',
            'mae_r': 'APPROXIMATE: worst bar extreme over [entry bar, exit bar] vs entry, in stop-distance R (exit bar may '
                     'include movement after the exit)',
            'giveback_r': 'mfe_r - realised R', 'max_dd_r': 'max drawdown of cumulative trade R in exit order',
            'max_dd_equity': 'max drawdown of the bar equity curve (fraction)',
            'regime': 'BTCUSDT close vs 4h EMA200 on the last closed candle before the entry fill'},
        'stats': {'per_symbol': per, 'aggregate': agg, 'regime': regimes, 'app': VC._app_stats(tr, cv, start),
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
    ap.add_argument('--cache', required=True)
    ap.add_argument('--window', required=True, help='START/END, UTC, 4h grid, end-exclusive (no default)')
    g = ap.add_mutually_exclusive_group(required=True)
    g.add_argument('--tier', choices=('CORE-10', 'CORE-20'))
    g.add_argument('--symbols')
    ap.add_argument('--key', required=True)
    ap.add_argument('--side', required=True, choices=tuple(SIDES))
    ap.add_argument('--mgmt', default='defaults', choices=('defaults', 'profit_capture', 'custom'))
    ap.add_argument('--cache-extra', default=None, help='a second cache dir read only for symbols --cache lacks')
    ap.add_argument('--regime-cache', default=None, help='the 4h cache dir (BTC regime split); required for non-4h keys')
    for k in CUSTOM:
        ap.add_argument('--' + k.replace('_', '-'), type=CUSTOM[k][2], default=None)
    ap.add_argument('--out', required=True)
    for k, v in APP_DEFAULTS.items():
        ap.add_argument('--' + k.replace('_', '-'), type=type(v), default=v)
    a = ap.parse_args(argv)
    try:
        pkt = run(cache=a.cache, window=a.window, out=a.out, key=a.key, side=a.side, mgmt_mode=a.mgmt,
                  custom={k: getattr(a, k) for k in CUSTOM}, tier=a.tier, regime_cache=a.regime_cache, cache_extra=a.cache_extra,
                  symbols=a.symbols.split(',') if a.symbols else None, **{k: getattr(a, k) for k in APP_DEFAULTS})
    except RunnerError as e:
        print(f'REFUSED: {e}', file=sys.stderr)
        return 2
    agg = pkt['stats']['aggregate']
    print(f"{a.key} {a.side} {a.mgmt}: {pkt['status']} {agg['trades']} trades, R sum {agg['r_sum']}, PF {agg['pf']}, "
          f"packet {pkt['packet_sha256'][:12]} -> {a.out}")
    return 0


if __name__ == '__main__':
    sys.exit(main())
