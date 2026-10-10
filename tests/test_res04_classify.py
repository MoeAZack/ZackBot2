"""RES-01 R4-0 item 2: the sourced point-in-time classification builder tools/research/classify.py, on small synthetic
exchangeInfo / announcement snapshots and a synthetic archive store in tmp_path (no network, no real data)."""
import hashlib
import io
import json
import os
import sys
import zipfile
from datetime import datetime, timezone

import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, 'tools', 'research'))
import classify as C                                                                        # noqa: E402
import manifest as M                                                                        # noqa: E402

DAY = 86_400_000
HOUR = 3_600_000
HDR = M.KLINE_HEADER.decode()
H = 'ab' * 32


def ms(s):
    return int(datetime.strptime(s, '%Y-%m-%d').replace(tzinfo=timezone.utc).timestamp() * 1000)


def put_daily(store, sym, start, n):
    t0 = ms(start)
    months = {}
    for i in range(n):
        t = t0 + i * DAY
        ym = datetime.fromtimestamp(t / 1000, tz=timezone.utc).strftime('%Y-%m')
        months.setdefault(ym, []).append(f'{t},1,2,0.5,1.5,10,{t + DAY - 1},1000,5,4,1000,0')
    for ym, rows in months.items():
        rel = f'um/monthly/klines/{sym}/1d/{sym}-1d-{ym}.zip'
        p = store.joinpath(*rel.split('/'))
        p.parent.mkdir(parents=True, exist_ok=True)
        buf = io.BytesIO()
        with zipfile.ZipFile(buf, 'w') as z:
            z.writestr(rel.rsplit('/', 1)[1][:-4] + '.csv', '\n'.join([HDR] + rows) + '\n')
        p.write_bytes(buf.getvalue())
        (p.parent / (p.name + '.ok')).write_text(hashlib.sha256(buf.getvalue()).hexdigest())


def manifest(store):
    return M.build(['um/monthly'], manifest_id='fx-archive', source_class='archive-verified', base=str(store),
                   data_root='binance_um', survivor_only=False, loader=M.ARCHIVE_LOADER)


def xi(sym, ut='COIN', ct='PERPETUAL', base=None, subs=('Crypto',), onboard=None, quote='USDT'):
    return {'symbol': sym, 'pair': sym, 'baseAsset': base or sym[:-4], 'quoteAsset': quote, 'contractType': ct,
            'underlyingType': ut, 'underlyingSubType': list(subs), 'onboardDate': onboard}


def tradfi(sym, ut, base=None, onboard=None):
    return xi(sym, ut=ut, ct='TRADIFI_PERPETUAL', base=base, subs=('TradFi',), onboard=onboard)


def snap(sid, retrieved, entries):
    return {'format': C.SNAPSHOT_FORMAT, 'snapshot_id': sid, 'retrieval_url': C.EXCHANGEINFO_URL,
            'retrieved_ms': retrieved, 'response_sha256': H, 'scope': 'fixture',
            'response_server_time_ms': None, 'symbols': entries}


def rec(rid, kind, sym, eff, published=None, cls=None, sub=None):
    published = eff - DAY if published is None else published
    return {'record_id': rid, 'kind': kind, 'symbol': sym, 'url': f'https://www.binance.com/en/support/announcement/{rid}',
            'published_ms': published, 'retrieved_ms': published + 10 * DAY, 'page_sha256': H, 'effective_ms': eff,
            'class': cls, 'subclass': sub}


def ann(*records):
    return {'format': C.ANNOUNCEMENTS_FORMAT, 'records': list(records)}


def rows(c, sym):
    return [(r['class'], r['subclass'], r['effective_from_ms'], r['effective_to_ms'], r['basis'])
            for r in c['rows'] if r['symbol'] == sym]


RETR = ms('2026-10-01')
CUT = ms('2025-12-11')


@pytest.fixture
def store(tmp_path):
    put_daily(tmp_path, 'BTCUSDT', '2024-01-01', 40)
    put_daily(tmp_path, 'XAUUSDT', '2025-12-11', 20)
    put_daily(tmp_path, 'PAXGUSDT', '2025-03-27', 20)
    put_daily(tmp_path, 'AAPLUSDT', '2026-04-06', 20)
    return tmp_path


