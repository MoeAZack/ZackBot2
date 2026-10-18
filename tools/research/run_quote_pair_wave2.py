"""Wave-2 USDT/USDC fee-rescue batch runner (research only, contract quote_pair_wave2_fee_rescue.v0).

One non-interactive command consumes the frozen candidate manifest (zb-wave2-fee-rescue-candidates/1), the 15m twin
manifest (zb-quote-pair-wave2-15m/1), the complete 4h twins (zb-quote-pair-klines/1) and the frozen USDT/USDC
accounting manifests (quote-pair-accounting/1). Everything is pinned and validated BEFORE any simulation: git head and
tree (clean), the runner / evaluator / registry files, every manifest and data file, and each fee-scenario definition.
Any defect returns non-zero and writes ONLY an explicit INCOMPLETE run record - never a comparison verdict.

Each cell (strategy x side x tf from the manifest; candidates and negative-gross controls) runs the app backtest on
each quote with quote-local BTC context and the manifest's own side (sleeve 'sides'), then every fill is re-priced
under each fee scenario's per-quote taker rate with the v0 view-A machinery (slip-v1 on 15 causally closed bars of the
cell's own tf, adverse tick rounding, exact funding on the held quantity with the 1h mark proxy). Episodes pair on the
exact (strategy, side, base, t_entry) key. Output: immutable per-cell detail files + one versioned summary
(zb-quote-pair-wave2-report/1). `USDC BETTER` is always UNAVAILABLE and the result is non-promotable; no routing,
config, install or executable path is touched.

Output layout under --out-dir (all write-once):
  <run_id>/preflight.json         pins of every input (deterministic; run_id = its sha256[:16])
  <run_id>/cells/<cell>.json      {'cell_sha256', 'content'} - hash-bound to itself and to the preflight
  <run_id>/summary.json           the report; only `run_record` varies between identical reruns (time / paths)
  INCOMPLETE.<utc stamp>.json     on any refusal (and nothing else new)
A rerun with identical pins reuses the same run_id and resumes only from cells whose envelope hash and preflight hash
verify; a tampered cell refuses the run. Changed inputs give a new run_id.
"""
import argparse
import datetime as _dt
import hashlib
import json
import math
import os
import subprocess
import sys

import numpy as np

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(os.path.dirname(HERE))
for _p in (ROOT, HERE):
    if _p not in sys.path:
        sys.path.insert(0, _p)

import backtest as BT  # noqa: E402
import costs as C  # noqa: E402
import run_quote_pair_usdc as Q  # noqa: E402
import strategies as S  # noqa: E402

BC, VC = Q.BC, Q.VC
REPORT_FORMAT = 'zb-quote-pair-wave2-report/1'
CELL_FORMAT = 'zb-quote-pair-wave2-cell/1'
PREFLIGHT_FORMAT = 'zb-quote-pair-wave2-preflight/1'
RUN_RECORD_FORMAT = 'zb-quote-pair-wave2-run-record/1'
CONTRACT = 'quote_pair_wave2_fee_rescue.v0'
CANDIDATES_FORMAT = 'zb-wave2-fee-rescue-candidates/1'
W15_FORMAT, W15_CONTRACT = 'zb-quote-pair-wave2-15m/1', 'quote_pair_wave2_15m.v0'
KLINES15_FORMAT = 'zb-klines-research/1'
USDC_BETTER = 'USDC BETTER: UNAVAILABLE'
VARIABLE_FIELDS = ('run_record',)            # the only summary fields that differ between identical reruns

# Frozen expected sha256 (re-verified from the files on every run; tests inject their own table).
EXPECTED = {
    'candidates': '1812f1f5b12cda24d1ade5be44e5b9f5e7d8f7f6a5fb071f4ef76f48094d21be',
    'wave2_15m': 'edf1a497601b822d8681c2c47749dfdf1c4f98b155eb59d301d80bf1b0f42271',
    'usdt_4h': 'd7c5572365843c0f3d1d259e898ef29ab95f75fd323d9dc4cc0a946ef21b2b0c',
    'usdc_4h': 'd596ca74d619b2c648180c8f4ab5e24ac5772f8aa4c2be6b8ffbe65a8a387c0a',
    'usdt_accounting': '7cfd88a8c328b18984ee7fbb94996500ad2fda185798940f2f48b6068b353898',
    'usdc_accounting': '3d250e9d841c8b97bd307dc1e7a752648a5ac0dd0932217cc2116bb06b20a731',
    'strategy_registry': '9c58dd236f0e70f6d138e8601cb35498c00499a19710f6cacd190599ebb68908',
    'fee': {'equal_5bp': 'cd7abfab23c0f5c6fc38495097394592a00e9adf9ba3af1cb9a67a28403c30cb',
            'advertised_regular_user': '36ec2c76648ada43da1b663537fbeebe44227de0e55c5ea5a159f1998f2fdc73',
            'promotion_expiry_stress': 'd2ef6be2f429bccfefcf861b6a995a596f0cd189d32aa563cda7115be416b799'},
}
# Fee scenarios are exactly the manifest's three; status -> how the row may be used.
FEE_STATUS = {'equal_5bp': 'COMPLETE', 'advertised_regular_user': 'PROVISIONAL_CURRENT_SCHEDULE',
              'promotion_expiry_stress': 'INCOMPLETE'}
