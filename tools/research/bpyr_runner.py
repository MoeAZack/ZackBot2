"""breakout_pyramid_falsify.v1: deterministic code-only research runner (slices 1-3; no evaluation is authorized).

Authoritative spec: research_evidence/prereg/breakout_pyramid_falsify.v1.json (signed; read by this module, nothing
else is policy).

  preflight   the signed contract (bytes hash == the `prereg_sha256` every frozen ledger row records), the six frozen
              ledger IDs exactly (S1, S2, S3, S1-T, B0, B2: kinds, gated flags, hash chain, REGISTRY genesis), the pinned
              function-level source hashes (sig fn / indicators / ema) + live mgmt, the costs.py and universe artifact
              hashes, the archive manifest digest, and the code identity (git commit + dirty flag + module hashes).
              Every mismatch raises a typed PreflightError subclass; a dirty tree refuses unless the explicit test flag.
  engine      the causal four-phase bar loop (book.bar_phases), one long position per symbol, the backtest.py
              path_points convention and the walk_path FBL-BT01 rules for every intrabar event.
  unit replay entry set E (S1 entries, fill-time re-arm) and the paired per-stage quantity-1 replays S1 / S1-T / S2 / S3.
  book        the account replay of one stage from the raw signals (admission on phase-1 state only, skips counted).
  B0 / B2     the seeded random-entry sampler (without replacement, no overlap under the fill-time re-arm rule,
              per symbol / slice count match, fail-closed BaselineIncomplete); B2 = report-only trend-filtered draws.
  statistics  bootstrap per-episode LCB (8-ISO-week moving blocks, multiplicity weights), PF, slices, remove-best-symbol,
              concentration, activity / data gates, D3 paired increment, B0 95th-percentile gate, DD tiers / ceilings /
              underwater / Calmar, sizing replays, folds; H1 -> H2 -> H3 credit order. decide() is pure and never called
              by run(); nothing writes a verdict.
  adapter     load_symbol(): edge05-compatible source (Bar / Funding rows) through edge05 Guard (sealed-cutoff refusal
              before and after every read), funding cadence proof (costs.check_funding_sequence) as proven runs,
              funding-mark-proxy-v1 from the 1h mark bar ending at T, point-in-time rules via rules_of(PitRules).
  execute     authorized only: run.json identity + hash-chained, fsync'd, resumable symbols.jsonl, mandatory
              --max-rss-mb; returns the summary in memory.
  gate_inputs the pure decide()-shaped dict (data incl. the credited stage's and book's own data state, activity, B0,
              H1-H3, book DD tier, tails, causality / accounting checks); run() assembles it, never evaluates it.
  CLI         main(): the real edge05 ArchiveSource (signed manifest index) and PitRules (rules artifact bound to its
              rules-source manifest), every input file checked against an expected sha256; --max-rss-mb mandatory; a
              dirty tree refused (no override flag). Without --authorization it runs preflight / identity /
              metadata-coverage checks only, opens no archive, writes nothing and exits. --authorization is checked
              by load_authorization(), the committed verifier (nothing is imported by name): a canonical
              zb-eval-authorization/1 artifact under research_evidence/authorizations/, byte-identical to HEAD,
              hash-pinned by --expect-authorization-sha256 and bound to the signed contract and the code commit.

Disclosures (Codex 6478332951; also emitted in every result dict and in run.json as `disclosures`):
  1. B0 is not path-identical to E: E (and the book) permanently stop re-arming a symbol after an INCOMPLETE episode,
     while B0 discards an INCOMPLETE random draw and keeps sampling.
  2. Later-in-bar (1h / 2h) funding is charged conservatively because its order against intrabar adds / exits is
     unknowable: paid on the larger quantity even after an intrabar exit, received on the smaller (0 after an exit).
     Events at the bar open (the 8h grid) are exact.

Declared, contract-sanctioned differences from backtest.py are listed in PARITY_DECLARED; tests/test_bpyr_runner.py
proves fill-for-fill parity with backtest.run on synthetic bars outside those cases.

Nothing here writes a verdict or spends a ledger row: run() returns an in-memory dict. write_result() and execute()
refuse unless an EvalAuthorization bound to the signed contract hash is passed, and never write under the ledger
directory. No network, no market-data path, no wall clock in any result byte.
"""
from __future__ import annotations

import bisect
import hashlib
import inspect
import json
import math
import os
import random
import re
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
                'tools/research/manifest.py', 'tools/research/edge05_runner.py', 'tools/research/edge05_coverage.py',
                'tools/research/splits.py', 'tools/research/bpyr_runner.py')
HOUR = 3_600_000
TF = 4 * HOUR
DAY_MS = 24 * HOUR
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


class AuthorizationRefused(WriteRefused):
    """load_authorization(): the artifact is not a committed, hash-pinned Integration authorization for this run."""


class SourceUncovered(RunnerError):
    """A source range the archive cannot prove (edge05 Incomplete), e.g. the straddle month without an artifact."""


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
    funding: list = field(default_factory=list)      # [(time_ms, rate, mark_px)] sorted; mark nan = no mark bar
    trend: list | None = None                         # EMA50 > EMA200 at the close of bar i (B2 draw filter)
    fund_runs: list | None = None                     # proven-complete funding spans [(lo, hi)]; None = unchecked
    _ft: list = field(default_factory=list)

    def __post_init__(self):
        self.funding = sorted(self.funding)
        self._ft = [e[0] for e in self.funding]

    def contiguous(self, j: int) -> bool:
        return 0 < j < len(self.t) and self.t[j] == self.t[j - 1] + TF

    def events(self, t0: int):
        a, b = bisect.bisect_left(self._ft, t0), bisect.bisect_left(self._ft, t0 + TF)
        return self.funding[a:b]

    def funding_covered(self, lo: int, hi: int) -> bool:
        """Every settled event due in [lo, hi) is proven present (adapter cadence proof); unchecked data passes."""
        if self.fund_runs is None:
            return True
        return any(a <= lo and hi <= b for a, b in self.fund_runs)


class _Bar:
    __slots__ = ('high', 'low', 'close')

    def __init__(self, h, l, c):
        self.high, self.low, self.close = h, l, c


def prepare_symbol(sym: str, bars, windows: Windows, funding=(), fund_runs=None) -> SymbolData:
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
    trend = [False] * len(t)
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
                    trend[seg + k] = bool(d['e50'].iloc[k] > d['e200'].iloc[k])
                    elig[seg + k] = k + 1 >= windows.warmup_bars
                    if k >= C.SLIP_BARS:
                        win = [_Bar(h[x], l[x], c[x]) for x in range(seg + k - C.SLIP_BARS, seg + k)]
                        slip[seg + k] = max(C.slip_bps(win, SLIP_C_CRYPTO), SLIP_FLOOR_BPS)
            seg = j
    return SymbolData(sym, t, o, h, l, c, atr, le, elig, slip, list(funding), trend, fund_runs)


# ------------------------------------------------------------------ position + bar phases
class Pos:
    """One long episode. Levels (stop, add, target) are set from the fill price of `scen` (contract fills.stop_reference);
    every fill is recorded with its reference price, slip bps, gap flag and the tick of the point-in-time rule IN FORCE
    AT THAT FILL (`rules(sym, t)` is queried at every fill; nothing from the entry rule is reused later), so a stress
    row can reprice it exactly."""

    def __init__(self, sd: SymbolData, f: int, q0: float, dist: float, mgmt: dict, scen: C.Scenario, rules=None):
        if not (dist > 0) or not math.isfinite(dist):
            raise Incomplete('incomplete.stop', sd.t[f])
        if not math.isfinite(sd.slip[f]):
            raise Incomplete('incomplete.slip_history', sd.t[f])
        self.sd, self.f, self.q0, self.R, self.m, self.scen, self.rules = sd, f, q0, dist, mgmt, scen, rules
        tick = self.tick_at(f)
        fill = self.px(sd.o[f], 'buy', sd.slip[f], False, tick)
        self.e0 = self.best = fill
        self.stop = fill - dist
        self.qty, self.cost, self.adds, self.exit_due = q0, q0 * fill, 0, False
        py = mgmt.get('pyramid')
        self.next_add = fill + py['step_r'] * dist if py else math.inf
        self.fills = [dict(kind='entry', j=f, ref=sd.o[f], bps=sd.slip[f], gap=False, qty=q0, why='entry', tick=tick)]
        self.funding = []
        self.exit = None                                       # (j, why, free_from)
        self.exit_rule = None
        self.refused = set()

    def rule_at(self, j):
        return self.rules(self.sd.sym, self.sd.t[j]) if self.rules else None

    def tick_at(self, j):
        return (self.rule_at(j) or {}).get('tick')

    def px(self, ref, action, bps, gap=False, tick=None):
        return C.fill_price(ref, action, bps, self.scen, gap=gap, tick=tick)

    def close(self, j, ref, why, gap, free_from):
        sd = self.sd
        if not math.isfinite(sd.slip[j]):
            raise Incomplete('incomplete.slip_history', sd.t[j])
        t_exit = sd.t[j] if free_from == j else sd.t[j] + TF            # an intrabar exit is costed to the bar end
        if not sd.funding_covered(sd.t[self.f], t_exit):
            raise Incomplete('incomplete.funding', sd.t[self.f])
        rule = self.rule_at(j)                                          # the rule in force at the exit order
        self.exit_rule = rule
        tick = (rule or {}).get('tick')
        self.fills.append(dict(kind='exit', j=j, ref=ref, bps=sd.slip[j], gap=gap, qty=self.qty, why=why, tick=tick))
        self.exit = (j, why, free_from)
        return self.px(ref, 'sell', sd.slip[j], gap, tick)


def phase1(pos: Pos, j: int):
    """Open-known events at the open of bar j: gap stop (open at / through the stop, filled at the open), then the
    scheduled next-open time exit. Returns the exit fill price or None. Frees the symbol for bar j's own open entries."""
    o = pos.sd.o[j]
    if o <= pos.stop:
        return pos.close(j, o, 'stop_gap', True, j)
    if pos.exit_due:
        return pos.close(j, o, 'time', False, j)
    return None


def _event(pos: Pos, t, rate, mark, qty) -> tuple:
    if not (isinstance(mark, (int, float)) and math.isfinite(mark) and mark > 0):
        raise Incomplete('incomplete.mark', t)
    rec = (t, rate, mark, qty)
    pos.funding.append(rec)
    return rec


def charge_open(pos: Pos, j: int) -> list:
    """The event AT the open of bar j (the 8h / 4h grid) on the quantity held after phases 1-2 (entry <= T < exit)."""
    t0 = pos.sd.t[j]
    return [_event(pos, t, rate, mark, pos.qty) for t, rate, mark in pos.sd.events(t0) if t == t0]


