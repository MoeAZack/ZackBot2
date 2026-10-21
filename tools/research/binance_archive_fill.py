"""Attested hole fill from Binance's official public archive (CORE-10 bounded remedy B/C, prep tooling).

Input: a `zb-rex-gap-inventory/1` report (`rex_gap_inventory.py`; digest re-checked). Output: either a download PLAN
(default, `--plan-only`: URLs only, nothing fetched) or, with `--fetch`, a manifest ADD-ON `zb-rex-gap-fill/1`
holding only the archive rows that fall inside the reported holes, with per-file and per-row SHA-256 digests.

Rules:
  * sources: https://data.binance.vision/data/futures/um/{monthly,daily}/{klines,markPriceKlines}/SYMBOL/INTERVAL/
    SYMBOL-INTERVAL-{YYYY-MM | YYYY-MM-DD}.zip and the sibling `.zip.CHECKSUM` (sha256). A monthly zip is planned only
    for a month the attested store does not already hold (the store's own monthly file IS the one with the hole); the
    daily zips of every UTC day the hole touches are always planned;
  * every zip is verified against its `.CHECKSUM` before it is opened; a mismatch rejects the file (nothing from it is
    used) and leaves its holes `unresolved`; the zip must hold exactly the expected CSV;
  * rows are copied VERBATIM (raw line bytes, never re-formatted) and only when their open time is one of the hole's
    missing opens; each row is schema-checked (12 columns, aligned open, close_time = open + interval - 1);
  * a missing open that no conclusively checked candidate (verified zip or HTTP 404) contains is a GENUINE EXCHANGE GAP:
    reported under `genuine_gaps`, never filled, never interpolated;
  * network sits behind an injectable `fetcher(url) -> (status, bytes, final_url)`; the FINAL url after any redirect
    must be https on exactly data.binance.vision under /data/futures/um/ (else the run stops: `offsite_redirect`);
    requests are sequential and paced >= 1 s apart (injectable `sleep` / `clock`); HTTP 429 / 418 stops the run
    (rate limit / ban) and HTTP 403 or an HTML body stops it (bot wall) - the remaining holes stay `unresolved`.
This tool never writes into the store or the manifest: the add-on is a separate document for Integration to review.

Usage (repo root):
  py -3.14 tools/research/binance_archive_fill.py --report REPORT.json --out PLAN.json            # plan only (default)
  py -3.14 tools/research/binance_archive_fill.py --report REPORT.json --out ADDON.json --fetch --zips ZIPDIR
                                                                    # paced real fetch; verified zips kept in ZIPDIR
"""
from __future__ import annotations

import argparse
import contextlib
import hashlib
import io
import json
import os
import re
import sys
import time
import urllib.parse
import zipfile
from datetime import datetime, timedelta, timezone

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import rex_gap_inventory as GI                                                              # noqa: E402
import runlock as RL                                                                        # noqa: E402

REPO = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

BASE = 'https://data.binance.vision/data/futures/um'
PLAN_FORMAT = 'zb-rex-gap-fill-plan/1'
ADDON_FORMAT = 'zb-rex-gap-fill/1'
SERIES = {'klines', 'markPriceKlines'}
INTERVALS = {'4h': 4 * 3_600_000, '1h': 3_600_000}
DAY = 86_400_000
MIN_GAP_S = 1.0
HOST = 'data.binance.vision'
PATH_PREFIX = '/data/futures/um/'
# Independent of the report and of pit_run: the sealed development cutoff (end-exclusive). Nothing on / after it is
# ever planned or fetched, whatever the report claims (Codex correction on #88).
SEALED_END = '2026-09-28T00:00:00Z'
STOP_STATUS = {429: 'rate_limited', 418: 'ip_banned', 403: 'bot_wall'}
SYM_RE = re.compile(r'^[A-Z0-9]+$')
HEX64 = re.compile(r'^[0-9a-f]{64}$')
USER_AGENT = 'ZackBot2-research-archive-fill/1 (+sequential, 1 req/s)'


class FillError(ValueError):
    pass


class StopRun(Exception):
    def __init__(self, reason, url, status):
        super().__init__(reason)
        self.reason, self.url, self.status = reason, url, status


def sha(b: bytes) -> str:
    return hashlib.sha256(b).hexdigest()


