"""STRAT-01 R4b-PIT: the real point-in-time runner of `trend_ema_mom.v1.cap180` (Codex rulings 6108364741 sections
1-4; prereg research_evidence/prereg/trend_ema_mom.v1.json; entry point `run_r4.py pit`).

ONE immutable path, CORE-10 then CORE-20 (the owner-approved static-40 artifact's frozen ranks; EXT-21-40 is a later
generalization report and is not run here). Both tiers use exactly the same code, run config, dataset pins and cost
rows; only the tier's symbol list differs. No retuning: the configurations are the registered ledger trials
(capped primary, uncapped development-only variant, the 12 one-at-a-time neighbours) and baselines B0-B3.

Order of operations (`run_pit`):
  1. the R4 gate (run_r4.gate: G1 clean tree, G2 prereg committed + valid, G3 registered in the ledger, G4 pins,
     G5 static universe) - any failure refuses before a byte of data is read;
  2. the environment is derived from the gate's committed files only (prereg splits / costs, the G5-validated static
     universe tiers, the manifest pinned by the prereg digest);
  3. per tier, per DEVELOPMENT split (train, walk_forward[0..4]; calibration is excluded from all evidence and the
     holdout can never be opened: `dev_window` refuses it, and any window touching the holdout start or beyond):
     a ledger-recorded `pit.Access.open` window over a `StaticDataset` (the manifest restricted to the tier's frozen
     members, labelled CURRENT-UNIVERSE / SURVIVOR-BIASED), and `pit.evaluate` of `pit_eval.tape` (sandboxed,
     perturbation-checked, two fresh processes, attestation in the family ledger);
  4. accounting from the attested tape (`account` -> `run_book`): the registered SHARED PORTFOLIO BOOK per
     configuration and stress row, one chronological cycle loop over the tier's symbols (m3_eval order): (A) exits
     due at the bar open (signal exits; the 180-bar cap: a lot open 180 bars after entry closes at the OPEN of the
     next bar, market taker), one wallet snapshot, entries in frozen rank order - 10,000 USDT start, risk 1% of the
     snapshot / the actual per-unit stop risk (tick-rounded fill - tick-floored stop), max 4 open lots, gross notional <= 3x equity sized on signal close x 1.10, quantity
     floored to the pinned rule step (never upsized), below min qty / min notional = counted refusal, a Cairo-day
     halted decision = counted refusal; fill at the next bar open (tick-rounded), stop = fill - registered distance
     floored to the tick; (B) stop over each played bar (`costs.stop_reference`: a gap stop fills and exits at the
     open; a non-gap stop fills at the stop and exits at the bar close, so the bar's funding is charged), funding at
     each actual funding time x mark proxy; (C) MTM at the closes, Cairo-day 3% MTM halt, drawdown kill disarmed.
     The bar opening at the split end is served by the causal `View.next_open` capability (its open only, never its
     H/L/C), so a cap or signal exit due exactly at the split end fills there; with no such bar it is INCOMPLETE,
     never a close. Costs: the registered `zb-cost-model/2` crypto row via costs.py (VIP0 taker, slip-v1 TR-SMA14 with
     c = slip-cal-v1 crypto, signed actual funding), every `costs.STRESS` row running its own book. Metrics are net %
     equity, MTM max drawdown, longest underwater and an episode-bootstrap MC drawdown p5/p50/p95; per-trade R
     (pnl / qty x initial stop risk) and MFE/MAE (stop bars bounded conservatively: earlier highs + the stop bar's
     open) are diagnostics. B1 keeps its registered unit-R definition (`account_unit`);
  5. INCOMPLETE, never counted: a lot meeting a missing bar (gap), a funding sequence that is not provably complete or
     lacks a mark, or a lot still open at the split end is INCOMPLETE; that (configuration, split, symbol) is excluded
     and the book is re-run without it until stable, with counts reported (never silently);
  6. aggregate metrics, paired baselines (B0 episode-paired matched random entries - same symbol, side, split and
     registered PIT regime label (BTC 4h close vs its EMA200, frozen in the attested tape), B1 weekly equal-weight hold and
     B2 20-bar momentum on calendar-month blocks, B3 cash = the expectancy row; one-sided sign-flip permutation test,
     Holm over the family ledger's n_trials) and the mechanical EDGE-00 / verdict rows of the prereg.
     Development evidence can neither PROMOTE nor REJECT (prereg verdict_rules.development): the verdict is PARK, with
     DEV-NEGATIVE flagged when the base-row development mean net R <= 0;
  7. per tier: an atomic progress snapshot (progress.py, operational state only), then the sealed artifacts written
     atomically (temp + os.replace) and the immutable final record (write-once, digest). CORE-10 is sealed before
     CORE-20 starts; separate evidence dirs and finals.

Interrupted runs: a tier dir holding a progress snapshot without a final refuses unless `resume=True`; a resume
re-verifies the identity (another identity / config in the dir refuses) and re-runs that tier from its first split
(deterministic), keeping a sealed CORE-10 after re-checking its final and artifact hashes. An integrity failure is
recorded as an aborted final (terminal: a new evidence dir is needed).

Disclosure (Codex 6108364741 item 3): `summary-<run>.json` carries the labels, aggregate metrics, the neighbour
sign-keep share, baseline p-values and the verdict rows only - no symbol names or rankings, no raw trades, no
neighbour-level results. Those live in `detail-<run>.json`, sealed by hash and not for disclosure before both tiers
are sealed and independently reviewed.
"""
from __future__ import annotations

import bisect
import glob
import hashlib
import json
import math
import os
import random
import subprocess
import sys
from collections import Counter
from decimal import ROUND_CEILING, Decimal

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import costs as C                                                                           # noqa: E402
import ledger as L                                                                          # noqa: E402
import m3_eval as E                                                                         # noqa: E402
import m3_repro as X                                                                        # noqa: E402
import manifest as M                                                                        # noqa: E402
import pit as P                                                                             # noqa: E402
import pit_eval as PE                                                                       # noqa: E402
import progress as PG                                                                       # noqa: E402
import splits as S                                                                          # noqa: E402
import static_universe as SU                                                                # noqa: E402
import trend_ema_mom as T                                                                   # noqa: E402

SUMMARY_FORMAT = 'zb-strat01-pit-summary/1'
DETAIL_FORMAT = 'zb-strat01-pit-detail/1'
LABEL = SU.LABEL
DEV_LABEL = 'DEVELOPMENT-ONLY'
RUN_TIERS = ('CORE-10', 'CORE-20')                 # EXT-21-40 is a later generalization report (Codex 6108364741)
PRIMARY_NAME = 'trend_ema_mom.v1.cap180'
DEV_ONLY = 'trend_ema_mom.v1.uncapped'
CAP = T.TIME_CAP_BARS
SEED = 20261009                                    # prereg baselines.B0 / p_value seed
RESAMPLES = 10_000                                 # prereg: episode-clustered bootstrap
PERMS = 10_000                                     # prereg: sign-flip permutations
B0_DRAWS = 1_000                                   # prereg: matched random entries per candidate trade
EVALUATOR = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'pit_eval.py')
FUNCTION = 'tape'
# STRAT-02 `trend_ema_mom_short.v1` (docs/newcore/research/STRAT02_SHORT_CONTRACT.md, frozen at PR #71): the short-side
# symmetry slice only. Long (STRAT-01) behaviour is the default of every function and is unchanged.
SHORT_FUNCTION = 'tape_short'
SHORT_PRIMARY_NAME = 'trend_ema_mom_short.v1.cap180'
SHORT_SEED = 20261012                              # STRAT-02 contract section 9 seeds (B0, bootstrap, permutations)
SIDES = ('long', 'short')
MANIFEST_PATH = 'research_evidence/manifests/binance-um-archive-v1.json.gz'
TF = T.TF_MS
HOUR = 3_600_000
DAY = 86_400_000
WEEK_BARS = 7 * DAY // TF
FUND_SPAN = (C.MAX_FUNDING_INTERVAL_H + 1) * HOUR
BASE = C.STRESS['base']
EXCLUDED_SPLITS = ('calibration', 'holdout')       # calibration: no strategy evaluation; holdout: sealed
NOT_EVALUATED = (
    'follower tiers 100 / 200 / 500 USDT and RULES-BACKFILLED share (E11)',
    'per-side split (long only rule; the short mirror is never an edge claim)',
    'stop-first vs target-first (one stop, no target: every exit bar is single-level)',
    'MDE at 80% power (holdout-only quantity)')
# Exchange lot rules of the registered book: ONE complete Binance USD-M MAINNET snapshot (direct public exchangeInfo
# fetch, Codex ruling 6110459316), pinned by its exact bytes here only. The M3 reproduction keeps its own testnet anchor
# (data/exchange_rules_testnet.json, m3_repro.RULES_SHA256) untouched; the two are never mixed.
RULES_PATH = 'data/exchange_rules_mainnet.json'
RULES_SHA256 = 'a34ec71130683101183cd6039fcdd37096457544ccb9c07bbcdaa09d90680aa3'
RULES_ENV = 'mainnet'
RULES_FIELDS = ('tick', 'step', 'min_qty', 'min_notional')


class PitRunError(ValueError):
    pass


class Incomplete(Exception):
    def __init__(self, reason):
        super().__init__(reason)
        self.reason = reason


def canon(obj) -> bytes:
    return json.dumps(obj, sort_keys=True, separators=(',', ':'), ensure_ascii=True, allow_nan=False).encode('ascii')


def sha(obj) -> str:
    return hashlib.sha256(canon(obj)).hexdigest()


# ------------------------------------------------------------------ the static-universe dataset (pit.Dataset)
class StaticDataset(P.Dataset):
    """`pit.Dataset` over a promotion-eligible manifest, serving exactly one tier of the owner-approved static list as
    members at every time (contract section 0.1: no PIT membership reconstruction), labelled CURRENT-UNIVERSE /
    SURVIVOR-BIASED. Every row is still read through the manifest's SHA-256 re-check (`Dataset.rows`)."""

    def __init__(self, manifest: dict, store: str, static: dict, symbols):
        M.validate(manifest)
        if not M.promotion_eligible(manifest):
            raise PitRunError('a reproduction-only manifest can never feed the PIT run')
        if static.get('format') != SU.FORMAT or static.get('label') != LABEL or static.get('owner_approved') is not True:
            raise PitRunError('the static universe must be the owner-approved CURRENT-UNIVERSE / SURVIVOR-BIASED list')
        ranked = [s['symbol'] for s in static['symbols']]
        symbols = list(symbols)
        if not symbols or symbols != ranked[:len(symbols)]:
            raise PitRunError('a tier must be a rank prefix of the static list (frozen ranks, never a selection)')
        self.manifest, self.store, self.digest = manifest, store, manifest['digest']
        self.archive = manifest['loader_version'] == M.ARCHIVE_LOADER
        self.universe_digest, self.labels, self.books = static['digest'], (LABEL,), (SU.BOOK,)
        self._mondays, self._members = None, None
        self._all = frozenset(symbols)
        self.symbol_class = {s: ('crypto', None) for s in symbols}
        self._files: dict[tuple, list] = {}
        for f in manifest['files']:
            if f['symbol'] not in self._all:
                continue
            key = (f.get('series', 'klines'), f['symbol'], f['interval'])
            self._files.setdefault(key, []).append(f)
        for key, v in self._files.items():
            v.sort(key=lambda f: (f['first_open_ms'] is None, f['first_open_ms'] or 0))
            P._refuse_overlap(key, v)
        self._cache: dict[str, list] = {}
        self._keys: dict[str, tuple] = {}


