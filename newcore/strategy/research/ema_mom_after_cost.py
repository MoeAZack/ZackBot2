"""After-cost evidence re-run of trend_ema_mom.v1 (M3 input). RESEARCH ONLY: never imported by the runtime.

QUICK SIZING MODEL, NOT THE ENGINE. Fixed-fractional risk (1% of closed equity per entry), at most one position per
symbol, a gross-notional cap (default 3x equity, slice plan 2.2), next-open market entries, stop = 2.5 ATR from the fill,
stop / gap / signal exits. Mechanics mirror legacy backtest.run for a stop-only sleeve (checked trade-for-trade with
--legacy-check). No daily halt, drawdown kill, exchange filters or NC-06 admit(): the full engine (S4) replaces this.

Costs (slice plan 5.3 / backtest.py): taker fee 0.05% per side on notional, slippage 0.02% per side on every fill
(entry, stop, gap, signal exit), funding as a FLAT cost of 0.005% of notional per 4h bar charged on BOTH sides.
There is no point-in-time funding history in the repo (DATA-01), so funding is an ASSUMPTION; a stress row doubles it.

Usage (from the repo root):
  python newcore/strategy/research/ema_mom_after_cost.py --out <file.md> [--trades-csv <file.csv>] [--legacy-check]
"""
from __future__ import annotations

import argparse
import csv
import hashlib
import json
import os
import subprocess
import sys
from datetime import datetime, timezone

import numpy as np

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))
sys.path.insert(0, ROOT)

import newcore.strategy as NS                                                                   # noqa: E402

CORE8 = ('BTCUSDT', 'ETHUSDT', 'SOLUSDT', 'BNBUSDT', 'XRPUSDT', 'DOGEUSDT', 'AVAXUSDT', 'LINKUSDT')
H4 = 14_400_000
HOLDOUT_MS = int(datetime(2025, 1, 1, tzinfo=timezone.utc).timestamp() * 1000)
COSTS = {                                   # name: (fee per side, slippage per side, funding per 4h bar on notional)
    'gross (no costs)': (0.0, 0.0, 0.0),
    'base': (0.0005, 0.0002, 0.00005),
    '2x fee+slip': (0.0010, 0.0004, 0.00005),
    'funding x2': (0.0005, 0.0002, 0.00010),
    '2x all': (0.0010, 0.0004, 0.00010),
}


def to_ms(s):
    return int(datetime.strptime(s, '%Y-%m-%d %H:%M:%S').replace(tzinfo=timezone.utc).timestamp()) * 1000


def load(sym):
    path = os.path.join(ROOT, 'data_long', '4h', f'{sym}_4h.csv')
    cols = {k: [] for k in 'tohlc'}
    with open(path, newline='') as f:
        for r in csv.DictReader(f):
            cols['t'].append(to_ms(r['t']))
            for k in 'ohlc':
                cols[k].append(float(r[k]))
    with open(path, 'rb') as f:
        sha = hashlib.sha256(f.read()).hexdigest()
    return NS.Bars(sym, H4, *(tuple(cols[k]) for k in 'tohlc')), sha


