"""BT-EXEC-OBS-01: execution-observation manifest, PIT view, `zb-research-run/3` identity and the fail-closed promotion
gate (tools/research/exec_obs.py + report.py /3). Contract 6224963744, Codex ruling 6224996960, Cowork review
6224991417, coordinator amendments 1-9. Synthetic fixture zips built in tmp_path only (no network, no real data)."""
import copy
import hashlib
import io
import json
import os
import subprocess
import sys
import zipfile
from datetime import datetime, timezone

import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, 'tools', 'research'))
import costs as C                                                                           # noqa: E402
import exec_obs as E                                                                        # noqa: E402
import manifest as M                                                                        # noqa: E402
import missing_data as MD                                                                   # noqa: E402
import pit as P                                                                             # noqa: E402
import report as R                                                                          # noqa: E402
import splits as S                                                                          # noqa: E402

SYM = 'AAAUSDT'
DAY, HOUR = 86_400_000, 3_600_000
D0 = S.parse_utc('2024-01-01T00:00:00Z')
BT_STEP, AT_STEP, BD_STEP = 30_000, 30_000, 100_000
DIRS = {'book_ticker': 'bookTicker', 'agg_trades': 'aggTrades', 'book_depth_summary': 'bookDepth'}
NL = chr(10)
BANDS = (-5, -4, -3, -2, -1, 1, 2, 3, 4, 5)


# ------------------------------------------------------------------ fixture store
def zput(store, rel, text: str, *, sidecar=None):
    """A deterministic zip (fixed member date) + its `.ok` checksum sidecar."""
    p = os.path.join(store, *rel.split('/'))
    os.makedirs(os.path.dirname(p), exist_ok=True)
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, 'w', zipfile.ZIP_DEFLATED) as z:
        z.writestr(zipfile.ZipInfo(rel.rsplit('/', 1)[1][:-4] + '.csv', date_time=(1980, 1, 1, 0, 0, 0)), text)
    raw = buf.getvalue()
    with open(p, 'wb') as f:
        f.write(raw)
    with open(p + '.ok', 'w') as f:
        f.write(sidecar if sidecar is not None else hashlib.sha256(raw).hexdigest())
    return p


def rel_of(series, day, sym=SYM):
    tag = datetime.fromtimestamp((D0 + day * DAY) / 1000, timezone.utc).strftime('%Y-%m-%d')
    d = DIRS[series]
    return f'um/daily/{d}/{sym}/{sym}-{d}-{tag}.zip'


def times(series, day):
    a, step = D0 + day * DAY, {'book_ticker': BT_STEP, 'agg_trades': AT_STEP, 'book_depth_summary': BD_STEP}[series]
    return range(a + 1000, a + DAY, step)


def bt_row(t, alt=0):
    k = (t - D0) // BT_STEP
    bid = 100 + (k % 50) / 10 + alt
    return f'{t // 1000},{bid:.1f},1.5,{bid + 0.1:.1f},2.5,{t - 3},{t}'


def at_row(t, alt=0):
    k = (t - D0) // AT_STEP
    return f'{1000 + k},{100 + (k % 50) / 10 + alt:.1f},0.25,{3 * k},{3 * k + 2},{t},{"true" if k % 2 else "false"}'