# ------------------------------------------------------------------ holdout guard
def holdout_start(plan: S.SplitPlan) -> int:
    return plan.access_range(plan.holdout_key())[0]


def dev_keys(plan: S.SplitPlan) -> list[str]:
    """The development splits evaluated, in time order: every split except calibration and the holdout."""
    return [k for k in plan.keys() if plan.split(k)['name'] not in EXCLUDED_SPLITS]


def dev_window(plan: S.SplitPlan, key: str) -> tuple[int, int]:
    """[lo, hi] of a development split; refuses the holdout, calibration and anything touching the holdout start."""
    s = plan.split(key)
    if s['name'] in EXCLUDED_SPLITS:
        raise PitRunError(f'{key}: the PIT runner opens development splits only (never {s["name"]})')
    lo, hi = plan.access_range(key)
    if plan.overlaps_holdout(lo, hi) or hi > holdout_start(plan):
        raise PitRunError(f'{key}: window reaches the sealed holdout ({S.utc(holdout_start(plan))})')
    return lo, hi


# ------------------------------------------------------------------ configurations (registered trials only)
def configs() -> list[dict]:
    out = [{'name': PRIMARY_NAME, 'kind': 'sig', 'k': 0, 'cap': CAP, 'stop': True, 'role': 'primary'},
           {'name': DEV_ONLY, 'kind': 'sig', 'k': 0, 'cap': None, 'stop': True, 'role': 'development_only'}]
    out += [{'name': n, 'kind': 'sig', 'k': i + 1, 'cap': CAP, 'stop': True, 'role': 'neighbour'}
            for i, (n, _) in enumerate(T.neighbours())]
    out += [{'name': 'B1', 'kind': 'b1', 'k': None, 'cap': WEEK_BARS, 'stop': False, 'role': 'baseline'},
            {'name': 'B2', 'kind': 'b2', 'k': None, 'cap': CAP, 'stop': True, 'role': 'baseline'}]
    if [n for n, _ in PE.SIGNAL_CONFIGS[1:]] != [c['name'] for c in out if c['role'] == 'neighbour']:
        raise PitRunError('the evaluator signal configurations differ from the registered neighbours')
    return out


# ------------------------------------------------------------------ the attested tape
class Sym:
    """One symbol's tape: closed bars (by close time), funding rows and their proxy marks."""

    def __init__(self, s):
        self.s, self.bars, self.avail, self.rec = s, [], [], {}
        self.fund, self.mark = [], {}
        self._bps = {}

    def add(self, t, rec):
        if 'b' in rec:
            o, h, lo, c = rec['b']
            self.bars.append(P.Bar(t - TF, o, h, lo, c, None, None, t))
            self.avail.append(t)
            self.rec[t] = rec
        for tm, rate, ih, mk in rec['f']:
            self.fund.append(P.Funding(tm, rate, ih, tm))
            self.mark[tm] = mk

    def bps(self, at_ms, vol, c):
        """slip-v1 bps from the bars closed at or before `at_ms` (TR-SMA14: last 15; Wilder: the read window)."""
        key = (at_ms, vol)
        if key not in self._bps:
            k = bisect.bisect_right(self.avail, at_ms)
            n = C.SLIP_BARS if vol == C.TR_SMA14 else T.WINDOW
            try:
                self._bps[key] = C.slip_bps(self.bars[max(0, k - n):k], c, vol)
            except C.CostError:
                self._bps[key] = None
        if self._bps[key] is None:
            raise Incomplete('incomplete.slip_history')
        return self._bps[key]

    def events(self, entry_ms, exit_ms):
        rows = [f for f in self.fund if entry_ms - FUND_SPAN <= f.time_ms < exit_ms]
        try:
            C.check_funding_sequence(self.s, rows, start_ms=entry_ms, end_ms=exit_ms)
        except C.CostError:
            raise Incomplete('incomplete.funding')
        ev = [(f.time_ms, f.rate, self.mark[f.time_ms]) for f in rows if f.time_ms >= entry_ms]
        if any(m is None for _, _, m in ev):
            raise Incomplete('incomplete.funding_mark')
        return ev


def read_tape(outputs) -> tuple[list[int], dict]:
    times, syms = [], {}
    for o in outputs:
        times.append(o['t'])
        for rec in o['r']:
            syms.setdefault(rec['s'], Sym(rec['s'])).add(o['t'], rec)
    if any(b <= a for a, b in zip(times, times[1:])):
        raise PitRunError('tape times must be strictly increasing')
    for x in syms.values():
        x.fund.sort(key=lambda f: f.time_ms)
    return times, syms


def net_r(x: Sym, *, entry_ref, entry_ms, dist, exit_ref, exit_ms, gap, exit_bps_at, nbars, slip_c, rows,
          side='long', tick=None):
    """Net R per stress row of one unit-quantity trade on a fixed path (diagnostics, B0 and B1 only). `side='short'`
    (STRAT-02): sell entry rounded DOWN / buy cover rounded UP to `tick`, PnL = entry - exit, short funding sign."""
    ev = x.events(entry_ms, exit_ms)
    out = {}
    if side == 'short':
        for name in rows:
            scen = C.STRESS[name]
            ef = C.fill_price(entry_ref, 'sell', x.bps(entry_ms, scen.vol, slip_c), scen, tick=tick)
            xf = C.fill_price(exit_ref, 'buy', x.bps(exit_bps_at, scen.vol, slip_c), scen, gap=gap, tick=tick)
            fees = C.fee(ef, 'taker', C.CRYPTO, scen) + C.fee(xf, 'taker', C.CRYPTO, scen)
            fund = C.funding_cost('short', 1.0, ev, scen, bars_held=nbars, entry_notional=ef)
            out[name] = (ef - xf - fees - fund) / dist
        return out
    if side != 'long':
        raise PitRunError("side must be 'long' or 'short'")
    for name in rows:
        scen = C.STRESS[name]
        ef = C.fill_price(entry_ref, 'buy', x.bps(entry_ms, scen.vol, slip_c), scen)
        xf = C.fill_price(exit_ref, 'sell', x.bps(exit_bps_at, scen.vol, slip_c), scen, gap=gap)
        fees = C.fee(ef, 'taker', C.CRYPTO, scen) + C.fee(xf, 'taker', C.CRYPTO, scen)
        fund = C.funding_cost('long', 1.0, ev, scen, bars_held=nbars, entry_notional=ef)
        out[name] = (xf - ef - fees - fund) / dist
    return out