def charge_later(pos: Pos, j: int, q_open: float, closed: bool) -> list:
    """Events later in bar j (1h / 2h cadence): their order against intrabar adds and exits is unknowable from a 4h
    bar, so each is charged conservatively (DISCLOSURES[1]): a PAID event on the larger of the open / end quantity
    (even when the position exited inside the bar), a RECEIVED event on the smaller (0 when it exited inside the bar)."""
    t0 = pos.sd.t[j]
    q_end = 0.0 if closed else pos.qty
    out = []
    for t, rate, mark in pos.sd.events(t0):
        if t == t0:
            continue
        pays = rate > 0                                              # a long pays a positive rate
        out.append(_event(pos, t, rate, mark, max(q_open, pos.qty) if pays else min(q_open, q_end)))
    return out


def charge_funding(pos: Pos, j: int) -> None:
    """Unit-replay convenience: the open event only (later-in-bar events are charged by charge_later after phase 3)."""
    charge_open(pos, j)


def walk(pos: Pos, j: int, add_gate, on_point=None):
    """Phase 3: backtest.walk_path (FBL-BT01) for one long position on bar j along backtest.path_points: adds fire in
    path order before the stop, stop-first suppresses the target (never the adds) when the open's stop is inside the
    range, a raised stop is live for later legs, the chandelier ratchets after each leg with the last CLOSED bar's ATR.
    `add_gate(pos, ref, qty) -> (ok, qty)`; `on_point(k, price)` is called after every path point the position
    survives (the book's path-aware equity sequence). Returns the exit fill price or None."""
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
                tick = pos.tick_at(j)                                          # the rule in force at the add
                fill = pos.px(ref, 'buy', sd.slip[j], ref != lv, tick)
                pos.fills.append(dict(kind='add', j=j, ref=ref, bps=sd.slip[j], gap=ref != lv, qty=q, why='add',
                                      tick=tick))
                pos.qty += q
                pos.cost += q * fill
                pos.adds += 1
                pos.next_add += py['step_r'] * pos.R
        pos.best = max(pos.best, b_)
        if k and trail and math.isfinite(atr):
            pos.stop = max(pos.stop, pos.best - trail * atr)
        if on_point:
            on_point(k, b_)
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
        charge_open(pos, j)
        q_open = pos.qty
        done = walk(pos, j, _unit_gate) is not None
        charge_later(pos, j, q_open, done)
        if done:
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
        fp = C.fill_price(x['ref'], action, x['bps'], scen, gap=x['gap'], tick=x.get('tick'))
        gross += sgn * x['ref'] * x['qty']
        fill_pnl += sgn * fp * x['qty']
        fees += C.fee(fp * x['qty'], 'taker', C.CRYPTO, scen)
    funding = -math.fsum(funding_paid(rate, q, mark, row) for _, rate, mark, q in pos.funding)
    net = fill_pnl - fees + funding
    r_unit = pos.R * pos.q0
    return {'gross': gross, 'fill_adj': fill_pnl - gross, 'fees': fees, 'funding': funding, 'net': net,
            'net_r': net / r_unit, 'r_unit': r_unit}


# ------------------------------------------------------------------ entry set E + paired unit replays
def entry_set(data: dict, windows: Windows, mgmt_s1: dict, check=None) -> dict:
    """E = every S1 entry under the fill-time re-arm rule, symbol ascending. A signal at the close of i fills at the
    open of i+1 when i is eligible, i+1 is contiguous and inside [dev start, last-entry cutoff), and the previous S1
    episode freed the symbol at or before that open (phase-1 exits free it for the same open). A signal whose next
    fill bar is missing (in the entry window) is a data-gap INCOMPLETE kept in `unfilled` (symbol, expected fill time,
    slice); run() adds it to every stage's per-slice K9 numerator; `unfilled_gap` stays its count."""
    eps, incomplete, unfilled = [], [], []
    for sym in sorted(data):
        if check:
            check()
        sd = data[sym]
        free = 0
        for i in range(len(sd.t)):
            if not (sd.le[i] and sd.elig[i]):
                continue
            f = i + 1
            if f >= len(sd.t) or not sd.contiguous(f):
                if windows.entry_ok(sd.t[i] + TF):
                    unfilled.append({'sym': sym, 't': sd.t[i] + TF, 'reason': 'unfilled_fill_bar',
                                     'slice': windows.slice_of(sd.t[i] + TF)})
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
    return {'episodes': eps, 'incomplete': incomplete, 'unfilled': unfilled, 'unfilled_gap': len(unfilled)}


def unit_replays(data: dict, E: dict, p: dict, stage: str, rows=tuple(ROWS), check=None) -> dict:
    """Paired replay of every e in E under one stage (overlap allowed here and only here)."""
    mg = stage_mgmt(p, stage)
    out, inc = [], []
    last = None
    for e in E['episodes']:
        if check and e['sym'] != last:
            check()                                              # once per symbol
            last = e['sym']
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
def _fills(q: Pos) -> list:
    return [(x['kind'], x['j'], x['ref'], x['gap'], x.get('tick')) for x in q.fills]


