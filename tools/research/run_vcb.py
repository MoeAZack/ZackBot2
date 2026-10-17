"""C2 dev-run CLI: vol_compression_breakout.v0 over a 4h app cache, one tier or symbol list, both sides - development
evidence only.

Adapted from the accepted C1 runner (run_lsfvg.py @ 50206bf); the same guards, re-stated for the C2 module's output:
  - window: explicit START/END, UTC, 4h grid, end-exclusive, through VC.check_window (pit_run.rex_window + the frozen
    development window + the sealed cutoff + the 220 x 4h warm-up guard + calibration overlap), plus this runner's own
    holdout check: any END after HOLDOUT_START (2026-09-28T00:00:00Z) is refused.
  - universe: the frozen static tiers (CORE-10 / CORE-20) or an explicit --symbols list inside the frozen universe.
    Eligibility by history (Codex 6229469658 section 3): a symbol enters a primary full-window run only if real bars cover
    the warm-up start through END with zero gaps and valid OHLC. A late-listed symbol is excluded ONLY when it is named
    in --exclude-late AND the cache proves it late-listed (its first bar before END opens on the 4h grid strictly after
    the warm-up start, and before END) and its own listed history [first bar, END) is gap-free, on grid, without
    duplicates and OHLC-sane (the flag never excuses a gap). Any other incomplete / gapped / off-grid / duplicate / invalid symbol - and any
    --exclude-late symbol that is not provably late-listed - REFUSES the whole run (no silent drop, no fill, no
    backdating). The packet carries universe.requested, universe.run (the actual symbols) and universe.excluded
    [{symbol, reason, first_bar, required_warmup_start}].
  - clean git tree before a single cache byte is read; packets are never overwritten; JSON sorted, allow_nan False,
    packet_sha256 over the canonical packet; runner_sha256 of this runner, the C2 rule module, the imported runner
    helpers, backtest.py, strategies.py and the pit_run / run_r4 / splits guard modules.
  - --check-coverage: reports only the first / last cached bar per symbol before END against the required warm-up
    start (nothing past END, no indicator, no signal); no packet.
  - cache: <cache>/<SYMBOL>_4h.csv (app._get_candles format) via B.read_cache, read and hashed whole, then sliced on the
    raw rows to [warm-up start, END) before any indicator, signal, sanity check or statistic (VC.slice_frame +
    VC.assert_bounded); completeness on the 4h grid (B.completeness) plus an exact-grid order check.
  - OHLC sanity before any indicator: any bar in [warm-up start, END) with a non-finite or non-positive o/h/l/c,
    h < l, h < max(o, c) or l > min(o, c) (equality valid) REFUSES the whole run. Bars at / after END are never checked.
  - packet write: written and fsynced in a unique temp file in the --out directory, then linked to --out exclusively
    (os.link fails if --out exists); the temp file is always removed.
  - warm-up: the 220 bars before START feed ATR14 (and the 120-value ATR history) only. Candidates whose breakout bar b
    is before START are removed BEFORE box consumption and the slot logic (the run starts flat and unconsumed at START,
    like backtest.run(t0=START)); they are counted in warmup.signals_excluded and never traded, skipped, blocked or
    reported as overlap.
  - trades still open at END: labelled 'open_end' by the module (R_net None); excluded from every statistic, counted in
    open_at_end and listed there with its entry. A close-rule exit decided on the last bar (fill at open[END]) is
    therefore also open_end. A signal on the last in-window bar is a 'no_next_bar' skip.
  - the rules and every cost are vol_compression_breakout.run_symbol / simulate_trade / summarize, unchanged (cost
    parity: no cost is recomputed here).

Added per side and combined: wins, avg R, max DD R (cumulative net R in exit order), per-quarter net R by ENTRY time
(own sign, every calendar quarter overlapping the window, empty quarters 0), mean / median hold bars, MFE / MAE /
giveback gross / net, exits, gap exits by label, skips by reason, consumption-blocked candidates, symbol concentration.

Decision (frozen C1 three-part rule, whole family / combined sides, per tier): CANDIDATE only if combined PF > 1.0 AND
combined positive quarters >= 2/3 of the window's quarters AND gross R per trade >= 3 x cost R per trade; else PARK.
Final: CANDIDATE on both eligible-history tiers.

Usage (PAPER research only, no network, no app settings or credentials):

  py -3.14 tools/research/run_vcb.py --cache <DATA>/candles --window 2024-10-14T00:00:00Z/2026-09-28T00:00:00Z \
     --tier CORE-10 --exclude-late HYPEUSDT --out <packet.json>
"""
import argparse
import contextlib
import hashlib
import json
import os
import sys
import tempfile
from datetime import datetime, timezone

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(os.path.dirname(HERE))
for _p in (ROOT, HERE):
    if _p not in sys.path:
        sys.path.insert(0, _p)

