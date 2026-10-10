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


def universe(members, monday=None):
    """A minimal committed-universe stand-in: one week whose crypto book selected `members`."""
    return {'universe_id': 'fx-universe', 'digest': H,
            'weeks': [{'monday_ms': ms('2026-04-06') if monday is None else monday,
                       'books': {'crypto': {'members': sorted(members)}}}]}


def build(store, snaps=None, records=(), cands=None):
    """Default: every fixture symbol except the gold pilot is a top-40 candidate (so it gets a detailed row)."""
    m = manifest(store)
    if cands is None:
        cands = sorted({f['symbol'] for f in m['files']} - set(C.GOLD_PILOT))
    return C.build(snaps if snaps is not None else [base_snap()], ann(*records), m, universe(cands),
                   classes_id='fx-classes')


# ---------------------------------------------------------------- determinism + digest

def test_deterministic_byte_identical_and_order_independent(store):
    a = build(store, records=[rec('L1', 'listing', 'XAUUSDT', ms('2025-12-11') + 8 * HOUR, cls='commodity'),
                              rec('L2', 'listing', 'BTCUSDT', ms('2024-01-01') + 8 * HOUR, cls='crypto')])
    s = base_snap()
    s['symbols'] = list(reversed(s['symbols']))
    b = build(store, snaps=[s], records=[rec('L2', 'listing', 'BTCUSDT', ms('2024-01-01') + 8 * HOUR, cls='crypto'),
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
    (d / 'uni.json').write_text(json.dumps(universe(['BTCUSDT'])))
    args = ['--exchange-info', str(d / 'snap.json'), '--announcements', str(d / 'ann.json'),
            '--manifest', str(d / 'man.json'), '--universe', str(d / 'uni.json'), '--classes-id', 'fx']
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
    assert C.main(['coverage'] + args) == 0                                                  # in memory, no file
    with pytest.raises(SystemExit):                                                          # the rule is gone
        C.main(['build', '--out', str(d / 'x.json'), '--tradfi-cutoff', 'TF'] + args)


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






# ---------------------------------------------------------------- disagreement / missing -> UNKNOWN

def test_announcement_vs_exchangeinfo_disagreement_is_unknown(store):
    c = build(store, records=[rec('L1', 'listing', 'XAUUSDT', ms('2025-12-11') + 8 * HOUR, cls='crypto')])
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


def test_missing_source_is_unknown_no_chronology_rule(store):
    # Codex PR #57 6095886569 ruling 2: listing before the first TradFi perp is absence-based inference, not identity
    put_daily(store, 'OLDUSDT', '2024-02-01', 10)              # delisted pre-TradFi contract, no exchangeInfo entry
    put_daily(store, 'NEWUSDT', '2026-05-01', 10)              # post-TradFi, no source at all
    c = build(store)
    assert rows(c, 'OLDUSDT') == [('UNKNOWN', 'UNKNOWN', ms('2024-02-01'), None, 'UNKNOWN:no-documented-listing')]
    assert rows(c, 'NEWUSDT') == [('UNKNOWN', 'UNKNOWN', ms('2026-05-01'), None, 'UNKNOWN:no-documented-listing')]
    assert not C.addressable(C.resolve_at(c, 'OLDUSDT', ms('2024-02-05')))
    assert 'tradfi-launch' not in C.KINDS and 'tradfi_cutoff' not in C.build.__code__.co_varnames
    with pytest.raises(C.ClassifyError, match='kind'):         # a chronology record is refused outright
        build(store, records=[rec('TF', 'tradfi-launch', None, CUT)])
    # only a dated, cited listing record makes it positive
    c = build(store, records=[rec('LO', 'listing', 'OLDUSDT', ms('2024-02-01') + 9 * HOUR, cls='crypto')])
    assert rows(c, 'OLDUSDT')[0][:2] == ('crypto', 'unspecified') and C.active(C.resolve_at(c, 'OLDUSDT', ms('2024-02-05')))


def test_unmapped_underlying_fails_closed(store):
    put_daily(store, 'MANTRAUSDT', '2026-03-04', 5)
    put_daily(store, 'DEFIUSDT', '2024-01-01', 5)
    s = base_snap()
    s['symbols'] += [xi('MANTRAUSDT', subs=('RWA', 'Crypto'), onboard=ms('2026-03-04') + 8 * HOUR),
                     xi('DEFIUSDT', ut='INDEX', subs=('Index',), onboard=ms('2024-01-01') + 8 * HOUR)]
    c = build(store, snaps=[s])
    assert rows(c, 'MANTRAUSDT')[0][4] == 'UNKNOWN:no-class-source(xinfo:s1=rwa-base-unmapped:MANTRA)'
    assert rows(c, 'DEFIUSDT')[0][4] == 'UNKNOWN:no-class-source(xinfo:s1=index-not-tagged-crypto)'
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
    assert rows(c, 'BTCUSDT')[0][:2] == ('crypto', 'coin')
    assert len({r['class'] for r in c['rows'] if r['symbol'] in ('XAUUSDT', 'PAXGUSDT', 'BTCUSDT')}) == 3
    scope = {r['symbol']: r['scope'] for r in c['rows']}
    assert scope['XAUUSDT'] == scope['PAXGUSDT'] == 'gold-pilot' and scope['BTCUSDT'] == 'crypto-research'
    # XAUT is not in the pilot: recognised as tokenized gold only to keep it out of the crypto book
    assert rows(c, 'XAUTUSDT') == [('OUT_OF_SCOPE', 'DEFERRED', ms('2026-03-26'), None,
                                    'OUT_OF_SCOPE:tokenized-gold-not-in-pilot')]
    assert scope['XAUTUSDT'] == 'deferred' and not C.addressable(C.resolve_at(c, 'XAUTUSDT', ms('2026-04-01')))

    # Codex #53 6095682220: only crypto is active; the gold-pilot identities are recorded but inactive
    at = ms('2026-04-01')
    assert C.active(C.resolve_at(c, 'BTCUSDT', at))
    for sym in ('XAUUSDT', 'PAXGUSDT'):
        assert C.addressable(C.resolve_at(c, sym, at)) and not C.active(C.resolve_at(c, sym, at))
    assert not C.active(C.resolve_at(c, 'XAUTUSDT', at)) and not C.active(None)


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


# ---------------------------------------------------------------- Codex rulings 6094970068 (2: identity) + scope cut

def test_identity_mismatch_is_unknown(store):
    s = base_snap()
    s['symbols'][0] = dict(s['symbols'][0], pair='BTCUSD')
    assert rows(build(store, snaps=[s]), 'BTCUSDT')[0][4] == 'UNKNOWN:identity-mismatch'


def test_archive_before_documented_listing_is_unknown(store):
    put_daily(store, 'RELUSDT', '2024-03-01', 20)                    # archived since 1 March, onboardDate 15 March
    s = base_snap()
    s['symbols'].append(xi('RELUSDT', onboard=ms('2024-03-15') + 8 * HOUR))
    c = build(store, snaps=[s])
    t = ms('2024-03-15') + 8 * HOUR
    assert rows(c, 'RELUSDT') == [
        ('UNKNOWN', 'UNKNOWN', ms('2024-03-01'), t, 'UNKNOWN:archive-precedes-documented-listing'),
        ('crypto', 'coin', t, None, 'identity-fact-at-listing:exchangeinfo')]




def test_announcement_only_row_and_provenance(store):
    put_daily(store, 'GONEUSDT', '2026-02-01', 10)                   # delisted: absent from exchangeInfo
    t = ms('2026-02-01') + 6 * HOUR
    c = build(store, records=[rec('LG', 'listing', 'GONEUSDT', t, published=t - 2 * DAY, cls='commodity',
                                  sub='silver-spot')])
    assert rows(c, 'GONEUSDT')[0][4] == 'OUT_OF_SCOPE:non-crypto-class:commodity/silver-spot'   # never active
    c = build(store, records=[rec('LG', 'listing', 'GONEUSDT', t, published=t - 2 * DAY, cls='crypto')])
    (r,) = [r for r in c['rows'] if r['symbol'] == 'GONEUSDT']
    assert (r['class'], r['subclass'], r['basis'], r['provenance'], r['scope']) == (
        'crypto', 'unspecified', 'identity-fact-at-listing:announcement', 'contemporaneous', 'crypto-research')


def _idx_snaps(t1, t2):
    on = ms('2026-04-06') + 13 * HOUR
    return [snap('s1', t1, [xi('IDXUSDT', onboard=on)]),
            snap('s2', t2, [xi('IDXUSDT', ut='INDEX', subs=('Index', 'Crypto'), onboard=on)])], on


def test_class_change_creates_new_dated_row_never_before_publication(store):
    put_daily(store, 'IDXUSDT', '2026-04-06', 20)
    eff, pub = ms('2026-04-10'), ms('2026-04-12')                   # retroactive notice: dated at publication
    snaps, on = _idx_snaps(ms('2026-04-07'), ms('2026-05-01'))
    c = build(store, snaps=snaps, records=[rec('CC', 'class-change', 'IDXUSDT', eff, published=pub,
                                               cls='crypto-index', sub='index')])
    assert rows(c, 'IDXUSDT') == [
        ('crypto', 'coin', on, pub, 'identity-fact-at-listing:exchangeinfo'),
        ('crypto-index', 'index', pub, None, 'class-change:exchangeinfo+announcement')]
    assert C.resolve_at(c, 'IDXUSDT', pub - 1)['class'] == 'crypto'
    assert C.resolve_at(c, 'IDXUSDT', pub)['class'] == 'crypto-index'


def test_undocumented_change_between_snapshots_leaves_unknown_gap(store):
    put_daily(store, 'IDXUSDT', '2026-04-06', 20)
    snaps, on = _idx_snaps(ms('2026-05-01'), ms('2026-06-01'))
    r = rows(build(store, snaps=snaps), 'IDXUSDT')
    assert [x[0] for x in r] == ['crypto', 'UNKNOWN', 'crypto-index']
    assert r[0][3] == r[1][2] == ms('2026-05-01') + 1 and r[1][3] == r[2][2] == ms('2026-06-01')
    assert r[1][4] == 'UNKNOWN:undocumented-class-change'


def test_tradfi_is_deferred_out_of_scope_and_audited(store):
    put_daily(store, 'CORNUSDT', '2026-05-01', 5)
    s = base_snap()
    s['symbols'].append(tradfi('CORNUSDT', 'COMMODITY', onboard=ms('2026-05-01') + 8 * HOUR))
    c = build(store, snaps=[s])
    for sym, t in (('AAPLUSDT', ms('2026-04-06')), ('CORNUSDT', ms('2026-05-01'))):
        (r,) = [r for r in c['rows'] if r['symbol'] == sym]
        assert (r['class'], r['scope'], r['basis'], r['effective_from_ms'], r['provenance']) == (
            'OUT_OF_SCOPE', 'deferred', 'OUT_OF_SCOPE:tradfi-deferred', t, None)
        assert r['sources'] == ['manifest:fx-archive', 'xinfo:s1'] and not C.addressable(r)
    assert c['row_counts'] == {'crypto-research:crypto': 1, 'deferred:OUT_OF_SCOPE': 2, 'gold-pilot:commodity': 1,
                               'gold-pilot:tokenized-gold': 1}


def test_gold_pilot_identity_mismatch_is_unknown(store):
    s = base_snap()
    s['symbols'][2] = xi('PAXGUSDT', onboard=ms('2025-03-27') + 10 * HOUR)    # RWA tag missing -> plain coin
    r = rows(build(store, snaps=[s]), 'PAXGUSDT')
    assert r == [('UNKNOWN', 'UNKNOWN', ms('2025-03-27') + 10 * HOUR, None,
                  'UNKNOWN:gold-pilot-identity-mismatch:crypto/coin')]


# ---------------------------------------------------------------- active set + blanket exclusion (PR #57 rulings)

def test_crypto_index_keeps_class_but_is_never_active(store):
    put_daily(store, 'BTCDOMUSDT', '2024-01-01', 20)
    s = base_snap()
    s['symbols'].append(xi('BTCDOMUSDT', ut='INDEX', base='BTCDOM', subs=('Index', 'Crypto'),
                           onboard=ms('2024-01-01') + 8 * HOUR))
    c = build(store, snaps=[s])
    r = C.resolve_at(c, 'BTCDOMUSDT', ms('2024-01-10'))
    assert (r['class'], r['scope']) == ('crypto-index', 'crypto-research')      # explicit class preserved
    assert C.addressable(r) and not C.active(r)                                  # ruling 1: not in the top-40 book
    assert C.active(C.resolve_at(c, 'BTCUSDT', ms('2024-01-10')))


def _narrow(store):
    """The owner scope correction: only BTCUSDT is a candidate; every other fixture symbol is blanket-excluded."""
    put_daily(store, 'XAUTUSDT', '2026-03-26', 5)
    put_daily(store, 'BTCDOMUSDT', '2024-01-01', 20)
    put_daily(store, 'ROGUEUSDT', '2024-01-01', 20)           # in the archive, in no source, never selected
    s = base_snap()
    s['symbols'] += [xi('XAUTUSDT', subs=('RWA', 'Crypto'), onboard=ms('2026-03-26') + 14 * HOUR),
                     xi('BTCDOMUSDT', ut='INDEX', base='BTCDOM', subs=('Index', 'Crypto'),
                        onboard=ms('2024-01-01') + 8 * HOUR)]
    m = manifest(store)
    return C.build([s], ann(), m, universe(['BTCUSDT']), classes_id='fx-narrow'), m


def test_blanket_exclusion_detailed_rows_only_for_candidates_and_gold(store):
    c, m = _narrow(store)
    assert sorted({r['symbol'] for r in c['rows']}) == ['BTCUSDT', 'PAXGUSDT', 'XAUUSDT']
    excluded = sorted({f['symbol'] for f in m['files']} - {'BTCUSDT', 'PAXGUSDT', 'XAUUSDT'})
    assert excluded == ['AAPLUSDT', 'BTCDOMUSDT', 'ROGUEUSDT', 'XAUTUSDT']
    assert c['exclusion'] == {'class': 'OUT_OF_SCOPE', 'scope': 'deferred', 'count': 4,
                              'symbols_sha256': hashlib.sha256(M.canonical(excluded)).hexdigest()}
    assert c['symbols'] == 7 and c['detailed_symbols'] == 3
    C.check(c, m)
    at = ms('2026-04-07')
    assert C.active_at(c, m, 'BTCUSDT', at)
    for sym in ('XAUUSDT', 'PAXGUSDT'):                       # recorded identities, inactive
        assert C.addressable(C.resolve_at(c, sym, at)) and not C.active_at(c, m, sym, at)


@pytest.mark.parametrize('sym', ['ROGUEUSDT',                 # unlisted / never selected
                                 'AAPLUSDT',                  # TradFi
                                 'BTCDOMUSDT',                # crypto-index
                                 'XAUTUSDT',                  # tokenized gold outside the pilot
                                 'NOSUCHUSDT'])               # not even in the manifest
def test_exclusion_is_fail_closed(store, sym):
    c, m = _narrow(store)
    for t in (ms('2024-01-10'), ms('2026-04-07'), ms('2026-09-01')):
        assert C.resolve_at(c, sym, t) is None
        assert not C.addressable(C.resolve_at(c, sym, t)) and not C.active_at(c, m, sym, t)


def test_tampered_exclusion_or_smuggled_row_refused(store):
    c, m = _narrow(store)
    bad = json.loads(json.dumps(c))
    bad['exclusion']['symbols_sha256'] = H                    # tampered digest, outer digest not refreshed
    with pytest.raises(C.ClassifyError, match='digest'):
        C.active_at(bad, m, 'BTCUSDT', ms('2026-04-07'))
    bad['digest'] = C.digest_of(bad)                          # ... and refreshed: the manifest recomputation catches it
    with pytest.raises(C.ClassifyError, match='exclusion'):
        C.check(bad, m)
    smuggled = json.loads(json.dumps(c))                      # a TradFi symbol slipped in as an active crypto row
    smuggled['rows'].append(dict(smuggled['rows'][0], symbol='AAPLUSDT'))
    smuggled['detailed_symbols'] += 1
    smuggled['digest'] = C.digest_of(smuggled)
    with pytest.raises(C.ClassifyError, match='exclusion'):
        C.active_at(smuggled, m, 'AAPLUSDT', ms('2026-04-07'))


def test_candidate_absent_from_manifest_refused(store):
    with pytest.raises(C.ClassifyError, match='absent from the manifest'):
        C.build([base_snap()], ann(), manifest(store), universe(['BTCUSDT', 'GHOSTUSDT']), classes_id='x')


def test_top40_candidates_and_coverage(store):
    u = universe(['BTCUSDT', 'OLDUSDT'], monday=ms('2024-02-05'))
    assert C.top40_candidates(u) == {'BTCUSDT': [ms('2024-02-05')], 'OLDUSDT': [ms('2024-02-05')]}
    put_daily(store, 'OLDUSDT', '2024-02-01', 10)
    c = C.build([base_snap()], ann(), manifest(store), u, classes_id='x')
    cov = C.coverage(c, C.top40_candidates(u))
    assert (cov['candidates'], cov['positive'], cov['not_positive']) == (2, 1, 1)
    assert cov['not_positive_symbols'] == {'OLDUSDT': ['UNKNOWN:no-documented-listing']}


def test_committed_classification_inputs_are_lf_in_every_checkout():
    # Codex #53 6096066849 (same class): core.autocrlf=true must not rewrite the hash-pinned classification inputs
    import subprocess
    d = 'research_evidence/inputs/instrument-classes-v2'
    files = subprocess.run(['git', 'ls-files', '--', d], cwd=ROOT, capture_output=True, text=True,
                           check=True).stdout.split()
    assert files and all(f.endswith('.json') for f in files)
    attr = subprocess.run(['git', 'check-attr', 'text', 'eol', '--', *files], cwd=ROOT, capture_output=True,
                          text=True, check=True).stdout
    for f in files:
        assert f'{f}: text: set' in attr and f'{f}: eol: lf' in attr, attr
        with open(os.path.join(ROOT, f), 'rb') as fh:
            assert b'\r' not in fh.read()
