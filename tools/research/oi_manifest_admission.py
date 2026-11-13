"""Fail-closed OI manifest admission for research-only loaders.

This is the production implementation of the signed OI-LOADER-01 contract.  It
does not open an archive or parse a row.  A caller receives an all-or-nothing
decision only after the requested files have been size- and SHA-256-verified
from a confined local store.
"""
from __future__ import annotations

import datetime as dt
import hashlib
import json
import math
import os
import re
from pathlib import Path


REPO = Path(__file__).resolve().parents[2]
CONTRACT_PATH = REPO / 'contracts' / 'oi_loader_01' / 'oi_loader_01_contract.v1.json'
CHUNK = 1 << 20

with CONTRACT_PATH.open(encoding='utf-8') as _fh:
    CONTRACT = json.load(_fh)

SCHEMA = CONTRACT['manifest_schema']
PINNED = SCHEMA['pinned_values']
IDENTITY = CONTRACT['identity_rules']
ISO = re.compile(CONTRACT['date_semantics']['iso_date_regex'])
SYMBOL = re.compile(IDENTITY['symbol_regex'])
SHA256 = re.compile(IDENTITY['sha256_regex'])
MONTH = re.compile(SCHEMA['value_rules']['month_regex'])
MIN_BYTES = SCHEMA['min_bytes_per_file']
MAX_BYTES = SCHEMA['max_bytes_per_file']
CUTOFF = dt.date.fromisoformat(CONTRACT['date_semantics']['cutoff_exclusive'])
ONE_DAY = dt.timedelta(days=1)


class AdmissionError(ValueError):
    """A deterministic fail-closed refusal."""


def _iso(value):
    if type(value) is not str or not ISO.fullmatch(value):
        return None
    try:
        return dt.date.fromisoformat(value)
    except ValueError:
        return None


def _nonfinite(value):
    if type(value) is float:
        return not math.isfinite(value)
    if isinstance(value, dict):
        return any(_nonfinite(v) for v in value.values())
    if isinstance(value, list):
        return any(_nonfinite(v) for v in value)
    return False


def _fields(value, expected):
    if not isinstance(value, dict):
        raise AdmissionError('SCHEMA_NOT_OBJECT')
    if set(value) - set(expected):
        raise AdmissionError('SCHEMA_UNKNOWN_FIELD')
    if set(expected) - set(value):
        raise AdmissionError('SCHEMA_MISSING_FIELD')


def _nonnegative_int(value):
    return type(value) is int and value >= 0


def _iso_or_none(value):
    return value is None or _iso(value) is not None


def _coverage_values_ok(cov):
    if cov['group'] not in SCHEMA['groups']:
        return False
    if not (_iso_or_none(cov['listed_first']) and _iso_or_none(cov['listed_last'])):
        return False
    for key in ('missing_days', 'missing_required_days'):
        if type(cov[key]) is not list or not all(_iso(day) is not None for day in cov[key]):
            return False
    if _iso(cov['required_start']) is None or _iso(cov['required_end_exclusive']) is None:
        return False
    for key in ('leading_gap', 'trailing_gap'):
        gap = cov[key]
        if gap is not None and not (
            _iso_or_none(gap['start']) and _iso(gap['end_exclusive']) is not None
            and (gap['days'] is None or _nonnegative_int(gap['days']))
        ):
            return False
    for key in ('longest_complete_month_run', 'month_run_if_holes_excused'):
        run = cov[key]
        if not _nonnegative_int(run['months']) or not all(
            value is None or (type(value) is str and MONTH.fullmatch(value))
            for value in (run['start'], run['end'])
        ):
            return False
    return True


def canonical(value):
    return json.dumps(value, sort_keys=True, separators=(',', ':'), ensure_ascii=True,
                      allow_nan=False).encode('ascii')


def digest_of(manifest):
    return hashlib.sha256(canonical({k: v for k, v in manifest.items() if k != 'digest'})).hexdigest()


def covers(cov, required_start, required_end_exclusive):
    try:
        first, last, missing = cov['listed_first'], cov['listed_last'], cov['missing_days']
    except (KeyError, TypeError):
        return False
    if first is None or last is None or missing is None:
        return False
    if dt.date.fromisoformat(first) > required_start:
        return False
    if dt.date.fromisoformat(last) < required_end_exclusive - ONE_DAY:
        return False
    return not any(required_start <= dt.date.fromisoformat(day) < required_end_exclusive for day in missing)