import numpy as np                                                                          # noqa: E402

import vol_compression_breakout as VCB                                                      # noqa: E402
import run_breadth_candidate as B                                                           # noqa: E402
import run_vwaprev_candidate as VC                                                          # noqa: E402
import splits as SP                                                                         # noqa: E402

FORMAT = 'zb-vcb-dev/1'
EVIDENCE_CLASS = 'candidate-runtime'
TF, TF_MS = VC.TF, VC.TF_MS
HOLDOUT_START = VC.SEALED_CUTOFF            # END may equal it (end-exclusive), never pass it
WARMUP_BARS = VC.WARMUP_BARS
assert VCB.TF == TF and WARMUP_BARS >= VCB.ATR_HIST + 14
SKIPS = ('cost_guard', 'gap_stop', 'overlap', 'no_next_bar')
LABELS = ('stop', 'target', 'invalidation', 'time')
ND = 9                                      # float rounding in the packet
FINAL_REQUIRES = 'CANDIDATE on both eligible-history CORE-10 and CORE-20 tiers (combined sides)'
NET_EPS = 1e-9                              # |group net R| below this: net_R_share is null
LATE_REASON = 'late_listed'
EXCLUSION_RULE = ('Codex 6229469658 section 3: a symbol enters a primary full-window tier only if real bars cover the '
                  'required warm-up start through END with zero gaps and valid OHLC; late-listed symbols are excluded '
                  'explicitly (--exclude-late, verified late-listed from the cache); never forward-fill, fake flat bars, '
                  'shorten the primary window, or silently rename an incomplete tier as complete')
# Codex 6229577171 FREEZE (Frozen choices) and 6229837790 rulings 1-4: verbatim, markdown emphasis removed
FROZEN_C2_SEMANTICS = (
    '4h, not 1h. Keep the 3R target, 18-bar decision cap / open b+19 exit, box-midpoint stop, and 0.75 / 120 '
    'compression thresholds.',
    'Box = b−12..b−1. Breakout/filters decide at close b; entry is actual open b+1.',
    'ATR is the existing Wilder EWM `adjust=False`. ATR index 0 is defined, but a candidate requires all 120 preceding '
    'ATR values b−120..b−1 finite and >0; earliest eligible b is 120. Median is the ordinary 120-value median '
    '(mean of the middle two).',
    'Filters use ATR[b−1]. The stop buffer intentionally uses ATR[b], known at close b. A mutation swapping those '
    'must fail.',
    'Long stop = midpoint − 0.10\xb7ATR[b]; short is exact mirror midpoint + 0.10\xb7ATR[b].',
    'Slot ordering: a trade still open during close x−1 owns the slot. Even if it exits at open x, the x−1 '
    'signal is ignored and that qualifying box is consumed. No same-open exit/re-entry.',
    'A box is consumed only after all compression + breakout tests pass, whether the resulting entry is taken, '
    'gap-skipped, cost-skipped, or overlap-ignored. Failed filters do not consume. Next eligible box candidate is '
    'b′≥b+13.',
    'Real same-b long/short conflict is impossible; implement/assert that invariant and allow injection-only testing, '
    'but do not add a reachable conflict branch.',
    'Short close-location is `(H−C)/(H−L) >= 0.70`; H=L gives no signal on either side.',
    'Cost guard uses the real entry price per side: R/entry ≥0.84%. Mirror tests must not expect equal cost ratios '
    'under an affine K−p transform.',
    'Costs are C1’s frozen model: taker fee 5 bps per fill, slippage 2 bps per fill, funding 5e−5 per held 4h '
    'bar, always cost.',
    'Exit ordering per bar: scheduled invalidation/time exit is evaluated after protective execution. Gap stop/target '
    'fills at that open and owns the label; otherwise intrabar stop beats target; otherwise a close-rule decision exits '
    'next open. Invalidation beats time when both decide on the same close.',
    'Long invalidation is C[k] < Hb; short is C[k] > Lb. Equality does not invalidate. First eligible k=b+1.',
)
C2_RULINGS = (
    'Accepted: consumption is per symbol across both sides.',
    'Accepted: a candidate inside the active b..b+12 consumption interval is blocked and does not extend it. Only an '
    'otherwise qualifying, unblocked signal starts a new 13-bar interval.',
    'Accepted: ATR[b] must be finite. With valid positive OHLC and the breakout-body rule, computed ATR[b] is positive; '
    'the later runner will independently refuse invalid OHLC before detection.',
    'Accepted: funding is the C1/`backtest.run` parity convention, charged as cost on closes e..x inclusive and '
    'disclosed in the packet.',
)
FROZEN_DECISION = ('The development decision rule remains the three frozen C1 tests, whole family/combined sides, on '
                   'both eligible-history tiers; otherwise PARK.')

