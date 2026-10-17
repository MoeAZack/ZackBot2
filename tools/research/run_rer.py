"""C4 dev-run CLI: range_extreme_reversion.v0 over a 4h app cache, one tier or symbol list, both sides - development
evidence only.

A port of the accepted C3 runner (run_tpc.py @ 676cfd1); only the strategy-specific parts change (rule module, prereg
strings, rules / skips block, cost note) plus the two C4 data hand-off rules of Codex 6233040303:
  - the fixture-only indicator injection hooks are dropped: any `e50` / `atr` column is removed from every frame before
    the C4 module sees it (module_frame), so EMA50 / ATR14 are always computed from the bars.
  - invalid data fails closed in the runner, before the module: a non-finite or non-positive price, h < l, or a body
    outside the range REFUSES the run (OHLC sanity, below); the module's own 'prices must be > 0' error is never the
    refusal path.
The same guards, re-stated for the C4 module's output:
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
    packet_sha256 over the canonical packet; runner_sha256 of this runner, the C4 rule module, the imported runner
    helpers, backtest.py, strategies.py and the pit_run / run_r4 / splits guard modules.
  - raw open times (Codex 6230537709): before any conversion, truncation or slicing, every row of every requested
    symbol's file (rows after END and late-excluded symbols included) is read as text; a non-numeric, NaN / inf,
    non-integer-millisecond, outside [0, 2^53) or off-4h-grid open time REFUSES the run (never dropped, never truncated onto the grid).
  - manifest binding (Codex 6231454794): --manifest is REQUIRED for a --tier run and optional for --symbols.
    Its bytes are hashed and parsed once; every requested symbol's cache file (late-excluded included) must equal its
    entry (sha256 and rows always; file name and first / last open time when the entry carries them). A mismatch, a
    missing entry or an unreadable manifest refuses the run. The packet records path, sha256, the optional
    --manifest-commit (recorded only) and the per-file binding.
  - a --symbols run is labelled tier_label SYMBOLS-<n run> with non_decision true and decision.tier_verdict
    NON_DECISION (the computed verdict is kept as diagnostic_verdict); a --tier run is decision-bearing (non_decision
    false) and labelled with its eligible count when symbols are excluded.
  - --check-coverage: reports only the first / last cached bar per symbol before END against the required warm-up
    start (nothing past END, no indicator, no signal); no packet.
  - cache: <cache>/<SYMBOL>_4h.csv (app._get_candles format) via B.read_cache, read and hashed whole, then sliced on the
    raw rows to [warm-up start, END) before any indicator, signal, sanity check or statistic (VC.slice_frame +
    VC.assert_bounded); completeness on the 4h grid (B.completeness) plus an exact-grid order check.
  - OHLC sanity before any indicator and before the C4 module: any bar in [warm-up start, END) with a non-finite or
    non-positive o/h/l/c, h < l, h < max(o, c) or l > min(o, c) (equality valid) REFUSES the whole run. Bars at / after
    END are never checked.
  - packet write: written and fsynced in a unique temp file in the --out directory, then linked to --out exclusively
    (os.link fails if --out exists); the temp file is always removed.
  - warm-up: the 220 bars before START feed EMA50 / ATR14 only. Candidates whose signal bar b is before START
    are removed BEFORE cooldown consumption and the slot logic (the run starts flat and unconsumed at START, like
    backtest.run(t0=START)); they are counted in warmup.signals_excluded and never traded, skipped, blocked or reported
    as overlap. The C4 module's own earliest b (WARM = 220) equals the slice index of START, so on a runner slice this
    count is 0 by construction; the wrapper stays as the independent guard.
  - trades still open at END: labelled 'open_end' by the module (R_net None); excluded from every statistic, counted in
    open_at_end and listed there with its entry. A close-rule exit decided on the last bar (fill at open[END]) is
    therefore also open_end. A signal on the last in-window bar is a 'no_next_bar' skip.
  - the rules and every cost are range_extreme_reversion.run_symbol / simulate_trade / summarize, unchanged (cost
    parity: no cost is recomputed here).

Added per side and combined: wins, avg R, max DD R (cumulative net R in exit order), per-quarter net R by ENTRY time
(own sign, every calendar quarter overlapping the window, empty quarters 0), mean / median hold bars, MFE / MAE /
giveback gross / net, exits, gap exits by label, skips by reason, cooldown-blocked candidates, symbol concentration.

Decision (frozen C1 three-part rule, whole family / combined sides, per tier): CANDIDATE only if combined PF > 1.0 AND
combined positive quarters >= 2/3 of the window's quarters AND gross R per trade >= 3 x cost R per trade; else PARK.
Final: CANDIDATE on both eligible-history tiers.

Usage (PAPER research only, no network, no app settings or credentials):

  py -3.14 tools/research/run_rer.py --cache <DATA>/candles --window 2024-10-14T00:00:00Z/2026-09-28T00:00:00Z \
     --tier CORE-10 --exclude-late HYPEUSDT --out <packet.json>
"""
import argparse
import contextlib
import decimal
import hashlib
import io
import json
import os
import sys
import tempfile
from datetime import datetime, timezone
from fractions import Fraction

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(os.path.dirname(HERE))
for _p in (ROOT, HERE):
    if _p not in sys.path:
        sys.path.insert(0, _p)

