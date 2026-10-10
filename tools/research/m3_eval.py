"""M3 reproduction evaluator (RES-01 R4-0, Codex 6094780810): the M3 canary book + `trend_ema_mom.v1`, run ONLY in
the `pit.evaluate` sandbox (`zb-eval-sandbox/1`) over a reproduction-only dataset, never in the harness process.

Entry points for `pit.evaluate(window, <this file>, function, times, summary='summary')`:
  m3_base(view) / m3_2x(view)   one cycle of the M3 book at `view.t` (the core-8 book, M3 `base` / `2x` cost row)
  summary(outputs)              the trade list and counts of the whole schedule (the attested `results` payload)
A fixture evaluator (tests) builds its own `Machine` with its own symbols / rules / book.

Cycle at bar close T (the M3 BookRunner order, origin 45c22df newcore/runner/book.py, sizing.py, risk/book.py,
adapters/fake_venue.py, runner/outcome.py; kill OFF = the rule evaluation):
  (A) fills of the decisions taken at T - 4h, at the open of the bar closing at T: exits first (taker, x (1 - slip)),
      then ONE equity snapshot (wallet after those exits), then entries in symbol order, each gated (one lot per
      symbol, Cairo-day halt of the decision day, max open lots) and sized: qty = min(risk x snapshot / dist,
      (lev x snapshot - open notional) / (signal close x (1 + gap buffer))) floored to the step; below min qty / min
      notional at the signal close = refusal; fill at open x (1 + slip), taker fee; stop = fill - dist floored to the
      tick; the position capability is issued here with `view.enter` (membership checked at the fill time).
  (B) the bar closing at T is played: LEGACY flat funding (rate x qty x that bar's close) on every lot open at its
      start, then the stop (opens through it -> fill at the open, else low <= stop -> fill at the stop; x (1 - slip),
      taker fee).
  (C) decision at T: MTM = wallet + open lots at the closes; Cairo-day roll / 3% halt; signals of `trend_ema_mom` over
      the last `window` closed bars available at T, queued for T + 4h.

State and the perturbation re-run. The book is path-dependent, so the evaluator carries state between decisions of
ONE sandbox process. State only ever derives from earlier views (rows available at those times). `pit.evaluate`
calls each decision time twice (the second time with every not-yet-available row perturbed) and replays the whole
schedule in a second fresh process: a repeated time recomputes from the state BEFORE that time and discards its new
state, so a repeat is a pure function of (prior state, view at T) and must match; a time going backwards is refused.
Bars are cached incrementally (the last `window` closed bars available at T, exactly what a full read returns; any
discontinuity triggers a full re-read), so the IPC per decision stays small.

Reproduction-only guard (R4-0 item 4, Codex 6080124561 item 2): the legacy flat-funding adapter (funding charged on
each bar close at a flat per-bar rate) refuses to run unless the dataset carries the REPRO-ONLY label
(`legacy-m3-repro-v1`), so it can never produce a primary result. Primary research funding is the actual timestamped
funding of `costs.py`.

Cairo calendar days come from `CAIRO_OFFSETS` (the sandbox has no tz database: `zoneinfo` needs site-packages);
the table is checked against `zoneinfo` Africa/Cairo hour by hour in the tests. Stdlib only.
"""
from __future__ import annotations

from collections import namedtuple
from dataclasses import dataclass
from decimal import ROUND_CEILING, ROUND_DOWN, ROUND_FLOOR, Context, Decimal

import trend_ema_mom as T

REPRO_LABEL = 'REPRO-ONLY'
CORE8 = ('BTCUSDT', 'ETHUSDT', 'SOLUSDT', 'BNBUSDT', 'XRPUSDT', 'DOGEUSDT', 'AVAXUSDT', 'LINKUSDT')
ZERO, ONE = Decimal(0), Decimal(1)
X = Context(prec=80)                               # exact for every product / sum the book forms
RC = Context(prec=34)                              # the M3 RCTX (R, halt ratio)
SC = Context(prec=34, rounding=ROUND_DOWN)         # the M3 sizing context
DAY_MS = 86_400_000
# Africa/Cairo UTC offsets: (first UTC ms the offset applies, offset seconds); 2019-01-01 .. 2031-01-01.
CAIRO_OFFSETS = ((1546300800000, 7200), (1682632800000, 10800), (1698354000000, 7200), (1714082400000, 10800),
                 (1730408400000, 7200), (1745532000000, 10800), (1761858000000, 7200), (1776981600000, 10800),
                 (1793307600000, 7200), (1809036000000, 10800), (1824757200000, 7200), (1840485600000, 10800),
                 (1856206800000, 7200), (1871935200000, 10800), (1887656400000, 7200), (1903384800000, 10800),
                 (1919710800000, 7200))
