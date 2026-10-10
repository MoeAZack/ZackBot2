"""RES-01 R1/R2: the `zb-binance-vision-zip/1` archive loader of tools/research/manifest.py and the PIT universe of
tools/research/universe.py, on small synthetic archive stores written to tmp_path (no network, no real data)."""
import gzip
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
import manifest as M                                                                        # noqa: E402
import universe as U                                                                        # noqa: E402

DAY = 86_400_000
HDR = M.KLINE_HEADER.decode()


def ms(s):
    return int(datetime.strptime(s, '%Y-%m-%d').replace(tzinfo=timezone.utc).timestamp() * 1000)


def kline(t, step, qv=1000.0):
    return f'{t},1,2,0.5,1.5,10,{t + step - 1},{qv},5,4,{qv},0'


def put_zip(store, rel, csv_text, *, ok=True, extra=None, ok_value=None):
    p = store.joinpath(*rel.split('/'))
    p.parent.mkdir(parents=True, exist_ok=True)
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, 'w') as z:
        z.writestr(rel.rsplit('/', 1)[1][:-4] + '.csv', csv_text)
        if extra:
            z.writestr(extra, 'x')
    raw = buf.getvalue()
    p.write_bytes(raw)
    if ok:
        (p.parent / (p.name + '.ok')).write_text(ok_value or hashlib.sha256(raw).hexdigest())
    return p


def put_daily(store, sym, qv_by_day, header=True):
    """qv_by_day: {'YYYY-MM-DD': quote volume}; written as one archive per month."""
    months = {}
    for day, qv in sorted(qv_by_day.items()):
        months.setdefault(day[:7], []).append(kline(ms(day), DAY, qv))
    for ym, rows in months.items():
        put_zip(store, f'um/monthly/klines/{sym}/1d/{sym}-1d-{ym}.zip', '\n'.join(([HDR] if header else []) + rows) + '\n')


def days(start, n, qv=1000.0):
    t0 = ms(start)
    return {datetime.fromtimestamp((t0 + i * DAY) / 1000, tz=timezone.utc).strftime('%Y-%m-%d'): qv for i in range(n)}


def build_manifest(store):
    return M.build(['um/monthly'], manifest_id='fx-archive', source_class='archive-verified', base=str(store),
                   data_root='binance_um', survivor_only=False, loader=M.ARCHIVE_LOADER)


CUTOFF = ms('2025-12-01')


def classes(entries=()):
    return U.make_classes(list(entries), classes_id='fx-classes', tradfi_cutoff_ms=CUTOFF, reviewed_cairo='2026-10-09',
                          method='fixture', pre_cutoff_rule='fixture: pre-cutoff symbols are crypto')


def entry(sym, cls, t, sub='x'):
    return {'symbol': sym, 'class': cls, 'subclass': sub, 'effective_from_ms': t, 'basis': 'fixture listing notice'}


def universe(store, classes_doc=None, **kw):
    m = build_manifest(store)
    return U.build(U.load_daily(m, str(store)), manifest_digest=m['digest'], classes=classes_doc or classes(), **kw)


def week(u, date, book='crypto'):
    w = next(w for w in u['weeks'] if w['monday_utc'] == date)
    return dict(w, members=w['books'][book]['members'], qv30d_usdt=w['books'][book]['qv30d_usdt'],
                eligible=w['books'][book]['eligible'])


def write_classes(path, entries=()):
    path.write_text(json.dumps(classes(entries)))
    return str(path)


# ---------------------------------------------------------------- R1 archive manifest

def test_archive_manifest_entries(tmp_path):
    put_daily(tmp_path, 'BTCUSDT', days('2024-01-30', 3))
    put_zip(tmp_path, 'um/monthly/klines/BTCUSDT/4h/BTCUSDT-4h-2024-01.zip',
            '\n'.join(kline(ms('2024-01-01') + i * 4 * 3_600_000, 4 * 3_600_000) for i in range(3)) + '\n')
    put_zip(tmp_path, 'um/monthly/fundingRate/BTCUSDT/BTCUSDT-fundingRate-2024-01.zip',
            M.FUNDING_HEADER.decode() + f'\n{ms("2024-01-01")},8,0.0001\n{ms("2024-01-01") + 8 * 3_600_000 + 5},8,-0.0002\n')
    (tmp_path / 'symbols_all_usdt_perp.json').write_text('[]')                 # non-zip files are ignored
    m = build_manifest(tmp_path)
    assert m['loader_version'] == M.ARCHIVE_LOADER and m['available_rule'] == M.ARCHIVE_RULE
    assert m['survivor_only'] is False and m['data_root'] == 'binance_um' and str(tmp_path) not in json.dumps(m)
    by = {f['path']: f for f in m['files']}
    assert len(by) == 4
    f = by['um/monthly/klines/BTCUSDT/1d/BTCUSDT-1d-2024-01.zip']
    assert (f['series'], f['symbol'], f['interval'], f['rows']) == ('klines', 'BTCUSDT', '1d', 2)
    assert f['first_open_ms'] == ms('2024-01-30') and f['last_open_ms'] == ms('2024-01-31')
    assert f['source'] == {'kind': M.ARCHIVE_KIND, 'url': 'https://data.binance.vision/data/futures/' + f['path'],
                           'archive_checksum': f['sha256']}
    fr = by['um/monthly/fundingRate/BTCUSDT/BTCUSDT-fundingRate-2024-01.zip']
    assert fr['series'] == 'fundingRate' and fr['interval'] is None and fr['rows'] == 2
    assert M.verify(m, str(tmp_path)) == []
    out = tmp_path / 'm.json'
    M.write(m, str(out))
    assert M.load(str(out))['digest'] == m['digest']


