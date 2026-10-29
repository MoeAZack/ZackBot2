"""OI-DATA-01 step 2: manifested, checksum-verified fetcher for Binance UM daily `metrics` zips (open-interest history).
Codex authorization: issue comment 6458048381 (code prepared now; the BULK DOWNLOAD stays deferred until Integration
releases the heavy resource lane).

Source: https://data.binance.vision/data/futures/um/daily/metrics/<SYM>/<SYM>-metrics-YYYY-MM-DD.zip + sibling
`.CHECKSUM` (sha256 text). There is no monthly prefix. Listing, key/date parsing and the cutoff check are REUSED from
`oi_metrics_probe`; URL guard, `.CHECKSUM` parsing, stop statuses and the redirect guard from `binance_archive_fill`;
the canonical JSON / digest convention from `manifest`.

Rules:
  * any `--end` on/after the sealed cutoff (2026-09-28T00:00Z) is refused BEFORE any network call, and a key dated
    on/after the cutoff is never fetched even when it is listed;
  * per key: GET the `.CHECKSUM`, then stream the zip to `<file>.part` while hashing (1 MiB chunks, low memory), verify
    sha256 and only then `os.replace` it into place (atomic); the `.CHECKSUM` is kept beside it, also atomically;
  * a mismatch fails CLOSED: the part file is deleted, nothing lands, the manifest lists it under `failures`, exit 1;
  * resumable: a zip already present whose bytes hash to its kept `.CHECKSUM` is skipped with no request;
  * bounded retries with exponential backoff on network errors / 5xx; HTTP 429 / 418 / 403, an HTML body or an
    off-archive redirect STOPS the run (exit 2); requests share one pacer (`--min-gap`) over `--workers` (default 2);
  * every ABSENT archive day inside [first listed, last listed before the cutoff] is recorded per symbol in
    `missing_days` (never forward-filled, interpolated or synthesized); days the listing does not reach inside the
    REQUIRED interval [--start or first listed, --end + 1 day) are recorded as `leading_gap` / `trailing_gap`;
    symbols are grouped `strict_complete` / `conditional_with_holes` / `short_history` (Codex rulings 6458228546,
    6489311350), in the plan and manifest. `strict_complete` = the ENTIRE required interval is covered; the
    longest-clean-run probe only screens. Loaders must gate on `covers(cov, start, end_exclusive)`, never on
    `missing_days == []` alone;
  * the zip is never opened: no CSV row or series value is read here (the PIT loader is a later ticket);
  * a real (authoritative) run needs explicit `--start` AND `--end`, refused before any network call otherwise;
    its manifest says `mode: bulk`, `authoritative: true`;
  * `--dry-run`: listing GETs only, prints the plan (files per symbol, listed bytes); no `.zip` / `.CHECKSUM` GET and
    nothing written. It may use default bounds and is labelled `mode: screening`, `authoritative: false`.

Usage (repo root):
  py -3.14 tools/research/oi_metrics_fetch.py --dry-run --symbols BTCUSDT,ETHUSDT
  py -3.14 tools/research/oi_metrics_fetch.py [--dest DIR] [--start YYYY-MM-DD] [--end YYYY-MM-DD]
"""
from __future__ import annotations

import argparse
import datetime as dt
import hashlib
import json
import os
import sys
import threading
import time
import urllib.parse
from concurrent.futures import ThreadPoolExecutor

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import binance_archive_fill as BAF                                                          # noqa: E402
import manifest as M                                                                        # noqa: E402
import oi_metrics_probe as P                                                                # noqa: E402

