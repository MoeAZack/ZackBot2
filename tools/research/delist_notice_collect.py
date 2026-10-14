"""REX-03: collect the committed `zb-delist-notice/1` artifact for a frozen tier (CORE-10 first) from OFFICIAL Binance
announcement evidence only (Codex ruling #81 6172019060 point 3). Network: public GETs to the binance.com CMS
announcement API, nothing else (no credentials, no market data, no holdout).

  python tools/research/delist_notice_collect.py collect --tier CORE-10 --out-dir research_evidence/notices

What it does (deterministic output ordering; a re-run differs only by fetch times / live digests):
  1. lists every announcement catalog (`CATALOG_URL`) and pages each catalog newest-first (`LIST_URL`, 50 per page)
     until the catalog is exhausted or a page reaches below the window start; every page's raw bytes are hashed
     (SHA-256), never stored. A catalog whose `total` changes during its crawl is re-crawled (page drift).
  2. fetches the detail (`DETAIL_URL`) of every crawled announcement released at / after the window start whose title
     contains `delist`, extracts its text and records which tier pairs (`BTCUSDT`) / base assets it names and whether it
     mentions a perpetual. Raw bytes are hashed; only the hit lists are kept.
  3. builds ONE fetch document (`range_vwap_rev_artifact` schema) whose `raw_digest` is the SHA-256 of the canonical
     page manifest [[url, sha256], ...] and whose items are the minimal audit excerpt: every crawled item whose title
     contains `delist` or names a tier pair (released at / after the window start), plus the oldest crawled
     item below the window start (the coverage-start witness). Replayed through the frozen inclusion rule.
  4. per symbol: `notice` (the frozen title rule included an announcement), `proven_none` (every catalog covered, no
     included notice and no delist-titled body names the pair next to a perpetual) or `unproven` (any doubt). Unproven
     symbols are LEFT OUT of the artifact's `frozen` list, so the runtime marks their rows INCOMPLETE (notice GAP).
  5. writes `zb-delist-notice-<tier>-<YYYYMMDD>.json` (the artifact) and `....coverage.json` (the page / body-scan
     digests and the per-symbol verdicts that prove the coverage). Existing files are never overwritten.
A bot wall / captcha / non-JSON answer stops the collector (exit 2): it is reported, never bypassed."""
from __future__ import annotations

import argparse
import datetime as dt
import hashlib
import json
import os
import re
import sys
import time
import urllib.error
import urllib.request

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import range_vwap_rev_artifact as NA                                                        # noqa: E402
import range_vwap_rev_notice as NT                                                          # noqa: E402

COVERAGE_FORMAT = 'zb-delist-notice-coverage/1'
API = 'https://www.binance.com/bapi/composite/v1/public/cms/article'
CATALOG_URL = API + '/list/query?type=1&pageNo=1&pageSize=1'
LIST_URL = API + '/list/query?type=1&catalogId={cid}&pageNo={page}&pageSize={size}'
DETAIL_URL = API + '/detail/query?articleCode={code}'
PUBLIC_URL = 'https://www.binance.com/en/support/announcement/detail/{code}'
PAGE_SIZE = 50
UA = 'ZackBot-notice-collector/1 (research script; public announcements only; sequential, 1 req / 8 s)'
WINDOW_FROM_MS = 1609718400000          # 2021-01-04T00:00:00Z (Codex 6172019060 point 1, development splits)
WINDOW_TO_MS = 1790553600000            # 2026-09-28T00:00:00Z (end exclusive)
UNIVERSE = os.path.join('research_evidence', 'universe', 'static-top40-minhist365-contract-20261011.json')
TIER_KEY = {'CORE-10': 'core_10', 'CORE-20': 'core_20'}
DELAY_S = 8.0                           # owner-approved slow rate: at most one request per 8 s


TRANSPORT_RETRIES = 4


class TransportError(RuntimeError):
    """No answer at all (connection reset / timeout) after bounded retries: the run is aborted, nothing written."""


class Blocked(RuntimeError):
    """The official source refused automated access (HTTP error / 429 / bot wall / non-JSON): stop and report."""

    def __init__(self, msg, *, status=None, retry_after=None):
        super().__init__(msg)
        self.status, self.retry_after = status, retry_after


