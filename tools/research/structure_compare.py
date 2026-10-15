"""REVIEW-ONLY: describe trades ALREADY TAKEN in frozen breadth packets by the structure state at entry.

Not a filter run, not a signal, no tuning: each packet trade (t_in = next-bar fill at the candle open) is joined to
structure_features.structure() of the last candle CLOSED at or before t_in (open time t_in - 4h), computed from that
symbol's cache file whose sha256 must equal the digest the packet recorded. Costs are the packet's (R is net).

  py -3.14 tools/research/structure_compare.py --packets DIR --cache DIR [--cache DIR ...] --out FILE.json
  py -3.14 tools/research/structure_compare.py --inputs <run dir>/inputs.json --universe CORE-10 --cache DIR [...] --out F
    (--inputs: fetches each packet's review branch, reads it at the exact recorded commit, checks its sha256, then runs)
"""
import argparse, glob, hashlib, json, math, os, subprocess, sys, tempfile

import numpy as np
import pandas as pd

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import structure_features as SF                         # noqa: E402

TF_MS = 4 * 3_600_000


def sha(p):
    return hashlib.sha256(open(p, 'rb').read()).hexdigest()


def find_file(dirs, name, digest):
    for d in dirs:
        p = os.path.join(d, name)
        if os.path.exists(p) and sha(p) == digest: return p
    return None


def stats(r):
    r = np.asarray(r, float)
    if not len(r): return dict(n=0, net_r=0.0, mean_r=None, pf=None, win=None)
    gw, gl = r[r > 0].sum(), -r[r < 0].sum()
    return dict(n=int(len(r)), net_r=round(float(r.sum()), 2), mean_r=round(float(r.mean()), 3),
                pf=round(float(gw / gl), 2) if gl > 0 else None, win=round(float((r > 0).mean()), 3))


def welch_t(a, b):
    a, b = np.asarray(a, float), np.asarray(b, float)
    if len(a) < 2 or len(b) < 2: return None
    se = math.sqrt(a.var(ddof=1) / len(a) + b.var(ddof=1) / len(b))
    return round(float((a.mean() - b.mean()) / se), 2) if se > 0 else None


def label(row, side):
    st = int(row['state'])
    align = 'none' if st == 0 else ('with' if st == side else 'counter')
    ev = row['event'] if align == 'with' else ''
    zp = row['zone_pos']
    zone = 'n/a' if np.isnan(zp) else ('inside' if zp == 0 else ('beyond_with' if zp == side else 'beyond_against'))
    fresh = 'n/a' if np.isnan(row['bars_event']) else ('fresh<=6' if row['bars_event'] <= 6 else 'old>6')
    dist = 'n/a' if zone != 'beyond_with' else ('<=1ATR' if row['dist_atr'] <= 1 else '>1ATR')
    return dict(align=align, aligned_event=ev or '-', zone=zone, event_age=fresh, ext_dist=dist)


