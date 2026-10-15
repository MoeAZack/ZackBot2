"""REX-01: synthetic mechanics of RANGE-EXIT-01 v0, family `range_vwap_rev`, candidate `range_vwap_rev.v1`
(frozen design: Claude v5 #13 6160852429 + addendum P1-P4 6161052222; Codex freeze + REX-01 release 6161875941;
split-end ruling 6169006548). DEVELOPMENT ONLY: this module reads no store, archive, manifest or holdout. It runs on
in-memory `Series` built by the caller (synthetic fixtures); the network loader, revision binding, registration and
every market / history / holdout read are REX-02 and later.

Contents
  * the symmetric closed-4h adapter: VWAP_N = sum quote_volume / sum volume, ATR = TR-SMA14 (`costs.tr_sma14`, computed
    here in exact decimal fractions so the frozen equality boundaries `z == +-k` and `drift == 1 ATR` are exact),
    z = (close - VWAP) / ATR, RG = |VWAP_i - VWAP_(i-6)| <= rg x ATR; long `RG, z_(i-1) <= -k, z_i > -k, close < VWAP`,
    short the exact mirror; a decision reads exactly N + 6 contiguous closed bars;
  * the X1 book (`run_book`): one lot per symbol per side book, market taker at the next open, stop floor/ceil_tick(fill
    -+ 1.5 ATR), exits = stop (intrabar; a bar opening through it fills at its open), mean close (X1 target), range
    invalidation, 30-bar time cap, delist notice; sizing qty = floor_step(1% W / (1.5 ATR + tick)), gross <= 3x on
    close x 1.10, min qty / notional at the decision and a counted fill-time refusal, max 4 lots, Cairo-day 3% MTM halt
    (`m3_eval.cairo_day`), drawdown kill disarmed; B0 (drawn closes) and B2 (naive fade) reuse this book;
  * `funding-window/2` (this family only; STRAT-01/02 keep the legacy `entry <= T < exit` window);
  * the X2 / X3 TAPE-LEVEL overlays (`overlay`) replayed from X1's admitted entries with X1's qty, stop and D;
  * `zb-missing-data/1` (`missing_data.py`, unchanged): ONE causal pass, the first unaccountable event halts the row at
    the last fully accounted cycle; a lot still open at the declared end is `incomplete.split_end` (Codex 6169006548:
    no synthesized close, no carry past the boundary, no later mark, no exclusion) -> INCOMPLETE / NO-RESULT.

Times: every bar is keyed by its open (ms UTC); a cycle `t` is the close of bar [t - 4h, t). A decision at close t
fills at the open of the bar opening at t. A run covers decisions at closes `first .. end` and fills / bars inside
[first, end); `first` = H (the bar closing at H is the last warm-up bar)."""
from __future__ import annotations

import copy
import math
import os
import sys
from collections import Counter
from dataclasses import dataclass, field
from decimal import Decimal
from fractions import Fraction

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import costs as C                                                                           # noqa: E402
import m3_eval as E                                                                         # noqa: E402
import missing_data as MD                                                                   # noqa: E402
import pit as P                                                                             # noqa: E402

FAMILY = 'range_vwap_rev'
CANDIDATE = 'range_vwap_rev.v1'
COMPARISON = 'SCOUT-EXIT-01'
PREREG_LABEL = 'DISCLOSED-EXPOSURE'
FUNDING_WINDOW = 'funding-window/2'
DEV_LABEL = 'DEVELOPMENT-ONLY-SYNTHETIC'
TAPE_LABEL = 'TAPE-LEVEL R - not book-level, not deployable'

HOUR = 3_600_000
TF = 4 * HOUR
N, K, STOP_ATR, RG_ATR, RG_LAG, CAP_BARS = 42, 2.0, 1.5, 1.0, 6, 30
W_BARS = 60                                         # warm-up W for every configuration (N + 12 needs 60)
WARMUP_MS = W_BARS * TF                             # 240 h
SLIP_C = 0.05                                       # slip-v1 c (SLIP-C-UNFITTED)
FUNDING_TOL = C.FUNDING_TIME_TOLERANCE_MS           # 60 s
FUNDING_GRIDS = (1, 2, 4, 8)                        # USD-M cadences; no sub-hour grid exists or is accepted
ROW = C.CRYPTO
BOOK = {'equity0': 10_000.0, 'risk_pct': 0.01, 'max_positions': 4, 'max_leverage': 3.0, 'cap_gap_buffer': 0.10,
        'daily_loss_pct': 0.03}
SIDES = ('long', 'short')

POLICY, COMPLETE, INCOMPLETE = MD.POLICY, MD.COMPLETE, MD.INCOMPLETE
GAP_CLASS = {'incomplete.gap': 'missing_bar', 'incomplete.slip_history': 'missing_bar',
             'incomplete.delisted': 'delisting', 'incomplete.funding': 'funding_rate_gap',
             'incomplete.funding_mark': 'funding_mark_gap', 'incomplete.split_end': 'split_censoring',
             'incomplete.notice_gap': 'notice_register_gap'}
HALTED = 'incomplete.book_halt'

