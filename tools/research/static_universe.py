"""`zb-static-universe/1`: the frozen current top-40 Binance USD-M crypto universe (contract section 0.1, owner ruling
6096247364, CORE tiers 6096262305).

Ranking reuses the approved `pit-top40-qv30d-v4` rule of universe.py unchanged (trailing 30-day exact quote volume, same
vetoes and tie-break, crypto book, unbounded `top_n` so the order past rank 40 is known), evaluated once at a refreshed
instant: 00:00 UTC after the latest complete UTC day at build time (Codex 6096513658). The manifest-verified store is
first re-checked against the committed v4 universe; the days after the store ends are filled by a bounded recent refresh
(`fetch-recent`: public fapi 1d klines, no keys, rate-limited, each response's sha256 recorded) for the store's symbols
that are TRADING in the snapshot. A symbol not in the store cannot have 365 days of history, so none is fetched. Tradability comes from one public USD-M `exchangeInfo`
snapshot, reduced to symbol / status / contractType / quoteAsset / underlyingType and pinned by the raw response sha256.
Walking the ranked book, a symbol is taken when it is an individual crypto USDT perpetual that is TRADING with
underlyingType COIN; the two gold identities (XAUUSDT, PAXGUSDT) are recorded separately as inactive and never count.
The first 40 taken are the universe; ranks 1-10 are CORE-10 and 1-20 CORE-20.

The file is labelled CURRENT-UNIVERSE / SURVIVOR-BIASED and `owner_approved` is false (PENDING) until the owner
approves it. Output is deterministic (its timestamp is the snapshot's serverTime in Africa/Cairo) and written once.
Stdlib only; the build reads bar volumes only (no strategy, outcomes or holdout) and makes no network call
(only `fetch-recent` does).

Owner rule (10 Oct 2026, set before any results): a symbol is eligible only with >= 365 days of continuous
archived daily history at the instant (history_days); a relisted symbol counts only its current segment.

Usage (repo root):
  python tools/research/static_universe.py fetch-recent --exchange-info-raw RAW.json --manifest MANIFEST
      --out research_evidence/universe/binance-um-klines1d-recent-<YYYYMMDD>.json
  python tools/research/static_universe.py build --exchange-info-raw RAW.json --recent RECENT.json --manifest MANIFEST --classes CLASSES
      --store C:/Dev/ZackBot2_data/binance_um --pit research_evidence/universe/pit-top40-qv30d-v4.json
      --out-dir research_evidence/universe [--workers N]
  python tools/research/static_universe.py verify FILE --snapshot SNAPSHOT
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import sys
import time
import urllib.request
from datetime import datetime, timezone
from zoneinfo import ZoneInfo

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import manifest as M                                                                        # noqa: E402
import universe as U                                                                        # noqa: E402

FORMAT = 'zb-static-universe/1'
SNAP_FORMAT = 'zb-binance-um-exchangeinfo-reduced/1'
SOURCE_URL = 'https://fapi.binance.com/fapi/v1/exchangeInfo'
SNAP_FIELDS = ('symbol', 'status', 'contractType', 'quoteAsset', 'underlyingType')
LABEL = 'CURRENT-UNIVERSE / SURVIVOR-BIASED'
BOOK = 'crypto'
N = 40
GOLD = ('PAXGUSDT', 'XAUUSDT')
ELIGIBLE = {'status': 'TRADING', 'contractType': 'PERPETUAL', 'quoteAsset': 'USDT', 'underlyingType': 'COIN'}
CAIRO = ZoneInfo('Africa/Cairo')
DAY = U.DAY
RULE_ID = 'qv30d-top40-minhist365'
MIN_HISTORY_DAYS = 365
RULE_NOTE = 'owner rule, 10 Oct 2026, set before any results'
VETO_HISTORY = f'history<{MIN_HISTORY_DAYS}d'
RECENT_FORMAT = 'zb-binance-um-klines1d-recent/1'
KLINES_URL = ('https://fapi.binance.com/fapi/v1/klines?symbol={symbol}&interval=1d&startTime={start}&endTime={end}'
              '&limit=100')


class StaticUniverseError(ValueError):
    pass


def sha(obj) -> str:
    return hashlib.sha256(M.canonical(obj)).hexdigest()


def body_digest(d: dict) -> str:
    return sha({k: v for k, v in d.items() if k != 'digest'})


def cairo(ms: int) -> str:
    return datetime.fromtimestamp(ms / 1000, tz=timezone.utc).astimezone(CAIRO).isoformat(timespec='seconds')


def reduce_exchange_info(raw: bytes, fetched_ms: int | None = None) -> dict:
    """The public exchangeInfo response -> the reduced, digest-pinned snapshot (five fields per symbol)."""
    info = json.loads(raw.decode('utf-8'))
    rows = sorted(({k: s.get(k) for k in SNAP_FIELDS} for s in info['symbols']), key=lambda r: r['symbol'])
    # exchangeInfo is served through a CDN cache and its serverTime can be days old; the as-of time is the fetch time,
    # read from /fapi/v1/time immediately before the fetch (falls back to serverTime only when not given)
    fetched = info['serverTime'] if fetched_ms is None else fetched_ms
    snap = {'format': SNAP_FORMAT, 'source_url': SOURCE_URL, 'server_time_ms': info['serverTime'],
            'fetched_ms': fetched, 'fetched_cairo': cairo(fetched), 'raw_sha256': hashlib.sha256(raw).hexdigest(),
            'fields': list(SNAP_FIELDS), 'symbols': rows}
    snap['digest'] = body_digest(snap)
    return snap


def asof(ms: int) -> str:
    u = datetime.fromtimestamp(ms / 1000, tz=timezone.utc)
    return f'{u.astimezone(CAIRO).strftime("%Y-%m-%d %H:%M:%S")} Cairo ({u.strftime("%Y-%m-%d %H:%M:%S")} UTC)'


def reject_reason(symbol: str, row: dict | None) -> str | None:
    if symbol in GOLD:
        return 'inactive-gold'
    if row is None:
        return 'absent-from-snapshot'
    bad = [f'{k}={row.get(k)}' for k, v in ELIGIBLE.items() if row.get(k) != v]
    return 'not-tradable:' + ','.join(bad) if bad else None


def build_static(ranked: list[list], snapshot: dict, source: dict) -> dict:
    """ranked = [[source_rank, symbol, qv30d_usdt, history_days], ...] in the rule's order; take the first N symbols
    that are tradable at the snapshot and have >= MIN_HISTORY_DAYS of continuous daily history at the instant."""
    by = {r['symbol']: r for r in snapshot['symbols']}
    taken, skipped, consumed, history_out, old = [], [], [], [], 0
    for src_rank, sym, qv, hist in ranked:
        if len(taken) == N:
            break
        consumed.append([src_rank, sym, qv, hist])
        why = reject_reason(sym, by.get(sym))
        if why:
            skipped.append({'source_rank': src_rank, 'symbol': sym, 'reason': why})
            continue
        old += 1                                    # rank in the list without the history rule (the 4d96e00 list)
        if hist < MIN_HISTORY_DAYS:
            if old <= N:                            # record only what the rule pushed out of the old top 40
                history_out.append({'old_rank': old, 'source_rank': src_rank, 'symbol': sym, 'history_days': hist})
            continue
        r = len(taken) + 1
        taken.append({'rank': r, 'symbol': sym, 'source_rank': src_rank, 'qv30d_usdt': qv,
                      'history_days': hist, 'tier': 'CORE-10' if r <= 10 else 'CORE-20' if r <= 20 else 'EXTENDED-40'})
    if len(taken) != N:
        raise StaticUniverseError(f'only {len(taken)} eligible symbols in the ranked book; need exactly {N}')
    syms = [t['symbol'] for t in taken]
    gold = [{'symbol': g, 'status': 'inactive', 'counts_toward_40': False, 'blocking': False,
             'snapshot_row': by.get(g)} for g in GOLD]
    u = {'format': FORMAT, 'universe_id': f'static-top40-minhist{MIN_HISTORY_DAYS}-{source["instant_utc"].replace("-", "")}',
         'label': LABEL,
         'owner_approved': False, 'owner_approval': 'PENDING',
         'ranking_rule': {'rule_id': RULE_ID, 'base_rule_id': source['base_rule_id'], 'book': BOOK,
                          'min_history_days': MIN_HISTORY_DAYS, 'min_history_note': RULE_NOTE,
                          'history': 'continuous archived daily bars ending with the bar that closes at the instant; '
                                     'any missing day (e.g. a relisting) restarts the count', 'instant_ms': source['instant_ms'],
                          'instant_utc': source['instant_utc'],
                          'selection': f'walk the ranked {BOOK} book; take the first {N} symbols whose snapshot row '
                                       f'is {ELIGIBLE} and whose history_days >= {MIN_HISTORY_DAYS}; gold identities '
                                       f'{list(GOLD)} never count'},
         'ranking_asof': asof(source['instant_ms']), 'tradability_asof': asof(snapshot['fetched_ms']),
         'sources': {'manifest_digest': source['manifest_digest'], 'classes_digest': source['classes_digest'],
                     'pit_universe_digest': source['pit_universe_digest'], 'recent_klines': source['recent_klines'],
                     'exchange_info': {'url': SOURCE_URL, 'fetched_ms': snapshot['fetched_ms'],
                                       'server_time_ms': snapshot['server_time_ms'],
                                       'raw_sha256': snapshot['raw_sha256'], 'reduced_digest': snapshot['digest']}},
         'symbols': taken, 'core_10': syms[:10], 'core_20': syms[:20], 'skipped': skipped,
         'excluded_by_history': history_out,
         'inactive_gold': gold, 'ranked_source': consumed, 'list_digest': sha(syms)}
    u['digest'] = body_digest(u)
    return u


def dumps(d: dict) -> bytes:
    return json.dumps(d, sort_keys=True, indent=1, ensure_ascii=True, allow_nan=False).encode('ascii') + b'\n'


def validate(u: dict, snapshot: dict) -> None:
    """The five validation families: count/order, uniqueness, tradability, digests, deterministic rebuild."""
    def need(ok, msg):
        if not ok:
            raise StaticUniverseError(msg)
    s = u['symbols']
    syms = [t['symbol'] for t in s]
    # 1. exact count and order
    need(len(s) == N, f'need exactly {N} symbols, have {len(s)}')
    need([t['rank'] for t in s] == list(range(1, N + 1)), 'ranks must be 1..40 in order')
    need([t['source_rank'] for t in s] == sorted(t['source_rank'] for t in s), 'source ranks must ascend')
    need(u['core_10'] == syms[:10] and u['core_20'] == syms[:20], 'CORE-10/CORE-20 must be ranks 1-10 / 1-20')
    need([t['tier'] for t in s] == ['CORE-10'] * 10 + ['CORE-20'] * 10 + ['EXTENDED-40'] * 20, 'tier labels')
    # 2. uniqueness
    need(len(set(syms)) == len(syms), 'duplicate symbol in the list')
    # 3. existence / tradability at the snapshot
    by = {r['symbol']: r for r in snapshot['symbols']}
    for sym in syms:
        why = reject_reason(sym, by.get(sym))
        need(why is None, f'{sym}: {why}')
    need(all(t['history_days'] >= MIN_HISTORY_DAYS for t in s), f'a listed symbol has < {MIN_HISTORY_DAYS} days history')
    # 4. digest mismatch is refused
    need(snapshot['digest'] == body_digest(snapshot), 'snapshot digest does not match its content')
    ex = u['sources']['exchange_info']
    need(ex['reduced_digest'] == snapshot['digest'] and ex['raw_sha256'] == snapshot['raw_sha256'],
         'universe was built from a different exchangeInfo snapshot')
    need(u['list_digest'] == sha(syms), 'list digest does not match the symbols')
    need(u['digest'] == body_digest(u), 'file digest does not match the canonical content')
    # 5. deterministic rebuild is byte-identical
    src = {'base_rule_id': u['ranking_rule']['base_rule_id'], 'instant_ms': u['ranking_rule']['instant_ms'],
           'instant_utc': u['ranking_rule']['instant_utc'], **{k: u['sources'][k] for k in
           ('manifest_digest', 'classes_digest', 'pit_universe_digest', 'recent_klines')}}
    rebuilt = build_static(u['ranked_source'], snapshot, src)
    if u['owner_approved']:                     # an approved file must be exactly the rebuilt PENDING list + approval
        rec = u['owner_approval']
        need(isinstance(rec, dict) and rec.get('pending_digest') == rebuilt['digest']
             and rec.get('list_digest') == rebuilt['list_digest'], 'approval does not pin this exact list')
        rebuilt = approve(rebuilt, approved_sha=rec['approved_at_sha'], date_cairo=rec['date_cairo'])
    else:
        need(u['owner_approval'] == 'PENDING', 'owner_approval must be PENDING while owner_approved is false')
    need(dumps(rebuilt) == dumps(u), 'rebuild is not byte-identical')


def approve(u: dict, *, approved_sha: str, date_cairo: str) -> dict:
    """The owner's approval of the exact PENDING list: only owner_approved / owner_approval change. The record pins the
    PENDING file digest and the list digest, so an approval cannot be carried over to a different list."""
    if u['owner_approved'] or u['owner_approval'] != 'PENDING' or u['digest'] != body_digest(u):
        raise StaticUniverseError('only an intact PENDING list can be approved')
    a = {k: v for k, v in u.items() if k != 'digest'}
    a['owner_approved'] = True
    a['owner_approval'] = {'status': 'APPROVED', 'by': 'owner', 'date_cairo': date_cairo,
                           'approved_at_sha': approved_sha, 'pending_digest': u['digest'],
                           'list_digest': u['list_digest']}
    a['digest'] = body_digest(a)
    return a


def write_once(d: dict, path: str) -> None:
    data = dumps(d)
    if os.path.exists(path):
        with open(path, 'rb') as f:
            if f.read() == data:
                return
        raise StaticUniverseError(f'{path} exists with different content; these files are immutable')
    with open(path, 'wb') as f:
        f.write(data)


def history_days(bars: dict, instant_ms: int) -> int:
    """Days of continuous daily history at the instant: the run of consecutive archived 1d bars ending with the bar
    that closes at the instant. A missing day (relisting or outage) starts a new segment; only the current one counts."""
    t = instant_ms - DAY
    if t not in bars:
        return 0
    while t - DAY in bars:
        t -= DAY
    return (instant_ms - t) // DAY


def instant_of(snapshot: dict) -> int:
    """00:00 UTC after the latest complete UTC day at the snapshot's fetch time."""
    return snapshot['fetched_ms'] // DAY * DAY


