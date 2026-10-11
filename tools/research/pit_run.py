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
  4. accounting from the attested tape (`account`): long only, one lot per symbol per configuration, decision at
     bar close t inside the split's decision range, fill at the next bar open, stop = fill - registered stop
     distance (2.5 x Wilder ATR14; neighbours their own), signal exit at the next bar open, stop over each played
     bar (`costs.stop_reference`: a bar opening through the stop fills at its open = gap), and the 180-bar cap: a lot
     still open at the close of bar entry_index + 179 exits there (the last price at entry + 180 x 4h; the next open
     is not available inside the split), so every capped episode lies inside its split (purge/embargo = the plan's).
     Costs are the registered `zb-cost-model/2` crypto row through costs.py itself: VIP0 taker each side, slip-v1
     (TR-SMA14, c = slip-cal-v1 crypto, floor 2 bps), signed actual funding at each actual funding time x
     funding-mark proxy v1, every stress row of `costs.STRESS` re-pricing the same path (the stop level and exit path
     are those of the base row; R = net per unit / registered stop distance). Unit-quantity R: no lot step / tick
     rounding (follower-tier sizing is a later row);
  5. INCOMPLETE, never counted: a lot meeting a missing bar (gap), a funding sequence that is not provably complete or
     lacks a mark, or a lot still open at the split end (only the uncapped variant can) is INCOMPLETE; every trade of
     that (configuration, split, symbol) is then excluded from the metrics and reported (counts, never silently);
  6. aggregate metrics, paired baselines (B0 episode-paired matched random entries, B1 weekly equal-weight hold and
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
from decimal import Decimal

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import costs as C                                                                           # noqa: E402
import ledger as L                                                                          # noqa: E402
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
MANIFEST_PATH = 'research_evidence/manifests/binance-um-archive-v1.json.gz'
TF = T.TF_MS
HOUR = 3_600_000
DAY = 86_400_000
WEEK_BARS = 7 * DAY // TF
FUND_SPAN = (C.MAX_FUNDING_INTERVAL_H + 1) * HOUR
BASE = C.STRESS['base']
EXCLUDED_SPLITS = ('calibration', 'holdout')       # calibration: no strategy evaluation; holdout: sealed
NOT_EVALUATED = (
    'MTM drawdown + episode-bootstrap MC p5/p50/p95 (closed-trade drawdown is reported; E10 is PARK-only)',
    'follower tiers 100 / 200 / 500 USDT and RULES-BACKFILLED share (E11; needs exchange lot rules)',
    'per-side split (long only rule; the short mirror is never an edge claim)',
    'contribution by regime and B0 regime matching (no registered PIT regime labeller in code)',
    'stop-first vs target-first (one stop, no target: every exit bar is single-level)',
    'MDE at 80% power (holdout-only quantity)')


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


def net_r(x: Sym, *, entry_ref, entry_ms, dist, exit_ref, exit_ms, gap, exit_bps_at, nbars, slip_c, rows):
    """Net R per stress row of one unit-quantity long trade on a fixed path (see the module doc)."""
    ev = x.events(entry_ms, exit_ms)
    out = {}
    for name in rows:
        scen = C.STRESS[name]
        ef = C.fill_price(entry_ref, 'buy', x.bps(entry_ms, scen.vol, slip_c), scen)
        xf = C.fill_price(exit_ref, 'sell', x.bps(exit_bps_at, scen.vol, slip_c), scen, gap=gap)
        fees = C.fee(ef, 'taker', C.CRYPTO, scen) + C.fee(xf, 'taker', C.CRYPTO, scen)
        fund = C.funding_cost('long', 1.0, ev, scen, bars_held=nbars, entry_notional=ef)
        out[name] = (xf - ef - fees - fund) / dist
    return out


def _signal(cfg, x: Sym, rec, t):
    """(le, lx, stop distance | None) of a configuration at t from the tape."""
    if cfg['kind'] == 'sig':
        k = cfg['k']
        if rec['w'][k] != '1':
            return False, False, None
        d = rec['e'].get(str(k))
        return d is not None, rec['x'][k] == '1', None if d is None else float(Decimal(d))
    d = None if rec['d'] is None else float(Decimal(rec['d']))
    if cfg['kind'] == 'b1':
        monday = t % DAY == 0 and (t // DAY - 4) % 7 == 0
        return monday and d is not None, False, d
    back = x.rec.get(t - 20 * TF)                      # B2: close > close 20 bars earlier (contiguous bars only)
    if back is None or any((t - j * TF) not in x.rec for j in range(1, 20)):
        return False, True, d
    up = rec['b'][3] > back['b'][3]
    return up and d is not None, not up, d


def account(outputs, cfg: dict, *, first: int, last: int, hi: int, slip_c: float, rows=tuple(C.STRESS)) -> dict:
    """Trades of one configuration over one split's attested tape (pure; see the module doc)."""
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
            if pend_entry is not None:
                sig_t, dist = pend_entry
                pend_entry = None
                try:
                    fill = C.fill_price(o, 'buy', x.bps(bo, BASE.vol, slip_c), BASE)
                except Incomplete:
                    refusals['entry_no_slip_history'] += 1
                else:
                    lot = {'entry_ms': bo, 'entry_ref': o, 'dist': dist, 'fill': fill,
                           'stop': fill - dist if cfg['stop'] else None, 'hi': o, 'lo': o}
            if lot is not None:
                lot['hi'], lot['lo'] = max(lot['hi'], h), min(lot['lo'], lo)
                if lot['stop'] is not None and (o <= lot['stop'] or lo <= lot['stop']):
                    ref, gap = C.stop_reference('long', lot['stop'], o)
                    close(ref, bo, 'exit.stop', gap, bo, (bo - lot['entry_ms']) // TF + 1)
                    lot = None
            if lot is not None and cfg['cap'] is not None:
                held = t - lot['entry_ms']
                if held > cfg['cap'] * TF:
                    raise PitRunError(f'{s}: lot held past its time cap')         # unreachable: every bar is played
                if held == cfg['cap'] * TF:
                    close(c, t, 'exit.time_cap', False, t, cfg['cap'])
                    lot = None
            le, lx, dist = _signal(cfg, x, rec, t)
            if lx and lot is not None:
                pend_exit = t
            if le and lot is None and first <= t <= last:
                if not dist or dist <= 0:
                    refusals['entry_bad_stop_distance'] += 1
                else:
                    pend_entry = (t, dist)
        if lot is not None:
            if cfg['cap'] is not None and lot['entry_ms'] + cfg['cap'] * TF <= hi:
                raise PitRunError(f'{s}: a capped lot outlived its cap inside the split')   # unreachable
            incomplete.append({'symbol': s, 'entry_ms': lot['entry_ms'], 'reason': 'incomplete.split_end'})
    trades.sort(key=lambda r: (r['entry_ms'], r['symbol']))
    bad = sorted({i['symbol'] for i in incomplete})
    counted = [r for r in trades if r['symbol'] not in bad]
    return {'config': cfg['name'], 'trades': trades, 'counted': counted, 'incomplete': incomplete,
            'excluded_symbols': bad, 'excluded_trades': len(trades) - len(counted), 'refusals': dict(sorted(refusals.items()))}


# ------------------------------------------------------------------ B0: matched random entries
def b0_pairs(outputs, primary: dict, *, first: int, last: int, hi: int, slip_c: float, seed_key: str,
             draws: int = B0_DRAWS) -> dict:
    """Episode-paired differences: candidate episode mean net R - the mean of its matched draws (same symbol, split,
    hold horizon = the candidate's realised bars capped at 180, same stop rule, base costs and funding; entry times
    drawn uniformly from that symbol's eligible decision times in the split). An invalid draw (gap / funding / slip
    history) is excluded and counted."""
    times, syms = read_tape(outputs)
    elig = {s: [t for t in times if first <= t <= last and t in x.rec and x.rec[t]['d'] is not None]
            for s, x in syms.items()}
    cache, invalid = {}, 0

    def sim(s, e, h):
        key = (s, e, h)
        if key not in cache:
            x = syms[s]
            d = float(Decimal(x.rec[e]['d']))
            try:
                if e + h * TF > hi or any((e + j * TF) not in x.rec for j in range(1, h + 1)):
                    raise Incomplete('incomplete.gap')
                o0 = x.rec[e + TF]['b'][0]
                fill = C.fill_price(o0, 'buy', x.bps(e, BASE.vol, slip_c), BASE)
                stop, res = fill - d, None
                for j in range(1, h + 1):
                    o, _, lo, c = x.rec[e + j * TF]['b']
                    if o <= stop or lo <= stop:
                        ref, gap = C.stop_reference('long', stop, o)
                        bo = e + (j - 1) * TF
                        res = net_r(x, entry_ref=o0, entry_ms=e, dist=d, exit_ref=ref, exit_ms=bo, gap=gap,
                                    exit_bps_at=bo, nbars=j, slip_c=slip_c, rows=('base',))['base']
                        break
                if res is None:
                    t = e + h * TF
                    res = net_r(x, entry_ref=o0, entry_ms=e, dist=d, exit_ref=x.rec[t]['b'][3], exit_ms=t, gap=False,
                                exit_bps_at=t, nbars=h, slip_c=slip_c, rows=('base',))['base']
                cache[key] = res
            except Incomplete:
                cache[key] = None
        return cache[key]

    means = {}
    for tr in primary['counted']:
        h = max(1, min(CAP, tr['bars']))
        pool = elig.get(tr['symbol'], [])
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
    return {'diffs': diffs, 'invalid_draws': invalid, 'episodes': len(diffs)}


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
           'stress_mean_r': {k: (math.fsum(r['r'][k] for r in tr) / len(tr) if tr else None) for k in rows}}
    if boot is not None:
        out.update(bootstrap(tr, **boot))
    return out


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
    ds = StaticDataset(env['manifest'], env['store'], env['static'], symbols)
    rows = tuple(cfg_run['stress_rows'])
    per_split, detail_splits, blocks, b0_diffs = {}, {}, [], []
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
        res = {c['name']: account(ev, c, first=first, last=last, hi=hi, slip_c=cfg_run['slip_c'], rows=rows)
               for c in cfgs}
        prim = res[PRIMARY_NAME]
        boot = {'seed': cfg_run['seed'], 'resamples': cfg_run['resamples']}
        per_split[key] = {'primary': metrics(prim, rows=rows, boot=boot),
                          'uncapped_development_only': metrics(res[DEV_ONLY], rows=rows, boot=boot)}
        b0 = b0_pairs(ev, prim, first=first, last=last, hi=hi, slip_c=cfg_run['slip_c'],
                      seed_key=f'{cfg_run["seed"]}|{tier}|{key}', draws=cfg_run['b0_draws'])
        b0_diffs += b0['diffs']
        blocks += month_blocks(key, first, last, prim, res['B1'], res['B2'])
        detail_splits[key] = {'attestation': ev.attestation_digest, 'outputs_digest': ev.attestation['outputs_digest'],
                              'b0_invalid_draws': b0['invalid_draws'],
                              'configs': {k: {'trades': v['trades'], 'incomplete': v['incomplete'],
                                              'excluded_symbols': v['excluded_symbols'], 'refusals': v['refusals']}
                                          for k, v in res.items()}}
        if prog is not None:
            prog.stage_done(stage, fold)
    return summarize(env, tier, symbols, per_split, detail_splits, blocks, b0_diffs)


def _counted(detail_splits, name, keys):
    out = []
    for k in keys:
        c = detail_splits[k]['configs'][name]
        bad = set(c['excluded_symbols'])
        out += [r for r in c['trades'] if r['symbol'] not in bad]
    return out


def _agg(detail_splits, name, keys, rows, boot):
    tr = _counted(detail_splits, name, keys)
    inc = [i for k in keys for i in detail_splits[k]['configs'][name]['incomplete']]
    ref = Counter()
    for k in keys:
        ref.update(detail_splits[k]['configs'][name]['refusals'])
    acc = {'counted': tr, 'incomplete': inc, 'refusals': dict(sorted(ref.items())),
           'excluded_symbols': sorted({f'{k}:{s}' for k in keys for s in detail_splits[k]['configs'][name]['excluded_symbols']}),
           'excluded_trades': sum(len(detail_splits[k]['configs'][name]['trades']) for k in keys) - len(tr)}
    return metrics(acc, rows=rows, boot=boot), tr


def summarize(env, tier, symbols, per_split, detail_splits, blocks, b0_diffs):
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
               'aggregates': {'development': dev_m, 'walk_forward': wf_m}, 'baselines': base, 'edge00': edge,
               'verdict': 'PARK', 'verdict_basis': 'prereg verdict_rules.development: development evidence can neither '
               'PROMOTE nor REJECT', 'flags': flags, 'not_evaluated': list(NOT_EVALUATED)}
    detail = {'format': DETAIL_FORMAT, 'tier': tier, 'label': LABEL, 'symbols': list(symbols), 'splits': detail_splits,
              'neighbours_mean_r': neigh, 'loso_mean_r': loso_m, 'loyo_mean_r': loyo_m, 'month_blocks': blocks,
              'b0_episode_differences': b0_diffs}
    return summary, detail


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
    run_config = {'configs': [c['name'] for c in configs()], 'stress_rows': list(C.STRESS),
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
            'identity': identity, 'n_trials': n_trials, 'cost_labels': model.labels('BTCUSDT')}