# ------------------------------------------------------------------ remedy A: development-only split-end entry cutoff
# Codex ruling 6185035993 point 4 (draft 6183334843 A). A NEW, explicitly versioned candidate rule; the frozen
# `range_vwap_rev.v1` is untouched (run_book's default `entry_rule=None` executes exactly the v1 path). Under
# `entry-cutoff/1` a NEW entry decided at close t is admitted only if its MAXIMUM possible holding window ends strictly
# inside the split: the fill is the open at t, the cap pends an exit at the close t + cap x 4h and that exit fills at
# the open of the bar [t + cap x 4h, t + (cap + 1) x 4h), which must lie inside [first, end):
#     allowed  <=>  t + (cap + 1) x 4h <= end          (cap = 30 -> the last 31 decision closes of a split are blocked)
# It reads only the decision time, the configuration's cap and the frozen split end (known before the run): no price,
# no outcome, no later bar. It never closes, flattens or relabels anything; it only refuses entries. A blocked X1 / B2
# signal is counted (`splitcut_blocked`), a blocked B0 draw is a counted shortfall (`b0_shortfall_splitcut`).
SPLITCUT_CANDIDATE = 'range_vwap_rev.v1-splitcut'
SPLITCUT_RULE = 'entry-cutoff/1'
SPLITCUT_LABEL = 'DEVELOPMENT-ONLY CANDIDATE RULE - NON-PROMOTABLE (remedy A, Codex 6185035993 point 4)'
SPLITCUT = {'candidate_id': SPLITCUT_CANDIDATE, 'variant_of': CANDIDATE, 'entry_rule': SPLITCUT_RULE,
            'label': SPLITCUT_LABEL, 'development_only': True, 'promotable': False,
            'boundary': 'decision close t admitted iff t + (cap + 1) x 4h <= split end'}
ENTRY_RULES = (None, SPLITCUT_RULE)


def splitcut_allows(t: int, cap: int, end: int) -> bool:
    """`entry-cutoff/1`: True iff an entry decided at close t can be fully exited (cap exit filled at the open
    t + cap x 4h) inside a split ending at `end`. Causal: time, cap and the frozen boundary only."""
    return t + (cap + 1) * TF <= end


class RexError(ValueError):
    pass


class Incomplete(Exception):
    """The first unaccountable event (reason, first unaccountable ms, phase); `symbol` is set by the book."""

    def __init__(self, reason, first_ms, phase, symbol=None):
        super().__init__(reason)
        self.reason, self.first_ms, self.phase, self.symbol = reason, first_ms, phase, symbol


class NoticeGapHalt(Incomplete):
    """An R_ext-GAP affecting the decisions at close t (Codex 6169708729 item 4): the row halts BEFORE those decisions;
    the fills already causally executed in [t - 4h, t) (ordered by the previous, fully accountable close) stay - the
    cycle is NOT rolled back."""


# ------------------------------------------------------------------ configurations (trials per side)
@dataclass(frozen=True)
class Cfg:
    name: str
    n: int = N
    k: float = K
    stop_atr: float = STOP_ATR
    rg: float = RG_ATR
    cap: int = CAP_BARS

    @property
    def lookback(self) -> int:
        return self.n + RG_LAG


PRIMARY = Cfg(f'{CANDIDATE}.x1_mean')


def neighbours() -> list[Cfg]:
    """The 8 one-step X1 neighbours: k +-0.5, N +-12, stop_atr +-0.5, RG +-0.5 ATR."""
    out = []
    for f, d in (('k', 0.5), ('n', 12), ('stop_atr', 0.5), ('rg', 0.5)):
        for sgn in (-1, 1):
            v = getattr(PRIMARY, f) + sgn * d
            out.append(Cfg(f'{CANDIDATE}.{f}{"+" if sgn > 0 else "-"}{d:g}', **{f: v}))
    return out


# ------------------------------------------------------------------ synthetic data containers
@dataclass
class Series:
    """One symbol: 4h last-price klines keyed by open ms (a missing key is a hole), funding rows (pit.Funding),
    1h mark closes keyed by the mark bar's open ms, and the pinned exchange rule (tick, step, min_qty, min_notional)."""
    bars: dict
    rule: dict
    funding: tuple = ()
    marks: dict = field(default_factory=dict)

    def __post_init__(self):
        self.funding = tuple(sorted(self.funding, key=lambda r: r.time_ms))
        self.first_ms = min(self.bars) if self.bars else None
        self.last_ms = max(self.bars) if self.bars else None


@dataclass(frozen=True)
class NoticeView:
    """Delist notices as the book sees them: {symbol: known-at ms (the first-seen releaseDate)} and the R_ext-GAP
    intervals [start, end) of the register (built by `range_vwap_rev_notice`)."""
    releases: dict = field(default_factory=dict)
    gaps: tuple = ()

    def known(self, s, t) -> bool:
        r = self.releases.get(s)
        return r is not None and r <= t

    def release(self, s):
        return self.releases.get(s)

    def gap_at(self, t):
        """The earliest GAP start at or before t (eligibility UNDETERMINED from there on), else None."""
        starts = [a for a, _ in self.gaps if a <= t]
        return min(starts) if starts else None


NO_NOTICES = NoticeView()


# ------------------------------------------------------------------ exact arithmetic helpers
def fr(x) -> Fraction:
    return Fraction(Decimal(repr(float(x)))) if not isinstance(x, Fraction) else x


def floor_to(v, unit) -> float:
    u = fr(unit)
    return float(math.floor(fr(v) / u) * u)


def ceil_to(v, unit) -> float:
    u = fr(unit)
    return float(math.ceil(fr(v) / u) * u)


