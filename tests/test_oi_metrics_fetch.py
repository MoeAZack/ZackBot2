"""OI-DATA-01 step 2: verified metrics fetcher on a fully faked network (no sockets, no real data)."""
import datetime as dt
import hashlib
import json
import os
import socket
import sys
import urllib.parse

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), 'tools', 'research'))
import oi_metrics_fetch as F                                                                 # noqa: E402
import oi_metrics_probe as O                                                                 # noqa: E402

D = dt.date


@pytest.fixture(autouse=True)
def no_sockets(monkeypatch):
    def boom(*a, **k):
        raise AssertionError('real network touched')
    monkeypatch.setattr(socket, 'socket', boom)
    monkeypatch.setattr(socket, 'create_connection', boom)
    monkeypatch.setattr(F, 'http_open', boom)
    monkeypatch.setattr(O, 'http_get', boom)


def days(a, b):
    return [a + dt.timedelta(days=i) for i in range((b - a).days + 1)]


def body(sym, d):
    return f'zip-bytes {sym} {d}'.encode() * 50


class Net:
    """Fake archive: listing pages + zip/.CHECKSUM bodies; records every request."""
    def __init__(self, files, bad=(), size=True):
        self.files = files                           # {sym: [dates]}
        self.bad, self.size = set(bad), size
        self.list_calls, self.body_calls = [], []

    def list_fetch(self, url):
        self.list_calls.append(url)
        q = urllib.parse.parse_qs(urllib.parse.urlsplit(url).query)
        prefix = q['prefix'][0]
        sym = prefix.rstrip('/').rsplit('/', 1)[1]
        c = ''
        for d in self.files.get(sym, []):
            k = F.key_for(sym, d)
            sz = f'<Size>{len(body(sym, d))}</Size>' if self.size else ''
            c += f'<Contents><Key>{k}</Key>{sz}</Contents><Contents><Key>{k}.CHECKSUM</Key><Size>99</Size></Contents>'
        return (f'<?xml version="1.0"?><ListBucketResult xmlns="http://s3.amazonaws.com/doc/2006-03-01/">'
                f'<IsTruncated>false</IsTruncated>{c}</ListBucketResult>').encode()

    def opener(self, url):
        self.body_calls.append(url)
        key = url[len(F.BASE):]
        is_ck = key.endswith('.CHECKSUM')
        zkey = key[:-9] if is_ck else key
        name = zkey.rsplit('/', 1)[1]
        sym, rest = name.split('-metrics-')
        d = rest[:-4]
        raw = body(sym, d)
        if is_ck:
            h = hashlib.sha256(b'other' if (sym, d) in self.bad else raw).hexdigest()
            return Resp(f'{h}  {name}\n'.encode(), url)
        return Resp(raw, url)


class Resp:
    def __init__(self, data, url, status=200):
        self.data, self.url, self.status, self.pos = data, url, status, 0

    def geturl(self):
        return self.url

    def read(self, n=-1):
        n = len(self.data) if n < 0 else n
        out = self.data[self.pos:self.pos + n]
        self.pos += len(out)
        return out

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False


def fetcher(net, **k):
    return F.Fetcher(opener=net.opener, min_gap=0, sleep=lambda s: None, **k)


def go(net, tmp_path, syms, end=D(2026, 9, 27), start=D(2000, 1, 1), workers=2, **k):
    return F.run(syms, str(tmp_path), start, end, list_fetch=net.list_fetch, fetcher=fetcher(net, **k),
                 workers=workers)


# ------------------------------------------------------------------ cutoff
@pytest.mark.parametrize('end', [D(2026, 9, 28), D(2026, 9, 29), D(2027, 1, 1)])
def test_cutoff_refused_before_any_call(tmp_path, end):
    net = Net({'BTCUSDT': [D(2026, 9, 1)]})
    with pytest.raises(ValueError, match='cutoff'):
        go(net, tmp_path, ['BTCUSDT'], end=end)
    with pytest.raises(ValueError, match='cutoff'):
        F.plan(['BTCUSDT'], None, end, net.list_fetch)
    with pytest.raises((ValueError, SystemExit)):
        F.main(['--symbols', 'BTCUSDT', '--end', end.isoformat(), '--dest', str(tmp_path), '--dry-run'])
    assert net.list_calls == [] and net.body_calls == [] and not os.listdir(tmp_path)