FEE_USE = {'COMPLETE': 'market-path isolation (equal fees); research only, no verdict',
           'PROVISIONAL_CURRENT_SCHEDULE': 'PROVISIONAL: never a quote-promotion verdict input',
           'INCOMPLETE': 'INCOMPLETE: no USDC numbers, never a comparison'}

# slip-v1 lookback = 15 causally closed bars of the cell's OWN tf (Integration ruling, Codex 6248681512): never pooled
# or equated across timeframes. One named constant per tf.
SLIP_BARS_15M = 15
SLIP_BARS_4H = 15
TFS = {'15m': {'tf_ms': 15 * 60_000, 'slip_bars': SLIP_BARS_15M},
       '4h': {'tf_ms': 4 * 3_600_000, 'slip_bars': SLIP_BARS_4H}}
ROUTED_TFS = tuple(TFS)
SIDES = ('long', 'short')
# A negative-gross CONTROL on a tf with no routed twin data is recorded as NOT_RUN (never simulated, never counted);
# a CANDIDATE on such a tf refuses the run.
UNROUTED_CONTROL_STATUS = 'NOT_RUN_UNROUTED_TF'
CODE_FILES = ('tools/research/run_quote_pair_wave2.py', 'tools/research/run_quote_pair_usdc.py', 'backtest.py',
              'feasibility.py', 'tools/research/costs.py', 'tools/research/run_breadth_candidate.py',
              'tools/research/run_vwaprev_candidate.py')                 # every module the simulation / pricing imports
# exchange_filter_refusals: backtest.run with exchange_rules=None (legacy mode) - ONE USDT-twin min-notional floor applied
# to both quotes; USDC lot / min-notional rules are loaded and pinned but only tickSize prices fills. Symmetric, not per-quote.
FILTER_BASIS = 'legacy_usdt_twin_min_notional_floor_both_quotes'
REGISTRY_FILE = 'strategies.py'


class Wave2Error(RuntimeError):
    pass


# ------------------------------------------------------------------ helpers
def sha_bytes(b):
    return hashlib.sha256(b).hexdigest()


def sha_file(path, what):
    try:
        with open(path, 'rb') as f:
            return sha_bytes(f.read())
    except OSError:
        raise Wave2Error(f'{what}: file missing or unreadable ({path})') from None


def canon(obj):
    return json.dumps(obj, sort_keys=True, separators=(',', ':'), allow_nan=False).encode()


def fee_definition_sha(definition):
    """Canonical form of a fee definition: compact sorted JSON, UTF-8, no trailing newline (matches the manifest)."""
    return sha_bytes(json.dumps(definition, sort_keys=True, separators=(',', ':')).encode('utf-8'))


def clean(x):
    """JSON-safe, deterministic: numpy scalars -> python, non-finite -> None, tuples -> lists."""
    if isinstance(x, dict):
        return {str(k): clean(v) for k, v in x.items()}
    if isinstance(x, (list, tuple)):
        return [clean(v) for v in x]
    if isinstance(x, (bool, np.bool_)):
        return bool(x)
    if isinstance(x, (int, np.integer)):
        return int(x)
    if isinstance(x, (float, np.floating)):
        v = float(x)
        return v if math.isfinite(v) else None
    return x


def dumps(obj):
    return json.dumps(clean(obj), sort_keys=True, indent=1, allow_nan=False) + '\n'


def write_once(path, text):
    """Write a new file atomically; an existing path is never overwritten."""
    if os.path.exists(path):
        raise Wave2Error(f'{path} exists; output is never overwritten')
    os.makedirs(os.path.dirname(os.path.abspath(path)), exist_ok=True)
    tmp = f'{path}.partial.{os.getpid()}'
    with open(tmp, 'x', encoding='utf-8', newline='\n') as f:
        f.write(text)
    try:
        os.rename(tmp, path)                 # refuses an existing destination on Windows; checked above elsewhere
    except OSError:
        os.remove(tmp)
        raise Wave2Error(f'{path} exists; output is never overwritten') from None


def _safe_rel(root, rel, what):
    if not isinstance(rel, str) or not rel or os.path.isabs(rel) or '..' in rel.replace('\\', '/').split('/'):
        raise Wave2Error(f'{what}: unsafe or missing relative path {rel!r}')
    return os.path.join(root, *rel.replace('\\', '/').split('/'))


def _read_json(path, what):
    try:
        return Q._read_json(path, what)
    except Q.PairRunnerError as e:
        raise Wave2Error(str(e)) from None


def git_state(repo):
    """Exact HEAD and tree of a clean checkout (tracked + untracked); refuses a dirty tree."""
    try:
        head = VC.clean_commit(repo)
    except VC.RunnerError as e:
        raise Wave2Error(f'git tree not clean / unprovable: {e}') from None
    try:
        tree = subprocess.run(['git', '-C', repo, 'rev-parse', 'HEAD^{tree}'], capture_output=True, text=True,
                              check=True).stdout.strip()
    except (OSError, subprocess.CalledProcessError) as e:
        raise Wave2Error(f'cannot read the git tree ({e})') from None
    return {'head': head, 'tree': tree, 'clean': True}


def _iso(ms):
    return _dt.datetime.fromtimestamp(ms / 1000, _dt.timezone.utc).strftime('%Y-%m-%dT%H:%M:%SZ')