def _groups_ok(manifest, symbols):
    groups = manifest['groups']
    if not isinstance(groups, dict) or set(groups) != set(SCHEMA['groups']):
        return False
    seen = []
    for name in SCHEMA['groups']:
        values = groups[name]
        if type(values) is not list or not all(type(s) is str for s in values) or values != sorted(set(values)):
            return False
        seen.extend(values)
    if len(seen) != len(set(seen)) or set(seen) != set(symbols):
        return False
    return all(symbol in groups[manifest['coverage'][symbol]['group']] for symbol in symbols)


def _preconditions(manifest, symbol, request_start, request_end_exclusive):
    start, end_exclusive = _iso(request_start), _iso(request_end_exclusive)
    if start is None or end_exclusive is None:
        raise AdmissionError('REQ_DATE')
    if end_exclusive <= start:
        raise AdmissionError('REQ_ORDER')
    if end_exclusive > CUTOFF:
        raise AdmissionError('REQ_CUTOFF')
    if not isinstance(manifest, dict):
        raise AdmissionError('SCHEMA_NOT_OBJECT')
    if _nonfinite(manifest):
        raise AdmissionError('SCHEMA_NONFINITE')
    _fields(manifest, SCHEMA['top_level_fields'])
    for key, value in PINNED.items():
        if type(manifest[key]) is not type(value) or manifest[key] != value:
            raise AdmissionError('SCHEMA_VERSION')
    if (
        type(manifest['authoritative']) is not bool or type(manifest['complete']) is not bool
        or type(manifest['files']) is not list or type(manifest['failures']) is not list
        or type(manifest['symbols']) is not list
        or not (manifest['stopped'] is None or isinstance(manifest['stopped'], dict))
        or type(manifest['total_bytes']) is not int or type(manifest['digest']) is not str
    ):
        raise AdmissionError('SCHEMA_TYPE')
    for entry in manifest['files']:
        _fields(entry, SCHEMA['file_entry_fields'])
        if any(type(entry[key]) is not str for key in ('path', 'symbol', 'date', 'sha256', 'source')) \
                or type(entry['bytes']) is not int:
            raise AdmissionError('SCHEMA_TYPE')
    manifest_start, manifest_end = _iso(manifest['start']), _iso(manifest['end'])
    if manifest_start is None or manifest_end is None or manifest_end >= CUTOFF or manifest_start > manifest_end:
        raise AdmissionError('SCHEMA_DATE')
    symbols = manifest['symbols']
    if (
        not all(type(s) is str and SYMBOL.fullmatch(s) for s in symbols)
        or symbols != sorted(set(symbols)) or not isinstance(manifest['coverage'], dict)
        or set(manifest['coverage']) != set(symbols)
    ):
        raise AdmissionError('SCHEMA_SYMBOLS')
    for cov in manifest['coverage'].values():
        _fields(cov, SCHEMA['coverage_entry_fields'])
        for key in ('leading_gap', 'trailing_gap'):
            if cov[key] is not None:
                _fields(cov[key], SCHEMA['gap_fields'])
        for key in ('longest_complete_month_run', 'month_run_if_holes_excused'):
            _fields(cov[key], SCHEMA['month_run_fields'])
        if not _coverage_values_ok(cov):
            raise AdmissionError('SCHEMA_TYPE')
    if not _groups_ok(manifest, symbols):
        raise AdmissionError('SCHEMA_GROUPS')
    envelope_end = (manifest_end + ONE_DAY).isoformat()
    if any(cov['required_start'] != manifest['start'] or cov['required_end_exclusive'] != envelope_end
           for cov in manifest['coverage'].values()):
        raise AdmissionError('SCHEMA_COVERAGE_ENVELOPE')
    if type(symbol) is not str or symbol not in symbols:
        raise AdmissionError('REQ_SYMBOL')
    if start < manifest_start or end_exclusive > manifest_end + ONE_DAY:
        raise AdmissionError('REQ_WINDOW')
    if manifest['total_bytes'] != sum(entry['bytes'] for entry in manifest['files']):
        raise AdmissionError('SCHEMA_TOTAL_BYTES')
    if manifest['digest'] != digest_of(manifest):
        raise AdmissionError('SCHEMA_DIGEST')
    return start, end_exclusive, manifest_start, manifest_end


