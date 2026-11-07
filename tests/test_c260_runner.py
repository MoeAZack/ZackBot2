"""Actual checksum ZIP→typed source→C260 book→CLI synthetic proofs only."""
from dataclasses import replace
from datetime import datetime, timezone
import hashlib
import json
import math
from pathlib import Path
import subprocess
import sys
from types import MappingProxyType
import zipfile

import pytest

from tools.research import c260_low_idio_variance as C
from tools.research import c260_runner as R
from tools.research import c260_authority as AUTH
from tools.research.c260_archive import (DevelopmentArchive, SyntheticArchive, SourceIncomplete,
                                        Kline, MINUTE, _trade_hours, milliseconds)
from tools.research import manifest as M
from tools.research import universe as U

H = C.HOUR_MS
START = milliseconds('2025-01-01T00:00:00Z')
END = milliseconds('2025-04-01T00:00:00Z')
MEMBERS = tuple(f'ALT{i:02d}USDT' for i in reversed(range(21))) + ('BTCUSDT',)


def write_json(path, obj):
    raw = M.canonical(obj)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(raw)
    return hashlib.sha256(raw).hexdigest()


def archive(store, symbol, series, month, rows, interval='1h'):
    suffix = 'fundingRate' if series == 'fundingRate' else interval
    folder = '' if series == 'fundingRate' else interval + '/'
    rel = f'um/monthly/{series}/{symbol}/{folder}{symbol}-{suffix}-2025-{month:02d}.zip'
    path = store / rel
    path.parent.mkdir(parents=True, exist_ok=True)
    header = M.FUNDING_HEADER if series == 'fundingRate' else M.KLINE_HEADER
    with zipfile.ZipFile(path, 'w', compression=zipfile.ZIP_DEFLATED) as z:
        z.writestr(path.name[:-4] + '.csv', header + b'\n' + b'\n'.join(rows) + b'\n')
    path.with_suffix('.zip.ok').write_text(hashlib.sha256(path.read_bytes()).hexdigest())
    return M.scan_archive(str(path), rel)


def make_fixture(root, *, hours=90 * 24, members=MEMBERS, trade_interval='1h'):
    files = []
    stop = min(END, START + hours * H)
    # More than twenty eligible members in nonlexicographic artifact order.
    for symbol in members:
        price = 100.
        all_rows = {}
        for index, t in enumerate(range(START, stop, H)):
            change = .00025 * math.sin(index * .07) + .00015 * math.cos(index * .03)
            if symbol != 'BTCUSDT':
                change = .5 * change + (int(symbol[3:5]) + 1) * .0000005 * math.sin(index * .11)
            opening = round(price, 2)
            price *= math.exp(change)
            closing = round(price, 2)
            values = [t, opening, round(max(opening, closing) + .25, 2), round(min(opening, closing) - .25, 2),
                      closing, 10000, t + H - 1, '100000000', 10, 5000, '50000000', 0]
            line = ','.join(map(str, values)).encode()
            month = datetime.fromtimestamp(t / 1000, timezone.utc).month
            all_rows.setdefault(month, []).append(line)
        for month, rows in all_rows.items():
            files.append(archive(root / 'store', symbol, 'markPriceKlines', month, rows))
            if trade_interval == '1m':
                minutes = []
                for row in rows:
                    cells = row.split(b','); timestamp = int(cells[0])
                    for minute in range(60):
                        point = list(cells)
                        point[0] = str(timestamp + minute * MINUTE).encode()
                        point[1] = cells[1] if minute == 0 else cells[4]
                        point[6] = str(timestamp + (minute + 1) * MINUTE - 1).encode()
                        point[7] = b'41000000' if minute == 0 else b'1000000'
                        minutes.append(b','.join(point))
                files.append(archive(root / 'store', symbol, 'klines', month, minutes, interval='1m'))
            else:
                files.append(archive(root / 'store', symbol, 'klines', month, rows))
            funding = [f'{t},8,0.00001'.encode() for t in range(START, stop, 8 * H)
                       if datetime.fromtimestamp(t / 1000, timezone.utc).month == month]
            files.append(archive(root / 'store', symbol, 'fundingRate', month, funding))
    manifest = dict(format=M.FORMAT, manifest_id='C260_SYNTHETIC_ONLY', source_class='archive-verified',
                    survivor_only=False, loader_version=M.ARCHIVE_LOADER, available_rule=M.ARCHIVE_RULE,
                    accessed_cairo=None, note='SYNTHETIC NON EVIDENCE', data_root=M.ARCHIVE_ROOT,
                    files=sorted(files, key=lambda r: r['path']))
    manifest['digest'] = M.digest_of(manifest)
    weeks = []
    for monday in range(milliseconds('2024-12-30T00:00:00Z'), END, 7 * C.DAY_MS):
        weeks.append({'monday_ms': monday,
                      'monday_utc': datetime.fromtimestamp(monday / 1000, timezone.utc).isoformat().replace('+00:00', 'Z'),
                      'books': {'crypto': {'members': list(members)}}})
    pit = {'format': U.FORMAT, 'universe_id': 'C260_SYNTHETIC_PIT_V8',
           'manifest_digest': manifest['digest'], 'weeks': weeks}
    pit['digest'] = U.digest_of(pit)
    pit_sha = write_json(root / 'pit.json', pit)
    captured = '2024-12-31T00:00:00Z'
    snapshot = {'serverTime': milliseconds('2024-12-30T00:00:00Z'), 'symbols': [
        {'symbol': symbol, 'status': 'TRADING', 'contractType': 'PERPETUAL', 'quoteAsset': 'USDT',
         'underlyingType': 'COIN', 'filters': [
             {'filterType': 'PRICE_FILTER', 'tickSize': '0.01'},
             {'filterType': 'MARKET_LOT_SIZE', 'stepSize': '0.001', 'minQty': '0.001', 'maxQty': '10000'},
             {'filterType': 'MIN_NOTIONAL', 'notional': '5'}]} for symbol in members]}
    snapshot_sha = write_json(root / 'snapshot.json', snapshot)
    core_cells = [{'monday_utc': w['monday_utc'], 'members': list(members[:20])} for w in weeks]
    base = {'authority': 'STRATEGY-CAUSAL-PACK-01-EXCHANGE-FILTER-AUTHORITY', 'version': 2,
            'status': 'FROZEN', 'signed_by': 'codex', 'pit_universe_path': 'old-pit.json',
            'pit_universe_sha256': '0' * 64, 'pit_universe_digest': '1' * 64,
            'snapshots': [{'captured_at': captured, 'server_time': '2024-12-30T00:00:00Z',
                           'path': 'snapshot.json', 'sha256': snapshot_sha}],
            # Preserve the actual v3 WF-only key shape. C260 must derive the
            # development snapshot without manufacturing a development cell.
            'fold_bindings': {fold: captured for fold in ('wf1', 'wf2', 'wf3')},
            'weekly_membership_sha256': {fold: hashlib.sha256(M.canonical(core_cells)).hexdigest()
                                         for fold in ('wf1', 'wf2', 'wf3')}}
    prior_sha = write_json(root / 'filters.v2.json', base)
    filters = {**base, 'version': 3, 'pit_universe_path': 'pit.json', 'pit_universe_sha256': pit_sha,
               'pit_universe_digest': pit['digest'],
               'supersedes': {'path': 'filters.v2.json', 'byte_sha256': prior_sha, **{
                   key: base[key] for key in ('version', 'pit_universe_path', 'pit_universe_sha256', 'pit_universe_digest')}},
               'rebind_scope': {'pit_identity_only': True, 'snapshot_rows_changed': False,
                                'fold_bindings_changed': False, 'weekly_membership_changed': False,
                                'market_rows_read': False, 'candidate_outcomes_read': False}}
    coverage = {'format': 'zb-strategy-causal-pack-01-authority-coverage-preflight/1',
                'manifest_digest': manifest['digest'], 'pit_universe_digest': pit['digest'],
                'required_streams': [{'series': s, 'interval': i} for s, i in
                                     [('klines', '1h'), ('markPriceKlines', '1h'), ('fundingRate', None)]]}
    coverage['digest'] = M.digest_of(coverage)
    artifacts = {}
    for name, value in [('manifest', manifest), ('pit', pit), ('filters', filters), ('coverage', coverage)]:
        rel = name + '.json'
        artifacts[name] = {'path': rel, 'byte_sha256': write_json(root / rel, value)}
    write_json(root / 'c260.synthetic.json', {'format': 'zb-c260-SYNTHETIC-NON-EVIDENCE/1', 'candidate_id': 260,
                                            'proposal_sha256': C.PREREG_SHA256, 'artifacts': artifacts})
    return root