def digest(doc: dict) -> str:
    return sha(GI.canon({k: v for k, v in doc.items() if k != 'digest'}))


def archive_url_ok(url) -> bool:
    """True only for https://data.binance.vision/data/futures/um/... (exact host, no userinfo, default port)."""
    try:
        u = urllib.parse.urlsplit(url)
        port = u.port
    except (ValueError, TypeError, AttributeError):
        return False
    return (u.scheme == 'https' and u.hostname == HOST and u.netloc == HOST and port is None
            and u.path.startswith(PATH_PREFIX) and '..' not in urllib.parse.unquote(u.path).split('/'))   # %2e%2e too


# ------------------------------------------------------------------ report + plan
def check_report(rep: dict) -> dict:
    try:
        GI.verify(rep)
    except GI.InventoryError as e:
        raise FillError(str(e))
    sealed = GI.S.parse_utc(SEALED_END)
    end = GI.S.parse_utc(rep['window']['end'])
    if end > sealed:
        raise FillError(f'report window end {rep["window"]["end"]} is after the sealed cutoff {SEALED_END}: refused')
    for h in rep['holes']:
        if h['series'] not in SERIES or h['interval'] not in INTERVALS or not SYM_RE.match(h['symbol']):
            raise FillError(f'unexpected hole series/interval/symbol {h["series"]}/{h["interval"]}/{h["symbol"]}')
        if (h['split_name'] not in ('train', 'walk_forward') or h['end_ms'] > end or h['end_ms'] > sealed
                or h['start_ms'] >= h['end_ms']):
            raise FillError(f'hole {hole_id(h)} lies outside the development window (holdout / calibration refused)')
    return rep


def hole_id(h: dict) -> str:
    return f'{h["symbol"]}|{h["series"]}|{h["interval"]}|{h["start_ms"]}'


def hole_opens(h: dict) -> list:
    return list(range(h['start_ms'], h['end_ms'], INTERVALS[h['interval']]))


def url_for(freq: str, series: str, symbol: str, interval: str, period: str) -> str:
    if freq not in ('monthly', 'daily') or series not in SERIES or interval not in INTERVALS:
        raise FillError(f'bad archive coordinates {freq}/{series}/{interval}')
    return f'{BASE}/{freq}/{series}/{symbol}/{interval}/{symbol}-{interval}-{period}.zip'


def _utc(ms: int) -> datetime:
    return datetime.fromtimestamp(ms / 1000, tz=timezone.utc)


def _store_months(h: dict) -> set:
    out = set()
    for p in h.get('store_files', ()):
        m = re.search(r'-(\d{4}-\d{2})\.zip$', p)
        if m and f'/{h["series"]}/{h["symbol"]}/{h["interval"]}/' in p:
            out.add(m.group(1))
    return out


def _period_end(freq: str, period: str) -> datetime:
    """Exclusive UTC end of an archive file's period (first instant it does NOT cover)."""
    if freq == 'daily':
        return datetime.strptime(period, '%Y-%m-%d').replace(tzinfo=timezone.utc) + timedelta(days=1)
    y, m = map(int, period.split('-'))
    return datetime(y + (m == 12), m % 12 + 1, 1, tzinfo=timezone.utc)


def candidates(h: dict) -> list:
    """(freq, period) zips that may hold h's missing opens. A file whose period runs past SEALED_END is never planned
    (Cowork 6301261896: the 2026-09 monthly zip holds sealed rows); the daily zips of every touched day still cover h."""
    a, b = h['start_ms'], h['end_ms']
    months = sorted({_utc(t).strftime('%Y-%m') for t in (a, b - 1)} |
                    {_utc(t).strftime('%Y-%m') for t in range(a - a % DAY, b, DAY)})
    days = [_utc(d).strftime('%Y-%m-%d') for d in range(a - a % DAY, b, DAY)]
    sealed = GI.S.parse_utc(SEALED_END)
    cands = [('monthly', m) for m in months if m not in _store_months(h)] + [('daily', d) for d in days]
    return [(f, p) for f, p in cands if int(_period_end(f, p).timestamp()) * 1000 <= sealed]


