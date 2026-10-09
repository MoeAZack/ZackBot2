"""`zb-pit-universe/2`: the point-in-time weekly universe `pit-top40-qv30d-v2` (RES-01 R2; plan sections 1a and 8).

Input is one `zb-data-manifest/1` built by the `zb-binance-vision-zip/1` loader: its daily last-price klines
(`um/monthly/klines/<SYMBOL>/1d/`) for every historically listed USD-M USDT perpetual, delisted contracts included,
plus one `zb-instrument-classes/1` classification (`research_evidence/universe/instrument-classes-v1.json`).
Each archive is re-hashed and must match both the manifest and its published checksum before a byte is parsed.

Rules (all evaluated with information available at the ranking instant only):
  * Ranking instant: every Monday 00:00 UTC. A daily bar counts only when `available_ms = open_ms + 1d <= instant`.
  * Score: trailing 30-day quote volume = the exact decimal sum (no binary float anywhere) of `quote_volume` over the
    daily bars opening in [instant - 30d, instant - 1d]. Rank descending, ties broken by symbol; keep the top 40.
  * Books (Codex ruling on #51): the ranking is done separately per asset-class book - `crypto`, `gold-commodity`,
    `equity`, `fx` - never as one mixed list. A symbol's class comes from the versioned classification and applies
    from its `effective_from_ms`; a symbol with class `unclassified` (or before its class is effective) joins no book
    and is vetoed `unclassified` in the weekly audit. A symbol first listed on/after the classification's
    `tradfi_cutoff_ms` with no entry is refused at build time (no silent crypto default). Gold (XAUUSDT) stays in,
    in the `gold-commodity` book (owner decision).
  * Eligibility test (a), listing age: instant - listing >= 30 days. Listing = the contract's first archived daily bar;
    `listing_censored` marks contracts already trading in the store's first month (the true listing is earlier, so the
    test is conservative). Test (b), the strategy's own warm-up, is per strategy and is applied by the R3 harness.
  * Trading-at-instant test: the contract must have the daily bar that closes exactly at the instant. A contract whose
    archive simply stops drops out the first Monday after its last bar - observable then, never earlier.
  * Complete window: every one of the 30 daily bars of the scored window must exist. A missing day is never read as
    zero volume: the contract is vetoed `data-gap` that week and its missing count is listed in the week's `gaps`.
    Every internal gap of every symbol is also listed in its `missing_days` (the bound 1d gap report).
  * Delist veto: only from a timestamped announcement/status observation (`--delist-observations`), effective from its
    `observed_ms`. The archive end date (`archive_last_close_ms`) and the last traded close (`last_traded_close_ms`;
    settled contracts keep printing zero-volume bars) are hindsight, recorded for audit only; they never veto a
    ranking. A contract with zero trailing quote volume is simply unrankable (`zero-qv30d`), which is observable.
  * Renames: refused (Codex P1 on #51). A non-empty rename list fails closed until time-scoped identity with overlap
    checks and price-continuity evidence exists; every symbol is its own contract.
  * Exchange rules (tick / step / min qty / min notional) before the first genuinely observed snapshot are labelled
    `RULES-BACKFILLED` per week (`--rules-first-observed-ms`; none exists yet, so every week carries the label).

Output is deterministic (no run clock); its digest is the SHA-256 of the canonical JSON without `digest`, and a written
universe file is never edited. Stdlib only, no network. No price returns are computed here.

Usage (repo root):
  python tools/research/universe.py build --manifest research_evidence/manifests/binance-um-archive-v1.json.gz
      --classes research_evidence/universe/instrument-classes-v1.json
      --store C:/Dev/ZackBot2_data/binance_um --out research_evidence/universe/pit-top40-qv30d-v2.json [--workers N]
  python tools/research/universe.py verify FILE --manifest MANIFEST --classes CLASSES --store DIR [--workers N]
"""
from __future__ import annotations

import argparse
import decimal
import hashlib
import json
import os
import sys
from concurrent.futures import ProcessPoolExecutor
from datetime import datetime, timezone
from decimal import Decimal

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import manifest as M                                                                        # noqa: E402

