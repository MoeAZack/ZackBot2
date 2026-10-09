"""`zb-research-run/2`: the frozen run envelope, its `run_digest`, and the report shape (RES-01 R3; plan section 6).

The envelope's `run` block is everything that identifies a run before it executes: family, candidate, split + window,
universe books, the manifest / universe / split-plan / cost-model / slip-cal digests, the evaluation identity `eval`
(the canonical evaluation file list with each file's SHA-256, plus the config) and `eval_digest` over it and the seeds,
and the code identity (Git HEAD, dirty flag, Python, the canonical dependency set), author and Cairo date.
`run_digest` = SHA-256 of the canonical JSON of `run`.

Code identity is never self-attested (Codex R3 P1, comment 6077894871): `freeze_run` captures HEAD / status / Python /
dependencies itself from the checkout, refuses a dirty tree, requires every file of `CORE_EVAL_FILES` in the evaluation
file list, requires each listed file to be tracked by Git, and hashes the files itself. `verify_code` re-captures all
of it at holdout access and recomputes `eval_digest` from the files on disk: a different HEAD, a dirty tree, a changed
evaluation file, a different Python or dependency set fails closed. "Dirty" ignores only `research_evidence/` (ledger,
run envelopes and data artifacts are written during a run; they are evidence, not code).

An envelope is written once to `<runs_dir>/<run_digest>.json` before any sealed access, and the ledger's
`recompute_run_digest()` reloads it to prove a holdout record's digest and identity.

A report = envelope + `results` {side: {stress row: {section: {...}}}} with every section of plan section 6, and a
`report_digest` over the whole. R3 defines and validates the shape only; it computes no metric.
"""
from __future__ import annotations

import hashlib
import json
import os
import platform
import re
import subprocess
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import costs as C                                                                           # noqa: E402
import manifest as M                                                                        # noqa: E402

FORMAT = 'zb-research-run/2'
HEX64 = re.compile(r'^[0-9a-f]{64}$')
HEAD_RE = re.compile(r'^[0-9a-f]{40}$')
DIGESTS = ('manifest_digest', 'universe_digest', 'split_plan_digest', 'cost_model_digest', 'slip_cal_digest',
           'eval_digest')
RUN_KEYS = {'family', 'candidate_id', 'split', 'window', 'books', 'seeds', 'eval', 'code', 'author', 'cairo_date',
            *DIGESTS}
CODE_KEYS = {'git_head', 'dirty', 'python', 'libs'}
EVAL_KEYS = {'files', 'config'}
# Every evaluation runs on these research modules; a run must hash them along with its own strategy files.
CORE_EVAL_FILES = ('feasibility.py', 'tools/research/costs.py', 'tools/research/intrabar.py',
                   'tools/research/ledger.py', 'tools/research/manifest.py', 'tools/research/pit.py',
                   'tools/research/report.py', 'tools/research/splits.py', 'tools/research/universe.py')
# The research path is stdlib-only: the canonical third-party dependency set is empty, and any distribution named here
# would be pinned by its installed version.
RESEARCH_DEPS: tuple[str, ...] = ()
EVIDENCE_DIR = 'research_evidence'
SIDES = ('long', 'short')
SECTIONS = {
    'expectancy': ('mean_net_r', 'net_pct_equity', 'profit_factor', 'win_rate', 'avg_win_r', 'avg_loss_r'),
    'drawdown': ('max_mtm_dd', 'mc_dd_p5', 'mc_dd_p50', 'mc_dd_p95', 'longest_underwater_bars'),
    'mfe_mae': ('mfe_r', 'mae_r', 'give_back'),
    'counts': ('trades', 'episodes', 'month_blocks', 'refusals_by_reason', 'exposure_time', 'ambiguity_rate'),
    'ci': ('episode_ci95', 'trade_ci95', 'resamples', 'seed'),
    'contribution': ('by_symbol', 'by_year', 'by_regime', 'top5_episodes', 'loso', 'loyo'),
    'stability': ('neighbours', 'stop_first', 'target_first', 'survivor_only_vs_pit'),
    'intrabar': ('ambiguous', 'unresolved', 'unresolved_rate', 'by_label', 'park'),
    'baselines': ('b0_random', 'b1_hold', 'b2_momentum', 'b3_cash'),
    'follower': ('usdt_100', 'usdt_200', 'usdt_500', 'rules_backfilled_share'),
}


