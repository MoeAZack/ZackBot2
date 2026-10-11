"""Static current top-40 universe (contract 0.1): the five validation families of tools/research/static_universe.py on a
synthetic exchangeInfo snapshot, plus the committed artifact against its committed snapshot (no network, no store)."""
import copy
import json
import os
import sys

import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, 'tools', 'research'))
import static_universe as S                                                                 # noqa: E402

UDIR = os.path.join(ROOT, 'research_evidence', 'universe')
SOURCE = {'base_rule_id': 'pit-top40-qv30d-v4', 'instant_ms': 1790553600000, 'instant_utc': '2026-09-28',
          'manifest_digest': 'a' * 64, 'classes_digest': 'b' * 64, 'pit_universe_digest': 'c' * 64,
          'recent_klines': {'source': 'x', 'start_ms': 0, 'symbols': 0, 'digest': 'e' * 64}}


def row(sym, status='TRADING', ct='PERPETUAL', quote='USDT', under='COIN', onboard=1567965300000):
    return {'symbol': sym, 'status': status, 'contractType': ct, 'quoteAsset': quote, 'underlyingType': under,
            'onboardDate': onboard}


def raw_info(rows):
    return json.dumps({'serverTime': 1791530164008, 'symbols': [dict(r, extra=1) for r in rows]}).encode()


COINS = [f'C{i:02d}USDT' for i in range(1, 46)]
ROWS = [row(s) for s in COINS] + [row('PAXGUSDT'), row('XAUUSDT', ct='TRADIFI_PERPETUAL', under='COMMODITY'),
                                  row('BTCDOMUSDT', under='INDEX'), row('DEADUSDT', status='SETTLING')]
RANKED_SYMS = COINS[:3] + ['PAXGUSDT', 'BTCDOMUSDT', 'DEADUSDT', 'GONEUSDT'] + COINS[3:]


def ranked(history=None):
    history = history or {}
    return [[i + 1, s, str(10 ** 9 - i), history.get(s, 400)] for i, s in enumerate(RANKED_SYMS)]


def resync_cores(u):
    syms = [t['symbol'] for t in u['symbols']]
    u['core_10'], u['core_20'] = syms[:10], syms[:20]


@pytest.fixture
def built():
    snap = S.reduce_exchange_info(raw_info(ROWS))
    return S.build_static(ranked(), snap, SOURCE), snap


def test_snapshot_is_reduced_and_pinned():
    raw = raw_info(ROWS)
    snap = S.reduce_exchange_info(raw)
    assert all(set(r) == set(S.SNAP_FIELDS) for r in snap['symbols'])
    assert snap['raw_sha256'] == S.hashlib.sha256(raw).hexdigest()
    assert snap['fetched_cairo'] == '2026-10-09T10:16:04+03:00'
    assert S.reduce_exchange_info(raw, 1791676800000)['fetched_ms'] == 1791676800000


# 1. exact count and order
def test_count_and_order(built):
    u, snap = built
    S.validate(u, snap)
    syms = [t['symbol'] for t in u['symbols']]
    assert len(syms) == 40 and syms == COINS[:40]
    assert u['core_10'] == syms[:10] and u['core_20'] == syms[:20]
    assert [s['reason'] for s in u['skipped']] == ['inactive-gold', 'not-tradable:underlyingType=INDEX',
                                                   'not-tradable:status=SETTLING', 'absent-from-snapshot']
    for mutate in (lambda d: d['symbols'].pop(), lambda d: d['symbols'].reverse(),
                   lambda d: d.__setitem__('core_10', d['core_10'][::-1])):
        bad = copy.deepcopy(u)
        mutate(bad)
        with pytest.raises(S.StaticUniverseError):
            S.validate(bad, snap)
    snap_short = S.reduce_exchange_info(raw_info(ROWS[:30]))
    with pytest.raises(S.StaticUniverseError, match='exactly 40'):
        S.build_static(ranked(), snap_short, SOURCE)


# 2. uniqueness
def test_uniqueness(built):
    u, snap = built
    bad = copy.deepcopy(u)
    bad['symbols'][5]['symbol'] = bad['symbols'][4]['symbol']
    resync_cores(bad)
    with pytest.raises(S.StaticUniverseError, match='duplicate'):
        S.validate(bad, snap)