REPO = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
UNIVERSE = os.path.join(REPO, 'research_evidence', 'universe', 'static-top40-minhist365-contract-20261011.json')
DEFAULT_DEST = 'C:/Dev/ZackBot2_data/binance_um_metrics_v1'
MANIFEST_NAME = 'oi_metrics_manifest.json'
FORMAT = 'zb-oi-metrics-fetch/1'
LOADER_VERSION = 'zb-oi-metrics-raw-zip/1'          # raw verified zips only; no row loader exists yet
AVAILABLE_RULE = 'UNSET: raw zips only, no rows read; the metrics loader ticket must fix the PIT rule'
SOURCE_CLASS = 'archive-verified'
DATA_ROOT = 'binance_um_metrics'
BASE = 'https://data.binance.vision/'
CUTOFF = P.CUTOFF                                   # 2026-09-28, end-exclusive
CHUNK = 1 << 20
MAX_CHECKSUM_BYTES = 4096
USER_AGENT = 'ZackBot2-research-oi-metrics-fetch/1 (+bounded, paced)'


class FetchError(ValueError):
    pass


class Retryable(Exception):
    pass


# ------------------------------------------------------------------ plan (listing only)
def key_for(sym: str, d: dt.date) -> str:
    return f'{P.DAILY_PREFIX}{sym}/{sym}-metrics-{d.isoformat()}.zip'


def rel_path(key: str) -> str:
    """Store-relative POSIX path: the key below `data/futures/um/` (daily/metrics/SYM/SYM-metrics-D.zip)."""
    pre = 'data/futures/um/'
    if not key.startswith(pre) or any(p in ('', '.', '..') for p in key[len(pre):].split('/')):
        raise FetchError(f'bad archive key {key}')
    return key[len(pre):]


def local_path(dest: str, key: str) -> str:
    root = os.path.abspath(dest)                    # rel_path() already bars '', '.', '..' segments
    out = os.path.abspath(os.path.join(root, *rel_path(key).split('/')))
    try:
        inside = os.path.commonpath([root, out]) == root
    except ValueError:
        inside = False
    if not inside:
        raise FetchError(f'{key} escapes {dest}')
    return out


def list_with_sizes(symbol: str, list_fetch=None) -> dict:
    """{key: size or None} of the listed daily metrics zips of `symbol` (probe pagination; sizes read off the pages)."""
    pages, list_fetch = [], list_fetch or P.http_get        # resolved at call time (patchable)

    def cap(url):
        b = list_fetch(url)
        pages.append(b)
        return b
    keys, _ = P.list_prefix(f'{P.DAILY_PREFIX}{symbol}/', cap)
    sizes = {}
    for b in pages:
        root = P.ET.fromstring(b)
        for c in root.findall(f'{P._NS}Contents'):
            s = c.findtext(f'{P._NS}Size')
            sizes[c.findtext(f'{P._NS}Key')] = int(s) if s and s.strip().isdigit() else None
    return {k: sizes.get(k) for k in keys}


def _gap(a: dt.date, b: dt.date):
    """An explicit uncovered stretch [a, b), or None when it is empty."""
    return {'start': a.isoformat(), 'end_exclusive': b.isoformat(), 'days': (b - a).days} if a < b else None


def boundary_gaps(listed_first, listed_last, required_start, required_end_exclusive: dt.date):
    """(leading_gap, trailing_gap) of the listing [listed_first, listed_last] against the required interval
    [required_start, required_end_exclusive) (Codex ruling 6489311350). An empty listing leaves the whole required
    interval uncovered (`days` None when no explicit start was requested)."""
    if listed_first is None or listed_last is None:
        if required_start is None:
            return ({'start': None, 'end_exclusive': required_end_exclusive.isoformat(), 'days': None}, None)
        return (_gap(required_start, required_end_exclusive), None)
    lf, ll = dt.date.fromisoformat(listed_first), dt.date.fromisoformat(listed_last)
    rs = required_start if required_start is not None else lf
    lead = _gap(rs, min(lf, required_end_exclusive))
    trail = _gap(max(ll + dt.timedelta(days=1), rs), required_end_exclusive)
    return lead, trail


