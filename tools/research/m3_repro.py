"""M3 reproduction harness (RES-01 R4-0 item 5; plan section 7): `trend_ema_mom.v1` on `legacy-m3-repro-v1`.

Target: the M3 long-candidate pack (origin commit 45c22df, docs/newcore/slice/M3_long_candidate_evidence.md), the
"kill OFF (rule evaluation)" runs at `base` and `2x` costs: S4 BookRunner, data_long core 8, 4h, long only, the canary
book limits with the drawdown kill disarmed, UNCAPPED (reproduction / development evidence only: the promotable
primary is the capped 180-bar rule, never part of the reproduction). Acceptance = the trade list reproduced trade for trade (same symbol, signal / entry / exit
candle, qty, entry / stop / exit price, exit reason) with |dR| <= R_TOL, through the accepted R3 boundary:

  data      `pit.Dataset` over the reproduction-only manifest `legacy-m3-repro-v1` (REPRO_DIGEST pinned; exactly the
            8 data_long/4h files the M3 run served, zero overlaps; `Dataset` itself refuses overlapping coverage, so
            there is no harness-side source filter), labelled SURVIVOR-ONLY + REPRO-ONLY, opened by `pit.Access` as a
            ledger-recorded DEVELOPMENT window only (`Access.open` refuses a repro-only manifest for any split).
  evaluator `m3_eval.py` (the M3 book + `trend_ema_mom` signals) runs only inside `pit.evaluate`: the zb-eval-sandbox
            process with a View proxy, every decision re-run under future perturbation, the whole schedule replayed in
            a second fresh process, and the zb-eval-attestation recorded in the family ledger before any result is
            returned. The harness never evaluates the rule in-process.
  costs     the M3 `base` row = VIP0 taker 0.05% (costs.TAKER), slippage 2 bps (costs.SLIP_FLOOR_BPS) flat per side,
            LEGACY flat funding costs.LEGACY_FLAT_FUNDING on each bar close (the reproduction-only adapter: m3_eval
            refuses to run without the REPRO-ONLY label); `2x` = the R3 `fees_slip_x2` multipliers on fee and slippage.
  rules     data/exchange_rules_testnet.json (tick, step, min qty, min notional), pinned by SHA-256 (RULES_SHA256); the
            core-8 rows embedded in m3_eval (the sandbox reads no data file) must equal it (`check_embedded`).
  reference the M3 trades CSVs read from Git at the pinned commit + blob id at run time only (never at import).
  reports   written atomically (temp file in the same directory + os.replace), never partially.

Nothing here runs on real data unless `run_r4.py m3` is invoked, and that refuses until the R4 gate passes.
"""
from __future__ import annotations

import csv
import hashlib
import io
import json
import os
import random
import subprocess
import sys
import tempfile
from decimal import Decimal

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import costs as C                                                                           # noqa: E402
import m3_eval as E                                                                         # noqa: E402
import manifest as M                                                                        # noqa: E402
import pit as P                                                                             # noqa: E402
import splits as S                                                                          # noqa: E402
import trend_ema_mom as T                                                                   # noqa: E402

FORMAT = 'zb-m3-repro/2'
CORE8 = E.CORE8
REPRO_MANIFEST = 'research_evidence/manifests/legacy-m3-repro-v1.json'
REPRO_ID = 'legacy-m3-repro-v1'
REPRO_DIGEST = 'a42c36927eccf48a16c09085192dd53a7ef7ba607dfc1021cf96a67c917e8bb6'
EVALUATOR = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'm3_eval.py')
FUNCTIONS = {'base': 'm3_base', '2x': 'm3_2x'}
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
Book, CostRow, Rule = E.Book, E.CostRow, E.Rule
X, RC, SC, ZERO, ONE = E.X, E.RC, E.SC, E.ZERO, E.ONE
q_down, q_up, dec, cairo_day = E.q_down, E.q_up, E.dec, E.cairo_day


class ReproError(ValueError):
    pass


def cost_row(name: str) -> CostRow:
    """M3 cost rows derived from the R3 constants and stress multipliers (m3_eval.COSTS must equal these)."""
    if name == 'base':
        s = C.STRESS['flat_funding_legacy']
    elif name == '2x':
        s = C.STRESS['fees_slip_x2']
    else:
        raise ReproError("cost row must be 'base' or '2x'")
    d = lambda v: Decimal(repr(v))
    return CostRow(name, X.multiply(d(C.TAKER), d(s.fee_mult)),
                   X.multiply(X.divide(d(C.SLIP_FLOOR_BPS), Decimal(10_000)), d(s.slip_mult)), d(C.LEGACY_FLAT_FUNDING))


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


def check_embedded(repo: str) -> None:
    """The evaluator's embedded rules and cost rows equal the pinned snapshot and the R3 constants."""
    want = load_rules(os.path.join(repo, RULES_PATH), CORE8)
    for s in CORE8:
        a, b = want[s], E.CORE8_RULES[s]
        if any(getattr(a, k) != getattr(b, k) for k in ('tick', 'step', 'min_qty', 'min_notional')):
            raise ReproError(f'{s}: m3_eval.CORE8_RULES differs from the pinned rules snapshot')
    for name in FUNCTIONS:
        if cost_row(name) != E.COSTS[name]:
            raise ReproError(f'{name}: m3_eval.COSTS differs from the R3 cost constants')


