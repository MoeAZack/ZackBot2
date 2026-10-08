"""`zb-data-manifest/1`: a versioned, hashed manifest over local candle files (DATA-01 R1; plan section 1).

A manifest pins every file a research run reads: path, symbol, interval, SHA-256, bytes, rows, first/last open time and
the point-in-time availability rule (`available_ms = open_ms + interval_ms`, closed bars only). Its digest is the SHA-256
of the canonical JSON of everything except the `digest` field. A result cites one digest; a written manifest is never
edited (`write` refuses to replace a file with different bytes).

Stdlib only, no network, data files are opened read-only. The output is deterministic: no run clock (`accessed_cairo`
is caller-supplied), files sorted by path.

Usage (repo root):
  python tools/research/manifest.py build --id ID --source-class legacy-unverified --root data_long [--root ...] --out FILE
  python tools/research/manifest.py verify FILE
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import sys
from datetime import datetime, timezone

FORMAT = 'zb-data-manifest/1'
LOADER_VERSION = 'zb-local-csv/1'
REPO = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
MIN = 60_000
INTERVALS = {'1m': MIN, '3m': 3 * MIN, '5m': 5 * MIN, '15m': 15 * MIN, '30m': 30 * MIN, '1h': 60 * MIN,
             '2h': 120 * MIN, '4h': 240 * MIN, '6h': 360 * MIN, '8h': 480 * MIN, '12h': 720 * MIN, '1d': 1440 * MIN}
NAME_RE = re.compile(r'^([A-Z0-9]+)_(' + '|'.join(INTERVALS) + r')\.csv$')
HEADER = 't,o,h,l,c,v'
AVAILABLE_RULE = 'open_ms + interval_ms'
SOURCE_CLASSES = ('legacy-unverified', 'archive-verified', 'rest-tail', 'fixture')
HEX64 = re.compile(r'^[0-9a-f]{64}$')
TOP_KEYS = {'format', 'manifest_id', 'source_class', 'survivor_only', 'loader_version', 'available_rule', 'accessed_cairo',
            'note', 'files', 'digest'}
FILE_KEYS = {'path', 'symbol', 'interval', 'sha256', 'bytes', 'rows', 'first_open_ms', 'last_open_ms', 'source'}


class ManifestError(ValueError):
    pass


def canonical(obj) -> bytes:
    return json.dumps(obj, sort_keys=True, separators=(',', ':'), ensure_ascii=True).encode('ascii')


def digest_of(manifest: dict) -> str:
    return hashlib.sha256(canonical({k: v for k, v in manifest.items() if k != 'digest'})).hexdigest()


def parse_open_ms(s: str) -> int:
    return int(datetime.strptime(s, '%Y-%m-%d %H:%M:%S').replace(tzinfo=timezone.utc).timestamp() * 1000)


def scan_file(abs_path: str, rel_path: str) -> dict:
    name = os.path.basename(rel_path)
    m = NAME_RE.match(name)
    if not m:
        raise ManifestError(f'{rel_path}: name is not <SYMBOL>_<interval>.csv')
    with open(abs_path, 'rb') as f:
        raw = f.read()
    lines = raw.decode('utf-8').splitlines()
    if not lines or lines[0].strip() != HEADER:
        raise ManifestError(f'{rel_path}: header is not {HEADER!r}')
    opens = [parse_open_ms(ln.split(',', 1)[0]) for ln in lines[1:] if ln.strip()]
    return {'path': rel_path.replace(os.sep, '/'), 'symbol': m.group(1), 'interval': m.group(2),
            'sha256': hashlib.sha256(raw).hexdigest(), 'bytes': len(raw), 'rows': len(opens),
            'first_open_ms': min(opens) if opens else None, 'last_open_ms': max(opens) if opens else None,
            'source': {'kind': 'local-file', 'url': None, 'archive_checksum': None}}


def build(roots, *, manifest_id: str, source_class: str, base: str = REPO, survivor_only: bool = True,
          accessed_cairo: str | None = None, note: str = '') -> dict:
    files = []
    for root in roots:
        for d, dirs, names in os.walk(os.path.join(base, root)):
            dirs.sort()
            for name in sorted(names):
                if NAME_RE.match(name):
                    files.append(scan_file(os.path.join(d, name), os.path.relpath(os.path.join(d, name), base)))
    m = {'format': FORMAT, 'manifest_id': manifest_id, 'source_class': source_class, 'survivor_only': survivor_only,
         'loader_version': LOADER_VERSION, 'available_rule': AVAILABLE_RULE, 'accessed_cairo': accessed_cairo,
         'note': note, 'files': sorted(files, key=lambda f: f['path'])}
    m['digest'] = digest_of(m)
    validate(m)
    return m


def validate(m: dict) -> None:
    """Schema + digest check. Raises ManifestError naming the first problem."""
    def need(ok, msg):
        if not ok:
            raise ManifestError(msg)
    need(isinstance(m, dict) and set(m) == TOP_KEYS, f'top-level keys must be exactly {sorted(TOP_KEYS)}')
    need(m['format'] == FORMAT, f'format must be {FORMAT}')
    need(isinstance(m['manifest_id'], str) and m['manifest_id'], 'manifest_id must be a non-empty string')
    need(m['source_class'] in SOURCE_CLASSES, f'source_class must be one of {SOURCE_CLASSES}')
    need(isinstance(m['survivor_only'], bool), 'survivor_only must be a bool')
    need(m['available_rule'] == AVAILABLE_RULE, f'available_rule must be {AVAILABLE_RULE!r}')
    need(isinstance(m['files'], list) and m['files'], 'files must be a non-empty list')
    paths = [f.get('path') for f in m['files']]
    need(paths == sorted(set(paths)), 'file paths must be unique and sorted')
    for f in m['files']:
        p = f.get('path')
        need(set(f) == FILE_KEYS, f'{p}: file keys must be exactly {sorted(FILE_KEYS)}')
        mm = NAME_RE.match(os.path.basename(p))
        need(mm and mm.group(1) == f['symbol'] and mm.group(2) == f['interval'], f'{p}: symbol/interval disagree with name')
        need(isinstance(f['sha256'], str) and HEX64.match(f['sha256']), f'{p}: sha256 must be 64 lowercase hex')
        need(isinstance(f['rows'], int) and f['rows'] >= 0 and isinstance(f['bytes'], int) and f['bytes'] >= 0,
             f'{p}: rows/bytes must be non-negative ints')
        if f['rows']:
            need(isinstance(f['first_open_ms'], int) and isinstance(f['last_open_ms'], int)
                 and f['first_open_ms'] <= f['last_open_ms'], f'{p}: first_open_ms <= last_open_ms required')
    need(m['digest'] == digest_of(m), 'digest does not match the canonical content')


def verify(m: dict, base: str = REPO) -> list[str]:
    """Re-scan every file on disk; return the list of mismatches (empty = the manifest still describes the bytes)."""
    validate(m)
    out = []
    for f in m['files']:
        ap = os.path.join(base, *f['path'].split('/'))
        if not os.path.exists(ap):
            out.append(f'{f["path"]}: missing')
            continue
        now = scan_file(ap, f['path'])
        for k in ('sha256', 'bytes', 'rows', 'first_open_ms', 'last_open_ms'):
            if now[k] != f[k]:
                out.append(f'{f["path"]}: {k} {f[k]} != {now[k]}')
    return out


def write(m: dict, path: str) -> None:
    """Write once. Re-writing identical bytes is a no-op; different bytes are refused (manifests are never edited)."""
    validate(m)
    data = json.dumps(m, sort_keys=True, indent=1, ensure_ascii=True).encode('ascii') + b'\n'
    if os.path.exists(path):
        with open(path, 'rb') as f:
            if f.read() == data:
                return
        raise ManifestError(f'{path} exists with different content; manifests are immutable, use a new manifest_id')
    with open(path, 'wb') as f:
        f.write(data)


def load(path: str) -> dict:
    with open(path, encoding='utf-8') as f:
        m = json.load(f)
    validate(m)
    return m


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split('\n')[0])
    sub = ap.add_subparsers(dest='cmd', required=True)
    b = sub.add_parser('build')
    b.add_argument('--id', required=True)
    b.add_argument('--source-class', required=True, choices=SOURCE_CLASSES)
    b.add_argument('--root', action='append', required=True)
    b.add_argument('--accessed-cairo')
    b.add_argument('--note', default='')
    b.add_argument('--out', required=True)
    v = sub.add_parser('verify')
    v.add_argument('file')
    a = ap.parse_args(argv)
    try:
        if a.cmd == 'build':
            m = build(a.root, manifest_id=a.id, source_class=a.source_class, accessed_cairo=a.accessed_cairo, note=a.note)
            write(m, a.out)
            print(m['digest'])
            return 0
        bad = verify(load(a.file))
        print('\n'.join(bad) if bad else 'OK')
        return 1 if bad else 0
    except ManifestError as e:
        print(f'error: {e}', file=sys.stderr)
        return 2


if __name__ == '__main__':
    sys.exit(main())