# ------------------------------------------------------------------ the adapter (closed 4h bars only)
@dataclass(frozen=True)
class Ind:
    close: Fraction
    vwap: Fraction | None
    vwap_prev: Fraction | None
    vwap_lag: Fraction | None
    atr: Fraction | None
    atr_prev: Fraction | None
    z: Fraction | None
    z_prev: Fraction | None
    rg: bool | None
    close_prev: Fraction


def _tr_sma14(bars) -> Fraction:
    """TR-SMA14 of the last 15 bars (the `costs.tr_sma14` formula, in exact decimal fractions)."""
    b = bars[-15:]
    tr = [max(fr(x.high) - fr(x.low), abs(fr(x.high) - fr(p.close)), abs(fr(x.low) - fr(p.close)))
          for p, x in zip(b, b[1:])]
    return sum(tr, Fraction(0)) / 14


def _vwap(bars):
    v = sum((fr(b.volume) for b in bars), Fraction(0))
    return None if v == 0 else sum((fr(b.quote_volume) for b in bars), Fraction(0)) / v


def indicators(cfg: Cfg, bars) -> Ind:
    """Indicators of the decision bar i = bars[-1] from exactly `cfg.lookback` contiguous closed bars i-N-5..i.
    Undefined VWAP (sum volume = 0) or ATR <= 0 leaves z / RG undefined (None): no entry, no indicator exit."""
    if len(bars) != cfg.lookback:
        raise RexError(f'a decision reads exactly N + 6 = {cfg.lookback} bars, got {len(bars)}')
    n, i = cfg.n, len(bars) - 1
    vw = _vwap(bars[i - n + 1:])
    vw_p = _vwap(bars[i - n:i])
    vw_l = _vwap(bars[i - RG_LAG - n + 1:i - RG_LAG + 1])
    atr, atr_p = _tr_sma14(bars), _tr_sma14(bars[:-1])
    c, cp = fr(bars[-1].close), fr(bars[-2].close)
    atr = atr if atr > 0 else None
    atr_p = atr_p if atr_p > 0 else None
    z = None if vw is None or atr is None else (c - vw) / atr
    zp = None if vw_p is None or atr_p is None else (cp - vw_p) / atr_p
    rg = None if vw is None or vw_l is None or atr is None else abs(vw - vw_l) <= fr(cfg.rg) * atr
    return Ind(c, vw, vw_p, vw_l, atr, atr_p, z, zp, rg, cp)


def entry_x1(cfg: Cfg, ind: Ind, side: str) -> bool:
    if ind.z is None or ind.z_prev is None or ind.rg is not True:
        return False
    k = fr(cfg.k)
    if side == 'long':
        return ind.z_prev <= -k and ind.z > -k and ind.close < ind.vwap
    return ind.z_prev >= k and ind.z < k and ind.close > ind.vwap


def entry_b2(cfg: Cfg, ind: Ind, side: str) -> bool:
    """B2 naive fade: the first close outside the band (no RG, no confirmation)."""
    if ind.z is None or ind.z_prev is None:
        return False
    k = fr(cfg.k)
    return (ind.z_prev > -k and ind.z <= -k) if side == 'long' else (ind.z_prev < k and ind.z >= k)


def mean_close(ind: Ind, side: str) -> bool:
    """X1 target: a close at or through VWAP."""
    if ind.vwap is None or ind.z is None:
        return False
    return ind.close >= ind.vwap if side == 'long' else ind.close <= ind.vwap


def opposite_band(cfg: Cfg, ind: Ind, side: str) -> bool:
    if ind.z is None:
        return False
    return ind.z >= fr(cfg.k) if side == 'long' else ind.z <= -fr(cfg.k)


def invalidated(ind: Ind) -> bool:
    return ind.rg is False


def in_bucket(cfg: Cfg, z, side: str) -> bool:
    """The B0 side bucket: long -k < z < 0, short 0 < z < k."""
    k = fr(cfg.k)
    return (-k < z < 0) if side == 'long' else (0 < z < k)


# ------------------------------------------------------------------ data reads (fail closed)
def _gap(x: Series, o: int, held: bool) -> str:
    """A held symbol whose series has ended without a notice is `incomplete.delisted`; any other missing bar is a hole
    (`incomplete.gap`, never relabelled with hindsight)."""
    return 'incomplete.delisted' if held and (x.last_ms is None or x.last_ms < o) else 'incomplete.gap'


def read(x: Series, t: int, nbars: int, *, held=False, phase='decision'):
    out = []
    for o in range(t - nbars * TF, t, TF):
        b = x.bars.get(o)
        if b is None:
            raise Incomplete(_gap(x, o, held), o, phase)
        out.append(b)
    return out


def bar_at(x: Series, o: int, *, held=False, phase='bar'):
    b = x.bars.get(o)
    if b is None:
        raise Incomplete(_gap(x, o, held), o, phase)
    return b


def bps_at(x: Series, bo: int, scen) -> float:
    """slip-v1 bps of a fill at open `bo`: the 15 closed bars ending at bo (missing = incomplete.slip_history)."""
    bars = []
    for o in range(bo - 15 * TF, bo, TF):
        b = x.bars.get(o)
        if b is None:
            raise Incomplete('incomplete.slip_history', o, 'fill')
        bars.append(b)
    return C.slip_bps(bars, SLIP_C, scen.vol)