class ReportError(ValueError):
    pass


def _digest(obj) -> str:
    return hashlib.sha256(M.canonical(obj)).hexdigest()


def _git(repo: str, *a) -> str:
    try:
        return subprocess.run(['git', '-C', repo, *a], capture_output=True, text=True, check=True,
                              stdin=subprocess.DEVNULL).stdout.strip()
    except (OSError, subprocess.CalledProcessError) as e:
        raise ReportError(f'git {" ".join(a)} failed in {repo}: {e}')


def canonical_libs() -> dict:
    from importlib import metadata
    out = {}
    for name in RESEARCH_DEPS:
        try:
            out[name] = metadata.version(name)
        except metadata.PackageNotFoundError:
            out[name] = 'MISSING'
    return dict(sorted(out.items()))


def code_identity(repo: str = M.REPO) -> dict:
    """Captured from the checkout, never passed in: HEAD, dirty (any tracked or untracked change outside
    research_evidence/), Python version and the canonical dependency set."""
    status = _git(repo, 'status', '--porcelain', '--untracked-files=all', '--', '.', f':(exclude){EVIDENCE_DIR}')
    return {'git_head': _git(repo, 'rev-parse', 'HEAD'), 'dirty': bool(status), 'python': platform.python_version(),
            'libs': canonical_libs()}


def _file_sha(repo: str, rel: str) -> str:
    M.check_rel_path(rel)
    with open(M.contained(repo, rel), 'rb') as f:
        return hashlib.sha256(f.read()).hexdigest()


def eval_digest(files, config: dict, seeds) -> str:
    """SHA-256 over the canonical evaluation identity: the sorted [{path, sha256}] list, the config and the seeds."""
    return _digest({'files': list(files), 'config': config, 'seeds': list(seeds)})


def eval_identity(repo: str, paths, config: dict) -> dict:
    """The canonical evaluation block: sorted unique repo-relative tracked files (CORE_EVAL_FILES included), hashed
    from disk, plus the config."""
    paths = sorted(set(paths) | set(CORE_EVAL_FILES))
    tracked = set(_git(repo, 'ls-files', '--', *paths).splitlines())
    missing = [p for p in paths if p not in tracked]
    if missing:
        raise ReportError(f'evaluation files must be tracked by Git: {missing}')
    return {'files': [{'path': p, 'sha256': _file_sha(repo, p)} for p in paths], 'config': config}


