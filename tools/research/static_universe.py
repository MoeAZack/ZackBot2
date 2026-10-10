"""`zb-static-universe/1`: the frozen current top-40 Binance USD-M crypto universe (contract section 0.1, owner ruling
6096247364, CORE tiers 6096262305).

Ranking reuses the approved `pit-top40-qv30d-v4` rule of universe.py unchanged: the crypto book of its latest weekly
ranking instant (trailing 30-day exact quote volume, same vetoes and tie-break), built from the manifest-verified store
with an unbounded `top_n` so the order past rank 40 is known. Tradability comes from one public USD-M `exchangeInfo`
snapshot, reduced to symbol / status / contractType / quoteAsset / underlyingType and pinned by the raw response sha256.
Walking the ranked book, a symbol is taken when it is an individual crypto USDT perpetual that is TRADING with
underlyingType COIN; the two gold identities (XAUUSDT, PAXGUSDT) are recorded separately as inactive and never count.
The first 40 taken are the universe; ranks 1-10 are CORE-10 and 1-20 CORE-20.

The file is labelled CURRENT-UNIVERSE / SURVIVOR-BIASED and `owner_approved` is false (PENDING) until the owner
approves it. Output is deterministic (its timestamp is the snapshot's serverTime in Africa/Cairo) and written once.
Stdlib only; the build reads bar volumes only (no strategy, outcomes or holdout) and makes no network call.

Usage (repo root):
  python tools/research/static_universe.py build --exchange-info-raw RAW.json --manifest MANIFEST --classes CLASSES
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


class StaticUniverseError(ValueError):
    pass


def sha(obj) -> str:
    return hashlib.sha256(M.canonical(obj)).hexdigest()


def body_digest(d: dict) -> str:
    return sha({k: v for k, v in d.items() if k != 'digest'})


def cairo(ms: int) -> str:
    return datetime.fromtimestamp(ms / 1000, tz=timezone.utc).astimezone(CAIRO).isoformat(timespec='seconds')


def reduce_exchange_info(raw: bytes) -> dict:
    """The public exchangeInfo response -> the reduced, digest-pinned snapshot (five fields per symbol)."""
    info = json.loads(raw.decode('utf-8'))
    rows = sorted(({k: s.get(k) for k in SNAP_FIELDS} for s in info['symbols']), key=lambda r: r['symbol'])
    snap = {'format': SNAP_FORMAT, 'source_url': SOURCE_URL, 'server_time_ms': info['serverTime'],
            'server_time_cairo': cairo(info['serverTime']), 'raw_sha256': hashlib.sha256(raw).hexdigest(),
            'fields': list(SNAP_FIELDS), 'symbols': rows}
    snap['digest'] = body_digest(snap)
    return snap


def reject_reason(symbol: str, row: dict | None) -> str | None:
    if symbol in GOLD:
        return 'inactive-gold'
    if row is None:
        return 'absent-from-snapshot'
    bad = [f'{k}={row.get(k)}' for k, v in ELIGIBLE.items() if row.get(k) != v]
    return 'not-tradable:' + ','.join(bad) if bad else None


def build_static(ranked: list[list], snapshot: dict, source: dict) -> dict:
    """ranked = [[source_rank, symbol, qv30d_usdt], ...] in the rule's order; take the first N eligible symbols."""
    by = {r['symbol']: r for r in snapshot['symbols']}
    taken, skipped, consumed = [], [], []
    for src_rank, sym, qv in ranked:
        if len(taken) == N:
            break
        consumed.append([src_rank, sym, qv])
        why = reject_reason(sym, by.get(sym))
        if why:
            skipped.append({'source_rank': src_rank, 'symbol': sym, 'reason': why})
            continue
        r = len(taken) + 1
        taken.append({'rank': r, 'symbol': sym, 'source_rank': src_rank, 'qv30d_usdt': qv,
                      'tier': 'CORE-10' if r <= 10 else 'CORE-20' if r <= 20 else 'EXTENDED-40'})
    if len(taken) != N:
        raise StaticUniverseError(f'only {len(taken)} eligible symbols in the ranked book; need exactly {N}')
    syms = [t['symbol'] for t in taken]
    gold = [{'symbol': g, 'status': 'inactive', 'counts_toward_40': False, 'blocking': False,
             'snapshot_row': by.get(g)} for g in GOLD]
    u = {'format': FORMAT, 'universe_id': f'static-top40-{source["instant_utc"].replace("-", "")}', 'label': LABEL,
         'owner_approved': False, 'owner_approval': 'PENDING',
         'ranking_rule': {'rule_id': source['rule_id'], 'book': BOOK, 'instant_ms': source['instant_ms'],
                          'instant_utc': source['instant_utc'],
                          'selection': f'walk the ranked {BOOK} book; take the first {N} symbols whose snapshot row '
                                       f'is {ELIGIBLE}; gold identities {list(GOLD)} never count'},
         'sources': {'manifest_digest': source['manifest_digest'], 'classes_digest': source['classes_digest'],
                     'pit_universe_digest': source['pit_universe_digest'],
                     'exchange_info': {'url': SOURCE_URL, 'server_time_ms': snapshot['server_time_ms'],
                                       'raw_sha256': snapshot['raw_sha256'], 'reduced_digest': snapshot['digest']}},
         'snapshot_cairo': snapshot['server_time_cairo'],
         'symbols': taken, 'core_10': syms[:10], 'core_20': syms[:20], 'skipped': skipped,
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
    # 4. digest mismatch is refused
    need(snapshot['digest'] == body_digest(snapshot), 'snapshot digest does not match its content')
    ex = u['sources']['exchange_info']
    need(ex['reduced_digest'] == snapshot['digest'] and ex['raw_sha256'] == snapshot['raw_sha256'],
         'universe was built from a different exchangeInfo snapshot')
    need(u['list_digest'] == sha(syms), 'list digest does not match the symbols')
    need(u['digest'] == body_digest(u), 'file digest does not match the canonical content')
    # 5. deterministic rebuild is byte-identical
    src = {'rule_id': u['ranking_rule']['rule_id'], 'instant_ms': u['ranking_rule']['instant_ms'],
           'instant_utc': u['ranking_rule']['instant_utc'], **{k: u['sources'][k] for k in
           ('manifest_digest', 'classes_digest', 'pit_universe_digest')}}
    need(dumps(build_static(u['ranked_source'], snapshot, src)) == dumps(u), 'rebuild is not byte-identical')


def write_once(d: dict, path: str) -> None:
    data = dumps(d)
    if os.path.exists(path):
        with open(path, 'rb') as f:
            if f.read() == data:
                return
        raise StaticUniverseError(f'{path} exists with different content; these files are immutable')
    with open(path, 'wb') as f:
        f.write(data)


def ranked_from_store(manifest_path: str, classes_path: str, store: str, pit_path: str, workers: int):
    """The full ranked crypto book at the PIT universe's latest instant, cross-checked against its committed top 40."""
    m = M.load(manifest_path)
    c = U.load_classes(classes_path)
    with open(pit_path, encoding='utf-8') as f:
        pit = json.load(f)
    if pit['digest'] != U.digest_of(pit) or pit['manifest_digest'] != m['digest'] \
            or pit['classes_digest'] != c['digest']:
        raise StaticUniverseError('PIT universe does not match its digest / manifest / classes')
    full = U.build(U.load_daily(m, store, workers), manifest_digest=m['digest'], classes=c, top_n=10 ** 6)
    week, pit_week = full['weeks'][-1], pit['weeks'][-1]
    book = week['books'][BOOK]
    if week['monday_ms'] != pit_week['monday_ms'] or book['members'][:U.TOP_N] != pit_week['books'][BOOK]['members']:
        raise StaticUniverseError('store rebuild disagrees with the committed PIT universe top 40')
    ranked = [[i + 1, s, q] for i, (s, q) in enumerate(zip(book['members'], book['qv30d_usdt']))]
    return ranked, {'rule_id': pit['universe_id'], 'instant_ms': week['monday_ms'], 'instant_utc': week['monday_utc'],
                    'manifest_digest': m['digest'], 'classes_digest': c['digest'], 'pit_universe_digest': pit['digest']}


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split('\n')[0])
    sub = ap.add_subparsers(dest='cmd', required=True)
    b = sub.add_parser('build')
    for k in ('--exchange-info-raw', '--manifest', '--classes', '--store', '--pit', '--out-dir'):
        b.add_argument(k, required=True)
    b.add_argument('--workers', type=int, default=1)
    v = sub.add_parser('verify')
    v.add_argument('file')
    v.add_argument('--snapshot', required=True)
    a = ap.parse_args(argv)
    try:
        if a.cmd == 'build':
            with open(a.exchange_info_raw, 'rb') as f:
                snap = reduce_exchange_info(f.read())
            ranked, src = ranked_from_store(a.manifest, a.classes, a.store, a.pit, a.workers)
            u = build_static(ranked, snap, src)
            validate(u, snap)
            day = datetime.fromtimestamp(snap['server_time_ms'] / 1000, tz=CAIRO).strftime('%Y%m%d')
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