def _daily_files(m: dict) -> list[dict]:
    return [f for f in m['files'] if f['series'] == 'klines' and f['interval'] == '1d']


def fetch_recent(m: dict, snapshot: dict, pause_s: float = 0.25, get=None) -> dict:
    """Bounded recent refresh: 1d klines from the day after the store ends up to the instant, for store symbols that
    are TRADING in the snapshot. Public endpoint, no keys; each raw response is pinned by its sha256."""
    get = get or (lambda url: urllib.request.urlopen(url, timeout=30).read())
    files = _daily_files(m)
    start = max(f['last_open_ms'] for f in files) + DAY                  # first day the store does not hold
    instant = instant_of(snapshot)
    trading = {r['symbol'] for r in snapshot['symbols'] if r['status'] == 'TRADING'}
    out = {}
    # non-ASCII symbols are vetoed not-addressable by the rule before ranking, so they are not fetched
    for sym in sorted(s for s in {f['symbol'] for f in files} & trading if U.addressable(s)):
        raw = get(KLINES_URL.format(symbol=sym, start=start, end=instant - 1))
        bars = [[int(k[0]), k[7]] for k in json.loads(raw) if int(k[0]) >= start and int(k[0]) + DAY <= instant]
        out[sym] = {'response_sha256': hashlib.sha256(raw).hexdigest(), 'bars': bars}
        time.sleep(pause_s)
    r = {'format': RECENT_FORMAT, 'source': KLINES_URL.split('?')[0], 'url_template': KLINES_URL,
         'start_ms': start, 'instant_ms': instant, 'snapshot_digest': snapshot['digest'], 'symbols': out}
    r['digest'] = body_digest(r)
    return r