def coverage(symbol: str, keys, required_start=None, required_end_exclusive: dt.date = CUTOFF) -> dict:
    """Coverage of the pre-cutoff listing against the REQUIRED interval [required_start, required_end_exclusive)
    (`required_start` None = the first listed day, i.e. the CLI default). Every absent day inside
    [first listed, last listed] is listed explicitly (never filled); days the listing does not reach at either end are
    recorded as `leading_gap` / `trailing_gap`. `missing_days` (whole listing) is kept for provenance;
    `missing_required_days` (inside the required interval only) drives the group (Codex rulings 6458228546,
    6489311350, 6489428240):
      strict_complete        the ENTIRE required interval is covered: no missing required day, no leading and no
                             trailing gap, and >= GATE_MONTHS of listed history (holes outside the window excused);
      conditional_with_holes a missing required day and/or a boundary gap, with >= GATE_MONTHS of history;
      short_history          everything else (incl. no files).
    A hole wholly outside the required interval does not fail that interval.
    The longest-clean-run / excused-holes runs are SCREENING aids only and never grant strict_complete on their own.
    No file or value is ever synthesized for a missing day."""
    if required_end_exclusive > CUTOFF:
        raise FetchError(f'required_end_exclusive {required_end_exclusive} is after the cutoff {CUTOFF}')
    if required_start is not None and required_start >= required_end_exclusive:
        raise FetchError('required_start must be before required_end_exclusive')
    summ = P.summarize(symbol, keys, CUTOFF)
    missing = [(dt.date.fromisoformat(a) + dt.timedelta(days=i)).isoformat()
               for a, _, n in summ['gaps'] for i in range(n)]
    dates = [d for d in (P.parse_key(k, symbol) for k in keys) if d is not None and d < CUTOFF]
    excused, _ = P.month_runs(set(dates) | {dt.date.fromisoformat(m) for m in missing}, CUTOFF)
    best = summ['longest_complete_month_run']
    lead, trail = boundary_gaps(summ['first'], summ['last'], required_start, required_end_exclusive)
    lo = (required_start or dt.date.min).isoformat()
    missing_req = [m for m in missing if lo <= m < required_end_exclusive.isoformat()]
    history_ok = summ['first'] is not None and excused['months'] >= P.GATE_MONTHS
    if not missing_req and lead is None and trail is None and history_ok:
        group = 'strict_complete'
    elif (missing_req or lead or trail) and history_ok:
        group = 'conditional_with_holes'
    else:
        group = 'short_history'
    return {'group': group, 'missing_days': missing, 'missing_required_days': missing_req,
            'first': summ['first'], 'last': summ['last'],
            'required_start': required_start.isoformat() if required_start else summ['first'],
            'required_end_exclusive': required_end_exclusive.isoformat(),
            'leading_gap': lead, 'trailing_gap': trail,
            'longest_complete_month_run': best, 'month_run_if_holes_excused': excused}


def covers(cov: dict, required_start: dt.date, required_end_exclusive: dt.date) -> bool:
    """Loader availability gate: True only when the coverage record `cov` (a plan `per_symbol` / manifest `coverage`
    entry) has every day of [required_start, required_end_exclusive) listed. `missing_days == []` alone is NOT
    sufficient: it only describes holes BETWEEN the first and last listed day, so the listed boundaries are checked
    against the required range too. Fails closed on an incomplete record. `required_end_exclusive` may EQUAL the
    sealed cutoff (2026-09-28, i.e. last day 2026-09-27); a range extending past the cutoff is refused."""
    if required_end_exclusive > CUTOFF:
        raise FetchError(f'required_end_exclusive {required_end_exclusive} is past the cutoff {CUTOFF}: refused')
    if required_start >= required_end_exclusive:
        raise FetchError('required_start must be before required_end_exclusive')
    try:
        lf, ll, missing = cov['listed_first'], cov['listed_last'], cov['missing_days']
    except (KeyError, TypeError):
        return False
    if lf is None or ll is None or missing is None:
        return False
    if (dt.date.fromisoformat(lf) > required_start
            or dt.date.fromisoformat(ll) < required_end_exclusive - dt.timedelta(days=1)):
        return False
    return not any(required_start <= dt.date.fromisoformat(m) < required_end_exclusive for m in missing)


