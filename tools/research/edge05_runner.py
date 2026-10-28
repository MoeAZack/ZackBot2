"""EDGE05 funding_carry_xsec.v1: low-RAM streaming research runner (executes the frozen, signed preregistration).

Authoritative spec: research_evidence/prereg/funding_carry_xsec.v1.json (read by this module; nothing else is policy).

Two passes, both streaming:
  pass 1 (resumable)  one UTC decision day D at a time: eligibility + F24 from only the rows available at D, the
                      k=5 per-side cohorts, and a per-unit replay (quantity 1) of every episode the books and baselines
                      need. Each day is ONE canonical JSON line appended (flush + fsync) to `days.jsonl`, carrying the
                      run-identity digest, the previous line's hash and its own record hash (a chain). A day is a pure
                      function of its inputs, so a resumed run is byte-identical to an uninterrupted one.
  pass 2 (summary)    re-streams and re-verifies `days.jsonl` line by line and runs every book - the primaries AND the
                      B0 / B1 / B2 baselines - through the same Book (sizing, leverage-cap scaling, quantity floors,
                      point-in-time rule refusals, costs); metrics and gates; writes `summary.json`.

Identity (`run.json`, bound into every line): prereg hash, universe, point-in-time exchange-rules artifact hash, source,
code identity (git commit + dirty flag + sha256 of this file and every repo module it imports), the optional holdout
freeze-record hash, and the `conventions` block (every Integration ruling, explicit). Resume verifies every retained
line (canonical bytes, record hash, chain, identity, plan key) and refuses on any difference.

Memory: per symbol and per day the source is asked only for that day's windows. `--max-rss-mb` is checked after every
symbol load and after every day; an abort never writes a partial day. Unmeasurable RSS with a limit set refuses to start.

Sealed boundary: every source read is clipped to its split and refused at or after min(sealed cutoff, holdout start),
before and after the read (HoldoutRefused). Archive months that straddle the cutoff are never opened. Calibration and
holdout splits are never evaluated.

No network, no exchange ids, no ledger write (Integration owns the ledger), no wall clock in any result byte.
"""
from __future__ import annotations

import argparse
import bisect
import gc
import hashlib
import json
import math
import os
import random
import re
import subprocess
import sys
import zipfile
from collections import OrderedDict, namedtuple
from dataclasses import dataclass

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import costs as C                                                                           # noqa: E402
import edge05_coverage as CV                                                                # noqa: E402
import manifest as M                                                                        # noqa: E402
import splits as S                                                                          # noqa: E402

sys.path.insert(0, M.REPO)
import feasibility as F                                                                     # noqa: E402

FORMAT = 'zb-edge05-run/6'
DAY_FORMAT = 'zb-edge05-day/2'
SUMMARY_FORMAT = 'zb-edge05-summary/4'
RULES_FORMAT = 'zb-exchange-rules-pit/5'
STRADDLE_FORMAT = 'zb-edge05-straddle/3'
AMENDMENT_FORMAT = 'zb-edge05-amendment/1'
SOURCE_MANIFEST_FORMAT = 'zb-upstream-source-manifest/2'
ACCOUNTING_REL = ('research_evidence', 'accounting', 'strat00-accounting-inputs-v1.json')
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
# Policy-bearing constants. Every one is re-derived from the frozen prereg (or the hash-bound registry input) at
# startup by `policy_of`; a drift between the code value and its source refuses the run.
AGE_DAYS = 30                                          # universe.eligibility_at_decision "at least 30 days before D"
DAILY_BARS = 15                                        # execution.r_unit "the 15 closed 1d bars ending at D"
ENTRY_OFFSET = 4 * HOUR                                # execution.entry: first 4h open at or after D + lag
LOOKBACK_BARS, HORIZON_BARS = 180, 6                   # windows.split_rule
B0_BOOKS = 1000                                        # baselines B0 "1000 seeded random books"
BOOT_BLOCK, BOOT_N, BOOT_SEED = 30, 10_000, 20261028   # gates.expectancy.lcb
HOLM_ALPHA = 0.05                                      # gates.multiplicity
DATA_MAX_INCOMPLETE = 0.02                             # gates.data "at most 2%"
SLIP_C_CRYPTO = 0.05                                   # slip-cal-v1 crypto c: strat00 accounting input costs.slip_c
SLIP_FLOOR_BPS = 5.0                                   # costs.slippage "the 0.05% unfitted fallback each market fill"
RESULT_FILES = ('days.jsonl', 'summary.json')
RULE_KEYS = ('tick', 'step', 'min_qty', 'min_notional', 'max_qty', 'max_notional')
INCOMPLETE_REASONS = ('incomplete.gap', 'incomplete.slip_history', 'incomplete.stop', 'incomplete.funding',
                      'incomplete.mark', 'incomplete.split_end', 'rules_uncovered', 'provenance_uncovered')
# INCOMPLETE reasons that are NEVER inside the 2% allowance: any one of them fails the data gate.
GATE_FAILING_REASONS = ('provenance_uncovered', 'rules_uncovered')
# every repo module this runner imports (a test re-derives the list in a fresh interpreter)
CODE_MODULES = ('feasibility.py', 'tools/research/costs.py', 'tools/research/edge05_coverage.py',
                'tools/research/edge05_runner.py', 'tools/research/manifest.py', 'tools/research/runlock.py',
                'tools/research/splits.py')
CONVENTIONS = {
    'slippage': 'per fill charged_bps = max(model_bps, floor_bps): model = slip-v1 TR-SMA14 on the 15 closed 4h bars, '
                'c = 0.05 (SLIP-C-UNFITTED); floor = 5 bp; both components recorded per fill (Integration ruling 1)',
    'worst_cohort_net_r': 'SUM of executable episode net R in one side/day cohort (Integration ruling 2)',
    'unresolved_accounting': 'every selected episode is OK (finite, reconciled: net = gross + fill_adj - fees + funding,'
                             ' r = net / dist) or INCOMPLETE with an enumerated reason; anything else is UNRESOLVED and '
                             'makes the side INCOMPLETE; the <=2% allowance counts only enumerated INCOMPLETE '
                             '(Integration ruling 3)',
    'incomplete_reasons': list(INCOMPLETE_REASONS),
    'gate_failing_reasons': list(GATE_FAILING_REASONS),
    'provenance': 'both input artifacts (PIT rules, straddle) carry {source: enumerated kind at its canonical endpoint '
                  '(no free text), source_manifest_sha256, upstream_files [{name in the exact published layout, '
                  'published sha256, as_of}], producer {commit, script, script_sha256}, coverage [{symbol, series, '
                  'from, to, rows, upstream}]}; the runner re-counts every coverage entry (a mismatch voids it); a '
                  'required (symbol, series, time) outside GOOD coverage is INCOMPLETE provenance_uncovered, which '
                  'fails the data gate (never inside the 2% allowance); a real run binds the expected artifact '
                  'sha256 (--expect-rules-sha256 / --expect-straddle-sha256) and refuses any other artifact',
    'source_authentication': 'artifact metadata is never self-authenticating. Straddle: every cited monthly archive '
                             'must EXIST in the run source manifest with the same sha256 (absent/mismatch refuses); '
                             'every cited daily archive must exist with the same sha256 and as_of in a separately '
                             'hash-bound daily source manifest (none bound -> its rows are unauthenticated); a '
                             'daily file with as_of after the cutoff or before its own data end is never used; the '
                             'authenticated files\' time spans must tile each coverage entry (a time-disjoint file '
                             'supports nothing); straddle rows themselves obey the prereg per-row availability rule '
                             '(pre-cutoff only). Rules: a hash-bound rules-source manifest lists every exchangeInfo '
                             'snapshot (sha256, capture as_of = the name time); every cited snapshot must exist and '
                             'hash-match; each interval names its snapshot and is causally supported only if that '
                             'snapshot\'s as_of <= the interval start (no backfill). Coverage/count checks run only on '
                             'authenticated, causally supported rows/intervals; anything else is provenance_uncovered',
    'snapshot_rule_values': 'each authenticated exchangeInfo snapshot an interval cites is read via the bound '
                            'rules-source manifest (contained path, sha256 re-verified while streaming, size-bounded, '
                            'admitted against the memory cap) and parsed deterministically: symbols[].filters[] -> '
                            'tick = PRICE_FILTER.tickSize; step = MARKET_LOT_SIZE.stepSize; min_qty = MARKET_LOT_SIZE.minQty; '
                            'max_qty = MARKET_LOT_SIZE.maxQty (every fill is a market order, so LOT_SIZE is never used; '
                            'Codex 6464073013); min_notional = '
                            'MIN_NOTIONAL.notional; max_notional = MAX_NOTIONAL.maxNotional when that filter exists, '
                            'else derived = PRICE_FILTER.maxPrice x MARKET_LOT_SIZE.maxQty (the largest notional the '
                            'snapshot filters admit). A symbol absent from the snapshot, or any missing/non-numeric '
                            'field or duplicated filter type, makes the interval unsupported (never defaulted); an '
                            'interval whose claimed tuple differs from the parsed tuple refuses the artifact. The '
                            'canonical per-symbol tuple digest of every parsed snapshot is bound in identity',
    'amendment': 'no ad-hoc end: the only shortened end is a signed zb-edge05-amendment/1 record for this exact '
                 'prereg, loaded with --expect-amendment-sha256, its sha256 and dev_end bound in identity',
    'codex_rulings_r4': {'AGE-ONLY-NO-COHORT-REPORTED': 'a no-cohort day caused only by listing age is counted and '
                                                        'reported, not INCOMPLETE',
                         'DATA-LOSS-NO-COHORT-GATED': 'a no-cohort day with any non-age ineligibility stays inside '
                                                      'the 2% data gate; a provenance_uncovered day fails it',
                         'ENTRY-EXIT-MAX-FILTERS': 'max qty/notional checked against the entry AND the exit rule',
                         'UW-COUNTS-RECOVERY-POINT': 'underwater duration includes the recovery point'},
    'exchange_rules': 'point-in-time artifact, per-symbol [from, to) intervals; the rule at the entry fill sizes and '
                      'ticks the entry/stop, the rule at the exit fill ticks the exit; either uncovered -> INCOMPLETE '
                      'rules_uncovered; never a current-rule substitution or an envelope (Integration ruling 4)',
    'holdout': 'dates frozen by Integration, opening prohibited; every read refused at/after min(sealed_cutoff, '
               'holdout.start); optional freeze-record sha256 bound in identity (Integration ruling 5)',
    'stop_bar_funding': 'intrabar stop: funding entry <= T < stop-bar close; gap stop: entry <= T < bar open',
    'split_contained_exit_bar': 'a day is scheduled only if its exit 4h bar closes inside its split',
    'fresh_books_per_split': '10000 USDT per side per split (every book, baselines included), compounding on closed '
                             'equity within the split',
    'b0_seed': "random.Random(int.from_bytes(sha256('funding_carry_xsec.v1|' + side + '|' + D_iso + '|' + i)[:8], "
               "'big')).sample(eligible sorted by symbol, k); D_iso = YYYY-MM-DDT00:00:00Z",
    'baselines_execution': 'B0 (1000 books), B1 (all eligible, equal risk) and B2 (sign-flipped) run through the same '
                           'Book as the primaries at base costs',
    'bootstrap': 'moving-block, non-circular, block 30 daily cohort means, 10000 resamples, seed 20261028; lcb95 = 5th '
                 'percentile (one-sided); p = (#means <= 0 + 1) / (N + 1); Holm alpha 0.05 over P-SHORT, P-LONG',
    'fold_chain': 'fresh books per fold; combined acceptance chains normalized fold equity chronologically (a '
                  'drawdown and its underwater run continue across a fold boundary): combined max DD, underwater days '
                  'and Calmar come from the chain; annualized return = chained growth over summed fold spans; closed-R '
                  'DD on the chained cumulative-R curve; PF in R; fold-local values reported alongside',
    'max_limits': 'an order whose quantity exceeds max_qty or whose notional exceeds max_notional of the entry-time '
                  'OR the exit-time rule is refused and counted (never clamped)',
    'data_gate': 'per walk-forward fold: <= 2% INCOMPLETE episodes in EACH of base, x1.5 and x2 (stress-only '
                 'INCOMPLETE counts); identical episode identities (symbol, entry) across base/x1.5/x2; no UNRESOLVED; '
                 'every scheduled day present; data-loss no-cohort days (a non-age ineligibility) <= 2% of scheduled '
                 'days; zero gate-failing INCOMPLETE (provenance_uncovered / rules_uncovered) in any episode and zero '
                 'days with a provenance_uncovered ineligibility; the 2% share counts only tolerable reasons; '
                 'missing capacity inputs count as infinite notional/volume in the capacity median',
    'sealed_month': 'an archive month containing rows at/after the cutoff is never opened; a real run needs the single '
                    'frozen provenance-complete straddle artifact (expected sha256 bound) or a signed amendment whose '
                    'dev_end is at or before that month',
    'memory': 'a finite --max-rss-mb is mandatory; measured = max(private committed, working set); every archive is '
              'admitted BEFORE expansion (compressed + max(3 x declared uncompressed, rows x 400 B) <= cap - current, '
              'evicting the month cache first, else refused); rows are parsed from the zip stream with a checkpoint '
              'every 2000 rows; the month cache is evicted by measured bytes (budget = cap / 4)',
}

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


def file_sha(path: str) -> str:
    h = hashlib.sha256()
    with open(path, 'rb') as f:
        for chunk in iter(lambda: f.read(1 << 20), b''):
            h.update(chunk)
    return h.hexdigest()


