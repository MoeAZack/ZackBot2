"""RES-01 R4 entry point (plan sections 7, 8; R4-0 gate Codex 6094720765): M3 reproduction on the reproduction-only
manifest, then the PIT run of `trend_ema_mom.v1`. Every subcommand that touches data first passes the R4 gate; the
gate is code, not a comment.

  python tools/research/run_r4.py check [--candidate trend_ema_mom.v1|trend_ema_mom_short.v1]
  python tools/research/run_r4.py register [--candidate ...] --author claude-code --cairo-date YYYY-MM-DD
  python tools/research/run_r4.py m3 --store <repo root holding data_long/4h> --cost base|2x|both
         --out-dir research_evidence/runs/r4 --author claude-code --cairo-date YYYY-MM-DD
  python tools/research/run_r4.py pit --store <binance_um store> --author claude-code --cairo-date YYYY-MM-DD
         [--out-dir research_evidence/runs/r4b] [--resume]
         (STRAT-01 R4b-PIT, pit_run.py: CORE-10 then CORE-20 development splits only; holdout never opened. Codex
         6108364741: run only after Codex posts `R4 READY`.)

Gate (`gate()` returns every failure; any failure = exit 3, nothing is read):
  G1 clean tree: the executing checkout has no uncommitted change (report.code_identity).
  G2 prereg committed: the prereg file is tracked, its working bytes equal HEAD's, and it validates
     (`validate_prereg`).
  G3 prereg registered: the family ledger verifies (ledger.verify: records, registry, append-only Git history) and
     holds the `variant` record of role `preregistration` carrying this prereg's SHA-256, plus every declared trial
     record; each of those lines is present in HEAD's committed ledger (registered AND committed).
  G4 pinned foundations (r4_gate.json `pins`, exact SHAs + ancestry, Codex 6080124561 / 6095813913): each pin exists,
     equals its branch tip when a branch is named, has its `after` commit as an ancestor, is merged into the
     integration ref and is contained in HEAD; a rebase-merged pin names its `integrated` master commit, whose tree
     must equal the reviewed source head's, and the ancestry checks apply to the integrated commit.
  G5 universe bound (STRAT-00, contract section 0.1; classification v2 is superseded and not required): the gate's
     `universe` block names the owner-approved static current top-40 artifact; it re-validates against its committed
     exchangeInfo snapshot (static_universe.validate: 40 unique ranked symbols, CORE-10 / CORE-20, tradability,
     digests, byte-identical rebuild + approval), carries the CURRENT-UNIVERSE / SURVIVOR-BIASED label and
     `owner_approved` true, and equals the pinned digest and list digest, which the prereg cites too.
Profiles (REG-02, Codex 6155928419): every gate file routes to exactly one candidate profile (`candidate_id`; absent =
the STRAT-01 long profile, whose gate `r4_gate.json` and 18-trial behaviour are unchanged). STRAT-02
`trend_ema_mom_short.v1` has its own gate `r4_gate_short.json` (exact `prereg_sha256`, #73 / #74 pins), its own root
family ledger and 17 trials (capped primary + 12 neighbours + B0-B3; no uncapped variant, no M3). Its G3 also requires
the program Holm family (trend_ema_mom + trend_ema_mom_short trial records) to be >= 35.
`m3` / `pit` stay STRAT-01 only.
`m3` keeps the sanitized progress snapshot + sealed final (progress.py, zb-run-progress/2) beside its reports:
operational state only. `register` (after Codex clears the prereg) needs G1 + G2 and appends the declared trial
records once; commit the ledger, then `m3` / `pit` pass G3. No network, no credentials; git is read locally (fetch
beforehand).
"""
from __future__ import annotations

import argparse
import dataclasses
import hashlib
import json
import os
import re
import subprocess
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import costs as C                                                                           # noqa: E402
import ledger as L                                                                          # noqa: E402
import manifest as M                                                                        # noqa: E402
import missing_data as MD                                                                   # noqa: E402
import progress as PG                                                                       # noqa: E402
import report as R                                                                          # noqa: E402
import static_universe as SU                                                                # noqa: E402
import splits as S                                                                          # noqa: E402
import trend_ema_mom as T                                                                   # noqa: E402

GATE = 'research_evidence/prereg/r4_gate.json'
GATE_FORMAT = 'zb-r4-gate/3'
UNIVERSE = 'research_evidence/universe/static-top40-minhist365-contract-20261011.json'
SNAPSHOT = 'research_evidence/universe/binance-um-exchangeinfo-v2-20261011.json'
TIERS = {'CORE-10': [1, 10], 'CORE-20': [1, 20], 'EXT-21-40': [21, 40]}   # reported separately, never pooled
PREREG_FORMAT = 'zb-prereg/1'
PREREG_KEYS = {'format', 'candidate_id', 'family', 'author', 'cairo_date', 'status', 'hypothesis', 'contamination',
               'data', 'universe_rule', 'timeframe', 'rule', 'primary_params', 'variants', 'neighbour_grid', 'trials',
               'costs', 'splits', 'metrics', 'baselines', 'edge00', 'verdict_rules', 'stop_condition',
               'm3_reproduction'}
EDGE_ROWS = {'sample', 'expectancy', 'baselines', 'multiplicity', 'pit', 'robustness', 'neighbours', 'intrabar',
             'e10_drawdown', 'e11_follower'}
