"""breakout_pyramid_falsify.v1: deterministic code-only research runner, slice 1 (no data adapter, no evaluation).

Authoritative spec: research_evidence/prereg/breakout_pyramid_falsify.v1.json (signed; read by this module, nothing
else is policy). This slice implements, on caller-supplied closed 4h bars only:

  preflight   the signed contract (bytes hash == the `prereg_sha256` every frozen ledger row records), the six frozen
              ledger IDs exactly (S1, S2, S3, S1-T, B0, B2: kinds, gated flags, hash chain, REGISTRY genesis), the pinned
              function-level source hashes (sig fn / indicators / ema) + live mgmt, the costs.py and universe artifact
              hashes, the archive manifest digest, and the code identity (git commit + dirty flag + module hashes).
              Every mismatch raises a typed PreflightError subclass; a dirty tree refuses unless the explicit test flag.
  engine      the causal four-phase bar loop (book.bar_phases), one long position per symbol, the backtest.py
              path_points convention and the walk_path FBL-BT01 rules for every intrabar event.
  unit replay entry set E (S1 entries, fill-time re-arm) and the paired per-stage quantity-1 replays S1 / S1-T / S2 / S3.
  book        the account replay of one stage from the raw signals (admission on phase-1 state only, skips counted).
  B0          the seeded random-entry sampler (without replacement, no overlap under the fill-time re-arm rule,
              per symbol / slice count match, fail-closed BaselineIncomplete).

Deferred to slice 2: the streaming archive data adapter (klines / funding / mark / point-in-time rules, funding cadence
proof via costs.check_funding_sequence), run.json + hash-chained resumable lines + --max-rss-mb, bootstrap LCB, PF,
B0 percentile gate, DD tiers / underwater / Calmar, concentration, folds and every gate / verdict computation.

Nothing here writes a verdict or spends a ledger row: run() returns an in-memory dict. write_result() refuses unless
an EvalAuthorization bound to the signed contract hash is passed, and never writes under the ledger directory.
No network, no market-data path, no wall clock in any result byte.
"""
from __future__ import annotations

import bisect
import hashlib
import inspect
import json
import math
import os
import random
import subprocess
import sys
from dataclasses import dataclass, field
from datetime import datetime, timezone

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import costs as C                                                                           # noqa: E402
import manifest as M                                                                        # noqa: E402

sys.path.insert(0, M.REPO)
import backtest as BT                                                                       # noqa: E402
import feasibility as F                                                                     # noqa: E402
import strategies as ST                                                                     # noqa: E402

REPO = M.REPO
CANDIDATE = 'breakout_pyramid_falsify.v1'
LEDGER_FAMILY = 'breakout_pyramid_falsify'
PREREG_REL = ('research_evidence', 'prereg', 'breakout_pyramid_falsify.v1.json')
LEDGER_REL = ('research_evidence', 'ledger', 'breakout_pyramid_falsify.jsonl')
REGISTRY_REL = ('research_evidence', 'ledger', 'REGISTRY.jsonl')
COSTS_REL = ('tools', 'research', 'costs.py')
UNIVERSE_REL = ('research_evidence', 'universe', 'static-top40-minhist365-contract-20261011.json')
LEDGER_IDS = ('S1', 'S2', 'S3', 'S1-T', 'B0', 'B2')                 # frozen order of the six ledger trial records
LEDGER_KINDS = ('variant', 'variant', 'variant', 'variant', 'baseline', 'baseline')
STAGE_IDS = ('S1', 'S1-T', 'S2', 'S3')
CODE_MODULES = ('backtest.py', 'feasibility.py', 'strategies.py', 'tools/research/costs.py',
                'tools/research/manifest.py', 'tools/research/bpyr_runner.py')
HOUR = 3_600_000
TF = 4 * HOUR
SLIP_C_CRYPTO = 0.05                     # slip-cal-v1 crypto coefficient (same accounting input as the EDGE05 runner)
SLIP_FLOOR_BPS = 5.0                     # costs.slippage "the 0.05% unfitted fallback each market fill"
MAX_OPEN_LOTS = 4                        # book.admission "open lots < 4"
GROSS_CAP = 3.0                          # book.admission "gross notional after the fill <= 3x equity"
START_EQUITY = 10_000.0                  # book.account
ROWS = {                                 # costs.stress_rows: (multiplier, funding mode)
    'base_x1': (1.0, 'actual'),
    'all_variable_costs_x1.5': (1.5, 'actual'),
    'all_variable_costs_x2': (2.0, 'actual'),
    'funding_zero': (1.0, 'zero'),
}
BASE = C.Scenario('base_x1')


# ------------------------------------------------------------------ errors
class RunnerError(RuntimeError):
    pass


class PreflightError(RunnerError):
    pass


class PreregInvalid(PreflightError):
    pass


class LedgerMismatch(PreflightError):
    pass


class IdentityMismatch(PreflightError):
    pass


class CostHashMismatch(PreflightError):
    pass


class ManifestMismatch(PreflightError):
    pass


class DirtyTree(PreflightError):
    pass


class HoldoutRefused(RunnerError):
    pass


class BaselineIncomplete(RunnerError):
    """B0 could not match a symbol / slice S1 count: the B0 gate fails closed (H1 cannot PASS, verdict INCOMPLETE)."""
    def __init__(self, sym, slice_no, replicate, got, need):
        super().__init__(f'B0 INCOMPLETE: {sym} slice {slice_no} replicate {replicate}: {got}/{need}')
        self.sym, self.slice_no, self.replicate, self.got, self.need = sym, slice_no, replicate, got, need


class WriteRefused(RunnerError):
    pass


class Incomplete(Exception):
    def __init__(self, reason, at_ms=None):
        super().__init__(reason)
        self.reason, self.at_ms = reason, at_ms