def run_book(data: dict, p: dict, stage: str, windows: Windows, rules, *, risk: float = 0.01,
             row: str = 'base_x1', check=None) -> dict:
    """Causal four-phase account replay from the raw stage signals (never from E). `rules(sym, t_ms)` -> the
    point-in-time exchange rule dict (step / min_qty / min_notional[, tick, max_qty]) or None (refused, counted); it is
    queried at EVERY order time (entry, each add, the exit), never carried over from the entry.

    Equity sequence (frozen; max equity DD and longest underwater both use it), per bar T:
      (1) after phase-1 exits, every position marked at its open, time T;
      (2) after phase-2 admissions and the open funding, marked at the opens, time T;
      (3) phase 3, symbol ascending: after each path point k (0..3) a position survives, that symbol at the path
          price, symbols already walked at their close, symbols not yet walked at their open, time T + k * 4h / 3 (never earlier than the previous point);
          after an intrabar exit, the account without it, time T + 4h;
      (4) the bar close, every position at its close, time T + 4h.
    `check()` (the --max-rss-mb guard) runs every 256 bars."""
    mg = stage_mgmt(p, stage)
    scen = row_scenario(row)
    syms = sorted(data)
    idx = {s: {t: j for j, t in enumerate(data[s].t)} for s in syms}
    times = sorted({t for s in syms for t in data[s].t})
    cash, pos, pend = START_EQUITY, {}, {}
    skips = {k: 0 for k in ('skipped_open_symbol', 'skipped_max_open', 'skipped_gross_cap', 'skipped_add_gross_cap',
                            'incomplete_fill_gap')}
    refusals, trades, incomplete, curve, events, seq = {}, [], [], [], [], []
    last_px = {}

    def count(code):
        refusals[code] = refusals.get(code, 0) + 1

    def put(pt):
        """Append an equity point; times never run backwards (a later symbol's path restarts at T, so its points are
        stamped at the latest time already in the sequence)."""
        seq.append((max(pt[0], seq[-1][0]) if seq else pt[0], pt[1]))

    def fee(notional):
        return C.fee(notional, 'taker', C.CRYPTO, scen)

    def finish(s, fill, j):
        nonlocal cash
        q = pos[s]
        out_fee = fee(q.qty * fill)
        cash += q.qty * fill - q.cost - out_fee
        if q.exit_rule is None:
            count('exit_rules_uncovered')                    # unresolved accounting row (no rule at the exit order)
        elif F.above_max(q.qty, q.exit_rule):
            count('exit_above_max_qty')                      # the exit order breaches the rule in force then
        trades.append({'sym': s, 't_entry': data[s].t[q.f], 't_exit': data[s].t[j], 'why': q.exit[1], 'adds': q.adds,
                       'slice': windows.slice_of(data[s].t[q.f]), 'pnl': q.qty * fill - q.cost - q.fee_paid - out_fee
                       - q.funding_paid, 'r_usd': q.R * q.q0, 'fills': _fills(q)})
        events.append((data[s].t[j], 1 if q.exit[1] in ('stop_gap', 'time') else 3, s, 'exit:' + q.exit[1]))
        del pos[s]

    def marks(T, px_of):
        upl = gross = 0.0
        for s, q in pos.items():
            x = px_of(s)
            upl += q.qty * x - q.cost
            gross += q.qty * x
        return cash + upl, gross

    def pay(q, recs):
        nonlocal cash
        paid = math.fsum(funding_paid(rate, qq, mark, row) for _, rate, mark, qq in recs)
        q.funding_paid += paid
        cash -= paid

    def drop(s, reason):
        incomplete.append({'sym': s, 't_entry': data[s].t[pos[s].f], 'reason': reason, 'fills': _fills(pos[s])})
        del pos[s]                                       # exit unknown: removed from the book, counted (data gate)

    for n_bar, T in enumerate(times):
        if check and n_bar % 256 == 0:
            check()
        bar = {s: idx[s].get(T) for s in syms}
        # ---- phase 1: open-known events, symbol ascending
        for s in sorted(pos):
            j = bar[s]
            if j is None or not data[s].contiguous(j):
                drop(s, 'incomplete.gap')
                continue
            try:
                fill = phase1(pos[s], j)
            except Incomplete as e:
                drop(s, e.reason)
                continue
            if fill is not None:
                finish(s, fill, j)
        open_px = lambda s: data[s].o[bar[s]] if bar[s] is not None else last_px[s]       # noqa: E731
        # ---- phase 2: admit pending next-open entries on the phase-1 state, symbol ascending
        eq_open, gross_open = marks(T, open_px)
        put((T, eq_open))
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
            q = Pos(sd, j, d['qty'], dist, mg, scen, rules=rules)
            q.fee_paid = fee(q.cost)
            q.funding_paid = 0.0
            cash -= q.fee_paid
            pos[s] = q
            gross_open += q.qty * q.e0
            events.append((T, 2, s, 'entry'))
        pend = {}
        # funding at the open on the quantity held after phases 1-2
        q_open = {}
        for s in sorted(pos):
            q = pos[s]
            try:
                pay(q, charge_open(q, bar[s]))
            except Incomplete as e:
                drop(s, e.reason)
                continue
            q_open[s] = q.qty
        put((T, marks(T, open_px)[0]))

        # ---- phase 3: intrabar paths, symbol ascending (including phase-2 admissions)
        def add_gate(q, ref, qty, _T=T):
            rule = rules(q.sd.sym, _T)                       # the rule in force at the add order
            code = 'add_rules_uncovered' if rule is None else None
            d = None if rule is None else F.size_check(qty, qty, ref, rule)
            if d is not None and d['ok'] is not True:
                code = 'add_' + d['code']
            if code:
                if (code, q.adds) not in q.refused:          # counted once per position/add level/reason; the add is still retried each bar
                    q.refused.add((code, q.adds))
                    count(code)
                return False, 0.0
            eq, gross = marks(_T, lambda s: ref if pos[s] is q else open_px(s))
            if gross + d['qty'] * ref > GROSS_CAP * eq:
                skips['skipped_add_gross_cap'] += 1
                return False, 0.0
            return True, d['qty']

        walked = set()
        cur_px = lambda s: (data[s].c[bar[s]] if s in walked else open_px(s))            # noqa: E731
        for s in sorted(pos):
            q = pos[s]
            j = bar[s]
            na = len(q.fills)

            def point(k, b_, _s=s, _T=T):
                put((_T + k * TF // 3, marks(_T, lambda x: b_ if x == _s else cur_px(x))[0]))
            try:
                fill = walk(q, j, add_gate, point)
                pay(q, charge_later(q, j, q_open[s], fill is not None))
            except Incomplete as e:
                drop(s, e.reason)
                continue
            for x in q.fills[na:]:
                if x['kind'] == 'add':
                    a_fill = q.px(x['ref'], 'buy', x['bps'], x['gap'], x['tick'])
                    a_fee = fee(a_fill * x['qty'])
                    q.fee_paid += a_fee
                    cash -= a_fee
                    events.append((T, 3, s, 'add'))
            if fill is not None:
                finish(s, fill, j)
                put((T + TF, marks(T, cur_px)[0]))
            walked.add(s)
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
        curve.append((T, marks(T, lambda s: last_px[s])[0], len(pos)))
        put((T + TF, curve[-1][1]))
    for s in sorted(pos):                                       # boundary INCOMPLETE: never closed with a later price
        drop(s, 'incomplete.boundary')
    return {'stage': stage, 'row': row, 'risk': risk, 'trades': trades, 'skips': skips, 'refusals': refusals,
            'incomplete': incomplete, 'curve': curve, 'events': events, 'equity_seq': seq,
            'max_equity_dd': max_dd(seq), 'end_equity_closed': cash}


def max_dd(seq) -> float:
    peak, dd = START_EQUITY, 0.0
    for _, e in seq:
        peak = max(peak, e)
        dd = max(dd, 1 - e / peak if peak > 0 else 0.0)
    return dd


def underwater_days(seq) -> float:
    """Longest span below the running peak on the SAME path-aware equity sequence as max equity DD (ruling 4
    amended): from the first point below the peak to the first point back at / above it (or the last point)."""
    uw, pk, since = 0.0, START_EQUITY, None
    for t, e in seq:
        if e >= pk:
            if since is not None:
                uw = max(uw, (t - since) / DAY_MS)
            pk, since = e, None
        elif since is None:
            since = t
    if since is not None and seq:
        uw = max(uw, (seq[-1][0] - since) / DAY_MS)
    return uw


# ------------------------------------------------------------------ B0 random-entry baseline
def b0_candidates(sd: SymbolData, windows: Windows, slice_no: int, trend_only: bool = False) -> list:
    """Eligible entry bars of one symbol / slice: a contiguous open before the last-entry cutoff whose decision bar is
    eligible; B2 (report-only) additionally needs EMA50 > EMA200 at the decision bar."""
    a, b = windows.slices[slice_no - 1]
    return [f for f in range(1, len(sd.t)) if sd.contiguous(f) and sd.elig[f - 1] and a <= sd.t[f] < b
            and windows.entry_ok(sd.t[f]) and (not trend_only or (sd.trend is not None and sd.trend[f - 1]))]


def b0_sample(data: dict, E: dict, windows: Windows, mgmt_s1: dict, *, replicates: int, base_seed: int,
              _cache=None, trend_only: bool = False, check=None) -> list:
    """Per replicate r: random.Random(base_seed + r); symbols then slices ascending; eligible entry bars drawn in a
    seeded random order WITHOUT replacement; a draw is accepted only if its S1 episode [fill, free) does not overlap
    an accepted one of that symbol (fill-time re-arm rule); stop at the S1 count of that symbol and slice. A draw whose
    S1 episode is INCOMPLETE is discarded and sampling continues (DISCLOSURES[0]: E instead stops re-arming that
    symbol, so B0 is not path-identical to E). Exhaustion -> BaselineIncomplete (the B0 gate fails closed)."""
    need = {}
    for e in E['episodes']:
        need[(e['sym'], e['slice'])] = need.get((e['sym'], e['slice']), 0) + 1
    cache = {} if _cache is None else _cache
    cands = {(s, k): b0_candidates(data[s], windows, k, trend_only) for s in sorted(data)
             for k in range(1, len(windows.slices) + 1)}
    out = []
    for r in range(replicates):
        if check:
            check()                                              # once per replicate
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


def b0_means(data: dict, reps: list, mgmt_s1: dict, row: str = 'base_x1', _cache=None, check=None) -> list:
    cache = {} if _cache is None else _cache
    res = []
    for rep in reps:
        if check:
            check()
        xs = []
        for e in rep:
            key = (e['sym'], e['f'])
            if cache.get(key) is None:
                cache[key] = replay_unit(data[e['sym']], e['f'], mgmt_s1)
            xs.append(price_episode(cache[key], row)['net_r'])
        res.append(math.fsum(xs) / len(xs) if xs else None)
    return res


# ------------------------------------------------------------------ statistics (gates.*, bootstrap, hypotheses)


def iso_week(t_ms: int) -> int:
    """Monday-based UTC ISO week ordinal (1970-01-01 is a Thursday, so day + 3 starts the week on Monday)."""
    return (t_ms // DAY_MS + 3) // 7


def bootstrap_lcb(eps, *, seed: int = 20261028, resamples: int = 10000, block: int = 8, alpha: float = 0.05,
                  check=None):
    """bootstrap block: per-episode mean net R; episodes grouped by the ISO week of their entry fill; moving blocks of
    `block` consecutive calendar ISO weeks (empty weeks included) drawn with replacement, ceil(W / block) per resample;
    every episode in a sampled block is retained (weight = multiplicity); LCB = the lower 5th percentile order
    statistic (index ceil(alpha * n) - 1). A resample with no episode has no mean and counts as -inf (conservative).
    eps: [(entry_ms, net_r)]. Returns (point, lcb) or (None, None) when empty."""
    if not eps:
        return None, None
    wk = [iso_week(t) for t, _ in eps]
    w0, W = min(wk), max(wk) - min(wk) + 1
    sums, cnts = [0.0] * W, [0] * W
    for w, (_, r) in zip(wk, eps):
        sums[w - w0] += r
        cnts[w - w0] += 1
    b = min(block, W)
    nb = W - b + 1
    bs = [math.fsum(sums[i:i + b]) for i in range(nb)]
    bc = [sum(cnts[i:i + b]) for i in range(nb)]
    k = math.ceil(W / b)
    rng = random.Random(seed)
    stats = []
    for n_res in range(resamples):
        if check and n_res % 1000 == 0:
            check()                                         # --max-rss-mb guard per batch of 1000 resamples
        s = c = 0
        for _ in range(k):
            i = rng.randrange(nb)
            s += bs[i]
            c += bc[i]
        stats.append(s / c if c else -math.inf)
    stats.sort()
    return math.fsum(r for _, r in eps) / len(eps), stats[max(0, math.ceil(alpha * resamples) - 1)]


def pf_of(xs):
    gain, loss = math.fsum(x for x in xs if x > 0), -math.fsum(x for x in xs if x < 0)
    return gain / loss if loss > 0 else (math.inf if gain > 0 else None)


def _mean(xs):
    return math.fsum(xs) / len(xs) if xs else None


def _pos(x):
    return x is not None and x > 0


def describe(xs_t, *, lcb: bool = True, seed: int = 20261028, resamples: int = 10000, check=None) -> dict:
    xs = [r for _, r in xs_t]
    out = {'n': len(xs), 'mean': _mean(xs), 'median': (sorted(xs)[len(xs) // 2] if len(xs) % 2 else
                                                       _mean(sorted(xs)[len(xs) // 2 - 1:len(xs) // 2 + 1])) if xs else None,
           'pf': pf_of(xs), 'win_rate': sum(x > 0 for x in xs) / len(xs) if xs else None,
           'worst': min(xs) if xs else None}
    if lcb:
        out['lcb'] = bootstrap_lcb(xs_t, seed=seed, resamples=resamples, check=check)[1]
    return out


def by_symbol(eps, row='base_x1') -> dict:
    out = {}
    for e in eps:
        out[e['sym']] = out.get(e['sym'], 0.0) + e['rows'][row]['net_r']
    return out


def concentration(eps, p: dict) -> dict:
    """gates.concentration: each symbol's share of the stage's total base net R (undefined -> fails when total <= 0)."""
    g = p['gates']['concentration']
    sym = by_symbol(eps)
    tot = math.fsum(sym.values())
    top = sorted(sym.values(), reverse=True)
    if not tot > 0:
        return {'max_share': None, 'top3_share': None, 'pass': False}
    s1, s3 = top[0] / tot, math.fsum(top[:3]) / tot
    return {'max_share': s1, 'top3_share': s3, 'pass': s1 <= g['max_symbol_share'] and s3 <= g['max_top3_share']}


def remove_best_symbol(eps) -> dict:
    sym = by_symbol(eps)
    if not sym:
        return {'removed': None, 'mean': None, 'pass': False}
    best = max(sorted(sym), key=lambda s: sym[s])
    rest = [e['rows']['base_x1']['net_r'] for e in eps if e['sym'] != best]
    m = _mean(rest)
    return {'removed': best, 'mean': m, 'pass': _pos(m)}


def slice_spans(p: dict, windows: Windows) -> list:
    """gates.activity.per_30d_span: slices 1-2 full span, slice 3 up to the last-entry cutoff (entry-eligible days)."""
    return [((min(b, windows.last_entry) - a) / DAY_MS) for a, b in windows.slices]


def activity(eps, p: dict, windows: Windows) -> dict:
    a = p['gates']['activity']
    k = len(windows.slices)
    per = [[e for e in eps if e['slice'] == s] for s in range(1, k + 1)]
    act = lambda es: sorted(s for s, n in _count(es).items() if n >= 5)                    # noqa: E731
    spans = slice_spans(p, windows)
    rate = [len(x) / (d / 30) if d > 0 else 0.0 for x, d in zip(per, spans)]
    out = {'total': len(eps), 'per_slice': [len(x) for x in per], 'per_30d': rate,
           'active_total': len(act(eps)), 'active_per_slice': [len(act(x)) for x in per]}
    out['pass'] = (out['total'] >= a['min_episodes_total'] and all(n >= a['min_episodes_per_slice'] for n in out['per_slice'])
                   and all(r >= a['min_episodes_per_30d_each_slice'] for r in rate)
                   and out['active_total'] >= a['min_active_symbols_total']
                   and all(n >= a['min_active_symbols_per_slice'] for n in out['active_per_slice']))
    return out


def _count(es):
    out = {}
    for e in es:
        out[e['sym']] = out.get(e['sym'], 0) + 1
    return out


def data_gate(rep: dict, windows: Windows, extra_incomplete=()) -> dict:
    """gates.data: per slice at most 2% INCOMPLETE (data gaps and boundary together) of that slice's episodes."""
    k = len(windows.slices)
    inc = list(rep['incomplete']) + list(extra_incomplete)
    frac = []
    for s in range(1, k + 1):
        n_ok = sum(e['slice'] == s for e in rep['episodes'])
        n_bad = sum(e.get('slice') == s for e in inc)
        frac.append(n_bad / (n_ok + n_bad) if n_ok + n_bad else 0.0)
    return {'incomplete_frac': frac, 'pass': all(f <= 0.02 for f in frac)}


def stage_quality(rep: dict, p: dict, windows: Windows, *, resamples: int = 10000, check=None) -> dict:
    """The per-stage H-test on the stage's OWN episodes (gates.stage_scope): base mean > 0 and LCB > 0, x1.5 mean > 0,
    every slice mean > 0, remove-best-symbol, concentration AND the stage's own data gate (gates.data: at most 2%
    INCOMPLETE per slice; a stage that breaches it can never be credited); plus the reported rows."""
    eps = rep['episodes']
    rows = {}
    for r in ROWS:
        xs = [(e['t'], e['rows'][r]['net_r']) for e in eps]
        rows[r] = describe(xs, lcb=r in ('base_x1', 'all_variable_costs_x1.5', 'all_variable_costs_x2'),
                           resamples=resamples, check=check)
    sl = [_mean([e['rows']['base_x1']['net_r'] for e in eps if e['slice'] == s]) for s in range(1, len(windows.slices) + 1)]
    out = {'rows': rows, 'slice_means': sl, 'remove_best_symbol': remove_best_symbol(eps),
           'concentration': concentration(eps, p), 'data': data_gate(rep, windows),
           'decomposition_r': {k: math.fsum(e['rows']['base_x1'][k] / e['rows']['base_x1']['r_unit'] for e in eps)
                               for k in ('gross', 'fill_adj', 'fees', 'funding')}}
    b = rows['base_x1']
    out['checks'] = {'expectancy': _pos(b['mean']) and _pos(b['lcb']), 'x1_5': _pos(rows['all_variable_costs_x1.5']['mean']),
                     'slices': all(_pos(m) for m in sl), 'remove_best_symbol': out['remove_best_symbol']['pass'],
                     'concentration': out['concentration']['pass'], 'data': out['data']['pass']}
    out['pass'] = all(out['checks'].values())
    return out


def paired_d3(s2: dict, s3: dict, windows: Windows, *, resamples: int = 10000, check=None) -> dict:
    """D3(e) = netR_S3(e) - netR_S2(e) on the episodes complete in both; gated by mean / LCB, x1.5 and three slices
    only (never remove-best-symbol or concentration). Report-only S2 - S1 uses the same function."""
    a = {(e['sym'], e['f']): e for e in s2['episodes']}
    pairs = [(e, a[(e['sym'], e['f'])]) for e in s3['episodes'] if (e['sym'], e['f']) in a]
    d = lambda r: [(x['t'], x['rows'][r]['net_r'] - y['rows'][r]['net_r']) for x, y in pairs]   # noqa: E731
    base = describe(d('base_x1'), resamples=resamples, check=check)
    x15 = _mean([v for _, v in d('all_variable_costs_x1.5')])
    sl = [_mean([x['rows']['base_x1']['net_r'] - y['rows']['base_x1']['net_r'] for x, y in pairs if x['slice'] == s])
          for s in range(1, len(windows.slices) + 1)]
    checks = {'expectancy': _pos(base['mean']) and _pos(base['lcb']), 'x1_5': _pos(x15), 'slices': all(_pos(m) for m in sl)}
    return {'n': len(pairs), 'base': base, 'x1_5_mean': x15, 'slice_means': sl, 'checks': checks,
            'pass': all(checks.values())}


def b0_gate(s1_mean, means) -> dict:
    """K5: S1 base mean net R strictly above the 95th percentile (order statistic ceil(0.95 n) - 1) of the B0
    replicate means; any missing replicate mean or an INCOMPLETE B0 fails closed."""
    if means is None or not means or any(m is None for m in means):
        return {'status': 'INCOMPLETE', 'p95': None, 'pass': False}
    xs = sorted(means)
    p95 = xs[max(0, math.ceil(0.95 * len(xs)) - 1)]
    rank = sum(m < s1_mean for m in xs) / len(xs) if s1_mean is not None else None
    return {'status': 'OK', 'p95': p95, 'percentile_rank': rank, 'pass': s1_mean is not None and s1_mean > p95}


def folds_report(p: dict, rep: dict) -> list:
    """windows.report_only_folds: per-fold base mean net R by entry fill (report-only, never gated)."""
    import re
    ds = [iso_ms(d + 'T00:00:00Z') for d in re.findall(r'\d{4}-\d\d-\d\d', p['windows']['report_only_folds'])]
    out = []
    for a, b in zip(ds, ds[1:]):
        xs = [e['rows']['base_x1']['net_r'] for e in rep['episodes'] if a <= e['t'] < b]
        out.append({'start': a, 'end': b, 'n': len(xs), 'mean': _mean(xs)})
    return out


def book_metrics(bk: dict, windows: Windows) -> dict:
    """Credited-stage book: max equity DD, closed-R DD, longest underwater days, annualized after-cost return over the
    development window, Calmar, total net R / closed-R DD, exposure, trade count; per entry slice trade counts."""
    tr = sorted(bk['trades'], key=lambda x: (x['t_exit'], x['sym']))
    rs = [x['pnl'] / x['r_usd'] for x in tr]
    cum = peak = crdd = 0.0
    for r in rs:
        cum += r
        peak = max(peak, cum)
        crdd = max(crdd, peak - cum)
    uw = underwater_days(bk['equity_seq'])                    # same sequence as max equity DD (ruling 4 amended)
    end = bk['end_equity_closed']
    years = (windows.sealed - windows.dev_start) / (365.25 * DAY_MS)
    ann = (end / START_EQUITY) ** (1 / years) - 1 if end > 0 and years > 0 else -1.0
    dd = bk['max_equity_dd']
    tot = math.fsum(rs)
    n_bars = len(bk['curve'])
    return {'max_equity_dd': dd, 'closed_r_dd': crdd, 'longest_underwater_days': uw, 'annualized_return': ann,
            'calmar': ann / dd if dd > 0 else (math.inf if ann > 0 else None),
            'total_net_r': tot, 'total_r_over_closed_r_dd': tot / crdd if crdd > 0 else (math.inf if tot > 0 else None),
            'exposure': sum(1 for _, _, n in bk['curve'] if n) / n_bars if n_bars else 0.0, 'trades': len(tr),
            'per_slice_trades': [sum(x['slice'] == s for x in tr) for s in range(1, len(windows.slices) + 1)],
            'skips': bk['skips'], 'refusals': bk['refusals'], 'incomplete': len(bk['incomplete']),
            'data': book_data(bk, windows), 'unresolved_accounting': unresolved(bk),
            'causality': book_causality(bk, windows)}


UNRESOLVED = ('rules_uncovered', 'add_rules_uncovered', 'exit_rules_uncovered', 'exit_above_max_qty')


def unresolved(bk: dict) -> int:
    """Accounting rows the book could not resolve against a point-in-time rule (gates.data 'no unresolved accounting
    row'; gates.causality): a missing rule at an entry / add / exit order, or an exit above the rule's maximum."""
    return sum(bk['refusals'].get(k, 0) for k in UNRESOLVED)


def book_data(bk: dict, windows: Windows) -> dict:
    """The credited book's own data state: per entry slice at most 2% INCOMPLETE (gap / boundary / funding / mark)
    of its episodes, and no unfilled-for-data pending entry."""
    frac = []
    for k in range(1, len(windows.slices) + 1):
        ok = sum(x['slice'] == k for x in bk['trades'])
        bad = sum(windows.slice_of(x['t_entry']) == k for x in bk['incomplete'])
        frac.append(bad / (ok + bad) if ok + bad else 0.0)
    n_un = unresolved(bk)
    return {'incomplete_frac': frac, 'fill_gaps': bk['skips']['incomplete_fill_gap'], 'unresolved_accounting': n_un,
            'pass': all(f <= 0.02 for f in frac) and bk['skips']['incomplete_fill_gap'] == 0 and n_un == 0}


def book_causality(bk: dict, windows: Windows) -> dict:
    """gates.causality for one book replay: no unresolved accounting row, the frozen equity sequence and the event log
    never run backwards in time, every entry fill inside [dev start, last-entry cutoff), every exit at or after its
    entry and before the sealed cutoff."""
    st = [t for t, _ in bk['equity_seq']]
    et = [e[0] for e in bk['events']]
    checks = {'no_unresolved_accounting': unresolved(bk) == 0,
              'equity_sequence_causal': all(a <= b for a, b in zip(st, st[1:])),
              'events_causal': all(a <= b for a, b in zip(et, et[1:])),
              'entries_inside_window': all(windows.entry_ok(x['t_entry']) for x in bk['trades']),
              'exits_before_seal': all(x['t_entry'] <= x['t_exit'] < windows.sealed for x in bk['trades'])}
    return {'checks': checks, 'pass': all(checks.values())}


def dd_tier(base: dict, x15: dict, p: dict) -> dict:
    """gates.drawdown_and_tail: (ordinary OR profitability_rescue) AND every all-path ceiling (base DD <= 55%,
    x1.5 DD <= 65%, longest underwater <= 365 d); passing only via rescue is a HIGH-RISK SLEEVE."""
    g = p['gates']['drawdown_and_tail']
    o, r = g['tiers']['ordinary'], g['tiers']['profitability_rescue']
    ordinary = base['max_equity_dd'] <= o['base_max_equity_dd_le'] and base['closed_r_dd'] < o['closed_r_dd_lt']
    rescue = (base['max_equity_dd'] <= r['base_max_equity_dd_le'] and (base['calmar'] or -1) >= r['annualized_net_return_over_max_dd_ge']
              and (base['total_r_over_closed_r_dd'] or -1) >= r['total_net_r_over_closed_r_dd_ge'])
    ceilings = (base['max_equity_dd'] <= r['base_max_equity_dd_le'] and x15['max_equity_dd'] <= g['x1_5_max_equity_dd_le']
                and base['longest_underwater_days'] <= g['longest_underwater_days_le'])
    ok = (ordinary or rescue) and ceilings
    return {'ordinary': ordinary, 'rescue': rescue, 'ceilings': ceilings, 'pass': ok,
            'label': ('HIGH-RISK SLEEVE' if ok and not ordinary else None)}


def credited_stage(h1: bool, h2: bool, h3: bool):
    """hypotheses.credited_stage + order: a stage is never credited when an earlier one failed."""
    if not h1:
        return None
    if not h2:
        return 'S1'
    return 'S3' if h3 else 'S2'


def decide(g: dict) -> str:
    """Pure kill-rule precedence over a computed gate dict (K9 -> INCOMPLETE; K1-K5 -> FALSIFIED; K6/K7 credit;
    K8 -> FAIL). Used by tests only in this PR: run() never calls it and nothing writes its result. A report-only
    universe has no gate inputs (None), which is refused rather than read."""
    if not isinstance(g, dict) or g.get('report_only'):
        raise RunnerError('no decision inputs (report-only universe or missing gate inputs)')
    if not g['data'] or not g['activity'] or g['b0']['status'] != 'OK':
        return 'INCOMPLETE'
    if not g['H1']:
        return 'FALSIFIED'
    st = credited_stage(g['H1'], g['H2'], g['H3'])
    if not (g['book_dd']['pass'] and g['tails'] and g['causality']):
        return 'FAIL'
    return f'SURVIVES_DEV({st})'


def gates(p: dict, windows: Windows, stages: dict, b0_means_: list | None, *, resamples: int = 10000,
          check=None) -> dict:
    """Every gated statistic per the signed JSON, in the fixed H1 -> H2 -> H3 order (one-sided alpha 0.05 each,
    sequential, no adjustment: trial_budget.multiplicity). No verdict string is produced here."""
    q = {s: stage_quality(stages[s], p, windows, resamples=resamples, check=check) for s in ('S1', 'S2', 'S3')}
    act = activity(stages['S1']['episodes'], p, windows)
    b0 = b0_gate(q['S1']['rows']['base_x1']['mean'], b0_means_)
    d3 = paired_d3(stages['S2'], stages['S3'], windows, resamples=resamples, check=check)
    h1 = q['S1']['pass'] and b0['pass']
    h2 = h1 and q['S2']['pass']
    h3 = h2 and d3['pass'] and q['S3']['pass']
    return {'quality': q, 'activity': act['pass'], 'activity_detail': act, 'data': q['S1']['data']['pass'],
            'data_by_stage': {s: q[s]['data'] for s in q},
            'b0': b0, 'D3': d3,
            'S2_minus_S1': paired_d3(stages['S1'], stages['S2'], windows, resamples=resamples, check=check),
            'H1': h1, 'H2': h2, 'H3': h3, 'credited_stage': credited_stage(h1, h2, h3)}


# ------------------------------------------------------------------ data adapter (repo store formats, guarded reads)
def funding_runs(sym: str, rows) -> list:
    """Cadence proof (costs.check_funding_sequence) split into maximal proven runs: each run [first event, last event
    + interval) has every settled event present; an off-grid row (edge05 t_nom) ends the data there (fail closed)."""
    rows = sorted(rows, key=lambda r: r.time_ms)
    runs, cur = [], []
    for r in rows:
        if cur:
            try:
                C.check_funding_sequence(sym, [cur[-1], r])
            except C.CostError:
                runs.append(cur)
                cur = []
        cur.append(r)
    if cur:
        runs.append(cur)
    out = []
    for run_ in runs:
        C.check_funding_sequence(sym, run_)
        out.append((run_[0].time_ms, run_[-1].time_ms + run_[-1].interval_hours * HOUR))
    return out


def load_symbol(src, sym: str, windows: Windows, *, warmup_start: int, check=None) -> SymbolData:
    """One symbol from an edge05-compatible source (`bars(sym, '4h', lo, hi)` / `funding(sym, lo, hi)` /
    `mark(sym, lo, hi)` rows: Bar / Funding namedtuples of tools/research/edge05_runner.py), through edge05 Guard:
    every read is clipped to [warmup_start, sealed cutoff) and refused before and after the read at the seal.
    Funding: nominal grid (t_nom), proven runs (funding_runs), funding-mark-proxy-v1 = close of the 1h mark bar
    ending at T (nan when absent -> the episode charged there is INCOMPLETE)."""
    import edge05_runner as E5
    g = E5.Guard(src, windows.sealed)
    g.scope(warmup_start, windows.sealed)
    try:
        bars = [(b.open_ms, b.open, b.high, b.low, b.close, b.volume)
                for b in g.bars(sym, '4h', warmup_start, windows.sealed)]
        if check:
            check()
        raw_f = g.funding(sym, warmup_start, windows.sealed)
        raw_m = g.mark(sym, warmup_start, windows.sealed)
    except E5.Incomplete as e:                     # e.g. the never-opened straddle month without a covering artifact
        raise SourceUncovered(f'{sym}: source rows {e.reason} from {_iso(e.at_ms)}; refusing (fail closed)') from None
    digest = rows_digest(sym, bars, [(r.time_ms, r.rate, r.interval_hours) for r in raw_f],
                         [(m.open_ms, m.close) for m in raw_m])
    try:
        fr = E5.nominal(raw_f)
    except E5.Incomplete:
        fr = []
    marks = {m.open_ms: m.close for m in raw_m}
    if check:
        check()
    ev = [(r.time_ms, r.rate, marks.get(r.time_ms - HOUR, math.nan)) for r in fr]
    sd = prepare_symbol(sym, bars, windows, funding=ev, fund_runs=funding_runs(sym, fr))
    sd.source_sha256 = digest
    return sd


def rows_digest(sym, bars, funding, marks) -> str:
    """sha256 of every source row a symbol's replay read (4h bars, funding rows, 1h mark closes), canonical JSON."""
    return sha(json.dumps([sym, [list(b) for b in bars], [list(f) for f in funding], [list(m) for m in marks]],
                          separators=(',', ':')).encode())


def rules_of(pit):
    """Point-in-time exchange rules (edge05 PitRules.lookup); uncovered -> None (a counted refusal, never a guess)."""
    import edge05_runner as E5

    def at(sym, t):
        try:
            return pit.lookup(sym, t)
        except E5.Incomplete:
            return None
    at.identity = getattr(pit, "identity", None)        # no identity -> rules_identity() refuses
    return at


RULES_ZERO_KEYS = ('bad_coverage_entries', 'unauthenticated_entries', 'causally_unsupported_intervals',
                   'unsourced_intervals')


def rules_identity(rules) -> dict:
    """The rules callable's immutable identity (PitRules.identity via rules_of): a 64-hex artifact sha256 and every
    RULES_ZERO_KEYS count present and exactly 0 (an int, not a bool / missing / None); anything else refuses."""
    fn = getattr(rules, 'identity', None)
    ident = fn() if callable(fn) else None
    h = ident.get('sha256') if isinstance(ident, dict) else None
    if not (isinstance(h, str) and len(h) == 64 and all(ch in '0123456789abcdef' for ch in h)):
        raise RunnerError('the point-in-time rules have no immutable identity (sha256); refusing')
    for k in RULES_ZERO_KEYS:                       # N7: present and exactly 0; absence is not a clean state
        v = ident.get(k, None)
        if type(v) is not int or v != 0:
            raise RunnerError(f'the point-in-time rules identity needs {k} == 0 (got {v!r})')
    return json.loads(canon(ident))


# ------------------------------------------------------------------ run (in memory only)
DISCLOSURES = (
    'B0 is not path-identical to E: E (and the book) permanently stop re-arming a symbol after an INCOMPLETE '
    'episode (its exit is unknown), while B0 discards an INCOMPLETE random draw and continues sampling '
    '(Codex 6478332951 item 4).',
    'Funding later in a 4h bar (1h / 2h cadence) is charged conservatively because its order against intrabar adds '
    'and exits is unknowable: a paid event on the larger of the open / end quantity even after an intrabar exit, a '
    'received event on the smaller (0 after an intrabar exit). Events at the bar open (the 8h grid) are charged '
    'exactly on the quantity held after phases 1-2 (Codex 6478332951 item 6).',
)
PARITY_DECLARED = (
    'time exit: the runner fills at the open after the 42-bar cap (fills.time_exit_fill); backtest.py closes at the '
    'close of the cap bar',
    're-arm: the runner decides re-arm / skipped_open_symbol at fill time after phase-1 exits (params.rearm_rule); '
    'backtest.py drops a signal raised while the position is still open, even when a gap stop frees it at the next open',
    'cross-symbol order: the runner admits entries on the phase-1 state (book.capacity_for_open_entries); backtest.py '
    'fills pending entries before any exit of that bar and caps by max_pos per sleeve',
    'admission: the runner skips an entry above 3x gross (never scaled); backtest.py scales it to max_lev',
    'costs: the runner prices slip-v1 / actual funding / taker fees on adds and stops (costs block); backtest.py uses '
    'flat SLIP / FEE / per-bar funding and fills adds without slippage',
)


def universe_symbols(p: dict, universe: str) -> set:
    if universe == 'FIXED-17':
        return set(p['universe']['symbols'])
    if universe == 'STATIC-40':
        return {x['symbol'] for x in json.loads(open(_p(REPO, UNIVERSE_REL), 'rb').read())['symbols']}
    raise IdentityMismatch(f'unknown universe {universe!r}')


def run(data: dict, pf: Preflight, *, windows: Windows | None = None, rules=None, b0_replicates: int = 0,
        book_stage: str | None = None, universe: str = 'FIXED-17', resamples: int = 10000, check=None,
        allow_partial_universe_test_only: bool = False) -> dict:
    """In-memory orchestration on SymbolData: E, the four paired unit replays, gate statistics, B0 (+ report-only B2),
    the 1.0% book of the credited stage (base + x1.5) with report-only 0.5% / 0.25% replays, folds. Writes nothing and
    carries no verdict string (decide() is never called here)."""
    if not isinstance(pf, Preflight):
        raise PreflightError('run() needs a passed Preflight')
    p = pf.prereg
    w = windows or Windows.of(p)
    allowed = universe_symbols(p, universe)
    if set(data) != allowed:                                   # the frozen book exactly: no subset, superset or swap
        if not (allow_partial_universe_test_only and set(data) < allowed):
            raise IdentityMismatch(f'{universe} needs exactly its {len(allowed)} symbols; missing '
                                   f'{sorted(allowed - set(data))}, extra {sorted(set(data) - allowed)}')
        universe = f'PARTIAL-TEST({universe})'                 # engine tests only: never a gated result
    for key, sd in data.items():
        if getattr(sd, 'sym', None) != key:
            raise IdentityMismatch(f'data[{key!r}] holds SymbolData for {getattr(sd, "sym", None)!r}')
    for sd in data.values():
        if sd.t and sd.t[-1] >= w.sealed:
            raise HoldoutRefused(f'{sd.sym}: bar at or after the sealed cutoff')
    s1 = stage_mgmt(p, 'S1')
    E = entry_set(data, w, s1, check)
    stages = {s: unit_replays(data, E, p, s, check=check) for s in STAGE_IDS}
    for s in STAGE_IDS:     # E's own INCOMPLETE entries and its unfilled-fill-bar data gaps count in every stage's K9
        stages[s]['incomplete'] = stages[s]['incomplete'] + E['incomplete'] + E['unfilled']
    out = {'candidate_id': CANDIDATE, 'prereg_sha256': pf.prereg_sha256, 'code': pf.code, 'evaluated': False,
           'decided': False, 'universe': universe, 'report_only': universe != 'FIXED-17', 'disclosures': list(DISCLOSURES),
           'parity_declared': list(PARITY_DECLARED),
           'E': {'n': len(E['episodes']), 'incomplete': len(E['incomplete']), 'unfilled_gap': E['unfilled_gap']},
           'stages': {s: summarize(stages[s], w) for s in STAGE_IDS},
           'S1-T': stage_quality(stages['S1-T'], p, w, resamples=resamples, check=check)['rows'],
           'folds': {s: folds_report(p, stages[s]) for s in STAGE_IDS}}
    means = None
    if b0_replicates:
        b0 = next(b for b in p['baselines'] if b['id'] == 'B0')
        cache = {}
        try:
            reps = b0_sample(data, E, w, s1, replicates=b0_replicates, base_seed=b0['seed']['base'], _cache=cache,
                             check=check)
            means = b0_means(data, reps, s1, _cache=cache, check=check)
            out['B0'] = {'status': 'OK', 'replicates': len(means)}
        except BaselineIncomplete as e:
            out['B0'] = {'status': 'INCOMPLETE', 'reason': str(e)}
        try:
            r2 = b0_sample(data, E, w, s1, replicates=b0_replicates, base_seed=b0['seed']['base'], _cache=cache,
                           trend_only=True, check=check)
            m2 = b0_means(data, r2, s1, _cache=cache, check=check)
            out['B2'] = {'status': 'OK', 'report_only': True, 'mean_of_means': _mean(m2)}
        except BaselineIncomplete as e:
            out['B2'] = {'status': 'INCOMPLETE', 'report_only': True, 'reason': str(e)}
    g = gates(p, w, stages, means, resamples=resamples, check=check)
    ro = out['report_only']         # STATIC-40 / PARTIAL-TEST: statistics only; it can never replace or rescue FIXED-17
    if ro:
        out['report_statistics'] = {k: v for k, v in g.items() if k not in DECISION_KEYS}
        out['decision_inputs_refused'] = f'report-only universe {universe}: no credited stage or gate inputs'
    else:
        out['gates'] = g
    stage = book_stage or (None if ro else g['credited_stage'])
    if stage:
        if rules is None:
            raise RunnerError('the book needs point-in-time exchange rules')
        books = {}
        for risk in p['sizing']['risk_per_trade']:
            for row in (('base_x1', 'all_variable_costs_x1.5') if risk == p['sizing']['gated_risk'] else ('base_x1',)):
                books[f'{risk}:{row}'] = book_metrics(run_book(data, p, stage, w, rules, risk=risk, row=row,
                                                               check=check), w)
        gr = p['sizing']['gated_risk']
        tails = [e['rows']['base_x1']['net_r'] for e in stages[stage]['episodes']]
        out['book'] = {'stage': stage, 'replays': books,
                       'dd_tier': dd_tier(books[f'{gr}:base_x1'], books[f'{gr}:all_variable_costs_x1.5'], p),
                       'tails_worst_ok': bool(tails) and min(tails) >= p['gates']['tails']['worst_episode_net_r_ge'],
                       'top1pct_share': _top1(tails)}
    bk = out.get('book')
    if ro:
        out['gate_inputs'] = None
        return out
    out['gate_inputs'] = gate_inputs(g, bk if bk and bk['stage'] == g['credited_stage'] else None,
                                     p['sizing']['gated_risk'])
    return out


GATE_KEYS = ('data', 'activity', 'b0', 'H1', 'H2', 'H3', 'book_dd', 'tails', 'causality')
DECISION_KEYS = ('H1', 'H2', 'H3', 'credited_stage')     # withheld from report-only universes


def gate_inputs(g: dict, book: dict | None, gated_risk) -> dict:
    """Pure: the exact dict decide() consumes (GATE_KEYS) plus its detail, from the gate dict and the CREDITED
    stage's book (None when no stage is credited or the book was replayed for another stage: then the book inputs are
    failing, so no survival can be read from it). data = the data gate of every REACHED sequential stage: S1 always, S2
    once H1 passes, S3 once H2 passes (K9 precedence: a reached stage that is untestable never falls back to a
    lower-stage survival, while an unreached stage's gap never turns a valid earlier falsification into INCOMPLETE;
    S1-T stays report-only) AND the
    credited book's data state (<= 2% INCOMPLETE per slice, no unfilled-for-data entry, no unresolved accounting row)
    at base and x1.5; causality = book_causality() at base and x1.5; tails = worst credited-stage episode >= -3R."""
    st = g['credited_stage']
    reached = ['S1'] + (['S2'] if g['H1'] else []) + (['S3'] if g['H1'] and g['H2'] else [])   # Codex 6481285622 (N6)
    data = bool(g['data']) and all(bool(g['data_by_stage'][s]['pass']) for s in reached)
    out = {'activity': bool(g['activity']), 'b0': g['b0'], 'H1': g['H1'], 'H2': g['H2'], 'H3': g['H3'],
           'credited_stage': st}
    if book is None:
        out.update(data=data, book_dd={'pass': False, 'reason': 'no credited-stage book'}, tails=False,
                   causality=False, causality_detail=None, book_data_detail=None)
        return out
    b, x = book['replays'][f'{gated_risk}:base_x1'], book['replays'][f'{gated_risk}:all_variable_costs_x1.5']
    out.update(data=data and b['data']['pass'] and x['data']['pass'], book_dd=book['dd_tier'],
               tails=bool(book['tails_worst_ok']), causality=b['causality']['pass'] and x['causality']['pass'],
               causality_detail={'base_x1': b['causality'], 'all_variable_costs_x1.5': x['causality']},
               book_data_detail={'base_x1': b['data'], 'all_variable_costs_x1.5': x['data']})
    return out


def _top1(xs):
    tot = math.fsum(xs)
    if not xs or not tot > 0:
        return None
    k = max(1, math.ceil(len(xs) * 0.01))
    return math.fsum(sorted(xs, reverse=True)[:k]) / tot


# ------------------------------------------------------------------ resumable streaming execution (authorized only)
RUN_FORMAT = 'zb-bpyr-run/1'
LINE_FORMAT = 'zb-bpyr-symbol/1'


def _fsync_append(fh, b: bytes):
    fh.write(b)
    fh.flush()
    os.fsync(fh.fileno())


def iter_chain(path: str, run_digest: str, plan: list):
    """Yield each verified line: canonical bytes, its own record hash, the prev chain, the run identity, the plan."""
    prev, n = run_digest, 0
    with open(path, 'rb') as fh:
        for line in fh:
            try:
                rec = json.loads(line)
                h = rec.pop('hash')
            except (ValueError, KeyError, AttributeError):
                raise RunnerError(f'symbols.jsonl line {n + 1} is not a hashed record; refusing') from None
            if line != canon(dict(rec, hash=h)) + b'\n' or sha(canon(rec)) != h:
                raise RunnerError(f'symbols.jsonl line {n + 1}: content does not match its hash; refusing')
            if rec.get('prev') != prev or rec.get('run') != run_digest:
                raise RunnerError(f'symbols.jsonl line {n + 1}: broken chain or another run identity; refusing')
            if n >= len(plan) or rec.get('sym') != plan[n]:
                raise RunnerError(f'symbols.jsonl line {n + 1} does not match the run plan; refusing to resume')
            prev, n = h, n + 1
            yield rec, h


def execute(outdir: str, src, pf: Preflight, *, symbols, rules, authorization: EvalAuthorization | None = None,
            max_rss_mb: float | None = None, rss=None, warmup_start: int | None = None, b0_replicates: int = 1000,
            stop_after: int | None = None, windows: Windows | None = None, resamples: int = 10000,
            universe: str = 'FIXED-17') -> dict:
    """Authorized streaming run. Pass 1 (resumable): one symbol at a time -> its E entries and the four priced unit
    replays as ONE canonical, hash-chained line of symbols.jsonl (flush + fsync); a resumed run re-verifies every
    retained line and continues. Pass 2: every symbol reloaded (RSS checked after each load), run() for B0 / B2,
    gates and books. Returns the summary in memory; run.json carries identity + disclosures. --max-rss-mb mandatory.
    The symbol plan must equal the frozen universe exactly; the source must expose a non-null immutable identity
    (a 64-hex `manifest_file_sha256`), bound into run.json and re-checked before and after pass 2; every pass-2 reload
    must reproduce the per-symbol source digest recorded in its verified pass-1 chain line."""
    _require_authorization(authorization, pf.prereg_sha256, 'execute()')
    if pf.code.get('dirty'):
        raise DirtyTree('the code tree is dirty; commit first')
    if max_rss_mb is None or not (isinstance(max_rss_mb, (int, float)) and math.isfinite(max_rss_mb) and max_rss_mb > 0):
        raise RunnerError('a finite --max-rss-mb is mandatory (low-RAM run)')
    if rss is None:
        import edge05_runner as E5
        rss = E5.rss_mb
    rss()

    def check():
        v = rss()
        if v > max_rss_mb:
            raise MemoryLimit(f'memory {v:.0f} MB > --max-rss-mb {max_rss_mb:g}; rerun the same command to resume')
    bind = getattr(src, 'set_memory', None)         # ArchiveSource: admission BEFORE each month expansion + cache budget
    if not callable(bind):
        raise RunnerError('the source cannot bind the --max-rss-mb guard (no set_memory); refusing before any read')
    bind(check, max_rss_mb)
    p = pf.prereg
    w = windows or Windows.of(p)
    ws = warmup_start if warmup_start is not None else iso_ms(p['windows']['calibration']['start'])
    plan = sorted(symbols)
    want = universe_symbols(p, universe)
    if set(plan) != want or len(plan) != len(want):
        raise IdentityMismatch(f'the symbol plan must be exactly {universe}: missing {sorted(want - set(plan))}, '
                               f'extra {sorted(set(plan) - want)}')
    src_id = source_identity(src)
    rules_id = rules_identity(rules)
    ledger_dir = os.path.abspath(os.path.join(REPO, *LEDGER_REL[:-1]))
    if os.path.commonpath([os.path.abspath(outdir), ledger_dir]) == ledger_dir:
        raise WriteRefused('the run directory may not be the ledger directory')
    ident = {'format': RUN_FORMAT, 'candidate_id': CANDIDATE, 'prereg_sha256': pf.prereg_sha256, 'code': pf.code,
             'sources': pf.sources, 'symbols': plan, 'windows': [w.dev_start, w.last_entry, w.sealed, list(w.slices)],
             'warmup_start': ws, 'slip': {'c': SLIP_C_CRYPTO, 'floor_bps': SLIP_FLOOR_BPS}, 'rows': list(ROWS),
             'b0_replicates': b0_replicates, 'resamples': resamples, 'disclosures': list(DISCLOSURES),
             'parity_declared': list(PARITY_DECLARED), 'authorization': authorization_identity(authorization),
             'source_identity': src_id, 'rules_identity': rules_id, 'universe': universe,
             'boundaries': {'holdout_read': False, 'ledger_written': False, 'verdict_written': False, 'mainnet': False}}
    os.makedirs(outdir, exist_ok=True)
    idp = os.path.join(outdir, 'run.json')
    ib = json.dumps(ident, sort_keys=True, indent=1).encode('ascii') + b'\n'
    if os.path.exists(idp):
        if open(idp, 'rb').read() != ib:
            raise RunnerError('run.json identity differs from this command/code; refusing to resume into it')
    else:
        with open(idp, 'wb') as fh:
            _fsync_append(fh, ib)
    run_digest = sha(canon(ident))
    lp = os.path.join(outdir, 'symbols.jsonl')
    done, prev = 0, run_digest
    if os.path.exists(lp):
        for _, h in iter_chain(lp, run_digest, plan):
            done, prev = done + 1, h
    s1 = stage_mgmt(p, 'S1')
    with open(lp, 'ab') as fh:
        for n in range(done, len(plan)):
            sym = plan[n]
            sd = load_symbol(src, sym, w, warmup_start=ws, check=check)
            check()
            E = entry_set({sym: sd}, w, s1, check)
            reps = {s: unit_replays({sym: sd}, E, p, s, check=check) for s in STAGE_IDS}
            rec = {'format': LINE_FORMAT, 'sym': sym, 'run': run_digest, 'prev': prev,
                   'source_sha256': sd.source_sha256,
                   'E': {'n': len(E['episodes']), 'incomplete': E['incomplete'], 'unfilled_gap': E['unfilled_gap']},
                   'stages': {s: {'episodes': [{k: e[k] for k in ('f', 't', 'slice', 'exit_j', 'why', 'adds')} |
                                               {'net_r': {r: e['rows'][r]['net_r'] for r in ROWS}}
                                               for e in r_['episodes']],
                                  'incomplete': [x['reason'] for x in r_['incomplete']]} for s, r_ in reps.items()}}
            del sd, E, reps
            check()
            prev = rec['hash'] = sha(canon(rec))
            _fsync_append(fh, canon(rec) + b'\n')
            if stop_after is not None and n + 1 - done >= stop_after:
                return {'status': 'PARTIAL', 'symbols_done': n + 1, 'symbols_total': len(plan)}
    bound = {rec['sym']: rec['source_sha256'] for rec, _ in iter_chain(lp, run_digest, plan)}   # re-verified chain
    if source_identity(src) != src_id:
        raise RunnerError('the source identity changed after pass 1; refusing pass 2')
    data = {}
    for sym in plan:
        data[sym] = load_symbol(src, sym, w, warmup_start=ws, check=check)
        if data[sym].source_sha256 != bound.get(sym):
            raise RunnerError(f'{sym}: source rows differ from the pass-1 chain line; refusing pass 2')
        check()
    res = run(data, pf, windows=w, rules=rules, b0_replicates=b0_replicates, resamples=resamples, check=check,
              universe=universe)
    if source_identity(src) != src_id:
        raise RunnerError('the source identity changed during pass 2; result discarded')
    if rules_identity(rules) != rules_id:
        raise RunnerError('the point-in-time rules identity changed during the run; result discarded')
    res['identity_sha256'] = run_digest
    res['source_identity'] = src_id
    res['rules_identity'] = rules_id
    res['evaluated'], res['decided'] = True, False     # authorized market-data statistics; no decision / verdict
    return res


def source_identity(src) -> dict:
    """A non-null immutable identity: the source's identity() with a 64-hex manifest_file_sha256 (ArchiveSource)."""
    ident = src.identity() if hasattr(src, 'identity') else None
    h = ident.get('manifest_file_sha256') if isinstance(ident, dict) else None
    if not (isinstance(h, str) and len(h) == 64 and all(ch in '0123456789abcdef' for ch in h)):
        raise RunnerError('the source has no immutable identity (manifest_file_sha256); refusing')
    return ident


class MemoryLimit(RunnerError):
    pass


# ------------------------------------------------------------------ guarded write
@dataclass(frozen=True)
class EvalAuthorization:
    """Issued only by Integration (codex) for an authorized evaluation; only load_authorization()
    constructs one, from a committed, hash-pinned artifact it has verified."""
    issuer: str
    comment_id: str
    prereg_sha256: str
    artifact_sha256: str | None = None       # set by load_authorization(); bound into run.json with the rest
    code_commit: str | None = None
    scope: tuple = ()


def _require_authorization(tok, prereg_sha256, where: str) -> None:
    """Fail-closed check of a COMPLETE verified token: issuer codex, ASCII-digit comment_id, this signed contract,
    a 64-hex artifact_sha256, a 40-hex code_commit and scope exactly ('development',). A token missing any field
    (or forged with another scope) is refused before anything is read or written."""
    if not isinstance(tok, EvalAuthorization):
        raise WriteRefused(f'{where}: no Integration evaluation authorization: results stay in memory')
    cid, a, cc = tok.comment_id, tok.artifact_sha256, tok.code_commit
    if tok.issuer != 'codex' or not isinstance(cid, str) or not cid.isascii() or not cid.isdigit() or \
            tok.prereg_sha256 != prereg_sha256:
        raise WriteRefused(f'{where}: authorization is not an Integration token for this signed contract')
    if not isinstance(a, str) or len(a) != 64 or not _HEX.fullmatch(a) or \
            not isinstance(cc, str) or len(cc) != 40 or not _HEX.fullmatch(cc) or \
            not isinstance(tok.scope, tuple) or tok.scope not in AUTH_SCOPES:
        raise WriteRefused(f'{where}: incomplete authorization (artifact_sha256 / code_commit / scope '
                           f'{[list(x) for x in AUTH_SCOPES]} are all required)')


def authorization_identity(tok: EvalAuthorization) -> dict:
    """The complete verified authorization as bound into run.json (a resume under another one fails identity)."""
    return {'issuer': tok.issuer, 'comment_id': tok.comment_id, 'prereg_sha256': tok.prereg_sha256,
            'artifact_sha256': tok.artifact_sha256, 'code_commit': tok.code_commit, 'scope': list(tok.scope)}


def write_result(result: dict, path: str, *, authorization: EvalAuthorization | None = None) -> str:
    """The only write path. Refuses without an Integration EvalAuthorization bound to this signed contract, and
    never writes a verdict, a ledger row or a registry line."""
    _require_authorization(authorization, result.get('prereg_sha256'), 'write_result()')
    ap = os.path.abspath(path)
    ledger_dir = os.path.abspath(os.path.join(REPO, *LEDGER_REL[:-1]))
    if os.path.commonpath([ap, ledger_dir]) == ledger_dir or 'verdict' in os.path.basename(ap).lower() \
            or 'verdict' in result or 'REGISTRY' in os.path.basename(ap):
        raise WriteRefused('verdict / ledger / registry writes are Integration-only')
    if result.get('decided') is not False:
        raise WriteRefused('a written result must be explicitly decided: false (no decision is made here)')
    with open(ap, 'xb') as f:
        f.write(canon(_json_safe(result)))
    return ap


def _json_safe(x):
    """Strict-JSON copy for writing: a non-finite float (an undefined Calmar / ratio, a nan mark) becomes the string
    'Infinity' / '-Infinity' / 'NaN' instead of making canon() refuse the whole result."""
    if isinstance(x, float) and not math.isfinite(x):
        return 'NaN' if x != x else ('Infinity' if x > 0 else '-Infinity')
    if isinstance(x, dict):
        return {k: _json_safe(v) for k, v in x.items()}
    if isinstance(x, (list, tuple)):
        return [_json_safe(v) for v in x]
    return x


def _iso(ms) -> str:
    if ms is None:
        return '?'
    return datetime.fromtimestamp(ms / 1000, timezone.utc).strftime('%Y-%m-%dT%H:%M:%SZ')


# ------------------------------------------------------------------ CLI (real ArchiveSource / PitRules wiring)
PREFLIGHT_FORMAT = 'zb-bpyr-preflight/1'
SERIES_KINDS = ('4h', 'funding', 'mark')


def _expect(path: str, expect: str | None, what: str) -> None:
    if not expect:
        raise RunnerError(f'--expect-{what}-sha256 is required with the {what} file')
    got = file_sha(path)
    if got != expect:
        raise RunnerError(f'{what} file sha256 {got} != expected {expect}; refusing')


def build_sources(a, manifest_path: str, symbols, cutoff_ms: int):
    """The real inputs, built as edge05_runner.main builds them: ArchiveSource over the archive manifest (the index
    only - no archive is opened here), the optional straddle artifact (+ daily source manifest) attached and
    authenticated, and the point-in-time rules artifact bound to its rules-source manifest. Every artifact is checked
    against an expected sha256 before it is parsed (SourceManifest.load enforces its own)."""
    import edge05_runner as E5
    src = E5.ArchiveSource(manifest_path, a.store, sorted(symbols), cutoff_ms, a.cache_months)
    if a.straddle_artifact:
        _expect(a.straddle_artifact, a.expect_straddle_sha256, 'straddle')
        daily = E5.SourceManifest.load(a.straddle_daily_manifest, a.expect_straddle_daily_sha256,
                                       'binance-vision-archive') if a.straddle_daily_manifest else None
        src.attach_straddle(E5.StraddleArtifact.load(a.straddle_artifact, cutoff_ms), daily)
    pit = None
    if a.rules_artifact:
        _expect(a.rules_artifact, a.expect_rules_sha256, 'rules')
        if not a.rules_source_manifest:
            raise RunnerError('--rules-source-manifest is required with --rules-artifact')
        rsrc = E5.SourceManifest.load(a.rules_source_manifest, a.expect_rules_source_sha256,
                                      'binance-um-exchangeinfo-snapshots', a.rules_snapshot_root)
        pit = E5.PitRules.load(a.rules_artifact, rsrc, a.max_rss_mb)
    return src, pit


def coverage_report(src, pit, symbols, lo: int, windows: Windows) -> dict:
    """Metadata-only readiness (no archive opened, no row read): per symbol and series the manifest's monthly files
    must tile [lo, sealed) up to the never-opened straddle month, whose pre-cutoff part needs GOOD (re-counted and
    authenticated) straddle-artifact coverage; the rules artifact must carry no bad / unauthenticated / unsupported
    entry and resolve every symbol at the first and the last possible entry time."""
    hi, gaps = windows.sealed, []
    for sym in sorted(symbols):
        for kind in SERIES_KINDS:
            cur = lo
            for m_lo, m_hi, *_ in src.index.get((sym, kind), ()):
                if m_hi <= cur:
                    continue
                if m_lo > cur:
                    break
                cur = m_hi
            if cur < hi and src.straddle_month.get((sym, kind)) == cur and src.straddle is not None \
                    and src.straddle.provenance.covered(sym, kind, cur, hi):
                cur = hi
            if cur < hi:
                gaps.append({'symbol': sym, 'series': kind, 'uncovered_from': _iso(cur)})
    rules_gaps, rid = [], None
    if pit is None:
        rules_ok = False
    else:
        rid = pit.identity()
        at = rules_of(pit)
        rules_gaps = [s for s in sorted(symbols)
                      if at(s, windows.dev_start) is None or at(s, windows.last_entry - TF) is None]
        rules_ok = not rules_gaps and not any(rid[k] for k in ('bad_coverage_entries', 'unauthenticated_entries',
                                                               'causally_unsupported_intervals', 'unsourced_intervals'))
    return {'series_gaps': gaps, 'rules_present': pit is not None, 'rules_gaps': rules_gaps, 'rules_ok': rules_ok,
            'straddle_files_never_opened': src.straddle_skipped, 'ready': not gaps and rules_ok}


AUTH_FORMAT = 'zb-eval-authorization/1'
AUTH_DIR_REL = ('research_evidence', 'authorizations')
AUTH_KEYS = frozenset({'format', 'candidate_id', 'prereg_sha256', 'issuer', 'comment_id', 'code_commit', 'scope'})
AUTH_SCOPES = (('development',),)        # execute() runs the whole development window; nothing narrower exists
_HEX = re.compile(r'[0-9a-f]+')


def authorization_bytes(fields: dict) -> bytes:
    """The canonical artifact bytes (canon(fields) + LF) Integration commits; no EvalAuthorization is made here."""
    return canon(fields) + b'\n'


def load_authorization(path: str, pf: Preflight, *, expect_sha256: str | None = None, repo: str = REPO,
                       git=None) -> EvalAuthorization:
    """The committed, identity-pinned verifier. Fail-closed (AuthorizationRefused) unless ALL hold:
      * pin     expect_sha256 (CLI --expect-authorization-sha256) is 64 lowercase hex and equals sha256(file bytes);
      * place   the real path sits directly under <repo>/research_evidence/authorizations/ (no symlink escape);
      * commit  the path is tracked and its LF-normalised bytes equal `git show HEAD:<path>`; HEAD is the preflight
                commit; artifact.code_commit is a STRICT ancestor of HEAD and `git diff --name-only code_commit HEAD` is
                exactly this artifact (the artifact cannot name the commit that contains it, so Integration commits
                it on top of the code commit it authorizes, and nothing else may change in between);
      * form    the bytes are canonical (authorization_bytes), keys exactly AUTH_KEYS, format zb-eval-authorization/1,
                candidate_id this candidate, prereg_sha256 == the signed contract hash, issuer 'codex', comment_id
                ASCII digits (the issuing GitHub comment), code_commit 40 lowercase hex, scope ['development'].
    No module is imported by name (PYTHONPATH / site-packages sit outside the dirty-tree check and the frozen code
    identity, so any such loader would be forgeable). `git(*args) -> stdout` is injectable for tests; the returned
    token carries artifact_sha256 / code_commit / scope, all bound into run.json by execute()."""
    def refuse(why):
        raise AuthorizationRefused(f'authorization refused ({path}): {why}')

    def real_git(*a):
        try:
            return subprocess.run(['git', '-C', repo, *a], capture_output=True, text=True, encoding='utf-8',
                                  check=True).stdout
        except (OSError, subprocess.CalledProcessError) as e:
            refuse(f'git {a[0]} failed ({e})')
    g = git or real_git
    if not isinstance(expect_sha256, str) or len(expect_sha256) != 64 or not _HEX.fullmatch(expect_sha256):
        refuse('--expect-authorization-sha256 (64 lowercase hex) is required')
    root = os.path.realpath(_p(repo, AUTH_DIR_REL))
    real = os.path.realpath(path)
    if os.path.dirname(real) != root or not os.path.isfile(real):
        refuse(f'the artifact must be a file directly under {"/".join(AUTH_DIR_REL)}/')
    with open(real, 'rb') as f:
        raw = f.read()
    if sha(raw) != expect_sha256:
        refuse(f'file sha256 {sha(raw)} != expected {expect_sha256}')
    rel = '/'.join(AUTH_DIR_REL + (os.path.basename(real),))
    if g('ls-files', '--', rel).strip() != rel:
        refuse('the artifact is not tracked (commit it)')
    body = raw.replace(b'\r\n', b'\n')
    if g('show', f'HEAD:{rel}').replace('\r\n', '\n').encode('utf-8') != body:
        refuse('the working-tree artifact differs from HEAD (modified or uncommitted)')
    head = g('rev-parse', 'HEAD').strip()
    if head != pf.code.get('commit'):
        refuse(f'HEAD {head} is not the preflight commit {pf.code.get("commit")}')

    def no_dupes(pairs):
        keys = [k for k, _ in pairs]
        if len(set(keys)) != len(keys):
            refuse('duplicate key')
        return dict(pairs)
    try:
        d = json.loads(body.decode('ascii'), object_pairs_hook=no_dupes)
    except (UnicodeDecodeError, ValueError):
        refuse('not ASCII JSON')
    if not isinstance(d, dict) or set(d) != AUTH_KEYS:
        refuse(f'keys must be exactly {sorted(AUTH_KEYS)}')
    if authorization_bytes(d) != body:
        refuse('not canonical (sorted keys, compact separators, ASCII, one trailing LF)')
    if d['format'] != AUTH_FORMAT or d['candidate_id'] != CANDIDATE:
        refuse('wrong format / candidate_id')
    if d['prereg_sha256'] != pf.prereg_sha256:
        refuse('prereg_sha256 is not the signed contract hash')
    if d['issuer'] != 'codex':
        refuse('issuer is not codex (Integration)')
    cid, cc, scope = d['comment_id'], d['code_commit'], d['scope']
    if not isinstance(cid, str) or not cid.isascii() or not cid.isdigit():
        refuse('comment_id must be an ASCII-digit string')
    if not isinstance(cc, str) or len(cc) != 40 or not _HEX.fullmatch(cc):
        refuse('code_commit must be 40 lowercase hex')
    if not isinstance(scope, list) or tuple(scope) not in AUTH_SCOPES:
        refuse(f'scope must be one of {[list(x) for x in AUTH_SCOPES]}')
    if cc == head:
        refuse(f'code_commit {cc} is HEAD; it must be a strict ancestor (the artifact is committed on top of it)')
    try:
        g('merge-base', '--is-ancestor', cc, head)
    except AuthorizationRefused:
        refuse(f'code_commit {cc} is not an ancestor of HEAD')
    if sorted(x for x in g('diff', '--name-only', cc, head).split('\n') if x.strip()) != [rel]:
        refuse(f'code_commit {cc} differs from HEAD in more than this artifact')
    return EvalAuthorization('codex', cid, d['prereg_sha256'], artifact_sha256=expect_sha256, code_commit=cc,
                             scope=tuple(scope))


def _runlock(out: str, universe: str, argv):
    import runlock
    return runlock.hold(REPO, 'bpyr-runner', {'out': os.path.abspath(out), 'universe': universe}, argv=argv)


def main(argv=None, *, _inject=None) -> int:
    """CLI. Exit 0 preflight report / authorized result written; 2 sealed-boundary refusal; 3 any other refusal;
    4 memory cap. `_inject` (tests only: git, manifest_check, manifest_path, rss, lock, authorize) is not reachable
    from argv."""
    import argparse
    import edge05_runner as E5
    inj = _inject or {}
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument('--store', required=True, help='Binance archive store root (read-only)')
    ap.add_argument('--max-rss-mb', type=float, required=True, help='mandatory private-memory cap (MB)')
    ap.add_argument('--universe', choices=('FIXED-17', 'STATIC-40'), default='FIXED-17')
    ap.add_argument('--rules-artifact', help='point-in-time exchange rules (zb-exchange-rules-pit)')
    ap.add_argument('--expect-rules-sha256')
    ap.add_argument('--rules-source-manifest', help='exchangeInfo snapshot list (zb-upstream-source-manifest)')
    ap.add_argument('--expect-rules-source-sha256')
    ap.add_argument('--rules-snapshot-root')
    ap.add_argument('--straddle-artifact', help='hash-bound pre-cutoff rows of the straddle month')
    ap.add_argument('--expect-straddle-sha256')
    ap.add_argument('--straddle-daily-manifest')
    ap.add_argument('--expect-straddle-daily-sha256')
    ap.add_argument('--cache-months', type=int, default=512)
    ap.add_argument('--authorization', help='Integration evaluation authorization; without it only preflight runs')
    ap.add_argument('--expect-authorization-sha256', help='sha256 of the committed authorization artifact')
    ap.add_argument('--out', help='run directory (authorized runs only)')
    a = ap.parse_args(argv)
    try:
        if not (math.isfinite(a.max_rss_mb) and a.max_rss_mb > 0):
            raise RunnerError('a finite, positive --max-rss-mb is mandatory')
        pf = preflight(git=inj.get('git'), manifest_check=inj.get('manifest_check'))     # dirty tree: DirtyTree
        p = pf.prereg
        w = Windows.of(p)
        ws = iso_ms(p['windows']['calibration']['start'])
        symbols = universe_symbols(p, a.universe)
        man = inj.get('manifest_path') or _p(REPO, tuple(p['source']['manifest'].split('/')))
        src, pit = build_sources(a, man, symbols, w.sealed)
        cov = coverage_report(src, pit, symbols, ws, w)
        report = {'format': PREFLIGHT_FORMAT, 'mode': 'authorized' if a.authorization else 'preflight-only',
                  'candidate_id': CANDIDATE, 'prereg_sha256': pf.prereg_sha256, 'code': pf.code,
                  'universe': a.universe, 'symbols': sorted(symbols), 'sealed_cutoff': _iso(w.sealed),
                  'warmup_start': _iso(ws), 'source_identity': source_identity(src),
                  'rules_identity': pit.identity() if pit is not None else None, 'coverage': cov,
                  'evaluated': False, 'written': None}
        if not a.authorization:
            print(json.dumps(report, sort_keys=True, indent=1))
            return 0
        tok = inj['authorize'](a.authorization, pf) if inj.get('authorize') else \
            load_authorization(a.authorization, pf, expect_sha256=a.expect_authorization_sha256)
        _require_authorization(tok, pf.prereg_sha256, 'authorized run')
        if not cov['ready']:
            raise RunnerError('inputs are not ready (series gaps / straddle / rules); see the preflight report')
        if not a.out:
            raise RunnerError('--out is required for an authorized run')
        with (inj.get('lock') or _runlock)(a.out, a.universe, argv):
            res = execute(a.out, src, pf, symbols=sorted(symbols), rules=rules_of(pit), authorization=tok,
                          max_rss_mb=a.max_rss_mb, rss=inj.get('rss'), universe=a.universe)
            path = write_result(res, os.path.join(a.out, 'result.json'), authorization=tok)
        print(json.dumps({'written': path, 'identity_sha256': res.get('identity_sha256')}, sort_keys=True))
        return 0
    except (MemoryLimit, E5.MemoryLimit) as e:
        print(f'ABORTED (memory): {e}', file=sys.stderr)
        return 4
    except (HoldoutRefused, E5.HoldoutRefused) as e:
        print(f'REFUSED (sealed): {e}', file=sys.stderr)
        return 2
    except (RunnerError, E5.RunnerError, E5.Incomplete, E5.CV.CoverageError, M.ManifestError, OSError,
            ValueError) as e:
        print(f'REFUSED: {e}', file=sys.stderr)
        return 3


if __name__ == '__main__':
    sys.exit(main())