def test_archive_manifest_detects_changed_bytes(tmp_path):
    put_daily(tmp_path, 'BTCUSDT', days('2024-01-01', 3))
    m = build_manifest(tmp_path)
    put_daily(tmp_path, 'BTCUSDT', days('2024-01-01', 3, qv=7.0))             # new bytes and a matching new .ok
    assert any('sha256' in x for x in M.verify(m, str(tmp_path)))


@pytest.mark.parametrize('case', ['no_ok', 'bad_ok', 'extra_member'])
def test_archive_checksum_and_member_refusals(tmp_path, case):
    rel = 'um/monthly/klines/BTCUSDT/1d/BTCUSDT-1d-2024-01.zip'
    body = kline(ms('2024-01-01'), DAY) + '\n'
    if case == 'no_ok':
        put_zip(tmp_path, rel, body, ok=False)
    elif case == 'bad_ok':
        put_zip(tmp_path, rel, body, ok_value='0' * 64)
    else:
        put_zip(tmp_path, rel, body, extra='other.csv')
    with pytest.raises(M.ManifestError, match={'no_ok': 'sidecar', 'bad_ok': 'checksum', 'extra_member': 'exactly'}[case]):
        build_manifest(tmp_path)


@pytest.mark.parametrize('rel', ['um/monthly/klines/BTCUSDT/1d/ETHUSDT-1d-2024-01.zip',
                                 'um/monthly/klines/BTCUSDT/4h/BTCUSDT-1d-2024-01.zip',
                                 'um/monthly/klines/BTCUSDT/BTCUSDT-1d-2024-01.zip',
                                 'um/monthly/trades/BTCUSDT/1d/BTCUSDT-1d-2024-01.zip',
                                 'um/monthly/klines/BTCUSDT/1d/BTCUSDT-1d-2024-13.zip',
                                 'um/daily/klines/BTCUSDT/1d/BTCUSDT-1d-2024-01.zip'])
def test_archive_layout_must_be_exact(tmp_path, rel):
    put_zip(tmp_path, rel, kline(ms('2024-01-01'), DAY) + '\n')
    with pytest.raises(M.ManifestError, match='um/monthly'):
        M.build(['um'], manifest_id='x', source_class='archive-verified', base=str(tmp_path), data_root='binance_um',
                survivor_only=False, loader=M.ARCHIVE_LOADER)