_num, _utc, _ms, _canon = VC._num, VC._utc, VC._ms, VC._canon


class RunnerError(RuntimeError):
    pass


def _r(x):
    return _num(x, ND)


def _err(e) -> RunnerError:
    return RunnerError(str(e).replace('VWAPREV candidate', 'vcb').replace('breadth candidate', 'vcb'))


DIGEST_MODULES = (sys.modules[__name__], VCB, B, VC, VC.BT, VC.ST, VC.PR, VC.G, VC.SP)


def runner_digest() -> dict:
    """sha256 of every module the packet numbers or guards depend on: this runner, the C2 rules, the breadth / VWAPREV
    helpers, backtest (cost constants), strategies (ATR14), pit_run / run_r4 / splits (window, universe, clean tree)."""
    return {os.path.basename(m.__file__): B._sha(os.path.abspath(m.__file__)) for m in DIGEST_MODULES}


# ------------------------------------------------------------------ guards
def check_window(window, plan=None):
    """(lo, hi, warm_lo) ms. VC.check_window (grid, dev window, sealed cutoff, warm-up, calibration) + own holdout check."""
    if not window:
        raise RunnerError('vcb: an explicit --window START/END is required')
    try:
        lo, hi, warm_lo = VC.check_window(window, plan)
    except VC.RunnerError as e:
        raise _err(e) from None
    if hi > SP.parse_utc(HOLDOUT_START):
        raise RunnerError(f'vcb: END {_utc(hi)} is inside the sealed holdout (starts {HOLDOUT_START}); refused')
    return lo, hi, warm_lo


def _syms(tier, symbols):
    try:
        static = VC.frozen_universe()
        return static, VC.pick_symbols(static, tier, symbols)
    except VC.RunnerError as e:
        raise _err(e) from None


def _late_list(exclude_late, syms) -> list:
    out = []
    for s in exclude_late or ():
        s = s.strip().upper()
        if s and s not in out:
            out.append(s)
    stray = [s for s in out if s not in syms]
    if stray:
        raise RunnerError(f'vcb: --exclude-late {stray} not in the requested symbols; refused')
    return out


def late_listing(df, warm_lo: int, hi: int):
    """First cached bar before END when the symbol is provably late-listed (on the 4h grid, strictly after the warm-up
    start, before END); else None. Reads open times only; nothing at / after END is used."""
    if df is None:
        return None
    t = df.t.to_numpy(np.int64)
    t = t[t < hi]
    if not len(t):
        return None
    first = int(t.min())
    if first <= warm_lo or (first - warm_lo) % TF_MS:
        return None
    return first


# ------------------------------------------------------------------ signals inside the window only
@contextlib.contextmanager
def window_signals(lo_i: int, excluded: list):
    """Run VCB.run_symbol with its detector restricted to candidates whose breakout bar b >= lo_i (the first in-window
    bar), so pre-window candidates neither consume a box nor hold the slot. The rule code is untouched: VCB.detect is
    wrapped for the duration and restored; dropped candidates go to `excluded`."""
    orig = VCB.detect

    def detect(df, side, atr=None):
        gs = orig(df, side, atr=atr)
        excluded.extend(g for g in gs if g['b'] < lo_i)
        return [g for g in gs if g['b'] >= lo_i]

    VCB.detect = detect
    try:
        yield
    finally:
        VCB.detect = orig