# ------------------------------------------------------------------ hashing
def canon(obj) -> bytes:
    return json.dumps(obj, sort_keys=True, separators=(',', ':'), ensure_ascii=True, allow_nan=False).encode('ascii')


def sha(b: bytes) -> str:
    return hashlib.sha256(b).hexdigest()


def file_sha(path: str) -> str:
    h = hashlib.sha256()
    with open(path, 'rb') as f:
        for chunk in iter(lambda: f.read(1 << 20), b''):
            h.update(chunk)
    return h.hexdigest()


def src_sha(obj) -> str:
    """identity.source_sha256_rule: sha256 of inspect.getsource(obj) with CRLF normalised to LF."""
    return sha(inspect.getsource(obj).replace('\r\n', '\n').encode())


def ledger_line(rec: dict) -> bytes:
    """ledger.line_of: the canonical line whose sha256 is the next record's `prev`."""
    return canon(rec) + b'\n'


def iso_ms(s: str) -> int:
    return int(datetime.strptime(s, '%Y-%m-%dT%H:%M:%SZ').replace(tzinfo=timezone.utc).timestamp() * 1000)


# ------------------------------------------------------------------ preflight
def _p(repo, rel):
    return os.path.join(repo, *rel)


def load_prereg(repo: str = REPO) -> tuple[dict, bytes]:
    raw = open(_p(repo, PREREG_REL), 'rb').read()
    return json.loads(raw.decode('utf-8')), raw


def load_jsonl(path: str) -> list[dict]:
    with open(path, 'rb') as f:
        return [json.loads(x) for x in f.read().decode('utf-8').splitlines() if x.strip()]


def verify_prereg(p: dict) -> None:
    want = {'format': 'zb-falsify-prereg/1', 'candidate_id': CANDIDATE, 'family': 'breakout_pyramid', 'side': 'long',
            'signed': True}
    for k, v in want.items():
        if p.get(k) != v:
            raise PreregInvalid(f'prereg {k} is {p.get(k)!r}, expected {v!r}')
    if not str(p.get('status', '')).startswith('FROZEN - SIGNED BY CODEX'):
        raise PreregInvalid('prereg is not frozen and signed')
    if p.get('boundaries') != {'paper': False, 'runtime_change': False, 'holdout_read': False, 'mainnet': False,
                               'evaluation_run': False}:
        raise PreregInvalid('prereg boundaries changed')
    tb = p['trial_budget']
    ids = tuple(tb['gated_ids']) + tuple(tb['diagnostic_ids']) + tuple(b['id'] for b in p['baselines'])
    if sorted(ids) != sorted(LEDGER_IDS) or tb['total_ledger_trial_records'] != len(LEDGER_IDS):
        raise LedgerMismatch(f'prereg trial ids {ids} deviate from the six frozen ledger ids')
    if set(p['stages']) - {'choices', 'no_cap', 'r_unit'} != set(STAGE_IDS):
        raise LedgerMismatch('prereg stage set deviates from S1 / S1-T / S2 / S3')


def verify_ledger(p: dict, prereg_sha: str, rows: list[dict], registry: list[dict]) -> None:
    """The six frozen trial records exactly: ids, order, kinds, gated flags, signed contract hash, chain, genesis."""
    if [r.get('detail', {}).get('id') for r in rows] != list(LEDGER_IDS):
        raise LedgerMismatch(f'ledger ids {[r.get("detail", {}).get("id") for r in rows]} != {list(LEDGER_IDS)}')
    gated = {s: p['stages'][s]['gated'] for s in STAGE_IDS}
    gated.update({b['id']: b['gated'] for b in p['baselines']})
    dev = p['windows']['development']
    prev = '0' * 64
    for k, (r, kind) in enumerate(zip(rows, LEDGER_KINDS), 1):
        d = r['detail']
        checks = {'seq': (r.get('seq'), k), 'prev': (r.get('prev'), prev), 'kind': (r.get('kind'), kind),
                  'family': (r.get('family'), LEDGER_FAMILY), 'candidate_id': (r.get('candidate_id'), CANDIDATE),
                  'gated': (d.get('gated'), gated[d['id']]), 'prereg_sha256': (d.get('prereg_sha256'), prereg_sha),
                  'prereg_path': (d.get('prereg_path'), '/'.join(PREREG_REL)),
                  'manifest_digest': (r.get('manifest_digest'), p['source']['manifest_digest']),
                  'split': (r.get('split'), 'development'), 'run_digest': (r.get('run_digest'), None),
                  'window': (r.get('window'), {'start': dev['start'], 'end': dev['end']})}
        for name, (got, exp) in checks.items():
            if got != exp:
                raise LedgerMismatch(f'ledger row {k} ({d.get("id")}) {name} is {got!r}, expected {exp!r}')
        prev = sha(ledger_line(r))
    fam = [r for r in registry if r.get('kind') == 'family' and r.get('family') == LEDGER_FAMILY]
    if len(fam) != 1 or fam[0]['ref'].get('genesis') != sha(ledger_line(rows[0])) or fam[0]['ref'].get('parent') is not None:
        raise LedgerMismatch('REGISTRY family line missing, duplicated or not bound to the ledger genesis')