@pytest.mark.parametrize('rows,msg', [
    ([kline(ms('2024-01-02'), DAY), kline(ms('2024-01-01'), DAY)], 'increasing'),
    ([kline(ms('2024-01-01'), DAY), kline(ms('2024-01-01'), DAY)], 'increasing'),
    ([kline(ms('2024-02-01'), DAY)], 'outside the file month'),
    ([kline(ms('2024-01-01') // 1000, DAY)], 'outside the file month'),             # seconds, not ms
    ([kline(ms('2024-01-01') + 3_600_000, DAY)], 'aligned'),
    ([kline(ms('2024-01-01'), DAY).replace(str(ms('2024-01-01') + DAY - 1), str(ms('2024-01-01') + DAY))], 'close_time'),
    ([kline(ms('2024-01-01'), DAY, qv='nan')], 'quote_volume'),
    ([kline(ms('2024-01-01'), DAY, qv='x')], 'quote_volume'),
    ([kline(ms('2024-01-01'), DAY, qv='1e5')], 'quote_volume'),                 # exponent form: not canonical
    ([kline(ms('2024-01-01'), DAY, qv='-1')], 'quote_volume'),
    ([kline(ms('2024-01-01'), DAY, qv='007')], 'quote_volume'),                 # leading zeros
    ([kline(ms('2024-01-01'), DAY, qv='1.')], 'quote_volume'),
    ([kline(ms('2024-01-01'), DAY, qv='1' * 21)], 'quote_volume'),             # beyond the bounded width
    ([kline(ms('2024-01-01'), DAY).replace('2,0.5', 'x,0.5').replace(str(ms('2024-01-01')) + ',', 'x,', 1)], 'non-numeric'),
    ([kline(ms('2024-01-01'), DAY) + ',9'], 'columns'),
    (['open_time,open', kline(ms('2024-01-01'), DAY)], 'columns'),
])
def test_archive_csv_schema_refusals(tmp_path, rows, msg):
    put_zip(tmp_path, 'um/monthly/klines/BTCUSDT/1d/BTCUSDT-1d-2024-01.zip', '\n'.join(rows) + '\n')
    with pytest.raises(M.ManifestError, match=msg):
        build_manifest(tmp_path)


def test_archive_manifest_pairing_and_data_roots(tmp_path):
    put_daily(tmp_path, 'BTCUSDT', days('2024-01-01', 2))
    m = build_manifest(tmp_path)
    for k, v in (('data_root', 'repo'), ('source_class', 'legacy-unverified'), ('survivor_only', True),
                 ('data_root', 'other_store')):
        x = dict(m, **{k: v})
        x['digest'] = M.digest_of(x)
        with pytest.raises(M.ManifestError, match='data_root|requires'):
            M.validate(x)
    for mutate in (lambda f: f.__setitem__('series', 'markPriceKlines'),
                   lambda f: f['source'].__setitem__('url', 'https://example.com/x.zip'),
                   lambda f: f['source'].__setitem__('archive_checksum', 'f' * 64),
                   lambda f: f.pop('series')):
        x = json.loads(json.dumps(m))
        mutate(x['files'][0])
        x['digest'] = M.digest_of(x)
        with pytest.raises(M.ManifestError):
            M.validate(x)


def test_archive_cli_needs_explicit_store(tmp_path, capsys):
    put_daily(tmp_path, 'BTCUSDT', days('2024-01-01', 2))
    out = tmp_path / 'm.json'
    M.write(build_manifest(tmp_path), str(out))
    assert M.main(['verify', str(out)]) == 2 and '--store' in capsys.readouterr().err
    assert M.main(['verify', str(out), '--store', str(tmp_path)]) == 0


# ---------------------------------------------------------------- R2 PIT universe

def test_ranking_uses_only_bars_closed_before_monday(tmp_path):
    # 2024-03-04 is a Monday. AAA leads the trailing window, BBB's spike opens ON the Monday (closes after it).
    a = days('2024-01-01', 80, 100.0)
    b = days('2024-01-01', 80, 90.0)
    b['2024-03-04'] = 1e12
    put_daily(tmp_path, 'AAAUSDT', a)
    put_daily(tmp_path, 'BBBUSDT', b)
    u = universe(tmp_path, top_n=1)
    w = week(u, '2024-03-04')
    assert w['members'] == ['AAAUSDT'] and w['qv30d_usdt'] == ['3000'] and w['eligible'] == 2
    assert week(u, '2024-03-11')['members'] == ['BBBUSDT']                     # visible one week later


def test_trailing_window_is_exactly_30_closed_days(tmp_path):
    q = days('2024-01-01', 80, 1.0)
    q['2024-02-03'] = 1000.0                                                    # 30 days before Monday 2024-03-04: in
    q['2024-02-02'] = 5000.0                                                    # 31 days before: out
    put_daily(tmp_path, 'AAAUSDT', q)
    assert week(universe(tmp_path), '2024-03-04')['qv30d_usdt'] == [str(1000 + 29)]


def test_listing_age_and_week_range(tmp_path):
    put_daily(tmp_path, 'OLDUSDT', days('2024-01-01', 90, 10.0))
    put_daily(tmp_path, 'NEWUSDT', days('2024-02-10', 50, 1e6))               # exactly 30 days old on 2024-03-11
    put_daily(tmp_path, 'LATEUSDT', days('2024-02-11', 50, 1e5))              # 29 days old on 2024-03-11
    u = universe(tmp_path)
    assert u['weeks'][0]['monday_utc'] == '2024-02-05'                          # first Monday with 30 days of history
    assert u['weeks'][-1]['monday_ms'] <= u['store_end_ms']
    assert ['NEWUSDT', U.VETO_AGE] in week(u, '2024-03-04')['vetoes']
    w = week(u, '2024-03-11')
    assert w['members'] == ['NEWUSDT', 'OLDUSDT'] and w['vetoes'] == [['LATEUSDT', U.VETO_AGE]]
    assert week(u, '2024-03-18')['members'] == ['NEWUSDT', 'LATEUSDT', 'OLDUSDT']
    sym = {s['symbol']: s for s in u['symbols']}
    assert sym['OLDUSDT']['listing_censored'] is True and sym['NEWUSDT']['listing_censored'] is False


def test_delisted_symbol_has_no_lookahead_and_drops_when_bars_stop(tmp_path):
    put_daily(tmp_path, 'LIVEUSDT', days('2024-01-01', 120, 10.0))
    put_daily(tmp_path, 'GONEUSDT', days('2024-01-01', 70, 1e6))               # last bar opens 2024-03-10
    u = universe(tmp_path)
    assert week(u, '2024-03-11')['members'][0] == 'GONEUSDT'                    # still trading at that instant
    w = week(u, '2024-03-18')
    assert w['members'] == ['LIVEUSDT'] and ['GONEUSDT', U.VETO_NOT_TRADING] in w['vetoes']
    gone = next(s for s in u['symbols'] if s['symbol'] == 'GONEUSDT')
    assert gone['archive_ended_before_store_end'] is True and gone['archive_last_close_ms'] == ms('2024-03-11')


def test_delist_veto_starts_at_the_observation_only(tmp_path):
    put_daily(tmp_path, 'AAAUSDT', days('2024-01-01', 90, 10.0))
    put_daily(tmp_path, 'BBBUSDT', days('2024-01-01', 90, 20.0))
    obs = [{'symbol': 'BBBUSDT', 'observed_ms': ms('2024-03-05'), 'source': 'fixture announcement'}]
    u = universe(tmp_path, delist_observations=obs)
    assert week(u, '2024-03-04')['members'] == ['BBBUSDT', 'AAAUSDT']
    w = week(u, '2024-03-11')
    assert w['members'] == ['AAAUSDT'] and w['vetoes'] == [['BBBUSDT', U.VETO_DELIST]]
    with pytest.raises(U.UniverseError, match='observation'):
        universe(tmp_path, delist_observations=[{'symbol': 'BBBUSDT', 'observed_ms': ms('2024-03-05')}])


def test_rules_backfilled_label(tmp_path):
    put_daily(tmp_path, 'AAAUSDT', days('2024-01-01', 90, 10.0))
    u = universe(tmp_path)
    assert {w['rules'] for w in u['weeks']} == {U.RULES_BACKFILLED} and u['rules_first_observed_ms'] is None
    u = universe(tmp_path, rules_first_observed_ms=ms('2024-03-06'))
    assert week(u, '2024-03-04')['rules'] == U.RULES_BACKFILLED and week(u, '2024-03-11')['rules'] == U.RULES_OBSERVED


def test_top_n_and_tie_break(tmp_path):
    for s in ('CCCUSDT', 'AAAUSDT', 'BBBUSDT'):
        put_daily(tmp_path, s, days('2024-01-01', 70, 5.0))
    w = week(universe(tmp_path, top_n=2), '2024-03-04')
    assert w['members'] == ['AAAUSDT', 'BBBUSDT'] and w['eligible'] == 3


def test_renames_fail_closed_before_and_after_the_effective_time(tmp_path):
    """Codex P1 (#51): a rename merged volume histories for every week, before its effective time too."""
    put_daily(tmp_path, 'OLDNAMEUSDT', days('2024-01-01', 60, 10.0))           # through 2024-02-29
    put_daily(tmp_path, 'NEWNAMEUSDT', days('2024-03-01', 30, 10.0))           # renamed 2024-03-01
    for eff in (ms('2024-03-01'), ms('2024-01-15')):
        ren = [{'old': 'OLDNAMEUSDT', 'new': 'NEWNAMEUSDT', 'effective_ms': eff, 'source': 'fixture notice'}]
        with pytest.raises(U.UniverseError, match='renames are refused'):
            universe(tmp_path, renames=ren)
    u = universe(tmp_path)                                                      # no rename: two separate contracts
    assert u['renames'] == [] and ['NEWNAMEUSDT', U.VETO_AGE] in week(u, '2024-03-04')['vetoes']
    assert week(u, '2024-02-05')['members'] == ['OLDNAMEUSDT']                  # history before the rename untouched
    assert {s['symbol'] for s in u['symbols']} == {'OLDNAMEUSDT', 'NEWNAMEUSDT'}


def test_universe_digest_deterministic_write_once_and_cli_verify(tmp_path):
    for s, q in (('AAAUSDT', 10.0), ('BBBUSDT', 20.0)):
        put_daily(tmp_path / 'store', s, days('2024-01-01', 70, q))
    a, b = universe(tmp_path / 'store'), universe(tmp_path / 'store')
    assert a['digest'] == b['digest'] == U.digest_of(a)
    mpath, upath = tmp_path / 'm.json', tmp_path / 'u.json'
    M.write(build_manifest(tmp_path / 'store'), str(mpath))
    cpath = write_classes(tmp_path / 'c.json')
    assert U.main(['build', '--manifest', str(mpath), '--classes', cpath, '--store', str(tmp_path / 'store'),
                   '--out', str(upath)]) == 0
    assert json.loads(upath.read_text())['digest'] == a['digest']
    assert U.main(['verify', str(upath), '--manifest', str(mpath), '--classes', cpath,
                   '--store', str(tmp_path / 'store')]) == 0
    c = dict(a, universe_id='other')
    c['digest'] = U.digest_of(c)
    with pytest.raises(U.UniverseError, match='immutable'):
        U.write(c, str(upath))


def test_universe_refuses_bytes_that_left_the_manifest(tmp_path):
    put_daily(tmp_path, 'AAAUSDT', days('2024-01-01', 40, 10.0))
    m = build_manifest(tmp_path)
    put_daily(tmp_path, 'AAAUSDT', days('2024-01-01', 40, 99.0))              # re-written with a matching .ok
    with pytest.raises(U.UniverseError, match='manifest'):
        U.load_daily(m, str(tmp_path))


def test_universe_refuses_survivor_only_manifest(tmp_path):
    d = tmp_path / 'fx'
    d.mkdir()
    (d / 'BTCUSDT_1d.csv').write_bytes(b't,o,h,l,c,v\n2025-01-01 00:00:00,1,2,0.5,1.5,10\n')
    m = M.build(['fx'], manifest_id='x', source_class='fixture', base=str(tmp_path))
    with pytest.raises(U.UniverseError, match='survivor'):
        U.load_daily(m, str(tmp_path))


def test_archive_accepts_non_ascii_symbols(tmp_path):
    sym = chr(0x54c8) + chr(0x57fa) + chr(0x7c73) + 'USDT'                    # a real CJK-named Binance perp
    put_daily(tmp_path, sym, days('2025-10-01', 2))
    m = build_manifest(tmp_path)
    f = m['files'][0]
    assert f['symbol'] == sym and f['source']['url'].endswith('/%E5%93%88%E5%9F%BA%E7%B1%B3USDT-1d-2025-10.zip')
    out = tmp_path / 'm.json'
    M.write(m, str(out))
    assert out.read_bytes().isascii() and M.load(str(out)) == m


CJK = chr(0x54c8) + chr(0x57fa) + chr(0x7c73) + 'USDT'


def test_non_ascii_symbol_is_vetoed_before_ranking_and_audited(tmp_path):
    """Codex 6088058441 / owner 6078694212: explicit pre-ranking `not-addressable:non-ascii-symbol` veto, with the
    would-be rank and top-40 flag recorded in every affected week."""
    put_daily(tmp_path, CJK, days('2024-01-01', 120, 1e9))                    # by far the largest volume
    put_daily(tmp_path, 'AAAUSDT', days('2024-01-01', 120, 10.0))
    put_daily(tmp_path, 'BBBUSDT', days('2024-01-01', 120, 20.0))
    young = '\u00c9TEUSDT'                                                     # non-ASCII and too young
    put_daily(tmp_path, young, days('2024-03-01', 60, 1e6))
    u = universe(tmp_path)
    w = week(u, '2024-03-04')
    assert w['members'] == ['BBBUSDT', 'AAAUSDT'] and CJK not in w['members']
    assert [CJK, U.VETO_NOT_ADDRESSABLE] in w['vetoes'] and [young, U.VETO_NOT_ADDRESSABLE] in w['vetoes']
    na = {e['symbol']: e for e in w['not_addressable']}
    assert na[CJK] == {'symbol': CJK, 'book': 'crypto', 'otherwise': 'eligible', 'qv30d_usdt': str(30 * 10 ** 9),
                       'would_rank': 1, 'would_be_top_n': True}
    assert na[young]['otherwise'] == U.VETO_AGE and na[young]['would_rank'] is None
    assert na[young]['would_be_top_n'] is False
    assert w['eligible'] == 2                                                  # never counted as eligible
    for wk in u['weeks']:                                                      # every affected week records it
        if any(v[0] == CJK for v in wk['vetoes']):
            assert {e['symbol'] for e in wk['not_addressable']} >= {CJK}
        assert all(r == U.VETO_NOT_ADDRESSABLE for s, r in wk['vetoes'] if not s.isascii())
    sym = {x['symbol']: x for x in u['symbols']}
    assert sym[CJK]['addressable'] is False and sym['AAAUSDT']['addressable'] is True
    assert U.addressable('BTCUSDT') and not U.addressable(CJK)
    # a would-be rank below the top N is recorded as such
    u2 = universe(tmp_path, top_n=1)
    na2 = {e['symbol']: e for e in week(u2, '2024-03-04')['not_addressable']}
    assert na2[CJK]['would_rank'] == 1 and na2[CJK]['would_be_top_n'] is True


def test_non_ascii_veto_replaces_the_gap_veto_and_keeps_the_underlying_reason(tmp_path):
    a = days('2024-01-01', 90, 5.0)
    del a['2024-02-20']
    put_daily(tmp_path, CJK, a)
    put_daily(tmp_path, 'AAAUSDT', days('2024-01-01', 90, 10.0))
    w = week(universe(tmp_path), '2024-03-04')
    assert [CJK, U.VETO_NOT_ADDRESSABLE] in w['vetoes'] and [CJK, U.VETO_GAP] not in w['vetoes']
    assert w['gaps'] == [] and w['not_addressable'][0]['otherwise'] == U.VETO_GAP


def test_gz_manifest_round_trip_and_immutability(tmp_path):
    put_daily(tmp_path, 'BTCUSDT', days('2024-01-01', 2))
    m = build_manifest(tmp_path)
    gz, plain = tmp_path / 'm.json.gz', tmp_path / 'm.json'
    M.write(m, str(gz))
    M.write(m, str(plain))
    assert gzip.decompress(gz.read_bytes()) == plain.read_bytes()
    assert M.load(str(gz)) == m
    M.write(m, str(gz))                                                         # identical content: no-op
    x = dict(m, note='changed')
    x['digest'] = M.digest_of(x)
    with pytest.raises(M.ManifestError, match='immutable'):
        M.write(x, str(gz))
    gz.write_bytes(b'junk')
    with pytest.raises(M.ManifestError, match='gzip'):
        M.load(str(gz))


def test_settled_contract_with_zero_volume_bars_is_unrankable_not_vetoed_early(tmp_path):
    q = days('2024-01-01', 90, 1e6)
    q.update({d: 0.0 for d in days('2024-03-11', 60)})                        # settled 2024-03-11, flat bars continue
    put_daily(tmp_path, 'DEADUSDT', q)
    put_daily(tmp_path, 'LIVEUSDT', days('2024-01-01', 120, 10.0))
    u = universe(tmp_path)
    assert week(u, '2024-03-11')['members'][0] == 'DEADUSDT'                    # volume before the instant still counts
    w = week(u, '2024-04-15')
    assert w['members'] == ['LIVEUSDT'] and ['DEADUSDT', U.VETO_NO_VOLUME] in w['vetoes']
    dead = next(s for s in u['symbols'] if s['symbol'] == 'DEADUSDT')
    assert dead['last_traded_close_ms'] == ms('2024-03-11') and dead['archive_ended_before_store_end'] is False


# ---------------------------------------------------------------- Codex #51 regressions: gaps, exact decimals, books

def test_missing_middle_day_is_a_data_gap_not_zero_volume(tmp_path):
    a = days('2024-01-01', 90, 100.0)
    del a['2024-02-20']                                                         # one archive day missing mid-window
    put_daily(tmp_path, 'AAAUSDT', a)
    put_daily(tmp_path, 'BBBUSDT', days('2024-01-01', 90, 99.0))
    u = universe(tmp_path)
    w = week(u, '2024-03-04')
    assert w['members'] == ['BBBUSDT'] and ['AAAUSDT', U.VETO_GAP] in w['vetoes'] and w['gaps'] == [['AAAUSDT', 1]]
    assert week(u, '2024-03-25')['members'] == ['AAAUSDT', 'BBBUSDT']           # gap left the 30-day window
    sym = {s['symbol']: s for s in u['symbols']}
    assert sym['AAAUSDT']['missing_days'] == [['2024-02-20', '2024-02-20']] and sym['AAAUSDT']['missing_day_count'] == 1
    assert sym['BBBUSDT']['missing_days'] == []


def test_missing_month_archive_is_never_zero_volume(tmp_path):
    a = days('2024-01-01', 120, 1e6)
    for d in [d for d in a if d.startswith('2024-02')]:
        del a[d]                                                                # the whole 2024-02 zip is absent
    put_daily(tmp_path, 'AAAUSDT', a)
    put_daily(tmp_path, 'BBBUSDT', days('2024-01-01', 120, 1.0))
    u = universe(tmp_path)
    assert ['AAAUSDT', U.VETO_NOT_TRADING] in week(u, '2024-02-12')['vetoes']   # no bar closes at the instant
    for date, n in (('2024-03-04', 27), ('2024-03-25', 6)):
        w = week(u, date)
        assert w['members'] == ['BBBUSDT'] and ['AAAUSDT', U.VETO_GAP] in w['vetoes'] and w['gaps'] == [['AAAUSDT', n]]
    assert week(u, '2024-04-01')['members'] == ['AAAUSDT', 'BBBUSDT']           # 30 complete days again
    assert next(s for s in u['symbols'] if s['symbol'] == 'AAAUSDT')['missing_days'] == [['2024-02-01', '2024-02-29']]


def test_near_tie_ranks_on_exact_decimals_not_binary_floats(tmp_path):
    # float 0.1 + 0.2 = 0.30000000000000004 > 0.3 would put BBB ahead; exactly they tie -> AAA first by symbol.
    a, b = days('2024-01-01', 90, '0'), days('2024-01-01', 90, '0')
    a['2024-02-20'] = '0.3'
    b['2024-02-20'], b['2024-02-21'] = '0.1', '0.2'
    # 2**53 + 1 collapses onto 2**53 as a float; exactly, DDD (one unit more) ranks first.
    c, d = days('2024-01-01', 90, '0'), days('2024-01-01', 90, '0')
    c['2024-02-20'], d['2024-02-20'] = str(2 ** 53), str(2 ** 53 + 1)
    for sym, q in (('AAAUSDT', a), ('BBBUSDT', b), ('CCCUSDT', c), ('DDDUSDT', d)):
        put_daily(tmp_path, sym, q)
    assert 0.1 + 0.2 > 0.3 and float(2 ** 53) == float(2 ** 53 + 1)             # the float hazard is real
    w = week(universe(tmp_path), '2024-03-04')
    assert w['members'] == ['DDDUSDT', 'CCCUSDT', 'AAAUSDT', 'BBBUSDT']
    assert w['qv30d_usdt'] == [str(2 ** 53 + 1), str(2 ** 53), '0.3', '0.3']
    with pytest.raises(U.UniverseError, match='Decimal'):
        U.build({'AAAUSDT': {ms('2024-01-01'): 1.5}}, manifest_digest='a' * 64, classes=classes())


def test_ranking_is_exact_beyond_28_significant_digits(tmp_path):
    # Codex 6078823692 P1: a negated Decimal key rounds to the default 28-digit context and merges these two.
    lo, hi = '12345678901234567890.123456789012345677', '12345678901234567890.123456789012345678'
    a, b = days('2024-01-01', 90, '0'), days('2024-01-01', 90, '0')
    a['2024-02-20'], b['2024-02-20'] = lo, hi
    put_daily(tmp_path, 'AAAUSDT', a)
    put_daily(tmp_path, 'BBBUSDT', b)
    from decimal import Decimal
    assert Decimal(hi) > Decimal(lo) and -Decimal(hi) == -Decimal(lo)          # the hazard is real
    w = week(universe(tmp_path), '2024-03-04')
    assert w['members'] == ['BBBUSDT', 'AAAUSDT'] and w['qv30d_usdt'] == [hi, lo]
    assert U.rank([('BBB', Decimal('1')), ('AAA', Decimal('1')), ('CCC', Decimal(hi))]) ==         [('CCC', Decimal(hi)), ('AAA', Decimal('1')), ('BBB', Decimal('1'))]


def test_full_window_outage_is_an_explicit_veto_not_a_silent_drop(tmp_path):
    # Codex 6078823692 P2: bars through 1 Feb, none 2 Feb - 31 Mar, then resumed.
    a = {d: q for d, q in days('2024-01-01', 150, 100.0).items() if not ('2024-02-02' <= d <= '2024-03-31')}
    put_daily(tmp_path, 'AAAUSDT', a)
    put_daily(tmp_path, 'BBBUSDT', days('2024-01-01', 150, 1.0))
    u = universe(tmp_path)
    w = week(u, '2024-03-04')                                                   # window 2 Feb - 3 Mar: no bar at all
    assert w['members'] == ['BBBUSDT'] and ['AAAUSDT', U.VETO_WINDOW_ABSENT] in w['vetoes']
    assert w['gaps'] == [['AAAUSDT', 30]]
    assert week(u, '2024-04-01')['gaps'] == [['AAAUSDT', 30]]                   # window 2 Mar - 31 Mar, still absent
    assert ['AAAUSDT', U.VETO_NOT_TRADING] in week(u, '2024-02-05')['vetoes']   # partial window: existing veto
    assert next(s for s in u['symbols'] if s['symbol'] == 'AAAUSDT')['missing_days'] == [['2024-02-02', '2024-03-31']]
    assert week(u, '2024-05-06')['members'] == ['AAAUSDT', 'BBBUSDT']           # 30 complete days again


def test_window_absent_veto_needs_a_listing_and_yields_to_a_delist_observation(tmp_path):
    put_daily(tmp_path, 'LIVEUSDT', days('2024-01-01', 150, 10.0))
    put_daily(tmp_path, 'GONEUSDT', days('2024-01-01', 40, 1e6))                # stops 2024-02-09
    put_daily(tmp_path, 'NEWUSDT', days('2024-04-20', 30, 1e6))                 # not listed before 2024-04-20
    u = universe(tmp_path)
    w = week(u, '2024-04-15')
    assert ['GONEUSDT', U.VETO_WINDOW_ABSENT] in w['vetoes'] and ['GONEUSDT', 30] in w['gaps']
    assert not any(v[0] == 'NEWUSDT' for v in w['vetoes'])                     # future listing is not looked ahead to
    obs = [{'symbol': 'GONEUSDT', 'observed_ms': ms('2024-02-12'), 'source': 'fixture notice'}]
    w = week(universe(tmp_path, delist_observations=obs), '2024-04-15')
    assert ['GONEUSDT', U.VETO_DELIST] in w['vetoes'] and not any(g[0] == 'GONEUSDT' for g in w['gaps'])


def test_books_are_ranked_separately_and_gold_stays_in(tmp_path):
    t = ms('2025-12-08')
    put_daily(tmp_path, 'BTCUSDT', days('2025-11-01', 90, 1e9))
    put_daily(tmp_path, 'PAXGUSDT', days('2025-11-01', 90, 1e3))
    put_daily(tmp_path, 'XAUUSDT', days('2025-12-08', 60, 1e10))               # bigger than BTC, but its own book
    put_daily(tmp_path, 'TSLAUSDT', days('2025-12-08', 60, 1e8))
    put_daily(tmp_path, 'ODDUSDT', days('2025-12-08', 60, 1e11))
    c = classes([entry('XAUUSDT', 'gold-commodity', t, 'gold-spot'), entry('PAXGUSDT', 'gold-commodity', ms('2025-11-01')),
                 entry('TSLAUSDT', 'equity', t), entry('ODDUSDT', 'unclassified', t)])
    u = universe(tmp_path, c)
    w = week(u, '2026-01-12')
    assert w['books']['crypto']['members'] == ['BTCUSDT']
    assert w['books']['gold-commodity']['members'] == ['XAUUSDT', 'PAXGUSDT']
    assert w['books']['equity']['members'] == ['TSLAUSDT'] and w['books']['fx']['members'] == []
    assert ['ODDUSDT', U.VETO_UNCLASSIFIED] in w['vetoes']
    assert U.members(u, w, 'gold-commodity') == ['XAUUSDT', 'PAXGUSDT']
    with pytest.raises(U.UniverseError, match='book'):
        U.members(u, w, 'mixed')
    assert u['classes_digest'] == c['digest'] and all('members' not in x for x in u['weeks'])
    sym = {s['symbol']: s for s in u['symbols']}
    assert (sym['BTCUSDT']['class'], sym['BTCUSDT']['subclass']) == ('crypto', 'pre-cutoff-rule')
    assert sym['XAUUSDT']['class'] == 'gold-commodity'


def test_post_cutoff_symbol_without_a_class_entry_fails_closed(tmp_path):
    put_daily(tmp_path, 'BTCUSDT', days('2025-11-01', 60, 1e9))
    put_daily(tmp_path, 'XAUUSDT', days('2025-12-08', 30, 1e10))
    with pytest.raises(U.UniverseError, match='no silent crypto default'):
        universe(tmp_path)


def test_class_applies_only_from_its_effective_time(tmp_path):
    put_daily(tmp_path, 'BTCUSDT', days('2024-01-01', 90, 1.0))
    put_daily(tmp_path, 'GLDUSDT', days('2024-01-01', 90, 9.0))
    u = universe(tmp_path, classes([entry('GLDUSDT', 'gold-commodity', ms('2024-03-05'))]))
    assert ['GLDUSDT', U.VETO_UNCLASSIFIED] in week(u, '2024-03-04')['vetoes']
    assert week(u, '2024-03-11', 'gold-commodity')['members'] == ['GLDUSDT']


@pytest.mark.parametrize('mutate,redigest', [
    (lambda c: c['entries'][0].__setitem__('class', 'mixed'), True),
    (lambda c: c['entries'][0].__setitem__('basis', ''), True),
    (lambda c: c['entries'].append(dict(c['entries'][0])), True),
    (lambda c: c.__setitem__('tradfi_cutoff_ms', 1), False),                   # content changed, digest stale
])
def test_classification_file_is_validated(mutate, redigest):
    c = classes([entry('XAUUSDT', 'gold-commodity', ms('2025-12-08'))])
    mutate(c)
    if redigest:
        c['digest'] = U.classes_digest(c)
    with pytest.raises(U.UniverseError, match='instrument classes'):
        U.validate_classes(c)


def test_committed_archive_manifest_classes_and_universe_are_consistent():
    """Schema/digest only (the archive bytes live outside the repo; `manifest.py verify --store` re-hashes them)."""
    res = os.path.join(ROOT, 'research_evidence')
    m = M.load(os.path.join(res, 'manifests', 'binance-um-archive-v1.json.gz'))
    assert m['digest'] == '54912d9d2bf6e6fc45bc75c0553f0bd867e877972beeb3452785937379baf1e7'
    assert len(m['files']) == 103659 and len({f['symbol'] for f in m['files']}) == 900
    c = U.load_classes(os.path.join(res, 'universe', 'instrument-classes-v1.json'))
    with open(os.path.join(res, 'universe', 'pit-top40-qv30d-v4.json'), encoding='utf-8') as f:
        u = json.load(f)
    assert u['digest'] == U.digest_of(u) and u['manifest_digest'] == m['digest'] and u['format'] == U.FORMAT
    assert u['classes_digest'] == c['digest'] and u['books'] == list(U.BOOKS) and u['renames'] == []
    for old in ('v1', 'v2', 'v3'):          # v1 mixed list; v2 rounding sort + silent drop; v3 no addressability veto
        assert not os.path.exists(os.path.join(res, 'universe', f'pit-top40-qv30d-{old}.json'))
    for w in u['weeks']:
        assert w['rules'] == U.RULES_BACKFILLED and 'members' not in w
        assert all(len(w['books'][b]['members']) <= U.TOP_N for b in U.BOOKS)
        assert {g[0] for g in w['gaps']} == {s for s, r in w['vetoes'] if r in (U.VETO_GAP, U.VETO_WINDOW_ABSENT)}
        assert all(n == U.LOOKBACK_DAYS for s, n in w['gaps'] if [s, U.VETO_WINDOW_ABSENT] in w['vetoes'])
        assert all(s.isascii() for b in U.BOOKS for s in w['books'][b]['members'])
        assert sorted(e['symbol'] for e in w['not_addressable']) == sorted(
            s for s, r in w['vetoes'] if r == U.VETO_NOT_ADDRESSABLE) == sorted(
            s for s, _ in w['vetoes'] if not s.isascii())
    assert [w['monday_ms'] for w in u['weeks']] == list(range(u['weeks'][0]['monday_ms'],
                                                               u['weeks'][-1]['monday_ms'] + U.WEEK, U.WEEK))
    cls = {s['symbol']: s['class'] for s in u['symbols']}
    assert cls['XAUUSDT'] == 'gold-commodity' and cls['BTCUSDT'] == 'crypto' and cls['TSLAUSDT'] == 'equity'
    assert any('XAUUSDT' in w['books']['gold-commodity']['members'] for w in u['weeks'])   # gold stays in
    assert not any(cls[s] != 'crypto' for w in u['weeks'] for s in w['books']['crypto']['members'])