def bd_rows(t, alt=0):
    ts = datetime.fromtimestamp(t // 1000, timezone.utc).strftime('%Y-%m-%d %H:%M:%S')
    return [f'{ts},{p},{abs(p) * 10 + alt}.5,{abs(p) * 1000 + alt}.25' for p in BANDS]


def lines(series, day, *, header=True, alt_after=None, drop=False):
    """One file's CSV text; rows with event > alt_after are perturbed (price/depth shifted, every 3rd dropped)."""
    out = [E.HEADERS[series].decode()] if header else []
    for i, t in enumerate(times(series, day)):
        fut = alt_after is not None and t > alt_after
        if fut and drop and i % 3 == 0:
            continue
        alt = 1 if fut else 0
        out += bd_rows(t, alt) if series == 'book_depth_summary' else [(bt_row if series == 'book_ticker'
                                                                        else at_row)(t, alt)]
    return '\n'.join(out) + '\n'


def make_store(store, days=(0, 1), series=tuple(DIRS), sym=SYM, **kw):
    for s in series:
        for d in days:
            zput(store, rel_of(s, d, sym), lines(s, d, **kw))
    return str(store)


def build(store, scope='bd-pct-1-5/1', **kw):
    return E.build(['um'], manifest_id='fx-exec', base=str(store), depth_scope_id=scope, **kw)


@pytest.fixture(scope='module')
def world(tmp_path_factory):
    store = make_store(tmp_path_factory.mktemp('exec'))
    return store, build(store)


# ------------------------------------------------------------------ an opened pit Window (access-controlled range)
def kline_ds(tmp):
    d = os.path.join(tmp, 'kl', 'data')
    os.makedirs(d, exist_ok=True)
    with open(os.path.join(d, f'{SYM}_1h.csv'), 'w', newline='\n') as f:
        f.write('t,o,h,l,c,v\n')
        for h in range(24 * 8):
            ts = datetime.fromtimestamp((D0 + h * HOUR) / 1000, timezone.utc).strftime('%Y-%m-%d %H:%M:%S')
            f.write(f'{ts},1,2,0.5,1.5,10\n')
    m = M.build(['data'], manifest_id='fx-kl', source_class='fixture', base=os.path.join(tmp, 'kl'))
    return P.Dataset(m, os.path.join(tmp, 'kl'), None)


def access(tmp, plan=None):
    led = os.path.join(tmp, 'ledger')
    os.makedirs(led, exist_ok=True)
    os.makedirs(os.path.join(tmp, 'runs'), exist_ok=True)
    return P.Access(kline_ds(tmp), plan, os.path.join(led, 'fam_x.jsonl'), candidate_id='c.v1', author='t',
                    cairo_date='2026-10-17')


def dev_window(tmp, start='2024-01-01T00:00:00Z', end='2024-01-03T00:00:00Z'):
    return access(str(tmp)).open_development(start, end, lineage='root')


def obs(m, store, tmp, lat='lat-none/1', **kw):
    return E.ExecObs(m, store, lat, dev_window(tmp, **kw))


def dump(v, sym=SYM) -> bytes:
    return M.canonical({'bt': v.book_ticker(sym), 'at': v.agg_trades(sym), 'bd': v.book_depth_summary(sym)})


# ------------------------------------------------------------------ 1. deterministic manifest + fresh-process replay
def test_manifest_is_deterministic_bytes_and_write_once(world, tmp_path):
    store, m = world
    m2 = build(store)
    E.write(m, str(tmp_path / 'a.json'))
    E.write(m2, str(tmp_path / 'b.json'))
    assert (tmp_path / 'a.json').read_bytes() == (tmp_path / 'b.json').read_bytes()
    assert m['digest'] == M.digest_of(m) and E.load(str(tmp_path / 'a.json')) == m
    E.write(m, str(tmp_path / 'a.json'))                                      # identical bytes: no-op
    other = dict(m, note='edited')
    other['digest'] = M.digest_of(other)
    with pytest.raises(E.ExecObsError, match='immutable'):
        E.write(other, str(tmp_path / 'a.json'))
    assert E.verify(m, store) == []
    assert {f['series'] for f in m['files']} == {'agg_trades', 'book_depth_summary', 'book_ticker'}
    bd = next(f for f in m['files'] if f['series'] == 'book_depth_summary')
    assert bd['first_seq'] is None and bd['last_seq'] is None and m['gap_report'] == []


REPLAY = r'''
import json, os, sys
sys.path.insert(0, os.path.join(sys.argv[1], 'tools', 'research'))
sys.path.insert(0, os.path.join(sys.argv[1], 'tests'))
import exec_obs as E, manifest as M, test_bt_exec_obs01 as T
store, tmp, t = sys.argv[2], sys.argv[3], int(sys.argv[4])
m = T.build(store)
o = E.ExecObs(m, store, 'lat-fixed-250/1', T.dev_window(tmp))
print(json.dumps({'digest': m['digest'], 'view': __import__('hashlib').sha256(T.dump(o.view(t))).hexdigest()}))
'''


def test_fresh_process_replay_same_digest_and_view(world, tmp_path):
    store, m = world
    t = D0 + 5 * HOUR + 1000
    here = hashlib.sha256(dump(obs(m, store, tmp_path / 'a', 'lat-fixed-250/1').view(t))).hexdigest()
    os.makedirs(tmp_path / 'b')
    p = subprocess.run([sys.executable, '-c', REPLAY, ROOT, store, str(tmp_path / 'b'), str(t)], capture_output=True,
                       text=True, stdin=subprocess.DEVNULL, env=dict(os.environ, PYTHONHASHSEED='12345'))
    assert p.returncode == 0, p.stderr
    assert json.loads(p.stdout) == {'digest': m['digest'], 'view': here}


# ------------------------------------------------------------------ 2. rejections (each guard on its own)
def one(tmp_path, series, text, day=0, scope='bd-pct-1-5/1', rel=None):
    zput(str(tmp_path), rel or rel_of(series, day), text)
    return lambda: build(str(tmp_path), scope)


def hdr(series):
    return E.HEADERS[series].decode()


T0 = D0 + 1000


@pytest.mark.parametrize('rows,match', [
    ([f'5,100.0,1,100.1,1,{T0},{T0}', f'5,100.0,1,100.1,1,{T0 + 9},{T0 + 9}'], 'update_id 5 not strictly increasing'),
    ([f'5,100.0,1,100.1,1,{T0},{T0}', f'4,100.0,1,100.1,1,{T0 + 9},{T0 + 9}'], 'update_id 4 not strictly increasing'),
    ([f'5,100.2,1,100.1,1,{T0},{T0}'], 'crossed or locked'),
    ([f'5,100.1,1,100.1,1,{T0},{T0}'], 'crossed or locked'),
    ([f'5,0,1,100.1,1,{T0},{T0}'], 'best_bid_price .* must be > 0'),
    ([f'5,100.0,0.0,100.1,1,{T0},{T0}'], 'best_bid_qty .* must be > 0'),
    ([f'5,100.0,1,100.1,0,{T0},{T0}'], 'best_ask_qty .* must be > 0'),
    ([f'5,100.0,1,100.1,1,{T0 + 1},{T0}'], 'transaction_time .* > event_time'),
    ([f'5,100.0,1,100.1,1,{T0},{T0 + 50}', f'6,100.0,1,100.1,1,{T0},{T0 + 40}'], 'event_time .* decreases'),
    ([f'5,100.0,1,100.1,1,{D0 - 5},{D0 - 1}'], 'outside the file period'),
    ([f'5,100.0,1,100.1,1,{T0},{D0 + DAY}'], 'outside the file period'),
    ([f'5,100.0,1,100.1,1,{T0},{T0 // 1000}'], 'not a canonical integer .13-digit ms'),
    ([f'5,1e2,1,100.1,1,{T0},{T0}'], 'not a canonical plain decimal'),
    ([f'5,0100.0,1,100.1,1,{T0},{T0}'], 'not a canonical plain decimal'),
    ([f'05,100.0,1,100.1,1,{T0},{T0}'], 'update_id .* not a canonical integer'),
    ([f'5,100.0,1,100.1,1,{T0}'], 'does not have 7 columns'),
    (['update_id,bid,bid_qty,ask,ask_qty,transaction_time,event_time', f'5,100.0,1,100.1,1,{T0},{T0}'],
     'unknown header'),
    ([], 'no rows'),
])
def test_book_ticker_rejections(tmp_path, rows, match):
    with pytest.raises(E.ExecObsError, match=match):
        one(tmp_path, 'book_ticker', '\n'.join(rows) + '\n' if rows else '')()


def test_book_ticker_header_is_optional(tmp_path):
    zput(str(tmp_path), rel_of('book_ticker', 0), lines('book_ticker', 0, header=False))
    a = build(str(tmp_path))
    zput(str(tmp_path), rel_of('book_ticker', 0), lines('book_ticker', 0, header=True))
    b = build(str(tmp_path))
    assert a['files'][0]['rows'] == b['files'][0]['rows'] == len(times('book_ticker', 0))


@pytest.mark.parametrize('rows,match', [
    ([f'7,100.0,1,10,12,{T0},true', f'7,100.0,1,13,13,{T0 + 9},false'], 'agg_trade_id 7 not strictly increasing'),
    ([f'7,100.0,1,10,12,{T0},true', f'8,100.0,1,12,13,{T0 + 9},false'], 'overlap the previous row'),
    ([f'7,100.0,1,12,10,{T0},true'], 'first_trade_id 12 > last_trade_id 10'),
    ([f'7,100.0,1,10,12,{T0 + 50},true', f'8,100.0,1,13,13,{T0},false'], 'transact_time .* decreases'),
    ([f'7,100.0,1,10,12,{T0},yes'], 'is_buyer_maker'),
    ([f'7,0,1,10,12,{T0},true'], 'price .* must be > 0'),
    ([f'7,100.0,0,10,12,{T0},true'], 'quantity .* must be > 0'),
    ([f'7,100.0,1,10,12,{D0 + DAY + 5},true'], 'outside the file period'),
    ([f'7,-1.0,1,10,12,{T0},true'], 'not a canonical plain decimal'),
])
def test_agg_trades_rejections(tmp_path, rows, match):
    with pytest.raises(E.ExecObsError, match=match):
        one(tmp_path, 'agg_trades', '\n'.join(rows) + '\n')()


def bd(ts='2024-01-01 00:00:10', bands=BANDS, depth=lambda p: abs(p) * 10, notional=lambda p: abs(p) * 1000):
    return [f'{ts},{p},{depth(p)},{notional(p)}' for p in bands]


@pytest.mark.parametrize('rows,match', [
    (bd(bands=BANDS[:-1]), 'bands .* != frozen depth scope'),
    (bd(bands=BANDS + (6,)), 'bands .* != frozen depth scope'),
    (bd(bands=(-5, -4, -3, -2, -1, 1, 2, 3, 4, 4)), 'bands .* != frozen depth scope'),
    (bd(depth=lambda p: 50 - abs(p)), 'not monotonic outward'),
    (bd(notional=lambda p: 9 if p == 2 else abs(p) * 1000), 'not monotonic outward'),
    (bd(depth=lambda p: 1 if p == -5 else abs(p) * 10), 'not monotonic outward'),
    (bd('2024-01-01 00:00:10') + bd('2024-01-01 00:00:05'), 'summary timestamp .* decreases'),
    (bd('2024-01-02 00:00:00'), 'outside the file period'),
    (bd('2024-1-1 00:00:10'), 'is not UTC'),
    (bd('2024-01-01T00:00:10'), 'is not UTC'),
    (bd('1704067210000'), 'is not UTC'),
    (bd(depth=lambda p: f'{abs(p)}e1'), 'not a canonical plain decimal'),
    (bd(depth=lambda p: -1), 'not a canonical plain decimal'),
    (['timestamp,pct,depth,notional'] + bd(), 'unknown header'),
])
def test_book_depth_summary_rejections(tmp_path, rows, match):
    with pytest.raises(E.ExecObsError, match=match):
        one(tmp_path, 'book_depth_summary', '\n'.join(rows) + '\n')()


def test_book_depth_summary_schema_accepts_equal_outward_and_header(tmp_path):
    zput(str(tmp_path), rel_of('book_depth_summary', 0),
         '\n'.join([hdr('book_depth_summary')] + bd(depth=lambda p: 7, notional=lambda p: 7)) + '\n')
    m = build(str(tmp_path))
    assert m['files'][0]['rows'] == 1 and m['files'][0]['first_event_ms'] == D0 + 10_000


def test_depth_needs_a_frozen_scope_and_unknown_scope_refused(tmp_path):
    zput(str(tmp_path), rel_of('book_depth_summary', 0), '\n'.join(bd()) + '\n')
    with pytest.raises(E.ExecObsError, match='needs a frozen depth scope'):
        build(str(tmp_path), scope='depth-none/1')
    with pytest.raises(E.ExecObsError, match='depth scope must be one of'):
        build(str(tmp_path), scope='bd-pct-1-10/1')


@pytest.mark.parametrize('rel,match', [
    ('um/daily/trades/AAAUSDT/AAAUSDT-trades-2024-01-01.zip', 'not um/'),
    ('um/monthly/bookDepth/AAAUSDT/AAAUSDT-bookDepth-2024-01.zip', 'not um/'),
    ('um/daily/bookTicker/BBBUSDT/AAAUSDT-bookTicker-2024-01-01.zip', 'not um/'),
    ('um/daily/bookTicker/AAAUSDT/AAAUSDT-bookTicker-2024-01.zip', 'not um/'),
    ('um/daily/bookTicker/AAAUSDT/AAAUSDT-bookTicker-2024-02-30.zip', 'not um/'),
])
def test_unknown_series_or_layout_is_refused_not_skipped(tmp_path, rel, match):
    zput(str(tmp_path), rel, f'5,100.0,1,100.1,1,{T0},{T0}\n')
    with pytest.raises(E.ExecObsError, match=match):
        build(str(tmp_path))


def test_gap_policy_is_an_allow_listed_id_never_a_threshold(tmp_path):
    make_store(tmp_path, days=(0,), series=('book_ticker',))
    with pytest.raises(E.ExecObsError, match='gap policy must be one of'):
        build(str(tmp_path), gap_policy_id='gap-policy/2')
    with pytest.raises(TypeError):
        build(str(tmp_path), max_gap_ms={'book_ticker': 5})
    m = build(str(tmp_path))
    bad = dict(m, gap_policy_id='gap-policy/0')
    bad['digest'] = M.digest_of(bad)
    with pytest.raises(E.ExecObsError, match='gap_policy_id must be one of'):
        E.validate(bad)


def test_overlapping_and_duplicate_periods_refused(tmp_path):
    make_store(tmp_path, days=(0,), series=('book_ticker',))
    zput(str(tmp_path), 'um/monthly/bookTicker/AAAUSDT/AAAUSDT-bookTicker-2024-01.zip',
         lines('book_ticker', 0))
    with pytest.raises(E.ExecObsError, match='overlap'):
        build(str(tmp_path))


def test_sequence_must_increase_across_file_boundaries(tmp_path):
    zput(str(tmp_path), rel_of('book_ticker', 0), f'9,100.0,1,100.1,1,{T0},{T0}\n')
    zput(str(tmp_path), rel_of('book_ticker', 1), f'9,100.0,1,100.1,1,{T0 + DAY},{T0 + DAY}\n')
    with pytest.raises(E.ExecObsError, match='does not increase across the file boundary'):
        build(str(tmp_path))
    zput(str(tmp_path), rel_of('agg_trades', 0), f'7,100.0,1,10,12,{T0},true\n')
    zput(str(tmp_path), rel_of('agg_trades', 1), f'7,100.0,1,13,13,{T0 + DAY},true\n')
    zput(str(tmp_path), rel_of('book_ticker', 1), f'10,100.0,1,100.1,1,{T0 + DAY},{T0 + DAY}\n')
    with pytest.raises(E.ExecObsError, match='does not increase across the file boundary'):
        build(str(tmp_path))


def test_gap_report_time_seq_file_and_cross_file_gaps(tmp_path):
    zput(str(tmp_path), rel_of('agg_trades', 0), f'7,100.0,1,10,12,{T0},true\n8,100.0,1,15,15,{T0 + 5},false\n')
    zput(str(tmp_path), rel_of('agg_trades', 1), f'10,100.0,1,16,16,{T0 + DAY},true\n')
    zput(str(tmp_path), rel_of('agg_trades', 3), f'11,100.0,1,17,17,{T0 + 3 * DAY},true\n')
    m = build(str(tmp_path))
    kinds = sorted((g['kind'], g['from'], g['to']) for g in m['gap_report'])
    assert ('seq', 12, 15) in kinds                                   # trade-id jump inside a file
    assert ('seq', 8, 10) in kinds                                    # agg id jump ACROSS the file boundary
    assert ('file', D0 + 2 * DAY, D0 + 3 * DAY) in kinds              # the missing day
    assert ('tail', T0 + 5, D0 + DAY) in kinds and ('head', D0 + DAY, T0 + DAY) not in kinds   # 1 s head < max gap
    f0 = next(f for f in m['files'] if f['path'] == rel_of('agg_trades', 0))
    assert f0['coverage']['covered_ms'] == T0 + 5 - D0 and f0['coverage']['gap_count'] == 2
    assert m['gap_report_digest'] == E.gap_report_digest(m['gap_report'])


def test_tampered_gap_digest_and_gap_report_fail(world, tmp_path):
    store, m = world
    bad = dict(m, gap_report_digest='0' * 64)
    bad['digest'] = M.digest_of(bad)
    with pytest.raises(E.ExecObsError, match='gap_report_digest does not match'):
        E.validate(bad)
    forged = copy.deepcopy(m)                                         # consistently re-digested lie about the files
    forged['gap_report'] = [{'symbol': SYM, 'series': 'book_ticker', 'path': m['files'][0]['path'], 'kind': 'time',
                             'from': D0, 'to': D0 + 1, 'at_ms': D0}]
    forged['gap_report'] = E._sort_gaps(forged['gap_report'])
    forged['gap_report_digest'] = E.gap_report_digest(forged['gap_report'])
    forged['digest'] = M.digest_of(forged)
    E.validate(forged)
    assert E.verify(forged, store) == ['gap_report_digest recomputed from the files differs from the manifest']


def test_tampered_file_hash_fails_before_parse(world, tmp_path):
    store, m = world
    t = tmp_path / 's'
    make_store(t)
    o = obs(m, str(t), tmp_path / 'w')
    zput(str(t), rel_of('book_ticker', 0), lines('book_ticker', 0, alt_after=D0))   # new bytes + matching sidecar
    with pytest.raises(E.ExecObsError, match='no longer match the manifest sha256'):
        o.view(D0 + HOUR).book_ticker(SYM)
    assert any('sha256' in x for x in E.verify(m, str(t)))


def test_tampered_checksum_sidecar_refused(world, tmp_path):
    store, m = world
    t = tmp_path / 's'
    make_store(t)
    zput(str(t), rel_of('agg_trades', 0), lines('agg_trades', 0), sidecar='f' * 64)
    o = obs(m, str(t), tmp_path / 'w')
    with pytest.raises(E.ExecObsError, match='published archive checksum'):
        o.view(D0 + HOUR).agg_trades(SYM)
    assert any('published archive checksum' in x for x in E.verify(m, str(t)))
    with pytest.raises(E.ExecObsError, match='published archive checksum'):
        build(str(t))


# ------------------------------------------------------------------ 3. PIT
@pytest.mark.parametrize('lat,feed', [('lat-none/1', 0), ('lat-fixed-250/1', 250)])
def test_pit_boundary_t_and_t_minus_1(world, tmp_path, lat, feed):
    store, m = world
    o = obs(m, store, tmp_path, lat)
    assert o.latency_label == ('none' if feed == 0 else 'assumed/unfitted')
    e = list(times('book_ticker', 0))[100]                            # an event time
    at = o.view(e + feed).book_ticker(SYM)
    assert at[-1].event_ms == e and at[-1].available_ms == e + feed   # available exactly at T: included
    assert o.view(e + feed - 1).book_ticker(SYM)[-1].event_ms < e      # T-1: excluded
    for r in at:
        assert r.available_ms <= e + feed
    a = list(times('agg_trades', 0))[7]
    assert o.view(a + feed).agg_trades(SYM)[-1].event_ms == a
    assert o.view(a + feed - 1).agg_trades(SYM)[-1].event_ms < a
    d = list(times('book_depth_summary', 0))[5] // 1000 * 1000
    assert o.view(d + feed).book_depth_summary(SYM)[-1].event_ms == d
    assert o.view(d + feed - 1).book_depth_summary(SYM)[-1].event_ms < d


def test_pit_event_at_or_before_t_but_not_yet_available_is_excluded(world, tmp_path):
    store, m = world
    o = obs(m, store, tmp_path, 'lat-fixed-250/1')
    e = list(times('book_ticker', 0))[100]
    for t in (e, e + 1, e + 249):                                     # event_ms <= T < available_ms
        assert all(r.event_ms < e for r in o.view(t).book_ticker(SYM))
    assert o.view(e + 250).book_ticker(SYM)[-1].event_ms == e


@pytest.mark.parametrize('lat,feed', [('lat-none/1', 0), ('lat-fixed-250/1', 250)])
def test_future_perturbation_cannot_change_output_at_t(world, tmp_path, lat, feed):
    store, m = world
    t = D0 + 7 * HOUR + 1000 + feed
    base = dump(obs(m, store, tmp_path / 'a', lat).view(t))
    alt = make_store(tmp_path / 'alt', alt_after=t - feed, drop=True)   # every row with available_ms > T changed/gone
    m2 = build(alt)
    assert m2['digest'] != m['digest']
    o2 = obs(m2, alt, tmp_path / 'b', lat)
    assert dump(o2.view(t)) == base
    later = t + 2 * HOUR
    assert dump(o2.view(later)) != dump(obs(m, store, tmp_path / 'c', lat).view(later))   # the mutation is real


def test_view_is_frozen_and_takes_int_time(world, tmp_path):
    store, m = world
    o = obs(m, store, tmp_path)
    v = o.view(D0 + HOUR)
    with pytest.raises(AttributeError):
        v.t = D0 + 2 * HOUR
    with pytest.raises(E.ExecObsError):
        o.view(float(D0 + HOUR))
    with pytest.raises(E.ExecObsError, match='latency policy'):
        E.ExecObs(m, store, 'lat-fixed-100/1', dev_window(tmp_path / 'x'))


# ------------------------------------------------------------------ amendment 8: no side channel into the holdout
PLAN = [{'name': 'train', 'start': '2024-01-02T00:00:00Z', 'end': '2024-01-03T00:00:00Z'},
        {'name': 'walk_forward', 'fold': 0, 'start': '2024-01-03T00:00:00Z', 'end': '2024-01-04T00:00:00Z'},
        {'name': 'holdout', 'start': '2024-01-04T00:00:00Z', 'end': '2024-01-05T00:00:00Z'}]


def test_exec_rows_only_inside_an_opened_window_never_past_hi(tmp_path):
    store = make_store(tmp_path / 's', days=(0, 1, 2, 3, 4))                 # covers the holdout dates too
    m = build(store)
    plan = S.SplitPlan(PLAN, interval='1h', lookback_bars=15, horizon_bars=1)
    w = access(str(tmp_path / 'a'), plan).open('train', lineage='root')
    o = E.ExecObs(m, store, 'lat-none/1', w)
    with pytest.raises(E.ExecObsError, match='outside the opened window'):
        o.view(w.hi + 1)
    with pytest.raises(E.ExecObsError, match='outside the opened window'):
        o.view(S.parse_utc('2024-01-04T12:00:00Z'))
    v = o.view(w.hi)
    for rows in (v.book_ticker(SYM), v.agg_trades(SYM), v.book_depth_summary(SYM)):
        assert rows and all(w.lo <= r.event_ms and r.available_ms <= w.hi for r in rows)
    assert w.lo > D0 and 0 <= v.book_ticker(SYM)[0].event_ms - w.lo < BT_STEP   # store has earlier rows: cut at lo
    with pytest.raises(P.PITError, match='sealed holdout'):              # the holdout opens only through its guard
        access(str(tmp_path / 'b'), plan).open('holdout', lineage='root')


def test_free_standing_ranges_are_refused(world, tmp_path):
    store, m = world
    w = dev_window(tmp_path)
    for bad in (None, (w.lo, w.hi), {'lo': w.lo, 'hi': w.hi}):
        with pytest.raises(E.ExecObsError, match='only inside a pit.Window'):
            E.ExecObs(m, store, 'lat-none/1', bad)
    fake = object.__new__(P.Window)
    fake.lo, fake.hi, fake._cap = w.lo, w.hi, P._Cap()
    with pytest.raises(E.ExecObsError, match='only inside a pit.Window'):
        E.ExecObs(m, store, 'lat-none/1', fake)


# ------------------------------------------------------------------ 4. run identity /2 and /3
def h(s):
    return hashlib.sha256(s.encode()).hexdigest()


def run2():
    files = sorted(set(R.CORE_EVAL_FILES) | {'tools/research/trend_ema_mom.py'})
    ev = {'files': [{'path': p, 'sha256': h('file:' + p)} for p in files], 'config': {'k': 1, 'symbols': ['BTCUSDT']},
          'entrypoint': {'path': 'tools/research/trend_ema_mom.py', 'function': 'decide', 'summary': 'summarize'},
          'schedule': {'n': 3, 'digest': h('schedule')}}
    xe = {'implementation': 'cpython', 'python': '3.14.0', 'version': '3.14.0 fixture', 'hexversion': 51249392,
          'cache_tag': 'cpython-314', 'executable_sha256': h('exe'), 'os': 'nt', 'platform': 'win32-AMD64',
          'pointer_bits': 64, 'cpu_count': 8, 'install_digest': h('install')}
    run = {'family': 'fam', 'candidate_id': 'fam.v1', 'split': 'development',
           'window': {'start': '2024-01-01T00:00:00Z', 'end': '2024-01-02T00:00:00Z'}, 'books': ['crypto'],
           'seeds': [7], 'eval': ev, 'code': {'git_head': 'a' * 40, 'dirty': False, 'python': '3.14.0', 'libs': {},
                                              'execution_environment': xe},
           'author': 'claude-code', 'cairo_date': '2026-10-17'}
    for k in R.DIGESTS[:-1]:
        run[k] = h(k)
    run['eval_digest'] = R.eval_digest(ev, run['seeds'])
    return run


# Computed with master 0944547's report.py before this ticket touched it (the /2 contract is frozen byte for byte).
RUN2_DIGEST = '9d4044c20b9c0df799939bb345fc8872db39e7c13ef66e002b341a8f06f7bb64'
RUN2_FILE_SHA = 'd47284229656ffae3526d098de95b5a362cd73dca36d762bb218e523e1f0761c'


def run3(m=None, claims=(), symbols=(SYM,), execution=None, **eo):
    run = run2()
    files = sorted(set(R.CORE_EVAL_FILES_3) | {'tools/research/trend_ema_mom.py'})
    run['eval'] = dict(run['eval'], files=[{'path': p, 'sha256': h('file:' + p)} for p in files],
                       config=dict({'k': 1, 'symbols': list(symbols)},
                                   **({} if execution is None else {'execution': execution})))
    run['eval_digest'] = R.eval_digest(run['eval'], run['seeds'])
    run['exec_obs'] = dict({'manifest_digest': m['digest'] if m else None,
                            'gap_report_digest': m['gap_report_digest'] if m else None,
                            'latency_policy_id': 'lat-fixed-250/1', 'depth_scope_id': 'bd-pct-1-5/1',
                            'queue_policy_id': 'queue-fifo-back/1', 'gap_policy_id': 'gap-policy/1',
                            'coverage_policy_id': 'coverage-policy/1', 'claims': sorted(claims)}, **eo)
    return run


def test_v2_run_digest_and_envelope_bytes_identical_to_master(tmp_path):
    r = run2()
    assert R.run_format(r) == R.FORMAT == 'zb-research-run/2'
    assert R.run_digest(r) == RUN2_DIGEST == hashlib.sha256(M.canonical(r)).hexdigest()
    env = R.envelope(r)
    assert env['format'] == R.FORMAT and set(env) == {'format', 'run', 'run_digest'}
    p = R.write_envelope(str(tmp_path), env)
    with open(p, 'rb') as f:
        assert hashlib.sha256(f.read()).hexdigest() == RUN2_FILE_SHA
    assert R.load_envelope(str(tmp_path), RUN2_DIGEST) == env
    with pytest.raises(R.ReportError, match='not zb-research-run/3'):  # a /2 block is never accepted as /3
        R.validate_run(r, R.FORMAT_3)


def test_registered_prereg_files_unchanged():
    import run_r4 as G
    for rel, gate in (('research_evidence/prereg/trend_ema_mom.v1.json', 'research_evidence/prereg/r4_gate.json'),
                      ('research_evidence/prereg/trend_ema_mom_short.v1.json',
                       'research_evidence/prereg/r4_gate_short.json')):
        with open(os.path.join(ROOT, rel), 'rb') as f:
            raw = f.read()
        base = subprocess.run(['git', '-C', ROOT, 'show', f'0944547:{rel}'], capture_output=True, check=True,
                              stdin=subprocess.DEVNULL).stdout
        assert raw == base
        assert G.validate_prereg(json.loads(raw.decode('utf-8')), ROOT) == []
        with open(os.path.join(ROOT, gate), encoding='utf-8') as f:
            pin = json.load(f).get('prereg_sha256')
        assert pin is None or pin == hashlib.sha256(raw).hexdigest()


def test_core_eval_files_v2_unchanged_and_exec_obs_only_in_v3():
    assert 'tools/research/exec_obs.py' not in R.CORE_EVAL_FILES
    assert set(R.CORE_EVAL_FILES_3) == set(R.CORE_EVAL_FILES) | {'tools/research/exec_obs.py'}
    assert 'tools/research/exec_obs.py' not in R.import_closure(ROOT, list(R.CORE_EVAL_FILES))
    assert 'tools/research/exec_obs.py' in R.import_closure(ROOT, list(R.CORE_EVAL_FILES_3))
    r = run3()
    R.validate_run(r)
    r['eval'] = dict(r['eval'], files=[f for f in r['eval']['files'] if f['path'] != 'tools/research/exec_obs.py'])
    r['eval_digest'] = R.eval_digest(r['eval'], r['seeds'])
    with pytest.raises(R.ReportError, match='CORE_EVAL_FILES_3'):
        R.validate_run(r)


def test_v3_envelope_roundtrip_and_distinct_from_v2(world, tmp_path):
    _, m = world
    r = run3()
    env = R.envelope(r)
    assert env['format'] == R.FORMAT_3 and R.run_format(r) == R.FORMAT_3
    p = R.write_envelope(str(tmp_path), env)
    assert R.load_envelope(str(tmp_path), env['run_digest']) == env and p.endswith(env['run_digest'] + '.json')
    plain = {k: v for k, v in r.items() if k != 'exec_obs'}               # the same run with no exec block
    assert R.run_digest(plain) != env['run_digest']
    with pytest.raises(R.ReportError):
        R.write_envelope(str(tmp_path), dict(env, format=R.FORMAT))
    with open(p, 'w') as f:
        json.dump(dict(env, format=R.FORMAT), f)
    with pytest.raises(R.ReportError, match='is not'):
        R.load_envelope(str(tmp_path), env['run_digest'])


@pytest.mark.parametrize('field,value', [
    ('manifest_digest', 'b' * 64), ('gap_report_digest', 'c' * 64), ('latency_policy_id', 'lat-none/1'),
    ('depth_scope_id', 'depth-none/1'), ('queue_policy_id', 'queue-none/1'), ('claims', ['maker_fill']),
])
def test_v3_run_digest_changes_with_each_exec_field(world, field, value):
    _, m = world
    a = run3(m)
    b = copy.deepcopy(a)
    b['exec_obs'][field] = value
    assert R.run_digest(a) != R.run_digest(b)


def test_v3_run_digest_changes_with_data_and_config(world, tmp_path):
    _, m = world
    a = run3(m)
    alt = make_store(tmp_path, days=(0,))
    m2 = build(alt)
    assert R.run_digest(a) != R.run_digest(run3(m2))
    b = copy.deepcopy(a)
    b['eval']['config']['k'] = 2
    b['eval_digest'] = R.eval_digest(b['eval'], b['seeds'])
    assert R.run_digest(a) != R.run_digest(b)


@pytest.mark.parametrize('field,value,match', [
    ('claims', ['fill_magic'], 'unknown claim'), ('claims', ['maker_fill', 'latency'], 'sorted unique'),
    ('claims', ['latency', 'latency'], 'sorted unique'), ('latency_policy_id', 'lat-fixed-100/1', 'latency_policy_id'),
    ('depth_scope_id', 'l2-20/1', 'depth_scope_id'), ('queue_policy_id', 'queue-pro-rata/1', 'queue_policy_id'),
    ('manifest_digest', 'XYZ', 'manifest_digest must be'), ('manifest_digest', None, 'both null or both set'),
])
def test_v3_block_refusals(world, field, value, match):
    _, m = world
    r = run3(m)
    r['exec_obs'][field] = value
    with pytest.raises(R.ReportError, match=match):
        R.run_digest(r)


def test_v3_block_keys_exact():
    r = run3()
    r['exec_obs']['extra'] = 1
    with pytest.raises(R.ReportError, match='keys must be exactly'):
        R.run_digest(r)


# ------------------------------------------------------------------ 5. promotion: every claim refused, reasons
ALL = list(E.CLAIMS)


def test_no_claims_is_todays_behaviour_promotable(world):
    store, m = world
    assert E.realism_refusals(run3(m), m, store=store) == []
    assert E.realism_refusals(run3(), None, store=None) == []


@pytest.mark.parametrize('claim', ALL)
def test_every_claim_refused_model_not_implemented_even_with_full_data(world, claim):
    store, m = world
    r = run3(m, [claim], latency_policy_id='lat-fixed-250/1')
    out = E.realism_refusals(r, m, store=store)
    assert f'model_not_implemented:{claim}' in out
    want = {f'model_not_implemented:{claim}'} | ({'latency_unfitted:latency'} if claim == 'latency' else set())
    assert set(out) == want                                           # data complete: only the model refusals


def test_latency_none_policy_and_queue_none_and_depth_scope_mismatch(world):
    store, m = world
    out = E.realism_refusals(run3(m, ['latency'], latency_policy_id='lat-none/1'), m, store=store)
    assert 'latency_policy_none:latency' in out and 'latency_unfitted:latency' in out
    out = E.realism_refusals(run3(m, ['queue_position'], queue_policy_id='queue-none/1'), m, store=store)
    assert 'queue_policy_none:queue_position' in out
    out = E.realism_refusals(run3(m, ['depth_impact'], depth_scope_id='depth-none/1'), m, store=store)
    assert 'depth_scope_mismatch:depth_impact' in out


@pytest.mark.parametrize('claim,missing', [('maker_fill', 'agg_trades'), ('maker_fill', 'book_ticker'),
                                           ('queue_position', 'agg_trades'), ('queue_position', 'book_ticker'),
                                           ('depth_impact', 'book_depth_summary')])
def test_missing_series_refused_per_claim(tmp_path, claim, missing):
    store = make_store(tmp_path, series=tuple(s for s in DIRS if s != missing))
    m = build(store)
    out = E.realism_refusals(run3(m, [claim]), m, store=store)
    assert f'missing_series:{claim}:{SYM}/{missing}' in out and f'model_not_implemented:{claim}' in out


def test_run_symbol_without_manifest_entry(world):
    store, m = world
    out = E.realism_refusals(run3(m, ['maker_fill'], symbols=(SYM, 'ZZZUSDT')), m, store=store)
    assert {'missing_series:maker_fill:ZZZUSDT/agg_trades', 'missing_series:maker_fill:ZZZUSDT/book_ticker'} <= set(out)
    assert not any(SYM + '/' in x for x in out)
    out = E.realism_refusals(run3(m, ['maker_fill'], symbols=()), m, store=store)
    assert 'run_symbols_undeclared:maker_fill' in out


def test_manifest_for_a_different_window_is_a_coverage_refusal(world):
    store, m = world
    r = run3(m, ['maker_fill', 'depth_impact'])
    r['window'] = {'start': '2024-02-01T00:00:00Z', 'end': '2024-02-02T00:00:00Z'}
    out = E.realism_refusals(r, m, store=store)
    for k in ('maker_fill:AAAUSDT/agg_trades', 'maker_fill:AAAUSDT/book_ticker',
              'depth_impact:AAAUSDT/book_depth_summary'):
        assert f'coverage_below_min:{k}' in out
    r['window'] = {'start': '2024-01-01T12:00:00Z', 'end': '2024-01-03T12:00:00Z'}  # half a day past the data
    assert 'coverage_below_min:maker_fill:AAAUSDT/book_ticker' in E.realism_refusals(r, m, store=store)


def test_coverage_shortfall_inside_files_by_policy_and_no_caller_threshold(tmp_path):
    store = str(tmp_path)
    make_store(tmp_path, series=('agg_trades', 'book_depth_summary'))
    t = [x for x in times('book_ticker', 0) if not D0 + 6 * HOUR <= x < D0 + 10 * HOUR]  # 4 h hole of 48 h
    zput(store, rel_of('book_ticker', 0), NL.join(bt_row(x) for x in t) + NL)
    zput(store, rel_of('book_ticker', 1), lines('book_ticker', 1))
    m = build(store)
    r = run3(m, ['maker_fill'])
    r['window'] = {'start': '2024-01-01T00:00:00Z', 'end': '2024-01-03T00:00:00Z'}
    assert 'coverage_below_min:maker_fill:AAAUSDT/book_ticker' in E.realism_refusals(r, m, store=store)
    r['window'] = {'start': '2024-01-01T12:00:00Z', 'end': '2024-01-03T00:00:00Z'}  # the hole is outside: covered
    assert not any('coverage' in x for x in E.realism_refusals(r, m, store=store))
    with pytest.raises(TypeError):                                     # thresholds are never caller-selected
        E.realism_refusals(r, m, store=store, min_coverage=0.5)
    r['exec_obs']['coverage_policy_id'] = 'coverage-policy/0'
    assert any(x.startswith('exec_obs_invalid') and 'coverage_policy_id' in x
               for x in E.realism_refusals(r, m, store=store))


def test_missing_period_and_sequence_gap_are_refusals_at_window_level(tmp_path):
    store = make_store(tmp_path, days=(0, 2), series=('book_ticker', 'book_depth_summary'))
    zput(store, rel_of('agg_trades', 0), lines('agg_trades', 0))
    zput(store, rel_of('agg_trades', 2), lines('agg_trades', 2))
    m = build(store)
    r = run3(m, ['maker_fill'])
    r['window'] = {'start': '2024-01-01T00:00:00Z', 'end': '2024-01-03T00:00:00Z'}
    out = E.realism_refusals(r, m, store=store)
    assert 'missing_period:maker_fill:AAAUSDT/book_ticker' in out
    assert 'sequence_gap:maker_fill:AAAUSDT/agg_trades' in out         # agg ids jump across the missing day
    assert 'coverage_below_min:maker_fill:AAAUSDT/book_ticker' in out
    r['window'] = {'start': '2024-01-03T00:00:00Z', 'end': '2024-01-03T12:00:00Z'}
    assert set(E.realism_refusals(r, m, store=store)) == {'model_not_implemented:maker_fill'}


def test_book_ticker_update_id_jumps_are_not_gaps(tmp_path):
    zput(str(tmp_path), rel_of('book_ticker', 0), NL.join(
        f'{u},100.0,1,100.1,1,{T0 + i * 1000},{T0 + i * 1000}' for i, u in enumerate((5, 900, 901, 10 ** 12))) + NL)
    m = build(str(tmp_path))
    assert not any(g['kind'] == 'seq' for g in m['gap_report'])


def test_promotion_recomputes_digests_never_trusts_the_envelope(world, tmp_path):
    store, m = world
    r = run3(m, ['maker_fill'])
    r['exec_obs']['manifest_digest'] = 'd' * 64
    assert 'manifest_digest_mismatch' in E.realism_refusals(r, m, store=store)
    r = run3(m, ['maker_fill'])
    r['exec_obs']['gap_report_digest'] = 'e' * 64
    assert 'gap_report_digest_mismatch' in E.realism_refusals(r, m, store=store)
    lie = dict(m, files=list(m['files']))                             # self-consistent lie: digest field edited too
    lie['note'] = 'x'
    out = E.realism_refusals(run3(m, ['maker_fill']), lie, store=store)
    assert any(x.startswith('manifest_invalid') for x in out) and 'model_not_implemented:maker_fill' in out
    lie['digest'] = M.digest_of(lie)
    assert 'manifest_digest_mismatch' in E.realism_refusals(run3(m, ['maker_fill']), lie, store=store)
    t = tmp_path / 's'                                                 # files on disk differ from the manifest
    make_store(t, alt_after=D0)
    out = E.realism_refusals(run3(m, ['maker_fill']), m, store=str(t))
    assert any(x.startswith('manifest_files_unverified') for x in out)


def test_manifest_listing_an_extra_file_absent_from_the_store(world, tmp_path):
    store, m = world
    t = tmp_path / 's'
    make_store(t)
    os.remove(os.path.join(str(t), *rel_of('book_depth_summary', 1).split('/')))   # listed but not stored
    out = E.realism_refusals(run3(m, ['maker_fill']), m, store=str(t))
    assert any(x.startswith('manifest_files_unverified') and 'bookDepth' in x for x in out)
    assert any('bookDepth' in x for x in E.verify(m, str(t)))


def test_unlisted_extra_store_file_is_never_served(world, tmp_path):
    store, m = world
    t = tmp_path / 's'
    make_store(t)
    make_store(t, days=(0,), series=('book_ticker',), sym='BBBUSDT')   # on disk, not in the manifest
    o = obs(m, str(t), tmp_path / 'w')
    assert o.view(D0 + HOUR).book_ticker('BBBUSDT') == ()
    assert E.realism_refusals(run3(m), m, store=str(t)) == []
    out = E.realism_refusals(run3(m, ['maker_fill'], symbols=('BBBUSDT',)), m, store=str(t))
    assert 'missing_series:maker_fill:BBBUSDT/book_ticker' in out


def test_no_manifest_and_unknown_claims(world):
    store, m = world
    out = E.realism_refusals(run3(None, ['depth_impact', 'maker_fill']), None, store=store)
    assert out == sorted(['model_not_implemented:depth_impact', 'model_not_implemented:maker_fill',
                          'no_manifest:depth_impact', 'no_manifest:maker_fill'])
    assert 'manifest_unavailable:maker_fill' in E.realism_refusals(run3(m, ['maker_fill']), None, store=store)
    assert 'manifest_unavailable:maker_fill' in E.realism_refusals(run3(m, ['maker_fill']), m, store=None)
    r = run3(m, ['maker_fill'])
    r['exec_obs']['claims'] = ['maker_fill', 'zz_teleport']
    out = E.realism_refusals(r, m, store=store)
    assert 'unknown_claim:zz_teleport' in out and any(x.startswith('exec_obs_invalid') for x in out)


# ------------------------------------------------------------------ make_report /3 label (and /2 shape unchanged)
def results():
    rows = {row: {s: {k: 0 for k in keys} for s, keys in R.SECTIONS.items()} for row in C.STRESS}
    return {'status': MD.COMPLETE, 'missing_data_policy': MD.POLICY, 'long': rows}


@pytest.fixture
def no_att(monkeypatch):
    monkeypatch.setattr(R, 'check_attestation', lambda env, att, led: 'f' * 64)
    monkeypatch.setattr(R, 'exec_env_mismatch', lambda *a: [])


def test_make_report_v3_labels_non_promotable_and_v2_shape_unchanged(world, no_att):
    store, m = world
    res = results()
    att = {'results_digest': R.results_digest(res)}
    rep = R.make_report(R.envelope(run3(m, ['maker_fill'])), res, att, 'unused', exec_manifest=m, exec_store=store)
    assert rep['exec_realism'] == {'claims': ['maker_fill'], 'refusals': ['model_not_implemented:maker_fill'],
                                   'promotable': False}
    assert rep['report_digest'] == R._digest({k: v for k, v in rep.items() if k != 'report_digest'})
    ok = R.make_report(R.envelope(run3(m)), res, att, 'unused', exec_manifest=m, exec_store=store)
    assert ok['exec_realism'] == {'claims': [], 'refusals': [], 'promotable': True}
    two = R.make_report(R.envelope(run2()), res, att, 'unused')
    assert 'exec_realism' not in two and set(two) == {'format', 'run', 'run_digest', 'results', 'attestation',
                                                      'attestation_digest', 'report_digest'}
    env = R.envelope(run3(m))
    with pytest.raises(R.ReportError, match='format'):
        R.make_report(dict(env, format=R.FORMAT), res, att, 'unused')


# ------------------------------------------------------------------ Codex addendum 6225107087
# item 1: each authentic schema on its own; a row the schema cannot hold is `source_schema_mismatch`
@pytest.mark.parametrize('series,rows', [
    ('book_ticker', [f'5,100.0,1,100.1,1,{T0}']),                                  # 6 of 7 columns
    ('book_ticker', [f'2024-01-01 00:00:01,-1,10,1000']),                          # a depth row in a ticker file
    ('book_ticker', [f'5,100.0,1,100.1,1,{T0},{T0},extra']),
    ('book_depth_summary', [f'5,100.0,1,100.1,1,{T0},{T0}']),                      # a ticker row in a depth file
    ('book_depth_summary', [f'{T0},-1,10,1000']),                                  # epoch ms is not the depth schema
    ('book_depth_summary', ['2024-01-01 00:00:01,-1,10,1000,7']),                  # an invented sequence column
    ('agg_trades', [f'7,100.0,1,10,12,{T0}']),                                     # no is_buyer_maker
    ('agg_trades', [f'7,100.0,1,10,12,{T0},True']),
    ('agg_trades', [f'7,100.0,1,10,12,{T0},true,{T0}']),                           # an invented event-time column
    ('agg_trades', [E.HEADERS['book_ticker'].decode(), f'7,100.0,1,10,12,{T0},true']),
    ('book_ticker', []),
])
def test_incompatible_rows_refuse_source_schema_mismatch(tmp_path, series, rows):
    with pytest.raises(E.ExecObsError, match='^source_schema_mismatch: '):
        one(tmp_path, series, NL.join(rows) + NL if rows else '')()


def test_schema_and_integrity_faults_create_no_artifact(tmp_path):
    store = make_store(tmp_path / 's', days=(0,))
    zput(store, rel_of('book_ticker', 0), f'5,100.2,1,100.1,1,{T0},{T0}' + NL)       # crossed book
    out = tmp_path / 'm.json'
    assert E.main(['build', '--store', store, '--id', 'x', '--root', 'um', '--depth-scope', 'bd-pct-1-5/1',
                   '--out', str(out)]) == 2
    assert not out.exists()
    zput(store, rel_of('book_ticker', 0), lines('book_ticker', 0))
    assert E.main(['build', '--store', store, '--id', 'x', '--root', 'um', '--depth-scope', 'bd-pct-1-5/1',
                   '--out', str(out)]) == 0 and out.exists()
    assert E.main(['verify', str(out), '--store', store]) == 0


# item 2: needed realism is derived from the registered execution config; empty claims cannot bypass
@pytest.mark.parametrize('execution,need', [
    ({'entry_liquidity': 'maker'}, ['maker_fill', 'queue_position']),
    ({'exit_liquidity': 'maker'}, ['maker_fill', 'queue_position']),
    ({'target_order': 'limit'}, ['maker_fill', 'queue_position']),
    ({'latency': 'modelled'}, ['latency']),
    ({'depth_impact': 'modelled'}, ['depth_impact']),
    ({'queue': 'modelled'}, ['queue_position']),
])
def test_needed_claims_derived_from_execution_config(world, execution, need):
    store, m = world
    out = E.realism_refusals(run3(m, [], execution=execution), m, store=store)
    for c in need:
        assert f'exec_claims_incomplete:{c}' in out and f'model_not_implemented:{c}' in out
    full = E.realism_refusals(run3(m, need, execution=execution), m, store=store)
    assert not any(x.startswith('exec_claims_incomplete') for x in full) and full     # still atomically refused


def test_taker_execution_config_needs_nothing(world):
    store, m = world
    taker = {'entry_liquidity': 'taker', 'exit_liquidity': 'taker', 'target_order': 'market', 'latency': 'none',
             'depth_impact': 'none', 'queue': 'none'}
    assert E.realism_refusals(run3(m, [], execution=taker), m, store=store) == []


@pytest.mark.parametrize('execution,config_extra,reason', [
    ({'fill_engine': 'x'}, {}, 'unknown_execution_key:fill_engine'),
    ({'entry_liquidity': 'post_only'}, {}, 'unknown_execution_value:entry_liquidity'),
    ({'entry_liquidity': 'Maker'}, {}, 'unknown_execution_value:entry_liquidity'),       # no normalization
    ({'latency': 250}, {}, 'unknown_execution_value:latency'),
    (None, {'maker_entries': True}, 'unscoped_execution_key:maker_entries'),
    (None, {'latency_ms': 250}, 'unscoped_execution_key:latency_ms'),
    ('maker', {}, 'execution_config_invalid'),
])
def test_unknown_execution_keys_fail_closed(world, execution, config_extra, reason):
    store, m = world
    r = run3(m, [])
    r['eval']['config'].update(config_extra)
    if execution is not None:
        r['eval']['config']['execution'] = execution
    out = E.realism_refusals(r, m, store=store)
    assert any(x.startswith(reason) for x in out)


def test_make_report_refuses_promotion_atomically_on_undeclared_need(world, no_att):
    store, m = world
    res = results()
    att = {'results_digest': R.results_digest(res)}
    rep = R.make_report(R.envelope(run3(m, [], execution={'entry_liquidity': 'maker'})), res, att, 'unused',
                        exec_manifest=m, exec_store=store)
    assert rep['exec_realism']['promotable'] is False and rep['exec_realism']['claims'] == []
    assert 'exec_claims_incomplete:maker_fill' in rep['exec_realism']['refusals']


def evidence_snapshot():
    out = {}
    for d, _, names in os.walk(os.path.join(ROOT, 'research_evidence')):
        for n in names:
            with open(os.path.join(d, n), 'rb') as f:
                out[os.path.join(d, n)] = hashlib.sha256(f.read()).hexdigest()
    return out


def test_refusal_paths_mutate_no_ledger_or_registry(world, no_att, tmp_path):
    store, m = world
    before = evidence_snapshot()
    res = results()
    att = {'results_digest': R.results_digest(res)}
    for claims in ([], ['maker_fill', 'queue_position'], ALL):
        R.make_report(R.envelope(run3(m, claims, execution={'queue': 'modelled'})), res, att, 'unused',
                      exec_manifest=m, exec_store=store)
        E.realism_refusals(run3(None, claims), None, store=None)
    assert evidence_snapshot() == before


# item 3: policy ids strict, mismatch refuses
def test_gap_policy_mismatch_between_run_and_manifest(world, monkeypatch):
    store, m = world
    monkeypatch.setitem(E.GAP_POLICIES, 'gap-policy/2', dict(E.GAP_POLICIES['gap-policy/1']))
    r = run3(m, ['maker_fill'], gap_policy_id='gap-policy/2')
    assert 'gap_policy_mismatch' in E.realism_refusals(r, m, store=store)


@pytest.mark.parametrize('field,value', [('gap_policy_id', 'gap-policy/1 '), ('gap_policy_id', 'GAP-POLICY/1'),
                                         ('coverage_policy_id', 'coverage-policy/2'), ('coverage_policy_id', None),
                                         ('latency_policy_id', 'LAT-NONE/1')])
def test_v3_policy_ids_strict_no_normalization(world, field, value):
    _, m = world
    r = run3(m)
    r['exec_obs'][field] = value
    with pytest.raises(R.ReportError, match=field):
        R.run_digest(r)


# item 4: raw sidecar bytes and the archive member are bound; bad members hard-refuse
def zmembers(store, rel, members):
    p = os.path.join(store, *rel.split('/'))
    os.makedirs(os.path.dirname(p), exist_ok=True)
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, 'w') as z:
        for name, text in members:
            z.writestr(zipfile.ZipInfo(name, date_time=(1980, 1, 1, 0, 0, 0)), text)
    raw = buf.getvalue()
    with open(p, 'wb') as f:
        f.write(raw)
    with open(p + '.ok', 'w') as f:
        f.write(hashlib.sha256(raw).hexdigest())