def simulate(book, mode, fee, slip, fund, risk=0.01, start=10_000.0, max_lev=3.0, max_pos=None):
    """book: {sym: (Bars, Evaluation)} on identical timestamps. mode: 'long' | 'short' | 'both'.
    Per bar i: (1) fill entries signalled at i-1 at o[i]; (2) manage every open lot: funding, gap through the stop at the
    open, stop touched inside the bar, signal exit at the close; (3) new signals at the close of i. Same order as
    legacy backtest.run. Returns (trades, curve[(t_ms, mtm equity)], stats)."""
    syms = list(book)
    T = book[syms[0]][0].t_ms
    n = len(T)
    first = max(ev.start for _, ev in book.values())
    eq = start
    pos, pend, trades, curve = {}, {}, [], []
    capped = 0
    max_gross = 0.0

    def notional(i):
        return sum(p['qty'] * book[s][0].c[i - 1] for s, p in pos.items())

    def close(s, p, px, i, why):
        nonlocal eq
        px = px * (1 - p['side'] * slip)
        pnl = p['side'] * (px - p['avg']) * p['qty'] - p['qty'] * px * fee
        eq += pnl
        p['realized'] += pnl
        trades.append(dict(sym=s, side='LONG' if p['side'] == 1 else 'SHORT', i_sig=p['i'] - 1, i_in=p['i'], i_out=i,
                           t_in=T[p['i']], t_out=T[i], t_end=T[i] + H4, px_in=p['avg'], px_out=px, why=why, pnl=p['realized'],
                           R=p['realized'] / p['risk'], risk=p['risk']))

    for i in range(first, n):
        # (1) entries (sized off the equity at the start of the bar, as legacy sl_eq)
        eq0 = eq
        for s, side in list(pend.items()):
            if max_pos is not None and len(pos) >= max_pos:
                break
            b, ev = book[s]
            atr = ev.atr[i - 1]
            px = b.o[i] * (1 + side * slip)
            risk_usd = eq0 * risk
            R = float(NS.stop_distance(atr, NS.Params().stop_atr))
            qty_raw = risk_usd / R
            qty = min(qty_raw, max(0.0, max_lev * eq0 - notional(i)) / px)
            if qty < qty_raw:
                capped += 1
            if qty <= 0:
                continue
            eq -= qty * px * fee
            pos[s] = dict(side=side, qty=qty, avg=px, stop=px - side * R, risk=risk_usd, i=i, realized=-qty * px * fee)
        pend = {}
        # (2) manage
        for s in list(pos):
            p = pos[s]
            b, ev = book[s]
            sd = p['side']
            o, h, l, c = b.o[i], b.h[i], b.l[i], b.c[i]
            f = p['qty'] * c * fund
            eq -= f
            p['realized'] -= f
            if (o <= p['stop']) if sd == 1 else (o >= p['stop']):
                close(s, p, o, i, 'gap_stop'); del pos[s]; continue
            if (l <= p['stop']) if sd == 1 else (h >= p['stop']):
                close(s, p, p['stop'], i, 'stop'); del pos[s]; continue
            if (ev.lx if sd == 1 else ev.sx)[i]:
                close(s, p, c, i, 'signal'); del pos[s]
        # (3) new signals -> next open
        if i + 1 < n:
            for s in syms:
                if s in pos:
                    continue
                ev = book[s][1]
                side = 1 if (mode in ('long', 'both') and ev.le[i]) else (-1 if (mode in ('short', 'both') and ev.se[i]) else 0)
                if side:
                    pend[s] = side
        up = sum(p['side'] * (book[s][0].c[i] - p['avg']) * p['qty'] for s, p in pos.items())
        curve.append((T[i], eq + up))
        gross = sum(p['qty'] * book[s][0].c[i] for s, p in pos.items())
        max_gross = max(max_gross, gross / (eq + up))
    return trades, curve, dict(capped=capped, open_at_end=len(pos), max_gross_lev=max_gross)


def max_dd(curve, t0=None, t1=None):
    pk, dd = None, 0.0
    for t, v in curve:
        if (t0 is not None and t < t0) or (t1 is not None and t >= t1):
            continue
        pk = v if pk is None else max(pk, v)
        dd = min(dd, v / pk - 1)
    return dd


def boot_ci(r, n_boot=10_000, seed=20261008):
    if len(r) < 2:
        return (float('nan'), float('nan'))
    rng = np.random.default_rng(seed)
    a = np.asarray(r, float)
    means = a[rng.integers(0, len(a), size=(n_boot, len(a)))].mean(axis=1)
    return float(np.percentile(means, 2.5)), float(np.percentile(means, 97.5))