def verify_code(p: dict) -> dict:
    """identity block: registry entry, function-level source hashes, live mgmt == S3, S2 == S3 minus the pyramid."""
    i = p['identity']
    st = ST.STRATEGIES.get(i['registry_key'])
    if st is None or st['fn'] is not getattr(ST, i['fn'], None) or st['sides'] != i['registry_sides'] \
            or st.get('tf', '4h') != i['tf'] or st.get('params'):
        raise IdentityMismatch('strategies registry entry differs from the signed identity')
    got = {'fn_source_sha256': src_sha(ST.sig_breakout_pyramid), 'indicators_source_sha256': src_sha(ST.indicators),
           'ema_source_sha256': src_sha(ST.ema)}
    for k, v in got.items():
        if v != i[k]:
            raise IdentityMismatch(f'{k} {v} != signed {i[k]}')
    live = ST.merge_mgmt(i['registry_key'], {})
    if live != i['live_mgmt'] or p['stages']['S3']['mgmt'] != live:
        raise IdentityMismatch('live mgmt / S3 mgmt differ from the signed identity')
    if p['stages']['S2']['mgmt'] != {k: v for k, v in live.items() if k != 'pyramid'}:
        raise IdentityMismatch('S2 is not S3 without the pyramid block')
    return got


def verify_costs(p: dict, repo: str = REPO) -> None:
    got = file_sha(_p(repo, COSTS_REL))
    if got != p['source']['costs_sha256']:
        raise CostHashMismatch(f'costs.py sha256 {got} != signed {p["source"]["costs_sha256"]}')
    u = p['universe']
    if file_sha(_p(repo, UNIVERSE_REL)) != p['source']['universe_sha256']:
        raise CostHashMismatch('universe artifact sha256 differs from the signed source')
    if sha(json.dumps(sorted(u['symbols']), separators=(',', ':')).encode()) != u['symbols_sha256'] \
            or len(u['symbols']) != u['size']:
        raise IdentityMismatch('FIXED-17 symbol list hash differs')


def verify_manifest(p: dict, repo: str = REPO, loader=None) -> None:
    """The archive manifest (file inventory metadata, not market data) must carry the signed digest."""
    m = (loader or M.load)(_p(repo, tuple(p['source']['manifest'].split('/'))))
    got = M.digest_of(m)
    if got != p['source']['manifest_digest'] or m.get('digest', got) != got:
        raise ManifestMismatch(f'manifest digest {got} != signed {p["source"]["manifest_digest"]}')


def code_identity(repo: str = REPO, git=None) -> dict:
    """git HEAD, a dirty flag (any porcelain status line) and the sha256 of every module this runner executes.
    `git(*args) -> stdout` is injectable so tests can present a clean or dirty tree without touching git."""
    def real_git(*a):
        try:
            return subprocess.run(['git', '-C', repo, *a], capture_output=True, text=True, check=True).stdout
        except (OSError, subprocess.CalledProcessError) as e:
            raise PreflightError(f'cannot read the code identity from git ({e})') from None
    g = git or real_git
    return {'commit': g('rev-parse', 'HEAD').strip(), 'dirty': bool(g('status', '--porcelain').strip()),
            'modules': {m: file_sha(os.path.join(repo, *m.split('/'))) for m in CODE_MODULES},
            'path_points_sha256': src_sha(BT.path_points)}


@dataclass(frozen=True)
class Preflight:
    prereg: dict
    prereg_sha256: str
    code: dict
    sources: dict


def preflight(repo: str = REPO, *, git=None, manifest_check=None, allow_dirty_test_only: bool = False) -> Preflight:
    """Fail closed on every identity binding before any bar is touched."""
    p, raw = load_prereg(repo)
    psha = sha(raw)
    verify_prereg(p)
    verify_ledger(p, psha, load_jsonl(_p(repo, LEDGER_REL)), load_jsonl(_p(repo, REGISTRY_REL)))
    srcs = verify_code(p)
    verify_costs(p, repo)
    (manifest_check or verify_manifest)(p, repo)
    code = code_identity(repo, git)
    if not code['commit'] or len(code['commit']) != 40:
        raise PreflightError('code identity has no commit')
    if code['dirty'] and not allow_dirty_test_only:
        raise DirtyTree('the code tree is dirty; commit first (a real run binds a clean commit)')
    return Preflight(p, psha, code, srcs)


# ------------------------------------------------------------------ windows / stages
@dataclass(frozen=True)
class Windows:
    dev_start: int
    last_entry: int                      # no entry fill at or after this
    sealed: int                          # nothing at or after this is read
    slices: tuple                        # ((start, end), ...) by entry fill
    warmup_bars: int = 240

    @staticmethod
    def of(p: dict) -> 'Windows':
        w = p['windows']
        sealed = iso_ms(w['sealed_cutoff'])
        lec = w['last_entry_cutoff']
        last = sealed - lec['bars'] * lec['bar_hours'] * HOUR
        if last != iso_ms(lec['at']) or iso_ms(w['development']['end']) != sealed:
            raise PreregInvalid('last-entry cutoff / development end inconsistent with the sealed cutoff')
        sl = tuple((iso_ms(s['start']), iso_ms(s['end'])) for s in w['slices'])
        if sl[0][0] != iso_ms(w['development']['start']) or sl[-1][1] != sealed or \
                any(a[1] != b[0] for a, b in zip(sl, sl[1:])):
            raise PreregInvalid('slices are not contiguous over the development window')
        return Windows(sl[0][0], last, sealed, sl, 240)

    def slice_of(self, t: int):
        for k, (a, b) in enumerate(self.slices, 1):
            if a <= t < b:
                return k
        return None

    def entry_ok(self, t: int) -> bool:
        return self.dev_start <= t < self.last_entry


def stage_mgmt(p: dict, stage: str) -> dict:
    if stage not in STAGE_IDS:
        raise LedgerMismatch(f'stage {stage!r} is not one of the frozen ledger ids')
    return dict(p['stages'][stage]['mgmt'])