def _ms(iso):
    return int(_dt.datetime.strptime(iso, '%Y-%m-%dT%H:%M:%SZ').replace(tzinfo=_dt.timezone.utc).timestamp() * 1000)


def window():
    return {'fetch_start': Q.WARM_S, 'development_start': Q.START_S, 'end_exclusive': Q.END_S}


# ------------------------------------------------------------------ candidate manifest
def _pin(name, got, expected=None):
    want = (expected or EXPECTED)[name]
    if got != want:
        raise Wave2Error(f'{name}: sha256 {got} != pinned {want}')
    return got


def check_fee_scenarios(fs):
    if not isinstance(fs, list) or len(fs) != len(FEE_STATUS):
        raise Wave2Error(f'fee_scenarios must be exactly {sorted(FEE_STATUS)}')
    out = {}
    for rec in fs:
        d = rec.get('definition') if isinstance(rec, dict) else None
        if not isinstance(d, dict) or d.get('id') not in FEE_STATUS or d['id'] in out:
            raise Wave2Error('fee scenario: unknown, duplicate or malformed definition')
        sid = d['id']
        got = fee_definition_sha(d)
        if rec.get('definition_sha256') != got:
            raise Wave2Error(f'fee scenario {sid}: definition_sha256 does not match its definition')
        if got != EXPECTED['fee'].get(sid):
            raise Wave2Error(f'fee scenario {sid}: definition sha {got} != pinned {EXPECTED["fee"].get(sid)}')
        if d.get('status') != FEE_STATUS[sid]:
            raise Wave2Error(f'fee scenario {sid}: status {d.get("status")!r} != {FEE_STATUS[sid]!r}')
        tk = d.get('taker_bps')
        if not isinstance(tk, dict) or set(tk) != set(Q.QUOTES):
            raise Wave2Error(f'fee scenario {sid}: taker_bps must name exactly {Q.QUOTES}')
        for q, v in tk.items():
            if v is None:
                if d['status'] != 'INCOMPLETE':
                    raise Wave2Error(f'fee scenario {sid}: {q} taker is null but status is not INCOMPLETE')
            elif isinstance(v, bool) or not isinstance(v, (int, float)) or not 0 <= v < 100:
                raise Wave2Error(f'fee scenario {sid}: {q} taker {v!r} is not a bps rate')
        eff = None
        if d.get('effective_from') is not None:
            try: eff = _ms(d['effective_from'])
            except (TypeError, ValueError): raise Wave2Error(f'fee scenario {sid}: unreadable effective_from') from None
        out[sid] = {'definition': d, 'sha256': got, 'status': d['status'], 'use': FEE_USE[d['status']],
                    'taker_bps': dict(tk), 'effective_from_ms': eff}
    return out


def _cell_spec(rec, role):
    keys = {'strategy', 'side', 'tf'} | ({'tier', 'evidence'} if role == 'candidate' else set())
    extra = {'registry_side_override_required'} if role == 'candidate' else set()
    if not isinstance(rec, dict) or not keys <= set(rec) or set(rec) - keys - extra:
        raise Wave2Error(f'{role} record malformed: {rec!r}')
    key, side, tf = rec['strategy'], rec['side'], rec['tf']
    if key not in S.STRATEGIES:
        raise Wave2Error(f'{role} {key}: unsupported strategy key (not in the registry)')
    if side not in SIDES:
        raise Wave2Error(f'{role} {key}: unsupported side {side!r}')
    reg = S.STRATEGIES[key]['sides']
    override = rec.get('registry_side_override_required', False)
    if override not in (True, False):
        raise Wave2Error(f'{role} {key}: registry_side_override_required must be a boolean')
    if reg not in ('both', side):
        if not override:
            raise Wave2Error(f'{role} {key}.{side}: registry side is {reg!r}; an explicit side override is required')
    elif override:
        raise Wave2Error(f'{role} {key}.{side}: side override declared but the registry already allows {side}')
    spec = {'role': role, 'strategy': key, 'side': side, 'tf': tf, 'registry_sides': reg,
            'side_override': bool(override), 'name': f'{role}.{key}.{side}.{tf}'}
    if role == 'candidate':
        spec.update(tier=rec['tier'], evidence=rec['evidence'])
    if tf not in ROUTED_TFS:
        if role == 'candidate':
            raise Wave2Error(f'candidate {key}.{side}: tf {tf!r} has no routed twin data ({ROUTED_TFS})')
        spec['status'] = UNROUTED_CONTROL_STATUS
    return spec


