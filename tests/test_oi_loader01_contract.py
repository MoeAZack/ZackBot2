"""OI-LOADER-01 admission contract twin (Codex GO 6489944476; rulings 6489828197 N1/N2, 6489428240 J1-J3).

The JSON twin is authoritative, DRAFT and unsigned, cites PR #225 head f850323 and must be rebound before signing.
This file holds a tiny REFERENCE EVALUATOR: a pure function over a dict manifest that applies the frozen predicate.
It imports no repo module (not the fetcher, not manifest.py) and does no I/O beyond reading the contract files.
It is a test oracle only, never a loader.
"""
import copy
import datetime as dt
import hashlib
import json
import math
import os
import re

import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
CONTRACT_DIR = os.path.join(ROOT, 'contracts', 'oi_loader_01')
JS = os.path.join(CONTRACT_DIR, 'oi_loader_01_contract.v1.json')
MD = os.path.join(CONTRACT_DIR, 'OI_LOADER_01_CONTRACT.md')

with open(JS, encoding='utf-8') as _fh:
    C = json.load(_fh)


@pytest.fixture(scope='module')
def md():
    with open(MD, encoding='utf-8') as fh:
        return fh.read()


# ------------------------------------------------------------------ reference evaluator (pure; constants from the JSON)
S = C['manifest_schema']
PIN = S['pinned_values']
IDR = C['identity_rules']
ISO = re.compile(C['date_semantics']['iso_date_regex'])
SYM = re.compile(IDR['symbol_regex'])
SHA = re.compile(IDR['sha256_regex'])
MONTH = re.compile(S['value_rules']['month_regex'])
MAX_BYTES = S['max_bytes_per_file']
CUTOFF = dt.date.fromisoformat(C['date_semantics']['cutoff_exclusive'])
ONE = dt.timedelta(days=1)


class Refuse(Exception):
    pass


def _iso(v):
    if type(v) is not str or not ISO.fullmatch(v):
        return None
    try:
        return dt.date.fromisoformat(v)
    except ValueError:
        return None


def _nonfinite(v):
    if type(v) is float:
        return not math.isfinite(v)
    if isinstance(v, dict):
        return any(_nonfinite(x) for x in v.values())
    if isinstance(v, list):
        return any(_nonfinite(x) for x in v)
    return False


def _iso_or_none(v):
    return v is None or _iso(v) is not None


def _nonneg_int(v):
    return type(v) is int and v >= 0


def _coverage_values_ok(cov):
    if cov['group'] not in S['groups']:
        return False
    if not (_iso_or_none(cov['listed_first']) and _iso_or_none(cov['listed_last'])):
        return False
    for k in ('missing_days', 'missing_required_days'):
        if type(cov[k]) is not list or not all(_iso(d) is not None for d in cov[k]):
            return False
    if _iso(cov['required_start']) is None or _iso(cov['required_end_exclusive']) is None:
        return False
    for g in ('leading_gap', 'trailing_gap'):
        gap = cov[g]
        if gap is not None and not (_iso_or_none(gap['start']) and _iso(gap['end_exclusive']) is not None
                                    and (gap['days'] is None or _nonneg_int(gap['days']))):
            return False
    for r in ('longest_complete_month_run', 'month_run_if_holes_excused'):
        run = cov[r]
        if not _nonneg_int(run['months']) or not all(
                x is None or (type(x) is str and MONTH.fullmatch(x)) for x in (run['start'], run['end'])):
            return False
    return True


def _fields(obj, fields):
    if not isinstance(obj, dict):
        raise Refuse('SCHEMA_NOT_OBJECT')
    if set(obj) - set(fields):
        raise Refuse('SCHEMA_UNKNOWN_FIELD')
    if set(fields) - set(obj):
        raise Refuse('SCHEMA_MISSING_FIELD')


def canonical(obj):
    return json.dumps(obj, sort_keys=True, separators=(',', ':'), ensure_ascii=True, allow_nan=False).encode('ascii')


def digest_of(m):
    return hashlib.sha256(canonical({k: v for k, v in m.items() if k != 'digest'})).hexdigest()