def run(packets, caches):
    feats, rows, files = {}, [], {}
    for p in sorted(glob.glob(os.path.join(packets, '*.json'))):
        d = json.load(open(p))
        hyp = f"{d['strategy']['key']}.{d['side']}"
        for t in d['trades']:
            sym = t['symbol']; f = d['cache']['files'][sym]
            if sym not in feats:
                path = find_file(caches, f['file'], f['sha256'])
                if path is None: raise SystemExit(f'BLOCKED: no cache file for {sym} with the packet digest {f["sha256"][:12]}')
                df = pd.read_csv(path)
                df = df.rename(columns={df.columns[0]: 't'}) if 't' not in df else df
                feats[sym] = SF.structure(df).set_index(df['t'].astype('int64'))
                files[sym] = f['sha256']
            elif files[sym] != f['sha256']: raise SystemExit(f'digest mismatch across packets for {sym}')
            t_in = int(pd.Timestamp(t['t_in']).value // 1_000_000)
            assert t_in % TF_MS == 0, t['t_in']
            row = feats[sym].loc[t_in - TF_MS]                # the last candle closed at the fill time
            rows.append(dict(hyp=hyp, symbol=sym, t_in=t['t_in'], R=t['R'], **label(row, int(t['side']))))
    return pd.DataFrame(rows), files


def table(df, col, order):
    out = {}
    for v in order:
        out[v] = stats(df.loc[df[col] == v, 'R'])
    return out


SPLITS = dict(align=['with', 'counter', 'none'], aligned_event=['BOS', 'MSS', '-'],
              zone=['beyond_with', 'inside', 'beyond_against', 'n/a'], event_age=['fresh<=6', 'old>6', 'n/a'],
              ext_dist=['<=1ATR', '>1ATR', 'n/a'])


def stratified(df, col, a_val, b_val, min_n=5):
    """Within-hypothesis mean-R difference (a - b), inverse-variance-style weights 1/(1/na + 1/nb); hypotheses with fewer
    than min_n trades on either side are skipped. Removes the composition effect of pooling different strategies."""
    num = den = 0.0; used = []
    for h, g in df.groupby('hyp'):
        a, b = g.R[g[col] == a_val], g.R[g[col] == b_val]
        if len(a) >= min_n and len(b) >= min_n:
            w = 1 / (1 / len(a) + 1 / len(b)); num += w * (a.mean() - b.mean()); den += w; used.append(h)
    return dict(diff_mean_r=round(num / den, 3) if den else None, hyps=used)


def stage_inputs(inputs, universe):
    """Packets of one universe from the committed inputs index -> a temp dir (sha256-checked). Never copies into the repo."""
    idx = [x for x in json.load(open(inputs))['packets'] if x['universe'] == universe]
    if not idx: raise SystemExit(f'no packets for {universe} in {inputs}')
    for b in sorted({x['branch'] for x in idx}):
        subprocess.run(['git', 'fetch', '-q', 'origin', b], check=True)
    d = tempfile.mkdtemp(prefix='structure_inputs_')
    for x in idx:
        raw = subprocess.run(['git', 'show', f"{x['commit']}:{x['path']}"], check=True, capture_output=True).stdout
        if hashlib.sha256(raw).hexdigest() != x['packet_sha256']: raise SystemExit(f"packet digest mismatch: {x['path']}")
        open(os.path.join(d, x['hypothesis'] + '.json'), 'wb').write(raw)
    return d


def main(argv=None):
    ap = argparse.ArgumentParser()
    g = ap.add_mutually_exclusive_group(required=True)
    g.add_argument('--packets'); g.add_argument('--inputs')
    ap.add_argument('--universe', choices=('CORE-10', 'CORE-20'))
    ap.add_argument('--cache', action='append', required=True)
    ap.add_argument('--out', required=True)
    a = ap.parse_args(argv)
    if a.inputs:
        if not a.universe: ap.error('--inputs needs --universe')
        a.packets = stage_inputs(a.inputs, a.universe)
    df, files = run(a.packets, a.cache)
    res = dict(n_trades=len(df), all=stats(df.R), files=files, n=SF.N,
               pooled={c: table(df, c, o) for c, o in SPLITS.items()},
               t_with_vs_counter=welch_t(df.R[df['align'] == 'with'], df.R[df['align'] == 'counter']),
               t_inside_vs_beyond_with=welch_t(df.R[df.zone == 'inside'], df.R[df.zone == 'beyond_with']),
               strat_with_minus_counter=stratified(df, 'align', 'with', 'counter'),
               strat_beyond_with_minus_inside=stratified(df, 'zone', 'beyond_with', 'inside'),
               per_hyp={h: dict(all=stats(g.R), **{c: table(g, c, SPLITS[c]) for c in ('align', 'zone')})
                        for h, g in df.groupby('hyp')})
    json.dump(res, open(a.out, 'w'), indent=1, sort_keys=True)
    print(json.dumps(dict(n=res['n_trades'], all=res['all'], align=res['pooled']['align'], zone=res['pooled']['zone'],
                          t_wc=res['t_with_vs_counter'], t_ib=res['t_inside_vs_beyond_with'])))


if __name__ == '__main__':
    main()