import numpy as np                                                                          # noqa: E402
import pandas as pd                                                                         # noqa: E402

import range_extreme_reversion as RX                                                       # noqa: E402
import run_breadth_candidate as B                                                           # noqa: E402
import run_vwaprev_candidate as VC                                                          # noqa: E402
import splits as SP                                                                         # noqa: E402

FORMAT = 'zb-rer-dev/1'
EVIDENCE_CLASS = 'candidate-runtime'
TF, TF_MS = VC.TF, VC.TF_MS
HOLDOUT_START = VC.SEALED_CUTOFF            # END may equal it (end-exclusive), never pass it
WARMUP_BARS = VC.WARMUP_BARS
assert RX.TF == TF and WARMUP_BARS >= RX.WARM
SKIPS = ('gap_stop', 'quality', 'room', 'cost_guard', 'no_next_bar', 'overlap')
LABELS = ('stop', 'target', 'invalidation', 'time')
ND = 9                                      # float rounding in the packet
FINAL_REQUIRES = 'CANDIDATE on both eligible-history CORE-10 and CORE-20 tiers (combined sides)'
NET_EPS = 1e-9                              # |group net R| below this: net_R_share is null
LATE_REASON = 'late_listed'
EXCLUSION_RULE = ('Codex 6229469658 section 3: a symbol enters a primary full-window tier only if real bars cover the '
                  'required warm-up start through END with zero gaps and valid OHLC; late-listed symbols are excluded '
                  'explicitly (--exclude-late, verified late-listed from the cache); never forward-fill, fake flat bars, '
                  'shorten the primary window, or silently rename an incomplete tier as complete')