def covers(cov, rs, re_x):
    """Pure copy of oi_metrics_fetch.covers @ f850323 (cutoff/order already refused by the preconditions)."""
    try:
        lf, ll, missing = cov['listed_first'], cov['listed_last'], cov['missing_days']
    except (KeyError, TypeError):
        return False
    if lf is None or ll is None or missing is None:
        return False
    if dt.date.fromisoformat(lf) > rs or dt.date.fromisoformat(ll) < re_x - ONE:
        return False
    return not any(rs <= dt.date.fromisoformat(m) < re_x for m in missing)


def _preconditions(m, symbol, req_start, req_end_x):
    rs, rx = _iso(req_start), _iso(req_end_x)
    if rs is None or rx is None:
        raise Refuse('REQ_DATE')
    if rx <= rs:
        raise Refuse('REQ_ORDER')
    if rx > CUTOFF:
        raise Refuse('REQ_CUTOFF')
    if not isinstance(m, dict):
        raise Refuse('SCHEMA_NOT_OBJECT')
    if _nonfinite(m):
        raise Refuse('SCHEMA_NONFINITE')
    _fields(m, S['top_level_fields'])
    for k, v in PIN.items():
        if type(m[k]) is not type(v) or m[k] != v:
            raise Refuse('SCHEMA_VERSION')
    if (type(m['authoritative']) is not bool or type(m['complete']) is not bool or type(m['files']) is not list
            or type(m['failures']) is not list or type(m['symbols']) is not list
            or not (m['stopped'] is None or isinstance(m['stopped'], dict)) or type(m['total_bytes']) is not int):
        raise Refuse('SCHEMA_TYPE')
    if m['stopped'] is not None:
        _fields(m['stopped'], S['stopped_fields'])
    for f in m['files']:
        _fields(f, S['file_entry_fields'])
        if (any(type(f[k]) is not str for k in ('path', 'symbol', 'date', 'sha256', 'source'))
                or type(f['bytes']) is not int):
            raise Refuse('SCHEMA_TYPE')
    ms, me = _iso(m['start']), _iso(m['end'])
    if ms is None or me is None or me >= CUTOFF or ms > me:
        raise Refuse('SCHEMA_DATE')
    syms = m['symbols']
    if (not all(type(s) is str and SYM.fullmatch(s) for s in syms) or syms != sorted(set(syms))
            or not isinstance(m['coverage'], dict) or set(m['coverage']) != set(syms)):
        raise Refuse('SCHEMA_SYMBOLS')
    _fields(m['groups'], S['groups'])
    for cov in m['coverage'].values():
        _fields(cov, S['coverage_entry_fields'])
        for g in ('leading_gap', 'trailing_gap'):
            if cov[g] is not None:
                _fields(cov[g], S['gap_fields'])
        for r in ('longest_complete_month_run', 'month_run_if_holes_excused'):
            _fields(cov[r], S['month_run_fields'])
        if not _coverage_values_ok(cov):
            raise Refuse('SCHEMA_TYPE')
    want = {g: sorted(s for s in syms if m['coverage'][s]['group'] == g) for g in S['groups']}
    if m['groups'] != want or any(type(v) is not list for v in m['groups'].values()):
        raise Refuse('SCHEMA_GROUPS')
    if symbol not in syms:
        raise Refuse('REQ_SYMBOL')
    if rs < ms or rx > me + ONE:
        raise Refuse('REQ_WINDOW')
    if any(type(f['bytes']) is not int for f in m['files']) or m['total_bytes'] != sum(f['bytes'] for f in m['files']):
        raise Refuse('SCHEMA_TOTAL_BYTES')
    if type(m['digest']) is not str or m['digest'] != digest_of(m):
        raise Refuse('SCHEMA_DIGEST')
    return rs, rx, ms, me