# ------------------------------------------------------------------ per-trade path measures
def excursions(df, t) -> dict:
    """MFE / MAE / giveback in R from real prices on the path entry -> exit: the full ranges of bars [entry_i, exit_i),
    plus the entry fill price, the exit bar's open and the exit price (the exit bar's range after the exit is never
    counted; for an intrabar stop / target only the exit level of that bar counts). giveback_gross_r = MFE - gross R,
    giveback_net_r = MFE - net R.
    DIAGNOSTIC: on an intrabar stop / target exit the exit bar's high / low before the exit are ignored (their order
    vs the exit is unknown), so MAE / MFE can understate the exit bar's excursion."""
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
def group_stats(rows, skips, opens, blocked, quarters, summary) -> dict:
    """rows: closed trade rows of one group; summary: the matching VCB.summarize block (same trades)."""
    n = len(rows)
    net = [x['R_net'] for x in rows]
    cost = summary['fee_R'] + summary['slip_R'] + summary['fund_R']
    q = {k: 0.0 for k in quarters}
    for x in rows:
        q[x['quarter']] = q.get(x['quarter'], 0.0) + x['R_net']
    order = sorted(rows, key=lambda x: (x['t_out'], x['t_in'], x['symbol'], x['side']))
    cum = np.cumsum([0.0] + [x['R_net'] for x in order])
    hold = [x['hold_bars'] for x in rows]
    return dict(
        _raw=dict(pf=summary['pf'], gross_R=summary['gross_R'], cost_R=cost),    # unrounded; popped before serializing
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
        gap_exit_by_label={k: sum(1 for x in rows if x['label'] == k and x['gap_exit']) for k in LABELS},
        skips_by_symbol={sym: {k: sum(1 for s in skips if s['symbol'] == sym and s['skip'] == k) for k in SKIPS}
                         for sym in sorted({s['symbol'] for s in skips})},
        skips={k: sum(1 for s in skips if s['skip'] == k) for k in SKIPS},
        blocked=len(blocked), open_end=len(opens))


def concentration(rows, symbols) -> dict:
    n, net = len(rows), sum(x['R_net'] for x in rows)
    out = {}
    for s in symbols:
        sel = [x for x in rows if x['symbol'] == s]
        v = sum(x['R_net'] for x in sel)
        out[s] = dict(n=len(sel), trade_share=_r(len(sel) / n) if n else None, net_R=_r(v),
                      net_R_share=_r(v / net) if abs(net) >= NET_EPS else None)
    return out


def decision(c: dict, quarters) -> dict:
    """CANDIDATE (per tier) only if combined PF > 1.0 AND combined own quarterly net R > 0 in >= 2/3 of the window
    quarters AND gross R per trade >= 3 x cost R per trade; otherwise PARK. Final: CANDIDATE on both eligible-history
    tiers."""
    # every threshold is evaluated on UNROUNDED values (c['_raw'] from group_stats, else c itself); only the serialized
    # fields below are rounded. positive_quarters is already counted on the raw quarterly sums.
    raw = c.get('_raw', c)
    n, nq, pf, gross, cost = c['n'], len(quarters), raw['pf'], raw['gross_R'], raw['cost_R']
    pf_ok = n > 0 and ((pf is not None and pf > 1.0) or (pf is None and c['losses'] == 0 and c['wins'] > 0))
    q_ok = nq > 0 and 3 * c['positive_quarters'] >= 2 * nq
    cost_ok = n > 0 and gross >= 3 * cost
    return dict(
        rule='CANDIDATE only if combined PF > 1.0 AND combined own quarterly net R > 0 in >= 2/3 of the window quarters '
             'AND gross R per trade >= 3 x cost R per trade; otherwise PARK (frozen C1 rule, whole family)',
        pf=_r(pf), pf_ok=bool(pf_ok), positive_quarters=c['positive_quarters'], quarters=nq, quarters_ok=bool(q_ok),
        gross_R_per_trade=_r(gross / n) if n else None, cost_R_per_trade=_r(cost / n) if n else None,
        cost_ok=bool(cost_ok), evaluated_on='unrounded values (serialized fields rounded to 9 dp)',
        tier_verdict='CANDIDATE' if (pf_ok and q_ok and cost_ok) else 'PARK', final_requires=FINAL_REQUIRES)


