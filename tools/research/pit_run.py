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
  5. missing data (M1, policy `POLICY`, docs/newcore/research/MISSING_DATA_POLICY_v1.md): ONE causal book pass per
     row, never a re-run without a symbol. The first unaccountable event (missing candle, a valid entry without its
     next-open candle, funding-rate gap, funding-mark gap, end of series / delisting without a notice) halts the row
     at the last fully accounted cycle; a lot open at the split end is split-end censoring. Such a row, and every
     aggregate / summary / final holding it, is explicitly INCOMPLETE and never evidence (sealed without evidence);
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

STRAT-02-PIT-01 (`trend_ema_mom_short.v1`): the same runner under an explicit immutable `RunProfile` (`SHORT_RUN`),
selected only by run_r4.run_pit from the committed short gate: short prereg + ledger, `tape_short`, `configs_short`
(capped primary + 12 neighbours + B1 perp short book + B2; no uncapped trial), short accounting / B0 (tick rules) /
B1 / B2 on book month-R blocks, seed 20261012, side-bound Access records. Holm gates on the PROGRAM family
(trend_ema_mom + trend_ema_mom_short, n >= 35, `program_multiplicity`); the own-family 17 is reported, never gating.
The profile binding enters the run config, tier config (run id), and the distinct zb-strat02-pit-* summary / detail
formats, so long and short runs or resumes never alias. `LONG_RUN` is the default and the STRAT-01 path unchanged.
"""
from __future__ import annotations

import bisect
import dataclasses
from datetime import datetime, timezone
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
import missing_data as MD                                                                   # noqa: E402
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
SHORT_NOT_EVALUATED = (
    'follower tiers 100 / 200 / 500 USDT and RULES-BACKFILLED share (E11)',
    'per-side split (short only rule; the long side is STRAT-01 and never pooled with it)',
    'stop-first vs target-first (one stop, no target: every exit bar is single-level)',
    'MDE at 80% power (holdout-only quantity)')


@dataclasses.dataclass(frozen=True)
class RunProfile:
    """STRAT-02-PIT-01: the immutable candidate profile of one PIT run (candidate, family, side, evaluator entry
    point, registered configurations, seed, artifact formats). Routed by run_r4.run_pit from the committed gate only;
    the long profile is the default everywhere and is byte-for-byte the STRAT-01 path."""
    tag: str
    candidate_id: str
    family: str
    side: str
    function: str
    primary: str
    dev_only: str | None
    seed: int
    summary_format: str
    detail_format: str
    configs: object                                 # () -> list[dict]: the registered configurations only
    not_evaluated: tuple

    def binding(self) -> dict:
        """What binds a run to this profile (run config, tier config, artifacts): long and short never alias."""
        return {'tag': self.tag, 'candidate_id': self.candidate_id, 'family': self.family, 'side': self.side,
                'function': self.function, 'primary': self.primary, 'summary_format': self.summary_format}


class PitRunError(ValueError):
    pass


class Incomplete(Exception):
    def __init__(self, reason):
        super().__init__(reason)
        self.reason = reason


# ------------------------------------------------------------------ missing-data policy (M1, versioned)
# docs/newcore/research/MISSING_DATA_POLICY_v1.md (Codex M1 release 6153717648). One causal book pass per row; the
# FIRST unaccountable event halts the row at the last fully accounted cycle (state restored, nothing forward-filled,
# nothing re-run without a symbol); the row and every aggregate holding it are INCOMPLETE and never evidence.
POLICY, COMPLETE, INCOMPLETE = MD.POLICY, MD.COMPLETE, MD.INCOMPLETE
GAP_CLASS = {'incomplete.gap': 'missing_bar', 'incomplete.slip_history': 'missing_bar',
             'incomplete.delisted': 'delisting', 'incomplete.funding': 'funding_rate_gap',
             'incomplete.funding_mark': 'funding_mark_gap', 'incomplete.split_end': 'split_censoring'}
HALTED = 'incomplete.book_halt'                    # an open lot stopped only because another symbol halted the row
PHASE_RANK = {'bar': 0, 'exit': 1, 'next_open': 2, 'fill': 2, 'funding': 3, 'decision': 4}   # in-cycle order


class _Halt(Exception):
    """The first unaccountable event of a book row (raised inside a cycle; the cycle is rolled back)."""

    def __init__(self, symbol, reason, first_ms, phase):
        super().__init__(reason)
        self.symbol, self.reason, self.first_ms, self.phase = symbol, reason, first_ms, phase


def gap_reason(x, t: int) -> str:
    """Primary reason of a symbol with no bar at cycle t: end-of-series (no later bar in this tape: delisting or data
    end) or a missing candle. A LABEL only: the accounting (halt at the last valid cycle) is identical for both."""
    return 'incomplete.delisted' if not x.avail or x.avail[-1] < t else 'incomplete.gap'


def require_complete(doc, what: str = 'result') -> None:
    """Fail closed unless `doc` is explicitly COMPLETE (the shared predicate `missing_data.check`: a missing status,
    INCOMPLETE, a COMPLETE still carrying incomplete rows / excluded symbols / a censor, or secondary rows without a
    primary are refused). Every evidence consumer uses this same predicate."""
    try:
        MD.check(doc, what)
    except MD.MissingDataError as e:
        raise PitRunError(str(e))


def _decision_inputs(x, t: int):
    """None when a SCHEDULED decision of symbol `x` at t (B1 long Monday, B1 short rebalance) has its exact input
    window: the bar closing at t and the T.WINDOW-bar signal read behind it, from the series start on (bars before
    the first one are warm-up, never missing). Else the primary reason: a missing decision candle / a hole inside
    the read changes membership, weights or the weekly schedule, so it is unaccountable, never a silent skip."""
    if not x.avail or x.avail[0] > t:
        return None                                     # not listed yet: no decision input exists
    if x.rec.get(t) is None:
        return gap_reason(x, t)
    return None if x.missing_in(t - (T.WINDOW - 1) * TF, t) is None else 'incomplete.gap'


def canon(obj) -> bytes:
    return json.dumps(obj, sort_keys=True, separators=(',', ':'), ensure_ascii=True, allow_nan=False).encode('ascii')


def sha(obj) -> str:
    return hashlib.sha256(canon(obj)).hexdigest()


# ------------------------------------------------------------------ the static-universe dataset (pit.Dataset)
class StaticDataset(P.Dataset):
    """`pit.Dataset` over a promotion-eligible manifest, serving exactly one tier of the owner-approved static list as
    members at every time (contract section 0.1: no PIT membership reconstruction), labelled CURRENT-UNIVERSE /
    SURVIVOR-BIASED. Every row is still read through the manifest's SHA-256 re-check (`Dataset.rows`)."""

    def __init__(self, manifest: dict, store: str, static: dict, symbols, *, exact_subset=False,
                 minute_overlay=None, minute_overlay_store=None):
        M.validate(manifest)
        if not M.promotion_eligible(manifest):
            raise PitRunError('a reproduction-only manifest can never feed the PIT run')
        if static.get('format') != SU.FORMAT or static.get('label') != LABEL or static.get('owner_approved') is not True:
            raise PitRunError('the static universe must be the owner-approved CURRENT-UNIVERSE / SURVIVOR-BIASED list')
        ranked = [s['symbol'] for s in static['symbols']]
        symbols = list(symbols)
        if exact_subset:
            # EDGE-04's preregistered headline is CORE-10-ELIGIBLE-9: the frozen rank order with HYPE removed for
            # insufficient full-window history.  This opt-in accepts only an explicit order-preserving subset; the
            # existing tier path remains prefix-only by default and cannot silently select a subset.
            if not symbols or len(symbols) != len(set(symbols)) or [s for s in ranked if s in set(symbols)] != symbols:
                raise PitRunError('an exact static subset must be unique, known, and preserve the frozen rank order')
        elif not symbols or symbols != ranked[:len(symbols)]:
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
        self.minute_overlay, self.minute_overlay_store = None, None
        self._minute_overlay_files, self._minute_overlay_cache = {}, {}
        if minute_overlay is not None:
            import edge04_1m_acquire as E1
            E1.validate_overlay_manifest(minute_overlay)
            if minute_overlay_store is None or minute_overlay["source_manifest_digest"] != self.digest:
                raise PitRunError("EDGE-04 1m overlay store/source identity mismatch")
            for f in minute_overlay["files"]:
                key = (f["symbol"], f["day"])
                if key in self._minute_overlay_files:
                    raise PitRunError("duplicate EDGE-04 1m overlay coverage")
                self._minute_overlay_files[key] = f
            self.minute_overlay, self.minute_overlay_store = minute_overlay, minute_overlay_store

    @property
    def latency_identity(self):
        return {"source_manifest_digest": self.digest,
                "overlay_manifest_digest": None if self.minute_overlay is None else self.minute_overlay["digest"]}

    def exact_minute_bar(self, symbol: str, open_ms: int):
        """Unique base-or-overlay 1m bar. An overlap is contamination, never overlay precedence."""
        import bisect as _bisect
        found = []
        for f in self.files("klines", symbol, "1m"):
            if f["first_open_ms"] is None or not f["first_open_ms"] <= open_ms <= f["last_open_ms"]:
                continue
            rows = self.rows(f); times = self._keys[f["path"]][0]
            i = _bisect.bisect_left(times, open_ms)
            if i < len(rows) and rows[i].open_ms == open_ms:
                found.append(rows[i])
        if self.minute_overlay is not None:
            import edge04_1m_acquire as E1
            day = datetime.fromtimestamp(open_ms / 1000, tz=timezone.utc).strftime("%Y-%m-%d")
            f = self._minute_overlay_files.get((symbol, day))
            if f is not None:
                rows = self._minute_overlay_cache.get(f["path"])
                if rows is None:
                    try:
                        rows = E1.overlay_rows(self.minute_overlay_store, f)
                    except E1.AcquireError as e:
                        raise PitRunError(str(e))
                    self._minute_overlay_cache[f["path"]] = rows
                i = _bisect.bisect_left([b.open_ms for b in rows], open_ms)
                if i < len(rows) and rows[i].open_ms == open_ms:
                    found.append(rows[i])
        if len(found) > 1:
            raise PitRunError(f"{symbol} {open_ms}: duplicate base/overlay 1m coverage")
        return found[0] if found else None


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
        self._bps, self._ft, self._holes = {}, None, None

    def add(self, t, rec):
        if 'b' in rec:
            o, h, lo, c = rec['b']
            self.bars.append(P.Bar(t - TF, o, h, lo, c, None, None, t))
            self.avail.append(t)
            self.rec[t] = rec
        for tm, rate, ih, mk in rec['f']:
            self.fund.append(P.Funding(tm, rate, ih, tm))
            self.mark[tm] = mk

    def missing_in(self, lo, hi):
        """The first bar-close time in the exact scheduled window [lo, hi] (on the bar grid) with no bar although the
        series had started (a hole) or had already ended; None = exactly covered (times before the first bar are
        warm-up, never missing)."""
        if not self.avail or hi < self.avail[0]:
            return None
        if self._holes is None:
            have = set(self.avail)
            self._holes = [g for g in range(self.avail[0] + TF, self.avail[-1], TF) if g not in have]
        k = bisect.bisect_left(self._holes, lo)
        if k < len(self._holes) and self._holes[k] <= hi:
            return self._holes[k]
        return max(lo, self.avail[-1] + TF) if hi > self.avail[-1] else None

    def bps(self, at_ms, vol, c):
        """slip-v1 bps from the EXACT scheduled lookback closing at `at_ms` (TR-SMA14: 15 bars; Wilder: the read
        window). A hole inside it is unaccountable (`incomplete.gap`; never an older bar substituted); too little
        history from the series start is `incomplete.slip_history`."""
        key = (at_ms, vol)
        if key not in self._bps:
            n = C.SLIP_BARS if vol == C.TR_SMA14 else T.WINDOW
            lo = at_ms - (n - 1) * TF
            miss = self.missing_in(lo, at_ms)
            if miss is not None:
                self._bps[key] = 'incomplete.gap' if miss <= self.avail[-1] else 'incomplete.delisted'
            else:
                try:
                    self._bps[key] = C.slip_bps(self.bars[bisect.bisect_left(self.avail, lo):
                                                          bisect.bisect_right(self.avail, at_ms)], c, vol)
                except C.CostError:
                    self._bps[key] = 'incomplete.slip_history'
        if isinstance(self._bps[key], str):
            raise Incomplete(self._bps[key])
        return self._bps[key]

    def covered(self, entry_ms, end_ms):
        """Causal funding proof of a lot held from `entry_ms` through `end_ms`, from the rows known before `end_ms`
        only (the prefix of the `events` check at exit, so it never fires on data that passes at exit)."""
        ts = [f.time_ms for f in self.fund] if self._ft is None else self._ft
        self._ft = ts
        rows = self.fund[bisect.bisect_left(ts, entry_ms - FUND_SPAN):bisect.bisect_left(ts, end_ms)]
        try:
            C.check_funding_sequence(self.s, rows, start_ms=entry_ms, end_ms=end_ms)
        except C.CostError:
            raise Incomplete('incomplete.funding')

    def bar_events(self, entry_ms, bo, t):
        """The funding events charged in the played bar [bo, t) of a lot held since `entry_ms`, after the causal
        rate proof through t; a missing mark is a funding-mark gap (never a filled mark)."""
        self.covered(entry_ms, t)
        ev = [(f.time_ms, f.rate, self.mark[f.time_ms]) for f in self.fund
              if bo <= f.time_ms < t and f.time_ms >= entry_ms]
        if any(m is None for _, _, m in ev):
            raise Incomplete('incomplete.funding_mark')
        return ev

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


class TapeData(tuple):
    """(times, syms) of one attested tape plus its direction `side` (STRAT-02: bound to the accounting side)."""
    side = 'long'


def tape_side(outputs) -> str:
    """The ONE direction of a tape: no cycle marker = the legacy long tape; a short tape marks EVERY cycle
    `side: 'short'`. A mixed or unknown marker refuses (fail closed)."""
    sides = {o.get('side', 'long') for o in outputs}
    if len(sides) > 1:
        raise PitRunError(f'mixed tape direction markers {sorted(sides)}: one tape is one side')
    side = sides.pop() if sides else 'long'
    if side not in SIDES:
        raise PitRunError(f'unknown tape direction marker {side!r}')
    return side


def _bind_side(tape, side: str) -> None:
    if side not in SIDES:
        raise PitRunError("side must be 'long' or 'short'")
    have = getattr(tape, 'side', 'long')
    if have != side:
        raise PitRunError(f'a {have} tape cannot be accounted as {side} (tape direction != accounting direction)')


def read_tape(outputs) -> tuple[list[int], dict]:
    side = tape_side(outputs)
    times, syms = [], {}
    for o in outputs:
        times.append(o['t'])
        for rec in o['r']:
            syms.setdefault(rec['s'], Sym(rec['s'])).add(o['t'], rec)
    if any(b <= a for a, b in zip(times, times[1:])):
        raise PitRunError('tape times must be strictly increasing')
    for x in syms.values():
        x.fund.sort(key=lambda f: f.time_ms)
    out = TapeData((times, syms))
    out.side = side
    return out


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


def _dec(v):
    """An exact Decimal of a tape distance (Decimal / decimal text as attested) or a float (its repr); None when it
    is not a finite positive number."""
    try:
        d = v if isinstance(v, Decimal) else Decimal(v) if isinstance(v, str) else Decimal(repr(float(v)))
    except (ArithmeticError, TypeError, ValueError):
        return None
    return d if d.is_finite() and d > 0 else None


def short_stop(fill: float, dist, tick):
    """STRAT-02 stop = ceil_tick(fill + dist), computed ONCE in exact decimal arithmetic (the tick-rounded fill's repr
    plus the attested distance text), so an on-grid sum is never widened by float noise. Returns (stop, risk per
    unit = stop - fill) as floats of the exact values, or None for a non-finite / non-positive distance or fill."""
    d = _dec(dist)
    f = _dec(fill)
    if d is None or f is None:
        return None
    u = Decimal(str(tick))
    stop = ((f + d) / u).to_integral_value(rounding=ROUND_CEILING) * u
    risk = stop - f
    if not risk > 0:
        return None
    return float(stop), float(risk)