# ------------------------------------------------------------------ symbol data
@dataclass
class SymbolData:
    sym: str
    t: list
    o: list
    h: list
    l: list
    c: list
    atr: list                            # Wilder ATR14 of closed bar i
    le: list                             # long entry signal at the close of bar i
    elig: list                           # >= warmup contiguous closed bars ending at i
    slip: list                           # charged bps for a market fill at the open of bar i (nan: no 15-bar history)
    funding: list = field(default_factory=list)      # [(time_ms, rate, mark_px)] sorted
    _ft: list = field(default_factory=list)

    def __post_init__(self):
        self.funding = sorted(self.funding)
        self._ft = [e[0] for e in self.funding]

    def contiguous(self, j: int) -> bool:
        return 0 < j < len(self.t) and self.t[j] == self.t[j - 1] + TF

    def events(self, t0: int):
        a, b = bisect.bisect_left(self._ft, t0), bisect.bisect_left(self._ft, t0 + TF)
        return self.funding[a:b]


class _Bar:
    __slots__ = ('high', 'low', 'close')

    def __init__(self, h, l, c):
        self.high, self.low, self.close = h, l, c


def prepare_symbol(sym: str, bars, windows: Windows, funding=()) -> SymbolData:
    """bars: [(open_ms, o, h, l, c, v)] closed 4h bars. Indicators and signals are computed per contiguous segment
    (no forward fill across a gap); a bar at or after the sealed cutoff is refused, never read."""
    import pandas as pd
    bars = sorted(bars)
    if any(b[0] >= windows.sealed for b in bars) or any(e[0] >= windows.sealed for e in funding):
        raise HoldoutRefused(f'{sym}: input reaches the sealed cutoff')
    if any(b[0] % TF for b in bars) or len({b[0] for b in bars}) != len(bars):
        raise RunnerError(f'{sym}: bars are not unique 4h opens')
    t = [int(b[0]) for b in bars]
    o, h, l, c = ([float(b[k]) for b in bars] for k in (1, 2, 3, 4))
    atr, le, elig, slip = [math.nan] * len(t), [False] * len(t), [False] * len(t), [math.nan] * len(t)
    seg = 0
    for j in range(len(t) + 1):
        if j == len(t) or (j and t[j] != t[j - 1] + TF):
            df = pd.DataFrame({'t': t[seg:j], 'o': o[seg:j], 'h': h[seg:j], 'l': l[seg:j], 'c': c[seg:j],
                               'v': [float(b[5]) for b in bars[seg:j]]})
            if len(df):
                d = ST.indicators(df)
                s = ST.sig_breakout_pyramid(d, {}, {})
                for k in range(len(df)):
                    atr[seg + k] = float(d['atr'].iloc[k])
                    le[seg + k] = bool(s['le'].iloc[k])
                    elig[seg + k] = k + 1 >= windows.warmup_bars
                    if k >= C.SLIP_BARS:
                        win = [_Bar(h[x], l[x], c[x]) for x in range(seg + k - C.SLIP_BARS, seg + k)]
                        slip[seg + k] = max(C.slip_bps(win, SLIP_C_CRYPTO), SLIP_FLOOR_BPS)
            seg = j
    return SymbolData(sym, t, o, h, l, c, atr, le, elig, slip, list(funding))


# ------------------------------------------------------------------ position + bar phases
class Pos:
    """One long episode. Levels (stop, add, target) are set from the fill price of `scen` (contract fills.stop_reference);
    every fill is recorded with its reference price, slip bps and gap flag so a stress row can reprice it."""

    def __init__(self, sd: SymbolData, f: int, q0: float, dist: float, mgmt: dict, scen: C.Scenario, tick=None):
        if not (dist > 0) or not math.isfinite(dist):
            raise Incomplete('incomplete.stop', sd.t[f])
        if not math.isfinite(sd.slip[f]):
            raise Incomplete('incomplete.slip_history', sd.t[f])
        self.sd, self.f, self.q0, self.R, self.m, self.scen, self.tick = sd, f, q0, dist, mgmt, scen, tick
        fill = self.px(sd.o[f], 'buy', sd.slip[f])
        self.e0 = self.best = fill
        self.stop = fill - dist
        self.qty, self.cost, self.adds, self.exit_due = q0, q0 * fill, 0, False
        py = mgmt.get('pyramid')
        self.next_add = fill + py['step_r'] * dist if py else math.inf
        self.fills = [dict(kind='entry', j=f, ref=sd.o[f], bps=sd.slip[f], gap=False, qty=q0, why='entry')]
        self.funding = []
        self.exit = None                                       # (j, why, free_from)

    def px(self, ref, action, bps, gap=False):
        return C.fill_price(ref, action, bps, self.scen, gap=gap, tick=self.tick)

    def close(self, j, ref, why, gap, free_from):
        sd = self.sd
        if not math.isfinite(sd.slip[j]):
            raise Incomplete('incomplete.slip_history', sd.t[j])
        self.fills.append(dict(kind='exit', j=j, ref=ref, bps=sd.slip[j], gap=gap, qty=self.qty, why=why))
        self.exit = (j, why, free_from)
        return self.px(ref, 'sell', sd.slip[j], gap)


def phase1(pos: Pos, j: int):
    """Open-known events at the open of bar j: gap stop (open at / through the stop, filled at the open), then the
    scheduled next-open time exit. Returns the exit fill price or None. Frees the symbol for bar j's own open entries."""
    o = pos.sd.o[j]
    if o <= pos.stop:
        return pos.close(j, o, 'stop_gap', True, j)
    if pos.exit_due:
        return pos.close(j, o, 'time', False, j)
    return None


def charge_funding(pos: Pos, j: int) -> None:
    """Settled events in [open_j, open_j + 4h) on the quantity held at the open (entry <= T < exit)."""
    for t, rate, mark in pos.sd.events(pos.sd.t[j]):
        pos.funding.append((t, rate, mark, pos.qty))


