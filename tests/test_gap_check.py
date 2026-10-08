"""tools/research/gap_check.py: one synthetic fixture per gap class, exit codes, determinism, read-only, Cairo time, and
the known 2026-08-02 16:00 UTC hole in the committed data1h/ files."""
import hashlib
import json
import os
import sys

import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, 'tools', 'research'))
import gap_check as GC                                                                      # noqa: E402

H = 3_600_000
T0 = 1_759_536_000_000                       # 2025-10-04 00:00:00 UTC (on every grid up to 1d)


def ts(ms):
    return GC.utc_str(ms)[:-4]


def bar(t, o='100', h='101', lo='99', c='100.5', v='10'):
    return f'{ts(t)},{o},{h},{lo},{c},{v}'


def write(root, name, rows, header='t,o,h,l,c,v'):
    os.makedirs(root, exist_ok=True)
    p = os.path.join(root, name)
    with open(p, 'w', encoding='utf-8', newline='\n') as f:
        f.write('\n'.join([header, *rows]) + '\n')
    return p


def clean(n=6, step=H, t0=T0):
    return [bar(t0 + i * step) for i in range(n)]


def classes(rep):
    return sorted(r['class'] for r in rep['findings'])


def run(tmp_path, **kw):
    return GC.run([str(tmp_path / 'r')], **kw)


def test_clean_file_has_no_findings_and_exits_zero(tmp_path):
    write(tmp_path / 'r', 'BTCUSDT_1h.csv', clean())
    rep = run(tmp_path)
    assert rep['findings'] == [] and rep['summary']['files'] == 1 and rep['summary']['rows'] == 6
    assert GC.main(['--root', str(tmp_path / 'r'), '--no-manifest']) == 0


def test_g1_missing_bars_listed_exactly_in_utc_and_cairo(tmp_path):
    rows = clean(10)
    del rows[3:5]                                                    # T0+3h and T0+4h missing
    write(tmp_path / 'r', 'BTCUSDT_1h.csv', rows)
    rep = run(tmp_path)
    (g,) = rep['findings']
    assert g['class'] == 'G1' and g['severity'] == 'error' and g['count'] == 2
    assert [m['ts_ms'] for m in g['missing']] == [T0 + 3 * H, T0 + 4 * H]
    assert g['missing'][0]['utc'] == '2025-10-04 03:00:00 UTC'
    assert g['missing'][0]['cairo'] == '2025-10-04 06:00:00 +0300'          # Egypt summer time (DST) on that date
    assert GC.main(['--root', str(tmp_path / 'r'), '--no-manifest']) == 1


def test_cairo_offset_follows_dst():
    winter = 1_767_225_600_000                                              # 2026-01-01 00:00 UTC
    assert GC.cairo_str(winter) == '2026-01-01 02:00:00 +0200'
    assert GC.cairo_str(T0) == '2025-10-04 03:00:00 +0300'


def test_g2_duplicates_identical_and_conflicting(tmp_path):
    rows = clean(4)
    rows.insert(2, rows[1])                                                  # identical duplicate of T0+1h
    rows.insert(4, bar(T0 + 2 * H, c='100.9'))                               # conflicting duplicate of T0+2h
    write(tmp_path / 'r', 'ETHUSDT_1h.csv', rows)
    rep = run(tmp_path)
    g2 = [r for r in rep['findings'] if r['class'] == 'G2']
    assert len(g2) == 2
    assert any('identical' in r['detail'] for r in g2) and any('CONFLICTING' in r['detail'] for r in g2)
    assert classes(rep) == ['G2', 'G2']                                       # a duplicate is not a gap / order error


@pytest.mark.parametrize('kw,why', [
    (dict(h='100.4'), 'high < max(open, close)'),
    (dict(lo='100.2'), 'low > min(open, close)'),
    (dict(h='99', lo='99.5', o='99.2', c='99.2'), 'high < low'),
    (dict(v='-1'), 'volume < 0'),
    (dict(o='0', lo='0'), 'price <= 0'),
    (dict(c='abc'), 'missing / non-numeric'),
    (dict(c=''), 'missing / non-numeric'),
])
def test_g3_invariants(tmp_path, kw, why):
    rows = clean(4)
    rows[2] = bar(T0 + 2 * H, **kw)
    write(tmp_path / 'r', 'SOLUSDT_1h.csv', rows)
    rep = run(tmp_path)
    (g,) = rep['findings']
    assert g['class'] == 'G3' and why in g['detail'] and g['from']['ts_ms'] == T0 + 2 * H