def _signal(cfg, x: Sym, rec, t, side='long'):
    """(le, lx, stop distance | None) of a configuration at t from the tape (short: se, sx; B2 short = close below the
    close 20 bars earlier)."""
    if cfg['kind'] == 'sig':
        k = cfg['k']
        if rec['w'][k] != '1':
            return False, False, None
        d = rec['e'].get(str(k))
        return d is not None, rec['x'][k] == '1', None if d is None else float(Decimal(d))
    d = None if rec['d'] is None else float(Decimal(rec['d']))
    if cfg['kind'] == 'b1' and side == 'short':
        raise PitRunError('B1 short is the perp short book (b1_short_book), never a signal configuration')
    if cfg['kind'] == 'b1':
        monday = t % DAY == 0 and (t // DAY - 4) % 7 == 0
        return monday and d is not None, False, d
    back = x.rec.get(t - 20 * TF)                      # B2: close > close 20 bars earlier (contiguous bars only)
    if back is None or any((t - j * TF) not in x.rec for j in range(1, 20)):
        return False, True, d
    if side == 'short':                                # B2 short: close < close 20 bars earlier, cover when not
        down = rec['b'][3] < back['b'][3]
        return down and d is not None, not down, d
    up = rec['b'][3] > back['b'][3]
    return up and d is not None, not up, d


def _next_open(x: Sym, at_ms: int, hi: int):
    """The open of the bar opening at `at_ms`: the next tape bar's open, or at the split end the attested
    `View.next_open` read (its open only). None = not observable inside the split."""
    nxt = x.rec.get(at_ms + TF)
    if nxt is not None:
        return nxt['b'][0]
    rec = x.rec.get(at_ms)
    return rec.get('n') if rec is not None and at_ms == hi else None


# ------------------------------------------------------------------ the registered book (prereg rule.book)
BOOK = {'equity0': 10_000.0, 'risk_pct': 0.01, 'max_positions': 4, 'max_leverage': 3.0, 'cap_gap_buffer': 0.10,
        'daily_loss_pct': 0.03}


def _floor(v: float, unit) -> float:
    return float(X.q_down(Decimal(repr(float(v))), Decimal(str(unit))))


def _ceil(v: float, unit) -> float:
    """STRAT-02: the short stop level CEILED to the tick (rounding can only widen the risk)."""
    u = Decimal(str(unit))
    return float((Decimal(repr(float(v))) / u).to_integral_value(rounding=ROUND_CEILING) * u)


def run_book(tape, cfg: dict, symbols, *, first: int, last: int, hi: int, slip_c: float, rules: dict, row: str = 'base',
             exclude=frozenset(), book=BOOK, side='long') -> dict:
    """ONE pass of the registered shared crypto book over a split's attested tape, one cost row (prereg rule.book,
    the M3 BookRunner order of m3_eval.Machine): cycle at bar close t (bar [t - 4h, t)):
      (A) fills at the open of that bar of the decisions taken at t - 4h: signal exits, then time-cap exits (a lot held
          180 bars exits at the open of the next bar), then ONE wallet snapshot, then entries in frozen-rank order:
          one lot per symbol, Cairo-day halt of the decision day, max open lots, qty = min(risk x snapshot / dist,
          (lev x snapshot - open notional) / (signal close x (1 + gap buffer))) floored to the step; below min qty /
          min notional = refusal; fill = open moved by slip-v1 and rounded adversely to the tick (taker fee);
          stop = fill - dist floored to the tick;
      (B) the bar is played per lot: stop (a bar opening through it fills at its open), else the actual funding events
          of the bar [t - 4h, t) at the funding-mark proxy are charged to the wallet;
      (C) decision at t: MTM = wallet + open lots at the closes; Cairo-day roll / 3% MTM halt; signals queued.
    At the split end (t == hi) a pending signal exit or a cap exit due at hi fills at the next open read through
    `View.next_open`; a lot that cannot exit inside the split, a lot meeting a missing bar, or an incomplete funding
    sequence / mark is INCOMPLETE (the caller re-runs the book without that symbol).

    `side='short'` (STRAT-02 contract sections 3-4, the exact mirror): market SELL entry rounded DOWN to the tick after
    adverse slip, stop = ceil_tick(fill + dist), actual per-unit risk stop - fill (non-finite / non-positive = refusal
    `execution.stop_risk_invalid`); a bar OPENING at or above the stop fills at its open (gap), else a HIGH reaching it
    fills at the stop; covers are BUYs rounded UP; PnL = (fill - exit) x qty; short funding sign; MFE from lows (stop
    bar: its open), MAE from highs (stop bar: bounded by the stop); MTM = wallet + qty x (fill - close); plus the
    PARK-only liquidation diagnostic (never an exit, fill or sizing input)."""
    if side not in SIDES:
        raise PitRunError("side must be 'long' or 'short'")
    short = side == 'short'
    times, syms = tape
    scen = C.STRESS[row]
    order = [s for s in symbols if s in syms and s not in exclude]
    wallet = book['equity0']
    lots, closes, trades, incomplete, refusals, series = {}, {}, [], [], Counter(), []
    pend_exit, pend_entry, day, day_start, halted_day = [], [], None, None, None
    cap_ms = None if cfg['cap'] is None else cfg['cap'] * TF
    liq = {'bars': 0, 'flagged_bars': 0, 'min_w_over_g': None, 'max_bar_high_over_open': None}

    def tick(s):
        return float(rules[s]['tick'])

    def drop(s, reason):
        incomplete.append({'symbol': s, 'entry_ms': lots[s]['entry_ms'], 'reason': reason})
        del lots[s]

    def close(s, ref, exit_ms, reason, gap, at, nbars):
        nonlocal wallet
        lot, x = lots[s], syms[s]
        try:
            x.events(lot['entry_ms'], exit_ms)                      # the whole funding sequence is provably complete
            xf = C.fill_price(ref, 'buy' if short else 'sell', x.bps(at, scen.vol, slip_c), scen, gap=gap,
                              tick=tick(s))
        except Incomplete as e:
            drop(s, e.reason)
            return
        if short:
            fee = C.fee(xf * lot['qty'], 'taker', C.CRYPTO, scen)
            gross = (lot['fill'] - xf) * lot['qty']
            wallet += gross - fee
            fees = lot['fees'] + fee
            pnl = gross - fees - lot['funding']
            risk_u = lot['stop'] - lot['fill']
            risk = lot['qty'] * risk_u
            trades.append({'config': cfg['name'], 'side': 'short', 'symbol': s, 'signal_ms': lot['signal_ms'],
                           'entry_ms': lot['entry_ms'], 'exit_ms': exit_ms, 'exit_reason': reason, 'gap': gap,
                           'bars': nbars, 'qty': lot['qty'], 'entry_price': lot['fill'], 'exit_price': xf,
                           'stop_price': lot['stop'], 'fees': fees, 'funding': lot['funding'], 'pnl': pnl,
                           'snapshot': lot['snapshot'], 'r': {row: pnl / risk if risk > 0 else 0.0},
                           'mfe_r': (lot['fill'] - lot['lo']) / risk_u, 'mae_r': (lot['fill'] - lot['hi']) / risk_u})
            del lots[s]
            return
        fee = C.fee(xf * lot['qty'], 'taker', C.CRYPTO, scen)
        gross = (xf - lot['fill']) * lot['qty']
        wallet += gross - fee
        fees = lot['fees'] + fee
        pnl = gross - fees - lot['funding']
        risk = lot['qty'] * (lot['fill'] - lot['stop'])
        trades.append({'config': cfg['name'], 'symbol': s, 'signal_ms': lot['signal_ms'], 'entry_ms': lot['entry_ms'],
                       'exit_ms': exit_ms, 'exit_reason': reason, 'gap': gap, 'bars': nbars, 'qty': lot['qty'],
                       'entry_price': lot['fill'], 'exit_price': xf, 'stop_price': lot['stop'], 'fees': fees,
                       'funding': lot['funding'], 'pnl': pnl, 'snapshot': lot['snapshot'],
                       'r': {row: pnl / risk if risk > 0 else 0.0},
                       'mfe_r': (lot['hi'] - lot['fill']) / (lot['fill'] - lot['stop']),
                       'mae_r': (lot['lo'] - lot['fill']) / (lot['fill'] - lot['stop'])})
        del lots[s]

    for t in times:
        bo = t - TF
        rec = {s: syms[s].rec.get(t) for s in order}
        for s in [s for s in lots if rec[s] is None]:
            drop(s, 'incomplete.gap')
        # (A) fills at the open of the bar [bo, t)
        for s, _ in pend_exit:
            if s in lots:
                close(s, rec[s]['b'][0], bo, 'exit.signal', False, bo, (bo - lots[s]['entry_ms']) // TF)
        pend_exit = []
        if cap_ms is not None:
            for s in [s for s in order if s in lots]:
                held = bo - lots[s]['entry_ms']
                if held > cap_ms:
                    raise PitRunError(f'{s}: lot held past its time cap')          # unreachable: every bar is played
                if held == cap_ms:
                    close(s, rec[s]['b'][0], bo, 'exit.time_cap', False, bo, cfg['cap'])
        snapshot = wallet
        for s, sig_t, dist, ref, halted in pend_entry:
            if s in lots:
                refusals['capacity.in_trade'] += 1
                continue
            if halted:
                refusals['filter.halt'] += 1
                continue
            if len(lots) >= book['max_positions']:
                refusals['capacity.max_positions'] += 1
                continue
            rule = rules[s]
            notional = math.fsum(x['qty'] * (x['fill'] if x['entry_ms'] == bo else closes.get(k, x['fill']))
                                 for k, x in lots.items())
            if short and not (dist is not None and math.isfinite(dist) and dist > 0):
                refusals['execution.stop_risk_invalid'] += 1         # a non-finite / non-positive stop risk
                continue
            if snapshot <= 0 or not dist or dist <= 0 or ref <= 0:
                refusals['execution.size_min'] += 1
                continue
            if rec[s] is None:
                refusals['entry_no_next_open'] += 1
                continue
            x = syms[s]
            if short:
                try:
                    fill = C.fill_price(rec[s]['b'][0], 'sell', x.bps(bo, scen.vol, slip_c), scen, tick=tick(s))
                except Incomplete:
                    refusals['entry_no_slip_history'] += 1
                    continue
                # actual per-unit risk from the tick-rounded (DOWN) sell fill and the CEILED stop (contract sec 4)
                stop = _ceil(fill + dist, rule['tick'])
                risk_u = stop - fill
                if not (fill > 0 and math.isfinite(stop) and math.isfinite(risk_u) and risk_u > 0):
                    refusals['execution.stop_risk_invalid'] += 1
                    continue
                raw = snapshot * book['risk_pct'] / risk_u
                room = book['max_leverage'] * snapshot - notional
                q = min(raw, max(room, 0.0) / (ref * (1 + book['cap_gap_buffer'])))
                q = _floor(q, rule['step']) if q > 0 else 0.0
                if q <= 0 or q < float(rule['min_qty']) or q * fill < float(rule['min_notional']):  # executable fill
                    refusals['execution.size_min'] += 1
                    continue
                fee = C.fee(fill * q, 'taker', C.CRYPTO, scen)
                wallet -= fee
                o = rec[s]['b'][0]
                lots[s] = {'signal_ms': sig_t, 'entry_ms': bo, 'qty': q, 'fill': fill, 'stop': stop, 'fees': fee,
                           'funding': 0.0, 'snapshot': snapshot, 'hi': o, 'lo': o}
                continue
            try:
                fill = C.fill_price(rec[s]['b'][0], 'buy', x.bps(bo, scen.vol, slip_c), scen, tick=tick(s))
            except Incomplete:
                refusals['entry_no_slip_history'] += 1
                continue
            # size from the ACTUAL per-unit stop risk: the adverse tick-rounded fill and the tick-floored stop
            # (flooring can only widen fill - stop beyond dist), so qty x (fill - stop) <= risk_pct x snapshot
            stop = _floor(fill - dist, rule['tick'])
            if stop <= 0:                                       # a long stop at/below zero can never trigger: no lot
                refusals['execution.stop_nonpositive'] += 1
                continue
            risk_u = fill - stop
            if risk_u <= 0:
                refusals['execution.size_min'] += 1
                continue
            raw = snapshot * book['risk_pct'] / risk_u
            room = book['max_leverage'] * snapshot - notional
            q = min(raw, max(room, 0.0) / (ref * (1 + book['cap_gap_buffer'])))
            q = _floor(q, rule['step']) if q > 0 else 0.0
            if q <= 0 or q < float(rule['min_qty']) or q * fill < float(rule['min_notional']):   # executable notional
                refusals['execution.size_min'] += 1
                continue
            fee = C.fee(fill * q, 'taker', C.CRYPTO, scen)
            wallet -= fee
            o = rec[s]['b'][0]
            lots[s] = {'signal_ms': sig_t, 'entry_ms': bo, 'qty': q, 'fill': fill,
                       'stop': stop, 'fees': fee, 'funding': 0.0, 'snapshot': snapshot,
                       'hi': o, 'lo': o}
        pend_entry = []
        # (B) play the bar: stop first; funding events entry <= T < exit time (accounting convention, PR #61:
        # a gap stop exits at the bar open, before the bar's events; a non-gap stop exits at the bar CLOSE t, so the
        # bar's events [bo, t) are charged first, with the fill still at the stop level)
        if short and lots:
            # PARK-only liquidation diagnostic (contract sec 4): every open short marked at its own bar high at once,
            # ignoring stops; wallet_t = the realised wallet after the fees / funding booked before this bar's events
            held = [(lots[k], rec[k]['b']) for k in order if k in lots]
            g = math.fsum(lt['qty'] * b[1] for lt, b in held)
            w = wallet + math.fsum(lt['qty'] * (lt['fill'] - b[1]) for lt, b in held)
            liq['bars'] += 1
            if w < g / 3:
                liq['flagged_bars'] += 1
            if g > 0:
                liq['min_w_over_g'] = w / g if liq['min_w_over_g'] is None else min(liq['min_w_over_g'], w / g)
            jump = max((b[1] - b[0]) / b[0] for _, b in held)
            liq['max_bar_high_over_open'] = jump if liq['max_bar_high_over_open'] is None else max(
                liq['max_bar_high_over_open'], jump)
        for s in [s for s in order if s in lots]:
            lot, (o, h, lo, c) = lots[s], rec[s]['b']
            if short:
                stop_hit = o >= lot['stop'] or h >= lot['stop']
                ref, gap = C.stop_reference('short', lot['stop'], o) if stop_hit else (None, False)
                if not (stop_hit and gap):
                    ev = [(f.time_ms, f.rate, syms[s].mark[f.time_ms]) for f in syms[s].fund
                          if bo <= f.time_ms < t and f.time_ms >= lot['entry_ms']]
                    if any(m is None for _, _, m in ev):
                        drop(s, 'incomplete.funding_mark')
                        continue
                    amt = C.funding_cost('short', lot['qty'], ev, scen, bars_held=1,
                                         entry_notional=lot['qty'] * lot['fill'])
                    wallet -= amt
                    lot['funding'] += amt
                if stop_hit:                    # MFE: earlier lows + the stop bar's OPEN; MAE: bounded by the stop
                    lot['lo'], lot['hi'] = min(lot['lo'], o), max(lot['hi'], min(h, lot['stop']))
                    close(s, ref, bo if gap else t, 'exit.stop', gap, bo, (bo - lot['entry_ms']) // TF + 1)
                    continue
                lot['hi'], lot['lo'] = max(lot['hi'], h), min(lot['lo'], lo)
                continue
            stop_hit = o <= lot['stop'] or lo <= lot['stop']
            ref, gap = C.stop_reference('long', lot['stop'], o) if stop_hit else (None, False)
            if not (stop_hit and gap):
                ev = [(f.time_ms, f.rate, syms[s].mark[f.time_ms]) for f in syms[s].fund
                      if bo <= f.time_ms < t and f.time_ms >= lot['entry_ms']]
                if any(m is None for _, _, m in ev):
                    drop(s, 'incomplete.funding_mark')
                    continue
                amt = C.funding_cost('long', lot['qty'], ev, scen, bars_held=1,
                                     entry_notional=lot['qty'] * lot['fill'])
                wallet -= amt
                lot['funding'] += amt
            if stop_hit:
                lot['hi'], lot['lo'] = max(lot['hi'], o), min(lot['lo'], lo)    # pre-stop bound: never the bar high
                close(s, ref, bo if gap else t, 'exit.stop', gap, bo, (bo - lot['entry_ms']) // TF + 1)
                continue
            lot['hi'], lot['lo'] = max(lot['hi'], h), min(lot['lo'], lo)
        for s in order:
            if rec[s] is not None:
                closes[s] = rec[s]['b'][3]
        # (C) decision at t
        if short:
            mtm = wallet + math.fsum(x['qty'] * (x['fill'] - closes.get(k, x['fill'])) for k, x in lots.items())
        else:
            mtm = wallet + math.fsum(x['qty'] * (closes.get(k, x['fill']) - x['fill']) for k, x in lots.items())
        series.append((t, mtm))
        d = E.cairo_day(t)
        if d != day:
            day, day_start = d, mtm
        if halted_day != d and day_start > 0 and mtm / day_start - 1.0 <= -book['daily_loss_pct']:
            halted_day = d
        for s in order:
            r = rec[s]
            if r is None:
                continue
            le, lx, dist = _signal(cfg, syms[s], r, t, side)
            if lx and s in lots:
                pend_exit.append((s, t))
            if le and first <= t <= last:
                pend_entry.append((s, t, dist, r['b'][3], halted_day == d))
        if t == hi:                                     # split end: fills due at hi read the next open
            sig_due = {s for s, _ in pend_exit if s in lots}
            due = set(sig_due)
            if cap_ms is not None:
                due |= {s for s in lots if lots[s]['entry_ms'] + cap_ms == hi}
            for s in [s for s in order if s in due]:
                n = _next_open(syms[s], hi, hi)
                if n is None:
                    drop(s, 'incomplete.split_end')
                else:
                    close(s, n, hi, 'exit.signal' if s in sig_due else 'exit.time_cap', False, hi,
                          (hi - lots[s]['entry_ms']) // TF)
            pend_exit = []
    for s in list(lots):
        if cap_ms is not None and lots[s]['entry_ms'] + cap_ms < hi:
            raise PitRunError(f'{s}: a capped lot outlived its cap inside the split')    # unreachable
        drop(s, 'incomplete.split_end')
    trades.sort(key=lambda r: (r['entry_ms'], r['symbol']))
    out = {'trades': trades, 'incomplete': incomplete, 'refusals': dict(sorted(refusals.items())),
           'wallet_end': wallet, 'series': series}
    if short:
        out['side'] = 'short'
        out['liquidation'] = dict(liq, park=liq['flagged_bars'] > 0,
                                  note='PARK-only diagnostic (W_t < G_t / 3); never an exit, fill or sizing input')
    return out


def book_stats(series, equity0=BOOK['equity0']) -> dict:
    peak, dd, under, longest = equity0, 0.0, 0, 0
    for _, m in series:
        if m >= peak:
            peak, under = m, 0
        else:
            under += 1
            longest = max(longest, under)
        dd = max(dd, (peak - m) / peak if peak > 0 else 0.0)
    return {'mtm_max_dd_pct': 100.0 * dd, 'longest_underwater_cycles': longest}


def account(outputs, cfg: dict, *, symbols, first: int, last: int, hi: int, slip_c: float, rules: dict,
            rows=('base',), side='long') -> dict:
    """The registered book for one configuration over one split, per cost row. A symbol with any INCOMPLETE lot is
    excluded and the book re-run without it until no INCOMPLETE remains (reported, never silently counted)."""
    tape = read_tape(outputs)
    missing = sorted(s for s in symbols if s in tape[1] and s not in rules)
    if missing:
        raise PitRunError(f'no pinned exchange rule (tick / step / min qty / min notional) for {missing}')
    if cfg['kind'] == 'b1' and side == 'short':
        return {'config': cfg['name'], 'book': True, 'side': 'short',
                'rows': {row: b1_short_book(outputs, symbols=symbols, first=first, last=last, hi=hi, slip_c=slip_c,
                                            rules=rules, row=row) for row in rows}}
    if cfg['kind'] == 'b1':
        return account_unit(outputs, cfg, first=first, last=last, hi=hi, slip_c=slip_c, rows=rows)
    out = {'config': cfg['name'], 'book': True, 'rows': {}}
    for row in rows:
        excl, seen, first_trades = set(), [], None
        while True:
            res = run_book(tape, cfg, symbols, first=first, last=last, hi=hi, slip_c=slip_c, rules=rules, row=row,
                           exclude=frozenset(excl), side=side)
            if first_trades is None:
                first_trades = res['trades']
            seen += res['incomplete']
            new = {i['symbol'] for i in res['incomplete']} - excl
            if not new:
                break
            excl |= new
        res.update(counted=res['trades'], incomplete=seen, excluded_symbols=sorted(excl),
                   excluded_trades=sum(1 for r in first_trades if r['symbol'] in excl),
                   net_pct_equity=100.0 * (res['wallet_end'] - BOOK['equity0']) / BOOK['equity0'],
                   **book_stats(res['series']))
        if side == 'short':                     # STRAT-02 B1 pairing: the candidate's book MTM month R, same units
            res['month_r'] = month_r(res['series'], first, last)
        res.pop('series')
        out['rows'][row] = res
    base = out['rows'][rows[0]]
    out.update({k: base[k] for k in ('trades', 'counted', 'incomplete', 'excluded_symbols', 'excluded_trades',
                                     'refusals')})
    return out


def account_unit(outputs, cfg: dict, *, first: int, last: int, hi: int, slip_c: float, rows=tuple(C.STRESS)) -> dict:
    """Unit-quantity R per symbol (no shared book): B1 (prereg: equal-weight weekly hold scaled to equal risk) and
    per-trade diagnostics. Same fill / stop / next-open cap / funding / INCOMPLETE rules as the book."""
    times, syms = read_tape(outputs)
    trades, incomplete, refusals = [], [], Counter()
    for s in sorted(syms):
        x, lot, pend_entry, pend_exit = syms[s], None, None, None

        def close(exit_ref, exit_ms, reason, gap, at, nbars):
            try:
                r = net_r(x, entry_ref=lot['entry_ref'], entry_ms=lot['entry_ms'], dist=lot['dist'], exit_ref=exit_ref,
                          exit_ms=exit_ms, gap=gap, exit_bps_at=at, nbars=nbars, slip_c=slip_c, rows=rows)
            except Incomplete as e:
                incomplete.append({'symbol': s, 'entry_ms': lot['entry_ms'], 'reason': e.reason})
                return
            trades.append({'config': cfg['name'], 'symbol': s, 'signal_ms': lot['entry_ms'],
                           'entry_ms': lot['entry_ms'], 'exit_ms': exit_ms, 'exit_reason': reason, 'gap': gap,
                           'bars': nbars, 'r': r, 'mfe_r': (lot['hi'] - lot['fill']) / lot['dist'],
                           'mae_r': (lot['lo'] - lot['fill']) / lot['dist']})

        for t in times:
            rec = x.rec.get(t)
            if rec is None:
                if lot is not None:
                    incomplete.append({'symbol': s, 'entry_ms': lot['entry_ms'], 'reason': 'incomplete.gap'})
                    lot = None
                if pend_entry is not None:
                    refusals['entry_no_next_open'] += 1
                pend_entry = pend_exit = None
                continue
            o, h, lo, c = rec['b']
            bo = t - TF
            if pend_exit is not None and lot is not None:
                close(o, bo, 'exit.signal', False, bo, (bo - lot['entry_ms']) // TF)
                lot = None
            pend_exit = None
            if lot is not None and cfg['cap'] is not None and bo - lot['entry_ms'] == cfg['cap'] * TF:
                close(o, bo, 'exit.time_cap', False, bo, cfg['cap'])
                lot = None
            if pend_entry is not None:
                sig_t, dist = pend_entry
                pend_entry = None
                try:
                    fill = C.fill_price(o, 'buy', x.bps(bo, BASE.vol, slip_c), BASE)
                except Incomplete:
                    refusals['entry_no_slip_history'] += 1
                    fill = None
                if fill is not None and cfg['stop'] and fill - dist <= 0:
                    refusals['execution.stop_nonpositive'] += 1      # an untriggerable long stop: never opened
                elif fill is not None:
                    lot = {'entry_ms': bo, 'entry_ref': o, 'dist': dist, 'fill': fill,
                           'stop': fill - dist if cfg['stop'] else None, 'hi': o, 'lo': o}
            if lot is not None:
                if lot['stop'] is not None and (o <= lot['stop'] or lo <= lot['stop']):
                    lot['hi'], lot['lo'] = max(lot['hi'], o), min(lot['lo'], lo)   # pre-stop bound
                    ref, gap = C.stop_reference('long', lot['stop'], o)
                    close(ref, bo if gap else t, 'exit.stop', gap, bo, (bo - lot['entry_ms']) // TF + 1)
                    lot = None
                else:
                    lot['hi'], lot['lo'] = max(lot['hi'], h), min(lot['lo'], lo)
            le, lx, dist = _signal(cfg, x, rec, t)
            if lx and lot is not None:
                pend_exit = t
            if le and lot is None and first <= t <= last:
                if not dist or dist <= 0:
                    refusals['entry_bad_stop_distance'] += 1
                else:
                    pend_entry = (t, dist)
            if t == hi and lot is not None and (pend_exit is not None or (
                    cfg['cap'] is not None and lot['entry_ms'] + cfg['cap'] * TF == hi)):
                n = _next_open(x, hi, hi)
                if n is not None:
                    close(n, hi, 'exit.signal' if pend_exit is not None else 'exit.time_cap', False, hi,
                          (hi - lot['entry_ms']) // TF)
                    lot = None
        if lot is not None:
            incomplete.append({'symbol': s, 'entry_ms': lot['entry_ms'], 'reason': 'incomplete.split_end'})
    trades.sort(key=lambda r: (r['entry_ms'], r['symbol']))
    bad = sorted({i['symbol'] for i in incomplete})
    counted = [r for r in trades if r['symbol'] not in bad]
    return {'config': cfg['name'], 'book': False, 'trades': trades, 'counted': counted, 'incomplete': incomplete,
            'excluded_symbols': bad, 'excluded_trades': len(trades) - len(counted),
            'refusals': dict(sorted(refusals.items()))}


# ------------------------------------------------------------------ B1 short: the perp short benchmark book
def _is_monday_open(t: int) -> bool:
    return t % DAY == 0 and (t // DAY - 4) % 7 == 0       # 1970-01-01 was a Thursday


def b1_short_book(outputs, *, symbols, first: int, last: int, hi: int, slip_c: float, rules: dict, row: str = 'base',
                  book=BOOK) -> dict:
    """STRAT-02 B1 (contract section 7.1, Codex 6112077638): an equal-weight USD-M perpetual SHORT basket on its own
    10,000 USDT book, no spot borrow, signed actual funding (a short receives positive and pays negative rates).
      * tau = every Monday 00:00 UTC inside the decision range [first, last] plus its first bar (`first`);
      * M_tau = tier members with a closed 4h bar at tau, ATR14 defined on the contiguous tail (tape `a`) and a bar
        opening at tau (`_next_open`); N = |M_tau|, N = 0 = flat until the next tau;
      * q*_s = floor_step((0.01 x E_tau / N) / (2.5 x ATR14_s(tau))) with E_tau the B1 MTM equity at tau; if
        sum(q*_s x close_s(tau) x 1.10) > 3 x E_tau every q*_s is scaled by 3 x E_tau / that sum BEFORE flooring;
      * a leg below min qty / min notional (qty x the executable sell fill, pinned rules) is set to 0 and counted;
      * at the open of the bar starting at tau each symbol trades q*_s - q_s (sell = add short, buy = reduce), taker,
        adverse slip-v1, sell rounded DOWN / buy rounded UP to the tick; a member leaving M_tau is fully covered;
      * no stop, no time cap, no other exit; funding on the held qty at each funding time T in the played bar
        [t - 4h, t) at the funding-mark proxy;
      * a missing mark or a missing bar while held makes that symbol INCOMPLETE: it is counted, its leg leaves the
        book unsettled from that time and the symbol is excluded from later rebalances; the book is then labelled
        status INCOMPLETE (never a silently complete result).
    Returns the MTM series and the calendar-month block R `(E_end(m) - E_start(m)) / (0.01 x E_start(m))`."""
    times, syms = read_tape(outputs)
    scen = C.STRESS[row]
    order = [s for s in symbols if s in syms]
    wallet = book['equity0']
    pos, closes, legs, incomplete, refusals, series = {}, {}, [], [], Counter(), []
    dead, pending, taus = set(), None, []

    def kill(s, t, reason):
        incomplete.append({'symbol': s, 'time_ms': t, 'reason': reason})
        dead.add(s)
        pos.pop(s, None)

    for t in times:
        bo = t - TF
        rec = {s: syms[s].rec.get(t) for s in order}
        for s in [s for s in order if s in pos and rec[s] is None]:
            kill(s, t, 'incomplete.gap')
        if pending is not None and pending[0] == bo:                  # (A) rebalance fills at the open of [tau, ...)
            targets = pending[1]
            for s in order:
                if s in dead or rec[s] is None:
                    if targets.get(s) and s not in dead:
                        refusals['b1.no_open'] += 1
                    continue
                cur = pos[s]['qty'] if s in pos else 0.0
                tgt = targets.get(s, 0.0)
                x, rule = syms[s], rules[s]
                o = rec[s]['b'][0]
                try:
                    bps = x.bps(bo, scen.vol, slip_c)
                except Incomplete:
                    refusals['b1.no_slip_history'] += 1
                    continue
                if tgt > 0:
                    chk = C.fill_price(o, 'sell', bps, scen, tick=float(rule['tick']))
                    if tgt < float(rule['min_qty']) or tgt * chk < float(rule['min_notional']):
                        refusals['b1.size_min'] += 1
                        tgt = 0.0
                dq = tgt - cur
                if abs(dq) < 1e-12:
                    continue
                act = 'sell' if dq > 0 else 'buy'
                fill = C.fill_price(o, act, bps, scen, tick=float(rule['tick']))
                fee = C.fee(fill * abs(dq), 'taker', C.CRYPTO, scen)
                wallet -= fee
                if dq > 0:
                    avg = fill if cur == 0 else (cur * pos[s]['avg'] + dq * fill) / (cur + dq)
                    pos[s] = {'qty': cur + dq, 'avg': avg}
                    realised = 0.0
                else:
                    realised = (pos[s]['avg'] - fill) * -dq
                    wallet += realised
                    if tgt <= 0:
                        del pos[s]
                    else:
                        pos[s]['qty'] = tgt
                legs.append({'symbol': s, 'time_ms': bo, 'action': act, 'qty': abs(dq), 'fill': fill, 'fee': fee,
                             'realised': realised})
            pending = None
        for s in [s for s in order if s in pos]:                       # (B) funding on the held quantity
            ev = [(f.time_ms, f.rate, syms[s].mark[f.time_ms]) for f in syms[s].fund if bo <= f.time_ms < t]
            if any(m is None for _, _, m in ev):
                kill(s, t, 'incomplete.funding_mark')
                continue
            wallet -= C.funding_cost('short', pos[s]['qty'], ev, scen, bars_held=1,
                                     entry_notional=pos[s]['qty'] * pos[s]['avg'])
        for s in order:
            if rec[s] is not None:
                closes[s] = rec[s]['b'][3]
        mtm = wallet + math.fsum(p['qty'] * (p['avg'] - closes.get(k, p['avg'])) for k, p in pos.items())
        series.append((t, mtm))
        if first <= t <= last and (t == first or _is_monday_open(t)):  # (C) the rebalance decision at tau
            members = [s for s in order if s not in dead and rec[s] is not None and rec[s].get('a') is not None
                       and _next_open(syms[s], t, hi) is not None]
            raw = {}
            if members and mtm > 0:
                for s in members:
                    unit = float(T.stop_distance(float(rec[s]['a']), T.PRIMARY))       # 2.5 x ATR14_s(tau)
                    if unit > 0:
                        raw[s] = (0.01 * mtm / len(members)) / unit
                gross = math.fsum(q * rec[s]['b'][3] * (1 + book['cap_gap_buffer']) for s, q in raw.items())
                if gross > book['max_leverage'] * mtm:
                    k = book['max_leverage'] * mtm / gross
                    raw = {s: q * k for s, q in raw.items()}
            targets = {s: _floor(q, rules[s]['step']) for s, q in raw.items()}
            taus.append({'tau': t, 'members': len(members)})
            pending = (t, targets)
    blocks = month_r(series, first, last, book['equity0'])
    return {'config': 'B1', 'side': 'short', 'row': row, 'legs': legs, 'incomplete': incomplete,
            'excluded_symbols': sorted(dead), 'refusals': dict(sorted(refusals.items())), 'rebalances': taus,
            'series': series, 'wallet_end': wallet, 'month_r': blocks,
            'status': 'INCOMPLETE' if incomplete else 'COMPLETE'}


def month_r(series, first: int, last: int, equity0=BOOK['equity0']) -> dict:
    """{YYYY-MM: (E_end - E_start) / (0.01 x E_start)} from MTM equity at the calendar-month boundaries (the last MTM
    at or before each boundary; equity0 before the first cycle). Used for B1 short and the candidate book alike."""
    def at(ms):
        k = bisect.bisect_right(ts, ms)
        return equity0 if k == 0 else series[k - 1][1]
    ts = [t for t, _ in series]
    out = {}
    for mo in months_of(first, last):
        y, m = map(int, mo.split('-'))
        a = S.parse_utc(f'{mo}-01T00:00:00Z')
        b = S.parse_utc(f'{y + (m == 12):04d}-{1 if m == 12 else m + 1:02d}-01T00:00:00Z')
        e0, e1 = at(a), at(b)
        out[mo] = (e1 - e0) / (0.01 * e0) if e0 > 0 else None
    return out


def configs_short() -> list[dict]:
    """STRAT-02 configurations (contract section 8): the single capped primary, the 12 one-at-a-time neighbours (same
    steps and evaluator indices as v1), B1 (perp short book) and B2 (20-bar momentum short). No uncapped variant."""
    out = [{'name': SHORT_PRIMARY_NAME, 'kind': 'sig', 'k': 0, 'cap': CAP, 'stop': True, 'role': 'primary'}]
    out += [{'name': n, 'kind': 'sig', 'k': i + 1, 'cap': CAP, 'stop': True, 'role': 'neighbour'}
            for i, (n, _) in enumerate(T.neighbours())]
    out += [{'name': 'B1', 'kind': 'b1', 'k': None, 'cap': None, 'stop': False, 'role': 'baseline'},
            {'name': 'B2', 'kind': 'b2', 'k': None, 'cap': CAP, 'stop': True, 'role': 'baseline'}]
    return out


# ------------------------------------------------------------------ B0: matched random entries
def b0_pool(outputs, *, first: int, last: int) -> dict:
    """{(symbol, regime label): [eligible decision times]}: inside the split's decision range, the symbol warm (its
    primary stop distance defined) and the tape's frozen regime label defined."""
    times, syms = read_tape(outputs)
    regime = {o['t']: o.get('g') for o in outputs}
    out = {}
    for s, x in sorted(syms.items()):
        for t in times:
            if first <= t <= last and t in x.rec and x.rec[t]['d'] is not None and regime.get(t) is not None:
                out.setdefault((s, regime[t]), []).append(t)
    return out


def b0_pairs(outputs, primary: dict, *, first: int, last: int, hi: int, slip_c: float, seed_key: str,
             draws: int = B0_DRAWS, side: str = 'long', rules=None) -> dict:
    """Episode-paired differences: candidate episode mean net R - the mean of its matched draws. A draw matches the
    candidate's symbol, side (long), split and registered PIT regime label at the decision (tape `g`: BTCUSDT close
    vs EMA200, frozen in the attested tape), uses its realised hold capped at 180 bars (exit at the next open, the
    same stop rule, base costs and funding) and is drawn uniformly from that symbol's eligible decision times in the
    split with the same label. A candidate whose label is undefined is unmatched (counted); an invalid draw (gap /
    funding / slip history / no next open in the split) is excluded and counted.

    `side='short'` (STRAT-02 B0): matched random SHORT entries - sell fill rounded DOWN to the symbol's tick (`rules`),
    stop = ceil_tick(fill + dist), a bar OPENING at or above it fills at its open, else a HIGH reaching it fills at the
    stop; covers rounded UP; R per unit of the actual risk stop - fill; short funding sign."""
    if side not in SIDES:
        raise PitRunError("side must be 'long' or 'short'")
    if side == 'short' and rules is None:
        raise PitRunError('B0 short needs the pinned tick rules (ceiled stop, tick-rounded fills)')
    times, syms = read_tape(outputs)
    regime = {o['t']: o.get('g') for o in outputs}
    elig = b0_pool(outputs, first=first, last=last)
    cache, invalid, unmatched = {}, 0, 0

    def sim_short(s, e, h):
        x, tk = syms[s], float(rules[s]['tick'])
        d = float(Decimal(x.rec[e]['d']))
        if e + h * TF > hi or any((e + j * TF) not in x.rec for j in range(1, h + 1)):
            raise Incomplete('incomplete.gap')
        o0 = x.rec[e + TF]['b'][0]
        fill = C.fill_price(o0, 'sell', x.bps(e, BASE.vol, slip_c), BASE, tick=tk)
        stop = _ceil(fill + d, tk)
        risk_u = stop - fill
        if not (fill > 0 and math.isfinite(risk_u) and risk_u > 0):
            raise Incomplete('incomplete.stop_risk_invalid')
        kw = dict(entry_ref=o0, entry_ms=e, dist=risk_u, slip_c=slip_c, rows=('base',), side='short', tick=tk)
        for j in range(1, h + 1):
            o, hh, _, _ = x.rec[e + j * TF]['b']
            if o >= stop or hh >= stop:
                ref, gap = C.stop_reference('short', stop, o)
                bo = e + (j - 1) * TF
                return net_r(x, exit_ref=ref, exit_ms=bo if gap else bo + TF, gap=gap, exit_bps_at=bo, nbars=j,
                             **kw)['base']
        t = e + h * TF
        n = _next_open(x, t, hi)
        if n is None:
            raise Incomplete('incomplete.split_end')
        return net_r(x, exit_ref=n, exit_ms=t, gap=False, exit_bps_at=t, nbars=h, **kw)['base']

    def sim(s, e, h):
        key = (s, e, h)
        if key not in cache and side == 'short':
            try:
                cache[key] = sim_short(s, e, h)
            except Incomplete:
                cache[key] = None
        if key not in cache:
            x = syms[s]
            d = float(Decimal(x.rec[e]['d']))
            try:
                if e + h * TF > hi or any((e + j * TF) not in x.rec for j in range(1, h + 1)):
                    raise Incomplete('incomplete.gap')
                o0 = x.rec[e + TF]['b'][0]
                fill = C.fill_price(o0, 'buy', x.bps(e, BASE.vol, slip_c), BASE)
                stop, res = fill - d, None
                if stop <= 0:
                    raise Incomplete('incomplete.stop_nonpositive')         # never simulate an untriggerable stop
                for j in range(1, h + 1):
                    o, _, lo, c = x.rec[e + j * TF]['b']
                    if o <= stop or lo <= stop:
                        ref, gap = C.stop_reference('long', stop, o)
                        bo = e + (j - 1) * TF
                        res = net_r(x, entry_ref=o0, entry_ms=e, dist=d, exit_ref=ref, exit_ms=bo if gap else bo + TF,
                                    gap=gap,
                                    exit_bps_at=bo, nbars=j, slip_c=slip_c, rows=('base',))['base']
                        break
                if res is None:
                    t = e + h * TF
                    n = _next_open(x, t, hi)
                    if n is None:
                        raise Incomplete('incomplete.split_end')
                    res = net_r(x, entry_ref=o0, entry_ms=e, dist=d, exit_ref=n, exit_ms=t, gap=False,
                                exit_bps_at=t, nbars=h, slip_c=slip_c, rows=('base',))['base']
                cache[key] = res
            except Incomplete:
                cache[key] = None
        return cache[key]

    means = {}
    for tr in primary['counted']:
        h = max(1, min(CAP, tr['bars']))
        lab = regime.get(tr['signal_ms'])
        pool = elig.get((tr['symbol'], lab), []) if lab is not None else []
        if lab is None:
            unmatched += 1
        rng = random.Random(f'{seed_key}|{tr["symbol"]}|{tr["entry_ms"]}')
        vals = []
        for _ in range(draws if pool else 0):
            v = sim(tr['symbol'], pool[rng.randrange(len(pool))], h)
            if v is None:
                invalid += 1
            else:
                vals.append(v)
        means[(tr['symbol'], tr['entry_ms'])] = vals
    diffs = []
    for ep in X.episodes(primary['counted']):
        draws_ep = [v for tr in ep for v in means[(tr['symbol'], tr['entry_ms'])]]
        if draws_ep:
            diffs.append(sum(tr['r']['base'] for tr in ep) / len(ep) - sum(draws_ep) / len(draws_ep))
    return {'diffs': diffs, 'invalid_draws': invalid, 'unmatched_trades': unmatched, 'episodes': len(diffs),
            'regime_label': 'BTC 4h close vs its trailing EMA200 (btc_up / btc_down)'}


# ------------------------------------------------------------------ statistics
def q95(xs):
    s = sorted(xs)
    return [s[int(0.025 * len(s))], s[min(len(s) - 1, int(0.975 * len(s)))]] if s else None


def bootstrap(rows, *, seed: int, resamples: int) -> dict:
    rs = [r['r']['base'] for r in rows]
    if not rs:
        return {'ci95_trade': None, 'ci95_episode': None}
    eps = [[r['r']['base'] for r in e] for e in X.episodes(rows)]
    rng = random.Random(seed)
    tr, ep = [], []
    n = len(rs)
    for _ in range(resamples):
        tr.append(math.fsum(rs[rng.randrange(n)] for _ in range(n)) / n)
        pick = [eps[rng.randrange(len(eps))] for _ in range(len(eps))]
        ep.append(math.fsum(map(math.fsum, pick)) / sum(map(len, pick)))
    return {'ci95_trade': q95(tr), 'ci95_episode': q95(ep), 'resamples': resamples, 'seed': seed}


def sign_flip_p(diffs, *, perms: int, seed: int):
    """One-sided paired sign-flip permutation p (H1: mean difference > 0)."""
    n = len(diffs)
    if not n:
        return None
    obs = math.fsum(diffs) / n
    rng, ge = random.Random(seed), 0
    for _ in range(perms):
        if math.fsum(d if rng.random() < 0.5 else -d for d in diffs) / n >= obs - 1e-12:
            ge += 1
    return (ge + 1) / (perms + 1)


def holm(ps: dict, m: int) -> dict:
    """Holm step-down adjustment of the tested p-values within a family of `m` trials (m >= the number tested)."""
    items = sorted((p, k) for k, p in ps.items() if p is not None)
    m = max(m, len(items))
    out, run = {k: None for k in ps}, 0.0
    for i, (p, k) in enumerate(items):
        run = max(run, min(1.0, (m - i) * p))
        out[k] = run
    return out


def ci_mean(xs):
    if len(xs) < 2:
        return None
    m = math.fsum(xs) / len(xs)
    sd = math.sqrt(math.fsum((x - m) ** 2 for x in xs) / (len(xs) - 1))
    half = 1.96 * sd / math.sqrt(len(xs))
    return [m - half, m + half]


def metrics(acc: dict, *, rows, boot=None) -> dict:
    """Aggregate metrics of the counted trades (no symbol names)."""
    tr = acc['counted']
    rs = [r['r']['base'] for r in tr]
    wins, losses = [x for x in rs if x > 0], [x for x in rs if x <= 0]
    cum = peak = dd = 0.0
    under = longest = 0
    for r in sorted(tr, key=lambda r: (r['exit_ms'], r['symbol'])):
        cum += r['r']['base']
        if cum >= peak:
            peak, under = cum, 0
        else:
            under += 1
            longest = max(longest, under)
        dd = max(dd, peak - cum)
    out = {'label': LABEL, 'trades': len(tr), 'episodes': len(X.episodes(tr)),
           'mean_r': math.fsum(rs) / len(rs) if rs else None, 'total_r': math.fsum(rs),
           'win_rate': len(wins) / len(rs) if rs else None,
           'pf': (math.fsum(wins) / -math.fsum(losses)) if losses and math.fsum(losses) < 0 else None,
           'avg_win': math.fsum(wins) / len(wins) if wins else None,
           'avg_loss': math.fsum(losses) / len(losses) if losses else None,
           'closed_trade_max_dd_r': dd, 'longest_underwater_trades': longest,
           'mean_mfe_r': math.fsum(r['mfe_r'] for r in tr) / len(tr) if tr else None,
           'mean_mae_r': math.fsum(r['mae_r'] for r in tr) / len(tr) if tr else None,
           'mean_giveback_r': math.fsum(r['mfe_r'] - r['r']['base'] for r in tr) / len(tr) if tr else None,
           'exposure_bars': sum(r['bars'] for r in tr),
           'exit_reasons': dict(sorted(Counter(r['exit_reason'] for r in tr).items())),
           'gap_fills': sum(1 for r in tr if r['gap']), 'intrabar_ambiguous': 0,
           'refusals': acc['refusals'], 'incomplete': len(acc['incomplete']),
           'incomplete_reasons': dict(sorted(Counter(i['reason'] for i in acc['incomplete']).items())),
           'excluded_symbol_count': len(acc['excluded_symbols']), 'excluded_trades': acc['excluded_trades'],
           'stress_mean_r': {}}
    by_row = acc.get('rows_trades')
    for k in rows:
        xs = [r['r'][k] for r in by_row[k]] if by_row is not None else [r['r'][k] for r in tr]
        out['stress_mean_r'][k] = math.fsum(xs) / len(xs) if xs else None
    if acc.get('book') is not None:
        out['book'] = {k: dict(v) for k, v in sorted(acc['book'].items())}
    if boot is not None:
        out.update(bootstrap(tr, **boot))
        if all('pnl' in r for r in tr):
            out['mc_drawdown_pct'] = mc_drawdown(tr, **boot)
    return out


def mc_drawdown(rows, *, seed: int, resamples: int, equity0=BOOK['equity0']):
    """Episode-bootstrap Monte Carlo of the book's closed-trade drawdown (% of the running peak): p5 / p50 / p95."""
    eps = [[r['pnl'] for r in e] for e in X.episodes(rows)]
    if not eps:
        return None
    rng, dds = random.Random(seed + 1), []
    for _ in range(resamples):
        eq = peak = equity0
        dd = 0.0
        for _ in range(len(eps)):
            for v in eps[rng.randrange(len(eps))]:
                eq += v
                peak = max(peak, eq)
                dd = max(dd, (peak - eq) / peak if peak > 0 else 0.0)
        dds.append(100.0 * dd)
    dds.sort()
    pick = lambda q: dds[min(len(dds) - 1, int(q * len(dds)))]
    return {'p5': pick(0.05), 'p50': pick(0.50), 'p95': pick(0.95), 'resamples': resamples}


def months_of(first: int, last: int) -> list[str]:
    a, b = S.utc(first)[:7], S.utc(last)[:7]
    out, (y, m) = [], map(int, a.split('-'))
    while f'{y:04d}-{m:02d}' <= b:
        out.append(f'{y:04d}-{m:02d}')
        y, m = (y + 1, 1) if m == 12 else (y, m + 1)
    return out


def month_blocks(key, first, last, cand, b1, b2) -> list[dict]:
    """Calendar-month blocks of one split (trades by entry month): candidate = sum of trade net R (each 1R),
    B2 = sum of its trade net R, B1 = per week the equal-weight mean over members of the weekly hold net R, summed."""
    out = []
    for mo in months_of(first, last):
        def tot(acc):
            return math.fsum(r['r']['base'] for r in acc['counted'] if S.utc(r['entry_ms'])[:7] == mo)
        weeks = {}
        for r in b1['counted']:
            if S.utc(r['entry_ms'])[:7] == mo:
                weeks.setdefault(r['entry_ms'], []).append(r['r']['base'])
        out.append({'block': f'{key}:{mo}', 'cand': tot(cand), 'B2': tot(b2),
                    'B1': math.fsum(math.fsum(v) / len(v) for v in weeks.values())})
    return out


# ------------------------------------------------------------------ the tier evaluation
def evaluate_tier(env: dict, tier: str, prog: PG.Progress | None) -> tuple[dict, dict]:
    """Open, evaluate and account every development split of one tier; returns (summary, detail)."""
    plan, cfgs, cfg_run = env['plan'], configs(), env['run_config']
    symbols = env['tiers'][tier]
    missing = [s for s in symbols if s not in env['rules']]
    if missing:
        raise PitRunError(f'{tier}: no pinned exchange rule for {missing}; the registered book cannot size them')
    ds = StaticDataset(env['manifest'], env['store'], env['static'], symbols)
    rows = tuple(cfg_run['stress_rows'])
    per_split, detail_splits, blocks, b0_diffs, b0_meta = {}, {}, [], [], Counter()
    for key in dev_keys(plan):
        lo, hi = dev_window(plan, key)
        first, last = plan.decision_range(key)
        name, fold = plan.split(key)['name'], plan.split(key)['fold']
        stage = 'pit.walk_forward' if name == 'walk_forward' else 'pit.development'
        if prog is not None:
            prog.stage_start(stage, fold)
        acc = P.Access(ds, plan, env['ledger'], candidate_id=T.RULE_ID, author=env['author'],
                       cairo_date=env['cairo_date'], repo=env['repo'])
        w = acc.open(key, detail={'purpose': f'STRAT-01 R4b-PIT {tier} development split (no holdout)',
                                  'tier': tier, 'symbols': list(symbols)}, lineage='root')
        ev = P.evaluate(w, env['evaluator'], FUNCTION, list(range(lo + TF, hi + 1, TF)), perturb=True)
        res = {c['name']: account(ev, c, symbols=symbols, first=first, last=last, hi=hi, slip_c=cfg_run['slip_c'],
                                  rules=env['rules'],
                                  rows=rows if c['role'] in ('primary', 'development_only', 'baseline') else ('base',))
               for c in cfgs}
        regime = {o['t']: o.get('g') for o in ev}
        for v in res.values():
            for tr in v['trades']:
                tr['regime'] = regime.get(tr['signal_ms'])
        prim = res[PRIMARY_NAME]
        boot = {'seed': cfg_run['seed'], 'resamples': cfg_run['resamples']}
        per_split[key] = {'primary': metrics(_acc(prim), rows=rows, boot=boot),
                          'uncapped_development_only': metrics(_acc(res[DEV_ONLY]), rows=rows, boot=boot)}
        b0 = b0_pairs(ev, prim, first=first, last=last, hi=hi, slip_c=cfg_run['slip_c'],
                      seed_key=f'{cfg_run["seed"]}|{tier}|{key}', draws=cfg_run['b0_draws'])
        b0_diffs += b0['diffs']
        b0_meta.update(invalid_draws=b0['invalid_draws'], unmatched_trades=b0['unmatched_trades'])
        blocks += month_blocks(key, first, last, prim, res['B1'], res['B2'])
        detail_splits[key] = {'attestation': ev.attestation_digest, 'outputs_digest': ev.attestation['outputs_digest'],
                              'b0_invalid_draws': b0['invalid_draws'], 'b0_unmatched_trades': b0['unmatched_trades'],
                              'configs': {k: _detail(v) for k, v in res.items()}}
        if prog is not None:
            prog.stage_done(stage, fold)
    return summarize(env, tier, symbols, per_split, detail_splits, blocks, b0_diffs, dict(b0_meta))


def _detail(v: dict) -> dict:
    out = {k: v[k] for k in ('trades', 'incomplete', 'excluded_symbols', 'excluded_trades', 'refusals')}
    if v.get('book'):
        out['rows'] = {r: {'trades': x['counted'],
                           'book': {k: x[k] for k in ('net_pct_equity', 'mtm_max_dd_pct', 'longest_underwater_cycles',
                                                      'wallet_end')}}
                       for r, x in v['rows'].items()}
    return out


def _acc(v: dict) -> dict:
    """The metrics view of one configuration's account (one split)."""
    a = {k: v[k] for k in ('counted', 'incomplete', 'refusals', 'excluded_symbols', 'excluded_trades')}
    if v.get('book'):
        a['rows_trades'] = {r: x['counted'] for r, x in v['rows'].items()}
        a['book'] = {r: {k: x[k] for k in ('net_pct_equity', 'mtm_max_dd_pct', 'longest_underwater_cycles')}
                     for r, x in v['rows'].items()}
    return a


def _counted(detail_splits, name, keys):
    out = []
    for k in keys:
        c = detail_splits[k]['configs'][name]
        bad = set(c['excluded_symbols'])
        out += [r for r in c['trades'] if r['symbol'] not in bad]
    return out


def _agg(detail_splits, name, keys, rows, boot):
    """Several splits: trades pooled; each split's book starts at its own 10,000 USDT, so net % equity is summed
    over the splits and the MTM drawdown / underwater are the worst split's."""
    cs = [detail_splits[k]['configs'][name] for k in keys]
    tr = _counted(detail_splits, name, keys)
    ref = Counter()
    for c in cs:
        ref.update(c['refusals'])
    acc = {'counted': tr, 'incomplete': [i for c in cs for i in c['incomplete']], 'refusals': dict(sorted(ref.items())),
           'excluded_symbols': sorted({f'{k}:{s}' for k, c in zip(keys, cs) for s in c['excluded_symbols']}),
           'excluded_trades': sum(c['excluded_trades'] for c in cs)}
    if cs and all('rows' in c for c in cs):
        rs = [r for r in rows if all(r in c['rows'] for c in cs)]
        acc['rows_trades'] = {r: [t for c in cs for t in c['rows'][r]['trades']] for r in rs}
        acc['book'] = {r: {'net_pct_equity': math.fsum(c['rows'][r]['book']['net_pct_equity'] for c in cs),
                           'mtm_max_dd_pct': max(c['rows'][r]['book']['mtm_max_dd_pct'] for c in cs),
                           'longest_underwater_cycles': max(c['rows'][r]['book']['longest_underwater_cycles']
                                                            for c in cs)} for r in rs}
        rows = rs
    return metrics(acc, rows=rows, boot=boot), tr


def summarize(env, tier, symbols, per_split, detail_splits, blocks, b0_diffs, b0_meta=None):
    cfg_run, plan = env['run_config'], env['plan']
    rows = tuple(cfg_run['stress_rows'])
    keys = list(per_split)
    wf = [k for k in keys if plan.split(k)['name'] == 'walk_forward']
    boot = {'seed': cfg_run['seed'], 'resamples': cfg_run['resamples']}
    dev_m, dev_tr = _agg(detail_splits, PRIMARY_NAME, keys, rows, boot)
    wf_m, _ = _agg(detail_splits, PRIMARY_NAME, wf, rows, boot)
    sign = (dev_m['mean_r'] or 0.0) > 0
    neigh = {}
    for c in configs():
        if c['role'] == 'neighbour':
            m, _ = _agg(detail_splits, c['name'], keys, rows, None)
            neigh[c['name']] = m['mean_r']
    keep = sum(1 for v in neigh.values() if v is not None and (v > 0) == sign)
    keep_share = keep / len(neigh) if neigh else None
    loso = {s: [r['r']['base'] for r in dev_tr if r['symbol'] != s] for s in sorted({r['symbol'] for r in dev_tr})}
    loso_m = {s: (math.fsum(v) / len(v) if v else None) for s, v in loso.items()}
    years = sorted({S.utc(r['entry_ms'])[:4] for r in dev_tr})
    loyo_m = {y: (lambda v: math.fsum(v) / len(v) if v else None)(
        [r['r']['base'] for r in dev_tr if S.utc(r['entry_ms'])[:4] != y]) for y in years}
    perms, seed = cfg_run['perms'], cfg_run['seed']
    d1 = [b['cand'] - b['B1'] for b in blocks]
    d2 = [b['cand'] - b['B2'] for b in blocks]
    ps = {'B0': sign_flip_p(b0_diffs, perms=perms, seed=seed), 'B1': sign_flip_p(d1, perms=perms, seed=seed),
          'B2': sign_flip_p(d2, perms=perms, seed=seed)}
    adj = holm(ps, env['n_trials'])
    base = {k: {'pairing': p, 'n_pairs': len(d), 'mean_difference': (math.fsum(d) / len(d) if d else None),
                'ci95': ci_mean(d), 'p_one_sided': ps[k], 'p_holm': adj[k], 'label': LABEL}
            for k, p, d in (('B0', 'episode', b0_diffs), ('B1', 'calendar-month block', d1),
                            ('B2', 'calendar-month block', d2))}
    base['B0'].update(match='symbol + side + split + PIT regime label (BTC 4h close vs EMA200)',
                      **(b0_meta or {}))
    base['B3'] = {'pairing': 'cash (0 R)', 'note': 'reduces to the expectancy row', 'label': LABEL}
    dev_mean = dev_m['mean_r']
    edge = {
        'sample': {'status': 'HOLDOUT-ONLY', 'development': {
            'episodes': dev_m['episodes'], 'month_blocks': len(blocks),
            'meets_60_episodes_6_blocks': dev_m['episodes'] >= 60 and len(blocks) >= 6}},
        'expectancy': {'status': 'HOLDOUT-ONLY', 'development': {
            'walk_forward_mean_positive': (wf_m['mean_r'] or 0.0) > 0}},
        'baselines': {'status': 'HOLDOUT-ONLY', 'development': {
            k: (adj[k] is not None and adj[k] < 0.05) for k in ('B0', 'B1', 'B2')}},
        'multiplicity': {'status': 'PASS', 'n_trials': env['n_trials']},
        'pit': {'status': 'HOLDOUT-ONLY', 'label': LABEL},
        'robustness': {'status': 'PASS' if loso_m and all(v is not None and v > 0 for v in loso_m.values())
                       and loyo_m and all(v is not None and v > 0 for v in loyo_m.values()) else 'FAIL',
                       'loso_positive_share': (sum(1 for v in loso_m.values() if v is not None and v > 0) / len(loso_m)
                                               if loso_m else None),
                       'loyo_positive_share': (sum(1 for v in loyo_m.values() if v is not None and v > 0) / len(loyo_m)
                                               if loyo_m else None)},
        'neighbours': {'status': 'PASS' if keep_share is not None and keep_share >= 0.8 else 'FAIL',
                       'sign_keep_share': keep_share},
        'intrabar': {'status': 'PASS', 'ambiguous_share': 0.0},
        'e10_drawdown': {'status': 'PARK-ONLY'},
        'e11_follower': {'status': 'NOT-EVALUATED'},
    }
    import run_r4 as G                                                                      # noqa: E402
    if set(edge) != G.EDGE_ROWS:
        raise PitRunError('the EDGE-00 rows differ from the prereg rows')
    flags = ['DEV-NEGATIVE'] if dev_mean is None or dev_mean <= 0 else []
    summary = {'format': SUMMARY_FORMAT, 'tier': tier, 'label': LABEL, 'labels': [LABEL, DEV_LABEL] + env['cost_labels'],
               'development_only': True, 'identity': env['identity'], 'run_config_digest': sha(cfg_run),
               'symbol_count': len(symbols), 'splits': per_split,
               'aggregates': {'development': dev_m, 'walk_forward': wf_m},
               'contribution_by_regime': {g: {'trades': len(v), 'mean_r': math.fsum(v) / len(v)}
                                          for g, v in sorted(_by_regime(dev_tr).items())},
               'book': dict(BOOK), 'baselines': base, 'edge00': edge,
               'verdict': 'PARK', 'verdict_basis': 'prereg verdict_rules.development: development evidence can neither '
               'PROMOTE nor REJECT', 'flags': flags, 'not_evaluated': list(NOT_EVALUATED)}
    detail = {'format': DETAIL_FORMAT, 'tier': tier, 'label': LABEL, 'symbols': list(symbols), 'splits': detail_splits,
              'neighbours_mean_r': neigh, 'loso_mean_r': loso_m, 'loyo_mean_r': loyo_m, 'month_blocks': blocks,
              'b0_episode_differences': b0_diffs}
    return summary, detail


def _by_regime(trades) -> dict:
    out = {}
    for r in trades:
        out.setdefault(str(r.get('regime')), []).append(r['r']['base'])
    return out


# ------------------------------------------------------------------ identity, progress, sealing, resume
def code_digest(repo: str) -> str:
    files = sorted(glob.glob(os.path.join(repo, 'tools', 'research', '*.py'))) + [os.path.join(repo, 'feasibility.py')]
    return sha([{'path': os.path.relpath(f, repo).replace(os.sep, '/'), 'sha256': PG.file_sha256(f)} for f in files])


def tier_config(env: dict, tier: str) -> dict:
    return {'tier': tier, 'symbols': list(env['tiers'][tier]), 'shared': sha(env['run_config'])}


def write_json(path: str, doc) -> str:
    return X.atomic_write_json(path, doc)


def _state(d: str, run: str, resume: bool) -> str:
    """'new' | 'resume' | 'sealed' for one tier dir; refuses an interrupted run without resume and any other run."""
    if not os.path.isdir(d):
        return 'new'
    found = [os.path.basename(p) for p in glob.glob(os.path.join(d, 'progress-*.json'))
             + glob.glob(os.path.join(d, 'final-*.json'))]
    other = sorted(f for f in found if run not in f)
    if other:
        raise PitRunError(f'{d}: holds another run identity/config ({other[0]}); use a new evidence dir')
    final = os.path.join(d, f'final-{run}.json')
    if os.path.exists(final):
        doc = PG.read(final)
        if doc['state'] != 'sealed':
            raise PitRunError(f'{final}: the run was aborted; use a new evidence dir')
        for a in doc['artifacts']:
            if PG.file_sha256(os.path.join(d, a['path'])) != a['sha256']:
                raise PitRunError(f'{d}: sealed artifact {a["path"]} no longer matches its final record')
        if not resume:
            raise PitRunError(f'{final}: tier already sealed (pass resume to continue)')
        return 'sealed'
    if os.path.exists(os.path.join(d, f'progress-{run}.json')):
        if not resume:
            raise PitRunError(f'{d}: interrupted run (progress without a final); pass resume to re-run it '
                              'under the same identity')
        return 'resume'
    return 'new'


def run_tiers(env: dict, out_dir: str, *, resume: bool = False, clock=PG.cairo_now) -> list[str]:
    """CORE-10 then CORE-20: each tier sealed (final written) before the next starts. Returns the final paths."""
    finals, ident = [], env['identity']
    for i, tier in enumerate(RUN_TIERS):
        d = os.path.join(out_dir, tier)
        run = PG.run_id(ident, tier_config(env, tier))
        state = _state(d, run, resume)
        final = os.path.join(d, f'final-{run}.json')
        if state == 'sealed':
            finals.append(final)
            continue
        if i:
            prev = os.path.join(out_dir, RUN_TIERS[i - 1], f'final-{PG.run_id(ident, tier_config(env, RUN_TIERS[i - 1]))}.json')
            doc = PG.read(prev)
            if doc is None or doc['state'] != 'sealed' or doc['identity'] != ident:
                raise PitRunError(f'{RUN_TIERS[i - 1]} must be sealed under the same identity before {tier} starts')
        prog = PG.Progress(d, identity=ident, config=tier_config(env, tier), clock=clock)
        stage = 'pit.open'
        try:
            prog.stage_start(stage)
            prog.stage_done(stage)
            stage = None
            summary, detail = evaluate_tier(env, tier, prog)
            paths = [write_json(os.path.join(d, f'summary-{run}.json'), summary),
                     write_json(os.path.join(d, f'detail-{run}.json'), detail)]
        except Exception as e:
            prog.aborted([PG.integrity_code(type(e).__name__)], stage=stage)
            raise
        prog.sealed(paths, [os.path.basename(paths[0])])
        finals.append(final)
    return finals


# ------------------------------------------------------------------ the real environment (gate-cleared repo only)
def load_rules(raw: bytes, symbols, *, pin: str = RULES_SHA256) -> dict:
    """The book's lot rules for `symbols` from the pinned mainnet snapshot bytes. Fail-closed: the bytes must hash to the
    pin (never the M3 testnet anchor), the snapshot must be a verified direct fetch of the mainnet environment, and
    EVERY symbol must be present with all four fields - no fallback, no partial tier, no testnet/mainnet mixing."""
    digest = hashlib.sha256(raw).hexdigest()
    if digest == X.RULES_SHA256:
        raise PitRunError('the M3 testnet rules anchor may not size the R4b book (no testnet/mainnet mixing)')
    if digest != pin:
        raise PitRunError(f'{RULES_PATH}: bytes differ from the pinned mainnet rules snapshot')
    snap = json.loads(raw)
    if (snap.get('environment'), snap.get('provenance'), snap.get('verified')) != (RULES_ENV, 'direct_fetch', True):
        raise PitRunError('the rules snapshot is not a verified direct fetch of Binance mainnet')
    if snap.get('source_url') != 'https://fapi.binance.com/fapi/v1/exchangeInfo':
        raise PitRunError('the rules snapshot source is not the Binance USD-M mainnet exchangeInfo')
    table = snap.get('symbols') or {}
    missing = [s for s in symbols if s not in table or any(k not in table[s] for k in RULES_FIELDS)]
    if missing:
        raise PitRunError(f'no pinned {RULES_ENV} exchange rule for {missing}; the registered book cannot size them')
    return {s: {k: table[s][k] for k in RULES_FIELDS} for s in symbols}


def real_env(repo: str, *, store: str, author: str, cairo_date: str, gate_path: str) -> dict:
    """Derived ONLY from the gate's committed files. Reads manifest metadata, never a data row."""
    import run_r4 as G                                                                      # noqa: E402
    g = json.loads(G._load(repo, gate_path))
    raw = G._load(repo, g['prereg'])
    pre = json.loads(raw)
    static = G.static_universe(repo, g['universe']['path'], g['universe']['snapshot'])
    man = M.load(os.path.join(repo, MANIFEST_PATH))
    if man['digest'] != pre['data']['manifest_digest']:
        raise PitRunError('the manifest differs from the prereg-pinned digest')
    sp = pre['splits']
    plan = S.SplitPlan(sp['splits'], interval=sp['interval'], lookback_bars=sp['lookback_bars'],
                       horizon_bars=sp['horizon_bars'])
    if [n for n, _ in T.neighbours()] != pre['neighbour_grid']['configs']:
        raise PitRunError('the neighbour grid differs from the registered prereg configs')
    if pre['variants']['primary']['name'] != PRIMARY_NAME or pre['variants']['secondary'][0]['name'] != DEV_ONLY:
        raise PitRunError('the variants differ from the registered prereg')
    if pre['costs']['stress_rows'] != list(C.STRESS):
        raise PitRunError('stress rows differ from the registered prereg')
    ledger = os.path.join(repo, g['ledger'])
    fam = L.verify(ledger).get(pre['family'], [])
    n_trials = sum(1 for r in fam if r['kind'] in ('variant', 'grid_point', 'baseline'))
    cal = pre['costs']['slip_cal_v1']
    model = C.CostModel(cal, {'BTCUSDT': 'crypto'})
    rules = load_rules(G._load(repo, RULES_PATH), static['core_20'])
    run_config = {'configs': [c['name'] for c in configs()], 'stress_rows': list(C.STRESS), 'book': dict(BOOK),
                  'rules_path': RULES_PATH, 'rules_sha256': RULES_SHA256, 'rules_env': RULES_ENV, 'rules': rules,
                  'slip_c': cal['c']['crypto'], 'seed': SEED, 'resamples': RESAMPLES, 'perms': PERMS,
                  'b0_draws': B0_DRAWS, 'dev_keys': dev_keys(plan), 'plan_digest': plan.digest, 'window': T.WINDOW,
                  'tape': PE.FORMAT, 'cost_model': model.digest, 'n_trials': n_trials}
    head = subprocess.run(['git', '-C', repo, 'rev-parse', 'HEAD'], capture_output=True, text=True,
                          check=True).stdout.strip()
    identity = {'commit': head, 'evaluator': sha({'code': code_digest(repo), 'config': run_config}),
                'manifest': man['digest'], 'universe': static['digest'], 'classification': None,
                'prereg': hashlib.sha256(raw).hexdigest()}
    return {'repo': repo, 'manifest': man, 'store': store, 'static': static,
            'tiers': {'CORE-10': static['core_10'], 'CORE-20': static['core_20']}, 'plan': plan, 'ledger': ledger,
            'evaluator': EVALUATOR, 'author': author, 'cairo_date': cairo_date, 'run_config': run_config,
            'rules': rules,
            'identity': identity, 'n_trials': n_trials, 'cost_labels': model.labels('BTCUSDT')}
