"""`zb-data-manifest/1`: a versioned, hashed manifest over local candle files (DATA-01 R1; plan section 1).

A manifest pins every file a research run reads: path, symbol, interval, SHA-256, bytes, rows, first/last open time and
the point-in-time availability rule (`available_ms = open_ms + interval_ms`, closed bars only). Its digest is the SHA-256
of the canonical JSON of everything except the `digest` field. A result cites one digest; a written manifest is never
edited (`write` refuses to replace a file with different bytes).

Stdlib only, no network, data files are opened read-only. The output is deterministic: no run clock (`accessed_cairo`
is caller-supplied), files sorted by path.

Containment (Codex R1 P2): every path is a canonical forward-slash path relative to the manifest's `data_root`, a
logical store name (default `repo` = the repository checkout), never a machine-specific absolute path. Empty, `.` and
`..` segments, drive/UNC/absolute paths, backslashes and NUL are rejected, and every root and file is resolved
(symlinks included) and proven to stay under the explicitly allowed store directory before it is read.

Two loaders share the schema (RES-01 R1):
  `zb-local-csv/1`           `<SYMBOL>_<interval>.csv` with header `t,o,h,l,c,v` (the legacy survivor-only store).
  `zb-binance-vision-zip/1`  a `data.binance.vision` UM monthly mirror `um/monthly/<series>/<SYMBOL>/[<interval>/]
                             <SYMBOL>-<interval|fundingRate>-YYYY-MM.zip`, each zip beside a `.ok` sidecar holding the
                             SHA-256 published in the archive's `.CHECKSUM`. Every zip is re-hashed and must equal its
                             sidecar; it must hold exactly the one expected CSV, whose schema (column count, optional
                             Binance header, 13-digit ms times inside the file's month, interval alignment, close_time =
                             open + interval - 1, canonical plain-decimal quote volume, strictly increasing) is checked row by row.
                             Archive file entries add a `series` key (`klines` / `markPriceKlines` / `fundingRate`);
                             funding has `interval` null and first/last times are `calc_time` (a funding row is
                             available at its `calc_time`).
`data_root` is one of the logical store names in DATA_ROOTS; the archive loader requires `binance_um`, a directory
outside the repository that is always passed explicitly with `--store`.

Usage (repo root):
  python tools/research/manifest.py build --id ID --source-class legacy-unverified --root data_long [--root ...] --out FILE
  python tools/research/manifest.py build --loader zb-binance-vision-zip/1 --store DIR --data-root binance_um
      --id ID --source-class archive-verified --root um/monthly --accessed-cairo TEXT --out FILE [--workers N]
  python tools/research/manifest.py verify FILE [--store DIR] [--workers N]
  (`--store DIR --data-root NAME` select a store other than the repository checkout.)
"""
from __future__ import annotations

import argparse
import gzip
import hashlib
import json
import os
import re
import sys
import urllib.parse
import zipfile
from concurrent.futures import ProcessPoolExecutor
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
            'note', 'data_root', 'files', 'digest'}
DATA_ROOT_RE = re.compile(r'^[a-z][a-z0-9_-]*$')
FILE_KEYS = {'path', 'symbol', 'interval', 'sha256', 'bytes', 'rows', 'first_open_ms', 'last_open_ms', 'source'}
DATA_ROOTS = ('repo', 'binance_um')                         # the only logical stores a manifest may name

ARCHIVE_LOADER = 'zb-binance-vision-zip/1'
ARCHIVE_RULE = 'klines, markPriceKlines: open_ms + interval_ms; fundingRate: calc_time'
ARCHIVE_ROOT = 'binance_um'
ARCHIVE_KIND = 'binance-vision-archive'
ARCHIVE_URL = 'https://data.binance.vision/data/futures/'   # + the store-relative path (um/monthly/...)
ARCHIVE_FILE_KEYS = FILE_KEYS | {'series'}
SERIES = ('klines', 'markPriceKlines', 'fundingRate')
# Binance lists a few non-ASCII perps (e.g. CJK-named memecoins): any non-ASCII char is allowed in the symbol.
ZIP_RE = re.compile(r'^((?:[A-Z0-9]|[^\x00-\x7f])+)-(' + '|'.join(INTERVALS) + r'|fundingRate)-(\d{4})-(\d{2})\.zip$')
KLINE_HEADER = (b'open_time,open,high,low,close,volume,close_time,quote_volume,count,taker_buy_volume,'
                b'taker_buy_quote_volume,ignore')