# ------------------------------------------------------------------ OHLC sanity
def ohlc_problems(d) -> dict:
    """Counts of insane bars in a slice already bounded to [warm_lo, END): non-finite / non-positive o/h/l/c,
    h < l, h < max(o, c), l > min(o, c); equality is valid. Empty dict = sane."""
    o, h, lw, c = (d[k].to_numpy(float) for k in ('o', 'h', 'l', 'c'))
    with np.errstate(invalid='ignore'):
        bad_val = ~(np.isfinite(o) & np.isfinite(h) & np.isfinite(lw) & np.isfinite(c)) | (o <= 0) | (h <= 0) | \
            (lw <= 0) | (c <= 0)
        inv = ~bad_val & (h < lw)
        hi_bad = ~bad_val & (h < np.maximum(o, c))
        lo_bad = ~bad_val & (lw > np.minimum(o, c))
    out = {k: int(v.sum()) for k, v in (('non_finite_or_non_positive', bad_val), ('h_below_l', inv),
                                        ('h_below_max_oc', hi_bad), ('l_above_min_oc', lo_bad)) if v.any()}
    if out:
        t = _ms(d.t)
        out['first_bad'] = _utc(int(t[np.flatnonzero(bad_val | inv | hi_bad | lo_bad)[0]]))
    return out


# ------------------------------------------------------------------ pre-registration (fixed before any data is read)
def prereg() -> dict:
    """Accounting and reporting commitments, built from code constants only (no cache input)."""
    return {
        'source': 'Codex 6230089008 (C2 runner contract); C2 freeze 6229577171; rulings 6229837790',
        'written_before_data': True,
        'funding': 'charged as a cost on both sides (FUND_PER_BAR x close of every managed bar entry..exit inclusive, '
                   'including the exit bar x on open fills), never credited; computed by the C2 module only',
        'pf_and_quarters_use': 'R_net (gross R - slip R - fee R - funding R)',
        'frozen_c2_semantics': list(FROZEN_C2_SEMANTICS),
        'frozen_c2_semantics_source': 'Codex 6229577171 (C2 CONTRACT FREEZE, Frozen choices; markdown emphasis removed)',
        'c2_rulings': list(C2_RULINGS),
        'c2_rulings_source': 'Codex 6229837790 (implementer resolutions 1-4; markdown emphasis removed)',
        'frozen_decision': FROZEN_DECISION,
        'decision_rule': decision.__doc__,
        'final_requires': FINAL_REQUIRES,
        'exclusion_rule': EXCLUSION_RULE,
        'reported_counts': ['gap_exit_by_label', 'lt_minus1_by_label',
                            'skips_by_symbol (cost_guard / gap_stop / overlap / no_next_bar)',
                            'skips_by_reason (cost_guard / gap_stop / overlap / no_next_bar)', 'blocked', 'open_end',
                            'giveback_gross_r / giveback_net_r (per trade and mean)'],
        'rule_text': VCB.__doc__,
        'rule_text_sha256': hashlib.sha256(VCB.__doc__.encode('utf-8')).hexdigest(),
    }


# ------------------------------------------------------------------ coverage pre-check (no computation)
def check_coverage(*, cache: str, window, tier=None, symbols=None, plan=None) -> dict:
    """First / last cached bar before END per symbol vs the required warm-up start. Nothing past END is reported; no
    indicator, signal or trade is computed and no packet is written."""
    lo, hi, warm_lo = check_window(window, plan)
    _, syms = _syms(tier, symbols)
    out = {}
    for s in syms:
        try:
            df, rec = B.read_cache(cache, s, TF)
        except B.RunnerError as e:
            raise _err(e) from None
        if df is None:
            out[s] = dict(missing_file=True, first=None, last=None, covers_warmup=False, reaches_end=False, ok=False,
                          late_listed=False)
            continue
        t = df.t.to_numpy(np.int64)
        t = np.sort(t[t < hi])                           # nothing at / after END is ever reported
        first, last = (int(t[0]), int(t[-1])) if len(t) else (None, None)
        r = dict(missing_file=False, first=_utc(first) if first is not None else None,
                 last=_utc(last) if last is not None else None,
                 covers_warmup=first is not None and first <= warm_lo,
                 reaches_end=last is not None and last == hi - TF_MS,
                 late_listed=late_listing(df, warm_lo, hi) is not None)
        r['ok'] = r['covers_warmup'] and r['reaches_end']
        out[s] = r
    return {'window': {'start': _utc(lo), 'end': _utc(hi), 'end_exclusive': True},
            'required_warmup_start': _utc(warm_lo), 'symbols': out,
            'late_or_short': sorted(s for s, v in out.items() if not v['ok']),
            'late_listed': sorted(s for s, v in out.items() if v['late_listed'])}