FORMAT = 'zb-pit-universe/2'
UNIVERSE_ID = 'pit-top40-qv30d-v2'
CLASSES_FORMAT = 'zb-instrument-classes/1'
DAY = 86_400_000
WEEK = 7 * DAY
MONDAY_EPOCH = 4 * DAY                                      # 1970-01-05 00:00 UTC was a Monday
TOP_N = 40
LOOKBACK_DAYS = 30
MIN_AGE_DAYS = 30
RULES_BACKFILLED = 'RULES-BACKFILLED'
RULES_OBSERVED = 'RULES-OBSERVED'
VETO_AGE = 'listing-age<30d'
VETO_NOT_TRADING = 'no-bar-closing-at-instant'
VETO_DELIST = 'delist-observed'
VETO_GAP = 'data-gap'
VETO_NO_VOLUME = 'zero-qv30d'
VETO_UNCLASSIFIED = 'unclassified'
BOOKS = ('crypto', 'gold-commodity', 'equity', 'fx')        # each ranked on its own; never one mixed list
UNCLASSIFIED = 'unclassified'
CLASS_KEYS = {'format', 'classes_id', 'reviewed_cairo', 'method', 'tradfi_cutoff_ms', 'pre_cutoff_rule', 'entries',
              'digest'}
ENTRY_KEYS = {'symbol', 'class', 'subclass', 'effective_from_ms', 'basis'}
# Exact decimal arithmetic: 30 bars of <= 20 integer + 18 fraction digits never need more than 40 significant digits;
# Inexact is trapped, so any rounding step raises instead of silently changing a rank.
QV_CONTEXT = decimal.Context(prec=60, traps=[decimal.Inexact, decimal.InvalidOperation, decimal.Overflow])


class UniverseError(ValueError):
    pass


def utc_date(ms: int) -> str:
    return datetime.fromtimestamp(ms / 1000, tz=timezone.utc).strftime('%Y-%m-%d')


def date_ms(date: str) -> int:
    return int(datetime.strptime(date, '%Y-%m-%d').replace(tzinfo=timezone.utc).timestamp() * 1000)


def qv_decimal(raw: bytes | str) -> Decimal:
    """One archive quote_volume field -> exact Decimal; only the canonical bounded plain-decimal form is accepted."""
    b = raw.encode('ascii') if isinstance(raw, str) else raw
    if not M.QV_RE.match(b):
        raise UniverseError(f'quote_volume {raw!r} is not a canonical bounded decimal')
    return Decimal(b.decode('ascii'))


def qv_text(q: Decimal) -> str:
    """Canonical text of an exact sum: plain notation, no exponent, trailing fractional zeros dropped."""
    t = format(q, 'f')
    return (t.rstrip('0').rstrip('.') if '.' in t else t) or '0'


def qv_sum(values) -> Decimal:
    total = Decimal(0)
    for v in values:
        if type(v) is not Decimal:
            raise UniverseError('quote volumes must be exact Decimals (binary floats are refused)')
        total = QV_CONTEXT.add(total, v)
    return total


def _daily_job(job):
    abs_path, rel, sha = job
    raw, published, data = M.read_archive(abs_path, rel)
    if published != sha:
        raise UniverseError(f'{rel}: bytes no longer match the manifest sha256')
    rows = M.parse_archive_csv(rel, data, M.archive_meta(rel))
    return M.archive_meta(rel)['symbol'], [(int(r[0]), r[7].decode('ascii')) for r in rows]


def load_daily(manifest: dict, store: str, workers: int = 1) -> dict[str, dict[int, Decimal]]:
    """{symbol: {open_ms: quote_volume (exact Decimal)}} from the manifest's 1d last-price klines, verified."""
    M.validate(manifest)
    if manifest['loader_version'] != M.ARCHIVE_LOADER:
        raise UniverseError(f'the PIT universe needs a {M.ARCHIVE_LOADER} manifest (all-listed, not survivor-only)')
    jobs = [(M.contained(store, f['path']), f['path'], f['sha256']) for f in manifest['files']
            if f['series'] == 'klines' and f['interval'] == '1d']
    if not jobs:
        raise UniverseError('the manifest has no 1d klines')
    if workers > 1 and len(jobs) > 1:
        with ProcessPoolExecutor(max_workers=workers) as ex:
            parts = list(ex.map(_daily_job, jobs, chunksize=64))
    else:
        parts = [_daily_job(j) for j in jobs]
    daily: dict[str, dict[int, Decimal]] = {}
    for sym, rows in parts:
        d = daily.setdefault(sym, {})
        for t, qv in rows:
            if t in d:
                raise UniverseError(f'{sym}: duplicate daily bar {utc_date(t)} across archives')
            d[t] = qv_decimal(qv)
    return daily