FUNDING_HEADER = b'calc_time,funding_interval_hours,last_funding_rate'
# quote_volume is ranked with exact decimal arithmetic (Codex P2 on #51), so only the canonical plain decimal form is
# part of the archive contract: no sign, exponent, inf/nan, leading zeros or blank; <= 20 integer + 18 fraction digits.
QV_RE = re.compile(rb'^(0|[1-9][0-9]{0,19})(\.[0-9]{1,18})?$')
LOADERS = {LOADER_VERSION: (FILE_KEYS, AVAILABLE_RULE), ARCHIVE_LOADER: (ARCHIVE_FILE_KEYS, ARCHIVE_RULE)}


class ManifestError(ValueError):
    pass


def canonical(obj) -> bytes:
    return json.dumps(obj, sort_keys=True, separators=(',', ':'), ensure_ascii=True, allow_nan=False).encode('ascii')


def _is_int(x) -> bool:
    return type(x) is int                                   # bool is not an int here


def _strict_date(s) -> bool:
    try:
        return isinstance(s, str) and len(s) == 10 and datetime.strptime(s, '%Y-%m-%d').strftime('%Y-%m-%d') == s
    except ValueError:
        return False


def digest_of(manifest: dict) -> str:
    return hashlib.sha256(canonical({k: v for k, v in manifest.items() if k != 'digest'})).hexdigest()


def parse_open_ms(s: str) -> int:
    return int(datetime.strptime(s, '%Y-%m-%d %H:%M:%S').replace(tzinfo=timezone.utc).timestamp() * 1000)


def check_rel_path(p) -> str:
    """A canonical forward-slash relative path: no empty/`.`/`..` segment, no drive/UNC/absolute form, no backslash, no NUL."""
    ok = (isinstance(p, str) and p and chr(92) not in p and chr(0) not in p and ':' not in p and not p.startswith('/')
          and all(seg not in ('', '.', '..') for seg in p.split('/')))
    if not ok:
        raise ManifestError(f'{p!r}: path must be a canonical forward-slash relative path inside the data store')
    return p


def contained(store: str, rel: str) -> str:
    """Resolve `rel` under `store` (symlinks followed) and prove it stays inside; return the resolved path."""
    top = os.path.realpath(store)
    full = os.path.realpath(os.path.join(top, *check_rel_path(rel).split('/')))
    if os.path.normcase(os.path.commonpath([top, full])) != os.path.normcase(top) or full == top:
        raise ManifestError(f'{rel}: resolves outside the allowed data store')
    return full


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
    try:
        opens = [parse_open_ms(ln.split(',', 1)[0]) for ln in lines[1:] if ln.strip()]
    except ValueError as e:
        raise ManifestError(f'{rel_path}: bad open time ({e})')
    if any(b <= a for a, b in zip(opens, opens[1:])):
        raise ManifestError(f'{rel_path}: bars must be strictly increasing by open time (unordered or duplicate bar)')
    return {'path': rel_path.replace(os.sep, '/'), 'symbol': m.group(1), 'interval': m.group(2),
            'sha256': hashlib.sha256(raw).hexdigest(), 'bytes': len(raw), 'rows': len(opens),
            'first_open_ms': min(opens) if opens else None, 'last_open_ms': max(opens) if opens else None,
            'source': {'kind': 'local-file', 'url': None, 'archive_checksum': None}}


def archive_url(rel: str) -> str:
    return ARCHIVE_URL + urllib.parse.quote(rel, safe='/')