def walk(pos: Pos, j: int, add_gate):
    """Phase 3: backtest.walk_path (FBL-BT01) for one long position on bar j along backtest.path_points: adds fire in
    path order before the stop, stop-first suppresses the target (never the adds) when the open's stop is inside the
    range, a raised stop is live for later legs, the chandelier ratchets after each leg with the last CLOSED bar's ATR.
    `add_gate(pos, ref, qty) -> (ok, qty)`. Returns the exit fill price or None."""
    sd, m = pos.sd, pos.m
    o, h, l, c = sd.o[j], sd.h[j], sd.l[j], sd.c[j]
    atr = sd.atr[j - 1]
    py, tp_r, trail = m.get('pyramid'), m.get('tp_r'), m.get('trail_atr')
    stop_in_candle = l <= pos.stop
    blocked_add = False
    pts = BT.path_points(o, h, l, c, 1)
    for k in range(len(pts)):
        a_, b_ = pts[max(0, k - 1)], pts[k]
        mv = b_ - a_
        if k and mv < 0 and b_ <= pos.stop:                                    # adverse leg: the stop
            ref = pos.stop if a_ >= pos.stop else a_
            return pos.close(j, ref, 'stop', ref != pos.stop, j + 1)
        if k == 0 or mv > 0:                                                   # favourable leg: adds / target in price order
            while True:
                cand = []
                if py and not blocked_add and pos.adds < py['n'] and b_ >= pos.next_add:
                    cand.append((pos.next_add, 1, 'add'))
                if tp_r and not stop_in_candle and b_ >= pos.e0 + tp_r * pos.R:
                    cand.append((pos.e0 + tp_r * pos.R, 4, 'tp'))
                if not cand:
                    break
                lv, _, ev = min(cand)
                if ev == 'tp':
                    return pos.close(j, lv, 'tp', False, j + 1)
                ref = max(lv, a_)
                ok, q = add_gate(pos, ref, pos.q0 * py['frac'])
                if not ok:
                    blocked_add = True
                    continue
                if not math.isfinite(sd.slip[j]):
                    raise Incomplete('incomplete.slip_history', sd.t[j])
                fill = pos.px(ref, 'buy', sd.slip[j], ref != lv)
                pos.fills.append(dict(kind='add', j=j, ref=ref, bps=sd.slip[j], gap=ref != lv, qty=q, why='add'))
                pos.qty += q
                pos.cost += q * fill
                pos.adds += 1
                pos.next_add += py['step_r'] * pos.R
        pos.best = max(pos.best, b_)
        if k and trail and math.isfinite(atr):
            pos.stop = max(pos.stop, pos.best - trail * atr)
    return None


def phase4(pos: Pos, j: int) -> None:
    """Close of bar j: schedule the time-cap exit for the next open (42 bars held incl. the fill bar)."""
    mb = pos.m.get('max_bars')
    if mb and j - pos.f + 1 >= mb:
        pos.exit_due = True


def _unit_gate(pos, ref, q):
    return True, q


# ------------------------------------------------------------------ unit replay + pricing
def replay_unit(sd: SymbolData, f: int, mgmt: dict) -> Pos:
    """Quantity-1 replay of the episode filled at the open of bar f (signal at the close of f-1), base-path levels.
    Raises Incomplete on a gap (no forward fill) or when the episode is still open at the end of the readable data
    (boundary INCOMPLETE: no price at or after the sealed cutoff is read to close it)."""
    if not sd.contiguous(f):
        raise Incomplete('incomplete.gap', sd.t[f] if f < len(sd.t) else None)
    pos = Pos(sd, f, 1.0, mgmt['stop_atr'] * sd.atr[f - 1], mgmt, BASE)
    j = f
    while True:
        if j > f:
            if j >= len(sd.t):
                raise Incomplete('incomplete.boundary', sd.t[-1] + TF)
            if not sd.contiguous(j):
                raise Incomplete('incomplete.gap', sd.t[j])
            if phase1(pos, j) is not None:
                return pos
        charge_funding(pos, j)
        if walk(pos, j, _unit_gate) is not None:
            return pos
        phase4(pos, j)
        j += 1


def row_scenario(row: str) -> C.Scenario:
    if row not in ROWS:
        raise RunnerError(f'unknown cost row {row!r}')
    mult, _ = ROWS[row]
    return C.Scenario(row, fee_mult=mult, slip_mult=mult)


def funding_paid(rate: float, qty: float, mark: float, row: str) -> float:
    """Paid (+) / received (-) by a long at one event; stress multiplies paid x m and divides received by m."""
    mult, mode = ROWS[row]
    if mode == 'zero':
        return 0.0
    c = C.funding_cost('long', qty, [(0, rate, mark)], BASE)
    return c * mult if c > 0 else c / mult


def price_episode(pos: Pos, row: str) -> dict:
    """Reprice the same episode (same fills, same quantities) under a cost row: net = gross + fill_adj - fees + funding."""
    scen = row_scenario(row)
    gross = fill_pnl = fees = 0.0
    for x in pos.fills:
        action = 'sell' if x['kind'] == 'exit' else 'buy'
        sgn = 1.0 if action == 'sell' else -1.0
        fp = C.fill_price(x['ref'], action, x['bps'], scen, gap=x['gap'], tick=pos.tick)
        gross += sgn * x['ref'] * x['qty']
        fill_pnl += sgn * fp * x['qty']
        fees += C.fee(fp * x['qty'], 'taker', C.CRYPTO, scen)
    funding = -math.fsum(funding_paid(rate, q, mark, row) for _, rate, mark, q in pos.funding)
    net = fill_pnl - fees + funding
    r_unit = pos.R * pos.q0
    return {'gross': gross, 'fill_adj': fill_pnl - gross, 'fees': fees, 'funding': funding, 'net': net,
            'net_r': net / r_unit}