def test_post_cutoff_listed_keys_skipped(tmp_path):
    net = Net({'BTCUSDT': days(D(2026, 9, 25), D(2026, 10, 3))})
    doc = go(net, tmp_path, ['BTCUSDT'])
    assert [f['date'] for f in doc['files']] == ['2026-09-25', '2026-09-26', '2026-09-27']
    assert not any('2026-09-28' <= u.rsplit('-metrics-', 1)[1][:10] for u in net.body_calls)
    assert F.plan(['BTCUSDT'], None, D(2026, 9, 27), net.list_fetch)['per_symbol']['BTCUSDT'][
        'listed_on_or_after_cutoff_skipped'] == 6
    with pytest.raises(F.FetchError, match='cutoff'):
        F.fetch_one({'symbol': 'BTCUSDT', 'date': '2026-09-28', 'key': F.key_for('BTCUSDT', D(2026, 9, 28))},
                    str(tmp_path), fetcher(net))


def test_end_and_start_window(tmp_path):
    net = Net({'BTCUSDT': days(D(2026, 1, 1), D(2026, 1, 10))})
    doc = go(net, tmp_path, ['BTCUSDT'], start=D(2026, 1, 3), end=D(2026, 1, 5))
    assert [f['date'] for f in doc['files']] == ['2026-01-03', '2026-01-04', '2026-01-05']


# ------------------------------------------------------------------ checksum / atomic / resume
def test_checksum_mismatch_fails_closed(tmp_path):
    bad = ('BTCUSDT', '2026-01-02')
    net = Net({'BTCUSDT': days(D(2026, 1, 1), D(2026, 1, 3))}, bad=[bad])
    doc = go(net, tmp_path, ['BTCUSDT'])
    assert F.exit_code(doc) == 1 and doc['complete'] is False
    assert [(f['date'], f['reason']) for f in doc['failures']] == [('2026-01-02', 'checksum_mismatch')]
    p = F.local_path(str(tmp_path), F.key_for('BTCUSDT', D(2026, 1, 2)))
    assert not os.path.exists(p) and not os.path.exists(p + '.part') and not os.path.exists(p + '.CHECKSUM')
    assert len(doc['files']) == 2
    on_disk = json.load(open(os.path.join(tmp_path, F.MANIFEST_NAME), encoding='utf-8'))
    assert on_disk['failures'] == doc['failures'] and on_disk['digest'] == doc['digest']


def test_checksum_fetched_before_zip_and_written_atomically(tmp_path, monkeypatch):
    net = Net({'BTCUSDT': [D(2026, 1, 1)]})
    replaced = []
    real = os.replace
    monkeypatch.setattr(F.os, 'replace', lambda a, b: (replaced.append((a, b)), real(a, b)))
    doc = go(net, tmp_path, ['BTCUSDT'])
    assert net.body_calls[0].endswith('.CHECKSUM') and net.body_calls[1].endswith('.zip')
    p = F.local_path(str(tmp_path), F.key_for('BTCUSDT', D(2026, 1, 1)))
    assert (p + '.part', p) in replaced and (p + '.CHECKSUM.part', p + '.CHECKSUM') in replaced
    assert open(p, 'rb').read() == body('BTCUSDT', '2026-01-01')
    assert doc['files'][0]['sha256'] == hashlib.sha256(body('BTCUSDT', '2026-01-01')).hexdigest()
    assert not [f for _, _, fs in os.walk(tmp_path) for f in fs if f.endswith('.part')]


