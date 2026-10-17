"""C1 dev-run CLI: liquidity_sweep_fvg.v0 over a 4h app cache, one tier or symbol list, both sides - development evidence only.

Built on tools/research/run_breadth_candidate.py conventions; its guards and helpers (and run_vwaprev_candidate's) are
imported, not copied:
  - window: explicit START/END, UTC, 4h grid, end-exclusive, through VC.check_window (pit_run.rex_window + the frozen
    development window + the sealed cutoff + the 220 x 4h warm-up guard + calibration overlap), plus this runner's own
    holdout check: any END after HOLDOUT_START (2026-09-28T00:00:00Z) is refused.
  - universe: the frozen static tiers (CORE-10 / CORE-20) or an explicit --symbols list inside the frozen universe.
  - clean git tree before a single cache byte is read; packets are never overwritten; JSON sorted, allow_nan False,
    packet_sha256 over the canonical packet; runner sha256 of every file the numbers depend on.
  - cache: <cache>/<SYMBOL>_4h.csv (app._get_candles format) via B.read_cache, sliced on the raw rows to
    [warm-up start, END) before any indicator exists (VC.slice_frame + VC.assert_bounded), completeness on the 4h grid
    (B.completeness). STRICTER than breadth: any requested symbol with a missing file or an incomplete slice REFUSES the
    whole run (breadth drops such a symbol from the book); C1 has no BTC regime split, so BTCUSDT is not loaded.
  - warm-up: the 220 bars before START feed ATR14 and the pivots only. Signals whose FVG bar n opens before START are
    removed BEFORE the one-slot-per-symbol logic (the run starts flat at START, like backtest.run(t0=START)); they are
    counted in warmup.signals_excluded and never traded, skipped or reported as overlap.
  - trades still open at END (no exit inside [START, END)): backtest.run's convention (run_breadth_candidate inherits
    it) is that a position still open after the last in-window bar never becomes a trade row - it is not force-closed.
    Mirrored: liquidity_sweep_fvg labels it 'open_end' (R_net None); it is excluded from every statistic, counted in
    open_at_end and listed there with its entry. A close-rule exit decided on the last bar (fill at open[END]) is
    therefore also open_end. A signal on the last in-window bar is a 'no_next_bar' skip.
  - the rules are liquidity_sweep_fvg.run_symbol / simulate_trade / summarize, unchanged; summarize supplies n, net /
    gross / fee / slip / funding R, PF and R<-1 by label for each of long, short, combined.

Added per side and combined: wins, avg R, max DD R (cumulative net R in exit order), per-quarter net R by ENTRY time
(own sign, every calendar quarter overlapping the window, empty quarters 0), mean / median hold bars, MFE / MAE /
giveback gross / net (per trade and mean), skip counts (cost_guard, gap_stop, overlap, conflict, no_next_bar), symbol concentration.

Decision (Codex reading, per tier; the cross-tier check is a later analyzer): CANDIDATE only if combined PF > 1.0 AND
combined positive quarters >= 2/3 of the window's quarters AND gross R per trade >= 3 x cost R per trade; else PARK.

Usage (PAPER research only, no network, no app settings or credentials):

  py -3.14 tools/research/run_lsfvg.py --cache <DATA>/candles --window 2024-10-14T00:00:00Z/2026-09-28T00:00:00Z \
     --tier CORE-10 --out <packet.json>
"""
import argparse
import contextlib
import hashlib
import json
import os
import sys
from datetime import datetime, timezone

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(os.path.dirname(HERE))
for _p in (ROOT, HERE):
    if _p not in sys.path:
        sys.path.insert(0, _p)

import numpy as np                                                                          # noqa: E402

import liquidity_sweep_fvg as LS                                                            # noqa: E402
import run_breadth_candidate as B                                                           # noqa: E402
import run_vwaprev_candidate as VC                                                          # noqa: E402
import splits as SP                                                                         # noqa: E402

