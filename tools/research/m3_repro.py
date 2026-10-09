"""M3 reproduction harness (RES-01 R4 acceptance; plan section 7): `trend_ema_mom.v1` on the legacy manifest.

Target: the M3 long-candidate pack (origin commit 45c22df, docs/newcore/slice/M3_long_candidate_evidence.md), the
"kill OFF (rule evaluation)" runs at `base` and `2x` costs: S4 BookRunner, data_long core 8, 4h, long only, the canary
book limits with the drawdown kill disarmed. Acceptance = the trade list reproduced trade for trade (same symbol,
signal / entry / exit candle, qty, entry / stop / exit price, exit reason) with |dR| <= R_TOL, through the R3 boundary:

  data      `pit.Dataset` over `legacy-unverified-v1` (SHA-256 re-checked at load, survivor-only label), restricted
            harness-side to the `data_long/` source (the manifest also lists `data/` 4h files for the same symbols over
            an overlapping span; `View` would concatenate both, see SOURCE_PREFIX), opened by `pit.Access` as a
            ledger-recorded development window of the `trend_ema_mom` family.
  strategy  `trend_ema_mom.decide`, run only by `pit.evaluate(window, evaluator, [t])`: it receives the frozen View and
            nothing else, and every decision is re-run under future perturbation.
  book      this module (harness side): the canary book semantics of the M3 run, reproduced from the 45c22df sources
            (newcore/runner/book.py, sizing.py, risk/book.py, adapters/fake_venue.py, runner/outcome.py):
              cycle at bar close T:  (A) fills of the decisions taken at T - 4h, at the open of the bar closing at T:
                                         exits first (taker, x (1 - slip)), then ONE equity snapshot (wallet after
                                         those exits), then entries in core-8 order, each gated (one lot per symbol,
                                         Cairo-day halt of the decision day, max 4 open lots) and sized:
                                         qty = min(1% x snapshot / dist, (3 x snapshot - open notional) /
                                         (signal close x 1.10)) floored to the step; below min qty / min notional at
                                         the signal close = refusal; fill at open x (1 + slip), taker fee; stop =
                                         fill - dist floored to the tick;
                                     (B) the bar closing at T is played: flat funding (0.005% x qty x bar close) on
                                         every lot open at its start, then the stop (opens through it -> fill at the
                                         open, else low <= stop -> fill at the stop; x (1 - slip), taker fee);
                                     (C) decision at T: MTM = wallet + open lots at the closes; Cairo-day roll / 3% halt
                                         (kill disarmed); `pit.evaluate` -> CLOSE / ENTER signals queued for T + 4h.
            Doing (A) at T instead of inside cycle T - 4h is the same arithmetic in the same order (the M3 venue fills
            a cycle's market orders at the next bar open before playing that bar); it only means no step reads a price
            before its `available_ms`.
  costs     the M3 `base` row = R3 `flat_funding_legacy` (VIP0 taker 0.05% = costs.TAKER, slippage 2 bps =
            costs.SLIP_FLOOR_BPS flat per side, funding costs.LEGACY_FLAT_FUNDING per bar); `2x` = the R3
            `fees_slip_x2` multipliers on fee and slippage, funding unchanged (as in M3). The M3 funding is charged on
            each bar's close (qty x close x rate), not on the entry notional (`costs.funding_cost` flat_legacy uses
            entry notional x bars: an R3 difference, reported, not used here).
  rules     data/exchange_rules_testnet.json (tick, step, min qty, min notional), pinned by SHA-256 (RULES_SHA256).
  reference the M3 trades CSVs read from Git at the pinned commit + blob id at run time only (never at import).

Decimal arithmetic throughout the book (prices are `Decimal(repr(float))` of the R3 rows = the CSV text value), so a
correct reproduction is exact up to the final R division. Nothing here runs on real data unless `run_r4.py m3` is
invoked, and that refuses until the R4 gate passes.
"""
from __future__ import annotations