def test_interrupted_stream_leaves_no_file(tmp_path):
    net = Net({'BTCUSDT': [D(2026, 1, 1)]})

    class Broken(Resp):
        def read(self, n=-1):
            if self.pos:
                raise ConnectionError('reset')
            return super().read(10)

    def opener(url):
        r = net.opener(url)
        return r if url.endswith('.CHECKSUM') else Broken(r.data, url)
    f = F.Fetcher(opener=opener, min_gap=0, sleep=lambda s: None, retries=2)
    doc = F.run(['BTCUSDT'], str(tmp_path), D(2000, 1, 1), D(2026, 9, 27), list_fetch=net.list_fetch, fetcher=f)
    assert doc['failures'][0]['reason'] == 'retries_exhausted' and F.exit_code(doc) == 1
    p = F.local_path(str(tmp_path), F.key_for('BTCUSDT', D(2026, 1, 1)))
    assert not os.path.exists(p) and not os.path.exists(p + '.part')


def test_resume_skips_verified_and_refetches_corrupt(tmp_path):
    net = Net({'BTCUSDT': days(D(2026, 1, 1), D(2026, 1, 3))})
    first = go(net, tmp_path, ['BTCUSDT'])
    assert len(net.body_calls) == 6
    p = F.local_path(str(tmp_path), F.key_for('BTCUSDT', D(2026, 1, 2)))
    open(p, 'wb').write(b'corrupt')
    net.body_calls.clear()
    again = go(net, tmp_path, ['BTCUSDT'])
    assert [u.rsplit('/', 1)[1] for u in net.body_calls] == ['BTCUSDT-metrics-2026-01-02.zip.CHECKSUM',
                                                             'BTCUSDT-metrics-2026-01-02.zip']
    assert again['digest'] == first['digest'] and F.exit_code(again) == 0


def test_stop_status_halts_run(tmp_path):
    net = Net({'BTCUSDT': days(D(2026, 1, 1), D(2026, 1, 5))})
    f = F.Fetcher(opener=lambda u: Resp(b'', u, status=429), min_gap=0, sleep=lambda s: None)
    doc = F.run(['BTCUSDT'], str(tmp_path), D(2000, 1, 1), D(2026, 9, 27), list_fetch=net.list_fetch, fetcher=f, workers=1)
    assert doc['stopped']['reason'] == 'rate_limited' and F.exit_code(doc) == 2 and not doc['files']


def test_retries_then_success_with_backoff(tmp_path):
    net = Net({'BTCUSDT': [D(2026, 1, 1)]})
    n, sleeps = {'k': 0}, []

    def flaky(url):
        n['k'] += 1
        return Resp(b'', url, status=503) if n['k'] <= 2 else net.opener(url)
    f = F.Fetcher(opener=flaky, min_gap=0, sleep=sleeps.append, retries=3, backoff=1.0)
    doc = F.run(['BTCUSDT'], str(tmp_path), D(2000, 1, 1), D(2026, 9, 27), list_fetch=net.list_fetch, fetcher=f)
    assert F.exit_code(doc) == 0 and sleeps == [1.0, 2.0]


# ------------------------------------------------------------------ manifest
def test_manifest_digest_deterministic(tmp_path):
    files = {'BTCUSDT': days(D(2026, 1, 1), D(2026, 1, 6)), 'ETHUSDT': days(D(2026, 1, 2), D(2026, 1, 4))}
    a = go(Net(files), tmp_path / 'a', ['BTCUSDT', 'ETHUSDT'], workers=4)
    b = go(Net(files), tmp_path / 'b', ['ETHUSDT', 'BTCUSDT'], workers=1)
    assert a['digest'] == b['digest'] and len(a['files']) == 9
    doc = dict(a)
    doc.pop('digest')
    assert hashlib.sha256(F.M.canonical(doc)).hexdigest() == a['digest']
    assert [f['path'] for f in a['files']] == sorted(f['path'] for f in a['files'])
    assert set(a['files'][0]) == {'path', 'symbol', 'date', 'bytes', 'sha256', 'source'}


