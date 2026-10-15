"""REX gap inventory + Binance archive fill (CORE-10 bounded remedy B/C prep). SYNTHETIC ONLY: a tmp archive store
whose every non-time column is POISON (any price / volume / rate parse would raise), a hand-built attested manifest,
the committed split plan (no data), and a fake archive behind the injectable fetcher. No real store, no network."""
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
import binance_archive_fill as BF                                                           # noqa: E402
import manifest as M                                                                        # noqa: E402
import pit as P                                                                             # noqa: E402
import pit_run as PR                                                                        # noqa: E402
import rex_gap_inventory as GI                                                              # noqa: E402
import splits as S                                                                          # noqa: E402

HOUR, TF, DAY = 3_600_000, 4 * 3_600_000, 86_400_000
SYMS = ('AAAUSDT', 'BBBUSDT')
MONTHS = [(2024, 10), (2024, 11), (2024, 12), (2025, 1), (2025, 2)]
WINDOW = '2024-11-04T00:00:00Z/2025-01-27T00:00:00Z'          # crosses the WF2 / WF3 boundary 2024-12-16
U = S.parse_utc
DROP_BARS = {'AAAUSDT': {U('2024-11-20T08:00:00Z'), U('2024-11-20T12:00:00Z'), U('2024-12-15T20:00:00Z'),
                         U('2024-12-16T00:00:00Z')}}
# BBB: the 07:00 mark is the one funding 08:00 needs; the 03:00 mark is needed by no funding event (not a hole)
DROP_MARKS = {'BBBUSDT': {U('2025-01-10T07:00:00Z'), U('2025-01-10T03:00:00Z')}}
POISON = 'POISON'