def eligible(x: Series, s: str, t: int, notices: NoticeView) -> bool:
    """(a) first archived 4h candle <= t - W x 4h and (b) no delist notice known at t (releaseDate <= t)."""
    return x.first_ms is not None and x.first_ms <= t - WARMUP_MS and not notices.known(s, t)


# ------------------------------------------------------------------ funding-window/2
def t_nom(row) -> int:
    """The nominal funding instant: row time rounded to the whole hour, valid only within 60 s AND on the row's
    interval grid from 00:00 UTC (1/2/4/8 h). Anything else is `incomplete.funding`."""
    h = (row.time_ms + HOUR // 2) // HOUR * HOUR
    iv = row.interval_hours
    if (abs(row.time_ms - h) > FUNDING_TOL or type(iv) is not int or iv not in FUNDING_GRIDS
            or h % (iv * HOUR) != 0):
        raise Incomplete('incomplete.funding', row.time_ms, 'funding')
    return h


def funding_events(x: Series, entry_open: int, lo: int, hi: int, *, incl: bool):
    """Coverage proof over (entry_open, hi] (or hi exclusive) and the events with lo < T_nom <= hi (or < hi):
    the PHASE ANCHOR (the event with T_nom <= entry_open; at a funding open it is the event at entry, never paid) must
    exist, consecutive events are exactly one interval apart, and the next event after the last covered row falls after
    hi (inclusive window) or at / after hi (exclusive). Returns [(T_nom, rate)]."""
    span_lo = entry_open - (C.MAX_FUNDING_INTERVAL_H * HOUR) - FUNDING_TOL
    rows = [(t_nom(r), r) for r in x.funding if span_lo <= r.time_ms <= hi + FUNDING_TOL]
    anchors = [(t, r) for t, r in rows if t <= entry_open]
    if not anchors:
        raise Incomplete('incomplete.funding', entry_open, 'funding')
    chain = [anchors[-1]] + [(t, r) for t, r in rows if anchors[-1][0] < t <= hi]
    for (ta, ra), (tb, rb) in zip(chain, chain[1:]):
        if (tb - ta) not in {ra.interval_hours * HOUR, rb.interval_hours * HOUR}:
            raise Incomplete('incomplete.funding', ta, 'funding')
    nxt = chain[-1][0] + chain[-1][1].interval_hours * HOUR
    if (nxt <= hi) if incl else (nxt < hi):
        raise Incomplete('incomplete.funding', nxt, 'funding')
    return [(t, r.rate) for t, r in chain if lo < t and (t <= hi if incl else t < hi)]


def mark_at(x: Series, T: int) -> float:
    m = x.marks.get(T - HOUR)                     # funding-mark proxy v1: the 1h mark bar ending at T
    if m is None:
        raise Incomplete('incomplete.funding_mark', T, 'funding')
    return m


def bar_funding(x: Series, side: str, qty: float, entry_open: int, bo: int, scen, *, stopped: bool):
    """Funding of one played bar [bo, bo + 4h) of a lot entered at `entry_open` (funding-window/2):
      held through the bar: every event bo < T_nom <= close (T at the close is paid; the exit / next fill at that open
      settles after it); T at bo was paid by the previous bar, and in the entry bar it is not paid (the entry filled
      after settlement);
      stopped inside the bar: interior events bo < T_nom < close are charged only when ADVERSE (long rate > 0, short
      rate < 0) - the stop instant is unknown; T at the close is not charged.
    Returns (cost, [(T, rate, mark)])."""
    t = bo + TF
    evs = funding_events(x, entry_open, bo, t, incl=not stopped)
    out = []
    for T, rate in evs:
        if stopped and not ((rate > 0) if side == 'long' else (rate < 0)):
            continue
        out.append((T, rate, mark_at(x, T)))
    return C.funding_cost(side, qty, out, scen, bars_held=1, entry_notional=0.0), out


def legacy_window(x: Series, entry_ms: int, exit_ms: int):
    """The registered pit_run window `entry_ms <= T < exit_ms` on raw row times: the non-gating
    `funding_legacy_window` report row only (never a trial, never used by this family's accounting)."""
    return [(r.time_ms, r.rate) for r in x.funding if entry_ms <= r.time_ms < exit_ms]


# ------------------------------------------------------------------ sizing / stops
def stop_of(side: str, fill: float, atr: Fraction, cfg: Cfg, tick) -> float:
    d = fr(cfg.stop_atr) * atr
    return floor_to(fr(fill) - d, tick) if side == 'long' else ceil_to(fr(fill) + d, tick)


def size(snapshot: float, atr: Fraction, close: Fraction, open_notional: float, cfg: Cfg, rule: dict, book=BOOK):
    """qty = floor_step(1% W / (1.5 ATR + tick)), gross <= 3x equity on close x 1.10 (decision-time values only);
    min qty / notional checked on qty x close_i. Returns (qty, None) or (None, refusal code)."""
    raw = fr(snapshot) * fr(book['risk_pct']) / (fr(cfg.stop_atr) * atr + fr(rule['tick']))
    room = max(fr(book['max_leverage']) * fr(snapshot) - fr(open_notional), Fraction(0))
    q = min(raw, room / (close * (1 + fr(book['cap_gap_buffer']))))
    q = floor_to(q, rule['step']) if q > 0 else 0.0
    if q <= 0 or q < float(rule['min_qty']) or fr(q) * close < fr(rule['min_notional']):
        return None, 'execution.size_min'
    return q, None


# ------------------------------------------------------------------ the X1 book (and B0 / B2 under X1 admission)
def _sgn(side):
    return 1.0 if side == 'long' else -1.0


def run_book(tape: dict, cfg: Cfg, side: str, *, first: int, end: int, order=None, row: str = 'base',
             notices: NoticeView = NO_NOTICES, mode: str = 'x1', draws=None, book=BOOK, entry_rule=None) -> dict:
    """ONE causal pass of one side's book over closes first..end (see module doc). `mode`: 'x1' (the primary and its
    neighbours), 'b2' (naive fade), 'b0' (entries only at the drawn closes `draws` = {(symbol, close)}; a draw the
    replicate cannot take - not flat, re-entry lock, ineligible - is a counted shortfall). Per cycle t:
      (A) open of the bar [t - 4h, t): pending exits (a bar opening at / through a stop is a gap stop at the open, the
          worse slippage row even when a market exit is also pending; a delist notice exits at the first open strictly
          after its known-at time), then one wallet snapshot, then entries in frozen rank order;
      (B) the bar per lot: stop touch (last-price low / high, exact touch triggers) fills at the stop with adverse
          slip; funding-window/2;
      (C) the decision at close t: MTM, Cairo-day 3% halt, then per symbol exits (mean / invalidation / cap) and entries
          (eligible and flat; re-entry needs the trigger bar i-1 to close strictly after the last exit fill, compared
          with the exit bar open). No entry is decided at close `end` (its fill would lie outside the window).
    A lot open at `end` (including an exit pending there) is `incomplete.split_end`.
    `entry_rule`: None = the frozen v1 book; SPLITCUT_RULE = the development-only `entry-cutoff/1` (see there), which
    must be selected explicitly and applies identically to every mode (X1, neighbours, B2, B0) and cost row."""
    if side not in SIDES or mode not in ('x1', 'b2', 'b0'):
        raise RexError('side must be long/short and mode x1/b2/b0')
    if entry_rule not in ENTRY_RULES:
        raise RexError(f'entry_rule must be one of {ENTRY_RULES}')
    if (first - end) % TF or end <= first:
        raise RexError('first < end on the 4h grid')
    scen = C.STRESS[row]
    order = list(order or tape)
    sg = _sgn(side)
    buy, sell = ('buy', 'sell') if side == 'long' else ('sell', 'buy')
    draws = set(draws or ())
    st = {'wallet': book['equity0'], 'lots': {}, 'last_exit': {}, 'pend_exit': {}, 'pend_entry': [],
          'day': None, 'day_start': None, 'halted_day': None, 'trades': [], 'refusals': Counter(), 'series': [],
          'signals': [], 'counts': Counter(), 'inel': Counter(), 'cycles': 0, 'closes': {}, 'stopped_now': set()}
    halt, prev_t = None, None

    def close_lot(s, ref, exit_open, reason, gap, bps, label=None):
        lot, x = st['lots'].pop(s), tape[s]
        xf = C.fill_price(ref, sell, bps, scen, gap=gap, tick=x.rule['tick'])
        fee = C.fee(lot['qty'] * xf, 'taker', ROW, scen)
        gross = sg * lot['qty'] * (xf - lot['fill'])
        st['wallet'] += gross - fee
        net = gross - lot['fees'] - fee - lot['funding']
        st['trades'].append({'symbol': s, 'side': side, 'decision_ms': lot['decision_ms'], 'entry_ms': lot['entry_ms'],
                             'z': lot['z'], 'atr': lot['atr'], 'qty': lot['qty'], 'fill': lot['fill'],
                             'stop': lot['stop'], 'D': lot['D'], 'exit_ms': exit_open, 'exit_fill': xf,
                             'reason': reason, 'gap': gap, 'fees': lot['fees'] + fee, 'funding': lot['funding'],
                             'net': net, 'r': net / lot['D'],
                             'bars': (exit_open - lot['entry_ms']) // TF + (1 if label and not gap else 0),
                             'intrabar': label, 'status': COMPLETE})
        st['last_exit'][s] = exit_open

    times = range(first, end + TF, TF)
    for t in times:
        snap = copy.deepcopy(st)
        cur = None
        try:
            st['stopped_now'] = set()
            if t > first:
                bo = t - TF
                g0 = notices.gap_at(bo)              # a gap already affecting the prior decision is checked BEFORE
                if g0 is not None:                   # that decision's open fills: none of them executes
                    raise Incomplete('incomplete.notice_gap', g0, 'open', symbol='*')
                # (A) exits at the open
                for s in [s for s in order if s in st['lots']]:
                    cur = s
                    x, lot = tape[s], st['lots'][s]
                    b = bar_at(x, bo, held=True, phase='exit')
                    rel = notices.release(s)
                    why = 'exit.delist' if rel is not None and rel < bo else st['pend_exit'].get(s)
                    ref, gap = C.stop_reference(side, lot['stop'], b.open)
                    if gap:
                        close_lot(s, ref, bo, 'exit.stop', True, bps_at(x, bo, scen), 'BAR')
                    elif why:
                        close_lot(s, b.open, bo, why, False, bps_at(x, bo, scen))
                st['pend_exit'] = {}
                snapshot = st['wallet']
                for s, dec_t, ind, halted in st['pend_entry']:
                    cur = s
                    x = tape[s]
                    if halted:
                        st['refusals']['book.day_halt'] += 1
                        continue
                    if len(st['lots']) >= book['max_positions']:
                        st['refusals']['book.max_positions'] += 1
                        continue
                    b = bar_at(x, bo, phase='next_open')
                    notional = math.fsum(v['qty'] * v['fill'] for v in st['lots'].values())
                    q, why = size(snapshot, ind.atr, ind.close, notional, cfg, x.rule, book)
                    if q is None:
                        st['refusals'][why] += 1
                        continue
                    fill = C.fill_price(b.open, buy, bps_at(x, bo, scen), scen, tick=x.rule['tick'])
                    if q * fill < float(x.rule['min_notional']):
                        st['refusals']['execution.size_min_fill'] += 1
                        continue
                    stop = stop_of(side, fill, ind.atr, cfg, x.rule['tick'])
                    fee = C.fee(q * fill, 'taker', ROW, scen)
                    st['wallet'] -= fee
                    st['lots'][s] = {'decision_ms': dec_t, 'entry_ms': bo, 'z': float(ind.z), 'atr': float(ind.atr),
                                     'qty': q, 'fill': fill, 'stop': stop, 'D': q * abs(fill - stop), 'fees': fee,
                                     'funding': 0.0}
                st['pend_entry'] = []
                # (B) the bar
                for s in [s for s in order if s in st['lots']]:
                    cur = s
                    x, lot = tape[s], st['lots'][s]
                    b = bar_at(x, bo, held=True)
                    hit = b.low <= lot['stop'] if side == 'long' else b.high >= lot['stop']
                    cost, _ = bar_funding(x, side, lot['qty'], lot['entry_ms'], bo, scen, stopped=hit)
                    lot['funding'] += cost
                    st['wallet'] -= cost
                    if hit:
                        ref, gap = C.stop_reference(side, lot['stop'], b.open)
                        close_lot(s, ref, bo, 'exit.stop', gap, bps_at(x, bo, scen), 'BAR')
                        st['stopped_now'].add(s)
                    else:
                        st['closes'][s] = b.close
            # (C) the close t: valuation of the bar just played, then the decisions
            mtm = st['wallet'] + math.fsum(sg * v['qty'] * (st['closes'][k] - v['fill'])
                                           for k, v in st['lots'].items())
            st['series'].append((t, mtm))
            g = notices.gap_at(t)
            if g is not None:                        # eligibility UNDETERMINED at t: halt before the decisions at t
                raise NoticeGapHalt('incomplete.notice_gap', g, 'decision', symbol='*')
            d = E.cairo_day(t)
            if d != st['day']:
                st['day'], st['day_start'] = d, mtm
            if st['halted_day'] != d and st['day_start'] > 0 and mtm / st['day_start'] - 1.0 <= -book['daily_loss_pct']:
                st['halted_day'] = d
            if t < end:
                st['cycles'] += 1
            for s in order:
                cur = s
                x = tape[s]
                held = s in st['lots']
                ok = eligible(x, s, t, notices)
                if t < end and not ok:
                    st['inel'][s] += 1
                if held:
                    ind = indicators(cfg, read(x, t, cfg.lookback, held=True))
                    lot = st['lots'][s]
                    if mean_close(ind, side):
                        st['pend_exit'][s] = 'exit.mean'
                    elif invalidated(ind):
                        st['pend_exit'][s] = 'exit.invalidation'
                    elif t - lot['entry_ms'] >= cfg.cap * TF:
                        st['pend_exit'][s] = 'exit.cap'
                    if mode == 'b0' and (s, t) in draws:
                        st['counts']['b0_shortfall_not_flat'] += 1
                    continue
                if not ok:
                    if mode == 'b0' and (s, t) in draws:
                        st['counts']['b0_shortfall_ineligible'] += 1
                    continue
                ind = indicators(cfg, read(x, t, cfg.lookback))
                if s in st['stopped_now'] and mean_close(ind, side):
                    st['counts']['same_bar_stop_priority'] += 1
                if t >= end:
                    continue
                locked = s in st['last_exit'] and not (t - TF > st['last_exit'][s])
                if mode == 'b0':
                    want = (s, t) in draws
                    if want and locked:
                        st['counts']['b0_shortfall_locked'] += 1
                        want = False
                else:
                    want = (entry_x1 if mode == 'x1' else entry_b2)(cfg, ind, side) and not locked
                if want and entry_rule is not None and not splitcut_allows(t, cfg.cap, end):
                    st['counts']['b0_shortfall_splitcut' if mode == 'b0' else 'splitcut_blocked'] += 1
                    want = False
                if want:
                    st['signals'].append((s, t, float(ind.z)))
                    st['pend_entry'].append((s, t, ind, st['halted_day'] == d))
        except Incomplete as h:
            if h.symbol is None:
                h.symbol = cur
            if isinstance(h, NoticeGapHalt):
                prev_t = t                           # [t - 4h, t) was fully accounted: its fills stay
            else:
                st = snap                            # roll the cycle back to the last fully accounted close
            halt = (h, t)
            break
        prev_t = t
    out = _finish(st, halt, prev_t, end, order, side, cfg, row, mode)
    if entry_rule is not None:
        out['entry_rule'] = entry_rule
        out['candidate_id'] = SPLITCUT_CANDIDATE
        out['promotable'] = False
    return out


def _lot_row(s, lot, reason, first_ms, last_ms, phase):
    return {'symbol': s, 'entry_ms': lot['entry_ms'], 'reason': reason, 'class': GAP_CLASS.get(reason, 'halted'),
            'first_unaccountable_ms': first_ms, 'last_valid_ms': last_ms, 'phase': phase,
            'secondary': reason == HALTED}


def _finish(st, halt, prev_t, end, order, side, cfg, row, mode) -> dict:
    inc, censor = [], None
    lots = st['lots']
    if halt is not None:
        h, at = halt
        prim = h.symbol
        rows = []
        if prim not in lots:
            rows.append({'symbol': prim, 'entry_ms': None, 'reason': h.reason, 'class': GAP_CLASS[h.reason],
                         'first_unaccountable_ms': h.first_ms, 'last_valid_ms': prev_t, 'phase': h.phase,
                         'secondary': False})
        for s in [s for s in order if s in lots]:
            if s == prim:
                rows.append(_lot_row(s, lots[s], h.reason, h.first_ms, prev_t, h.phase))
            else:
                rows.append(dict(_lot_row(s, lots[s], HALTED, at, prev_t, 'halt'), primary_ref=f'{prim}@{h.first_ms}'))
        inc = rows
        censor = {'class': GAP_CLASS[h.reason], 'reason': h.reason, 'symbol': prim, 'phase': h.phase, 'halted': True,
                  'cycle_ms': at, 'first_unaccountable_ms': h.first_ms, 'last_valid_ms': prev_t}
    else:
        for s in [s for s in order if s in lots]:
            inc.append(_lot_row(s, lots[s], 'incomplete.split_end', end, end, 'split_end'))
        if inc:
            censor = {'class': 'split_censoring', 'reason': 'incomplete.split_end', 'symbol': None,
                      'phase': 'split_end', 'halted': False, 'cycle_ms': end, 'first_unaccountable_ms': end,
                      'last_valid_ms': end}
    cyc = st['cycles']
    share = {s: (st['inel'][s] / cyc if cyc else 0.0) for s in order}
    book_share = (sum(st['inel'].values()) / (cyc * len(order))) if cyc and order else 0.0
    out = {'status': INCOMPLETE if inc else COMPLETE, 'policy': POLICY, 'side': side, 'config': cfg.name,
           'row': row, 'mode': mode, 'trades': sorted(st['trades'], key=lambda r: (r['entry_ms'], r['symbol'])),
           'incomplete': inc, 'refusals': dict(sorted(st['refusals'].items())), 'wallet_end': st['wallet'],
           'series': st['series'], 'signals': st['signals'], 'counts': dict(sorted(st['counts'].items())),
           'ineligible': {'per_symbol': share, 'book': book_share, 'cycles': cyc},
           'open_lots': {s: {k: v for k, v in lots[s].items()} for s in order if s in lots}}
    if censor is not None:
        out['censor'] = censor
    return out


# ------------------------------------------------------------------ SCOUT-EXIT-01 overlays (TAPE-LEVEL)
def overlay(tape: dict, cfg: Cfg, side: str, trade: dict, arm: str, *, end: int, row: str = 'base',
            notices: NoticeView = NO_NOTICES) -> dict:
    """Replay X1's admitted trade j under arm 'x2' (partial at the mean event + breakeven runner, exits on its stop,
    an opposite-band close or the common exits) or 'x3' (no target; trail stop <- max(stop, floor_tick(close - 1.5
    ATR)), short mirror, effective from the next bar), from X1's entry fill with X1's qty, stop and D. No book, caps,
    lockout or halts. Any hole the overlay touches, or a slice still open at `end`, makes the PAIR INCOMPLETE (this
    comparison only; X1 is never altered). R = sum of slice net PnL / D_j - TAPE-LEVEL, not deployable."""
    if arm not in ('x2', 'x3'):
        raise RexError("arm must be 'x2' or 'x3'")
    if trade.get('status') != COMPLETE:
        return {'status': INCOMPLETE, 'reason': 'x1_trade_incomplete', 'arm': arm, 'label': TAPE_LABEL}
    s, x = trade['symbol'], tape[trade['symbol']]
    scen = C.STRESS[row]
    sg = _sgn(side)
    sell = 'sell' if side == 'long' else 'buy'
    e0, fill, qty, stop, D = trade['entry_ms'], trade['fill'], trade['qty'], trade['stop'], trade['D']
    tick, rule = x.rule['tick'], x.rule
    slices, funding, counts, path = [], 0.0, Counter(), []
    rem, partial_done, pend, pend_partial = qty, False, None, False

    def out_slice(q, ref, bo, reason, gap):
        xf = C.fill_price(ref, sell, bps_at(x, bo, scen), scen, gap=gap, tick=tick)
        fee = C.fee(q * xf, 'taker', ROW, scen)
        slices.append({'qty': q, 'exit_ms': bo, 'exit_fill': xf, 'reason': reason, 'gap': gap, 'fee': fee,
                       'gross': sg * q * (xf - fill)})

    def below_min(q, px):
        return q <= 0 or q < float(rule['min_qty']) or q * px < float(rule['min_notional'])

    try:
        bo = e0
        while True:
            if bo >= end:
                raise Incomplete('incomplete.split_end', end, 'split_end')
            b = bar_at(x, bo, held=True, phase='exit')
            if bo > e0:
                rel = notices.release(s)
                ref, gap = C.stop_reference(side, stop, b.open)
                if gap:
                    out_slice(rem, ref, bo, 'exit.breakeven' if partial_done else 'exit.stop', True)
                    rem = 0
                elif rel is not None and rel < bo:
                    out_slice(rem, b.open, bo, 'exit.delist', False)
                    rem = 0
                elif pend_partial:
                    q1 = floor_to(0.5 * qty, rule['step'])
                    q2 = float(fr(qty) - fr(q1))
                    if below_min(q1, b.open) or below_min(q2, b.open):
                        counts['x2_whole_below_min'] += 1
                        out_slice(rem, b.open, bo, 'exit.mean_whole', False)
                        rem = 0
                    else:
                        out_slice(q1, b.open, bo, 'exit.partial', False)
                        rem, partial_done, stop = q2, True, fill
                        be_ref, be_gap = C.stop_reference(side, stop, b.open)
                        if be_gap:
                            out_slice(rem, be_ref, bo, 'exit.breakeven', True)
                            rem = 0
                elif pend:
                    out_slice(rem, b.open, bo, pend, False)
                    rem = 0
                pend, pend_partial = None, False
                if rem == 0:
                    break
            path.append((bo, stop, rem))
            hit = b.low <= stop if side == 'long' else b.high >= stop
            cost, _ = bar_funding(x, side, rem, e0, bo, scen, stopped=hit)
            funding += cost
            if hit:
                ref, gap = C.stop_reference(side, stop, b.open)
                out_slice(rem, ref, bo, 'exit.breakeven' if partial_done else 'exit.stop', gap)
                rem = 0
                break
            t = bo + TF
            ind = indicators(cfg, read(x, t, cfg.lookback, held=True))
            if arm == 'x2' and not partial_done and mean_close(ind, side):
                pend_partial = True
            elif arm == 'x2' and partial_done and opposite_band(cfg, ind, side):
                pend = 'exit.opposite_band'
            elif invalidated(ind):
                pend = 'exit.invalidation'
            elif t - e0 >= cfg.cap * TF:
                pend = 'exit.cap'
            if pend_partial and (invalidated(ind) or t - e0 >= cfg.cap * TF):
                pend_partial, pend = False, 'exit.invalidation' if invalidated(ind) else 'exit.cap'
            if arm == 'x3' and ind.atr is not None:
                d = fr(cfg.stop_atr) * ind.atr
                stop = (max(stop, floor_to(ind.close - d, tick)) if side == 'long'
                        else min(stop, ceil_to(ind.close + d, tick)))
            bo = t
    except Incomplete as h:
        return {'status': INCOMPLETE, 'reason': h.reason, 'first_unaccountable_ms': h.first_ms, 'arm': arm,
                'symbol': s, 'entry_ms': e0, 'label': TAPE_LABEL}
    entry_fee = C.fee(qty * fill, 'taker', ROW, scen)
    net = math.fsum(sl['gross'] - sl['fee'] for sl in slices) - entry_fee - funding
    return {'status': COMPLETE, 'arm': arm, 'symbol': s, 'entry_ms': e0, 'qty': qty, 'D': D, 'stop0': trade['stop'],
            'slices': slices, 'funding': funding, 'net': net, 'r': net / D,
            'exit_ms': max(sl['exit_ms'] for sl in slices), 'counts': dict(counts), 'path': path, 'label': TAPE_LABEL}


def compare(x1: dict, pairs: dict, side: str) -> dict:
    """Paired overlay statistics of one side (descriptive, non-gating, outside Holm, never promotable): pair status
    (> 5% INCOMPLETE -> NOT-EVALUABLE), R < -1 count / share per arm, gross overlap with X1's next trade."""
    MD_ok = x1.get('status') == COMPLETE
    out = {'label': TAPE_LABEL, 'side': side, 'gating': False, 'x1_status': x1.get('status')}
    trades = x1['trades']
    nxt = {}
    by_sym = {}
    for tr in trades:
        by_sym.setdefault(tr['symbol'], []).append(tr)
    for lst in by_sym.values():
        for a, b in zip(lst, lst[1:]):
            nxt[(a['symbol'], a['entry_ms'])] = b['entry_ms']
    out['x1'] = {'n': len(trades), 'r_lt_m1': sum(t['r'] < -1 for t in trades)}
    out['x1']['r_lt_m1_share'] = out['x1']['r_lt_m1'] / len(trades) if trades else 0.0
    for arm, ps in pairs.items():
        n = len(ps)
        inc = sum(p['status'] != COMPLETE for p in ps)
        done = [p for p in ps if p['status'] == COMPLETE]
        r_lt = sum(p['r'] < -1 for p in done)
        over = sum(p['exit_ms'] > nxt.get((p['symbol'], p['entry_ms']), math.inf) for p in done)
        status = 'NOT-EVALUABLE' if (not MD_ok or (n and inc / n > 0.05)) else 'EVALUABLE'
        diffs = []
        if status == 'EVALUABLE':
            x1r = {(t['symbol'], t['entry_ms']): t['r'] for t in trades}
            diffs = [p['r'] - x1r[(p['symbol'], p['entry_ms'])] for p in done]
        out[arm] = {'n': n, 'incomplete': inc, 'incomplete_share': inc / n if n else 0.0, 'status': status,
                    'r_lt_m1': r_lt, 'r_lt_m1_share': r_lt / len(done) if done else 0.0, 'gross_overlap': over,
                    'paired_mean_diff': math.fsum(diffs) / len(diffs) if diffs else None}
    return out