# ------------------------------------------------------------------ dry-run
def test_dry_run_makes_no_body_gets(tmp_path, monkeypatch, capsys):
    net = Net({'BTCUSDT': days(D(2026, 1, 1), D(2026, 1, 4)), 'ETHUSDT': [D(2026, 2, 1)]})
    monkeypatch.setattr(O, 'http_get', net.list_fetch)
    dest = tmp_path / 'store'
    assert F.main(['--dry-run', '--symbols', 'BTCUSDT,ETHUSDT', '--dest', str(dest)]) == 0
    out = json.loads(capsys.readouterr().out)
    assert out['files'] == 5 and out['per_symbol']['BTCUSDT']['files'] == 4
    assert out['listed_bytes'] == sum(len(body('BTCUSDT', d.isoformat())) for d in days(D(2026, 1, 1), D(2026, 1, 4))) \
        + len(body('ETHUSDT', '2026-02-01'))
    assert net.body_calls == [] and len(net.list_calls) == 2 and not dest.exists()


def test_dry_run_unknown_sizes():
    net = Net({'BTCUSDT': [D(2026, 1, 1)]}, size=False)
    assert F.plan(['BTCUSDT'], None, D(2026, 9, 27), net.list_fetch)['listed_bytes'] is None


# ------------------------------------------------------------------ Codex ruling 6458228546: holes + groups
def test_missing_days_recorded_never_filled(tmp_path):
    ds = [d for d in days(D(2026, 1, 1), D(2026, 1, 10)) if d not in (D(2026, 1, 4), D(2026, 1, 7))]
    net = Net({'NEARUSDT': ds + [D(2026, 9, 29)]})
    doc = go(net, tmp_path, ['NEARUSDT'])
    cov = doc['coverage']['NEARUSDT']
    assert cov['missing_days'] == ['2026-01-04', '2026-01-07']
    assert cov['listed_first'] == '2026-01-01' and cov['listed_last'] == '2026-01-10'
    assert [f['date'] for f in doc['files']] == [d.isoformat() for d in ds]          # nothing synthesized
    assert not any(('2026-01-04' in u or '2026-01-07' in u) for u in net.body_calls)
    assert F.exit_code(doc) == 0


def test_group_split_from_listing(tmp_path, monkeypatch):
    monkeypatch.setattr(O, 'GATE_MONTHS', 3)
    full = days(D(2026, 5, 1), D(2026, 9, 27))                                      # May..Aug complete = 4 months
    holed = [d for d in full if d not in (D(2026, 6, 10), D(2026, 6, 11))]
    files = {'BTCUSDT': full, 'ZECUSDT': holed, 'NEWUSDT': days(D(2026, 8, 1), D(2026, 9, 27)), 'NONEUSDT': []}
    net = Net(files)
    pl = F.plan(list(files), None, D(2026, 9, 27), net.list_fetch)
    want = {'strict_complete': ['BTCUSDT'], 'conditional_with_holes': ['ZECUSDT'],
            'short_history': ['NEWUSDT', 'NONEUSDT']}
    assert pl['groups'] == want
    assert pl['per_symbol']['ZECUSDT']['missing_days'] == ['2026-06-10', '2026-06-11']
    assert pl['per_symbol']['ZECUSDT']['longest_complete_month_run']['months'] == 2
    assert pl['per_symbol']['ZECUSDT']['month_run_if_holes_excused']['months'] == 4
    doc = F.run(list(files), str(tmp_path), D(2026, 9, 1), D(2026, 9, 2), list_fetch=net.list_fetch,
                fetcher=fetcher(net))
    # ruling 6489428240: groups are relative to the required window; ZEC's June holes lie wholly outside 9/1..9/2
    assert doc['groups'] == dict(want, strict_complete=['BTCUSDT', 'ZECUSDT'], conditional_with_holes=[])
    assert doc['coverage']['ZECUSDT']['missing_days'] == ['2026-06-10', '2026-06-11']      # provenance kept
    assert doc['coverage']['ZECUSDT']['missing_required_days'] == []
    holed_big = [d for d in full if d.month != 6]                                   # a whole missing month
    assert F.coverage('X', [F.key_for('X', d) for d in holed_big])['group'] == 'conditional_with_holes'
    sparse = [d for d in full if d.day % 2]                                          # holes everywhere, short
    assert F.coverage('X', [F.key_for('X', d) for d in sparse[:20]])['group'] == 'short_history'