# ------------------------------------------------------------------ the run
def run(*, cache: str, window, out: str, tier=None, symbols=None, exclude_late=None, repo: str = VC.ROOT,
        plan=None) -> dict:
    lo, hi, warm_lo = check_window(window, plan)
    pre = prereg()                                       # fixed before a single cache byte is read
    static, syms = _syms(tier, symbols)
    late = _late_list(exclude_late, syms)
    if os.path.exists(out):
        raise RunnerError(f'vcb: {out} exists; a packet is never overwritten')
    try:
        commit = VC.clean_commit(repo)                   # before a single cache byte is read
    except VC.RunnerError as e:
        raise _err(e) from None
    frames, files, comp, bad, excluded_u = {}, {}, {}, [], []
    for s in syms:
        try:
            df, rec = B.read_cache(cache, s, TF)
        except B.RunnerError as e:
            raise _err(e) from None
        files[s] = rec
        if s in late:
            first = late_listing(df, warm_lo, hi)
            if first is None:
                raise RunnerError(f'vcb: --exclude-late {s} is not provably late-listed (needs a first bar before END '
                                  f'on the 4h grid after the warm-up start {_utc(warm_lo)}); refused')
            dl = VC.slice_frame(df, first, hi)           # integrity of its listed history only (no indicator)
            cl = B.completeness(dl, first, hi)
            if not cl['complete'] or not np.array_equal(_ms(dl.t), np.arange(first, hi, TF_MS, dtype=np.int64)):
                raise RunnerError(f'vcb: --exclude-late {s} is late-listed but its history [{_utc(first)}, {_utc(hi)}) '
                                  f'is INCOMPLETE ({cl}); the flag does not excuse gaps; refused')
            pl = ohlc_problems(dl)
            if pl:
                raise RunnerError(f'vcb: --exclude-late {s} is late-listed but OHLC sanity failed ({pl}); refused')
            excluded_u.append(dict(symbol=s, reason=LATE_REASON, first_bar=_utc(first),
                                   required_warmup_start=_utc(warm_lo)))
            continue                                     # nothing of an excluded symbol is computed
        d = VC.slice_frame(df, warm_lo, hi) if df is not None else None
        comp[s] = B.completeness(d, warm_lo, hi)
        if not comp[s]['complete']:
            bad.append(s)
        frames[s] = d
    if bad:
        raise RunnerError(f'vcb: INCOMPLETE cache for {bad} over [{_utc(warm_lo)}, {_utc(hi)}); refused (no fill)')
    run_syms = [s for s in syms if s not in late]
    if not run_syms:
        raise RunnerError('vcb: every requested symbol is excluded; nothing to run')
    grid = np.arange(warm_lo, hi, TF_MS, dtype=np.int64)
    unordered = [s for s in run_syms if not np.array_equal(_ms(frames[s].t), grid)]
    if unordered:
        raise RunnerError(f'vcb: cache rows out of time order for {unordered}; refused')
    insane = {s: p for s in run_syms for p in [ohlc_problems(frames[s])] if p}
    if insane:
        raise RunnerError(f'vcb: OHLC sanity failed for {insane} over [{_utc(warm_lo)}, {_utc(hi)}); refused')
    try:
        VC.assert_bounded(frames, warm_lo, hi)
    except VC.RunnerError as e:
        raise _err(e) from None
    rows, skips, opens, blocked, excluded = [], [], [], [], {'long': 0, 'short': 0}
    for s in run_syms:
        d = frames[s]
        t = _ms(d.t)
        lo_i = int(np.searchsorted(t, lo))
        if lo_i != WARMUP_BARS:
            raise RunnerError(f'vcb: {s} slice has {lo_i} warm-up bars, expected {WARMUP_BARS}; aborted')
        ex = []
        with window_signals(lo_i, ex):
            res = VCB.run_symbol(d)
        for g in ex:
            excluded[g['side']] += 1
        if any(g['b'] < lo_i for g in res['signals'] + res['blocked'] + res['skips']):
            raise RunnerError(f'vcb: {s} used a pre-window candidate; aborted')
        for k in res['blocked']:
            blocked.append(dict(symbol=s, side=k['side'], n=int(k['b']), t_signal=_utc(t[k['b']]),
                                consumed_by=_utc(t[k['consumed_by']])))
        for k in res['skips']:
            skips.append(dict(symbol=s, side=k['side'], n=int(k['b']), skip=k['skip'], t_signal=_utc(t[k['b']])))
        for x in res['trades']:
            if x['b'] < lo_i:
                raise RunnerError(f"vcb: {s} traded a pre-window signal (b={x['b']}); aborted")
            base = dict(symbol=s, side=x['side'], t_signal=_utc(t[x['b']]), t_in=_utc(t[x['entry_i']]),
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
    blocked.sort(key=lambda x: (x['t_signal'], x['symbol'], x['side']))
    summ = VCB.summarize([dict(x) for x in rows])        # the module's own totals (R_net / R_gross / costs / PF / R<-1)
    quarters = window_quarters(lo, hi)
    groups = {}
    for g, sel in (('long', 'long'), ('short', 'short'), ('combined', None)):
        rs = [x for x in rows if sel is None or x['side'] == sel]
        pick = lambda lst: [k for k in lst if sel is None or k['side'] == sel]
        groups[g] = group_stats(rs, pick(skips), pick(opens), pick(blocked), quarters,
                                summ['total' if sel is None else sel])
        groups[g]['concentration'] = concentration(rs, run_syms)
    dec = decision(groups['combined'], quarters)                 # on the unrounded values
    for g in groups.values():
        g.pop('_raw')
    rnd = lambda x: {k: (_r(v) if isinstance(v, float) else v) for k, v in x.items()}
    label = None
    if tier is not None:
        label = tier if not excluded_u else f'{tier}-ELIGIBLE-{len(run_syms)}'
    pkt = {
        'format': FORMAT, 'evidence_class': EVIDENCE_CLASS, 'non_promotable': True,
        'purpose': 'C2 development run (development window only; not evidence for promotion)',
        'candidate': VCB.CANDIDATE, 'family': VCB.FAMILY, 'label': VCB.DEV_LABEL,
        'commit': commit, 'tree': 'clean', 'runner_sha256': runner_digest(),
        'window': {'start': _utc(lo), 'end': _utc(hi), 'end_exclusive': True, 'bars': int((hi - lo) // TF_MS), 'tf': TF,
                   'guard': 'pit_run.rex_window', 'holdout_start': HOLDOUT_START, 'quarters': quarters},
        'warmup': {'bars': WARMUP_BARS, 'start': _utc(warm_lo), 'signals_excluded': excluded,
                   'rule': 'warm-up bars feed ATR14 / the 120-value ATR history only; candidates with breakout bar b '
                           'before START are dropped before box consumption and the slot logic (run starts flat and '
                           'unconsumed at START)'},
        'universe': {'digest': static['digest'], 'id': static.get('universe_id'), 'tier': tier, 'tier_label': label,
                     'requested': syms, 'run': run_syms, 'excluded': excluded_u, 'exclusion_rule': EXCLUSION_RULE},
        'cache': {'format': f'app._get_candles CSV <SYMBOL>_{TF}.csv: t(ms),o,h,l,c,v,qv', 'files': files,
                  'completeness': comp, 'incomplete_policy': 'refuse the run'},
        'rules': {'box': VCB.BOX, 'atr_hist': VCB.ATR_HIST, 'ratio_max': str(VCB.RATIO_MAX),
                  'width_atr': str(VCB.WIDTH_ATR), 'buf_atr': str(VCB.BUF_ATR), 'body_atr': str(VCB.BODY_ATR),
                  'cloc_min': str(VCB.CLOC_MIN), 'stop_atr': str(VCB.STOP_ATR), 'cost_guard': str(VCB.COST_GUARD),
                  'target_r': VCB.TARGET_R, 'cap_bars': VCB.CAP_BARS, 'consume': VCB.CONSUME,
                  'slot': 'one trade per symbol, both sides share it; overlap ignored (consumes); box consumption per '
                          'symbol across both sides'},
        'costs': {'fee_taker': float(VCB.FEE), 'slip': float(VCB.SLIP), 'fund_per_bar': float(VCB.FUND_PER_BAR),
                  'source': 'vol_compression_breakout.simulate_trade (no recompute in the runner)'},
        'open_at_end': {'convention': 'backtest.run / run_breadth_candidate: a position still open after the last '
                                      'in-window bar is not a trade (no forced close); excluded from every statistic',
                        'count': len(opens), 'trades': [rnd(o) for o in opens]},
        'definitions': {
            't_in': 'open time of the entry bar (market fill at its open)',
            't_out': 'open time of the exit bar (open fill, or an intrabar stop / target inside that bar)',
            'quarter': 'calendar quarter (UTC) of t_in', 'net_R': 'gross R - slip R - fee R - funding R',
            'mfe_r': 'best favourable excursion on the path entry -> exit in R (bars [entry, exit) full range + entry, '
                     'exit-bar open and exit price; nothing after the exit)',
            'mae_r': 'worst adverse excursion on the same path, in R', 'giveback_gross_r': 'mfe_r - gross R',
            'giveback_net_r': 'mfe_r - net R',
            'max_dd_R': 'max drawdown of cumulative net R in exit order',
            'blocked': 'qualifying candidates inside an active 13-bar consumption interval (consume nothing)',
            'concentration': 'per symbol: share of the group trades and of the group net R (None when net R is ~0)'},
        'stats': groups,
        'prereg': pre,
        'decision': dec,
        'skips_by_reason': {k: sum(1 for x in skips if x['skip'] == k) for k in SKIPS},
        'skips': skips,
        'blocked': blocked,
        'trades': [rnd(x) for x in rows],
    }
    pkt['packet_sha256'] = hashlib.sha256(_canon(pkt)).hexdigest()
    os.makedirs(os.path.dirname(os.path.abspath(out)), exist_ok=True)
    fd, tmp = tempfile.mkstemp(prefix=os.path.basename(out) + '.', suffix='.tmp',
                               dir=os.path.dirname(os.path.abspath(out)))
    try:
        with os.fdopen(fd, 'wb') as f:
            f.write(json.dumps(pkt, sort_keys=True, indent=1, allow_nan=False).encode())
            f.flush()
            os.fsync(f.fileno())                         # the temp is complete on disk before the final name exists
        try:
            os.link(tmp, out)                            # exclusive: fails if --out appeared during the run
        except FileExistsError:
            raise RunnerError(f'vcb: {out} was created during the run; a packet is never overwritten') from None
    finally:
        os.remove(tmp)
    return pkt


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split('\n')[0])
    ap.add_argument('--cache', required=True, help='the 4h app cache dir')
    ap.add_argument('--window', required=True, help='START/END, UTC, 4h grid, end-exclusive (no default)')
    g = ap.add_mutually_exclusive_group(required=True)
    g.add_argument('--tier', choices=('CORE-10', 'CORE-20'))
    g.add_argument('--symbols')
    ap.add_argument('--exclude-late', default=None,
                    help='comma list of requested symbols to exclude as late-listed (each verified from the cache)')
    ap.add_argument('--out', default=None, help='packet path (required unless --check-coverage)')
    ap.add_argument('--check-coverage', action='store_true',
                    help='only report first / last bar per symbol vs the warm-up start; computes nothing else')
    a = ap.parse_args(argv)
    syms = a.symbols.split(',') if a.symbols else None
    if a.check_coverage:
        try:
            cov = check_coverage(cache=a.cache, window=a.window, tier=a.tier, symbols=syms)
        except RunnerError as e:
            print(f'REFUSED: {e}', file=sys.stderr)
            return 2
        print(json.dumps(cov, sort_keys=True, indent=1))
        return 0
    if not a.out:
        ap.error('--out is required unless --check-coverage')
    try:
        pkt = run(cache=a.cache, window=a.window, out=a.out, tier=a.tier, symbols=syms,
                  exclude_late=a.exclude_late.split(',') if a.exclude_late else None)
    except RunnerError as e:
        print(f'REFUSED: {e}', file=sys.stderr)
        return 2
    c = pkt['stats']['combined']
    print(f"{VCB.CANDIDATE}: {c['n']} trades, net R {c['net_R']}, PF {c['pf']}, {pkt['decision']['tier_verdict']}, "
          f"run {len(pkt['universe']['run'])} excluded {len(pkt['universe']['excluded'])}, "
          f"packet {pkt['packet_sha256'][:12]} -> {a.out}")
    return 0


if __name__ == '__main__':
    sys.exit(main())