def test_g3_unparseable_time_and_unknown_header(tmp_path):
    rows = clean(3) + ['not-a-time,1,1,1,1,1']
    write(tmp_path / 'r', 'SOLUSDT_1h.csv', rows)
    write(tmp_path / 'r', 'XRPUSDT_1h.csv', ['1,2,3'], header='a,b,c')
    rep = run(tmp_path)
    assert classes(rep) == ['G3', 'G3']
    assert any('unparseable open time' in r['detail'] for r in rep['findings'])
    assert any('unknown header' in r['detail'] for r in rep['findings'])


def test_g4_zero_volume_and_flat_bars_are_warnings(tmp_path):
    rows = clean(5)
    rows[1] = bar(T0 + H, v='0')
    rows[3] = bar(T0 + 3 * H, o='100', h='100', lo='100', c='100')
    write(tmp_path / 'r', 'DOGEUSDT_1h.csv', rows)
    rep = run(tmp_path)
    assert classes(rep) == ['G4', 'G4'] and all(r['severity'] == 'warning' for r in rep['findings'])
    assert GC.main(['--root', str(tmp_path / 'r'), '--no-manifest']) == 0                 # warnings pass ...
    assert GC.main(['--root', str(tmp_path / 'r'), '--no-manifest', '--strict']) == 1     # ... unless --strict


def test_k1_misaligned_open(tmp_path):
    rows = clean(3) + [bar(T0 + 3 * H + 60_000)]                             # 03:01 on a 1h file
    write(tmp_path / 'r', 'BNBUSDT_1h.csv', rows)
    rep = run(tmp_path)
    assert classes(rep) == ['K1'] and 'offset 60000 ms' in rep['findings'][0]['detail']


def test_k2_non_monotonic_time(tmp_path):
    rows = clean(5)
    rows[2], rows[3] = rows[3], rows[2]
    write(tmp_path / 'r', 'LINKUSDT_1h.csv', rows)
    rep = run(tmp_path)
    assert classes(rep) == ['K2']                    # the sorted keys are complete: not a G1 as well


def test_s1_stale_tail_against_the_newest_file_of_the_interval(tmp_path):
    write(tmp_path / 'r' / 'a', 'BTCUSDT_1h.csv', clean(6))
    write(tmp_path / 'r' / 'b', 'ETHUSDT_1h.csv', clean(4))
    rep = run(tmp_path)
    (s,) = rep['findings']
    assert s['class'] == 'S1' and s['count'] == 2 and s['file'].endswith('ETHUSDT_1h.csv')


def test_x1_same_series_in_two_roots_must_agree(tmp_path):
    a = clean(4)
    b = list(a)
    b[3] = bar(T0 + 3 * H, c='100.7')
    write(tmp_path / 'r' / 'a', 'BTCUSDT_1h.csv', a)
    write(tmp_path / 'r' / 'b', 'BTCUSDT_1h.csv', b)
    rep = run(tmp_path)
    (x,) = rep['findings']
    assert x['class'] == 'X1' and x['count'] == 1 and x['shared'] == 4
    assert x['examples'][0]['a'][3] == '100.5' and x['examples'][0]['b'][3] == '100.7'


def test_x1_numeric_equality_ignores_formatting(tmp_path):
    a = clean(2)
    b = [bar(T0, o='100.0', v='10.000'), a[1]]
    write(tmp_path / 'r' / 'a', 'BTCUSDT_1h.csv', a)
    write(tmp_path / 'r' / 'b', 'BTCUSDT_1h.csv', b)
    assert run(tmp_path)['findings'] == []


def test_x2_partial_coarse_bar_against_its_finer_bars(tmp_path):
    fine = [bar(T0 + i * H, o=str(100 + i), h=str(102 + i), lo=str(99 + i), c=str(101 + i), v='5') for i in range(8)]
    good = bar(T0 + 4 * 4 * 0, o='100', h='105', lo='99', c='104', v='20')          # aggregate of hours 0-3
    partial = bar(T0 + 4 * H, o='104', h='106', lo='103', c='105', v='5')           # only hour 4 seen: open candle
    write(tmp_path / 'r', 'BTCUSDT_1h.csv', fine)
    write(tmp_path / 'r', 'BTCUSDT_4h.csv', [good, partial])
    rep = run(tmp_path)
    (x,) = rep['findings']
    assert x['class'] == 'X2' and x['count'] == 1 and x['checked'] == 2
    assert x['from']['ts_ms'] == T0 + 4 * H and x['examples'][0]['aggregate'] == ['104', '109', '103', '108', '20']


