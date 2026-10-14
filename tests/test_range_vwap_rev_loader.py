"""REX-02 loader of range_vwap_rev.v1 (Codex ruling #81 6172019060 point 2): the attested `zb-data-manifest/1` ->
StaticDataset -> ledger-recorded `pit.Access.open_development` (distinct range_vwap_rev family ledger, lineage root) ->
a tape bounded to the access window (funding: + the documented 60 s tail) -> pit_run.run_rex, end to end through
`run_r4 rex --store`. SYNTHETIC archive store, manifest, universe, rules and notice artifact in tmp dirs only: no market
data, no network, no committed ledger is touched (pit_run.rex_sources is replaced by the synthetic sources)."""
import hashlib
import io
import json
import math
import os
import sys
import zipfile
from datetime import datetime, timezone

import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, 'tools', 'research'))
import ledger as L                                                                          # noqa: E402
import manifest as M                                                                        # noqa: E402
import pit as P                                                                             # noqa: E402
import pit_run as PR                                                                        # noqa: E402
import progress as PG                                                                       # noqa: E402
import range_vwap_rev as RV                                                                 # noqa: E402
import range_vwap_rev_artifact as NA                                                        # noqa: E402
import range_vwap_rev_notice as NT                                                          # noqa: E402
import run_r4 as G                                                                          # noqa: E402
import splits as S                                                                          # noqa: E402
import static_universe as SU                                                                # noqa: E402

HOUR, TF, DAY = RV.HOUR, RV.TF, 86_400_000
SYMS = ('AAAUSDT', 'BBBUSDT')
MONTHS = [(2024, m) for m in range(1, 5)]            # the store holds rows on both sides of the window
START = S.parse_utc('2024-01-01T00:00:00Z')
LO, HI = S.parse_utc('2024-02-05T00:00:00Z'), S.parse_utc('2024-03-25T00:00:00Z')
WINDOW = f'{S.utc(LO)}/{S.utc(HI)}'
RULES = {s: {'tick': 0.0001, 'step': 0.001, 'min_qty': 0.001, 'min_notional': 5.0} for s in SYMS}


def price(k, x):
    return 100.0 * (k + 1) * math.exp(0.08 * math.sin(x / (5.0 + 2 * k)) + 0.02 * math.sin(x / 2.3 + k))


def kline(k, t, step):
    x0, x1 = (t - START) / TF, (t + step - START) / TF
    o, c = round(price(k, x0), 4), round(price(k, x1), 4)
    return f'{t},{o},{round(max(o, c) * 1.004, 4)},{round(min(o, c) * 0.996, 4)},{c},10,{t + step - 1},' \
           f'{1000.0 + (t // step) % 7},5,4,1,0'


def put(store, rel, lines):
    p = os.path.join(store, *rel.split('/'))
    os.makedirs(os.path.dirname(p), exist_ok=True)
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, 'w') as z:
        z.writestr(rel.rsplit('/', 1)[1][:-4] + '.csv', '\n'.join(lines) + '\n')
    raw = buf.getvalue()
    with open(p, 'wb') as f:
        f.write(raw)
    with open(p + '.ok', 'w') as f:
        f.write(hashlib.sha256(raw).hexdigest())