GROUPS = ('strict_complete', 'conditional_with_holes', 'short_history')


def groups(per_symbol: dict) -> dict:
    return {g: sorted(s for s, v in per_symbol.items() if v['group'] == g) for g in GROUPS}


def plan(symbols, start, end: dt.date, list_fetch=None) -> dict:
    """Items to fetch, per symbol, from the listing: dates in [start, end] and strictly before the cutoff."""
    P.check_end(end, CUTOFF)                        # refuse before any network call
    items, per_symbol = [], {}
    for s in symbols:
        listed = list_with_sizes(s, list_fetch)
        n_after = 0
        rows = []
        for k, size in listed.items():
            d = P.parse_key(k, s)
            if d is None:
                continue
            if d >= CUTOFF:
                n_after += 1
                continue
            if d > end or (start and d < start):
                continue
            rows.append({'symbol': s, 'date': d.isoformat(), 'key': k, 'listed_bytes': size})
        rows.sort(key=lambda r: r['date'])
        known = [r['listed_bytes'] for r in rows]
        cov = coverage(s, listed, start, min(end + dt.timedelta(days=1), CUTOFF))
        per_symbol[s] = {'group': cov['group'], 'missing_days': cov['missing_days'],
                         'missing_required_days': cov['missing_required_days'],
                         'listed_first': cov['first'], 'listed_last': cov['last'],
                         'required_start': cov['required_start'],
                         'required_end_exclusive': cov['required_end_exclusive'],
                         'leading_gap': cov['leading_gap'], 'trailing_gap': cov['trailing_gap'],
                         'longest_complete_month_run': cov['longest_complete_month_run'],
                         'month_run_if_holes_excused': cov['month_run_if_holes_excused'], 'files': len(rows), 'first': rows[0]['date'] if rows else None,
                         'last': rows[-1]['date'] if rows else None,
                         'listed_bytes': sum(known) if rows and None not in known else None,
                         'listed_on_or_after_cutoff_skipped': n_after}
        items += rows
    known = [r['listed_bytes'] for r in items]
    return {'items': items, 'per_symbol': per_symbol, 'files': len(items), 'groups': groups(per_symbol),
            'listed_bytes': sum(known) if None not in known else None,
            'mode': 'screening', 'authoritative': False}           # a listing-only plan never certifies a window


# ------------------------------------------------------------------ network (injectable; tests replace http_open)
class _Status:
    def __init__(self, status, url):
        self.status, self._url = status, url

    def geturl(self):
        return self._url

    def read(self, n=-1):
        return b''

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False


def http_open(url: str):                                     # the real network path; never used by the tests
    """A response-like object (status, read(n), geturl(), context manager). Off-archive redirects are refused before
    they are followed (reported as their target URL); HTTP errors come back as a bodyless status."""
    import urllib.error
    import urllib.request
    if not BAF.archive_url_ok(url):
        raise FetchError(f'refusing a non-archive URL {url}')
    opener = urllib.request.build_opener(BAF._redirect_handler())
    req = urllib.request.Request(url, headers={'User-Agent': USER_AGENT})
    try:
        return opener.open(req, timeout=60)
    except BAF.OffsiteRedirect as e:
        return _Status(e.code, e.newurl)
    except urllib.error.HTTPError as e:
        return _Status(e.code, e.geturl() or url)