CAIRO_END_MS = 1924992000000                       # 2031-01-01T00:00:00Z


class ReproOnlyError(RuntimeError):
    pass


@dataclass(frozen=True)
class Book:
    """The canary book limits of the M3 run (BookPolicy defaults, kill disarmed for the rule evaluation)."""
    equity0: Decimal = Decimal('10000')
    risk_pct: Decimal = Decimal('0.01')
    max_positions: int = 4
    max_leverage: Decimal = Decimal('3')
    cap_gap_buffer: Decimal = Decimal('0.10')
    daily_loss_pct: Decimal = Decimal('0.03')


@dataclass(frozen=True)
class CostRow:
    name: str
    taker: Decimal
    slip: Decimal
    funding_per_bar: Decimal


@dataclass(frozen=True)
class Rule:
    tick: Decimal
    step: Decimal
    min_qty: Decimal
    min_notional: Decimal


# M3 cost rows (checked against the R3 constants / stress multipliers by m3_repro.cost_row in the tests).
COSTS = {'base': CostRow('base', Decimal('0.0005'), Decimal('0.0002'), Decimal('0.00005')),
         '2x': CostRow('2x', Decimal('0.001'), Decimal('0.0004'), Decimal('0.00005'))}
# Core-8 rules of the pinned M3 snapshot data/exchange_rules_testnet.json (sha256 d1ef2a9c...; equality checked).
CORE8_RULES = {
    'BTCUSDT': Rule(Decimal('0.1'), Decimal('0.0001'), Decimal('0.0001'), Decimal('50')),
    'ETHUSDT': Rule(Decimal('0.01'), Decimal('0.001'), Decimal('0.001'), Decimal('20')),
    'SOLUSDT': Rule(Decimal('0.01'), Decimal('0.01'), Decimal('0.01'), Decimal('5')),
    'BNBUSDT': Rule(Decimal('0.01'), Decimal('0.01'), Decimal('0.01'), Decimal('5')),
    'XRPUSDT': Rule(Decimal('0.0001'), Decimal('0.1'), Decimal('0.1'), Decimal('5')),
    'DOGEUSDT': Rule(Decimal('0.00001'), Decimal('1'), Decimal('1'), Decimal('5')),
    'AVAXUSDT': Rule(Decimal('0.001'), Decimal('1'), Decimal('1'), Decimal('5')),
    'LINKUSDT': Rule(Decimal('0.001'), Decimal('0.01'), Decimal('0.01'), Decimal('5')),
}

Lot = namedtuple('Lot', 'symbol signal_close_ms entry_ms qty entry_price stop_price fees funding position')


def q_down(x: Decimal, unit: Decimal) -> Decimal:
    return X.multiply(X.divide(x, unit).to_integral_value(rounding=ROUND_FLOOR), unit)


def q_up(x: Decimal, unit: Decimal) -> Decimal:
    return X.multiply(X.divide(x, unit).to_integral_value(rounding=ROUND_CEILING), unit)


def dec(x: float) -> Decimal:
    return Decimal(repr(float(x)))