def _p6(m, symbol, rs, rx, ms, me):
    bad, paths = [], set()
    for f in m['files']:
        d = _iso(f['date'])
        if type(f['symbol']) is not str or not SYM.fullmatch(f['symbol']) or f['symbol'] not in m['symbols']:
            bad.append('entry_symbol')
        elif d is None:
            bad.append('entry_date')
        elif not (ms <= d <= me and d < CUTOFF):
            bad.append('outside_window')
        elif f['path'] != IDR['path_template'].format(symbol=f['symbol'], date=f['date']):
            bad.append('entry_path')
        elif f['source'] != IDR['source_template'].format(path=f['path']):
            bad.append('entry_source')
        elif type(f['sha256']) is not str or not SHA.fullmatch(f['sha256']):
            bad.append('entry_sha256')
        elif type(f['bytes']) is not int or not 0 < f['bytes'] <= MAX_BYTES:
            bad.append('entry_bytes')
        if f['path'] in paths:
            bad.append('duplicate_path')
        paths.add(f['path'])
    d = rs
    while d < rx:
        n = sum(1 for f in m['files'] if f['symbol'] == symbol and f['date'] == d.isoformat())
        if n == 0:
            bad.append('missing_day')
        elif n > 1:
            bad.append('duplicate_day')
        d += ONE
    return sorted(set(bad))


def evaluate(m, symbol, req_start, req_end_x):
    """(admitted, reasons). Reasons: one precondition code, or every failing conjunct (P6 as 'P6:<detail>')."""
    try:
        return _evaluate(m, symbol, req_start, req_end_x)
    except Refuse as r:
        return False, [str(r)]
    except Exception:                                           # never raise: any residual error refuses
        return False, ['SCHEMA_INVALID']


def _evaluate(m, symbol, req_start, req_end_x):
    rs, rx, ms, me = _preconditions(m, symbol, req_start, req_end_x)
    reasons = []
    if m['authoritative'] is not True:
        reasons.append('P1')
    if m['complete'] is not True:
        reasons.append('P2')
    if m['stopped'] is not None:
        reasons.append('P3')
    if m['failures'] != []:
        reasons.append('P4')
    if not covers(m['coverage'][symbol], rs, rx):
        reasons.append('P5')
    reasons += ['P6:' + b for b in _p6(m, symbol, rs, rx, ms, me)]
    return not reasons, reasons


def heads(reasons):
    return sorted({r.split(':')[0] for r in reasons})


# ------------------------------------------------------------------ manifest builder (shape of build_manifest @ f850323)
def entry(sym, day, size=1234):
    path = f'daily/metrics/{sym}/{sym}-metrics-{day}.zip'
    return {'path': path, 'symbol': sym, 'date': day, 'bytes': size,
            'sha256': hashlib.sha256(path.encode()).hexdigest(),
            'source': 'https://data.binance.vision/data/futures/um/' + path}


def days(a, b_incl):
    a, b = dt.date.fromisoformat(a), dt.date.fromisoformat(b_incl)
    return [(a + dt.timedelta(days=i)).isoformat() for i in range((b - a).days + 1)]


def seal(m):
    m['total_bytes'] = sum(f['bytes'] for f in m['files']) if all(type(f.get('bytes')) is int for f in m['files']) \
        else m['total_bytes']
    m['digest'] = digest_of(m)
    return m


def manifest(start='2026-09-01', end='2026-09-03', symbols=('BTCUSDT',)):
    run0 = {'months': 0, 'start': None, 'end': None}
    files = sorted((entry(s, d) for s in symbols for d in days(start, end)), key=lambda e: e['path'])
    cov = {s: {'group': 'strict_complete', 'missing_days': [], 'missing_required_days': [],
               'listed_first': '2023-01-01', 'listed_last': end, 'required_start': start,
               'required_end_exclusive': (dt.date.fromisoformat(end) + ONE).isoformat(),
               'leading_gap': None, 'trailing_gap': None,
               'longest_complete_month_run': dict(run0), 'month_run_if_holes_excused': dict(run0)}
           for s in sorted(symbols)}
    m = dict(PIN, start=start, end=end, symbols=sorted(symbols), files=files, failures=[], stopped=None,
             complete=True, total_bytes=0, authoritative=True, coverage=cov,
             groups={'strict_complete': sorted(symbols), 'conditional_with_holes': [], 'short_history': []})
    return seal(m)


def edit(m, fn):
    m = copy.deepcopy(m)
    fn(m)
    return seal(m)