PRIMARY_NAME = 'trend_ema_mom.v1.cap180'        # the promotable primary (Codex 6095682220)
PRIMARY_CAP = T.TIME_CAP_BARS                   # its finite time cap = the purge / embargo horizon
DEV_ONLY = 'trend_ema_mom.v1.uncapped'          # the uncapped M3 rule: reproduction / development evidence only
SPENT_END = '2026-10-05T00:00:00Z'               # end of the ledger's spent window (trend_ema_mom seq 1)
HOLDOUT_WEEKS = 52                              # fixed holdout length; the end is exclusive
TRIALS = 18                                     # crypto family; gold has its own future budget
DEFERRED = {'gold-spot': 'XAUUSDT', 'gold-tokenized': 'PAXGUSDT'}  # costs.py class names
M3_EVALUATOR = 'tools/research/m3_eval.py'
EVAL_FILES = ('tools/research/m3_eval.py', 'tools/research/trend_ema_mom.py')
ROLE_PREREG = 'preregistration'
HEX40 = re.compile(r'[0-9a-f]{40}\Z')
HEX64 = re.compile(r'[0-9a-f]{64}\Z')

# STRAT-02 `trend_ema_mom_short.v1` (REG-02, Codex 6155928419; docs/newcore/research/STRAT02_SHORT_CONTRACT.md)
SHORT_ID = 'trend_ema_mom_short.v1'
SHORT_FAMILY = 'trend_ema_mom_short'
SHORT_GATE = 'research_evidence/prereg/r4_gate_short.json'
SHORT_PREREG = 'research_evidence/prereg/trend_ema_mom_short.v1.json'
SHORT_LEDGER = 'research_evidence/ledger/trend_ema_mom_short.jsonl'
SHORT_PRIMARY_NAME = 'trend_ema_mom_short.v1.cap180'    # == pit_run.SHORT_PRIMARY_NAME
SHORT_TRIALS = 17                                       # 1 capped primary + 12 neighbours + B0..B3
SHORT_SEED = 20261012                                   # == pit_run.SHORT_SEED (prereg section 9 seeds)
SHORT_PREREG_KEYS = PREREG_KEYS | {'missing_data'}
SHORT_ENTRY = ('se at the close of bar i = cross_down(EMA20, EMA50) and close < EMA200 and ret7d < 0 and ret30d < 0; '
               'market SELL (open short) at the open of bar i+1')
SHORT_RULE_KEYS = {'entry', 'exit_signal', 'stop', 'time_cap', 'exit_times', 'funding', 'excursions', 'indicators',
                   'book', 'liquidation', 'no_maker', 'fill_rounding', 'window'}
MISSING_DATA_DOC = 'docs/newcore/research/MISSING_DATA_POLICY_v1.md'
MISSING_DATA_RULES = {'one_pass', 'first_unaccountable_event', 'classes', 'one_primary', 'delist', 'slippage_window',
                      'b1_decision', 'split_end', 'flat_gaps', 'fail_closed'}
PROGRAM_FAMILIES = ('trend_ema_mom', SHORT_FAMILY)      # the gating program Holm family (Codex 6112077638)
PROGRAM_N_AT_FREEZE = TRIALS + SHORT_TRIALS             # 18 + 17 = 35; later records of either family only raise it


@dataclasses.dataclass(frozen=True)
class Profile:
    """One registrable candidate: where its prereg / gate / ledger live and what `register` appends for it."""
    candidate_id: str
    family: str
    prereg: str
    ledger: str
    gate: str
    primary: str
    dev_only: str | None            # the uncapped development-only variant (STRAT-01 only)
    trials: int
    root: bool                      # the first registration creates the family as an independent root (lineage='root')
    program: bool                   # G3 also requires the program Holm family >= PROGRAM_N_AT_FREEZE


LONG = Profile(T.RULE_ID, 'trend_ema_mom', 'research_evidence/prereg/trend_ema_mom.v1.json',
               'research_evidence/ledger/trend_ema_mom.jsonl', GATE, PRIMARY_NAME, DEV_ONLY, TRIALS, False, False)
SHORT = Profile(SHORT_ID, SHORT_FAMILY, SHORT_PREREG, SHORT_LEDGER, SHORT_GATE, SHORT_PRIMARY_NAME, None, SHORT_TRIALS,
                True, True)
PROFILES = {p.candidate_id: p for p in (LONG, SHORT)}


class GateError(ValueError):
    pass


def _git(repo, *a, check=True):
    p = subprocess.run(['git', '-C', repo, *a], capture_output=True, stdin=subprocess.DEVNULL)
    if check and p.returncode:
        raise GateError(f'git {" ".join(a)}: {p.stderr.decode(errors="replace").strip()}')
    return p


def _load(repo, rel):
    with open(os.path.join(repo, rel), 'rb') as f:
        return f.read()


def profile_of(pre) -> Profile:
    """The profile a prereg belongs to (anything that is not the short candidate is validated as STRAT-01)."""
    return SHORT if isinstance(pre, dict) and pre.get('candidate_id') == SHORT_ID else LONG


def route(g: dict) -> Profile:
    """Explicit gate routing: the gate's `candidate_id` (absent = STRAT-01) names one profile, and the gate must point
    at exactly that profile's prereg and ledger (a gate is never cross-wired to the other family). A root-family
    profile's gate also pins the exact prereg SHA-256."""
    p = PROFILES.get(g.get('candidate_id', LONG.candidate_id))
    if p is None:
        raise GateError(f'unknown candidate_id {g.get("candidate_id")!r} (known: {sorted(PROFILES)})')
    if (g.get('prereg'), g.get('ledger')) != (p.prereg, p.ledger):
        raise GateError(f'routing: a {p.candidate_id} gate must name prereg {p.prereg} and ledger {p.ledger}')
    if p.root and not (isinstance(g.get('prereg_sha256'), str) and HEX64.match(g['prereg_sha256'])):
        raise GateError(f'routing: the {p.candidate_id} gate must pin the exact 64-hex prereg_sha256')
    return p