FORMAT = 'zb-lsfvg-dev/1'
EVIDENCE_CLASS = 'candidate-runtime'
TF, TF_MS = VC.TF, VC.TF_MS
HOLDOUT_START = VC.SEALED_CUTOFF            # END may equal it (end-exclusive), never pass it
WARMUP_BARS = VC.WARMUP_BARS
assert LS.TF == TF and WARMUP_BARS >= 200
SKIPS = ('cost_guard', 'gap_stop', 'overlap', 'conflict', 'no_next_bar')
LABELS = ('stop', 'target', 'invalidation', 'time')
ND = 9                                      # float rounding in the packet

_num, _utc, _ms, _canon = VC._num, VC._utc, VC._ms, VC._canon


class RunnerError(RuntimeError):
    pass


def _r(x):
    return _num(x, ND)


def _err(e) -> RunnerError:
    return RunnerError(str(e).replace('VWAPREV candidate', 'lsfvg').replace('breadth candidate', 'lsfvg'))


def runner_digest() -> dict:
    return {os.path.basename(m.__file__): B._sha(os.path.abspath(m.__file__)) for m in (sys.modules[__name__], LS, B, VC)}


# ------------------------------------------------------------------ guards
def check_window(window, plan=None):
    """(lo, hi, warm_lo) ms. VC.check_window (grid, dev window, sealed cutoff, warm-up, calibration) + own holdout check."""
    if not window:
        raise RunnerError('lsfvg: an explicit --window START/END is required')
    try:
        lo, hi, warm_lo = VC.check_window(window, plan)
    except VC.RunnerError as e:
        raise _err(e) from None
    if hi > SP.parse_utc(HOLDOUT_START):
        raise RunnerError(f'lsfvg: END {_utc(hi)} is inside the sealed holdout (starts {HOLDOUT_START}); refused')
    return lo, hi, warm_lo


# ------------------------------------------------------------------ signals inside the window only
@contextlib.contextmanager
def window_signals(lo_i: int, excluded: list):
    """Run LS.run_symbol with its detector restricted to signals whose FVG bar n >= lo_i (the first in-window bar).
    The rule code is untouched: LS.detect is wrapped for the duration and restored; dropped signals go to `excluded`."""
    orig = LS.detect

    def detect(df, side, atr=None):
        gs = orig(df, side, atr=atr)
        excluded.extend(g for g in gs if g['n'] < lo_i)
        return [g for g in gs if g['n'] >= lo_i]

    LS.detect = detect
    try:
        yield
    finally:
        LS.detect = orig


# ------------------------------------------------------------------ per-trade path measures
def excursions(df, t) -> dict:
    """MFE / MAE / giveback in R from real prices on the path entry -> exit: the full ranges of bars [entry_i, exit_i),
    plus the entry fill price, the exit bar's open and the exit price (the exit bar's range after the exit is never
    counted; for an intrabar stop / target only the exit level of that bar counts). giveback_gross_r = MFE - gross R,
    giveback_net_r = MFE - net R (Codex 6228979583: no unqualified giveback field)."""
    e, x, E, X, R = t['entry_i'], t['exit_i'], t['entry'], t['exit'], t['R_unit']
    h = np.asarray(df['h'], dtype=float)
    lw = np.asarray(df['l'], dtype=float)
    o = np.asarray(df['o'], dtype=float)
    hi = max([E, X, float(o[x])] + list(h[e:x]))
    lo = min([E, X, float(o[x])] + list(lw[e:x]))
    fav, adv = (E - lo, hi - E) if t['side'] == 'short' else (hi - E, E - lo)
    mfe, mae = fav / R, adv / R
    return dict(mfe_r=mfe, mae_r=mae, giveback_gross_r=mfe - t['R_gross'], giveback_net_r=mfe - t['R_net'])


def quarter(ms: int) -> str:
    d = datetime.fromtimestamp(ms / 1000, tz=timezone.utc)
    return f'{d.year}Q{(d.month - 1) // 3 + 1}'


def window_quarters(lo: int, hi: int) -> list:
    """Every calendar quarter overlapping [lo, hi)."""
    d = datetime.fromtimestamp(lo / 1000, tz=timezone.utc)
    y, q, out = d.year, (d.month - 1) // 3, []
    while True:
        start = int(datetime(y, 3 * q + 1, 1, tzinfo=timezone.utc).timestamp() * 1000)
        if start >= hi:
            return out
        out.append(f'{y}Q{q + 1}')
        y, q = (y + 1, 0) if q == 3 else (y, q + 1)