def _check_renames(renames) -> None:
    if renames:
        raise UniverseError('renames are refused (fail closed): a rename list needs time-scoped contract identity '
                            'with overlap checks and price-continuity evidence, which does not exist yet')


def _check_observations(obs, symbols) -> dict[str, int]:
    out = {}
    for o in obs:
        if not (isinstance(o, dict) and set(o) == {'symbol', 'observed_ms', 'source'} and o['symbol'] in symbols
                and type(o['observed_ms']) is int and isinstance(o['source'], str) and o['source']):
            raise UniverseError(f'bad delist observation {o!r}: need {{symbol, observed_ms, source}}')
        out[o['symbol']] = min(out.get(o['symbol'], o['observed_ms']), o['observed_ms'])
    return out


# ---------------------------------------------------------------- instrument classification

def classes_digest(c: dict) -> str:
    return hashlib.sha256(M.canonical({k: v for k, v in c.items() if k != 'digest'})).hexdigest()


def make_classes(entries, *, classes_id: str, tradfi_cutoff_ms: int, reviewed_cairo: str, method: str,
                 pre_cutoff_rule: str) -> dict:
    c = {'format': CLASSES_FORMAT, 'classes_id': classes_id, 'reviewed_cairo': reviewed_cairo, 'method': method,
         'tradfi_cutoff_ms': tradfi_cutoff_ms, 'pre_cutoff_rule': pre_cutoff_rule,
         'entries': sorted(entries, key=lambda e: e['symbol'])}
    c['digest'] = classes_digest(c)
    validate_classes(c)
    return c


def validate_classes(c: dict) -> None:
    def need(ok, msg):
        if not ok:
            raise UniverseError(f'instrument classes: {msg}')
    need(isinstance(c, dict) and set(c) == CLASS_KEYS, f'keys must be exactly {sorted(CLASS_KEYS)}')
    need(c['format'] == CLASSES_FORMAT, f'format must be {CLASSES_FORMAT}')
    need(isinstance(c['classes_id'], str) and c['classes_id'], 'classes_id must be a non-empty string')
    need(type(c['tradfi_cutoff_ms']) is int, 'tradfi_cutoff_ms must be an int')
    for k in ('reviewed_cairo', 'method', 'pre_cutoff_rule'):
        need(isinstance(c[k], str) and c[k], f'{k} must be a non-empty string')
    need(isinstance(c['entries'], list), 'entries must be a list')
    syms = [e.get('symbol') if isinstance(e, dict) else None for e in c['entries']]
    need(None not in syms and syms == sorted(set(syms)), 'entry symbols must be unique and sorted')
    for e in c['entries']:
        need(set(e) == ENTRY_KEYS, f'{e.get("symbol")}: entry keys must be exactly {sorted(ENTRY_KEYS)}')
        need(e['class'] in BOOKS + (UNCLASSIFIED,), f'{e["symbol"]}: class must be one of {BOOKS + (UNCLASSIFIED,)}')
        need(isinstance(e['subclass'], str) and e['subclass'], f'{e["symbol"]}: subclass must be a non-empty string')
        need(type(e['effective_from_ms']) is int, f'{e["symbol"]}: effective_from_ms must be an int')
        need(isinstance(e['basis'], str) and e['basis'], f'{e["symbol"]}: basis must be a non-empty source string')
    need(c['digest'] == classes_digest(c), 'digest does not match the canonical content')


def resolve_classes(c: dict, first_open: dict) -> dict[str, dict]:
    """symbol -> {class, subclass, effective_from_ms}; fails closed on any post-cutoff symbol without an entry."""
    validate_classes(c)
    by = {e['symbol']: e for e in c['entries']}
    out, missing = {}, []
    for s, t0 in first_open.items():
        if s in by:
            e = by[s]
            out[s] = {'class': e['class'], 'subclass': e['subclass'], 'effective_from_ms': e['effective_from_ms']}
        elif t0 is not None and t0 < c['tradfi_cutoff_ms']:
            out[s] = {'class': 'crypto', 'subclass': 'pre-cutoff-rule', 'effective_from_ms': t0}
        else:
            missing.append(s)
    if missing:
        raise UniverseError(f'{len(missing)} symbol(s) listed on/after the TradFi cutoff have no classification entry '
                            f'(no silent crypto default): {sorted(missing)[:10]}')
    return out