# Codex 6232261614 C4 CONTRACT FREEZE (Frozen parameters; Short mirror pinned explicitly; Lifecycle and priority; the
# decision bullet of Acceptance) and Build draft 6232236186 (Bars and indicators; Range regime; Long trigger; Trade;
# Consumption and ties; Worst-case cost; Decision): verbatim, list markers and markdown bold (**) removed, and the
# section-title words 'Bars and indicators.' / 'Decision.' dropped from the front of their paragraphs (as C3 did).
# Left out as test-method / scope / non-rule text (as C3 did): freeze 'Mirror tests use a positive affine reflection
# ...' and Acceptance 'Synthetic oracle first ...' / 'Scope: ...'; draft 'How it differs from range_scalp ...',
# 'Before any historical run: synthetic planted-range oracle tests ...' and 'Choices for Codex to freeze or change ...'
# (superseded by the freeze), plus the comment headers, PATH / ROADMAP / DEFERRED / LANES lines.
FROZEN_C4_PARAMETERS = (
    '4h closed bars; earliest b=220; EMA50 uses the existing adjust=False recurrence seeded from close[0]; '
    'ATR14 is the accepted Wilder implementation. Every referenced value is finite and ATR >0.',
    'Box is exactly the inclusive bars b-30..b-1. RH=max high, RL=min low, W=RH-RL, M=(RH+RL)/2. Bar b is '
    'excluded.',
    'Regime: 4.0*ATR[b-1] <= W <= 12.0*ATR[b-1]; abs(EMA50[b-1]-EMA50[b-13]) <= 0.5*ATR[b-1]; ER <=0.30, '
    'where ER numerator is abs(C[b-1]-C[b-31]) and denominator is the sum of the 30 absolute close changes '
    'i=b-30..b-1. Zero denominator fails.',
    'Long trigger/entry/stop/target/room/R cap/cost/invalidation/time/cooldown are exactly as drafted: probe '
    '0.10..1.50 ATR, close location >=0.60, next open, stop low[b]-0.25*ATR[b], target frozen M, room >=1R, '
    "R<=2.5*ATR[b], R/entry>=1%, invalidation close < low[b], time close b+24/open b+25, cooldown b' >= b+7.",
    'All threshold decisions use exact fractions of the float inputs and decimal constants; written '
    'equalities pass and strict comparisons remain strict.',
)
FROZEN_C4_SHORT_MIRROR = (
    'high[b] >= RH+0.10*ATR[b-1] and <= RH+1.50*ATR[b-1]; C[b] < RH; close location <=0.40; C[b] > M.',
    'stop=high[b]+0.25*ATR[b]; entry=open[b+1]; skip if entry>=stop; R=stop-entry; target=M; room requires '
    'entry-M >=1R; invalidation is C>high[b] strictly. Other rules mirror the long side.',
)
FROZEN_C4_LIFECYCLE = (
    'A signal is a candidate passing every regime and trigger rule. It consumes the cross-side cooldown '
    'whether taken, no-next-bar, entry/stop gap-skipped, quality/room/cost-skipped or overlap-ignored. A '
    'candidate failing regime/trigger does not consume; a cooldown-blocked candidate does not extend the '
    'cooldown.',
    'Trade-skip order is entry/stop gap -> R cap quality -> target room -> cost. Target and stop are frozen '
    'at signal time. A gap entry beyond the mean therefore fails room.',
    'One combined-side slot. A trade exiting at open x still blocks a signal at close x-1. open_end owns the '
    'slot.',
    'Exit priority and cost/funding arithmetic are the accepted C2/C3 convention: gap stop/target owns the '
    'label; otherwise pending close-rule fill precedes intrabar checks; intrabar stop beats target; '
    'invalidation beats time when decided together. Time exit funding uses the accepted inclusive e..x '
    'convention (25 funding bars here).',
    'Same-bar L/S conflict is asserted unreachable for a valid nonzero-range trigger and must be '
    'injection-tested.',
)
C4_DRAFT_RULES = (
    '4h closed bars; signal bar `b`.',
    'EMA50 of close (the existing implementation, as in C3); ATR14 (Wilder).',
    'The earliest `b` is 220. Every referenced value must be finite, and ATR > 0.',
    'Range regime, declared from closed bars `b−30..b−1` only. Bar `b` is never part of the box.',
    'Box: RH = max(high[b−30..b−1]), RL = min(low[b−30..b−1]), W = RH − RL, M = (RH + RL)/2.',
    'Width: 4.0·ATR[b−1] ≤ W ≤ 12.0·ATR[b−1]. The range must be wide enough for a medium-horizon R and '
    'target, and not so wide that it is really a trend leg.',
    'Flat mean: |EMA50[b−1] − EMA50[b−13]| ≤ 0.5·ATR[b−1].',
    'Low efficiency: ER = |C[b−1] − C[b−31]| / Σ_{i=b−30..b−1} |C[i] − C[i−1]| ≤ 0.30. If the denominator is '
    '0, the regime fails.',
    'Long trigger at the close of `b` (shorts are the exact mirror: RH ↔ RL, high ↔ low, every comparison '
    'inverted).',
    'Excursion: low[b] ≤ RL − 0.10·ATR[b−1]. This is a real probe below the range.',
    'Not a breakdown: low[b] ≥ RL − 1.50·ATR[b−1].',
    'Rejection / re-entry: C[b] > RL (strict), and close location (C − low)/(high − low) ≥ 0.60. If high = '
    'low, the trigger fails.',
    'Room to the mean: C[b] < M (strict).',
    'Entry at open[b+1].',
    'Stop = low[b] − 0.25·ATR[b]. Skip if entry ≤ stop.',
    'R = entry − stop. Quality guard: skip if R > 2.5·ATR[b].',
    'Target = M, frozen at signal time. Skip if (M − entry) < 1.0·R, so the reward to the mean must be at '
    'least 1R.',
    'Cost guard: skip unless R / entry ≥ 1.0%.',
    'Skip order: entry ≤ stop → R cap (`quality`) → target room (`room`) → cost guard.',
    'Regime-break invalidation: C < low[b] (strict) at the close of any bar from b+1 on, i.e. acceptance '
    'below the excursion low, exits at the next open. Equality does not invalidate.',
    'Time cap: decided at the close of b+24, exit at open b+25 (4 days).',
    'Exit priority, slot rules, costs and funding: identical to the accepted C2/C3 convention. A gap through '
    'the stop or target at the open owns the label. Otherwise the intrabar stop beats the target, and '
    'invalidation beats the time cap. A signal at close x−1 is ignored when the trade exits at open x. An '
    'open_end trade owns the slot.',
    'One slot per symbol, both sides combined.',
    'A qualifying signal consumes its setup whether it is taken, skipped (no-next-bar, gap, quality, room or '
    'cost) or overlap-ignored. Then the next signal on that symbol, either side, needs `b′ ≥ b + 7`. A '
    'candidate that fails a gate does not consume, and a blocked candidate does not extend the cooldown.',
    'The same-bar long/short conflict is unreachable, since a bar cannot close both above RL with location ≥ '
    '0.6 and below RH with location ≤ 0.4. Assert it and test it by injection.',
    'RH and RL ties do not matter, because only the values are used.',
    'Worst-case cost at the guard minimum (R/E = 1.0%): fees and slippage 0.14R plus funding over 25 closes × '
    '5e-5 / 1% = 0.125R, about 0.265R.',
)
FROZEN_DECISION = (
    'The frozen C1 rule, whole family, combined sides, with CORE-10 and CORE-20 both needed for a CANDIDATE; '
    'otherwise PARK.')
FROZEN_C4_DECISION = (
    'Later whole-family decision remains combined PF>1, at least 2/3 positive UTC quarters and gross R/trade '
    '>=3x cost on both authorized tiers; otherwise PARK. No tuning or side split.')
# Worst case at the cost-guard minimum (R / entry = 1.0%) held to the time exit (entry b+1 .. exit open b+25: 25
# funding closes e..x), every close and the exit at the entry price: fees 2 x 5 bps = 0.10R, slippage 2 x 2 bps = 0.04R,
# funding 25 x 5e-5 = 0.125R; exactly 0.265R (= 0.00265 / 0.01, decimal constants).
COST_NOTE = ('worst case at the cost-guard minimum (R / entry = 1.0%) held to the time exit (25 funding closes e..x, '
             'entry b+1 to exit open b+25): fees 2 x 5 bps + slippage 2 x 2 bps = 0.14R, funding 25 x 5e-5 x close / '
             'entry = 0.125R; total exactly 0.265R per trade with every close and the exit at the entry price (more if '
             'the closes rise during the hold); computed by the C4 module only')