REQ = ('BTCUSDT', '2026-09-01', '2026-09-04')
BASE = manifest()
FAIL = {'path': 'daily/metrics/BTCUSDT/BTCUSDT-metrics-2026-09-02.zip', 'symbol': 'BTCUSDT', 'date': '2026-09-02',
        'source': 'https://data.binance.vision/data/futures/um/daily/metrics/BTCUSDT/BTCUSDT-metrics-2026-09-02.zip',
        'reason': 'retries_exhausted', 'detail': 'x'}


# ------------------------------------------------------------------ contract JSON shape
def test_json_is_draft_unsigned_and_bound_to_225(md):
    assert C['contract'] == 'OI-LOADER-01' and C['version'] == 1
    assert C['status'] == 'DRAFT' and C['signed_by'] is None and C['frozen_at'] is None
    assert C['assignment'] == '6489944476' and C['ruling_source'].startswith('Codex 6489944476')
    assert C['integration_rulings'] == ['6489828197', '6489428240']
    ci = C['code_identity']
    assert ci['provenance_pr'] == 225 and ci['provenance_head'] == 'f850323'
    assert ci['provenance_head_full'].startswith('f850323') and ci['on_master'] is False
    assert 'DRAFT' in ci['rebind_required_before_signing']
    assert 'tools/research/oi_metrics_fetch.py' in ci['files']
    assert C['doc'] == os.path.basename(MD)
    assert '**Status: DRAFT — unsigned.**' in md and 'the JSON wins any conflict' in md and 'f850323' in md


def test_predicate_is_exactly_the_six_conjuncts_in_order():
    p = C['admission_predicate']
    assert p['combinator'] == 'ALL'
    assert [(c['id'], c['expr']) for c in p['conjuncts']] == [
        ('P1', 'authoritative is true'),
        ('P2', 'complete is true'),
        ('P3', 'stopped is null'),
        ('P4', 'failures == []'),
        ('P5', 'covers(manifest_coverage, required_start, required_end_exclusive)'),
        ('P6', 'every required day in [required_start, required_end_exclusive) has exactly one verified manifest '
               'file entry whose path/date/symbol/hash/size identity is valid and belongs to the requested symbol '
               'and window'),
    ]
    assert all('f850323' in c['citation'] for c in p['conjuncts'])
    assert {'authoritative', 'covers()'} <= set(p['never_sufficient_alone'])


def test_forbidden_list_has_the_key_items():
    assert {'loader implementation in this PR', 'archive or network access', 'bulk run', 'feature computation',
            'ML use', 'runtime import', 'PAPER authority'} <= set(C['forbidden'])


def test_pinned_values_equal_the_225_constants():
    # literal values of oi_metrics_fetch.py / oi_metrics_probe.py @ f850323 (fetcher is not on master; no import)
    assert PIN['format'] == 'zb-oi-metrics-fetch/1' and PIN['loader_version'] == 'zb-oi-metrics-raw-zip/1'
    assert PIN['source_class'] == 'archive-verified' and PIN['data_root'] == 'binance_um_metrics'
    assert PIN['series'] == 'metrics' and PIN['mode'] == 'bulk' and PIN['gate_months'] == 36
    assert PIN['cutoff_exclusive'] == C['date_semantics']['cutoff_exclusive'] == '2026-09-28'
    assert C['date_semantics']['last_admissible_day'] == '2026-09-27'
    assert len(S['top_level_fields']) == 21 and S['file_entry_fields'] == sorted(
        ['path', 'symbol', 'date', 'bytes', 'sha256', 'source'])
    assert len(S['coverage_entry_fields']) == 11 and 'missing_required_days' in S['coverage_entry_fields']
    assert all('f850323' in C['date_semantics'][k] for k in ('cutoff_citation', 'end_exclusive_citation'))


def test_md_names_every_code_and_conjunct(md):
    for p in C['preconditions_fail_closed']:
        assert p['code'] in md, p['code']
    for c in C['admission_predicate']['conjuncts']:
        assert c['id'] in md
    for q in C['open_questions']:
        assert q['id'] in md


def test_evaluator_imports_no_repo_module():
    src = open(__file__, encoding='utf-8').read()
    assert not re.search(r'^\s*(import|from)\s+(oi_metrics|manifest|tools|binance_archive_fill)', src, re.M)
    assert ('sys.' + 'path') not in src and ('import' + 'lib') not in src