def _dist_exact(cfg, rec):
    """The attested stop-distance TEXT of a configuration's entry (exact; never via float)."""
    v = rec['e'].get(str(cfg['k'])) if cfg['kind'] == 'sig' else rec['d']
    return None if v is None else Decimal(v)


def size_short(snapshot: float, fill: float, dist, ref: float, notional: float, rule: dict, book=BOOK):
    """STRAT-02 book sizing of one short entry (contract section 4): (qty, stop, None) or (None, None, refusal code).
    stop = ceil_tick(fill + dist) with `fill` the slipped sell fill already rounded DOWN; the actual per-unit risk
    stop - fill must be finite and > 0 (else `execution.stop_risk_invalid`); qty = min(1% x snapshot / risk,
    (3 x snapshot - open notional) / (signal close x 1.10)) floored to the step, never upsized; below min qty or
    qty x the executable fill below min notional = `execution.size_min`."""
    sr = short_stop(fill, dist, rule['tick'])
    if sr is None:
        return None, None, 'execution.stop_risk_invalid'
    stop, risk_u = sr
    raw = snapshot * book['risk_pct'] / risk_u
    room = book['max_leverage'] * snapshot - notional
    q = min(raw, max(room, 0.0) / (ref * (1 + book['cap_gap_buffer'])))
    q = _floor(q, rule['step']) if q > 0 else 0.0
    if q <= 0 or q < float(rule['min_qty']) or q * fill < float(rule['min_notional']):     # the executable fill
        return None, None, 'execution.size_min'
    return q, stop, None


def liq_point(wallet: float, held) -> tuple[float, float, bool]:
    """STRAT-02 PARK-only liquidation diagnostic of one bar (contract section 4): `held` = [(qty, entry, bar high)] of
    every open short. G = sum q x high, W = wallet + sum q x (entry - high); flagged when W < G / 3. Never an exit,
    a fill or a sizing input."""
    g = math.fsum(q * h for q, _, h in held)
    w = wallet + math.fsum(q * (e - h) for q, e, h in held)
    return w, g, w < g / 3


def delist_cover(*, notice_ms, opens, last_mark: float, bps: float, scen, tick) -> tuple[float, int | None, str]:
    """STRAT-02 delist cover price (prereg costs.delist, short side): with a timestamped notice, BUY at the first bar
    open STRICTLY after the notice (an open at the notice time is not causally executable) (`opens` = [(open_ms, open)] ascending) -> (fill, open_ms, 'exit.delist'); with no
    observed notice (or no open after it), BUY at the last archived mark with 5x slippage -> (fill, None,
    'exit.delist_forced'), reported separately. Buys round UP to the tick. M1: `run_book(delist=...)` covers a held
    short through this at the first open strictly after a timestamped notice; with no notice a held symbol whose bars
    end is INCOMPLETE (missing-data policy), so the forced-mark branch is never used by the book."""
    if notice_ms is not None:
        for ms, o in opens:
            if ms > notice_ms:
                return C.fill_price(o, 'buy', bps, scen, tick=tick), ms, 'exit.delist'
    return C.fill_price(last_mark, 'buy', 5.0 * bps, scen, tick=tick), None, 'exit.delist_forced'