def validate_run(run: dict) -> None:
    def need(ok, msg):
        if not ok:
            raise ReportError(msg)
    need(isinstance(run, dict) and set(run) == RUN_KEYS, f'run keys must be exactly {sorted(RUN_KEYS)}')
    for k in DIGESTS:
        need(isinstance(run[k], str) and HEX64.match(run[k]), f'{k} must be 64 lowercase hex')
    for k in ('family', 'candidate_id', 'split', 'author'):
        need(isinstance(run[k], str) and run[k], f'{k} must be a non-empty string')
    need(M._strict_date(run['cairo_date']), 'cairo_date must be YYYY-MM-DD')
    w = run['window']
    need(isinstance(w, dict) and set(w) == {'start', 'end'} and all(isinstance(w[k], str) for k in w),
         'window must be {start, end} UTC strings')
    need(isinstance(run['books'], list) and run['books'] == sorted(set(run['books']))
         and all(isinstance(b, str) and b for b in run['books']), 'books must be a sorted list of book names')
    need(isinstance(run['seeds'], list) and all(type(s) is int for s in run['seeds']), 'seeds must be a list of ints')
    e = run['eval']
    need(isinstance(e, dict) and set(e) == EVAL_KEYS and isinstance(e['config'], dict) and isinstance(e['files'], list)
         and all(isinstance(f, dict) and set(f) == {'path', 'sha256'} and isinstance(f['path'], str)
                 and isinstance(f['sha256'], str) and HEX64.match(f['sha256']) for f in e['files']),
         'eval must be {files: [{path, sha256}], config}')
    paths = [f['path'] for f in e['files']]
    need(paths == sorted(set(paths)) and set(CORE_EVAL_FILES) <= set(paths),
         f'eval.files must be sorted, unique and include every CORE_EVAL_FILES entry')
    need(run['eval_digest'] == eval_digest(e['files'], e['config'], run['seeds']),
         'eval_digest does not match the eval files, config and seeds')
    c = run['code']
    need(isinstance(c, dict) and set(c) == CODE_KEYS and isinstance(c['git_head'], str) and type(c['dirty']) is bool
         and isinstance(c['python'], str) and isinstance(c['libs'], dict), f'code must be {sorted(CODE_KEYS)}')


def run_digest(run: dict) -> str:
    validate_run(run)
    return _digest(run)


def envelope(run: dict) -> dict:
    return {'format': FORMAT, 'run': run, 'run_digest': run_digest(run)}


def freeze_run(*, repo: str = M.REPO, eval_files, config: dict, seeds, **ident) -> dict:
    """Build the envelope with an independently captured code identity and evaluation digest. `ident` carries family,
    candidate_id, split, window, books, author, cairo_date and the five data/cost digests. A dirty tree is refused."""
    code = code_identity(repo)
    if code['dirty']:
        raise ReportError('dirty working tree: commit the evaluation code before freezing a run')
    ev = eval_identity(repo, eval_files, config)
    run = dict(ident, code=code, eval=ev, seeds=list(seeds), eval_digest=eval_digest(ev['files'], config, seeds))
    return envelope(run)


def sealable(env: dict) -> list[str]:
    """Reasons this envelope cannot open a sealed split on its recorded face (empty = it can); the live check of the
    checkout is `verify_code`."""
    c = env['run']['code']
    out = []
    if c['dirty']:
        out.append('dirty working tree')
    if not HEAD_RE.match(c['git_head']):
        out.append('no Git HEAD recorded')
    return out


def verify_code(env: dict, repo: str = M.REPO) -> list[str]:
    """Re-capture the code identity from the executing checkout and recompute `eval_digest` from the files on disk.
    Returns the mismatches (empty = this checkout is exactly the frozen evaluation)."""
    run = env['run']
    try:
        validate_run(run)
        now = code_identity(repo)
    except ReportError as e:
        return [str(e)]
    out = sealable(env)
    if now['git_head'] != run['code']['git_head']:
        out.append(f'HEAD moved: {now["git_head"][:12]} != frozen {run["code"]["git_head"][:12]}')
    if now['dirty']:
        out.append('the executing checkout is dirty')
    if now['python'] != run['code']['python']:
        out.append(f'Python {now["python"]} != frozen {run["code"]["python"]}')
    if now['libs'] != run['code']['libs'] or run['code']['libs'] != canonical_libs():
        out.append('dependency set differs from the canonical frozen set')
    try:
        files = [{'path': f['path'], 'sha256': _file_sha(repo, f['path'])} for f in run['eval']['files']]
    except (OSError, M.ManifestError) as e:
        return out + [f'evaluation file unreadable: {e}']
    changed = [a['path'] for a, b in zip(files, run['eval']['files']) if a != b]
    if changed:
        out.append(f'evaluation files changed: {changed}')
    if eval_digest(files, run['eval']['config'], run['seeds']) != run['eval_digest']:
        out.append('eval_digest recomputed at access differs from the frozen one')
    return out