def _canon(o) -> bytes:
    return json.dumps(o, sort_keys=True, separators=(',', ':'), ensure_ascii=True).encode()


def sha(b: bytes) -> str:
    return hashlib.sha256(b).hexdigest()


def dump(o) -> bytes:
    return json.dumps(o, sort_keys=True, indent=1, ensure_ascii=True, allow_nan=False).encode('ascii') + b'\n'


def utc(ms: int) -> str:
    return dt.datetime.fromtimestamp(ms / 1000, dt.timezone.utc).strftime('%Y-%m-%dT%H:%M:%SZ')


def tier_symbols(root: str, tier: str) -> tuple[list[str], str]:
    with open(os.path.join(root, UNIVERSE), encoding='utf-8') as f:
        u = json.load(f)
    return list(u[TIER_KEY[tier]]), u['digest']


def bases(symbols) -> dict:
    return {s: s[:-4] for s in symbols if s.endswith('USDT')}


def tokens(text: str, words) -> list[str]:
    t = NT.norm(text)
    return sorted(w for w in words if re.search(r'(?<![a-z0-9])' + re.escape(w.casefold()) + r'(?![a-z0-9])', t))


def body_text(detail: dict) -> str:
    """Plain text of an announcement detail: every text node of its `body` JSON tree (falls back to stripped HTML)."""
    raw = detail.get('body') or ''
    out = []

    def walk(n):
        if isinstance(n, dict):
            if n.get('node') == 'text' and isinstance(n.get('text'), str):
                out.append(n['text'])
            for c in n.get('child') or []:
                walk(c)
        elif isinstance(n, list):
            for c in n:
                walk(c)
    try:
        walk(json.loads(raw))
    except (ValueError, TypeError):
        out = [re.sub(r'<[^>]+>', ' ', raw)]
    return ' '.join(out)


def scan(title: str, text: str, symbols) -> dict:
    t = NT.norm(title + ' ' + text)
    return {'pair_hits': tokens(t, symbols), 'base_hits': tokens(t, sorted(set(bases(symbols).values()))),
            'perpetual': 'perpetual' in t}


def verdicts(symbols, reg_entries, scans, catalogs, *, scan_failures: int) -> dict:
    """Per symbol: 'notice' | 'proven_none' | 'unproven' (pure; unit-tested)."""
    covered = bool(catalogs) and all(c['complete'] or c['reached_below_window'] for c in catalogs)
    out = {}
    for s in symbols:
        mine = sorted({e['code'] for e in reg_entries if e['symbol'] == s})
        unexplained = sorted(x['code'] for x in scans if s in x['pair_hits'] and x['perpetual'] and x['code'] not in mine)
        if not covered or scan_failures:
            v, why = 'unproven', 'catalog coverage incomplete' if not covered else f'{scan_failures} body fetch failures'
        elif unexplained:
            v, why = 'unproven', 'a delist-titled announcement body names the pair next to a perpetual but the frozen ' \
                                 'title rule did not include it: ' + ','.join(unexplained)
        elif mine:
            v, why = 'notice', 'included by the frozen title rule'
        else:
            v, why = 'proven_none', 'every catalog covered; no included notice; no delist-titled body names the pair ' \
                                    'next to a perpetual'
        out[s] = {'status': v, 'reason': why, 'notice_codes': mine, 'body_pair_hits': sorted(
            x['code'] for x in scans if s in x['pair_hits'])}
    return out


EFFECTIVE_RE = re.compile(r'(\d{4}-\d{2}-\d{2})\s+(\d{2}:\d{2})\s*\(UTC\)')


def effective_ms(text: str, published_at: int) -> int | None:
    """The first 'YYYY-MM-DD HH:MM (UTC)' at / after the announcement in its text (the stated delist time)."""
    for d, h in EFFECTIVE_RE.findall(text):
        ms = int(dt.datetime.strptime(d + ' ' + h, '%Y-%m-%d %H:%M').replace(tzinfo=dt.timezone.utc).timestamp() * 1000)
        if ms >= published_at:
            return ms
    return None