# ------------------------------------------------------------------ positives
def test_valid_multi_day_manifest_admitted():
    assert evaluate(BASE, *REQ) == (True, [])


def test_valid_single_day_manifest_admitted():
    m = manifest('2026-09-01', '2026-09-01')
    assert evaluate(m, 'BTCUSDT', '2026-09-01', '2026-09-02') == (True, [])


def test_sub_window_and_multi_symbol_admitted():
    m = manifest(symbols=('BTCUSDT', 'ETHUSDT'))
    assert evaluate(m, 'ETHUSDT', '2026-09-02', '2026-09-03') == (True, [])


def test_end_exclusive_equal_to_cutoff_admitted():
    m = manifest('2026-09-25', '2026-09-27')
    assert evaluate(m, 'BTCUSDT', '2026-09-25', '2026-09-28') == (True, [])


def test_end_exclusive_cutoff_plus_one_refused():
    m = manifest('2026-09-25', '2026-09-27')
    assert evaluate(m, 'BTCUSDT', '2026-09-25', '2026-09-29') == (False, ['REQ_CUTOFF'])


# ------------------------------------------------------------------ each conjunct failing alone
def _drop_day(m):
    m['files'] = [f for f in m['files'] if f['date'] != '2026-09-02']


ALONE = {
    'P1': lambda m: m.update(authoritative=False),
    'P2': lambda m: m.update(complete=False),
    'P3': lambda m: m.update(stopped={'reason': 'http_429', 'url': 'u', 'status': 429}),
    'P4': lambda m: m.update(failures=[dict(FAIL)]),
    'P5': lambda m: m['coverage']['BTCUSDT'].update(missing_days=['2026-09-02']),
    'P6': _drop_day,
}


@pytest.mark.parametrize('cid', list(ALONE))
def test_each_conjunct_failing_alone_refuses(cid):
    ok, reasons = evaluate(edit(BASE, ALONE[cid]), *REQ)
    assert ok is False and heads(reasons) == [cid], reasons


def test_p5_alone_via_listed_boundary():
    ok, reasons = evaluate(edit(BASE, lambda m: m['coverage']['BTCUSDT'].update(listed_first='2026-09-02')), *REQ)
    assert (ok, reasons) == (False, ['P5'])


# ------------------------------------------------------------------ P6 identity matrix
def _set(i, **kw):
    return lambda m: m['files'][i].update(**kw)


P6_CASES = {
    'missing_required_day': (_drop_day, ['P6:missing_day']),
    'duplicate_required_day': (lambda m: m['files'].append(dict(m['files'][0])), ['P6:duplicate_day', 'P6:duplicate_path']),
    'entry_outside_window': (lambda m: m['files'].append(entry('BTCUSDT', '2026-09-10')), ['P6:outside_window']),
    'entry_on_cutoff': (lambda m: m['files'].append(entry('BTCUSDT', '2026-09-28')), ['P6:outside_window']),
    'wrong_symbol_not_in_manifest': (_set(0, symbol='ETHUSDT'), ['P6:entry_symbol', 'P6:missing_day']),
    'wrong_symbol_lowercase': (_set(0, symbol='btcusdt'), ['P6:entry_symbol', 'P6:missing_day']),
    'wrong_date_vs_path': (_set(0, date='2026-09-02'), ['P6:duplicate_day', 'P6:entry_path', 'P6:missing_day']),
    'malformed_date': (_set(0, date='2026/09/01'), ['P6:entry_date', 'P6:missing_day']),
    'wrong_path_symbol': (_set(0, path='daily/metrics/ETHUSDT/ETHUSDT-metrics-2026-09-01.zip'), ['P6:entry_path']),
    'wrong_path_prefix': (_set(0, path='monthly/metrics/BTCUSDT/BTCUSDT-metrics-2026-09-01.zip'), ['P6:entry_path']),
    'traversal_path': (_set(0, path='daily/metrics/../BTCUSDT-metrics-2026-09-01.zip'), ['P6:entry_path']),
    'wrong_source': (_set(0, source='https://evil.example/x.zip'), ['P6:entry_source']),
    'hash_short': (_set(0, sha256='a' * 63), ['P6:entry_sha256']),
    'hash_long': (_set(0, sha256='a' * 65), ['P6:entry_sha256']),
    'hash_uppercase': (_set(0, sha256='A' * 64), ['P6:entry_sha256']),
    'hash_non_hex': (_set(0, sha256='g' * 64), ['P6:entry_sha256']),
    'size_zero': (_set(0, bytes=0), ['P6:entry_bytes']),
    'size_negative': (_set(0, bytes=-5), ['P6:entry_bytes']),
    'size_above_cap': (_set(0, bytes=MAX_BYTES + 1), ['P6:entry_bytes']),
    'size_10_pow_400': (_set(0, bytes=10 ** 400), ['P6:entry_bytes']),
    'coverage_claims_complete_but_entry_absent': (
        lambda m: (_drop_day(m), m['coverage']['BTCUSDT'].update(group='strict_complete', missing_days=[])),
        ['P6:missing_day']),
}


