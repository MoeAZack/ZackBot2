"""EDGE05 funding_carry_xsec.v1: low-RAM streaming research runner (executes the frozen, signed preregistration).

Authoritative spec: research_evidence/prereg/funding_carry_xsec.v1.json (read by this module; nothing else is policy).

Two passes, both streaming:
  pass 1 (resumable)  one UTC decision day D at a time: eligibility + F24 from only the rows available at D, the
                      k=5 per-side cohorts, and a per-unit replay (quantity 1) of every episode the books and baselines
                      need. Each day is ONE canonical JSON line appended (flush + fsync) to `days.jsonl`; a day is
                      a pure function of its inputs, so a resumed run is byte-identical to an uninterrupted one.
  pass 2 (summary)    re-streams `days.jsonl` line by line and runs the books (sizing, leverage cap, exchange-rule
                      floors, equity path), the B0/B1/B2 baselines, metrics and gates; writes `summary.json`.

Memory: the source is asked, per symbol and per day, only for the rows of that day's window (funding ~2.5 days,
4h bars ~4 days, 1h mark bars ~1.2 days, 15 daily bars). Nothing spans the universe x history. `--max-rss-mb` aborts
cleanly between days (the completed lines stay; rerun the same command to resume).

Per-unit replay: every cost is linear in quantity, so net R = net_per_unit / stop_distance is exact for any quantity;
quantity matters only to the book layer (equity, drawdown, refusals, capacity), which pass 2 applies.

Sealed boundary: every source read is clipped to its split and refused at or after the prereg sealed cutoff
(2026-09-28T00:00:00Z) / holdout start, before and after the read (HoldoutRefused). Archive months that straddle the
cutoff are never opened. Calibration and holdout splits are never evaluated.

No network, no exchange ids, no ledger write (Integration owns the ledger), no wall clock in any result byte.
"""
from __future__ import annotations

import argparse
import bisect
import hashlib
import json
import math
import os
import random
import sys
from collections import OrderedDict, namedtuple
from dataclasses import dataclass

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import costs as C                                                                           # noqa: E402
import edge05_coverage as CV                                                                # noqa: E402
import manifest as M                                                                        # noqa: E402
import splits as S                                                                          # noqa: E402

sys.path.insert(0, M.REPO)
import feasibility as F                                                                     # noqa: E402

FORMAT = 'zb-edge05-run/1'
DAY_FORMAT = 'zb-edge05-day/1'
SUMMARY_FORMAT = 'zb-edge05-summary/1'
PREREG_REL = ('research_evidence', 'prereg', 'funding_carry_xsec.v1.json')
CANDIDATE = 'funding_carry_xsec.v1'
HOUR = 3_600_000
DAY = 24 * HOUR
TF = 4 * HOUR
TOL = C.FUNDING_TIME_TOLERANCE_MS                      # 60 s settled-calc tolerance
FUNDING_GRIDS = (1, 2, 4, 8)                           # same nominal-grid rule as range_vwap_rev.t_nom
SIDES = ('short', 'long')
ROWS = ('base_x1', 'all_variable_costs_x1.5', 'all_variable_costs_x2', 'funding_zero', 'funding_mark_adverse',
        'entry_latency_1bar')
# Constants the prereg states in prose (not as numeric fields); tests assert each one against the prereg text.
AGE_DAYS = 30                                          # universe.eligibility_at_decision "at least 30 days before D"
DAILY_BARS = 15                                        # execution.r_unit "the 15 closed 1d bars ending at D"
ENTRY_OFFSET = 4 * HOUR                                # execution.entry "i.e. 04:00 UTC"
LOOKBACK_BARS, HORIZON_BARS = 180, 6                   # windows.split_rule
B0_BOOKS = 1000                                        # baselines B0 "1000 seeded random books"
BOOT_BLOCK, BOOT_N, BOOT_SEED = 30, 10_000, 20261028   # gates.expectancy.lcb
SLIP_C_CRYPTO = 0.05                                   # slip-cal-v1 crypto c (unfitted; SLIP-C-UNFITTED)
SLIP_FALLBACK_BPS = 5.0                                # "the 0.05% unfitted fallback each market fill"
RESULT_FILES = ('days.jsonl', 'summary.json')

Bar = namedtuple('Bar', 'open_ms open high low close volume quote_volume available_ms')
Funding = namedtuple('Funding', 'time_ms rate interval_hours available_ms')


class RunnerError(RuntimeError):
    pass


class HoldoutRefused(RunnerError):
    pass


class MemoryLimit(RunnerError):
    pass


class Incomplete(Exception):
    def __init__(self, reason, at_ms=None):
        super().__init__(reason)
        self.reason, self.at_ms = reason, at_ms


def canon(obj) -> bytes:
    return json.dumps(obj, sort_keys=True, separators=(',', ':'), ensure_ascii=True, allow_nan=False).encode('ascii')


def sha(b: bytes) -> str:
    return hashlib.sha256(b).hexdigest()


# ------------------------------------------------------------------ prereg
@dataclass(frozen=True)
class Params:
    k: int
    lag_ms: int
    min_eligible: int
    stop_mult: float
    hold_ms: int
    start_usdt: float
    risk: float
    lev_cap: float
    cutoff_ms: int
    expected: dict
    gates: dict


def prereg_path(repo: str = M.REPO) -> str:
    return os.path.join(repo, *PREREG_REL)


def load_prereg(path: str | None = None) -> tuple[dict, bytes]:
    raw = open(path or prereg_path(), 'rb').read()
    return json.loads(raw), raw


def params_of(p: dict) -> Params:
    """Validate the frozen prereg and pull every numeric the runner uses from it (fail closed)."""
    if p.get('format') != 'zb-edge05-prereg/1' or p.get('candidate_id') != CANDIDATE:
        raise RunnerError('not the funding_carry_xsec.v1 prereg')
    if p.get('signed') is not True or not str(p.get('status', '')).startswith('FROZEN'):
        raise RunnerError('the prereg is not frozen and signed; refusing to evaluate')
    w = p['windows']
    cutoff = min(S.parse_utc(w['sealed_cutoff']), S.parse_utc(w['holdout']['start']), S.parse_utc(CV.SEALED_CUTOFF))
    for win in [w['calibration'], w['train'], *w['walk_forward']]:
        if S.parse_utc(win['end']) > cutoff:
            raise HoldoutRefused(f'window ending {win["end"]} reaches past the sealed cutoff {S.utc(cutoff)}')
    sig, sel, ex = p['signal'], p['selection'], p['execution']
    if sel['k'] != sel['max_positions_per_book'] or 2 * sel['k'] >= p['universe']['min_eligible']:
        raise RunnerError('prereg k / min_eligible inconsistent')
    acct = ex['account']
    return Params(k=sel['k'], lag_ms=sig['availability_lag_seconds'] * 1000, min_eligible=p['universe']['min_eligible'],
                  stop_mult=float(ex['stop_atr_mult']), hold_ms=ex['holding_cap_hours'] * HOUR,
                  start_usdt=float(acct['start_usdt']), risk=float(acct['risk_fraction_per_position']),
                  lev_cap=float(acct['max_gross_leverage_per_book']), cutoff_ms=cutoff,
                  expected={int(k): v for k, v in sig['expected_events_per_grid'].items()}, gates=p['gates'])