def archive_meta(rel: str) -> dict:
    """Series/symbol/interval/month of a store-relative archive path; raises unless the layout is exact."""
    parts = check_rel_path(rel).split('/')
    m = ZIP_RE.match(parts[-1])
    ok = bool(m) and len(parts) >= 5 and parts[:2] == ['um', 'monthly'] and parts[2] in SERIES and parts[3] == m.group(1)
    funding = ok and parts[2] == 'fundingRate'
    if ok:
        ok = (len(parts) == 5 and m.group(2) == 'fundingRate') if funding else \
             (len(parts) == 6 and parts[4] == m.group(2) and m.group(2) in INTERVALS)
    if not ok or not 1 <= int(m.group(4)) <= 12:
        raise ManifestError(f'{rel}: not um/monthly/<series>/<SYMBOL>/[<interval>/]<SYMBOL>-<interval>-YYYY-MM.zip')
    y, mo = int(m.group(3)), int(m.group(4))
    start = int(datetime(y, mo, 1, tzinfo=timezone.utc).timestamp() * 1000)
    end = int(datetime(y + mo // 12, mo % 12 + 1, 1, tzinfo=timezone.utc).timestamp() * 1000)
    return {'series': parts[2], 'symbol': m.group(1), 'interval': None if funding else m.group(2),
            'month_start_ms': start, 'month_end_ms': end}


def parse_archive_csv(rel: str, data: bytes, meta: dict) -> list[list[bytes]]:
    """Row-by-row schema check of one archive CSV; returns the split rows (header removed)."""
    funding = meta['series'] == 'fundingRate'
    header, ncol = (FUNDING_HEADER, 3) if funding else (KLINE_HEADER, 12)
    lines = data.split(b'\n')
    if lines and lines[-1] == b'':
        lines.pop()
    lines = [ln[:-1] if ln.endswith(b'\r') else ln for ln in lines]
    if lines and lines[0] == header:
        lines = lines[1:]
    rows = [ln.split(b',') for ln in lines]
    if any(len(r) != ncol for r in rows):
        raise ManifestError(f'{rel}: a row does not have {ncol} columns (or the header is unknown)')
    try:
        t = [int(r[0]) for r in rows]
        if not funding:
            close = [int(r[6]) for r in rows]
    except ValueError as e:
        raise ManifestError(f'{rel}: non-numeric time/volume field ({e})')
    lo, hi = meta['month_start_ms'], meta['month_end_ms']
    if any(not lo <= x < hi for x in t):
        raise ManifestError(f'{rel}: a row time lies outside the file month (or is not in ms)')
    if any(b <= a for a, b in zip(t, t[1:])):
        raise ManifestError(f'{rel}: rows must be strictly increasing by time (unordered or duplicate row)')
    if not funding:
        step = INTERVALS[meta['interval']]
        if any(x % step for x in t):
            raise ManifestError(f'{rel}: an open time is not aligned to {meta["interval"]}')
        if any(c != x + step - 1 for x, c in zip(t, close)):
            raise ManifestError(f'{rel}: close_time != open_time + interval - 1')
        bad = next((r[7] for r in rows if not QV_RE.match(r[7])), None)
        if bad is not None:                                     # the PIT universe sums it exactly (Decimal)
            raise ManifestError(f'{rel}: quote_volume {bad!r} is not a canonical non-negative bounded decimal')
    return rows


def read_archive(abs_path: str, rel_path: str) -> tuple[bytes, str, bytes]:
    """(zip bytes, published sidecar checksum, CSV bytes); refuses a hash mismatch or an unexpected member."""
    with open(abs_path, 'rb') as f:
        raw = f.read()
    try:
        with open(abs_path + '.ok', 'rb') as f:
            published = f.read().strip().decode('ascii')
    except (OSError, UnicodeDecodeError):
        raise ManifestError(f'{rel_path}: missing or unreadable .ok checksum sidecar')
    ours = hashlib.sha256(raw).hexdigest()
    if not HEX64.match(published) or published != ours:
        raise ManifestError(f'{rel_path}: SHA-256 {ours} != published archive checksum {published!r}')
    csv_name = os.path.basename(rel_path)[:-4] + '.csv'
    try:
        with zipfile.ZipFile(abs_path) as z:
            names = z.namelist()
            if names != [csv_name]:
                raise ManifestError(f'{rel_path}: zip must hold exactly {csv_name}, holds {names}')
            return raw, published, z.read(csv_name)
    except zipfile.BadZipFile as e:
        raise ManifestError(f'{rel_path}: bad zip ({e})')


def scan_archive(abs_path: str, rel_path: str) -> dict:
    meta = archive_meta(rel_path)
    raw, published, data = read_archive(abs_path, rel_path)
    t = [int(r[0]) for r in parse_archive_csv(rel_path, data, meta)]
    return {'path': rel_path, 'series': meta['series'], 'symbol': meta['symbol'], 'interval': meta['interval'],
            'sha256': published, 'bytes': len(raw), 'rows': len(t),
            'first_open_ms': t[0] if t else None, 'last_open_ms': t[-1] if t else None,
            'source': {'kind': ARCHIVE_KIND, 'url': archive_url(rel_path), 'archive_checksum': published}}


def _scan_job(job):
    loader, abs_path, rel = job
    return (scan_archive if loader == ARCHIVE_LOADER else scan_file)(abs_path, rel)


def _scan_all(jobs, workers: int) -> list[dict]:
    if workers <= 1 or len(jobs) < 2:
        return [_scan_job(j) for j in jobs]
    with ProcessPoolExecutor(max_workers=workers) as ex:
        return list(ex.map(_scan_job, jobs, chunksize=32))


def build(roots, *, manifest_id: str, source_class: str, base: str = REPO, data_root: str = 'repo',
          survivor_only: bool = True, accessed_cairo: str | None = None, note: str = '',
          loader: str = LOADER_VERSION, workers: int = 1) -> dict:
    """`base` is the allowed data-store directory; `data_root` its logical name recorded in the manifest."""
    if loader not in LOADERS:
        raise ManifestError(f'loader must be one of {sorted(LOADERS)}')
    pick = (lambda n: n.endswith('.zip')) if loader == ARCHIVE_LOADER else NAME_RE.match
    jobs = []
    for root in roots:
        top = contained(base, root)
        if not os.path.isdir(top):
            raise ManifestError(f'{root}: not a directory under the data store')
        for d, dirs, names in os.walk(top):
            dirs.sort()
            for name in sorted(names):
                if pick(name):
                    rel = os.path.relpath(os.path.join(d, name), os.path.realpath(base)).replace(os.sep, '/')
                    jobs.append((loader, contained(base, rel), rel))
    files = _scan_all(jobs, workers)
    m = {'format': FORMAT, 'manifest_id': manifest_id, 'source_class': source_class, 'survivor_only': survivor_only,
         'loader_version': loader, 'available_rule': LOADERS[loader][1], 'accessed_cairo': accessed_cairo,
         'note': note, 'data_root': data_root, 'files': sorted(files, key=lambda f: f['path'])}
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
    need(m['loader_version'] in LOADERS, f'loader_version must be one of {sorted(LOADERS)}')
    file_keys, rule = LOADERS[m['loader_version']]
    archive = m['loader_version'] == ARCHIVE_LOADER
    need(m['available_rule'] == rule, f'available_rule must be {rule!r}')
    need(isinstance(m['note'], str), 'note must be a string')
    need(m['accessed_cairo'] is None or (isinstance(m['accessed_cairo'], str) and _strict_date(m['accessed_cairo'][:10])),
         'accessed_cairo must be null or a string starting with a valid YYYY-MM-DD Cairo date')
    need(isinstance(m['data_root'], str) and DATA_ROOT_RE.match(m['data_root']),
         f'data_root must be a logical store name matching {DATA_ROOT_RE.pattern} (never an absolute path)')
    need(m['data_root'] in DATA_ROOTS, f'data_root must be one of {DATA_ROOTS}')
    need(not archive or (m['data_root'] == ARCHIVE_ROOT and m['source_class'] == 'archive-verified'
                         and m['survivor_only'] is False),
         f'{ARCHIVE_LOADER} requires data_root {ARCHIVE_ROOT!r}, source_class archive-verified, survivor_only false')
    need(isinstance(m['files'], list) and m['files'], 'files must be a non-empty list')
    paths = [f.get('path') for f in m['files']]
    need(paths == sorted(set(paths)), 'file paths must be unique and sorted')
    for f in m['files']:
        p = f.get('path')
        check_rel_path(p)
        need(set(f) == file_keys, f'{p}: file keys must be exactly {sorted(file_keys)}')
        if archive:
            meta = archive_meta(p)
            need(all(f[k] == meta[k] for k in ('series', 'symbol', 'interval')),
                 f'{p}: series/symbol/interval disagree with the path')
            s = f['source']
            need(isinstance(s, dict) and s.get('kind') == ARCHIVE_KIND and s.get('url') == archive_url(p)
                 and s.get('archive_checksum') == f['sha256'],
                 f'{p}: archive source must be {ARCHIVE_KIND} at {ARCHIVE_URL}<path> with checksum == sha256')
        else:
            mm = NAME_RE.match(os.path.basename(p))
            need(mm and mm.group(1) == f['symbol'] and mm.group(2) == f['interval'],
                 f'{p}: symbol/interval disagree with name')
        need(isinstance(f['sha256'], str) and HEX64.match(f['sha256']), f'{p}: sha256 must be 64 lowercase hex')
        need(_is_int(f['rows']) and f['rows'] >= 0 and _is_int(f['bytes']) and f['bytes'] >= 0,
             f'{p}: rows/bytes must be non-negative ints')
        if f['rows']:
            need(_is_int(f['first_open_ms']) and _is_int(f['last_open_ms'])
                 and f['first_open_ms'] <= f['last_open_ms'], f'{p}: first_open_ms <= last_open_ms required')
        else:
            need(f['first_open_ms'] is None and f['last_open_ms'] is None, f'{p}: no rows means null open times')
        s = f['source']
        need(isinstance(s, dict) and set(s) == {'kind', 'url', 'archive_checksum'} and isinstance(s['kind'], str)
             and s['kind'] and (s['url'] is None or isinstance(s['url'], str))
             and (s['archive_checksum'] is None or (isinstance(s['archive_checksum'], str)
                                                    and HEX64.match(s['archive_checksum']))),
             f'{p}: source must be {{kind, url, archive_checksum}} (kind string, url null|string, checksum null|64 hex)')
    need(m['digest'] == digest_of(m), 'digest does not match the canonical content')


def verify(m: dict, base: str = REPO, workers: int = 1) -> list[str]:
    """Re-scan every file on disk; return the list of mismatches (empty = the manifest still describes the bytes).
    `base` is the allowed store for `m['data_root']`; a path resolving outside it raises before anything is read."""
    validate(m)
    out, jobs, want = [], [], []
    for f in m['files']:
        ap = contained(base, f['path'])
        if not os.path.exists(ap):
            out.append(f'{f["path"]}: missing')
            continue
        jobs.append((m['loader_version'], ap, f['path']))
        want.append(f)
    for f, now in zip(want, _scan_all(jobs, workers)):
        for k in ('sha256', 'bytes', 'rows', 'first_open_ms', 'last_open_ms'):
            if now[k] != f[k]:
                out.append(f'{f["path"]}: {k} {f[k]} != {now[k]}')
    return out


def _read_text(path: str) -> bytes:
    """The manifest's JSON bytes; a `.json.gz` file is a gzip wrapper over exactly the bytes a `.json` file holds."""
    with open(path, 'rb') as f:
        raw = f.read()
    if path.endswith('.gz'):
        try:
            return gzip.decompress(raw)
        except (OSError, EOFError) as e:
            raise ManifestError(f'{path}: not a valid gzip file ({e})')
    return raw


def write(m: dict, path: str) -> None:
    """Write once. Re-writing identical content is a no-op; different content is refused (manifests are never edited).
    A path ending `.json.gz` stores the same JSON bytes gzip-compressed (mtime 0, no file name) for large archive
    manifests; immutability is judged on the decompressed bytes, so a different zlib build cannot fake a change."""
    validate(m)
    data = json.dumps(m, sort_keys=True, indent=1, ensure_ascii=True).encode('ascii') + b'\n'
    if os.path.exists(path):
        if _read_text(path) == data:
            return
        raise ManifestError(f'{path} exists with different content; manifests are immutable, use a new manifest_id')
    with open(path, 'wb') as f:
        f.write(gzip.compress(data, compresslevel=9, mtime=0) if path.endswith('.gz') else data)


def load(path: str) -> dict:
    try:
        m = json.loads(_read_text(path).decode('utf-8'),
                       parse_constant=lambda c: (_ for _ in ()).throw(ValueError(c)))
    except ValueError as e:
        raise ManifestError(f'{path}: not strict JSON (NaN / Infinity refused): {e}')
    validate(m)
    return m


def _store_for(data_root: str, store) -> str:
    if store is None and data_root != 'repo':
        raise ManifestError(f'data_root {data_root!r} lives outside the repository: pass --store explicitly')
    return REPO if store is None else store


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
    b.add_argument('--loader', default=LOADER_VERSION, choices=sorted(LOADERS))
    for p in (b, v):
        p.add_argument('--store', help='allowed data-store directory (default: the repository checkout; '
                                       'required for any data_root other than repo)')
        p.add_argument('--workers', type=int, default=1, help='parallel scan processes')
    b.add_argument('--data-root', default='repo', help='logical name of --store recorded in the manifest')
    a = ap.parse_args(argv)
    try:
        if a.cmd == 'build':
            store = _store_for(a.data_root, a.store)
            m = build(a.root, manifest_id=a.id, source_class=a.source_class, base=store, data_root=a.data_root,
                      accessed_cairo=a.accessed_cairo, note=a.note, loader=a.loader, workers=a.workers,
                      survivor_only=a.loader != ARCHIVE_LOADER)
            write(m, a.out)
            print(m['digest'])
            return 0
        m = load(a.file)
        bad = verify(m, _store_for(m['data_root'], a.store), a.workers)
        print('\n'.join(bad) if bad else 'OK')
        return 1 if bad else 0
    except ManifestError as e:
        print(f'error: {e}', file=sys.stderr)
        return 2


if __name__ == '__main__':
    sys.exit(main())