import csv
import functools
import hashlib
import io
import json
import os
import random
import subprocess
import sys
from dataclasses import dataclass, field
from datetime import datetime, timezone
from decimal import ROUND_CEILING, ROUND_DOWN, ROUND_FLOOR, Context, Decimal
from zoneinfo import ZoneInfo

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import costs as C                                                                           # noqa: E402
import manifest as M                                                                        # noqa: E402
import pit as P                                                                             # noqa: E402
import splits as S                                                                          # noqa: E402
import trend_ema_mom as T                                                                   # noqa: E402

FORMAT = 'zb-m3-repro/1'
CORE8 = ('BTCUSDT', 'ETHUSDT', 'SOLUSDT', 'BNBUSDT', 'XRPUSDT', 'DOGEUSDT', 'AVAXUSDT', 'LINKUSDT')
LEGACY_MANIFEST = 'research_evidence/manifests/legacy-unverified-v1.json'
LEGACY_DIGEST = '0cc8ba493a714b1100ab5445f9bab432dc35dd2637260bf5d841e46a6ebc5fd5'
SOURCE_PREFIX = 'data_long/'
RULES_PATH = 'data/exchange_rules_testnet.json'
RULES_SHA256 = 'd1ef2a9c60db9858774d68903648c334ca807a66c23dc85cce13fd8add96d2af'
REF_COMMIT = '45c22dfd3100b701c5cae451b1018bfff9d24c6e'
REF_FILES = {
    'base': ('docs/newcore/slice/evidence/trend_ema_mom_v1_canary_base_kill_off.csv',
             'b8cda06962f32189555c004aa20b8255cfbd07ab'),
    '2x': ('docs/newcore/slice/evidence/trend_ema_mom_v1_canary_2x_kill_off.csv',
           '297522bdf1fd57d78d0b81eb0df376091dd09d33'),
}
R_TOL = Decimal('1e-9')
SEED, RESAMPLES = 20261008, 10_000
CAIRO = ZoneInfo('Africa/Cairo')
ZERO, ONE = Decimal(0), Decimal(1)
X = Context(prec=80)                               # exact for every product / sum the book forms
RC = Context(prec=34)                              # the M3 RCTX (R, halt ratio)
SC = Context(prec=34, rounding=ROUND_DOWN)         # the M3 sizing context


class ReproError(ValueError):
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


def cost_row(name: str) -> CostRow:
    """M3 cost rows from the R3 constants and stress multipliers."""
    if name == 'base':
        s = C.STRESS['flat_funding_legacy']
    elif name == '2x':
        s = C.STRESS['fees_slip_x2']
    else:
        raise ReproError("cost row must be 'base' or '2x'")
    d = lambda v: Decimal(repr(v))
    return CostRow(name, X.multiply(d(C.TAKER), d(s.fee_mult)),
                   X.multiply(X.divide(d(C.SLIP_FLOOR_BPS), Decimal(10_000)), d(s.slip_mult)), d(C.LEGACY_FLAT_FUNDING))


@dataclass(frozen=True)
class Rule:
    tick: Decimal
    step: Decimal
    min_qty: Decimal
    min_notional: Decimal


def load_rules(path: str, symbols, sha256: str | None = RULES_SHA256) -> dict:
    with open(path, 'rb') as f:
        raw = f.read()
    if sha256 is not None and hashlib.sha256(raw).hexdigest() != sha256:
        raise ReproError(f'{path}: bytes differ from the pinned M3 rules snapshot')
    doc = json.loads(raw, parse_float=Decimal, parse_int=Decimal)
    if doc.get('schema') != 'zackbot.exchange_rules/1':
        raise ReproError(f'{path}: not a zackbot.exchange_rules/1 snapshot')
    return {s: Rule(*(Decimal(doc['symbols'][s][k]) for k in ('tick', 'step', 'min_qty', 'min_notional')))
            for s in symbols}


def q_down(x: Decimal, unit: Decimal) -> Decimal:
    return X.multiply(X.divide(x, unit).to_integral_value(rounding=ROUND_FLOOR), unit)


def q_up(x: Decimal, unit: Decimal) -> Decimal:
    return X.multiply(X.divide(x, unit).to_integral_value(rounding=ROUND_CEILING), unit)