# ------------------------------------------------------------------ entry set E + paired unit replays
def entry_set(data: dict, windows: Windows, mgmt_s1: dict) -> dict:
    """E = every S1 entry under the fill-time re-arm rule, symbol ascending. A signal at the close of i fills at the
    open of i+1 when i is eligible, i+1 is contiguous and inside [dev start, last-entry cutoff), and the previous S1
    episode freed the symbol at or before that open (phase-1 exits free it for the same open)."""
    eps, incomplete, unfilled = [], [], 0
    for sym in sorted(data):
        sd = data[sym]
        free = 0
        for i in range(len(sd.t)):
            if not (sd.le[i] and sd.elig[i]):
                continue
            f = i + 1
            if f >= len(sd.t) or not sd.contiguous(f):
                unfilled += 1 if windows.entry_ok(sd.t[i] + TF) else 0
                continue
            if not windows.entry_ok(sd.t[f]) or f < free:
                continue
            try:
                pos = replay_unit(sd, f, mgmt_s1)
            except Incomplete as e:
                incomplete.append({'sym': sym, 'f': f, 't': sd.t[f], 'reason': e.reason,
                                   'slice': windows.slice_of(sd.t[f])})
                free = math.inf                                   # exit unknown: the symbol never re-arms (fail closed)
                continue
            eps.append({'sym': sym, 'f': f, 't': sd.t[f], 'slice': windows.slice_of(sd.t[f]), 'free': pos.exit[2]})
            free = pos.exit[2]
    return {'episodes': eps, 'incomplete': incomplete, 'unfilled_gap': unfilled}


def unit_replays(data: dict, E: dict, p: dict, stage: str, rows=tuple(ROWS)) -> dict:
    """Paired replay of every e in E under one stage (overlap allowed here and only here)."""
    mg = stage_mgmt(p, stage)
    out, inc = [], []
    for e in E['episodes']:
        try:
            pos = replay_unit(data[e['sym']], e['f'], mg)
        except Incomplete as x:
            inc.append({**e, 'reason': x.reason})
            continue
        out.append({**e, 'exit_j': pos.exit[0], 'why': pos.exit[1], 'adds': pos.adds,
                    'rows': {r: price_episode(pos, r) for r in rows}})
    return {'stage': stage, 'episodes': out, 'incomplete': inc}


def summarize(rep: dict, windows: Windows) -> dict:
    """Descriptive counts and means only (no gate, no verdict): per cost row, overall and per entry slice."""
    out = {}
    for r in ROWS:
        xs = [(e['slice'], e['rows'][r]['net_r']) for e in rep['episodes'] if r in e['rows']]
        per = {}
        for k in range(1, len(windows.slices) + 1):
            v = [x for s, x in xs if s == k]
            per[str(k)] = {'n': len(v), 'mean_net_r': math.fsum(v) / len(v) if v else None}
        out[r] = {'n': len(xs), 'mean_net_r': math.fsum(x for _, x in xs) / len(xs) if xs else None, 'slices': per}
    return {'stage': rep['stage'], 'rows': out, 'incomplete': len(rep['incomplete'])}