_num, _utc, _ms, _canon = VC._num, VC._utc, VC._ms, VC._canon


class RunnerError(RuntimeError):
    pass


def _r(x):
    return _num(x, ND)


def _err(e) -> RunnerError:
    return RunnerError(str(e).replace('VWAPREV candidate', 'rer').replace('breadth candidate', 'rer'))


DIGEST_MODULES = (sys.modules[__name__], RX, B, VC, VC.BT, VC.ST, VC.PR, VC.G, VC.SP)


def runner_digest() -> dict:
    """sha256 of every module the packet numbers or guards depend on: this runner, the C4 rules, the breadth / VWAPREV
    helpers, backtest (cost constants), strategies (ATR14), pit_run / run_r4 / splits (window, universe, clean tree)."""
    return {os.path.basename(m.__file__): B._sha(os.path.abspath(m.__file__)) for m in DIGEST_MODULES}


# ------------------------------------------------------------------ guards
def check_window(window, plan=None):
    """(lo, hi, warm_lo) ms. VC.check_window (grid, dev window, sealed cutoff, warm-up, calibration) + own holdout check."""
    if not window:
        raise RunnerError('rer: an explicit --window START/END is required')
    try:
        lo, hi, warm_lo = VC.check_window(window, plan)
    except VC.RunnerError as e:
        raise _err(e) from None
    if hi > SP.parse_utc(HOLDOUT_START):
        raise RunnerError(f'rer: END {_utc(hi)} is inside the sealed holdout (starts {HOLDOUT_START}); refused')
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
        raise RunnerError(f'rer: --exclude-late {stray} not in the requested symbols; refused')
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


# ------------------------------------------------------------------ raw-time integrity (Codex 6230537709)
def raw_time_problems(raw: bytes) -> dict:
    """Every row of the raw cache bytes (the whole file, rows at / after END included), read as TEXT before any float
    conversion, truncation or slicing: an open time that is non-numeric, NaN / inf, not an exact integer millisecond
    value, an integer outside [0, 2^53), or an integer not on the 4h grid is counted. Empty dict = every open time is an exact on-grid integer.
    A file without a `t` column returns {} (the shared reader refuses it)."""
    try:
        col = pd.read_csv(io.BytesIO(raw), dtype=str, keep_default_na=False, na_filter=False)
    except (ValueError, pd.errors.ParserError) as e:
        return {'unparseable_file': type(e).__name__}
    if 't' not in col:
        return {}
    out, first = {}, None
    for i, v in enumerate(col['t'].tolist()):
        try:
            d = decimal.Decimal(str(v).strip())
        except decimal.InvalidOperation:
            k = 'non_numeric'
        else:
            if not d.is_finite():
                k = 'nan_or_inf'
            elif d != d.to_integral_value():
                k = 'non_integer_ms'
            elif not 0 <= int(d) < 2 ** 53:              # pre-epoch, or beyond exact float / datetime range
                k = 'out_of_range'
            elif int(d) % TF_MS:
                k = 'off_grid'
            else:
                continue
        out[k] = out.get(k, 0) + 1
        if first is None:
            first = f'data row {i + 1}: {v!r}'           # 1-based among non-blank data rows (blank lines skipped)
    if out:
        out['first_bad'] = first
    return out


def read_checked(cache: str, s: str):
    """B.read_cache, preceded by raw_time_problems on the same file bytes (refuses before any conversion); the bytes
    checked must be the bytes the shared reader hashed."""
    path = os.path.join(cache, f'{s}_{TF}.csv')
    raw = None
    if os.path.exists(path):
        try:
            with open(path, 'rb') as f:
                raw = f.read()
        except OSError as e:
            raise RunnerError(f'rer: cache file {s}_{TF}.csv unreadable ({type(e).__name__})') from None
        p = raw_time_problems(raw)
        if p:
            raise RunnerError(f'rer: malformed raw open times in {s}_{TF}.csv ({p}); refused')
    try:
        df, rec = B.read_cache(cache, s, TF)
    except B.RunnerError as e:
        raise _err(e) from None
    if raw is None:                                      # absent at check time: the reader must also have found nothing
        if df is not None or rec.get('sha256') is not None or not rec.get('missing_file'):
            raise RunnerError(f'rer: cache file {s}_{TF}.csv appeared after the raw-time check; refused')
    elif rec.get('sha256') != hashlib.sha256(raw).hexdigest():
        raise RunnerError(f'rer: cache file {s}_{TF}.csv changed while being read; refused')
    return df, rec


# ------------------------------------------------------------------ manifest binding (Codex 6231454794)
class DuplicateKeyError(ValueError):
    pass


def _no_dup_keys(pairs):
    """json object_pairs_hook: a key repeated at any level of the manifest refuses it (no last-one-wins)."""
    out = {}
    for k, v in pairs:
        if k in out:
            raise DuplicateKeyError(f'duplicate key {k!r}')
        out[k] = v
    return out


