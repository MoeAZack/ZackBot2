"""PROT-CMP-01 (research only): compare profit-protection variants on the 4h cohort strategies, after costs.

Each strategy runs ALONE (share 1, risk 2%, max_pos 4, the cohort's sides and mgmt overrides) so a variant's effect is not
mixed with slot interactions. Every variant changes only the stop movers (be_r / trail_atr / runner / ttp / prot); entries,
targets, tp1 partials, time exits and sizing are untouched.

Costs: the backtest's own model, re-run at three all-in levels per executed side (FWD-01's 7 / 15 / 23 bp), by setting
backtest.FEE (slip kept at 2 bp) for the run; funding stays at backtest.FUND_PER_BAR. prot's breakeven BUFFER is pinned to
the live engine's (prot.PROT_FEE / PROT_SLIP, what a slot without 'buf' / 'cost' gets in engine.py) whatever the cost level
(--buffer live, default), so a recommended slot is tested exactly as it would run; --buffer cost lets it follow the level.
RUN_* variants are a different EXIT REGIME, not just a stop mover: with 'runner' backtest.run also drops tp_r / max_bars and
keeps winners in a healthy trend through signal exits. They are reported for reference and never recommended.
Trades still open at the period end are not in sumR: open_usd (curve end - start - closed
P&L) shows their unrealised P&L, and ret_pct includes it.

Periods (fixed in advance, no tuning on B or L):
  A = data/ (40 coins) first half after warm-up      -> pick
  B = data/ second half                               -> confirm
  L = data_long/4h (8 coins) before data/ starts      -> older, independent era (2022 bear included)

Per trade: MFE in R from the entry fill (fill_log ref) over candles i_in..i_out (high / low; slightly generous on the exit
candle, same for every variant). red winner = MFE >= 1R and net R <= 0 (a
fee-breakeven scratch counts); red loss = MFE >= 1R and net R < -0.25 (a real loss). giveback = MFE_R - R on trades with MFE >= 1R.

Usage: python tools/research/prot_compare.py OUT_DIR [--jobs N] [--pessimistic path|worst] [--only S,..] [--variants V,..]
Writes OUT_DIR/rows.csv (one row per strategy x variant x cost x period) and OUT_DIR/meta.json. No network, no account."""
import argparse, glob, hashlib, json, os, sys
from concurrent.futures import ProcessPoolExecutor

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, ROOT)
import numpy as np, pandas as pd  # noqa: E402

# the cohort's enabled 4h slots (settings.json SLEEVES, read 16 Nov 2026) - key, sides, mgmt overrides
COHORT = [
    ('donchian_ens', 'both', {}), ('exp_trend', 'both', {'be_r': 1.0, 'tp1_frac': 0.33, 'tp1_r': 1.5}),
    ('pullback_rsi', 'both', {}), ('bear_breakdown', 'short', {}), ('rotation', 'long', {}), ('ema_mom', 'both', {}),
    ('exp_mom_short', 'short', {}), ('exp_range', 'both', {'be_r': 0.5, 'max_bars': 18.0, 'tp1_frac': 0.5, 'tp1_r': 0.5}),
    ('breakout_pyramid', 'both', {}), ('squeeze_tp', 'both', {}), ('ema_st', 'both', {}), ('hot_coin', 'long', {}),
    ('exp_breakout', 'both', {'be_r': 1.0, 'tp1_frac': 0.5, 'tp1_r': 1.25}),
]
OFF = {'be_r': 0, 'trail_atr': 0, 'runner': None, 'ttp': None}
# PROT-CMP-01 recommendation = variant P1_own (fee-adjusted breakeven at +1R, then the strategy's OWN trail, if it has one)
# on these strategies only. Left unchanged (BASE): donchian_ens, ema_mom, rotation, squeeze_tp (worse on period A, chosen on
# A only) and breakout_pyramid (better on A/B, much worse on the 1h-path check: mixed, waits for PYR-01).
RECOMMENDED = ('bear_breakdown', 'ema_st', 'exp_breakout', 'exp_mom_short', 'exp_range', 'exp_trend', 'hot_coin', 'pullback_rsi')


def recommended_mgmt(key, base=None):
    """the slot mgmt dict PROT-CMP-01 recommends for `key` (cohort overrides in `base` kept), or None = leave as is"""
    if key not in RECOMMENDED: return None
    return clean_off(variants(key, dict(base or {}))['P1_own'])