def atomic_write_json(path: str, doc) -> str:
    """Write `doc` to `path` atomically: a temp file in the same directory, fsync, os.replace. A failure at any
    point leaves either the previous file or no file, never a partial one."""
    d = os.path.dirname(os.path.abspath(path))
    os.makedirs(d, exist_ok=True)
    fd, tmp = tempfile.mkstemp(prefix='.' + os.path.basename(path) + '.', suffix='.tmp', dir=d)
    try:
        with os.fdopen(fd, 'w', encoding='utf-8', newline='\n') as f:
            json.dump(doc, f, indent=1, sort_keys=True)
            f.write('\n')
            f.flush()
            os.fsync(f.fileno())
        os.replace(tmp, path)
    except BaseException:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise
    return path


# ------------------------------------------------------------------ dataset (R3 boundary) + sandboxed run
def require_repro(ds: P.Dataset) -> None:
    """The M3 adapter runs only on a reproduction-only, survivor-only dataset (never primary data)."""
    if M.promotion_eligible(ds.manifest) or P.REPRO_ONLY not in ds.labels or ds.universe_digest is not None:
        raise ReproError('M3 reproduction runs only on a reproduction-only manifest (legacy-m3-repro-v1), '
                         'never on primary research data')


def span(ds: P.Dataset, symbols=CORE8, interval: str = T.TF) -> tuple[int, int]:
    """[first open, last close] of the first symbol's series (the M3 replay clock), from manifest metadata only."""
    f = ds.files('klines', symbols[0], interval)
    if len(f) != 1:
        raise ReproError(f'{symbols[0]} {interval}: expected exactly one manifest file, found {len(f)}')
    return f[0]['first_open_ms'], f[0]['last_open_ms'] + M.INTERVALS[interval]


def schedule(start_ms: int, end_ms: int) -> list[int]:
    """Every 4h bar close in (start, end]: one book cycle each."""
    iv = T.TF_MS
    if (end_ms - start_ms) % iv or start_ms % iv or end_ms <= start_ms:
        raise ReproError('start/end must lie on the 4h grid, start < end')
    return list(range(start_ms + iv, end_ms + 1, iv))


def replay(window, *, start_ms: int, end_ms: int, function: str, evaluator: str = EVALUATOR,
           perturb: bool = True) -> P.Evaluated:
    """Run the M3 book over (start_ms, end_ms] through the accepted sandboxed runner (`pit.evaluate`). Returns the
    attested `pit.Evaluated` (per-cycle outputs, `.results` = m3_eval.summary, `.attestation`)."""
    require_repro(window._ds)
    return P.evaluate(window, evaluator, function, schedule(start_ms, end_ms), summary='summary', perturb=perturb)


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
    """m3_eval trade dicts (Decimal text) -> comparison rows."""
    return [{**{k: t[k] for k in REF_KEYS}, **{k: Decimal(t[k]) for k in REF_DECIMALS + ('r', 'pnl')}}
            for t in sorted(trades, key=lambda t: (t['entry_ms'], t['symbol']))]


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


def report(*, name: str, ref_text: str, ev, meta: dict) -> dict:
    res = ev.results
    ref, got = parse_reference(ref_text), as_rows(res['trades'])
    att = ev.attestation
    return {'format': FORMAT, 'cost_row': name, 'meta': meta, 'acceptance': compare(ref, got),
            'reference_summary': summary(ref), 'repro_summary': summary(got),
            'attestation': {'digest': ev.attestation_digest, 'runner': att['runner'], 'isolation': att['isolation'],
                            'perturbed': att['perturbed'], 'evaluator': att['evaluator'],
                            'outputs_digest': att['outputs_digest'], 'results_digest': att['results_digest']},
            'repro_counts': {'cycles': res['cycles'], 'decisions': max(res['cycles'] - 1, 0),
                             'refusals': res['refusals'], 'halts': len(res['halts']),
                             'open_lots_at_end': res['open_lots_at_end'], 'wallet_end': res['wallet_end']}}


def open_repro(repo: str, store: str, ledger_path: str, *, author: str, cairo_date: str):
    """The pinned reproduction-only Dataset and its ledger-recorded development window (no split can open)."""
    man = M.load(os.path.join(repo, REPRO_MANIFEST))
    if man['digest'] != REPRO_DIGEST or man['manifest_id'] != REPRO_ID:
        raise ReproError(f'manifest differs from the pinned {REPRO_ID} ({REPRO_DIGEST[:12]})')
    ds = P.Dataset(man, store, None)
    require_repro(ds)
    lo, hi = span(ds)
    acc = P.Access(ds, None, ledger_path, candidate_id=T.RULE_ID, author=author, cairo_date=cairo_date, repo=repo)
    w = acc.open_development(S.utc(lo), S.utc(hi), detail={
        'purpose': f'R4-0 item 5: M3 reproduction on {REPRO_ID} (REPRO-ONLY development window, spent, never evidence)',
        'symbols': list(CORE8), 'reference_commit': REF_COMMIT})
    return ds, w, lo, hi
