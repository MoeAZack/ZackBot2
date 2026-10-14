"""REX-02 binding of range_vwap_rev.v1 into pit_run / run_r4: the fail-closed input preflight (4h klines with
quote_volume, funding rows, 1h funding marks, the attested zb-delist-notice/1 register), the explicit development window
(no default, refused on / after the sealed cutoff of the committed split plan) and an end-to-end run on a synthetic tape
producing the STRAT-style provenance (progress snapshot, sealed final, summary / detail artifacts).
Synthetic tapes only (rex_synth, shifted 52 weeks back so they lie inside the development period); no market data."""
import json
import os
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import rex_synth as RS                                                                      # noqa: E402

ROOT = RS.ROOT
import pit as P                                                                             # noqa: E402
import pit_run as PR                                                                        # noqa: E402
import progress as PG                                                                       # noqa: E402
import range_vwap_rev_notice as NT                                                          # noqa: E402
import run_r4 as G                                                                          # noqa: E402
import splits as S                                                                          # noqa: E402

RV = RS.RV
SHIFT = 52 * 7 * 86_400_000                          # whole weeks: Monday / funding grid / UTC-day alignment kept
N_BARS = 16
LO = RS.ms(0) - SHIFT                                # 2025-11-17T00:00Z
HI = RS.ms(N_BARS) - SHIFT
WINDOW = f'{S.utc(LO)}/{S.utc(HI)}'
COMMIT = 'a' * 40
SYMS = ['AAA', 'BBB']


def shift(x: RV.Series, d: int = SHIFT) -> RV.Series:
    bars = {k - d: b._replace(open_ms=b.open_ms - d, available_ms=b.available_ms - d) for k, b in x.bars.items()}
    fund = [r._replace(time_ms=r.time_ms - d, available_ms=r.available_ms - d) for r in x.funding]
    return RV.Series(bars, dict(x.rule), tuple(fund), {k - d: v for k, v in x.marks.items()})


def tape(**kw):
    return {'AAA': shift(RS.build(n=N_BARS)), 'BBB': shift(RS.build(n=N_BARS, **kw))}


def register(frozen=SYMS, fetch_ts=HI + 3_600_000):
    r = NT.Register(frozen)
    r.ingest({'fetch_ts': fetch_ts, 'ok': True, 'schema': NT.SCHEMA, 'raw_digest': '0' * 64, 'items': []})
    return r


@pytest.fixture(scope='module')
def plan():
    return PR.rex_plan(ROOT)                          # the committed STRAT-02 prereg split plan (no data read)


def env(plan, t=None, reg=None, man=None, **kw):
    t = tape() if t is None else t
    reg = register() if reg is None else reg
    man = PR.rex_tape_manifest(t, reg, symbols=SYMS) if man is None else man
    return dict({'tape': t, 'manifest': man, 'register': reg, 'symbols': list(SYMS), 'tier': 'CORE-10',
                 'plan': plan, 'commit': COMMIT, 'universe': None}, **kw)


def pre(e, window=(LO, HI)):
    return PR.rex_preflight(e['manifest'], e['tape'], e['register'], symbols=e['symbols'], window=window)


# ------------------------------------------------------------------ the sealed cutoff and the window
def test_cutoff_is_the_committed_development_end(plan):
    assert S.utc(PR.rex_sealed_cutoff(plan)) == '2026-09-28T00:00:00Z'
    assert PR.rex_sealed_cutoff(plan) <= PR.holdout_start(plan)


@pytest.mark.parametrize('spec', [None, '', '   '])
def test_window_is_required_no_default(plan, spec):
    with pytest.raises(PR.PitRunError, match='explicit --window'):
        PR.rex_window(spec, plan)


@pytest.mark.parametrize('spec', ['2026-09-01T00:00:00Z/2026-09-28T04:00:00Z',     # end one bar past the cutoff
                                  '2026-09-28T00:00:00Z/2026-10-05T00:00:00Z',     # starts at the cutoff
                                  '2026-10-12T00:00:00Z/2026-11-16T00:00:00Z',     # inside the holdout
                                  '2025-01-06T00:00:00Z/2027-01-04T00:00:00Z'])    # spans it
def test_window_overlapping_the_sealed_holdout_refuses(plan, spec):
    with pytest.raises(PR.PitRunError, match='sealed holdout'):
        PR.rex_window(spec, plan)


@pytest.mark.parametrize('spec', ['2025-01-06T00:00:00Z', '2025-01-06T01:00:00Z/2025-02-03T00:00:00Z',
                                  '2025-02-03T00:00:00Z/2025-01-06T00:00:00Z', '2025-01-06/2025-02-03'])