def load_manifest(path: str):
    """(manifest dict, sha256 of the exact bytes parsed). Unreadable / non-JSON / no `files` object refuses."""
    try:
        with open(path, 'rb') as f:
            raw = f.read()
    except OSError as e:
        raise RunnerError(f'rer: manifest {path} unreadable ({type(e).__name__}); refused') from None
    try:
        m = json.loads(raw.decode('utf-8'), object_pairs_hook=_no_dup_keys)
    except (UnicodeDecodeError, ValueError) as e:
        raise RunnerError(f'rer: manifest {path} is not valid JSON ({type(e).__name__}: {e}); refused') from None
    if not isinstance(m, dict) or not isinstance(m.get('files'), dict):
        raise RunnerError(f'rer: manifest {path} has no "files" object; refused')
    return m, hashlib.sha256(raw).hexdigest()


def bind_manifest(man: dict, s: str, df, rec: dict) -> dict:
    """The cache file must equal its manifest entry: sha256 and row count always; file name, first / last open time
    (ISO `first` / `last` or integer `first_ms` / `last_ms`, whole file) when the entry carries them."""
    e = man['files'].get(s)
    if not isinstance(e, dict):
        raise RunnerError(f'rer: manifest has no entry for {s}; refused')
    if rec.get('sha256') is None or rec.get('sha256') != e.get('sha256'):
        raise RunnerError(f"rer: {s} cache sha256 {rec.get('sha256')} != manifest {e.get('sha256')}; refused")
    if type(e.get('rows')) is not int or rec.get('rows') != e['rows']:
        raise RunnerError(f"rer: {s} cache rows {rec.get('rows')} != manifest {e.get('rows')}; refused")
    checked = ['sha256', 'rows']
    if 'file' in e:
        if e['file'] != rec.get('file'):
            raise RunnerError(f"rer: {s} cache file {rec.get('file')} != manifest {e['file']}; refused")
        checked.append('file')
    t = df.t.to_numpy(np.int64) if df is not None and len(df) else None
    got = {'first_ms': None if t is None else int(t.min()), 'last_ms': None if t is None else int(t.max())}
    for k in ('first', 'last'):
        if k + '_ms' in e:
            if type(e[k + '_ms']) is not int or e[k + '_ms'] != got[k + '_ms']:
                raise RunnerError(f'rer: {s} cache {k} open time != manifest {k}_ms {e[k + "_ms"]}; refused')
            checked.append(k + '_ms')
        if k in e:
            if got[k + '_ms'] is None or e[k] != _utc(got[k + '_ms']):
                raise RunnerError(f'rer: {s} cache {k} open time != manifest {k} {e[k]}; refused')
            checked.append(k)
    return dict(result='match', checked=checked, sha256=rec['sha256'], rows=rec['rows'])


# ------------------------------------------------------------------ signals inside the window only
@contextlib.contextmanager
def window_signals(lo_i: int, excluded: list):
    """Run RX.run_symbol with its detector restricted to candidates whose signal bar b >= lo_i (the first in-window
    bar), so pre-window candidates neither consume the cooldown nor hold the slot. The rule code is untouched: RX.detect
    is wrapped for the duration and restored; dropped candidates go to `excluded`."""
    orig = RX.detect

    def detect(df, side, ind=None):
        gs = orig(df, side, ind=ind)
        excluded.extend(g for g in gs if g['b'] < lo_i)
        return [g for g in gs if g['b'] >= lo_i]

    RX.detect = detect
    try:
        yield
    finally:
        RX.detect = orig


# ------------------------------------------------------------------ the frame the C4 module sees (Codex 6233040303)
INJECTION_COLS = ('e50', 'atr')          # the C4 module's fixture-only indicator injection hooks