def plan(rep: dict) -> list:
    """Deterministic request list: one entry per distinct zip, with the hole ids it may fill."""
    check_report(rep)
    reqs: dict = {}
    for h in rep['holes']:
        for freq, period in candidates(h):
            u = url_for(freq, h['series'], h['symbol'], h['interval'], period)
            r = reqs.setdefault(u, {'url': u, 'checksum_url': u + '.CHECKSUM', 'freq': freq, 'series': h['series'],
                                    'symbol': h['symbol'], 'interval': h['interval'], 'period': period,
                                    'member': f'{h["symbol"]}-{h["interval"]}-{period}.csv', 'holes': []})
            r['holes'].append(hole_id(h))
    return [dict(r, holes=sorted(set(r['holes']))) for _, r in sorted(reqs.items())]


def plan_doc(rep: dict) -> dict:
    reqs = plan(rep)
    doc = {'format': PLAN_FORMAT, 'inventory_digest': rep[GI.SIG], 'manifest_digest': rep['manifest_digest'],
           'holes': len(rep['holes']), 'requests': reqs, 'fetches': 2 * len(reqs), 'min_gap_s': MIN_GAP_S}
    doc['digest'] = digest(doc)
    return doc


# ------------------------------------------------------------------ fetch (injectable, paced, stop on 429 / bot wall)
class Pacer:
    def __init__(self, fetcher, *, sleep=time.sleep, clock=time.monotonic, min_gap=MIN_GAP_S):
        if min_gap < MIN_GAP_S:
            raise FillError(f'requests must be >= {MIN_GAP_S} s apart')
        self.fetcher, self.sleep, self.clock, self.min_gap = fetcher, sleep, clock, min_gap
        self.last, self.calls = None, []

    def get(self, url: str):
        if self.last is not None:
            wait = self.min_gap - (self.clock() - self.last)
            if wait > 0:
                self.sleep(wait)
        self.last = self.clock()
        if not archive_url_ok(url):
            raise FillError(f'refusing a non-archive URL {url}')
        status, body, final = self.fetcher(url)
        self.calls.append(url)
        if not archive_url_ok(final):
            raise StopRun('offsite_redirect', final, status)
        if status in STOP_STATUS:
            raise StopRun(STOP_STATUS[status], url, status)
        if status == 200 and body[:512].lstrip().lower().startswith((b'<!doctype html', b'<html')):
            raise StopRun('bot_wall', url, status)
        return status, body


def parse_checksum(body: bytes, name: str) -> str:
    parts = body.decode('ascii', 'replace').split()
    if len(parts) < 1 or not HEX64.match(parts[0].lower()) or (len(parts) > 1 and parts[1].lstrip('*') != name):
        raise FillError(f'unreadable .CHECKSUM for {name}')
    return parts[0].lower()


def extract(raw: bytes, req: dict, wanted: set) -> list:
    """Verbatim rows of the zip's single CSV whose open time is in `wanted` (schema-checked; nothing else kept)."""
    iv = INTERVALS[req['interval']]
    with zipfile.ZipFile(io.BytesIO(raw)) as z:
        if z.namelist() != [req['member']]:
            raise FillError(f'{req["url"]}: zip must hold exactly {req["member"]}, holds {z.namelist()}')
        data = z.read(req['member'])
    out = []
    for ln in data.split(b'\n'):
        ln = ln.rstrip(b'\r')
        head = ln.split(b',', 1)[0]
        if not head.isdigit() or int(head) not in wanted:
            continue
        cols = ln.split(b',')
        t = int(head)
        if len(cols) != 12 or t % iv or not cols[6].isdigit() or int(cols[6]) != t + iv - 1:
            raise FillError(f'{req["url"]}: row at {t} fails the kline schema')
        out.append((t, ln))
    return out


def zip_path(keep_dir: str, url: str) -> str:
    """Where a verified archive zip is kept: <keep_dir>/<path under /data/futures/um/> (its `.CHECKSUM` beside it).
    The add-on loader (rex_addon) re-proves every add-on row from these exact bytes."""
    if not archive_url_ok(url):
        raise FillError(f'refusing a non-archive URL {url}')
    rel = urllib.parse.urlsplit(url).path[len(PATH_PREFIX):]
    if not rel or any(p in ('', '.', '..') for p in rel.split('/')):
        raise FillError(f'bad archive path {url}')
    root = os.path.realpath(keep_dir)
    out = os.path.realpath(os.path.join(root, *rel.split('/')))
    if os.path.commonpath([root, out]) != root:
        raise FillError(f'archive path {url} escapes {keep_dir}')
    return out