def split_plan(p: dict) -> S.SplitPlan:
    w = p['windows']
    rows = [{'name': 'calibration', **w['calibration']}, {'name': 'train', **w['train']}]
    rows += [{'name': 'walk_forward', 'fold': f['fold'], 'start': f['start'], 'end': f['end']} for f in w['walk_forward']]
    rows.append({'name': 'holdout', 'start': w['holdout']['start'], 'end': w['holdout']['end']})
    return S.SplitPlan([{k: r[k] for k in ('name', 'fold', 'start', 'end') if k in r} for r in rows],
                       interval='4h', lookback_bars=LOOKBACK_BARS, horizon_bars=HORIZON_BARS)


def evaluated_keys(plan: S.SplitPlan) -> list[str]:
    return [k for k in plan.keys() if k == 'train' or k.startswith('walk_forward')]


def decision_days(plan: S.SplitPlan, key: str, prm: Params) -> list[int]:
    """D (00:00 UTC) whose entry/hold sits in the split's decision range and whose exit bar is fully available
    inside the split (exit bar open + 4h <= split end, so its row never reads past the boundary)."""
    if key not in evaluated_keys(plan):
        raise HoldoutRefused(f'{key} is never evaluated (calibration = warm-up only, holdout = sealed)')
    lo, hi = plan.access_range(key)
    first, last = plan.decision_range(key)
    if hi > prm.cutoff_ms:
        raise HoldoutRefused(f'{key} ends after the sealed cutoff')
    out, d = [], -(-lo // DAY) * DAY
    while d < hi:
        entry = d + ENTRY_OFFSET
        if first <= entry <= last and entry + prm.hold_ms + TF <= hi:
            out.append(d)
        d += DAY
    return out


# ------------------------------------------------------------------ guarded source
class Guard:
    """Every read is clipped to [lo_bound, hi_bound) of the current split and the sealed cutoff; a request or a row
    beyond it is refused (fail closed), checked before AND after the underlying read."""

    def __init__(self, src, cutoff_ms: int):
        self.src, self.cutoff = src, cutoff_ms
        self.lo_bound, self.hi_bound = None, cutoff_ms

    def scope(self, lo: int, hi: int):
        if hi > self.cutoff:
            raise HoldoutRefused(f'scope end {S.utc(hi)} is after the sealed cutoff')
        self.lo_bound, self.hi_bound = lo, hi

    def _pre(self, lo, hi):
        if hi > self.cutoff or hi > self.hi_bound:
            raise HoldoutRefused(f'read to {S.utc(hi)} refused (sealed cutoff / split end {S.utc(self.hi_bound)})')
        if self.lo_bound is not None and lo < self.lo_bound:
            raise HoldoutRefused(f'read from {S.utc(lo)} crosses the split start {S.utc(self.lo_bound)}')

    def _post(self, rows, lo, hi):
        for r in rows:
            t = r[0]
            avail = r.available_ms if hasattr(r, 'available_ms') else t
            if not lo <= t < hi or avail > self.hi_bound or t >= self.cutoff or avail > self.cutoff:
                raise HoldoutRefused(f'source returned a row at {t} outside [{lo}, {hi}) / past the sealed boundary')
        return rows

    def funding(self, sym, lo, hi):
        self._pre(lo, hi)
        return self._post(self.src.funding(sym, lo, hi), lo, hi)

    def bars(self, sym, interval, lo, hi):
        self._pre(lo, hi)
        return self._post(self.src.bars(sym, interval, lo, hi), lo, hi)

    def mark(self, sym, lo, hi):
        self._pre(lo, hi)
        return self._post(self.src.mark(sym, lo, hi), lo, hi)

    def first_ms(self, sym):
        return self.src.first_ms(sym)


class ArchiveSource:
    """Binance archive store, indexed from a streamed manifest (never loaded whole). Months are parsed on demand into
    a small LRU; a month file whose last row is at/after the sealed cutoff (the straddle month) is never opened."""

    SERIES = {('fundingRate', None): 'funding', ('klines', '4h'): '4h', ('klines', '1d'): '1d',
              ('markPriceKlines', '1h'): 'mark'}

    def __init__(self, manifest_path: str, store: str, symbols, cutoff_ms: int, cache_months: int = 512):
        self.store, self.cutoff, self.cap = store, cutoff_ms, cache_months
        want, h = set(symbols), hashlib.sha256()
        with open(manifest_path, 'rb') as f:
            for chunk in iter(lambda: f.read(1 << 20), b''):
                h.update(chunk)
        self.manifest_sha256 = h.hexdigest()
        self.index: dict = {}
        self.first: dict = {}
        self.straddle_skipped = 0
        for e in CV.iter_file_entries(manifest_path):
            kind = self.SERIES.get((e.get('series'), e.get('interval')))
            if kind is None or e.get('symbol') not in want:
                continue
            first, last = e['first_open_ms'], e['last_open_ms']
            if first is None:
                continue
            fk = (e['symbol'], kind)
            self.first[fk] = first if fk not in self.first else min(self.first[fk], first)
            if last >= cutoff_ms:
                self.straddle_skipped += 1
                continue
            meta = M.archive_meta(e['path'])
            self.index.setdefault(fk, []).append((meta['month_start_ms'], meta['month_end_ms'], e['path'], e['sha256'],
                                                  e['rows']))
        for v in self.index.values():
            v.sort()
        self._lru: OrderedDict = OrderedDict()

    def identity(self) -> dict:
        return {'kind': 'binance-archive', 'manifest_file_sha256': self.manifest_sha256}

    def _month(self, path, sha256, rows, kind):
        if path in self._lru:
            self._lru.move_to_end(path)
            return self._lru[path]
        ap = M.contained(self.store, path)
        _, published, data = M.read_archive(ap, path)
        if published != sha256:
            raise RunnerError(f'{path}: bytes no longer match the manifest sha256')
        raw = M.parse_archive_csv(path, data, M.archive_meta(path))
        del data
        if kind == 'funding':
            out = [Funding(int(r[0]), float(r[2]), int(r[1]), int(r[0])) for r in raw]
        else:
            iv = M.INTERVALS['1h' if kind == 'mark' else kind]
            out = [Bar(int(r[0]), float(r[1]), float(r[2]), float(r[3]), float(r[4]), float(r[5]), float(r[7]),
                       int(r[0]) + iv) for r in raw]
        if len(out) != rows:
            raise RunnerError(f'{path}: {len(out)} rows != manifest {rows}')
        keys = [r[0] for r in out]
        self._lru[path] = (keys, out)
        while len(self._lru) > self.cap:
            self._lru.popitem(last=False)
        return keys, out

    def _rows(self, sym, kind, lo, hi):
        out = []
        for m_lo, m_hi, path, sha256, rows in self.index.get((sym, kind), ()):
            if m_hi <= lo or m_lo >= hi:
                continue
            keys, rs = self._month(path, sha256, rows, kind)
            out.extend(rs[bisect.bisect_left(keys, lo):bisect.bisect_left(keys, hi)])
        return out

    def funding(self, sym, lo, hi):
        return self._rows(sym, 'funding', lo, hi)

    def bars(self, sym, interval, lo, hi):
        return self._rows(sym, interval, lo, hi)

    def mark(self, sym, lo, hi):
        return self._rows(sym, 'mark', lo, hi)

    def first_ms(self, sym):
        return {'funding': self.first.get((sym, 'funding')), '4h': self.first.get((sym, '4h'))}


# ------------------------------------------------------------------ signal
def t_nom(row) -> int:
    """Nominal funding instant (identical rule to range_vwap_rev.t_nom): whole hour within 60 s, on the row's
    1/2/4/8h grid from 00:00 UTC; anything else is unaccountable."""
    h = (row.time_ms + HOUR // 2) // HOUR * HOUR
    iv = row.interval_hours
    if abs(row.time_ms - h) > TOL or type(iv) is not int or iv not in FUNDING_GRIDS or h % (iv * HOUR):
        raise Incomplete('funding_off_grid', row.time_ms)
    return h


def nominal(rows):
    return [Funding(t_nom(r), r.rate, r.interval_hours, r.available_ms) for r in rows]


def f24(sym: str, rows, D: int, prm: Params) -> float:
    """F24 at D from the loaded funding rows. Raises Incomplete(reason) when the symbol is ineligible.
    prereg signal.runner_requirement: filter to T + lag <= D BEFORE the sequence check, and assert it."""
    lo = D - DAY
    near = [r for r in rows if lo - 9 * HOUR <= r.time_ms <= D + TOL]
    nom = nominal(near)
    avail = [r for r in nom if r.time_ms + prm.lag_ms <= D]
    anchors = [r for r in avail if r.time_ms <= lo]
    if not anchors:
        raise Incomplete('funding_no_anchor')
    seq = [r for r in avail if r.time_ms >= anchors[-1].time_ms]
    if any(r.time_ms + prm.lag_ms > D for r in seq):
        raise RunnerError('runner_requirement violated: a row with T + lag > D reached the sequence check')
    try:
        C.check_funding_sequence(sym, seq, start_ms=lo, end_ms=D)
    except C.CostError:
        raise Incomplete('funding_sequence')
    win = [r for r in seq if D - DAY - prm.lag_ms < r.time_ms <= D - prm.lag_ms]
    ivs = {r.interval_hours for r in win} | {anchors[-1].interval_hours}
    if len(ivs) == 1:
        iv = ivs.pop()
        want = prm.expected.get(iv, 24 // iv)
        if len(win) != want:
            raise Incomplete('funding_count')
    return math.fsum(r.rate for r in win)


def select(f: dict, k: int) -> dict:
    """prereg selection: rank by F24, ties by symbol ascending. short = k highest, long = k lowest."""
    hi = sorted(f, key=lambda s: (-f[s], s))[:k]
    lo = sorted(f, key=lambda s: (f[s], s))[:k]
    return {'short': hi, 'long': lo, 'b2_short': lo, 'b2_long': hi}


# ------------------------------------------------------------------ costs / replay
@dataclass(frozen=True)
class Stress:
    name: str
    mult: float = 1.0
    funding: str = 'actual'            # 'actual' | 'zero'
    mark_bps: float = 0.0
    latency_bars: int = 0


STRESS_ROWS = {
    'base_x1': Stress('base_x1'),
    'all_variable_costs_x1.5': Stress('all_variable_costs_x1.5', mult=1.5),
    'all_variable_costs_x2': Stress('all_variable_costs_x2', mult=2.0),
    'funding_zero': Stress('funding_zero', funding='zero'),
    'funding_mark_adverse': Stress('funding_mark_adverse', mark_bps=C.STRESS['funding_mark_adverse'].mark_adverse_bps),
    'entry_latency_1bar': Stress('entry_latency_1bar', latency_bars=1),
}


def funding_paid(side: str, rate: float, mark: float, st: Stress) -> float:
    """Funding PAID (+) / received (-) per unit at one event. Stress multiplies only costs: paid x m, received / m."""
    if st.funding == 'zero':
        return 0.0
    c = C.funding_cost(side, 1.0, [(0, rate, mark)], C.Scenario('e05', mark_adverse_bps=st.mark_bps))
    return c * st.mult if c > 0 else c / st.mult


def slip(bars4: dict, at: int) -> float:
    """slip-v1 on the 15 closed 4h bars ending at `at`, c = slip-cal-v1 crypto; never below the 0.05% unfitted
    fallback (open point: the prereg names both; the larger, more conservative one is charged)."""
    win = []
    for o in range(at - 15 * TF, at, TF):
        b = bars4.get(o)
        if b is None:
            raise Incomplete('incomplete.slip_history', o)
        win.append(b)
    return max(C.slip_bps(win, SLIP_C_CRYPTO), SLIP_FALLBACK_BPS)


def _tick_round(px, tick, action):
    return C.round_adverse(px, tick, action) if tick else px


def replay(side: str, D: int, st: Stress, dist: float, day: dict, rule, prm: Params, split_hi: int) -> dict:
    """Per-unit (qty 1) replay of one episode. Raises Incomplete for anything unaccountable."""
    sgn = 1.0 if side == 'long' else -1.0
    act_in, act_out = ('buy', 'sell') if side == 'long' else ('sell', 'buy')
    scen = C.Scenario(st.name, fee_mult=st.mult, slip_mult=st.mult)
    tick = (rule or {}).get('tick')
    entry = D + ENTRY_OFFSET + st.latency_bars * TF
    exit_sched = entry + prm.hold_ms
    if exit_sched + TF > split_hi:
        raise Incomplete('incomplete.split_end', exit_sched)
    bars4 = day['bars4']
    eb = bars4.get(entry)
    if eb is None:
        raise Incomplete('incomplete.gap', entry)
    ref_in = eb.open
    fill_in = C.fill_price(ref_in, act_in, slip(bars4, entry), scen, tick=tick)
    stop = _tick_round(fill_in - dist, tick, 'sell') if side == 'long' else _tick_round(fill_in + dist, tick, 'buy')
    if not stop > 0 or not dist > 0:
        raise Incomplete('incomplete.stop', entry)
    fee_in = C.fee(fill_in, 'taker', C.CRYPTO, scen)
    closes, exit_t, ref_out, why, gap, bps_at = [], None, None, None, False, None
    for j in range(prm.hold_ms // TF):
        o = entry + j * TF
        b = bars4.get(o)
        if b is None:
            raise Incomplete('incomplete.gap', o)
        through = b.open <= stop if side == 'long' else b.open >= stop
        touched = b.low <= stop if side == 'long' else b.high >= stop
        if through or touched:                        # gap/open first, then conservative stop-first
            ref_out, gap = C.stop_reference(side, stop, b.open)
            exit_t, why, bps_at = (o if gap else o + TF), 'exit.stop', o
            break
        closes.append((o + TF, b.close))
    if exit_t is None:
        xb = bars4.get(exit_sched)
        if xb is None:
            raise Incomplete('incomplete.gap', exit_sched)
        ref_out, exit_t, why, bps_at = xb.open, exit_sched, 'exit.time', exit_sched
    fill_out = C.fill_price(ref_out, act_out, slip(bars4, bps_at), scen, gap=gap, tick=tick)
    fee_out = C.fee(fill_out, 'taker', C.CRYPTO, scen)
    # funding: costs.py rule entry <= T < exit, every event proven present (phase anchor + continuity)
    try:
        nom = [r for r in nominal([r for r in day['funding'] if entry - 9 * HOUR <= r.time_ms <= exit_t + TOL])
               if r.time_ms < exit_t]
    except Incomplete:
        raise Incomplete('incomplete.funding', entry)
    anchors = [r for r in nom if r.time_ms <= entry]
    if not anchors:
        raise Incomplete('incomplete.funding', entry)
    seq = [r for r in nom if r.time_ms >= anchors[-1].time_ms]
    try:
        C.check_funding_sequence('e05', seq, start_ms=entry, end_ms=exit_t)
    except C.CostError:
        raise Incomplete('incomplete.funding', entry)
    held = [r for r in seq if entry <= r.time_ms < exit_t]
    marks = day['mark']
    events = []
    for r in held:
        mb = marks.get(r.time_ms - HOUR)              # funding-mark-proxy-v1: the 1h mark bar ending at T
        if mb is None:
            raise Incomplete('incomplete.mark', r.time_ms)
        events.append((r.time_ms, r.rate, mb.close))
    paid = [funding_paid(side, rate, mk, st) for _, rate, mk in events]
    funding_u = -math.fsum(paid)                      # received (+) / paid (-), the net_formula sign
    gross_u = sgn * (ref_out - ref_in)
    fill_pnl = sgn * (fill_out - fill_in)
    fees_u = fee_in + fee_out
    net_u = fill_pnl - fees_u + funding_u
    mtm = [sgn * (c - fill_in) - fee_in - math.fsum(p for (t, _, _), p in zip(events, paid) if t < tc)
           for tc, c in closes]
    return {'st': 'OK', 'why': why, 'gap': gap, 'entry_ms': entry, 'exit_ms': exit_t, 'ref_in': ref_in,
            'fill_in': fill_in, 'ref_out': ref_out, 'fill_out': fill_out, 'stop': stop, 'dist': dist,
            'gross_u': gross_u, 'fill_adj_u': fill_pnl - gross_u, 'fees_u': fees_u, 'funding_u': funding_u,
            'net_u': net_u, 'r': net_u / dist, 'n_funding': len(events), 'mtm_u': mtm}


def safe_replay(*a, **kw) -> dict:
    try:
        return replay(*a, **kw)
    except Incomplete as e:
        return {'st': 'INCOMPLETE', 'why': e.reason, 'at_ms': e.at_ms}


# ------------------------------------------------------------------ one day (pass 1)
def load_day(src: Guard, sym: str, D: int, split_hi: int) -> dict:
    """The only reads of a day, per symbol: bounded windows around D (never the full range)."""
    f_lo, f_hi = D - DAY - 9 * HOUR - TOL, min(D + 2 * TF + DAY + TOL, split_hi)
    return {
        'funding': src.funding(sym, f_lo, f_hi),
        'bars4': {b.open_ms: b for b in src.bars(sym, '4h', D + ENTRY_OFFSET - 15 * TF,
                                                    min(D + ENTRY_OFFSET + DAY + 2 * TF, split_hi))},
        'bars1d': src.bars(sym, '1d', D - DAILY_BARS * DAY, D),
        'mark': {b.open_ms: b for b in src.mark(sym, D + ENTRY_OFFSET - HOUR,
                                                min(D + ENTRY_OFFSET + TF + DAY, split_hi))},
    }


def daily_dist(bars1d, D: int, prm: Params) -> float:
    want = [D - (DAILY_BARS - i) * DAY for i in range(DAILY_BARS)]
    if [b.open_ms for b in bars1d] != want:
        raise Incomplete('bars_1d')
    d = prm.stop_mult * C.tr_sma14(bars1d)
    if not d > 0:
        raise Incomplete('dist')
    return d


def run_day(src: Guard, symbols, D: int, key: str, prm: Params, rules: dict, split_hi: int) -> dict:
    elig, inel, days = {}, {}, {}
    for sym in symbols:
        fm = src.first_ms(sym)
        if fm.get('funding') is None or fm.get('4h') is None or max(fm['funding'], fm['4h']) > D - AGE_DAYS * DAY:
            inel['age'] = inel.get('age', 0) + 1
            continue
        day = load_day(src, sym, D, split_hi)
        try:
            dist = daily_dist(day['bars1d'], D, prm)
            elig[sym] = (f24(sym, day['funding'], D, prm), dist)
        except Incomplete as e:
            inel[e.reason] = inel.get(e.reason, 0) + 1
            continue
        days[sym] = day
    rec = {'format': DAY_FORMAT, 'D': S.utc(D), 'split': key, 'n_universe': len(symbols), 'eligible': len(elig),
           'ineligible': dict(sorted(inel.items())), 'no_cohort': len(elig) < prm.min_eligible}
    if rec['no_cohort']:
        return rec
    f = {s: v[0] for s, v in elig.items()}
    sel = select(f, prm.k)
    rec['f24'] = dict(sorted(f.items()))
    rec['dist'] = {s: v[1] for s, v in sorted(elig.items())}
    base = {}
    for side in SIDES:
        base[side] = {}
        for s in sorted(elig):
            ep = safe_replay(side, D, STRESS_ROWS['base_x1'], elig[s][1], days[s], rules.get(s), prm, split_hi)
            base[side][s] = {'st': ep['st'], 'r': ep.get('r'), 'why': ep['why']}
    rec['base'] = base
    rec['b2'] = {'short': sel['b2_short'], 'long': sel['b2_long']}
    prim = {}
    for side in SIDES:
        prim[side] = []
        for s in sel[side]:
            bars4 = days[s]['bars4']
            qv = [bars4.get(o) for o in range(D + ENTRY_OFFSET - DAY, D + ENTRY_OFFSET, TF)]
            rows = {name: safe_replay(side, D, st, elig[s][1], days[s], rules.get(s), prm, split_hi)
                    for name, st in STRESS_ROWS.items()}
            prim[side].append({'symbol': s, 'f24': f[s], 'rows': rows,
                               'qv24': None if any(b is None or b.quote_volume is None for b in qv)
                               else math.fsum(b.quote_volume for b in qv)})
    rec['primary'] = prim
    return rec


# ------------------------------------------------------------------ memory guard
def rss_mb() -> float:
    try:
        if os.name == 'nt':
            import ctypes
            from ctypes import wintypes

            class PMC(ctypes.Structure):
                _fields_ = [('cb', wintypes.DWORD), ('PageFaultCount', wintypes.DWORD),
                            ('PeakWorkingSetSize', ctypes.c_size_t), ('WorkingSetSize', ctypes.c_size_t),
                            ('QuotaPeakPagedPoolUsage', ctypes.c_size_t), ('QuotaPagedPoolUsage', ctypes.c_size_t),
                            ('QuotaPeakNonPagedPoolUsage', ctypes.c_size_t),
                            ('QuotaNonPagedPoolUsage', ctypes.c_size_t), ('PagefileUsage', ctypes.c_size_t),
                            ('PeakPagefileUsage', ctypes.c_size_t)]
            c = PMC()
            c.cb = ctypes.sizeof(PMC)
            k32 = ctypes.WinDLL('kernel32')
            k32.GetCurrentProcess.restype = wintypes.HANDLE
            psapi = ctypes.WinDLL('psapi')
            psapi.GetProcessMemoryInfo.argtypes = [wintypes.HANDLE, ctypes.POINTER(PMC), wintypes.DWORD]
            if psapi.GetProcessMemoryInfo(k32.GetCurrentProcess(), ctypes.byref(c), c.cb):
                return c.WorkingSetSize / 2 ** 20
            return 0.0
        with open('/proc/self/statm') as f:
            return int(f.read().split()[1]) * os.sysconf('SC_PAGE_SIZE') / 2 ** 20
    except Exception:                                  # noqa: BLE001 - unknown platform: the guard cannot measure
        return 0.0


# ------------------------------------------------------------------ pass 1 driver (resumable)
def _fsync_append(fh, line: bytes):
    fh.write(line)
    fh.flush()
    os.fsync(fh.fileno())


def identity_doc(prereg_raw: bytes, symbols, rules: dict, source_identity: dict, keys) -> dict:
    return {'format': FORMAT, 'candidate_id': CANDIDATE, 'prereg_sha256': sha(prereg_raw),
            'universe': list(symbols), 'rules_sha256': sha(canon(rules)), 'source': source_identity,
            'splits': list(keys), 'slip': {'c': SLIP_C_CRYPTO, 'fallback_bps': SLIP_FALLBACK_BPS},
            'rows': list(ROWS), 'boundaries': {'holdout_read': False, 'ledger_written': False, 'mainnet': False}}


def _resume_point(path: str, plan_keys: list) -> int:
    """Lines already complete, validated in lockstep against the plan (streamed). A torn last line is truncated."""
    if not os.path.exists(path):
        return 0
    with open(path, 'rb+') as fh:
        data_end = fh.seek(0, 2)
        if data_end:
            fh.seek(max(0, data_end - 1))
            if fh.read(1) != b'\n':                    # torn tail: cut back to the last newline
                pos = data_end
                while pos > 0:
                    step = min(65536, pos)
                    fh.seek(pos - step)
                    chunk = fh.read(step)
                    i = chunk.rfind(b'\n')
                    if i >= 0:
                        pos = pos - step + i + 1
                        break
                    pos -= step
                fh.truncate(pos)
    n = 0
    with open(path, 'rb') as fh:
        for line in fh:
            d = json.loads(line)
            if n >= len(plan_keys) or (d.get('split'), d.get('D')) != plan_keys[n]:
                raise RunnerError(f'days.jsonl line {n + 1} does not match the run plan; refusing to resume')
            n += 1
    return n


def run(outdir: str, src, symbols, rules: dict, prereg: dict, prereg_raw: bytes, *, max_rss_mb: float | None = None,
        dry_run: bool = False, only_keys=None, progress=None, stop_after_days: int | None = None) -> dict:
    prm = params_of(prereg)
    plan = split_plan(prereg)
    keys = [k for k in evaluated_keys(plan) if only_keys is None or k in only_keys]
    guard = Guard(src, prm.cutoff_ms)
    schedule = [(k, d) for k in keys for d in decision_days(plan, k, prm)]
    ident = identity_doc(prereg_raw, symbols, rules, src.identity(), keys)
    if dry_run:
        return dry_report(guard, symbols, rules, schedule, plan, prm, ident)
    os.makedirs(outdir, exist_ok=True)
    idp = os.path.join(outdir, 'run.json')
    ib = json.dumps(ident, sort_keys=True, indent=1).encode('ascii') + b'\n'
    if os.path.exists(idp):
        if open(idp, 'rb').read() != ib:
            raise RunnerError('run.json identity differs from this command; refusing to resume into it')
    else:
        with open(idp, 'wb') as fh:
            _fsync_append(fh, ib)
    if os.path.exists(os.path.join(outdir, 'summary.json')):
        raise RunnerError('summary.json exists: this run is complete and sealed')
    days_path = os.path.join(outdir, 'days.jsonl')
    plan_keys = [(k, S.utc(d)) for k, d in schedule]
    done = _resume_point(days_path, plan_keys)
    did = 0
    with open(days_path, 'ab') as fh:
        for i in range(done, len(schedule)):
            k, D = schedule[i]
            lo, hi = plan.access_range(k)
            guard.scope(lo, hi)
            rec = run_day(guard, symbols, D, k, prm, rules, hi)
            _fsync_append(fh, canon(rec) + b'\n')
            did += 1
            if progress:
                progress(i + 1, len(schedule), k, rec['D'])
            if max_rss_mb is not None and rss_mb() > max_rss_mb:
                raise MemoryLimit(f'RSS {rss_mb():.0f} MB > --max-rss-mb {max_rss_mb:g} after {rec["D"]}; '
                                  f'{i + 1}/{len(schedule)} days done, rerun the same command to resume')
            if stop_after_days is not None and did >= stop_after_days:
                return {'status': 'PARTIAL', 'days_done': i + 1, 'days_total': len(schedule)}
    summ = summarize(days_path, prereg, rules, keys)
    sp = os.path.join(outdir, 'summary.json')
    tmp = sp + '.tmp'
    with open(tmp, 'wb') as fh:
        _fsync_append(fh, json.dumps(summ, sort_keys=True, indent=1, allow_nan=False).encode('ascii') + b'\n')
    os.replace(tmp, sp)
    return summ


def dry_report(guard: Guard, symbols, rules, schedule, plan, prm, ident) -> dict:
    """Inputs and coverage only: metadata, the schedule, the rules, and the guard; no row read, no result."""
    missing_meta = [s for s in symbols if None in (guard.first_ms(s).get('funding'), guard.first_ms(s).get('4h'))]
    per_split = {}
    for k, d in schedule:
        per_split.setdefault(k, []).append(d)
    return {'format': FORMAT, 'mode': 'dry-run', 'identity_sha256': sha(canon(ident)),
            'cutoff': S.utc(prm.cutoff_ms), 'symbols': len(symbols), 'symbols_without_metadata': missing_meta,
            'symbols_without_rules': [s for s in symbols if s not in rules],
            'splits': {k: {'days': len(v), 'first_D': S.utc(v[0]), 'last_D': S.utc(v[-1])} for k, v in per_split.items()},
            'plan_digest': plan.digest}


# ------------------------------------------------------------------ pass 2 (streamed summary)
def _floor(q, step):
    return F.round_step(q, step) if step else q


class Book:
    """One side x one cost row x one split: sizing (1% of closed equity / 1R), the 3x per-book leverage cap (whole
    cohort scaled by one factor), floors, the closed + mark-to-market equity path."""

    def __init__(self, prm: Params):
        self.prm = prm
        self.e0 = self.eq = self.peak = prm.start_usdt
        self.maxdd = 0.0
        self.cum_r = self.peak_r = self.rdd = 0.0
        self.uw_start = None
        self.uw_max = 0
        self.first_D = self.last_D = None
        self.r, self.cohort_r, self.cohort_mean = [], [], []
        self.refused = {}
        self.incomplete = 0
        self.selected = 0
        self.notional_traded = []
        self.cap_ratio = []
        self.exposure_ms = 0
        self.decomp = {'gross': 0.0, 'fill_adj': 0.0, 'fees': 0.0, 'funding': 0.0}
        self.sym_abs = {}
        self.f24_sign = {'pos': 0, 'zero': 0, 'neg': 0}

    def _mark(self, v, D):
        if v > self.peak:
            self.peak = v
        self.maxdd = max(self.maxdd, (self.peak - v) / self.peak if self.peak > 0 else 1.0)

    def cohort(self, D: int, positions, rules):
        self.first_D = D if self.first_D is None else self.first_D
        self.last_D = D
        live = []
        for p in positions:
            self.selected += 1
            ep = p['ep']
            v = p['f24']
            self.f24_sign['pos' if v > 0 else 'neg' if v < 0 else 'zero'] += 1
            if ep['st'] != 'OK':
                self.incomplete += 1
                continue
            live.append(p)
        E = self.eq
        sized = []
        for p in live:
            ep, rule = p['ep'], rules.get(p['symbol'])
            qraw = E * self.prm.risk / ep['dist']
            sized.append([p, rule, qraw, _floor(qraw, (rule or {}).get('step'))])
        notional = math.fsum(q * x[0]['ep']['fill_in'] for x in sized for q in [x[3]])
        if notional > self.prm.lev_cap * E and notional > 0:
            f = self.prm.lev_cap * E / notional
            for x in sized:
                x[3] = _floor(x[2] * f, (x[1] or {}).get('step'))
        fills = []
        for p, rule, qraw, q in sized:
            chk = F.size_check(qraw, q, p['ep']['fill_in'], rule)
            if chk['ok'] is not True:
                self.refused[chk['code']] = self.refused.get(chk['code'], 0) + 1
                continue
            fills.append((p, chk['qty']))
        if not fills:
            return
        n_bars = max(len(p['ep']['mtm_u']) for p, _ in fills)
        for j in range(n_bars):
            self._mark(E + math.fsum(q * (p['ep']['mtm_u'][j] if j < len(p['ep']['mtm_u']) else p['ep']['net_u'])
                                     for p, q in fills), D)
        pnl = math.fsum(q * p['ep']['net_u'] for p, q in fills)
        self.eq = E + pnl
        self._mark(self.eq, D)
        if self.eq >= self.peak:
            self.uw_start = None
        elif self.uw_start is None:
            self.uw_start = D
        if self.uw_start is not None:
            self.uw_max = max(self.uw_max, (D - self.uw_start) // DAY)
        rs = []
        for p, q in fills:
            ep = p['ep']
            rs.append(ep['r'])
            d = ep['dist']
            self.decomp['gross'] += ep['gross_u'] / d
            self.decomp['fill_adj'] += ep['fill_adj_u'] / d
            self.decomp['fees'] -= ep['fees_u'] / d
            self.decomp['funding'] += ep['funding_u'] / d
            self.sym_abs[p['symbol']] = self.sym_abs.get(p['symbol'], 0.0) + abs(ep['r'])
            self.notional_traded.append(q * (ep['fill_in'] + ep['fill_out']) / E)
            if p.get('qv24'):
                self.cap_ratio.append(q * ep['fill_in'] / p['qv24'])
        self.exposure_ms += max(p['ep']['exit_ms'] - p['ep']['entry_ms'] for p, _ in fills)
        self.r.extend(rs)
        self.cohort_r.append(math.fsum(rs))
        self.cohort_mean.append(math.fsum(rs) / len(rs))
        self.cum_r += math.fsum(rs)
        self.peak_r = max(self.peak_r, self.cum_r)
        self.rdd = max(self.rdd, self.peak_r - self.cum_r)


def _pf(rs):
    pos = math.fsum(r for r in rs if r > 0)
    neg = -math.fsum(r for r in rs if r < 0)
    return None if neg == 0 else pos / neg


def _pct(xs, q):
    if not xs:
        return None
    s = sorted(xs)
    return s[min(len(s) - 1, max(0, int(math.floor(q * (len(s) - 1)))))]


def _mean(xs):
    return math.fsum(xs) / len(xs) if xs else None


def bootstrap(xs, block=BOOT_BLOCK, n=BOOT_N, seed=BOOT_SEED) -> dict:
    """Moving-block bootstrap of the mean (non-circular blocks, uniform starts, concatenated and cut to len(xs)).
    lcb95 = 5th percentile of the resampled means (one-sided); p = (#means <= 0 + 1) / (n + 1)."""
    m = len(xs)
    if m == 0:
        return {'lcb95': None, 'p': None, 'n': 0}
    b = min(block, m)
    pre = [0.0]
    for x in xs:
        pre.append(pre[-1] + x)
    starts = m - b + 1
    nb, last = divmod(m, b)
    rng = random.Random(seed)
    means = []
    for _ in range(n):
        tot = 0.0
        for _ in range(nb):
            s = rng.randrange(starts)
            tot += pre[s + b] - pre[s]
        if last:
            s = rng.randrange(starts)
            tot += pre[s + last] - pre[s]
        means.append(tot / m)
    means.sort()
    return {'lcb95': means[int(0.05 * n)], 'p': (sum(1 for v in means if v <= 0) + 1) / (n + 1), 'n': m}


def b0_seed(side: str, D_iso: str, i: int) -> int:
    return int.from_bytes(hashlib.sha256(f'{CANDIDATE}|{side}|{D_iso}|{i}'.encode('ascii')).digest()[:8], 'big')


def _book_metrics(b: Book) -> dict:
    span_days = ((b.last_D - b.first_D) // DAY + 1) if b.first_D is not None else 0
    ann = ((b.eq / b.e0) ** (365.0 / span_days) - 1.0) if span_days and b.eq > 0 else None
    tot = math.fsum(b.r)
    return {'episodes': len(b.r), 'selected': b.selected, 'incomplete': b.incomplete, 'refused': dict(sorted(b.refused.items())),
            'incomplete_share': (b.incomplete / b.selected) if b.selected else None,
            'cohort_days': len(b.cohort_r), 'mean_r': _mean(b.r), 'total_r': tot, 'pf': _pf(b.r),
            'final_equity': b.eq, 'max_equity_dd': b.maxdd, 'closed_r_dd': b.rdd, 'longest_underwater_days': b.uw_max,
            'annualized_return': ann, 'calmar': (ann / b.maxdd) if ann is not None and b.maxdd > 0 else None,
            'total_r_over_closed_r_dd': (tot / b.rdd) if b.rdd > 0 else None,
            'exposure_share': (b.exposure_ms / (span_days * DAY)) if span_days else None,
            'worst_cohort_r': min(b.cohort_r) if b.cohort_r else None, 'p01_episode_r': _pct(b.r, 0.01),
            'decomposition_r': dict(b.decomp), 'f24_sign_at_selection': dict(b.f24_sign),
            'funding_contribution_r': b.decomp['funding'],
            'price_contribution_r': b.decomp['gross'] + b.decomp['fill_adj'] + b.decomp['fees'],
            'daily_turnover_mean': (math.fsum(b.notional_traded) / len(b.cohort_r)) if b.cohort_r else None,
            'median_notional_over_qv24': _pct(b.cap_ratio, 0.5)}


def summarize(days_path: str, prereg: dict, rules: dict, keys) -> dict:
    """Pass 2: stream days.jsonl once; books per (split, side, row), baselines per (split, side)."""
    prm = params_of(prereg)
    books = {}                                         # (split, side, row) -> Book
    b2 = {}                                            # (split, side) -> Book
    b1 = {}                                            # (split, side) -> [sum, n]
    b0 = {side: [[0.0, 0] for _ in range(B0_BOOKS)] for side in SIDES}     # combined walk-forward
    b0_split = {}
    counts = {}
    for line in open(days_path, 'rb'):
        d = json.loads(line)
        k, D = d['split'], S.parse_utc(d['D'])
        c = counts.setdefault(k, {'days': 0, 'no_cohort_days': 0, 'ineligible': {}})
        c['days'] += 1
        for r, n in d['ineligible'].items():
            c['ineligible'][r] = c['ineligible'].get(r, 0) + n
        if d['no_cohort']:
            c['no_cohort_days'] += 1
            continue
        for side in SIDES:
            for row in ROWS:
                bk = books.setdefault((k, side, row), Book(prm))
                bk.cohort(D, [{'symbol': p['symbol'], 'f24': p['f24'], 'qv24': p['qv24'], 'ep': p['rows'][row]}
                              for p in d['primary'][side]], rules)
            base = d['base'][side]
            acc = b1.setdefault((k, side), [0.0, 0])
            for s, ep in base.items():
                if ep['st'] == 'OK':
                    acc[0] += ep['r']
                    acc[1] += 1
            # B2 report-only: sign-flipped selection, base costs, R only (the per-unit base replay)
            bb = b2.setdefault((k, side), [0.0, 0])
            for s in d['b2'][side]:
                if base[s]['st'] == 'OK':
                    bb[0] += base[s]['r']
                    bb[1] += 1
            elig = sorted(base)
            per = b0_split.setdefault((k, side), [[0.0, 0] for _ in range(B0_BOOKS)])
            for i in range(B0_BOOKS):
                for s in random.Random(b0_seed(side, d['D'], i)).sample(elig, prm.k):
                    if base[s]['st'] == 'OK':
                        per[i][0] += base[s]['r']
                        per[i][1] += 1
                        if k.startswith('walk_forward'):
                            b0[side][i][0] += base[s]['r']
                            b0[side][i][1] += 1
    out = {'format': SUMMARY_FORMAT, 'candidate_id': CANDIDATE, 'splits': {}, 'counts': counts,
           'labels': ['SLIP-C-UNFITTED', C.FUNDING_MARK_LABEL, 'STATIC-40-SURVIVOR-BIASED', 'NOT-A-LEDGER-RECORD']}
    for k in keys:
        out['splits'][k] = {}
        for side in SIDES:
            rows = {row: _book_metrics(books[(k, side, row)]) for row in ROWS if (k, side, row) in books}
            acc, bb, per = b1.get((k, side), [0.0, 0]), b2.get((k, side), [0.0, 0]), b0_split.get((k, side))
            b0m = sorted(s / n for s, n in per if n) if per else []
            out['splits'][k][side] = {'rows': rows, 'B1_mean_r': acc[0] / acc[1] if acc[1] else None,
                                      'B2_mean_r': bb[0] / bb[1] if bb[1] else None,
                                      'B0_p95_mean_r': _pct(b0m, 0.95), 'B0_median_mean_r': _pct(b0m, 0.5)}
    out['walk_forward'] = gates(books, b1, b0, keys, prm)
    return out


def _combined(books, keys, side, row, prm):
    wf = [k for k in keys if k.startswith('walk_forward')]
    bs = [books[(k, side, row)] for k in wf if (k, side, row) in books]
    rs = [r for b in bs for r in b.r]
    cm = [x for b in bs for x in b.cohort_mean]
    cum = peak = dd = 0.0
    for b in bs:
        for x in b.cohort_r:
            cum += x
            peak = max(peak, cum)
            dd = max(dd, peak - cum)
    days = sum(((b.last_D - b.first_D) // DAY + 1) for b in bs if b.first_D is not None)
    growth = math.prod(b.eq / b.e0 for b in bs) if bs else None
    ann = (growth ** (365.0 / days) - 1.0) if days and growth and growth > 0 else None
    maxdd = max((b.maxdd for b in bs), default=None)
    return {'books': bs, 'folds': wf, 'r': rs, 'cohort_means': cm, 'closed_r_dd': dd, 'ann': ann, 'maxdd': maxdd,
            'total_r': math.fsum(rs)}


def gates(books, b1, b0, keys, prm: Params) -> dict:
    g = prm.gates
    res = {}
    for side in SIDES:
        c = _combined(books, keys, side, 'base_x1', prm)
        if not c['books']:
            res[side] = {'verdict': 'NOT_RUN'}
            continue
        x15 = _combined(books, keys, side, 'all_variable_costs_x1.5', prm)
        x2 = _combined(books, keys, side, 'all_variable_costs_x2', prm)
        mean = _mean(c['r'])
        boot = bootstrap(c['cohort_means'])
        fold_means = [_mean(b.r) for b in c['books']]
        inc = [(b.incomplete / b.selected) if b.selected else 0.0 for b in c['books']]
        b0m = sorted(s / n for s, n in b0[side] if n)
        b1s = [b1.get((k, side), [0.0, 0]) for k in c['folds']]
        b1_mean = (sum(a[0] for a in b1s) / sum(a[1] for a in b1s)) if sum(a[1] for a in b1s) else None
        dd = g['drawdown_and_tail']
        tiers = dd['tiers']
        ratio = (c['total_r'] / c['closed_r_dd']) if c['closed_r_dd'] > 0 else math.inf
        calmar = (c['ann'] / c['maxdd']) if c['ann'] is not None and c['maxdd'] else (math.inf if c['ann'] and c['ann'] > 0 else None)
        ordinary = c['maxdd'] <= tiers['ordinary']['base_max_equity_dd_le'] and c['closed_r_dd'] < tiers['ordinary']['closed_r_dd_lt']
        rescue = (c['maxdd'] <= tiers['profitability_rescue']['base_max_equity_dd_le'] and calmar is not None
                  and calmar >= tiers['profitability_rescue']['annualized_net_return_over_max_dd_ge']
                  and ratio >= tiers['profitability_rescue']['total_net_r_over_closed_r_dd_ge'])
        worst = min((x for b in c['books'] for x in b.cohort_r), default=None)
        uw = max(b.uw_max for b in c['books'])
        ceilings = (x15['maxdd'] is not None and x15['maxdd'] <= dd['x1_5_max_equity_dd_le']
                    and uw <= dd['longest_underwater_days_le'] and worst is not None
                    and worst >= dd['worst_cohort_net_r_ge'])
        sym_abs = {}
        for b in c['books']:
            for s, v in b.sym_abs.items():
                sym_abs[s] = sym_abs.get(s, 0.0) + v
        tot_abs = math.fsum(sym_abs.values())
        shares = sorted((v / tot_abs for v in sym_abs.values()), reverse=True) if tot_abs else []
        caps = [x for b in c['books'] for x in b.cap_ratio]
        conc = g['concentration']
        checks = {
            'data': len(c['books']) == len(c['folds']) and all(x <= 0.02 for x in inc),
            'activity': len(c['r']) >= 1000 and all(len(b.cohort_r) >= 200 for b in c['books'])
                        and len(c['books']) == len(c['folds']),
            'fold_stability': sum(1 for m in fold_means if m is not None and m > 0) >= g['fold_stability']['positive_folds_min'],
            'expectancy': (mean is not None and mean > g['expectancy']['combined_mean_net_r_gt']
                           and (_pf(c['r']) or 0) > g['expectancy']['combined_pf_gt']
                           and boot['lcb95'] is not None and boot['lcb95'] > 0),
            'baselines': (mean is not None and bool(b0m) and mean > _pct(b0m, 0.95)
                          and b1_mean is not None and mean > b1_mean),
            'stress': (_mean(x15['r']) or 0) > g['stress']['x1_5_mean_net_r_gt'],
            'drawdown_and_tail': (ordinary or rescue) and ceilings
                                 and c['maxdd'] <= tiers['profitability_rescue']['base_max_equity_dd_le'],
            'concentration': bool(shares) and shares[0] <= conc['max_symbol_share'] and sum(shares[:3]) <= conc['max_top3_share'],
            'capacity': bool(caps) and _pct(caps, 0.5) <= g['capacity']['median_notional_over_prior_24h_quote_volume_le'],
        }
        res[side] = {'checks': checks, 'mean_r': mean, 'pf': _pf(c['r']), 'episodes': len(c['r']),
                     'fold_mean_r': fold_means, 'fold_incomplete_share': inc, 'bootstrap': boot,
                     'B0_p95_mean_r': _pct(b0m, 0.95), 'B1_mean_r': b1_mean,
                     'x1_5_mean_r': _mean(x15['r']), 'x2_mean_r': _mean(x2['r']),
                     'promotion_x2_ok': (_mean(x2['r']) or 0) > g['stress']['x2_mean_net_r_gt_for_promotion'],
                     'max_equity_dd': c['maxdd'], 'x1_5_max_equity_dd': x15['maxdd'], 'closed_r_dd': c['closed_r_dd'],
                     'annualized_return': c['ann'], 'calmar': calmar if calmar != math.inf else None,
                     'total_r_over_closed_r_dd': ratio if ratio != math.inf else None,
                     'dd_tier': 'ordinary' if ordinary else 'profitability_rescue' if rescue else None,
                     'longest_underwater_days': uw, 'worst_cohort_r': worst,
                     'max_symbol_share': shares[0] if shares else None, 'top3_share': sum(shares[:3]) if shares else None,
                     'median_notional_over_qv24': _pct(caps, 0.5)}
    ps = {s: res[s]['bootstrap']['p'] for s in SIDES if 'bootstrap' in res[s] and res[s]['bootstrap']['p'] is not None}
    holm = holm_reject(ps, 0.05)
    for side in SIDES:
        if 'checks' not in res[side]:
            continue
        ch = res[side]['checks']
        ch['multiplicity'] = holm.get(side, False)
        if not ch['data']:
            res[side]['verdict'] = 'INCOMPLETE'
        else:
            res[side]['verdict'] = 'PASS' if all(ch.values()) else 'FAIL'
    return res


def holm_reject(ps: dict, alpha: float) -> dict:
    out, m = {}, len(ps)
    for i, (s, p) in enumerate(sorted(ps.items(), key=lambda kv: (kv[1], kv[0]))):
        if p <= alpha / (m - i):
            out[s] = True
        else:
            break
    return {s: out.get(s, False) for s in ps}


# ------------------------------------------------------------------ CLI
def load_rules(path: str, symbols) -> dict:
    """{symbol: {tick, step, min_qty, min_notional[, max_qty]}} floats, from a flat map or a
    zackbot.exchange_rules/1 snapshot. A symbol without a rule is kept and its fills are refused (code 'unknown')."""
    doc = json.load(open(path, encoding='utf-8'))
    src = doc.get('symbols', doc) if isinstance(doc, dict) else {}
    out = {}
    for s in symbols:
        r = src.get(s)
        if isinstance(r, dict):
            out[s] = {k: float(r[k]) for k in ('tick', 'step', 'min_qty', 'min_notional', 'max_qty') if r.get(k) is not None}
    return out


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument('--store', required=True, help='Binance archive store root (read-only)')
    ap.add_argument('--rules', required=True, help='exchange rules JSON (tick/step/min_qty/min_notional per symbol)')
    ap.add_argument('--out', help='result directory (append-only days.jsonl + summary.json)')
    ap.add_argument('--max-rss-mb', type=float, default=None)
    ap.add_argument('--dry-run', action='store_true', help='validate inputs/coverage; write nothing')
    ap.add_argument('--split', action='append', help='limit to these split keys (default: train + walk_forward)')
    ap.add_argument('--cache-months', type=int, default=512)
    a = ap.parse_args(argv)
    try:
        prereg, raw = load_prereg()
        prm = params_of(prereg)
        uni = os.path.join(M.REPO, *prereg['universe']['artifact'].split('/'))
        if sha(open(uni, 'rb').read()) != prereg['source']['universe_sha256']:
            raise RunnerError('universe artifact hash differs from the prereg')
        symbols = CV.load_universe(uni)
        man = os.path.join(M.REPO, *prereg['source']['manifest'].split('/'))
        src = ArchiveSource(man, a.store, symbols, prm.cutoff_ms, a.cache_months)
        rules = load_rules(a.rules, symbols)
        if a.dry_run:
            print(json.dumps(run(a.out or '', src, symbols, rules, prereg, raw, dry_run=True, only_keys=a.split),
                             indent=1, sort_keys=True))
            return 0
        if not a.out:
            raise RunnerError('--out is required unless --dry-run')
        import runlock
        with runlock.hold(M.REPO, 'edge05-runner', {'out': os.path.abspath(a.out), 'splits': a.split}, argv=argv):
            res = run(a.out, src, symbols, rules, prereg, raw, max_rss_mb=a.max_rss_mb, only_keys=a.split,
                      progress=lambda i, n, k, d: print(f'{i}/{n} {k} {d}', file=sys.stderr, flush=True))
        print(json.dumps({s: res['walk_forward'][s].get('verdict') for s in SIDES}, sort_keys=True))
        return 0
    except MemoryLimit as e:
        print(f'ABORTED (memory): {e}', file=sys.stderr)
        return 4
    except HoldoutRefused as e:
        print(f'REFUSED (sealed): {e}', file=sys.stderr)
        return 2
    except RunnerError as e:
        print(f'REFUSED: {e}', file=sys.stderr)
        return 3


if __name__ == '__main__':
    sys.exit(main())