# ------------------------------------------------------------------ code identity
def code_identity(repo: str = M.REPO) -> dict:
    """git HEAD of the runner's repo, a dirty flag (any porcelain status line) and the sha256 of every imported module."""
    def git(*a):
        try:
            return subprocess.run(['git', '-C', repo, *a], capture_output=True, text=True, check=True).stdout
        except (OSError, subprocess.CalledProcessError) as e:
            raise RunnerError(f'cannot read the code identity from git ({e})') from None
    return {'commit': git('rev-parse', 'HEAD').strip(), 'dirty': bool(git('status', '--porcelain').strip()),
            'modules': {m: file_sha(os.path.join(repo, *m.split('/'))) for m in CODE_MODULES}}


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
    policy: dict


def prereg_path(repo: str = M.REPO) -> str:
    return os.path.join(repo, *PREREG_REL)


def load_prereg(path: str | None = None) -> tuple[dict, bytes]:
    raw = open(path or prereg_path(), 'rb').read()
    return json.loads(raw), raw


def accounting_path(repo: str = M.REPO) -> str:
    return os.path.join(repo, *ACCOUNTING_REL)


def _num(pattern: str, text: str, what: str) -> float:
    m = re.search(pattern, text)
    if not m:
        raise RunnerError(f'policy drift: {what} not found in the prereg text')
    return float(m.group(1))