# 3. existence / tradability at the snapshot; gold never counts
def test_tradability(built):
    u, snap = built
    assert [g['symbol'] for g in u['inactive_gold']] == ['PAXGUSDT', 'XAUUSDT']
    assert all(g['status'] == 'inactive' and not g['counts_toward_40'] for g in u['inactive_gold'])
    for change in ({'status': 'SETTLING'}, {'underlyingType': 'INDEX'}, {'contractType': 'TRADIFI_PERPETUAL'}):
        rows = [dict(r, **change) if r['symbol'] == 'C07USDT' else r for r in ROWS]
        with pytest.raises(S.StaticUniverseError, match='C07USDT: not-tradable'):
            S.validate(u, S.reduce_exchange_info(raw_info(rows)))
    with pytest.raises(S.StaticUniverseError, match='absent-from-snapshot'):
        S.validate(u, S.reduce_exchange_info(raw_info([r for r in ROWS if r['symbol'] != 'C07USDT'])))
    bad = copy.deepcopy(u)
    bad['symbols'][0]['symbol'] = 'PAXGUSDT'
    resync_cores(bad)
    with pytest.raises(S.StaticUniverseError, match='inactive-gold'):
        S.validate(bad, snap)


# 4. digest mismatch is refused
def test_digest_mismatch(built):
    u, snap = built
    bad = copy.deepcopy(u)
    bad['sources']['manifest_digest'] = 'd' * 64
    with pytest.raises(S.StaticUniverseError, match='file digest'):
        S.validate(bad, snap)
    bad = copy.deepcopy(u)
    bad['list_digest'] = '0' * 64
    with pytest.raises(S.StaticUniverseError, match='list digest'):
        S.validate(bad, snap)
    tampered = copy.deepcopy(snap)
    tampered['server_time_ms'] += 1
    with pytest.raises(S.StaticUniverseError, match='snapshot digest'):
        S.validate(u, tampered)
    other = S.reduce_exchange_info(raw_info(ROWS + [row('NEWUSDT')]))
    with pytest.raises(S.StaticUniverseError, match='different exchangeInfo'):
        S.validate(u, other)


# 5. deterministic rebuild is byte-identical; written files are immutable
def test_deterministic_rebuild(built, tmp_path):
    u, snap = built
    again = S.build_static(ranked(), S.reduce_exchange_info(raw_info(ROWS)), SOURCE)
    assert S.dumps(again) == S.dumps(u)
    p = str(tmp_path / 'u.json')
    S.write_once(u, p)
    S.write_once(again, p)                                              # identical bytes: no-op
    with pytest.raises(S.StaticUniverseError, match='immutable'):
        S.write_once(S.build_static(ranked()[1:], snap, SOURCE), p)
    bad = copy.deepcopy(u)
    bad['ranked_source'][0][2] = '1'                                    # source qv no longer matches the list
    bad['digest'] = S.body_digest(bad)
    with pytest.raises(S.StaticUniverseError, match='byte-identical'):
        S.validate(bad, snap)


def test_committed_artifact():
    path = os.path.join(UDIR, 'static-top40-minhist365-contract-20261011.json')
    snap_path = os.path.join(UDIR, 'binance-um-exchangeinfo-v2-20261011.json')
    assert S.main(['verify', path, '--snapshot', snap_path]) == 0
    with open(path, encoding='ascii') as f:
        u = json.load(f)
    assert u['label'] == 'CURRENT-UNIVERSE / SURVIVOR-BIASED' and u['owner_approved'] is True
    assert u['owner_approval']['approved_at_sha'] == '487c2c855572321b9c53119e6936f3ba5c6843ce'
    assert u['ranking_rule']['rule_id'] == 'qv30d-top40-minhist365' and u['ranking_rule']['min_history_days'] == 365
    with open(os.path.join(UDIR, 'binance-um-klines1d-recent-v2-20261011.json'), encoding='ascii') as f:
        recent = json.load(f)
    assert recent['digest'] == S.body_digest(recent) == u['sources']['recent_klines']['digest']
    assert recent['instant_ms'] == u['ranking_rule']['instant_ms']
    assert u['ranking_rule']['base_rule_id'] == 'pit-top40-qv30d-v4' and u['core_10'][:2] == ['BTCUSDT', 'ETHUSDT']


# owner rule 10 Oct 2026: >= 365 days of continuous daily history at the instant
def test_min_history_365():
    snap = S.reduce_exchange_info(raw_info(ROWS))
    u = S.build_static(ranked({'C02USDT': 364, 'C03USDT': 365}), snap, SOURCE)
    syms = [t['symbol'] for t in u['symbols']]
    assert 'C02USDT' not in syms and 'C03USDT' in syms and syms[-1] == 'C41USDT'
    assert u['excluded_by_history'] == [{'old_rank': 2, 'source_rank': 2, 'symbol': 'C02USDT', 'history_days': 364}]
    S.validate(u, snap)
    # a short-history symbol ranked below the old top 40 is not recorded (no catalog)
    assert S.build_static(ranked({'C44USDT': 10}), snap, SOURCE)['excluded_by_history'] == []
    bad = copy.deepcopy(u)
    bad['symbols'][1]['history_days'] = 364
    with pytest.raises(S.StaticUniverseError, match='history'):
        S.validate(bad, snap)