def cairo_day(ms: int) -> str:
    """The Africa/Cairo calendar date of a UTC ms instant (table-driven; refuses outside the table)."""
    if not CAIRO_OFFSETS[0][0] <= ms < CAIRO_END_MS:
        raise ValueError('instant outside the embedded Africa/Cairo offset table')
    off = 0
    for start, o in CAIRO_OFFSETS:
        if start > ms:
            break
        off = o
    days = (ms + off * 1000) // DAY_MS                 # days since 1970-01-01 local
    # civil date from days (Howard Hinnant's algorithm)
    z = days + 719468
    era = z // 146097
    doe = z - era * 146097
    yoe = (doe - doe // 1460 + doe // 36524 - doe // 146096) // 365
    y = yoe + era * 400
    doy = doe - (365 * yoe + yoe // 4 - yoe // 100)
    mp = (5 * doy + 2) // 153
    d = doy - (153 * mp + 2) // 5 + 1
    m = mp + 3 if mp < 10 else mp - 9
    return f'{y + (m <= 2):04d}-{m:02d}-{d:02d}'


def _trade(lot, px, at_ms, reason, exit_sig, fee):
    gross = X.multiply(X.subtract(px, lot.entry_price), lot.qty)
    fees = X.add(lot.fees, fee)
    pnl = X.subtract(X.subtract(gross, fees), lot.funding)
    risk = X.multiply(lot.qty, abs(X.subtract(lot.entry_price, lot.stop_price)))
    r = RC.divide(pnl, risk) if risk > 0 else ZERO
    return {'symbol': lot.symbol, 'side': 'LONG', 'signal_close_ms': lot.signal_close_ms, 'entry_ms': lot.entry_ms,
            'exit_ms': at_ms, 'exit_signal_close_ms': exit_sig, 'qty': str(lot.qty),
            'entry_price': str(lot.entry_price), 'exit_price': str(px), 'stop_price': str(lot.stop_price),
            'risk_usd': str(risk), 'gross': str(gross), 'fees': str(fees), 'funding': str(lot.funding),
            'pnl': str(pnl), 'r': str(r), 'exit_reason': reason}


class Machine:
    """The M3 book as a deterministic state machine over sandbox views (see the module doc)."""

    def __init__(self, *, symbols, rules: dict, costs: CostRow, book: Book = Book(), params: T.Params = T.PRIMARY,
                 window: int = T.WINDOW):
        self.symbols, self.rules, self.costs, self.book = tuple(symbols), dict(rules), costs, book
        self.params, self.window = params, window
        self._last_t = None
        self._prev = self._cur = None

    def _initial(self) -> dict:
        return {'wallet': self.book.equity0, 'lots': {}, 'closes': {}, 'cache': {}, 'pend_exit': (),
                'pend_entry': (), 'day': None, 'day_start': None, 'halted_day': None}

    def decide(self, view):
        if REPRO_LABEL not in tuple(view.labels):
            raise ReproOnlyError('the M3 legacy flat-funding adapter runs only on a REPRO-ONLY dataset '
                                 '(legacy-m3-repro-v1); it can never produce a primary result')
        t = view.t
        if self._last_t is None or t > self._last_t:
            base = self._cur if self._cur is not None else self._initial()
            out, st = self._step(base, view)
            self._prev, self._cur, self._last_t = base, st, t
            return out
        if t == self._last_t:                       # the perturbation re-run: same prior state, new state discarded
            return self._step(self._prev, view)[0]
        raise ValueError('decision times must be non-decreasing within one sandbox process')

    # ------------------------------------------------------------------ bars (incremental, exact)
    def _bars(self, view, s, pos, cache):
        have = cache.get(s, ())
        if have:
            new = view.bars(s, T.TF, 2, position=pos)
            add = tuple(b for b in new if b.open_ms > have[-1].open_ms)
            if len(add) < len(new):                 # overlaps the cache: nothing was skipped
                have = (have + add)[-self.window:]
                cache[s] = have
                return have
        have = tuple(view.bars(s, T.TF, self.window, position=pos))
        cache[s] = have
        return have

    def _step(self, base: dict, view):
        st = dict(base, lots=dict(base['lots']), closes=dict(base['closes']), cache=dict(base['cache']))
        lots, closes, cache, costs, book = st['lots'], st['closes'], st['cache'], self.costs, self.book
        t, iv = view.t, T.TF_MS
        trades, refusals, halt = [], [], None
        members = view.members()
        series = {}
        for s in self.symbols:
            pos = lots[s].position if s in lots else None
            if pos is None and s not in members:
                continue
            series[s] = self._bars(view, s, pos, cache)
        bars = {s: (b[-1] if b and b[-1].available_ms == t else None) for s, b in series.items()}

        def close_lot(lot, px, at_ms, reason, exit_sig):
            fee = X.multiply(X.multiply(lot.qty, px), costs.taker)
            gross = X.multiply(X.subtract(px, lot.entry_price), lot.qty)
            st['wallet'] = X.subtract(X.add(st['wallet'], gross), fee)
            trades.append(_trade(lot, px, at_ms, reason, exit_sig, fee))
            del lots[lot.symbol]

        # (A) fills of the decisions taken at t - 4h
        for s, sig_ms in st['pend_exit']:
            lot, b = lots.get(s), bars.get(s)
            if lot is None:
                continue
            if b is None:
                refusals.append('exit_no_next_open')
                continue
            close_lot(lot, X.multiply(dec(b.open), X.subtract(ONE, costs.slip)), b.open_ms, 'exit.exit_signal', sig_ms)
        snapshot = st['wallet']
        for s, sig_ms, dist, ref, halted in st['pend_entry']:
            dist, ref = Decimal(dist), Decimal(ref)
            if s in lots:
                refusals.append('capacity.in_trade')
                continue
            if halted:
                refusals.append('filter.halt')
                continue
            if len(lots) >= book.max_positions:
                refusals.append('capacity.max_positions')
                continue
            b, rule = bars.get(s), self.rules[s]
            notional = ZERO
            for x in lots.values():
                notional = X.add(notional, X.multiply(x.qty, x.entry_price if x.entry_ms == t - iv
                                                      else closes.get(x.symbol, x.entry_price)))
            if snapshot <= 0 or dist <= 0 or ref <= 0:
                refusals.append('execution.size_min')
                continue
            raw = SC.divide(SC.multiply(snapshot, book.risk_pct), dist)
            room = SC.subtract(SC.multiply(book.max_leverage, snapshot), notional)
            cap = SC.divide(max(room, ZERO), SC.multiply(ref, SC.add(ONE, book.cap_gap_buffer)))
            q = min(raw, cap)
            q = q_down(q, rule.step) if q > 0 else ZERO
            if q <= 0 or q < rule.min_qty or SC.multiply(q, ref) < rule.min_notional:
                refusals.append('execution.size_min')
                continue
            if b is None:
                refusals.append('entry_no_next_open')
                continue
            px = X.multiply(dec(b.open), X.add(ONE, costs.slip))
            fee = X.multiply(X.multiply(q, px), costs.taker)
            st['wallet'] = X.subtract(st['wallet'], fee)
            pos = view.enter(s, f'{T.RULE_ID}:{s}:{sig_ms}')
            lots[s] = Lot(s, sig_ms, b.open_ms, q, px, q_down(X.subtract(px, dist), rule.tick), fee, ZERO, pos)
        # (B) play the bar closing at t: legacy flat funding on lots open at its start, then the stop
        for s in sorted(lots):
            lot, b = lots[s], bars.get(s)
            if b is None:
                continue
            amt = X.multiply(X.multiply(lot.qty, dec(b.close)), costs.funding_per_bar)
            st['wallet'] = X.subtract(st['wallet'], amt)
            lot = lots[s] = lot._replace(funding=X.add(lot.funding, amt))
            o, lo = dec(b.open), dec(b.low)
            hit = o if o <= lot.stop_price else (lot.stop_price if lo <= lot.stop_price else None)
            if hit is not None:
                close_lot(lot, X.multiply(hit, X.subtract(ONE, costs.slip)), b.open_ms, 'exit.stop', None)
        for s, b in bars.items():
            if b is not None:
                closes[s] = dec(b.close)
        # (C) decision at t
        mtm = st['wallet']
        for x in lots.values():
            mtm = X.add(mtm, X.multiply(X.subtract(closes.get(x.symbol, x.entry_price), x.entry_price), x.qty))
        d = cairo_day(t)
        if d != st['day']:
            st['day'], st['day_start'] = d, mtm
        if st['halted_day'] != d and st['day_start'] > 0 and \
                RC.subtract(RC.divide(mtm, st['day_start']), ONE) <= -book.daily_loss_pct:
            st['halted_day'] = halt = d
        pend_exit, pend_entry = [], []
        for s in self.symbols:
            b = series.get(s)
            if not b or b[-1].available_ms != t:
                continue                                    # no bar closing at t: no decision (never stale)
            sig = T.signal_at_last(T.contiguous_tail(b), self.params)
            if sig is None:
                continue
            le, lx, atr = sig
            if lx and s in lots:
                pend_exit.append((s, t))
            if le:
                pend_entry.append((s, t, str(T.stop_distance(atr, self.params)), repr(b[-1].close),
                                   st['halted_day'] == d))
        st['pend_exit'], st['pend_entry'] = tuple(pend_exit), tuple(pend_entry)
        out = {'t': t, 'trades': trades, 'refusals': refusals, 'halt': halt, 'open': len(lots),
               'wallet': str(st['wallet']), 'mtm': str(mtm)}
        return out, st


def summary(outputs) -> dict:
    """The whole schedule: every closed trade (entry order), refusal counts, halts, end state."""
    trades = sorted((tr for o in outputs for tr in o['trades']), key=lambda r: (r['entry_ms'], r['symbol']))
    ref = {}
    for o in outputs:
        for k in o['refusals']:
            ref[k] = ref.get(k, 0) + 1
    last = outputs[-1] if outputs else {'open': 0, 'wallet': None}
    return {'trades': trades, 'refusals': dict(sorted(ref.items())), 'halts': [o['halt'] for o in outputs if o['halt']],
            'cycles': len(outputs), 'open_lots_at_end': last['open'], 'wallet_end': last['wallet']}


_BASE = Machine(symbols=CORE8, rules=CORE8_RULES, costs=COSTS['base'])
_X2 = Machine(symbols=CORE8, rules=CORE8_RULES, costs=COSTS['2x'])


def m3_base(view):
    return _BASE.decide(view)


def m3_2x(view):
    return _X2.decide(view)
