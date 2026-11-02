"""Bounded readiness gate for a future OI-price-divergence 1h Stage-A.

This tool evaluates data readiness only.  It never computes a signal or return,
never downloads data, and is hard-bounded to the development window.  The
signed OI admission predicate runs before the causal row loader opens a zip.
"""
from __future__ import annotations

import argparse
import datetime as dt
import hashlib
import json
import os
import sys
from pathlib import Path

import oi_manifest_admission as ADMIT
import oi_metrics_loader as OIL


FORMAT = 'zb-oi-divergence-stagea-readiness/1'
START = '2024-10-14'
END_EXCLUSIVE = '2025-04-01'
CUTOFF = '2026-09-28'
PER_DAY = 288
SYMBOLS = (
    '1000PEPEUSDT', 'AAVEUSDT', 'ADAUSDT', 'ARBUSDT', 'AVAXUSDT',
    'BCHUSDT', 'BNBUSDT', 'BTCUSDT', 'DASHUSDT', 'DOGEUSDT',
    'ETHUSDT', 'FILUSDT', 'HBARUSDT', 'LINKUSDT', 'LTCUSDT',
    'QNTUSDT', 'RLCUSDT', 'SOLUSDT', 'UNIUSDT', 'XRPUSDT',
)


class ReadinessError(ValueError):
    pass


def _sha(path):
    h = hashlib.sha256()
    with open(path, 'rb') as f:
        for block in iter(lambda: f.read(1 << 20), b''):
            h.update(block)
    return h.hexdigest()


def _parse_utc(text):
    return dt.datetime.fromisoformat(text.replace('Z', '+00:00'))


def summarize_rows(rows, symbols=SYMBOLS, start=START, end_exclusive=END_EXCLUSIVE):
    lo, hi = dt.date.fromisoformat(start), dt.date.fromisoformat(end_exclusive)
    expected = (hi - lo).days * PER_DAY
    by = {s: [] for s in symbols}
    for row in rows:
        if row.get('symbol') not in by:
            raise ReadinessError('ROW:unexpected_symbol')
        period_end, available = _parse_utc(row['period_end']), _parse_utc(row['available_at'])
        if available - period_end != dt.timedelta(minutes=5):
            raise ReadinessError('ROW:availability_rule')
        by[row['symbol']].append((period_end, available))
    details = {}
    for sym, values in by.items():
        if len(values) != expected:
            raise ReadinessError(f'ROW:count:{sym}:{len(values)}!={expected}')
        if any(b[0] <= a[0] for a, b in zip(values, values[1:])):
            raise ReadinessError(f'ROW:order:{sym}')
        details[sym] = {'rows': len(values), 'first_period_end': values[0][0].isoformat(),
                        'last_period_end': values[-1][0].isoformat(),
                        'last_available_at': values[-1][1].isoformat()}
    return details


def check(manifest_path, store):
    """Return a durable READY/BLOCKED record; never raises for expected data absence."""
    base = {'format': FORMAT, 'candidate': 'OI-price divergence/reclaim 1h (rules not yet preregistered)',
            'scope': {'start': START, 'end_exclusive': END_EXCLUSIVE, 'symbols': list(SYMBOLS)},
            'sealed_holdout_opened': False, 'paper_authority': False, 'runtime_authority': False}
    path = Path(manifest_path)
    if not path.is_file():
        return {**base, 'status': 'DATA_BLOCKED', 'code': 'MANIFEST_MISSING',
                'detail': f'authoritative OI manifest not found: {path.resolve()}'}
    if not Path(store).is_dir():
        return {**base, 'status': 'DATA_BLOCKED', 'code': 'STORE_MISSING',
                'detail': f'OI archive store not found: {Path(store).resolve()}'}
    try:
        with path.open(encoding='utf-8') as f:
            manifest = json.load(f)
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        return {**base, 'status': 'DATA_BLOCKED', 'code': 'MANIFEST_UNREADABLE',
                'detail': type(exc).__name__}
    ok, reasons = ADMIT.admit(manifest, list(SYMBOLS), START, END_EXCLUSIVE, store)
    if not ok:
        return {**base, 'status': 'DATA_BLOCKED', 'code': 'ADMISSION_REFUSED', 'reasons': reasons,
                'manifest_sha256': _sha(path)}
    try:
        rows = OIL.load_rows(manifest, list(SYMBOLS), START, END_EXCLUSIVE, store)
        coverage = summarize_rows(rows)
    except (OIL.LoaderError, ReadinessError, OSError) as exc:
        return {**base, 'status': 'DATA_BLOCKED', 'code': 'ROW_VALIDATION_REFUSED',
                'detail': str(exc), 'manifest_sha256': _sha(path)}
    return {**base, 'status': 'READY', 'code': None, 'manifest_sha256': _sha(path),
            'manifest_digest': manifest['digest'], 'rows': len(rows), 'coverage': coverage,
            'availability_rule': 'normalized 5m period_end + 5 minutes'}


def _write_new(path, doc):
    path = Path(path)
    if path.exists():
        raise ReadinessError(f'{path} exists; readiness records are never overwritten')
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + '.part')
    try:
        with tmp.open('x', encoding='utf-8', newline='\n') as f:
            json.dump(doc, f, sort_keys=True, indent=1, allow_nan=False)
            f.write('\n'); f.flush(); os.fsync(f.fileno())
        os.replace(tmp, path)
    except BaseException:
        try: tmp.unlink()
        except FileNotFoundError: pass
        raise


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument('--manifest', required=True)
    ap.add_argument('--store', required=True)
    ap.add_argument('--out', required=True)
    a = ap.parse_args(argv)
    doc = check(a.manifest, a.store)
    _write_new(a.out, doc)
    print(f"{doc['status']}: {doc['code'] or 'OI rows admitted'} -> {a.out}")
    return 0 if doc['status'] == 'READY' else 2


if __name__ == '__main__':
    sys.exit(main())