def test_malformed_window_refuses(plan, spec):
    with pytest.raises(PR.PitRunError):
        PR.rex_window(spec, plan)


def test_window_up_to_the_cutoff_is_accepted(plan):
    lo, hi = PR.rex_window('2025-12-15T00:00:00Z/2026-09-28T00:00:00Z', plan)
    assert S.utc(hi) == '2026-09-28T00:00:00Z' and PR.rex_window(WINDOW, plan) == (LO, HI)


def test_run_refuses_without_window_before_anything_is_written(plan, tmp_path):
    for w in (None, '2026-09-01T00:00:00Z/2026-09-28T04:00:00Z'):
        with pytest.raises(PR.PitRunError):
            PR.run_rex(env(plan), str(tmp_path), window=w, clock=None)
    assert os.listdir(tmp_path) == []


# ------------------------------------------------------------------ preflight: pass, and fail per missing input
def test_preflight_passes_on_the_full_spec(plan):
    e = env(plan)
    assert pre(e) == []
    assert set(e['manifest']['series']) == set(PR.REX_INPUTS)
    assert 'quote_volume' in e['manifest']['series']['klines_4h']['columns']


def _no_qv():
    t = tape()
    x = t['BBB']
    t['BBB'] = RV.Series({k: b._replace(quote_volume=None) for k, b in x.bars.items()}, x.rule, x.funding, x.marks)
    return t


def _drop_col(m, kind, col):
    m = json.loads(json.dumps(m))
    m['series'][kind]['columns'].remove(col)
    return PR.rex_seal_manifest(m)


def _drop_series(m, kind):
    m = json.loads(json.dumps(m))
    del m['series'][kind]
    return PR.rex_seal_manifest(m)


def _notice(m, **kw):
    m = json.loads(json.dumps(m))
    m['notice_register'].update(kw)
    return PR.rex_seal_manifest(m)


def _strip(t, what):
    x = t['BBB']
    t['BBB'] = RV.Series(x.bars, x.rule, () if what == 'funding' else x.funding, {} if what == 'marks' else x.marks)
    return t


CASES = {
    'qv_null_in_rows': (lambda p: env(p, t=_no_qv()), 'quote_volume'),
    'qv_undeclared': (lambda p: env(p, man=_drop_col(env(p)['manifest'], 'klines_4h', 'quote_volume')),
                      "klines_4h: required column(s) missing ['quote_volume']"),
    'klines_series_missing': (lambda p: env(p, man=_drop_series(env(p)['manifest'], 'klines_4h')),
                              'klines_4h: required series missing'),
    'funding_series_missing': (lambda p: env(p, man=_drop_series(env(p)['manifest'], 'funding')),
                               'funding: required series missing'),
    'funding_rate_undeclared': (lambda p: env(p, man=_drop_col(env(p)['manifest'], 'funding', 'rate')),
                                "funding: required column(s) missing ['rate']"),
    'funding_rows_absent': (lambda p: env(p, t=_strip(tape(), 'funding')), 'BBB: no funding rows'),
    'marks_series_missing': (lambda p: env(p, man=_drop_series(env(p)['manifest'], 'mark_1h')),
                             'mark_1h: required series missing'),
    'marks_rows_absent': (lambda p: env(p, t=_strip(tape(), 'marks')), 'BBB: no 1h funding-mark rows'),
    'register_missing': (lambda p: dict(env(p), register=None), 'notice register missing'),
    'register_block_missing': (lambda p: env(p, man=PR.rex_seal_manifest(
        {k: v for k, v in env(p)['manifest'].items() if k != 'notice_register'})), 'notice register missing'),
    'register_unattested': (lambda p: env(p, man=_notice(env(p)['manifest'], attested=False)), 'not attested'),
    'register_other_digest': (lambda p: env(p, man=_notice(env(p)['manifest'], digest='f' * 64)),
                              'not the attested one'),
    'register_not_covering_end': (lambda p: env(p, reg=register(fetch_ts=HI - RV.TF)), 'does not cover'),
    'register_missing_symbol': (lambda p: env(p, reg=register(frozen=['AAA'])), 'not built over every run symbol'),
    'register_parser_differs': (lambda p: env(p, man=_notice(env(p)['manifest'], parser_sha='0' * 64)),
                                'parser_sha'),
    'tape_tampered': (lambda p: dict(env(p), tape=_strip(tape(), 'marks')), 'mark_1h: the tape is not the attested'),
    'manifest_digest': (lambda p: dict(env(p), manifest=dict(env(p)['manifest'], digest='0' * 64)),
                        'manifest digest does not match'),
}