# ------------------------------------------------------------------ stats
def group_stats(rows, skips, opens, quarters, summary) -> dict:
    """rows: closed trade rows of one group; summary: the matching LS.summarize block (same trades)."""
    n = len(rows)
    net = [x['R_net'] for x in rows]
    cost = summary['fee_R'] + summary['slip_R'] + summary['fund_R']
    q = {k: 0.0 for k in quarters}
    for x in rows:
        q[x['quarter']] = q.get(x['quarter'], 0.0) + x['R_net']
    order = sorted(rows, key=lambda x: (x['t_out'], x['t_in'], x['symbol'], x['side']))
    cum = np.cumsum([0.0] + [x['R_net'] for x in order])
    hold = [x['hold_bars'] for x in rows]
    sk = {k: sum(1 for s in skips if s['skip'] == k) for k in SKIPS}
    return dict(
        n=summary['n'], wins=sum(1 for v in net if v > 0), losses=sum(1 for v in net if v < 0),
        net_R=_r(summary['net_R']), gross_R=_r(summary['gross_R']), fee_R=_r(summary['fee_R']),
        slip_R=_r(summary['slip_R']), fund_R=_r(summary['fund_R']), cost_R=_r(cost), pf=_r(summary['pf']),
        avg_R=_r(summary['net_R'] / n) if n else None,
        gross_R_per_trade=_r(summary['gross_R'] / n) if n else None, cost_R_per_trade=_r(cost / n) if n else None,
        max_dd_R=_r(float((np.maximum.accumulate(cum) - cum).max())),
        quarters={k: _r(v) for k, v in sorted(q.items())}, positive_quarters=sum(1 for v in q.values() if v > 0),
        hold_bars_mean=_r(np.mean(hold)) if n else None, hold_bars_median=_r(np.median(hold)) if n else None,
        mfe_r_mean=_r(np.mean([x['mfe_r'] for x in rows])) if n else None,
        mae_r_mean=_r(np.mean([x['mae_r'] for x in rows])) if n else None,
        giveback_gross_r_mean=_r(np.mean([x['giveback_gross_r'] for x in rows])) if n else None,
        giveback_net_r_mean=_r(np.mean([x['giveback_net_r'] for x in rows])) if n else None,
        exits={k: sum(1 for x in rows if x['label'] == k) for k in LABELS},
        lt_minus1=summary['lt_minus1'], lt_minus1_by_label=dict(summary['lt_minus1_by_label']),
        skips=sk, conflict_events=len({(s['symbol'], s['n']) for s in skips if s['skip'] == 'conflict'}),
        open_end=len(opens))


def concentration(rows, symbols) -> dict:
    n, net = len(rows), sum(x['R_net'] for x in rows)
    out = {}
    for s in symbols:
        sel = [x for x in rows if x['symbol'] == s]
        v = sum(x['R_net'] for x in sel)
        out[s] = dict(n=len(sel), trade_share=_r(len(sel) / n) if n else None, net_R=_r(v),
                      net_R_share=_r(v / net) if net != 0 else None)
    return out


def decision(c: dict, quarters) -> dict:
    n, nq = c['n'], len(quarters)
    pf_ok = n > 0 and ((c['pf'] is not None and c['pf'] > 1.0) or (c['pf'] is None and c['losses'] == 0 and c['wins'] > 0))
    q_ok = nq > 0 and 3 * c['positive_quarters'] >= 2 * nq
    cost_ok = n > 0 and c['gross_R'] >= 3 * c['cost_R']
    return dict(
        rule='CANDIDATE only if combined PF > 1.0 AND combined own quarterly net R > 0 in >= 2/3 of the window quarters '
             'AND gross R per trade >= 3 x cost R per trade; otherwise PARK (Codex reading; cross-tier check later)',
        pf=c['pf'], pf_ok=bool(pf_ok), positive_quarters=c['positive_quarters'], quarters=nq, quarters_ok=bool(q_ok),
        gross_R_per_trade=c['gross_R_per_trade'], cost_R_per_trade=c['cost_R_per_trade'], cost_ok=bool(cost_ok),
        verdict='CANDIDATE' if (pf_ok and q_ok and cost_ok) else 'PARK')