@pytest.fixture(scope='module')
def fixture_root(tmp_path_factory):
    return make_fixture(tmp_path_factory.mktemp('c260-archive'))


@pytest.fixture(scope='module')
def minute_fixture_root(tmp_path_factory):
    # Production-type issued loop derives actual authenticated minute ZIPs.
    # A small universe keeps this proof cheap; full22 ranks use the separate,
    # explicitly synthetic native-hourly component fixture.
    return make_fixture(tmp_path_factory.mktemp('c260-minute-archive'),
                        members=('ALT00USDT', 'ALT01USDT', 'BTCUSDT'), trade_interval='1m')


@pytest.fixture(scope='module')
def parsed_fixture(fixture_root):
    # Authenticate the explicit native-hourly fixture once. Each test receives
    # its own cache dictionary; values are immutable typed rows, and mutations
    # replace only that test's map. CLI proofs still load actual ZIPs afresh.
    source = SyntheticArchive(fixture_root)
    for symbol in MEMBERS:
        source.klines(symbol)
        source.marks(symbol)
        source.funding(symbol)
    return source


@pytest.fixture
def source(fixture_root, parsed_fixture):
    source = SyntheticArchive(fixture_root)
    source._cache = dict(parsed_fixture._cache)
    source._issued_streams = dict(parsed_fixture._issued_streams)
    return source


def issue_test_metadata(monkeypatch, *, repository, store, artifacts, common):
    """Exercise the actual issuer/claim with synthetic authenticated metadata.

    The verifier itself is independently tested with real Git receipts. This
    seam test substitutes only its metadata result, never an issued capability,
    acceptance PASS, real store, or original repository identity directory.
    """
    monkeypatch.setattr(AUTH, 'ROOT', repository)
    monkeypatch.setattr(AUTH, '_git', lambda *args: str(common) if args == ('rev-parse', '--git-common-dir')
                        else pytest.fail('unexpected Git authority call'))
    monkeypatch.setattr(AUTH, '_authenticate_metadata', lambda: (
        'a' * 40, 'b' * 64, store, artifacts, START, END))


def fixture_artifacts(root):
    receipt = json.loads((root / 'c260.synthetic.json').read_bytes())
    return tuple((name, pin['path'], pin['byte_sha256'], (root / pin['path']).read_bytes())
                 for name, pin in receipt['artifacts'].items())


def repin_synthetic_manifest(root, manifest):
    """Keep generated fixture crossbindings exact; never a production receipt."""
    manifest['digest'] = M.digest_of(manifest)
    pit = json.loads((root / 'pit.json').read_bytes())
    pit['manifest_digest'] = manifest['digest']; pit['digest'] = U.digest_of(pit)
    pit_sha = write_json(root / 'pit.json', pit)
    filters = json.loads((root / 'filters.json').read_bytes())
    filters['pit_universe_sha256'], filters['pit_universe_digest'] = pit_sha, pit['digest']
    coverage = json.loads((root / 'coverage.json').read_bytes())
    coverage['manifest_digest'], coverage['pit_universe_digest'] = manifest['digest'], pit['digest']
    coverage['digest'] = M.digest_of(coverage)
    receipt = json.loads((root / 'c260.synthetic.json').read_bytes())
    for name, doc in (('manifest', manifest), ('pit', pit), ('filters', filters), ('coverage', coverage)):
        receipt['artifacts'][name]['byte_sha256'] = write_json(root / (name + '.json'), doc)
    write_json(root / 'c260.synthetic.json', receipt)


def write_test_zip(store, rel, lines):
    path = store / rel; path.parent.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(path, 'w', compression=zipfile.ZIP_DEFLATED) as output:
        output.writestr(path.name[:-4] + '.csv', M.KLINE_HEADER + b'\n' + b'\n'.join(lines) + b'\n')
    raw = path.read_bytes(); digest = hashlib.sha256(raw).hexdigest()
    Path(str(path) + '.ok').write_text(digest)
    return raw, digest


def minute_points(hours=1):
    return {START + i * MINUTE: Kline(START + i * MINUTE, 100. + i * .01,
                                     102. + i * .01, 99. + i * .01, 101. + i * .01,
                                     1e16 if i % 60 == 0 else 1.) for i in range(hours * 60)}


def test_exact_sixty_minute_hour_ohlc_and_binary64_fsum():
    minutes = minute_points()
    hour, = _trade_hours(minutes).values()
    assert hour.open_ms == START and hour.open == minutes[START].open
    assert hour.high == max(row.high for row in minutes.values())
    assert hour.low == min(row.low for row in minutes.values())
    assert hour.close == minutes[START + 59 * MINUTE].close
    assert hour.quote_volume == math.fsum(row.quote_volume for row in minutes.values())
    naive = 0.
    for row in minutes.values():
        naive += row.quote_volume
    assert hour.quote_volume != naive
    assert set(hour.__dataclass_fields__) == {'open_ms', 'open', 'high', 'low', 'close', 'quote_volume'}


@pytest.mark.parametrize('attack', ['missing_minute', 'missing_whole_hour', 'off_grid'])
def test_derivation_rejects_partial_hours_internal_gaps_and_off_grid_minutes(attack):
    points = minute_points(3)
    if attack == 'missing_minute':
        del points[START + 30 * MINUTE]
    elif attack == 'missing_whole_hour':
        for t in range(START + H, START + 2 * H, MINUTE): del points[t]
    else:
        points[START + 1] = points[START]
    with pytest.raises(SourceIncomplete, match='TRADE_1M'):
        _trade_hours(points)