def check_candidates(m, registry_sha):
    if m.get('format') != CANDIDATES_FORMAT:
        raise Wave2Error(f'candidate manifest format is not {CANDIDATES_FORMAT}')
    if 'RESEARCH_ONLY' not in str(m.get('authority')):
        raise Wave2Error('candidate manifest is not RESEARCH_ONLY')
    if m.get('bases') != list(Q.BASES):
        raise Wave2Error(f'candidate manifest bases {m.get("bases")} != {list(Q.BASES)}')
    if m.get('window') != window():
        raise Wave2Error(f'candidate manifest window {m.get("window")} != frozen {window()}')
    if Q.END > _ms(VC.SEALED_CUTOFF):
        raise Wave2Error('window reaches past the sealed holdout cutoff')
    reg = m.get('strategy_registry') or {}
    if reg.get('sha256') != registry_sha:
        raise Wave2Error(f'strategy registry sha {registry_sha} != manifest {reg.get("sha256")}')
    cands, ctrls = m.get('candidates'), m.get('controls_negative_gross_only')
    if not isinstance(cands, list) or not cands or not isinstance(ctrls, list):
        raise Wave2Error('candidate manifest lacks candidates / controls_negative_gross_only lists')
    cells = [_cell_spec(r, 'candidate') for r in cands] + [_cell_spec(r, 'control') for r in ctrls]
    seen = set()
    for c in cells:
        k = (c['strategy'], c['side'], c['tf'])
        if k in seen:
            raise Wave2Error(f'cell {k} is listed twice (candidate and control lists overlap)')
        seen.add(k)
    data = m.get('data') or {}
    d15, d4 = data.get('15m') or {}, data.get('4h') or {}
    if d15.get('status') != 'COMPLETE' or d4.get('status') != 'COMPLETE':
        raise Wave2Error('candidate manifest data routes are not COMPLETE')
    return cells, check_fee_scenarios(m.get('fee_scenarios')), data


# ------------------------------------------------------------------ 15m twins
def load_15m(path, combined, quote):
    """One quote's 15m twin pack -> ({sym: frame sliced}, {sym: file record}, per-quote manifest sha)."""
    root = os.path.dirname(os.path.abspath(path))
    tf_ms = TFS['15m']['tf_ms']
    expected = (Q.END - Q.WARM) // tf_ms
    qrec = (combined.get('quotes') or {}).get(quote) or {}
    if qrec.get('collector_format') != KLINES15_FORMAT or qrec.get('collector_incomplete') != []:
        raise Wave2Error(f'{quote} 15m: collector record is not complete {KLINES15_FORMAT}')
    mpath = _safe_rel(root, qrec.get('manifest'), f'{quote} 15m manifest')
    msha = sha_file(mpath, f'{quote} 15m manifest')
    if msha != qrec.get('manifest_sha256'):
        raise Wave2Error(f'{quote} 15m manifest sha {msha} != combined manifest record')
    m = _read_json(mpath, f'{quote} 15m manifest')
    w, fe = m.get('window') or {}, m.get('fetch') or {}
    if (m.get('format') != KLINES15_FORMAT or m.get('interval') != '15m' or m.get('incomplete') != []
            or m.get('stopped') is not None or fe.get('start') != Q.WARM_S or fe.get('end_exclusive') != Q.END_S
            or w.get('start') != Q.START_S or w.get('end') != Q.END_S or w.get('end_exclusive') is not True):
        raise Wave2Error(f'{quote} 15m manifest does not prove the frozen complete window')
    raw, files = {}, {}
    for base in Q.BASES:                                  # BTC first: quote-local context for backtest.Book
        sym = base + quote
        rec, crec = (m.get('files') or {}).get(sym) or {}, (qrec.get('files') or {}).get(sym) or {}
        if rec.get('status') != 'complete' or rec.get('rows') != expected or crec.get('rows') != expected:
            raise Wave2Error(f'{sym} 15m is INCOMPLETE (status / rows != {expected})')
        if rec.get('sha256') != crec.get('sha256'):
            raise Wave2Error(f'{sym} 15m: per-quote and combined manifest sha256 disagree')
        try:
            data, frec = Q._file(os.path.dirname(mpath), rec, f'{sym} 15m')
            d = Q._csv(data, VC.COLS, f'{sym} 15m')
        except Q.PairRunnerError as e:
            raise Wave2Error(str(e)) from None
        t = d.t.to_numpy(np.int64)
        if len(t) and int(t.max()) >= Q.END:
            raise Wave2Error(f'{sym} 15m holds a row at / after the sealed end; refused unused')
        sliced = VC.slice_frame(d, Q.WARM, Q.END)
        comp = BC.completeness(sliced, Q.WARM, Q.END, tf_ms)
        if (not comp['complete'] or len(d) != expected or not np.all(np.diff(t) > 0)
                or not np.array_equal(t, d.t.to_numpy(float))):
            raise Wave2Error(f'{sym} 15m is INCOMPLETE for the frozen window (missing / off-grid / unordered candles)')
        if int(t[0]) != Q.WARM or int(t[-1]) != Q.END - tf_ms:
            raise Wave2Error(f'{sym} 15m does not cover exactly [{Q.WARM_S}, {Q.END_S})')
        if not Q._ohlc_sane(*(d[k].to_numpy(float) for k in 'ohlc')):
            raise Wave2Error(f'{sym} 15m: next-open / OHLC defect')
        raw[sym] = sliced
        files[sym] = dict(frec, rows=int(len(d)))
    try:
        Q._distinct(files, quote, '15m kline')
    except Q.PairRunnerError as e:
        raise Wave2Error(str(e)) from None
    return raw, files, msha