class Fetcher:
    def __init__(self, *, opener=None, retries=3, backoff=2.0, min_gap=0.25, sleep=time.sleep, clock=time.monotonic):
        self.opener = opener or http_open
        self.retries, self.backoff, self.min_gap = retries, backoff, min_gap
        self.sleep, self.clock = sleep, clock
        self._lock, self._last = threading.Lock(), None
        self.calls = []

    def _pace(self):
        with self._lock:
            if self._last is not None and self.min_gap > 0:
                wait = self.min_gap - (self.clock() - self._last)
                if wait > 0:
                    self.sleep(wait)
            self._last = self.clock()

    def request(self, url: str, consume):
        """consume(first_chunk, resp) -> result for a 200; returns (status, result). Retries network errors / 5xx."""
        if not BAF.archive_url_ok(url):
            raise FetchError(f'refusing a non-archive URL {url}')
        err = None
        for attempt in range(self.retries + 1):
            if attempt:
                self.sleep(self.backoff * 2 ** (attempt - 1))
            self._pace()
            self.calls.append(url)
            try:
                with self.opener(url) as r:
                    status, final = r.status, r.geturl()
                    if not BAF.archive_url_ok(final):
                        raise BAF.StopRun('offsite_redirect', final, status)
                    if status in BAF.STOP_STATUS:
                        raise BAF.StopRun(BAF.STOP_STATUS[status], url, status)
                    if status >= 500:
                        raise Retryable(f'HTTP {status}')
                    if status != 200:
                        return status, None
                    first = r.read(CHUNK)
                    if first[:512].lstrip().lower().startswith((b'<!doctype html', b'<html')):
                        raise BAF.StopRun('bot_wall', url, status)
                    return status, consume(first, r)
            except (BAF.StopRun, FetchError):
                raise
            except (Retryable, OSError, EOFError, ConnectionError) as e:   # URLError/timeouts are OSErrors
                err = e
            except Exception as e:                                          # http.client.HTTPException et al.
                if type(e).__module__ != 'http.client':
                    raise
                err = e
        raise Retryable(f'{url}: {self.retries + 1} attempts failed ({err})')


def _read_small(first, r):
    buf = bytearray(first)
    while len(buf) <= MAX_CHECKSUM_BYTES:
        c = r.read(CHUNK)
        if not c:
            return bytes(buf)
        buf += c
    raise FetchError('.CHECKSUM body too large')


def _write_atomic(path: str, data: bytes) -> None:
    tmp = path + '.part'
    with open(tmp, 'wb') as f:
        f.write(data)
        f.flush()
        os.fsync(f.fileno())
    os.replace(tmp, path)


def sha_file(path: str):
    h, n = hashlib.sha256(), 0
    with open(path, 'rb') as f:
        for c in iter(lambda: f.read(CHUNK), b''):
            h.update(c)
            n += len(c)
    return h.hexdigest(), n


def local_verified(path: str, name: str):
    """(sha256, bytes) when `path` and its kept .CHECKSUM agree, else None."""
    ck = path + '.CHECKSUM'
    if not (os.path.isfile(path) and os.path.isfile(ck)):
        return None
    try:
        with open(ck, 'rb') as f:
            published = BAF.parse_checksum(f.read(MAX_CHECKSUM_BYTES + 1), name)
    except (BAF.FillError, OSError):
        return None
    got, n = sha_file(path)
    return (got, n) if got == published else None