@pytest.mark.parametrize('members', [
    lambda own, txt: [(own, txt), ('extra.csv', txt)],                                   # extra member
    lambda own, txt: [(own.replace('2024-01-01', '2024-01-02'), txt)],                   # substituted day
    lambda own, txt: [(own.replace('AAAUSDT', 'BBBUSDT'), txt)],                         # substituted symbol
    lambda own, txt: [(own.replace('bookTicker', 'aggTrades'), txt)],                    # substituted series
    lambda own, txt: [('../' + own, txt)],                                               # traversal
    lambda own, txt: [('sub/' + own, txt)],
])
def test_bad_archive_members_hard_refuse_before_any_use(world, tmp_path, members):
    _, m = world
    rel = rel_of('book_ticker', 0)
    txt = lines('book_ticker', 0)
    t = make_store(tmp_path / 's')
    zmembers(t, rel, members(E.member_of(rel), txt))
    with pytest.raises(E.ExecObsError, match='zip must hold exactly'):
        build(t)
    o = obs(m, t, tmp_path / 'w')
    with pytest.raises(E.ExecObsError, match='zip must hold exactly'):
        o.view(D0 + HOUR).book_ticker(SYM)
    assert any('zip must hold exactly' in x for x in E.verify(m, t))


def test_raw_sidecar_bytes_are_bound(world, tmp_path):
    store, m = world
    f = next(x for x in m['files'] if x['path'] == rel_of('agg_trades', 0))
    assert f['member'] == 'AAAUSDT-aggTrades-2024-01-01.csv'
    with open(os.path.join(store, *f['path'].split('/')) + '.ok', 'rb') as fh:
        assert f['sidecar_sha256'] == hashlib.sha256(fh.read()).hexdigest()
    t = make_store(tmp_path / 's')
    side = os.path.join(t, *f['path'].split('/')) + '.ok'
    with open(side, 'ab') as fh:
        fh.write(b'\n')                                  # same checksum text, different raw sidecar bytes
    assert any('sidecar_sha256' in x for x in E.verify(m, t))
    with pytest.raises(E.ExecObsError, match='sidecar'):
        obs(m, t, tmp_path / 'w').view(D0 + HOUR).agg_trades(SYM)
    bad = copy.deepcopy(m)
    bad['files'][0]['member'] = 'other.csv'
    bad['digest'] = M.digest_of(bad)
    with pytest.raises(E.ExecObsError, match='archive member must be exactly'):
        E.validate(bad)