def check_15m_combined(m):
    w = m.get('window') or {}
    tf_ms = TFS['15m']['tf_ms']
    expected = (Q.END - Q.WARM) // tf_ms
    if (m.get('format') != W15_FORMAT or m.get('contract') != W15_CONTRACT or m.get('status') != 'complete'
            or m.get('problems') != [] or w.get('interval') != '15m' or w.get('interval_ms') != tf_ms
            or w.get('fetch_start') != Q.WARM_S or w.get('development_start') != Q.START_S
            or w.get('end_exclusive') != Q.END_S or w.get('expected_rows_per_symbol') != expected):
        raise Wave2Error('15m twin manifest does not prove the frozen complete contract')
    pairs = m.get('pairs') or {}
    for b in Q.BASES:
        p = pairs.get(b) or {}
        if (p.get('exact_timestamp_match') is not True or p.get('timestamp_only_usdt') != 0
                or p.get('timestamp_only_usdc') != 0 or p.get('usdt_rows') != expected or p.get('usdc_rows') != expected):
            raise Wave2Error(f'15m twin {b}: timestamp mismatch / row-count hole in the combined manifest')


# ------------------------------------------------------------------ preflight
def preflight(*, candidates, wave2_15m, usdt_4h, usdc_4h, usdt_accounting, usdc_accounting, repo=ROOT):
    """Every pin and every input check, before any simulation. Returns (pins, cells, fees, data, accounting)."""
    pins = {'format': PREFLIGHT_FORMAT, 'contract': CONTRACT, 'window': window()}
    pins['git'] = git_state(repo)
    pins['code'] = {f: sha_file(os.path.join(repo, *f.split('/')), f) for f in CODE_FILES}
    reg_sha = sha_file(os.path.join(repo, REGISTRY_FILE), REGISTRY_FILE)
    pins['strategy_registry'] = _pin('strategy_registry', reg_sha)
    pins['candidates'] = _pin('candidates', sha_file(candidates, 'candidate manifest'))
    cm = _read_json(candidates, 'candidate manifest')
    cells, fees, data = check_candidates(cm, reg_sha)
    pins['fee_scenarios'] = {k: v['sha256'] for k, v in fees.items()}
    # routed manifests: CLI file sha == candidate-manifest sha == pinned constant
    d15, d4 = data['15m'], data['4h']
    w15 = _pin('wave2_15m', sha_file(wave2_15m, '15m twin manifest'))
    if w15 != d15.get('combined_manifest_sha256'):
        raise Wave2Error('15m twin manifest sha does not match the candidate manifest data.15m route')
    k4 = {}
    for q, p in (('USDT', usdt_4h), ('USDC', usdc_4h)):
        s = _pin(f'{q.lower()}_4h', sha_file(p, f'{q} 4h manifest'))
        if s != d4.get(f'{q}_manifest_sha256'):
            raise Wave2Error(f'{q} 4h manifest sha does not match the candidate manifest data.4h route')
        k4[q] = s
    acc_sha = {q: _pin(f'{q.lower()}_accounting', sha_file(p, f'{q} accounting manifest'))
               for q, p in (('USDT', usdt_accounting), ('USDC', usdc_accounting))}
    try:
        k, a = Q.load_all(usdt_4h, usdc_4h, usdt_accounting, usdc_accounting)    # 4h twins + funding / mark / rules
    except Q.PairRunnerError as e:
        raise Wave2Error(str(e)) from None
    combined = _read_json(wave2_15m, '15m twin manifest')
    check_15m_combined(combined)
    k15 = {q: load_15m(wave2_15m, combined, q) for q in Q.QUOTES}
    try:
        Q.validate_twins(k15['USDT'][0], k15['USDC'][0], k15['USDT'][1], k15['USDC'][1])
    except Q.PairRunnerError as e:
        raise Wave2Error(f'15m: {e}') from None
    pins['inputs'] = {
        '15m': {'manifest': w15, 'quotes': {q: {'manifest': k15[q][2], 'files': k15[q][1]} for q in Q.QUOTES}},
        '4h': {q: {'manifest': k4[q], 'files': k[q][2]} for q in Q.QUOTES},
        'accounting': {q: {'manifest': acc_sha[q], 'files': a[q][2]} for q in Q.QUOTES}}
    pins['cells'] = [c['name'] for c in cells]
    datas = {'15m': {q: k15[q][0] for q in Q.QUOTES}, '4h': {q: k[q][1] for q in Q.QUOTES}}
    return clean(pins), cells, fees, datas, {q: a[q][1] for q in Q.QUOTES}


# ------------------------------------------------------------------ one cell
def slip_label(tf):
    nb, tf_ms = TFS[tf]['slip_bars'], TFS[tf]['tf_ms']
    return {'id': f'{C.SLIP_ID}@{tf}', 'lookback_bars': nb, 'lookback_seconds': nb * tf_ms // 1000,
            'bar_tf': tf, 'c': Q.SLIP_C, 'causal': 'closed bars only'}


def quote_metrics(priced, tf):
    eps = priced['episodes']
    cs = Q.cost_summary(eps)
    if not eps:                                           # same key set for an empty cell: sums 0, ratios undefined
        cs = dict({k: 0.0 for k in ('net_r_sum', 'gross_r_sum', 'slip_r_sum', 'fee_r_sum', 'funding_r_sum',
                                     'start_equity_pct_sum', 'max_dd_r')},
                  **{k: None for k in ('net_r_mean', 'pf', 'win_rate', 'mfe_r_mean', 'mae_r_mean', 'giveback_r_mean',
                                       'hold_bars_mean')}, n=0)
    tf_h = TFS[tf]['tf_ms'] / Q.HOUR
    usd = lambda k: Q._r(sum(e[k] * e['risk'] for e in eps)) if eps else 0.0
    return dict(cs, closed_trades=len(eps), open_trades=len(priced['open_at_end']),
                gross_pnl=usd('gross_r'), fees=usd('fee_r'), adverse_slippage=usd('slip_r'), funding=usd('funding_r'),
                net_pnl=Q._r(sum(e['net'] for e in eps)) if eps else 0.0,
                turnover=Q._r(sum(e['turnover'] for e in eps)) if eps else 0.0,
                expectancy_r=cs.get('net_r_mean'),
                hold_hours_mean=Q._r(cs['hold_bars_mean'] * tf_h) if eps else None)