def fetch_one(item: dict, dest: str, fetcher: Fetcher) -> dict:
    """Fetch + verify one key. Returns a file entry ({'status': 'fetched' | 'resumed'}) or a failure entry."""
    d = dt.date.fromisoformat(item['date'])
    if d >= CUTOFF:                                     # defence in depth: never reachable from plan()
        raise FetchError(f'{item["key"]}: dated on/after the cutoff {CUTOFF}')
    key, name = item['key'], item['key'].rsplit('/', 1)[1]
    path = local_path(dest, key)
    base = {'path': rel_path(key), 'symbol': item['symbol'], 'date': item['date'], 'source': BASE + key}
    have = local_verified(path, name)
    if have:
        return dict(base, sha256=have[0], bytes=have[1], status='resumed')
    st, body = fetcher.request(BASE + key + '.CHECKSUM', _read_small)
    if st != 200:
        return dict(base, reason='checksum_http', detail=f'HTTP {st}')
    try:
        published = BAF.parse_checksum(body, name)
    except BAF.FillError as e:
        return dict(base, reason='checksum_unreadable', detail=str(e))
    os.makedirs(os.path.dirname(path), exist_ok=True)
    tmp = path + '.part'

    def consume(first, r):
        h, n = hashlib.sha256(), 0
        with open(tmp, 'wb') as f:
            c = first
            while c:
                h.update(c)
                f.write(c)
                n += len(c)
                c = r.read(CHUNK)
            f.flush()
            os.fsync(f.fileno())
        return h.hexdigest(), n
    try:
        st, res = fetcher.request(BASE + key, consume)
    except BaseException:
        _rm(tmp)
        raise
    if st != 200:
        _rm(tmp)
        return dict(base, reason='zip_http', detail=f'HTTP {st}')
    got, n = res
    if got != published:
        _rm(tmp)
        return dict(base, reason='checksum_mismatch', published=published, sha256=got, bytes=n)
    if item.get('listed_bytes') is not None and item['listed_bytes'] != n:
        _rm(tmp)
        return dict(base, reason='size_mismatch', detail=f'listed {item["listed_bytes"]} got {n}')
    os.replace(tmp, path)
    _write_atomic(path + '.CHECKSUM', body)
    return dict(base, sha256=got, bytes=n, status='fetched')


def _rm(p):
    try:
        os.remove(p)
    except FileNotFoundError:
        pass


# ------------------------------------------------------------------ manifest
def build_manifest(entries, failures, *, symbols, start, end, stopped=None, per_symbol=None) -> dict:
    files = sorted(({k: e[k] for k in ('path', 'symbol', 'date', 'bytes', 'sha256', 'source')} for e in entries),
                   key=lambda e: e['path'])
    fails = sorted(failures, key=lambda e: (e['path'], e['reason']))
    doc = {'format': FORMAT, 'source_class': SOURCE_CLASS, 'loader_version': LOADER_VERSION,
           'available_rule': AVAILABLE_RULE, 'data_root': DATA_ROOT, 'series': 'metrics',
           'cutoff_exclusive': CUTOFF.isoformat(), 'start': start.isoformat() if start else None,
           'end': end.isoformat(), 'symbols': sorted(symbols), 'files': files, 'failures': fails,
           'stopped': stopped, 'complete': not fails and stopped is None,
           'total_bytes': sum(f['bytes'] for f in files)}
    per_symbol = per_symbol or {}
    doc['mode'], doc['authoritative'] = 'bulk', True
    doc['coverage'] = {s: {k: v[k] for k in ('group', 'missing_days', 'missing_required_days',
                                             'listed_first', 'listed_last',
                                             'required_start', 'required_end_exclusive', 'leading_gap',
                                             'trailing_gap', 'longest_complete_month_run',
                                             'month_run_if_holes_excused')}
                       for s, v in sorted(per_symbol.items())}
    doc['groups'] = groups(per_symbol)
    doc['gate_months'] = P.GATE_MONTHS
    doc['digest'] = hashlib.sha256(M.canonical(doc)).hexdigest()
    return doc


