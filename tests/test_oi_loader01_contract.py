"""OI-LOADER-01 admission contract twin (Codex GO 6489944476; rulings 6489828197 N1/N2, 6489428240 J1-J3).

The JSON twin is authoritative, DRAFT and unsigned, is rebound to merged master 93642d8 (6493041460, then 6494813780 after #237)
with content-addressed sha256 pins of the cited files.
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
import subprocess

import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
CONTRACT_DIR = os.path.join(ROOT, 'contracts', 'oi_loader_01')
JS = os.path.join(CONTRACT_DIR, 'oi_loader_01_contract.v1.json')
MD = os.path.join(CONTRACT_DIR, 'OI_LOADER_01_CONTRACT.md')
MASTER = '93642d833ef0c45a3d4c143c4938f4a810421308'           # #237 merge (ruling 6494813780; #225 merge f8f03df was 6493041460)

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
MIN_BYTES = S['min_bytes_per_file']
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
    """Pure copy of oi_metrics_fetch.covers @ 93642d8 (cutoff/order already refused by the preconditions)."""
    try:
        lf, ll, missing = cov['listed_first'], cov['listed_last'], cov['missing_days']
    except (KeyError, TypeError):
        return False
    if lf is None or ll is None or missing is None:
        return False
    if dt.date.fromisoformat(lf) > rs or dt.date.fromisoformat(ll) < re_x - ONE:
        return False
    return not any(rs <= dt.date.fromisoformat(m) < re_x for m in missing)


AUDIT = ('failures', 'stopped')                            # Q6: nested content is audit payload only


def _groups_ok(m, syms):
    """H2 / ruling 6490439004: groups is binding and exactly partitions `symbols` by coverage[symbol].group."""
    g = m['groups']
    if not isinstance(g, dict) or set(g) != set(S['groups']):
        return False
    seen = []
    for name in S['groups']:
        v = g[name]
        if type(v) is not list or not all(type(s) is str for s in v) or v != sorted(set(v)):
            return False                                   # not a list of str, unsorted or duplicated
        seen += v
    if len(seen) != len(set(seen)) or set(seen) != set(syms):
        return False                                       # a symbol in two lists, or missing from the partition
    return all(s in g[m['coverage'][s]['group']] for s in syms)


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
    if _nonfinite(m):                                      # NaN / Infinity anywhere, refused at schema entry
        raise Refuse('SCHEMA_NONFINITE')
    _fields(m, S['top_level_fields'])
    for k, v in PIN.items():
        if type(m[k]) is not type(v) or m[k] != v:         # type equality: a bool never passes for gate_months
            raise Refuse('SCHEMA_VERSION')
    if (type(m['authoritative']) is not bool or type(m['complete']) is not bool or type(m['files']) is not list
            or type(m['failures']) is not list or type(m['symbols']) is not list
            or not (m['stopped'] is None or isinstance(m['stopped'], dict)) or type(m['total_bytes']) is not int
            or type(m['digest']) is not str):
        raise Refuse('SCHEMA_TYPE')
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
    for cov in m['coverage'].values():
        _fields(cov, S['coverage_entry_fields'])
        for g in ('leading_gap', 'trailing_gap'):
            if cov[g] is not None:
                _fields(cov[g], S['gap_fields'])
        for r in ('longest_complete_month_run', 'month_run_if_holes_excused'):
            _fields(cov[r], S['month_run_fields'])
        if not _coverage_values_ok(cov):
            raise Refuse('SCHEMA_TYPE')
    if not _groups_ok(m, syms):
        raise Refuse('SCHEMA_GROUPS')
    for cov in m['coverage'].values():                     # Q5: exact envelope, covers() cannot excuse it
        if cov['required_start'] != m['start'] or cov['required_end_exclusive'] != (me + ONE).isoformat():
            raise Refuse('SCHEMA_COVERAGE_ENVELOPE')
    if type(symbol) is not str or symbol not in syms:
        raise Refuse('REQ_SYMBOL')
    if rs < ms or rx > me + ONE:                           # Q1: sub-windows only
        raise Refuse('REQ_WINDOW')
    if m['total_bytes'] != sum(f['bytes'] for f in m['files']):
        raise Refuse('SCHEMA_TOTAL_BYTES')
    if m['digest'] != digest_of(m):
        raise Refuse('SCHEMA_DIGEST')
    return rs, rx, ms, me


def _p6(m, symbol, rs, rx, ms, me):
    """Runs on a validated manifest only: every value is already type-checked."""
    bad, paths = [], set()
    for f in m['files']:
        d = _iso(f['date'])
        if not SYM.fullmatch(f['symbol']) or f['symbol'] not in m['symbols']:
            bad.append('entry_symbol')
        elif d is None:
            bad.append('entry_date')
        elif not (ms <= d <= me and d < CUTOFF):
            bad.append('outside_window')
        elif f['path'] != IDR['path_template'].format(symbol=f['symbol'], date=f['date']):
            bad.append('entry_path')
        elif f['source'] != IDR['source_template'].format(path=f['path']):
            bad.append('entry_source')
        elif not SHA.fullmatch(f['sha256']):
            bad.append('entry_sha256')
        elif not MIN_BYTES <= f['bytes'] <= MAX_BYTES:     # no clamping
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


def _audit(m):
    """Q6: P3/P4 from well-typed audit containers, plus a diagnostic for malformed nested content. Never raises."""
    out = []
    try:
        st, fl = m['stopped'], m['failures']
        if isinstance(st, dict):
            out.append('P3')
        if type(fl) is list and fl:
            out.append('P4')
        bad = ((isinstance(st, dict) and set(st) != set(S['stopped_fields']))
               or (type(fl) is list and not all(isinstance(x, dict) for x in fl)))
    except Exception:                                      # audit payload only: never decides admission
        bad = True
    if bad:
        out.append('DIAG:audit_content')
    return out


def _disk(m, symbol, rs, rx, read_bytes):
    """Q3: every admitted file must match its exact size and sha256 on disk before any row is read."""
    bad = []
    for f in m['files']:
        if f['symbol'] != symbol or not rs <= dt.date.fromisoformat(f['date']) < rx:
            continue
        try:
            data = read_bytes(f['path'])
        except FileNotFoundError:
            bad.append('DISK:missing')
            continue
        except OSError:
            bad.append('DISK:unreadable')
            continue
        if data is None:
            bad.append('DISK:missing')
        elif type(data) is not bytes:
            bad.append('DISK:unreadable')
        elif len(data) != f['bytes']:
            bad.append('DISK:size')
        elif hashlib.sha256(data).hexdigest() != f['sha256']:
            bad.append('DISK:sha256')
    return sorted(set(bad))


def evaluate_symbol(m, symbol, req_start, req_end_x, read_bytes):
    """(admitted, reasons) for ONE symbol. DIAG:* entries are diagnostics only.
    SCHEMA_MALFORMED catches unexpected errors ONLY while validating the input; once the manifest is validated, an
    exception is a programmer error and propagates (never silently turned into a refusal)."""
    audit = _audit(m) if isinstance(m, dict) else []
    try:
        rs, rx, ms, me = _preconditions(m, symbol, req_start, req_end_x)
    except Refuse as r:
        return False, [str(r)] + [a for a in audit if a in ('P3', 'P4')]
    except Exception:
        return False, ['SCHEMA_MALFORMED']
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
    if not reasons:                                        # disk reads only after all six hold (never after P3/P4)
        reasons = _disk(m, symbol, rs, rx, read_bytes)
    reasons += [a for a in audit if a.startswith('DIAG:')]
    return not [r for r in reasons if not r.startswith('DIAG:')], reasons


def admit(m, symbols, req_start, req_end_x, read_bytes):
    """Q4: (admitted, {symbol: reasons}). All-or-nothing over the requested set."""
    if (type(symbols) is not list or not symbols or not all(type(s) is str for s in symbols)
            or len(set(symbols)) != len(symbols)):
        return False, {'*': ['REQ_SYMBOL']}
    per = {s: evaluate_symbol(m, s, req_start, req_end_x, read_bytes) for s in symbols}
    return all(ok for ok, _ in per.values()), {s: r for s, (_, r) in per.items()}


def heads(reasons):
    return sorted({r.split(':')[0] for r in reasons})


# ------------------------------------------------------------------ manifest builder (shape of build_manifest @ 93642d8)
def content(path, size=1234):
    return (path.encode() * (size // len(path) + 1))[:size]      # deterministic fake zip bytes


def STORE(path):                                                    # injected read_bytes: a consistent fake disk
    return content(path)


def entry(sym, day, size=1234):
    path = f'daily/metrics/{sym}/{sym}-metrics-{day}.zip'
    return {'path': path, 'symbol': sym, 'date': day, 'bytes': size,
            'sha256': hashlib.sha256(content(path, size)).hexdigest(),
            'source': 'https://data.binance.vision/data/futures/um/' + path}


def evaluate(m, symbol, req_start, req_end_x, read_bytes=STORE):
    return evaluate_symbol(m, symbol, req_start, req_end_x, read_bytes)


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
    assert ci['provenance_pr'] == 225 and ci['master_reference'] == MASTER and ci['on_master'] is True
    assert ci['master_reference_short'] == MASTER[:7] and ci['pre_merge_head'].startswith('f850323')
    assert 'rebind_required_before_signing' not in ci
    assert ci['rebind']['status'] == 'DONE' and ci['rebind']['ruling'] == '6494813780'
    assert ci['rebind']['rebound_to'] == MASTER
    assert 'tools/research/oi_metrics_fetch.py' in ci['files']
    assert C['doc'] == os.path.basename(MD)
    assert '**Status: DRAFT — unsigned.**' in md and 'the JSON wins any conflict' in md and '93642d8' in md


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
    assert all('93642d8' in c['citation'] for c in p['conjuncts'])
    assert {'authoritative', 'covers()'} <= set(p['never_sufficient_alone'])


def test_forbidden_list_has_the_key_items():
    assert {'loader implementation in this PR', 'archive or network access', 'bulk run', 'feature computation',
            'ML use', 'runtime import', 'PAPER authority'} <= set(C['forbidden'])


def test_pinned_values_equal_the_225_constants():
    # literal values of oi_metrics_fetch.py / oi_metrics_probe.py @ 93642d8 (asserted as literals; no import)
    assert PIN['format'] == 'zb-oi-metrics-fetch/1' and PIN['loader_version'] == 'zb-oi-metrics-raw-zip/1'
    assert PIN['source_class'] == 'archive-verified' and PIN['data_root'] == 'binance_um_metrics'
    assert PIN['series'] == 'metrics' and PIN['mode'] == 'bulk' and PIN['gate_months'] == 36
    assert PIN['cutoff_exclusive'] == C['date_semantics']['cutoff_exclusive'] == '2026-09-28'
    assert C['date_semantics']['last_admissible_day'] == '2026-09-27'
    assert len(S['top_level_fields']) == 21 and S['file_entry_fields'] == sorted(
        ['path', 'symbol', 'date', 'bytes', 'sha256', 'source'])
    assert len(S['coverage_entry_fields']) == 11 and 'missing_required_days' in S['coverage_entry_fields']
    assert all('93642d8' in C['date_semantics'][k] for k in ('cutoff_citation', 'end_exclusive_citation'))
    assert 'oi_metrics_probe.py' in C['date_semantics']['cutoff_citation']
    assert 'parse_key' in C['identity_rules']['path_citation']


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


def test_size_bounds_are_1_to_100_mib_and_cap_itself_passes_p6():
    assert (MIN_BYTES, MAX_BYTES) == (1, 104857600)
    assert evaluate(edit(BASE, _set(0, bytes=MAX_BYTES)), *REQ) == (False, ['DISK:size'])   # passes P6, not the disk


# ------------------------------------------------------------------ H1 (Cowork 6490219007): mistyped values refuse, never raise
def _raw(fn):
    m = copy.deepcopy(BASE)
    fn(m)
    return m                                               # not resealed: digest_of would refuse NaN itself


@pytest.mark.parametrize('name,fn', [
    ('nan_top_level', lambda m: m.update(total_bytes=float('nan'))),
    ('inf_in_entry', lambda m: m['files'][0].update(bytes=float('inf'))),
    ('nan_in_groups', lambda m: m['groups'].update(short_history=[float('nan')])),
    ('nan_in_coverage', lambda m: m['coverage']['BTCUSDT']['longest_complete_month_run'].update(months=float('nan'))),
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


class _HostileDict(dict):
    def items(self):
        raise KeyError('hostile mapping')


def test_unexpected_malformed_input_maps_to_schema_malformed():
    assert evaluate(_HostileDict(BASE), *REQ) == (False, ['SCHEMA_MALFORMED'])


def test_internal_bug_on_a_valid_manifest_is_not_hidden(monkeypatch):
    """Ruling 6490439004: SCHEMA_MALFORMED must never hide a programmer error on VALID input."""
    def boom(*a):
        raise RuntimeError('injected internal bug')
    monkeypatch.setitem(globals(), 'covers', boom)
    with pytest.raises(RuntimeError, match='injected internal bug'):
        evaluate(BASE, *REQ)
    monkeypatch.undo()
    monkeypatch.setitem(globals(), '_p6', boom)
    with pytest.raises(RuntimeError):
        evaluate(BASE, *REQ)


@pytest.mark.parametrize('m', [None, [], 'x', 7])
def test_non_object_manifest_refused_not_raised(m):
    assert evaluate(m, *REQ) == (False, ['SCHEMA_NOT_OBJECT'])


# ------------------------------------------------------------------ H2 (Cowork 6490219007): groups is binding
GROUPS_CASES = {
    'missing_key': lambda m: m['groups'].pop('short_history'),
    'extra_key': lambda m: m['groups'].update(other=[]),
    'not_a_dict': lambda m: m.update(groups=['strict_complete']),
    'value_str': lambda m: m['groups'].update(strict_complete='BTCUSDT'),
    'value_dict': lambda m: m['groups'].update(short_history={'BTCUSDT': 1}),
    'value_non_str_item': lambda m: m['groups'].update(short_history=[7]),
    'unsorted': lambda m: m['groups'].update(strict_complete=['ETHUSDT', 'BTCUSDT']),
    'duplicate': lambda m: m['groups'].update(strict_complete=['BTCUSDT', 'BTCUSDT', 'ETHUSDT']),
    'missing_from_partition': lambda m: m['groups'].update(strict_complete=['BTCUSDT']),
    'in_two_lists': lambda m: m['groups'].update(short_history=['ETHUSDT']),
    'unknown_symbol': lambda m: m['groups'].update(short_history=['SOLUSDT']),
    'group_vs_coverage_mismatch': lambda m: m['groups'].update(strict_complete=['BTCUSDT'], short_history=['ETHUSDT']),
    'coverage_group_changed': _cov(group='conditional_with_holes'),
}


@pytest.mark.parametrize('name', list(GROUPS_CASES))
def test_groups_partition_attacks_refused(name):
    m = edit(manifest(symbols=('BTCUSDT', 'ETHUSDT')), GROUPS_CASES[name])
    assert evaluate(m, *REQ) == (False, ['SCHEMA_GROUPS'])


def test_groups_valid_mixed_partition_admitted():
    def mixed(m):
        m['coverage']['ETHUSDT']['group'] = 'conditional_with_holes'
        m['groups'].update(strict_complete=['BTCUSDT'], conditional_with_holes=['ETHUSDT'])
    assert evaluate(edit(manifest(symbols=('BTCUSDT', 'ETHUSDT')), mixed), *REQ) == (True, [])


def test_h1_h2_recorded_in_contract():
    assert C['manifest_schema']['max_bytes_per_file'] == 104857600 and C['manifest_schema']['min_bytes_per_file'] == 1
    assert 'binding' in C['manifest_schema']['groups_rule'] and '93642d8' in C['manifest_schema']['groups_rule']
    codes = [p['code'] for p in C['preconditions_fail_closed']]
    assert {'SCHEMA_NONFINITE', 'SCHEMA_GROUPS', 'SCHEMA_MALFORMED'} <= set(codes) and 'SCHEMA_INVALID' not in codes
    assert codes.index('SCHEMA_NONFINITE') < codes.index('SCHEMA_DIGEST')
    assert set(C['review_6490219007']) == {'source', 'H1', 'H2'}
    assert set(C['ruling_6490439004']) == {'H1', 'H2_bytes', 'H2_groups', 'Q6_scope'}


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
               lambda m: m['coverage']['BTCUSDT']['longest_complete_month_run'].update(x=1)):
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


# ------------------------------------------------------------------ ruling 6490314342 (Q1-Q6)
def test_all_questions_resolved_by_ruling():
    rq = {q['id']: q['ruling'] for q in C['resolved_questions']}
    assert list(rq) == ['Q1', 'Q2', 'Q3', 'Q4', 'Q5', 'Q6', 'Q7', 'Q8']
    assert {rq[q] for q in ('Q1', 'Q2', 'Q3', 'Q4', 'Q5', 'Q6')} == {'6490314342'}
    assert rq['Q7'] == rq['Q8'] == '6490439004'
    assert C['open_questions'] == []
    assert set(C['ruling_6490314342']) == {'Q1', 'Q2', 'Q3', 'Q4', 'Q5', 'Q6'}
    assert C['disk_verification']['status'] == 'MANDATORY loader step'
    assert set(C['disk_verification']['refusals']) == {'DISK:missing', 'DISK:unreadable', 'DISK:size', 'DISK:sha256'}
    assert C['status'] == 'DRAFT' and C['signed_by'] is None
    assert 'rebind' in json.dumps(C['code_identity'])


def test_q1_sub_window_inside_admitted_outside_refused():
    assert evaluate(BASE, 'BTCUSDT', '2026-09-02', '2026-09-03') == (True, [])
    assert evaluate(BASE, 'BTCUSDT', '2026-08-31', '2026-09-02') == (False, ['REQ_WINDOW'])
    assert evaluate(BASE, 'BTCUSDT', '2026-09-03', '2026-09-05') == (False, ['REQ_WINDOW'])


def test_q2_digest_and_total_bytes_are_global_not_p6():
    m = copy.deepcopy(BASE)
    m['files'][0]['sha256'] = '0' * 64                         # identity-valid, but the digest no longer matches
    assert evaluate(m, *REQ) == (False, ['SCHEMA_DIGEST'])
    m2 = edit(manifest(symbols=('BTCUSDT', 'ETHUSDT')), _set(-1, sha256='A' * 64))   # corrupt ETH entry
    assert evaluate(m2, *REQ) == (False, ['P6:entry_sha256'])  # refuses a BTC request too


def _reader(override):
    def rb(path):
        if path in override:
            v = override[path]
            if isinstance(v, BaseException):
                raise v
            return v
        return STORE(path)
    return rb


P02 = 'daily/metrics/BTCUSDT/BTCUSDT-metrics-2026-09-02.zip'


@pytest.mark.parametrize('name,value,want', [
    ('missing_raises', FileNotFoundError(P02), 'DISK:missing'),
    ('missing_none', None, 'DISK:missing'),
    ('unreadable', PermissionError('denied'), 'DISK:unreadable'),
    ('not_bytes', 'text', 'DISK:unreadable'),
    ('size_short', content(P02)[:-1], 'DISK:size'),
    ('size_long', content(P02) + b'x', 'DISK:size'),
    ('hash_same_size', b'z' * 1234, 'DISK:sha256'),
])
def test_q3_disk_verification_refuses(name, value, want):
    assert evaluate(BASE, *REQ, read_bytes=_reader({P02: value})) == (False, [want])


def test_q3_disk_reads_only_requested_files_and_never_after_p3_p4():
    seen = []

    def rb(path):
        seen.append(path)
        return STORE(path)
    assert evaluate(BASE, 'BTCUSDT', '2026-09-02', '2026-09-03', read_bytes=rb) == (True, [])
    assert seen == [P02]
    seen.clear()
    for cid in ('P3', 'P4'):
        assert evaluate(edit(BASE, ALONE[cid]), *REQ, read_bytes=rb) == (False, [cid])
    assert seen == []


TWO = manifest(symbols=('BTCUSDT', 'ETHUSDT'))
REQ2 = ('2026-09-01', '2026-09-04')


def test_q4_all_symbols_admitted():
    ok, per = admit(TWO, ['BTCUSDT', 'ETHUSDT'], *REQ2, STORE)
    assert ok is True and per == {'BTCUSDT': [], 'ETHUSDT': []}


def test_q4_one_symbol_refused_refuses_the_set_with_per_symbol_evidence():
    m = edit(TWO, lambda m: m['files'].remove(next(f for f in m['files'] if f['symbol'] == 'ETHUSDT')))
    ok, per = admit(m, ['BTCUSDT', 'ETHUSDT'], *REQ2, STORE)
    assert ok is False and per == {'BTCUSDT': [], 'ETHUSDT': ['P6:missing_day']}
    assert admit(m, ['BTCUSDT'], *REQ2, STORE) == (True, {'BTCUSDT': []})      # single symbol stands alone


def test_q4_disk_failure_on_one_symbol_refuses_the_set():
    eth = 'daily/metrics/ETHUSDT/ETHUSDT-metrics-2026-09-03.zip'
    ok, per = admit(TWO, ['BTCUSDT', 'ETHUSDT'], *REQ2, _reader({eth: None}))
    assert ok is False and per == {'BTCUSDT': [], 'ETHUSDT': ['DISK:missing']}


def test_q4_unknown_symbol_in_batch_refuses_the_set():
    ok, per = admit(TWO, ['BTCUSDT', 'SOLUSDT'], *REQ2, STORE)
    assert ok is False and per == {'BTCUSDT': [], 'SOLUSDT': ['REQ_SYMBOL']}


@pytest.mark.parametrize('syms', [[], ['BTCUSDT', 'BTCUSDT'], 'BTCUSDT', ['BTCUSDT', 7], None, [['BTCUSDT']]])
def test_q4_malformed_symbol_list_refused(syms):
    assert admit(TWO, syms, *REQ2, STORE) == (False, {'*': ['REQ_SYMBOL']})


@pytest.mark.parametrize('field,value', [
    ('required_start', '2026-08-31'), ('required_start', '2026-09-02'),
    ('required_end_exclusive', '2026-09-03'), ('required_end_exclusive', '2026-09-05'),
])
def test_q5_coverage_envelope_mismatch_refused_even_when_covers_passes(field, value):
    m = edit(BASE, _cov(**{field: value}))
    assert covers(m['coverage']['BTCUSDT'], dt.date(2026, 9, 1), dt.date(2026, 9, 4)) is True
    assert evaluate(m, *REQ) == (False, ['SCHEMA_COVERAGE_ENVELOPE'])


def test_q5_envelope_checked_for_every_symbol():
    m = edit(TWO, lambda m: m['coverage']['ETHUSDT'].update(required_start='2026-09-02'))
    assert evaluate(m, *REQ) == (False, ['SCHEMA_COVERAGE_ENVELOPE'])


def _raw_audit(**kw):
    m = copy.deepcopy(BASE)
    m.update(**kw)
    try:
        m['digest'] = digest_of(m)
    except ValueError:
        pass                                                # NaN payload: digest cannot be sealed
    return m


@pytest.mark.parametrize('name,kw,want', [
    ('stopped_empty_object', {'stopped': {}}, ['P3', 'DIAG:audit_content']),
    ('stopped_extra_fields', {'stopped': {'reason': 'r', 'url': 'u', 'status': 1, 'x': 2}}, ['P3', 'DIAG:audit_content']),
    ('stopped_nested_junk', {'stopped': {'reason': [1], 'url': {}, 'status': None}}, ['P3']),
    ('failure_item_int', {'failures': [1]}, ['P4', 'DIAG:audit_content']),
    ('failure_item_list', {'failures': [['a']]}, ['P4', 'DIAG:audit_content']),
    ('failure_unhashable_path', {'failures': [{'path': ['x'], 'reason': {}}]}, ['P4']),
    ('failure_nan', {'failures': [{'x': float('nan')}]}, ['SCHEMA_NONFINITE', 'P4']),
    ('stopped_inf', {'stopped': {'reason': 'r', 'url': 'u', 'status': float('inf')}}, ['SCHEMA_NONFINITE', 'P3']),
])
def test_q6_nested_audit_content_is_diagnostic_only(name, kw, want):
    called = []
    ok, reasons = evaluate(_raw_audit(**kw), *REQ, read_bytes=lambda p: called.append(p))
    assert (ok, reasons) == (False, want) and called == []


def test_q6_container_types_are_the_only_audit_precondition():
    assert evaluate(_raw_audit(failures={'a': 1}), *REQ) == (False, ['SCHEMA_TYPE'])
    assert evaluate(_raw_audit(stopped=['x']), *REQ) == (False, ['SCHEMA_TYPE'])


def test_q6_precondition_refusal_still_reports_p3_p4():
    m = _raw_audit(failures=[dict(FAIL)], stopped={'reason': 'r', 'url': 'u', 'status': 429})
    m['format'] = 'zb-oi-metrics-fetch/2'
    assert evaluate(m, *REQ) == (False, ['SCHEMA_VERSION', 'P3', 'P4'])


# ------------------------------------------------------------------ ruling 6490439004 (H1 / H2 / Q6 scope)
@pytest.mark.parametrize('name,fn', [
    ('gate_months_bool', lambda m: m.update(gate_months=True)),
    ('gap_days_bool', _cov(leading_gap={'start': '2026-08-01', 'end_exclusive': '2026-08-02', 'days': True})),
    ('month_run_months_bool', _cov(longest_complete_month_run={'months': False, 'start': None, 'end': None})),
    ('total_bytes_bool', lambda m: m.update(total_bytes=True)),
])
def test_bools_rejected_where_ints_required(name, fn):
    m = copy.deepcopy(BASE)
    fn(m)
    m['digest'] = digest_of(m)
    ok, reasons = evaluate(m, *REQ)
    assert ok is False and reasons[0] in ('SCHEMA_VERSION', 'SCHEMA_TYPE')


@pytest.mark.parametrize('field,value', [('start', '2026-9-01'), ('end', '2026-09-3'), ('start', ' 2026-09-01'),
                                         ('end', '2026-09-03\n'), ('start', '+2026-09-01')])
def test_manifest_dates_must_be_exact_zero_padded_iso(field, value):
    assert evaluate(edit(BASE, lambda m: m.update({field: value})), *REQ) == (False, ['SCHEMA_DATE'])


def test_q6_scope_does_not_relax_other_checks():
    """A refused audit payload never excuses a file, coverage, groups, date, numeric or digest defect."""
    base = dict(failures=[dict(FAIL)])
    for fn, code in ((lambda m: m['files'][0].update(bytes=True), 'SCHEMA_TYPE'),
                     (_cov(listed_first='2026-9-1'), 'SCHEMA_TYPE'),
                     (lambda m: m['groups'].pop('short_history'), 'SCHEMA_GROUPS'),
                     (lambda m: m.update(end='2026-09-03T00:00'), 'SCHEMA_DATE'),
                     (lambda m: m['files'][0].update(bytes=float('inf')), 'SCHEMA_NONFINITE')):
        m = copy.deepcopy(BASE)
        m.update(**copy.deepcopy(base))
        fn(m)
        assert evaluate(m, *REQ) == (False, [code, 'P4'])
    m = _raw_audit(failures=[dict(FAIL)])
    m['digest'] = '0' * 64
    assert evaluate(m, *REQ) == (False, ['SCHEMA_DIGEST', 'P4'])


# ------------------------------------------------------------------ rebind to merged master 93642d8 (rulings 6493041460, 6494813780)
def _blob_at(commit, rel):
    """Content-addressed read of a committed blob via git (a subprocess, never an import), so a later change on
    master cannot invalidate the pin."""
    return subprocess.run(['git', '-C', ROOT, 'show', f'{commit}:{rel}'], stdin=subprocess.DEVNULL,
                          capture_output=True, check=True).stdout.replace(b'\r\n', b'\n')


def test_code_pins_match_master_blobs():
    ci = C['code_identity']
    pins = {k: v for k, v in ci['pinned_files'].items() if not k.startswith('_')}
    assert set(pins) == set(ci['files'])
    assert {'tools/research/oi_metrics_fetch.py', 'tools/research/oi_metrics_probe.py'} <= set(pins)
    for path, want in pins.items():
        assert re.fullmatch(r'[0-9a-f]{64}', want), path
        assert hashlib.sha256(_blob_at(ci['master_reference'], path)).hexdigest() == want, path


def test_every_citation_is_rebound_to_93642d8():
    body = {k: v for k, v in C.items() if k != 'code_identity'}
    text = json.dumps(body)
    assert 'f850323' not in text and text.count('@ 93642d8') >= 18


def test_rebind_history_records_both_merges():
    rb = C['code_identity']['rebind']
    assert rb['status'] == 'DONE' and rb['ruling'] == '6494813780'
    assert [(h['ruling'], h['to'][:7]) for h in rb['history']] == [('6493041460', 'f8f03df'), ('6494813780', '93642d8')]
    assert rb['history'][0]['from'] == 'f850323' and rb['history'][1]['from'] == 'f8f03df'