@pytest.mark.parametrize('name', list(P6_CASES))
def test_p6_identity_matrix(name):
    fn, want = P6_CASES[name]
    assert evaluate(edit(BASE, fn), *REQ) == (False, want)


@pytest.mark.parametrize('field', ['sha256', 'bytes'])
def test_hash_or_size_absent_refused(field):
    m = edit(BASE, lambda m: m['files'][0].pop(field))
    assert evaluate(m, *REQ) == (False, ['SCHEMA_MISSING_FIELD'])


@pytest.mark.parametrize('bad', [1.0, '1234', True, None])
def test_size_non_int_refused(bad):
    m = copy.deepcopy(BASE)
    m['files'][0]['bytes'] = bad
    m['digest'] = digest_of(m)
    assert evaluate(m, *REQ) == (False, ['SCHEMA_TYPE'])


def test_size_cap_is_one_gib_and_cap_itself_admitted():
    assert MAX_BYTES == 1 << 30
    assert evaluate(edit(BASE, _set(0, bytes=MAX_BYTES)), *REQ) == (True, [])


# ------------------------------------------------------------------ H1 (Cowork 6490219007): mistyped values refuse, never raise
def _raw(fn):
    m = copy.deepcopy(BASE)
    fn(m)
    return m                                               # not resealed: digest_of would refuse NaN itself


@pytest.mark.parametrize('name,fn', [
    ('nan_top_level', lambda m: m.update(total_bytes=float('nan'))),
    ('inf_in_entry', lambda m: m['files'][0].update(bytes=float('inf'))),
    ('neg_inf_in_failure', lambda m: m.update(failures=[{'x': float('-inf')}])),
    ('nan_in_coverage', lambda m: m['coverage']['BTCUSDT']['longest_complete_month_run'].update(months=float('nan'))),
    ('nan_in_stopped', lambda m: m.update(stopped={'reason': 'r', 'url': 'u', 'status': float('nan')})),
])
def test_non_finite_refused_before_digest(name, fn):
    assert evaluate(_raw(fn), *REQ) == (False, ['SCHEMA_NONFINITE'])


ENTRY_TYPE_CASES = {
    'path_list': _set(0, path=['daily', 'metrics']), 'path_dict': _set(0, path={'p': 1}),
    'symbol_int': _set(0, symbol=7), 'date_int': _set(0, date=20260901), 'date_list': _set(0, date=['2026-09-01']),
    'sha256_null': _set(0, sha256=None), 'sha256_int': _set(0, sha256=12), 'source_null': _set(0, source=None),
}


@pytest.mark.parametrize('name', list(ENTRY_TYPE_CASES))
def test_file_entry_value_types_refused(name):
    assert evaluate(edit(BASE, ENTRY_TYPE_CASES[name]), *REQ) == (False, ['SCHEMA_TYPE'])


def _cov(**kw):
    return lambda m: m['coverage']['BTCUSDT'].update(**kw)


