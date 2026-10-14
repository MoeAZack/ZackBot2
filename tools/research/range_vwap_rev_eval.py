"""REX-01 evaluation mechanics of `range_vwap_rev.v1` (v5 sections 4, 6, 7 + P3; Codex rulings 3-8, 6161875941;
split-end ruling 6169006548; contract rulings 6169708729). Synthetic / development only: no store, archive, holdout or market read happens here.

  * trials: 15 per side (X1, X2, X3; 8 X1 neighbours; B0-B3), family 30; program Holm n = 65 FROZEN in the prereg
    (18 trend_ema_mom + 17 trend_ema_mom_short + 30 range_vwap_rev), outside `run_r4.PROGRAM_FAMILIES`; each re-issue
    adds 30 (95, 125, ...), never reusing 65; the ledger counts may only be >= the frozen basis;
  * B0 timing null: X1 runs first, then per (symbol, side, week, z-quintile bin of X1's realised entries) draws from
    that week's eligible closes inside the side bucket, R = 10,000 replicate books (PCG64 from
    SeedSequence(20261026).spawn(R)); shortfall counted; a zero-trade replicate counts as an exceedance; mean |z|
    must match X1 within 0.10 and the shortfall must stay <= 25% of X1 signals, else PARK;
  * B1 weekly perp hold (1% / N risk units at 1.5 TR-SMA14, gross <= 3x), B2 naive fade (via `run_book`), B3 cash;
  * weekly R = (E_end - E_start) / (0.01 E_start) at Monday boundaries; one-sided paired sign-flip, exact at m <= 24
    non-zero differences, else 10,000 flips (B1 seed [20261026, 1], B2 [20261026, 2]);
  * seeds are fixed literals, never derived from H or data: B0 SeedSequence(20261026), sign-flip [20261026, 1] (B1)
    / [20261026, 2] (B2), episode-bootstrap / CI SeedSequence([20261026, 3]) (Codex 6169708729 item 1);
  * E10 = DRAWDOWN, PARK-only (item 2): bar-close MTM max drawdown + 10k episode-bootstrap DD p5 / p50 / p95; it can
    never PASS or REJECT until GOV freezes a ceiling, so every current EDGE-00 result carries e10_park = True;
  * EDGE-00 (CORE-20 X1, per side), M3 ineligibility caps, sealed INCOMPLETE packets (NO-RESULT), the atomic CORE-10 ->
    CORE-20 reveal, the outcome-blind VOID monitor, dates derived from H and the register access guards."""
from __future__ import annotations

import bisect
import calendar
import hashlib
import itertools
import json
import math
import os
import sys
from collections import Counter, defaultdict

import numpy as np

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import costs as C                                                                           # noqa: E402
import missing_data as MD                                                                   # noqa: E402
import range_vwap_rev as RV                                                                 # noqa: E402

TF, HOUR, DAY = RV.TF, RV.HOUR, 86_400_000
WEEK = 7 * DAY
ALPHA = 0.05
PER_SIDE = {'variants': 3, 'neighbours': 8, 'baselines': 4}
TRIALS_PER_SIDE = sum(PER_SIDE.values())                      # 15
FAMILY_TRIALS = 2 * TRIALS_PER_SIDE                           # 30
PROGRAM_BASIS = {'trend_ema_mom': 18, 'trend_ema_mom_short': 17, RV.FAMILY: FAMILY_TRIALS}
PROGRAM_N = sum(PROGRAM_BASIS.values())                       # 65, frozen
R_B0 = 10_000
FLIPS = 10_000
BOOT = 10_000
EXACT_MAX_M = 24
SEED = 20261026                                               # fixed literal, independent of H
SEED_B1, SEED_B2 = [SEED, 1], [SEED, 2]
SEED_BOOT = [20261026, 3]                                     # FROZEN literal episode-bootstrap / CI seed (6169708729)
E10_CEILING = None                                            # no GOV drawdown ceiling frozen: E10 is PARK-only
B0_MATCH_Z = 0.10
B0_SHORTFALL_CAP = 0.25
INCOMPLETE_PAIR_CAP = 0.05
INELIGIBLE_SYMBOL_CAP, INELIGIBLE_BOOK_CAP = 0.25, 0.10
UNRESOLVED_CAP = 0.05
EDGE_MIN_TRADES, EDGE_MIN_WEEKS, EDGE_MIN_BLOCKS = 40, 20, 6
HOLDOUT_WEEKS, BLOCK_WEEKS = 52, 4
S_END = calendar.timegm((2026, 10, 5, 0, 0, 0)) * 1000       # the only literal date
H_RECOMMENDED = calendar.timegm((2026, 11, 16, 0, 0, 0)) * 1000
HOLDOUT_MS = 364 * DAY
NO_RESULT = 'NO-RESULT'