def _feas(cv):
    f = dict(getattr(cv, 'attrs', {}).get('feasibility') or {})
    return {k: f.get(k) for k in ('mode', 'attempts', 'executed', 'rule_blocked', 'skipped', 'add_skipped')}


def causal_window(p, eff_ms, tf_ms):
    """Codex 6248825944 #2: a schedule with effective_from prices ONLY episodes whose entry and every charged event
    (fills, funding, exit) are at / after it. Episodes entered before it are excluded - including one that straddles it -
    and positions still open that were entered before it. The same rule, on the same exact keys, runs for both twins."""
    keep, before, straddle = [], 0, 0
    for e in p['episodes']:
        if e['t_entry'] >= eff_ms: keep.append(e)
        elif e['t_entry'] + e['hold_bars'] * tf_ms >= eff_ms: straddle += 1
        else: before += 1
    still = [o for o in p['open_at_end'] if o['t_entry'] >= eff_ms]
    out = dict(p, episodes=keep, open_at_end=still, costs=Q.cost_summary(keep),
               spread_rows={str(b): {'label': Q.SPREAD_LABEL, 'half_spread_per_fill_bps': b / 2,
                                     'net_r_sum': Q._r(sum(e['spread_r'][str(b)] for e in keep))} for b in Q.SPREAD_BPS})
    return out, {'from_utc': _iso(eff_ms), 'to_utc_exclusive': Q.END_S, 'rule': 'entry and every charged event >= from',
                 'kept': len(keep), 'excluded_entered_before': before, 'excluded_straddling': straddle,
                 'excluded_open_entered_before': len(p['open_at_end']) - len(still)}


def _ep(e):
    return dict(e, episode_key=[e['strategy'], e['side'], e['base'], e['t_entry']])


def compute_cell(spec, datas, accounting, fees, preflight_sha):
    tf, key, side = spec['tf'], spec['strategy'], spec['side']
    tf_ms, tf_sec, nb = TFS[tf]['tf_ms'], TFS[tf]['tf_ms'] // 1000, TFS[tf]['slip_bars']
    sim = {q: Q.run_cell(datas[tf][q], q, key, side, tf_sec=tf_sec) for q in Q.QUOTES}
    scen = {}
    for sid, fs in fees.items():
        quotes, priced = {}, {}
        for q in Q.QUOTES:
            rate = fs['taker_bps'][q]
            if rate is None:
                quotes[q] = {'status': 'INCOMPLETE', 'reason': f'no {q} taker rate in {sid}'}
                continue
            p = Q.price_cell(sim[q], q, accounting[q], taker=rate / 1e4, tf_ms=tf_ms, slip_bars=nb)
            win = None
            if fs.get('effective_from_ms') is not None:          # never applied retroactively (Codex ruling #2)
                p, win = causal_window(p, fs['effective_from_ms'], tf_ms)
            priced[q] = p
            quotes[q] = {'status': 'complete', 'taker_bps': rate, 'fee_window': win, 'metrics': quote_metrics(p, tf),
                         'spread_rows': p['spread_rows'], 'episodes': [_ep(e) for e in p['episodes']],
                         'open_at_end': p['open_at_end']}
        wins = {q: quotes[q].get('fee_window') for q in priced}
        if len({json.dumps({k: v for k, v in (w or {}).items() if k not in ('kept', 'excluded_entered_before',
                'excluded_straddling', 'excluded_open_entered_before')}, sort_keys=True) for w in wins.values()}) > 1:
            raise Wave2Error(f'{spec["name"]} {sid}: twin fee windows differ')     # symmetric windowing or nothing
        row = {'status': fs['status'], 'use': fs['use'], 'definition_sha256': fs['sha256'], 'quotes': quotes,
               'slip': slip_label(tf), 'paired': None}
        if fs['status'] != 'INCOMPLETE' and set(priced) == set(Q.QUOTES):
            pr = Q.pair_episodes(priced['USDT']['episodes'], priced['USDC']['episodes'])
            row['paired'] = {
                'metrics': Q.paired_metrics(pr), 'sufficiency': Q.sufficiency(pr),
                'episodes': [{'episode_key': list(Q._key(x)), 'usdt_R': x['R'], 'usdc_R': y['R'],
                              'delta_R': y['R'] - x['R'], 'delta_fee_r': y['fee_r'] - x['fee_r'],
                              'delta_slip_r': y['slip_r'] - x['slip_r'], 'delta_funding_r': y['funding_r'] - x['funding_r'],
                              'delta_gross_r': y['gross_r'] - x['gross_r'], 'quarter': Q._quarter(x['t_entry'])}
                             for x, y in pr['paired']],
                'only_usdt': [list(Q._key(e)) for e in pr['only_usdt']],
                'only_usdc': [list(Q._key(e)) for e in pr['only_usdc']],
                'verdict_use': 'NONE (research only; USDC BETTER UNAVAILABLE)'}
        scen[sid] = row
    content = {k: v for k, v in spec.items()}
    content.update(
        format=CELL_FORMAT, preflight_sha256=preflight_sha, status='complete',
        config={'sleeve': {q: BC.sleeve(key, side, [b + q for b in Q.BASES], {}, Q.SIM['risk'], Q.SIM['max_pos'])
                           for q in Q.QUOTES}, 'sim': Q.SIM, 'window': window()},
        context_symbol={q: sim[q]['context_symbol'] for q in Q.QUOTES},
        signals={q: {b: len(v) for b, v in sim[q]['signals'].items()} for q in Q.QUOTES},
        agreement=Q.agreement(sim['USDT']['signals'], sim['USDC']['signals'], tf_ms=tf_ms),
        exchange_filter_refusals={q: _feas(sim[q]['curve']) for q in Q.QUOTES},
        spread={'bps': list(Q.SPREAD_BPS), 'label': Q.SPREAD_LABEL}, slip=slip_label(tf), scenarios=scen)
    return clean(content)