# ------------------------------------------------------------------ Codex ruling 6489311350: required-range coverage
def _cov(ds, start=None, end_excl=F.CUTOFF):
    return F.coverage('X', [F.key_for('X', d) for d in ds], start, end_excl)


@pytest.fixture
def gate3(monkeypatch):
    monkeypatch.setattr(O, 'GATE_MONTHS', 3)


def test_leading_gap_not_strict(gate3):
    c = _cov(days(D(2026, 5, 10), D(2026, 9, 27)), start=D(2026, 5, 1))
    assert c['leading_gap'] == {'start': '2026-05-01', 'end_exclusive': '2026-05-10', 'days': 9}
    assert c['trailing_gap'] is None and c['missing_days'] == []
    assert c['group'] == 'conditional_with_holes'
    assert not F.covers({'listed_first': c['first'], 'listed_last': c['last'], 'missing_days': []},
                        D(2026, 5, 1), F.CUTOFF)


def test_trailing_gap_not_strict(gate3):
    c = _cov(days(D(2026, 1, 1), D(2026, 8, 31)))                     # required end = the cutoff
    assert c['required_start'] == '2026-01-01' and c['required_end_exclusive'] == '2026-09-28'
    assert c['leading_gap'] is None
    assert c['trailing_gap'] == {'start': '2026-09-01', 'end_exclusive': '2026-09-28', 'days': 27}
    assert c['missing_days'] == [] and c['group'] == 'conditional_with_holes'


def test_both_boundary_gaps(gate3):
    c = _cov(days(D(2026, 2, 1), D(2026, 8, 31)), start=D(2026, 1, 15))
    assert c['leading_gap']['days'] == 17 and c['trailing_gap']['days'] == 27
    assert c['group'] != 'strict_complete'


def test_exact_full_cover_is_strict(gate3):
    c = _cov(days(D(2026, 5, 1), D(2026, 9, 27)), start=D(2026, 5, 1))
    assert c['leading_gap'] is None and c['trailing_gap'] is None and c['group'] == 'strict_complete'
    rec = {'listed_first': c['first'], 'listed_last': c['last'], 'missing_days': c['missing_days']}
    assert F.covers(rec, D(2026, 5, 1), F.CUTOFF) and F.covers(rec, D(2026, 6, 3), D(2026, 6, 4))


def test_internal_one_day_hole_conditional(gate3):
    c = _cov([d for d in days(D(2026, 5, 1), D(2026, 9, 27)) if d != D(2026, 7, 15)], start=D(2026, 5, 1))
    assert c['missing_days'] == ['2026-07-15'] and c['group'] == 'conditional_with_holes'
    assert c['leading_gap'] is None and c['trailing_gap'] is None
    rec = {'listed_first': c['first'], 'listed_last': c['last'], 'missing_days': c['missing_days']}
    assert not F.covers(rec, D(2026, 5, 1), F.CUTOFF)
    assert F.covers(rec, D(2026, 7, 16), F.CUTOFF)                   # a sub-range that avoids the hole is covered


def test_probe_pass_with_early_hole_not_strict(gate3):
    ds = [d for d in days(D(2026, 1, 1), D(2026, 9, 27)) if d != D(2026, 1, 5)]
    c = _cov(ds)
    assert c['longest_complete_month_run']['months'] >= O.GATE_MONTHS          # the screening probe passes
    assert c['group'] == 'conditional_with_holes'


def test_covers_refuses_empty_missing_with_uncovered_boundaries():
    rec = {'listed_first': '2026-03-01', 'listed_last': '2026-08-31', 'missing_days': []}
    assert not F.covers(rec, D(2026, 2, 1), D(2026, 4, 1))                    # leading boundary uncovered
    assert not F.covers(rec, D(2026, 8, 1), F.CUTOFF)                         # trailing boundary uncovered
    assert F.covers(rec, D(2026, 3, 1), D(2026, 9, 1))
    assert not F.covers({'missing_days': []}, D(2026, 3, 1), D(2026, 4, 1))   # incomplete record fails closed
    assert not F.covers({'listed_first': None, 'listed_last': None, 'missing_days': []}, D(2026, 3, 1), D(2026, 4, 1))
    with pytest.raises(ValueError):
        F.covers(rec, D(2026, 9, 1), D(2026, 9, 29))                          # reaching past the cutoff is refused