def dec(x: float) -> Decimal:
    return Decimal(repr(float(x)))


def cairo_day(ms: int) -> str:
    return datetime.fromtimestamp(ms // 1000, tz=timezone.utc).astimezone(CAIRO).date().isoformat()


# ------------------------------------------------------------------ dataset (R3 boundary)
def restrict_sources(ds: P.Dataset, prefix: str = SOURCE_PREFIX, symbols=CORE8, interval: str = T.TF) -> None:
    """Harness-side source choice on a survivor-only legacy Dataset: keep only `prefix` files for the served symbols
    and refuse any remaining overlap (a View concatenates every file of a key, so overlapping sources would serve
    duplicated bars)."""
    if ds.universe_digest is not None:
        raise ReproError('source restriction is only for the survivor-only legacy reproduction')
    for s in symbols:
        key = ('klines', s, interval)
        keep = [f for f in ds._files.get(key, []) if f['path'].startswith(prefix)]
        if len(keep) != 1:
            raise ReproError(f'{s} {interval}: expected exactly one {prefix} file, found {len(keep)}')
        ds._files[key] = keep
    for key, fs in ds._files.items():
        for a, b in zip(fs, fs[1:]):
            if a['last_open_ms'] is not None and b['first_open_ms'] is not None and b['first_open_ms'] <= a['last_open_ms']:
                if key[1] in symbols and key[2] == interval:
                    raise ReproError(f'{key}: overlapping source files {a["path"]} / {b["path"]}')


def span(ds: P.Dataset, symbols=CORE8, interval: str = T.TF) -> tuple[int, int]:
    """[first open, last close] of the first symbol's series (the M3 replay clock), from manifest metadata only."""
    f = ds.files('klines', symbols[0], interval)
    if len(f) != 1:
        raise ReproError(f'{symbols[0]}: restrict_sources first')
    return f[0]['first_open_ms'], f[0]['last_open_ms'] + M.INTERVALS[interval]


# ------------------------------------------------------------------ book replay
@dataclass
class Lot:
    symbol: str
    signal_close_ms: int
    entry_ms: int
    qty: Decimal
    entry_price: Decimal
    stop_price: Decimal
    fees: Decimal
    position: object
    funding: Decimal = ZERO


@dataclass
class Trade:
    symbol: str
    side: str
    signal_close_ms: int
    entry_ms: int
    exit_ms: int
    exit_signal_close_ms: int | None
    qty: Decimal
    entry_price: Decimal
    exit_price: Decimal
    stop_price: Decimal
    risk_usd: Decimal
    gross: Decimal
    fees: Decimal
    funding: Decimal
    pnl: Decimal
    r: Decimal
    exit_reason: str


@dataclass
class Replay:
    trades: list = field(default_factory=list)
    refusals: dict = field(default_factory=dict)
    halts: list = field(default_factory=list)
    curve: list = field(default_factory=list)
    open_lots: int = 0
    wallet: Decimal = ZERO
    decisions: int = 0


def _bar_at(view, s, pos):
    b = view.bars(s, T.TF, 1, position=pos) if (pos is not None or s in view.members()) else ()
    return b[-1] if b and b[-1].available_ms == view.t else None


def replay(window, *, symbols=CORE8, start_ms: int, end_ms: int, costs: CostRow, rules: dict, book: Book = Book(),
           params: T.Params = T.PRIMARY, perturb: bool = True) -> Replay:
    """Run the M3 book over [start_ms, end_ms] (bar opens/closes on the 4h grid) through `window` (a pit.Window)."""
    iv = T.TF_MS
    if (end_ms - start_ms) % iv or start_ms % iv:
        raise ReproError('start/end must lie on the 4h grid')
    out = Replay(wallet=book.equity0)
    lots: dict[str, Lot] = {}
    closes: dict[str, Decimal] = {}
    pend_exit, pend_entry = [], []
    day, day_start, halted_day = None, None, None

    def refuse(reason):
        out.refusals[reason] = out.refusals.get(reason, 0) + 1

    def close_lot(lot, px, at_ms, reason, exit_sig):
        fee = X.multiply(X.multiply(lot.qty, px), costs.taker)
        gross = X.multiply(X.subtract(px, lot.entry_price), lot.qty)
        out.wallet = X.subtract(X.add(out.wallet, gross), fee)
        fees = X.add(lot.fees, fee)
        pnl = X.subtract(X.subtract(gross, fees), lot.funding)
        risk = X.multiply(lot.qty, abs(X.subtract(lot.entry_price, lot.stop_price)))
        out.trades.append(Trade(lot.symbol, 'LONG', lot.signal_close_ms, lot.entry_ms, at_ms, exit_sig, lot.qty,
                                lot.entry_price, px, lot.stop_price, risk, gross, fees, lot.funding, pnl,
                                RC.divide(pnl, risk) if risk > 0 else ZERO, reason))
        del lots[lot.symbol]

    t = start_ms + iv
    while t <= end_ms:
        v = window.view(t)
        bars = {s: _bar_at(v, s, lots[s].position if s in lots else None) for s in symbols}
        # (A) fills of the decisions taken at t - 4h, at the open of the bar closing at t
        for s, sig_ms in pend_exit:
            lot, b = lots.get(s), bars[s]
            if lot is None:
                continue
            if b is None:
                refuse('exit_no_next_open')
                continue
            close_lot(lot, X.multiply(dec(b.open), X.subtract(ONE, costs.slip)), b.open_ms, 'exit.exit_signal', sig_ms)
        snapshot = out.wallet
        for s, sig_ms, dist, ref, halted in pend_entry:
            if s in lots:
                refuse('capacity.in_trade')
                continue
            if halted:
                refuse('filter.halt')
                continue
            if len(lots) >= book.max_positions:
                refuse('capacity.max_positions')
                continue
            b, rule = bars[s], rules[s]
            notional = ZERO
            for x in lots.values():
                notional = X.add(notional, X.multiply(x.qty, x.entry_price if x.entry_ms == t - iv
                                                      else closes.get(x.symbol, x.entry_price)))
            risk_usd = SC.multiply(snapshot, book.risk_pct)
            if snapshot <= 0 or dist <= 0 or ref <= 0:
                refuse('execution.size_min')
                continue
            raw = SC.divide(risk_usd, dist)
            room = SC.subtract(SC.multiply(book.max_leverage, snapshot), notional)
            cap = SC.divide(max(room, ZERO), SC.multiply(ref, SC.add(ONE, book.cap_gap_buffer)))
            q = min(raw, cap)
            q = q_down(q, rule.step) if q > 0 else ZERO
            if q <= 0 or q < rule.min_qty or SC.multiply(q, ref) < rule.min_notional:
                refuse('execution.size_min')
                continue
            if b is None:
                refuse('entry_no_next_open')
                continue
            px = X.multiply(dec(b.open), X.add(ONE, costs.slip))
            fee = X.multiply(X.multiply(q, px), costs.taker)
            out.wallet = X.subtract(out.wallet, fee)
            pos = window.view(sig_ms).enter(s, f'{T.RULE_ID}:{s}:{sig_ms}')
            lots[s] = Lot(s, sig_ms, b.open_ms, q, px, q_down(X.subtract(px, dist), rule.tick), fee, pos)
        # (B) play the bar closing at t: funding on lots open at its start, then the stop
        for s in sorted(lots):
            lot, b = lots[s], bars[s]
            if b is None:
                continue
            amt = X.multiply(X.multiply(lot.qty, dec(b.close)), costs.funding_per_bar)
            out.wallet = X.subtract(out.wallet, amt)
            lot.funding = X.add(lot.funding, amt)
            o, lo = dec(b.open), dec(b.low)
            hit = o if o <= lot.stop_price else (lot.stop_price if lo <= lot.stop_price else None)
            if hit is not None:
                close_lot(lot, X.multiply(hit, X.subtract(ONE, costs.slip)), b.open_ms, 'exit.stop', None)
        for s, b in bars.items():
            if b is not None:
                closes[s] = dec(b.close)
        # (C) decision at t
        mtm = out.wallet
        for x in lots.values():
            mtm = X.add(mtm, X.multiply(X.subtract(closes.get(x.symbol, x.entry_price), x.entry_price), x.qty))
        out.curve.append((t, str(mtm)))
        d = cairo_day(t)
        if d != day:
            day, day_start = d, mtm
        if halted_day != d and day_start > 0 and RC.subtract(RC.divide(mtm, day_start), ONE) <= -book.daily_loss_pct:
            halted_day = d
            out.halts.append(d)
        pend_exit, pend_entry = [], []
        if t < end_ms:
            held = tuple((s, lots[s].position) for s in symbols if s in lots)
            ev = functools.partial(T.decide, params=params, symbols=tuple(symbols), held=held)
            (sigs,) = P.evaluate(window, ev, [t], perturb=perturb)
            out.decisions += 1
            for sg in sigs:
                if sg[0] == T.CLOSE and sg[1] in lots:
                    pend_exit.append((sg[1], sg[2]))
            for sg in sigs:
                if sg[0] == T.ENTER:
                    pend_entry.append((sg[1], sg[2], Decimal(sg[3]), Decimal(sg[4]), halted_day == d))
        t += iv
    out.open_lots = len(lots)
    return out


# ------------------------------------------------------------------ reference + comparison + statistics
REF_KEYS = ('symbol', 'side', 'signal_close_ms', 'entry_ms', 'exit_ms', 'exit_reason')
REF_DECIMALS = ('qty', 'entry_price', 'exit_price', 'stop_price')


def read_reference(repo: str, name: str) -> str:
    """The pinned M3 trades CSV from Git (blob id verified). Called only by the gated runner."""
    path, blob = REF_FILES[name]
    got = subprocess.run(['git', '-C', repo, 'rev-parse', f'{REF_COMMIT}:{path}'], capture_output=True, text=True)
    if got.returncode or got.stdout.strip() != blob:
        raise ReproError(f'M3 reference {path}@{REF_COMMIT[:7]} missing or not blob {blob[:12]} (git fetch origin?)')
    return subprocess.run(['git', '-C', repo, 'cat-file', 'blob', blob], capture_output=True, check=True,
                          text=True).stdout


def parse_reference(text: str) -> list[dict]:
    rows = list(csv.DictReader(io.StringIO(text)))
    for r in rows:
        for k in ('signal_close_ms', 'entry_ms', 'exit_ms'):
            r[k] = int(r[k])
        for k in REF_DECIMALS + ('r', 'pnl'):
            r[k] = Decimal(r[k])
    return rows


def as_rows(trades) -> list[dict]:
    return [{'symbol': t.symbol, 'side': t.side, 'signal_close_ms': t.signal_close_ms, 'entry_ms': t.entry_ms,
             'exit_ms': t.exit_ms, 'exit_reason': t.exit_reason, 'qty': t.qty, 'entry_price': t.entry_price,
             'exit_price': t.exit_price, 'stop_price': t.stop_price, 'r': t.r, 'pnl': t.pnl}
            for t in sorted(trades, key=lambda t: (t.entry_ms, t.symbol))]


def compare(ref: list[dict], got: list[dict]) -> dict:
    """Trade-for-trade acceptance: keys exact, prices / qty numerically equal, |dR| <= R_TOL."""
    k = lambda r: (r['symbol'], r['entry_ms'])
    a, b = {k(r): r for r in ref}, {k(r): r for r in got}
    missing, extra = sorted(set(a) - set(b)), sorted(set(b) - set(a))
    fields, max_dr = [], ZERO
    for key in sorted(set(a) & set(b)):
        x, y = a[key], b[key]
        bad = [f for f in REF_KEYS if x[f] != y[f]] + [f for f in REF_DECIMALS if Decimal(x[f]) != Decimal(y[f])]
        dr = abs(Decimal(x['r']) - Decimal(y['r']))
        max_dr = max(max_dr, dr)
        if dr > R_TOL:
            bad.append('r')
        if bad:
            fields.append({'trade': list(key), 'fields': bad})
    ok = not missing and not extra and not fields and len(ref) == len(got)
    return {'verdict': 'REPRODUCED' if ok else 'NOT REPRODUCED', 'reference_trades': len(ref), 'repro_trades': len(got),
            'matched': len(set(a) & set(b)), 'missing_in_repro': [list(m) for m in missing[:50]],
            'extra_in_repro': [list(m) for m in extra[:50]], 'field_mismatches': fields[:50], 'max_abs_dR': str(max_dr)}


def episodes(rows, tf_ms: int = T.TF_MS) -> list[list[dict]]:
    """Trades whose holding windows [entry, end] overlap on any symbol form one episode (end = exit + one bar for a
    stop fill, which happens during its bar)."""
    out, cur_end = [], None
    for r in sorted(rows, key=lambda r: (r['entry_ms'], r['symbol'])):
        end = r['exit_ms'] + (tf_ms if r['exit_reason'] == 'exit.stop' else 0)
        if out and r['entry_ms'] < cur_end:
            out[-1].append(r)
            cur_end = max(cur_end, end)
        else:
            out.append([r])
            cur_end = end
    return out


def summary(rows, *, seed: int = SEED, resamples: int = RESAMPLES) -> dict:
    """Mean net R per trade with the per-trade and episode-clustered 95% bootstrap CIs (same code for reference and
    reproduction, so the comparison is like for like)."""
    rs = [float(r['r']) for r in rows]
    n = len(rs)
    if not n:
        return {'trades': 0, 'episodes': 0}
    eps = [[float(r['r']) for r in e] for e in episodes(rows)]
    rng = random.Random(seed)
    tr, ep = [], []
    for _ in range(resamples):
        tr.append(sum(rs[rng.randrange(n)] for _ in range(n)) / n)
        pick = [eps[rng.randrange(len(eps))] for _ in range(len(eps))]
        ep.append(sum(map(sum, pick)) / sum(map(len, pick)))
    q = lambda xs: [sorted(xs)[int(0.025 * len(xs))], sorted(xs)[min(len(xs) - 1, int(0.975 * len(xs)))]]
    return {'trades': n, 'episodes': len(eps), 'mean_r': sum(rs) / n, 'win_rate': sum(r > 0 for r in rs) / n,
            'trade_ci95': q(tr), 'episode_ci95': q(ep), 'resamples': resamples, 'seed': seed}


def report(*, name: str, ref_text: str, rep: Replay, meta: dict) -> dict:
    ref, got = parse_reference(ref_text), as_rows(rep.trades)
    return {'format': FORMAT, 'cost_row': name, 'meta': meta, 'acceptance': compare(ref, got),
            'reference_summary': summary(ref), 'repro_summary': summary(got),
            'repro_counts': {'decisions': rep.decisions, 'refusals': dict(sorted(rep.refusals.items())),
                             'halts': len(rep.halts), 'open_lots_at_end': rep.open_lots, 'wallet_end': str(rep.wallet)}}


def open_legacy(repo: str, store: str, ledger_path: str, *, author: str, cairo_date: str):
    """The legacy Dataset restricted to data_long core 8, and its ledger-recorded development window."""
    man = M.load(os.path.join(repo, LEGACY_MANIFEST))
    if man['digest'] != LEGACY_DIGEST:
        raise ReproError('legacy manifest digest differs from the pinned legacy-unverified-v1')
    ds = P.Dataset(man, store, None)
    restrict_sources(ds)
    lo, hi = span(ds)
    acc = P.Access(ds, None, ledger_path, candidate_id=T.RULE_ID, author=author, cairo_date=cairo_date)
    w = acc.open_development(S.utc(lo), S.utc(hi), detail={
        'purpose': 'R4 acceptance: M3 reproduction on legacy-unverified-v1 (development, survivor-only, spent window)',
        'symbols': list(CORE8), 'source_prefix': SOURCE_PREFIX, 'reference_commit': REF_COMMIT})
    return ds, w, lo, hi