COVERAGE_TYPE_CASES = {
    'listed_first_int': _cov(listed_first=20230101), 'listed_first_loose_iso': _cov(listed_first='2026-9-1'),
    'listed_first_datetime': _cov(listed_first='2023-01-01T00:00Z'), 'listed_last_list': _cov(listed_last=['x']),
    'listed_last_loose_iso': _cov(listed_last='2026-09-3'), 'missing_days_str': _cov(missing_days='2026-09-02'),
    'missing_days_null': _cov(missing_days=None), 'missing_days_int_item': _cov(missing_days=[20260902]),
    'missing_days_loose_iso': _cov(missing_days=['2026-9-2']), 'missing_required_days_dict': _cov(missing_required_days={}),
    'group_unknown': _cov(group='complete'), 'required_start_null': _cov(required_start=None),
    'required_end_exclusive_bad': _cov(required_end_exclusive='2026-09-04Z'),
    'gap_days_negative': _cov(leading_gap={'start': '2026-09-01', 'end_exclusive': '2026-09-02', 'days': -1}),
    'gap_end_null': _cov(trailing_gap={'start': None, 'end_exclusive': None, 'days': None}),
    'month_run_bad_month': _cov(longest_complete_month_run={'months': 1, 'start': '2026-9', 'end': None}),
    'month_run_bool_months': _cov(month_run_if_holes_excused={'months': True, 'start': None, 'end': None}),
}


@pytest.mark.parametrize('name', list(COVERAGE_TYPE_CASES))
def test_coverage_value_types_refused(name):
    assert evaluate(edit(BASE, COVERAGE_TYPE_CASES[name]), *REQ) == (False, ['SCHEMA_TYPE'])


def test_null_listed_bounds_are_well_typed_but_fail_p5():
    m = edit(BASE, _cov(listed_first=None, listed_last=None))
    assert evaluate(m, *REQ) == (False, ['P5'])


def test_residual_exception_maps_to_schema_invalid(monkeypatch):
    def boom(*a):
        raise RuntimeError('unexpected')
    monkeypatch.setitem(globals(), 'covers', boom)
    assert evaluate(BASE, *REQ) == (False, ['SCHEMA_INVALID'])


@pytest.mark.parametrize('m', [None, [], 'x', 7])
def test_non_object_manifest_refused_not_raised(m):
    assert evaluate(m, *REQ) == (False, ['SCHEMA_NOT_OBJECT'])


# ------------------------------------------------------------------ H2 (Cowork 6490219007): groups is binding
GROUPS_CASES = {
    'groups_value_str': lambda m: m['groups'].update(strict_complete='BTCUSDT'),
    'groups_value_tuple_like_dict': lambda m: m['groups'].update(short_history={'BTCUSDT': 1}),
    'groups_inconsistent': lambda m: m['groups'].update(strict_complete=[], short_history=['BTCUSDT']),
    'groups_unsorted': lambda m: m['groups'].update(strict_complete=['ETHUSDT', 'BTCUSDT']),
    'groups_vs_coverage_group': _cov(group='conditional_with_holes'),
}


@pytest.mark.parametrize('name', list(GROUPS_CASES))
def test_groups_type_and_consistency_refused(name):
    m = edit(manifest(symbols=('BTCUSDT', 'ETHUSDT')) if name == 'groups_unsorted' else BASE, GROUPS_CASES[name])
    assert evaluate(m, *REQ) == (False, ['SCHEMA_GROUPS'])


def test_groups_not_a_dict_refused():
    assert evaluate(edit(BASE, lambda m: m.update(groups=['strict_complete'])), *REQ) == (False, ['SCHEMA_NOT_OBJECT'])


def test_h1_h2_recorded_in_contract():
    assert C['manifest_schema']['max_bytes_per_file'] == 1073741824
    assert 'binding' in C['manifest_schema']['groups_rule'] and 'f850323' in C['manifest_schema']['groups_rule']
    codes = [p['code'] for p in C['preconditions_fail_closed']]
    assert {'SCHEMA_NONFINITE', 'SCHEMA_GROUPS', 'SCHEMA_INVALID'} <= set(codes)
    assert codes.index('SCHEMA_NONFINITE') < codes.index('SCHEMA_DIGEST')
    assert {'Q7', 'Q8'} <= {q['id'] for q in C['open_questions']}
    assert set(C['review_6490219007']) == {'source', 'H1', 'H2'}