def keep_zip(keep_dir: str, url: str, raw: bytes, checksum: bytes) -> None:
    p = zip_path(keep_dir, url)
    os.makedirs(os.path.dirname(p), exist_ok=True)
    with open(p, 'wb') as f:
        f.write(raw)
    with open(p + '.CHECKSUM', 'wb') as f:
        f.write(checksum)


def fill(rep: dict, fetcher, *, sleep=time.sleep, clock=time.monotonic, min_gap=MIN_GAP_S, keep_dir=None) -> dict:
    """`keep_dir`: keep every VERIFIED zip + its .CHECKSUM there (zip_path), so the add-on loader can re-prove the
    rows from the archive bytes (required for `run_r4 rex --addon`)."""
    reqs = plan(rep)
    pacer = Pacer(fetcher, sleep=sleep, clock=clock, min_gap=min_gap)
    holes = {hole_id(h): h for h in rep['holes']}
    need = {k: set(hole_opens(h)) for k, h in holes.items()}
    got: dict = {k: {} for k in holes}
    inconclusive: set = set()                                # holes with a rejected / failed / unfetched candidate
    files, stopped = [], None
    for i, r in enumerate(reqs):
        entry = {'url': r['url'], 'freq': r['freq'], 'period': r['period'], 'holes': r['holes']}
        try:
            st, body = pacer.get(r['checksum_url'])
            ck_body = body
            if st == 404:
                files.append(dict(entry, status='archive_missing'))
                continue
            if st != 200:
                raise FillError(f'HTTP {st}')
            published = parse_checksum(body, r['url'].rsplit('/', 1)[1])
            st, raw = pacer.get(r['url'])
            if st == 404:
                files.append(dict(entry, status='archive_missing'))
                continue
            if st != 200:
                raise FillError(f'HTTP {st}')
            if sha(raw) != published:
                files.append(dict(entry, status='rejected_checksum', published=published, sha256=sha(raw)))
                inconclusive.update(r['holes'])
                continue
            wanted = set().union(*(need[k] for k in r['holes']))
            rows = extract(raw, r, wanted)
            if keep_dir is not None:
                keep_zip(keep_dir, r['url'], raw, ck_body)
            for t, ln in rows:
                for k in r['holes']:
                    if t in need[k]:
                        got[k].setdefault(t, (ln, r['url']))
            files.append(dict(entry, status='verified', sha256=published, bytes=len(raw), member=r['member'],
                              rows=len(rows), rows_digest=sha(b'\n'.join(ln for _, ln in rows))))
        except StopRun as e:
            stopped = {'reason': e.reason, 'status': e.status, 'url': e.url}
            for rr in reqs[i:]:
                inconclusive.update(rr['holes'])
            break
        except (FillError, zipfile.BadZipFile) as e:
            files.append(dict(entry, status='error', error=str(e)))
            inconclusive.update(r['holes'])
    rows, out_holes, genuine = [], [], []
    for k in sorted(holes):
        h = holes[k]
        for t in sorted(got[k]):
            ln, src = got[k][t]
            rows.append({'symbol': h['symbol'], 'series': h['series'], 'interval': h['interval'], 'open_ms': t,
                         'line': ln.decode('ascii'), 'sha256': sha(ln), 'source_url': src, 'hole': k})
        left = sorted(need[k] - set(got[k]))
        status = 'filled' if not left else ('unresolved' if k in inconclusive else
                                            ('archive_missing' if not got[k] else 'partial_archive_missing'))
        out_holes.append({'hole': k, 'reason': h['reason'], 'split': h['split'], 'status': status,
                          'filled': len(got[k]), 'missing': len(left)})
        if left and k not in inconclusive:
            iv = INTERVALS[h['interval']]
            for a, b, n in GI._runs(left, iv):
                genuine.append({'hole': k, 'symbol': h['symbol'], 'series': h['series'], 'start_ms': a, 'end_ms': b,
                                'missing': n, 'reason': h['reason'], 'split': h['split'],
                                'status': 'genuine_exchange_gap (never filled)'})
    doc = {'format': ADDON_FORMAT, 'inventory_digest': rep[GI.SIG], 'manifest_digest': rep['manifest_digest'],
           'stopped': stopped, 'files': files, 'rows': rows, 'holes': out_holes, 'genuine_gaps': genuine,
           'fetched': len(pacer.calls)}
    doc['digest'] = digest(doc)
    return doc