def test_minute_order_does_not_become_a_new_execution_path():
    a = {START + i * MINUTE: Kline(START + i * MINUTE, 100., 101., 99., 100., 1.) for i in range(60)}
    a[START + 10 * MINUTE] = replace(a[START + 10 * MINUTE], high=110.)
    a[START + 20 * MINUTE] = replace(a[START + 20 * MINUTE], low=90.)
    b = dict(a)
    b[START + 10 * MINUTE] = replace(a[START + 20 * MINUTE], open_ms=START + 10 * MINUTE)
    b[START + 20 * MINUTE] = replace(a[START + 10 * MINUTE], open_ms=START + 20 * MINUTE)
    first, second = _trade_hours(a)[START], _trade_hours(b)[START]
    assert first == second
    rule = C.PACK.ExchangeRule(.01, .001, .001, 1., 1000.)
    def fill(hour):
        pos = C.PACK.open_position(side='long', entry_index=0, entry_time_ms=START, reference_open=100.,
            atr_entry=1., risk_cash=20., leverage_notional_cap=3000., management={'stop_atr': 2.}, rule=rule)
        return C.PACK.walk_bar(pos, hour.pack(), 0, {'stop_atr': 2.}, rule)
    assert fill(first) == fill(second) and fill(first)[0].reason == 'stop'


@pytest.mark.parametrize('attack', ['minute_gap', 'duplicate', 'bad_close_time', 'ignore_native_hourly'])
def test_issued_minute_archive_integrity_and_no_native_fallback(tmp_path, monkeypatch, attack):
    root = make_fixture(tmp_path / 'fixture', hours=2, members=('ALT00USDT', 'BTCUSDT'), trade_interval='1m')
    manifest = json.loads((root / 'manifest.json').read_bytes())
    record = next(r for r in manifest['files'] if r['symbol'] == 'BTCUSDT' and r['series'] == 'klines')
    _, _, payload = M.read_archive(str(root / 'store' / record['path']), record['path'])
    lines = payload.splitlines()[1:]
    if attack in ('minute_gap', 'bad_close_time'):
        if attack == 'minute_gap': del lines[30]
        else:
            cells = lines[0].split(b','); cells[6] = str(START + MINUTE).encode(); lines[0] = b','.join(cells)
        raw, digest = write_test_zip(root / 'store', record['path'], lines)
        record.update(sha256=digest, bytes=len(raw), rows=len(lines))
        record['source']['archive_checksum'] = digest
    elif attack == 'duplicate':
        rel = 'um/daily/klines/BTCUSDT/1m/BTCUSDT-1m-2025-01-01.zip'
        write_test_zip(root / 'store', rel, lines[:60])
        manifest['files'].append(M.scan_archive(str(root / 'store' / rel), rel))
    else:
        # Schema/checksum-valid but deliberately nonsensical native1h prices.
        # The production type must never open or interpret this fallback.
        native = f'{START},NATIVE_1H_MUST_NOT_BE_READ,101,99,100,1,{START+H-1},1,1,1,1,0'.encode()
        manifest['files'].append(archive(root / 'store', 'BTCUSDT', 'klines', 1, [native]))
    manifest['files'].sort(key=lambda r: r['path'])
    repin_synthetic_manifest(root, manifest)
    issue_test_metadata(monkeypatch, repository=root, store=root / 'store',
                        artifacts=fixture_artifacts(root), common=tmp_path / 'common')
    original, opened = M.read_archive, []
    def watch(path, rel):
        opened.append(rel)
        return original(path, rel)
    monkeypatch.setattr(M, 'read_archive', watch)
    with AUTH.development_authority() as cap:
        source = DevelopmentArchive(cap)
        if attack == 'ignore_native_hourly':
            bars = source.klines('BTCUSDT')
            assert len(bars) == 2 and bars[START].open == 100.
            assert all(M.archive_meta(rel)['interval'] == '1m' for rel in opened)
        else:
            with pytest.raises((SourceIncomplete, M.ManifestError), match='GAP|DUPLICATE_SOURCE_COORDINATE|close_time'):
                source.klines('BTCUSDT')


def test_production_type_refuses_native_only_trade_fixture_without_reading_it(fixture_root, tmp_path, monkeypatch):
    issue_test_metadata(monkeypatch, repository=fixture_root, store=fixture_root / 'store',
                        artifacts=fixture_artifacts(fixture_root), common=tmp_path / 'common')
    monkeypatch.setattr(M, 'read_archive', lambda *args: pytest.fail('native1h fallback read'))
    with AUTH.development_authority() as cap:
        source = DevelopmentArchive(cap)
        with pytest.raises(SourceIncomplete, match='MISSING_REQUIRED_STREAM'):
            source.klines('BTCUSDT')


def test_production_minute_derivation_excludes_april_poison_before_source_read(tmp_path, monkeypatch):
    root = make_fixture(tmp_path / 'fixture', hours=1, members=('ALT00USDT', 'BTCUSDT'), trade_interval='1m')
    manifest = json.loads((root / 'manifest.json').read_bytes())
    manifest['files'] = [r for r in manifest['files'] if not (r['symbol'] == 'BTCUSDT' and r['series'] == 'klines')]
    for month, start in ((3, END - H), (4, END)):
        rows = []
        for minute in range(60):
            t = start + minute * MINUTE
            price = '100' if month == 3 else 'FORBIDDEN_OUTSIDE_DEVELOPMENT'
            rows.append(f'{t},{price},{price},{price},{price},1,{t+MINUTE-1},1,1,1,1,0'.encode())
        manifest['files'].append(archive(root / 'store', 'BTCUSDT', 'klines', month, rows, interval='1m'))
    manifest['files'].sort(key=lambda r: r['path'])
    repin_synthetic_manifest(root, manifest)
    issue_test_metadata(monkeypatch, repository=root, store=root / 'store',
                        artifacts=fixture_artifacts(root), common=tmp_path / 'common')
    original, opened = M.read_archive, []
    def watch(path, rel):
        assert '2025-04' not in rel
        opened.append(rel)
        return original(path, rel)
    monkeypatch.setattr(M, 'read_archive', watch)
    with AUTH.development_authority() as cap:
        source = DevelopmentArchive(cap)
        bars = source.klines('BTCUSDT')
        assert tuple(bars) == (END - H,) and bars[END - H].close == 100.
        assert bars[END - H].quote_volume == 60. and len(opened) == 1


def test_report_explicitly_pins_hourly_document_only_for_production_type(tmp_path, monkeypatch):
    # Report-provenance-only drill: no economic rows or pretend PASS. Full
    # replay/publication and source derivation are proved by the existing CLI.
    native = make_fixture(tmp_path / 'native', hours=1, members=('ALT00USDT', 'BTCUSDT'))
    minute = make_fixture(tmp_path / 'minute', hours=1, members=('ALT00USDT', 'BTCUSDT'), trade_interval='1m')
    monkeypatch.setattr(R.DevelopmentBook, 'run', lambda book: {
        **book.report(), 'data_complete': False, 'failures': ['SYNTHETIC_METADATA_DRILL_NO_ROWS']})
    monkeypatch.setattr(M, 'read_archive', lambda *args: pytest.fail('provenance drill read prices'))
    synthetic = R._replay_development(SyntheticArchive(native))
    assert synthetic['hourly_source_derivation'] is None
    assert synthetic['trade_bar_derivation'] == 'SYNTHETIC_NATIVE_1H_COMPONENT_FIXTURE'
    issue_test_metadata(monkeypatch, repository=minute, store=minute / 'store',
                        artifacts=fixture_artifacts(minute), common=tmp_path / 'common')
    with AUTH.development_authority() as cap:
        report = R._replay_development(DevelopmentArchive(cap))
    rel = 'contracts/c260_low_idio_variance/c260_hourly_source_derivation.v1.json'
    digest = hashlib.sha256((C.PREREG.parents[2] / rel).read_bytes()).hexdigest()
    assert report['hourly_source_derivation'] == {'path': rel, 'byte_sha256': digest}
    assert digest == AUTH.DOCUMENTS[rel]
    assert report['trade_bar_derivation'] == 'C260_DEV_TRADE_1M_EXACT60_TO_1H_V1'
    assert report['economic_development_verdict'] == 'DATA_INCOMPLETE'
    assert not report['paper_qualified'] and not report['promotable']


