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
import trade_audit as TA                                                                    # noqa: E402

FORMAT = 'zb-scalp-exec/1'
KEYS = ('exp_scalp', 'range_scalp')
TF, TF_MS = '15m', 900_000
MINUTE = IB.MINUTE
SEALED_CUTOFF = '2026-09-28T00:00:00Z'               # = run_vwaprev_candidate.SEALED_CUTOFF (pinned by a test)
CUTOFF_MS = int(pd.Timestamp(SEALED_CUTOFF).value // 10 ** 6)
EDGE01_X1_5 = C.Scenario('edge01_x1_5', fee_mult=1.5, slip_mult=1.5)
SCENARIOS = {**C.STRESS, EDGE01_X1_5.name: EDGE01_X1_5}
TWO_SIDED, ONE_LEVEL, RES_1M, SAME_MINUTE, NO_1M = 'TWO-SIDED', IB.ONE_LEVEL, IB.RES_1M, IB.SAME_MINUTE, IB.NO_1M
NO_LATENCY = 'NO-1M-NO-LATENCY'                     # latency row: a candle without 1m data decides at its open
UNRESOLVED = (SAME_MINUTE, NO_1M)
LIMITATION = ('repriced rows: per-trade R exact under each cost row, account path (sizing) from the zero-cost run - '
              'diagnostic only; account rows: costs applied at fill time (Codex ruling 00ba8d0)')
ACCOUNT_ROWS = ('base', 'edge01_x1_5')
LATENCIES = (0, 60)                                  # primary and the single preregistered stress row
CONFIG_SHA256 = '3027cb8a048fcaae89154fb842fdadc5ccdf66e68f9b50d13afb7de0919320de'
MANIFEST_DIGEST = '54912d9d2bf6e6fc45bc75c0553f0bd867e877972beeb3452785937379baf1e7'
WARM_BARS = 1000                                     # the engine reads 1000 x 15m candles per cycle
# Codex ruling 6356220526 pt 4: 1,000 served candles is accepted because each key's longest indicator window is < 200
# (exp_scalp: EMA50; range_scalp: 32-candle range + 32-candle invalidation lookback; ATR / ADX 14). Pinned per packet.
MAX_WINDOW = {'exp_scalp': 50, 'range_scalp': 64}
DEPTH_LIMIT = 200
COMPARABLE_MATCH_PCT = 95.0                          # ruling 6356220526 pt 2 (registered before any real-data packet)
COMPARABLE_DR_SHARE = 0.25                           # median |dR| <= 25 % of the cell's median |cost-R|


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


def complete_minutes(bar, minutes) -> list | None:
    """The candle's 15 x 1m bars in time order, or None when any is missing; refuses 1m bars that contradict the candle."""
    mins = sorted(minutes or (), key=lambda m: m.open_ms)
    if [m.open_ms for m in mins] != list(range(bar.open_ms, bar.open_ms + TF_MS, MINUTE)):
        return None
    lo, hi = min(m.l for m in mins), max(m.h for m in mins)
    if not (math.isclose(mins[0].o, bar.o, rel_tol=1e-9) and math.isclose(mins[-1].c, bar.c, rel_tol=1e-9)
            and math.isclose(lo, bar.l, rel_tol=1e-9) and math.isclose(hi, bar.h, rel_tol=1e-9)):
        raise ScalpExecError(f'1m bars do not reproduce the 15m candle at {bar.open_ms} (inconsistent data)')
    return mins


def bar_path(bar, minutes, sides, levels, lo=0, hi=15) -> tuple[list, str]:
    """(points, label) for minutes [lo, hi) of one 15m candle, starting from minute lo's open (the candle open when lo=0,
    already walked). `bar`/`minutes`: objects with open_ms/o/h/l/c; `sides`: {+1 / -1 held}; `levels`: (stops,
    targets). Missing 1m data: the whole candle as the 4-point stop-first path (lo must then be 0)."""
    stops, tg = levels
    allv = list(stops) + list(tg)
    mins = complete_minutes(bar, minutes)
    if mins is None:
        if lo:
            raise ScalpExecError('a partial-candle walk needs complete 1m data')
        ext = _order(bar.o, bar.h, bar.l, bar.c, sides if len(sides) == 1 else {1})
        t = bar.open_ms + TF_MS // 2
        pts, a = [], bar.o
        for b in (*ext, bar.c):
            pts += _leg(a, b, allv, t); a = b
        both = _spans(bar, stops) and _spans(bar, tg)
        return pts, NO_1M if both else ONE_LEVEL
    seg = mins[lo:hi]
    pts, a = [], seg[0].o if seg else bar.o
    for k, m in enumerate(seg):
        if k:                                       # a minute open is a gap point: a stop it opens through fills there
            pts.append((m.o, True, m.open_ms)); a = m.o
        t = m.open_ms + MINUTE // 2
        for b in (*_order(m.o, m.h, m.l, m.c, sides), m.c):
            pts += _leg(a, b, allv, t); a = b
    if len(sides) > 1:
        return pts, TWO_SIDED
    if any(_spans(m, stops) and _spans(m, tg) for m in seg):
        return pts, SAME_MINUTE
    lo_, hi_ = (min(m.l for m in seg), max(m.h for m in seg)) if seg else (bar.o, bar.o)
    span = type('s', (), dict(l=lo_, h=hi_))
    return pts, RES_1M if _spans(span, stops) and _spans(span, tg) else ONE_LEVEL


class Bar(tuple):
    __slots__ = ()
    open_ms = property(lambda s: s[0]); o = property(lambda s: s[1]); h = property(lambda s: s[2])
    l = property(lambda s: s[3]); c = property(lambda s: s[4])
    high, low, close = h, l, c                      # costs.py estimator names


def bars_of(df) -> list:
    t = (pd.to_datetime(df.t).astype('datetime64[ns]').astype('int64') // 10 ** 6).to_numpy()
    return [Bar((int(a), float(o), float(h), float(l), float(c))) for a, o, h, l, c in zip(t, df.o, df.h, df.l, df.c)]


class Minutes:
    """One symbol's 1m bars as arrays; `of(open_ms)` = the Bars inside the 15m candle opening then (any count)."""
    def __init__(self, df):
        self.t = (pd.to_datetime(df.t).astype('datetime64[ns]').astype('int64') // 10 ** 6).to_numpy() \
            if not isinstance(df, dict) else np.asarray(df['t'], dtype='int64')
        self.v = [np.asarray(df[k], dtype=float) for k in ('o', 'h', 'l', 'c')]

    def of(self, open_ms: int) -> list:
        a, b = np.searchsorted(self.t, open_ms), np.searchsorted(self.t, open_ms + TF_MS)
        o, h, l, c = self.v
        return [Bar((int(self.t[k]), float(o[k]), float(h[k]), float(l[k]), float(c[k]))) for k in range(a, b)]


def _levels_of(lots, s):
    st, tg = [], []
    for lot in lots.values():
        if lot['symbol'] == s:
            a, b = lot_levels(lot); st += a; tg += b
    return st, tg


def make_path_fn(raw15: dict, minutes: dict, labels: list, latency_s: int = 0):
    """run_replay hooks for the 1m path: (path_fn, pre_fn). `labels` collects (symbol, open_ms, label) per walked
    segment. latency_s (a whole number of minutes, < 15): the engine's cycle runs at minute L = latency_s / 60's open;
    pre_fn walks minutes [0, L) for the symbols already holding a lot and moves every symbol to minute L's open; pre_fn
    is None at zero latency. A candle without complete 1m data keeps the decision at its open (no latency possible),
    labelled NO-1M-NO-LATENCY. Known approximations (Cowork #187): the engine stamps a lot opened at previous close
    + 15 s even at 60 s latency (hold-time exits shift by ~45 s); funding counts an entry exactly at T (a long pays, a
    short receives - the costs.py convention)."""
    if latency_s % 60 or not 0 <= latency_s < TF_MS // 1000:
        raise ScalpExecError('latency_s must be a whole number of minutes below 15')
    L = latency_s // 60
    B15 = {s: bars_of(df.reset_index(drop=True)) for s, df in raw15.items()}
    M = {s: Minutes(df) for s, df in (minutes or {}).items()}
    delayed = set()

    def mins_of(s, bar):
        return complete_minutes(bar, M[s].of(bar.open_ms)) if s in M else None

    def pre(j, held, lots):
        out = {}
        for s in raw15:
            bar = B15[s][j]
            mins = mins_of(s, bar)
            if mins is None:                        # no 1m data: this symbol decides at the candle open, labelled
                labels.append((s, bar.open_ms, NO_LATENCY))
                continue
            delayed.add((s, j))
            if s in held:
                pts, label = bar_path(bar, mins, held[s], _levels_of(lots, s), 0, L)
                labels.append((s, bar.open_ms, label))
            else:
                pts = []
            out[s] = pts + [(mins[L].o, True, mins[L].open_ms)]
        return out

    def fn(j, held, lots):
        out = {}
        for s, sides in held.items():
            bar = B15[s][j]
            lo = L if (s, j) in delayed else 0
            mins = mins_of(s, bar) if lo else (M[s].of(bar.open_ms) if s in M else None)
            pts, label = bar_path(bar, mins, sides, _levels_of(lots, s), lo)
            out[s] = pts
            labels.append((s, bar.open_ms, label))
        return out
    return fn, (pre if L else None)


# ------------------------------------------------------------------ guards
def check_inputs(raw15: dict, minutes: dict, funding: dict) -> dict:
    """Sealed cutoff (fail closed) and 15m completeness. Returns {symbol: reason} for INCOMPLETE symbols (excluded)."""
    def last_ms(df):                                # a DataFrame, or the loader's {t: int ms array, ...}
        if isinstance(df, dict):
            return int(np.max(df['t'])) if len(df['t']) else None
        return int(pd.to_datetime(df.t).max().value // 10 ** 6) if len(df) else None
    for what, d, span in (('15m', raw15, TF_MS), ('1m', minutes or {}, MINUTE)):
        for s, df in d.items():
            last = last_ms(df)
            if last is not None and last + span > CUTOFF_MS:
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
def _qeq(a, b) -> bool:
    return abs(a - b) <= 1e-9 * max(1.0, abs(a), abs(b))


def _legs_match(t, h) -> bool:
    """The trade's legs are the engine lot's own booked fills (history 'fills' [t, kind, qty, px]) in their causal
    order: the same interleaved sequence of entry / close legs with the same quantities (Codex 6373065821 - an add
    and a partial close in the other order is another lot history). A row without fills is not checkable."""
    fl = h.get('fills')
    if not isinstance(fl, list) or not fl: return False
    try:
        seq = [(f[1] in TA.ENTRY_KINDS, float(f[2])) for f in fl]
    except (TypeError, ValueError, IndexError):
        return False
    return len(seq) == len(t['seq']) and all(a == b and _qeq(x, y) for (a, x), (b, y) in zip(seq, t['seq']))


NA_ADD = 'not_applicable_add'          # repriced risk_fill_would of an add lot: read the cost-applied account path


def _rf_legs(t, h):
    """The engine's RISK-FILL-01 action per entry leg of the trade, in causal order (None = no decision recorded), and
    the row's status when the map is a string ('pending' / 'unavailable'). The history map is keyed by the lot's fill
    index ({'0': {'action': ...}, '2': {...}}); a decision on a fill that is not one of the trade's entry legs fails
    closed - no action is dropped or collapsed (Codex 6373243304)."""
    rf, n = h.get('risk_fill'), len(t['entries'])
    if not isinstance(rf, dict):
        return [None] * n, rf if isinstance(rf, str) else None
    fl = h.get('fills')
    idx = ([i for i, f in enumerate(fl) if f[1] in TA.ENTRY_KINDS] if isinstance(fl, list) and fl else
           ([0] if n == 1 else None))
    if idx is None or len(idx) != n:
        raise ScalpExecError(f"{(t['sym'], t['ps'])}: risk_fill decisions cannot be mapped to the entry legs")
    extra = set(rf) - {str(i) for i in idx}
    if extra:
        raise ScalpExecError(f"{(t['sym'], t['ps'])}: a risk_fill decision on a fill that is not an entry leg ({sorted(extra)})")
    return [(rf.get(str(i)) or {}).get('action') if isinstance(rf.get(str(i)), dict) else None for i in idx], None


def trades_of(fill_log, history, exchange_rules=None) -> list[dict]:
    """Group the simulated fills into one trade per engine lot per (symbol, side) and attach the engine's risk_usd
    (history, in close order). #187 (Codex 6372669079): an add / second entry on an open lot joins that lot's trade as
    a further entry leg (entries, causal order; entry = the first leg, which anchors the stop and the times), so no
    lot is dropped or split. Fails closed on a close without an open lot, an over-close, a non-positive quantity, a
    trade whose legs are not its history row's own booked fills (ambiguous lot identity / row order) and an add lot
    without an authoritative risk_usd."""
    import replay as RP
    open_, done, carry, recent = {}, [], {}, {}
    for f in fill_log:
        k = (f['sym'], f['ps'])
        if f['act'] != 'stop_set':
            recent.setdefault(k, []).append((f['act'], f['qty'], f.get('t_ms')))
            recent[k] = recent[k][-8:]
        if f['act'] == 'stop_set':                  # the first stop placed after the entry = the planned stop
            if k in open_ and 'stop0' not in open_[k]: open_[k]['stop0'] = f['ref']
            continue
        if not f['qty'] > 0:
            raise ScalpExecError(f"{k}: a fill with a non-positive quantity ({f['qty']})")
        if f['act'] == 'open':
            tr = open_.get(k)
            if tr is None:
                open_[k] = dict(sym=f['sym'], ps=f['ps'], entry=f, entries=[f], closes=[], qty=f['qty'],
                                seq=[(True, f['qty'])])
            else:                                   # an add on the open lot: one more entry leg of the same trade
                tr['entries'].append(f); tr['qty'] += f['qty']; tr['seq'].append((True, f['qty']))
        else:
            tr = open_.get(k)
            if tr is None:
                raise ScalpExecError(f'{k}: close without an open lot in the fill log')
            entered = sum(x['qty'] for x in tr['entries'])
            eps = 1e-12 * max(1.0, entered)
            tol = RP.qty_flat_tolerance(k[0], exchange_rules)
            qty = f['qty']
            if qty > tr['qty'] + eps:
                excess = qty - tr['qty']
                if excess > carry.get(k, 0.0) + eps:
                    raise ScalpExecError(f'{k}: the closes exceed the quantity entered on the lot '
                                         f'(residual={tr["qty"]:.17g}, carry={carry.get(k, 0.0):.17g}, '
                                         f'flat_tolerance={tol:.17g}, '
                                         f'recent={recent[k]})')
                qty = tr['qty']
                carry[k] = max(0.0, carry.get(k, 0.0) - excess)
            close = dict(f, qty=qty)
            tr['closes'].append(close); tr['qty'] -= qty; tr['seq'].append((False, qty))
            if tr['qty'] < -eps:
                raise ScalpExecError(f'{k}: the closes exceed the quantity entered on the lot '
                                     f'(residual={tr["qty"]:.17g}, carry={carry.get(k, 0.0):.17g}, '
                                     f'flat_tolerance={tol:.17g}, '
                                     f'recent={recent[k]})')
            if tr['qty'] <= tol:
                dust = max(0.0, tr['qty'])
                if dust:
                    tr['closes'][-1]['qty'] += dust
                    tr['seq'][-1] = (False, tr['seq'][-1][1] + dust)
                    carry[k] = carry.get(k, 0.0) + dust
                elif qty == f['qty']:
                    carry.pop(k, None)              # the next lot closed exactly: replay discarded the old dust
                tr['qty'] = 0.0
                done.append(open_.pop(k))
    hist = {}
    for h in history:
        hist.setdefault((h['symbol'], h['side']), []).append(h)
    for k in set(hist) | {(t['sym'], t['ps']) for t in done}:
        if len(hist.get(k, ())) != sum((t['sym'], t['ps']) == k for t in done):
            raise ScalpExecError(f'{k}: engine history and fill log disagree on the number of closed trades')
    pos = {}
    for t in done:
        k = (t['sym'], t['ps']); n = pos.get(k, 0); pos[k] = n + 1
        h = hist[k][n]
        adds = len(t['entries']) - 1
        if adds and not _legs_match(t, h):
            raise ScalpExecError(f'{k}: an add lot whose legs are not its history row\'s booked fills (ambiguous lot)')
        if adds and not h.get('risk_usd'):
            raise ScalpExecError(f'{k}: an add lot without an authoritative risk_usd')
        t['adds'], t['add_qty'] = adds, sum(x['qty'] for x in t['entries'][1:])
        t['qty_in'] = sum(x['qty'] for x in t['entries'])
        t['risk_usd'], t['exit_reason'], t['engine_gross'] = h.get('risk_usd'), h.get('exit_reason'), h.get('pnl_gross')
        t['risk_fill'], t['risk_fill_status'] = _rf_legs(t, h)      # one action per entry leg, causal order
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
    ents = tr.get('entries') or [e]                 # #187: every entry leg (the first is `entry`)
    legs = [(x, C.fill_price(x['ref'], 'buy' if sd == 1 else 'sell', bps_at(x['t_ms']), scen, tick=tick)) for x in ents]
    e_px = legs[0][1]
    fees = math.fsum(C.fee(x['qty'] * px, 'taker', row, scen) for x, px in legs)
    gross = -sd * math.fsum(px * x['qty'] for x, px in legs)       # net cash of a fully closed lot (any leg order)
    gross_ref = -sd * math.fsum(x['ref'] * x['qty'] for x in ents)
    for f in tr['closes']:
        px = C.fill_price(f['ref'], 'sell' if sd == 1 else 'buy', bps_at(f['t_ms']), scen, gap=f['kind'] in ('stop', 'gap'),
                          tick=tick)
        gross += sd * px * f['qty']
        gross_ref += sd * f['ref'] * f['qty']
        fees += C.fee(f['qty'] * px, 'taker', row, scen)
    t_exit = tr['closes'][-1]['t_ms']
    complete = True
    if scen.funding_mode == 'flat_legacy':
        n_bars = max(1, math.ceil((t_exit - e['t_ms']) / TF_MS))
        fund = C.funding_cost(side, sum(x['qty'] for x in ents), [], scen, bars_held=n_bars,
                              entry_notional=math.fsum(x['qty'] * px for x, px in legs))
    elif funding_rows is None:
        fund, complete = 0.0, False
    else:
        fund = 0.0
        for ev in funding_events(funding_rows, e['t_ms'], t_exit):
            q = (sum(x['qty'] for x in ents if x['t_ms'] <= ev[0])
                 - sum(f['qty'] for f in tr['closes'] if f['t_ms'] <= ev[0]))
            if q > 1e-12:
                fund += C.funding_cost(side, q, [ev], scen)
    net = gross - fees - fund
    rf = (NA_ADD if len(ents) > 1 else               # an add lot: never the first leg alone - see the account path
          RF.decide(tr['ps'], e['qty'], e_px, tr['stop0'], e['ref'], entry_fees=C.fee(e['qty'] * e_px, 'taker', row, scen))['action']
          if tr.get('stop0') else 'unmeasured')        # what RISK-FILL-01 WOULD do on this priced entry (A1; never applied)
    risk = tr.get('risk_usd')
    r = (lambda v: v / risk) if risk else (lambda v: None)
    return dict(net=net, gross=gross, gross_ref=gross_ref, fees=fees, funding=fund, net_r=r(net), gross_r=r(gross_ref),
                costs_r=r(gross_ref - net), complete=complete, risk_fill_would=rf)


# ------------------------------------------------------------------ cost-applied account path (Codex ruling 00ba8d0)
class Pricer:
    """run_replay fill_px / fund_fn for one cost scenario: every fill priced with costs.py at fill time (the engine sees
    that price), funding charged on the exchange lots at each actual funding time. Funding paid per (symbol, side) is
    kept in `paid` for per-trade attribution; a symbol without funding rows marks the run incomplete."""
    def __init__(self, scen: C.Scenario, model: C.CostModel, raw15: dict, *, funding=None, rules=None):
        self.scen, self.model, self.funding, self.rules = scen, model, funding or {}, rules or {}
        self.B15 = {s: bars_of(df.reset_index(drop=True)) for s, df in raw15.items()}
        self.opens = {s: [b.open_ms for b in v] for s, v in self.B15.items()}
        self.paid, self.incomplete = [], set()

    def bps(self, s, t_ms):
        k = int(np.searchsorted(self.opens[s], t_ms - TF_MS, side='right'))
        win = self.B15[s][max(0, k - C.SLIP_BARS):k]
        if len(win) < C.SLIP_BARS:
            raise ScalpExecError(f'{s}: fewer than {C.SLIP_BARS} closed candles before a fill (warm-up)')
        return C.slip_bps(win, self.model.slip_c(s), self.scen.vol)

    def __call__(self, f):
        s, buy = f['sym'], (f['act'] == 'open') == (f['ps'] == 'LONG')
        px = C.fill_price(f['ref'], 'buy' if buy else 'sell', self.bps(s, f['t_ms']), self.scen,
                          gap=f['kind'] in ('stop', 'gap'), tick=(self.rules.get(s) or {}).get('tick'))
        return px, C.fee(f['qty'] * px, 'taker', self.model.row(s), self.scen)

    def fund(self, t0, t1, lots):
        tot = 0.0
        for l in lots:
            rows = self.funding.get(l['s'])
            if rows is None:
                self.incomplete.add(l['s']); continue
            for ev in funding_events(rows, t0, t1):
                x = C.funding_cost('long' if l['ps'] == 'LONG' else 'short', l['q'], [ev], self.scen)
                self.paid.append((ev[0], l['s'], l['ps'], x)); tot += x
        return tot


def account_trades(fills, history, paid, exchange_rules=None) -> list[dict]:
    """Per-trade net from the priced fills (fill price, fee) plus the funding paid while the lot was open."""
    out = []
    for tr in trades_of(fills, history, exchange_rules):
        sd = 1 if tr['ps'] == 'LONG' else -1
        e, ents = tr['entry'], tr.get('entries') or [tr['entry']]
        gross = sd * (math.fsum(f['px'] * f['qty'] for f in tr['closes']) - math.fsum(x['px'] * x['qty'] for x in ents))
        fees = math.fsum(x['fee'] for x in ents) + math.fsum(f['fee'] for f in tr['closes'])
        t1 = tr['closes'][-1]['t_ms']
        fund = math.fsum(x for t, s, ps, x in paid if s == tr['sym'] and ps == tr['ps'] and e['t_ms'] <= t < t1)
        net = gross - fees - fund
        risk = tr.get('risk_usd')
        out.append(dict(sym=tr['sym'], side=tr['ps'], entry_ms=e['t_ms'], exit_ms=t1, qty=tr.get('qty_in', e['qty']),
                        adds=tr.get('adds', 0), add_qty=tr.get('add_qty', 0.0),
                        exit_kinds=[f['kind'] for f in tr['closes']], exit_reason=tr['exit_reason'], risk_usd=risk,
                        risk_fill=tr.get('risk_fill'), risk_fill_status=tr.get('risk_fill_status'), net=net, fees=fees, funding=fund,
                        net_r=net / risk if risk else None))
    return out


def add_stats(trades, open_lots=None) -> dict:
    """#187 provenance: how many CLOSED trades carry adds, how many add legs and how much add quantity, plus the engine
    lots still open at the end that carry adds (not priced, counted so the sample stays auditable)."""
    ol = list((open_lots or {}).values()) if isinstance(open_lots, dict) else list(open_lots or ())
    oa = [l for l in ol if sum(1 for f in l.get('fills') or () if f[1] in TA.ENTRY_KINDS) > 1]
    return dict(scope='closed_trades', trades=len(trades), trades_with_adds=sum(1 for t in trades if t.get('adds')),
                add_legs=sum(int(t.get('adds') or 0) for t in trades), add_qty=math.fsum(t.get('add_qty') or 0.0 for t in trades),
                open_lots=len(ol), open_lots_with_adds=len(oa))


def rf_counts(trades) -> dict:
    """Engine RISK-FILL-01 actions counted per ENTRY LEG (an add lot contributes one per leg)."""
    legs = [a for t in trades for a in t.get('risk_fill') or ()]
    return {a: sum(x == a for x in legs) for a in RF.ACTIONS}


def rf_basis(trades) -> dict:
    """The denominator of rf_counts: entry legs, decided legs, legs without a recorded decision, and lot statuses."""
    legs = [a for t in trades for a in t.get('risk_fill') or ()]
    st = [t.get('risk_fill_status') for t in trades if t.get('risk_fill_status')]
    return dict(per='entry_leg', entry_legs=len(legs), decided=sum(x in RF.ACTIONS for x in legs),
                no_decision=sum(x is None for x in legs), other=sum(x is not None and x not in RF.ACTIONS for x in legs),
                lot_status={k: st.count(k) for k in sorted(set(st))})


def summary(rs, weeks: float) -> dict:
    """Headline numbers of one list of per-trade net R (trades without a risk are excluded and counted)."""
    xs = [r for r in rs if r is not None]
    win, loss = math.fsum(x for x in xs if x > 0), -math.fsum(x for x in xs if x < 0)
    return dict(n=len(xs), no_risk=len(rs) - len(xs), net_r=math.fsum(xs), mean_r=math.fsum(xs) / len(xs) if xs else None,
                pf=(win / loss) if loss else None, win_rate=sum(x > 0 for x in xs) / len(xs) if xs else None,
                per_week=len(xs) / weeks if weeks else None)


def divergence(zero: list, acct: list, row: str) -> dict:
    """Account path (costs at fill time) vs the zero-cost decisions repriced on the same row: trade set, sizing,
    occupancy and per-trade R. Comparability (Codex ruling 6356220526 pt 2): matched >= 95 % and median |dR| <= 25 % of
    the median |cost-R| of this row's repriced trades, else NOT_COMPARABLE (the account row stays primary)."""
    key = lambda t: (t['sym'], t['side'], t['entry_ms'])
    za, ac = {key(t): t for t in zero}, {key(t): t for t in acct}
    both = sorted(set(za) & set(ac))
    dr = sorted(abs(ac[k]['net_r'] - za[k]['rows'][row]['net_r']) for k in both
                if ac[k]['net_r'] is not None and za[k]['rows'][row]['net_r'] is not None)
    qr = sorted(abs(ac[k]['qty'] / za[k]['qty'] - 1) for k in both if za[k].get('qty'))
    occ = lambda ts: math.fsum(t['exit_ms'] - t['entry_ms'] for t in ts)
    n = max(len(za), len(ac), 1)
    med = dr[len(dr) // 2] if dr else 0.0
    out = dict(zero=len(za), account=len(ac), matched=len(both), matched_pct=round(100 * len(both) / n, 2),
               only_zero=len(set(za) - set(ac)), only_account=len(set(ac) - set(za)),
               qty_rel_median=qr[len(qr) // 2] if qr else 0.0,
               occupancy_ratio=(occ(acct) / occ(zero)) if occ(zero) else None,
               dr_median=med, dr_p95=dr[int(0.95 * (len(dr) - 1))] if dr else 0.0)
    cr = sorted(abs(t['rows'][row]['costs_r']) for t in zero if t['rows'][row]['costs_r'] is not None)
    out['cost_r_median'] = cr[len(cr) // 2] if cr else None
    ok = out['matched_pct'] >= COMPARABLE_MATCH_PCT and cr and med <= COMPARABLE_DR_SHARE * out['cost_r_median']
    out['comparability'] = 'COMPARABLE' if ok else 'NOT_COMPARABLE'
    return out


# ------------------------------------------------------------------ one cell
def _engine(run, minutes, sleeves, t0, *, start, rules, settings, latency_s, pricer=None, durable_io=False):
    """One engine replay. durable_io=False (default) skips the engine's durable state-file writes for the run (about
    70 % of replay time; harness-side, like replay's own clock / exchange patches): state stays in memory, and a test
    pins identical fills with and without the writes."""
    import engine as E
    import replay as RP
    labels, fills = [], []
    fn, pre = make_path_fn(run, minutes, labels, latency_s)
    saved = E.save_json
    if not durable_io:
        E.save_json = lambda *a, **k: None
    try:
        out = RP.run_replay(copy.deepcopy(run), copy.deepcopy(sleeves), t0, tf_sec=TF_MS // 1000, start=start,
                            exchange_rules=rules, path_fn=fn, pre_fn=pre, fill_log=fills, compare=False, settings=settings,
                            costs_off=pricer is None, fill_px=pricer, fund_fn=pricer.fund if pricer else None)
    finally:
        E.save_json = saved
    return out, fills, labels


def _sides(rows, get, weeks):
    return {sd: summary([get(r) for r in rows if sd in ('ALL', r['side'])], weeks) for sd in ('ALL', 'LONG', 'SHORT')}


def run_cell(raw15: dict, minutes: dict, sleeves, t0: int, *, model: C.CostModel, funding=None, exchange_rules=None,
             start=2000.0, scenarios=None, latency_s=0, settings=None, account_rows=ACCOUNT_ROWS) -> dict:
    """One cell at one latency: (1) the engine on the 1m path with costs off, every scenario re-priced per trade
    (diagnostic); (2) one engine run per account row with that row's costs applied at fill time (RISK-FILL-01 acts on
    the priced fills); (3) their divergence."""
    check_sleeves(sleeves)
    bad = check_inputs(raw15, minutes, funding)
    run = {s: df for s, df in raw15.items() if s not in bad}
    if not run:
        raise ScalpExecError('no complete symbol to run')
    rules = exchange_rules or {}
    scen = scenarios or SCENARIOS
    out, fills, labels = _engine(run, minutes, sleeves, t0, start=start, rules=exchange_rules, settings=settings,
                                 latency_s=latency_s)
    B15 = {s: bars_of(df.reset_index(drop=True)) for s, df in run.items()}
    rows = []
    for tr in trades_of(fills, out['history'], rules):
        s = tr['sym']
        rec = dict(sym=s, side=tr['ps'], entry_ms=tr['entry']['t_ms'], exit_ms=tr['closes'][-1]['t_ms'],
                   qty=tr['qty_in'], adds=tr['adds'], add_qty=tr['add_qty'], exit_kinds=[f['kind'] for f in tr['closes']],
                   exit_reason=tr['exit_reason'], risk_usd=tr['risk_usd'], rows={})
        for name, sc in scen.items():
            rec['rows'][name] = price_trade(tr, sc, row=model.row(s), slip_c=model.slip_c(s), bars15=B15[s],
                                            tick=(rules.get(s) or {}).get('tick'), funding_rows=(funding or {}).get(s))
        rows.append(rec)
    t_all = pd.to_datetime(next(iter(run.values())).t)
    weeks = (t_all.iloc[-1] - t_all.iloc[t0]).total_seconds() / (7 * 86400)
    acct, div, acct_inc = {}, {}, False
    for name in account_rows:
        pr = Pricer(scen[name], model, run, funding=funding, rules=rules)
        o2, f2, _l2 = _engine(run, minutes, sleeves, t0, start=start, rules=exchange_rules, settings=settings,
                              latency_s=latency_s, pricer=pr)
        ts = account_trades(f2, o2['history'], pr.paid, rules)
        acct[name] = dict(trades=ts, open_lots=len(o2['lots']), incomplete_funding=sorted(pr.incomplete),
                          adds=add_stats(ts, o2['lots']),
                          risk_fill=rf_counts(ts), risk_fill_basis=rf_basis(ts),
                          summary=_sides(ts, lambda t: t['net_r'], weeks))
        acct_inc = acct_inc or bool(pr.incomplete)
        div[name] = divergence(rows, ts, name)
    lab = [x[2] for x in labels]
    unres = sum(x in UNRESOLVED for x in lab)
    incomplete = acct_inc or bool(bad) or any(not r['rows'][n]['complete'] for r in rows for n in r['rows']
                                              if scen[n].funding_mode == 'actual')
    return dict(format=FORMAT, non_promotable=True, limitation=LIMITATION, latency_s=latency_s,
                config_digest=config_digest(sleeves), cost_model_digest=model.digest,
                labels=sorted({l for s in run for l in model.labels(s)}), excluded_symbols=bad,
                status='INCOMPLETE' if incomplete else 'COMPLETE', weeks=weeks,
                repriced=dict(trades=rows, summary={n: _sides(rows, lambda r, n=n: r['rows'][n]['net_r'], weeks) for n in scen},
                              risk_fill_would={n: {a: sum(r['rows'][n]['risk_fill_would'] == a for r in rows)
                                                   for a in RF.ACTIONS} for n in scen},
                              risk_fill_would_basis=dict(trades=len(rows), first_entry_only=True,
                                                         not_applicable_add=sum(1 for r in rows if r.get('adds')))),
                account=acct, divergence=div, adds=add_stats(rows, out['lots']),
                intrabar=dict(segments=len(lab), by_label={k: lab.count(k) for k in (ONE_LEVEL, RES_1M, SAME_MINUTE, NO_1M,
                                                                                     TWO_SIDED, NO_LATENCY)},
                              unresolved=unres, unresolved_share=unres / len(rows) if rows else 0.0,
                              park=bool(rows) and unres / len(rows) > IB.PARK_UNRESOLVED_SHARE),
                refused=len([m for m in out['missed'] if m.get('kind') != 'deferred']), open_lots=len(out['lots']),
                code=code_ids())


# ------------------------------------------------------------------ frozen inputs
def load_config(path: str, sha256: str = CONFIG_SHA256) -> dict:
    """The hashed as-run sleeve file (EDGE01_FROZEN_BASELINE_CONFIG_2026-10-23.json); refuses any other bytes."""
    with open(path, 'rb') as fh:
        raw = fh.read()
    if hashlib.sha256(raw).hexdigest() != sha256:
        raise ScalpExecError(f'{path}: sha256 does not match the frozen config {sha256[:12]}')
    doc = json.loads(raw)
    check_sleeves(doc['sleeves'])
    return doc


def cell_sleeves(doc: dict, key: str, side: str) -> list:
    """The frozen sleeve of `key`, unchanged except `sides` (long / short = a promotion cell; both = as run)."""
    if side not in ('long', 'short', 'both'):
        raise ScalpExecError('side must be long, short or both')
    sl = [copy.deepcopy(x) for x in doc['sleeves'] if x['key'] == key]
    if len(sl) != 1:
        raise ScalpExecError(f'the frozen config has {len(sl)} sleeves of {key}')
    sl[0]['sides'] = side
    return sl


def load_store(manifest_path: str, store: str, static_path: str, symbols, lo_ms: int, hi_ms: int,
               warm_bars: int = WARM_BARS) -> dict:
    """Manifest-bound inputs (every file re-hashed by pit.Dataset.rows): 1m klines -> the 15m candles (exact OHLC
    aggregation of complete minutes; a candle with a missing minute is dropped and counted), funding rows with the
    FUNDING-MARK-PROXY-V1 mark (close of the 1h mark bar ending at or before T), and per-symbol coverage."""
    import manifest as M
    import pit_run as PR
    man = M.load(manifest_path)
    if man['digest'] != MANIFEST_DIGEST:
        raise ScalpExecError(f'manifest digest {man["digest"][:12]} is not the frozen {MANIFEST_DIGEST[:12]}')
    if hi_ms > CUTOFF_MS:
        raise ScalpExecError(f'window end after the sealed cutoff {SEALED_CUTOFF}')
    with open(static_path, encoding='utf-8') as fh:
        static = json.load(fh)
    core = static['core_10']
    if any(s not in core for s in symbols):
        raise ScalpExecError('symbols must be CORE-10 members')
    ds = PR.StaticDataset(man, store, static, core[:max(core.index(s) for s in symbols) + 1])
    from_ms = lo_ms - warm_bars * TF_MS
    end = hi_ms - 1                                 # Dataset.slice keeps available_ms <= end: the window END is excluded
    #                                                 (a funding event AT the cutoff is sealed data, #187 first-run refusal)
    raw15, minutes, funding, cover = {}, {}, {}, {}
    for s in symbols:
        rows = [r for f in ds.files('klines', s, '1m') for r in ds.slice(f, from_ms, end)]
        ds._cache.clear(); ds._keys.clear()
        t = np.array([r[0] for r in rows], dtype='int64')
        o, h, l, c, v = (np.array([r[k] for r in rows], dtype=float) for k in (1, 2, 3, 4, 5))
        del rows
        g = t - t % TF_MS
        starts = np.flatnonzero(np.r_[True, g[1:] != g[:-1]])
        full = np.diff(np.r_[starts, len(t)]) == 15
        st = starts[full]
        df = pd.DataFrame(dict(t=pd.to_datetime(g[st], unit='ms'), o=o[st], h=np.maximum.reduceat(h, starts)[full],
                               l=np.minimum.reduceat(l, starts)[full], c=c[st + 14], v=np.add.reduceat(v, starts)[full]))
        raw15[s], minutes[s] = df, dict(t=t, o=o, h=h, l=l, c=c)
        mk = {r[0]: r[4] for f in ds.files('markPriceKlines', s, '1h') for r in ds.slice(f, from_ms - 3_600_000, end)}
        fr = [r for f in ds.files('fundingRate', s, None) for r in ds.slice(f, from_ms, end)]
        ds._cache.clear(); ds._keys.clear()
        ev, miss = [], 0
        for r in fr:
            m = mk.get(r[0] - r[0] % 3_600_000 - 3_600_000)
            if m is None:
                miss += 1; continue
            ev.append((int(r[0]), float(r[1]), float(m)))
        funding[s] = ev
        cover[s] = dict(first_ms=int(t[0]) if len(t) else None, last_ms=int(t[-1]) if len(t) else None,
                        minutes=int(len(t)), candles=int(full.sum()), candles_dropped=int((~full).sum()),
                        candles_expected=int((hi_ms - from_ms) // TF_MS), funding_rows=len(fr), funding_no_mark=miss)
    return dict(raw15=raw15, minutes=minutes, funding=funding, coverage=cover, manifest_digest=man['digest'],
                static_digest=static.get('digest'))


def window_start(raw15: dict, lo_ms: int) -> int:
    """t0 = the index of the first 15m candle at or after the window start on the run grid (>= 260 warm-up candles)."""
    t = next(iter(raw15.values())).t
    t0 = int(np.searchsorted(pd.to_datetime(t).astype('datetime64[ns]').astype('int64') // 10 ** 6, lo_ms))
    if t0 < 260:
        raise ScalpExecError('fewer than 260 warm-up candles before the window start')
    return t0


# ------------------------------------------------------------------ trial ledger + CLI
def trial_records(packet: dict, *, key: str, side: str, mgmt: str = 'frozen') -> list:
    """One trial per strategy x side x management x cost row x latency (Codex ruling 00ba8d0), for the family ledger."""
    return [dict(key=key, side=side, mgmt=mgmt, cost_row=n, latency_s=packet['latency_s'], kind=k,
                 config_digest=packet['config_digest'], status=packet['status'])
            for k, names in (('repriced', list(packet['repriced']['summary'])), ('account', list(packet['account'])))
            for n in names]


def candle_depth(key: str) -> dict:
    """The ruling-4 invariant for one key: served candles vs its longest indicator window (refuses when it fails)."""
    w = MAX_WINDOW[key]
    if not w < DEPTH_LIMIT <= WARM_BARS:
        raise ScalpExecError(f'{key}: indicator window {w} breaks the 1,000-candle acceptance (< {DEPTH_LIMIT})')
    return dict(served=WARM_BARS, max_window=w, limit=DEPTH_LIMIT, ok=True)


def main(argv=None) -> int:
    import argparse
    ap = argparse.ArgumentParser(description='SCALP-EXEC-15M-01 execution-grade cell (research only, non-promotable)')
    for k in ('--config', '--manifest', '--store', '--static', '--out'):
        ap.add_argument(k, required=True)
    ap.add_argument('--symbols', required=True, help='comma list of CORE-10 members')
    ap.add_argument('--window', required=True, help='START/END ISO UTC, END <= the sealed cutoff')
    ap.add_argument('--key', required=True, choices=KEYS)
    ap.add_argument('--side', required=True, choices=('long', 'short', 'both'))
    ap.add_argument('--latency-s', type=int, required=True, choices=LATENCIES)
    ap.add_argument('--rules', help='exchange-rules snapshot JSON {symbol: rule}')
    ap.add_argument('--cal', required=True, help='slip-cal-v1 JSON (or a prereg holding costs.slip_cal_v1)')
    ap.add_argument('--start', type=float, default=2000.0)
    a = ap.parse_args(argv)
    if os.path.exists(a.out):
        raise ScalpExecError(f'{a.out} exists (packets are never overwritten)')
    lo, hi = (int(pd.Timestamp(x).value // 10 ** 6) for x in a.window.split('/'))
    doc = load_config(a.config)
    syms = a.symbols.split(',')
    data = load_store(a.manifest, a.store, a.static, syms, lo, hi)
    with open(a.cal, encoding='utf-8') as fh:
        cal = json.load(fh)                         # the calibration itself, or a prereg holding costs.slip_cal_v1
        model = C.CostModel(cal.get('costs', {}).get('slip_cal_v1', cal), {s: 'crypto' for s in syms})
    rules = None
    if a.rules:
        with open(a.rules, encoding='utf-8') as fh:
            rules = json.load(fh)
    raw15 = data['raw15']
    pk = run_cell(raw15, data['minutes'], cell_sleeves(doc, a.key, a.side), window_start(raw15, lo), model=model,
                  funding=data['funding'], exchange_rules=rules, start=a.start, latency_s=a.latency_s,
                  settings={'PRESET': doc['preset']})
    pk.update(key=a.key, side=a.side, window=a.window, symbols=syms, coverage=data['coverage'],
              manifest_digest=data['manifest_digest'], static_digest=data['static_digest'], config_sha256=CONFIG_SHA256)
    pk['candle_depth'] = candle_depth(a.key)
    pk['trials'] = trial_records(pk, key=a.key, side=a.side)
    with open(a.out, 'x', encoding='utf-8') as fh:
        json.dump(pk, fh, sort_keys=True, default=float)
    print(json.dumps(dict(out=a.out, status=pk['status'], trades=len(pk['repriced']['trades']), trials=len(pk['trials'])),
                     sort_keys=True))
    return 0


if __name__ == '__main__':
    sys.exit(main())
