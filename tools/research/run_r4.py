"""RES-01 R4 entry point (plan sections 7, 8): M3 reproduction on the legacy manifest, then the PIT run of
`trend_ema_mom.v1`. Every subcommand that touches data first passes the R4 gate; the gate is code, not a comment.

  python tools/research/run_r4.py check
  python tools/research/run_r4.py register --author claude-code --cairo-date YYYY-MM-DD
  python tools/research/run_r4.py m3 --store <legacy data root> --cost base|2x|both --out-dir research_evidence/runs/r4
         --author claude-code --cairo-date YYYY-MM-DD
  python tools/research/run_r4.py pit ...        (refuses: the PIT evaluator lands after M3 acceptance)

Gate (`gate()` returns every failure; any failure = exit 3, nothing is read):
  G1 clean tree: the executing checkout has no uncommitted change (report.code_identity).
  G2 prereg committed: the prereg file is tracked, its working bytes equal HEAD's, and it validates (zb-prereg/1
     shape, the split plan builds, slip-cal-v1 validates, primary + neighbour configs equal trend_ema_mom.py, the
     lookback covers every configuration, the universe digest matches the committed universe file).
  G3 prereg registered: the family ledger verifies (ledger.verify: records, registry, append-only Git history) and
     holds the `variant` record of role `preregistration` carrying this prereg's SHA-256, plus every declared trial
     record; each of those lines is present in HEAD's committed ledger (registered AND committed).
  G4 slices cleared: each pinned R1/R2/R3 head (r4_gate.json) exists, equals its branch tip when that branch still
     exists (no newer uncleared fix round), is merged into the integration ref, and is contained in HEAD.
`m3` writes `progress-<run_id>.jsonl` (progress.py, zb-run-progress/1) beside its reports: operational state only.
`register` (after Codex clears the prereg) needs G1 + G2 and appends the declared trial records once; commit the
ledger, then `m3` / `pit` pass G3. No network, no credentials; git is read locally (fetch beforehand).
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import subprocess
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import costs as C                                                                           # noqa: E402
import ledger as L                                                                          # noqa: E402
import manifest as M                                                                        # noqa: E402
import progress as PG                                                                       # noqa: E402
import report as R                                                                          # noqa: E402
import splits as S                                                                          # noqa: E402
import trend_ema_mom as T                                                                   # noqa: E402

GATE = 'research_evidence/prereg/r4_gate.json'
UNIVERSE = 'research_evidence/universe/pit-top40-qv30d-v2.json'
PREREG_FORMAT = 'zb-prereg/1'
PREREG_KEYS = {'format', 'candidate_id', 'family', 'author', 'cairo_date', 'status', 'hypothesis', 'contamination',
               'data', 'universe_rule', 'timeframe', 'rule', 'primary_params', 'neighbour_grid', 'trials', 'costs',
               'splits', 'metrics', 'baselines', 'edge00', 'verdict_rules', 'stop_condition', 'm3_reproduction'}
EDGE_ROWS = {'sample', 'expectancy', 'baselines', 'multiplicity', 'pit', 'robustness', 'neighbours', 'intrabar',
             'e10_drawdown', 'e11_follower'}
EVAL_FILES = ('tools/research/m3_repro.py', 'tools/research/progress.py', 'tools/research/run_r4.py',
              'tools/research/trend_ema_mom.py')
ROLE_PREREG = 'preregistration'


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
    """The ledger records `register` appends: the primary variant (the preregistration), the secondary-book variant,
    every neighbour grid point and every baseline choice. Same order every time."""
    sp = pre['splits']['splits']
    win = {'start': sp[1]['start'], 'end': sp[-1]['end']}
    base = {'prereg_path': rel, 'prereg_sha256': sha}
    out = [dict(kind='variant', detail=dict(base, role=ROLE_PREREG, book='crypto', config='primary')),
           dict(kind='variant', detail=dict(base, role='book', book='gold-commodity', config='primary'))]
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
    cap, win = pp.pop('time_cap_bars', None), pp.pop('window_bars', None)
    if pp != T.PRIMARY.doc() or win != T.WINDOW or type(cap) is not int or cap < 1:
        bad.append('primary_params differ from trend_ema_mom.PRIMARY / WINDOW, or no finite time cap')
    names = [n for n, _ in T.neighbours()]
    if pre['neighbour_grid']['configs'] != names or pre['neighbour_grid']['steps'] != T.STEPS:
        bad.append('neighbour grid differs from trend_ema_mom.neighbours()')
    sp = pre['splits']
    try:
        plan = S.SplitPlan(sp['splits'], interval=sp['interval'], lookback_bars=sp['lookback_bars'],
                           horizon_bars=sp['horizon_bars'])
        if sp['horizon_bars'] != cap:
            bad.append('split horizon_bars must equal the declared time cap')
        if sp['lookback_bars'] < T.max_lookback_bars([T.PRIMARY] + [p for _, p in T.neighbours()]):
            bad.append('lookback_bars does not cover every evaluated configuration')
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
    if set(pre['edge00']) != EDGE_ROWS:
        bad.append(f'edge00 rows must be exactly {sorted(EDGE_ROWS)}')
    if set(pre['verdict_rules']) != {'development', 'PROMOTE', 'REJECT', 'PARK'}:
        bad.append('verdict_rules must define development / PROMOTE / REJECT / PARK')
    if set(pre['universe_rule']['books']) != {'crypto', 'gold-commodity'}:
        bad.append('books must state crypto (primary) and gold-commodity (secondary) coverage')
    t = pre['trials']['declared']
    if t != {'variant': 2, 'grid_point': len(names), 'baseline': 4, 'total': 6 + len(names)}:
        bad.append('declared trials differ from the records register appends')
    try:
        u = json.loads(_load(repo, UNIVERSE))
        if (u['digest'], u['manifest_digest']) != (pre['data']['universe_digest'], pre['data']['manifest_digest']):
            bad.append('universe / manifest digests differ from the committed universe file')
    except (OSError, ValueError, KeyError) as e:
        bad.append(f'universe file: {e}')
    return bad


# ------------------------------------------------------------------ the gate
def gate(repo: str = M.REPO, *, gate_path: str = GATE, registry_genesis=None, need_registered: bool = True,
         need_cleared: bool = True) -> list[str]:
    fail = []
    try:
        g = json.loads(_load(repo, gate_path))
        pre_rel, led_rel = g['prereg'], g['ledger']
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
    # G4
    if need_cleared:
        ref = g.get('integration_ref', 'origin/master')
        for c in g.get('cleared', []):
            h, tag = c['head'], f'G4 {c["slice"]} (#{c["pr"]}) {c["head"][:7]}'
            if _git(repo, 'rev-parse', '--verify', '--quiet', f'{h}^{{commit}}', check=False).returncode:
                fail.append(f'{tag}: commit not present (git fetch)')
                continue
            tip = _git(repo, 'rev-parse', '--verify', '--quiet', c['branch'], check=False)
            if not tip.returncode and tip.stdout.decode().strip() != h:
                fail.append(f'{tag}: branch {c["branch"]} moved to {tip.stdout.decode().strip()[:7]} (uncleared round)')
            if _git(repo, 'merge-base', '--is-ancestor', h, ref, check=False).returncode:
                fail.append(f'{tag}: not merged into {ref}')
            if _git(repo, 'merge-base', '--is-ancestor', h, 'HEAD', check=False).returncode:
                fail.append(f'{tag}: not contained in the executing HEAD')
        if not g.get('cleared'):
            fail.append('G4 no cleared slice heads pinned')
    return fail


def refuse_unless_open(repo, **kw):
    fail = gate(repo, **kw)
    if fail:
        raise GateError('R4 gate closed:\n  ' + '\n  '.join(fail))


# ------------------------------------------------------------------ subcommands
def register(repo: str, *, author: str, cairo_date: str, gate_path: str = GATE, registry_genesis=None) -> list[dict]:
    refuse_unless_open(repo, gate_path=gate_path, registry_genesis=registry_genesis, need_registered=False,
                       need_cleared=False)
    g = json.loads(_load(repo, gate_path))
    raw = _load(repo, g['prereg'])
    pre, sha = json.loads(raw), hashlib.sha256(raw).hexdigest()
    path = os.path.join(repo, g['ledger'])
    recs = L.verify(path, registry_genesis=registry_genesis).get(pre['family'], [])
    if any(r['detail'].get('prereg_sha256') == sha for r in recs):
        raise GateError('this prereg is already registered (append-only: nothing appended)')
    out = []
    for r in trial_records(pre, sha, g['prereg']):
        out.append(L.append(path, author=author, cairo_date=cairo_date, **r))
    return out


def run_m3(repo: str, *, store: str, rows, out_dir: str, author: str, cairo_date: str, perturb: bool = True,
           gate_kw=None, clock=PG.cairo_now) -> list[str]:
    refuse_unless_open(repo, **(gate_kw or {}))
    import m3_repro as X                                                                    # noqa: E402
    g = json.loads(_load(repo, GATE))
    config = {'rows': list(rows), 'perturb': perturb, 'params': T.PRIMARY.doc(), 'window': T.WINDOW,
              'book': {k: str(v) for k, v in X.Book().__dict__.items()}, 'source_prefix': X.SOURCE_PREFIX}
    code = R.code_identity(repo)
    # Sanitized operational record (Codex 6094685201): commit, declared dataset identity, stage state, integrity
    # codes, artifact hashes. No outcome ever enters it; the reports below are referenced only by SHA-256.
    prog = PG.Progress(out_dir, commit=code['git_head'], config=config, clock=clock,
                       dataset={'manifest': X.LEGACY_MANIFEST, 'digest': X.LEGACY_DIGEST})
    stage = 'm3.open'
    try:
        prog.stage_start(stage)
        ds, w, lo, hi = X.open_legacy(repo, store, os.path.join(repo, g['ledger']), author=author,
                                      cairo_date=cairo_date)
        if ds.digest != X.LEGACY_DIGEST:
            raise X.ReproError('manifest digest differs from the declared legacy identity')
        rules = X.load_rules(os.path.join(repo, X.RULES_PATH), X.CORE8)
        meta = {'code': code, 'eval': R.eval_identity(repo, EVAL_FILES, config),
                'manifest_digest': ds.digest, 'labels': list(ds.labels), 'window': [S.utc(lo), S.utc(hi)]}
        prog.stage_done(stage)
        written = []
        for name in rows:
            stage = f'm3.{name}'
            prog.stage_start(stage)
            rep = X.replay(w, start_ms=lo, end_ms=hi, costs=X.cost_row(name), rules=rules, perturb=perturb)
            doc = X.report(name=name, ref_text=X.read_reference(repo, name), rep=rep, meta=meta)
            p = os.path.join(out_dir, f'm3_repro_{name}.json')
            with open(p, 'w', encoding='utf-8', newline='\n') as f:
                json.dump(doc, f, indent=1, sort_keys=True)
                f.write('\n')
            written.append(p)
            prog.stage_done(stage, artifacts=[p])
    except Exception as e:
        prog.aborted([type(e).__name__], stage=stage)
        raise
    prog.sealed(written, [os.path.basename(p) for p in written])
    return written


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
        refuse_unless_open(M.REPO)
        print('pit: the PIT evaluator lands after M3 is REPRODUCED and reviewed (R4b); nothing run', file=sys.stderr)
        return 4
    except GateError as e:
        print(str(e), file=sys.stderr)
        return 3
    except (L.LedgerError, R.ReportError, M.ManifestError, C.CostError, S.SplitError, PG.ProgressError,
            ValueError) as e:
        print(f'error: {e}', file=sys.stderr)
        return 2


if __name__ == '__main__':
    sys.exit(main())