def first_ranking(source, day=31):
    d = START + day * C.DAY_MS
    wanted = range(d - 721 * H, d, H)
    return C.rank_supplied_closed_cohort(decision_ms=d, fold_start_ms=START,
        pit_filter_eligible_symbols=source.eligible(d),
        btc_bars=tuple(source.klines('BTCUSDT')[t].pack() for t in wanted),
        bars_by_symbol={s: tuple(source.klines(s)[t].pack() for t in wanted)
                        for s in source.eligible(d) if s != 'BTCUSDT'})


def admitted_book(source):
    book = R.DevelopmentBook(source, capital=2000)
    rank = first_ranking(source)
    book._admit(rank, rank.entry_due_ms)
    assert len(book.lots) == 1
    return book, next(iter(book.lots.values()))


def test_full_pit_order_and_typed_immutable_source_integrity(source):
    assert source.eligible(START) == MEMBERS and len(MEMBERS) > 20
    bars = source.klines('ALT00USDT')
    assert len(bars) == 90 * 24
    with pytest.raises(TypeError):
        bars[START] = bars[START]
    with pytest.raises(SourceIncomplete, match='MISSING_REQUIRED_STREAM'):
        source.klines('UNREGISTEREDUSDT')


def test_rank_walk_past_occupied_and_infeasible_no_rerank(source):
    book, lot = admitted_book(source)
    old = first_ranking(source)
    # Unit-test the book's rank walk with an explicitly fixed prepared order;
    # fit/ranking correctness is independently tested, not asserted by this tape.
    ranking = replace(old, decision_ms=old.decision_ms + C.DAY_MS,
                      entry_due_ms=old.entry_due_ms + C.DAY_MS,
                      scheduled_exit_ms=old.scheduled_exit_ms + C.DAY_MS)
    book._admit(ranking, ranking.entry_due_ms)
    assert len(book.lots) == 2
    attempts = [a for a in book.attempts if a.get('original_rank') is not None]
    assert [(a['original_rank'], a['reason']) for a in attempts[:3]] == [(1, 'ADMITTED'), (1, 'OCCUPIED'), (2, 'ADMITTED')]
    # A maximum violation is rejected, not clipped; lower ranks still work.
    other = R.DevelopmentBook(source, capital=2000)
    symbol = ranking.ranked[0].symbol
    original = source.filters.rules[symbol]['max_qty']
    source.filters.rules[symbol]['max_qty'] = .001
    try:
        other._admit(ranking, ranking.entry_due_ms)
        assert other.attempts[0]['reason'] == 'MAX_QTY_NO_CLIPPING'
        assert next(iter(other.lots.values())).original_rank == 2
    finally:
        source.filters.rules[symbol]['max_qty'] = original


def test_risk_gross_concurrent_and_diagnostic_isolation(source):
    book, lot = admitted_book(source)
    assert lot.initial_risk <= 20. and lot.position.initial_qty * lot.position.entry_exec <= 3000.
    ranking = first_ranking(source, 32)
    book._admit(ranking, ranking.entry_due_ms)
    ranking = first_ranking(source, 33)
    book._admit(ranking, ranking.entry_due_ms)
    assert len(book.lots) <= 3
    assert sum(l.initial_risk for l in book.lots.values()) <= .03 * book.equity() + 1e-8
    before = book.cash
    diag = R.DevelopmentBook(source, capital=4000)
    diag._admit(ranking, ranking.entry_due_ms)
    assert book.cash == before and diag.cash != book.cash