class Client:
    """Sequential, fixed-rate GETs to the official API (at most one request per `delay` seconds). With `cache_dir`
    (a LOCAL, uncommitted scratch dir) every validated raw response is kept with its fetch time, and a resumed run
    reads it from there instead of re-requesting it. Any server refusal (HTTP error / 429 / bot wall) stops the run;
    a 429's Retry-After is reported and recorded, never worked around."""

    def __init__(self, delay=DELAY_S, cache_dir=None):
        self.delay, self.cache_dir = delay, cache_dir
        self.requests = self.cache_hits = 0
        self._last = 0.0
        if cache_dir:
            os.makedirs(cache_dir, exist_ok=True)

    def _cpath(self, url):
        return os.path.join(self.cache_dir, sha(url.encode()))

    def _parse(self, url, raw, ctype):
        if 'json' not in ctype:
            raise Blocked(f'{url}: content-type {ctype!r} (bot wall / captcha? stopping; not bypassed)')
        try:
            doc = json.loads(raw)
        except ValueError:
            raise Blocked(f'{url}: non-JSON answer (bot wall / captcha? stopping; not bypassed)')
        if doc.get('code') != '000000' or not isinstance(doc.get('data'), dict):
            raise Blocked(f'{url}: API error {doc.get("code")!r} {doc.get("message")!r}')
        return doc['data']

    def get(self, url: str) -> tuple[int, bytes, dict]:
        if not url.startswith(API):
            raise ValueError(f'refusing a non-official URL: {url}')
        if self.cache_dir and os.path.exists(self._cpath(url) + '.bin'):
            with open(self._cpath(url) + '.bin', 'rb') as f:
                raw = f.read()
            with open(self._cpath(url) + '.ts', encoding='ascii') as f:
                ts = int(f.read())
            self.cache_hits += 1
            return ts, raw, self._parse(url, raw, 'application/json')
        req = urllib.request.Request(url, headers={'User-Agent': UA, 'Accept': 'application/json'})
        for attempt in range(TRANSPORT_RETRIES + 1):
            wait = self._last + self.delay - time.time()
            if wait > 0:
                time.sleep(wait)
            self._last = time.time()
            ts = int(self._last * 1000)
            self.requests += 1
            try:
                with urllib.request.urlopen(req, timeout=30) as r:
                    raw, ctype, code = r.read(), r.headers.get('content-type', ''), r.status
                break
            except urllib.error.HTTPError as e:             # an answer from the server: never retried / bypassed
                ra = e.headers.get('Retry-After') if e.headers else None
                raise Blocked(f'{url}: HTTP {e.code} Retry-After={ra!r} (stopping; not bypassed)', status=e.code,
                              retry_after=ra)
            except (urllib.error.URLError, OSError) as e:   # transport only (TLS reset / timeout): bounded backoff
                if attempt == TRANSPORT_RETRIES:
                    raise TransportError(f'{url}: {e}')
                time.sleep(self.delay * 2 ** (attempt + 1))
        if code != 200:
            raise Blocked(f'{url}: HTTP {code} (stopping; not bypassed)', status=code)
        data = self._parse(url, raw, ctype)
        if self.requests % 25 == 0:
            print(f'.. {self.requests} requests ({self.cache_hits} cached) {utc(ts)}', file=sys.stderr, flush=True)
        if self.cache_dir:
            with open(self._cpath(url) + '.ts', 'w', encoding='ascii') as f:
                f.write(str(ts))
            with open(self._cpath(url) + '.bin', 'wb') as f:
                f.write(raw)
        return ts, raw, data