@pytest.mark.parametrize('case', list(CASES))
def test_preflight_fails_closed_per_missing_input(plan, tmp_path, case):
    build, want = CASES[case]
    e = build(plan)
    fails = pre(e)
    assert fails and any(want in f for f in fails), fails
    with pytest.raises(PR.PitRunError, match='preflight refused'):
        PR.run_rex(e, str(tmp_path), window=WINDOW, clock=None)
    assert os.listdir(tmp_path) == []                 # refused before any progress / artifact


# ------------------------------------------------------------------ end to end: STRAT-style provenance
def _final(path):
    doc = PG.read(path)
    d = os.path.dirname(path)
    for a in doc['artifacts']:
        assert PG.file_sha256(os.path.join(d, a['path'])) == a['sha256']
    return doc, d


def test_end_to_end_complete_run_is_sealed_with_evidence(plan, tmp_path):
    e = env(plan)
    final = PR.run_rex(e, str(tmp_path), window=WINDOW, clock=None)
    doc, d = _final(final)
    assert doc['state'] == 'sealed' and os.path.basename(d) == 'CORE-10'
    assert [s['name'] for s in doc['stages']] == ['pit.open', 'pit.development']
    assert doc['identity']['manifest'] == e['manifest']['digest'] and doc['identity']['prereg'] is None
    run = doc['run']
    assert doc['evidence'] == [f'summary-{run}.json']
    summary = json.load(open(os.path.join(d, f'summary-{run}.json')))
    detail = json.load(open(os.path.join(d, f'detail-{run}.json')))
    assert summary['format'] == PR.REX_SUMMARY_FORMAT and detail['format'] == PR.REX_DETAIL_FORMAT
    assert summary['status'] == detail['status'] == RV.COMPLETE
    assert summary['window'] == {'start': S.utc(LO), 'end': S.utc(HI)} and summary['label'] == PR.DEV_LABEL
    names = [c['name'] for c in PR.rex_configs()]
    assert len(names) == 10 and set(summary['counts']) == set(RV.SIDES)
    assert all(set(summary['counts'][s]) == set(names) for s in RV.SIDES)
    assert all(set(summary['counts'][s][n]) == set(PR.REX_ROWS) for s in RV.SIDES for n in names)
    acc = detail['access']
    assert acc['kind'] == 'data_access' and acc['split'] == 'development'
    assert acc['window'] == {'start': S.utc(LO - RV.WARMUP_MS), 'end': S.utc(HI)}
    assert acc['detail']['notice_register'] == e['register'].digest()
    assert not any(k in json.dumps(summary) for k in ('"trades": [', 'BBB', 'AAA'))   # no symbols / trades disclosed
    # same identity again: refuses without resume, returns the sealed final with it
    with pytest.raises(PR.PitRunError, match='already sealed'):
        PR.run_rex(env(plan), str(tmp_path), window=WINDOW, clock=None)
    assert PR.run_rex(env(plan), str(tmp_path), window=WINDOW, resume=True, clock=None) == final
    # another window is another identity: never aliases into the same evidence dir
    with pytest.raises(PR.PitRunError, match='another run identity'):
        PR.run_rex(env(plan), str(tmp_path), window=f'{S.utc(LO)}/{S.utc(HI - RV.TF)}', clock=None)


def test_end_to_end_with_trades_records_them_and_seals_status_consistently(plan, tmp_path):
    t = tape(vwap=RS.long_entry(5), close={12: 104.0})
    e = env(plan, t=t)
    final = PR.run_rex(e, str(tmp_path), window=WINDOW, clock=None)
    doc, d = _final(final)
    run = doc['run']
    summary = json.load(open(os.path.join(d, f'summary-{run}.json')))
    detail = json.load(open(os.path.join(d, f'detail-{run}.json')))
    prim = detail['results']['long'][RV.PRIMARY.name]['base']
    (tr,) = prim['trades']
    assert (tr['symbol'], tr['reason'], tr['exit_ms']) == ('BBB', 'exit.mean', RS.ms(13) - SHIFT)
    assert summary['counts']['long'][RV.PRIMARY.name]['base'] == {'trades': 1, 'status': RV.COMPLETE}
    complete = summary['status'] == RV.COMPLETE
    assert doc['evidence'] == ([f'summary-{run}.json'] if complete else [])
    if not complete:
        assert summary['incomplete'] and all(r['secondary'] is False for r in summary['incomplete'])
    # deterministic: an identical run in a fresh dir produces byte-identical artifacts
    final2 = PR.run_rex(env(plan, t=tape(vwap=RS.long_entry(5), close={12: 104.0})), str(tmp_path / 'b'),
                        window=WINDOW, clock=None)
    doc2, _ = _final(final2)
    assert doc2['artifacts'] == doc['artifacts']