def envelope(content):
    return {'cell_sha256': sha_bytes(canon(content)), 'content': content}


def load_cell(path, name, preflight_sha):
    """A completed cell is trusted only when its envelope hash, name and preflight hash all verify."""
    try:
        with open(path, encoding='utf-8') as f:
            env = json.load(f)
        content = env['content']
        ok = (env.get('cell_sha256') == sha_bytes(canon(content)) and content.get('name') == name
              and content.get('preflight_sha256') == preflight_sha and content.get('status') == 'complete')
    except (OSError, ValueError, KeyError, TypeError, AttributeError):
        ok = False
    if not ok:
        raise Wave2Error(f'completed cell {name} fails its hash binding (tampered or foreign); refused, not trusted')
    return env


# ------------------------------------------------------------------ summary
def _strip(content):
    out = {k: v for k, v in content.items() if k != 'scenarios'}
    sc = {}
    for sid, row in content['scenarios'].items():
        r = {k: v for k, v in row.items() if k not in ('quotes', 'paired')}
        r['quotes'] = {q: {k: v for k, v in x.items() if k not in ('episodes', 'open_at_end')}
                       for q, x in row['quotes'].items()}
        p = row['paired']
        r['paired'] = None if p is None else {
            'metrics': p['metrics'], 'sufficiency': p['sufficiency'], 'verdict_use': p['verdict_use'],
            'n_paired': len(p['episodes']), 'unmatched': {'only_usdt': len(p['only_usdt']), 'only_usdc': len(p['only_usdc'])},
            'episode_keys': [e['episode_key'] for e in p['episodes']]}
        sc[sid] = r
    out['scenarios'] = sc
    return out


def build_summary(pins, preflight_sha, run_id, cells, fees, done, run_record):
    body = {'format': REPORT_FORMAT, 'contract': CONTRACT, 'authority': 'RESEARCH_ONLY; NO_EXECUTION_OR_PROMOTION_AUTHORITY',
            'run_id': run_id, 'preflight_sha256': preflight_sha, 'pins': pins, 'window': dict(window(), holdout_read=False),
            'fee_scenarios': {k: {'status': v['status'], 'use': v['use'], 'definition': v['definition'],
                                  'definition_sha256': v['sha256']} for k, v in fees.items()},
            'spread': {'bps': list(Q.SPREAD_BPS), 'label': Q.SPREAD_LABEL},
            'slip': {tf: slip_label(tf) for tf in TFS},
            'cells': {}, 'controls_not_run': [], 'controls_incomplete': False, 'unrouted_control_count': 0,
            'packet_status': 'COMPLETE', 'global_verdict_available': False, 'exchange_filter_basis': FILTER_BASIS,
            'resume_binding': 'cell hash + name + preflight hash: detects corruption / foreign cells, not a deliberate '
                              're-hashed edit (no secret); resumed cells are counted in run_record',
            'verdict': None, 'usdc_better': USDC_BETTER,
            'usdc_better_reason': 'CI recipe not frozen; provisional / incomplete fee rows are never verdict inputs',
            'non_promotable': True, 'routing_or_config_changes': None, 'variable_fields': list(VARIABLE_FIELDS),
            'run_record': run_record}
    for c in cells:
        if c.get('status') == UNROUTED_CONTROL_STATUS:
            body['controls_not_run'].append({k: c[k] for k in ('name', 'role', 'strategy', 'side', 'tf', 'status')})
            body['controls_incomplete'] = True                # an explicit, counted coverage hole (controls only)
            body['unrouted_control_count'] += 1
            body['packet_status'] = 'INCOMPLETE'              # Codex 6248825944 #1: cell rows usable, packet is not
            continue
        env = done[c['name']]
        body['cells'][c['name']] = dict(_strip(env['content']), cell_file=f'cells/{c["name"]}.json',
                                        cell_sha256=env['cell_sha256'])
    return body


# ------------------------------------------------------------------ run
def _now():
    return _dt.datetime.now(_dt.timezone.utc).strftime('%Y-%m-%dT%H:%M:%S.%fZ')