# item 5: deterministic order for equal timestamps; future data changes digests, not views <= T; latency guard
def test_equal_timestamps_order_deterministically(tmp_path):
    same = T0 + 5000
    zput(str(tmp_path), rel_of('book_ticker', 0), NL.join(f'{u},100.0,1,100.1,1,{same},{same}' for u in (3, 4, 9)) + NL)
    zput(str(tmp_path), rel_of('agg_trades', 0), NL.join(f'{a},100.0,1,{a},{a},{same},true' for a in (1, 2)) + NL)
    zput(str(tmp_path), rel_of('book_depth_summary', 0), NL.join(bd('2024-01-01 00:00:06')) + NL)
    m = build(str(tmp_path))
    o = obs(m, str(tmp_path), tmp_path / 'w')
    v = o.view(same)
    assert [r.update_id for r in v.book_ticker(SYM)] == [3, 4, 9]
    ev = [(s, E._seq(s, r)) for s, r in v.events(SYM)]
    assert ev == [('agg_trades', 1), ('agg_trades', 2), ('book_depth_summary', None), ('book_ticker', 3),
                  ('book_ticker', 4), ('book_ticker', 9)]
    assert M.canonical(v.events(SYM)) == M.canonical(obs(m, str(tmp_path), tmp_path / 'w2').view(same).events(SYM))