def load_classes(path: str) -> dict:
    with open(path, encoding='utf-8') as f:
        c = json.load(f)
    validate_classes(c)
    return c


# ---------------------------------------------------------------- build

def _gap_ranges(d: dict) -> list[list[str]]:
    """Internal missing daily bars between a symbol's first and last bar, as [first_missing, last_missing] dates."""
    out, run = [], None
    if d:
        for t in range(min(d), max(d) + DAY, DAY):
            if t not in d:
                run = [run[0] if run else t, t]
            elif run:
                out.append([utc_date(run[0]), utc_date(run[1])])
                run = None
    return out


def build(daily: dict[str, dict[int, Decimal]], *, manifest_digest: str, classes: dict, renames=(),
          delist_observations=(), rules_first_observed_ms: int | None = None, top_n: int = TOP_N,
          universe_id: str = UNIVERSE_ID) -> dict:
    symbols = sorted(daily)
    if not symbols or not M.HEX64.match(manifest_digest or ''):
        raise UniverseError('need daily bars and a 64-hex manifest digest')
    for s in symbols:
        qv_sum(daily[s].values())                                             # type check: Decimals only
    _check_renames(list(renames))
    delist = _check_observations(list(delist_observations), set(symbols))
    store_start = min(min(d) for d in daily.values() if d)
    store_end = max(max(d) for d in daily.values() if d) + DAY
    listing = {s: min(daily[s]) if daily[s] else None for s in symbols}
    cls = resolve_classes(classes, listing)

    table = []
    for s in symbols:
        d = daily[s]
        traded = [t for t, q in d.items() if q > 0]
        gaps = _gap_ranges(d)
        table.append({'symbol': s, 'class': cls[s]['class'], 'subclass': cls[s]['subclass'],
                      'class_effective_from_ms': cls[s]['effective_from_ms'],
                      'first_open_ms': listing[s], 'listing_ms': listing[s],
                      'listing_censored': listing[s] == store_start,
                      'archive_last_close_ms': max(d) + DAY if d else None,
                      'archive_ended_before_store_end': bool(d) and max(d) + DAY < store_end,
                      # settled contracts keep printing zero-volume daily bars, so the last traded close is the
                      # practical delist date; like the archive end it is hindsight, recorded for audit only
                      'last_traded_close_ms': max(traded) + DAY if traded else None,
                      'missing_days': gaps,
                      'missing_day_count': sum((date_ms(b) - date_ms(a)) // DAY + 1 for a, b in gaps)})

    first = store_start + LOOKBACK_DAYS * DAY
    monday = first + (MONDAY_EPOCH - first) % WEEK
    weeks = []
    while monday <= store_end:
        window = range(monday - LOOKBACK_DAYS * DAY, monday, DAY)   # open in [instant-30d, instant-1d] = closed by then
        scored = {b: [] for b in BOOKS}
        vetoes, gaps = [], []
        for s in symbols:
            d = daily[s]
            present = [t for t in window if t in d]
            live = monday - DAY in d
            if not present:
                continue                                    # not trading in the window at all
            qv = qv_sum(d[t] for t in present)
            missing = len(window) - len(present)
            c = cls[s]
            reason = None
            if c['class'] == UNCLASSIFIED or c['effective_from_ms'] > monday:
                reason = VETO_UNCLASSIFIED
            elif monday - listing[s] < MIN_AGE_DAYS * DAY:
                reason = VETO_AGE
            elif not live:
                reason = VETO_NOT_TRADING
            elif delist.get(s, monday + 1) <= monday:
                reason = VETO_DELIST
            elif missing:
                reason = VETO_GAP
                gaps.append([s, missing])
            elif qv <= 0:
                reason = VETO_NO_VOLUME
            if reason:
                vetoes.append([s, reason])
            else:
                scored[c['class']].append((-qv, s, qv))
        books = {}
        for b in BOOKS:
            ranked = sorted(scored[b])
            top = ranked[:top_n]
            books[b] = {'eligible': len(ranked), 'members': [s for _, s, _ in top],
                        'qv30d_usdt': [qv_text(q) for _, _, q in top]}
        weeks.append({'monday_ms': monday, 'monday_utc': utc_date(monday), 'books': books,
                      'vetoes': sorted(vetoes), 'gaps': sorted(gaps),
                      'rules': RULES_OBSERVED if rules_first_observed_ms is not None and monday >= rules_first_observed_ms
                      else RULES_BACKFILLED})
        monday += WEEK

    u = {'format': FORMAT, 'universe_id': universe_id, 'manifest_digest': manifest_digest,
         'classes_id': classes['classes_id'], 'classes_digest': classes['digest'], 'books': list(BOOKS),
         'rule': {'instant': 'Monday 00:00 UTC',
                  'score': f'exact decimal sum of quote_volume of 1d bars with open in [instant-{LOOKBACK_DAYS}d, '
                           f'instant-1d] (available_ms <= instant)',
                  'books': 'ranked separately per asset-class book; unclassified symbols join no book',
                  'top_n': top_n, 'min_listing_age_days': MIN_AGE_DAYS, 'tie_break': 'symbol ascending',
                  'complete_window': f'all {LOOKBACK_DAYS} daily bars present, else veto {VETO_GAP} (never zero volume)',
                  'warmup_test': 'per strategy, applied by the R3 harness (independent of the listing-age test)',
                  'delist_veto': 'timestamped observations only; archive end is hindsight, audit only',
                  'renames': 'refused (fail closed) until time-scoped identity + price-continuity evidence exists',
                  'rules_label': f'{RULES_BACKFILLED} before rules_first_observed_ms'},
         'store_start_ms': store_start, 'store_end_ms': store_end, 'rules_first_observed_ms': rules_first_observed_ms,
         'renames': [],
         'delist_observations': sorted(delist_observations, key=lambda o: (o['observed_ms'], o['symbol'])),
         'symbols': table, 'weeks': weeks}
    u['digest'] = digest_of(u)
    return u


def members(u: dict, week: dict, book: str) -> list[str]:
    """The members of one book in one week (no mixed list across books exists)."""
    if book not in u['books']:
        raise UniverseError(f'unknown book {book!r}; books are {u["books"]}')
    return week['books'][book]['members']


def digest_of(u: dict) -> str:
    return hashlib.sha256(M.canonical({k: v for k, v in u.items() if k != 'digest'})).hexdigest()


def write(u: dict, path: str) -> None:
    """Write once (identical bytes are a no-op; different bytes are refused)."""
    if u.get('digest') != digest_of(u):
        raise UniverseError('digest does not match the canonical content')
    data = json.dumps(u, sort_keys=True, indent=1, ensure_ascii=True, allow_nan=False).encode('ascii') + b'\n'
    if os.path.exists(path):
        with open(path, 'rb') as f:
            if f.read() == data:
                return
        raise UniverseError(f'{path} exists with different content; universe files are immutable, use a new id')
    with open(path, 'wb') as f:
        f.write(data)


def _load_json(path):
    if not path:
        return []
    with open(path, encoding='utf-8') as f:
        return json.load(f)


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split('\n')[0])
    sub = ap.add_subparsers(dest='cmd', required=True)
    b = sub.add_parser('build')
    b.add_argument('--out', required=True)
    v = sub.add_parser('verify')
    v.add_argument('file')
    for p in (b, v):
        p.add_argument('--manifest', required=True)
        p.add_argument('--classes', required=True, help='a zb-instrument-classes/1 file')
        p.add_argument('--store', required=True, help='the data store the manifest describes (outside the repo)')
        p.add_argument('--renames', help='JSON list of renames; refused (fail closed) when non-empty')
        p.add_argument('--delist-observations', help='JSON list of {symbol, observed_ms, source}')
        p.add_argument('--rules-first-observed-ms', type=int)
        p.add_argument('--workers', type=int, default=1)
    a = ap.parse_args(argv)
    try:
        m = M.load(a.manifest)
        u = build(load_daily(m, a.store, a.workers), manifest_digest=m['digest'], classes=load_classes(a.classes),
                  renames=_load_json(a.renames), delist_observations=_load_json(a.delist_observations),
                  rules_first_observed_ms=a.rules_first_observed_ms)
        if a.cmd == 'build':
            write(u, a.out)
            print(u['digest'])
            return 0
        with open(a.file, encoding='utf-8') as f:
            have = json.load(f)
        ok = have.get('digest') == u['digest'] == digest_of(have)
        print('OK' if ok else f'MISMATCH: file {have.get("digest")} rebuilt {u["digest"]}')
        return 0 if ok else 1
    except (M.ManifestError, UniverseError) as e:
        print(f'error: {e}', file=sys.stderr)
        return 2


if __name__ == '__main__':
    sys.exit(main())