def test_split_end_censoring_is_incomplete_without_evidence(plan, tmp_path):
    t = tape(vwap=RS.long_entry(5))                   # the long lot is still open at the window end
    final = PR.run_rex(env(plan, t=t), str(tmp_path), window=WINDOW, clock=None)
    doc, d = _final(final)
    assert doc['state'] == 'sealed' and doc['evidence'] == []
    summary = json.load(open(os.path.join(d, f'summary-{doc["run"]}.json')))
    assert summary['status'] == RV.INCOMPLETE
    assert {'side': 'long', 'config': RV.PRIMARY.name, 'row': 'base', 'reason': 'incomplete.split_end',
            'class': 'split_censoring', 'secondary': False} in summary['incomplete']
    assert not PR.is_complete(summary)
    assert G.pit_incomplete([final]) == [final]


def test_notice_register_feeds_the_book(plan, tmp_path):
    t = tape(vwap=RS.long_entry(5))
    rel = RS.ms(8) - SHIFT + 3_600_000
    r2 = NT.Register(['BBBUSDT', 'AAA'])
    assert NT.classify('Binance Futures Will Delist USDⓈ-M BBBUSDT Perpetual Contract', r2.frozen)[0] == 'include'
    t = {'AAA': t['AAA'], 'BBBUSDT': t['BBB']}
    r2.ingest({'fetch_ts': HI + 3_600_000, 'ok': True, 'schema': NT.SCHEMA, 'raw_digest': '0' * 64,
               'items': [{'code': 'c1', 'title': 'Binance Futures Will Delist USDⓈ-M BBBUSDT Perpetual Contract',
                          'releaseDate': rel, 'url': None}]})
    man = PR.rex_tape_manifest(t, r2, symbols=['AAA', 'BBBUSDT'])
    e = dict(env(plan, t=t, reg=r2, man=man), symbols=['AAA', 'BBBUSDT'])
    assert pre(e) == []
    final = PR.run_rex(e, str(tmp_path), window=WINDOW, clock=None)
    doc, d = _final(final)
    detail = json.load(open(os.path.join(d, f'detail-{doc["run"]}.json')))
    (tr,) = detail['results']['long'][RV.PRIMARY.name]['base']['trades']
    assert tr['reason'] == 'exit.delist' and tr['exit_ms'] == RS.ms(9) - SHIFT


# ------------------------------------------------------------------ run_r4 entry point
def test_run_r4_rex_requires_a_window(capsys):
    with pytest.raises(SystemExit) as ex:
        G.main(['rex', '--tier', 'CORE-10'])
    assert ex.value.code == 2 and '--window' in capsys.readouterr().err


def test_run_r4_rex_refuses_holdout_window_and_unbound_loader(capsys, tmp_path):
    out = str(tmp_path / 'rex')
    assert G.main(['rex', '--tier', 'CORE-10', '--out-dir', out,
                   '--window', '2026-09-01T00:00:00Z/2026-10-05T00:00:00Z']) == 2
    assert 'sealed holdout' in capsys.readouterr().err
    assert G.main(['rex', '--tier', 'CORE-10', '--out-dir', out, '--window', WINDOW]) == 3
    assert 'no attested REX tape loader' in capsys.readouterr().err
    assert not os.path.exists(out)


def test_run_r4_rex_with_a_synthetic_loader_runs_end_to_end(plan, tmp_path):
    seen = []

    def loader(repo, tier, window):
        seen.append((tier, window))
        e = env(plan)
        return {k: e[k] for k in ('tape', 'manifest', 'register', 'symbols', 'commit', 'universe')}
    final = G.run_rex(ROOT, window=WINDOW, tier='CORE-10', out_dir=str(tmp_path), loader=loader, clock=None)
    assert seen == [('CORE-10', WINDOW)] and PG.read(final)['state'] == 'sealed'
    assert G.pit_incomplete([final]) == []


def test_rex_profile_is_not_gate_routed():
    assert PR.REX_RUN.candidate_id == RV.CANDIDATE and PR.REX_RUN.family == RV.FAMILY
    assert RV.CANDIDATE not in PR.RUN_PROFILES and RV.CANDIDATE not in G.PROFILES
    assert PR.REX_PROFILES == {RV.CANDIDATE: PR.REX_RUN}
