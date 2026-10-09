"""`zb-research-run/1`: the frozen run envelope, its `run_digest`, and the report shape (RES-01 R3; plan section 6).

The envelope's `run` block is everything that identifies a run before it executes: family, candidate, split + window,
the manifest / universe / split-plan / cost-model / slip-cal digests, `eval_digest` (evaluation code + config + seeds),
the seeds, the code identity (Git HEAD, dirty flag, Python, library versions), author and Cairo date.
`run_digest` = SHA-256 of the canonical JSON of `run`. An envelope is written once to `<runs_dir>/<run_digest>.json`
before any sealed access, and the ledger's `recompute_run_digest()` reloads it to prove a holdout record's digest.
A dirty tree or unrecorded library versions invalidate a run for sealed use (`sealable`).

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

FORMAT = 'zb-research-run/1'
HEX64 = re.compile(r'^[0-9a-f]{64}$')
DIGESTS = ('manifest_digest', 'universe_digest', 'split_plan_digest', 'cost_model_digest', 'slip_cal_digest',
           'eval_digest')
RUN_KEYS = {'family', 'candidate_id', 'split', 'window', 'seeds', 'code', 'author', 'cairo_date', *DIGESTS}
CODE_KEYS = {'git_head', 'dirty', 'python', 'libs'}
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


def code_identity(repo: str = M.REPO, libs=None) -> dict:
    """Git HEAD + dirty flag (any tracked or untracked change) + Python and library versions."""
    def git(*a):
        return subprocess.run(['git', '-C', repo, *a], capture_output=True, text=True, check=True,
                              stdin=subprocess.DEVNULL).stdout.strip()
    return {'git_head': git('rev-parse', 'HEAD'), 'dirty': bool(git('status', '--porcelain')),
            'python': platform.python_version(), 'libs': dict(sorted((libs or {}).items()))}


def eval_digest(paths, config: dict, seeds) -> str:
    """SHA-256 over the evaluation code files (bytes, in the given order), the canonical config and the seeds."""
    h = hashlib.sha256()
    for p in paths:
        with open(p, 'rb') as f:
            data = f.read()
        h.update(hashlib.sha256(data).digest())
    h.update(M.canonical({'config': config, 'seeds': list(seeds)}))
    return h.hexdigest()


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
    need(isinstance(run['seeds'], list) and all(type(s) is int for s in run['seeds']), 'seeds must be a list of ints')
    c = run['code']
    need(isinstance(c, dict) and set(c) == CODE_KEYS and isinstance(c['git_head'], str) and type(c['dirty']) is bool
         and isinstance(c['python'], str) and isinstance(c['libs'], dict), f'code must be {sorted(CODE_KEYS)}')


def run_digest(run: dict) -> str:
    validate_run(run)
    return _digest(run)


def envelope(run: dict) -> dict:
    return {'format': FORMAT, 'run': run, 'run_digest': run_digest(run)}


def sealable(env: dict) -> list[str]:
    """Reasons this envelope cannot open a sealed split (empty = it can)."""
    c = env['run']['code']
    out = []
    if c['dirty']:
        out.append('dirty working tree')
    if not re.match(r'^[0-9a-f]{40}$', c['git_head']):
        out.append('no Git HEAD recorded')
    return out


def write_envelope(runs_dir: str, env: dict) -> str:
    """Write once to `<runs_dir>/<run_digest>.json`; identical bytes are a no-op, different bytes are refused."""
    if env.get('format') != FORMAT or env.get('run_digest') != run_digest(env.get('run')) or set(env) != {
            'format', 'run', 'run_digest'}:
        raise ReportError('not a valid zb-research-run/1 envelope')
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
    `run_digest`. The record's identity (family, candidate, manifest, window, eval_digest) must equal the envelope's;
    a missing envelope or any identity difference returns a value that can never equal the stored digest."""
    try:
        env = load_envelope(runs_dir, rec.get('run_digest'))
        run = env['run']
        same = (run['family'] == rec['family'] and run['candidate_id'] == rec['candidate_id']
                and run['manifest_digest'] == rec['manifest_digest'] and run['window'] == rec['window']
                and run['eval_digest'] == rec['detail'].get('eval_digest') and run['split'] == rec['split'])
        return run_digest(run) if same else 'identity-mismatch'
    except (ReportError, KeyError, TypeError):
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
