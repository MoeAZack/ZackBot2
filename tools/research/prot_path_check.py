"""PROT-CMP-01 path check (research only): is a protection variant's gain real, or an artefact of the 4h intrabar path rule?

Breakeven / trail stops raised inside a 4h candle are sensitive to the order of the candle's high and low, which a 4h bar does
not record. On the 8 coins with 1h history (data_long/1h, from 2022-08-26) this replays each 4h candle along its four real 1h
sub-candles instead of the colour rule, by swapping backtest.path_points for a lookup (research process only; backtest.py is
not changed):
  path    = backtest's own rule (4h candle colour; doji = worst case)
  h1      = the 4 real 1h candles in order, each walked by the colour rule
  h1worst = the 4 real 1h candles in order, each walked worst-case for the position's side (a conservative bound)
A 4h candle whose 1h candles are missing or do not reproduce its o/h/l/c exactly keeps backtest's rule (counted in meta).

Usage: python tools/research/prot_path_check.py OUT_DIR [--variants BASE,P1_own,...] [--jobs N]"""
import argparse, glob, json, os, sys
from concurrent.futures import ProcessPoolExecutor

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import prot_compare as PC  # noqa: E402
import numpy as np, pandas as pd  # noqa: E402

ROOT = PC.ROOT
_STATE = {}


def _sub_paths():
    """{(o,h,l,c) of a 4h candle: [(o,h,l,c) of its 1h candles]} for the candles the 1h data reproduces exactly"""
    out, bad = {}, 0
    for f in sorted(glob.glob(os.path.join(ROOT, 'data_long', '1h', '*_1h.csv'))):
        sym = os.path.basename(f).split('_')[0]
        h1 = pd.read_csv(f, parse_dates=['t']).drop_duplicates('t').sort_values('t')
        h1['b'] = h1.t.dt.floor('4h')
        grp = {b: g for b, g in h1.groupby('b')}
        h4 = pd.read_csv(os.path.join(ROOT, 'data_long', '4h', f'{sym}_4h.csv'), parse_dates=['t']).drop_duplicates('t')
        h4 = h4[h4.t >= h1.t.iloc[0]]
        for r in h4.itertuples():
            sub = grp.get(r.t)
            if sub is None or len(sub) != 4 or sub.o.iloc[0] != r.o or sub.c.iloc[-1] != r.c or sub.h.max() != r.h or sub.l.min() != r.l:
                bad += 1; continue
            out[(r.o, r.h, r.l, r.c)] = list(zip(sub.o, sub.h, sub.l, sub.c))
    return out, bad


def _install(mode):
    import backtest as BT
    orig = _STATE.setdefault('orig', BT.path_points)     # the module's own rule, kept once per process
    if mode == 'path':
        BT.path_points = orig; return
    if 'sub' not in _STATE: _STATE['sub'] = _sub_paths()
    sub = _STATE['sub'][0]

    def pp(o, h, l, c, side, worst=False):
        bars = sub.get((o, h, l, c))
        if bars is None: return orig(o, h, l, c, side, worst)
        pts = []
        for (o1, h1, l1, c1) in bars:
            seg = orig(o1, h1, l1, c1, side, worst or mode == 'h1worst')
            pts.extend(seg if not pts else seg[1:] if seg[0] == pts[-1] else seg)
        return tuple(pts)
    BT.path_points = pp


def _books():
    import backtest as BT
    if 'book' not in _STATE:
        raw = PC.load(os.path.join(ROOT, 'data_long', '4h', '*_4h.csv'))
        t0 = min(pd.read_csv(f, parse_dates=['t']).t.min() for f in glob.glob(os.path.join(ROOT, 'data_long', '1h', '*_1h.csv')))
        raw = {s: d[d.t >= t0 - pd.Timedelta(days=40 * 4 / 24 * 6)] for s, d in raw.items()}   # ~240 bars of warm-up before 1h starts
        _STATE['book'] = BT.Book(raw); _STATE['t0'] = t0
    return _STATE['book'], _STATE['t0']


def one(job):
    key, sides, vname, mg, cost_bp, mode = job
    import backtest as BT
    _install(mode)
    book, t0 = _books()
    BT.FEE = cost_bp / 1e4 - BT.SLIP
    PC._pin_buffer('live')                                 # the live engine's breakeven buffer (see prot_compare)
    fl = []
    try:
        tr, cv = BT.run(book, [dict(key=key, share=1.0, risk=0.02, max_pos=4, sides=sides, mgmt=mg)], start=4000.0, t0=t0, fill_log=fl)
    except Exception as ex:
        return dict(strategy=key, variant=vname, cost_bp=cost_bp, mode=mode, error=f'{type(ex).__name__}: {ex}')
    tr = PC.trade_rows(book, tr, fl)
    out = dict(strategy=key, variant=vname, cost_bp=cost_bp, mode=mode, n=0 if tr is None else len(tr))
    if out['n']:
        R = tr['R'].values; hit = tr[tr['mfe_R'] >= 1.0]
        out.update(sumR=float(R.sum()), n_mfe1=int(len(hit)), red_loss=int((hit['R'] < -0.25).sum()),
                   mdd_pct=float(((cv / cv.cummax()) - 1).min() * 100), open_usd=float(cv.iloc[-1] - 4000.0 - tr['pnl'].sum()))
    return out


def main():
    ap = argparse.ArgumentParser(); ap.add_argument('out'); ap.add_argument('--jobs', type=int, default=os.cpu_count())
    ap.add_argument('--variants', default='BASE,P1_own,P1_T3,RUN_lock')
    a = ap.parse_args(); os.makedirs(a.out, exist_ok=True)
    want = a.variants.split(',')
    jobs = [(k, sd, vn, mg, c, mode) for k, sd, base in PC.COHORT for vn, mg in PC.variants(k, base).items() if vn in want
            for c in (7, 15, 23) for mode in ('path', 'h1', 'h1worst')]
    with ProcessPoolExecutor(a.jobs) as ex:
        rows = list(ex.map(one, jobs, chunksize=4))
    df = pd.DataFrame(rows); df.to_csv(os.path.join(a.out, 'rows.csv'), index=False, encoding='utf-8')
    sub, bad = _sub_paths()
    meta = dict(head=os.popen(f'git -C "{ROOT}" rev-parse HEAD').read().strip(), jobs=len(jobs), candles_resolved=len(sub),
                candles_unresolved=bad, start=str(_books()[1]),
                data_long_1h_sha256=PC._hash(os.path.join(ROOT, 'data_long', '1h', '*_1h.csv')),
                data_long_4h_sha256=PC._hash(os.path.join(ROOT, 'data_long', '4h', '*_4h.csv')))
    json.dump(meta, open(os.path.join(a.out, 'meta.json'), 'w', encoding='utf-8'), indent=1)
    print(json.dumps(meta)); print(df.groupby(['variant', 'cost_bp', 'mode'])['sumR'].sum(min_count=1).unstack().round(1))
    if 'error' in df and df['error'].notna().any(): print('ERRORS:', df[df['error'].notna()][['strategy', 'variant', 'error']])


if __name__ == '__main__':
    main()