def run_book(tape, cfg: dict, symbols, *, first: int, last: int, hi: int, slip_c: float, rules: dict, row: str = 'base',
             book=BOOK, side='long', delist=None) -> dict:
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
    `View.next_open`.

    Missing data (policy `POLICY`, M1): ONE causal pass, never a re-run without a symbol. The first event that makes the
    shared book unaccountable - a held symbol without its candle (missing candle, or end-of-series = delisting when no
    notice), a valid entry decision whose next-open candle is missing, a funding-rate or funding-mark gap of a held lot
    (proved per bar from the rows known by then), or no slip history at a fill - rolls that cycle back and HALTS the
    row: everything through the last fully accounted cycle (decisions, fills, trades, wallet, exposure, capacity,
    series) is exactly what the same tape without the event gives; the affected symbol gets its primary reason, other
    open lots `HALTED`; status INCOMPLETE. A lot still open at the split end (no observable next open) is split-end
    censoring: INCOMPLETE, nothing after it to halt. `delist` = {symbol: notice_ms}: a held lot exits at the first
    bar open strictly after the notice (`exit.delist`; a short covers through `delist_cover`); no entry after it.

    `side='short'` (STRAT-02 contract sections 3-4, the exact mirror): market SELL entry rounded DOWN to the tick after
    adverse slip, stop = ceil_tick(fill + dist), actual per-unit risk stop - fill (non-finite / non-positive = refusal
    `execution.stop_risk_invalid`); a bar OPENING at or above the stop fills at its open (gap), else a HIGH reaching it
    fills at the stop; covers are BUYs rounded UP; PnL = (fill - exit) x qty; short funding sign; MFE from lows (stop
    bar: its open), MAE from highs (stop bar: bounded by the stop); MTM = wallet + qty x (fill - close); plus the
    PARK-only liquidation diagnostic (never an exit, fill or sizing input)."""
    _bind_side(tape, side)
    short = side == 'short'
    times, syms = tape
    scen = C.STRESS[row]
    order = [s for s in symbols if s in syms]
    notice = dict(delist or {})
    wallet = book['equity0']
    lots, closes, trades, incomplete, refusals, series = {}, {}, [], [], Counter(), []
    pend_exit, pend_entry, day, day_start, halted_day = [], [], None, None, None
    cap_ms = None if cfg['cap'] is None else cfg['cap'] * TF
    liq = {'bars': 0, 'flagged_bars': 0, 'min_w_over_g': None, 'max_bar_high_over_open': None}

    def tick(s):
        return float(rules[s]['tick'])

    def lot_row(s, lot, reason, first_ms, last_ms):
        return {'symbol': s, 'entry_ms': lot['entry_ms'], 'reason': reason, 'class': GAP_CLASS.get(reason, 'halted'),
                'first_unaccountable_ms': first_ms, 'last_valid_ms': last_ms, 'qty': lot['qty'], 'fill': lot['fill'],
                'stop': lot['stop'], 'fees': lot['fees'], 'funding': lot['funding'], 'secondary': reason == HALTED}

    def close(s, ref, exit_ms, reason, gap, at, nbars, xf=None):
        nonlocal wallet
        lot, x = lots[s], syms[s]
        try:
            x.events(lot['entry_ms'], exit_ms)                      # the whole funding sequence is provably complete
            if xf is None:
                xf = C.fill_price(ref, 'buy' if short else 'sell', x.bps(at, scen.vol, slip_c), scen, gap=gap,
                                  tick=tick(s))
        except Incomplete as e:
            raise _Halt(s, e.reason, at, 'exit')
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

    halt, prev_t = None, None
    for t in times:
        snap = (wallet, {k: dict(v) for k, v in lots.items()}, len(trades), Counter(refusals), len(series),
                dict(closes))
        try:
            bo = t - TF
            rec = {s: syms[s].rec.get(t) for s in order}
            for s in [s for s in order if s in lots and rec[s] is None]:
                raise _Halt(s, gap_reason(syms[s], t), t, 'bar')      # a held symbol without its candle
            # (A) fills at the open of the bar [bo, t)
            for s, why in pend_exit:
                if s in lots and why == 'exit.delist':          # the first open strictly after the notice
                    xf = None
                    if short:
                        xf = delist_cover(notice_ms=notice[s], opens=[(bo, rec[s]['b'][0])], last_mark=0.0,
                                          bps=syms[s].bps(bo, scen.vol, slip_c), scen=scen, tick=tick(s))[0]
                    close(s, rec[s]['b'][0], bo, why, False, bo, (bo - lots[s]['entry_ms']) // TF, xf=xf)
                elif s in lots:
                    close(s, rec[s]['b'][0], bo, 'exit.signal', False, bo, (bo - lots[s]['entry_ms']) // TF)
            pend_exit = []
            if cap_ms is not None:
                for s in [s for s in order if s in lots]:
                    held = bo - lots[s]['entry_ms']
                    if held > cap_ms:
                        raise PitRunError(f'{s}: lot held past its time cap')      # unreachable: every bar is played
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
                if short and _dec(dist) is None:
                    refusals['execution.stop_risk_invalid'] += 1         # a non-finite / non-positive stop risk
                    continue
                if snapshot <= 0 or not dist or dist <= 0 or ref <= 0:
                    refusals['execution.size_min'] += 1
                    continue
                if s in notice and notice[s] <= sig_t:          # no entry once a delist notice is known
                    refusals['filter.delist_notice'] += 1
                    continue
                if rec[s] is None:                              # a valid decision with no next-open candle
                    raise _Halt(s, gap_reason(syms[s], t), t, 'next_open')
                x = syms[s]
                if short:
                    try:
                        fill = C.fill_price(rec[s]['b'][0], 'sell', x.bps(bo, scen.vol, slip_c), scen, tick=tick(s))
                    except Incomplete as e:
                        if e.reason != 'incomplete.slip_history':       # a hole in the exact lookback
                            raise _Halt(s, e.reason, bo, 'fill')
                        refusals['entry_no_slip_history'] += 1
                        continue
                    q, stop, why = size_short(snapshot, fill, dist, ref, notional, rule, book)
                    if why is not None:
                        refusals[why] += 1
                        continue
                    fee = C.fee(fill * q, 'taker', C.CRYPTO, scen)
                    wallet -= fee
                    o = rec[s]['b'][0]
                    lots[s] = {'signal_ms': sig_t, 'entry_ms': bo, 'qty': q, 'fill': fill, 'stop': stop, 'fees': fee,
                               'funding': 0.0, 'snapshot': snapshot, 'hi': o, 'lo': o}
                    continue
                try:
                    fill = C.fill_price(rec[s]['b'][0], 'buy', x.bps(bo, scen.vol, slip_c), scen, tick=tick(s))
                except Incomplete as e:
                    if e.reason != 'incomplete.slip_history':           # a hole in the exact lookback
                        raise _Halt(s, e.reason, bo, 'fill')
                    refusals['entry_no_slip_history'] += 1
                    continue
                # size from the ACTUAL per-unit stop risk: the adverse tick-rounded fill and the tick-floored stop
                # (flooring can only widen fill - stop beyond dist), so qty x (fill - stop) <= risk_pct x snapshot
                stop = _floor(fill - dist, rule['tick'])
                if stop <= 0:                               # a long stop at/below zero can never trigger: no lot
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
                if q <= 0 or q < float(rule['min_qty']) or q * fill < float(rule['min_notional']):  # executable
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
            if short:
                # (B) short, three passes over the open lots in rank order (contract sections 3-4):
                #   1. a gap stop exits at the bar OPEN (before the bar's funding events); every other lot books the
                #      bar's eligible funding events (a non-gap stop exits at the bar CLOSE, so they are charged);
                #   2. the PARK-only liquidation diagnostic on the lots still open, wallet_t = the realised wallet after
                #      the fees and funding booked up to t, every short marked at its own bar high at once, ignoring the
                #      bar's non-gap stops (never an exit, fill or sizing input);
                #   3. non-gap stops fill at the stop level and exit at the bar close; other lots update MFE / MAE.
                hits = {}
                for s in [s for s in order if s in lots]:
                    lot, (o, h, lo, c) = lots[s], rec[s]['b']
                    stop_hit = o >= lot['stop'] or h >= lot['stop']
                    ref, gap = C.stop_reference('short', lot['stop'], o) if stop_hit else (None, False)
                    if stop_hit and gap:                    # MFE: earlier lows + the open; MAE: the known adverse open
                        lot['lo'], lot['hi'] = min(lot['lo'], o), max(lot['hi'], o)
                        close(s, ref, bo, 'exit.stop', True, bo, (bo - lot['entry_ms']) // TF + 1)
                        continue
                    try:
                        ev = syms[s].bar_events(lot['entry_ms'], bo, t)
                    except Incomplete as e:
                        raise _Halt(s, e.reason, bo, 'funding')
                    amt = C.funding_cost('short', lot['qty'], ev, scen, bars_held=1,
                                         entry_notional=lot['qty'] * lot['fill'])
                    wallet -= amt
                    lot['funding'] += amt
                    hits[s] = (stop_hit, ref)
                held = [(lots[k], rec[k]['b']) for k in order if k in lots]
                if held:
                    w, g, flag = liq_point(wallet, [(lt['qty'], lt['fill'], b[1]) for lt, b in held])
                    liq['bars'] += 1
                    liq['flagged_bars'] += flag
                    if g > 0:
                        liq['min_w_over_g'] = w / g if liq['min_w_over_g'] is None else min(liq['min_w_over_g'], w / g)
                    jump = max((b[1] - b[0]) / b[0] for _, b in held)
                    liq['max_bar_high_over_open'] = jump if liq['max_bar_high_over_open'] is None else max(
                        liq['max_bar_high_over_open'], jump)
                for s in [s for s in order if s in lots]:
                    lot, (o, h, lo, c) = lots[s], rec[s]['b']
                    stop_hit, ref = hits[s]
                    if stop_hit:                            # ambiguous intrabar path: MAE bounded by the stop
                        lot['lo'], lot['hi'] = min(lot['lo'], o), max(lot['hi'], min(h, lot['stop']))
                        close(s, ref, t, 'exit.stop', False, bo, (bo - lot['entry_ms']) // TF + 1)
                        continue
                    lot['hi'], lot['lo'] = max(lot['hi'], h), min(lot['lo'], lo)
            for s in [s for s in order if s in lots and not short]:
                lot, (o, h, lo, c) = lots[s], rec[s]['b']
                stop_hit = o <= lot['stop'] or lo <= lot['stop']
                ref, gap = C.stop_reference('long', lot['stop'], o) if stop_hit else (None, False)
                if not (stop_hit and gap):
                    try:
                        ev = syms[s].bar_events(lot['entry_ms'], bo, t)
                    except Incomplete as e:
                        raise _Halt(s, e.reason, bo, 'funding')
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
                if s in lots and s in notice and notice[s] < t:
                    pend_exit.append((s, 'exit.delist'))
                elif lx and s in lots:
                    pend_exit.append((s, t))
                if le and first <= t <= last:
                    pend_entry.append((s, t, _dist_exact(cfg, r) if short else dist, r['b'][3], halted_day == d))
            if t == hi:                                     # split end: fills due at hi read the next open
                sig_due = {s for s, _ in pend_exit if s in lots}
                due = set(sig_due)
                if cap_ms is not None:
                    due |= {s for s in lots if lots[s]['entry_ms'] + cap_ms == hi}
                tag = {s: why for s, why in pend_exit}
                for s in [s for s in order if s in due]:
                    n = _next_open(syms[s], hi, hi)
                    if n is not None:
                        why = 'exit.delist' if tag.get(s) == 'exit.delist' else (
                            'exit.signal' if s in sig_due else 'exit.time_cap')
                        xf = None
                        if why == 'exit.delist' and short:
                            xf = delist_cover(notice_ms=notice[s], opens=[(hi, n)], last_mark=0.0,
                                              bps=syms[s].bps(hi, scen.vol, slip_c), scen=scen, tick=tick(s))[0]
                        close(s, n, hi, why, False, hi, (hi - lots[s]['entry_ms']) // TF, xf=xf)
                pend_exit = []
        except _Halt as h:                              # roll the cycle back: state = end of the last valid cycle
            wallet, lots, refusals, closes = snap[0], snap[1], snap[3], snap[5]
            del trades[snap[2]:], series[snap[4]:]
            halt = (h, t)
            break
        prev_t = t
    censor = None
    if halt is not None:
        h, at = halt
        for s in [s for s in order if s in lots or s == h.symbol]:
            if s not in lots:                           # a flat symbol: the decision with no next open
                incomplete.append({'symbol': s, 'entry_ms': None, 'reason': h.reason,
                                   'class': GAP_CLASS[h.reason], 'first_unaccountable_ms': h.first_ms,
                                   'last_valid_ms': prev_t, 'phase': h.phase, 'secondary': False})
                continue
            why = h.reason if s == h.symbol else HALTED        # exactly ONE primary; ties -> the frozen order
            r = dict(lot_row(s, lots[s], why, h.first_ms if s == h.symbol else at, prev_t),
                     phase=h.phase if s == h.symbol else 'halt')
            if why == HALTED:
                r['primary_ref'] = f'{h.symbol}@{h.first_ms}'
            incomplete.append(r)
        censor = {'class': GAP_CLASS[h.reason], 'reason': h.reason, 'symbol': h.symbol, 'phase': h.phase,
                  'halted': True, 'cycle_ms': at, 'first_unaccountable_ms': h.first_ms, 'last_valid_ms': prev_t}
    else:
        for s in [s for s in order if s in lots]:
            if cap_ms is not None and lots[s]['entry_ms'] + cap_ms < hi:
                raise PitRunError(f'{s}: a capped lot outlived its cap inside the split')    # unreachable
            incomplete.append(dict(lot_row(s, lots[s], 'incomplete.split_end', hi, hi), phase='split_end'))
        if incomplete:
            censor = {'class': 'split_censoring', 'reason': 'incomplete.split_end', 'symbol': None,
                      'phase': 'split_end', 'halted': False, 'cycle_ms': hi, 'first_unaccountable_ms': hi,
                      'last_valid_ms': hi}
    if censor is not None:                              # cash / exposure at the refusal, consistent with the series
        sgn = -1.0 if short else 1.0
        censor.update(wallet=wallet, mtm=series[-1][1] if series else book['equity0'], open_lots=len(lots),
                      gross_exposure=math.fsum(x['qty'] * closes.get(k, x['fill']) for k, x in lots.items()),
                      unrealised=math.fsum(sgn * x['qty'] * (closes.get(k, x['fill']) - x['fill'])
                                           for k, x in lots.items()))
    trades.sort(key=lambda r: (r['entry_ms'], r['symbol']))
    out = {'trades': trades, 'incomplete': incomplete, 'refusals': dict(sorted(refusals.items())),
           'wallet_end': wallet, 'series': series, 'status': INCOMPLETE if incomplete else COMPLETE, 'policy': POLICY}
    if censor is not None:
        out['censor'] = censor
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
            rows=('base',), side='long', delist=None) -> dict:
    """The registered book for one configuration over one split, per cost row: ONE causal `run_book` pass per row
    (missing-data policy `POLICY`: never a re-run without a symbol). A row with any incomplete record is INCOMPLETE;
    its book figures are None (the realised wallet omits the unresolved lots) and the result is never evidence."""
    tape = read_tape(outputs)
    _bind_side(tape, side)
    missing = sorted(s for s in symbols if s in tape[1] and s not in rules)
    if missing:
        raise PitRunError(f'no pinned exchange rule (tick / step / min qty / min notional) for {missing}')
    if cfg['kind'] == 'b1' and side == 'short':
        res = {'config': cfg['name'], 'book': True, 'side': 'short',
               'rows': {row: b1_short_book(outputs, symbols=symbols, first=first, last=last, hi=hi, slip_c=slip_c,
                                           rules=rules, row=row) for row in rows}}
        return dict(res, status=_status(res['rows'].values()), policy=POLICY)
    if cfg['kind'] == 'b1':
        return account_unit(outputs, cfg, first=first, last=last, hi=hi, slip_c=slip_c, rows=rows)
    out = {'config': cfg['name'], 'book': True, 'rows': {}}
    for row in rows:
        res = run_book(tape, cfg, symbols, first=first, last=last, hi=hi, slip_c=slip_c, rules=rules, row=row,
                       side=side, delist=delist)
        done = res['status'] == COMPLETE
        res.update(counted=res['trades'], excluded_symbols=[], excluded_trades=0,
                   net_pct_equity=100.0 * (res['wallet_end'] - BOOK['equity0']) / BOOK['equity0'] if done else None,
                   **(book_stats(res['series']) if done else {'mtm_max_dd_pct': None,
                                                              'longest_underwater_cycles': None}))
        if side == 'short':                     # STRAT-02 B1 pairing: the candidate's book MTM month R, same units
            res['month_r'] = month_r(res['series'], first, last) if done else None
        res.pop('series')
        out['rows'][row] = res
    base = out['rows'][rows[0]]
    out.update({k: base[k] for k in ('trades', 'counted', 'incomplete', 'excluded_symbols', 'excluded_trades',
                                     'refusals')})
    out.update(status=_status(out['rows'].values()), policy=POLICY)
    return out


def _status(rows) -> str:
    """INCOMPLETE when any row is not explicitly COMPLETE (a missing status fails closed)."""
    return COMPLETE if all(r.get('status') == COMPLETE for r in rows) else INCOMPLETE


def account_unit(outputs, cfg: dict, *, first: int, last: int, hi: int, slip_c: float, rows=tuple(C.STRESS)) -> dict:
    """Unit-quantity R per symbol (no shared book): B1 (prereg: equal-weight weekly hold scaled to equal risk) and
    per-trade diagnostics. Same fill / stop / next-open cap / funding rules as the book.
    Missing data (policy `POLICY`): the symbols share no state, so each is played causally on its own and the row is
    then cut exactly where ONE joint causal pass would halt - at the earliest cycle holding an unaccountable event (a
    held symbol without its candle, a valid entry without its next-open candle, a funding-rate / mark gap proved per
    bar, an exit without slip history). Trades closed in earlier cycles are kept (never a whole-symbol exclusion);
    lots open at that cycle are INCOMPLETE (the affected one with its reason, others `HALTED`). Without such an event,
    lots open at the split end are split-end censoring. Any incomplete record = status INCOMPLETE."""
    tape = read_tape(outputs)
    _bind_side(tape, 'long')
    times, syms = tape
    refusals, per = Counter(), {}
    for s in sorted(syms):
        x, lot, pend_entry, pend_exit = syms[s], None, None, None
        done, lots_seen, ev, sym_ref = [], [], None, []   # (cycle, trade); [entry, close cycle, lot]; (cycle, refusal)

        def close(exit_ref, exit_ms, reason, gap, at, nbars, cyc):
            try:
                r = net_r(x, entry_ref=lot['entry_ref'], entry_ms=lot['entry_ms'], dist=lot['dist'], exit_ref=exit_ref,
                          exit_ms=exit_ms, gap=gap, exit_bps_at=at, nbars=nbars, slip_c=slip_c, rows=rows)
            except Incomplete as e:
                raise _Halt(s, e.reason, at, 'exit')
            done.append((cyc, {'config': cfg['name'], 'symbol': s, 'signal_ms': lot['entry_ms'],
                               'entry_ms': lot['entry_ms'], 'exit_ms': exit_ms, 'exit_reason': reason, 'gap': gap,
                               'bars': nbars, 'r': r, 'mfe_r': (lot['hi'] - lot['fill']) / lot['dist'],
                               'mae_r': (lot['lo'] - lot['fill']) / lot['dist']}))
            lots_seen[-1][1] = cyc

        for t in times:
            try:
                rec = x.rec.get(t)
                sched = cfg['kind'] == 'b1' and first <= t <= last and _is_monday_open(t)
                if rec is None:
                    if lot is not None:
                        raise _Halt(s, gap_reason(x, t), t, 'bar')
                    if pend_entry is not None:
                        raise _Halt(s, gap_reason(x, t), t, 'next_open')
                    if sched and _decision_inputs(x, t) is not None:   # the scheduled Monday decision candle
                        raise _Halt(s, gap_reason(x, t), t, 'decision')
                    pend_exit = None
                    continue
                o, h, lo, c = rec['b']
                bo = t - TF
                if pend_exit is not None and lot is not None:
                    close(o, bo, 'exit.signal', False, bo, (bo - lot['entry_ms']) // TF, t)
                    lot = None
                pend_exit = None
                if lot is not None and cfg['cap'] is not None and bo - lot['entry_ms'] == cfg['cap'] * TF:
                    close(o, bo, 'exit.time_cap', False, bo, cfg['cap'], t)
                    lot = None
                if pend_entry is not None:
                    sig_t, dist = pend_entry
                    pend_entry = None
                    try:
                        fill = C.fill_price(o, 'buy', x.bps(bo, BASE.vol, slip_c), BASE)
                    except Incomplete as e:
                        if e.reason != 'incomplete.slip_history':       # a hole in the exact lookback
                            raise _Halt(s, e.reason, bo, 'fill')
                        sym_ref.append((t, 'entry_no_slip_history'))
                        fill = None
                    if fill is not None and cfg['stop'] and fill - dist <= 0:
                        sym_ref.append((t, 'execution.stop_nonpositive'))   # an untriggerable long stop: no lot
                    elif fill is not None:
                        lot = {'entry_ms': bo, 'entry_ref': o, 'dist': dist, 'fill': fill,
                               'stop': fill - dist if cfg['stop'] else None, 'hi': o, 'lo': o}
                        lots_seen.append([t, None, lot])
                if lot is not None:
                    stop_hit = lot['stop'] is not None and (o <= lot['stop'] or lo <= lot['stop'])
                    if not (stop_hit and C.stop_reference('long', lot['stop'], o)[1]):
                        try:                                    # the bar's funding obligation, proved causally
                            x.bar_events(lot['entry_ms'], bo, t)
                        except Incomplete as e:
                            raise _Halt(s, e.reason, bo, 'funding')
                    if stop_hit:
                        lot['hi'], lot['lo'] = max(lot['hi'], o), min(lot['lo'], lo)   # pre-stop bound
                        ref, gap = C.stop_reference('long', lot['stop'], o)
                        close(ref, bo if gap else t, 'exit.stop', gap, bo, (bo - lot['entry_ms']) // TF + 1, t)
                        lot = None
                    else:
                        lot['hi'], lot['lo'] = max(lot['hi'], h), min(lot['lo'], lo)
                if sched and lot is None and _decision_inputs(x, t) is not None:   # a hole in its exact inputs
                    raise _Halt(s, _decision_inputs(x, t), t, 'decision')
                le, lx, dist = _signal(cfg, x, rec, t)
                if lx and lot is not None:
                    pend_exit = t
                if le and lot is None and first <= t <= last:
                    if not dist or dist <= 0:
                        sym_ref.append((t, 'entry_bad_stop_distance'))
                    else:
                        pend_entry = (t, dist)
                if t == hi and lot is not None and (pend_exit is not None or (
                        cfg['cap'] is not None and lot['entry_ms'] + cfg['cap'] * TF == hi)):
                    n = _next_open(x, hi, hi)
                    if n is not None:
                        close(n, hi, 'exit.signal' if pend_exit is not None else 'exit.time_cap', False, hi,
                              (hi - lot['entry_ms']) // TF, t)
                        lot = None
            except _Halt as e:
                ev = (t, e)
                break
        per[s] = {'done': done, 'lots': lots_seen, 'event': ev, 'refusals': sym_ref}
    halts = sorted((v['event'][0], PHASE_RANK[v['event'][1].phase], s) for s, v in per.items()
                   if v['event'] is not None)              # the joint pass's order inside a cycle, then the symbol
    at = halts[0][0] if halts else None
    trades, incomplete = [], []
    for s, v in sorted(per.items()):
        trades += [tr for cyc, tr in v['done'] if at is None or cyc < at]
        refusals.update(k for c, k in v['refusals'] if at is None or c < at)
    if at is not None:
        prim = [halts[0][2]]                           # exactly ONE primary; same-cycle faults are HALTED
        ref0 = f'{prim[0]}@{per[prim[0]]["event"][1].first_ms}'
        for s, v in sorted(per.items()):
            open_at = [lt for c0, c1, lt in v['lots'] if c0 < at and (c1 is None or c1 >= at)]
            if s in prim:
                e = v['event'][1]
                incomplete.append({'symbol': s, 'entry_ms': open_at[0]['entry_ms'] if open_at else None,
                                   'reason': e.reason, 'class': GAP_CLASS[e.reason], 'phase': e.phase,
                                   'first_unaccountable_ms': e.first_ms, 'last_valid_ms': at - TF, 'secondary': False})
                continue
            for lt in open_at:
                incomplete.append({'symbol': s, 'entry_ms': lt['entry_ms'], 'reason': HALTED, 'class': 'halted',
                                   'phase': 'halt', 'first_unaccountable_ms': at, 'last_valid_ms': at - TF,
                                   'secondary': True, 'primary_ref': ref0})
    else:
        for s, v in sorted(per.items()):
            for c0, c1, lt in v['lots']:
                if c1 is None:
                    incomplete.append({'symbol': s, 'entry_ms': lt['entry_ms'], 'reason': 'incomplete.split_end',
                                       'class': 'split_censoring', 'phase': 'split_end', 'first_unaccountable_ms': hi,
                                       'last_valid_ms': hi, 'secondary': False})
    trades.sort(key=lambda r: (r['entry_ms'], r['symbol']))
    return {'config': cfg['name'], 'book': False, 'trades': trades, 'counted': list(trades), 'incomplete': incomplete,
            'excluded_symbols': [], 'excluded_trades': 0, 'refusals': dict(sorted(refusals.items())),
            'status': INCOMPLETE if incomplete else COMPLETE, 'policy': POLICY,
            **({} if at is None else {'censor': {'cycle_ms': at, 'last_valid_ms': at - TF, 'halted': True}})}


# ------------------------------------------------------------------ B1 short: the perp short benchmark book
def _is_monday_open(t: int) -> bool:
    return t % DAY == 0 and (t // DAY - 4) % 7 == 0       # 1970-01-01 was a Thursday


def b1_short_book(outputs, *, symbols, first: int, last: int, hi: int, slip_c: float, rules: dict, row: str = 'base',
                  book=BOOK) -> dict:
    """STRAT-02 B1 (contract section 7.1, Codex 6112077638): an equal-weight USD-M perpetual SHORT basket on its own
    10,000 USDT book, no spot borrow, signed actual funding (a short receives positive and pays negative rates).
      * tau = every Monday 00:00 UTC inside the decision range [first, last] plus its first bar (`first`);
      * M_tau = tier members with a closed 4h bar at tau and ATR14 defined on the contiguous tail (tape `a`), known
        at the decision close (M1: the execution open is NOT a membership filter - a decided leg whose open is
        missing halts the book INCOMPLETE, phase next_open; a listed tier symbol whose decision candle or exact
        T.WINDOW-bar input read has a hole halts it, phase decision); N = |M_tau|, N = 0 = flat until the next tau;
      * q*_s = floor_step((0.01 x E_tau / N) / (2.5 x ATR14_s(tau))) with E_tau the B1 MTM equity at tau; if
        sum(q*_s x close_s(tau) x 1.10) > 3 x E_tau every q*_s is scaled by 3 x E_tau / that sum BEFORE flooring;
      * a leg below min qty / min notional (qty x the executable sell fill, pinned rules) is set to 0 and counted;
      * at the open of the bar starting at tau each symbol trades q*_s - q_s (sell = add short, buy = reduce), taker,
        adverse slip-v1, sell rounded DOWN / buy rounded UP to the tick; a member leaving M_tau is fully covered;
      * no stop, no time cap, no other exit; funding on the held qty at each funding time T in the played bar
        [t - 4h, t) at the funding-mark proxy;
      * missing data (policy `POLICY`, M1): the first unaccountable event - a held symbol without its candle, a
        target leg without its open, a funding-rate / mark gap of a held leg (proved per bar), a fill without slip
        history - rolls that cycle back and HALTS the book (one causal pass, no symbol excluded and the book never
        continued from an unknowable wallet); status INCOMPLETE, the affected symbol with its reason, other held legs
        `HALTED`.
    Returns the MTM series and the calendar-month block R `(E_end(m) - E_start(m)) / (0.01 x E_start(m))`."""
    tape = read_tape(outputs)
    _bind_side(tape, 'short')
    times, syms = tape
    scen = C.STRESS[row]
    order = [s for s in symbols if s in syms]
    wallet = book['equity0']
    pos, closes, legs, incomplete, refusals, series = {}, {}, [], [], Counter(), []
    pending, taus, halt, prev_t = None, [], None, None

    for t in times:
        snap = (wallet, {k: dict(v) for k, v in pos.items()}, len(legs), Counter(refusals))
        try:
            bo = t - TF
            rec = {s: syms[s].rec.get(t) for s in order}
            for s in [s for s in order if s in pos and rec[s] is None]:
                raise _Halt(s, gap_reason(syms[s], t), t, 'bar')
            if pending is not None and pending[0] == bo:              # (A) rebalance fills at the open of [tau, ...)
                targets = pending[1]
                for s in order:
                    cur = pos[s]['qty'] if s in pos else 0.0
                    tgt = targets.get(s, 0.0)
                    if rec[s] is None:
                        if abs(tgt - cur) >= 1e-12:             # a decided trade with no open candle
                            raise _Halt(s, gap_reason(syms[s], t), t, 'next_open')
                        continue                                # nothing to execute: flat and irrelevant
                    x, rule = syms[s], rules[s]
                    o = rec[s]['b'][0]
                    try:
                        bps = x.bps(bo, scen.vol, slip_c)
                    except Incomplete as e:                     # missing candles behind a fill
                        raise _Halt(s, e.reason, bo, 'fill')
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
                        pos[s] = {'qty': cur + dq, 'avg': avg, 'since': pos[s]['since'] if cur else bo}
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
                try:
                    ev = syms[s].bar_events(pos[s]['since'], bo, t)
                except Incomplete as e:
                    raise _Halt(s, e.reason, bo, 'funding')
                wallet -= C.funding_cost('short', pos[s]['qty'], ev, scen, bars_held=1,
                                         entry_notional=pos[s]['qty'] * pos[s]['avg'])
            for s in order:
                if rec[s] is not None:
                    closes[s] = rec[s]['b'][3]
            mtm = wallet + math.fsum(p['qty'] * (p['avg'] - closes.get(k, p['avg'])) for k, p in pos.items())
            series.append((t, mtm))
            if first <= t <= last and (t == first or _is_monday_open(t)):  # (C) the rebalance decision at tau
                for s in order:                                 # exact inputs: never shrink M_tau silently
                    why = _decision_inputs(syms[s], t)
                    if why is not None:
                        raise _Halt(s, why, t, 'decision')
                members = [s for s in order if rec[s] is not None and rec[s].get('a') is not None]
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
        except _Halt as h:                              # roll the cycle back: state = end of the last valid cycle
            wallet, pos, refusals = snap[0], snap[1], snap[3]
            del legs[snap[2]:]
            halt = (h, t)
            break
        prev_t = t
    if halt is not None:
        h, at = halt
        for s in [s for s in order if s in pos or s == h.symbol]:
            ir = {'symbol': s, 'time_ms': at, 'last_valid_ms': prev_t, 'secondary': s != h.symbol,
                  'qty': pos[s]['qty'] if s in pos else 0.0, 'avg': pos[s]['avg'] if s in pos else None}
            if s == h.symbol:
                ir.update(reason=h.reason, **{'class': GAP_CLASS[h.reason]}, phase=h.phase,
                           first_unaccountable_ms=h.first_ms)
            else:
                ir.update(reason=HALTED, **{'class': 'halted'}, phase='halt', first_unaccountable_ms=at,
                          primary_ref=f'{h.symbol}@{h.first_ms}')
            incomplete.append(ir)
    blocks = month_r(series, first, last, book['equity0'])
    return {'config': 'B1', 'side': 'short', 'row': row, 'legs': legs, 'incomplete': incomplete,
            'excluded_symbols': [], 'refusals': dict(sorted(refusals.items())), 'rebalances': taus,
            'series': series, 'wallet_end': wallet, 'month_r': blocks if not incomplete else None,
            'status': INCOMPLETE if incomplete else COMPLETE, 'policy': POLICY}


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


LONG_RUN = RunProfile('STRAT-01', T.RULE_ID, 'trend_ema_mom', 'long', FUNCTION, PRIMARY_NAME, DEV_ONLY, SEED,
                      SUMMARY_FORMAT, DETAIL_FORMAT, configs, NOT_EVALUATED)
SHORT_RUN = RunProfile('STRAT-02', 'trend_ema_mom_short.v1', 'trend_ema_mom_short', 'short', SHORT_FUNCTION,
                       SHORT_PRIMARY_NAME, None, SHORT_SEED, 'zb-strat02-pit-summary/1', 'zb-strat02-pit-detail/1',
                       configs_short, SHORT_NOT_EVALUATED)
RUN_PROFILES = {p.candidate_id: p for p in (LONG_RUN, SHORT_RUN)}


def profile_of(env: dict) -> RunProfile:
    """The env's run profile (absent = STRAT-01). A short env must carry its binding in the run config and point at
    its own family ledger; anything cross-wired refuses before a split is opened."""
    prof = env.get('profile', LONG_RUN)
    if prof not in RUN_PROFILES.values():
        raise PitRunError(f'unknown run profile {prof!r}')
    if prof is not LONG_RUN:
        if env['run_config'].get('profile') != prof.binding():
            raise PitRunError(f'{prof.tag}: the run config is not bound to {prof.candidate_id}')
        if os.path.splitext(os.path.basename(env['ledger']))[0] != prof.family:
            raise PitRunError(f'{prof.tag}: Access records must go to the {prof.family} family ledger')
    return prof


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
    tape = read_tape(outputs)
    _bind_side(tape, side)
    times, syms = tape
    regime = {o['t']: o.get('g') for o in outputs}
    elig = b0_pool(outputs, first=first, last=last)
    cache, invalid, unmatched = {}, 0, 0

    def sim_short(s, e, h):
        x, tk = syms[s], float(rules[s]['tick'])
        if e + h * TF > hi or any((e + j * TF) not in x.rec for j in range(1, h + 1)):
            raise Incomplete('incomplete.gap')
        o0 = x.rec[e + TF]['b'][0]
        fill = C.fill_price(o0, 'sell', x.bps(e, BASE.vol, slip_c), BASE, tick=tk)
        sr = short_stop(fill, Decimal(x.rec[e]['d']), tk)
        if sr is None:
            raise Incomplete('incomplete.stop_risk_invalid')
        stop, risk_u = sr
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
           'status': acc.get('status', INCOMPLETE), 'missing_data_policy': POLICY,
           'stress_mean_r': {}}
    if acc['incomplete']:
        out['incomplete_classes'] = dict(sorted(Counter(i.get('class', '?') for i in acc['incomplete']).items()))
    by_row = acc.get('rows_trades')
    for k in rows:
        xs = [r['r'][k] for r in by_row[k]] if by_row is not None else [r['r'][k] for r in tr]
        out['stress_mean_r'][k] = math.fsum(xs) / len(xs) if xs else None
    if acc.get('book') is not None:
        out['book'] = {k: dict(v) for k, v in sorted(acc['book'].items())}
    if acc.get('liquidation') is not None:
        out['liquidation'] = {k: dict(v) for k, v in sorted(acc['liquidation'].items())}
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


def b1_short_view(v: dict) -> dict:
    """The STRAT-02 B1 perp short book account (`account`, kind b1, side short) in the shared detail / metrics shape:
    no trades (its legs are rebalances, never episodes), the book figures per row (None when a row is INCOMPLETE), the
    month R kept for the block pairing. Status is re-derived from the rows (fail closed)."""
    rows = {}
    for r, x in v['rows'].items():
        done = x.get('status') == COMPLETE
        rows[r] = dict({k: x[k] for k in ('incomplete', 'refusals', 'month_r', 'wallet_end', 'status')},
                       counted=[], trades=[], excluded_symbols=[], excluded_trades=0,
                       net_pct_equity=100.0 * (x['wallet_end'] - BOOK['equity0']) / BOOK['equity0'] if done else None,
                       **(book_stats(x['series']) if done else {'mtm_max_dd_pct': None,
                                                                'longest_underwater_cycles': None}))
    base = rows[next(iter(rows))]
    return {'config': v['config'], 'book': True, 'side': 'short', 'rows': rows, 'trades': [], 'counted': [],
            'incomplete': base['incomplete'], 'excluded_symbols': [], 'excluded_trades': 0,
            'refusals': base['refusals'], 'status': _status(rows.values()), 'policy': POLICY}


def month_blocks_short(key, first, last, cand, b1, b2) -> list[dict]:
    """STRAT-02 calendar-month blocks (prereg baselines B1 / B2): every side in the same units, the base-row book MTM
    month R (`month_r`: (E_end - E_start) / (0.01 x E_start)) of the candidate, the B1 perp short book and B2. A row
    with no month R (INCOMPLETE) gives None, never a zero; such a tier is INCOMPLETE and never evidence."""
    mr = lambda v: (v['rows']['base'].get('month_r') or {}) if v['rows'].get('base') else {}
    c, x1, x2 = mr(cand), mr(b1), mr(b2)
    return [{'block': f'{key}:{mo}', 'cand': c.get(mo), 'B2': x2.get(mo), 'B1': x1.get(mo)}
            for mo in months_of(first, last)]


# ------------------------------------------------------------------ the tier evaluation
def evaluate_tier(env: dict, tier: str, prog: PG.Progress | None) -> tuple[dict, dict]:
    """Open, evaluate and account every development split of one tier; returns (summary, detail)."""
    prof = profile_of(env)
    plan, cfgs, cfg_run = env['plan'], prof.configs(), env['run_config']
    short = prof.side == 'short'
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
        acc = P.Access(ds, plan, env['ledger'], candidate_id=prof.candidate_id, author=env['author'],
                       cairo_date=env['cairo_date'], repo=env['repo'])
        w = acc.open(key, detail={'purpose': f'{prof.tag} R4b-PIT {tier} development split (no holdout)',
                                  'tier': tier, 'symbols': list(symbols),
                                  **({'side': prof.side} if short else {})}, lineage='root')
        ev = P.evaluate(w, env['evaluator'], prof.function, list(range(lo + TF, hi + 1, TF)), perturb=True)
        res = {c['name']: account(ev, c, symbols=symbols, first=first, last=last, hi=hi, slip_c=cfg_run['slip_c'],
                                  rules=env['rules'],
                                  rows=rows if c['role'] in ('primary', 'development_only', 'baseline') else ('base',),
                                  **({'delist': env['delist_notices']} if env.get('delist_notices') and
                                     c['kind'] != 'b1' else {}), **({'side': 'short'} if short else {}))
               for c in cfgs}
        if short:
            res['B1'] = b1_short_view(res['B1'])
        regime = {o['t']: o.get('g') for o in ev}
        for v in res.values():
            for tr in v['trades']:
                tr['regime'] = regime.get(tr['signal_ms'])
        prim = res[prof.primary]
        boot = {'seed': cfg_run['seed'], 'resamples': cfg_run['resamples']}
        per_split[key] = {'primary': metrics(_acc(prim), rows=rows, boot=boot)}
        if prof.dev_only:                                  # STRAT-01 only: STRAT-02 has no uncapped variant
            per_split[key]['uncapped_development_only'] = metrics(_acc(res[prof.dev_only]), rows=rows, boot=boot)
        b0 = b0_pairs(ev, prim, first=first, last=last, hi=hi, slip_c=cfg_run['slip_c'],
                      seed_key=f'{cfg_run["seed"]}|{tier}|{key}', draws=cfg_run['b0_draws'],
                      **({'side': 'short', 'rules': env['rules']} if short else {}))
        b0_diffs += b0['diffs']
        b0_meta.update(invalid_draws=b0['invalid_draws'], unmatched_trades=b0['unmatched_trades'])
        blocks += (month_blocks_short if short else month_blocks)(key, first, last, prim, res['B1'], res['B2'])
        detail_splits[key] = {'attestation': ev.attestation_digest, 'outputs_digest': ev.attestation['outputs_digest'],
                              'b0_invalid_draws': b0['invalid_draws'], 'b0_unmatched_trades': b0['unmatched_trades'],
                              'configs': {k: _detail(v) for k, v in res.items()}}
        if prog is not None:
            prog.stage_done(stage, fold)
    return summarize(env, tier, symbols, per_split, detail_splits, blocks, b0_diffs, dict(b0_meta))


def _detail(v: dict) -> dict:
    out = {k: v[k] for k in ('trades', 'incomplete', 'excluded_symbols', 'excluded_trades', 'refusals')}
    ok = (v.get('status') == COMPLETE and not v['incomplete'] and not v['excluded_symbols']
          and all(x.get('status') == COMPLETE for x in (v.get('rows') or {}).values()))
    out.update(status=COMPLETE if ok else INCOMPLETE, policy=POLICY)                 # derived from its rows
    if v.get('book'):
        out['rows'] = {r: {'trades': x['counted'], 'status': x.get('status', INCOMPLETE),
                           **({'censor': x['censor']} if x.get('censor') else {}),
                           'book': {k: x[k] for k in ('net_pct_equity', 'mtm_max_dd_pct', 'longest_underwater_cycles',
                                                      'wallet_end')}}
                       for r, x in v['rows'].items()}
        for r, x in v['rows'].items():                     # STRAT-02 short rows only: the PARK-only diagnostic
            if 'liquidation' in x:
                out['rows'][r]['liquidation'] = dict(x['liquidation'])
    return out


def _acc(v: dict) -> dict:
    """The metrics view of one configuration's account (one split)."""
    a = {k: v[k] for k in ('counted', 'incomplete', 'refusals', 'excluded_symbols', 'excluded_trades')}
    a['status'] = v.get('status', INCOMPLETE)
    if v.get('book'):
        a['rows_trades'] = {r: x['counted'] for r, x in v['rows'].items()}
        a['book'] = {r: {k: x[k] for k in ('net_pct_equity', 'mtm_max_dd_pct', 'longest_underwater_cycles')}
                     for r, x in v['rows'].items()}
        if any('liquidation' in x for x in v['rows'].values()):
            a['liquidation'] = {r: dict(x['liquidation']) for r, x in v['rows'].items() if 'liquidation' in x}
    return a