def test_stop_range_excludes_boundary_funding_gap_and_due_include_it(source):
    for phase, reason, included in [(0, 'STOP_RANGE', False), (2, 'STOP_GAP', True), (3, 'TIME_72H', True)]:
        book, lot = admitted_book(source)
        t = ((lot.entry_ms // (8 * H)) + 1) * 8 * H
        if included:
            book._funding(lot, t)
        book._close(lot, lot.position.stop if phase != 3 else lot.position.entry_reference, t, phase, reason)
        assert (book.closed[0]['funding_cash'] != 0) is included
        assert book.events[-1]['impact_asof_ms'] == t - (H if phase == 0 else 0)
        assert book.report()['cash_reconciled']
    book, lot = admitted_book(source)
    assert not [e for e in book.events if e['kind'] == 'funding']  # entry boundary is excluded


def test_missing_required_funding_or_mark_retains_known_costs_and_exposure(source):
    book, lot = admitted_book(source)
    boundary = ((lot.entry_ms // (8 * H)) + 1) * 8 * H
    rows = dict(source.funding(lot.symbol)); del rows[boundary]
    source._cache[lot.symbol, 'fundingRate'] = MappingProxyType(rows)
    with pytest.raises(SourceIncomplete, match='MISSING_REQUIRED_FUNDING'):
        book._funding(lot, boundary)
    report = book.report()
    assert report['known_open_cash'] < 0 and len(report['unresolved_exposures']) == 1
    assert report['cash_reconciled'] and not report['closed']


def test_marked_exposure_survives_funding_and_cost_phases(source):
    book, lot = admitted_book(source)
    lot.mark += 10
    equity = book.equity()
    t = ((lot.entry_ms // (8 * H)) + 1) * 8 * H
    book._point(t - H, 6)
    book._funding(lot, t)
    assert book.equity() > book.cash
    assert math.isclose(equity - book.equity(), -lot.funding, abs_tol=1e-8)
    assert book.equity_points[-1][2] > book.cash


def test_funding_uses_last_completed_raw_mark_not_an_extra_hour_old(source):
    book, lot = admitted_book(source)
    t = ((lot.entry_ms // (8 * H)) + 1) * 8 * H
    marks = dict(source.marks(lot.symbol))
    marks[t - 2 * H] = replace(marks[t - 2 * H], close=50.)
    marks[t - H] = replace(marks[t - H], close=150.)
    source._cache[lot.symbol, 'markPriceKlines'] = MappingProxyType(marks)
    rate = source.funding(lot.symbol)[t].rate
    book._funding(lot, t)
    assert lot.funding == -lot.position.initial_qty * 150. * rate
    assert book.events[-1]['mark_open_ms'] == t - H
    assert book.events[-1]['mark_available_ms'] == t


def test_overlapping_phase_exposures_use_completed_survivor_marks_and_no_post_stop_close(source):
    book, stopped = admitted_book(source)
    ranking = first_ranking(source, 32)
    book._admit(ranking, ranking.entry_due_ms)
    survivor = next(lot for lot in book.lots.values() if lot is not stopped)
    t = ((ranking.entry_due_ms // (8 * H)) + 1) * 8 * H
    for lot, value in ((stopped, 999.), (survivor, 200.)):
        marks = dict(source.marks(lot.symbol))
        marks[t - H] = replace(marks[t - H], close=value)
        source._cache[lot.symbol, 'markPriceKlines'] = MappingProxyType(marks)
    book._mark_phase_exposures(t, [(stopped, stopped.position.stop)])
    assert stopped.mark == stopped.position.stop and stopped.mark != 999.
    assert survivor.mark == 200.
    book._close(stopped, stopped.position.stop, t, 0, 'STOP_RANGE')
    unrealized = survivor.position.initial_qty * (200. - survivor.position.entry_reference)
    assert math.isclose(book.equity_points[-1][2], book.cash + unrealized, abs_tol=1e-8)
    book._funding(survivor, t)
    assert math.isclose(book.equity_points[-1][2], book.cash + unrealized, abs_tol=1e-8)


def test_duplicate_cohort_cannot_admit_again_and_noncanonical_time_refuses(source):
    book, lot = admitted_book(source)
    ranking = first_ranking(source)
    book._admit(ranking, ranking.entry_due_ms)
    assert len(book.lots) == 1 and book.attempts[-1]['reason'] == 'DUPLICATE_DECISION_REFUSED'
    with pytest.raises(SourceIncomplete, match='NONCANONICAL_ENTRY'):
        book._admit(ranking, ranking.entry_due_ms + 1)


def test_large_magnitudes_do_not_turn_relative_tolerance_into_grid_authority():
    rule = C.PACK.ExchangeRule(tick=.01, step=.001, min_qty=.001, min_notional=1., max_qty=1e9)
    R.DevelopmentBook._feasible(100000., 100., rule)
    with pytest.raises(SourceIncomplete, match='FILTER_GRID'):
        R.DevelopmentBook._feasible(100000.0005, 100., rule)
    with pytest.raises(SourceIncomplete, match='FILTER_GRID'):
        R.DevelopmentBook._grid(100.005, .01)


def test_missing_mark_after_exposure_fails_without_erasing_open_costs(source):
    book, lot = admitted_book(source)
    t = lot.entry_ms + H
    marks = dict(source.marks(lot.symbol)); del marks[t - H]
    source._cache[lot.symbol, 'markPriceKlines'] = MappingProxyType(marks)
    with pytest.raises(SourceIncomplete, match='MISSING_HELD_CAUSAL_MARK'):
        book._manage_boundary(t)
    assert book.report()['known_open_cash'] < 0 and len(book.lots) == 1


def test_entry_bar_poison_does_not_enter_admission_or_atr(source):
    ranking = first_ranking(source)
    a = R.DevelopmentBook(source, capital=2000); a._admit(ranking, ranking.entry_due_ms)
    symbol = next(iter(a.lots))
    bars = dict(source.klines(symbol)); original = bars[ranking.entry_due_ms]
    bars[ranking.entry_due_ms] = replace(original, high=original.high * 1000, low=.001, close=original.close * 500)
    source._cache[symbol, 'klines'] = MappingProxyType(bars)
    b = R.DevelopmentBook(source, capital=2000); b._admit(ranking, ranking.entry_due_ms)
    assert a.lots[symbol].initial_risk == b.lots[symbol].initial_risk
    assert a.lots[symbol].position.qty == b.lots[symbol].position.qty
    assert a.lots[symbol].position.stop == b.lots[symbol].position.stop


def test_actual_cli_archive_closed_loop_and_single_synthetic_spend(fixture_root, tmp_path):
    out = tmp_path / 'development.json'
    result = subprocess.run([sys.executable, '-m', 'tools.research.c260_runner', 'development',
                             '--synthetic-root', str(fixture_root), '--out', str(out)],
                            capture_output=True, text=True, timeout=180)
    assert result.returncode == 0, result.stderr
    report = json.loads(out.read_text())
    primary = report['primary']
    assert primary['data_complete'], primary['failures']
    assert 0 < primary['closed_count'] < 50 and not primary['unresolved_exposures']
    assert not report['development_gates']['sample']
    assert report['economic_development_verdict'] == 'REJECT'
    assert report['forward_state'] == 'VETOED_BEFORE_FORWARD_READ'
    assert primary['cash_reconciled'] and len(primary['hourly_equity']) == 90 * 24
    assert sum(primary['daily_closed_count']) == primary['closed_count']
    assert len(primary['daily_closed_count']) == 90
    assert set(t['reason'] for t in primary['closed']) == {'TIME_72H'}
    assert all(t['holding_hours'] == 72 for t in primary['closed'])
    assert any(e['kind'] == 'funding' for e in primary['events'])
    assert not report['paper_qualified'] and not report['execution_authorized']
    assert not report['report_acceptance_complete'] and report['synthetic_only']
    assert report['historical_global_variant_total'] == 'UNKNOWN'
    assert all(read['end_ms'] == END for read in report['source_reads'])
    assert len(report['diagnostic_books']) == 3 and report['stress']['stress']
    duplicate = subprocess.run([sys.executable, '-m', 'tools.research.c260_runner', 'development',
                                '--synthetic-root', str(fixture_root), '--out', str(tmp_path / 'duplicate.json')],
                               capture_output=True, text=True, timeout=30)
    assert duplicate.returncode != 0 and 'FileExistsError' in duplicate.stderr


def test_real_cli_missing_receipt_is_locked_before_source_or_claim(monkeypatch, tmp_path):
    monkeypatch.setattr(AUTH, '_git', lambda *args: '' if args == ('status', '--porcelain') else 'a' * 40)
    monkeypatch.setattr(AUTH, '_tree', lambda *args: (_ for _ in ()).throw(C.C260Locked('PROTECTED_DOCUMENT_ABSENT')))
    original = Path.open
    out = tmp_path / 'unused.json'
    def guarded(path, *args, **kwargs):
        if path in (C.PREREG, out):
            return original(path, *args, **kwargs)
        pytest.fail('unauthorized source/identity I/O')
    monkeypatch.setattr(Path, 'open', guarded)
    with pytest.raises(C.C260Locked, match='PROTECTED_DOCUMENT_ABSENT'):
        R.main(['development', '--out', str(out)])
    assert not out.exists()


def test_raw_archive_integrity_failure_does_not_become_skip(source):
    record = next(r for r in source.manifest['files'] if r['series'] == 'klines')
    bad = {**record, 'sha256': '0' * 64}
    with pytest.raises(SourceIncomplete, match='SOURCE_CHECKSUM'):
        source._selected_rows(bad, START, END)


def test_source_timestamp_clip_precedes_economic_parse(source, tmp_path):
    rel = 'um/monthly/klines/ALT00USDT/1h/ALT00USDT-1h-2025-01.zip'
    record = next(r for r in source.manifest['files'] if r['path'] == rel)
    raw, _, data = M.read_archive(str(source.store / rel), rel)
    lines = data.splitlines()
    poisoned = lines[-1].split(b','); poisoned[1] = b'NOT_A_PRICE'
    lines[-1] = b','.join(poisoned)
    isolated = tmp_path / 'store' / rel; isolated.parent.mkdir(parents=True)
    with zipfile.ZipFile(isolated, 'w') as z:
        z.writestr(isolated.name[:-4] + '.csv', b'\n'.join(lines) + b'\n')
    payload = isolated.read_bytes(); digest = hashlib.sha256(payload).hexdigest()
    isolated.with_suffix('.zip.ok').write_text(digest)
    source.store = tmp_path / 'store'
    clipped, _ = source._selected_rows({**record, 'sha256': digest, 'bytes': len(payload)}, START, START + H)
    assert len(clipped) == 1 and float(clipped[0][1]) > 0


def test_72h_due_open_precedes_current_range_and_gap_precedes_due(source):
    for gap, expected in ((False, 'TIME_72H'), (True, 'STOP_GAP')):
        book, lot = admitted_book(source)
        due = lot.due_ms
        bars = dict(source.klines(lot.symbol)); raw = bars[due]
        # A due bar's later low touches the stop; actual execution must already
        # be flat at the due open. A true gap through the stop takes precedence.
        bars[due] = replace(raw, open=lot.position.stop - 1 if gap else raw.open,
                            low=lot.position.stop - 2)
        source._cache[lot.symbol, 'klines'] = MappingProxyType(bars)
        book._manage_boundary(due)
        assert book.closed[0]['exit_ms'] == due and book.closed[0]['reason'] == expected
        assert lot.position.target is None and lot.position.tp1 is None


def test_adverse_trigger_is_exact_floor_and_risk_uses_that_trigger(source):
    from decimal import Decimal, ROUND_FLOOR
    book, lot = admitted_book(source)
    attempt = next(a for a in book.attempts if a['reason'] == 'ADMITTED')
    raw_stop = lot.position.entry_exec - 2 * attempt['atr']
    tick = Decimal(str(lot.rule.tick))
    expected = float((Decimal(str(raw_stop)) / tick).to_integral_value(rounding=ROUND_FLOOR) * tick)
    assert lot.position.stop == expected <= raw_stop
    assert lot.position.stop < source.marks(lot.symbol)[lot.entry_ms - H].close
    assert lot.initial_risk <= .01 * attempt['equity_before']
    R.DevelopmentBook._grid(lot.position.stop, lot.rule.tick)


def test_bounded_september_rest_page_uses_actual_query_not_calendar_month_end(source, tmp_path):
    symbol = 'ALT00USDT'
    first = milliseconds('2026-09-27T00:00:00Z')
    rel = f'um/api/fundingRate/{symbol}/{symbol}-fundingRate-2026-09-01-2026-09-30-p000.json'
    path = tmp_path / rel; path.parent.mkdir(parents=True)
    payload = [{'symbol': symbol, 'fundingTime': first, 'fundingRate': '0.001'},
               {'symbol': symbol, 'fundingTime': first + 8 * H, 'fundingRate': '0.002'}]
    raw = M.canonical(payload); path.write_bytes(raw)
    digest = hashlib.sha256(raw).hexdigest()
    Path(str(path) + '.ok').write_text(digest)
    url = f'https://fapi.binance.com/fapi/v1/fundingRate?symbol={symbol}&startTime={first}&endTime={first+8*H}&limit=1000'
    provenance = {'url': url, 'response_sha256': digest, 'accessed_cairo': 'synthetic',
                  'server_time_ms': first + 9 * H, 'rows': 2, 'first_funding_time_ms': first,
                  'last_funding_time_ms': first + 8 * H, 'terminal_page': True}
    provenance_sha = write_json(Path(str(path) + '.provenance.json'), provenance)
    source.store = tmp_path
    record = {'path': rel, 'sha256': digest, 'bytes': len(raw), 'rows': 2,
              'first_open_ms': first, 'last_open_ms': first + 8 * H,
              'source': {'kind': M.FUNDING_API_KIND, 'url': url, 'archive_checksum': digest,
                         'provenance_path': rel + '.provenance.json', 'provenance_sha256': provenance_sha}}
    rows, zipped = source._selected_rows(record, first, first + 9 * H)
    assert not zipped and rows == payload and M.archive_meta(rel)['month_end_ms'] > first + 9 * H
    bad = {**record, 'sha256': '0' * 64}
    with pytest.raises(SourceIncomplete, match='SOURCE_CHECKSUM_BEFORE_PARSE'):
        source._selected_rows(bad, first, first + 9 * H)


def test_causal_entry_mark_must_allow_placeable_stop(source):
    ranking = first_ranking(source)
    symbol = ranking.ranked[0].symbol
    marks = dict(source.marks(symbol))
    marks[ranking.entry_due_ms - H] = replace(marks[ranking.entry_due_ms - H], close=1.)
    source._cache[symbol, 'markPriceKlines'] = MappingProxyType(marks)
    book = R.DevelopmentBook(source, capital=2000)
    book._admit(ranking, ranking.entry_due_ms)
    assert book.attempts[0]['reason'] == 'PROTECTION_COLLAPSED'
    assert symbol not in book.lots


def test_pre_entry_off_tick_skips_in_original_rank_but_exposed_exit_fails(source):
    ranking = first_ranking(source)
    symbol = ranking.ranked[0].symbol
    bars = dict(source.klines(symbol)); row = bars[ranking.entry_due_ms]
    bars[ranking.entry_due_ms] = replace(row, open=row.open + .005)
    source._cache[symbol, 'klines'] = MappingProxyType(bars)
    book = R.DevelopmentBook(source, capital=2000)
    book._admit(ranking, ranking.entry_due_ms)
    assert book.attempts[0]['reason'] == 'ENTRY_PRICE_OFF_TICK'
    assert next(iter(book.lots.values())).original_rank == 2
    lot = next(iter(book.lots.values()))
    with pytest.raises(SourceIncomplete, match='FILTER_GRID'):
        book._close(lot, 100.005, lot.due_ms, 3, 'TIME_72H')
    assert lot.symbol in book.lots and book.report()['unresolved_exposures']


def test_issued_factory_actual_cli_uses_same_checksum_book_and_shared_single_spend(minute_fixture_root, tmp_path, monkeypatch):
    fixture_root = minute_fixture_root
    issue_test_metadata(monkeypatch, repository=fixture_root, store=fixture_root / 'store',
                        artifacts=fixture_artifacts(fixture_root), common=tmp_path / 'common')
    # The production path must never read or relabel a synthetic receipt.
    original = Path.read_bytes
    def guarded(path):
        if path.name == 'c260.synthetic.json':
            pytest.fail('production path relabeled a fixture receipt')
        return original(path)
    monkeypatch.setattr(Path, 'read_bytes', guarded)
    original_open = Path.open
    published = []
    def check_publication(path, *args, **kwargs):
        if path.name == 'candidate-260-development.report.json':
            cap, = AUTH._ISSUED.values()
            AUTH.validate_issued(cap)
            published.append(path)
        return original_open(path, *args, **kwargs)
    monkeypatch.setattr(Path, 'open', check_publication)
    out = tmp_path / 'issued-development.json'
    assert R.main(['development', '--out', str(out)]) == 0
    report = json.loads(out.read_text())
    assert not report['synthetic_only'] and report['development_source_authorized']
    assert report['format'] == 'zb-c260-development-research/1'
    assert report['trade_bar_derivation'] == 'C260_DEV_TRADE_1M_EXACT60_TO_1H_V1'
    assert report['period_coverage_is_not_native_trade_1h_proof']
    assert report['development_authority']['implementation_head'] == 'a' * 40
    assert report['economic_development_verdict'] == 'REJECT'
    assert report['forward_state'] == 'VETOED_BEFORE_FORWARD_READ'
    assert report['primary']['data_complete'] and report['primary']['cash_reconciled']
    assert report['primary']['closed_count'] > 0 and all(row['end_ms'] == END for row in report['source_reads'])
    assert not report['report_acceptance_complete'] and not report['paper_qualified'] and not report['promotable']
    assert len(published) == 1 and not AUTH._ISSUED
    canonical = Path(report['canonical_report_path'])
    assert canonical == tmp_path / 'common/c260-evaluation/candidate-260-development.report.json'
    # Opening the canonical record after the context is itself allowed; only
    # the publication, intercepted above, must happen while authority is live.
    monkeypatch.setattr(Path, 'open', original_open)
    assert canonical.read_bytes() == out.read_bytes()
    with pytest.raises(C.C260Locked, match='ALREADY_SPENT'):
        R.main(['development', '--out', str(tmp_path / 'duplicate.json')])


@pytest.mark.parametrize('fake', [None, True, False, {}, 'fixture'])
def test_production_factory_requires_actual_issued_object_before_artifact_access(fake, monkeypatch):
    monkeypatch.setattr(Path, 'read_bytes', lambda *args: pytest.fail('artifact/identity access'))
    with pytest.raises(C.C260Locked, match='ISSUED'):
        DevelopmentArchive(fake)


def test_issued_source_expiry_copy_scope_and_cache_cannot_reopen(minute_fixture_root, tmp_path, monkeypatch):
    fixture_root = minute_fixture_root
    issue_test_metadata(monkeypatch, repository=fixture_root, store=fixture_root / 'store',
                        artifacts=fixture_artifacts(fixture_root), common=tmp_path / 'common')
    with AUTH.development_authority() as cap:
        with pytest.raises(C.C260Locked, match='ISSUED'):
            DevelopmentArchive(replace(cap))
        source = DevelopmentArchive(cap)
        assert source.klines('BTCUSDT')
        with pytest.raises(C.C260Locked, match='FIXED_DEVELOPMENT_SOURCE_RECORD'):
            source._selected_rows(source.manifest['files'][0], END, END + H)
        source.end_ms += H
        with pytest.raises(C.C260Locked, match='SCOPE_CHANGED'):
            source.klines('BTCUSDT')  # already cached, still must revalidate
        source.end_ms = END
    with pytest.raises(C.C260Locked, match='ISSUED'):
        source.klines('BTCUSDT')
    with pytest.raises(C.C260Locked, match='ISSUED'):
        source.eligible(START)
    with pytest.raises(C.C260Locked, match='ISSUED'):
        R.DevelopmentBook(source, capital=2000)


def test_production_metadata_and_issued_cached_rows_cannot_mutate_or_be_replaced(minute_fixture_root, tmp_path, monkeypatch):
    fixture_root = minute_fixture_root
    issue_test_metadata(monkeypatch, repository=fixture_root, store=fixture_root / 'store',
                        artifacts=fixture_artifacts(fixture_root), common=tmp_path / 'common')
    with AUTH.development_authority() as cap:
        source = DevelopmentArchive(cap)
        with pytest.raises(TypeError):
            source.manifest['files'][0]['sha256'] = '0' * 64
        with pytest.raises(AttributeError):
            source.pit['weeks'][0]['books']['crypto']['members'].append('FAKEUSDT')
        symbol = source.eligible(START)[0]
        with pytest.raises(TypeError):
            source.filters.rules[symbol]['tick'] = 1.
        rows = source.klines('BTCUSDT')
        with pytest.raises(TypeError):
            rows[START] = rows[START]
        from dataclasses import FrozenInstanceError
        with pytest.raises(FrozenInstanceError):
            rows[START].close = 1.
        for name in ('manifest', 'pit', 'filters', 'digests', '_cache'):
            original = getattr(source, name)
            setattr(source, name, {})
            with pytest.raises(C.C260Locked, match='METADATA_REPLACED'):
                source.eligible(START)
            setattr(source, name, original)
        source._cache['BTCUSDT', 'klines'] = MappingProxyType({})
        with pytest.raises(C.C260Locked, match='STREAM_REPLACED'):
            source.klines('BTCUSDT')


@pytest.mark.parametrize('attack', ['existing', 'missing_parent'])
def test_cli_preflights_export_before_authority_or_one_shot_claim(tmp_path, monkeypatch, attack):
    out = tmp_path / 'result.json' if attack == 'existing' else tmp_path / 'absent/result.json'
    if attack == 'existing':
        out.write_bytes(b'previous record')
    monkeypatch.setattr(AUTH, 'development_authority', lambda: pytest.fail('claim reached before export preflight'))
    with pytest.raises(OSError):
        R.main(['development', '--out', str(out)])
    if attack == 'existing':
        assert out.read_bytes() == b'previous record'


def test_export_failure_preserves_canonical_record_published_inside_authority(minute_fixture_root, tmp_path, monkeypatch):
    fixture_root = minute_fixture_root
    issue_test_metadata(monkeypatch, repository=fixture_root, store=fixture_root / 'store',
                        artifacts=fixture_artifacts(fixture_root), common=tmp_path / 'common')
    # Publication-only failure drill. The full economic replay and its exact
    # canonical record are exercised separately above; no fake economic PASS.
    monkeypatch.setattr(R, '_replay_development', lambda source: {
        'economic_development_verdict': 'NOT_EVALUATED_SYNTHETIC_PUBLICATION_DRILL',
        'synthetic_only': True, 'report_acceptance_complete': False, 'paper_qualified': False,
        'promotable': False, 'execution_authorized': False})
    monkeypatch.setattr(M, 'read_archive', lambda *args: pytest.fail('publication drill read prices'))
    out = tmp_path / 'export.json'
    original_open = Path.open
    attempts = 0
    def fail_export(path, *args, **kwargs):
        nonlocal attempts
        if path == out:
            attempts += 1
            if attempts == 2:
                raise PermissionError('synthetic post-preflight export failure')
        return original_open(path, *args, **kwargs)
    monkeypatch.setattr(Path, 'open', fail_export)
    with pytest.raises(PermissionError, match='post-preflight'):
        R.main(['development', '--out', str(out)])
    canonical = tmp_path / 'common/c260-evaluation/candidate-260-development.report.json'
    record = json.loads(canonical.read_bytes())
    assert record['economic_development_verdict'] == 'NOT_EVALUATED_SYNTHETIC_PUBLICATION_DRILL'
    assert not record['report_acceptance_complete'] and not record['paper_qualified'] and not AUTH._ISSUED
    with pytest.raises(C.C260Locked, match='ALREADY_SPENT'):
        with AUTH.development_authority():
            pytest.fail('export failure refunded the candidate')


@pytest.mark.parametrize('stage', ['before_entry', 'after_exposure'])
def test_source_file_oserror_is_incomplete_and_preserves_known_exposure(source, monkeypatch, stage):
    book = R.DevelopmentBook(source, capital=2000)
    original = source.klines
    def missing(symbol):
        if stage == 'before_entry' or book.lots:
            raise FileNotFoundError('synthetic missing raw cache')
        return original(symbol)
    monkeypatch.setattr(source, 'klines', missing)
    report = book.run()
    assert not report['data_complete'] and report['cash_reconciled']
    assert report['failures'] == ['synthetic missing raw cache']
    assert report['closed_count'] == 0
    if stage == 'after_exposure':
        assert report['known_open_cash'] < 0 and report['settled_cash'] < book.capital
        assert len(report['unresolved_exposures']) == 1
        assert report['unresolved_exposures'][0]['known_cost_cash'] > 0
        assert any(attempt.get('reason') == 'ADMITTED' for attempt in report['attempts'])
    else:
        assert report['known_open_cash'] == 0 and report['settled_cash'] == book.capital
        assert report['unresolved_exposures'] == []


def test_missing_raw_cache_cli_persists_canonical_data_incomplete_before_revoking_cap(minute_fixture_root, tmp_path, monkeypatch):
    fixture_root = minute_fixture_root
    issue_test_metadata(monkeypatch, repository=fixture_root, store=fixture_root / 'store',
                        artifacts=fixture_artifacts(fixture_root), common=tmp_path / 'common')
    monkeypatch.setattr(M, 'read_archive', lambda *args: (_ for _ in ()).throw(
        FileNotFoundError('synthetic missing checksum ZIP')))
    out = tmp_path / 'incomplete.json'
    assert R.main(['development', '--out', str(out)]) == 0
    report = json.loads(out.read_bytes())
    assert report['economic_development_verdict'] == 'DATA_INCOMPLETE'
    assert report['forward_state'] == 'VETOED_BEFORE_FORWARD_READ'
    assert not report['development_gates']['data_complete']
    assert report['primary']['failures'] == ['synthetic missing checksum ZIP']
    assert report['primary']['cash_reconciled'] and not report['paper_qualified']
    assert Path(report['canonical_report_path']).read_bytes() == out.read_bytes()
    assert not AUTH._ISSUED


def test_actual_frozen_v3_wf_only_metadata_derives_development_without_market_or_identity_reads(tmp_path, monkeypatch):
    repository = C.PREREG.parents[2]
    proposal = C.load_proposal_metadata()
    pins = proposal['stage_a_authority_proposal']
    artifacts = tuple((name, pins[name]['path'], pins[name]['byte_sha256'],
                       (repository / pins[name]['path']).read_bytes())
                      for name in ('manifest', 'pit', 'filters', 'coverage'))
    filters = json.loads(next(raw for name, _, _, raw in artifacts if name == 'filters'))
    assert set(filters['fold_bindings']) == {'wf1', 'wf2', 'wf3'} and 'development' not in filters['weekly_membership_sha256']
    store = tmp_path / 'empty-store'; store.mkdir()
    issue_test_metadata(monkeypatch, repository=repository, store=store, artifacts=artifacts, common=tmp_path / 'common')
    monkeypatch.setattr(M, 'read_archive', lambda *args: pytest.fail('real archive read'))
    with AUTH.development_authority() as cap:
        source = DevelopmentArchive(cap)
        assert source.filters.captured_at == '2024-12-13T04:44:48Z'
        expected = next(w for w in reversed(source.pit['weeks']) if w['monday_ms'] <= START)['books']['crypto']['members']
        assert source.eligible(START) == tuple(s for s in expected if s in source.filters.eligible_symbols)
        assert source.source_reads == [] and list(store.iterdir()) == []
        # Metadata/grid only. The old v3 period profile's logical1h request did
        # not establish that native hourly trade files actually existed.
        active = next(w for w in reversed(source.pit['weeks']) if w['monday_ms'] <= START)
        weeks = [active, *(w for w in source.pit['weeks'] if START < w['monday_ms'] < END)]
        required = {'BTCUSDT'} | {s for w in weeks for s in w['books']['crypto']['members']
                                  if s in source.filters.eligible_symbols}
        native = [record for record in source.manifest['files'] if record['symbol'] in required
                  and record['series'] == 'klines' and record['interval'] == '1h' and record['rows']
                  and record['last_open_ms'] >= START and record['first_open_ms'] < END]
        assert len(required) == 63 and native == []
        selected = []
        def intercept(record, start, end):
            assert (start, end) == (START, END)
            selected.append(record)
            return (), True  # stop before ANY raw price/cache byte read
        monkeypatch.setattr(source, '_selected_rows', intercept)
        for symbol in required:
            records = source._records(symbol, 'klines')
            assert len(records) == 3 and all(record['interval'] == '1m' for record in records)
            assert min(record['first_open_ms'] for record in records) == START
            assert max(record['last_open_ms'] for record in records) == END - MINUTE
            assert sum(record['rows'] for record in records) == 90 * 1440
            # Exercise the actual production stream route, not only its filter
            # helper. The former native1h assumption would visit zero paths.
            with pytest.raises(SourceIncomplete, match='MISSING_REQUIRED_STREAM'):
                source.klines(symbol)
        assert len(selected) == 189 and sum(record['rows'] for record in selected) == 8_164_800
        assert all(record['interval'] == '1m' and record['series'] == 'klines' for record in selected)
        assert source.source_reads == [] and list(store.iterdir()) == []


@pytest.mark.parametrize('attack', ['equal_capture', 'future_capture', 'equal_server', 'future_server', 'snapshot_digest'])
def test_development_filter_snapshot_never_uses_equal_future_or_changed_evidence(fixture_root, tmp_path, monkeypatch, attack):
    root = tmp_path / 'metadata'; root.mkdir()
    for path in fixture_root.glob('*.json'):
        (root / path.name).write_bytes(path.read_bytes())
    filters = json.loads((root / 'filters.json').read_bytes())
    previous = json.loads((root / 'filters.v2.json').read_bytes())
    if attack == 'snapshot_digest':
        (root / 'snapshot.json').write_bytes(b'changed payload')
    else:
        key = 'captured_at' if 'capture' in attack else 'server_time'
        value = '2025-01-01T00:00:00Z' if attack.startswith('equal') else '2025-01-02T00:00:00Z'
        for doc in (filters, previous):
            doc['snapshots'][0][key] = value
        filters['supersedes']['byte_sha256'] = write_json(root / 'filters.v2.json', previous)
        digest = write_json(root / 'filters.json', filters)
        receipt = json.loads((root / 'c260.synthetic.json').read_bytes())
        receipt['artifacts']['filters']['byte_sha256'] = digest
        write_json(root / 'c260.synthetic.json', receipt)
    monkeypatch.setattr(M, 'read_archive', lambda *args: pytest.fail('price read before snapshot validation'))
    with pytest.raises(SourceIncomplete, match='FILTER_SNAPSHOT_CHECKSUM|NO_STRICTLY_PRE_DEVELOPMENT'):
        SyntheticArchive(root)