def base_snap(retrieved=RETR):
    return snap('s1', retrieved, [
        xi('BTCUSDT', subs=('PoW', 'Crypto'), onboard=ms('2024-01-01') + 8 * HOUR),
        tradfi('XAUUSDT', 'COMMODITY', base='XAU', onboard=ms('2025-12-11') + 8 * HOUR),
        xi('PAXGUSDT', subs=('RWA', 'Crypto'), onboard=ms('2025-03-27') + 10 * HOUR),
        tradfi('AAPLUSDT', 'EQUITY', onboard=ms('2026-04-06') + 13 * HOUR)])


def build(store, snaps=None, records=(), cutoff=None):
    return C.build(snaps if snaps is not None else [base_snap()], ann(*records), manifest(store),
                   classes_id='fx-classes', tradfi_cutoff=cutoff)


# ---------------------------------------------------------------- determinism + digest

def test_deterministic_byte_identical_and_order_independent(store):
    a = build(store, records=[rec('L1', 'listing', 'XAUUSDT', ms('2025-12-11') + 8 * HOUR, cls='commodity'),
                              rec('L2', 'listing', 'AAPLUSDT', ms('2026-04-06') + 13 * HOUR, cls='equity')])
    s = base_snap()
    s['symbols'] = list(reversed(s['symbols']))
    b = build(store, snaps=[s], records=[rec('L2', 'listing', 'AAPLUSDT', ms('2026-04-06') + 13 * HOUR, cls='equity'),
                                         rec('L1', 'listing', 'XAUUSDT', ms('2025-12-11') + 8 * HOUR, cls='commodity')])
    assert C.dumps(a) == C.dumps(b)
    assert a['digest'] == C.digest_of(a) == hashlib.sha256(M.canonical({k: v for k, v in a.items()
                                                                         if k != 'digest'})).hexdigest()
    assert a['format'] == 'zb-instrument-classes/2' and a['symbols'] == 4
    for r in a['rows']:
        assert r['sources'] and all(x in a['sources'] for x in r['sources'])


def test_cli_build_verify_and_immutable(store, tmp_path, capsys):
    d = tmp_path / 'in'
    d.mkdir()
    (d / 'snap.json').write_text(json.dumps(base_snap()))
    (d / 'ann.json').write_text(json.dumps(ann()))
    m = manifest(store)
    (d / 'man.json').write_text(json.dumps(m))
    args = ['--exchange-info', str(d / 'snap.json'), '--announcements', str(d / 'ann.json'),
            '--manifest', str(d / 'man.json'), '--classes-id', 'fx']
    out = d / 'classes.json'
    assert C.main(['build', '--out', str(out)] + args) == 0
    first = out.read_bytes()
    assert C.main(['build', '--out', str(out)] + args) == 0 and out.read_bytes() == first   # identical: no-op
    assert C.main(['verify', str(out)] + args) == 0
    c = json.loads(first)
    assert c['sources']['xinfo:s1']['file_sha256'] == hashlib.sha256((d / 'snap.json').read_bytes()).hexdigest()
    (d / 'ann.json').write_text(json.dumps(ann(rec('L1', 'listing', 'BTCUSDT', ms('2024-01-01') + 8 * HOUR))))
    assert C.main(['verify', str(out)] + args) == 1                                          # input changed
    assert C.main(['build', '--out', str(out)] + args) == 2                                  # never overwritten
    assert out.read_bytes() == first


def test_extract_minimal_scoped_compact(store):
    body = {'timezone': 'UTC', 'symbols': [dict(xi('BTCUSDT', onboard=1), filters=[], status='TRADING'),
                                           dict(xi('ZZZUSDT', onboard=1), deliveryDate=4133404800000)]}
    raw = json.dumps(body).encode()
    s = C.extract(raw, retrieved_ms=5, snapshot_id='x')
    assert s['response_sha256'] == hashlib.sha256(raw).hexdigest() and set(s['symbols'][0]) == C.SNAP_SYMBOL_KEYS
    m = manifest(store)
    s = C.extract(raw, retrieved_ms=5, snapshot_id='x', manifest=m)
    assert [e['symbol'] for e in s['symbols']] == ['BTCUSDT'] and m['digest'] in s['scope']
    data = C.dumps_compact(s, 'symbols')
    assert json.loads(data) == s and data.count(b'\n') == 3                # header line, 1 symbol line, closing line


# ---------------------------------------------------------------- point-in-time dating

def test_effective_from_is_documented_listing_not_archive_or_retrieval(store):
    c = build(store)
    assert rows(c, 'XAUUSDT') == [('commodity', 'gold-spot', ms('2025-12-11') + 8 * HOUR, None,
                                   'identity-fact-at-listing:exchangeinfo')]
    r = C.resolve_at(c, 'XAUUSDT', ms('2025-12-11'))
    assert r is None and not C.addressable(r)                       # before listing: nothing in force
    row = C.resolve_at(c, 'XAUUSDT', ms('2025-12-12'))
    assert C.addressable(row) and row['class_first_observed_ms'] == RETR and row['provenance'] == 'retrospective'