def multiplicity(families: dict) -> dict:
    """The gating program Holm family size (every variant / grid point / baseline record of PROGRAM_FAMILIES) with
    the per-family sizes (an own-family size is reporting only), from verified ledger records."""
    by = {f: L.n_trials(families.get(f, [])) for f in PROGRAM_FAMILIES}
    return {'program': sum(by.values()), 'by_family': by}


def trial_records(pre: dict, sha: str, rel: str) -> list[dict]:
    """The ledger records `register` appends: the capped primary variant (the preregistration), the uncapped
    development-only variant (STRAT-01 only), every neighbour grid point and every baseline choice (crypto family only;
    the deferred gold pilot has its own future budget). Same order every time: 18 for STRAT-01, 17 for STRAT-02."""
    sp = pre['splits']['splits']
    win = {'start': sp[1]['start'], 'end': sp[-1]['end']}
    base = {'prereg_path': rel, 'prereg_sha256': sha}
    prof = profile_of(pre)
    out = [dict(kind='variant', detail=dict(base, role=ROLE_PREREG, book='crypto', config=prof.primary))]
    if prof.dev_only:                                    # STRAT-01 only; STRAT-02 has no uncapped variant
        out.append(dict(kind='variant', detail=dict(base, role='development_only_variant', book='crypto',
                                                    config=prof.dev_only)))
    out += [dict(kind='grid_point', detail=dict(base, role='neighbour', book='crypto', config=n))
            for n in pre['neighbour_grid']['configs']]
    out += [dict(kind='baseline', detail=dict(base, role='baseline', baseline=b)) for b in ('B0', 'B1', 'B2', 'B3')]
    for r in out:
        r.update(candidate_id=pre['candidate_id'], split='development', window=win,
                 manifest_digest=pre['data']['manifest_digest'])
    return out


# ------------------------------------------------------------------ prereg validation
def validate_prereg(pre: dict, repo: str) -> list[str]:
    """Every finding that keeps this prereg from being the frozen one of its profile (empty = valid). STRAT-01 checks
    are unchanged and in the same order; STRAT-02 (`trend_ema_mom_short.v1`) runs the same shared sections plus its own
    identity / side / single-variant / 17-trial / program-multiplicity / no-M3 / holdout / rule / M1-policy checks."""
    if profile_of(pre) is SHORT:
        return _validate_short(pre, repo)
    bad = []
    if not isinstance(pre, dict) or set(pre) != PREREG_KEYS:
        return [f'prereg keys must be exactly {sorted(PREREG_KEYS)}']
    if pre['format'] != PREREG_FORMAT or pre['candidate_id'] != T.RULE_ID or pre['family'] != 'trend_ema_mom':
        bad.append('format / candidate_id / family mismatch')
    bad += _params_failures(pre)
    v = pre['variants']
    try:
        sec = v['secondary']
        if (v['primary'].get('name') != PRIMARY_NAME or v['primary'].get('time_cap_bars') != PRIMARY_CAP
                or v['primary'].get('book') != 'crypto' or v['primary'].get('promotable') is not True):
            bad.append(f'variants.primary must be the promotable {PRIMARY_NAME} (time_cap_bars {PRIMARY_CAP}, crypto)')
        if (len(sec) != 1 or sec[0].get('name') != DEV_ONLY or sec[0].get('time_cap_bars') is not None
                or sec[0].get('book') != 'crypto' or sec[0].get('promotable') is not False):
            bad.append(f'variants.secondary must be exactly {DEV_ONLY} (uncapped, crypto, never promotable)')
        if not str(v.get('selection', '')).startswith('none'):
            bad.append('variants.selection must declare no adaptive choice between the variants')
    except (KeyError, TypeError, AttributeError) as e:
        bad.append(f'variants: {e}')
    shared, names = _shared_failures(pre)
    bad += shared
    t = pre['trials']['declared']
    want = {'variant': 2, 'grid_point': len(names), 'baseline': 4, 'total': 6 + len(names)}
    if t != want or t['total'] != TRIALS:
        bad.append(f'declared trials differ from the records register appends ({TRIALS}, crypto family only)')
    bad += _universe_data_failures(pre, repo)
    m3 = pre['m3_reproduction'].get('manifest', {})
    import m3_repro as X                                                                    # noqa: E402
    if m3.get('id') != X.REPRO_ID or m3.get('digest') != X.REPRO_DIGEST or m3.get('source_class') != M.REPRO_ONLY:
        bad.append('m3_reproduction.manifest must pin legacy-m3-repro-v1 (repro-only) by digest')
    return bad


def _params_failures(pre: dict) -> list[str]:
    bad = []
    if pre['timeframe'] != T.TF:
        bad.append(f'timeframe must be {T.TF}')
    pp = dict(pre['primary_params'])
    win, cap = pp.pop('window_bars', None), pp.pop('time_cap_bars', None)
    if cap != PRIMARY_CAP:
        bad.append(f'primary_params: the promotable primary needs the finite time cap time_cap_bars {PRIMARY_CAP}')
    if pp != T.PRIMARY.doc() or win != T.WINDOW:
        bad.append('primary_params differ from trend_ema_mom.PRIMARY / WINDOW')
    return bad