# ------------------------------------------------------------------ book (account replay of one stage)
def run_book(data: dict, p: dict, stage: str, windows: Windows, rules, *, risk: float = 0.01,
             row: str = 'base_x1') -> dict:
    """Causal four-phase account replay from the raw stage signals (never from E). `rules(sym, t_ms)` -> the
    point-in-time exchange rule dict (step / min_qty / min_notional[, tick, max_qty]) or None (refused, counted)."""
    mg = stage_mgmt(p, stage)
    scen = row_scenario(row)
    syms = sorted(data)
    idx = {s: {t: j for j, t in enumerate(data[s].t)} for s in syms}
    times = sorted({t for s in syms for t in data[s].t})
    cash, pos, pend = START_EQUITY, {}, {}
    skips = {k: 0 for k in ('skipped_open_symbol', 'skipped_max_open', 'skipped_gross_cap', 'skipped_add_gross_cap',
                            'incomplete_fill_gap')}
    refusals, trades, incomplete, curve, events = {}, [], [], [], []
    last_px = {}

    def fee(notional):
        return C.fee(notional, 'taker', C.CRYPTO, scen)

    def finish(s, fill, j):
        nonlocal cash
        q = pos[s]
        out_fee = fee(q.qty * fill)
        cash += q.qty * fill - q.cost - out_fee
        trades.append({'sym': s, 't_entry': data[s].t[q.f], 't_exit': data[s].t[j], 'why': q.exit[1], 'adds': q.adds,
                       'slice': windows.slice_of(data[s].t[q.f]), 'pnl': q.qty * fill - q.cost - q.fee_paid - out_fee
                       - q.funding_paid, 'r_usd': q.R * q.q0})
        events.append((data[s].t[j], 1 if q.exit[1] in ('stop_gap', 'time') else 3, s, 'exit:' + q.exit[1]))
        del pos[s]

    def marks(T, px_of):
        upl = gross = 0.0
        for s, q in pos.items():
            x = px_of(s)
            upl += q.qty * x - q.cost
            gross += q.qty * x
        return cash + upl, gross

    for T in times:
        bar = {s: idx[s].get(T) for s in syms}
        # ---- phase 1: open-known events, symbol ascending
        for s in sorted(pos):
            j = bar[s]
            if j is None or not data[s].contiguous(j):
                incomplete.append({'sym': s, 't_entry': data[s].t[pos[s].f], 'reason': 'incomplete.gap'})
                del pos[s]                                   # exit unknown: removed from the book, counted (data gate)
                continue
            try:
                fill = phase1(pos[s], j)
            except Incomplete as e:
                incomplete.append({'sym': s, 't_entry': data[s].t[pos[s].f], 'reason': e.reason})
                del pos[s]
                continue
            if fill is not None:
                finish(s, fill, j)
        open_px = lambda s: data[s].o[bar[s]] if bar[s] is not None else last_px[s]       # noqa: E731
        # ---- phase 2: admit pending next-open entries on the phase-1 state, symbol ascending
        eq_open, gross_open = marks(T, open_px)
        for s in sorted(pend):
            i = pend[s]
            j = bar[s]
            sd = data[s]
            if j is None or j != i + 1 or not sd.contiguous(j):
                skips['incomplete_fill_gap'] += 1
                continue
            if s in pos:
                skips['skipped_open_symbol'] += 1
                continue
            if len(pos) >= MAX_OPEN_LOTS:
                skips['skipped_max_open'] += 1
                continue
            rule = rules(s, T)
            dist = mg['stop_atr'] * sd.atr[i]
            ref_fill = C.fill_price(sd.o[j], 'buy', sd.slip[j], scen, tick=(rule or {}).get('tick')) \
                if math.isfinite(sd.slip[j]) else math.nan
            if not (dist > 0) or not math.isfinite(ref_fill):
                skips['incomplete_fill_gap'] += 1
                continue
            qty_raw = eq_open * risk / dist
            d = F.size_check(qty_raw, qty_raw, ref_fill, rule)
            if d['ok'] is not True:
                code = 'rules_uncovered' if rule is None else d['code']
                refusals[code] = refusals.get(code, 0) + 1
                continue
            if gross_open + d['qty'] * ref_fill > GROSS_CAP * eq_open:
                skips['skipped_gross_cap'] += 1
                continue
            q = Pos(sd, j, d['qty'], dist, mg, scen, tick=(rule or {}).get('tick'))
            q.fee_paid = fee(q.cost)
            q.funding_paid = 0.0
            q.rule = rule
            cash -= q.fee_paid
            pos[s] = q
            gross_open += q.qty * q.e0
            events.append((T, 2, s, 'entry'))
        pend = {}
        # funding at the open on the quantity held after phases 1-2
        for s in sorted(pos):
            q = pos[s]
            n0 = len(q.funding)
            charge_funding(q, bar[s])
            paid = math.fsum(funding_paid(rate, qq, mark, row) for _, rate, mark, qq in q.funding[n0:])
            q.funding_paid += paid
            cash -= paid

        # ---- phase 3: intrabar paths, symbol ascending (including phase-2 admissions)
        def add_gate(q, ref, qty, _T=T):
            d = F.size_check(qty, qty, ref, q.rule)
            if d['ok'] is not True:
                refusals['add_' + d['code']] = refusals.get('add_' + d['code'], 0) + 1
                return False, 0.0
            eq, gross = marks(_T, lambda s: ref if pos[s] is q else open_px(s))
            if gross + d['qty'] * ref > GROSS_CAP * eq:
                skips['skipped_add_gross_cap'] += 1
                return False, 0.0
            return True, d['qty']

        for s in sorted(pos):
            q = pos[s]
            j = bar[s]
            na = len(q.fills)
            try:
                fill = walk(q, j, add_gate)
            except Incomplete as e:
                incomplete.append({'sym': s, 't_entry': data[s].t[q.f], 'reason': e.reason})
                del pos[s]
                continue
            for x in q.fills[na:]:
                if x['kind'] == 'add':
                    a_fee = fee(q.px(x['ref'], 'buy', x['bps'], x['gap']) * x['qty'])
                    q.fee_paid += a_fee
                    cash -= a_fee
                    events.append((T, 3, s, 'add'))
            if fill is not None:
                finish(s, fill, j)
        # ---- phase 4: close signals -> pending for the next open; time caps scheduled
        for s in syms:
            j = bar[s]
            if j is None:
                continue
            last_px[s] = data[s].c[j]
            if s in pos:
                phase4(pos[s], j)
            sd = data[s]
            if sd.le[j] and sd.elig[j] and windows.entry_ok(T + TF):
                pend[s] = j
        curve.append((T, marks(T, lambda s: last_px[s])[0]))
    for s in sorted(pos):                                       # boundary INCOMPLETE: never closed with a later price
        incomplete.append({'sym': s, 't_entry': data[s].t[pos[s].f], 'reason': 'incomplete.boundary'})
    peak, dd = START_EQUITY, 0.0
    for _, e in curve:
        peak = max(peak, e)
        dd = max(dd, 1 - e / peak if peak > 0 else 0.0)
    return {'stage': stage, 'row': row, 'risk': risk, 'trades': trades, 'skips': skips, 'refusals': refusals,
            'incomplete': incomplete, 'curve': curve, 'events': events, 'max_equity_dd': dd,
            'end_equity_closed': cash}


# ------------------------------------------------------------------ B0 random-entry baseline
def b0_candidates(sd: SymbolData, windows: Windows, slice_no: int) -> list:
    a, b = windows.slices[slice_no - 1]
    return [f for f in range(1, len(sd.t)) if sd.contiguous(f) and sd.elig[f - 1] and a <= sd.t[f] < b
            and windows.entry_ok(sd.t[f])]