def month_range(y, m):
    a = int(datetime(y, m, 1, tzinfo=timezone.utc).timestamp() * 1000)
    return a, int(datetime(y + m // 12, m % 12 + 1, 1, tzinfo=timezone.utc).timestamp() * 1000)


def zbytes(member, lines):
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, 'w') as z:
        z.writestr(member, '\n'.join(lines) + '\n')
    return buf.getvalue()


def entry(store, rel, lines, series, sym, iv, times):
    p = os.path.join(store, *rel.split('/'))
    os.makedirs(os.path.dirname(p), exist_ok=True)
    raw = zbytes(rel.rsplit('/', 1)[1][:-4] + '.csv', lines)
    with open(p, 'wb') as f:
        f.write(raw)
    h = hashlib.sha256(raw).hexdigest()
    with open(p + '.ok', 'w') as f:
        f.write(h)
    return {'path': rel, 'series': series, 'symbol': sym, 'interval': iv, 'sha256': h, 'bytes': len(raw),
            'rows': len(times), 'first_open_ms': times[0] if times else None,
            'last_open_ms': times[-1] if times else None,
            'source': {'kind': M.ARCHIVE_KIND, 'url': M.archive_url(rel), 'archive_checksum': h}}


def poisoned_kline(t, step):
    return ','.join([str(t)] + [POISON] * 11)                  # even close_time is poisoned: only col 0 may be read


def build_store(store):
    files = []
    for sym in SYMS:
        for y, m in MONTHS:
            a, b = month_range(y, m)
            tag = f'{y}-{m:02d}'
            bt = [t for t in range(a, b, TF) if t not in DROP_BARS.get(sym, ())]
            files.append(entry(store, f'um/monthly/klines/{sym}/4h/{sym}-4h-{tag}.zip',
                               [poisoned_kline(t, TF) for t in bt], 'klines', sym, '4h', bt))
            mt = [t for t in range(a, b, HOUR) if t not in DROP_MARKS.get(sym, ())]
            files.append(entry(store, f'um/monthly/markPriceKlines/{sym}/1h/{sym}-1h-{tag}.zip',
                               [poisoned_kline(t, HOUR) for t in mt], 'markPriceKlines', sym, '1h', mt))
            ft = [t + 3 for t in range(a, b, 8 * HOUR)]
            files.append(entry(store, f'um/monthly/fundingRate/{sym}/{sym}-fundingRate-{tag}.zip',
                               ['calc_time,funding_interval_hours,last_funding_rate'] +
                               [f'{t},{POISON},{POISON}' for t in ft], 'fundingRate', sym, None, ft))
    m = {'format': M.FORMAT, 'manifest_id': 'fx-gap', 'source_class': 'archive-verified', 'survivor_only': False,
         'loader_version': M.ARCHIVE_LOADER, 'available_rule': M.ARCHIVE_RULE, 'accessed_cairo': None, 'note': '',
         'data_root': M.ARCHIVE_ROOT, 'files': sorted(files, key=lambda f: f['path'])}
    m['digest'] = M.digest_of(m)
    M.validate(m)
    return m


@pytest.fixture(scope='module')
def plan():
    return PR.rex_plan(ROOT)


CLEAN = 'c' * 40


def fake_git(dirty=''):
    def git(repo, *args):
        return dirty if args[0] == 'status' else CLEAN + '\n'
    return git


@pytest.fixture
def fx(tmp_path, monkeypatch):
    monkeypatch.setattr(PR, '_git', fake_git())      # tests never depend on the worktree's own git state
    store = str(tmp_path / 'store')
    return {'store': store, 'man': build_store(store)}


def report(fx, plan, **kw):
    a = dict(symbols=list(SYMS), window=WINDOW, plan=plan, tier='CORE-10', repo=ROOT)
    a.update(kw)
    return GI.build(fx['man'], fx['store'], **a)


def key(h):
    return (h['symbol'], h['series'], S.utc(h['start_ms']), S.utc(h['end_ms']), h['missing'], h['reason'], h['class'],
            h['split'])


# ------------------------------------------------------------------ inventory
def test_inventory_finds_injected_bar_and_mark_holes_exactly(fx, plan):
    rep = report(fx, plan)
    assert [key(h) for h in rep['holes']] == [
        ('AAAUSDT', 'klines', '2024-11-20T08:00:00Z', '2024-11-20T16:00:00Z', 2, 'incomplete.gap', 'missing_bar',
         'walk_forward[2]'),
        ('AAAUSDT', 'klines', '2024-12-15T20:00:00Z', '2024-12-16T00:00:00Z', 1, 'incomplete.gap', 'missing_bar',
         'walk_forward[2]'),
        ('AAAUSDT', 'klines', '2024-12-16T00:00:00Z', '2024-12-16T04:00:00Z', 1, 'incomplete.gap', 'missing_bar',
         'walk_forward[3]'),
        ('BBBUSDT', 'markPriceKlines', '2025-01-10T07:00:00Z', '2025-01-10T08:00:00Z', 1, 'incomplete.funding_mark',
         'funding_mark_gap', 'walk_forward[3]')]
    mh = rep['holes'][-1]
    assert mh['funding_events_utc'] == ['2025-01-10T08:00:00Z']
    assert mh['store_files'] == ['um/monthly/markPriceKlines/BBBUSDT/1h/BBBUSDT-1h-2025-01.zip']
    assert rep['series_end'] == [] and rep['off_grid_funding'] == {}
    assert rep['missing_by_reason_split'] == {'incomplete.gap|walk_forward[2]': 3, 'incomplete.gap|walk_forward[3]': 1,
                                              'incomplete.funding_mark|walk_forward[3]': 1}


def test_split_assignment_follows_the_committed_plan(fx, plan):
    rep = report(fx, plan)
    segs = {s['key']: s for s in rep['segments']}
    assert list(segs) == ['walk_forward[2]', 'walk_forward[3]']
    assert segs['walk_forward[3]']['lo'] == U('2024-12-16T00:00:00Z')
    for h in rep['holes']:
        s = segs[h['split']]
        assert s['lo'] <= h['start_ms'] < h['end_ms'] <= s['hi']
        assert h['split_name'] == 'walk_forward'


def test_only_time_columns_are_read(fx, plan, monkeypatch):
    """Structural price-blindness: every non-time column of the store is POISON, the row parsers that yield prices
    (pit.Dataset.rows, manifest.parse_archive_csv) and float() itself are disabled in the module, and the run still
    succeeds; only holey / funding files are opened at all."""
    def boom(*a, **k):
        raise AssertionError('a price-bearing parser was called')
    monkeypatch.setattr(M, 'parse_archive_csv', boom)
    monkeypatch.setattr(P.Dataset, 'rows', boom)
    monkeypatch.setattr(GI, 'float', boom, raising=False)
    opened = []
    real = GI.file_times

    def spy(store, f, lo, hi):
        opened.append(f['path'])
        out = real(store, f, lo, hi)
        assert all(type(x) is int for x in out)
        return out
    rep = report(fx, plan, reader=spy)
    assert len(rep['holes']) == 4
    assert all('/fundingRate/' in p or p.endswith(('AAAUSDT-4h-2024-11.zip', 'AAAUSDT-4h-2024-12.zip',
                                                   'BBBUSDT-1h-2025-01.zip')) for p in opened)
    assert POISON not in json.dumps(rep)
    assert GI.time_column(b'open_time,open\n1,POISON,x\n2,y\n') == (1, 2)


def test_time_reader_rechecks_the_manifest_sha(fx, plan):
    f = next(f for f in fx['man']['files'] if f['path'].endswith('AAAUSDT-4h-2024-11.zip'))
    with pytest.raises(GI.InventoryError, match='sha256'):
        GI.file_times(fx['store'], dict(f, sha256='0' * 64), 0, 2 ** 62)


@pytest.mark.parametrize('spec', ['2026-09-14T00:00:00Z/2026-10-26T00:00:00Z',     # reaches the sealed cutoff / holdout
                                  '2020-06-01T00:00:00Z/2020-09-01T00:00:00Z',     # calibration
                                  '2026-10-19T00:00:00Z/2026-12-28T00:00:00Z'])    # the holdout itself
def test_holdout_and_calibration_are_refused_before_any_read(fx, plan, spec):
    def never(*a, **k):
        raise AssertionError('store opened')
    with pytest.raises(PR.PitRunError):
        report(fx, plan, window=spec, reader=never)


def test_inventory_is_deterministic(fx, plan):
    a, b = report(fx, plan), report(fx, plan)
    assert GI.canon(a) == GI.canon(b) and a[GI.SIG] == GI.sign(a)[GI.SIG]


# ------------------------------------------------------------------ archive fill
def good_kline(t, step):
    return f'{t},1.0,1.0,1.0,1.0,0,{t + step - 1},0,0,0,0,0'


def day_zip(series, sym, iv, day, step, drop=()):
    a = U(f'{day}T00:00:00Z')
    name = f'{sym}-{iv}-{day}'
    raw = zbytes(name + '.csv', [good_kline(t, step) for t in range(a, a + DAY, step) if t not in drop])
    url = BF.url_for('daily', series, sym, iv, day)
    return url, raw, f'{hashlib.sha256(raw).hexdigest()}  {name}.zip'.encode()


def fake_archive(files, overrides=None, finals=None):
    pages, finals = {}, finals or {}
    for url, raw, ck in files:
        pages[url], pages[url + '.CHECKSUM'] = (200, raw), (200, ck)
    pages.update(overrides or {})
    calls = []

    def fetch(url):
        calls.append(url)
        st, body = pages.get(url, (404, b''))
        return st, body, finals.get(url, url)
    fetch.calls = calls
    return fetch


class Clock:
    def __init__(self):
        self.t, self.slept = 0.0, []

    def clock(self):
        return self.t

    def sleep(self, s):
        self.slept.append(s)
        self.t += s


def all_days(rep):
    return [day_zip('klines', 'AAAUSDT', '4h', '2024-11-20', TF), day_zip('klines', 'AAAUSDT', '4h', '2024-12-15', TF),
            day_zip('klines', 'AAAUSDT', '4h', '2024-12-16', TF),
            day_zip('markPriceKlines', 'BBBUSDT', '1h', '2025-01-10', HOUR)]


def run_fill(rep, fetch):
    c = Clock()
    return BF.fill(rep, fetch, sleep=c.sleep, clock=c.clock), c


def test_plan_urls_monthly_vs_daily(fx, plan):
    rep = report(fx, plan)
    urls = [r['url'] for r in BF.plan(rep)]
    base = 'https://data.binance.vision/data/futures/um'
    assert urls == sorted([f'{base}/daily/klines/AAAUSDT/4h/AAAUSDT-4h-2024-11-20.zip',
                           f'{base}/daily/klines/AAAUSDT/4h/AAAUSDT-4h-2024-12-15.zip',
                           f'{base}/daily/klines/AAAUSDT/4h/AAAUSDT-4h-2024-12-16.zip',
                           f'{base}/daily/markPriceKlines/BBBUSDT/1h/BBBUSDT-1h-2025-01-10.zip'])
    assert all(r['checksum_url'] == r['url'] + '.CHECKSUM' for r in BF.plan(rep))
    # a month the store does not hold gets its monthly zip (the store's own monthly file is the one with the hole)
    rep2 = dict(rep, holes=[dict(h, store_files=[]) if h['series'] == 'markPriceKlines' else h for h in rep['holes']])
    rep2 = GI.sign(rep2)
    assert f'{base}/monthly/markPriceKlines/BBBUSDT/1h/BBBUSDT-1h-2025-01.zip' in [r['url'] for r in BF.plan(rep2)]
    doc = BF.plan_doc(rep)
    assert doc['format'] == BF.PLAN_FORMAT and doc['fetches'] == 8


def test_plan_only_cli_fetches_nothing(fx, plan, tmp_path, monkeypatch):
    rep = report(fx, plan)
    p, out = tmp_path / 'rep.json', tmp_path / 'plan.json'
    p.write_text(json.dumps(rep))
    monkeypatch.setattr(BF, 'http_fetcher', lambda url: pytest.fail('network touched'))
    assert BF.main(['--report', str(p), '--out', str(out)]) == 0
    assert json.loads(out.read_text())['format'] == BF.PLAN_FORMAT


def test_fill_extracts_only_hole_rows_with_digests_and_paces(fx, plan):
    rep = report(fx, plan)
    doc, c = run_fill(rep, fake_archive(all_days(rep)))
    assert doc['stopped'] is None and doc['genuine_gaps'] == []
    assert [(r['symbol'], S.utc(r['open_ms'])) for r in doc['rows']] == [
        ('AAAUSDT', '2024-11-20T08:00:00Z'), ('AAAUSDT', '2024-11-20T12:00:00Z'), ('AAAUSDT', '2024-12-15T20:00:00Z'),
        ('AAAUSDT', '2024-12-16T00:00:00Z'), ('BBBUSDT', '2025-01-10T07:00:00Z')]
    assert all(r['sha256'] == hashlib.sha256(r['line'].encode()).hexdigest() for r in doc['rows'])
    assert {h['status'] for h in doc['holes']} == {'filled'}
    assert all(f['status'] == 'verified' and len(f['sha256']) == 64 for f in doc['files'])
    assert sum(f['rows'] for f in doc['files']) == 5               # the other 18 + 23 rows of those days are not kept
    assert doc['fetched'] == 8 and len(c.slept) == 7 and all(s >= 1.0 for s in c.slept)
    with pytest.raises(BF.FillError):
        BF.Pacer(lambda u: (200, b'', u), min_gap=0.5)


def test_checksum_mismatch_is_rejected(fx, plan):
    rep = report(fx, plan)
    files = all_days(rep)
    url, raw, _ = files[0]
    files[0] = (url, raw, b'0' * 64 + b'  AAAUSDT-4h-2024-11-20.zip')
    doc, _ = run_fill(rep, fake_archive(files))
    f = next(f for f in doc['files'] if f['url'] == url)
    assert f['status'] == 'rejected_checksum'
    h = next(h for h in doc['holes'] if h['hole'].endswith(str(U('2024-11-20T08:00:00Z'))))
    assert h['status'] == 'unresolved' and h['filled'] == 0
    assert not any(r['hole'] == h['hole'] for r in doc['rows']) and doc['genuine_gaps'] == []


def test_archive_missing_hole_is_reported_not_filled(fx, plan):
    rep = report(fx, plan)
    files = all_days(rep)
    files[3] = day_zip('markPriceKlines', 'BBBUSDT', '1h', '2025-01-10', HOUR, drop={U('2025-01-10T07:00:00Z')})
    del files[2]                                                   # 2024-12-16 daily file: HTTP 404
    doc, _ = run_fill(rep, fake_archive(files))
    gg = [(g['symbol'], g['series'], S.utc(g['start_ms']), g['status']) for g in doc['genuine_gaps']]
    assert gg == [('AAAUSDT', 'klines', '2024-12-16T00:00:00Z', 'genuine_exchange_gap (never filled)'),
                  ('BBBUSDT', 'markPriceKlines', '2025-01-10T07:00:00Z', 'genuine_exchange_gap (never filled)')]
    assert not any(r['symbol'] == 'BBBUSDT' or r['open_ms'] == U('2024-12-16T00:00:00Z') for r in doc['rows'])
    assert sorted(h['status'] for h in doc['holes']) == ['archive_missing', 'archive_missing', 'filled', 'filled']


@pytest.mark.parametrize('status,body,reason', [(429, b'', 'rate_limited'), (418, b'', 'ip_banned'),
                                                (403, b'', 'bot_wall'), (200, b'<!DOCTYPE html><title>x', 'bot_wall')])
def test_rate_limit_or_bot_wall_stops_the_run(fx, plan, status, body, reason):
    rep = report(fx, plan)
    files = all_days(rep)
    first = BF.plan(rep)[1]['checksum_url']
    fetch = fake_archive(files, {first: (status, body)})
    doc, _ = run_fill(rep, fetch)
    assert doc['stopped'] == {'reason': reason, 'status': status, 'url': first}
    assert fetch.calls[-1] == first and doc['fetched'] == 3       # nothing after the stop
    assert doc['genuine_gaps'] == []
    assert sum(h['status'] == 'unresolved' for h in doc['holes']) == 3


def test_fill_is_deterministic(fx, plan):
    rep = report(fx, plan)
    a, _ = run_fill(rep, fake_archive(all_days(rep)))
    b, _ = run_fill(rep, fake_archive(all_days(rep)))
    assert GI.canon(a) == GI.canon(b) and a['digest'] == BF.digest(a)
    assert GI.canon(BF.plan_doc(rep)) == GI.canon(BF.plan_doc(rep))


def test_edited_or_out_of_window_report_is_refused(fx, plan):
    rep = report(fx, plan)
    with pytest.raises(BF.FillError, match='sha256'):
        BF.plan(dict(rep, holes=rep['holes'][:1]))
    bad = dict(rep, holes=[dict(rep['holes'][0], split_name='holdout')])
    bad = GI.sign(bad)
    with pytest.raises(BF.FillError, match='holdout'):
        BF.plan(bad)


# ------------------------------------------------------------------ Codex correction on #88 (9b6eb03)
def test_report_end_after_the_sealed_cutoff_is_refused_before_planning(fx, plan):
    rep = report(fx, plan)
    late = GI.sign(dict(rep, window={'start': rep['window']['start'], 'end': '2026-10-05T00:00:00Z'}))
    with pytest.raises(BF.FillError, match='sealed cutoff'):
        BF.plan(late)
    hole = GI.sign(dict(rep, window={'start': rep['window']['start'], 'end': '2026-12-28T00:00:00Z'},
                        holes=[dict(rep['holes'][0], start_ms=U('2026-10-20T00:00:00Z'),
                                    end_ms=U('2026-10-20T04:00:00Z'))]))
    with pytest.raises(BF.FillError):
        BF.plan_doc(hole)
    with pytest.raises(BF.FillError):
        run_fill(hole, lambda u: pytest.fail('fetched'))
    assert BF.SEALED_END == PR.REX_DEV_WINDOW[1]


@pytest.mark.parametrize('final', [
    'http://data.binance.vision/data/futures/um/daily/klines/AAAUSDT/4h/AAAUSDT-4h-2024-11-20.zip',      # http
    'https://evil.example.com/data/futures/um/daily/klines/AAAUSDT/4h/AAAUSDT-4h-2024-11-20.zip',        # other host
    'https://data.binance.vision.evil.com/data/futures/um/daily/klines/AAAUSDT/4h/x.zip',                # lookalike
    'https://data-binance.vision/data/futures/um/daily/klines/AAAUSDT/4h/x.zip',                         # lookalike
    'https://data.binance.vision@evil.com/data/futures/um/daily/klines/AAAUSDT/4h/x.zip',                # userinfo
    'https://data.binance.vision:8443/data/futures/um/daily/klines/AAAUSDT/4h/x.zip'])                   # port
def test_redirect_whose_final_url_is_off_archive_stops_the_run(fx, plan, final):
    rep = report(fx, plan)
    first = BF.plan(rep)[0]
    fetch = fake_archive(all_days(rep), finals={first['checksum_url']: final})
    doc, _ = run_fill(rep, fetch)
    assert doc['stopped']['reason'] == 'offsite_redirect' and doc['stopped']['url'] == final
    assert fetch.calls == [first['checksum_url']] and doc['rows'] == [] and doc['genuine_gaps'] == []
    assert all(h['status'] == 'unresolved' for h in doc['holes'])


def test_real_fetcher_redirect_guard_refuses_before_following():
    import urllib.request
    g = BF._redirect_handler()
    req = urllib.request.Request(BF.url_for('daily', 'klines', 'AAAUSDT', '4h', '2024-11-20'))
    for bad in ('http://data.binance.vision/data/futures/um/x.zip', 'https://evil.com/data/futures/um/x.zip',
                'https://data.binance.vision.evil.com/data/futures/um/x.zip'):
        with pytest.raises(BF.OffsiteRedirect):
            g.redirect_request(req, None, 302, 'Found', {}, bad)
    ok = 'https://data.binance.vision/data/futures/um/daily/klines/AAAUSDT/4h/AAAUSDT-4h-2024-11-21.zip'
    assert g.redirect_request(req, None, 302, 'Found', {}, ok).full_url == ok
    with pytest.raises(BF.FillError):
        BF.http_fetcher('https://evil.com/data/futures/um/x.zip')        # refused before any socket is opened


def test_inventory_opens_no_ledger_and_pins_provenance(fx, plan, monkeypatch, tmp_path):
    import ledger as L

    def boom(*a, **k):
        raise AssertionError('a PIT data_access / ledger record was attempted')
    monkeypatch.setattr(L, 'append', boom)
    monkeypatch.setattr(P.Access, '__init__', boom)
    monkeypatch.setattr(P.Access, 'open_development', boom)
    rep = report(fx, plan)
    assert rep['purpose'] == 'timestamp-only gap inventory'
    assert rep['provenance'] == {'commit': CLEAN, 'tree': 'clean'} and rep['manifest_digest'] == fx['man']['digest']
    assert rep['window'] == {'start': '2024-11-04T00:00:00Z', 'end': '2025-01-27T00:00:00Z'}
    by_path = {f['path']: f['sha256'] for f in fx['man']['files']}
    assert rep['files_read'] and all(by_path[r['path']] == r['sha256'] for r in rep['files_read'])
    for r in rep['files_read']:
        with open(os.path.join(fx['store'], *r['path'].split('/')), 'rb') as f:
            assert hashlib.sha256(f.read()).hexdigest() == r['sha256']
    want = sorted(p for p in by_path
                  if '/fundingRate/' in p and p.endswith(('2024-11.zip', '2024-12.zip', '2025-01.zip')))
    assert want == sorted(r['path'] for r in rep['files_read'] if '/fundingRate/' in r['path'])
    assert not any('ledger' in n for n in os.listdir(tmp_path))


def test_inventory_refuses_a_dirty_tree_before_any_read(fx, plan, monkeypatch):
    monkeypatch.setattr(PR, '_git', fake_git(' M tools/x.py\n'))
    with pytest.raises(PR.PitRunError, match='dirty'):
        report(fx, plan, reader=lambda *a: pytest.fail('store opened'))


def test_report_is_self_digested_and_tamper_evident(fx, plan):
    rep = report(fx, plan)
    body = {k: v for k, v in rep.items() if k != 'sha256'}
    assert rep['sha256'] == hashlib.sha256(GI.canon(body)).hexdigest()
    assert GI.verify(rep) is rep
    for edit in ({'purpose': 'x'}, {'files_read': []}, {'provenance': {'commit': 'd' * 40, 'tree': 'clean'}}):
        with pytest.raises(GI.InventoryError, match='sha256'):
            GI.verify(dict(rep, **edit))