def test_listing_announcement_within_tolerance_takes_earliest(store):
    t = ms('2025-12-11') + 7 * HOUR
    c = build(store, records=[rec('L1', 'listing', 'XAUUSDT', t, published=ms('2025-12-08'), cls='commodity',
                                  sub='gold-spot')])
    (r,) = [r for r in c['rows'] if r['symbol'] == 'XAUUSDT']
    assert r['effective_from_ms'] == t and r['class_first_observed_ms'] == ms('2025-12-08')
    assert r['sources'] == ['ann:L1', 'xinfo:s1'] and r['basis'] == 'identity-fact-at-listing:exchangeinfo+announcement'
    assert r['provenance'] == 'contemporaneous'


def test_class_change_creates_new_dated_row_never_before_publication(store):
    eff, pub = ms('2026-04-10'), ms('2026-04-12')                   # retroactive notice: dated at publication
    c = build(store, records=[rec('CC', 'class-change', 'AAPLUSDT', eff, published=pub, cls='pre-ipo', sub='premarket')],
              snaps=[base_snap(retrieved=ms('2026-04-07')),
                     snap('s2', ms('2026-05-01'), [tradfi('AAPLUSDT', 'PREMARKET', onboard=ms('2026-04-06') + 13 * HOUR)])])
    assert rows(c, 'AAPLUSDT') == [
        ('equity', 'equity/UNKNOWN', ms('2026-04-06') + 13 * HOUR, pub, 'identity-fact-at-listing:exchangeinfo'),
        ('pre-ipo', 'premarket', pub, None, 'class-change:exchangeinfo+announcement')]
    assert C.resolve_at(c, 'AAPLUSDT', pub - 1)['class'] == 'equity'
    assert C.resolve_at(c, 'AAPLUSDT', pub)['class'] == 'pre-ipo'


def test_undocumented_change_between_snapshots_leaves_unknown_gap(store):
    s2 = snap('s2', ms('2026-06-01'), [tradfi('AAPLUSDT', 'PREMARKET', onboard=ms('2026-04-06') + 13 * HOUR)])
    c = build(store, snaps=[base_snap(retrieved=ms('2026-05-01')), s2])
    r = rows(c, 'AAPLUSDT')
    assert [x[0] for x in r] == ['equity', 'UNKNOWN', 'pre-ipo']
    assert r[0][3] == r[1][2] == ms('2026-05-01') + 1 and r[1][3] == r[2][2] == ms('2026-06-01')
    assert r[1][4] == 'UNKNOWN:undocumented-class-change'


# ---------------------------------------------------------------- disagreement / missing -> UNKNOWN

def test_announcement_vs_exchangeinfo_disagreement_is_unknown(store):
    c = build(store, records=[rec('L1', 'listing', 'XAUUSDT', ms('2025-12-11') + 8 * HOUR, cls='equity')])
    (r,) = [r for r in c['rows'] if r['symbol'] == 'XAUUSDT']
    assert r['class'] == 'UNKNOWN' and r['basis'] == 'UNKNOWN:announcement-vs-exchangeinfo'
    assert not C.addressable(C.resolve_at(c, 'XAUUSDT', ms('2026-01-01')))
    assert r['sources'] == ['ann:L1', 'xinfo:s1']


def test_subclass_disagreement_and_announcements_disagreeing(store):
    c = build(store, records=[rec('L1', 'listing', 'XAUUSDT', ms('2025-12-11') + 8 * HOUR, cls='commodity',
                                  sub='silver-spot')])
    assert rows(c, 'XAUUSDT')[0][0] == 'UNKNOWN'
    c = build(store, records=[rec('L1', 'listing', 'XAUUSDT', ms('2025-12-11') + 8 * HOUR, cls='commodity'),
                              rec('L2', 'listing', 'XAUUSDT', ms('2025-12-11') + 8 * HOUR, cls='tokenized-gold')])
    assert rows(c, 'XAUUSDT')[0][4] == 'UNKNOWN:announcements-disagree'


def test_listing_time_disagreement_is_unknown(store):
    c = build(store, records=[rec('L1', 'listing', 'XAUUSDT', ms('2025-12-13'), cls='commodity')])
    assert rows(c, 'XAUUSDT')[0][4] == 'UNKNOWN:listing-time-disagreement'