def _shared_failures(pre: dict) -> tuple[list[str], list[str]]:
    """Neighbour grid, split plan + holdout shape, costs, EDGE-00 rows, verdict rules, books and the deferred gold
    pilot: identical for both profiles (STRAT-02 is the exact sign mirror on the same book, splits and costs)."""
    bad = []
    names = [n for n, _ in T.neighbours()]
    if pre['neighbour_grid']['configs'] != names or pre['neighbour_grid']['steps'] != T.STEPS:
        bad.append('neighbour grid differs from trend_ema_mom.neighbours()')
    sp = pre['splits']
    try:
        plan = S.SplitPlan(sp['splits'], interval=sp['interval'], lookback_bars=sp['lookback_bars'],
                           horizon_bars=sp['horizon_bars'])
        if sp['horizon_bars'] != PRIMARY_CAP:
            bad.append('split horizon_bars must equal the primary time cap (the purge / embargo horizon)')
        if sp['lookback_bars'] < T.max_lookback_bars([T.PRIMARY] + [p for _, p in T.neighbours()]):
            bad.append('lookback_bars does not cover every evaluated configuration')
        ho = plan.split('holdout')
        h0, h1 = S.parse_utc(ho['start']), S.parse_utc(ho['end'])
        if h0 <= S.parse_utc(SPENT_END) or h0 % (4 * 3_600_000) or h1 - h0 != HOLDOUT_WEEKS * 7 * 86_400_000:
            bad.append(f'holdout must start on a 4h boundary after {SPENT_END} and last exactly {HOLDOUT_WEEKS} weeks')
        cal = pre['costs']['slip_cal_v1']['calibration_window']
        if plan.split('calibration') != {**plan.split('calibration'), 'start': cal['start'], 'end': cal['end']}:
            bad.append('the calibration split must be the slip-cal-v1 calibration window')
    except (S.SplitError, KeyError, TypeError) as e:
        bad.append(f'split plan: {e}')
    try:
        C.validate_calibration(pre['costs']['slip_cal_v1'], C.DEFAULT_ROWS)
    except (C.CostError, KeyError, TypeError) as e:
        bad.append(f'slip-cal-v1: {e}')
    if pre['costs'].get('stress_rows') != list(C.STRESS):
        bad.append('stress_rows must list every R3 stress row')
    if set(pre['costs'].get('rows', {})) != {'crypto'}:
        bad.append('costs.rows: this run carries the crypto row only (gold rows belong to the deferred pilot)')
    if set(pre['costs'].get('deferred_pilot', {})) != set(DEFERRED):
        bad.append('costs.deferred_pilot must give gold-spot and gold-tokenized their own cost rows')
    if set(pre['edge00']) != EDGE_ROWS:
        bad.append(f'edge00 rows must be exactly {sorted(EDGE_ROWS)}')
    vr = pre['verdict_rules']
    if set(vr) != {'development', 'PROMOTE', 'REJECT', 'PARK'}:
        bad.append('verdict_rules must define development / PROMOTE / REJECT / PARK')
    elif not (isinstance(vr['REJECT'], dict) and 'powered_mean' in vr['REJECT']
              and set(vr['REJECT'].get('retained_gates', {})) == {'cost', 'drawdown', 'stability'}):
        bad.append('verdict_rules.REJECT must state the powered-mean rule AND retain the cost / drawdown / '
                   'stability gates')
    ur = pre['universe_rule']
    if set(ur['books']) != {'crypto'}:
        bad.append('books: the crypto top-40 book is the only book of this run (gold is a deferred pilot)')
    dp = ur.get('deferred_pilot', {})
    if any(not isinstance(dp.get(k), dict) or dp[k].get('symbol') != s or dp[k].get('book') != k
           or dp[k].get('cost_row') != k for k, s in DEFERRED.items()):
        bad.append('universe_rule.deferred_pilot must record gold-spot (XAUUSDT) and gold-tokenized (PAXGUSDT) as '
                   'separate books with separate cost rows')
    return bad, names


def _universe_data_failures(pre: dict, repo: str) -> list[str]:
    bad = []
    ur = pre['universe_rule']
    d = pre['data']
    if d.get('universe_path') != UNIVERSE:
        bad.append(f'data.universe_path must be the frozen static current list {UNIVERSE}')
    try:
        u = static_universe(repo, UNIVERSE)
        if (u['digest'], u['list_digest'], u['sources']['manifest_digest']) != (
                d['universe_digest'], d['universe_list_digest'], d['manifest_digest']):
            bad.append('universe / list / manifest digests differ from the committed static universe file')
    except (OSError, ValueError, KeyError, GateError) as e:
        bad.append(f'universe file: {e}')
    if d.get('label') != SU.LABEL or ur.get('label') != SU.LABEL:
        bad.append(f'data.label and universe_rule.label must be {SU.LABEL!r}')
    if ur.get('report_tiers') != TIERS:
        bad.append(f'universe_rule.report_tiers must be exactly {TIERS} (reported separately)')
    return bad