def b0_sample(data: dict, E: dict, windows: Windows, mgmt_s1: dict, *, replicates: int, base_seed: int,
              _cache=None) -> list:
    """Per replicate r: random.Random(base_seed + r); symbols then slices ascending; eligible entry bars drawn in a
    seeded random order WITHOUT replacement; a draw is accepted only if its S1 episode [fill, free) does not overlap
    an accepted one of that symbol (fill-time re-arm rule); stop at the S1 count of that symbol and slice. A draw whose
    S1 episode is INCOMPLETE is not accepted. Exhaustion -> BaselineIncomplete (the B0 gate fails closed)."""
    need = {}
    for e in E['episodes']:
        need[(e['sym'], e['slice'])] = need.get((e['sym'], e['slice']), 0) + 1
    cache = {} if _cache is None else _cache
    cands = {(s, k): b0_candidates(data[s], windows, k) for s in sorted(data) for k in range(1, len(windows.slices) + 1)}
    out = []
    for r in range(replicates):
        rng = random.Random(base_seed + r)
        rep = []
        for s in sorted(data):
            acc = []
            for k in range(1, len(windows.slices) + 1):
                n = need.get((s, k), 0)
                if not n:
                    continue
                got = 0
                for f in rng.sample(cands[(s, k)], len(cands[(s, k)])):
                    if (s, f) not in cache:
                        try:
                            cache[(s, f)] = replay_unit(data[s], f, mgmt_s1)
                        except Incomplete:
                            cache[(s, f)] = None
                    pos = cache[(s, f)]
                    if pos is None:
                        continue
                    lo, hi = f, pos.exit[2]
                    if any(lo < b and a < hi for a, b in acc):
                        continue
                    acc.append((lo, hi))
                    rep.append({'sym': s, 'f': f, 'slice': k})
                    got += 1
                    if got == n:
                        break
                if got < n:
                    raise BaselineIncomplete(s, k, r, got, n)
        out.append(rep)
    return out


def b0_means(data: dict, reps: list, mgmt_s1: dict, row: str = 'base_x1', _cache=None) -> list:
    cache = {} if _cache is None else _cache
    res = []
    for rep in reps:
        xs = []
        for e in rep:
            key = (e['sym'], e['f'])
            if cache.get(key) is None:
                cache[key] = replay_unit(data[e['sym']], e['f'], mgmt_s1)
            xs.append(price_episode(cache[key], row)['net_r'])
        res.append(math.fsum(xs) / len(xs) if xs else None)
    return res


# ------------------------------------------------------------------ run (in memory only) + guarded write
def run(data: dict, pf: Preflight, *, windows: Windows | None = None, rules=None, b0_replicates: int = 0,
        book_stage: str | None = None) -> dict:
    """Slice-1 orchestration on caller-supplied SymbolData: E, the four paired unit replays with descriptive
    summaries, optional B0 draws and an optional book replay. Returns a dict; writes nothing; carries no verdict."""
    if not isinstance(pf, Preflight):
        raise PreflightError('run() needs a passed Preflight')
    p = pf.prereg
    w = windows or Windows.of(p)
    universe = set(p['universe']['symbols'])
    bad = sorted(set(data) - universe)
    if bad:
        raise IdentityMismatch(f'symbols outside the FIXED-17 book: {bad}')
    for sd in data.values():
        if sd.t and sd.t[-1] >= w.sealed:
            raise HoldoutRefused(f'{sd.sym}: bar at or after the sealed cutoff')
    s1 = stage_mgmt(p, 'S1')
    E = entry_set(data, w, s1)
    stages = {s: unit_replays(data, E, p, s) for s in STAGE_IDS}
    out = {'candidate_id': CANDIDATE, 'prereg_sha256': pf.prereg_sha256, 'code': pf.code, 'evaluated': False,
           'E': {'n': len(E['episodes']), 'incomplete': len(E['incomplete']), 'unfilled_gap': E['unfilled_gap']},
           'stages': {s: summarize(stages[s], w) for s in STAGE_IDS}}
    if b0_replicates:
        b0 = next(b for b in p['baselines'] if b['id'] == 'B0')
        try:
            reps = b0_sample(data, E, w, s1, replicates=b0_replicates, base_seed=b0['seed']['base'])
            out['B0'] = {'status': 'OK', 'means_base': b0_means(data, reps, s1)}
        except BaselineIncomplete as e:
            out['B0'] = {'status': 'INCOMPLETE', 'reason': str(e)}
    if book_stage:
        if rules is None:
            raise RunnerError('the book needs point-in-time exchange rules')
        bk = run_book(data, p, book_stage, w, rules)
        out['book'] = {k: bk[k] for k in ('stage', 'row', 'risk', 'skips', 'refusals', 'max_equity_dd')} | \
            {'trades': len(bk['trades']), 'incomplete': len(bk['incomplete'])}
    return out


@dataclass(frozen=True)
class EvalAuthorization:
    """Issued only by Integration (codex) for an authorized evaluation; nothing in this module constructs one."""
    issuer: str
    comment_id: str
    prereg_sha256: str


def write_result(result: dict, path: str, *, authorization: EvalAuthorization | None = None) -> str:
    """The only write path. Refuses without an Integration EvalAuthorization bound to this signed contract, and
    never writes a verdict, a ledger row or a registry line."""
    if not isinstance(authorization, EvalAuthorization):
        raise WriteRefused('no Integration evaluation authorization: results stay in memory')
    if authorization.issuer != 'codex' or not str(authorization.comment_id).isdigit() or \
            authorization.prereg_sha256 != result.get('prereg_sha256'):
        raise WriteRefused('authorization is not an Integration token for this signed contract')
    ap = os.path.abspath(path)
    ledger_dir = os.path.abspath(os.path.join(REPO, *LEDGER_REL[:-1]))
    if os.path.commonpath([ap, ledger_dir]) == ledger_dir or 'verdict' in os.path.basename(ap).lower() \
            or 'verdict' in result or 'REGISTRY' in os.path.basename(ap):
        raise WriteRefused('verdict / ledger / registry writes are Integration-only')
    with open(ap, 'xb') as f:
        f.write(canon(result))
    return ap