def test_manifest_records_boundary_gaps(tmp_path):
    net = Net({'NEARUSDT': days(D(2026, 1, 1), D(2026, 1, 10))})
    doc = go(net, tmp_path, ['NEARUSDT'], start=D(2025, 12, 30))
    cov = doc['coverage']['NEARUSDT']
    assert cov['required_start'] == '2025-12-30' and cov['required_end_exclusive'] == '2026-09-28'
    assert cov['leading_gap']['days'] == 2 and cov['trailing_gap']['start'] == '2026-01-11'
    assert cov['group'] != 'strict_complete'


# ------------------------------------------------------------------ Codex ruling 6489428240: bounds, window, cutoff
def test_authoritative_run_needs_explicit_bounds(tmp_path):
    net = Net({'BTCUSDT': [D(2026, 9, 1)]})
    for bounds in ({'start': None}, {'end': None}, {'start': None, 'end': None}):   # each one-sided omission
        with pytest.raises(F.FetchError, match='explicit'):
            go(net, tmp_path, ['BTCUSDT'], **bounds)
    for argv in (['--end', '2026-09-27'], ['--start', '2026-01-01'], []):
        with pytest.raises(SystemExit, match='explicit'):
            F.main(['--symbols', 'BTCUSDT', '--dest', str(tmp_path)] + argv)
    assert net.list_calls == [] and net.body_calls == [] and not os.listdir(tmp_path)


def test_manifest_labelled_authoritative(tmp_path):
    doc = go(Net({'BTCUSDT': [D(2026, 9, 1)]}), tmp_path, ['BTCUSDT'], start=D(2026, 9, 1), end=D(2026, 9, 1))
    assert doc['mode'] == 'bulk' and doc['authoritative'] is True


def test_dry_run_labelled_screening(tmp_path, monkeypatch, capsys):
    net = Net({'BTCUSDT': days(D(2026, 1, 1), D(2026, 1, 4))})
    monkeypatch.setattr(O, 'http_get', net.list_fetch)
    assert F.main(['--dry-run', '--symbols', 'BTCUSDT', '--dest', str(tmp_path / 's')]) == 0
    out = json.loads(capsys.readouterr().out)
    assert out['mode'] == 'screening' and out['authoritative'] is False and out['explicit_bounds'] is False
    assert 'SCREENING' in out['note'] and net.body_calls == []


def test_hole_outside_window_strict_inside_not(gate3):
    ds = [d for d in days(D(2026, 1, 1), D(2026, 9, 27)) if d != D(2026, 2, 10)]
    out = _cov(ds, start=D(2026, 5, 1))
    assert out['missing_days'] == ['2026-02-10'] and out['missing_required_days'] == []
    assert out['group'] == 'strict_complete'
    inside = _cov(ds, start=D(2026, 2, 1))
    assert inside['missing_required_days'] == ['2026-02-10'] and inside['group'] == 'conditional_with_holes'
    edge = _cov(ds, start=D(2026, 1, 1), end_excl=D(2026, 2, 10))                  # end-exclusive excludes the hole
    assert edge['missing_required_days'] == [] and edge['group'] == 'strict_complete'


def test_covers_cutoff_equality_allowed_past_refused():
    rec = {'listed_first': '2026-01-01', 'listed_last': '2026-09-27', 'missing_days': []}
    assert F.covers(rec, D(2026, 9, 1), F.CUTOFF) is True                         # end_exclusive == cutoff
    with pytest.raises(F.FetchError, match='cutoff'):
        F.covers(rec, D(2026, 9, 1), F.CUTOFF + dt.timedelta(days=1))