class OffsiteRedirect(FillError):
    def __init__(self, code, newurl):
        super().__init__(f'redirect {code} to a non-archive URL {newurl}')
        self.code, self.newurl = code, newurl


def _redirect_handler():
    import urllib.request

    class Guard(urllib.request.HTTPRedirectHandler):
        """Refuses (before following) any redirect whose target is not the https archive host."""
        def redirect_request(self, req, fp, code, msg, headers, newurl):
            if not archive_url_ok(newurl):
                raise OffsiteRedirect(code, newurl)
            return super().redirect_request(req, fp, code, msg, headers, newurl)
    return Guard()


def http_fetcher(url: str):                                  # the real network path; never used by the tests
    """(status, body, final_url). Redirects off the archive host are refused before they are followed and reported as
    their target URL, which the Pacer then rejects; the final URL of a followed redirect is returned for re-check."""
    import urllib.error
    import urllib.request
    if not archive_url_ok(url):
        raise FillError(f'refusing a non-archive URL {url}')
    opener = urllib.request.build_opener(_redirect_handler())
    req = urllib.request.Request(url, headers={'User-Agent': USER_AGENT})
    try:
        with opener.open(req, timeout=60) as resp:
            return resp.status, resp.read(), resp.geturl()
    except OffsiteRedirect as e:
        return e.code, b'', e.newurl
    except urllib.error.HTTPError as e:
        return e.code, b'', e.geturl() or url


# RUNLOCK-01: a fetch's lock identity is its whole parsed command minus where it writes (--out / --zips) and the report
# PATH, which is replaced by the report's byte digest; any identity-bearing argument added later enters automatically.
LOCK_EXCLUDED = frozenset({'out', 'zips', 'report'})


def lock_identity(a, report_bytes: bytes) -> dict:
    ident = {k: v for k, v in vars(a).items() if k not in LOCK_EXCLUDED}
    ident['report_sha256'] = hashlib.sha256(report_bytes).hexdigest()
    return ident


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split('\n')[0])
    ap.add_argument('--report', required=True)
    ap.add_argument('--out', required=True)
    g = ap.add_mutually_exclusive_group()
    g.add_argument('--plan-only', action='store_true', default=True, help='list URLs only (default)')
    g.add_argument('--fetch', action='store_true', help='paced sequential fetch from data.binance.vision')
    ap.add_argument('--zips', default=None, help='with --fetch (required): keep every verified zip + .CHECKSUM here '
                                                 '(the add-on loader re-proves each row from them)')
    a = ap.parse_args(argv)
    if a.fetch and not a.zips:
        ap.error('--fetch needs --zips DIR (the verified zips the loader re-proves rows from)')
    with open(a.report, 'rb') as f:
        raw = f.read()
    rep = check_report(json.loads(raw))
    with contextlib.ExitStack() as held:
        if a.fetch:                     # RUNLOCK-01: one live fetch per report across every worktree, before any network
            try:
                held.enter_context(RL.hold(REPO, 'archive_fill', lock_identity(a, raw),
                                           argv=sys.argv[1:] if argv is None else argv))
            except RL.RunActive as e:
                print(str(e), file=sys.stderr)
                return 5
        doc = fill(rep, http_fetcher, keep_dir=a.zips) if a.fetch else plan_doc(rep)
        with open(a.out, 'w', newline='\n') as f:
            json.dump(doc, f, sort_keys=True, indent=1)
            f.write('\n')
    if a.fetch:
        print(f'{len(doc["rows"])} row(s) filled, {len(doc["genuine_gaps"])} genuine gap(s), stopped={doc["stopped"]}'
              f' -> {a.out}')
        return 3 if doc['stopped'] else 0
    print(f'plan: {len(doc["requests"])} zip(s), {doc["fetches"]} request(s) -> {a.out}')
    return 0


if __name__ == '__main__':
    sys.exit(main())