def policy_of(p: dict, accounting: dict, costs_sha: str) -> dict:
    """Re-derive every policy-bearing code constant from the frozen prereg / registry input; refuse any drift."""
    sig, ex, w, g, co = p['signal'], p['execution'], p['windows'], p['gates'], p['costs']
    lag_ms = sig['availability_lag_seconds'] * 1000
    pol = {
        'age_days': _num(r'at least (\d+) days before D', p['universe']['eligibility_at_decision'], 'age rule'),
        'daily_bars': _num(r'the (\d+) closed 1d bars ending at D', ex['r_unit'], 'R-unit bars'),
        'entry_offset_ms': -(-lag_ms // TF) * TF,
        'entry_text_hour': _num(r'i\.e\. (\d\d):00 UTC', ex['entry'], 'entry hour'),
        'decision_hour': _num(r'^(\d\d):00 daily', ex['decision_time_utc'], 'decision time'),
        'lookback_bars': _num(r'lookback (\d+) bars', w['split_rule'], 'lookback'),
        'horizon_bars': _num(r'horizon (\d+) bars', w['split_rule'], 'horizon'),
        'signal_interval': _num(r'(\d+)h signal interval', w['split_rule'], 'signal interval'),
        'b0_books': _num(r'(\d+) seeded random books', p['baselines'][0]['rule'], 'B0 books'),
        'boot_block': _num(r'block (\d+) cohorts', g['expectancy']['lcb'], 'bootstrap block'),
        'boot_n': _num(r'(\d+) resamples', g['expectancy']['lcb'], 'bootstrap N'),
        'boot_seed': _num(r'seed (\d+)', g['expectancy']['lcb'], 'bootstrap seed'),
        'holm_alpha': _num(r'Holm alpha ([0-9.]+)', g['multiplicity'], 'Holm alpha'),
        'data_max_incomplete': _num(r'at most ([0-9.]+)% INCOMPLETE', g['data'], 'data gate') / 100,
        'taker': _num(r'taker ([0-9.]+)% each fill', co['fee'], 'taker fee') / 100,
        'slip_floor_bps': _num(r'the ([0-9.]+)% unfitted fallback', co['slippage'], 'slippage floor') * 100,
        'slip_c': float(accounting['costs']['slip_c']),
        'hold_bars': ex['holding_cap_hours'] * HOUR // TF,
    }
    if 'slip-cal-v1' not in co['slippage'] or 'slip-cal-v1 crypto' not in accounting['conventions']['slip_bps']:
        raise RunnerError('policy drift: the slippage coefficient source is not slip-cal-v1 crypto')
    if costs_sha != p['source']['costs_sha256']:
        raise RunnerError('policy drift: costs.py differs from the prereg-bound costs_sha256')
    if ex.get('maker') != 'none; every fill is taker':
        raise RunnerError('policy drift: maker policy')
    stress = {}
    for name, text in co['stress_rows'].items():
        m = re.search(r'fees x([0-9.]+), slippage x([0-9.]+), funding paid x([0-9.]+), funding received / ([0-9.]+)',
                      text)
        if m:
            vals = {float(x) for x in m.groups()}
            if len(vals) != 1:
                raise RunnerError(f'policy drift: {name} multipliers are not uniform')
            stress[name] = Stress(name, mult=vals.pop())
        elif text == 'fees x1, slippage x1, funding as settled':
            stress[name] = Stress(name)
        elif 'funding set to 0' in text:
            stress[name] = Stress(name, funding='zero')
        elif 'costs.py funding_mark_adverse row' in text:
            stress[name] = Stress(name, mark_bps=C.STRESS['funding_mark_adverse'].mark_adverse_bps)
        elif 'one 4h bar later' in text:
            stress[name] = Stress(name, latency_bars=1)
        else:
            raise RunnerError(f'policy drift: stress row {name!r} has no implementation')
    code = {'age_days': AGE_DAYS, 'daily_bars': DAILY_BARS, 'entry_offset_ms': ENTRY_OFFSET,
            'entry_text_hour': ENTRY_OFFSET // HOUR, 'decision_hour': 0, 'lookback_bars': LOOKBACK_BARS,
            'horizon_bars': HORIZON_BARS, 'signal_interval': TF // HOUR, 'b0_books': B0_BOOKS,
            'boot_block': BOOT_BLOCK, 'boot_n': BOOT_N, 'boot_seed': BOOT_SEED, 'holm_alpha': HOLM_ALPHA,
            'data_max_incomplete': DATA_MAX_INCOMPLETE, 'taker': C.TAKER, 'slip_floor_bps': SLIP_FLOOR_BPS,
            'slip_c': SLIP_C_CRYPTO, 'hold_bars': HORIZON_BARS}
    bad = sorted(k for k in code if not math.isclose(float(code[k]), float(pol[k]), rel_tol=1e-12, abs_tol=1e-15))
    if C.CRYPTO.taker != C.TAKER:
        bad.append('costs.CRYPTO.taker')
    if tuple(stress) != ROWS or stress != STRESS_ROWS:
        bad.append('stress_rows')
    if bad:
        raise RunnerError(f'policy drift between code and the frozen prereg/registry: {bad}; refusing')
    pol['stress_rows'] = {k: [v.mult, v.funding, v.mark_bps, v.latency_bars] for k, v in stress.items()}
    return pol


def params_of(p: dict, accounting: dict | None = None, costs_sha: str | None = None) -> Params:
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
    if accounting is None:
        accounting = json.loads(open(accounting_path(), 'rb').read())
    pol = policy_of(p, accounting, costs_sha or file_sha(C.__file__))
    return Params(policy=pol, k=sel['k'], lag_ms=sig['availability_lag_seconds'] * 1000, min_eligible=p['universe']['min_eligible'],
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


def split_end(plan: S.SplitPlan, key: str, end_ms: int | None = None) -> int:
    """The effective end of a split's reads: the split end, shortened only by a signed amendment's dev_end."""
    hi = plan.access_range(key)[1]
    return hi if end_ms is None else min(hi, end_ms)


def decision_days(plan: S.SplitPlan, key: str, prm: Params, end_ms: int | None = None) -> list[int]:
    """D (00:00 UTC) whose entry/hold sits in the split's decision range and whose exit bar is fully available
    inside the split (exit bar open + 4h <= split end, or <= the amendment dev_end, so its row never reads past it)."""
    if key not in evaluated_keys(plan):
        raise HoldoutRefused(f'{key} is never evaluated (calibration = warm-up only, holdout = sealed)')
    lo, hi = plan.access_range(key)
    first, last = plan.decision_range(key)
    if hi > prm.cutoff_ms:
        raise HoldoutRefused(f'{key} ends after the sealed cutoff')
    hi = split_end(plan, key, end_ms)
    out, d = [], -(-lo // DAY) * DAY
    while d < hi:
        entry = d + ENTRY_OFFSET
        if first <= entry <= last and entry + prm.hold_ms + TF <= hi:
            out.append(d)
        d += DAY
    return out


# ------------------------------------------------------------------ provenance (shared by both artifacts)
HEX64 = re.compile(r'[0-9a-f]{64}\Z')
HEX40 = re.compile(r'[0-9a-f]{40}\Z')
PROVENANCE_SOURCES = {                                  # the only accepted upstream kinds (no free-text source)
    'binance-vision-archive': M.ARCHIVE_URL,
    'binance-um-exchangeinfo-snapshots': 'https://fapi.binance.com/fapi/v1/exchangeInfo',
}
PROVENANCE_KEYS = {'source', 'source_manifest_sha256', 'upstream_files', 'producer', 'coverage'}
SNAPSHOT_RE = re.compile(r'exchangeInfo@(\d{4}-\d\d-\d\dT\d\d:\d\d:\d\dZ)\Z')
DAILY_RE = re.compile(r'um/daily/(fundingRate|klines|markPriceKlines)/([A-Z0-9]+)/(?:(1h|4h|1d)/)?'
                      r'\2-(fundingRate|1h|4h|1d)-(\d{4}-\d\d-\d\d)\.zip\Z')
ARCHIVE_KIND = {('fundingRate', None): 'funding', ('klines', '4h'): '4h', ('klines', '1d'): '1d',
                ('markPriceKlines', '1h'): 'mark'}


def _upstream_meta(kind: str, name: str):
    """(series, symbol, start_ms, end_ms) of one upstream file name, from its exact published layout."""
    if kind == 'binance-um-exchangeinfo-snapshots':
        m = SNAPSHOT_RE.match(name)
        if not m:
            raise RunnerError(f'provenance: {name!r} is not exchangeInfo@<UTC>')
        t = S.parse_utc(m.group(1))
        return 'rules', None, t, t
    m = DAILY_RE.match(name)
    if m:
        series, sym, iv, iv2, day = m.groups()
        if (iv or 'fundingRate') != iv2 or (series == 'fundingRate') != (iv is None):
            raise RunnerError(f'provenance: {name!r} interval mismatch')
        lo = S.parse_utc(day + 'T00:00:00Z')
        return ARCHIVE_KIND.get((series, iv)), sym, lo, lo + DAY
    try:
        meta = M.archive_meta(name)
    except M.ManifestError as e:
        raise RunnerError(f'provenance: {e}') from None
    return ARCHIVE_KIND.get((meta['series'], meta['interval'])), meta['symbol'], meta['month_start_ms'], \
        meta['month_end_ms']


SNAPSHOT_MAX_BYTES = 64 * 2 ** 20                       # an exchangeInfo snapshot is ~1-3 MB; refuse anything larger
SNAPSHOT_EXPAND = 12                                     # parsed JSON objects vs raw bytes (memory admission bound)


class _NoSource(Exception):
    pass


def _snap_num(filters: dict, ftype: str, key: str) -> float:
    x = filters.get(ftype)
    v = x.get(key) if isinstance(x, dict) else None
    if isinstance(v, bool) or not isinstance(v, (str, int, float)):
        raise _NoSource(f'{ftype}.{key}')
    try:
        f = float(v)
    except ValueError:
        raise _NoSource(f'{ftype}.{key}') from None
    if not math.isfinite(f):
        raise _NoSource(f'{ftype}.{key}')
    return f


def snapshot_rule_tuple(entry) -> tuple | None:
    """The canonical RULE_KEYS tuple of one exchangeInfo symbols[] entry, or None when any field has no source
    (missing filter / key, non-numeric, or a filter type listed twice). Field sources are fixed (see CONVENTIONS)."""
    filters = {}
    for x in (entry.get('filters') if isinstance(entry, dict) else None) or ():
        ft = x.get('filterType') if isinstance(x, dict) else None
        if not isinstance(ft, str) or ft in filters:
            return None
        filters[ft] = x
    try:
        tick = _snap_num(filters, 'PRICE_FILTER', 'tickSize')
        step = _snap_num(filters, 'MARKET_LOT_SIZE', 'stepSize')     # market orders: never LOT_SIZE (Codex 6464073013)
        min_qty = _snap_num(filters, 'MARKET_LOT_SIZE', 'minQty')
        max_qty = _snap_num(filters, 'MARKET_LOT_SIZE', 'maxQty')
        min_notional = _snap_num(filters, 'MIN_NOTIONAL', 'notional')
        max_notional = _snap_num(filters, 'MAX_NOTIONAL', 'maxNotional') if 'MAX_NOTIONAL' in filters else \
            _snap_num(filters, 'PRICE_FILTER', 'maxPrice') * max_qty
    except _NoSource:
        return None
    return (tick, step, min_qty, min_notional, max_qty, max_notional)          # RULE_KEYS order


def snapshot_tuples(doc, want) -> dict:
    """{symbol: tuple | None} for the wanted symbols present in an exchangeInfo document (absent = not a key)."""
    syms = doc.get('symbols') if isinstance(doc, dict) else None
    if not isinstance(syms, list):
        raise RunnerError('not an exchangeInfo snapshot (no symbols[] list)')
    out = {}
    for e in syms:
        sym = e.get('symbol') if isinstance(e, dict) else None
        if sym not in want:
            continue
        if sym in out:
            raise RunnerError(f'exchangeInfo snapshot lists {sym} twice')
        out[sym] = snapshot_rule_tuple(e)
    return out


class SourceManifest:
    """`zb-upstream-source-manifest/1`: an independently produced, hash-bound list of upstream files {format, kind,
    files: [{name, sha256 (published), as_of (capture / publication)}]}. kind 'binance-um-exchangeinfo-snapshots'
    (names exchangeInfo@<UTC>, as_of = that time) or 'binance-vision-archive' (DAILY archive names only, as_of >= the
    file's data end). Snapshot entries also carry `path` (relative to `root`), the only way a snapshot is read.
    Loaded only against an expected sha256."""

    def __init__(self, doc: dict, raw: bytes, expect_sha256: str | None, kind: str, root: str | None = None):
        self.sha256 = sha(raw)
        if not expect_sha256 or self.sha256 != expect_sha256:
            raise RunnerError(f'{kind} source manifest sha256 differs from its expected value (or none was given)')
        if not isinstance(doc, dict) or set(doc) != {'format', 'kind', 'files'} or \
                doc['format'] != SOURCE_MANIFEST_FORMAT or doc['kind'] != kind or not isinstance(doc['files'], list):
            raise RunnerError(f'source manifest must be {SOURCE_MANIFEST_FORMAT} of kind {kind}')
        self.kind, self.files, self.paths, self.root = kind, {}, {}, root
        keys = {'name', 'sha256', 'as_of'} | ({'path'} if kind == 'binance-um-exchangeinfo-snapshots' else set())
        for f in doc['files']:
            if not isinstance(f, dict) or set(f) != keys or not HEX64.match(str(f['sha256'])):
                raise RunnerError(f'source manifest entry {f!r} needs exactly {sorted(keys)}')
            if 'path' in f:
                self.paths[f['name']] = M.check_rel_path(f['path'])
            if f['name'] in self.files:
                raise RunnerError(f'source manifest lists {f["name"]} twice')
            meta = _upstream_meta(kind, f['name'])
            t = S.parse_utc(f['as_of'])
            if kind == 'binance-um-exchangeinfo-snapshots' and t != meta[2]:
                raise RunnerError(f'{f["name"]}: as_of must equal the snapshot capture time')
            if kind == 'binance-vision-archive' and (not DAILY_RE.match(f['name']) or t < meta[3]):
                raise RunnerError(f'{f["name"]}: a daily archive whose as_of precedes its own data end')
            self.files[f['name']] = (f['sha256'], t, meta)

    @classmethod
    def load(cls, path: str, expect_sha256: str | None, kind: str, root: str | None = None) -> 'SourceManifest':
        raw = open(path, 'rb').read()
        return cls(json.loads(raw), raw, expect_sha256, kind,
                   root if root is not None else os.path.dirname(os.path.abspath(path)))

    def identity(self) -> dict:
        return {'format': SOURCE_MANIFEST_FORMAT, 'kind': self.kind, 'sha256': self.sha256, 'files': len(self.files)}

    def snapshot_rules(self, name: str, want, max_mb: float | None = None) -> dict:
        """Read ONE snapshot through this manifest (contained path, size bound, memory admission, sha256 verified
        while streaming) and return {symbol: canonical tuple | None} for `want`."""
        if name not in self.paths or self.root is None:
            raise RunnerError(f'rules snapshot {name} has no readable path in the bound rules-source manifest')
        ap = M.contained(self.root, self.paths[name])
        try:
            size = os.path.getsize(ap)
        except OSError:
            raise RunnerError(f'rules snapshot {name}: file missing') from None
        if size > SNAPSHOT_MAX_BYTES:
            raise RunnerError(f'rules snapshot {name}: {size} bytes exceeds the {SNAPSHOT_MAX_BYTES} bound')
        if max_mb is not None:
            remaining = (max_mb - rss_mb()) * 2 ** 20
            if size * SNAPSHOT_EXPAND > remaining:
                raise MemoryLimit(f'rules snapshot {name}: parsing needs ~{size * SNAPSHOT_EXPAND / 2 ** 20:.1f} MB, '
                                  f'{max(remaining, 0) / 2 ** 20:.1f} MB remain; refused before reading')
        h, buf = hashlib.sha256(), bytearray()
        with open(ap, 'rb') as fh:
            for chunk in iter(lambda: fh.read(1 << 20), b''):
                h.update(chunk)
                buf += chunk
                if len(buf) > SNAPSHOT_MAX_BYTES:
                    raise RunnerError(f'rules snapshot {name}: grew past the size bound while reading')
        if h.hexdigest() != self.files[name][0]:
            raise RunnerError(f'rules snapshot {name}: bytes differ from the rules-source manifest sha256')
        try:
            doc = json.loads(bytes(buf))
        except ValueError as e:
            raise RunnerError(f'rules snapshot {name}: not JSON ({e})') from None
        del buf
        out = snapshot_tuples(doc, want)
        del doc
        gc.collect()
        return out


class Provenance:
    """Authenticity + completeness declaration of an input artifact: the upstream source kind and its canonical
    endpoint, the source-manifest sha256, every upstream file with its published sha256 and as-of time, the producer
    code identity, and the expected coverage (symbol x series x [from, to) with row counts), which the consuming
    artifact re-counts. A coverage entry whose count does not match is BAD and covers nothing."""

    def __init__(self, prov, kinds, series_ok):
        if not isinstance(prov, dict) or set(prov) != PROVENANCE_KEYS:
            raise RunnerError(f'provenance must have exactly {sorted(PROVENANCE_KEYS)}')
        src = prov['source']
        if not isinstance(src, dict) or set(src) != {'kind', 'endpoint'} or src['kind'] not in kinds \
                or src['endpoint'] != PROVENANCE_SOURCES[src['kind']]:
            raise RunnerError(f'provenance source must be one of {sorted(kinds)} at its canonical endpoint '
                              '(free-text sources are refused)')
        self.kind = src['kind']
        if not isinstance(prov['source_manifest_sha256'], str) or not HEX64.match(prov['source_manifest_sha256']):
            raise RunnerError('provenance source_manifest_sha256 must be a sha256')
        pr = prov['producer']
        if not isinstance(pr, dict) or set(pr) != {'commit', 'script', 'script_sha256'} \
                or not HEX40.match(str(pr['commit'])) or not HEX64.match(str(pr['script_sha256'])):
            raise RunnerError('provenance producer must be {commit: 40-hex, script, script_sha256}')
        M.check_rel_path(pr['script'])
        files, self.files, self.as_of = {}, {}, {}
        for f in prov['upstream_files'] or ():
            if not isinstance(f, dict) or set(f) != {'name', 'sha256', 'as_of'} or not HEX64.match(str(f['sha256'])):
                raise RunnerError(f'provenance upstream file {f!r} needs {{name, sha256 (published), as_of}}')
            S.parse_utc(f['as_of'])
            if f['name'] in files:
                raise RunnerError(f'provenance upstream file {f["name"]} listed twice')
            files[f['name']] = _upstream_meta(self.kind, f['name'])
            self.files[f['name']] = f['sha256']
            self.as_of[f['name']] = S.parse_utc(f['as_of'])
        if not files:
            raise RunnerError('provenance lists no upstream files')
        self.coverage = {}
        for c in prov['coverage'] or ():
            if not isinstance(c, dict) or set(c) != {'symbol', 'series', 'from', 'to', 'rows', 'upstream'}:
                raise RunnerError(f'coverage entry {c!r} needs {{symbol, series, from, to, rows, upstream}}')
            lo, hi = S.parse_utc(c['from']), S.parse_utc(c['to'])
            if c['series'] not in series_ok or not lo < hi or type(c['rows']) is not int or c['rows'] < 0 \
                    or not isinstance(c['upstream'], list) or not c['upstream']:
                raise RunnerError(f'invalid coverage entry {c!r}')
            for name in c['upstream']:
                if name not in files:
                    raise RunnerError(f'coverage entry cites unlisted upstream file {name!r}')
                ser, sym, _, _ = files[name]
                if ser != 'rules' and (ser != c['series'] or sym != c['symbol']):
                    raise RunnerError(f'coverage {c["symbol"]}/{c["series"]} cites {name} ({sym}/{ser})')
            # [from, to, rows, count_ok, authenticated, cited files]; unauthenticated until a source vouches
            self.coverage.setdefault((c['symbol'], c['series']), []).append([lo, hi, c['rows'], True, False,
                                                                             tuple(c['upstream'])])
        if not self.coverage:
            raise RunnerError('provenance declares no coverage')
        for v in self.coverage.values():
            v.sort()
            if any(b[0] < a[1] for a, b in zip(v, v[1:])):
                raise RunnerError('provenance coverage entries overlap')
        self.digest = sha(canon(prov))
        self.doc = {'kind': self.kind, 'source_manifest_sha256': prov['source_manifest_sha256'],
                    'producer': dict(pr), 'upstream_files': len(files), 'digest': self.digest}

    def recount(self, count) -> int:
        """`count(symbol, series, lo, hi)` -> actual rows; marks mismatching entries BAD. Returns the number BAD."""
        bad = 0
        for (sym, ser), v in self.coverage.items():
            for e in v:
                e[3] = count(sym, ser, e[0], e[1]) == e[2]
                bad += not e[3]
        return bad

    def authenticate(self, fn) -> int:
        """`fn(symbol, series, lo, hi, cited names)` -> True when authenticated, causal sources back the entry.
        Returns the number of entries left unauthenticated."""
        bad = 0
        for (sym, ser), v in self.coverage.items():
            for e in v:
                e[4] = bool(fn(sym, ser, e[0], e[1], e[5]))
                bad += not e[4]
        return bad

    def covered(self, sym: str, series: str, lo: int, hi: int) -> bool:
        """[lo, hi) lies inside the union of GOOD (re-counted AND authenticated) coverage entries."""
        cur = lo
        for a, b, _, ok, auth, _ in self.coverage.get((sym, series), ()):
            if not (ok and auth):
                continue
            if a <= cur < b:
                cur = b
            if cur >= hi:
                return True
        return cur >= hi


# ------------------------------------------------------------------ point-in-time exchange rules
class PitRules:
    """`zb-exchange-rules-pit/5`: {format, provenance, symbols: {SYM: [{from, to, snapshot, tick, step, min_qty,
    min_notional, max_qty, max_notional}]}} with strict UTC [from, to) intervals, sorted and non-overlapping. Every
    interval's values must equal the tuple parsed from its cited snapshot (else refused); a symbol/field the snapshot
    lacks leaves the interval unsupported. Provenance coverage
    (series 'rules', rows = intervals inside [from, to), which must tile it) is re-counted; `lookup` raises
    `provenance_uncovered` outside GOOD coverage and never substitutes another interval."""

    def __init__(self, doc: dict, raw: bytes, source: SourceManifest | None = None, max_mb: float | None = None):
        if not isinstance(doc, dict) or doc.get('format') != RULES_FORMAT or set(doc) != {'format', 'provenance',
                                                                                         'symbols'}:
            raise RunnerError(f'rules artifact must be {RULES_FORMAT} {{format, provenance, symbols}}')
        if not isinstance(doc.get('symbols'), dict):
            raise RunnerError('rules artifact needs a symbols map')
        self.sha256, self._iv, self.source, self.unsupported = sha(raw), {}, source, 0
        self.unsourced, self.snapshot_digests, tuples = 0, {}, {}
        self.provenance = Provenance(doc['provenance'], ('binance-um-exchangeinfo-snapshots',), ('rules',))
        if source is not None:
            if source.kind != 'binance-um-exchangeinfo-snapshots' or \
                    self.provenance.doc['source_manifest_sha256'] != source.sha256:
                raise RunnerError('rules artifact was produced from another rules-source manifest')
            for name, h in self.provenance.files.items():
                got = source.files.get(name)
                if got is None:
                    raise RunnerError(f'rules snapshot {name} is absent from the bound rules-source manifest')
                if got[0] != h or got[1] != self.provenance.as_of[name]:
                    raise RunnerError(f'rules snapshot {name}: sha256/as_of differ from the rules-source manifest')
            want = set(doc['symbols'])
            cited = sorted({iv.get('snapshot') for ivs in doc['symbols'].values() for iv in ivs
                            if isinstance(iv, dict) and iv.get('snapshot') in source.files})
            for name in cited:                                  # one snapshot in memory at a time
                tuples[name] = source.snapshot_rules(name, want, max_mb)
                self.snapshot_digests[name] = sha(canon({k: list(v) if v is not None else None
                                                         for k, v in sorted(tuples[name].items())}))
        for sym, ivs in doc['symbols'].items():
            rows = []
            for iv in ivs:
                try:
                    lo, hi = S.parse_utc(iv['from']), S.parse_utc(iv['to'])
                    rule = {k: float(iv[k]) for k in RULE_KEYS}
                except (KeyError, TypeError, ValueError, S.SplitError) as e:
                    raise RunnerError(f'{sym}: bad rules interval {iv!r} ({e})') from None
                if not lo < hi or not all(math.isfinite(v) for v in rule.values()) or rule['tick'] <= 0 \
                        or rule['step'] <= 0 or rule['min_qty'] < 0 or rule['min_notional'] < 0 \
                        or rule['max_qty'] <= 0 or rule['max_notional'] <= 0:
                    raise RunnerError(f'{sym}: invalid rules interval {iv!r}')
                snap = iv.get('snapshot')
                if snap not in self.provenance.files:
                    raise RunnerError(f'{sym}: interval {iv!r} must name a cited upstream snapshot')
                rows.append((lo, hi, dict(rule, **{'from': iv['from'], 'to': iv['to'], 'snapshot': snap})))
            rows.sort(key=lambda r: r[0])
            if any(b[0] < a[1] for a, b in zip(rows, rows[1:])):
                raise RunnerError(f'{sym}: rules intervals overlap')
            # value support: the interval's tuple must be exactly the one parsed from its snapshot
            sourced = []
            for r in rows:
                parsed = tuples.get(r[2]['snapshot'], {}).get(sym)
                if parsed is None:
                    self.unsourced += 1                       # symbol/field absent from the snapshot: unsupported
                    continue
                if tuple(r[2][k] for k in RULE_KEYS) != parsed:
                    raise RunnerError(f'{sym} {r[2]["from"]}: rule values differ from snapshot {r[2]["snapshot"]} '
                                      f'({dict(zip(RULE_KEYS, parsed))})')
                sourced.append(r)
            # causal support: an authenticated snapshot captured at or before the interval start (no backfill)
            ok = [r for r in sourced if source.files[r[2]['snapshot']][1] <= r[0]]
            self.unsupported += len(rows) - len(ok)
            self._iv[sym] = ([r[0] for r in ok], ok)
        self.bad_coverage = self.provenance.recount(self._count)
        self.unauthenticated = self.provenance.authenticate(
            lambda s, k, lo, hi, names: source is not None and all(n in source.files for n in names))

    def _count(self, sym, series, lo, hi):
        """Intervals inside [lo, hi); -1 unless they tile it exactly (a gap is a count mismatch)."""
        rows = [r for r in self._iv.get(sym, ((), ()))[1] if lo <= r[0] and r[1] <= hi]
        cur = lo
        for a, b, _ in rows:
            if a != cur:
                return -1
            cur = b
        return len(rows) if cur == hi else -1

    @classmethod
    def load(cls, path: str, source: SourceManifest | None = None, max_mb: float | None = None) -> 'PitRules':
        raw = open(path, 'rb').read()
        return cls(json.loads(raw), raw, source, max_mb)

    def identity(self) -> dict:
        return {'format': RULES_FORMAT, 'sha256': self.sha256, 'provenance': self.provenance.doc,
                'source_manifest': self.source.identity() if self.source else None,
                'bad_coverage_entries': self.bad_coverage, 'unauthenticated_entries': self.unauthenticated,
                'causally_unsupported_intervals': self.unsupported, 'unsourced_intervals': self.unsourced,
                'snapshot_tuple_digests': dict(sorted(self.snapshot_digests.items()))}

    def at(self, sym: str, t: int):
        starts, rows = self._iv.get(sym, ((), ()))
        i = bisect.bisect_right(starts, t) - 1
        if i < 0 or not rows[i][0] <= t < rows[i][1]:
            return None
        return dict(rows[i][2])

    def lookup(self, sym: str, t: int):
        """The rule in force at t; Incomplete('provenance_uncovered') outside GOOD coverage (gate-failing)."""
        if not self.provenance.covered(sym, 'rules', t, t + 1):
            raise Incomplete('provenance_uncovered', t)
        return self.at(sym, t)


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


class StraddleArtifact:
    """`zb-edge05-straddle/2`: hash-bound PRE-CUTOFF rows of the archive month that straddles the sealed cutoff,
    produced by Integration without this runner ever opening that month. {format, cutoff, provenance, series:
    {funding: {SYM: [[time_ms, rate, interval_h], ...]}, 4h | 1d | mark: {SYM: [[open_ms, o, h, l, c, v, quote_v],
    ...]}}}. Every row must lie in the cutoff's month, be available before the cutoff (bars: open + interval <=
    cutoff) and fall inside a declared coverage entry; each entry's row count is re-counted (a mismatch = BAD)."""

    IV = {'4h': TF, '1d': DAY, 'mark': HOUR}

    def __init__(self, doc: dict, raw: bytes, cutoff_ms: int):
        if not isinstance(doc, dict) or doc.get('format') != STRADDLE_FORMAT or \
                set(doc) != {'format', 'cutoff', 'provenance', 'series'}:
            raise RunnerError(f'straddle artifact must be {STRADDLE_FORMAT} {{format, cutoff, provenance, series}}')
        if doc.get('cutoff') != S.utc(cutoff_ms):
            raise HoldoutRefused('straddle artifact cutoff differs from the run cutoff')
        self.sha256 = sha(raw)
        self.provenance = Provenance(doc['provenance'], ('binance-vision-archive',), ('funding', '4h', '1d', 'mark'))
        mon = CV.parse_utc(CV.month_key(cutoff_ms) + '-01T00:00:00Z')
        self.month_start = mon
        self.rows = {}
        for kind, per in doc['series'].items():
            if kind not in ('funding', '4h', '1d', 'mark'):
                raise RunnerError(f'straddle artifact: unknown series {kind!r}')
            for sym, rows in per.items():
                out = []
                for r in rows:
                    if kind == 'funding':
                        x = Funding(int(r[0]), float(r[1]), int(r[2]), int(r[0]))
                        avail = x.time_ms
                    else:
                        x = Bar(int(r[0]), *map(float, r[1:7]), int(r[0]) + self.IV[kind])
                        avail = x.available_ms
                    if x[0] < mon or avail > cutoff_ms or x[0] >= cutoff_ms:
                        raise HoldoutRefused(f'straddle artifact row {sym} {kind} {x[0]} is outside '
                                             '[cutoff month start, cutoff)')
                    if not any(e[0] <= x[0] < e[1] for e in self.provenance.coverage.get((sym, kind), ())):
                        raise RunnerError(f'straddle artifact row {sym} {kind} {x[0]} is outside every declared '
                                          'coverage entry')
                    out.append(x)
                out.sort()
                self.rows[(sym, kind)] = ([x[0] for x in out], out)
        self.bad_coverage = self.provenance.recount(lambda s, k, lo, hi: len(self.slice(s, k, lo, hi)))
        self.cutoff = cutoff_ms
        self.unauthenticated = self.provenance.authenticate(lambda *a: False)     # until a source vouches for it

    @classmethod
    def load(cls, path: str, cutoff_ms: int) -> 'StraddleArtifact':
        raw = open(path, 'rb').read()
        return cls(json.loads(raw), raw, cutoff_ms)

    def identity(self) -> dict:
        return {'format': STRADDLE_FORMAT, 'sha256': self.sha256, 'provenance': self.provenance.doc,
                'bad_coverage_entries': self.bad_coverage, 'unauthenticated_entries': self.unauthenticated}

    def slice(self, sym, kind, lo, hi):
        keys, rows = self.rows.get((sym, kind), ((), ()))
        return list(rows[bisect.bisect_left(keys, lo):bisect.bisect_left(keys, hi)])


def _deep_size(rows) -> int:
    """Measured bytes of a parsed month: the list, every row tuple and every field object."""
    n = sys.getsizeof(rows)
    for r in rows:
        n += sys.getsizeof(r) + sum(sys.getsizeof(x) for x in r)
    return n


class ArchiveSource:
    """Binance archive store, indexed from a streamed manifest (never loaded whole). A month is ADMITTED before it is
    expanded (compressed + declared uncompressed + per-row parsed bytes against the remaining memory budget, evicting
    first), then parsed row by row from the zip stream with a memory checkpoint every CHECK_ROWS rows; the month cache
    is evicted by MEASURED bytes. A month file whose last row is at/after the sealed cutoff (the straddle month) is
    never opened; reads that reach into it need a provenance-complete straddle artifact covering them."""

    SERIES = {('fundingRate', None): 'funding', ('klines', '4h'): '4h', ('klines', '1d'): '1d',
              ('markPriceKlines', '1h'): 'mark'}
    ROW_BYTES = 400                                # admission estimate per parsed row (a Bar is ~330 B measured)
    EXPAND_FACTOR = 3                              # parsed objects vs declared CSV bytes (admission upper bound)
    CHECK_ROWS = 2000
    LINE_MAX = 4096

    def __init__(self, manifest_path: str, store: str, symbols, cutoff_ms: int, cache_months: int = 512):
        self.store, self.cutoff, self.cap = store, cutoff_ms, cache_months
        want = set(symbols)
        self.manifest_sha256 = file_sha(manifest_path)
        self.index: dict = {}
        self.first: dict = {}
        self.straddle_skipped = 0
        self.straddle_from = None                  # month start of the earliest never-opened straddle month
        self.straddle_month: dict = {}             # (symbol, kind) -> start of its never-opened straddle month
        self.straddle_files: dict = {}             # never-opened straddle month path -> manifest sha256
        self.manifest_files: dict = {}             # every listed universe file: path -> (sha256, [month lo, hi))
        self.daily = None                          # bound daily-archive SourceManifest (straddle daily files)
        self.straddle = None                       # StraddleArtifact serving that month's pre-cutoff rows
        self.check = None
        self.max_mb = None
        self.cache_budget = None                   # bytes; None = count cap only (no memory cap set yet)
        self.cache_bytes = 0
        for e in CV.iter_file_entries(manifest_path):
            kind = self.SERIES.get((e.get('series'), e.get('interval')))
            if kind is None or e.get('symbol') not in want:
                continue
            first, last = e['first_open_ms'], e['last_open_ms']
            if first is None:
                continue
            fk = (e['symbol'], kind)
            self.first[fk] = first if fk not in self.first else min(self.first[fk], first)
            meta = M.archive_meta(e['path'])
            self.manifest_files[e['path']] = (e['sha256'], meta['month_start_ms'], meta['month_end_ms'])
            if last >= cutoff_ms:
                self.straddle_skipped += 1
                self.straddle_files[e['path']] = e['sha256']
                if meta['month_start_ms'] < cutoff_ms:
                    self.straddle_from = meta['month_start_ms'] if self.straddle_from is None else \
                        min(self.straddle_from, meta['month_start_ms'])
                    self.straddle_month[fk] = min(self.straddle_month.get(fk, meta['month_start_ms']),
                                                  meta['month_start_ms'])
                continue
            self.index.setdefault(fk, []).append((meta['month_start_ms'], meta['month_end_ms'], e['path'], e['sha256'],
                                                  e['rows']))
        for v in self.index.values():
            v.sort()
        self._lru: OrderedDict = OrderedDict()     # path -> (keys, rows, measured bytes)

    def identity(self) -> dict:
        return {'kind': 'binance-archive', 'manifest_file_sha256': self.manifest_sha256,
                'daily_source_manifest': self.daily.identity() if self.daily else None,
                'straddle': self.straddle.identity() if self.straddle else None}

    def attach_straddle(self, art: StraddleArtifact, daily: SourceManifest | None = None) -> None:
        """Authenticate the artifact against INDEPENDENT sources. It must name this run's source manifest; every
        cited monthly archive must exist in that manifest with the same sha256 (absent or different refuses); every
        cited daily archive must exist with the same sha256/as_of in the bound daily source manifest (none bound ->
        unauthenticated), and is used only when its as_of is at or before the cutoff. A coverage entry is
        authenticated only when its usable files' time spans tile it."""
        if art.provenance.doc['source_manifest_sha256'] != self.manifest_sha256:
            raise RunnerError('straddle artifact was produced from another source manifest')
        if daily is not None and daily.kind != 'binance-vision-archive':
            raise RunnerError('the straddle daily source manifest must be of kind binance-vision-archive')
        spans = {}
        for name, h in art.provenance.files.items():
            if DAILY_RE.match(name):
                if daily is None:
                    continue                                       # unauthenticated: no bound daily manifest
                got = daily.files.get(name)
                if got is None:
                    raise RunnerError(f'straddle upstream {name} is absent from the bound daily source manifest')
                if got[0] != h or got[1] != art.provenance.as_of[name]:
                    raise RunnerError(f'straddle upstream {name}: sha256/as_of differ from the daily source manifest')
                if got[1] <= self.cutoff:
                    spans[name] = (got[2][2], got[2][3])
            else:
                got = self.manifest_files.get(name)
                if got is None:
                    raise RunnerError(f'straddle upstream {name} is absent from the run source manifest')
                if got[0] != h:
                    raise RunnerError(f'straddle upstream hash for {name} differs from the manifest')
                spans[name] = (got[1], got[2])

        def tiled(sym, ser, lo, hi, names):
            cur = lo
            for a, b in sorted(spans[n] for n in names if n in spans):
                if a <= cur < b:
                    cur = b
            return cur >= hi
        art.unauthenticated = art.provenance.authenticate(tiled)
        self.straddle, self.daily = art, daily

    def set_memory(self, check, max_mb: float) -> None:
        """Memory checkpoint during every expansion; admission against the cap; cache budget = 1/4 of the cap."""
        self.check, self.max_mb = check, max_mb
        self.cache_budget = int(max_mb * 2 ** 20 // 4)

    def _evict_one(self) -> None:
        _, (_, _, nb) = self._lru.popitem(last=False)
        self.cache_bytes -= nb
        gc.collect()

    def _admit(self, path: str, need: int) -> None:
        """Refuse (or evict first) BEFORE expansion when `need` bytes do not fit the remaining budget."""
        if self.max_mb is None:
            return
        while True:
            remaining = (self.max_mb - rss_mb()) * 2 ** 20
            if need <= remaining:
                return
            if not self._lru:
                raise MemoryLimit(f'{path}: expansion needs ~{need / 2 ** 20:.1f} MB but only '
                                  f'{max(remaining, 0) / 2 ** 20:.1f} MB of --max-rss-mb {self.max_mb:g} remain; '
                                  'refused before decompressing')
            self._evict_one()

    def _stream_rows(self, f, path, meta, kind, expect_rows):
        """Row-by-row parse of the zip member stream, mirroring manifest.parse_archive_csv's checks."""
        funding = kind == 'funding'
        header, ncol = (M.FUNDING_HEADER, 3) if funding else (M.KLINE_HEADER, 12)
        lo, hi = meta['month_start_ms'], meta['month_end_ms']
        step = None if funding else M.INTERVALS[meta['interval']]
        out, prev, first = [], None, True
        while True:
            ln = f.readline(self.LINE_MAX + 1)
            if not ln:
                break
            if len(ln) > self.LINE_MAX:
                raise RunnerError(f'{path}: a line exceeds {self.LINE_MAX} bytes')
            ln = ln.rstrip(b'\n')
            if ln.endswith(b'\r'):
                ln = ln[:-1]
            if first:
                first = False
                if ln == header:
                    continue
            r = ln.split(b',')
            if len(r) != ncol:
                raise RunnerError(f'{path}: a row does not have {ncol} columns (or the header is unknown)')
            try:
                t = int(r[0])
                if funding:
                    row = Funding(t, float(r[2]), int(r[1]), t)
                else:
                    if int(r[6]) != t + step - 1 or t % step:
                        raise RunnerError(f'{path}: misaligned bar or close_time != open_time + interval - 1')
                    if not M.QV_RE.match(r[7]):
                        raise RunnerError(f'{path}: quote_volume {r[7]!r} is not a canonical decimal')
                    row = Bar(t, float(r[1]), float(r[2]), float(r[3]), float(r[4]), float(r[5]), float(r[7]),
                              t + step)
            except ValueError as e:
                raise RunnerError(f'{path}: non-numeric field ({e})') from None
            if not lo <= t < hi or (prev is not None and t <= prev):
                raise RunnerError(f'{path}: row time outside the file month or not strictly increasing')
            prev = t
            out.append(row)
            if len(out) > expect_rows:
                raise RunnerError(f'{path}: more rows than the manifest declares ({expect_rows})')
            if self.check and len(out) % self.CHECK_ROWS == 0:
                self.check()
        return out

    def _month(self, path, sha256, rows, kind):
        if path in self._lru:
            self._lru.move_to_end(path)
            return self._lru[path][:2]
        ap = M.contained(self.store, path)
        meta = M.archive_meta(path)
        if self.check:
            self.check()
        try:
            published = open(ap + '.ok', 'rb').read().strip().decode('ascii')
        except (OSError, UnicodeDecodeError):
            raise RunnerError(f'{path}: missing or unreadable .ok checksum sidecar') from None
        csv_name = os.path.basename(path)[:-4] + '.csv'
        with open(ap, 'rb') as fh:
            h = hashlib.sha256()
            for chunk in iter(lambda: fh.read(1 << 20), b''):
                h.update(chunk)
            if h.hexdigest() != published or published != sha256:
                raise RunnerError(f'{path}: bytes no longer match the published checksum / manifest sha256')
            fh.seek(0)
            try:
                with zipfile.ZipFile(fh) as z:
                    infos = z.infolist()
                    if [i.filename for i in infos] != [csv_name]:
                        raise RunnerError(f'{path}: zip must hold exactly {csv_name}')
                    info = infos[0]
                    need = info.compress_size + max(info.file_size * self.EXPAND_FACTOR, rows * self.ROW_BYTES)
                    self._admit(path, need)
                    with z.open(info) as f:
                        out = self._stream_rows(f, path, meta, kind, rows)
            except zipfile.BadZipFile as e:
                raise RunnerError(f'{path}: bad zip ({e})') from None
        if len(out) != rows:
            raise RunnerError(f'{path}: {len(out)} rows != manifest {rows}')
        if self.check:
            self.check()
        keys = [r[0] for r in out]
        nb = _deep_size(out) + sys.getsizeof(keys)
        self._lru[path] = (keys, out, nb)
        self.cache_bytes += nb
        while len(self._lru) > 1 and (len(self._lru) > self.cap or
                                      (self.cache_budget is not None and self.cache_bytes > self.cache_budget)):
            self._evict_one()
        return keys, out

    def _rows(self, sym, kind, lo, hi):
        out = []
        for m_lo, m_hi, path, sha256, rows in self.index.get((sym, kind), ()):
            if m_hi <= lo or m_lo >= hi:
                continue
            keys, rs = self._month(path, sha256, rows, kind)
            out.extend(rs[bisect.bisect_left(keys, lo):bisect.bisect_left(keys, hi)])
        sm = self.straddle_month.get((sym, kind))
        if sm is not None and hi > sm and lo < self.cutoff:
            a, b = max(lo, sm), min(hi, self.cutoff)
            if self.straddle is None or not self.straddle.provenance.covered(sym, kind, a, b):
                raise Incomplete('provenance_uncovered', a)
            out.extend(self.straddle.slice(sym, kind, a, b))
        return out

    def funding(self, sym, lo, hi):
        return self._rows(sym, 'funding', lo, hi)

    def bars(self, sym, interval, lo, hi):
        return self._rows(sym, interval, lo, hi)

    def mark(self, sym, lo, hi):
        return self._rows(sym, 'mark', lo, hi)

    def first_ms(self, sym):
        return {'funding': self.first.get((sym, 'funding')), '4h': self.first.get((sym, '4h'))}


class Amendment:
    """`zb-edge05-amendment/1`: the ONLY route to a shortened end. A signed record amending the frozen prereg, read
    from a hash-bound file: {format, candidate_id, prereg_sha256, status (FROZEN...), signed: true, signed_by,
    dev_end (UTC midnight <= cutoff), reason}. Its sha256 and exact end are bound in the run identity."""

    KEYS = {'format', 'candidate_id', 'prereg_sha256', 'status', 'signed', 'signed_by', 'dev_end', 'reason'}

    def __init__(self, doc: dict, raw: bytes, prereg_raw: bytes, cutoff_ms: int, expect_sha256: str | None):
        self.sha256 = sha(raw)
        if not expect_sha256 or self.sha256 != expect_sha256:
            raise RunnerError('amendment record sha256 differs from --expect-amendment-sha256 (or none was given)')
        if not isinstance(doc, dict) or set(doc) != self.KEYS or doc['format'] != AMENDMENT_FORMAT \
                or doc['candidate_id'] != CANDIDATE or doc['prereg_sha256'] != sha(prereg_raw):
            raise RunnerError(f'amendment must be {AMENDMENT_FORMAT} for this candidate and this exact prereg')
        if doc['signed'] is not True or not str(doc['status']).startswith('FROZEN') \
                or not isinstance(doc['signed_by'], str) or not doc['signed_by'].strip() \
                or not isinstance(doc['reason'], str) or not doc['reason'].strip():
            raise RunnerError('amendment is not frozen and signed (status FROZEN..., signed true, signed_by, reason)')
        self.dev_end = S.parse_utc(doc['dev_end'])
        if self.dev_end % DAY or self.dev_end > cutoff_ms:
            raise HoldoutRefused('amendment dev_end must be a UTC midnight at or before the sealed cutoff')
        self.doc = doc

    @classmethod
    def load(cls, path: str, prereg_raw: bytes, cutoff_ms: int, expect_sha256: str | None) -> 'Amendment':
        raw = open(path, 'rb').read()
        return cls(json.loads(raw), raw, prereg_raw, cutoff_ms, expect_sha256)

    def identity(self) -> dict:
        return {'format': AMENDMENT_FORMAT, 'sha256': self.sha256, 'dev_end': self.doc['dev_end'],
                'signed_by': self.doc['signed_by']}


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


def slip(bars4: dict, at: int) -> dict:
    """slip-v1 on the 15 closed 4h bars ending at `at` (c = slip-cal-v1 crypto) and the 5 bp floor; the charged bps is
    the larger (Integration ruling 1). Both components are returned so every fill records them."""
    win = []
    for o in range(at - 15 * TF, at, TF):
        b = bars4.get(o)
        if b is None:
            raise Incomplete('incomplete.slip_history', o)
        win.append(b)
    model = C.slip_bps(win, SLIP_C_CRYPTO)
    return {'model_bps': model, 'floor_bps': SLIP_FLOOR_BPS, 'charged_bps': max(model, SLIP_FLOOR_BPS)}


def _tick_round(px, tick, action):
    return C.round_adverse(px, tick, action) if tick else px


def replay(side: str, D: int, st: Stress, dist: float, day: dict, rules_at, prm: Params, split_hi: int) -> dict:
    """Per-unit (qty 1) replay of one episode. `rules_at(t)` -> the point-in-time rule or None.
    Raises Incomplete (an enumerated INCOMPLETE_REASONS value) for anything unaccountable."""
    sgn = 1.0 if side == 'long' else -1.0
    act_in, act_out = ('buy', 'sell') if side == 'long' else ('sell', 'buy')
    scen = C.Scenario(st.name, fee_mult=st.mult, slip_mult=st.mult)
    entry = D + ENTRY_OFFSET + st.latency_bars * TF
    exit_sched = entry + prm.hold_ms
    if exit_sched + TF > split_hi:
        raise Incomplete('incomplete.split_end', exit_sched)
    rule_in = rules_at(entry)
    if rule_in is None:
        raise Incomplete('rules_uncovered', entry)
    tick = rule_in['tick']
    bars4 = day['bars4']
    eb = bars4.get(entry)
    if eb is None:
        raise Incomplete('incomplete.gap', entry)
    ref_in = eb.open
    slip_in = slip(bars4, entry)
    fill_in = C.fill_price(ref_in, act_in, slip_in['charged_bps'], scen, tick=tick)
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
    rule_out = rules_at(exit_t)
    if rule_out is None:
        raise Incomplete('rules_uncovered', exit_t)
    slip_out = slip(bars4, bps_at)
    fill_out = C.fill_price(ref_out, act_out, slip_out['charged_bps'], scen, gap=gap, tick=rule_out['tick'])
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
            'slip_in': slip_in, 'slip_out': slip_out, 'rule': rule_in, 'rule_exit': rule_out,
            'gross_u': gross_u, 'fill_adj_u': fill_pnl - gross_u, 'fees_u': fees_u, 'funding_u': funding_u,
            'net_u': net_u, 'r': net_u / dist, 'n_funding': len(events), 'mtm_u': mtm}


def safe_replay(*a, **kw) -> dict:
    try:
        return replay(*a, **kw)
    except Incomplete as e:
        return {'st': 'INCOMPLETE', 'why': e.reason, 'at_ms': e.at_ms}


def _finite(v) -> bool:
    return isinstance(v, (int, float)) and not isinstance(v, bool) and math.isfinite(v)


def classify(ep) -> tuple[str, str]:
    """Integration ruling 3: ('OK', '') only for a finite, reconciled episode; ('INCOMPLETE', reason) only for an
    enumerated reason; everything else is ('UNRESOLVED', why)."""
    if not isinstance(ep, dict):
        return 'UNRESOLVED', 'not_a_record'
    st = ep.get('st')
    if st == 'INCOMPLETE':
        return ('INCOMPLETE', ep.get('why')) if ep.get('why') in INCOMPLETE_REASONS else ('UNRESOLVED', 'unknown_reason')
    if st != 'OK':
        return 'UNRESOLVED', 'unknown_status'
    num = ('dist', 'fill_in', 'fill_out', 'ref_in', 'ref_out', 'gross_u', 'fill_adj_u', 'fees_u', 'funding_u',
           'net_u', 'r')
    if any(not _finite(ep.get(k)) for k in num) or not ep['dist'] > 0 or not ep['fill_in'] > 0:
        return 'UNRESOLVED', 'non_finite'
    if not isinstance(ep.get('mtm_u'), list) or any(not _finite(x) for x in ep['mtm_u']):
        return 'UNRESOLVED', 'non_finite'
    for rk in ('rule', 'rule_exit'):
        rule = ep.get(rk)
        if not isinstance(rule, dict) or any(not _finite(rule.get(k)) for k in RULE_KEYS):
            return 'UNRESOLVED', 'no_rule'
    for k in ('slip_in', 'slip_out'):
        s = ep.get(k)
        if not isinstance(s, dict) or any(not _finite(s.get(x)) for x in ('model_bps', 'floor_bps', 'charged_bps')) \
                or s['charged_bps'] != max(s['model_bps'], s['floor_bps']):
            return 'UNRESOLVED', 'slippage_components'

    def close(a, b):
        return abs(a - b) <= 1e-9 * max(1.0, abs(a), abs(b))
    if not close(ep['net_u'], ep['gross_u'] + ep['fill_adj_u'] - ep['fees_u'] + ep['funding_u']):
        return 'UNRESOLVED', 'net_mismatch'
    if not close(ep['r'], ep['net_u'] / ep['dist']):
        return 'UNRESOLVED', 'r_mismatch'
    sgn = 1.0 if ep.get('side', 'long') == 'long' else -1.0
    if 'side' in ep and not close(ep['gross_u'] + ep['fill_adj_u'], sgn * (ep['fill_out'] - ep['fill_in'])):
        return 'UNRESOLVED', 'fill_mismatch'
    return 'OK', ''


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


def _ep(side, D, st, dist, day, rules, sym, prm, split_hi):
    ep = safe_replay(side, D, st, dist, day, lambda t: rules.lookup(sym, t), prm, split_hi)
    ep['side'] = side
    return ep


def run_day(src: Guard, symbols, D: int, key: str, prm: Params, rules: PitRules, split_hi: int, check=None) -> dict:
    elig, inel, days = {}, {}, {}
    for sym in symbols:
        fm = src.first_ms(sym)
        if fm.get('funding') is None or fm.get('4h') is None or max(fm['funding'], fm['4h']) > D - AGE_DAYS * DAY:
            inel['age'] = inel.get('age', 0) + 1
            continue
        try:
            day = load_day(src, sym, D, split_hi)
        except Incomplete as e:                        # provenance_uncovered: a required straddle row is unproven
            inel[e.reason] = inel.get(e.reason, 0) + 1
            continue
        if check is not None:
            check()                                    # bounded RSS checkpoint: after every symbol load
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
    qv = {}
    for s in sorted(elig):
        bs = [days[s]['bars4'].get(o) for o in range(D + ENTRY_OFFSET - DAY, D + ENTRY_OFFSET, TF)]
        qv[s] = None if any(b is None or b.quote_volume is None for b in bs) else math.fsum(b.quote_volume for b in bs)
    rec['qv24'] = qv
    rec['base'] = {side: {s: _ep(side, D, STRESS_ROWS['base_x1'], elig[s][1], days[s], rules, s, prm, split_hi)
                          for s in sorted(elig)} for side in SIDES}
    rec['b2'] = {'short': sel['b2_short'], 'long': sel['b2_long']}
    rec['primary'] = {side: [{'symbol': s, 'f24': f[s], 'qv24': qv[s],
                              'rows': {name: _ep(side, D, st, elig[s][1], days[s], rules, s, prm, split_hi)
                                       for name, st in STRESS_ROWS.items()}}
                             for s in sel[side]] for side in SIDES}
    return rec


# ------------------------------------------------------------------ memory guard
def _mem_counters() -> tuple[float, float]:
    """(working set MB, private committed MB) of this process."""
    if os.name == 'nt':
        import ctypes
        from ctypes import wintypes

        class PMC(ctypes.Structure):
            _fields_ = [('cb', wintypes.DWORD), ('PageFaultCount', wintypes.DWORD),
                        ('PeakWorkingSetSize', ctypes.c_size_t), ('WorkingSetSize', ctypes.c_size_t),
                        ('QuotaPeakPagedPoolUsage', ctypes.c_size_t), ('QuotaPagedPoolUsage', ctypes.c_size_t),
                        ('QuotaPeakNonPagedPoolUsage', ctypes.c_size_t),
                        ('QuotaNonPagedPoolUsage', ctypes.c_size_t), ('PagefileUsage', ctypes.c_size_t),
                        ('PeakPagefileUsage', ctypes.c_size_t), ('PrivateUsage', ctypes.c_size_t)]
        c = PMC()
        c.cb = ctypes.sizeof(PMC)
        k32 = ctypes.WinDLL('kernel32')
        k32.GetCurrentProcess.restype = wintypes.HANDLE
        psapi = ctypes.WinDLL('psapi')
        psapi.GetProcessMemoryInfo.argtypes = [wintypes.HANDLE, ctypes.POINTER(PMC), wintypes.DWORD]
        if not psapi.GetProcessMemoryInfo(k32.GetCurrentProcess(), ctypes.byref(c), c.cb):
            raise OSError('GetProcessMemoryInfo failed')
        return c.WorkingSetSize / 2 ** 20, max(c.PrivateUsage, c.PagefileUsage) / 2 ** 20
    vals = {}
    with open('/proc/self/status') as f:
        for ln in f:
            k, _, v = ln.partition(':')
            if k in ('VmRSS', 'RssAnon', 'VmData'):
                vals[k] = int(v.split()[0]) / 1024
    return vals['VmRSS'], max(vals.get('RssAnon', 0.0), vals.get('VmData', 0.0))


def rss_mb() -> float:
    """max(private committed, working set) in MB; raises RunnerError when it cannot be measured (never a silent 0)."""
    try:
        ws, pv = _mem_counters()
    except Exception as e:                              # noqa: BLE001
        raise RunnerError(f'cannot measure process memory ({e})') from None
    v = max(ws, pv)
    if not (math.isfinite(v) and v > 0):
        raise RunnerError('cannot measure process memory (non-positive reading)')
    return v


# ------------------------------------------------------------------ pass 1 driver (resumable, chained)
def _fsync_append(fh, line: bytes):
    fh.write(line)
    fh.flush()
    os.fsync(fh.fileno())


def identity_doc(prereg_raw: bytes, symbols, rules, source_identity: dict, keys, code: dict,
                 freeze_sha: str | None, prm: Params, amendment, schedule: dict, excluded: dict) -> dict:
    return {'format': FORMAT, 'candidate_id': CANDIDATE, 'prereg_sha256': sha(prereg_raw),
            'accounting_input_sha256': file_sha(accounting_path()), 'policy': prm.policy,
            'amendment': amendment.identity() if amendment is not None else None,
            'dev_end': S.utc(amendment.dev_end) if amendment is not None else None,
            'scheduled_days': schedule, 'dev_end_excluded_days': excluded,
            'universe': list(symbols), 'rules': rules.identity() if rules is not None else None,
            'source': source_identity, 'splits': list(keys), 'code': code,
            'holdout_freeze_record_sha256': freeze_sha, 'conventions': CONVENTIONS,
            'slip': {'c': SLIP_C_CRYPTO, 'floor_bps': SLIP_FLOOR_BPS}, 'rows': list(ROWS),
            'boundaries': {'holdout_read': False, 'ledger_written': False, 'mainnet': False}}


def iter_verified(path: str, run_digest: str, plan_keys=None):
    """Yield every line's record after checking canonical bytes, its hash, the chain and the identity digest."""
    prev, n = run_digest, 0
    with open(path, 'rb') as fh:
        for line in fh:
            try:
                rec = json.loads(line)
                h = rec.pop('hash')
            except (ValueError, KeyError, AttributeError):
                raise RunnerError(f'days.jsonl line {n + 1} is not a hashed record; refusing') from None
            if line != canon(dict(rec, hash=h)) + b'\n' or sha(canon(rec)) != h:
                raise RunnerError(f'days.jsonl line {n + 1}: content does not match its record hash; refusing')
            if rec.get('prev') != prev:
                raise RunnerError(f'days.jsonl line {n + 1}: broken hash chain; refusing')
            if rec.get('run') != run_digest:
                raise RunnerError(f'days.jsonl line {n + 1}: written under another run identity; refusing')
            if plan_keys is not None and (n >= len(plan_keys) or (rec.get('split'), rec.get('D')) != plan_keys[n]):
                raise RunnerError(f'days.jsonl line {n + 1} does not match the run plan; refusing to resume')
            prev, n = h, n + 1
            yield rec, h


def _truncate_torn_tail(path: str) -> None:
    with open(path, 'rb+') as fh:
        end = fh.seek(0, 2)
        if not end:
            return
        fh.seek(end - 1)
        if fh.read(1) == b'\n':
            return
        pos = end
        while pos > 0:
            step = min(65536, pos)
            fh.seek(pos - step)
            i = fh.read(step).rfind(b'\n')
            if i >= 0:
                pos = pos - step + i + 1
                break
            pos -= step
        fh.truncate(pos)


def _resume_point(path: str, plan_keys: list, run_digest: str) -> tuple[int, str]:
    if not os.path.exists(path):
        return 0, run_digest
    _truncate_torn_tail(path)
    n, prev = 0, run_digest
    for _, h in iter_verified(path, run_digest, plan_keys):
        n, prev = n + 1, h
    return n, prev


def run(outdir: str, src, symbols, rules, prereg: dict, prereg_raw: bytes, *, max_rss_mb: float | None = None,
        dry_run: bool = False, only_keys=None, progress=None, stop_after_days: int | None = None,
        code: dict | None = None, allow_dirty_test_only: bool = False, holdout_freeze_record: str | None = None,
        amendment: Amendment | None = None, expect_rules_sha256: str | None = None,
        expect_straddle_sha256: str | None = None) -> dict:
    prm = params_of(prereg)
    plan = split_plan(prereg)
    keys = [k for k in evaluated_keys(plan) if only_keys is None or k in only_keys]
    if amendment is not None and not isinstance(amendment, Amendment):
        raise RunnerError('a shortened end comes only from a signed, hash-bound Amendment record')
    if amendment is not None and amendment.doc['prereg_sha256'] != sha(prereg_raw):
        raise RunnerError('amendment record is for another prereg')
    dev_end = amendment.dev_end if amendment is not None else None
    cut = prm.cutoff_ms if dev_end is None else dev_end
    guard = Guard(src, cut)
    schedule = [(k, d) for k in keys for d in decision_days(plan, k, prm, dev_end)]
    sched_n = {k: sum(1 for kk, _ in schedule if kk == k) for k in keys}
    excluded = {k: len(decision_days(plan, k, prm)) - sched_n[k] for k in keys}
    code = code if code is not None else code_identity()
    freeze_sha = file_sha(holdout_freeze_record) if holdout_freeze_record else None
    ident = identity_doc(prereg_raw, symbols, rules, src.identity(), keys, code, freeze_sha, prm, amendment, sched_n,
                         excluded)
    straddle_from = getattr(src, 'straddle_from', None)
    straddle = getattr(src, 'straddle', None)
    straddle_needed = straddle_from is not None and (dev_end is None or dev_end > straddle_from)
    straddle_open = straddle_needed and straddle is None
    mism = []
    if rules is not None and expect_rules_sha256 != rules.sha256:
        mism.append('rules')
    if (straddle is not None or straddle_needed) and \
            (straddle is None or expect_straddle_sha256 != getattr(straddle, 'sha256', None)):
        mism.append('straddle')
    if dry_run:
        rep = dry_report(guard, symbols, rules, schedule, plan, prm, ident)
        rep['straddle_unresolved_from'] = S.utc(straddle_from) if straddle_open else None
        rep['expected_artifact_mismatch'] = mism
        return rep
    if straddle_open:
        raise RunnerError(f'archive months from {S.utc(straddle_from)} straddle the sealed cutoff and are never '
                          'opened: an evidence run needs the frozen provenance-complete --straddle-artifact (or a '
                          'signed amendment ending at or before that month); no silent drop of scheduled data')
    if rules is None:
        raise RunnerError('no point-in-time exchange-rules artifact; refusing a real run (dry-run reports coverage)')
    if getattr(rules, 'source', None) is None:
        raise RunnerError('the rules artifact is not verified against a hash-bound rules-source manifest '
                          '(--rules-source-manifest + --expect-rules-source-sha256)')
    if mism:
        raise RunnerError(f'expected artifact sha256 missing or different for: {", ".join(mism)}; an evidence run '
                          'accepts only the frozen artifacts (--expect-rules-sha256 / --expect-straddle-sha256)')
    if code.get('dirty') and not allow_dirty_test_only:
        raise RunnerError('the code tree is dirty; commit first (a real run binds a clean commit)')
    if max_rss_mb is None or not (isinstance(max_rss_mb, (int, float)) and math.isfinite(max_rss_mb)
                                  and max_rss_mb > 0):
        raise RunnerError('a finite --max-rss-mb is mandatory (low-RAM run)')
    rss_mb()                                           # refuse to start when memory cannot be measured

    def check():
        v = rss_mb()
        if v > max_rss_mb:
            raise MemoryLimit(f'memory {v:.0f} MB > --max-rss-mb {max_rss_mb:g}; no partial day written, '
                              'rerun the same command to resume')
    if hasattr(src, 'set_memory'):
        src.set_memory(check, max_rss_mb)
    os.makedirs(outdir, exist_ok=True)
    idp = os.path.join(outdir, 'run.json')
    ib = json.dumps(ident, sort_keys=True, indent=1).encode('ascii') + b'\n'
    if os.path.exists(idp):
        if open(idp, 'rb').read() != ib:
            raise RunnerError('run.json identity differs from this command/code; refusing to resume into it')
    else:
        with open(idp, 'wb') as fh:
            _fsync_append(fh, ib)
    if os.path.exists(os.path.join(outdir, 'summary.json')):
        raise RunnerError('summary.json exists: this run is complete and sealed')
    run_digest = sha(canon(ident))
    days_path = os.path.join(outdir, 'days.jsonl')
    plan_keys = [(k, S.utc(d)) for k, d in schedule]
    done, prev = _resume_point(days_path, plan_keys, run_digest)
    did = 0
    with open(days_path, 'ab') as fh:
        for i in range(done, len(schedule)):
            k, D = schedule[i]
            lo, hi = plan.access_range(k)[0], split_end(plan, k, dev_end)
            guard.scope(lo, hi)
            rec = run_day(guard, symbols, D, k, prm, rules, hi, check)
            check()
            rec['run'], rec['prev'] = run_digest, prev
            prev = rec['hash'] = sha(canon(rec))
            _fsync_append(fh, canon(rec) + b'\n')
            did += 1
            if progress:
                progress(i + 1, len(schedule), k, rec['D'])
            if stop_after_days is not None and did >= stop_after_days:
                return {'status': 'PARTIAL', 'days_done': i + 1, 'days_total': len(schedule)}
    summ = summarize(days_path, prereg, keys, run_digest, sched_n)
    summ['dev_end'] = ident['dev_end']
    summ['dev_end_excluded_days'] = excluded
    summ['identity_sha256'] = run_digest
    sp = os.path.join(outdir, 'summary.json')
    tmp = sp + '.tmp'
    with open(tmp, 'wb') as fh:
        _fsync_append(fh, json.dumps(summ, sort_keys=True, indent=1, allow_nan=False).encode('ascii') + b'\n')
    os.replace(tmp, sp)
    return summ


def dry_report(guard: Guard, symbols, rules, schedule, plan, prm, ident) -> dict:
    """Inputs and coverage only: metadata, the schedule, rules coverage and the guard; no row read, no result."""
    missing_meta = [s for s in symbols if None in (guard.first_ms(s).get('funding'), guard.first_ms(s).get('4h'))]
    per_split = {}
    for k, d in schedule:
        per_split.setdefault(k, []).append(d)
    if rules is None:
        cov = None
    else:
        def bad(s, t):
            try:
                return rules.lookup(s, t) is None
            except Incomplete:
                return True
        cov = {s: sum(1 for _, d in schedule if bad(s, d + ENTRY_OFFSET) or bad(s, d + ENTRY_OFFSET + prm.hold_ms))
               for s in symbols}
        cov = {s: n for s, n in cov.items() if n}
    return {'format': FORMAT, 'mode': 'dry-run', 'identity_sha256': sha(canon(ident)), 'code_dirty': ident['code']['dirty'],
            'cutoff': S.utc(prm.cutoff_ms), 'symbols': len(symbols), 'symbols_without_metadata': missing_meta,
            'rules': ident['rules'], 'rules_uncovered_days_by_symbol': cov,
            'splits': {k: {'days': len(v), 'first_D': S.utc(v[0]), 'last_D': S.utc(v[-1])} for k, v in per_split.items()},
            'plan_digest': plan.digest}


# ------------------------------------------------------------------ pass 2 (streamed summary)
def _floor(q, step):
    return F.round_step(q, step) if step else q


class Book:
    """One book (side x cost row x split, or a baseline): sizing (1% of closed equity / 1R), the 3x per-book leverage
    cap (whole cohort scaled by one factor), step floors and min checks from the episode's point-in-time rule, the
    closed + mark-to-market equity path. `light` keeps scalars only (B0's 1000 books)."""

    def __init__(self, prm: Params, light: bool = False):
        self.prm, self.light = prm, light
        self.e0 = self.eq = self.peak = prm.start_usdt
        self.maxdd = 0.0
        self.cum_r = self.peak_r = self.rdd = 0.0
        self.uw_start = None
        self.uw_max = 0
        self.first_D = self.last_D = None
        self.sum_r, self.n_r = 0.0, 0
        self.r, self.cohort_r, self.cohort_mean = [], [], []
        self.refused, self.incomplete_by, self.unresolved_by = {}, {}, {}
        self.incomplete = self.unresolved = self.selected = 0
        self.notional_traded, self.cap_ratio = [], []
        self.exposure_ms = 0
        self.decomp = {'gross': 0.0, 'fill_adj': 0.0, 'fees': 0.0, 'funding': 0.0}
        self.sym_abs = {}
        self.f24_sign = {'pos': 0, 'zero': 0, 'neg': 0}
        self.cap_missing = 0
        self.path = []                             # (D, equity / e0, closed?) for the cross-fold chain

    def _mark(self, v):
        if v > self.peak:
            self.peak = v
        self.maxdd = max(self.maxdd, (self.peak - v) / self.peak if self.peak > 0 else 1.0)

    def cohort(self, D: int, positions):
        """positions: [{symbol, f24, qv24, ep[, cls]}]."""
        self.first_D = D if self.first_D is None else self.first_D
        self.last_D = D
        live = []
        for p in positions:
            self.selected += 1
            v = p['f24']
            self.f24_sign['pos' if v > 0 else 'neg' if v < 0 else 'zero'] += 1
            st, why = p.get('cls') or classify(p['ep'])
            if st == 'INCOMPLETE':
                self.incomplete += 1
                self.incomplete_by[why] = self.incomplete_by.get(why, 0) + 1
            elif st != 'OK':
                self.unresolved += 1
                self.unresolved_by[why] = self.unresolved_by.get(why, 0) + 1
            else:
                live.append(p)
        E = self.eq
        sized = []
        for p in live:
            ep = p['ep']
            qraw = E * self.prm.risk / ep['dist']
            sized.append([p, qraw, _floor(qraw, ep['rule']['step'])])
        notional = math.fsum(x[2] * x[0]['ep']['fill_in'] for x in sized)
        if notional > self.prm.lev_cap * E and notional > 0:
            f = self.prm.lev_cap * E / notional
            for x in sized:
                x[2] = _floor(x[1] * f, x[0]['ep']['rule']['step'])
        fills = []
        for p, qraw, q in sized:
            ep = p['ep']
            chk = F.size_check(qraw, q, ep['fill_in'], ep['rule'])
            code = chk['code'] if chk['ok'] is not True else max_limit_code(chk['qty'], ep)
            if code:
                self.refused[code] = self.refused.get(code, 0) + 1
                continue
            fills.append((p, chk['qty']))
        if not fills:
            return
        if not self.light:
            n_bars = max(len(p['ep']['mtm_u']) for p, _ in fills)
            for j in range(n_bars):
                v = E + math.fsum(q * (p['ep']['mtm_u'][j] if j < len(p['ep']['mtm_u']) else p['ep']['net_u'])
                                  for p, q in fills)
                self._mark(v)
                self.path.append((D, v / self.e0, False))
        self.eq = E + math.fsum(q * p['ep']['net_u'] for p, q in fills)
        rs = [p['ep']['r'] for p, _ in fills]
        self.sum_r += math.fsum(rs)
        self.n_r += len(rs)
        if self.light:
            return
        self._mark(self.eq)
        self.path.append((D, self.eq / self.e0, True))
        if self.uw_start is not None:                    # the run lasts until the recovery point itself
            self.uw_max = max(self.uw_max, (D - self.uw_start) // DAY)
        if self.eq >= self.peak:
            self.uw_start = None
        elif self.uw_start is None:
            self.uw_start = D
        for p, q in fills:
            ep = p['ep']
            d = ep['dist']
            self.decomp['gross'] += ep['gross_u'] / d
            self.decomp['fill_adj'] += ep['fill_adj_u'] / d
            self.decomp['fees'] -= ep['fees_u'] / d
            self.decomp['funding'] += ep['funding_u'] / d
            self.sym_abs[p['symbol']] = self.sym_abs.get(p['symbol'], 0.0) + abs(ep['r'])
            self.notional_traded.append(q * (ep['fill_in'] + ep['fill_out']) / E)
            qv = p.get('qv24')
            if _finite(qv) and qv > 0:
                self.cap_ratio.append(q * ep['fill_in'] / qv)
            else:
                self.cap_missing += 1                  # counted, and +inf in the capacity median
        self.exposure_ms += max(p['ep']['exit_ms'] - p['ep']['entry_ms'] for p, _ in fills)
        self.r.extend(rs)
        self.cohort_r.append(math.fsum(rs))
        self.cohort_mean.append(math.fsum(rs) / len(rs))
        self.cum_r += math.fsum(rs)
        self.peak_r = max(self.peak_r, self.cum_r)
        self.rdd = max(self.rdd, self.peak_r - self.cum_r)

    def mean_r(self):
        return self.sum_r / self.n_r if self.n_r else None


def max_limit_code(q: float, ep: dict) -> str:
    """'' when the order fits max_qty / max_notional of both the entry-time and the exit-time rule; else the refusal
    code (refuse and count, never clamp). feasibility.size_check has already applied the entry max_qty."""
    ri, rx = ep['rule'], ep['rule_exit']
    if q * ep['fill_in'] > ri['max_notional'] * (1 + 1e-12):
        return 'above_max_notional'
    if q > rx['max_qty'] * (1 + 1e-12):
        return 'exit_above_max_qty'
    if q * ep['fill_out'] > rx['max_notional'] * (1 + 1e-12):
        return 'exit_above_max_notional'
    return ''


def capacity_median(ratios, missing: int):
    """Median notional / prior-24h quote volume with every missing capacity input as +inf (None when that is the
    median: the capacity gate cannot pass on missing inputs)."""
    m = _pct(list(ratios) + [math.inf] * missing, 0.5)
    return None if m is None or m == math.inf else m


def chain_folds(books) -> dict:
    """Chain normalized fold equity chronologically: fresh books per fold, but a drawdown and its underwater run
    continue across a fold boundary. Underwater duration is measured on closed points, in days."""
    level, peak, maxdd, uw_start, uw_max, days = 1.0, 1.0, 0.0, None, 0, 0
    for b in books:
        for D, v, closed in b.path:
            x = level * v
            peak = max(peak, x)
            maxdd = max(maxdd, (peak - x) / peak)
            if closed:
                if uw_start is not None:                 # the run lasts until the recovery point itself
                    uw_max = max(uw_max, (D - uw_start) // DAY)
                if x >= peak:
                    uw_start = None
                elif uw_start is None:
                    uw_start = D
        level *= b.eq / b.e0
        if b.first_D is not None:
            days += (b.last_D - b.first_D) // DAY + 1
    ann = (level ** (365.0 / days) - 1.0) if days and level > 0 else None
    return {'max_equity_dd': maxdd, 'longest_underwater_days': uw_max, 'growth': level, 'annualized_return': ann,
            'calmar': (ann / maxdd) if ann is not None and maxdd > 0 else None}


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


def bootstrap(xs, block=None, n=None, seed=None) -> dict:
    """Moving-block bootstrap of the mean (non-circular blocks, uniform starts, concatenated and cut to len(xs)).
    lcb95 = 5th percentile of the resampled means (one-sided); p = (#means <= 0 + 1) / (n + 1)."""
    block = BOOT_BLOCK if block is None else block
    n = BOOT_N if n is None else n
    seed = BOOT_SEED if seed is None else seed
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
    return {'episodes': len(b.r), 'selected': b.selected, 'incomplete': b.incomplete,
            'incomplete_by_reason': dict(sorted(b.incomplete_by.items())), 'unresolved': b.unresolved,
            'unresolved_by_reason': dict(sorted(b.unresolved_by.items())), 'refused': dict(sorted(b.refused.items())),
            'incomplete_share': (b.incomplete / b.selected) if b.selected else None,
            'cohort_days': len(b.cohort_r), 'mean_r': _mean(b.r), 'total_r': tot, 'pf': _pf(b.r),
            'final_equity': b.eq, 'max_equity_dd': b.maxdd, 'closed_r_dd': b.rdd, 'longest_underwater_days': b.uw_max,
            'annualized_return': ann, 'calmar': (ann / b.maxdd) if ann is not None and b.maxdd > 0 else None,
            'total_r_over_closed_r_dd': (tot / b.rdd) if b.rdd > 0 else None,
            'exposure_share': (b.exposure_ms / (span_days * DAY)) if span_days else None,
            'worst_cohort_net_r_sum': min(b.cohort_r) if b.cohort_r else None, 'p01_episode_r': _pct(b.r, 0.01),
            'decomposition_r': dict(b.decomp), 'f24_sign_at_selection': dict(b.f24_sign),
            'funding_contribution_r': b.decomp['funding'],
            'price_contribution_r': b.decomp['gross'] + b.decomp['fill_adj'] + b.decomp['fees'],
            'daily_turnover_mean': (math.fsum(b.notional_traded) / len(b.cohort_r)) if b.cohort_r else None,
            'median_notional_over_qv24': capacity_median(b.cap_ratio, b.cap_missing),
            'capacity_inputs_missing': b.cap_missing}


MANDATORY_ROWS = ('base_x1', 'all_variable_costs_x1.5', 'all_variable_costs_x2')


def summarize(days_path: str, prereg: dict, keys, run_digest: str, scheduled: dict | None = None) -> dict:
    """Pass 2: stream and re-verify days.jsonl once; every book (primaries and baselines) through Book."""
    prm = params_of(prereg)
    books, b1, b2, b0 = {}, {}, {}, {}                 # b0: (split, side) -> [1000 light Books]
    counts = {k: {'days': 0, 'scheduled_days': (scheduled or {}).get(k), 'no_cohort_days': 0,
                  'data_loss_no_cohort_days': 0, 'provenance_uncovered_days': 0, 'gate_failing_episodes': 0,
                  'ineligible': {}} for k in keys}
    stress = {}                                        # (split, side) -> {mismatch, stress_only_incomplete}
    for d, _ in iter_verified(days_path, run_digest):
        k, D = d['split'], S.parse_utc(d['D'])
        c = counts[k]
        c['days'] += 1
        for r, n in d['ineligible'].items():
            c['ineligible'][r] = c['ineligible'].get(r, 0) + n
        if d['ineligible'].get('provenance_uncovered'):
            c['provenance_uncovered_days'] += 1        # gate-failing: never inside the 2% allowance
        if not d['no_cohort']:
            for side in SIDES:
                eps = list(d['base'][side].values()) + [e for p in d['primary'][side] for e in p['rows'].values()]
                c['gate_failing_episodes'] += sum(1 for e in eps if classify(e) in
                                                  {('INCOMPLETE', g) for g in GATE_FAILING_REASONS})
        if d['no_cohort']:
            c['no_cohort_days'] += 1
            if any(r != 'age' for r in d['ineligible']):
                c['data_loss_no_cohort_days'] += 1     # data went missing, not a listing-age shortfall
            continue
        for side in SIDES:
            st = stress.setdefault((k, side), {'identity_mismatch': 0, 'stress_only_incomplete': 0})
            for p in d['primary'][side]:
                eps = [p['rows'].get(r) for r in MANDATORY_ROWS]
                cls = [classify(e)[0] for e in eps]
                if len(set(cls)) > 1:
                    st['stress_only_incomplete'] += 1
                if all(x == 'OK' for x in cls) and len({(e['entry_ms'], e['side']) for e in eps}) != 1:
                    st['identity_mismatch'] += 1
            for row in ROWS:
                books.setdefault((k, side, row), Book(prm)).cohort(
                    D, [{'symbol': p['symbol'], 'f24': p['f24'], 'qv24': p['qv24'], 'ep': p['rows'].get(row)}
                        for p in d['primary'][side]])
            base = {s: {'symbol': s, 'f24': d['f24'][s], 'qv24': d['qv24'][s], 'ep': ep, 'cls': classify(ep)}
                    for s, ep in d['base'][side].items()}
            elig = sorted(base)
            b1.setdefault((k, side), Book(prm)).cohort(D, [base[s] for s in elig])
            b2.setdefault((k, side), Book(prm)).cohort(D, [base[s] for s in d['b2'][side]])
            per = b0.get((k, side))
            if per is None:
                per = b0[(k, side)] = [Book(prm, light=True) for _ in range(B0_BOOKS)]
            for i in range(B0_BOOKS):
                per[i].cohort(D, [base[s] for s in random.Random(b0_seed(side, d['D'], i)).sample(elig, prm.k)])
    out = {'format': SUMMARY_FORMAT, 'candidate_id': CANDIDATE, 'splits': {}, 'counts': counts,
           'conventions': CONVENTIONS, 'policy': prm.policy,
           'labels': ['SLIP-C-UNFITTED', 'SLIP-FLOOR-5BP', C.FUNDING_MARK_LABEL, 'STATIC-40-SURVIVOR-BIASED',
                      'PIT-EXCHANGE-RULES', 'WORST-COHORT-NET-R-IS-SUM', 'FOLD-CHAINED-DD', 'NOT-A-LEDGER-RECORD',
                      'PROVENANCE-BOUND-ARTIFACTS', 'AGE-ONLY-NO-COHORT-REPORTED', 'DATA-LOSS-NO-COHORT-GATED',
                      'ENTRY-EXIT-MAX-FILTERS', 'UW-COUNTS-RECOVERY-POINT']}
    for k in keys:
        out['splits'][k] = {}
        for side in SIDES:
            rows = {row: _book_metrics(books[(k, side, row)]) for row in ROWS if (k, side, row) in books}
            means = sorted(m for m in (b.mean_r() for b in b0.get((k, side)) or ()) if m is not None)
            out['splits'][k][side] = {
                'rows': rows, 'stress_identity': stress.get((k, side)),
                'B1': _book_metrics(b1[(k, side)]) if (k, side) in b1 else None,
                'B2': _book_metrics(b2[(k, side)]) if (k, side) in b2 else None,
                'B0_p95_mean_r': _pct(means, 0.95), 'B0_median_mean_r': _pct(means, 0.5),
                'B0_refused': _sum_dicts(b.refused for b in b0.get((k, side)) or ()),
            }
    out['walk_forward'] = gates(books, b1, b0, keys, prm, counts, stress)
    return out


def _sum_dicts(ds):
    out = {}
    for d in ds:
        for k, v in d.items():
            out[k] = out.get(k, 0) + v
    return dict(sorted(out.items()))


def _combined(books, keys, side, row):
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
    ch = chain_folds(bs)
    return {'books': bs, 'folds': wf, 'r': rs, 'cohort_means': cm, 'closed_r_dd': dd, 'chain': ch,
            'ann': ch['annualized_return'], 'maxdd': ch['max_equity_dd'] if bs else None,
            'uw': ch['longest_underwater_days'], 'total_r': math.fsum(rs)}


def data_checks(books, keys, side, counts, stress) -> dict:
    """gates.data per walk-forward fold (Integration ruling 3 + the addendum): every loss is gated, none evades."""
    wf = [k for k in keys if k.startswith('walk_forward')]
    per = {}
    for k in wf:
        c = counts.get(k, {})
        sched = c.get('scheduled_days')
        def tolerable(b):
            return b.incomplete - sum(b.incomplete_by.get(g, 0) for g in GATE_FAILING_REASONS)
        inc = {r: (tolerable(books[(k, side, r)]) / books[(k, side, r)].selected)
               if (k, side, r) in books and books[(k, side, r)].selected else None for r in MANDATORY_ROWS}
        gate_fail = sum(books[(k, side, r)].incomplete_by.get(g, 0) for r in ROWS if (k, side, r) in books
                        for g in GATE_FAILING_REASONS) + c.get('gate_failing_episodes', 0)
        prov_days = c.get('provenance_uncovered_days', 0)
        unresolved = sum(books[(k, side, r)].unresolved for r in ROWS if (k, side, r) in books)
        loss = c.get('data_loss_no_cohort_days', 0)
        st = stress.get((k, side)) or {'identity_mismatch': 0, 'stress_only_incomplete': 0}
        ok = (all((k, side, r) in books for r in MANDATORY_ROWS)
              and all(v is not None and v <= DATA_MAX_INCOMPLETE for v in inc.values())
              and unresolved == 0 and st['identity_mismatch'] == 0 and gate_fail == 0 and prov_days == 0
              and sched is not None and c.get('days') == sched
              and (loss / sched if sched else 1.0) <= DATA_MAX_INCOMPLETE)
        per[k] = {'ok': ok, 'incomplete_share': inc, 'unresolved': unresolved, 'days': c.get('days'),
                  'gate_failing_incomplete': gate_fail, 'provenance_uncovered_days': prov_days,
                  'scheduled_days': sched, 'data_loss_no_cohort_days': loss,
                  'no_cohort_days': c.get('no_cohort_days'), **st}
    return {'ok': bool(per) and all(v['ok'] for v in per.values()), 'folds': per}


def gates(books, b1, b0, keys, prm: Params, counts=None, stress=None) -> dict:
    g = prm.gates
    res = {}
    wf = [k for k in keys if k.startswith('walk_forward')]
    for side in SIDES:
        c = _combined(books, keys, side, 'base_x1')
        if not c['books']:
            res[side] = {'verdict': 'NOT_RUN'}
            continue
        x15 = _combined(books, keys, side, 'all_variable_costs_x1.5')
        x2 = _combined(books, keys, side, 'all_variable_costs_x2')
        mean = _mean(c['r'])
        boot = bootstrap(c['cohort_means'])
        fold_means = [_mean(b.r) for b in c['books']]
        data = data_checks(books, keys, side, counts or {}, stress or {})
        b0m = []
        for i in range(B0_BOOKS):
            s = n = 0
            for k in wf:
                bk = (b0.get((k, side)) or [None] * B0_BOOKS)[i]
                if bk is not None:
                    s, n = s + bk.sum_r, n + bk.n_r
            if n:
                b0m.append(s / n)
        b0m.sort()
        b1r = [r for k in wf if (k, side) in b1 for r in b1[(k, side)].r]
        b1_mean = _mean(b1r)
        dd = g['drawdown_and_tail']
        tiers = dd['tiers']
        ratio = (c['total_r'] / c['closed_r_dd']) if c['closed_r_dd'] > 0 else math.inf
        calmar = (c['ann'] / c['maxdd']) if c['ann'] is not None and c['maxdd'] else \
            (math.inf if c['ann'] and c['ann'] > 0 else None)
        ordinary = c['maxdd'] <= tiers['ordinary']['base_max_equity_dd_le'] and \
            c['closed_r_dd'] < tiers['ordinary']['closed_r_dd_lt']
        rescue = (c['maxdd'] <= tiers['profitability_rescue']['base_max_equity_dd_le'] and calmar is not None
                  and calmar >= tiers['profitability_rescue']['annualized_net_return_over_max_dd_ge']
                  and ratio >= tiers['profitability_rescue']['total_net_r_over_closed_r_dd_ge'])
        worst = min((x for b in c['books'] for x in b.cohort_r), default=None)
        uw = c['uw']
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
        cap_missing = sum(b.cap_missing for b in c['books'])
        cap_med = capacity_median(caps, cap_missing)
        conc = g['concentration']
        checks = {
            'data': data['ok'] and len(c['books']) == len(c['folds']),
            'activity': len(c['r']) >= 1000 and all(len(b.cohort_r) >= 200 for b in c['books'])
                        and len(c['books']) == len(c['folds']),
            'fold_stability': sum(1 for m in fold_means if m is not None and m > 0)
                              >= g['fold_stability']['positive_folds_min'],
            'expectancy': (mean is not None and mean > g['expectancy']['combined_mean_net_r_gt']
                           and (_pf(c['r']) or 0) > g['expectancy']['combined_pf_gt']
                           and boot['lcb95'] is not None and boot['lcb95'] > 0),
            'baselines': (mean is not None and bool(b0m) and mean > _pct(b0m, 0.95)
                          and b1_mean is not None and mean > b1_mean),
            'stress': (_mean(x15['r']) or 0) > g['stress']['x1_5_mean_net_r_gt'],
            'drawdown_and_tail': (ordinary or rescue) and ceilings
                                 and c['maxdd'] <= tiers['profitability_rescue']['base_max_equity_dd_le'],
            'concentration': bool(shares) and shares[0] <= conc['max_symbol_share']
                             and sum(shares[:3]) <= conc['max_top3_share'],
            'capacity': cap_med is not None
                        and cap_med <= g['capacity']['median_notional_over_prior_24h_quote_volume_le'],
        }
        res[side] = {'checks': checks, 'data': data, 'mean_r': mean, 'pf': _pf(c['r']), 'episodes': len(c['r']),
                     'fold_mean_r': fold_means, 'bootstrap': boot, 'B0_p95_mean_r': _pct(b0m, 0.95),
                     'B1_mean_r': b1_mean, 'x1_5_mean_r': _mean(x15['r']), 'x2_mean_r': _mean(x2['r']),
                     'promotion_x2_ok': (_mean(x2['r']) or 0) > g['stress']['x2_mean_net_r_gt_for_promotion'],
                     'max_equity_dd_chained': c['maxdd'], 'x1_5_max_equity_dd_chained': x15['maxdd'],
                     'longest_underwater_days_chained': uw, 'annualized_return_chained': c['ann'],
                     'calmar_chained': calmar if calmar != math.inf else None,
                     'fold_local': {'max_equity_dd': [b.maxdd for b in c['books']],
                                    'x1_5_max_equity_dd': [b.maxdd for b in x15['books']],
                                    'longest_underwater_days': [b.uw_max for b in c['books']]},
                     'closed_r_dd': c['closed_r_dd'],
                     'total_r_over_closed_r_dd': ratio if ratio != math.inf else None,
                     'dd_tier': 'ordinary' if ordinary else 'profitability_rescue' if rescue else None,
                     'worst_cohort_net_r_sum': worst,
                     'max_symbol_share': shares[0] if shares else None,
                     'top3_share': sum(shares[:3]) if shares else None,
                     'median_notional_over_qv24': cap_med, 'capacity_inputs_missing': cap_missing}
    ps = {s: res[s]['bootstrap']['p'] for s in SIDES if 'bootstrap' in res[s] and res[s]['bootstrap']['p'] is not None}
    holm = holm_reject(ps, HOLM_ALPHA)
    for side in SIDES:
        if 'checks' not in res[side]:
            continue
        ch = res[side]['checks']
        ch['multiplicity'] = holm.get(side, False)
        res[side]['verdict'] = 'INCOMPLETE' if not ch['data'] else 'PASS' if all(ch.values()) else 'FAIL'
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
def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument('--store', required=True, help='Binance archive store root (read-only)')
    ap.add_argument('--rules-artifact', help=f'point-in-time exchange rules ({RULES_FORMAT}); required for a real run')
    ap.add_argument('--holdout-freeze-record', help='Integration holdout freeze record; its sha256 is bound')
    ap.add_argument('--out', help='result directory (append-only chained days.jsonl + summary.json)')
    ap.add_argument('--max-rss-mb', type=float, required=True, help='mandatory private-memory cap (MB)')
    ap.add_argument('--expect-rules-sha256', help='sha256 of the frozen rules artifact; any other is refused')
    ap.add_argument('--rules-source-manifest', help=f'exchangeInfo snapshot list ({SOURCE_MANIFEST_FORMAT})')
    ap.add_argument('--expect-rules-source-sha256', help='sha256 of the rules-source manifest')
    ap.add_argument('--rules-snapshot-root', help='directory the rules-source manifest paths are relative to '
                                                  '(default: the manifest\'s directory)')
    ap.add_argument('--straddle-daily-manifest', help=f'daily archive list for the straddle ({SOURCE_MANIFEST_FORMAT})')
    ap.add_argument('--expect-straddle-daily-sha256', help='sha256 of the straddle daily source manifest')
    ap.add_argument('--straddle-artifact', help=f'hash-bound pre-cutoff rows of the straddle month ({STRADDLE_FORMAT})')
    ap.add_argument('--expect-straddle-sha256', help='sha256 of the frozen straddle artifact; any other is refused')
    ap.add_argument('--amendment-record', help=f'signed prereg amendment ({AMENDMENT_FORMAT}); the only shortened end')
    ap.add_argument('--expect-amendment-sha256', help='sha256 of the signed amendment record')
    ap.add_argument('--dry-run', action='store_true', help='validate inputs/coverage; write nothing')
    ap.add_argument('--split', action='append', help='limit to these split keys (default: train + walk_forward)')
    ap.add_argument('--cache-months', type=int, default=512)
    ap.add_argument('--allow-dirty-test-only', action='store_true', help=argparse.SUPPRESS)
    a = ap.parse_args(argv)
    try:
        prereg, raw = load_prereg()
        prm = params_of(prereg)
        uni = os.path.join(M.REPO, *prereg['universe']['artifact'].split('/'))
        if file_sha(uni) != prereg['source']['universe_sha256']:
            raise RunnerError('universe artifact hash differs from the prereg')
        symbols = CV.load_universe(uni)
        rsrc = SourceManifest.load(a.rules_source_manifest, a.expect_rules_source_sha256,
                                   'binance-um-exchangeinfo-snapshots', a.rules_snapshot_root) \
            if a.rules_source_manifest else None
        rules = PitRules.load(a.rules_artifact, rsrc, a.max_rss_mb) if a.rules_artifact else None
        man = os.path.join(M.REPO, *prereg['source']['manifest'].split('/'))
        src = ArchiveSource(man, a.store, symbols, prm.cutoff_ms, a.cache_months)
        if a.straddle_artifact:
            daily = SourceManifest.load(a.straddle_daily_manifest, a.expect_straddle_daily_sha256,
                                        'binance-vision-archive') if a.straddle_daily_manifest else None
            src.attach_straddle(StraddleArtifact.load(a.straddle_artifact, prm.cutoff_ms), daily)
        amend = Amendment.load(a.amendment_record, raw, prm.cutoff_ms, a.expect_amendment_sha256) \
            if a.amendment_record else None
        kw = dict(only_keys=a.split, holdout_freeze_record=a.holdout_freeze_record, amendment=amend,
                  expect_rules_sha256=a.expect_rules_sha256, expect_straddle_sha256=a.expect_straddle_sha256)
        if a.dry_run:
            print(json.dumps(run(a.out or '', src, symbols, rules, prereg, raw, dry_run=True,
                                 max_rss_mb=a.max_rss_mb, **kw),
                             indent=1, sort_keys=True))
            return 0
        if not a.out:
            raise RunnerError('--out is required unless --dry-run')
        import runlock
        with runlock.hold(M.REPO, 'edge05-runner', {'out': os.path.abspath(a.out), 'splits': a.split}, argv=argv):
            res = run(a.out, src, symbols, rules, prereg, raw, max_rss_mb=a.max_rss_mb,
                      allow_dirty_test_only=a.allow_dirty_test_only,
                      progress=lambda i, n, k, d: print(f'{i}/{n} {k} {d}', file=sys.stderr, flush=True), **kw)
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