def run(symbols, dest: str, start, end: dt.date, *, list_fetch=None, fetcher: Fetcher = None, workers=2,
        manifest_path=None) -> dict:
    if start is None or end is None:                # authoritative output needs explicit bounds (ruling 6489428240)
        raise FetchError('an authoritative fetch/manifest run needs explicit --start and --end: refused')
    P.check_end(end, CUTOFF)                        # before any network call
    if start > end:
        raise FetchError('start after end')
    if not 1 <= workers <= 8:
        raise FetchError('workers must be 1..8')
    fetcher = fetcher or Fetcher()
    pl = plan(symbols, start, end, list_fetch)
    entries, failures, stopped = [], [], None
    with ThreadPoolExecutor(max_workers=workers) as ex:
        futs = [(it, ex.submit(fetch_one, it, dest, fetcher)) for it in pl['items']]
        for it, f in futs:
            try:
                e = f.result()
            except BAF.StopRun as s:
                stopped = stopped or {'reason': s.reason, 'url': s.url, 'status': s.status}
                for _, g in futs:
                    g.cancel()
                continue
            except Retryable as r:
                failures.append({'path': rel_path(it['key']), 'symbol': it['symbol'], 'date': it['date'],
                                 'source': BASE + it['key'], 'reason': 'retries_exhausted', 'detail': str(r)})
                continue
            except Exception:
                if stopped:                         # cancelled / aborted after a stop: already reported
                    continue
                raise
            (entries if 'status' in e else failures).append(e)
    doc = build_manifest(entries, failures, symbols=symbols, start=start, end=end, stopped=stopped,
                         per_symbol=pl['per_symbol'])
    os.makedirs(dest, exist_ok=True)
    _write_atomic(manifest_path or os.path.join(dest, MANIFEST_NAME),
                  json.dumps(doc, indent=1, sort_keys=True).encode('utf-8') + b'\n')
    return doc


def exit_code(doc: dict) -> int:
    return 2 if doc['stopped'] else (1 if doc['failures'] else 0)


def load_symbols(path: str):
    with open(path, encoding='utf-8') as f:
        return [s['symbol'] if isinstance(s, dict) else s for s in json.load(f)['symbols']]


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument('--universe', default=UNIVERSE, help='static-top40 contract JSON (its `symbols` list)')
    ap.add_argument('--symbols', default=None, help='comma list; overrides --universe')
    ap.add_argument('--dest', default=DEFAULT_DEST)
    ap.add_argument('--start', default=None, help='first date (YYYY-MM-DD); REQUIRED except with --dry-run '
                                                  '(screening default = first listed)')
    ap.add_argument('--end', default=None, help='last date, < cutoff; REQUIRED except with --dry-run '
                                                '(screening default = the day before the cutoff)')
    ap.add_argument('--workers', type=int, default=2)
    ap.add_argument('--retries', type=int, default=3)
    ap.add_argument('--min-gap', type=float, default=0.25, help='seconds between request starts (shared)')
    ap.add_argument('--dry-run', action='store_true', help='listing only: print the plan, fetch and write nothing')
    a = ap.parse_args(argv)
    if not a.dry_run and not (a.start and a.end):                     # refuse before anything else
        raise SystemExit('refused: a fetch/manifest run needs explicit --start and --end (only --dry-run screens '
                         'with defaults)')
    end = P.check_end(dt.date.fromisoformat(a.end) if a.end else CUTOFF - dt.timedelta(days=1), CUTOFF)
    start = dt.date.fromisoformat(a.start) if a.start else None
    if start and start > end:
        raise SystemExit('start after end')
    syms = [s.strip() for s in a.symbols.split(',') if s.strip()] if a.symbols else load_symbols(a.universe)
    if a.dry_run:
        pl = plan(syms, start, end)
        out = {k: v for k, v in pl.items() if k != 'items'}
        out['requests_if_fetched'] = 2 * pl['files']
        out['explicit_bounds'] = bool(a.start and a.end)
        out['note'] = 'SCREENING ONLY: not bulk-ready; groups do not certify a research window (use covers())'
        print(json.dumps(out, indent=1, sort_keys=True))
        return 0
    doc = run(syms, a.dest, start, end, workers=a.workers,
              fetcher=Fetcher(retries=a.retries, min_gap=a.min_gap))
    print(f"files {len(doc['files'])} failures {len(doc['failures'])} stopped {doc['stopped']} "
          f"bytes {doc['total_bytes']} digest {doc['digest']}")
    return exit_code(doc)


if __name__ == '__main__':
    sys.exit(main())