def clean_off(m):
    """drop the None placeholders (runner / ttp) so the dict is what a slot would store; 0 keeps a default mover OFF"""
    return {k: v for k, v in m.items() if v is not None}


def variants(key, base):
    """name -> mgmt override (merged over the strategy defaults by backtest.run). Strategy's own trail is kept inside prot
    where a variant says 'own'."""
    import strategies as S
    eff = S.merge_mgmt(key, base)
    own_tr = eff.get('trail_atr') or None
    V = {'BASE': dict(base), 'NONE': {**base, **OFF}}
    for name, at, tr in (('P1_own', 1.0, 'own'), ('P1_T3', 1.0, 3.0), ('P1_T2', 1.0, 2.0), ('P075_T3', 0.75, 3.0),
                         ('P15_T3', 1.5, 3.0), ('P15_T4', 1.5, 4.0), ('P2_T3', 2.0, 3.0), ('P1_none', 1.0, None),
                         ('P15_own', 1.5, 'own'), ('P2_own', 2.0, 'own')):
        k = own_tr if tr == 'own' else tr
        p = {'at_r': at}
        if k: p['trail_atr'] = k
        V[name] = {**base, **OFF, 'prot': p}
    V['RUN_lock'] = {**base, 'be_r': 0, 'runner': {'be_r': 1.0, 'step_r': 1.0, 'gap_r': 1.0}}
    V['RUN_gb50'] = {**base, 'be_r': 0, 'runner': {'be_r': 1.0, 'step_r': 1.0, 'gap_r': 1.0, 'giveback': 0.5, 'gb_from': 2.0}}
    return V


def load(pattern, t1=None):
    raw = {}
    for f in sorted(glob.glob(pattern)):
        d = pd.read_csv(f, parse_dates=['t'])
        if t1 is not None: d = d[d.t < t1]
        raw[os.path.basename(f).split('_')[0]] = d
    return raw


def _hash(pattern):
    h = hashlib.sha256()
    for f in sorted(glob.glob(pattern)):
        h.update(os.path.basename(f).encode()); h.update(open(f, 'rb').read())
    return h.hexdigest()


_BOOKS = {}


def books():
    import backtest as BT
    if not _BOOKS:
        short = load(os.path.join(ROOT, 'data', '*_4h.csv'))
        s0 = min(d.t.min() for d in short.values())
        _BOOKS['S'] = BT.Book(short)
        _BOOKS['L'] = BT.Book(load(os.path.join(ROOT, 'data_long', '4h', '*_4h.csv'), t1=s0))
    return _BOOKS


def periods():
    b = books(); T = b['S'].t
    a0 = pd.Timestamp(T.iloc[220]); end = pd.Timestamp(T.iloc[-1]) + pd.Timedelta(hours=4)
    mid = a0 + (end - a0) / 2
    LT = b['L'].t
    return {'A': ('S', a0, mid), 'B': ('S', mid, end), 'L': ('L', pd.Timestamp(LT.iloc[220]), pd.Timestamp(LT.iloc[-1]) + pd.Timedelta(hours=4))}


def trade_rows(book, tr, fl):
    """per-trade MFE_R from the entry fill (first 'entry' fill of the position)"""
    if tr is None or tr.empty: return tr
    ent = {}
    for f in fl:
        if f['kind'] == 'entry':
            ent.setdefault((f['sleeve'], f['sym'], f['pos_i']), (f['ref'], f['stop_dist']))
    mfe = []
    for r in tr.itertuples():
        e = ent.get((r.sleeve, r.sym, r.i_in))
        if not e or not e[1]: mfe.append(np.nan); continue
        a = book.arr[r.sym]; lo, hi = int(r.i_in), int(r.i_out) + 1
        ext = a['h'][lo:hi].max() if r.side == 1 else a['l'][lo:hi].min()
        mfe.append(r.side * (ext - e[0]) / e[1])
    tr = tr.copy(); tr['mfe_R'] = mfe
    return tr


_PROT_LEVEL = {}


def _pin_buffer(mode):
    """'live': prot_level ignores the fee / slip backtest.run passes and uses prot's own defaults = the live engine's buffer.
    'cost': the original function (buffer follows backtest.FEE). Never wraps an earlier patch."""
    import prot as PROT
    orig = _PROT_LEVEL.setdefault('orig', PROT.prot_level)
    if mode == 'cost':
        PROT.prot_level = orig; return
    PROT.prot_level = lambda prot, side, e0, avg, R, best, atr, fee=None, slip=None: orig(prot, side, e0, avg, R, best, atr)