# ------------------------------------------------------------------ Cowork N1/N2 partial-run shape
def test_n1_n2_partial_run_refused_even_though_covers_is_true():
    def partial(m):
        _drop_day(m)
        m.update(complete=False, failures=[dict(FAIL)], stopped=None, authoritative=True)
    m = edit(BASE, partial)
    rs, rx = dt.date(2026, 9, 1), dt.date(2026, 9, 4)
    assert covers(m['coverage']['BTCUSDT'], rs, rx) is True             # the listing reaches every day
    assert evaluate(m, *REQ) == (False, ['P2', 'P4', 'P6:missing_day'])


# ------------------------------------------------------------------ fail-closed preconditions
def test_unknown_fields_refused():
    for fn in (lambda m: m.update(extra=1),
               lambda m: m['files'][0].update(status='fetched'),
               lambda m: m['coverage']['BTCUSDT'].update(x=1),
               lambda m: m['coverage']['BTCUSDT']['longest_complete_month_run'].update(x=1),
               lambda m: m['groups'].update(other=[])):
        assert evaluate(edit(BASE, fn), *REQ) == (False, ['SCHEMA_UNKNOWN_FIELD'])


def test_missing_fields_refused():
    for fn in (lambda m: m.pop('coverage'), lambda m: m.pop('authoritative'),
               lambda m: m['files'][0].pop('path'), lambda m: m['coverage']['BTCUSDT'].pop('missing_days')):
        assert evaluate(edit(BASE, fn), *REQ) == (False, ['SCHEMA_MISSING_FIELD'])


@pytest.mark.parametrize('field,value', [('format', 'zb-oi-metrics-fetch/2'), ('loader_version', 'zb-oi-metrics-raw-zip/2'),
                                         ('mode', 'screening'), ('cutoff_exclusive', '2026-10-28'),
                                         ('gate_months', 24), ('source_class', 'listing'), ('series', 'klines')])
def test_unknown_version_refused(field, value):
    assert evaluate(edit(BASE, lambda m: m.update({field: value})), *REQ) == (False, ['SCHEMA_VERSION'])


@pytest.mark.parametrize('start,end_x,code', [
    ('20260901', '2026-09-04', 'REQ_DATE'), ('2026-09-01T00:00Z', '2026-09-04', 'REQ_DATE'),
    ('2026-09-01', '2026-09-04+02:00', 'REQ_DATE'), (dt.date(2026, 9, 1), '2026-09-04', 'REQ_DATE'),
    ('2026-02-30', '2026-09-04', 'REQ_DATE'), (None, '2026-09-04', 'REQ_DATE'),
    ('2026-09-04', '2026-09-04', 'REQ_ORDER'), ('2026-09-04', '2026-09-01', 'REQ_ORDER'),
    ('2026-08-31', '2026-09-04', 'REQ_WINDOW'), ('2026-09-01', '2026-09-05', 'REQ_WINDOW'),
])
def test_ambiguous_date_boundaries_refused(start, end_x, code):
    assert evaluate(BASE, 'BTCUSDT', start, end_x) == (False, [code])


def test_manifest_date_problems_refused():
    for fn in (lambda m: m.update(start=None), lambda m: m.update(end='2026-09-28'),
               lambda m: m.update(start='2026-09-04'), lambda m: m.update(end='2026-09-03T00:00')):
        assert evaluate(edit(BASE, fn), *REQ) == (False, ['SCHEMA_DATE'])


def test_type_symbol_total_digest_refused():
    assert evaluate(edit(BASE, lambda m: m.update(authoritative=1)), *REQ) == (False, ['SCHEMA_TYPE'])
    assert evaluate(edit(BASE, lambda m: m.update(stopped='429')), *REQ) == (False, ['SCHEMA_TYPE'])
    assert evaluate(BASE, 'ETHUSDT', '2026-09-01', '2026-09-04') == (False, ['REQ_SYMBOL'])
    assert evaluate(edit(BASE, lambda m: m['coverage'].clear()), *REQ) == (False, ['SCHEMA_SYMBOLS'])
    m = copy.deepcopy(BASE)
    m['total_bytes'] += 1
    m['digest'] = digest_of(m)
    assert evaluate(m, *REQ) == (False, ['SCHEMA_TOTAL_BYTES'])
    m = copy.deepcopy(BASE)
    m['complete'] = False                                   # edited after sealing: digest no longer matches
    assert evaluate(m, *REQ) == (False, ['SCHEMA_DIGEST'])