def _p6(manifest, symbol, start, end_exclusive, manifest_start, manifest_end):
    bad, paths = [], set()
    for entry in manifest['files']:
        day = _iso(entry['date'])
        if not SYMBOL.fullmatch(entry['symbol']) or entry['symbol'] not in manifest['symbols']:
            bad.append('entry_symbol')
        elif day is None:
            bad.append('entry_date')
        elif not (manifest_start <= day <= manifest_end and day < CUTOFF):
            bad.append('outside_window')
        elif entry['path'] != IDENTITY['path_template'].format(symbol=entry['symbol'], date=entry['date']):
            bad.append('entry_path')
        elif entry['source'] != IDENTITY['source_template'].format(path=entry['path']):
            bad.append('entry_source')
        elif not SHA256.fullmatch(entry['sha256']):
            bad.append('entry_sha256')
        elif not MIN_BYTES <= entry['bytes'] <= MAX_BYTES:
            bad.append('entry_bytes')
        if entry['path'] in paths:
            bad.append('duplicate_path')
        paths.add(entry['path'])
    day = start
    while day < end_exclusive:
        count = sum(1 for entry in manifest['files']
                    if entry['symbol'] == symbol and entry['date'] == day.isoformat())
        if count == 0:
            bad.append('missing_day')
        elif count > 1:
            bad.append('duplicate_day')
        day += ONE_DAY
    return sorted(set(bad))


def _audit(manifest):
    output = []
    try:
        stopped, failures = manifest['stopped'], manifest['failures']
        if isinstance(stopped, dict):
            output.append('P3')
        if type(failures) is list and failures:
            output.append('P4')
        bad = ((isinstance(stopped, dict) and set(stopped) != set(SCHEMA['stopped_fields']))
               or (type(failures) is list and not all(isinstance(item, dict) for item in failures)))
    except Exception:
        bad = True
    if bad:
        output.append('DIAG:audit_content')
    return output


def confined_path(root, relative):
    """Resolve a manifest POSIX path beneath root without following a path escape."""
    if type(relative) is not str or '\\' in relative or any(part in ('', '.', '..') for part in relative.split('/')):
        raise OSError('unreadable manifest path')
    root = Path(root).resolve()
    path = root.joinpath(*relative.split('/')).resolve()
    try:
        path.relative_to(root)
    except ValueError as exc:
        raise OSError('manifest path escapes store') from exc
    return path


def _verify_disk(manifest, symbol, start, end_exclusive, root):
    bad = []
    for entry in manifest['files']:
        if entry['symbol'] != symbol or not start <= dt.date.fromisoformat(entry['date']) < end_exclusive:
            continue
        try:
            path = confined_path(root, entry['path'])
            size = path.stat().st_size
            if size != entry['bytes']:
                bad.append('DISK:size')
                continue
            digest = hashlib.sha256()
            with path.open('rb') as fh:
                for chunk in iter(lambda: fh.read(CHUNK), b''):
                    digest.update(chunk)
        except FileNotFoundError:
            bad.append('DISK:missing')
            continue
        except OSError:
            bad.append('DISK:unreadable')
            continue
        if digest.hexdigest() != entry['sha256']:
            bad.append('DISK:sha256')
    return sorted(set(bad))


def evaluate_symbol(manifest, symbol, request_start, request_end_exclusive, root):
    """Return ``(admitted, reasons)`` for one symbol without opening any zip."""
    audit = _audit(manifest) if isinstance(manifest, dict) else []
    try:
        start, end_exclusive, manifest_start, manifest_end = _preconditions(
            manifest, symbol, request_start, request_end_exclusive)
    except AdmissionError as exc:
        return False, [str(exc)] + [reason for reason in audit if reason in ('P3', 'P4')]
    except Exception:
        return False, ['SCHEMA_MALFORMED']
    reasons = []
    if manifest['authoritative'] is not True:
        reasons.append('P1')
    if manifest['complete'] is not True:
        reasons.append('P2')
    if manifest['stopped'] is not None:
        reasons.append('P3')
    if manifest['failures'] != []:
        reasons.append('P4')
    if not covers(manifest['coverage'][symbol], start, end_exclusive):
        reasons.append('P5')
    reasons.extend('P6:' + item for item in _p6(
        manifest, symbol, start, end_exclusive, manifest_start, manifest_end))
    if not reasons:
        reasons = _verify_disk(manifest, symbol, start, end_exclusive, root)
    reasons.extend(reason for reason in audit if reason.startswith('DIAG:'))
    return not [reason for reason in reasons if not reason.startswith('DIAG:')], reasons


def admit(manifest, symbols, request_start, request_end_exclusive, root):
    """All-or-nothing batch admission; every requested file is verified before success."""
    if (type(symbols) is not list or not symbols or not all(type(s) is str for s in symbols)
            or len(set(symbols)) != len(symbols)):
        return False, {'*': ['REQ_SYMBOL']}
    decisions = {
        symbol: evaluate_symbol(manifest, symbol, request_start, request_end_exclusive, root)
        for symbol in symbols
    }
    return all(ok for ok, _ in decisions.values()), {
        symbol: reasons for symbol, (_, reasons) in decisions.items()
    }