class EvalError(ValueError):
    pass


def canon(o) -> bytes:
    return json.dumps(o, sort_keys=True, separators=(',', ':'), ensure_ascii=True, allow_nan=False).encode()


def digest(o) -> str:
    return hashlib.sha256(canon(o)).hexdigest()


# ------------------------------------------------------------------ multiplicity
def program_n(reissues: int = 0) -> int:
    """Frozen program Holm n: 65 for v1, + 30 per re-issue (95, 125, ...). Never derived from ledger counts."""
    if type(reissues) is not int or reissues < 0:
        raise EvalError('reissues must be a non-negative int')
    return PROGRAM_N + FAMILY_TRIALS * reissues


def check_basis(ledger_counts: dict, reissues: int = 0) -> int:
    """The range register's check: every family's ledger trial count >= the frozen basis (a shrink refuses; STRAT may
    grow past 35 without moving the frozen n). Returns the frozen n."""
    basis = dict(PROGRAM_BASIS, **{RV.FAMILY: FAMILY_TRIALS * (1 + reissues)})
    short = {f: (ledger_counts.get(f, 0), n) for f, n in basis.items() if ledger_counts.get(f, 0) < n}
    if short:
        raise EvalError(f'ledger trial counts below the frozen program basis: {short}')
    return program_n(reissues)


def holm(ps: dict, m: int) -> dict:
    """Holm step-down adjusted p-values over m (>= tested) trials (the pit_run.holm rule)."""
    items = sorted((p, k) for k, p in ps.items() if p is not None)
    m = max(m, len(items))
    out, run = {k: None for k in ps}, 0.0
    for i, (p, k) in enumerate(items):
        run = max(run, min(1.0, (m - i) * p))
        out[k] = run
    return out


def mc_p(k: int, R: int = R_B0) -> float:
    return (1 + k) / (R + 1)


def min_attainable_p(method: str, m: int | None = None, R: int = R_B0) -> float:
    if method == 'mc':
        return 1 / (R + 1)
    if method == 'exact':
        return 2.0 ** -m
    raise EvalError("method must be 'mc' or 'exact'")


def resolvable(min_p: float, n: int = PROGRAM_N) -> bool:
    """A row whose minimum attainable p exceeds the first Holm step 0.05 / n is PARK (unresolvable)."""
    return min_p <= ALPHA / n