def _validate_short(pre: dict, repo: str) -> list[str]:
    """STRAT-02 (`trend_ema_mom_short.v1`, REG-02): the shared sections of STRAT-01 plus the short-only identity."""
    bad = []
    if set(pre) != SHORT_PREREG_KEYS:
        return [f'short prereg keys must be exactly {sorted(SHORT_PREREG_KEYS)}']
    if pre['format'] != PREREG_FORMAT or pre['candidate_id'] != SHORT_ID or pre['family'] != SHORT_FAMILY:
        bad.append('format / candidate_id / family mismatch (short identity)')
    try:
        bad += _params_failures(pre)
        if pre['hypothesis'].get('side') != 'short':
            bad.append('hypothesis.side must be short')
        v = pre['variants']
        pv = v['primary']
        if (pv.get('name') != SHORT_PRIMARY_NAME or pv.get('time_cap_bars') != PRIMARY_CAP or pv.get('book') != 'crypto'
                or pv.get('side') != 'short' or pv.get('promotable') is not True):
            bad.append(f'variants.primary must be the promotable short {SHORT_PRIMARY_NAME} (time_cap_bars '
                       f'{PRIMARY_CAP}, crypto, side short)')
        if v.get('secondary') != []:
            bad.append('variants.secondary must be empty: STRAT-02 has no uncapped / development-only variant')
        if not str(v.get('selection', '')).startswith('none'):
            bad.append('variants.selection must declare no adaptive choice')
        shared, names = _shared_failures(pre)
        bad += shared
        t = pre['trials']['declared']
        want = {'variant': 1, 'grid_point': len(names), 'baseline': 4, 'total': 5 + len(names)}
        if t != want or t['total'] != SHORT_TRIALS:
            bad.append(f'declared trials differ from the records register appends ({SHORT_TRIALS}: 1 capped primary + '
                       f'{len(names)} neighbours + B0-B3)')
        pm = str(pre['trials'].get('program_multiplicity', ''))
        if f'n = {PROGRAM_N_AT_FREEZE}' not in pm or 'trend_ema_mom' not in pm:
            bad.append(f'trials.program_multiplicity must declare the program Holm family n = {PROGRAM_N_AT_FREEZE} '
                       '(trend_ema_mom 18 + trend_ema_mom_short 17)')
        mult = str(pre['edge00'].get('multiplicity', ''))
        if 'trend_ema_mom_short AND trend_ema_mom' not in mult or f'n = {PROGRAM_N_AT_FREEZE}' not in mult:
            bad.append('edge00.multiplicity must gate on the program family of both ledgers')
        bad += _universe_data_failures(pre, repo)
        m3 = pre['m3_reproduction']
        if not isinstance(m3, dict) or m3.get('applicable') is not False or set(m3) != {'applicable', 'reason'}:
            bad.append('m3_reproduction must be inapplicable (applicable false, no manifest): no short M3 rule exists')
        sp = pre['splits']['splits']
        ho = next(x for x in sp if x['name'] == 'holdout')
        h0 = S.parse_utc(ho['start'])
        dev_end = max(S.parse_utc(x['end']) for x in sp if x['name'] != 'holdout')
        frozen = S.parse_utc(f"{pre['cairo_date']}T00:00:00Z")
        if h0 <= max(dev_end, frozen) or (h0 // 86_400_000 + 3) % 7 != 0 or h0 % 86_400_000:
            bad.append('holdout must start on a Monday 00:00 UTC after every development split and after the freeze '
                       'date (never back-dated)')
        r = pre['rule']
        if (set(r) != SHORT_RULE_KEYS or not str(r.get('entry', '')).startswith(SHORT_ENTRY)
                or 'cross_down' not in str(r.get('indicators', ''))
                or 'CEILED' not in str(r.get('stop', ''))
                or 'RECEIVES a positive rate' not in str(r.get('funding', ''))):
            bad.append('rule must be the short mirror (se cross_down / SELL entry, ceiled stop, short funding sign)')
        b = pre['baselines']
        if (f'seed {SHORT_SEED}' not in str(b.get('B0', '')) or f'seed {SHORT_SEED}' not in str(b.get('p_value', ''))
                or 'SHORT' not in str(b.get('B0', ''))):
            bad.append(f'baselines: B0 must be matched random SHORT entries and every seed {SHORT_SEED}')
        md = pre['missing_data']
        if (not isinstance(md, dict) or set(md) != {'policy', 'doc', 'rules'} or md['policy'] != MD.POLICY
                or md['doc'] != MISSING_DATA_DOC or not os.path.isfile(os.path.join(repo, MISSING_DATA_DOC))
                or not isinstance(md['rules'], dict) or set(md['rules']) != MISSING_DATA_RULES
                or not all(isinstance(x, str) and x for x in md['rules'].values())):
            bad.append(f'missing_data must bind the accepted policy {MD.POLICY} ({MISSING_DATA_DOC}) with exactly the '
                       f'rules {sorted(MISSING_DATA_RULES)}')
    except (KeyError, TypeError, AttributeError, StopIteration, ValueError) as e:
        bad.append(f'short prereg structure: {type(e).__name__}: {e}')
    return bad


# ------------------------------------------------------------------ the static universe (G5)
def static_universe(repo: str, rel: str, snapshot_rel: str = SNAPSHOT) -> dict:
    """The owner-approved static current list, re-validated by static_universe.validate against its committed
    exchangeInfo snapshot (count / order / CORE tiers / uniqueness / tradability / digests / byte-identical rebuild +
    approval). No network."""
    raw = _load(repo, rel)
    u = json.loads(raw)
    try:
        if raw != SU.dumps(u):
            raise GateError(f'{rel}: not in canonical written form')
        SU.validate(u, json.loads(_load(repo, snapshot_rel)))
    except SU.StaticUniverseError as e:
        raise GateError(f'{rel}: {e}') from e
    if u.get('label') != SU.LABEL:
        raise GateError(f'{rel}: label must be {SU.LABEL!r}')
    if u.get('owner_approved') is not True:
        raise GateError(f'{rel}: owner_approved is not true')
    return u


def universe_failures(repo: str, g: dict, pre: dict | None = None) -> list[str]:
    """G5: the gate's universe block binds the static artifact by digest + list digest, owner_approved true."""
    b = g.get('universe') or {}
    if b.get('owner_approved') is not True or not all(isinstance(b.get(k), str) and HEX64.match(b[k])
                                                      for k in ('digest', 'list_digest')):
        return ['G5 universe: the gate must pin a 64-hex digest + list_digest with owner_approved true']
    try:
        u = static_universe(repo, b.get('path', ''), b.get('snapshot', SNAPSHOT))
    except (OSError, ValueError, KeyError, GateError) as e:
        return [f'G5 universe: {e}']
    fail = []
    if (u['universe_id'], u['digest'], u['list_digest']) != (b.get('id'), b['digest'], b['list_digest']):
        fail.append('G5 universe: the file differs from the pinned id / digest / list digest')
    if pre is not None and (pre['data'].get('universe_digest'), pre['data'].get('universe_list_digest')) != (
            b['digest'], b['list_digest']):
        fail.append('G5 universe: the prereg cites a different universe than the gate pins')
    return fail


# ------------------------------------------------------------------ the gate
def gate(repo: str = M.REPO, *, gate_path: str = GATE, registry_genesis=None, need_registered: bool = True,
         need_cleared: bool = True) -> list[str]:
    fail = []
    try:
        g = json.loads(_load(repo, gate_path))
        pre_rel, led_rel = g['prereg'], g['ledger']
        if g.get('format') != GATE_FORMAT:
            return [f'gate file {gate_path}: format must be {GATE_FORMAT}']
        prof = route(g)
    except (OSError, ValueError, KeyError, AttributeError) as e:
        return [f'gate file {gate_path}: {e}']
    # G1
    try:
        if R.code_identity(repo)['dirty']:
            fail.append('G1 dirty working tree')
    except R.ReportError as e:
        fail.append(f'G1 {e}')
    # G2
    try:
        raw = _load(repo, pre_rel)
    except OSError as e:
        return fail + [f'G2 prereg missing: {e}']
    sha = hashlib.sha256(raw).hexdigest()
    head = _git(repo, 'show', f'HEAD:{pre_rel}', check=False)
    if head.returncode or head.stdout != raw:
        fail.append('G2 prereg is not committed at HEAD with these exact bytes')
    if 'prereg_sha256' in g and g['prereg_sha256'] != sha:
        fail.append('G2 the prereg bytes differ from the gate-pinned prereg_sha256')
    try:
        pre = json.loads(raw)
    except ValueError as e:
        return fail + [f'G2 prereg is not JSON: {e}']
    if not isinstance(pre, dict) or pre.get('candidate_id') != prof.candidate_id:
        return fail + [f'G2 routing: the gate routes {prof.candidate_id} but the prereg is another candidate']
    fail += [f'G2 {b}' for b in validate_prereg(pre, repo)]
    # G3
    if need_registered:
        try:
            fams = L.verify(os.path.join(repo, led_rel), registry_genesis=registry_genesis)
            recs = fams.get(pre.get('family'), [])
        except L.LedgerError as e:
            recs = None
            fail.append(f'G3 ledger does not verify: {e}')
        if recs is not None and prof.program:
            n = multiplicity(fams)['program']
            if n < PROGRAM_N_AT_FREEZE:
                fail.append(f'G3 program Holm family {n} < {PROGRAM_N_AT_FREEZE} (trend_ema_mom 18 + '
                            f'{SHORT_FAMILY} 17): a family is not fully registered')
        if recs is not None:
            want = trial_records(pre, sha, pre_rel)
            got = [r for r in recs if r['detail'].get('prereg_sha256') == sha]
            if not any(r['kind'] == 'variant' and r['detail'].get('role') == ROLE_PREREG for r in got):
                fail.append('G3 no preregistration record with this prereg SHA-256 in the family ledger (run register)')
            key = lambda r: (r['kind'], json.dumps(r['detail'], sort_keys=True))
            if sorted(map(key, got)) != sorted(map(key, want)):
                fail.append('G3 the ledger trial records for this prereg differ from the declared set')
            hb = _git(repo, 'show', f'HEAD:{led_rel}', check=False)
            committed = hb.stdout if not hb.returncode else b''
            if any(L.line_of(r) not in committed for r in got):
                fail.append('G3 the registration records are not committed at HEAD')
    # G4 + G5
    if need_cleared:
        fail += pin_failures(repo, g)
        fail += universe_failures(repo, g, pre)
    return fail


def _tree(repo, sha):
    p = _git(repo, 'rev-parse', '--verify', '--quiet', f'{sha}^{{tree}}', check=False)
    return None if p.returncode else p.stdout.decode().strip()


def pin_failures(repo: str, g: dict) -> list[str]:
    """G4: exact SHA pins with ancestry. A pin may carry `integrated` (Codex 6095813913): the reviewed source head
    `head` was rebase-merged, so the check is on the integrated master commit (exists, tree identical to the source
    head's, descends from `after`, merged into the integration ref, in HEAD); the rewritten source head itself is
    only required to exist and is never required to be a master ancestor."""
    fail, ref = [], g.get('integration_ref', 'origin/master')
    pins = g.get('pins') or []
    if not pins:
        return ['G4 no foundation heads pinned']
    for c in pins:
        h, integ = c.get('head', ''), c.get('integrated')
        tag = f'G4 {c.get("slice")} (#{",".join(map(str, c.get("prs", [])))}) {h[:7]}'
        if not HEX40.match(h) or (integ is not None and not HEX40.match(str(integ))):
            fail.append(f'{tag}: pins must be full 40-hex SHAs')
            continue
        missing = [x for x in (h, integ) if x and _git(repo, 'rev-parse', '--verify', '--quiet', f'{x}^{{commit}}',
                                                         check=False).returncode]
        if missing:
            fail.append(f'{tag}: commit {missing[0][:7]} not present (git fetch)')
            continue
        if c.get('branch'):
            tip = _git(repo, 'rev-parse', '--verify', '--quiet', c['branch'], check=False)
            if not tip.returncode and tip.stdout.decode().strip() != h:
                fail.append(f'{tag}: branch {c["branch"]} moved to {tip.stdout.decode().strip()[:7]} (uncleared round)')
        m = integ or h                                   # the commit that must be in master and HEAD
        if integ:
            tag += f' -> {integ[:7]}'
            if _tree(repo, h) != _tree(repo, integ):
                fail.append(f'{tag}: integrated commit tree differs from the reviewed source head tree')
        if c.get('after') and _git(repo, 'merge-base', '--is-ancestor', c['after'], m, check=False).returncode:
            fail.append(f'{tag}: does not descend from {c["after"][:7]}')
        if _git(repo, 'merge-base', '--is-ancestor', m, ref, check=False).returncode:
            fail.append(f'{tag}: not merged into {ref}')
        if _git(repo, 'merge-base', '--is-ancestor', m, 'HEAD', check=False).returncode:
            fail.append(f'{tag}: not contained in the executing HEAD')
    return fail


def refuse_unless_open(repo, **kw):
    fail = gate(repo, **kw)
    if fail:
        raise GateError('R4 gate closed:\n  ' + '\n  '.join(fail))


# ------------------------------------------------------------------ subcommands
def register(repo: str, *, author: str, cairo_date: str, gate_path: str = GATE, registry_genesis=None,
             now_ms: int | None = None) -> list[dict]:
    """Append the declared trial records once. Refused at or after the prereg's holdout start (Codex 6106611116): a
    forward holdout may never begin before its preregistration is recorded. Every refusal happens before the first
    byte is written. A root-family profile (STRAT-02) creates its family with its first append (lineage='root',
    exactly once) and needs the other program family fully registered first, so the program Holm family reaches
    PROGRAM_N_AT_FREEZE (35) with this registration."""
    refuse_unless_open(repo, gate_path=gate_path, registry_genesis=registry_genesis, need_registered=False,
                       need_cleared=False)
    g = json.loads(_load(repo, gate_path))
    prof = route(g)
    raw = _load(repo, g['prereg'])
    pre, sha = json.loads(raw), hashlib.sha256(raw).hexdigest()
    h0 = S.parse_utc(next(x for x in pre['splits']['splits'] if x['name'] == 'holdout')['start'])
    if (int(time.time() * 1000) if now_ms is None else now_ms) >= h0:
        raise GateError(f'registration refused: the holdout started at {S.utc(h0)}; re-date the prereg holdout first')
    path = os.path.join(repo, g['ledger'])
    fams = L.verify(path, registry_genesis=registry_genesis)
    recs = fams.get(pre['family'], [])
    if any(r['detail'].get('prereg_sha256') == sha for r in recs):
        raise GateError('this prereg is already registered (append-only: nothing appended)')
    want = trial_records(pre, sha, g['prereg'])
    if prof.root:
        if pre['family'] in fams:
            raise GateError(f'family {pre["family"]!r} already exists: its registration must create it as an '
                            'independent root (nothing appended)')
        if multiplicity(fams)['program'] + len(want) < PROGRAM_N_AT_FREEZE:
            raise GateError(f'the program Holm family would stay below {PROGRAM_N_AT_FREEZE}: register STRAT-01 '
                            'first (nothing appended)')
    out = []
    for i, r in enumerate(want):
        out.append(L.append(path, author=author, cairo_date=cairo_date, **r,
                            lineage='root' if prof.root and i == 0 else None))
    return out


def m3_identity(repo: str, rows, times, config: dict, prereg_sha: str | None) -> dict:
    """The run identity of an M3 reproduction: evaluator-identity digest (per cost row: R.eval_identity of the
    sandboxed entrypoint over the schedule) + exact commit + manifest / universe / classification / prereg digests."""
    import m3_repro as X                                                                    # noqa: E402
    per_row = {n: R.eval_digest(R.eval_identity(repo, EVAL_FILES, config, {
        'path': M3_EVALUATOR, 'function': X.FUNCTIONS[n], 'summary': 'summary'}, times), []) for n in rows}
    return {'commit': R.code_identity(repo)['git_head'],
            'evaluator': hashlib.sha256(M.canonical(per_row)).hexdigest(),
            'manifest': X.REPRO_DIGEST, 'universe': None, 'classification': None, 'prereg': prereg_sha}


def run_m3(repo: str, *, store: str, rows, out_dir: str, author: str, cairo_date: str, perturb: bool = True,
           gate_kw=None, clock=PG.cairo_now) -> list[str]:
    refuse_unless_open(repo, **(gate_kw or {}))
    import m3_repro as X                                                                    # noqa: E402
    import pit as P                                                                         # noqa: E402
    g = json.loads(_load(repo, GATE))
    X.check_embedded(repo)
    man = M.load(os.path.join(repo, X.REPRO_MANIFEST))
    lo, hi = X.span(P.Dataset(man, store, None))                 # manifest metadata only: no data row is read
    times = X.schedule(lo, hi)
    config = {'rows': list(rows), 'perturb': perturb, 'params': T.PRIMARY.doc(), 'window': T.WINDOW,
              'time_cap_bars': None, 'book': {k: str(v) for k, v in X.Book().__dict__.items()}}
    pre = os.path.join(repo, g['prereg'])
    ident = m3_identity(repo, rows, times, config, PG.file_sha256(pre) if os.path.exists(pre) else None)
    # Sanitized operational record (Codex 6094685201 / 6094780810): identity, stage state, integrity codes,
    # artifact hashes. No outcome ever enters it; the reports below are referenced only by SHA-256.
    prog = PG.Progress(out_dir, identity=ident, config=config, clock=clock)
    stage = 'm3.open'

    def opened():
        # STRAT-00: one freshly opened (ledgered) development window per cost row. A Window records the decisions it
        # issued, so a second row replayed on the same Window re-issues the same decision ids and is refused.
        ds, w, lo2, hi2 = X.open_repro(repo, store, os.path.join(repo, g['ledger']), author=author,
                                       cairo_date=cairo_date)
        if (ds.digest, lo2, hi2) != (X.REPRO_DIGEST, lo, hi):
            raise X.ReproError('the opened dataset differs from the declared identity')
        return ds, w

    try:
        prog.stage_start(stage)
        ds, w = opened()
        meta = {'code': R.code_identity(repo), 'identity': ident, 'manifest_digest': ds.digest,
                'labels': list(ds.labels), 'window': [S.utc(lo), S.utc(hi)], 'cycles': len(times)}
        prog.stage_done(stage)
        written = []
        for name in rows:
            stage = f'm3.{name}'
            prog.stage_start(stage)
            if name != rows[0]:
                ds, w = opened()
            ev = X.replay(w, start_ms=lo, end_ms=hi, function=X.FUNCTIONS[name], perturb=perturb)
            doc = X.report(name=name, ref_text=X.read_reference(repo, name), ev=ev, meta=meta)
            written.append(X.atomic_write_json(os.path.join(out_dir, f'm3_repro_{name}.json'), doc))
            prog.stage_done(stage, artifacts=[written[-1]])
    except Exception as e:
        prog.aborted([PG.integrity_code(type(e).__name__)], stage=stage)
        raise
    prog.sealed(written, [os.path.basename(p) for p in written])
    return written


def run_pit(repo: str, *, store: str, out_dir: str, author: str, cairo_date: str, resume: bool = False,
            gate_kw=None, clock=PG.cairo_now) -> list[str]:
    """STRAT-01 R4b-PIT (pit_run.py): the R4 gate first (nothing is read on any failure), then CORE-10 and CORE-20
    over the development splits. Returns the sealed final record paths (operational; no outcome is printed)."""
    refuse_unless_open(repo, **(gate_kw or {}))
    if route(json.loads(_load(repo, (gate_kw or {}).get('gate_path', GATE)))) is not LONG:
        raise GateError('pit: only STRAT-01 is wired; short real-environment evaluation is a later ticket')
    import pit_run as PR                                                                    # noqa: E402
    env = PR.real_env(repo, store=store, author=author, cairo_date=cairo_date,
                      gate_path=(gate_kw or {}).get('gate_path', GATE))
    return PR.run_tiers(env, out_dir, resume=resume, clock=clock)


def pit_incomplete(finals) -> list[str]:
    """The PIT tier finals that are not evidence: unreadable, not sealed, sealed with no evidence (an INCOMPLETE tier),
    or whose summary is not explicitly COMPLETE (missing-data policy; fail closed)."""
    import pit_run as PR                                                                    # noqa: E402
    bad = []
    for f in finals:
        doc = PG.read(f)
        ok = doc is not None and doc['state'] == 'sealed' and bool(doc['evidence'])
        if ok:
            try:
                for e in doc['evidence']:
                    with open(os.path.join(os.path.dirname(f), e), 'rb') as fh:
                        PR.require_complete(json.loads(fh.read()), e)
            except (OSError, ValueError):
                ok = False
        if not ok:
            bad.append(f)
    return bad


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description='RES-01 R4: gated M3 reproduction and PIT run of trend_ema_mom.v1')
    sub = ap.add_subparsers(dest='cmd', required=True)
    c = sub.add_parser('check')
    r = sub.add_parser('register')
    for x in (c, r):                                    # explicit candidate -> gate routing (REG-02)
        x.add_argument('--candidate', choices=sorted(PROFILES), default=LONG.candidate_id)
    m = sub.add_parser('m3')
    p = sub.add_parser('pit')
    for x in (r, m, p):
        x.add_argument('--author', required=True)
        x.add_argument('--cairo-date', required=True)
    m.add_argument('--store', required=True)
    m.add_argument('--cost', choices=['base', '2x', 'both'], default='both')
    m.add_argument('--out-dir', default=os.path.join(M.REPO, 'research_evidence', 'runs', 'r4'))
    p.add_argument('--store', required=True)
    p.add_argument('--out-dir', default=os.path.join(M.REPO, 'research_evidence', 'runs', 'r4b'))
    p.add_argument('--resume', action='store_true', help='re-run an interrupted tier under the same identity')
    a = ap.parse_args(argv)
    try:
        if a.cmd == 'check':
            prof = PROFILES[a.candidate]
            fail = gate(M.REPO, gate_path=prof.gate)
            tag = '' if prof is LONG else f' [{prof.candidate_id}]'
            print(f'R4 gate OPEN{tag}' if not fail else f'R4 gate CLOSED{tag}:\n  ' + '\n  '.join(fail))
            return 0 if not fail else 3
        if a.cmd == 'register':
            prof = PROFILES[a.candidate]
            recs = register(M.REPO, author=a.author, cairo_date=a.cairo_date, gate_path=prof.gate)
            print(f'appended {len(recs)} records; commit the ledger before running')
            if prof.program:
                n = multiplicity(L.verify(os.path.join(M.REPO, prof.ledger)))
                print(f'program Holm family n = {n["program"]} {n["by_family"]} '
                      f'(own-family {len(recs)}: reporting only)')
            return 0
        if a.cmd == 'm3':
            rows = ('base', '2x') if a.cost == 'both' else (a.cost,)
            for path in run_m3(M.REPO, store=a.store, rows=rows, out_dir=a.out_dir, author=a.author,
                               cairo_date=a.cairo_date):
                print(path)
            return 0
        finals = run_pit(M.REPO, store=a.store, out_dir=a.out_dir, author=a.author, cairo_date=a.cairo_date,
                         resume=a.resume)
        for path in finals:
            print(path)
        bad = pit_incomplete(finals)
        if bad:                                 # missing-data policy: sealed for audit, never evidence
            print('INCOMPLETE (no evidence; not usable by any gate, verdict, promotion or ledger record): '
                  + ', '.join(bad), file=sys.stderr)
            return 4
        return 0
    except GateError as e:
        print(str(e), file=sys.stderr)
        return 3
    except (L.LedgerError, R.ReportError, M.ManifestError, C.CostError, S.SplitError, PG.ProgressError,
            ValueError) as e:
        print(f'error: {e}', file=sys.stderr)
        return 2


if __name__ == '__main__':
    sys.exit(main())