# ------------------------------------------------------------------ the run
def run(*, cache: str, window, out: str, tier=None, symbols=None, repo: str = VC.ROOT, plan=None) -> dict:
    lo, hi, warm_lo = check_window(window, plan)
    try:
        static = VC.frozen_universe()
        syms = VC.pick_symbols(static, tier, symbols)
    except VC.RunnerError as e:
        raise _err(e) from None
    if os.path.exists(out):
        raise RunnerError(f'lsfvg: {out} exists; a packet is never overwritten')
    try:
        commit = VC.clean_commit(repo)                   # before a single cache byte is read
    except VC.RunnerError as e:
        raise _err(e) from None
    frames, files, comp, bad = {}, {}, {}, []
    for s in syms:
        try:
            df, rec = B.read_cache(cache, s, TF)
        except B.RunnerError as e:
            raise _err(e) from None
        files[s] = rec
        d = VC.slice_frame(df, warm_lo, hi) if df is not None else None
        comp[s] = B.completeness(d, warm_lo, hi)
        if not comp[s]['complete']:
            bad.append(s)
        frames[s] = d
    if bad:
        raise RunnerError(f'lsfvg: INCOMPLETE cache for {bad} over [{_utc(warm_lo)}, {_utc(hi)}); refused (no fill)')
    try:
        VC.assert_bounded(frames, warm_lo, hi)
    except VC.RunnerError as e:
        raise _err(e) from None
    rows, skips, opens, excluded = [], [], [], {'long': 0, 'short': 0}
    for s in syms:
        d = frames[s]
        t = _ms(d.t)
        lo_i = int(np.searchsorted(t, lo))
        assert lo_i == WARMUP_BARS
        ex = []
        with window_signals(lo_i, ex):
            res = LS.run_symbol(d)
        for g in ex:
            excluded[g['side']] += 1
        for k in res['skips']:
            skips.append(dict(symbol=s, side=k['side'], n=int(k['n']), skip=k['skip'], t_signal=_utc(t[k['n']])))
        for x in res['trades']:
            assert x['n'] >= lo_i
            base = dict(symbol=s, side=x['side'], t_signal=_utc(t[x['n']]), t_in=_utc(t[x['entry_i']]),
                        quarter=quarter(int(t[x['entry_i']])), entry=x['entry'], stop=x['stop'], target=x['target'],
                        R_unit=x['R_unit'])
            if x['R_net'] is None:
                opens.append(dict(base, label='open_end'))
                continue
            row = dict(base, t_out=_utc(t[x['exit_i']]), exit=x['exit'], label=x['label'], gap_exit=x['gap_exit'],
                       R_gross=x['R_gross'], R_net=x['R_net'], fee_R=x['fee_R'], slip_R=x['slip_R'], fund_R=x['fund_R'],
                       hold_bars=int(x['hold_bars']), lt_minus1=bool(x['lt_minus1']), **excursions(d, x))
            rows.append(row)
    key = lambda x: (x['t_in'], x['symbol'], x['side'])
    rows.sort(key=key)
    opens.sort(key=key)
    skips.sort(key=lambda x: (x['t_signal'], x['symbol'], x['side'], x['skip']))
    summ = LS.summarize([dict(x) for x in rows])         # the module's own totals (R_net / R_gross / costs / PF / R<-1)
    quarters = window_quarters(lo, hi)
    groups = {}
    for g, sel in (('long', 'long'), ('short', 'short'), ('combined', None)):
        rs = [x for x in rows if sel is None or x['side'] == sel]
        groups[g] = group_stats(rs, [k for k in skips if sel is None or k['side'] == sel],
                                [o for o in opens if sel is None or o['side'] == sel], quarters,
                                summ['total' if sel is None else sel])
        groups[g]['concentration'] = concentration(rs, syms)
    trades = [{k: (_r(v) if isinstance(v, float) else v) for k, v in x.items()} for x in rows]
    pkt = {
        'format': FORMAT, 'evidence_class': EVIDENCE_CLASS, 'non_promotable': True,
        'purpose': 'C1 development run (development window only; not evidence for promotion)',
        'candidate': LS.CANDIDATE, 'family': LS.FAMILY, 'label': LS.DEV_LABEL,
        'commit': commit, 'tree': 'clean', 'runner_sha256': runner_digest(),
        'window': {'start': _utc(lo), 'end': _utc(hi), 'end_exclusive': True, 'bars': int((hi - lo) // TF_MS), 'tf': TF,
                   'guard': 'pit_run.rex_window', 'holdout_start': HOLDOUT_START, 'quarters': quarters},
        'warmup': {'bars': WARMUP_BARS, 'start': _utc(warm_lo), 'signals_excluded': excluded,
                   'rule': 'warm-up bars feed ATR14 / pivots only; signals with FVG bar n before START are dropped '
                           'before the slot logic (run starts flat at START)'},
        'universe': {'digest': static['digest'], 'id': static.get('universe_id'), 'tier': tier, 'run': syms},
        'cache': {'format': f'app._get_candles CSV <SYMBOL>_{TF}.csv: t(ms),o,h,l,c,v,qv', 'files': files,
                  'completeness': comp, 'incomplete_policy': 'refuse the run'},
        'rules': {'pivot_k': LS.PIVOT_K, 'pivot_lookback': LS.PIVOT_LOOKBACK, 'wick_atr': str(LS.WICK_ATR),
                  'body_atr': str(LS.BODY_ATR), 'stop_atr': str(LS.STOP_ATR), 'cost_guard': str(LS.COST_GUARD),
                  'target_r': LS.TARGET_R, 'fvg_max': LS.FVG_MAX, 'cap_bars': LS.CAP_BARS,
                  'slot': 'one trade per symbol, both sides share it; overlap ignored, same-bar conflict skips both'},
        'costs': {'fee_taker': float(LS.FEE), 'slip': float(LS.SLIP), 'fund_per_bar': float(LS.FUND_PER_BAR)},
        'open_at_end': {'convention': 'backtest.run / run_breadth_candidate: a position still open after the last '
                                      'in-window bar is not a trade (no forced close); excluded from every statistic',
                        'count': len(opens), 'trades': [{k: (_r(v) if isinstance(v, float) else v) for k, v in o.items()}
                                                        for o in opens]},
        'definitions': {
            't_in': 'open time of the entry bar (market fill at its open)',
            't_out': 'open time of the exit bar (open fill, or an intrabar stop / target inside that bar)',
            'quarter': 'calendar quarter (UTC) of t_in', 'net_R': 'gross R - slip R - fee R - funding R',
            'mfe_r': 'best favourable excursion on the path entry -> exit in R (bars [entry, exit) full range + entry, '
                     'exit-bar open and exit price; nothing after the exit)',
            'mae_r': 'worst adverse excursion on the same path, in R', 'giveback_gross_r': 'mfe_r - gross R', 'giveback_net_r': 'mfe_r - net R',
            'max_dd_R': 'max drawdown of cumulative net R in exit order',
            'skips': 'skip records; a conflict writes one record per side (conflict_events = distinct symbol / bar)',
            'concentration': 'per symbol: share of the group trades and of the group net R (None when net R is 0)'},
        'stats': groups,
        'decision': decision(groups['combined'], quarters),
        'skips': skips,
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
    ap.add_argument('--cache', required=True, help='the 4h app cache dir')
    ap.add_argument('--window', required=True, help='START/END, UTC, 4h grid, end-exclusive (no default)')
    g = ap.add_mutually_exclusive_group(required=True)
    g.add_argument('--tier', choices=('CORE-10', 'CORE-20'))
    g.add_argument('--symbols')
    ap.add_argument('--out', required=True)
    a = ap.parse_args(argv)
    try:
        pkt = run(cache=a.cache, window=a.window, out=a.out, tier=a.tier,
                  symbols=a.symbols.split(',') if a.symbols else None)
    except RunnerError as e:
        print(f'REFUSED: {e}', file=sys.stderr)
        return 2
    c = pkt['stats']['combined']
    print(f"{LS.CANDIDATE}: {c['n']} trades, net R {c['net_R']}, PF {c['pf']}, {pkt['decision']['verdict']}, "
          f"packet {pkt['packet_sha256'][:12]} -> {a.out}")
    return 0


if __name__ == '__main__':
    sys.exit(main())
