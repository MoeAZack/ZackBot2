"""Bounded breadth research runner: any 4h strategies.STRATEGIES key, one side, one management variant - candidate evidence only.

Generalises tools/research/run_vwaprev_candidate.py (unchanged; its guards and helpers are imported, not copied):
  - the same frozen development-window guard (pit_run.rex_window + sealed cutoff 2026-09-28T00:00:00Z + warm-up guard),
    the same frozen static universe tiers (CORE-10 / CORE-20), the same clean-tree check before any cache read, the same
    app cache format (<cache>/<SYMBOL>_4h.csv) with SHA-256 digests, the same slice-before-indicators + bounded assert,
    the same app machinery (backtest.Book + backtest.run, app costs / account / sleeve defaults, legacy exchange rules).
  - --key: any strategies.STRATEGIES key whose timeframe is 4h (the only cached timeframe). Another tf is refused
    ('BLOCKED: no <tf> cache'), never resampled.
  - --side long|short: the sleeve 'sides' field (backtest.run zeroes the other side's entry signals). A side whose raw
    entry signal never fires in the window over the run symbols is recorded 'not emitted' (status in the packet).
  - --mgmt defaults (frozen strategy mgmt, empty override) | custom (explicit --tp1-r / --tp1-frac / --be-r / --max-bars
    overrides on top of the defaults; no named preset is invented here).
  - completeness: a symbol whose [warm-up start, END) slice has any missing 4h candle (gap, late listing, early end) is
    INCOMPLETE: it is never filled, never enters the book (a gap would drop that bar for every symbol through the Book's
    timestamp intersection), is reported separately and is excluded from every aggregate. BTCUSDT incomplete refuses.
  - per trade (read-only instrumentation of backtest.close, see FeeProbe): gross price P&L after slippage, exit fees,
    entry fees, funding (residual), MFE (path best price, in stop-distance R), MAE (bar-extreme approximation over
    [entry bar, exit bar], in stop-distance R) and giveback (MFE R - realised R). The probe does not change any number:
    BT.FEE is replaced for the run by an object that multiplies exactly like 0.0005 and only records the close() products.
  - regime split: BTCUSDT close vs its 4h EMA200 (strategies.indicators e200) on the last CLOSED candle before the entry
    fill (bar i_in - 1), i.e. causal.
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
TF, TF_MS, TF_SEC, BTC = VC.TF, VC.TF_MS, VC.TF_SEC, VC.BTC
SEALED_CUTOFF, WARMUP_BARS, APP_DEFAULTS = VC.SEALED_CUTOFF, VC.WARMUP_BARS, VC.APP_DEFAULTS
SIDES = {'long': 1, 'short': -1}
# custom mgmt fields and their allowed ranges (app.MG_NUM, the app's own validation for slot management)
CUSTOM = {'tp1_r': (0.1, 50.0, float), 'tp1_frac': (0.05, 1.0, float), 'be_r': (0.1, 50.0, float),
          'max_bars': (1, 5000, int)}
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
    if tf != TF:
        raise RunnerError(f'BLOCKED: no {tf} cache ({key} runs on {tf}; only {TF} candles are cached)')
    return ST.STRATEGIES[key]


def build_mgmt(mode, custom=None) -> dict:
    """defaults -> {} (frozen strategy mgmt); custom -> explicit overrides only, range-checked like app.MG_NUM."""
    custom = {k: v for k, v in (custom or {}).items() if v is not None}
    if mode == 'defaults':
        if custom:
            raise RunnerError('breadth candidate: --mgmt defaults takes no overrides')
        return {}
    if mode != 'custom':
        raise RunnerError('breadth candidate: --mgmt must be defaults or custom')
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


def mgmt_label(mode, mg) -> str:
    if mode == 'defaults':
        return 'defaults'
    return 'custom-' + '-'.join(f'{k}{mg[k]:g}' for k in sorted(mg))


# ------------------------------------------------------------------ completeness
def completeness(d: pd.DataFrame, warm_lo: int, hi: int) -> dict:
    """Missing 4h candles in [warm_lo, hi) (open times). Never filled."""
    exp = (hi - warm_lo) // TF_MS
    t = np.unique(_ms(d.t)) if len(d) else np.array([], dtype=np.int64)
    grid = np.arange(warm_lo, hi, TF_MS, dtype=np.int64)
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


def simulate(raw, key, side, symbols, mgmt, lo, hi, *, start, max_lev, daily_halt, risk, max_pos):
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
                        fund_per_bar=BT.FUND_PER_BAR * TF_SEC / 14400, exchange_rules=None)
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
def enrich(tr: pd.DataFrame, pos: dict, book) -> pd.DataFrame:
    """Per trade: gross / fees / funding / MFE / MAE / giveback / BTC regime at entry (all causal or realised)."""
    if not len(tr):
        return tr.assign(gross=[], fees=[], funding=[], mfe_r=[], mae_r=[], giveback_r=[], regime=[])
    bt = book.d[BTC]
    btc_c, btc_e = bt.c.to_numpy(float), bt.e200.to_numpy(float)
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
        j = int(r.i_in) - 1
        reg = 'n/a' if j < 0 or not math.isfinite(btc_e[j]) else ('btc_above_ema200' if btc_c[j] > btc_e[j]
                                                                     else 'btc_below_ema200')
        rows.append(dict(gross=x['gross'], fees=fees, funding=funding, mfe_r=max(0.0, mfe), mae_r=mae,
                         giveback_r=max(0.0, mfe) - float(r.R), regime=reg))
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
        symbols=None, repo: str = VC.ROOT, plan=None, start=APP_DEFAULTS['start'], max_lev=APP_DEFAULTS['max_lev'],
        daily_halt=APP_DEFAULTS['daily_halt'], risk=APP_DEFAULTS['risk'], max_pos=APP_DEFAULTS['max_pos']) -> dict:
    strat = check_key(key)
    if side not in SIDES:
        raise RunnerError('breadth candidate: --side must be long or short')
    mg = build_mgmt(mgmt_mode, custom)
    try:
        lo, hi, warm_lo = VC.check_window(window, plan)
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
    book_syms = sorted(set(syms) | {BTC})
    raw, files, comp, nan_qv, incomplete = {}, {}, {}, {}, []
    for s in book_syms:
        df, rec = VC.read_cache(cache, s)
        files[s] = rec
        d = VC.slice_frame(df, warm_lo, hi)
        comp[s] = completeness(d, warm_lo, hi)
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
    run_syms = [s for s in syms if s in raw]
    tr, cv, book, pos = simulate(raw, key, side, run_syms, mg, lo, hi, start=start, max_lev=max_lev,
                                 daily_halt=daily_halt, risk=risk, max_pos=max_pos)
    n_sig = emitted(book, key, side, run_syms, lo, hi)
    if len(tr) and (tr.side != SIDES[side]).any():
        raise RunnerError('breadth candidate: a trade on the filtered-out side; aborted')
    tr = enrich(tr, pos, book)
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
        'window': {'start': _utc(lo), 'end': _utc(hi), 'end_exclusive': True, 'bars': int((hi - lo) // TF_MS),
                   'guard': 'pit_run.rex_window', 'sealed_cutoff': SEALED_CUTOFF},
        'warmup': {'bars': WARMUP_BARS, 'start': _utc(warm_lo), 'book_bars_before_start': int((bt < lo).sum())},
        'universe': {'digest': static['digest'], 'id': static.get('universe_id'), 'tier': tier, 'requested': syms,
                     'run': run_syms, 'book': sorted(raw), 'incomplete': incomplete},
        'cache': {'format': 'app._get_candles CSV <SYMBOL>_4h.csv: t(ms),o,h,l,c,v,qv', 'files': files,
                  'completeness': comp},
        'nan_qv': nan_qv,
        'strategy': {'key': key, 'name': strat['name'], 'tf': strat.get('tf', '4h'), 'registry_sides': strat['sides'],
                     'params': dict(strat.get('params') or {}), 'mgmt_mode': mgmt_mode, 'mgmt_override': mg,
                     'mgmt': ST.merge_mgmt(key, mg), 'reentry_lock': bool(strat.get('reentry_lock'))},
        'side': side,
        'sleeve': sleeve(key, side, run_syms, mg, risk, max_pos),
        'account': {'start': float(start), 'max_lev': float(max_lev), 'daily_halt': float(daily_halt)},
        'costs': {'fee_taker': FEE_VALUE, 'slip': BT.SLIP, 'fund_per_bar': BT.FUND_PER_BAR * TF_SEC / 14400,
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
    ap.add_argument('--mgmt', default='defaults', choices=('defaults', 'custom'))
    for k in CUSTOM:
        ap.add_argument('--' + k.replace('_', '-'), type=CUSTOM[k][2], default=None)
    ap.add_argument('--out', required=True)
    for k, v in APP_DEFAULTS.items():
        ap.add_argument('--' + k.replace('_', '-'), type=type(v), default=v)
    a = ap.parse_args(argv)
    try:
        pkt = run(cache=a.cache, window=a.window, out=a.out, key=a.key, side=a.side, mgmt_mode=a.mgmt,
                  custom={k: getattr(a, k) for k in CUSTOM}, tier=a.tier,
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