def write_envelope(runs_dir: str, env: dict) -> str:
    """Write once to `<runs_dir>/<run_digest>.json`; identical bytes are a no-op, different bytes are refused."""
    if env.get('format') != FORMAT or env.get('run_digest') != run_digest(env.get('run')) or set(env) != {
            'format', 'run', 'run_digest'}:
        raise ReportError(f'not a valid {FORMAT} envelope')
    path = os.path.join(runs_dir, env['run_digest'] + '.json')
    data = json.dumps(env, sort_keys=True, indent=1, ensure_ascii=True, allow_nan=False).encode('ascii') + b'\n'
    if os.path.exists(path):
        with open(path, 'rb') as f:
            if f.read() == data:
                return path
        raise ReportError(f'{path} exists with different content; run envelopes are immutable')
    with open(path, 'wb') as f:
        f.write(data)
    return path


def load_envelope(runs_dir: str, digest: str) -> dict:
    if not HEX64.match(digest or ''):
        raise ReportError('run digest must be 64 lowercase hex')
    path = os.path.join(runs_dir, digest + '.json')
    try:
        with open(path, 'rb') as f:
            env = json.loads(f.read(), parse_constant=lambda c: (_ for _ in ()).throw(ValueError(c)))
    except (OSError, ValueError) as e:
        raise ReportError(f'run envelope {digest[:12]}... unreadable: {e}')
    if not isinstance(env, dict) or env.get('format') != FORMAT or set(env) != {'format', 'run', 'run_digest'}:
        raise ReportError(f'run envelope {digest[:12]}... is not {FORMAT}')
    return env


def recompute_run_digest(rec: dict, runs_dir: str) -> str:
    """The R3 hook for ledger.py: the digest recomputed from the frozen envelope stored under the record's
    `run_digest`. The record's full identity (family, candidate, split, manifest, window, eval_digest) must equal the
    envelope's; a missing envelope or any identity difference returns a value that can never equal the stored digest."""
    try:
        env = load_envelope(runs_dir, rec.get('run_digest'))
        run = env['run']
        same = (run['family'] == rec['family'] and run['candidate_id'] == rec['candidate_id']
                and run['manifest_digest'] == rec['manifest_digest'] and run['window'] == rec['window']
                and run['eval_digest'] == rec['detail'].get('eval_digest') and run['split'] == rec['split'])
        return run_digest(run) if same else 'identity-mismatch'
    except (ReportError, KeyError, TypeError, AttributeError):
        return 'missing-or-invalid-envelope'


def make_report(env: dict, results: dict) -> dict:
    """Validate the results shape against plan section 6 and seal it with `report_digest`."""
    if env.get('run_digest') != run_digest(env['run']):
        raise ReportError('envelope run_digest does not match its run block')
    if not isinstance(results, dict) or not results or set(results) - set(SIDES):
        raise ReportError(f'results must be keyed by side {SIDES}')
    for side, rows in results.items():
        if not isinstance(rows, dict) or set(rows) != set(C.STRESS):
            raise ReportError(f'{side}: every stress row {sorted(C.STRESS)} is always reported')
        for row, secs in rows.items():
            if not isinstance(secs, dict) or set(secs) != set(SECTIONS):
                raise ReportError(f'{side}/{row}: sections must be exactly {sorted(SECTIONS)}')
            for s, keys in SECTIONS.items():
                if not isinstance(secs[s], dict) or set(secs[s]) != set(keys):
                    raise ReportError(f'{side}/{row}/{s}: keys must be exactly {sorted(keys)}')
    rep = {**env, 'results': results}
    try:
        rep['report_digest'] = _digest(rep)
    except ValueError:
        raise ReportError('results must be strict JSON (NaN / Infinity refused)')
    return rep