def test_extra_future_data_changes_manifest_and_run_digest_not_views(world, tmp_path):
    store, m = world
    t = make_store(tmp_path / 's', days=(0, 1, 2))                      # one more (future) day of every series
    m3 = build(t)
    assert m3['digest'] != m['digest'] and R.run_digest(run3(m3)) != R.run_digest(run3(m))
    for lat in ('lat-none/1', 'lat-fixed-250/1'):
        for at in (D0 + HOUR, D0 + 2 * DAY):
            assert dump(obs(m, store, tmp_path / f'a{lat[4]}{at}', lat).view(at)) == \
                dump(obs(m3, t, tmp_path / f'b{lat[4]}{at}', lat).view(at))


@pytest.mark.parametrize('value', [-1, -250, E.MAX_FEED_MS + 1, 10 ** 30, True, 0.5, '250'])
def test_negative_overflow_or_non_int_latency_refuses(world, tmp_path, monkeypatch, value):
    store, m = world
    monkeypatch.setitem(E.LATENCY_POLICIES, 'lat-bad/1', value)
    monkeypatch.setitem(E.LATENCY_LABELS, 'lat-bad/1', 'bad')
    with pytest.raises(E.ExecObsError, match='is not an int in'):
        E.ExecObs(m, store, 'lat-bad/1', dev_window(tmp_path))


