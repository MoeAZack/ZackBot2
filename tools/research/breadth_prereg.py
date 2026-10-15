"""`zb-breadth-prereg/1`: the preregistration of the breadth Static-40 evaluation family (Codex 6200896386).

Separate from `zb-prereg/1` / run_r4 (which stay unchanged). One evaluation family `breadth_static40.v1` holding 12
immutable hypotheses (10 registered longs + the donchian_ens and squeeze_tp shorts), one holdout spend, one 12-test
Holm family. The ledger family file is `research_evidence/ledger/breadth_static40.jsonl` (zb-ledger/1 family names
cannot hold a dot; the candidate ids carry `breadth_static40.v1`).

  build      the prereg is a deterministic function of the frozen code: `build(repo)` recomputes every digest, the
             config read from the frozen runner and the per-key strategy definitions. `validate` refuses any prereg
             that differs from it (tamper) or any repo whose frozen files changed (drift).
  register   appends the 12 registration records (zb-ledger/1, kind `variant`, role `preregistration`) once. The time
             is taken internally (module CLOCK); no caller-supplied time is accepted; refused at or after the holdout
             start; refused when the accepted commit is later than the clock (backdating) or does not hold the exact
             prereg / validator bytes; refused when the family already exists (duplicate).
  verify_registration   the record-level check for CI: exactly the 12 records, one timestamp, accepted commit time
             <= registered_at_utc <= the commit that introduced the records < holdout start, prereg SHA bound, no
             tuning record ever (no retune), at most one holdout reveal (one evaluation).
  sign_flip_p / holm      the fixed day-level statistics, pure functions on already-computed daily P&L.

This module never reads market data: no cache, candle or network path (tests enforce it). The holdout is evaluated
later by a separate reviewed evaluation step; the frozen runner refuses any window after its sealed cutoff.

Usage: python tools/research/breadth_prereg.py check | build | register --author A --accepted-commit SHA | verify
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
from datetime import datetime, timezone

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(os.path.dirname(HERE))
sys.path.insert(0, HERE)
import ledger as L                                                                          # noqa: E402

FORMAT = 'zb-breadth-prereg/1'
CANDIDATE = 'breadth_static40.v1'
FAMILY = 'breadth_static40'
PREREG = 'research_evidence/breadth_prereg/breadth_static40.v1.json'   # outside prereg/ (zb-prereg/1 only)
LEDGER = 'research_evidence/ledger/breadth_static40.jsonl'
VALIDATOR = 'tools/research/breadth_prereg.py'
AUTHOR = 'claude-code'
FROZEN_CAIRO_DATE = '2026-10-15'
RULING = 'https://github.com/MoeAZack/ZackBot2/issues/13#issuecomment-6200896386'
HOLDOUT = {'start': '2026-10-19T00:00:00Z', 'end': '2027-10-18T00:00:00Z'}     # end-exclusive, 52 weeks
DEV_WINDOW = {'start': '2024-10-14T00:00:00Z', 'end': '2026-09-28T00:00:00Z'}
RUNNER_COMMIT = 'c8c7b92'
LONGS = ('breakout_pyramid', 'donchian_ens', 'ema_mom', 'ema_st', 'exp_breakout', 'exp_trend', 'hot_coin',
         'pullback_rsi', 'rotation', 'squeeze_tp')
SHORTS = ('donchian_ens', 'squeeze_tp')
HYPOTHESES = tuple((k, 'long') for k in LONGS) + tuple((k, 'short') for k in SHORTS)
BREADTH_BOTH = ('breakout_pyramid', 'donchian_ens', 'ema_mom', 'ema_st', 'pullback_rsi', 'squeeze_tp')  # engine.py L350
EVALUATOR = 'tools/research/breadth_eval.py'
CODE_FILES = ('tools/research/run_breadth_candidate.py', 'tools/research/collect_klines_research.py', 'strategies.py',
              'backtest.py', 'engine.py', 'tools/research/run_vwaprev_candidate.py', 'market_data.py', EVALUATOR)
REGISTRATION_FILES = ('research_evidence/ledger/breadth_static40.jsonl', 'research_evidence/ledger/REGISTRY.jsonl')
UNIVERSE = 'research_evidence/universe/static-top40-minhist365-contract-20261011.json'
DEV_INCOMPLETE = ('AKEUSDT', 'BRUSDT', 'HYPEUSDT', 'MUBARAKUSDT', 'PENGUUSDT', 'PUMPUSDT', 'TRUMPUSDT')
GATE = {'data': 'complete_and_accountable', 'no_bust': True, 'min_closed_trades': 30, 'net_cash_gt': 0.0,
        'pf_gt': 1.05, 'max_dd_r_lt': 60.0}
N_FLIPS = 100_000
ALPHA = 0.05
KEYS = {'format', 'candidate_id', 'family', 'author', 'cairo_date', 'status', 'ruling', 'holdout', 'development',
        'code', 'config', 'data', 'universe', 'hypotheses', 'gate', 'statistics', 'reporting', 'rules', 'registration'}
HEX40 = re.compile(r'[0-9a-f]{40}\Z')
HEX64 = re.compile(r'[0-9a-f]{64}\Z')
TS_RE = re.compile(r'\d{4}-\d\d-\d\dT\d\d:\d\d:\d\d\.\d{3}Z\Z')
DAY_MS = 86_400_000


class BreadthPreregError(ValueError):
    pass


def _ms(utc: str) -> int:
    return int(datetime.strptime(utc, '%Y-%m-%dT%H:%M:%SZ').replace(tzinfo=timezone.utc).timestamp() * 1000)


HOLDOUT_START_MS = _ms(HOLDOUT['start'])


def ts(ms: int) -> str:
    """Millisecond UTC timestamp `YYYY-MM-DDTHH:MM:SS.mmmZ`."""
    return datetime.fromtimestamp(ms // 1000, timezone.utc).strftime('%Y-%m-%dT%H:%M:%S') + f'.{ms % 1000:03d}Z'


def parse_ts(s) -> int:
    if not isinstance(s, str) or not TS_RE.match(s):
        raise BreadthPreregError(f'registered_at_utc {s!r} must be YYYY-MM-DDTHH:MM:SS.mmmZ')
    return _ms(s[:19] + 'Z') + int(s[20:23])


def _wall_clock_ms() -> int:
    return time.time_ns() // 1_000_000


CLOCK = _wall_clock_ms          # production time source; tests replace this module attribute, callers cannot pass one


def hypothesis_id(key: str, side: str) -> str:
    return f'{CANDIDATE}/{key}.{side}'


def _sha_file(repo: str, rel: str) -> str:
    with open(os.path.join(repo, rel), 'rb') as f:
        return hashlib.sha256(f.read().replace(b'\r\n', b'\n')).hexdigest()


def canon(obj) -> bytes:
    return json.dumps(obj, sort_keys=True, separators=(',', ':'), ensure_ascii=True, allow_nan=False).encode()


def dumps(pre: dict) -> bytes:
    """The written (LF, canonical-sorted, indented) form; the prereg SHA-256 is over exactly these bytes."""
    return (json.dumps(pre, sort_keys=True, indent=1, ensure_ascii=True, allow_nan=False) + '\n').encode()


# Development lineage (informational only: no spend, no trial). #114 = Static-40 defaults packets on runner c8c7b92.
LINEAGE_PACKETS = {
    'breakout_pyramid.long': '7874fd5dd95521d7a283c64dd8c3bdfafa208bce9c3eb902f136eeb484eac03c',
    'donchian_ens.long': '5550e6fe23230bd82455306a1fafeaf92ac11176aa7bce6ee62c4c8e772ffe59',
    'ema_mom.long': '54d6a0445148b8694a2e84e9056c9f4b6035aed299a65036efca8260c6e5aceb',
    'ema_st.long': 'abe7c630ca81fd545094638420f4f7643240e3062591f58cd2f1c762c7b7be8d',
    'exp_breakout.long': '9267a7594aa9bdfa37e3c470c25c04a46246ce98699fb8aa21d791bbd1115fb4',
    'exp_trend.long': '33a092e1fc8e75f5f3cb502efb16f4e091d03a99a713b79d19acf660897fca1e',
    'hot_coin.long': 'dc47f68468304761429f756b42bbe2e547e9b2a8e0725208de8caf6009087279',
    'pullback_rsi.long': '5a7550792d30f2c54127627df2c060522ea9df39392f18614566feeefe590afd',
    'rotation.long': '7e8ee4f4383e6605d046fd9cf7bcdc2f1f1511ee08639dd5f4070d74a4e55c1f',
    'squeeze_tp.long': '92faff1a7a0e1886fb6d3307b487616d0c46f4fb1f7c72bad0fa6de40ccb2661',
    'donchian_ens.short': 'dcc684c8bad4205d4a038b135d4e4ebbacec5b23d4034d7384b063edd9743214',
    'squeeze_tp.short': 'e0a48622438334a7ddb611406eb6739824cdb02c8a4d4a0cc259f2538e410a22',
}
LINEAGE_DIR = 'research_evidence/runs/breadth_candidate/2026-10-15_static40_c8c7b92'
DEV_DATA_MANIFEST_SHA = '4f778e3bad364aaf7c01ff7c3fd018b71756cc2df30dd16ae6464886c3004330'   # #113 4h collector manifest


def _frozen_config(repo: str) -> dict:
    """Read from the frozen modules themselves (no data is read)."""
    if repo not in sys.path:
        sys.path.insert(0, repo)
    sys.path.insert(0, os.path.join(repo, 'tools', 'research'))
    import backtest as BT
    import run_breadth_candidate as B
    return {'tf': '4h', 'mgmt_mode': 'defaults', 'mgmt_override': {},
            'costs': {'fee_taker': float(B.FEE_VALUE), 'slip': float(BT.SLIP), 'fund_per_bar_4h': float(BT.FUND_PER_BAR),
                      'note': 'every fill taker; adverse slip both sides; funding charged per held 4h bar'},
            'account': {k: float(v) if isinstance(v, float) else v for k, v in B.APP_DEFAULTS.items()},
            'warmup_bars': int(B.WARMUP_BARS), 'bust_frac': float(B.BUST_FRAC),
            'runner_format': B.FORMAT, 'runner_entry': 'run_breadth_candidate.run (frozen; same sleeve/Book path)'}


def _hypotheses(repo: str) -> list[dict]:
    import strategies as ST
    out = []
    for k, s in HYPOTHESES:
        st = ST.STRATEGIES[k]
        reg = st['sides']
        out.append({'id': hypothesis_id(k, s), 'key': k, 'side': s, 'tf': st.get('tf', '4h'),
                    'registry_sides': reg, 'paper_experimental_sides': 'both' if k in BREADTH_BOTH else reg,
                    'formal_basis': 'registry side' if reg in ('both', s) else
                    'registered here (PAPER experiment until this prereg; Codex 6200595681 / 6200896386)',
                    'params': dict(st.get('params') or {}), 'mgmt': ST.merge_mgmt(k, {}),
                    'reentry_lock': bool(st.get('reentry_lock')), 'fn': st['fn'].__name__,
                    'lineage': {'informational_only': True, 'spend': None,
                                'dev_packet': f"{LINEAGE_DIR}/{k}_{s}_defaults.json",
                                'dev_packet_sha256': LINEAGE_PACKETS[f'{k}.{s}'],
                                'dev_prs': ['#105', '#106', '#110', '#114'], 'shortlist': '#112'}})
    return out


def build(repo: str = ROOT) -> dict:
    """The one valid prereg for this repo state (deterministic)."""
    with open(os.path.join(repo, UNIVERSE), encoding='utf-8') as f:
        u = json.load(f)
    code = {rel: _sha_file(repo, rel) for rel in CODE_FILES}
    config = _frozen_config(repo)
    pre = {
        'format': FORMAT, 'candidate_id': CANDIDATE, 'family': FAMILY, 'author': AUTHOR,
        'cairo_date': FROZEN_CAIRO_DATE, 'ruling': RULING,
        'status': 'FROZEN v1: 12 hypotheses, one holdout spend, one 12-test Holm family. Registration appends 12 '
                  f'zb-ledger/1 records to {LEDGER} before the holdout start; any byte change voids the binding.',
        'holdout': dict(HOLDOUT, end_exclusive=True, weeks=52, sealed=True,
                        reveal='once, atomically, after the end; no interim looks; one evaluation per hypothesis'),
        'development': dict(DEV_WINDOW, end_exclusive=True, status='DEVELOPMENT-ONLY: cannot promote or reject'),
        'code': {'runner_commit': RUNNER_COMMIT, 'sha256': code,
                 'digest': hashlib.sha256(canon(code)).hexdigest()},
        'config': dict(config, digest=hashlib.sha256(canon(config)).hexdigest()),
        'data': {'source': 'https://fapi.binance.com/fapi/v1/klines (public Binance USD-M trade klines, no key)',
                 'collector': 'tools/research/collect_klines_research.py (zb-klines-research/1 manifest)',
                 'interval': '4h', 'holdout_fetch': 'warm-up (220 bars) + holdout, endTime = holdout end - 1 ms, '
                 'collected only after the holdout end, into a research cache outside the repo; manifest committed '
                 'with source, UTC bounds, rows, gaps and sha256 before any evaluation',
                 'dev_manifest_sha256': DEV_DATA_MANIFEST_SHA},
        'universe': {'path': UNIVERSE, 'sha256': _sha_file(repo, UNIVERSE), 'id': u['universe_id'],
                     'digest': u['digest'], 'list_digest': u['list_digest'], 'book': 'all 40 symbols, rank order',
                     'dev_incomplete': list(DEV_INCOMPLETE),
                     'incomplete_rule': 'a symbol with any missing / off-grid / duplicate 4h bar over warm-up + holdout '
                                        'is INCOMPLETE: excluded from the book, never filled; recorded in the packet',
                     'rotation_note': 'rotation ranks within the run book'},
        'hypotheses': _hypotheses(repo),
        'gate': dict(GATE, rule='per hypothesis, all must hold: packet status ok and every book symbol complete or '
                     'excluded by the incomplete rule; bust.busted false; closed trades >= 30; after-cost net cash > 0; '
                     'PF > 1.05; max drawdown < 60R; and Holm-adjusted sign-flip p <= alpha'),
        'statistics': {
            'test': 'one-sided day-level sign-flip (randomization) test of after-cost daily P&L vs 0',
            'unit': 'UTC calendar day of trade exit (t_out); daily P&L = sum of net cash pnl (after fees, slippage, '
                    'funding) of trades closed that day, in integer micro-USDT; days without a close are omitted',
            'statistic': 'T = sum of daily P&L', 'alternative': 'greater',
            'method': 'monte_carlo', 'n_flips': N_FLIPS,
            'rng': 'sign bits = SHA-256(seed + "|" + flip index + "|" + block) big-endian bit stream, bit 1 = flip',
            'seed': '<hypothesis id>', 'p_value': '(1 + #{flips with T* >= T}) / (n_flips + 1)',
            'multiplicity': {'method': 'Holm step-down', 'alpha': ALPHA, 'm': len(HYPOTHESES),
                             'ties': 'equal p-values keep hypothesis order'},
            'context_only': 'BTCUSDT side-matched passive (buy-and-hold for longs, short-and-hold for shorts) over '
                            'the holdout with the same costs: reported, never a gate'},
        'reporting': {'per_hypothesis': ['trades', 'pf', 'r_sum', 'pnl', 'max_dd_r', 'max_dd_equity', 'win_rate',
                                         'bust', 'incomplete', 'n_days', 'sign_flip_p', 'holm_adjusted_p',
                                         'gate_rows', 'verdict'],
                      'verdict': 'PASS only when every gate row holds; otherwise FAIL naming the rows',
                      'context': 'BTC side-matched passive', 'claims': 'no edge claim beyond the gate result'},
        'rules': {'no_retune': 'no parameter, mgmt, cost, universe, window or code change after registration; the '
                               'ledger family may never hold a variant / grid_point / baseline record besides the 12 '
                               'registration records (verify_registration)',
                  'one_evaluation': 'one holdout_reveal for the family (one spend); reruns only as zb-ledger/1 '
                                    'holdout_rerun of the identical identity',
                  'supersession': 'none: a new family and window; trend_ema_mom.v1 records are untouched',
                  'deferred': ['holdout evaluation', 'mainnet', 'tuning', 'breakout_pyramid short']},
        'registration': {'ledger': LEDGER, 'family': FAMILY, 'records': len(HYPOTHESES),
                         'kind': 'variant', 'role': 'preregistration',
                         'time': 'registered_at_utc (ms) taken internally at registration; refused at or after '
                                 f'{HOLDOUT["start"]}',
                         'binds': ['prereg_sha256', 'accepted_commit (the master commit holding the accepted prereg, '
                                   'validator and evaluator)', 'accepted_tree'],
                         'registration_commit': 'a direct descendant of accepted_commit whose diff from it touches only '
                                                'the family ledger and REGISTRY.jsonl (the family declaration)',
                         'authoritative_time': 'the time the registration commit lands on master (GitHub merge time; '
                                               'squash merge), before the holdout start',
                         'runbook': 'research_evidence/breadth_prereg/RUNBOOK.md',
                         'evaluation_guard': 'the evaluation path refuses a dirty worktree and any HEAD whose '
                                             'commit or tree differs from the registered accepted commit / tree '
                                             '(this transitively freezes every module the runner loads)'},
    }
    return pre


def validate(pre: dict, repo: str = ROOT) -> list[str]:
    bad = []
    if not isinstance(pre, dict) or set(pre) != KEYS:
        return [f'prereg keys must be exactly {sorted(KEYS)}']
    if (pre['format'], pre['candidate_id'], pre['family']) != (FORMAT, CANDIDATE, FAMILY):
        bad.append('format / candidate_id / family mismatch')
    ids = [h.get('id') for h in pre['hypotheses']] if isinstance(pre['hypotheses'], list) else []
    if len(ids) != len(set(ids)):
        bad.append('duplicate hypothesis id')
    if ids != [hypothesis_id(k, s) for k, s in HYPOTHESES]:
        bad.append('hypotheses must be exactly the 12 frozen ids in order')
    h0, h1 = _ms(HOLDOUT['start']), _ms(HOLDOUT['end'])
    if (pre['holdout'].get('start'), pre['holdout'].get('end')) != (HOLDOUT['start'], HOLDOUT['end']) \
            or h1 - h0 != 364 * DAY_MS or h0 <= _ms(f'{pre["cairo_date"]}T00:00:00Z'):
        bad.append('holdout window must be the frozen 52-week window after the freeze date')
    try:
        want = build(repo)
    except (OSError, KeyError, ValueError, ImportError) as e:
        return bad + [f'cannot rebuild the prereg from the frozen code: {type(e).__name__}: {e}']
    for k in sorted(KEYS):
        if canon(pre[k]) != canon(want[k]):
            bad.append(f'{k}: differs from the frozen build (tamper or drift of the frozen files)')
    return bad


def load_prereg(repo: str = ROOT) -> tuple[dict, str]:
    with open(os.path.join(repo, PREREG), 'rb') as f:
        raw = f.read().replace(b'\r\n', b'\n')
    return json.loads(raw), hashlib.sha256(raw).hexdigest()


# ------------------------------------------------------------------ statistics (pure; no data access)
def _sign_bits(seed: str, flip: int, n: int) -> str:
    """n sign bits ('1' = flip) from the SHA-256 counter stream of (seed, flip)."""
    out, block = [], 0
    while 256 * len(out) < n:
        out.append(format(int.from_bytes(hashlib.sha256(f'{seed}|{flip}|{block}'.encode()).digest(), 'big'), '0256b'))
        block += 1
    return ''.join(out)[:n]


def daily_pnl_micro(trades) -> list[int]:
    """Integer micro-USDT P&L per UTC exit day (sorted by day), from packet trades {'t_out', 'pnl'}."""
    by = {}
    for t in trades:
        day = t['t_out'][:10]
        by[day] = by.get(day, 0) + int(round(float(t['pnl']) * 1_000_000))
    return [by[d] for d in sorted(by)]


def sign_flip_p(daily: list[int], seed: str, n_flips: int = N_FLIPS) -> float:
    """One-sided (greater) Monte Carlo sign-flip p-value, exact integer arithmetic, library-independent RNG."""
    x = [int(v) for v in daily]
    t0 = sum(x)
    ge = 0
    for k in range(n_flips):
        bits = _sign_bits(seed, k, len(x))
        if sum(-v if b == '1' else v for v, b in zip(x, bits)) >= t0:
            ge += 1
    return (1 + ge) / (n_flips + 1)


def holm(pvals: dict, order, alpha: float = ALPHA) -> dict:
    """Holm step-down over `order` (tie-break = order). Returns {id: (adjusted_p, reject)}."""
    m = len(order)
    ranked = sorted(order, key=lambda h: (pvals[h], order.index(h)))
    out, run, stop = {}, 0.0, False
    for i, h in enumerate(ranked):
        run = max(run, min(1.0, (m - i) * pvals[h]))
        rej = (not stop) and pvals[h] * (m - i) <= alpha
        stop = stop or not rej
        out[h] = (run, rej)
    return out


def gate_rows(row: dict) -> dict:
    """The fixed per-hypothesis gate on one evaluated packet summary row."""
    return {'data': row['status'] == 'ok' and row['data_complete'] is True,
            'no_bust': row['busted'] is False,
            'min_closed_trades': row['trades'] >= GATE['min_closed_trades'],
            'net_cash': row['pnl'] > GATE['net_cash_gt'],
            'pf': row['pf'] is not None and row['pf'] > GATE['pf_gt'],
            'max_dd_r': row['max_dd_r'] < GATE['max_dd_r_lt'],
            'holm': row['holm_reject'] is True}


# ------------------------------------------------------------------ git helpers
def _git(repo, *a, check=True) -> str:
    p = subprocess.run(['git', '-C', repo, *a], capture_output=True)
    if check and p.returncode:
        raise BreadthPreregError(f'git {" ".join(a)}: {p.stderr.decode(errors="replace").strip()}')
    return p.stdout.decode() if not p.returncode else ''


def _blob(repo, rev, rel):
    p = subprocess.run(['git', '-C', repo, 'show', f'{rev}:{rel}'], capture_output=True)
    return p.stdout.replace(b'\r\n', b'\n') if p.returncode == 0 else None


def _commit_ms(repo, rev) -> int:
    return int(_git(repo, 'show', '-s', '--format=%ct', rev).strip()) * 1000


def _dirty(repo) -> list[str]:
    raw = _git(repo, 'status', '--porcelain=v1', '--untracked-files=all')
    return sorted(line[3:] for line in raw.splitlines() if line.strip())


# ------------------------------------------------------------------ registration
def registration_records(pre_sha: str, accepted_commit: str, accepted_tree: str, registered_ms: int) -> list[dict]:
    at = ts(registered_ms)
    cairo = datetime.fromtimestamp(registered_ms / 1000, timezone.utc).astimezone(_cairo()).strftime('%Y-%m-%d')
    out = []
    for k, s in HYPOTHESES:
        out.append({'kind': 'variant', 'candidate_id': hypothesis_id(k, s), 'split': 'development',
                    'window': {'start': DEV_WINDOW['start'], 'end': HOLDOUT['end']}, 'manifest_digest': None,
                    'run_digest': None, 'author': AUTHOR, 'cairo_date': cairo,
                    'detail': {'role': 'preregistration', 'format': FORMAT, 'evaluation_family': CANDIDATE,
                               'prereg_path': PREREG, 'prereg_sha256': pre_sha, 'accepted_commit': accepted_commit,
                               'accepted_tree': accepted_tree,
                               'registered_at_utc': at, 'key': k, 'side': s, 'holm_m': len(HYPOTHESES),
                               'holdout': dict(HOLDOUT)}})
    return out


def _cairo():
    try:
        from zoneinfo import ZoneInfo
        return ZoneInfo('Africa/Cairo')
    except Exception as e:                                   # no tz database: refuse rather than guess an offset
        raise BreadthPreregError(f'Africa/Cairo time zone unavailable: {e}')


def register(repo: str = ROOT, *, author: str = AUTHOR, accepted_commit: str, **unexpected) -> list[dict]:
    """Append the 12 registration records once. The time is CLOCK() (internal); no caller time is accepted."""
    if unexpected:
        raise BreadthPreregError(f'register takes no {sorted(unexpected)}: the registration time is taken internally')
    if author != AUTHOR:
        raise BreadthPreregError(f'author must be {AUTHOR!r}')
    now = int(CLOCK())
    if now >= HOLDOUT_START_MS:
        raise BreadthPreregError(f'registration refused: {ts(now)} is at or after the holdout start {HOLDOUT["start"]}')
    pre, sha = load_prereg(repo)
    bad = validate(pre, repo)
    if bad:
        raise BreadthPreregError('prereg invalid: ' + '; '.join(bad))
    if not (isinstance(accepted_commit, str) and HEX40.match(accepted_commit)):
        raise BreadthPreregError('accepted_commit must be the full 40-hex SHA of the Codex-accepted head')
    _git(repo, 'cat-file', '-e', f'{accepted_commit}^{{commit}}')
    tree = _git(repo, 'rev-parse', f'{accepted_commit}^{{tree}}').strip()
    for rel in (PREREG, VALIDATOR, EVALUATOR):
        if _blob(repo, accepted_commit, rel) != _blob(repo, 'HEAD', rel):
            raise BreadthPreregError(f'{rel} at HEAD differs from the accepted commit {accepted_commit[:10]}')
    if hashlib.sha256(_blob(repo, accepted_commit, PREREG)).hexdigest() != sha:
        raise BreadthPreregError('the working prereg differs from the accepted commit')
    if _commit_ms(repo, accepted_commit) > now:
        raise BreadthPreregError('the accepted commit is later than the registration clock (backdated registration)')
    dirt = [p for p in _dirty(repo) if p not in (LEDGER, f'{os.path.dirname(LEDGER)}/{L.REGISTRY}')]
    if dirt:
        raise BreadthPreregError(f'uncommitted changes besides the ledger: {dirt}')
    path = os.path.join(repo, LEDGER)
    fams = L.verify(path)
    if FAMILY in fams:
        raise BreadthPreregError(f'family {FAMILY!r} already registered (duplicate; append-only: nothing appended)')
    out = []
    for i, r in enumerate(registration_records(sha, accepted_commit, tree, now)):
        r['author'] = author
        out.append(L.append(path, **r, lineage='root' if i == 0 else None))
    return out


def _introducing_commit(repo, line: bytes):
    for c in _git(repo, 'rev-list', '--reverse', 'HEAD', '--', LEDGER).split():
        b = _blob(repo, c, LEDGER)
        if b is not None and line in b.splitlines(keepends=True):
            return c
    return None


def verify_registration(repo: str = ROOT, *, require_committed: bool = True) -> list[str]:
    """Record-level registration check (no data access). Returns failures; [] = valid or not yet registered."""
    path = os.path.join(repo, LEDGER)
    if not os.path.exists(path):
        return []
    try:
        recs = L.verify(path).get(FAMILY, [])
    except L.LedgerError as e:
        return [f'ledger: {e}']
    bad = []
    reg = [r for r in recs if r['detail'].get('role') == 'preregistration']
    ids = [r['candidate_id'] for r in reg]
    if len(ids) != len(set(ids)):
        bad.append('duplicate hypothesis id in the registration records')
    if ids != [hypothesis_id(k, s) for k, s in HYPOTHESES] or recs[:len(reg)] != reg:
        bad.append('registration must be exactly the 12 frozen hypothesis records, first in the family')
    if any(r['kind'] in L.TRIAL_KINDS for r in recs[len(reg):]):
        bad.append('no retune: a variant / grid_point / baseline record after the registration')
    if sum(r['kind'] == 'holdout_reveal' for r in recs) > 1:
        bad.append('one evaluation: more than one holdout_reveal in the family')
    if not reg:
        return bad
    d0 = reg[0]['detail']
    bind = ('registered_at_utc', 'accepted_commit', 'accepted_tree', 'prereg_sha256')
    if any(tuple(r['detail'].get(k) for k in bind) != tuple(d0.get(k) for k in bind) for r in reg):
        bad.append('all 12 records must carry one registered_at_utc / accepted_commit / accepted_tree / prereg_sha256')
    try:
        at = parse_ts(d0.get('registered_at_utc'))
    except BreadthPreregError as e:
        return bad + [str(e)]
    if at >= HOLDOUT_START_MS:
        bad.append('late registration: registered_at_utc is at or after the holdout start')
    _, sha = load_prereg(repo)
    if d0.get('prereg_sha256') != sha:
        bad.append('prereg_sha256 does not match the committed prereg')
    acc = d0.get('accepted_commit')
    if not (isinstance(acc, str) and HEX40.match(acc)) or _blob(repo, acc, PREREG) is None:
        bad.append('accepted_commit is not available in this clone (fetch it) or not a full SHA')
    else:
        if hashlib.sha256(_blob(repo, acc, PREREG)).hexdigest() != sha:
            bad.append('the accepted commit holds a different prereg')
        if at < _commit_ms(repo, acc):
            bad.append('backdated: registered_at_utc is before the accepted commit')
        if _git(repo, 'rev-parse', f'{acc}^{{tree}}').strip() != d0.get('accepted_tree'):
            bad.append('accepted_tree does not match the accepted commit')
    if require_committed:
        c = _introducing_commit(repo, L.line_of(reg[0]))
        if c is None:
            bad.append('the registration records are not committed')
        else:
            cm = _commit_ms(repo, c)
            if not (at <= cm + 999 and cm < HOLDOUT_START_MS):
                bad.append('the committing commit must be at/after registered_at_utc and before the holdout start')
            if isinstance(acc, str) and HEX40.match(acc):
                if subprocess.run(['git', '-C', repo, 'merge-base', '--is-ancestor', acc, c],
                                  capture_output=True).returncode:
                    bad.append('accepted_commit is not an ancestor of the registration commit')
                touched = set(_git(repo, 'diff', '--name-only', acc, c, check=False).split())
                if not touched or touched - set(REGISTRATION_FILES):
                    bad.append('registration commit is not ledger-only: the diff from accepted_commit touches '
                               f'{sorted(touched - set(REGISTRATION_FILES)) or "nothing"}')
    return bad


def registered_binding(repo: str = ROOT, ledger_ref: str = 'HEAD') -> dict:
    """The registration binding read from the family ledger at `ledger_ref` (hash chain checked; the full record checks
    are verify_registration's, run where the ledger is committed)."""
    raw = _blob(repo, ledger_ref, LEDGER)
    if raw is None:
        raise BreadthPreregError(f'no registration: {LEDGER} absent at {ledger_ref}')
    try:
        recs = [r for r, _ in L._lines(raw, FAMILY)]
    except L.LedgerError as e:
        raise BreadthPreregError(f'ledger at {ledger_ref}: {e}') from None
    reg = [r for r in recs if r['detail'].get('role') == 'preregistration']
    if [r['candidate_id'] for r in reg] != [hypothesis_id(k, s) for k, s in HYPOTHESES]:
        raise BreadthPreregError('the ledger does not hold the 12 registration records')
    d = reg[0]['detail']
    return {k: d[k] for k in ('accepted_commit', 'accepted_tree', 'prereg_sha256', 'registered_at_utc')}


def evaluation_guard(repo: str, binding: dict) -> None:
    """Called by the evaluation path before it reads anything: the worktree must be clean and HEAD must be exactly the
    registered accepted commit with the registered tree, so every module the frozen runner loads is the accepted
    byte-for-byte state (Codex 6201186673). Raises BreadthPreregError otherwise."""
    dirt = _dirty(repo)
    if dirt:
        raise BreadthPreregError(f'evaluation refused: dirty worktree {dirt[:5]}')
    head = _git(repo, 'rev-parse', 'HEAD').strip()
    tree = _git(repo, 'rev-parse', 'HEAD^{tree}').strip()
    if head != binding.get('accepted_commit') or tree != binding.get('accepted_tree'):
        raise BreadthPreregError(f'evaluation refused: HEAD {head[:10]} / tree {tree[:10]} is not the registered '
                                 'accepted commit / tree')
    if load_prereg(repo)[1] != binding.get('prereg_sha256'):
        raise BreadthPreregError('evaluation refused: prereg bytes differ from the registration')


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split('\n')[0])
    sub = ap.add_subparsers(dest='cmd', required=True)
    sub.add_parser('check')
    sub.add_parser('build')
    r = sub.add_parser('register')
    r.add_argument('--author', default=AUTHOR)
    r.add_argument('--accepted-commit', required=True)
    sub.add_parser('verify')
    a = ap.parse_args(argv)
    try:
        if a.cmd == 'build':
            with open(os.path.join(ROOT, PREREG), 'wb') as f:
                f.write(dumps(build(ROOT)))
            return 0
        if a.cmd == 'check':
            pre, sha = load_prereg(ROOT)
            bad = validate(pre, ROOT)
        elif a.cmd == 'register':
            recs = register(ROOT, author=a.author, accepted_commit=a.accepted_commit)
            print(f'registered {len(recs)} records at {recs[0]["detail"]["registered_at_utc"]}')
            return 0
        else:
            bad = verify_registration(ROOT)
            sha = load_prereg(ROOT)[1]
    except (BreadthPreregError, L.LedgerError) as e:
        print(f'error: {e}', file=sys.stderr)
        return 1
    for b in bad:
        print(f'FAIL {b}', file=sys.stderr)
    if not bad:
        print(f'OK {PREREG} sha256 {sha}')
    return 1 if bad else 0


if __name__ == '__main__':
    sys.exit(main())