def test_x2_skips_coarse_bars_the_finer_file_does_not_cover(tmp_path):
    write(tmp_path / 'r', 'BTCUSDT_1h.csv', clean(3))                                 # hours 0-2 only
    write(tmp_path / 'r', 'BTCUSDT_4h.csv', [bar(T0, v='999')])
    assert run(tmp_path)['findings'] == []


def test_p1_manifest_disagreements(tmp_path):
    r = tmp_path / 'r'
    p = write(r, 'BTCUSDT_1h.csv', clean(3))
    write(r, 'ETHUSDT_1h.csv', clean(3))
    with open(p, 'rb') as f:
        sha = hashlib.sha256(f.read()).hexdigest()
    man = {'files': {'r/BTCUSDT_1h.csv': {'sha256': sha, 'rows': 4, 'first': ts(T0), 'last': ts(T0 + 2 * H)},
                     'r/GONEUSDT_1h.csv': {'sha256': '0' * 64}}}
    mp = tmp_path / 'MANIFEST.json'
    mp.write_text(json.dumps(man), encoding='utf-8')
    rep = GC.run([str(r)], str(mp))
    p1 = sorted(r_['detail'] for r_ in rep['findings'] if r_['class'] == 'P1')
    assert p1 == ['listed in the manifest, file not found', 'not listed in the manifest', 'rows 4 != 3']


def test_long_format_header_with_ms_open_times(tmp_path):
    rows = [f'{T0 + i * H},1,2,0.5,1.5,3' for i in (0, 1, 3)]
    write(tmp_path / 'r', 'BTCUSDT_1h.csv', rows, header='open_time,open,high,low,close,volume')
    rep = run(tmp_path)
    assert classes(rep) == ['G1'] and rep['findings'][0]['count'] == 1


def test_deterministic_and_read_only(tmp_path):
    rows = clean(10)
    del rows[5]
    p = write(tmp_path / 'r', 'BTCUSDT_1h.csv', rows)
    before = (os.stat(p).st_mtime_ns, open(p, 'rb').read())
    j1, j2 = tmp_path / 'a.json', tmp_path / 'b.json'
    m1, m2 = tmp_path / 'a.md', tmp_path / 'b.md'
    assert GC.main(['--root', str(tmp_path / 'r'), '--no-manifest', '--json', str(j1), '--md', str(m1)]) == 1
    assert GC.main(['--root', str(tmp_path / 'r'), '--no-manifest', '--json', str(j2), '--md', str(m2)]) == 1
    assert j1.read_bytes() == j2.read_bytes() and m1.read_bytes() == m2.read_bytes()
    assert (os.stat(p).st_mtime_ns, open(p, 'rb').read()) == before
    doc = json.loads(j1.read_text(encoding='utf-8'))
    assert doc['format'] == 'zb-data-gaps/1' and doc['summary']['missing_bars'] == 1
    assert '**G1** (error)' in m1.read_text(encoding='utf-8')


def test_bad_manifest_path_is_a_usage_error(tmp_path):
    write(tmp_path / 'r', 'BTCUSDT_1h.csv', clean())
    assert GC.main(['--root', str(tmp_path / 'r'), '--manifest', str(tmp_path / 'nope.json')]) == 2


@pytest.mark.skipif(not os.path.isfile(os.path.join(ROOT, 'data1h', 'BTCUSDT_1h.csv')), reason='no committed data1h/')
def test_flags_the_known_2026_08_02_16_utc_hole_in_every_data1h_file():
    rep = GC.run([os.path.join(ROOT, 'data1h')])
    g1 = [r for r in rep['findings'] if r['class'] == 'G1']
    assert len(g1) == len(rep['files']) == 8
    for r in g1:
        assert r['count'] == 1 and r['missing'][0]['utc'] == '2026-08-02 16:00:00 UTC'
        assert r['missing'][0]['cairo'] == '2026-08-02 19:00:00 +0300'
