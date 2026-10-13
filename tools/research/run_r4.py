"""RES-01 R4 entry point (plan sections 7, 8; R4-0 gate Codex 6094720765): M3 reproduction on the reproduction-only
manifest, then the PIT run of `trend_ema_mom.v1`. Every subcommand that touches data first passes the R4 gate; the
gate is code, not a comment.

  python tools/research/run_r4.py check
  python tools/research/run_r4.py register --author claude-code --cairo-date YYYY-MM-DD
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
`m3` keeps the sanitized progress snapshot + sealed final (progress.py, zb-run-progress/2) beside its reports:
operational state only. `register` (after Codex clears the prereg) needs G1 + G2 and appends the declared trial
records once; commit the ledger, then `m3` / `pit` pass G3. No network, no credentials; git is read locally (fetch
beforehand).
"""
from __future__ import annotations

import argparse
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


def trial_records(pre: dict, sha: str, rel: str) -> list[dict]:
    """The ledger records `register` appends: the capped primary variant (the preregistration), the uncapped
    development-only variant, every neighbour grid point and every baseline choice (crypto family only; the deferred
    gold pilot has its own future budget). Same order every time."""
    sp = pre['splits']['splits']
    win = {'start': sp[1]['start'], 'end': sp[-1]['end']}
    base = {'prereg_path': rel, 'prereg_sha256': sha}
    out = [dict(kind='variant', detail=dict(base, role=ROLE_PREREG, book='crypto', config=PRIMARY_NAME)),
           dict(kind='variant', detail=dict(base, role='development_only_variant', book='crypto', config=DEV_ONLY))]
    out += [dict(kind='grid_point', detail=dict(base, role='neighbour', book='crypto', config=n))
            for n in pre['neighbour_grid']['configs']]
    out += [dict(kind='baseline', detail=dict(base, role='baseline', baseline=b)) for b in ('B0', 'B1', 'B2', 'B3')]
    for r in out:
        r.update(candidate_id=pre['candidate_id'], split='development', window=win,
                 manifest_digest=pre['data']['manifest_digest'])
    return out


# ------------------------------------------------------------------ prereg validation
def validate_prereg(pre: dict, repo: str) -> list[str]:
    bad = []
    if not isinstance(pre, dict) or set(pre) != PREREG_KEYS:
        return [f'prereg keys must be exactly {sorted(PREREG_KEYS)}']
    if pre['format'] != PREREG_FORMAT or pre['candidate_id'] != T.RULE_ID or pre['family'] != 'trend_ema_mom':
        bad.append('format / candidate_id / family mismatch')
    if pre['timeframe'] != T.TF:
        bad.append(f'timeframe must be {T.TF}')
    pp = dict(pre['primary_params'])
    win, cap = pp.pop('window_bars', None), pp.pop('time_cap_bars', None)
    if cap != PRIMARY_CAP:
        bad.append(f'primary_params: the promotable primary needs the finite time cap time_cap_bars {PRIMARY_CAP}')
    if pp != T.PRIMARY.doc() or win != T.WINDOW:
        bad.append('primary_params differ from trend_ema_mom.PRIMARY / WINDOW')
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
    t = pre['trials']['declared']
    want = {'variant': 2, 'grid_point': len(names), 'baseline': 4, 'total': 6 + len(names)}
    if t != want or t['total'] != TRIALS:
        bad.append(f'declared trials differ from the records register appends ({TRIALS}, crypto family only)')
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
    m3 = pre['m3_reproduction'].get('manifest', {})
    import m3_repro as X                                                                    # noqa: E402
    if m3.get('id') != X.REPRO_ID or m3.get('digest') != X.REPRO_DIGEST or m3.get('source_class') != M.REPRO_ONLY:
        bad.append('m3_reproduction.manifest must pin legacy-m3-repro-v1 (repro-only) by digest')
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
    except (OSError, ValueError, KeyError) as e:
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
    try:
        pre = json.loads(raw)
    except ValueError as e:
        return fail + [f'G2 prereg is not JSON: {e}']
    fail += [f'G2 {b}' for b in validate_prereg(pre, repo)]
    # G3
    if need_registered:
        try:
            recs = L.verify(os.path.join(repo, led_rel), registry_genesis=registry_genesis).get(pre.get('family'), [])
        except L.LedgerError as e:
            recs = None
            fail.append(f'G3 ledger does not verify: {e}')
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
    forward holdout may never begin before its preregistration is recorded."""
    refuse_unless_open(repo, gate_path=gate_path, registry_genesis=registry_genesis, need_registered=False,
                       need_cleared=False)
    g = json.loads(_load(repo, gate_path))
    raw = _load(repo, g['prereg'])
    pre, sha = json.loads(raw), hashlib.sha256(raw).hexdigest()
    h0 = S.parse_utc(next(x for x in pre['splits']['splits'] if x['name'] == 'holdout')['start'])
    if (int(time.time() * 1000) if now_ms is None else now_ms) >= h0:
        raise GateError(f'registration refused: the holdout started at {S.utc(h0)}; re-date the prereg holdout first')
    path = os.path.join(repo, g['ledger'])
    recs = L.verify(path, registry_genesis=registry_genesis).get(pre['family'], [])
    if any(r['detail'].get('prereg_sha256') == sha for r in recs):
        raise GateError('this prereg is already registered (append-only: nothing appended)')
    out = []
    for r in trial_records(pre, sha, g['prereg']):
        out.append(L.append(path, author=author, cairo_date=cairo_date, **r))
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
    sub.add_parser('check')
    r = sub.add_parser('register')
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
            fail = gate(M.REPO)
            print('R4 gate OPEN' if not fail else 'R4 gate CLOSED:\n  ' + '\n  '.join(fail))
            return 0 if not fail else 3
        if a.cmd == 'register':
            recs = register(M.REPO, author=a.author, cairo_date=a.cairo_date)
            print(f'appended {len(recs)} records; commit the ledger before running')
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
