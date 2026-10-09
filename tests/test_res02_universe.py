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


def universe(store, **kw):
    m = build_manifest(store)
    return U.build(U.load_daily(m, str(store)), manifest_digest=m['digest'], **kw)


def week(u, date):
    return next(w for w in u['weeks'] if w['monday_utc'] == date)


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
    ([kline(ms('2024-01-01'), DAY, qv='x')], 'non-numeric'),
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
    assert w['members'] == ['AAAUSDT'] and w['qv30d_usdt'] == [3000] and w['eligible'] == 2
    assert week(u, '2024-03-11')['members'] == ['BBBUSDT']                     # visible one week later


def test_trailing_window_is_exactly_30_closed_days(tmp_path):
    q = days('2024-01-01', 80, 1.0)
    q['2024-02-03'] = 1000.0                                                    # 30 days before Monday 2024-03-04: in
    q['2024-02-02'] = 5000.0                                                    # 31 days before: out
    put_daily(tmp_path, 'AAAUSDT', q)
    assert week(universe(tmp_path), '2024-03-04')['qv30d_usdt'] == [1000 + 29]


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


def test_rename_keeps_contract_identity(tmp_path):
    put_daily(tmp_path, 'OLDNAMEUSDT', days('2024-01-01', 60, 10.0))           # through 2024-02-29
    put_daily(tmp_path, 'NEWNAMEUSDT', days('2024-03-01', 30, 10.0))           # renamed 2024-03-01
    ren = [{'old': 'OLDNAMEUSDT', 'new': 'NEWNAMEUSDT', 'effective_ms': ms('2024-03-01'), 'source': 'fixture notice'}]
    w = week(universe(tmp_path, renames=ren), '2024-03-04')
    assert w['members'] == ['NEWNAMEUSDT'] and w['qv30d_usdt'] == [300] and w['vetoes'] == []
    w = week(universe(tmp_path), '2024-03-04')                                  # without the rename: a 3-day-old listing
    assert ['NEWNAMEUSDT', U.VETO_AGE] in w['vetoes']
    with pytest.raises(U.UniverseError, match='rename'):
        universe(tmp_path, renames=[{'old': 'OLDNAMEUSDT', 'new': 'NEWNAMEUSDT', 'effective_ms': 1}])


def test_universe_digest_deterministic_write_once_and_cli_verify(tmp_path):
    for s, q in (('AAAUSDT', 10.0), ('BBBUSDT', 20.0)):
        put_daily(tmp_path / 'store', s, days('2024-01-01', 70, q))
    a, b = universe(tmp_path / 'store'), universe(tmp_path / 'store')
    assert a['digest'] == b['digest'] == U.digest_of(a)
    mpath, upath = tmp_path / 'm.json', tmp_path / 'u.json'
    M.write(build_manifest(tmp_path / 'store'), str(mpath))
    assert U.main(['build', '--manifest', str(mpath), '--store', str(tmp_path / 'store'), '--out', str(upath)]) == 0
    assert json.loads(upath.read_text())['digest'] == a['digest']
    assert U.main(['verify', str(upath), '--manifest', str(mpath), '--store', str(tmp_path / 'store')]) == 0
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


def test_committed_archive_manifest_and_universe_are_consistent():
    """Schema/digest only (the archive bytes live outside the repo; `manifest.py verify --store` re-hashes them)."""
    res = os.path.join(ROOT, 'research_evidence')
    m = M.load(os.path.join(res, 'manifests', 'binance-um-archive-v1.json.gz'))
    assert m['digest'] == '54912d9d2bf6e6fc45bc75c0553f0bd867e877972beeb3452785937379baf1e7'
    assert len(m['files']) == 103659 and len({f['symbol'] for f in m['files']}) == 900
    with open(os.path.join(res, 'universe', 'pit-top40-qv30d-v1.json'), encoding='utf-8') as f:
        u = json.load(f)
    assert u['digest'] == U.digest_of(u) and u['manifest_digest'] == m['digest'] and u['format'] == U.FORMAT
    assert all(len(w['members']) <= U.TOP_N and w['rules'] == U.RULES_BACKFILLED for w in u['weeks'])
    assert [w['monday_ms'] for w in u['weeks']] == list(range(u['weeks'][0]['monday_ms'],
                                                               u['weeks'][-1]['monday_ms'] + U.WEEK, U.WEEK))