def test_missing_source_is_unknown_and_cutoff_rule_is_bounded(store, tmp_path):
    put_daily(store, 'OLDUSDT', '2024-02-01', 10)              # delisted pre-TradFi contract, no exchangeInfo entry
    put_daily(store, 'NEWUSDT', '2026-05-01', 10)              # post-TradFi, no source at all
    c = build(store)
    assert rows(c, 'OLDUSDT') == [('UNKNOWN', 'UNKNOWN', ms('2024-02-01'), None, 'UNKNOWN:no-documented-listing')]
    launch = rec('TF', 'tradfi-launch', None, CUT)
    c = build(store, records=[launch], cutoff='TF')
    assert rows(c, 'OLDUSDT') == [
        ('crypto', 'pre-tradfi-cutoff', ms('2024-02-01'), ms('2024-02-11'), 'rule:pre-tradfi-cutoff'),
        ('UNKNOWN', 'UNKNOWN', ms('2024-02-11'), None, 'UNKNOWN:archive-beyond-pre-tradfi-run')]
    assert rows(c, 'NEWUSDT') == [('UNKNOWN', 'UNKNOWN', ms('2026-05-01'), None, 'UNKNOWN:no-documented-listing')]
    with pytest.raises(C.ClassifyError, match='tradfi-launch'):
        build(store, records=[launch], cutoff='NOPE')


def test_unmapped_underlying_fails_closed(store):
    put_daily(store, 'MANTRAUSDT', '2026-03-04', 5)
    put_daily(store, 'DEFIUSDT', '2024-01-01', 5)
    put_daily(store, 'CORNUSDT', '2026-05-01', 5)
    s = base_snap()
    s['symbols'] += [xi('MANTRAUSDT', subs=('RWA', 'Crypto'), onboard=ms('2026-03-04') + 8 * HOUR),
                     xi('DEFIUSDT', ut='INDEX', subs=('Index',), onboard=ms('2024-01-01') + 8 * HOUR),
                     tradfi('CORNUSDT', 'COMMODITY', onboard=ms('2026-05-01') + 8 * HOUR)]
    c = build(store, snaps=[s])
    assert rows(c, 'MANTRAUSDT')[0][4] == 'UNKNOWN:no-class-source(xinfo:s1=rwa-base-unmapped:MANTRA)'
    assert rows(c, 'DEFIUSDT')[0][4] == 'UNKNOWN:no-class-source(xinfo:s1=index-not-tagged-crypto)'
    assert rows(c, 'CORNUSDT')[0][4] == 'UNKNOWN:no-class-source(xinfo:s1=commodity-base-unmapped:CORN)'
    # an announcement can supply the missing class (Cowork-verified record)
    c = build(store, snaps=[s], records=[rec('LM', 'listing', 'MANTRAUSDT', ms('2026-03-04') + 8 * HOUR, cls='crypto')])
    assert rows(c, 'MANTRAUSDT')[0][:2] == ('crypto', 'unspecified')


def test_contract_type_mismatch_is_unknown(store):
    s = base_snap()
    s['symbols'][1] = xi('XAUUSDT', ut='COMMODITY', ct='PERPETUAL', base='XAU', onboard=ms('2025-12-11') + 8 * HOUR)
    assert rows(build(store, snaps=[s]), 'XAUUSDT')[0][4] == 'UNKNOWN:contract-type:COMMODITY/PERPETUAL'


# ---------------------------------------------------------------- gold-spot vs tokenized gold

def test_gold_spot_and_tokenized_gold_are_distinct_classes(store):
    put_daily(store, 'XAUTUSDT', '2026-03-26', 5)
    s = base_snap()
    s['symbols'].append(xi('XAUTUSDT', subs=('RWA', 'Crypto'), onboard=ms('2026-03-26') + 14 * HOUR))
    c = build(store, snaps=[s])
    assert rows(c, 'XAUUSDT')[0][:2] == ('commodity', 'gold-spot')
    assert rows(c, 'PAXGUSDT')[0][:2] == ('tokenized-gold', 'paxg')
    assert rows(c, 'XAUTUSDT')[0][:2] == ('tokenized-gold', 'xaut')
    assert rows(c, 'BTCUSDT')[0][:2] == ('crypto', 'coin') and rows(c, 'AAPLUSDT')[0][:2] == ('equity', 'equity/UNKNOWN')
    assert len({r['class'] for r in c['rows'] if r['symbol'] in ('XAUUSDT', 'PAXGUSDT', 'BTCUSDT')}) == 3


# ---------------------------------------------------------------- input validation