def crawl_catalog(cl: Client, cid: int, *, tries=3) -> tuple[dict, list, list]:
    for _ in range(tries):
        pages, arts, totals = [], [], set()
        for p in range(1, 10_000):
            url = LIST_URL.format(cid=cid, page=p, size=PAGE_SIZE)
            ts, raw, data = cl.get(url)
            cats = data.get('catalogs') or []
            got = cats[0]['articles'] if cats else []
            if cats:
                totals.add(cats[0]['total'])
            for a in got:
                if not (isinstance(a.get('code'), str) and isinstance(a.get('title'), str)
                        and type(a.get('releaseDate')) is int):
                    raise Blocked(f'{url}: article schema changed: {sorted(a)}')
            rds = [a['releaseDate'] for a in got]
            pages.append({'catalog_id': cid, 'page_no': p, 'url': url, 'fetch_ts': ts, 'sha256': sha(raw),
                          'n': len(got), 'newest': max(rds) if rds else None, 'oldest': min(rds) if rds else None})
            arts += [{'code': a['code'], 'title': a['title'], 'releaseDate': a['releaseDate'], 'catalog_id': cid}
                     for a in got]
            if not got or min(rds) < WINDOW_FROM_MS:
                break
        if len(totals) <= 1:
            break
    codes = [a['code'] for a in arts]
    total = totals.pop() if len(totals) == 1 else None
    complete = total is not None and pages[-1]['n'] == 0 and len(set(codes)) == total
    below = any(a['releaseDate'] < WINDOW_FROM_MS for a in arts)
    cat = {'catalog_id': cid, 'total': total, 'pages': len(pages), 'articles': len(set(codes)),
           'oldest': min((a['releaseDate'] for a in arts), default=None), 'complete': complete,
           'reached_below_window': below, 'stable_total': total is not None}
    return cat, pages, arts


