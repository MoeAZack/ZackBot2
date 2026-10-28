"""OI-DATA-01: list-only metrics coverage probe, on synthetic listings (no network)."""
import datetime as dt
import os
import sys
import urllib.parse

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), 'tools', 'research'))
import oi_metrics_probe as O                                                                 # noqa: E402

D = dt.date


def key(sym, d, checksum=False):
    return f'{O.DAILY_PREFIX}{sym}/{sym}-metrics-{d.isoformat()}.zip' + ('.CHECKSUM' if checksum else '')


def days(a, b):
    return [a + dt.timedelta(days=i) for i in range((b - a).days + 1)]


def page(keys, prefixes=(), truncated=False, next_marker=None):
    c = ''.join(f'<Contents><Key>{k}</Key><Size>1</Size></Contents>' for k in keys)
    p = ''.join(f'<CommonPrefixes><Prefix>{x}</Prefix></CommonPrefixes>' for x in prefixes)
    nm = f'<NextMarker>{next_marker}</NextMarker>' if next_marker else ''
    return (f'<?xml version="1.0" encoding="UTF-8"?><ListBucketResult xmlns="http://s3.amazonaws.com/doc/2006-03-01/">'
            f'{nm}<IsTruncated>{"true" if truncated else "false"}</IsTruncated>{c}{p}</ListBucketResult>').encode()


def test_parse_key_dates_only_zip_of_that_symbol():
    assert O.parse_key(key('BTCUSDT', D(2022, 1, 13)), 'BTCUSDT') == D(2022, 1, 13)
    assert O.parse_key(key('BTCUSDT', D(2022, 1, 13), checksum=True), 'BTCUSDT') is None
    assert O.parse_key(key('BTCUSDT', D(2022, 1, 13)), 'BTC') is None
    assert O.parse_key(f'{O.DAILY_PREFIX}BTCUSDT/BTCUSDT-metrics-2022-02-30.zip', 'BTCUSDT') is None
    assert O.parse_key(f'{O.DAILY_PREFIX}BTCUSDT/BTCUSDT-metrics-2022-1-3.zip', 'BTCUSDT') is None


def test_cutoff_refusal_and_clip():
    with pytest.raises(ValueError):
        O.check_end(D(2026, 9, 28))
    with pytest.raises(ValueError):
        O.check_end(D(2026, 10, 1))
    assert O.check_end(D(2026, 9, 27)) == D(2026, 9, 27)
    with pytest.raises(ValueError):
        O.probe(['X'], fetch=lambda u: pytest.fail('no fetch'), end=D(2026, 9, 28))
    ks = [key('X', d) for d in days(D(2026, 9, 20), D(2026, 10, 5))]
    r = O.summarize('X', ks)
    assert r['last'] == '2026-09-27' and r['files'] == 8 and r['files_on_or_after_cutoff_count_only'] == 8
    assert all(x[1] < '2026-09-28' for x in r['gaps'])


def test_month_runs():
    ds = days(D(2023, 1, 1), D(2026, 9, 27))
    best, trailing = O.month_runs(ds)
    assert best == {'months': 44, 'start': '2023-01', 'end': '2026-08'} and trailing == best   # 2026-09 ends after cutoff
    ds2 = [d for d in ds if d != D(2024, 2, 29)]                                                # leap day hole
    best, trailing = O.month_runs(ds2)
    assert best == {'months': 30, 'start': '2024-03', 'end': '2026-08'} and trailing == best
    best, trailing = O.month_runs(days(D(2020, 1, 15), D(2024, 6, 30)))                         # partial first month
    assert best == {'months': 53, 'start': '2020-02', 'end': '2024-06'} and trailing['months'] == 0
    assert O.month_runs([]) == ({'months': 0, 'start': None, 'end': None},) * 2


def test_gap_counting():
    ds = [d for d in days(D(2022, 1, 1), D(2022, 3, 31)) if not (D(2022, 2, 10) <= d <= D(2022, 2, 12))
          and d != D(2022, 3, 1)]
    assert O.gaps(ds) == [['2022-02-10', '2022-02-12', 3], ['2022-03-01', '2022-03-01', 1]]
    r = O.summarize('S', [key('S', d) for d in ds] + [key('S', d, True) for d in ds])
    assert (r['first'], r['last'], r['files'], r['missing_days']) == ('2022-01-01', '2022-03-31', len(ds), 4)
    assert r['longest_complete_month_run']['months'] == 1                                      # only January complete
    assert O.summarize('S', [])['files'] == 0


def test_pagination_and_probe_gate():
    full = [key('A', d) for d in days(D(2021, 1, 1), D(2026, 10, 2))]
    calls = []

    def fetch(url):
        q = dict(urllib.parse.parse_qsl(urllib.parse.urlsplit(url).query))
        calls.append(q)
        assert q['delimiter'] == '/'
        if q['prefix'] == f'{O.DAILY_PREFIX}A/':
            i = full.index(q['marker']) + 1 if 'marker' in q else 0
            chunk = full[i:i + 1000]
            return page(chunk, truncated=i + 1000 < len(full))                                 # no NextMarker -> last key
        if q['prefix'] == f'{O.DAILY_PREFIX}B/':
            return page([])
        if q['prefix'] == 'data/futures/um/monthly/':
            return page([], prefixes=['data/futures/um/monthly/klines/'])
        return page([])
    res = O.probe(['A', 'B'], fetch=fetch)
    a, b = res['symbols']
    assert a['files'] == len(days(D(2021, 1, 1), D(2026, 9, 27))) and a['last'] == '2026-09-27'
    assert a['longest_complete_month_run']['months'] == 68 and b['files'] == 0
    assert res['no_metrics'] == ['B'] and res['monthly_metrics_prefix_exists'] is False
    assert res['gate']['symbols_passing'] == 1 and res['gate']['result'] == 'FAIL'
    assert sum(1 for q in calls if q['prefix'] == f'{O.DAILY_PREFIX}A/') == 3
    assert O.gate([a] * 15)['result'] == 'PASS'


def test_list_only_no_download_code():
    src = open(O.__file__, encoding='utf-8').read()
    assert 'zipfile' not in src and "open(a.out, 'w'" in src
    assert 'data.binance.vision/data/' not in src                                              # never a file URL