@pytest.mark.parametrize('mutate, match', [
    (lambda s: s.update(retrieval_url='https://example.com/x'), 'retrieval_url'),
    (lambda s: s.update(response_sha256='XYZ'), 'response_sha256'),
    (lambda s: s['symbols'].append(dict(s['symbols'][0])), 'duplicated'),
    (lambda s: s['symbols'][0].pop('onboardDate'), 'keys must be exactly'),
    (lambda s: s['symbols'][0].update(status='TRADING'), 'keys must be exactly'),     # minimal fields only
    (lambda s: s.update(scope=''), 'scope'),
])
def test_bad_snapshot_refused(store, mutate, match):
    s = base_snap()
    mutate(s)
    with pytest.raises(C.ClassifyError, match=match):
        build(store, snaps=[s])


def test_bad_records_refused(store):
    bad = rec('X', 'class-change', 'XAUUSDT', ms('2026-01-01'))                      # change without a class
    with pytest.raises(C.ClassifyError, match='class-change must assert'):
        build(store, records=[bad])
    bad = rec('X', 'listing', 'XAUUSDT', ms('2026-01-01'), cls='gold')
    with pytest.raises(C.ClassifyError, match='class must be null or one of'):
        build(store, records=[bad])
    bad = dict(rec('X', 'listing', 'XAUUSDT', ms('2026-01-01')), url='http://evil.example/')
    with pytest.raises(C.ClassifyError, match='url'):
        build(store, records=[bad])
    bad = rec('X', 'listing', 'XAUUSDT', ms('2026-01-01'), published=ms('2026-01-01'))
    bad['retrieved_ms'] = bad['published_ms'] - 1
    with pytest.raises(C.ClassifyError, match='published_ms <= retrieved_ms'):
        build(store, records=[bad])


# ---------------------------------------------------------------- Codex rulings 6094970068 (2: identity, 4: equity kind)

def test_identity_mismatch_is_unknown(store):
    s = base_snap()
    s['symbols'][0] = dict(s['symbols'][0], pair='BTCUSD')
    assert rows(build(store, snaps=[s]), 'BTCUSDT')[0][4] == 'UNKNOWN:identity-mismatch'


def test_archive_before_documented_listing_is_unknown_even_pre_tradfi(store):
    put_daily(store, 'RELUSDT', '2024-03-01', 20)                    # archived since 1 March, onboardDate 15 March
    s = base_snap()
    s['symbols'].append(xi('RELUSDT', onboard=ms('2024-03-15') + 8 * HOUR))
    c = build(store, snaps=[s], records=[rec('TF', 'tradfi-launch', None, CUT)], cutoff='TF')
    t = ms('2024-03-15') + 8 * HOUR
    assert rows(c, 'RELUSDT') == [
        ('UNKNOWN', 'UNKNOWN', ms('2024-03-01'), t, 'UNKNOWN:archive-precedes-documented-listing'),
        ('crypto', 'coin', t, None, 'identity-fact-at-listing:exchangeinfo')]


def test_equity_kind_only_from_announcement(store):
    t = ms('2026-04-06') + 13 * HOUR
    c = build(store, records=[rec('LA', 'listing', 'AAPLUSDT', t, cls='equity', sub='single-stock')])
    assert rows(c, 'AAPLUSDT')[0][:2] == ('equity', 'equity/single-stock')
    c = build(store, records=[rec('LA', 'listing', 'AAPLUSDT', t, cls='equity', sub='single-stock'),
                              rec('LB', 'listing', 'AAPLUSDT', t, cls='equity', sub='etf')])
    assert rows(c, 'AAPLUSDT')[0][4] == 'UNKNOWN:announcements-disagree'
    with pytest.raises(C.ClassifyError, match='equity subclass'):
        build(store, records=[rec('LA', 'listing', 'AAPLUSDT', t, cls='equity', sub='stock')])


def test_announcement_only_row_and_provenance(store):
    put_daily(store, 'GONEUSDT', '2026-02-01', 10)                   # delisted: absent from exchangeInfo
    t = ms('2026-02-01') + 6 * HOUR
    c = build(store, records=[rec('LG', 'listing', 'GONEUSDT', t, published=t - 2 * DAY, cls='commodity',
                                  sub='silver-spot')])
    (r,) = [r for r in c['rows'] if r['symbol'] == 'GONEUSDT']
    assert (r['class'], r['subclass'], r['basis'], r['provenance']) == (
        'commodity', 'silver-spot', 'identity-fact-at-listing:announcement', 'contemporaneous')