def liq_combine(parts) -> dict:
    """Several splits' liquidation diagnostics of one row: counts summed, the worst W / G and largest bar jump, PARK
    when any split flagged."""
    mins = [p['min_w_over_g'] for p in parts if p.get('min_w_over_g') is not None]
    jumps = [p['max_bar_high_over_open'] for p in parts if p.get('max_bar_high_over_open') is not None]
    flagged = sum(p['flagged_bars'] for p in parts)
    return {'bars': sum(p['bars'] for p in parts), 'flagged_bars': flagged,
            'min_w_over_g': min(mins) if mins else None, 'max_bar_high_over_open': max(jumps) if jumps else None,
            'park': flagged > 0,
            'note': 'PARK-only diagnostic (W_t < G_t / 3); never an exit, fill or sizing input'}


def liquidation_flags(m: dict) -> list[str]:
    """`LIQUIDATION-PARK` when any row of an aggregate's liquidation diagnostic flagged a bar (contract section 4)."""
    liq = m.get('liquidation') or {}
    return ['LIQUIDATION-PARK'] if any(v.get('park') for v in liq.values()) else []


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
           'excluded_trades': sum(c['excluded_trades'] for c in cs), 'status': _status(cs)}
    if cs and all('rows' in c for c in cs):
        rs = [r for r in rows if all(r in c['rows'] for c in cs)]
        acc['rows_trades'] = {r: [t for c in cs for t in c['rows'][r]['trades']] for r in rs}
        bk = lambda r, k: [c['rows'][r]['book'][k] for c in cs]
        acc['book'] = {r: ({'net_pct_equity': math.fsum(bk(r, 'net_pct_equity')),
                            'mtm_max_dd_pct': max(bk(r, 'mtm_max_dd_pct')),
                            'longest_underwater_cycles': max(bk(r, 'longest_underwater_cycles'))}
                           if acc['status'] == COMPLETE else          # an INCOMPLETE split has no book figures
                           {'net_pct_equity': None, 'mtm_max_dd_pct': None, 'longest_underwater_cycles': None})
                       for r in rs}
        rows = rs
        lr = [r for r in rs if all('liquidation' in c['rows'][r] for c in cs)]
        if lr:
            acc['liquidation'] = {r: liq_combine([c['rows'][r]['liquidation'] for c in cs]) for r in lr}
    return metrics(acc, rows=rows, boot=boot), tr