def test_shipped_latency_policies_are_valid_and_unknown_refuses(world, tmp_path):
    store, m = world
    assert {k: E.feed_ms_of(k) for k in E.LATENCY_POLICIES} == {'lat-none/1': 0, 'lat-fixed-250/1': 250}
    assert E.LATENCY_LABELS == {'lat-none/1': 'none', 'lat-fixed-250/1': 'assumed/unfitted'}
    with pytest.raises(E.ExecObsError, match='latency policy must be one of'):
        E.ExecObs(m, store, 'lat-none/2', dev_window(tmp_path))


# item 6: golden test of EVERY registered /2 envelope / digest in the repository
def _tracked(*roots):
    out = subprocess.run(['git', '-C', ROOT, 'ls-files', '-z', '--', *roots], capture_output=True, check=True,
                         stdin=subprocess.DEVNULL).stdout.decode('utf-8').split('\0')
    return [p for p in out if p]


def _docs(rel):
    import gzip
    with open(os.path.join(ROOT, rel), 'rb') as f:
        raw = f.read()
    if rel.endswith('.gz'):
        raw = gzip.decompress(raw)
    text = raw.decode('utf-8')
    if rel.endswith(('.jsonl', '.jsonl.gz')):
        return [json.loads(ln) for ln in text.splitlines() if ln.strip()]
    return [json.loads(text)]