def collect(root: str, tier: str, out_dir: str, *, cl: Client | None = None) -> tuple[str, str]:
    cl = cl or Client()
    symbols, universe_digest = tier_symbols(root, tier)
    start_ms = int(time.time() * 1000)
    ts, raw, data = cl.get(CATALOG_URL)
    cat_names = {c['catalogId']: c['catalogName'] for c in data['catalogs']}
    catalogs, pages, arts = [], [], {}
    for cid in sorted(cat_names):
        cat, pg, aa = crawl_catalog(cl, cid)
        cat['name'] = cat_names[cid]
        print(f'catalog {cid} {cat_names[cid]!r}: {cat["pages"]} pages, {cat["articles"]} articles', file=sys.stderr)
        catalogs.append(cat)
        pages += pg
        for a in aa:
            arts.setdefault(a['code'], a)
    words = list(symbols)                              # pair tokens: what the frozen inclusion rule matches on
    keep = sorted((a for a in arts.values() if WINDOW_FROM_MS <= a['releaseDate'] < WINDOW_TO_MS and
                   ('delist' in NT.norm(a['title']) or tokens(a['title'], words))),
                  key=lambda a: (-a['releaseDate'], a['code']))
    below = [a for a in arts.values() if a['releaseDate'] < WINDOW_FROM_MS]
    witness = max(below, key=lambda a: (a['releaseDate'], a['code'])) if below else None
    items = [{'code': a['code'], 'title': a['title'], 'releaseDate': a['releaseDate'],
              'url': PUBLIC_URL.format(code=a['code'])} for a in keep + ([witness] if witness else [])]
    scans, failures, texts = [], 0, {}
    for a in sorted((a for a in keep if 'delist' in NT.norm(a['title'])), key=lambda a: (a['releaseDate'], a['code'])):
        try:
            dts, draw, det = cl.get(DETAIL_URL.format(code=a['code']))
        except (Blocked, TransportError):
            raise
        except Exception:                                               # noqa: BLE001 - counted, makes all unproven
            failures += 1
            continue
        text = body_text(det)
        texts[a['code']] = text
        scans.append({'code': a['code'], 'title': a['title'], 'releaseDate': a['releaseDate'],
                      'url': DETAIL_URL.format(code=a['code']), 'fetch_ts': dts, 'sha256': sha(draw),
                      **scan(a['title'], text, symbols)})
    manifest = [[p['url'], p['sha256']] for p in sorted(pages, key=lambda p: (p['catalog_id'], p['page_no']))]
    fetch = {'fetch_ts': start_ms, 'ok': True, 'schema': NT.SCHEMA, 'raw_digest': sha(_canon(manifest)),
             'items': items}
    probe = NT.Register(symbols)
    probe.ingest(fetch)
    v = verdicts(symbols, probe.entries, scans, catalogs, scan_failures=failures)
    frozen = [s for s in symbols if v[s]['status'] != 'unproven']
    effective = {}
    reg = NT.Register(frozen)
    reg.ingest(fetch)
    for e in reg.entries:
        ms = effective_ms(texts.get(e['code'], ''), e['releaseDate'])
        if ms is None:                                                  # no stated delist time: cannot prove it
            v[e['symbol']].update(status='unproven', reason='included notice without a parseable effective time')
        else:
            effective[e['code']] = ms
    frozen = [s for s in symbols if v[s]['status'] != 'unproven']
    if not frozen:
        raise SystemExit('no symbol has proven coverage; nothing to commit')
    art = NA.build(frozen, [fetch], effective, covers_from_ms=WINDOW_FROM_MS)
    NA.validate(art, expect_digest=art['digest'])
    cov = {'format': COVERAGE_FORMAT, 'artifact_digest': art['digest'], 'tier': tier, 'symbols': symbols,
           'universe': {'path': UNIVERSE.replace(os.sep, '/'), 'digest': universe_digest},
           'window': {'from_ms': WINDOW_FROM_MS, 'to_ms_exclusive': WINDOW_TO_MS,
                      'from_utc': utc(WINDOW_FROM_MS), 'to_utc': utc(WINDOW_TO_MS)},
           'endpoints': {'catalogs': CATALOG_URL, 'list': LIST_URL, 'detail': DETAIL_URL, 'public': PUBLIC_URL},
           'collected_utc': utc(start_ms), 'collector': 'tools/research/delist_notice_collect.py',
           'catalog_probe': {'fetch_ts': ts, 'sha256': sha(raw)},
           'catalogs': catalogs, 'pages': sorted(pages, key=lambda p: (p['catalog_id'], p['page_no'])),
           'page_manifest_digest': fetch['raw_digest'], 'body_scans': scans, 'body_scan_failures': failures,
           'verdicts': v, 'frozen': frozen}
    cov['digest'] = sha(_canon(cov))
    os.makedirs(out_dir, exist_ok=True)
    stem = f'zb-delist-notice-{tier.lower()}-{dt.datetime.fromtimestamp(start_ms / 1000, dt.timezone.utc):%Y%m%d}'
    paths = (os.path.join(out_dir, stem + '.json'), os.path.join(out_dir, stem + '.coverage.json'))
    for p in paths:
        if os.path.exists(p):
            raise SystemExit(f'{p} exists; committed evidence is never overwritten')
    for p, d in zip(paths, (art, cov)):
        with open(p, 'wb') as f:
            f.write(dump(d))
    return paths


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split('\n')[0])
    sub = ap.add_subparsers(dest='cmd', required=True)
    c = sub.add_parser('collect')
    c.add_argument('--tier', default='CORE-10', choices=sorted(TIER_KEY))
    c.add_argument('--out-dir', default=os.path.join('research_evidence', 'notices'))
    c.add_argument('--root', default='.')
    c.add_argument('--cache-dir', default=os.path.join(os.environ.get('TEMP', '/tmp'), 'zb_notice_cache'),
                   help='LOCAL uncommitted raw-response cache (resume without re-requesting)')
    sub.add_parser('probe', help='ONE request to the catalog list (cool-down check)')
    a = ap.parse_args(argv)
    if a.cmd == 'probe':
        try:
            ts, raw, data = Client(delay=0).get(CATALOG_URL)
        except Blocked as e:
            print(f'BLOCKED: {e}', file=sys.stderr)
            return 2
        except TransportError as e:
            print(f'TRANSPORT: {e}', file=sys.stderr)
            return 3
        print('OK', utc(ts), sha(raw), [(c['catalogId'], c['total']) for c in data['catalogs']])
        return 0
    cl = Client(cache_dir=a.cache_dir)
    t0 = time.time()
    try:
        paths = collect(a.root, a.tier, a.out_dir, cl=cl)
    except Blocked as e:
        print(f'BLOCKED: {e} | requests={cl.requests} cache_hits={cl.cache_hits} {time.time() - t0:.0f}s', file=sys.stderr)
        return 2
    except TransportError as e:
        print(f'TRANSPORT: {e} | requests={cl.requests} cache_hits={cl.cache_hits}', file=sys.stderr)
        return 3
    print(f'requests={cl.requests} cache_hits={cl.cache_hits} duration={time.time() - t0:.0f}s')
    for p in paths:
        with open(p, 'rb') as f:
            print(p, sha(f.read()))
    return 0


if __name__ == '__main__':
    sys.exit(main())