def month_range(y, m):
    a = int(datetime(y, m, 1, tzinfo=timezone.utc).timestamp() * 1000)
    return a, int(datetime(y + m // 12, m % 12 + 1, 1, tzinfo=timezone.utc).timestamp() * 1000)


def build_store(store):
    for k, sym in enumerate(SYMS):
        for y, m in MONTHS:
            a, b = month_range(y, m)
            tag = f'{y}-{m:02d}'
            put(store, f'um/monthly/klines/{sym}/4h/{sym}-4h-{tag}.zip', [kline(k, t, TF) for t in range(a, b, TF)])
            put(store, f'um/monthly/markPriceKlines/{sym}/1h/{sym}-1h-{tag}.zip',
                [kline(k, t, HOUR) for t in range(a, b, HOUR)])
            put(store, f'um/monthly/fundingRate/{sym}/{sym}-fundingRate-{tag}.zip',
                ['calc_time,funding_interval_hours,last_funding_rate'] +
                [f'{t + 3},8,{0.0001 * (1 + k)}' for t in range(a, b, 8 * HOUR)])
    return M.build(['um/monthly'], manifest_id='fx-rex02', source_class='archive-verified', base=store,
                   data_root='binance_um', survivor_only=False, loader=M.ARCHIVE_LOADER)


def static():
    s = [{'symbol': x, 'rank': i + 1} for i, x in enumerate(SYMS)]
    return {'format': SU.FORMAT, 'label': SU.LABEL, 'owner_approved': True, 'symbols': s,
            'core_10': list(SYMS), 'core_20': list(SYMS),
            'digest': hashlib.sha256(json.dumps(SYMS).encode()).hexdigest()}


def artifact():
    old = {'code': 'old', 'title': 'Binance Futures Will Launch USDⓈ-M ZZZUSDT Perpetual Contract',
           'releaseDate': LO - 60 * DAY, 'url': 'https://www.binance.com/en/support/announcement/old'}
    f = {'fetch_ts': HI + HOUR, 'ok': True, 'schema': NT.SCHEMA, 'raw_digest': '1' * 64, 'items': [old]}
    return NA.build(list(SYMS), [f], {}, covers_from_ms=LO - 60 * DAY)


@pytest.fixture
def fx(tmp_path):
    store = str(tmp_path / 'store')
    man = build_store(store)
    art = artifact()
    notice = str(tmp_path / 'notice.json')
    with open(notice, 'w', newline='\n') as f:
        json.dump(art, f)
    ledger = str(tmp_path / 'ledger' / 'range_vwap_rev.jsonl')
    os.makedirs(os.path.dirname(ledger))
    src = {'manifest': man, 'static': static(), 'rules': RULES, 'ledger': ledger}
    return {'store': store, 'man': man, 'art': art, 'notice': notice, 'ledger': ledger, 'src': src,
            'out': str(tmp_path / 'out')}


def load(fx, **kw):
    a = dict(repo=ROOT, store=fx['store'], tier='CORE-10', window=WINDOW, notice_path=fx['notice'],
             notice_sha256=fx['art']['digest'], author='t', cairo_date='2026-10-14', plan=PR.rex_plan(ROOT),
             commit='b' * 40)
    a.update(kw)
    return PR.rex_load(fx['src'], **a)


def records(ledger):
    return L._check_state(ledger)[RV.FAMILY]


def test_loader_binds_the_attested_manifest_through_a_development_access(fx):
    e = load(fx)
    assert e['symbols'] == list(SYMS) and e['notice_sha256'] == fx['art']['digest']
    (r,) = records(fx['ledger'])                      # the access is ledgered before any row is returned
    assert (r['kind'], r['split'], r['candidate_id']) == ('data_access', 'development', RV.CANDIDATE)
    assert r['window'] == {'start': S.utc(LO), 'end': S.utc(HI)} and r['manifest_digest'] == fx['man']['digest']
    assert r['detail']['promotable'] is False and r['detail']['notice_artifact'] == fx['art']['digest']
    assert e['manifest']['source'] == {'data_manifest': fx['man']['digest'], 'universe': static()['digest'],
                                       'loader': M.ARCHIVE_LOADER}
    assert e['manifest']['rules'] == RULES
    for s in SYMS:                                    # F2: bounded to the window although the store holds more
        x = e['tape'][s]
        assert min(x.bars) == LO and max(b.available_ms for b in x.bars.values()) == HI
        assert len(x.bars) == (HI - LO) // TF
        assert min(x.marks) == LO and max(x.marks) == HI - HOUR
        assert min(r.time_ms for r in x.funding) >= LO
        assert max(r.available_ms for r in x.funding) == HI + 3     # the documented tail: the END event's calc_time
        assert all(b.quote_volume is not None for b in x.bars.values())
    assert PR.rex_preflight(e['manifest'], e['tape'], e['notice_artifact'], symbols=e['symbols'], window=(LO, HI),
                            artifact_sha=e['notice_sha256']) == []


def test_cli_rex_runs_end_to_end_on_a_synthetic_store(fx, monkeypatch, capsys):
    monkeypatch.setattr(PR, 'rex_sources', lambda repo: fx['src'])
    rc = G.main(['rex', '--window', WINDOW, '--tier', 'CORE-10', '--out-dir', fx['out'], '--store', fx['store'],
                 '--notice', fx['notice'], '--notice-sha256', fx['art']['digest'], '--author', 't',
                 '--cairo-date', '2026-10-14'])
    out = capsys.readouterr()
    assert rc in (0, 4), out.err                      # never 3 once an attested store is bound
    final = out.out.strip().splitlines()[-1]
    doc = PG.read(final)
    assert doc['state'] == 'sealed'
    summary = json.load(open(os.path.join(os.path.dirname(final), f'summary-{doc["run"]}.json')))
    assert (rc == 0) == (summary['status'] == RV.COMPLETE) and summary['promotable'] is False
    assert summary['window'] == {'start': S.utc(LO), 'end': S.utc(HI)}
    assert doc['identity']['universe'] == static()['digest']
    recs = records(fx['ledger'])
    assert [r['kind'] for r in recs] == ['data_access'] + (['variant'] * 9 + ['baseline']) * 2
    assert all(r['split'] == 'development' and r['window'] == recs[0]['window'] for r in recs)
    assert all(r['detail']['promotable'] is False and r['detail']['run'] == doc['run'] for r in recs[1:])
    assert {(r['detail']['side'], r['detail']['config']) for r in recs[1:]} == {
        (s, c['name']) for s in RV.SIDES for c in PR.rex_configs()}
    assert L.n_trials(recs) == 20
    reg = [json.loads(x) for x in open(os.path.join(os.path.dirname(fx['ledger']), L.REGISTRY))]
    assert [(r['kind'], r['family'], r['ref']['parent']) for r in reg] == [('family', RV.FAMILY, None)]


def test_cli_without_a_store_still_refuses(fx, capsys):
    assert G.main(['rex', '--window', WINDOW, '--tier', 'CORE-10', '--out-dir', fx['out']]) == 3
    assert not os.path.exists(fx['out']) and not os.path.exists(fx['ledger'])


@pytest.mark.parametrize('kw, want', [
    ({'notice_sha256': 'e' * 64}, 'not the artifact the run command pinned'),
    ({'window': '2020-12-28T00:00:00Z/2021-03-01T00:00:00Z'}, 'calibration'),
    ({'window': '2026-09-01T00:00:00Z/2026-10-05T00:00:00Z'}, 'sealed holdout'),
    ({'tier': 'EXT'}, 'tier'),
])
def test_loader_refusals_open_nothing(fx, kw, want):
    with pytest.raises(PR.PitRunError, match=want):
        load(fx, **kw)
    assert not os.path.exists(fx['ledger'])


def test_loader_refuses_another_family_ledger_or_one_with_a_holdout_record(fx, tmp_path):
    with pytest.raises(PR.PitRunError, match='distinct range_vwap_rev family ledger'):
        load(dict(fx, src=dict(fx['src'], ledger=str(tmp_path / 'ledger' / 'trend_ema_mom.jsonl'))))
    L.append(fx['ledger'], kind='window_spent', candidate_id=RV.CANDIDATE, split='holdout',
             window={'start': '2026-10-19T00:00:00Z', 'end': '2027-10-18T00:00:00Z'}, author='t',
             cairo_date='2026-10-14', detail={}, lineage='root')
    with pytest.raises(PR.PitRunError, match='holdout record'):
        load(fx)
    assert [r['kind'] for r in records(fx['ledger'])] == ['window_spent']     # no access was recorded


def test_a_tampered_store_file_is_refused_by_the_manifest_recheck(fx):
    p = os.path.join(fx['store'], 'um', 'monthly', 'klines', 'AAAUSDT', '4h', 'AAAUSDT-4h-2024-02.zip')
    with open(p, 'ab') as f:
        f.write(b'\0')
    with pytest.raises((P.PITError, M.ManifestError), match='checksum|no longer match'):
        load(fx)