def merge_recent(daily: dict, recent: dict, snapshot: dict) -> None:
    if recent['digest'] != body_digest(recent) or recent['snapshot_digest'] != snapshot['digest'] \
            or recent['instant_ms'] != instant_of(snapshot):
        raise StaticUniverseError('recent klines do not match their digest / the exchangeInfo snapshot')
    for sym, v in recent['symbols'].items():
        d = daily.get(sym)
        if d is None:
            raise StaticUniverseError(f'{sym}: recent bars for a symbol outside the store')
        for t, qv in v['bars']:
            if t in d or t < recent['start_ms'] or t + DAY > recent['instant_ms']:
                raise StaticUniverseError(f'{sym}: recent bar {U.utc_date(t)} overlaps the store or the instant')
            d[t] = U.qv_decimal(qv)


def ranked_from_store(manifest_path: str, classes_path: str, store: str, pit_path: str, recent: dict,
                      snapshot: dict, workers: int):
    """The full ranked crypto book at the refreshed instant (the store is first re-checked against committed v4)."""
    m = M.load(manifest_path)
    c = U.load_classes(classes_path)
    with open(pit_path, encoding='utf-8') as f:
        pit = json.load(f)
    if pit['digest'] != U.digest_of(pit) or pit['manifest_digest'] != m['digest'] \
            or pit['classes_digest'] != c['digest']:
        raise StaticUniverseError('PIT universe does not match its digest / manifest / classes')
    daily = U.load_daily(m, store, workers)
    check = U.build(daily, manifest_digest=m['digest'], classes=c)['weeks'][-1]
    if check['monday_ms'] != pit['weeks'][-1]['monday_ms'] or check['books'] != pit['weeks'][-1]['books']:
        raise StaticUniverseError('store rebuild disagrees with the committed PIT universe')
    merge_recent(daily, recent, snapshot)
    instant = recent['instant_ms']
    week = U.build(daily, manifest_digest=m['digest'], classes=c, top_n=10 ** 6, anchor_ms=instant)['weeks'][-1]
    if week['monday_ms'] != instant:
        raise StaticUniverseError('the refreshed instant was not ranked (the data does not reach it)')
    book = week['books'][BOOK]
    ranked = [[i + 1, s, q, history_days(daily[s], instant)]
              for i, (s, q) in enumerate(zip(book['members'], book['qv30d_usdt']))]
    return ranked, {'base_rule_id': pit['universe_id'], 'instant_ms': instant, 'instant_utc': week['monday_utc'],
                    'manifest_digest': m['digest'], 'classes_digest': c['digest'], 'pit_universe_digest': pit['digest'],
                    'recent_klines': {'source': recent['source'], 'start_ms': recent['start_ms'],
                                      'symbols': len(recent['symbols']), 'digest': recent['digest']}}


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split('\n')[0])
    sub = ap.add_subparsers(dest='cmd', required=True)
    fr = sub.add_parser('fetch-recent')
    for k in ('--exchange-info-raw', '--manifest', '--out'):
        fr.add_argument(k, required=True)
    fr.add_argument('--fetched-ms', type=int, required=True, help='/fapi/v1/time read just before the exchangeInfo fetch')
    b = sub.add_parser('build')
    for k in ('--exchange-info-raw', '--recent', '--manifest', '--classes', '--store', '--pit', '--out-dir'):
        b.add_argument(k, required=True)
    b.add_argument('--fetched-ms', type=int, required=True)
    b.add_argument('--workers', type=int, default=1)
    v = sub.add_parser('verify')
    v.add_argument('file')
    v.add_argument('--snapshot', required=True)
    a = ap.parse_args(argv)
    try:
        if a.cmd == 'fetch-recent':
            with open(a.exchange_info_raw, 'rb') as f:
                snap = reduce_exchange_info(f.read(), a.fetched_ms)
            write_once(fetch_recent(M.load(a.manifest), snap), a.out)
            return 0
        if a.cmd == 'build':
            with open(a.exchange_info_raw, 'rb') as f:
                snap = reduce_exchange_info(f.read(), a.fetched_ms)
            with open(a.recent, encoding='utf-8') as f:
                recent = json.load(f)
            ranked, src = ranked_from_store(a.manifest, a.classes, a.store, a.pit, recent, snap, a.workers)
            u = build_static(ranked, snap, src)
            validate(u, snap)
            day = datetime.fromtimestamp(snap['fetched_ms'] / 1000, tz=CAIRO).strftime('%Y%m%d')
            write_once(snap, os.path.join(a.out_dir, f'binance-um-exchangeinfo-{day}.json'))
            write_once(u, os.path.join(a.out_dir, f'{u["universe_id"]}.json'))
            print(u['digest'])
            return 0
        with open(a.file, 'rb') as f:
            raw = f.read()
        u = json.loads(raw.decode('ascii'))
        if raw != dumps(u):
            raise StaticUniverseError(f'{a.file} is not in canonical written form (bytes differ)')
        with open(a.snapshot, encoding='utf-8') as f:
            snap = json.load(f)
        validate(u, snap)
        print('OK')
        return 0
    except (M.ManifestError, U.UniverseError, StaticUniverseError) as e:
        print(f'error: {e}', file=sys.stderr)
        return 2


if __name__ == '__main__':
    sys.exit(main())