def episodes(tr):
    """Independent episodes: trades whose holding windows [fill-bar open, exit-bar close) overlap in time, on ANY
    symbol, are one episode (core-8 coins move together, so simultaneous trades are not independent samples). Within a
    symbol trades never overlap (one position per symbol), so a lone trade is its own episode."""
    out, end = [], None
    for t in sorted(tr, key=lambda t: (t['t_in'], t['sym'])):
        if out and t['t_in'] < end:
            out[-1].append(t); end = max(end, t['t_end'])
        else:
            out.append([t]); end = t['t_end']
    return out


def cluster_ci(tr, n_boot=10_000, seed=20261008):
    """95% CI of mean R per trade, resampling whole EPISODES with replacement (cluster bootstrap)."""
    eps = episodes(tr)
    if len(eps) < 2:
        return (float('nan'), float('nan'))
    rs = np.array([sum(t['R'] for t in e) for e in eps], float)
    ns = np.array([len(e) for e in eps], float)
    rng = np.random.default_rng(seed)
    idx = rng.integers(0, len(eps), size=(n_boot, len(eps)))
    m = rs[idx].sum(axis=1) / ns[idx].sum(axis=1)
    return float(np.percentile(m, 2.5)), float(np.percentile(m, 97.5))


def btc_regime(b, ema_days=200, min_days=30):
    """Per 4h bar of BTC: 'bull' / 'bear' from the last COMPLETED UTC-day close vs its daily EMA200 (point in time,
    legacy strategies.regime definition), 'unknown' before min_days daily closes."""
    day = lambda ms: ms // 86_400_000
    closes, days = [], []
    for t, c in zip(b.t_ms, b.c):
        if days and days[-1] == day(t):
            closes[-1] = c
        else:
            days.append(day(t)); closes.append(c)
    e, a = [], 2.0 / (ema_days + 1)
    for c in closes:
        e.append(c if not e else (1 - a) * e[-1] + a * c)
    out, k = [], 0
    for t in b.t_ms:
        ct = t + H4
        while k < len(days) and (days[k] + 1) * 86_400_000 <= ct:
            k += 1
        out.append('unknown' if k < min_days else ('bull' if closes[k - 1] > e[k - 1] else 'bear'))
    return out


def summarize(tr):
    r = [t['R'] for t in tr]
    gw = sum(t['pnl'] for t in tr if t['pnl'] > 0)
    gl = -sum(t['pnl'] for t in tr if t['pnl'] < 0)
    lo, hi = boot_ci(r)
    clo, chi = cluster_ci(tr)
    return dict(ep=len(episodes(tr)), clo=clo, chi=chi, n=len(tr), n_long=sum(t['side'] == 'LONG' for t in tr), n_short=sum(t['side'] == 'SHORT' for t in tr),
                exp=float(np.mean(r)) if r else float('nan'), lo=lo, hi=hi,
                pf=(gw / gl) if gl > 0 else float('inf'), win=(sum(x > 0 for x in r) / len(r)) if r else float('nan'),
                net=sum(t['pnl'] for t in tr))


def fmt(x, nd=3, pct=False):
    if x != x:
        return 'n/a'
    if x == float('inf'):
        return 'inf'
    return f'{x * 100:.1f}%' if pct else f'{x:+.{nd}f}' if nd == 3 else f'{x:.{nd}f}'


def year(ms):
    return datetime.fromtimestamp(ms / 1000, tz=timezone.utc).year


def concentration(tr, key):
    groups = {}
    for t in tr:
        groups.setdefault(key(t), []).append(t)
    tot_net = sum(t['pnl'] for t in tr)
    tot_r = sum(t['R'] for t in tr)
    rows = []
    for k in sorted(groups):
        g = groups[k]
        net = sum(t['pnl'] for t in g)
        sr = sum(t['R'] for t in g)
        rows.append((k, len(g), sr, sr / len(g), net, net / tot_net if tot_net else float('nan'), sr / tot_r if tot_r else float('nan')))
    return rows