def program_multiplicity(env: dict, prof: RunProfile) -> dict:
    """The Holm family a profile gates on. STRAT-01: its own family ledger count (unchanged). STRAT-02: the PROGRAM
    family (every trial record of trend_ema_mom AND trend_ema_mom_short, n = 35 at freeze) carried by the env from the
    verified ledgers; the own-family n is reported beside it, never gating. Absent / inconsistent refuses: the long
    family count is never silently substituted."""
    if prof is LONG_RUN:
        return {'status': 'PASS', 'n_trials': env['n_trials']}
    import run_r4 as G                                                                      # noqa: E402
    m = env.get('multiplicity')
    by = (m or {}).get('by_family') or {}
    if (not m or set(by) != set(G.PROGRAM_FAMILIES) or m.get('program') != sum(by.values())
            or env['n_trials'] != m['program'] or m['program'] < G.PROGRAM_N_AT_FREEZE
            or by.get(prof.family, 0) < G.SHORT_TRIALS):
        raise PitRunError(f'{prof.tag}: the gating Holm family must be the program family (>= '
                          f'{G.PROGRAM_N_AT_FREEZE}, {" + ".join(G.PROGRAM_FAMILIES)}); got {m!r} / '
                          f'n_trials {env["n_trials"]!r}')
    return {'status': 'PASS', 'n_trials': m['program'], 'program_n_trials': m['program'],     # n_trials: compat
            'gating_family': 'program', 'program_families': dict(by), 'own_family_n_trials': by[prof.family],
            'own_family_gating': False}