def write_incomplete(out_dir, stage, reason, run_id=None, pins=None, completed=()):
    rec = {'format': RUN_RECORD_FORMAT, 'contract': CONTRACT, 'status': 'INCOMPLETE', 'stage': stage,
           'reason': reason, 'run_id': run_id, 'pins': pins, 'completed_cells': sorted(completed),
           'verdict': None, 'usdc_better': USDC_BETTER, 'non_promotable': True, 'written_utc': _now()}
    os.makedirs(out_dir, exist_ok=True)
    stamp = rec['written_utc'].replace(':', '').replace('-', '').replace('.', '')
    n = 0
    while True:
        path = os.path.join(out_dir, f'INCOMPLETE.{stamp}{"." + str(n) if n else ""}.json')
        if not os.path.exists(path):
            break
        n += 1
    write_once(path, dumps(rec))
    return path


def run(*, candidates, wave2_15m, usdt_4h, usdc_4h, usdt_accounting, usdc_accounting, out_dir, repo=ROOT):
    """-> (exit code, path written). 0 + summary path on a complete run; 2 + INCOMPLETE record path otherwise."""
    out_dir = os.path.abspath(out_dir)
    stage, run_id, pins, completed = 'preflight', None, None, []
    try:
        rp = os.path.abspath(repo)
        try: inside = os.path.commonpath([rp, out_dir]) == rp
        except ValueError: inside = False                     # another drive: cannot be inside the repository
        if inside:
            raise Wave2Error('--out-dir is inside the repository (would dirty the pinned tree)')
        pins, cells, fees, datas, acc = preflight(candidates=candidates, wave2_15m=wave2_15m, usdt_4h=usdt_4h,
                                                  usdc_4h=usdc_4h, usdt_accounting=usdt_accounting,
                                                  usdc_accounting=usdc_accounting, repo=repo)
        preflight_sha = sha_bytes(canon(pins))
        run_id = preflight_sha[:16]
        rdir = os.path.join(out_dir, run_id)
        stage = 'output'
        summary_path = os.path.join(rdir, 'summary.json')
        if os.path.exists(summary_path):
            raise Wave2Error(f'{summary_path} exists; a completed run is never overwritten')
        pf_path, pf_text = os.path.join(rdir, 'preflight.json'), dumps(pins)
        if os.path.exists(pf_path):
            with open(pf_path, encoding='utf-8') as f:
                if f.read() != pf_text:
                    raise Wave2Error(f'{pf_path} does not match this run\'s pins; refused (start a new out-dir)')
        else:
            write_once(pf_path, pf_text)
        stage = 'simulation'
        done, resumed = {}, 0
        for c in cells:
            if c.get('status') == UNROUTED_CONTROL_STATUS:
                continue
            path = os.path.join(rdir, 'cells', f'{c["name"]}.json')
            if os.path.exists(path):
                done[c['name']] = load_cell(path, c['name'], preflight_sha)
                resumed += 1
            else:
                env = envelope(compute_cell(c, datas, acc, fees, preflight_sha))
                write_once(path, dumps(env))
                done[c['name']] = env
            completed.append(c['name'])
        stage = 'summary'
        rec = {'written_utc': _now(), 'out_dir': out_dir, 'resumed_cells': resumed,
               'input_paths': {'candidates': os.path.abspath(candidates), 'wave2_15m': os.path.abspath(wave2_15m),
                               'usdt_4h': os.path.abspath(usdt_4h), 'usdc_4h': os.path.abspath(usdc_4h),
                               'usdt_accounting': os.path.abspath(usdt_accounting),
                               'usdc_accounting': os.path.abspath(usdc_accounting)}}
        write_once(summary_path, dumps(build_summary(pins, preflight_sha, run_id, cells, fees, done, rec)))
        return 0, summary_path
    except Exception as e:                       # noqa: BLE001 - every failure is an INCOMPLETE record, never a verdict
        reason = f'{type(e).__name__}: {e}'
        return 2, write_incomplete(out_dir, stage, reason, run_id, pins, completed)


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.split('\n')[0])
    ap.add_argument('--candidates', required=True, help=f'{CANDIDATES_FORMAT} manifest')
    ap.add_argument('--wave2-15m', required=True, help=f'{W15_FORMAT} combined 15m twin manifest')
    ap.add_argument('--usdt-4h', required=True, help=f'USDT {Q.KLINES_FORMAT} manifest')
    ap.add_argument('--usdc-4h', required=True, help=f'USDC {Q.KLINES_FORMAT} manifest')
    ap.add_argument('--usdt-accounting', required=True, help=f'USDT {Q.ACCOUNTING_FORMAT} manifest')
    ap.add_argument('--usdc-accounting', required=True, help=f'USDC {Q.ACCOUNTING_FORMAT} manifest')
    ap.add_argument('--out-dir', required=True, help='output root (outside the repo)')
    x = ap.parse_args(argv)
    code, path = run(candidates=x.candidates, wave2_15m=x.wave2_15m, usdt_4h=x.usdt_4h, usdc_4h=x.usdc_4h,
                     usdt_accounting=x.usdt_accounting, usdc_accounting=x.usdc_accounting, out_dir=x.out_dir)
    print(('wave-2 batch complete; summary -> ' if code == 0 else 'INCOMPLETE; run record -> ') + path,
          file=sys.stdout if code == 0 else sys.stderr)
    return code


if __name__ == '__main__':
    raise SystemExit(main())