def _walk(o):
    yield o
    if isinstance(o, dict):
        for v in o.values():
            yield from _walk(v)
    elif isinstance(o, list):
        for v in o:
            yield from _walk(v)


# Registered /2 envelopes found in the repository at 0944547: none are committed (no run has opened a sealed split
# yet; every ledger `run_digest` is null). A newly committed envelope fails this pin until its digest is added here.
GOLDEN_V2 = {}


def test_every_registered_v2_envelope_and_run_digest_is_golden():
    envs, digests = {}, set()
    for rel in _tracked('research_evidence'):
        if not rel.endswith(('.json', '.jsonl', '.json.gz', '.jsonl.gz')):
            continue
        for doc in _docs(rel):
            for o in _walk(doc):
                if isinstance(o, dict) and o.get('format') == R.FORMAT and 'run' in o:
                    assert R.run_format(o['run']) == R.FORMAT and R.run_digest(o['run']) == o['run_digest']
                    envs[rel] = o['run_digest']
                if isinstance(o, dict) and isinstance(o.get('run_digest'), str):
                    digests.add(o['run_digest'])
    assert envs == GOLDEN_V2
    for d in digests:                                                   # every cited digest has its envelope
        env = R.load_envelope(os.path.join(ROOT, 'research_evidence', 'runs'), d)
        assert R.run_digest(env['run']) == d
    diff = subprocess.run(['git', '-C', ROOT, 'diff', '--quiet', '0944547', '--', 'research_evidence'],
                          stdin=subprocess.DEVNULL)
    assert diff.returncode == 0                                         # no registered evidence byte changed


def test_test_fixture_v2_envelopes_keep_their_shape(tmp_path):
    """The /2 fixtures the existing suites freeze (test_res03_core) go through the same unchanged validate path."""
    r = run2()
    for k in ('exec_obs',):
        assert k not in R.RUN_KEYS
    bad = dict(r, exec_obs=None)                                         # a /2 block with a stray key is not /2
    assert R.run_format(bad) == R.FORMAT_3
    with pytest.raises(R.ReportError):
        R.run_digest(bad)
