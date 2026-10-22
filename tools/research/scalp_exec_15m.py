"""SCALP-EXEC-15M-01: the exact runtime `exp_scalp` / `range_scalp` code on execution-grade 1m data (EDGE-01A; contract
ZackBot2 #186 comment 6353256443).

Decisions are the real engine's: `replay.run_replay` drives `engine.Engine` (signals from strategies.py, slot gates,
sizing, manage(), exchange stops, take-profits, signal / time exits) against its simulated exchange on 15m candles. This
module adds no rule, filter or parameter. It only supplies two things the legacy replay approximates:

  * the price path inside a 15m candle that holds a lot: the candle's own 15 x 1m bars in time order, each minute walked
    open -> ADVERSE extreme -> favourable extreme -> close for the side held (stop first; Cowork #186 item 5). Every open
    stop / take-profit level the leg crosses is inserted as its own point, so the engine fills AT the level, never at a
    minute extreme. Minutes after the first open as gap points (a stop they open through fills at that open).
    Labels per (symbol, candle): `BAR` (one level or none), `RES-1M`, `SAME-MINUTE-STOP-FIRST` (one minute spans the stop
    and a target: stop first, both bounds counted), `NO-1M` (1m bars missing / incomplete: 4-point stop-first fallback,
    unresolved), `TWO-SIDED` (long and short held on the coin: colour order). Unresolved candles count toward the
    5 % PARK rule (intrabar.PARK_UNRESOLVED_SHARE). 1m bars that contradict their 15m candle refuse the run.
  * costs: the engine runs with costs off (fills at the reference price) and logs every fill; each fill is then re-priced
    per cost scenario with tools/research/costs.py (`zb-cost-model/2`): taker fees, slip-v1 adverse slippage rounded to
    the tick (stop / gap fills take the gap multiplier), signed actual funding at every funding time the lot spans
    (entry <= T < exit; qty held at T). Every costs.STRESS row plus the EDGE-01 gate row `edge01_x1_5` is reported.
    Limitation (in every packet): per-trade R is exact under each row; the account path (equity-driven sizing) is the
    zero-cost run's.

Research only: `non_promotable` is always true. Nothing here reads the PAPER data dir, settings or credentials, and no
data on or after the sealed cutoff is accepted. Long and short are separate cells (`side`); `both` reproduces the PAPER
slot as it ran and is reported per side, never as a promotion cell.
"""
from __future__ import annotations

import copy
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

import costs as C                                                                           # noqa: E402
import intrabar as IB                                                                       # noqa: E402
import risk_fill as RF                                                                      # noqa: E402
import strategies as ST                                                                     # noqa: E402