def module_frame(d):
    """The OHLC frame handed to the C4 module with every fixture-only `e50` / `atr` column dropped, so the module always
    computes EMA50 / ATR14 from the bars (a cache or reader that carried such a column can never inject indicators)."""
    return d.drop(columns=[k for k in INJECTION_COLS if k in d.columns])


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
    """rows: closed trade rows of one group; summary: the matching RX.summarize block (same trades)."""
    n = len(rows)
    net = [x['R_net'] for x in rows]
    cost = summary['fee_R'] + summary['slip_R'] + summary['fund_R']
    q = {k: 0.0 for k in quarters}
    qx = {k: Fraction(0) for k in quarters}             # exact quarterly sums (decision); q is the serialized float
    for x in rows:
        q[x['quarter']] = q.get(x['quarter'], 0.0) + x['R_net']
        qx[x['quarter']] = qx.get(x['quarter'], Fraction(0)) + Fraction(x['R_net'])
    order = sorted(rows, key=lambda x: (x['t_out'], x['t_in'], x['symbol'], x['side']))
    cum = np.cumsum([0.0] + [x['R_net'] for x in order])
    hold = [x['hold_bars'] for x in rows]
    # exact decision inputs: Fraction sums of every trade's own float R components (no float summation error)
    xw = sum((Fraction(x['R_net']) for x in rows if x['R_net'] > 0), Fraction(0))
    xl = -sum((Fraction(x['R_net']) for x in rows if x['R_net'] < 0), Fraction(0))
    xg = sum((Fraction(x['R_gross']) for x in rows), Fraction(0))
    xc = sum((Fraction(x['fee_R']) + Fraction(x['slip_R']) + Fraction(x['fund_R']) for x in rows), Fraction(0))
    return dict(
        _raw=dict(pf=summary['pf'], gross_R=summary['gross_R'], cost_R=cost,      # popped before serializing
                  exact=dict(pf=(xw / xl) if xl > 0 else None, gross_R=xg, cost_R=xc)),
        n=summary['n'], wins=sum(1 for v in net if v > 0), losses=sum(1 for v in net if v < 0),
        net_R=_r(summary['net_R']), gross_R=_r(summary['gross_R']), fee_R=_r(summary['fee_R']),
        slip_R=_r(summary['slip_R']), fund_R=_r(summary['fund_R']), cost_R=_r(cost), pf=_r(summary['pf']),
        avg_R=_r(summary['net_R'] / n) if n else None,
        gross_R_per_trade=_r(summary['gross_R'] / n) if n else None, cost_R_per_trade=_r(cost / n) if n else None,
        max_dd_R=_r(float((np.maximum.accumulate(cum) - cum).max())),
        quarters={k: _r(v) for k, v in sorted(q.items())}, positive_quarters=sum(1 for v in qx.values() if v > 0),
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
    # every threshold is evaluated on EXACT values (c['_raw']['exact'] from group_stats: Fraction sums of the per-trade
    # floats; else the unrounded raw / given values); the serialized fields below are the float sums rounded to 9 dp.
    # positive_quarters is already counted on the exact quarterly sums.
    raw = c.get('_raw', c)
    ex = raw.get('exact', raw)
    n, nq, pf, gross, cost = c['n'], len(quarters), raw['pf'], raw['gross_R'], raw['cost_R']
    xpf, xg, xc = ex['pf'], ex['gross_R'], ex['cost_R']
    pf_ok = n > 0 and ((xpf is not None and xpf > 1) or (xpf is None and c['losses'] == 0 and c['wins'] > 0))
    q_ok = nq > 0 and 3 * c['positive_quarters'] >= 2 * nq
    cost_ok = n > 0 and xg >= 3 * xc
    return dict(
        rule='CANDIDATE only if combined PF > 1.0 AND combined own quarterly net R > 0 in >= 2/3 of the window quarters '
             'AND gross R per trade >= 3 x cost R per trade; otherwise PARK (frozen C1 rule, whole family)',
        pf=_r(pf), pf_ok=bool(pf_ok), positive_quarters=c['positive_quarters'], quarters=nq, quarters_ok=bool(q_ok),
        gross_R_per_trade=_r(gross / n) if n else None, cost_R_per_trade=_r(cost / n) if n else None,
        cost_ok=bool(cost_ok), evaluated_on='exact Fraction sums of per-trade float values',
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
        'source': 'Codex 6232261614 (C4 CONTRACT FREEZE); Build draft 6232236186; Codex 6233040303 (runner hand-off: '
                  'drop e50 / atr, fail closed on invalid data); runner ported from run_tpc.py @ 676cfd1',
        'written_before_data': True,
        'funding': 'charged as a cost on both sides (FUND_PER_BAR x close of every managed bar entry..exit inclusive, '
                   'including the exit bar x on open fills), never credited; computed by the C4 module only',
        'pf_and_quarters_use': 'R_net (gross R - slip R - fee R - funding R)',
        'cost_note': COST_NOTE,
        'frozen_c4_parameters': list(FROZEN_C4_PARAMETERS),
        'frozen_c4_short_mirror': list(FROZEN_C4_SHORT_MIRROR),
        'frozen_c4_lifecycle': list(FROZEN_C4_LIFECYCLE),
        'frozen_c4_decision': FROZEN_C4_DECISION,
        'frozen_c4_source': 'Codex 6232261614 (C4 CONTRACT FREEZE: Frozen parameters; Short mirror pinned explicitly '
                            'without the mirror-test bullet; Lifecycle and priority; the decision bullet of Acceptance; '
                            'list markers and markdown bold removed)',
        'c4_draft_rules': list(C4_DRAFT_RULES),
        'c4_draft_rules_source': 'Build 6232236186 (DRAFT CONTRACT accepted by 6232261614: Bars and indicators; Range '
                                 'regime; Long trigger; Trade; Consumption and ties; Worst-case cost; list markers and '
                                 'markdown bold removed)',
        'frozen_decision': FROZEN_DECISION,
        'frozen_decision_source': 'Build 6232236186 (Decision; markdown bold removed)',
        'decision_rule': decision.__doc__,
        'final_requires': FINAL_REQUIRES,
        'exclusion_rule': EXCLUSION_RULE,
        'module_input': 'every frame is OHLC-sane before the C4 module runs, and its e50 / atr columns are dropped '
                        '(Codex 6233040303)',
        'reported_counts': ['gap_exit_by_label', 'lt_minus1_by_label',
                            'skips_by_symbol (' + ' / '.join(SKIPS) + ')',
                            'skips_by_reason (' + ' / '.join(SKIPS) + ')', 'blocked',
                            'open_end',
                            'giveback_gross_r / giveback_net_r (per trade and mean)'],
        'rule_text': RX.__doc__,
        'rule_text_sha256': hashlib.sha256(RX.__doc__.encode('utf-8')).hexdigest(),
    }


# ------------------------------------------------------------------ coverage pre-check (no computation)
def check_coverage(*, cache: str, window, tier=None, symbols=None, plan=None) -> dict:
    """First / last cached bar before END per symbol vs the required warm-up start. Nothing past END is reported; no
    indicator, signal or trade is computed and no packet is written."""
    lo, hi, warm_lo = check_window(window, plan)
    _, syms = _syms(tier, symbols)
    out = {}
    for s in syms:
        df, rec = read_checked(cache, s)
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
        plan=None, manifest=None, manifest_commit=None) -> dict:
    lo, hi, warm_lo = check_window(window, plan)
    pre = prereg()                                       # fixed before a single cache byte is read
    static, syms = _syms(tier, symbols)
    late = _late_list(exclude_late, syms)
    if tier is not None and not manifest:
        raise RunnerError('rer: a --tier run is decision-bearing and requires --manifest; refused')
    if os.path.exists(out):
        raise RunnerError(f'rer: {out} exists; a packet is never overwritten')
    try:
        commit = VC.clean_commit(repo)                   # before a single cache byte is read
    except VC.RunnerError as e:
        raise _err(e) from None
    man, man_sha = load_manifest(manifest) if manifest else (None, None)
    frames, files, comp, bad, excluded_u, binding = {}, {}, {}, [], [], {}
    for s in syms:
        df, rec = read_checked(cache, s)
        files[s] = rec
        if man is not None:                              # every requested symbol, late-excluded ones included
            binding[s] = bind_manifest(man, s, df, rec)
        if s in late:
            first = late_listing(df, warm_lo, hi)
            if first is None:
                raise RunnerError(f'rer: --exclude-late {s} is not provably late-listed (needs a first bar before END '
                                  f'on the 4h grid after the warm-up start {_utc(warm_lo)}); refused')
            dl = VC.slice_frame(df, first, hi)           # integrity of its listed history only (no indicator)
            cl = B.completeness(dl, first, hi)
            if not cl['complete'] or not np.array_equal(_ms(dl.t), np.arange(first, hi, TF_MS, dtype=np.int64)):
                raise RunnerError(f'rer: --exclude-late {s} is late-listed but its history [{_utc(first)}, {_utc(hi)}) '
                                  f'is INCOMPLETE ({cl}); the flag does not excuse gaps; refused')
            pl = ohlc_problems(dl)
            if pl:
                raise RunnerError(f'rer: --exclude-late {s} is late-listed but OHLC sanity failed ({pl}); refused')
            excluded_u.append(dict(symbol=s, reason=LATE_REASON, first_bar=_utc(first),
                                   required_warmup_start=_utc(warm_lo)))
            continue                                     # nothing of an excluded symbol is computed
        d = VC.slice_frame(df, warm_lo, hi) if df is not None else None
        comp[s] = B.completeness(d, warm_lo, hi)
        if not comp[s]['complete']:
            bad.append(s)
        frames[s] = d
    if bad:
        raise RunnerError(f'rer: INCOMPLETE cache for {bad} over [{_utc(warm_lo)}, {_utc(hi)}); refused (no fill)')
    run_syms = [s for s in syms if s not in late]
    if not run_syms:
        raise RunnerError('rer: every requested symbol is excluded; nothing to run')
    grid = np.arange(warm_lo, hi, TF_MS, dtype=np.int64)
    unordered = [s for s in run_syms if not np.array_equal(_ms(frames[s].t), grid)]
    if unordered:
        raise RunnerError(f'rer: cache rows out of time order for {unordered}; refused')
    insane = {s: p for s in run_syms for p in [ohlc_problems(frames[s])] if p}
    if insane:
        raise RunnerError(f'rer: OHLC sanity failed for {insane} over [{_utc(warm_lo)}, {_utc(hi)}); refused')
    try:
        VC.assert_bounded(frames, warm_lo, hi)
    except VC.RunnerError as e:
        raise _err(e) from None
    rows, skips, opens, blocked, excluded = [], [], [], [], {'long': 0, 'short': 0}
    for s in run_syms:
        d = module_frame(frames[s])                      # sane bars only, never an injected e50 / atr
        t = _ms(d.t)
        lo_i = int(np.searchsorted(t, lo))
        if lo_i != WARMUP_BARS:
            raise RunnerError(f'rer: {s} slice has {lo_i} warm-up bars, expected {WARMUP_BARS}; aborted')
        ex = []
        with window_signals(lo_i, ex):
            res = RX.run_symbol(d)
        for g in ex:
            excluded[g['side']] += 1
        if any(g['b'] < lo_i for g in res['signals'] + res['blocked'] + res['skips']):
            raise RunnerError(f'rer: {s} used a pre-window candidate; aborted')
        for k in res['blocked']:
            blocked.append(dict(symbol=s, side=k['side'], n=int(k['b']), t_signal=_utc(t[k['b']]),
                                consumed_by=_utc(t[k['consumed_by']])))
        for k in res['skips']:
            skips.append(dict(symbol=s, side=k['side'], n=int(k['b']), skip=k['skip'], t_signal=_utc(t[k['b']])))
        for x in res['trades']:
            if x['b'] < lo_i:
                raise RunnerError(f"rer: {s} traded a pre-window signal (b={x['b']}); aborted")
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
    summ = RX.summarize([dict(x) for x in rows])        # the module's own totals (R_net / R_gross / costs / PF / R<-1)
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
    non_decision = tier is None                          # Codex 6230537709 Q2: a --symbols run is diagnostic only
    if non_decision:
        label = f'SYMBOLS-{len(run_syms)}'
        dec = dict(dec, diagnostic_verdict=dec['tier_verdict'], tier_verdict='NON_DECISION',
                   note='--symbols run: decision fields are diagnostic only; no tier gate may treat them as '
                        'decision-bearing')
    else:
        label = tier if not excluded_u else f'{tier}-ELIGIBLE-{len(run_syms)}'
    dec['non_decision'] = non_decision
    pkt = {
        'format': FORMAT, 'evidence_class': EVIDENCE_CLASS, 'non_promotable': True, 'non_decision': non_decision,
        'purpose': 'C4 development run (development window only; not evidence for promotion)',
        'candidate': RX.CANDIDATE, 'family': RX.FAMILY, 'label': RX.DEV_LABEL,
        'commit': commit, 'tree': 'clean', 'runner_sha256': runner_digest(),
        'manifest': None if man is None else {
            'path': os.path.abspath(manifest), 'sha256': man_sha, 'commit': manifest_commit,
            'commit_note': 'recorded only (--manifest-commit is not verified by the runner)',
            'files': binding, 'policy': 'every requested symbol (late-excluded included) must match its entry; '
                                        'any mismatch, missing entry or unreadable manifest refuses the run'},
        'window': {'start': _utc(lo), 'end': _utc(hi), 'end_exclusive': True, 'bars': int((hi - lo) // TF_MS), 'tf': TF,
                   'guard': 'pit_run.rex_window', 'holdout_start': HOLDOUT_START, 'quarters': quarters},
        'warmup': {'bars': WARMUP_BARS, 'start': _utc(warm_lo), 'signals_excluded': excluded,
                   'rule': 'warm-up bars feed EMA50 / ATR14 only; candidates with signal bar b before START '
                           'are dropped before cooldown consumption and the slot logic (run starts flat and unconsumed at '
                           'START)'},
        'universe': {'digest': static['digest'], 'id': static.get('universe_id'), 'tier': tier, 'tier_label': label,
                     'non_decision': non_decision,
                     'requested': syms, 'run': run_syms, 'excluded': excluded_u, 'exclusion_rule': EXCLUSION_RULE},
        'cache': {'format': f'app._get_candles CSV <SYMBOL>_{TF}.csv: t(ms),o,h,l,c,v,qv', 'files': files,
                  'completeness': comp, 'incomplete_policy': 'refuse the run'},
        'rules': {'warm': RX.WARM, 'box': RX.BOX, 'slope_lag': RX.SLOPE_LAG, 'w_min_atr': str(RX.W_MIN_ATR),
                  'w_max_atr': str(RX.W_MAX_ATR), 'slope_atr': str(RX.SLOPE_ATR), 'er_max': str(RX.ER_MAX),
                  'probe_min_atr': str(RX.PROBE_MIN_ATR), 'probe_max_atr': str(RX.PROBE_MAX_ATR),
                  'cloc_min': str(RX.CLOC_MIN), 'stop_atr': str(RX.STOP_ATR), 'rcap_atr': str(RX.RCAP_ATR),
                  'room_r': str(RX.ROOM_R), 'cost_guard': str(RX.COST_GUARD), 'target': 'box mean M (frozen)',
                  'cap_bars': RX.CAP_BARS, 'cooldown': RX.COOLDOWN,
                  'skip_order': ['gap_stop', 'quality', 'room', 'cost_guard'],
                  'slot': 'one trade per symbol, both sides share it; overlap ignored (consumes); cooldown per symbol '
                          "across both sides (a signal needs b' >= previous signal b + 7)"},
        'costs': {'fee_taker': float(RX.FEE), 'slip': float(RX.SLIP), 'fund_per_bar': float(RX.FUND_PER_BAR),
                  'source': 'range_extreme_reversion.simulate_trade (no recompute in the runner)'},
        'notes': {'cost': COST_NOTE},
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
            'blocked': "qualifying candidates inside the cooldown (b' < previous signal b + 7; consume nothing)",
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
            raise RunnerError(f'rer: {out} was created during the run; a packet is never overwritten') from None
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
    ap.add_argument('--manifest', default=None,
                    help='klines manifest JSON every cache file must match (required for --tier, optional for --symbols)')
    ap.add_argument('--manifest-commit', default=None, help='git commit of the manifest (recorded only)')
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
                  exclude_late=a.exclude_late.split(',') if a.exclude_late else None, manifest=a.manifest,
                  manifest_commit=a.manifest_commit)
    except RunnerError as e:
        print(f'REFUSED: {e}', file=sys.stderr)
        return 2
    c = pkt['stats']['combined']
    print(f"{RX.CANDIDATE}: {c['n']} trades, net R {c['net_R']}, PF {c['pf']}, {pkt['decision']['tier_verdict']}, "
          f"run {len(pkt['universe']['run'])} excluded {len(pkt['universe']['excluded'])}, "
          f"packet {pkt['packet_sha256'][:12]} -> {a.out}")
    return 0


if __name__ == '__main__':
    sys.exit(main())