def test_history_days_counts_current_segment_only():
    D, inst = S.DAY, 1000 * S.DAY
    bars = {inst - k * D: 1 for k in range(1, 501) if k != 366}       # gap 366 days back: a relisting
    assert S.history_days(bars, inst) == 365
    del bars[inst - 200 * D]
    assert S.history_days(bars, inst) == 199
    assert S.history_days({inst - 2 * D: 1}, inst) == 0                # no bar closing at the instant


# bounded recent refresh (no network: a fake getter); digest / overlap mismatches are refused
def test_recent_refresh_and_merge():
    D = S.DAY
    store_end = 20 * D
    m = {'files': [{'series': 'klines', 'interval': '1d', 'symbol': 'C01USDT', 'last_open_ms': store_end - D},
                   {'series': 'klines', 'interval': '1d', 'symbol': 'DEADUSDT', 'last_open_ms': store_end - 2 * D}]}
    snap = S.reduce_exchange_info(raw_info(ROWS), 23 * D + 5)
    assert S.instant_of(snap) == 23 * D
    urls = []

    def get(url):
        urls.append(url)
        return json.dumps([[t, 0, 0, 0, 0, 0, t + D - 1, '7.5'] for t in range(store_end, 24 * D, D)]).encode()
    r = S.fetch_recent(m, snap, pause_s=0, get=get)
    assert list(r['symbols']) == ['C01USDT'] and len(urls) == 1          # SETTLING DEADUSDT is not fetched
    assert [b[0] for b in r['symbols']['C01USDT']['bars']] == [20 * D, 21 * D, 22 * D]   # day 23 is not complete
    daily = {'C01USDT': {store_end - D: S.U.Decimal(1)}}
    S.merge_recent(daily, r, snap)
    assert S.history_days(daily['C01USDT'], 23 * D) == 4
    with pytest.raises(S.StaticUniverseError, match='overlaps'):
        S.merge_recent(daily, r, snap)
    bad = copy.deepcopy(r)
    bad['symbols']['C01USDT']['bars'][0][1] = '9'
    with pytest.raises(S.StaticUniverseError, match='digest'):
        S.merge_recent({'C01USDT': {}}, bad, snap)


# owner approval: only the two approval fields change, and the approval is pinned to the exact PENDING list
def test_owner_approval_is_pinned_to_the_exact_list(built):
    u, snap = built
    a = S.approve(u, approved_sha='0' * 40, date_cairo='2026-10-11')
    S.validate(a, snap)
    assert a['owner_approved'] is True and a['owner_approval']['pending_digest'] == u['digest']
    assert {k for k in a if a[k] != u.get(k)} == {'owner_approved', 'owner_approval', 'digest'}
    with pytest.raises(S.StaticUniverseError):
        S.approve(a, approved_sha='0' * 40, date_cairo='2026-10-11')              # approve once only
    moved = copy.deepcopy(a)                                                    # approval carried to another list
    moved['symbols'][0], moved['symbols'][1] = moved['symbols'][1], moved['symbols'][0]
    moved['digest'] = S.body_digest(moved)
    with pytest.raises(S.StaticUniverseError):
        S.validate(moved, snap)
    forged = copy.deepcopy(a)                                                   # approval record re-pinned
    forged['owner_approval']['pending_digest'] = 'f' * 64
    forged['digest'] = S.body_digest(forged)
    with pytest.raises(S.StaticUniverseError, match='approval does not pin'):
        S.validate(forged, snap)
    half = copy.deepcopy(u)                                                     # flag flipped without a record
    half['owner_approved'] = True
    half['digest'] = S.body_digest(half)
    with pytest.raises(S.StaticUniverseError):
        S.validate(half, snap)


# Codex 6106070994: a reused ticker (old contract's archive continues under the same symbol) counts only from the
# current contract's onboardDate
def test_reused_ticker_counts_from_current_contract():
    inst = SOURCE['instant_ms']
    rows = [row(r['symbol'], r['status'], r['contractType'], r['quoteAsset'], r['underlyingType'],
                inst - 300 * S.DAY + 1234 if r['symbol'] == 'C05USDT' else r['onboardDate']) for r in ROWS]
    snap = S.reduce_exchange_info(raw_info(rows))
    u = S.build_static(ranked({'C05USDT': 2000}), snap, SOURCE)            # archive says 2000 continuous days
    assert 'C05USDT' not in u['core_10'] and u['symbols'][-1]['symbol'] == 'C41USDT'
    assert u['excluded_by_history'] == [{'old_rank': 5, 'source_rank': 9, 'symbol': 'C05USDT', 'history_days': 299}]
    S.validate(u, snap)
    with pytest.raises(S.StaticUniverseError, match='onboardDate'):
        S.build_static(ranked(), S.reduce_exchange_info(raw_info([dict(r, onboardDate=None) for r in ROWS])), SOURCE)