def legacy_check(book, raw_frames):
    """Trade-for-trade comparison with legacy backtest.run at base cost (research cross-check only)."""
    import pandas as pd
    import backtest as LB
    lbook = LB.Book({s: pd.DataFrame(dict(t=pd.to_datetime(b.t_ms, unit='ms'), o=b.o, h=b.h, l=b.l, c=b.c, v=v))
                     for s, (b, v) in raw_frames.items()})
    out = []
    for mode in ('long', 'short', 'both'):
        ltr, _ = LB.run(lbook, [dict(key='ema_mom', share=1.0, risk=0.01, max_pos=len(book), sides=mode)], start=10_000.0,
                        max_lev=3.0, daily_halt=1.0, warmup=220, maint_margin=None)
        mine, _, _ = simulate(book, mode, *COSTS['base'])
        a = sorted((t['sym'], 1 if t['side'] == 'LONG' else -1, t['i_in'], t['i_out']) for t in mine)
        b = sorted((r.sym, int(r.side), int(r.i_in), int(r.i_out)) for r in ltr.itertuples())
        mr = {(t['sym'], t['i_in']): t['R'] for t in mine}
        dr = max((abs(mr[(r.sym, int(r.i_in))] - r.R) for r in ltr.itertuples() if (r.sym, int(r.i_in)) in mr), default=0.0)
        out.append((mode, len(a), len(b), a == b, dr))
    return out