def one(job):
    key, sides, vname, mg, cost_bp, per = job[:6]
    pess = job[6] if len(job) > 6 else 'path'
    import backtest as BT
    b = books(); bk, t0, t1 = periods()[per]; book = b[bk]
    BT.FEE = cost_bp / 1e4 - BT.SLIP if cost_bp else BT.FEE
    _pin_buffer(job[7] if len(job) > 7 else 'live')
    fl = []
    try:
        tr, cv = BT.run(book, [dict(key=key, share=1.0, risk=0.02, max_pos=4, sides=sides, mgmt=mg)], start=4000.0,
                        t0=t0, t1=t1, fill_log=fl, pessimistic=pess)
    except Exception as ex:
        return dict(strategy=key, variant=vname, cost_bp=cost_bp, period=per, error=f'{type(ex).__name__}: {ex}')
    tr = trade_rows(book, tr, fl)
    n = 0 if tr is None else len(tr)
    out = dict(strategy=key, variant=vname, cost_bp=cost_bp, period=per, n=n, path=pess)
    if n:
        R = tr['R'].values; w = R[R > 0].sum(); l_ = -R[R < 0].sum()
        hit = tr[tr['mfe_R'] >= 1.0]
        out.update(sumR=float(R.sum()), avgR=float(R.mean()), pf=float(w / l_) if l_ > 0 else np.inf, win=float((R > 0).mean()),
                   n_mfe1=int(len(hit)), red_win=int((hit['R'] <= 0).sum()), red_loss=int((hit['R'] < -0.25).sum()),
                   gb_med=float((hit['mfe_R'] - hit['R']).median()) if len(hit) else np.nan,
                   ret_pct=float(cv.iloc[-1] / 4000.0 * 100 - 100) if cv is not None and len(cv) else np.nan,
                   open_usd=float(cv.iloc[-1] - 4000.0 - tr['pnl'].sum()) if cv is not None and len(cv) else np.nan)
        if cv is not None and len(cv):
            out['mdd_pct'] = float(((cv / cv.cummax()) - 1).min() * 100)
    return out


def main():
    ap = argparse.ArgumentParser(); ap.add_argument('out'); ap.add_argument('--jobs', type=int, default=os.cpu_count())
    ap.add_argument('--only', default=None, help='comma list of strategies (smoke)')
    ap.add_argument('--variants', default=None, help='comma list of variants (default all)')
    ap.add_argument('--pessimistic', default='path', choices=['path', 'worst'], help="intrabar order (backtest.run)")
    ap.add_argument('--buffer', default='live', choices=['live', 'cost'], help="prot breakeven buffer (see module doc)")
    a = ap.parse_args(); os.makedirs(a.out, exist_ok=True)
    jobs = []
    for key, sides, base in COHORT:
        if a.only and key not in a.only.split(','): continue
        for vn, mg in variants(key, base).items():
            if a.variants and vn not in a.variants.split(','): continue
            for c in (7, 15, 23):
                for per in ('A', 'B', 'L'):
                    jobs.append((key, sides, vn, mg, c, per, a.pessimistic, a.buffer))
    with ProcessPoolExecutor(a.jobs) as ex:
        rows = list(ex.map(one, jobs, chunksize=4))
    df = pd.DataFrame(rows); df.to_csv(os.path.join(a.out, 'rows.csv'), index=False, encoding='utf-8')
    P = periods()
    meta = dict(pessimistic=a.pessimistic, buffer=a.buffer, head=os.popen(f'git -C "{ROOT}" rev-parse HEAD').read().strip(), jobs=len(jobs),
                data_sha256=_hash(os.path.join(ROOT, 'data', '*_4h.csv')), data_long_sha256=_hash(os.path.join(ROOT, 'data_long', '4h', '*_4h.csv')),
                periods={k: [v[0], str(v[1]), str(v[2])] for k, v in P.items()})
    json.dump(meta, open(os.path.join(a.out, 'meta.json'), 'w', encoding='utf-8'), indent=1)
    print(json.dumps(meta, indent=1)); print(df.groupby(['variant', 'cost_bp', 'period'])['sumR'].sum(min_count=1).unstack().round(1))
    if 'error' in df and df['error'].notna().any(): print('ERRORS:', df[df['error'].notna()][['strategy', 'variant', 'error']])


if __name__ == '__main__':
    main()