FORMAT = 'zb-scalp-exec/1'
KEYS = ('exp_scalp', 'range_scalp')
TF, TF_MS = '15m', 900_000
MINUTE = IB.MINUTE
SEALED_CUTOFF = '2026-09-28T00:00:00Z'               # = run_vwaprev_candidate.SEALED_CUTOFF (pinned by a test)
CUTOFF_MS = int(pd.Timestamp(SEALED_CUTOFF).value // 10 ** 6)
EDGE01_X1_5 = C.Scenario('edge01_x1_5', fee_mult=1.5, slip_mult=1.5)
SCENARIOS = {**C.STRESS, EDGE01_X1_5.name: EDGE01_X1_5}
TWO_SIDED, ONE_LEVEL, RES_1M, SAME_MINUTE, NO_1M = 'TWO-SIDED', IB.ONE_LEVEL, IB.RES_1M, IB.SAME_MINUTE, IB.NO_1M
UNRESOLVED = (SAME_MINUTE, NO_1M)
LIMITATION = 'per-trade R exact under each cost row; account path (equity-driven sizing) from the zero-cost run'


class ScalpExecError(ValueError):
    pass


# ------------------------------------------------------------------ the 1m price path
def lot_levels(lot) -> tuple[list[float], list[float]]:
    """(stops, targets) the engine can fill on this lot inside a candle: its exchange stop, the tp1 level until taken,
    the ladder levels not yet done and the full take-profit (tp_r, unless a runner / trailing TP owns the exit)."""
    sd = 1 if lot['side'] == 'LONG' else -1
    g, e0, R = lot.get('mgmt') or {}, lot.get('e0'), lot.get('R')
    stops = [float(lot['stop'])] if lot.get('stop') else []
    tg = []
    if e0 and R:
        if g.get('tp1_r') and not lot.get('tp1'): tg.append(e0 + sd * g['tp1_r'] * R)
        for k, (r_, _f) in enumerate(ST.norm_tps(g.get('tps')) or []):
            if k not in (lot.get('tps_done') or []): tg.append(e0 + sd * r_ * R)
        if g.get('tp_r') and not g.get('runner') and not g.get('ttp'): tg.append(e0 + sd * g['tp_r'] * R)
    return stops, tg


def _leg(a: float, b: float, levels, t: int) -> list:
    """Points from a (exclusive) to b (inclusive), with every level strictly between them first, in travel order."""
    inside = sorted((x for x in levels if min(a, b) < x < max(a, b)), reverse=b < a)
    return [(x, False, t) for x in inside] + [(b, False, t)]


def _order(o, h, l, c, sides) -> tuple:
    """Extremes order for one bar: adverse first for the single side held; colour order (doji: high first) otherwise."""
    if len(sides) == 1:
        return (l, h) if next(iter(sides)) == 1 else (h, l)
    return (l, h) if c > o else (h, l) if c < o else (h, l)


def _spans(bar, levels) -> bool:
    return any(bar.l <= x <= bar.h for x in levels)


def bar_path(bar, minutes, sides, levels) -> tuple[list, str]:
    """(points after the candle open, label) for one 15m candle. `bar`/`minutes`: objects with open_ms/o/h/l/c;
    `sides`: {+1 / -1 held}; `levels`: (stops, targets)."""
    stops, tg = levels
    allv = list(stops) + list(tg)
    mins = sorted(minutes or (), key=lambda m: m.open_ms)
    want = list(range(bar.open_ms, bar.open_ms + TF_MS, MINUTE))
    if [m.open_ms for m in mins] != want:
        ext = _order(bar.o, bar.h, bar.l, bar.c, sides if len(sides) == 1 else {1})
        t = bar.open_ms + TF_MS // 2
        pts, a = [], bar.o
        for b in (*ext, bar.c):
            pts += _leg(a, b, allv, t); a = b
        both = _spans(bar, stops) and _spans(bar, tg)
        return pts, NO_1M if both else ONE_LEVEL
    lo, hi = min(m.l for m in mins), max(m.h for m in mins)
    if not (math.isclose(mins[0].o, bar.o, rel_tol=1e-9) and math.isclose(mins[-1].c, bar.c, rel_tol=1e-9)
            and math.isclose(lo, bar.l, rel_tol=1e-9) and math.isclose(hi, bar.h, rel_tol=1e-9)):
        raise ScalpExecError(f'1m bars do not reproduce the 15m candle at {bar.open_ms} (inconsistent data)')
    pts, a = [], bar.o
    for k, m in enumerate(mins):
        if k:                                       # a minute open is a gap point: a stop it opens through fills there
            pts.append((m.o, True, m.open_ms)); a = m.o
        t = m.open_ms + MINUTE // 2
        for b in (*_order(m.o, m.h, m.l, m.c, sides), m.c):
            pts += _leg(a, b, allv, t); a = b
    if len(sides) > 1:
        return pts, TWO_SIDED
    if any(_spans(m, stops) and _spans(m, tg) for m in mins):
        return pts, SAME_MINUTE
    return pts, RES_1M if _spans(bar, stops) and _spans(bar, tg) else ONE_LEVEL


class Bar(tuple):
    __slots__ = ()
    open_ms = property(lambda s: s[0]); o = property(lambda s: s[1]); h = property(lambda s: s[2])
    l = property(lambda s: s[3]); c = property(lambda s: s[4])
    high, low, close = h, l, c                      # costs.py estimator names


def bars_of(df) -> list:
    t = (pd.to_datetime(df.t).astype('datetime64[ns]').astype('int64') // 10 ** 6).to_numpy()
    return [Bar((int(a), float(o), float(h), float(l), float(c))) for a, o, h, l, c in zip(t, df.o, df.h, df.l, df.c)]


def make_path_fn(raw15: dict, minutes: dict, labels: list):
    """run_replay path hook: the 1m path for every symbol holding a lot; `labels` collects (symbol, open_ms, label)."""
    B15 = {s: bars_of(df.reset_index(drop=True)) for s, df in raw15.items()}
    M = {s: {} for s in raw15}
    for s, df in (minutes or {}).items():
        for b in bars_of(df):
            M.setdefault(s, {}).setdefault(b.open_ms - b.open_ms % TF_MS, []).append(b)

    def fn(j, held, lots):
        out = {}
        for s, sides in held.items():
            bar = B15[s][j]
            st, tg = [], []
            for lot in lots.values():
                if lot['symbol'] == s:
                    a, b = lot_levels(lot); st += a; tg += b
            pts, label = bar_path(bar, M.get(s, {}).get(bar.open_ms), sides, (st, tg))
            out[s] = pts
            labels.append((s, bar.open_ms, label))
        return out
    return fn


# ------------------------------------------------------------------ guards
def check_inputs(raw15: dict, minutes: dict, funding: dict) -> dict:
    """Sealed cutoff (fail closed) and 15m completeness. Returns {symbol: reason} for INCOMPLETE symbols (excluded)."""
    def last_ms(df):
        return int(pd.to_datetime(df.t).max().value // 10 ** 6) if len(df) else 0
    for what, d, span in (('15m', raw15, TF_MS), ('1m', minutes or {}, MINUTE)):
        for s, df in d.items():
            if len(df) and last_ms(df) + span > CUTOFF_MS:
                raise ScalpExecError(f'{what} {s}: data at or after the sealed cutoff {SEALED_CUTOFF} (holdout stays closed)')
    for s, rows in (funding or {}).items():
        if any(t >= CUTOFF_MS for t, _r, _m in rows):
            raise ScalpExecError(f'funding {s}: data at or after the sealed cutoff {SEALED_CUTOFF}')
    bad = {}
    ts = None
    for s, df in raw15.items():
        t = pd.to_datetime(df.t).reset_index(drop=True)
        if len(t) < 2 or (t.diff().dropna() != pd.Timedelta(milliseconds=TF_MS)).any():
            bad[s] = 'missing 15m candle(s)'
        elif ts is not None and not t.equals(ts):
            bad[s] = '15m timestamps differ from the run grid'
        else:
            ts = t if ts is None else ts
    return bad


def check_sleeves(sleeves) -> None:
    for sl in sleeves:
        if sl.get('key') not in KEYS:
            raise ScalpExecError(f"sleeve {sl.get('id')}: {sl.get('key')!r} is not one of {KEYS}")
        if sl.get('tf') != TF:
            raise ScalpExecError(f"sleeve {sl.get('id')}: timeframe must be {TF}")
        if (sl.get('mgmt') or {}).get('resting_tp'):
            raise ScalpExecError(f"sleeve {sl.get('id')}: resting exchange TP is not modelled in v1 (fail closed)")


CODE_FILES = ('engine.py', 'strategies.py', 'replay.py', 'risk_fill.py', 'feasibility.py', 'tools/research/costs.py',
              'tools/research/intrabar.py', 'tools/research/scalp_exec_15m.py')


def code_ids() -> dict:
    """Git blob ids of the code that made the decisions and the prices (A1: the packet names the exact engine)."""
    out = {}
    for f in CODE_FILES:
        with open(os.path.join(ROOT, f), 'rb') as fh:
            b = fh.read().replace(b'\r\n', b'\n')
        out[f] = hashlib.sha1(b'blob %d\x00' % len(b) + b).hexdigest()
    return out


def config_digest(sleeves) -> str:
    return hashlib.sha256(json.dumps(sleeves, sort_keys=True, separators=(',', ':')).encode()).hexdigest()


# ------------------------------------------------------------------ trades from the fill log
def trades_of(fill_log, history) -> list[dict]:
    """Group the simulated fills into trades per (symbol, side) and attach the engine's risk_usd (history, in close order)."""
    open_, done = {}, []
    for f in fill_log:
        k = (f['sym'], f['ps'])
        if f['act'] == 'stop_set':                  # the first stop placed after the entry = the planned stop
            if k in open_ and 'stop0' not in open_[k]: open_[k]['stop0'] = f['ref']
            continue
        if f['act'] == 'open':
            if k in open_ and open_[k]['qty'] > 1e-12:
                raise ScalpExecError(f'{k}: an add / second entry on an open lot is not modelled in v1')
            open_[k] = dict(sym=f['sym'], ps=f['ps'], entry=f, closes=[], qty=f['qty'])
        else:
            tr = open_.get(k)
            if tr is None:
                raise ScalpExecError(f'{k}: close without an open lot in the fill log')
            tr['closes'].append(f); tr['qty'] -= f['qty']
            if tr['qty'] <= 1e-9 * max(1.0, tr['entry']['qty']):
                done.append(open_.pop(k))
    hist = {}
    for h in history:
        hist.setdefault((h['symbol'], h['side']), []).append(h)
    for k in hist:
        if len(hist[k]) != sum((t['sym'], t['ps']) == k for t in done):
            raise ScalpExecError(f'{k}: engine history and fill log disagree on the number of closed trades')
    pos = {}
    for t in done:
        k = (t['sym'], t['ps']); n = pos.get(k, 0); pos[k] = n + 1
        h = hist[k][n]
        t['risk_usd'], t['exit_reason'], t['engine_gross'] = h.get('risk_usd'), h.get('exit_reason'), h.get('pnl_gross')
    return done


def funding_events(rows, t_from: int, t_to: int):
    """Funding rows (time_ms, rate, mark) with t_from <= T < t_to."""
    return [r for r in rows if t_from <= r[0] < t_to]


def price_trade(tr, scen: C.Scenario, *, row: C.CostRow, slip_c: float, bars15, tick=None, funding_rows=None,
                ) -> dict:
    """Net P&L of one trade under one scenario. bars15: the symbol's 15m Bar list (slip-v1 reads the 15 bars closed
    before each fill). funding_rows None = no funding data: the trade is INCOMPLETE under every actual-funding row."""
    sd = 1 if tr['ps'] == 'LONG' else -1
    side = 'long' if sd == 1 else 'short'
    opens = [b.open_ms for b in bars15]

    def bps_at(t_ms):
        k = int(np.searchsorted(opens, t_ms - TF_MS, side='right'))      # candles closed at or before t_ms
        win = bars15[max(0, k - C.SLIP_BARS):k]
        if len(win) < C.SLIP_BARS:
            raise ScalpExecError(f"{tr['sym']}: fewer than {C.SLIP_BARS} closed candles before a fill (warm-up)")
        return C.slip_bps(win, slip_c, scen.vol)

    e = tr['entry']
    e_px = C.fill_price(e['ref'], 'buy' if sd == 1 else 'sell', bps_at(e['t_ms']), scen, tick=tick)
    fees = C.fee(e['qty'] * e_px, 'taker', row, scen)
    gross = gross_ref = 0.0
    for f in tr['closes']:
        px = C.fill_price(f['ref'], 'sell' if sd == 1 else 'buy', bps_at(f['t_ms']), scen, gap=f['kind'] in ('stop', 'gap'),
                          tick=tick)
        gross += sd * (px - e_px) * f['qty']
        gross_ref += sd * (f['ref'] - e['ref']) * f['qty']
        fees += C.fee(f['qty'] * px, 'taker', row, scen)
    t_exit = tr['closes'][-1]['t_ms']
    complete = True
    if scen.funding_mode == 'flat_legacy':
        n_bars = max(1, math.ceil((t_exit - e['t_ms']) / TF_MS))
        fund = C.funding_cost(side, e['qty'], [], scen, bars_held=n_bars, entry_notional=e['qty'] * e_px)
    elif funding_rows is None:
        fund, complete = 0.0, False
    else:
        fund = 0.0
        for ev in funding_events(funding_rows, e['t_ms'], t_exit):
            q = e['qty'] - sum(f['qty'] for f in tr['closes'] if f['t_ms'] <= ev[0])
            if q > 1e-12:
                fund += C.funding_cost(side, q, [ev], scen)
    net = gross - fees - fund
    rf = (RF.decide(tr['ps'], e['qty'], e_px, tr['stop0'], e['ref'], entry_fees=C.fee(e['qty'] * e_px, 'taker', row, scen))['action']
          if tr.get('stop0') else 'unmeasured')        # what RISK-FILL-01 WOULD do on this priced entry (A1; never applied)
    risk = tr.get('risk_usd')
    r = (lambda v: v / risk) if risk else (lambda v: None)
    return dict(net=net, gross=gross, gross_ref=gross_ref, fees=fees, funding=fund, net_r=r(net), gross_r=r(gross_ref),
                costs_r=r(gross_ref - net), complete=complete, risk_fill_would=rf)


# ------------------------------------------------------------------ one cell
def run_cell(raw15: dict, minutes: dict, sleeves, t0: int, *, model: C.CostModel, funding=None, exchange_rules=None,
             start=2000.0, scenarios=None, latency_s=0) -> dict:
    """One (strategy, side) cell: the engine on the 1m path with costs off, then every scenario re-priced per trade."""
    import replay as RP
    if latency_s:
        raise ScalpExecError('latency_s > 0 is not implemented in v1 (fills at the next 15m open)')
    check_sleeves(sleeves)
    bad = check_inputs(raw15, minutes, funding)
    run = {s: df for s, df in raw15.items() if s not in bad}
    if not run:
        raise ScalpExecError('no complete symbol to run')
    labels, fills = [], []
    out = RP.run_replay(copy.deepcopy(run), copy.deepcopy(sleeves), t0, tf_sec=TF_MS // 1000, start=start,
                        exchange_rules=exchange_rules, path_fn=make_path_fn(run, minutes, labels), costs_off=True,
                        fill_log=fills, compare=False)
    trades = trades_of(fills, out['history'])
    B15 = {s: bars_of(df.reset_index(drop=True)) for s, df in run.items()}
    rules = exchange_rules or {}
    scen = scenarios or SCENARIOS
    rows = []
    for tr in trades:
        s = tr['sym']
        rec = dict(sym=s, side=tr['ps'], entry_ms=tr['entry']['t_ms'], exit_ms=tr['closes'][-1]['t_ms'],
                   exit_kinds=[f['kind'] for f in tr['closes']], exit_reason=tr['exit_reason'], risk_usd=tr['risk_usd'],
                   rows={})
        for name, sc in scen.items():
            rec['rows'][name] = price_trade(tr, sc, row=model.row(s), slip_c=model.slip_c(s), bars15=B15[s],
                                            tick=(rules.get(s) or {}).get('tick'), funding_rows=(funding or {}).get(s))
        rows.append(rec)
    lab = [x[2] for x in labels]
    held_bars = len(lab)
    unres = sum(x in UNRESOLVED for x in lab)
    incomplete = bool(bad) or any(not r['rows'][n]['complete'] for r in rows for n in r['rows']
                                  if scen[n].funding_mode == 'actual')
    return dict(format=FORMAT, non_promotable=True, limitation=LIMITATION, config_digest=config_digest(sleeves),
                cost_model_digest=model.digest, labels=sorted({l for s in run for l in model.labels(s)}),
                excluded_symbols=bad, status='INCOMPLETE' if incomplete else 'COMPLETE', trades=rows,
                intrabar=dict(held_bars=held_bars, by_label={k: lab.count(k) for k in (ONE_LEVEL, RES_1M, SAME_MINUTE, NO_1M, TWO_SIDED)},
                              unresolved=unres, unresolved_share=unres / len(rows) if rows else 0.0,
                              park=bool(rows) and unres / len(rows) > IB.PARK_UNRESOLVED_SHARE),
                refused=len([m for m in out['missed'] if m.get('kind') != 'deferred']), open_lots=len(out['lots']),
                code=code_ids(),
                risk_fill_would={n: {a: sum(r['rows'][n]['risk_fill_would'] == a for r in rows) for a in RF.ACTIONS}
                                 for n in scen})