def report(book, shas, git_sha, legacy):
    L = []
    w = L.append
    T = book[CORE8[0]][0].t_ms
    first = max(ev.start for _, ev in book.values())
    reg = btc_regime(book['BTCUSDT'][0])
    w('# trend_ema_mom.v1 after-cost evidence (M3 input) - candidate evidence, long side\n')
    w('**QUICK SIZING MODEL, NOT THE ENGINE.** Fixed-fractional 1% risk of closed equity per entry, at most one position '
      'per symbol (long or short), gross notional capped at 3x equity, next-open market entries, stop 2.5 x ATR14 from '
      'the fill, exits on stop / gap through stop (at the open) / signal at the close. No daily halt, drawdown kill, '
      'exchange filters or risk gateway. Mechanics match legacy backtest.run for a stop-only sleeve (see cross-check).\n')
    w(f'- Code: `{git_sha}`; rule `{NS.RULE_ID}` from `newcore/strategy` (signals checked == legacy sig_ema_mom on 4h).')
    w(f'- Data: `data_long/4h`, core 8, {len(T)} bars each, first signal bar '
      f'{datetime.fromtimestamp(T[first] / 1000, tz=timezone.utc):%Y-%m-%d %H:%M} UTC (220-bar warmup) to '
      f'{datetime.fromtimestamp(T[-1] / 1000, tz=timezone.utc):%Y-%m-%d %H:%M} UTC. sha256 match DATA_MANIFEST: '
      f'{all(v[1] for v in shas.values())}.')
    w('- **Universe / survivorship:** core 8 (BTC, ETH, SOL, BNB, XRP, DOGE, AVAX, LINK) is fixed for the whole window. '
      'All 8 were listed Binance USDT-M perps before 2021-12-19, so there is no listing look-ahead, but the set is '
      'chosen today from coins that stayed large: coins that collapsed or were delisted in the window (e.g. LUNA, FTT) '
      'cannot appear, which flatters a long trend rule. No point-in-time universe (historical top-N by volume or market '
      'cap) exists in the repo, so none is used; no top-40 hindsight list is used.')
    w('- **Costs:** taker fee 0.05%/side on notional, slippage 0.02%/side on every fill (stop fills at stop x (1 -/+ '
      'slip), gaps at the open x (1 -/+ slip)). **Funding is an ASSUMPTION**: no point-in-time funding history in the '
      'repo (DATA-01), so a flat 0.005% of notional per 4h bar (= 0.01%/8h, the Binance base rate) is charged as a '
      'cost on BOTH sides. Roughly right-signed for longs; for shorts real funding was mostly a credit, so the short '
      'numbers are pessimistic. Stress rows double fee+slip and/or funding.')
    w('- **Split** by entry fill time: in-sample 2022-01 -> 2024-12, holdout 2025-01 -> 2026-10-04. Caveat: the legacy '
      'research and preset choices already saw the whole window, so the holdout is not untouched; the only untouched '
      'sample is forward data from 2026-10-04.')
    w('- **Statistics:** expectancy = mean net R per closed trade (R = net $ incl. fees, slippage, funding / risked $). '
      'Two 95% bootstrap CIs (10,000 resamples, seed 20261008): per trade, and **clustered by episode** (whole episodes '
      'resampled). An **episode** = trades whose holding windows overlap in time on any symbol; the episode count is '
      'the effective number of independent samples. PF on net $. Max DD on mark-to-market equity at every 4h close. '
      'Positions open at the end are excluded. Regime = BTC last completed UTC-day close vs its daily EMA200 at the '
      'signal (point in time; the EMA is seeded at 2021-12-19, so early-2022 labels lean on a short seed).\n')
    if legacy:
        w('**Legacy cross-check (base cost, backtest.run with the same sizing, max_lev 3, daily halt off):**\n')
        w('| mode | trades (this) | trades (legacy) | identical (sym, side, entry bar, exit bar) | max abs R diff |')
        w('|---|---|---|---|---|')
        for mode, a, b, eqv, dr in legacy:
            w(f'| {mode} | {a} | {b} | {eqv} | {dr:.2e} |')
        w('')
    runs = {}
    for mode in ('long', 'short', 'both'):
        for cname, (fee, slip, fund) in COSTS.items():
            runs[(mode, cname)] = simulate(book, mode, fee, slip, fund)
    runs[('long', 'base, max_pos 4')] = simulate(book, 'long', *COSTS['base'], max_pos=4)
    title = {'long': 'Long only (the candidate)',
             'short': 'Short only - mirrored rule: MECHANICS PARITY, NOT EDGE',
             'both': 'Long + mirrored short (one position per symbol) - short legs are mechanics parity, not edge'}
    ci = lambda a, b: f'[{fmt(a)}, {fmt(b)}]'
    for mode in ('long', 'short', 'both'):
        w(f'## {title[mode]}\n')
        w('| costs | trades (L/S) | episodes | win | exp R | trade CI | episode CI | PF | net % | max DD | '
          'IS n/ep exp [episode CI] | holdout n/ep exp [episode CI] | IS DD | holdout DD |')
        w('|---|---|---|---|---|---|---|---|---|---|---|---|---|---|')
        for k in [k for k in runs if k[0] == mode]:
            tr, cv, st = runs[k]
            a = summarize(tr)
            is_ = summarize([t for t in tr if t['t_in'] < HOLDOUT_MS])
            ho = summarize([t for t in tr if t['t_in'] >= HOLDOUT_MS])
            w(f"| {k[1]} | {a['n']} ({a['n_long']}/{a['n_short']}) | {a['ep']} | {fmt(a['win'], pct=True)} | "
              f"{fmt(a['exp'])} | {ci(a['lo'], a['hi'])} | {ci(a['clo'], a['chi'])} | {fmt(a['pf'], 2)} | "
              f"{fmt(cv[-1][1] / 10_000 - 1, pct=True)} | {fmt(max_dd(cv), pct=True)} | "
              f"{is_['n']}/{is_['ep']} {fmt(is_['exp'])} {ci(is_['clo'], is_['chi'])} | "
              f"{ho['n']}/{ho['ep']} {fmt(ho['exp'])} {ci(ho['clo'], ho['chi'])} | "
              f"{fmt(max_dd(cv, t1=HOLDOUT_MS), pct=True)} | {fmt(max_dd(cv, t0=HOLDOUT_MS), pct=True)} |")
        tr, cv, st = runs[(mode, 'base')]
        eps = episodes(tr)
        sizes = sorted(len(e) for e in eps)
        w(f"\nBase run: {st['capped']} entries trimmed by the 3x notional cap, {st['open_at_end']} positions open at the "
          f"end (excluded), peak gross leverage {st['max_gross_lev']:.2f}x. Episodes: {len(eps)} from {len(tr)} trades "
          f"(trades per episode: median {sizes[len(sizes) // 2]}, max {sizes[-1]}; "
          f"{sum(1 for x in sizes if x == 1)} single-trade episodes).")
        if mode == 'both':
            for side in ('LONG', 'SHORT'):
                s = summarize([t for t in tr if t['side'] == side])
                w(f"- {side} legs inside the combined base run: n {s['n']} / {s['ep']} episodes, exp {fmt(s['exp'])} R, "
                  f"episode CI {ci(s['clo'], s['chi'])}, PF {fmt(s['pf'], 2)}.")
        w('\nCuts at base cost (episodes and CI computed inside each cut; flags: symbol > 40% or year > 50% of net $ or '
          'of summed R):\n')
        w('| cut | trades | episodes | mean R | episode CI | sum R | net $ | share of net $ | share of sum R |')
        w('|---|---|---|---|---|---|---|---|---|')
        tot_net, tot_r = sum(t['pnl'] for t in tr), sum(t['R'] for t in tr)
        cuts = (('regime ', lambda t: reg[t['i_sig']], None), ('', lambda t: t['sym'], 0.40),
                ('year ', lambda t: year(t['t_in']), 0.50))
        for label, key, lim in cuts:
            groups = {}
            for t in tr:
                groups.setdefault(key(t), []).append(t)
            for g in sorted(groups):
                s = summarize(groups[g])
                sr = sum(t['R'] for t in groups[g])
                sh = s['net'] / tot_net if tot_net else float('nan')
                shr = sr / tot_r if tot_r else float('nan')
                flag = ' **(!)**' if lim and ((sh == sh and sh > lim) or (shr == shr and shr > lim)) else ''
                w(f"| {label}{g} | {s['n']} | {s['ep']} | {fmt(s['exp'])} | {ci(s['clo'], s['chi'])} | {sr:+.2f} | "
                  f"{s['net']:+,.0f} | {fmt(sh, pct=True)} | {fmt(shr, pct=True)}{flag} |")
        srt = sorted(tr, key=lambda t: -t['R'])
        w('\nOutlier dependence (base): ' + '; '.join(
            f"without top {k}: exp {fmt(s['exp'])} R, episode CI {ci(s['clo'], s['chi'])}"
            for k in (1, 5) for s in [summarize(srt[k:])]) + '. Top 5: ' + ', '.join(
            f"{t['sym']} {t['side'][0]} {datetime.fromtimestamp(t['t_in'] / 1000, tz=timezone.utc):%Y-%m-%d} {t['R']:+.1f}R"
            for t in srt[:5]) + '.')
        exits = {}
        for t in tr:
            exits[t['why']] = exits.get(t['why'], 0) + 1
        w(f'\nExit mix (base): {json.dumps(exits, sort_keys=True)}. Worst trade {min((t["R"] for t in tr), default=float("nan")):+.2f} R, '
          f'best {max((t["R"] for t in tr), default=float("nan")):+.2f} R.\n')
    # candidate-evidence read for the long (Codex ruling: no universal raw trade-count gate)
    rl = {k: summarize(runs[('long', k)][0]) for k in ('base', '2x fee+slip', '2x all')}
    ho = {k: summarize([t for t in runs[('long', k)][0] if t['t_in'] >= HOLDOUT_MS]) for k in ('base', '2x fee+slip')}
    dd = max_dd(runs[('long', 'base')][1])
    w('## Candidate-evidence read, long side\n')
    w('No universal raw trade-count gate (Codex ruling); the effective sample is the episode count. The short mirror '
      'is mechanics parity only and cannot satisfy an edge milestone.\n')
    w('| check | value | met |')
    w('|---|---|---|')
    for k in ('base', '2x fee+slip', '2x all'):
        w(f"| episode-clustered CI lower bound > 0, full window, {k} | {fmt(rl[k]['clo'])} R | {rl[k]['clo'] > 0} |")
    for k in ('base', '2x fee+slip'):
        w(f"| holdout (2025-26) episode-clustered CI lower bound > 0, {k} | {fmt(ho[k]['clo'])} R "
          f"({ho[k]['n']} trades / {ho[k]['ep']} episodes) | {ho[k]['clo'] > 0} |")
    w(f"| point expectancy > 0 at 2x fee+slip | {fmt(rl['2x fee+slip']['exp'])} R | {rl['2x fee+slip']['exp'] > 0} |")
    w(f"| max DD <= 35% at 1% risk (base, full window) | {fmt(dd, pct=True)} | {dd >= -0.35} |")
    w(f"| effective independent samples (episodes), full / holdout | {rl['base']['ep']} / {ho['base']['ep']} "
      f"(from {rl['base']['n']} / {ho['base']['n']} trades) | report only |")
    for k in (1, 5):
        s = summarize(sorted(runs[('long', 'base')][0], key=lambda t: -t['R'])[k:])
        w(f"| (robustness) episode CI lower bound > 0 at base without the top {k} trade(s) | {fmt(s['clo'])} R | {s['clo'] > 0} |")
    return '\n'.join(L) + '\n', runs