def summarize(env, tier, symbols, per_split, detail_splits, blocks, b0_diffs, b0_meta=None):
    prof = profile_of(env)
    mult = program_multiplicity(env, prof)
    cfg_run, plan = env['run_config'], env['plan']
    rows = tuple(cfg_run['stress_rows'])
    keys = list(per_split)
    wf = [k for k in keys if plan.split(k)['name'] == 'walk_forward']
    boot = {'seed': cfg_run['seed'], 'resamples': cfg_run['resamples']}
    dev_m, dev_tr = _agg(detail_splits, prof.primary, keys, rows, boot)
    wf_m, _ = _agg(detail_splits, prof.primary, wf, rows, boot)
    sign = (dev_m['mean_r'] or 0.0) > 0
    neigh = {}
    for c in prof.configs():
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
    d1 = [b['cand'] - b['B1'] for b in blocks if b['cand'] is not None and b['B1'] is not None]
    d2 = [b['cand'] - b['B2'] for b in blocks if b['cand'] is not None and b['B2'] is not None]
    ps = {'B0': sign_flip_p(b0_diffs, perms=perms, seed=seed), 'B1': sign_flip_p(d1, perms=perms, seed=seed),
          'B2': sign_flip_p(d2, perms=perms, seed=seed)}
    adj = holm(ps, mult['n_trials'])
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
        'multiplicity': mult,
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
    flags = (['DEV-NEGATIVE'] if dev_mean is None or dev_mean <= 0 else []) + liquidation_flags(dev_m)
    # missing-data policy: any INCOMPLETE primary / neighbour / baseline row makes the tier INCOMPLETE (the uncapped
    # development-only variant is split-end censored by design and can never be evidence; reported, not gating)
    role = {c['name']: c['role'] for c in prof.configs()}
    bad_roles, bad_classes = Counter(), Counter()
    for sp in detail_splits.values():
        for name, c in sp['configs'].items():
            if c.get('status') != COMPLETE:
                bad_roles[role.get(name, 'unknown')] += 1
                if role.get(name) != 'development_only':
                    bad_classes.update(i.get('class', '?') for i in c['incomplete'] if not i.get('secondary'))
    status = INCOMPLETE if any(n for r, n in bad_roles.items() if r != 'development_only') else COMPLETE
    verdict, basis = 'PARK', 'prereg verdict_rules.development: development evidence can neither PROMOTE nor REJECT'
    if status != COMPLETE:
        verdict, basis = INCOMPLETE, (f'missing-data policy {POLICY}: an unaccountable row halted the book; no '
                                      'verdict, gate, promotion or ledger evidence may use this tier')
        flags.append(INCOMPLETE)
        for v in edge.values():
            if v['status'] in ('PASS', 'FAIL'):
                v['status'] = INCOMPLETE
    bind = {} if prof is LONG_RUN else {'candidate_id': prof.candidate_id, 'side': prof.side,
                                        'profile': prof.binding(), 'program_n_trials': mult['program_n_trials'],
                                        'own_family_n_trials': mult['own_family_n_trials']}
    summary = {'format': prof.summary_format, **bind, 'status': status, 'missing_data_policy': POLICY,
               **({'incomplete_rows': {r: n for r, n in sorted(bad_roles.items()) if r != 'development_only'},
                   'incomplete_classes': dict(sorted(bad_classes.items()))} if status != COMPLETE else {}),
               **({'development_only_incomplete_rows': bad_roles['development_only']}
                  if bad_roles['development_only'] else {}),
               'tier': tier, 'label': LABEL, 'labels': [LABEL, DEV_LABEL] + env['cost_labels'],
               'development_only': True, 'identity': env['identity'], 'run_config_digest': sha(cfg_run),
               'symbol_count': len(symbols), 'splits': per_split,
               'aggregates': {'development': dev_m, 'walk_forward': wf_m},
               'contribution_by_regime': {g: {'trades': len(v), 'mean_r': math.fsum(v) / len(v)}
                                          for g, v in sorted(_by_regime(dev_tr).items())},
               'book': dict(BOOK), 'baselines': base, 'edge00': edge,
               'verdict': verdict, 'verdict_basis': basis, 'flags': flags, 'not_evaluated': list(prof.not_evaluated)}
    detail = {'format': prof.detail_format, **bind, 'status': status,            # status: derived from the rows
              'missing_data_policy': POLICY,
              'tier': tier, 'label': LABEL, 'symbols': list(symbols), 'splits': detail_splits,
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
    """The run config of one tier (with the identity -> run id). A non-STRAT-01 profile binds its candidate / side /
    profile here too, so a long and a short run (or resume) can never share a run id, progress or final."""
    prof = env.get('profile', LONG_RUN)
    return {'tier': tier, 'symbols': list(env['tiers'][tier]), 'shared': sha(env['run_config']),
            **({} if prof is LONG_RUN else {'profile': prof.binding()})}


def write_json(path: str, doc) -> str:
    return X.atomic_write_json(path, doc)


def fold_bound(env: dict) -> int | None:
    """The progress fold bound of the routed profile (STRAT-02-PIT-01): STRAT-01 keeps the default (None = its own
    prereg, unchanged); any other profile MUST carry the walk-forward fold count of its own routed prereg / plan, so
    neither profile's progress, finals or resumes validate through the other's prereg."""
    prof = env.get('profile', LONG_RUN)
    if prof is LONG_RUN:
        return env.get('folds')
    n = env.get('folds')
    if type(n) is not int or n < 1:
        raise PitRunError(f'{prof.tag}: no fold bound from the routed {prof.candidate_id} prereg')
    plan = env.get('plan')
    if plan is not None and n != sum(1 for k in plan.keys() if plan.split(k)['name'] == 'walk_forward'):
        raise PitRunError(f'{prof.tag}: the fold bound differs from the routed split plan')
    return n


def _sealed_final(d: str, final: str, folds) -> dict:
    """A sealed final whose every artifact still hashes to its record (else refuse: aborted / tampered)."""
    doc = PG.read(final, folds)
    if doc['state'] != 'sealed':
        raise PitRunError(f'{final}: the run was aborted; use a new evidence dir')
    for a in doc['artifacts']:
        if PG.file_sha256(os.path.join(d, a['path'])) != a['sha256']:
            raise PitRunError(f'{d}: sealed artifact {a["path"]} no longer matches its final record')
    return doc


def tier_evidence(final: str, ident: dict, folds=None) -> bool:
    """The CORE barrier (Codex 6157420962 item 2): True only for a same-identity final that is sealed, hash-valid,
    evidence-bearing and whose every NAMED evidence artifact is a sealed artifact and explicitly COMPLETE. False = a
    sealed INCOMPLETE / no-evidence audit final (the next tier never opens). Anything else refuses."""
    d = os.path.dirname(final)
    doc = PG.read(final, folds)
    if doc is None:
        raise PitRunError(f'{final}: no final record')
    if doc['identity'] != ident:
        raise PitRunError(f'{final}: sealed under another identity')
    doc = _sealed_final(d, final, folds)
    if not doc['evidence']:
        return False
    arts = {a['path'] for a in doc['artifacts']}
    for e in doc['evidence']:
        if e not in arts:
            raise PitRunError(f'{final}: evidence {e!r} is not a sealed artifact')
        with open(os.path.join(d, e), 'rb') as f:
            require_complete(json.loads(f.read()), e)
    return True


def _state(d: str, run: str, resume: bool, folds=None) -> str:
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
        _sealed_final(d, final, folds)
        if not resume:
            raise PitRunError(f'{final}: tier already sealed (pass resume to continue)')
        return 'sealed'
    if os.path.exists(os.path.join(d, f'progress-{run}.json')):
        if not resume:
            raise PitRunError(f'{d}: interrupted run (progress without a final); pass resume to re-run it '
                              'under the same identity')
        PG.read(os.path.join(d, f'progress-{run}.json'), folds)
        return 'resume'
    return 'new'


def run_tiers(env: dict, out_dir: str, *, resume: bool = False, clock=PG.cairo_now) -> list[str]:
    """CORE-10 then CORE-20: each tier sealed (final written) before the next starts, and the next tier opens ONLY
    after the same-identity previous final is sealed, hash-valid, explicitly COMPLETE and evidence-bearing
    (`tier_evidence`). A fresh or resumed INCOMPLETE / no-evidence tier stops the run there: its audit final is
    returned (pit_incomplete -> exit 4) and no later tier dir, Access window, progress or artifact is created."""
    finals, ident, folds = [], env['identity'], fold_bound(env)
    for i, tier in enumerate(RUN_TIERS):
        d = os.path.join(out_dir, tier)
        run = PG.run_id(ident, tier_config(env, tier))
        final = os.path.join(d, f'final-{run}.json')
        if i:
            prev = os.path.join(out_dir, RUN_TIERS[i - 1],
                                f'final-{PG.run_id(ident, tier_config(env, RUN_TIERS[i - 1]))}.json')
            if not os.path.exists(prev):
                raise PitRunError(f'{RUN_TIERS[i - 1]} must be sealed under the same identity before {tier} starts')
            if not tier_evidence(prev, ident, folds):
                return finals                   # INCOMPLETE audit final: stop, never open the next tier
        state = _state(d, run, resume, folds)
        if state == 'sealed':
            finals.append(final)
            continue
        prog = PG.Progress(d, identity=ident, config=tier_config(env, tier), clock=clock, folds=folds)
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
        ok = is_complete(summary) and is_complete(detail)                # both artifacts explicitly COMPLETE
        prog.sealed(paths, [os.path.basename(paths[0])] if ok else [])                # INCOMPLETE: no evidence
        finals.append(final)
    return finals


def is_complete(doc) -> bool:
    try:
        require_complete(doc)
    except PitRunError:
        return False
    return True


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


def real_env(repo: str, *, store: str, author: str, cairo_date: str, gate_path: str,
             profile: RunProfile = LONG_RUN) -> dict:
    """Derived ONLY from the gate's committed files. Reads manifest metadata, never a data row. `profile` (routed by
    run_r4.run_pit from the gate's candidate) must be the candidate of that gate and its prereg."""
    import run_r4 as G                                                                      # noqa: E402
    if profile not in RUN_PROFILES.values():
        raise PitRunError(f'unknown run profile {profile!r}')
    g = json.loads(G._load(repo, gate_path))
    raw = G._load(repo, g['prereg'])
    pre = json.loads(raw)
    short = profile is SHORT_RUN
    if (G.route(g).candidate_id, pre.get('candidate_id'), pre.get('family')) != (
            profile.candidate_id, profile.candidate_id, profile.family) or (
            os.path.splitext(os.path.basename(g['ledger']))[0] != profile.family):
        raise PitRunError(f'{profile.tag}: the gate / prereg / ledger are not the {profile.candidate_id} profile')
    static = G.static_universe(repo, g['universe']['path'], g['universe']['snapshot'])
    man = M.load(os.path.join(repo, MANIFEST_PATH))
    if man['digest'] != pre['data']['manifest_digest']:
        raise PitRunError('the manifest differs from the prereg-pinned digest')
    sp = pre['splits']
    plan = S.SplitPlan(sp['splits'], interval=sp['interval'], lookback_bars=sp['lookback_bars'],
                       horizon_bars=sp['horizon_bars'])
    if [n for n, _ in T.neighbours()] != pre['neighbour_grid']['configs']:
        raise PitRunError('the neighbour grid differs from the registered prereg configs')
    v = pre['variants']
    if v['primary']['name'] != profile.primary or (v['secondary'] != [] if short else
                                                    v['secondary'][0]['name'] != DEV_ONLY):
        raise PitRunError('the variants differ from the registered prereg')     # STRAT-02: no uncapped variant
    if short and (v['primary'].get('side') != 'short' or f'seed {SHORT_SEED}' not in pre['baselines']['B0']
                  or pre['data']['exchange_rules'].get('sha256') != RULES_SHA256):
        raise PitRunError('STRAT-02: side / seed / exchange-rules pin differ from the registered short prereg')
    if pre['costs']['stress_rows'] != list(C.STRESS):
        raise PitRunError('stress rows differ from the registered prereg')
    ledger = os.path.join(repo, g['ledger'])
    fams = L.verify(ledger)
    fam = fams.get(pre['family'], [])
    n_trials = sum(1 for r in fam if r['kind'] in ('variant', 'grid_point', 'baseline'))
    mult = None
    if short:                                       # gating Holm = the program family (18 + 17 = 35), never own-family
        mult = G.multiplicity(fams)
        if mult['by_family'][profile.family] != n_trials:
            raise PitRunError('STRAT-02: the own-family count differs between the ledger and the program family')
        n_trials = mult['program']
    cal = pre['costs']['slip_cal_v1']
    model = C.CostModel(cal, {'BTCUSDT': 'crypto'})
    rules = load_rules(G._load(repo, RULES_PATH), static['core_20'])
    run_config = {'configs': [c['name'] for c in profile.configs()], 'stress_rows': list(C.STRESS), 'book': dict(BOOK),
                  'rules_path': RULES_PATH, 'rules_sha256': RULES_SHA256, 'rules_env': RULES_ENV, 'rules': rules,
                  'slip_c': cal['c']['crypto'], 'seed': profile.seed, 'resamples': RESAMPLES, 'perms': PERMS,
                  'b0_draws': B0_DRAWS, 'dev_keys': dev_keys(plan), 'plan_digest': plan.digest, 'window': T.WINDOW,
                  'tape': PE.FORMAT, 'cost_model': model.digest, 'n_trials': n_trials,
                  **({'profile': profile.binding(), 'multiplicity': mult} if short else {})}
    head = subprocess.run(['git', '-C', repo, 'rev-parse', 'HEAD'], capture_output=True, text=True,
                          check=True).stdout.strip()
    identity = {'commit': head, 'evaluator': sha({'code': code_digest(repo), 'config': run_config}),
                'manifest': man['digest'], 'universe': static['digest'], 'classification': None,
                'prereg': hashlib.sha256(raw).hexdigest()}
    return {'repo': repo, 'manifest': man, 'store': store, 'static': static,
            'tiers': {'CORE-10': static['core_10'], 'CORE-20': static['core_20']}, 'plan': plan, 'ledger': ledger,
            'evaluator': EVALUATOR, 'author': author, 'cairo_date': cairo_date, 'run_config': run_config,
            'rules': rules,
            'identity': identity, 'n_trials': n_trials, 'cost_labels': model.labels('BTCUSDT'),
            **({'profile': profile, 'multiplicity': mult,                 # the routed prereg's own fold bound
                'folds': PG.fold_count_of(os.path.join(repo, g['prereg']))} if short else {})}


# ------------------------------------------------------------------ REX-02: range_vwap_rev.v1 binding
# REX-02 binds the REX-01 module (`range_vwap_rev`, `range_vwap_rev_notice`, `range_vwap_rev_eval`) into this runner
# under its own immutable profile (`REX_RUN`, NOT in the gate-routed RUN_PROFILES: the family has no R4 gate yet).
# Fail closed at every input (Codex rulings 6169708729 / 6172019060, Cowork 6172000821):
#   * window: an explicit development ACCESS window [START, END] (no default) inside the frozen `REX_DEV_WINDOW`
#     (train + WF0..WF4 of the committed split plan, end-exclusive at the sealed cutoff); calibration and the holdout
#     are refused. The first decision close is START + W x 4h (the warm-up is read inside the window, never before it);
#   * loader: the attested `zb-data-manifest/1` -> StaticDataset -> a ledger-recorded `pit.Access.open_development`
#     (distinct `range_vwap_rev` family ledger, lineage root; development data_access + variant / baseline records;
#     a ledger carrying any holdout record refuses) -> rows bounded to the window -> the REX tape;
#   * tape manifest: every required series / column (4h klines WITH quote_volume, funding rows, 1h funding marks),
#     the per-symbol exchange rules (F1: in the manifest digest, run config and run id) and the committed
#     `zb-delist-notice/1` artifact bound by its exact digest (pinned by the run command, never read from the artifact);
#   * preflight (F2): no kline at / after the cutoff, no row before the window start, no kline / mark available after
#     the window end and no funding row available after END + `REX_FUNDING_TAIL_MS` (see there);
#   * costs: only `base` and `fees_slip_x2` (frozen; nothing else can be configured).
# Notice register (Codex 6172596212 a): a MISSING, unreadable or digest-mismatched `zb-delist-notice/1` artifact is a
# PREFLIGHT REFUSAL (loader and runner). A present, validly attested register whose PROVEN coverage is missing for a
# symbol (not in its frozen list) or a time (coverage hole), and data / notice disagreements, are NOT refusals: they
# become notice GAPs of the affected split segment only, so only those book rows halt INCOMPLETE (no fill).
# Split-aware evidence (6172596212 b): one invocation, but the access window is cut at every frozen train / WF
# boundary of the committed split plan (`rex_segments`); each segment is a fresh, independent pass (its own clipped
# tape, its own W x 4h warm-up read inside the segment, fresh books, its own notice view and completeness status), so
# no position, indicator warm-up or completeness state crosses a boundary (REX runs no bootstrap). Metrics and status
# are emitted per split; the aggregate is derived from the split results afterwards (`rex_aggregate`).
# Provenance (6172596212 c): a historical / store run (rex_load) refuses a dirty git tree before any access and records
# the exact clean commit SHA; any other (synthetic / unit) run is marked `historical_evidence: False`.
# Add-on (Codex 6185035993 point 3, optional `rex_load(addon=, addon_report=)` / `run_r4 rex --addon --addon-report`):
# the base archive stays immutable; a checksum-pinned zb-rex-gap-fill/1 add-on fills ONLY exact holes of its pinned
# signed gap report where no base row exists (rex_addon: validated in full before the Access opens, any failure or
# collision refuses). Its provenance goes on the data_access detail, the run config (run id) and the summary.
# Provenance files: the same progress snapshot + sealed final + summary / detail artifacts as the STRAT runs
# (progress.py, `_state`, `is_complete`). Nothing is promotable.
import range_vwap_rev as RV                                                                 # noqa: E402
import range_vwap_rev_artifact as NA                                                        # noqa: E402
import range_vwap_rev_eval as RE                                                            # noqa: E402
import range_vwap_rev_notice as NT                                                          # noqa: E402

REX_TAPE_FORMAT = 'zb-rex-tape-manifest/2'
REX_SUMMARY_FORMAT = 'zb-rex02-run-summary/1'
REX_DETAIL_FORMAT = 'zb-rex02-run-detail/1'
REX_COST_ROWS = ('base', 'fees_slip_x2')             # FROZEN (6172019060 point 5): the only cost rows of a REX run
REX_ROWS = REX_COST_ROWS                             # the rows EDGE-00 reads (range_vwap_rev_eval.edge00)
# FROZEN development window (6172019060 point 1): train + WF0..WF4 of the committed split plan, END EXCLUSIVE.
# Calibration (2020-02-03 .. 2021-01-04) and everything from the cutoff on (post-development gap, holdout) are refused.
REX_DEV_WINDOW = ('2021-01-04T00:00:00Z', '2026-09-28T00:00:00Z')
REX_LEDGER = 'research_evidence/ledger/range_vwap_rev.jsonl'
REX_SPLIT_MODE = 'per-split reset at the train / WF boundaries of the committed plan; aggregate derived from the splits'
# Funding tail allowance (F2): a funding row is AVAILABLE at its calc_time, which lies up to RV.FUNDING_TOL (60 s)
# after its nominal hour. funding-window/2 (RV.funding_events) proves coverage of a played bar (bo, close] by reading
# rows with time <= close + FUNDING_TOL, so the event nominally AT the window end (paid by a lot held through the last
# bar) is available up to 60 s after END. Without it that last bar would be `incomplete.funding` instead of the true
# `incomplete.split_end`. Klines and 1h marks need no tail (mark_at(T) reads the bar ENDING at T <= END).
REX_FUNDING_TAIL_MS = RV.FUNDING_TOL
REX_INPUTS = {                                       # the explicit input spec: series -> required columns
    'klines_4h': ('open_ms', 'open', 'high', 'low', 'close', 'volume', 'quote_volume', 'available_ms'),
    'funding': ('time_ms', 'rate', 'interval_hours', 'available_ms'),
    'mark_1h': ('open_ms', 'close'),                 # funding-mark proxy v1 (RV.mark_at); funding is unpriced without it
}
REX_NOTICE_KEYS = ('rule_id', 'schema', 'parser_sha', 'artifact_digest', 'register_digest', 'frozen', 'attested')
REX_NOT_EVALUATED = (
    'B0 matched random entries and B1 weekly book (range_vwap_rev_eval; holdout-protocol quantities)',
    'X2 / X3 tape-level overlays (SCOUT-EXIT-01 comparison, TAPE-LEVEL only)',
    'EDGE-00 verdict rows (holdout only; development can neither promote nor reject)')


def rex_configs() -> list[dict]:
    """The registered X1 trials of one side (primary + 8 one-step neighbours) and B2 (naive fade, primary params)."""
    out = [{'name': RV.PRIMARY.name, 'cfg': RV.PRIMARY, 'mode': 'x1', 'role': 'primary'}]
    out += [{'name': c.name, 'cfg': c, 'mode': 'x1', 'role': 'neighbour'} for c in RV.neighbours()]
    out.append({'name': 'B2', 'cfg': RV.PRIMARY, 'mode': 'b2', 'role': 'baseline'})
    return out


REX_RUN = RunProfile('REX-02', RV.CANDIDATE, RV.FAMILY, 'both', 'range_vwap_rev.run_book', RV.PRIMARY.name, None,
                     RE.SEED, REX_SUMMARY_FORMAT, REX_DETAIL_FORMAT, rex_configs, REX_NOT_EVALUATED)
REX_PROFILES = {REX_RUN.candidate_id: REX_RUN}
# Development-only candidate rules (Codex 6185035993 point 4). A run is the frozen v1 unless a variant is selected
# EXPLICITLY (`variant=` / `run_r4.py rex --variant`); there is no default switch. The variant changes nothing of v1's
# path, config, records or run id: it adds its own `variant` block to the run config (so its run id differs), records
# its own candidate id on every data_access / variant / baseline record of the family ledger, and is never promotable.
# It applies to every book the runner opens (X1 primary, the 8 neighbours, B2) and to both cost rows via
# run_book(entry_rule=...); B0 (range_vwap_rev_eval.b0 / z_table) takes the same entry_rule; B1 (continuous weekly
# comparator, valued at the boundaries, never split_end) and B3 (cash) open no X1-book lot and are unaffected.
REX_VARIANTS = {RV.SPLITCUT_CANDIDATE: RV.SPLITCUT}


def rex_variant(variant) -> tuple:
    """(candidate id, variant record) of an explicit selection: None = the frozen v1 (no variant record); a
    development-only variant id must be named exactly; anything else refuses."""
    if variant is None or variant == RV.CANDIDATE:
        return RV.CANDIDATE, None
    if variant not in REX_VARIANTS:
        raise PitRunError(f'REX-02: unknown variant {variant!r} (development-only variants: {sorted(REX_VARIANTS)})')
    return variant, dict(REX_VARIANTS[variant])


def _rex_rule(env: dict):
    v = env.get('variant_record')
    return None if v is None else v['entry_rule']


def rex_plan(repo: str) -> S.SplitPlan:
    """The committed split plan whose development end is the sealed cutoff (the STRAT-02 prereg, the latest freeze:
    development ends 2026-09-28; everything from there on belongs to no development split and is never read)."""
    import run_r4 as G                                                                      # noqa: E402
    sp = json.loads(G._load(repo, G.SHORT_PREREG))['splits']
    return S.SplitPlan(sp['splits'], interval=sp['interval'], lookback_bars=sp['lookback_bars'],
                       horizon_bars=sp['horizon_bars'])


def rex_calibration(plan: S.SplitPlan) -> list[tuple[int, int]]:
    return [plan.access_range(k) for k in plan.keys() if plan.split(k)['name'] == 'calibration']


def rex_dev_window(plan: S.SplitPlan) -> tuple[int, int]:
    """`REX_DEV_WINDOW` in ms, re-proved against the committed plan: it must be exactly train .. WF-last (contiguous,
    the development splits only), after every calibration split and before the holdout / never-read window."""
    lo, hi = (S.parse_utc(x) for x in REX_DEV_WINDOW)
    keys = dev_keys(plan)
    spans = [dev_window(plan, k) for k in keys]
    names = [plan.split(k)['name'] for k in keys]
    if (names[:1] != ['train'] or any(n != 'walk_forward' for n in names[1:]) or spans[0][0] != lo
            or spans[-1][1] != hi or any(a[1] != b[0] for a, b in zip(spans, spans[1:]))):
        raise PitRunError('REX-02: the frozen development window differs from the committed train + WF splits')
    if any(c_hi > lo for _, c_hi in rex_calibration(plan)):
        raise PitRunError('REX-02: the frozen development window overlaps the calibration split')
    if hi > holdout_start(plan) or hi > RE.S_END:
        raise PitRunError('REX-02: the development end reaches the holdout / never-read window')
    return lo, hi


def rex_sealed_cutoff(plan: S.SplitPlan) -> int:
    """The first sealed instant: the (exclusive) end of the frozen development window."""
    return rex_dev_window(plan)[1]


def rex_window(spec, plan: S.SplitPlan) -> tuple[int, int]:
    """The ACCESS window [lo, hi] of a REX development run from an EXPLICIT `START/END` (UTC, 4h grid). No default.
    It must equal or lie within `REX_DEV_WINDOW`; overlapping calibration, starting before the development window,
    reaching past the sealed cutoff / into the holdout, or leaving no decision after the W x 4h warm-up refuses.
    Decisions run on closes lo + W x 4h .. hi."""
    if spec is None or (isinstance(spec, str) and not spec.strip()):
        raise PitRunError(f'REX-02: an explicit --window START/END is required (no default; the frozen development '
                          f'window is {"/".join(REX_DEV_WINDOW)})')
    parts = spec.split('/') if isinstance(spec, str) else list(spec)
    if len(parts) != 2:
        raise PitRunError('REX-02: --window must be START/END (UTC YYYY-MM-DDTHH:MM:SSZ)')
    try:
        lo, hi = (S.parse_utc(p.strip()) for p in parts)
    except S.SplitError as e:
        raise PitRunError(f'REX-02: {e}')
    if lo % RV.TF or hi % RV.TF:
        raise PitRunError('REX-02: window bounds must lie on the 4h grid')
    if hi <= lo:
        raise PitRunError('REX-02: window end must be after its start')
    dlo, dhi = rex_dev_window(plan)
    if any(lo < c_hi and c_lo < hi for c_lo, c_hi in rex_calibration(plan)):
        raise PitRunError('REX-02: window overlaps the calibration split (excluded from all evidence)')
    if hi > dhi or plan.overlaps_holdout(lo, hi):
        raise PitRunError(f'REX-02: window reaches the sealed holdout (on / after {S.utc(dhi)})')
    if lo < dlo:
        raise PitRunError(f'REX-02: window starts before the frozen development window ({REX_DEV_WINDOW[0]})')
    if lo + RV.WARMUP_MS >= hi:
        raise PitRunError('REX-02: window is not longer than the W x 4h warm-up (no decision close)')
    rex_segments(plan, lo, hi)                              # every split segment must hold a decision (fail closed)
    return lo, hi


def rex_segments(plan: S.SplitPlan, lo: int, hi: int) -> list[dict]:
    """The access window [lo, hi] cut at the frozen development split boundaries of the committed plan (read from the
    plan, never hardcoded): one segment per train / WF split it intersects, in time order. Each segment is evaluated
    independently with its own warm-up read INSIDE it (decisions from seg_lo + W x 4h), so a segment not longer than
    the warm-up would carry no decision and refuses (align the window to the split boundaries instead)."""
    out = []
    for k in dev_keys(plan):
        a, b = dev_window(plan, k)
        s_lo, s_hi = max(lo, a), min(hi, b)
        if s_lo >= s_hi:
            continue
        if s_lo + RV.WARMUP_MS >= s_hi:
            raise PitRunError(f'REX-02: the window covers only {S.utc(s_lo)}..{S.utc(s_hi)} of split {k}, not longer '
                              f'than the per-split W x 4h warm-up (no decision; warm-up never crosses a split boundary)')
        sp = plan.split(k)
        out.append({'key': k, 'name': sp['name'], 'fold': sp['fold'], 'lo': s_lo, 'hi': s_hi,
                    'first': s_lo + RV.WARMUP_MS})
    if not out or out[0]['lo'] != lo or out[-1]['hi'] != hi or any(x['hi'] != y['lo'] for x, y in zip(out, out[1:])):
        raise PitRunError('REX-02: the window is not covered exactly by development splits of the committed plan')
    return out


def rex_clip(x, lo: int, hi: int):
    """One symbol's series restricted to a segment [lo, hi] (funding: + the documented tail): nothing before the
    segment start is visible, so the warm-up and every indicator are read inside the segment."""
    return RV.Series({k: b for k, b in x.bars.items() if k >= lo and b.available_ms <= hi}, dict(x.rule),
                     tuple(r for r in x.funding if r.time_ms >= lo and r.available_ms <= hi + REX_FUNDING_TAIL_MS),
                     {k: v for k, v in x.marks.items() if k >= lo and k + RV.HOUR <= hi})


def _rex_rows(kind: str, x) -> list:
    if kind == 'klines_4h':
        return [list(x.bars[k]) for k in sorted(x.bars)]
    if kind == 'funding':
        return [list(r) for r in x.funding]
    return [[k, x.marks[k]] for k in sorted(x.marks)]


def rex_tape_digest(kind: str, tape: dict, symbols) -> str:
    return sha({s: _rex_rows(kind, tape[s]) for s in symbols})


def rex_rules(tape: dict, symbols) -> dict:
    """The per-symbol exchange rules of a tape (F1): exactly RULES_FIELDS, None where a field is missing."""
    return {s: {k: (tape[s].rule or {}).get(k) for k in RULES_FIELDS} for s in symbols}


def rex_tape_manifest(tape: dict, artifact: dict, *, symbols=None, attested: bool = True, source=None) -> dict:
    """The tape manifest of an in-memory REX tape: per series the symbols that carry rows, the columns present in EVERY
    row (a column with any null is NOT declared), and the content digest; the per-symbol exchange rules + their
    digest (F1); the source (data manifest / universe digests, None for an in-memory tape); and the binding of the
    `zb-delist-notice/1` artifact by its digest."""
    symbols = list(symbols or tape)
    series = {}
    for kind, cols in REX_INPUTS.items():
        rows = {s: _rex_rows(kind, tape[s]) for s in symbols}
        have = [s for s in symbols if rows[s]]
        present = [c for i, c in enumerate(cols)
                   if have and all(r[i] is not None for s in have for r in rows[s])]
        series[kind] = {'symbols': have, 'columns': present, 'digest': rex_tape_digest(kind, tape, symbols),
                        **({'interval': '4h'} if kind == 'klines_4h' else {})}
    rules = rex_rules(tape, symbols)
    doc = {'format': REX_TAPE_FORMAT, 'candidate_id': RV.CANDIDATE, 'symbols': symbols, 'series': series,
           'rules': rules, 'rules_digest': sha(rules), 'source': source,
           'notice_register': {'rule_id': NT.RULE_ID, 'schema': NT.SCHEMA, 'parser_sha': NT.parser_sha(),
                               'artifact_digest': (artifact or {}).get('digest'),
                               'register_digest': (artifact or {}).get('register_digest'),
                               'frozen': list((artifact or {}).get('frozen') or ()), 'attested': attested}}
    doc['digest'] = sha(doc)
    return doc


def rex_seal_manifest(doc: dict) -> dict:
    """Re-digest a manifest after an edit (tests / the loader); the preflight checks the digest, never trusts it."""
    doc = {k: v for k, v in doc.items() if k != 'digest'}
    doc['digest'] = sha(doc)
    return doc


def _rex_bounds(s: str, x, lo: int, hi: int, cut: int) -> list[str]:
    """F2: every row of the tape lies in the access window (funding: + the documented tail); none at / after the
    sealed cutoff."""
    fail = []
    if any(o >= cut for o in x.bars):
        fail.append(f'{s}: a kline opening at / after the holdout cutoff {S.utc(cut)}')
    if any(o < lo or b.available_ms > hi for o, b in x.bars.items()):
        fail.append(f'{s}: a kline outside the access window [{S.utc(lo)}, {S.utc(hi)}]')
    if any(k % RV.HOUR or k < lo or k + RV.HOUR > hi for k in x.marks):
        fail.append(f'{s}: a 1h mark off the hour grid or outside the access window')
    if any(r.time_ms < lo or r.available_ms > hi + REX_FUNDING_TAIL_MS or r.time_ms >= cut + REX_FUNDING_TAIL_MS
           for r in x.funding):
        fail.append(f'{s}: a funding row outside the access window (+{REX_FUNDING_TAIL_MS} ms tail)')
    return fail


def rex_preflight(manifest, tape, artifact, *, symbols, window, artifact_sha, cutoff=None) -> list[str]:
    """Every failure of the REX input spec (empty = pass). Fail closed: a missing / undeclared series or column, a
    symbol without rows, a tape that is not the attested one, exchange rules that are not the attested ones (F1), a
    row outside the access window or at / after the cutoff (F2), or a notice artifact that is missing, malformed or not
    the exact pinned digest is a failure; nothing is ever defaulted."""
    fail = []
    if not isinstance(manifest, dict):
        return ['tape manifest missing']
    if manifest.get('format') != REX_TAPE_FORMAT or manifest.get('candidate_id') != RV.CANDIDATE:
        fail.append(f'tape manifest is not {REX_TAPE_FORMAT} of {RV.CANDIDATE}')
    if manifest.get('digest') != sha({k: v for k, v in manifest.items() if k != 'digest'}):
        fail.append('tape manifest digest does not match its content')
    symbols = list(symbols)
    if not symbols:
        fail.append('no symbols')
    absent = [s for s in symbols if not isinstance(tape, dict) or s not in tape]
    if absent:
        return fail + [f'tape has no series for {absent}']
    if list(manifest.get('symbols') or ()) != symbols:
        fail.append('tape manifest symbols differ from the run symbols (frozen rank order)')
    series = manifest.get('series') or {}
    for kind, cols in REX_INPUTS.items():
        e = series.get(kind)
        if not isinstance(e, dict):
            fail.append(f'{kind}: required series missing from the tape manifest')
            continue
        miss = [c for c in cols if c not in (e.get('columns') or ())]
        if miss:
            fail.append(f'{kind}: required column(s) missing {miss}')
        nosym = [s for s in symbols if s not in (e.get('symbols') or ())]
        if nosym:
            fail.append(f'{kind}: no rows for {nosym}')
        if kind == 'klines_4h' and e.get('interval') != '4h':
            fail.append('klines_4h: interval is not 4h')
        if e.get('digest') != rex_tape_digest(kind, tape, symbols):
            fail.append(f'{kind}: the tape is not the attested one (digest mismatch)')
    rules = manifest.get('rules')                           # F1: the exchange rules are part of the provenance
    if not isinstance(rules, dict) or manifest.get('rules_digest') != sha(rules):
        fail.append('exchange rules missing from the tape manifest (or rules_digest mismatch)')
    elif rules != rex_rules(tape, symbols):
        fail.append('exchange rules differ from the attested ones')
    lo, hi = window
    cut = rex_dev_window(rex_plan(M.REPO))[1] if cutoff is None else cutoff
    for s in symbols:                                       # the rows themselves (never trust the declaration alone)
        x = tape[s]
        if any(b.quote_volume is None or b.volume is None for b in x.bars.values()):
            fail.append(f'{s}: a 4h bar without volume / quote_volume (VWAP undefined)')
        if any(b.available_ms - b.open_ms != RV.TF or b.open_ms % RV.TF for b in x.bars.values()):
            fail.append(f'{s}: a kline that is not a closed 4h bar on the grid')
        if not x.funding:
            fail.append(f'{s}: no funding rows')
        if not x.marks:
            fail.append(f'{s}: no 1h funding-mark rows')
        if any((x.rule or {}).get(k) is None for k in RULES_FIELDS):
            fail.append(f'{s}: exchange rule incomplete')
        fail += _rex_bounds(s, x, lo, hi, cut)
    nb = manifest.get('notice_register')
    if not isinstance(nb, dict):
        return fail + [f'{NT.RULE_ID}: notice artifact binding missing']
    try:
        val = NA.validate(artifact, expect_digest=artifact_sha)
    except NA.ArtifactError as e:
        return fail + [str(e)]
    miss = [k for k in REX_NOTICE_KEYS if k not in nb]
    if miss:
        fail.append(f'{NT.RULE_ID}: binding key(s) missing {miss}')
    if nb.get('attested') is not True:
        fail.append(f'{NT.RULE_ID}: notice artifact not attested')
    if (nb.get('rule_id'), nb.get('schema'), nb.get('parser_sha')) != (NT.RULE_ID, NT.SCHEMA, NT.parser_sha()):
        fail.append(f'{NT.RULE_ID}: rule_id / schema / parser_sha differ from the frozen parser')
    if (nb.get('artifact_digest'), nb.get('register_digest')) != (val.digest, val.register.digest()):
        fail.append(f'{NT.RULE_ID}: the tape manifest binds another notice artifact (digest mismatch)')
    if list(nb.get('frozen') or ()) != list(artifact['frozen']):
        fail.append(f'{NT.RULE_ID}: the tape manifest binds another frozen symbol list')
    # a run symbol outside the register's frozen list is NOT a refusal: its coverage is unproven, so the affected rows
    # are INCOMPLETE (Validated.view gap; Codex 6172596212 a)
    return fail


def rex_code_digest() -> str:
    here = os.path.dirname(os.path.abspath(__file__))
    files = sorted(glob.glob(os.path.join(here, 'range_vwap_rev*.py'))) + [os.path.abspath(__file__)]
    return sha([{'path': os.path.basename(f), 'sha256': PG.file_sha256(f)} for f in files])


def _jsonable(o):
    if isinstance(o, dict):
        return {str(k): _jsonable(v) for k, v in o.items()}
    if isinstance(o, (list, tuple)):
        return [_jsonable(v) for v in o]
    if isinstance(o, (int, float, str, bool)) or o is None:
        return o
    return float(o)                                          # Fraction / Decimal diagnostics


def rex_check_rows(env: dict) -> None:
    """Costs are frozen (6172019060 point 5): `base` and `fees_slip_x2` only; no env may configure another row."""
    if tuple(REX_ROWS) != REX_COST_ROWS or REX_COST_ROWS != ('base', 'fees_slip_x2'):
        raise PitRunError('REX-02: the cost rows are frozen to base + fees_slip_x2')
    if any(k in env for k in ('rows', 'stress_rows', 'cost_rows')):
        raise PitRunError('REX-02: cost rows cannot be configured (frozen: base + fees_slip_x2)')


REX_SYNTHETIC = {'class': 'synthetic', 'historical_evidence': False,
                 'note': 'synthetic / unit run (no clean-tree store provenance): never historical evidence'}


def rex_provenance(env: dict) -> dict:
    """The run's evidence class (Codex 6172596212 c): `historical` only for an env built by the store loader
    (rex_load: clean tree proven before any access, exact commit recorded); everything else is synthetic."""
    pv = env.get('provenance')
    if pv is None:
        return dict(REX_SYNTHETIC)
    if (pv.get('class') != 'historical' or pv.get('tree') != 'clean' or pv.get('commit') != env.get('commit')
            or not isinstance(pv.get('commit'), str) or len(pv['commit']) != 40):
        raise PitRunError('REX-02: historical provenance must carry the exact clean commit SHA the loader proved')
    return {'class': 'historical', 'historical_evidence': True, 'commit': pv['commit'], 'tree': 'clean'}


def rex_split_pass(env: dict, seg: dict, symbols) -> dict:
    """ONE independent split pass: the tape clipped to the segment (warm-up read inside it), a notice view over the
    segment only, and fresh side books per configuration and row (run_book keeps no state between calls), so nothing
    of a previous split (lot, indicator, halt, completeness) is visible here."""
    lo, hi = seg['lo'], seg['hi']
    tape = {s: rex_clip(env['tape'][s], lo, hi) for s in symbols}
    notices = NA.validate(env['notice_artifact'], expect_digest=env['notice_sha256']).view(tape, symbols, lo, hi)
    results, counts, inc = {}, {}, []
    for side in RV.SIDES:
        for c in rex_configs():
            for row in REX_ROWS:
                res = RV.run_book(tape, c['cfg'], side, first=seg['first'], end=hi, order=symbols, row=row,
                                  notices=notices, mode=c['mode'], entry_rule=_rex_rule(env))
                results.setdefault(side, {}).setdefault(c['name'], {})[row] = _jsonable(res)
                counts.setdefault(side, {}).setdefault(c['name'], {})[row] = {
                    'trades': len(res['trades']), 'status': res['status'],
                    'net': _jsonable(sum(t['net'] for t in res['trades'])),
                    'r': _jsonable(sum(t['r'] for t in res['trades']))}
                if res['status'] != COMPLETE:
                    cz = res.get('censor') or {}
                    inc.append({'split': seg['key'], 'side': side, 'config': c['name'], 'row': row,
                                'reason': cz.get('reason'), 'class': cz.get('class'), 'secondary': False})
    head = {'key': seg['key'], 'name': seg['name'], 'fold': seg['fold'],
            'window': {'start': S.utc(lo), 'end': S.utc(hi)}, 'first_decision': S.utc(seg['first']),
            'status': INCOMPLETE if inc else COMPLETE}
    if inc:
        head['incomplete'] = inc
    return {'summary': dict(head, counts=counts), 'detail': dict(head, results=results)}


def rex_aggregate(splits: list[dict]) -> tuple[dict, dict, list]:
    """The aggregate, derived ONLY from the per-split results (never a pooled pass): per side / config / row the
    summed trades, net and R, the per-split statuses, and COMPLETE only when every split is COMPLETE."""
    counts, results, inc = {}, {}, []
    for sp in splits:
        key = sp['summary']['key']
        inc += sp['summary'].get('incomplete', [])
        for side, by_cfg in sp['detail']['results'].items():
            for name, by_row in by_cfg.items():
                for row, res in by_row.items():
                    c = counts.setdefault(side, {}).setdefault(name, {}).setdefault(
                        row, {'trades': 0, 'status': COMPLETE, 'net': 0.0, 'r': 0.0, 'splits': {}})
                    r = results.setdefault(side, {}).setdefault(name, {}).setdefault(
                        row, {'status': COMPLETE, 'trades': [], 'splits': {}})
                    n = sp['summary']['counts'][side][name][row]
                    c['trades'] += n['trades']
                    c['net'] += n['net']
                    c['r'] += n['r']
                    c['splits'][key] = r['splits'][key] = res['status']
                    r['trades'] += [dict(t, split=key) for t in res['trades']]
                    if res['status'] != COMPLETE:
                        c['status'] = r['status'] = INCOMPLETE
    return counts, results, inc


def rex_evaluate(env: dict, tier: str, lo: int, hi: int, run_config: dict) -> tuple[dict, dict]:
    """Split-aware (Codex 6172596212 b): one independent pass per frozen split segment of [lo, hi] (rex_segments /
    rex_split_pass), per-split metrics and status, then the aggregate derived from them (rex_aggregate)."""
    rex_check_rows(env)
    symbols = list(env['symbols'])
    segs = rex_segments(env['plan'], lo, hi)
    splits = [rex_split_pass(env, seg, symbols) for seg in segs]
    counts, results, inc = rex_aggregate(splits)
    status = INCOMPLETE if inc else COMPLETE
    man = env['manifest']
    cid = env.get('candidate_id', RV.CANDIDATE)
    access = {'kind': 'data_access', 'candidate_id': cid, 'split': 'development',
              'window': {'start': S.utc(lo), 'end': S.utc(hi)},
              'detail': {'purpose': 'development', 'promotable': False, 'tape_manifest': man['digest'],
                         'file_digests': {k: v['digest'] for k, v in man['series'].items()},
                         'rules_digest': man['rules_digest'], 'source': man.get('source'),
                         'notice_artifact': env['notice_sha256'], 'funding_tail_ms': REX_FUNDING_TAIL_MS,
                         'ledger': env.get('access')}}
    if env.get('addon') is not None:
        access['detail']['addon'] = env['addon']
    head = {'candidate_id': cid, 'family': RV.FAMILY, 'tier': tier, 'label': DEV_LABEL,
            'universe_label': LABEL, 'split': 'development', 'promotable': False, 'provenance': rex_provenance(env),
            'window': {'start': S.utc(lo), 'end': S.utc(hi)}, 'first_decision': S.utc(segs[0]['first']),
            'split_mode': REX_SPLIT_MODE, 'split_keys': [g['key'] for g in segs],
            'cost_rows': list(REX_ROWS), 'funding_window': RV.FUNDING_WINDOW, 'notice_rule': NT.RULE_ID,
            'missing_data_policy': POLICY, 'status': status, 'run': sha(run_config)}
    if env.get('variant_record') is not None:
        head['variant'] = env['variant_record']
    if env.get('addon') is not None:
        head['addon'] = env['addon']
    if inc:
        head['incomplete'] = inc
    summary = dict(head, format=REX_SUMMARY_FORMAT, counts=counts, splits=[sp['summary'] for sp in splits],
                   not_evaluated=list(REX_NOT_EVALUATED))
    detail = dict(head, format=REX_DETAIL_FORMAT, access=access, results=results,
                  splits=[sp['detail'] for sp in splits])
    return summary, detail


def rex_run_config(env: dict, lo: int, hi: int) -> dict:
    man = env['manifest']
    out = {'profile': REX_RUN.binding(), 'configs': [c['name'] for c in rex_configs()], 'rows': list(REX_ROWS),
           'book': dict(RV.BOOK), 'slip_c': RV.SLIP_C, 'funding_window': RV.FUNDING_WINDOW,
           'notice_rule': NT.RULE_ID, 'parser_sha': NT.parser_sha(), 'notice_artifact': env['notice_sha256'],
           'inputs': {k: list(v) for k, v in REX_INPUTS.items()}, 'rules_digest': man['rules_digest'],
           'source': man.get('source'), 'tape_manifest': man['digest'], 'window': [S.utc(lo), S.utc(hi)],
           'dev_window': list(REX_DEV_WINDOW), 'funding_tail_ms': REX_FUNDING_TAIL_MS,
           'cutoff': S.utc(rex_sealed_cutoff(env['plan'])), 'plan_digest': env['plan'].digest,
           'split_mode': REX_SPLIT_MODE, 'splits': [g['key'] for g in rex_segments(env['plan'], lo, hi)],
           'provenance': rex_provenance(env)}
    if env.get('variant_record') is not None:
        out['variant'] = env['variant_record']
    if env.get('addon') is not None:                         # the add-on is part of the run identity (run id)
        out['addon'] = env['addon']
    return out


def rex_no_holdout(ledger: str) -> None:
    """A REX development run never sits on a ledger that has ANY holdout record (no holdout record is allowed)."""
    if os.path.splitext(os.path.basename(ledger))[0] != RV.FAMILY:
        raise PitRunError(f'REX-02: Access records must go to the distinct {RV.FAMILY} family ledger')
    if not os.path.exists(ledger):
        return
    for r in L._check_state(ledger).get(RV.FAMILY, []):
        if r.get('split') == 'holdout' or r.get('kind') in L.SPEND_KINDS + ('holdout_rerun',):
            raise PitRunError(f'REX-02: the {RV.FAMILY} ledger carries a holdout record; refusing a development run')


def rex_record_trials(env: dict, run: str) -> None:
    """The development variant / baseline records of this run (one per side x registered configuration)."""
    v = env.get('variant_record')
    for side in RV.SIDES:
        for c in rex_configs():
            detail = {'purpose': 'REX-02 development screen (not promotable)', 'promotable': False,
                      'config': c['name'], 'role': c['role'], 'side': side, 'run': run,
                      'cost_rows': list(REX_ROWS), 'tier': env['tier']}
            if v is not None:
                detail = dict(detail, purpose=f'REX-02 development screen, {v["label"]}', variant=v)
            L.append(env['ledger'], kind='baseline' if c['role'] == 'baseline' else 'variant',
                     candidate_id=env.get('candidate_id', RV.CANDIDATE), split='development',
                     window=env['access']['window'], author=env['author'], cairo_date=env['cairo_date'],
                     manifest_digest=env['access']['manifest'], detail=detail)
    rex_no_holdout(env['ledger'])


def run_rex(env: dict, out_dir: str, *, window, resume: bool = False, clock=PG.cairo_now, variant=None) -> str:
    """REX-02 development run of `range_vwap_rev.v1` on one tier: window check (explicit, inside REX_DEV_WINDOW) ->
    input preflight (fail closed) -> progress snapshot -> both side books -> sealed summary / detail + final record.
    `env`: tape {symbol: RV.Series}, manifest (rex_tape_manifest), notice_artifact (zb-delist-notice/1 doc),
    notice_sha256 (the digest the run command pinned), symbols (frozen rank order), tier, plan (rex_plan), commit
    (40-hex), universe (64-hex digest or None); from the store loader also ledger / access / author / cairo_date
    (development variant / baseline records) and the `historical` provenance (clean commit); without it the run is
    synthetic and marked `historical_evidence: False`. `variant`: None = the frozen v1; a development-only candidate
    rule (REX_VARIANTS) only when named explicitly - a store env must have been loaded for the same variant.
    Returns the final record path."""
    cid, vrec = rex_variant(variant)
    loaded = env.get('variant', RV.CANDIDATE if env.get('ledger') else cid)      # a store env names its variant
    if loaded != cid:
        raise PitRunError(f'REX-02: the env was loaded for {loaded!r}, not {cid!r}')
    env = {k: v for k, v in env.items() if k not in ('candidate_id', 'variant_record')}
    if vrec is not None:
        env.update(candidate_id=cid, variant_record=vrec)
    lo, hi = rex_window(window, env['plan'])
    tier = env.get('tier')
    if tier not in RUN_TIERS:
        raise PitRunError(f'REX-02: tier must be one of {RUN_TIERS}')
    rex_check_rows(env)
    fail = rex_preflight(env.get('manifest'), env.get('tape'), env.get('notice_artifact'),
                         symbols=env.get('symbols') or (), window=(lo, hi), artifact_sha=env.get('notice_sha256'),
                         cutoff=rex_sealed_cutoff(env['plan']))
    if fail:
        raise PitRunError('REX-02 preflight refused (fail closed):\n  ' + '\n  '.join(fail))
    rex_provenance(env)                                      # a historical env must carry its proven clean commit
    run_config = rex_run_config(env, lo, hi)
    identity = {'commit': env['commit'], 'evaluator': sha({'code': rex_code_digest(), 'config': run_config}),
                'manifest': env['manifest']['digest'], 'universe': env.get('universe'), 'classification': None,
                'prereg': None}
    cfg = {'tier': tier, 'symbols': list(env['symbols']), 'shared': sha(run_config), 'profile': REX_RUN.binding()}
    d = os.path.join(out_dir, tier)
    run = PG.run_id(identity, cfg)
    final = os.path.join(d, f'final-{run}.json')
    if _state(d, run, resume) == 'sealed':
        return final
    prog = PG.Progress(d, identity=identity, config=cfg, clock=clock)
    stage = 'pit.open'
    try:
        prog.stage_start(stage)
        if env.get('ledger'):
            rex_record_trials(env, run)
        prog.stage_done(stage)
        stage = 'pit.development'
        prog.stage_start(stage)
        summary, detail = rex_evaluate(env, tier, lo, hi, run_config)
        prog.stage_done(stage)
        stage = None
        paths = [write_json(os.path.join(d, f'summary-{run}.json'), summary),
                 write_json(os.path.join(d, f'detail-{run}.json'), detail)]
    except Exception as e:
        prog.aborted([PG.integrity_code(type(e).__name__)], stage=stage)
        raise
    ok = is_complete(summary) and is_complete(detail)
    prog.sealed(paths, [os.path.basename(paths[0])] if ok else [])
    return final


# ------------------------------------------------------------------ REX-02 store loader (attested manifest -> Access)
def rex_sources(repo: str) -> dict:
    """The committed inputs of a real REX development run (metadata only; no data row is read here): the attested
    archive manifest pinned by the latest prereg, the owner-approved static universe of the committed short gate, the
    pinned mainnet exchange rules and the distinct range_vwap_rev family ledger."""
    import run_r4 as G                                                                      # noqa: E402
    pre = json.loads(G._load(repo, G.SHORT_PREREG))
    g = json.loads(G._load(repo, G.SHORT_GATE))
    man = M.load(os.path.join(repo, MANIFEST_PATH))
    if man['digest'] != pre['data']['manifest_digest']:
        raise PitRunError('REX-02: the manifest differs from the prereg-pinned digest')
    static = G.static_universe(repo, g['universe']['path'], g['universe']['snapshot'])
    rules = load_rules(G._load(repo, RULES_PATH), static['core_20'])
    return {'manifest': man, 'static': static, 'rules': rules, 'ledger': os.path.join(repo, REX_LEDGER)}


def _git(repo: str, *args) -> str:
    return subprocess.run(['git', '-C', repo, *args], capture_output=True, text=True, check=True).stdout


def rex_clean_commit(repo: str) -> str:
    """The exact commit a historical / store run executes (Codex 6172596212 c): refuses unless `git status
    --porcelain` (tracked changes AND untracked files) is empty; returns the 40-hex HEAD SHA. Called before any
    artifact, manifest, ledger or data access."""
    try:
        dirty = _git(repo, 'status', '--porcelain', '--untracked-files=all')
        head = _git(repo, 'rev-parse', '--verify', 'HEAD^{commit}').strip()
    except (OSError, subprocess.CalledProcessError) as e:
        raise PitRunError(f'REX-02: cannot prove a clean git tree at {repo} ({e}); a store run refuses')
    if dirty.strip():
        n = len(dirty.strip().splitlines())
        raise PitRunError(f'REX-02: dirty git tree ({n} changed / untracked path(s)); a historical / store run '
                          f'refuses before any access (commit or remove them first)')
    if len(head) != 40 or any(c not in '0123456789abcdef' for c in head):
        raise PitRunError(f'REX-02: HEAD {head!r} is not a 40-hex commit SHA')
    return head


def rex_series(w, s: str, rule: dict):
    """One symbol's REX series read THROUGH the opened Access window (Cowork N1): klines and 1h marks from a View at
    the decision end (w.hi - tail: rows with open >= w.lo, available <= end), funding from a View at w.hi (the window
    was opened to end + REX_FUNDING_TAIL_MS, so the documented 60 s tail is inside the recorded access window). Every
    row passes the window gate and the manifest's SHA-256 re-check (Dataset.rows); no direct dataset read."""
    end = w.hi - REX_FUNDING_TAIL_MS
    v = w.view(end)
    bars = {b.open_ms: b for b in v.bars(s, '4h', (end - w.lo) // RV.TF + 1)}
    marks = {b.open_ms: b.close for b in v.mark_bars(s, (end - w.lo) // RV.HOUR + 1)}
    fund = w.view(w.hi).funding(s, w.lo)
    return RV.Series(bars, {k: rule[k] for k in RULES_FIELDS}, tuple(fund), marks)


def _rex_read_artifact(path: str, sha256: str) -> dict:
    """The pinned zb-delist-notice/1 artifact: missing, unreadable, not JSON or not the pinned digest REFUSES
    (Codex 6172596212 a: absence is never INCOMPLETE)."""
    try:
        with open(path, 'rb') as f:
            art = json.loads(f.read())
    except (OSError, TypeError, ValueError) as e:
        raise PitRunError(f'REX-02: {NT.RULE_ID} artifact missing or unreadable ({type(e).__name__}): refused')
    try:
        NA.validate(art, expect_digest=sha256)
    except NA.ArtifactError as e:
        raise PitRunError(f'REX-02: {e}')
    return art


def rex_load(src: dict, *, repo: str, store: str, tier: str, window, notice_path: str, notice_sha256: str,
             author: str, cairo_date: str, plan: S.SplitPlan, variant=None, addon=None, addon_report=None, addon_sha256=None,
             addon_zips=None) -> dict:
    """The REX-02 loader: window -> CLEAN git tree (exact commit SHA) -> notice artifact (exact pinned digest) -> tier
    symbols + rules -> ledger guard -> StaticDataset -> ledger-recorded development Access (lineage root) -> tape read
    through the Window + its manifest. Returns the run env for run_rex with `historical` provenance (nothing is read
    before the window, clean-tree, artifact and ledger checks pass). `variant` (explicit, development-only) records
    the Access under the variant's own candidate id; None = the frozen v1. Add-on (optional; all four or none):
    `addon` = the RAW bytes of a `zb-rex-gap-fill/1` add-on file, `addon_sha256` = its digest pinned by the run
    command (checked before parsing), `addon_report` = the signed gap report it pins, `addon_zips` = the kept archive
    zips every row is re-proved from (rex_addon). All of it, incl. value checks and the base-collision check (time
    column only), runs before the Access opens; the rows are merged into exact holes after the base tape is read.
    Without them the loader is unchanged."""
    cid, vrec = rex_variant(variant)
    lo, hi = rex_window(window, plan)
    if tier not in RUN_TIERS:
        raise PitRunError(f'REX-02: tier must be one of {RUN_TIERS}')
    commit = rex_clean_commit(repo)
    art = _rex_read_artifact(notice_path, notice_sha256)
    static = src['static']
    symbols = list(static['core_10' if tier == 'CORE-10' else 'core_20'])
    missing = [s for s in symbols if s not in src['rules']]
    if missing:
        raise PitRunError(f'REX-02: no pinned exchange rule for {missing}')
    ledger = src['ledger']
    rex_no_holdout(ledger)
    added = None
    parts = (addon, addon_report, addon_sha256, addon_zips)
    if any(x is not None for x in parts):
        import rex_addon as RA                                                              # noqa: E402
        import rex_gap_inventory as GI                                                      # noqa: E402
        if any(x is None for x in parts):
            raise PitRunError('REX-02: an add-on needs both the add-on manifest and its signed gap report, its pinned '
                              'sha256 and the kept archive zips (all four)')
        try:
            doc = RA.load_pinned(addon, addon_sha256)
            added = RA.validate(doc, addon_report, manifest_digest=src['manifest']['digest'], symbols=symbols,
                                lo=lo, hi=hi, cutoff=rex_sealed_cutoff(plan), zips=addon_zips,
                                addon_sha256=addon_sha256)
            try:
                idx = GI.index(src['manifest'], store, symbols, lo, hi)
            except (GI.InventoryError, M.ManifestError, P.PITError, OSError, ValueError) as e:
                raise RA.AddonError(f'cannot index the base store times ({type(e).__name__}: {e})')
            RA.check_base(added, idx)
        except RA.AddonError as e:
            raise PitRunError(f'REX-02: add-on refused: {e}')
    ds = StaticDataset(src['manifest'], store, static, symbols)
    acc = P.Access(ds, plan, ledger, candidate_id=cid, author=author, cairo_date=cairo_date, repo=repo)
    detail = {'purpose': 'REX-02 range_vwap_rev.v1 development screen (no holdout, not promotable)',
              'promotable': False, 'tier': tier, 'symbols': symbols, 'notice_artifact': notice_sha256,
              'commit': commit, 'decision_window': {'start': S.utc(lo), 'end': S.utc(hi)},
              'funding_tail_ms': REX_FUNDING_TAIL_MS, 'cost_rows': list(REX_ROWS)}
    if vrec is not None:
        detail = dict(detail, purpose=f'REX-02 {cid} development screen (no holdout), {vrec["label"]}',
                      variant=vrec)
    if added is not None:
        detail = dict(detail, addon=added['provenance'])
    w = acc.open_development(S.utc(lo), S.utc(hi + REX_FUNDING_TAIL_MS), lineage='root', detail=detail)
    tape = {s: rex_series(w, s, src['rules'][s]) for s in symbols}
    source = {'data_manifest': ds.digest, 'universe': static['digest'], 'loader': ds.manifest['loader_version']}
    if added is not None:
        try:
            tape = RA.merge(tape, added)
        except RA.AddonError as e:
            raise PitRunError(f'REX-02: add-on refused: {e}')
        source = dict(source, addon=added['provenance']['digest'])
    man = rex_tape_manifest(tape, art, symbols=symbols, source=source)
    return {'tape': tape, 'manifest': man, 'notice_artifact': art, 'notice_sha256': notice_sha256,
            'symbols': symbols, 'commit': commit, 'universe': static['digest'], 'ledger': ledger, 'author': author,
            'cairo_date': cairo_date,
            'provenance': {'class': 'historical', 'commit': commit, 'tree': 'clean'},
            'access': {'family': RV.FAMILY, 'window': w.window_doc, 'manifest': ds.digest, 'split': 'development'},
            **({} if vrec is None else {'variant': cid}),
            **({} if added is None else {'addon': added['provenance']})}