# ------------------------------------------------------------------ sign flip
def sign_flip(diffs, *, seed, flips: int = FLIPS) -> dict:
    """One-sided paired sign-flip p (H1: mean d > 0) over the non-zero d_w: exact enumeration (meet in the middle) at
    m <= 24, else `flips` Monte Carlo flips from PCG64(SeedSequence(seed)), p = (1 + k) / (flips + 1)."""
    d = [x for x in diffs if x != 0]
    m = len(d)
    if not m:
        return {'p': None, 'method': None, 'm': 0, 'min_p': None}
    obs = math.fsum(d)
    eps = 1e-12 * max(1.0, abs(obs))
    if m <= EXACT_MAX_M:
        a, b = d[:m // 2], d[m // 2:]
        sa = [math.fsum(s * x for s, x in zip(sg, a)) for sg in itertools.product((1, -1), repeat=len(a))]
        sb = sorted(math.fsum(s * x for s, x in zip(sg, b)) for sg in itertools.product((1, -1), repeat=len(b)))
        ge = sum(len(sb) - bisect.bisect_left(sb, obs - eps - x) for x in sa)
        return {'p': ge / 2 ** m, 'method': 'exact', 'm': m, 'min_p': min_attainable_p('exact', m)}
    rng = np.random.Generator(np.random.PCG64(np.random.SeedSequence(seed)))
    arr = np.array(d)
    signs = rng.choice(np.array([1.0, -1.0]), size=(flips, m))
    k = int(np.sum(signs @ arr >= obs - eps))
    return {'p': mc_p(k, flips), 'method': 'mc', 'm': m, 'min_p': min_attainable_p('mc', R=flips)}


# ------------------------------------------------------------------ weeks, weekly R
def week_of(ms: int, H: int) -> int:
    return (ms - H) // WEEK


def weekly_r(series, H: int, end: int, equity0: float = RV.BOOK['equity0']) -> dict:
    """R(w) = (E_end(w) - E_start(w)) / (0.01 E_start(w)) from MTM equity at the Monday boundaries H + 7j d."""
    eq = dict(series)
    out = {}
    for j, w0 in enumerate(range(H, end, WEEK)):
        w1 = min(w0 + WEEK, end)
        e0, e1 = eq.get(w0, equity0 if w0 == H else None), eq.get(w1)
        if e0 is None or e1 is None:
            raise EvalError(f'weekly R: no MTM equity at the week {j} boundary (incomplete series)')
        out[j] = (e1 - e0) / (0.01 * e0)
    return out


# ------------------------------------------------------------------ B0 (timing null under X1 admission)
def z_table(tape, cfg, side, *, first, end, notices=RV.NO_NOTICES) -> dict:
    """Decision-time z_i of every eligible close in [first, end) (no outcome): {(symbol, close): z}."""
    out = {}
    for s, x in tape.items():
        for t in range(first, end, TF):
            if not RV.eligible(x, s, t, notices):
                continue
            try:
                ind = RV.indicators(cfg, RV.read(x, t, cfg.lookback))
            except RV.Incomplete:
                continue
            if ind.z is not None:
                out[(s, t)] = float(ind.z)
    return out


def b0_bins(x1: dict) -> list[float]:
    """Per-side quantile bin edges of X1's realised entry z_i: Q = min(5, n) bins (Q - 1 interior edges)."""
    zs = [tr['z'] for tr in x1['trades']] + [v['z'] for v in x1.get('open_lots', {}).values()]
    q = min(5, len(zs))
    if q == 0:
        return []
    return [float(v) for v in np.quantile(np.array(zs), [j / q for j in range(1, q)])]


def bin_of(z: float, edges) -> int:
    return bisect.bisect_left(edges, z)


def b0_draws(x1: dict, zt: dict, cfg, side, *, H, rng) -> tuple[set, int]:
    """One replicate's draws: for every X1 signal (symbol, week, bin b) one close drawn without replacement from that
    symbol's closes in that week with z in bin b inside the side bucket. Returns (draws, empty-bin shortfall)."""
    edges = b0_bins(x1)
    need = Counter((s, week_of(t, H), bin_of(z, edges)) for s, t, z in x1['signals'])
    pool = defaultdict(list)
    for (s, t), z in sorted(zt.items()):
        if RV.in_bucket(cfg, RV.fr(z), side):
            pool[(s, week_of(t, H), bin_of(z, edges))].append(t)
    draws, short = set(), 0
    for key in sorted(need):
        cand = pool.get(key, [])
        k = min(need[key], len(cand))
        short += need[key] - k
        if k:
            pick = rng.choice(len(cand), size=k, replace=False)
            draws |= {(key[0], cand[int(i)]) for i in sorted(pick)}
    return draws, short


def b0_park(x1_abs_z, b0_abs_z, shortfall_share) -> list[str]:
    """B0 is PARK when its pooled mean entry |z| differs from X1's by more than 0.10 z (unmatched null) or the
    shortfall exceeds 25% of X1's signals on the side (P3)."""
    park = []
    if x1_abs_z is None or b0_abs_z is None or abs(b0_abs_z - x1_abs_z) > B0_MATCH_Z + 1e-12:
        park.append('B0-UNMATCHED')
    if shortfall_share > B0_SHORTFALL_CAP:
        park.append('B0-SHORTFALL')
    return park


def b0(tape, cfg, side, x1: dict, *, first, end, notices=RV.NO_NOTICES, R: int = R_B0, row='base') -> dict:
    """B0 replicate books after X1 (X1 counts fixed first). Inherits INCOMPLETE from X1 or any replicate."""
    if x1['status'] != RV.COMPLETE:
        return {'status': RV.INCOMPLETE, 'inherited': True, 'policy': RV.POLICY, 'incomplete': [{'reason': 'x1'}]}
    zt = z_table(tape, cfg, side, first=first, end=end, notices=notices)
    seqs = np.random.SeedSequence(SEED).spawn(R)
    x1_mean = (math.fsum(t['r'] for t in x1['trades']) / len(x1['trades'])) if x1['trades'] else None
    k, short, absz, n_sig = 0, 0, [], len(x1['signals'])
    for ss in seqs:
        rng = np.random.Generator(np.random.PCG64(ss))
        draws, s0 = b0_draws(x1, zt, cfg, side, H=first, rng=rng)
        rep = RV.run_book(tape, cfg, side, first=first, end=end, notices=notices, mode='b0', draws=draws, row=row)
        if rep['status'] != RV.COMPLETE:
            return {'status': RV.INCOMPLETE, 'inherited': True, 'policy': RV.POLICY, 'incomplete': rep['incomplete']}
        short += s0 + sum(v for c, v in rep['counts'].items() if c.startswith('b0_shortfall'))
        tr = rep['trades']
        absz += [abs(t['z']) for t in tr]
        mean = math.fsum(t['r'] for t in tr) / len(tr) if tr else None
        if mean is None or x1_mean is None or mean >= x1_mean - 1e-12:
            k += 1                                    # zero-trade replicate = exceedance (fail closed)
    x1z = math.fsum(abs(t['z']) for t in x1['trades']) / len(x1['trades']) if x1['trades'] else None
    b0z = math.fsum(absz) / len(absz) if absz else None
    share = short / (R * n_sig) if n_sig else 0.0
    park = b0_park(x1z, b0z, share)
    return {'status': RV.COMPLETE, 'policy': RV.POLICY, 'k': k, 'R': R, 'p': mc_p(k, R),
            'min_p': min_attainable_p('mc', R=R), 'shortfall': short, 'shortfall_share': share,
            'x1_mean_abs_z': x1z, 'b0_mean_abs_z': b0z, 'park': park, 'row': 'PARK' if park else 'TESTED'}


# ------------------------------------------------------------------ B1 weekly perp hold
def b1_targets(E_tau: float, atrs: dict, closes: dict, rules: dict, cfg=RV.PRIMARY, book=RV.BOOK) -> dict:
    """q*_s = floor_step((1% E / N) / (1.5 TR-SMA14_s)); if sum q* close 1.10 > 3 E all legs are scaled to the cap
    BEFORE flooring."""
    n = len(atrs)
    if not n:
        return {}
    raw = {s: RV.fr(E_tau) * RV.fr(book['risk_pct']) / n / (RV.fr(cfg.stop_atr) * a) for s, a in atrs.items()}
    gross = sum(q * RV.fr(closes[s]) * (1 + RV.fr(book['cap_gap_buffer'])) for s, q in raw.items())
    cap = RV.fr(book['max_leverage']) * RV.fr(E_tau)
    scale = cap / gross if gross > cap else 1
    return {s: RV.floor_to(q * scale, rules[s]['step']) for s, q in raw.items()}


def b1_book(tape, side, *, first, end, order=None, row='base', notices=RV.NO_NOTICES, cfg=RV.PRIMARY) -> dict:
    """B1 per side (Codex 6169708729 item 3): a CONTINUOUS equal-risk weekly comparator, not an X1 trade book.
    Rebalance to `b1_targets` at every Monday 00:00 UTC open (H + 7j d) trading the deltas (taker, slip-v1, X1 costs),
    no stop / cap, funding-window/2 per leg (anchored at the leg's first open), MTM at every bar close, a delist notice
    exits at the first open after it. Open legs are VALUED at each declared Monday / end boundary with the last
    causally available in-window mark (the close of the bar ending at the boundary): no synthetic exit or fill is
    created, nothing past the boundary is read, and a missing bar / funding / mark is INCOMPLETE, never bypassed."""
    scen = C.STRESS[row]
    order = list(order or tape)
    sg = 1.0 if side == 'long' else -1.0
    buy, sell = ('buy', 'sell') if side == 'long' else ('sell', 'buy')
    wallet, legs, closes, series, target = RV.BOOK['equity0'], {}, {}, [], None
    try:
        for t in range(first, end + TF, TF):
            if t > first:
                bo = t - TF
                for s in list(legs):
                    rel = notices.release(s)
                    if rel is not None and rel < bo:
                        target = dict(target or {}, **{s: 0.0})
                if target is not None:
                    for s in order:
                        x = tape[s]
                        q0 = legs.get(s, {}).get('qty', 0.0)
                        q1 = target.get(s, q0)                 # a symbol without a target keeps its leg
                        dq = q1 - q0
                        if dq == 0:
                            continue
                        b = RV.bar_at(x, bo, held=s in legs, phase='next_open')
                        act = buy if dq > 0 else sell
                        px = C.fill_price(b.open, act, RV.bps_at(x, bo, scen), scen, tick=x.rule['tick'])
                        wallet -= C.fee(abs(dq) * px, 'taker', RV.ROW, scen)
                        if s in legs:
                            leg = legs[s]
                            if dq < 0:
                                wallet += sg * (-dq) * (px - leg['avg'])
                            else:
                                leg['avg'] = (leg['avg'] * q0 + px * dq) / q1
                            leg['qty'] = q1
                            if q1 == 0:
                                del legs[s]
                        else:
                            legs[s] = {'qty': q1, 'avg': px, 'entry_ms': bo}
                    target = None
                for s, leg in legs.items():
                    x = tape[s]
                    b = RV.bar_at(x, bo, held=True)
                    cost, _ = RV.bar_funding(x, side, leg['qty'], leg['entry_ms'], bo, scen, stopped=False)
                    wallet -= cost
                    closes[s] = b.close
            mtm = wallet + math.fsum(sg * v['qty'] * (closes[s] - v['avg']) for s, v in legs.items())
            series.append((t, mtm))
            if (t - first) % WEEK == 0 and t < end:
                atrs, cl = {}, {}
                for s in order:
                    x = tape[s]
                    if not RV.eligible(x, s, t, notices):
                        continue
                    bars = RV.read(x, t, 15)
                    atrs[s], cl[s] = RV._tr_sma14(bars), bars[-1].close
                target = b1_targets(mtm, atrs, cl, {s: tape[s].rule for s in order}, cfg)
                target.update({s: 0.0 for s in legs if s not in target})
    except RV.Incomplete as h:
        return {'status': RV.INCOMPLETE, 'policy': RV.POLICY, 'series': [],
                'incomplete': [{'reason': h.reason, 'first_unaccountable_ms': h.first_ms, 'secondary': False}]}
    return {'status': RV.COMPLETE, 'policy': RV.POLICY, 'series': series, 'legs_end': len(legs)}


# ------------------------------------------------------------------ EDGE-00
def week_clustered_boot(trades, H, *, seed=SEED_BOOT, B: int = BOOT) -> float | None:
    """Lower 95% bound of mean net R, resampling holdout weeks (with trades) with replacement."""
    wk = defaultdict(list)
    for t in trades:
        wk[week_of(t['entry_ms'], H)].append(t['r'])
    keys = sorted(wk)
    if not keys:
        return None
    sums = np.array([math.fsum(wk[k]) for k in keys])
    cnts = np.array([len(wk[k]) for k in keys])
    rng = np.random.Generator(np.random.PCG64(np.random.SeedSequence(seed)))
    idx = rng.integers(0, len(keys), size=(B, len(keys)))
    means = sums[idx].sum(axis=1) / cnts[idx].sum(axis=1)
    return float(np.sort(means)[int(0.025 * B)])


def ineligibility(per_symbol: dict, book: float) -> dict:
    over = sorted(s for s, v in per_symbol.items() if v > INELIGIBLE_SYMBOL_CAP)
    ok = not over and book <= INELIGIBLE_BOOK_CAP
    return {'per_symbol': per_symbol, 'book': book, 'over_symbol_cap': over,
            'status': 'EVALUABLE' if ok else 'NOT-EVALUABLE'}


def episodes(trades) -> list[list[dict]]:
    """Trades whose holding windows overlap form one episode (an intrabar stop holds through its bar)."""
    out, cur = [], None
    for r in sorted(trades, key=lambda r: (r['entry_ms'], r['symbol'])):
        end = r['exit_ms'] + (TF if r.get('intrabar') else 0)
        if out and r['entry_ms'] < cur:
            out[-1].append(r)
            cur = max(cur, end)
        else:
            out.append([r])
            cur = end
    return out


def e10_drawdown(series, trades, *, seed=SEED_BOOT, B: int = BOOT, equity0: float = RV.BOOK['equity0']) -> dict:
    """E10 = DRAWDOWN, PARK-only (Codex 6169708729 item 2): the bar-close MTM max drawdown (% of the running peak) and
    the episode-bootstrap closed-trade drawdown p5 / p50 / p95 (B resamples, PCG64 from SeedSequence(seed)). With no
    GOV ceiling frozen it can never PASS or REJECT: status PARK, park True."""
    peak, dd = equity0, 0.0
    for _, m in series:
        peak = max(peak, m)
        dd = max(dd, (peak - m) / peak if peak > 0 else 0.0)
    eps = [[t['net'] for t in e] for e in episodes(trades)]
    boot = None
    if eps:
        rng = np.random.Generator(np.random.PCG64(np.random.SeedSequence(seed)))
        dds = []
        for row in rng.integers(0, len(eps), size=(B, len(eps))):
            eq = pk = equity0
            d = 0.0
            for i in row:
                for v in eps[i]:
                    eq += v
                    pk = max(pk, eq)
                    d = max(d, (pk - eq) / pk if pk > 0 else 0.0)
            dds.append(100.0 * d)
        dds.sort()
        boot = {q: dds[min(B - 1, int(f * B))] for q, f in (('p5', 0.05), ('p50', 0.50), ('p95', 0.95))}
        boot.update(resamples=B, seed=list(seed))
    park = E10_CEILING is None
    return {'row': 'E10-DRAWDOWN', 'status': 'PARK' if park else 'EVALUATED', 'park': park, 'park_only': True,
            'ceiling': E10_CEILING, 'mtm_max_dd_pct': 100.0 * dd, 'boot_dd_pct': boot}


def edge00(*, side, H, base: dict, x2: dict, holm_p: dict, neighbours: dict, unresolved_share: float = 0.0,
           b0_row: str = 'TESTED') -> dict:
    """EDGE-00 per side on the CORE-20 X1 book (`base` = the base-row book, `x2` = the fees_slip_x2 book).
    Any INCOMPLETE input -> NO-RESULT. REJECT: sample met and base mean <= 0 (an expectancy verdict; E10 never
    rejects). PASS needs every row AND E10 not PARK - impossible while no GOV ceiling is frozen, so every current
    non-REJECT result is PARK with e10_park = True."""
    for name, doc in (('base', base), ('fees_slip_x2', x2)):
        try:
            MD.check(doc, name)
        except MD.MissingDataError:
            return {'side': side, 'verdict': NO_RESULT, 'reason': f'{name} INCOMPLETE ({RV.POLICY})'}
    inel = ineligibility(base['ineligible']['per_symbol'], base['ineligible']['book'])
    tr = base['trades']
    weeks = {week_of(t['entry_ms'], H) for t in tr}
    blocks = {w // BLOCK_WEEKS for w in weeks}
    rows = {'sample': len(tr) >= EDGE_MIN_TRADES and len(weeks) >= EDGE_MIN_WEEKS and len(blocks) >= EDGE_MIN_BLOCKS}
    mean = math.fsum(t['r'] for t in tr) / len(tr) if tr else None
    lo_b = week_clustered_boot(tr, H)
    lo_x = week_clustered_boot(x2['trades'], H)
    rows['expectancy'] = lo_b is not None and lo_x is not None and lo_b > 0 and lo_x > 0
    rows['baselines'] = (b0_row == 'TESTED' and all(holm_p.get(b) is not None and holm_p[b] < ALPHA
                                                     for b in ('B0', 'B1', 'B2')))

    def loo(key):
        g = defaultdict(list)
        for t in tr:
            g[key(t)].append(t['r'])
        outs = []
        for k in g:
            rest = [t['r'] for t in tr if key(t) != k]
            outs.append(math.fsum(rest) / len(rest) if rest else None)
        return bool(outs) and all(v is not None and v > 0 for v in outs)
    rows['robustness'] = loo(lambda t: t['symbol']) and loo(lambda t: week_of(t['entry_ms'], H) // BLOCK_WEEKS)
    keep = 0
    for nb in neighbours.values():
        if nb.get('status') != RV.COMPLETE or not nb['trades'] or mean is None:
            continue                                  # an INCOMPLETE neighbour is a fail
        m = math.fsum(t['r'] for t in nb['trades']) / len(nb['trades'])
        keep += (m > 0) == (mean > 0)
    rows['neighbours'] = len(neighbours) == 8 and keep >= 7
    rows['intrabar'] = unresolved_share <= UNRESOLVED_CAP
    rows['eligibility'] = inel['status'] == 'EVALUABLE'
    e10 = e10_drawdown(base.get('series', []), tr)
    if rows['sample'] and mean is not None and mean <= 0:
        verdict = 'REJECT'
    elif all(rows.values()) and not e10['park']:
        verdict = 'PASS'
    else:
        verdict = 'PARK'
    if not rows['eligibility']:
        verdict = 'PARK'                              # NOT-EVALUABLE: the universe cannot shrink silently
    return {'side': side, 'verdict': verdict, 'rows': rows, 'mean_r': mean, 'neighbours_kept': keep,
            'ineligibility': inel, 'e10': e10, 'e10_park': e10['park']}


# ------------------------------------------------------------------ sealed packets and the atomic reveal
PACKET_KEYS = ('status', 'policy', 'first_unaccountable_ms', 'last_valid_ms', 'class', 'symbol', 'digests', 'verdict')


def sealed_incomplete(doc: dict, digests: dict) -> dict:
    """The sealed INCOMPLETE packet: status, policy id, first unaccountable / last valid ms, the primary class and
    symbol, digests, verdict NO-RESULT - no trades, counts, metrics or equity (pre-halt included)."""
    c = doc.get('censor') or {}
    prim = next((r for r in doc.get('incomplete', []) if r.get('secondary') is False), {})
    return {'status': RV.INCOMPLETE, 'policy': RV.POLICY, 'first_unaccountable_ms': c.get('first_unaccountable_ms'),
            'last_valid_ms': c.get('last_valid_ms'), 'class': c.get('class') or prim.get('class'),
            'symbol': c.get('symbol', prim.get('symbol')), 'digests': dict(digests), 'verdict': NO_RESULT}


class Reveal:
    """The atomic final packet: once, at or after H + 364 d. CORE-10 integrity is validated first, then CORE-20; a
    failed / incomplete CORE-10 prevents CORE-20 evaluation and the packet is NO-RESULT. Both are exposed together."""

    def __init__(self, H: int):
        self.H, self.done = H, False

    def reveal(self, now_ms: int, core10: dict, core20_eval, digests: dict) -> dict:
        if self.done:
            raise EvalError('the holdout packet is revealed exactly once')
        if now_ms < self.H + HOLDOUT_MS:
            raise EvalError('no interim look: the reveal is at H + 364 d')
        self.done = True
        try:
            MD.check_tree(core10, 'CORE-10')
        except MD.MissingDataError:
            return {'verdict': NO_RESULT, 'core10': sealed_incomplete(core10, digests), 'core20': None,
                    'reason': 'CORE-10 integrity failed; CORE-20 not evaluated'}
        core20 = core20_eval()
        try:
            MD.check_tree(core20, 'CORE-20')
        except MD.MissingDataError:
            return {'verdict': NO_RESULT, 'core10': core10, 'core20': sealed_incomplete(core20, digests)}
        return {'verdict': core20.get('verdict'), 'core10': core10, 'core20': core20, 'digests': dict(digests)}


# ------------------------------------------------------------------ VOID monitor (outcome-blind)
def void_monitor(presence: dict, *, first, end, notices=RV.NO_NOTICES, funding_gaps=(), mark_gaps=()) -> dict:
    """Inputs: candle open timestamps per symbol (no prices, volumes, signals or positions), the notice register view
    and the eligibility rule. VOID only on a candle hole of an eligible symbol; funding / mark gaps are position-
    dependent and reported UNDETERMINED, never VOID."""
    holes = []
    for s in sorted(presence):
        ts = set(presence[s])
        firsts = min(ts) if ts else None
        for t in range(first - RV.WARMUP_MS, end, TF):
            listed = firsts is not None and firsts <= t + TF - RV.WARMUP_MS
            if listed and not notices.known(s, t + TF) and t not in ts:
                holes.append((s, t))
    undetermined = sorted(set(map(tuple, funding_gaps)) | set(map(tuple, mark_gaps)))
    return {'status': 'VOID' if holes else ('UNDETERMINED' if undetermined else 'OK'), 'holes': holes,
            'undetermined': undetermined}


def presence_of(tape: dict) -> dict:
    return {s: sorted(x.bars) for s, x in tape.items()}


# ------------------------------------------------------------------ dates and access guards
def is_monday_midnight(ms: int) -> bool:
    return ms % DAY == 0 and ((ms // DAY) + 3) % 7 == 0       # 1970-01-01 was a Thursday


def windows(H: int) -> dict:
    """Every window derives from H (S_END is the only literal)."""
    if not is_monday_midnight(H):
        raise EvalError('H must be a Monday 00:00 UTC')
    return {'never_read': (S_END, H - RV.WARMUP_MS), 'warmup': (H - RV.WARMUP_MS, H), 'holdout': (H, H + HOLDOUT_MS),
            'weeks': [(H + j * WEEK, H + (j + 1) * WEEK) for j in range(HOLDOUT_WEEKS)],
            'blocks': [(H + j * BLOCK_WEEKS * WEEK, H + (j + 1) * BLOCK_WEEKS * WEEK)
                       for j in range(HOLDOUT_WEEKS // BLOCK_WEEKS)]}


def slide_h(H: int, now_ms: int) -> int:
    """If prerequisites miss H, slide by whole weeks to the first Monday whose H - 240 h is still future."""
    while H - RV.WARMUP_MS <= now_ms:
        H += WEEK
    return H


def first_read(H: int, cfg) -> tuple[int, int]:
    """The bars the first decision at close H reads: [H - lookback x 4h, H)."""
    return H - cfg.lookback * TF, H


def iso(ms: int) -> str:
    import datetime as dt
    return dt.datetime.fromtimestamp(ms / 1000, dt.timezone.utc).strftime('%Y-%m-%dT%H:%M:%SZ')


def warmup_record(H: int, file_digests: dict) -> dict:
    """The `data_access` record of the warm-up read (existing ledger split `forward`, input only)."""
    a, b = windows(H)['warmup']
    return {'kind': 'data_access', 'candidate_id': RV.CANDIDATE, 'split': 'forward',
            'window': {'start': iso(a), 'end': iso(b)},
            'detail': {'purpose': 'input_only', 'file_digests': dict(file_digests), 'outcome': 'no outcome computed'}}


def _ms(s: str) -> int:
    return calendar.timegm(tuple(int(v) for v in (s[0:4], s[5:7], s[8:10], s[11:13], s[14:16], s[17:19]))) * 1000


def check_access(records, *, H: int, registration_ms: int, revealed: bool = False) -> None:
    """The range register refuses: registration at / after H or at / after the first read; any family read
    overlapping [S_end, H - 240 h); a `forward` read whose window is not exactly [H - 240 h, H); any read reaching
    >= H before the reveal."""
    if registration_ms >= H:
        raise EvalError('registration at or after H')
    nr0, nr1 = windows(H)['never_read']
    wa, wb = windows(H)['warmup']
    for r in records:
        if r.get('kind') != 'data_access':
            continue
        a, b = _ms(r['window']['start']), _ms(r['window']['end'])
        if a < nr1 and nr0 < b:
            raise EvalError('a family read overlaps the never-read window [S_end, H - 240 h)')
        if r['split'] == 'forward' and (a, b) != (wa, wb):
            raise EvalError('a forward read must be exactly [H - 240 h, H)')
        if b > H and not revealed:
            raise EvalError('a read at or after H before the reveal')
        if registration_ms >= a:
            raise EvalError('registration at or after the first read')