def main():
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument('--out', required=True)
    ap.add_argument('--trades-csv')
    ap.add_argument('--legacy-check', action='store_true')
    a = ap.parse_args()
    manifest = json.load(open(os.path.join(ROOT, 'DATA_MANIFEST.json'), encoding='utf-8'))
    files = manifest.get('files', manifest)
    book, shas, raw = {}, {}, {}
    p = NS.Params(enable_short=True)
    for s in CORE8:
        b, sha = load(s)
        rec = files.get(f'data_long/4h/{s}_4h.csv', {})
        shas[s] = (sha, rec.get('sha256') == sha)
        book[s] = (b, NS.evaluate(b, p, as_of_ms=b.close_ms(len(b) - 1)))
        raw[s] = (b, [1.0] * len(b))
    assert len({book[s][0].t_ms for s in CORE8}) == 1, 'core-8 timestamps differ'
    try:
        git_sha = subprocess.run(['git', 'rev-parse', 'HEAD'], cwd=ROOT, capture_output=True, text=True).stdout.strip()
    except Exception:
        git_sha = 'unknown'
    legacy = legacy_check(book, raw) if a.legacy_check else None
    text, runs = report(book, shas, git_sha, legacy)
    os.makedirs(os.path.dirname(os.path.abspath(a.out)), exist_ok=True)
    with open(a.out, 'w', encoding='utf-8') as f:
        f.write(text)
    if a.trades_csv:
        with open(a.trades_csv, 'w', newline='', encoding='utf-8') as f:
            wr = csv.writer(f)
            wr.writerow(['mode', 'costs', 'sym', 'side', 'i_in', 'i_out', 't_in_utc', 'px_in', 'px_out', 'why', 'pnl', 'R'])
            for (mode, cname), (tr, _, _) in runs.items():
                for t in tr:
                    wr.writerow([mode, cname, t['sym'], t['side'], t['i_in'], t['i_out'],
                                 datetime.fromtimestamp(t['t_in'] / 1000, tz=timezone.utc).strftime('%Y-%m-%d %H:%M'),
                                 f"{t['px_in']:.10g}", f"{t['px_out']:.10g}", t['why'], f"{t['pnl']:.6f}", f"{t['R']:.6f}"])
    print(text)


if __name__ == '__main__':
    main()
