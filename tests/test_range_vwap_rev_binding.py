"""REX-02 binding of range_vwap_rev.v1 into pit_run / run_r4: the frozen development window (train + WF0..WF4,
calibration and holdout refused, no default), the fail-closed input preflight (4h klines with quote_volume, funding rows,
1h funding marks, the per-symbol exchange rules (F1), rows bounded to the access window / cutoff (F2), the pinned
zb-delist-notice/1 artifact), causal marks, frozen cost rows, notice coverage / disagreement -> INCOMPLETE, and an
end-to-end run on a synthetic tape producing the STRAT-style provenance (progress snapshot, sealed final, summary /
detail artifacts). Synthetic tapes only (rex_synth, shifted 52 weeks back into the development period); no market data."""
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
import range_vwap_rev_artifact as NA                                                        # noqa: E402

SHIFT = 52 * 7 * 86_400_000                          # whole weeks: Monday / funding grid / UTC-day alignment kept
N_BARS = 16
LO = RS.ms(0) - SHIFT                                # 2025-11-17T00:00Z: the first decision close
ALO = LO - RV.WARMUP_MS                              # the access window start (the warm-up is read inside it)
HI = RS.ms(N_BARS) - SHIFT
WINDOW = f'{S.utc(ALO)}/{S.utc(HI)}'
COMMIT = 'a' * 40
SYMS = ['AAA', 'BBB']
DAY = 86_400_000
TAIL = PR.REX_FUNDING_TAIL_MS
WF3 = 'walk_forward[3]'                              # the committed split holding the synthetic window


def shift(x: RV.Series, d: int = SHIFT) -> RV.Series:
    bars = {k - d: b._replace(open_ms=b.open_ms - d, available_ms=b.available_ms - d) for k, b in x.bars.items()}
    fund = [r._replace(time_ms=r.time_ms - d, available_ms=r.available_ms - d) for r in x.funding]
    return RV.Series(bars, dict(x.rule), tuple(fund), {k - d: v for k, v in x.marks.items()})


def clip(x: RV.Series, lo: int = ALO, hi: int = HI) -> RV.Series:
    """The rows a bounded loader serves: [lo, hi] (funding: + the documented tail)."""
    return RV.Series({k: b for k, b in x.bars.items() if k >= lo and b.available_ms <= hi}, dict(x.rule),
                     tuple(r for r in x.funding if r.time_ms >= lo and r.available_ms <= hi + TAIL),
                     {k: v for k, v in x.marks.items() if k >= lo and k + RV.HOUR <= hi})


def tape(**kw):
    return {'AAA': clip(shift(RS.build(n=N_BARS))), 'BBB': clip(shift(RS.build(n=N_BARS, **kw)))}


OLD = {'code': 'old', 'title': 'Binance Futures Will Launch USDⓈ-M ZZZUSDT Perpetual Contract',
       'releaseDate': ALO - 30 * DAY, 'url': 'https://www.binance.com/en/support/announcement/old'}


def artifact(frozen=SYMS, fetch_ts=HI + 3_600_000, items=(OLD,), effective=None, covers_from=ALO - 30 * DAY):
    f = {'fetch_ts': fetch_ts, 'ok': True, 'schema': NT.SCHEMA, 'raw_digest': '0' * 64, 'items': list(items)}
    return NA.build(list(frozen), [f], effective or {}, covers_from_ms=covers_from)


@pytest.fixture(scope='module')
def plan():
    return PR.rex_plan(ROOT)                          # the committed STRAT-02 prereg split plan (no data read)


def env(plan, t=None, art=None, man=None, symbols=SYMS, **kw):
    t = tape() if t is None else t
    art = artifact() if art is None else art
    man = PR.rex_tape_manifest(t, art, symbols=symbols) if man is None else man
    return dict({'tape': t, 'manifest': man, 'notice_artifact': art, 'notice_sha256': art['digest'],
                 'symbols': list(symbols), 'tier': 'CORE-10', 'plan': plan, 'commit': COMMIT, 'universe': None}, **kw)


def pre(e, window=(ALO, HI)):
    return PR.rex_preflight(e['manifest'], e['tape'], e['notice_artifact'], symbols=e['symbols'], window=window,
                            artifact_sha=e['notice_sha256'])


def run(e, d, **kw):
    return PR.run_rex(e, str(d), window=kw.pop('window', WINDOW), clock=None, **kw)


def docs(final):
    doc = PG.read(final)
    d = os.path.dirname(final)
    for a in doc['artifacts']:
        assert PG.file_sha256(os.path.join(d, a['path'])) == a['sha256']
    load = lambda k: json.load(open(os.path.join(d, f'{k}-{doc["run"]}.json')))
    return doc, load('summary'), load('detail')


# ------------------------------------------------------------------ the frozen development window (ruling 1)
def test_cutoff_is_the_committed_development_end(plan):
    assert S.utc(PR.rex_sealed_cutoff(plan)) == '2026-09-28T00:00:00Z'
    assert PR.rex_sealed_cutoff(plan) <= PR.holdout_start(plan)


def test_frozen_window_is_train_through_wf4_of_the_committed_plan(plan):
    assert PR.REX_DEV_WINDOW == ('2021-01-04T00:00:00Z', '2026-09-28T00:00:00Z')
    keys = PR.dev_keys(plan)
    assert [plan.split(k)['name'] for k in keys] == ['train'] + ['walk_forward'] * 5
    lo, hi = PR.rex_dev_window(plan)
    assert (lo, hi) == (plan.access_range(keys[0])[0], plan.access_range(keys[-1])[1])
    assert PR.rex_calibration(plan) == [(S.parse_utc('2020-02-03T00:00:00Z'), lo)]   # calibration ends where it starts
    assert PR.rex_window('/'.join(PR.REX_DEV_WINDOW), plan) == (lo, hi)               # the whole window is accepted


def test_a_frozen_window_that_drifts_from_the_plan_refuses(plan, monkeypatch):
    monkeypatch.setattr(PR, 'REX_DEV_WINDOW', ('2021-01-11T00:00:00Z', '2026-09-28T00:00:00Z'))
    with pytest.raises(PR.PitRunError, match='differs from the committed train'):
        PR.rex_window(WINDOW, plan)


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


@pytest.mark.parametrize('spec', ['2020-12-28T00:00:00Z/2021-03-01T00:00:00Z',     # reaches into calibration
                                  '2020-03-02T00:00:00Z/2020-06-01T00:00:00Z',     # inside calibration
                                  '2021-01-03T20:00:00Z/2021-03-01T00:00:00Z'])    # one bar before the dev start
def test_window_overlapping_calibration_refuses(plan, spec):
    with pytest.raises(PR.PitRunError, match='calibration'):
        PR.rex_window(spec, plan)


def test_window_before_the_development_window_or_shorter_than_warmup_refuses(plan):
    with pytest.raises(PR.PitRunError, match='before the frozen development window'):
        PR.rex_window('2019-01-07T00:00:00Z/2019-03-04T00:00:00Z', plan)
    with pytest.raises(PR.PitRunError, match='warm-up'):
        PR.rex_window('2025-01-06T00:00:00Z/2025-01-16T00:00:00Z', plan)            # exactly W x 4h: no decision


@pytest.mark.parametrize('spec', ['2025-01-06T00:00:00Z', '2025-01-06T01:00:00Z/2025-02-03T00:00:00Z',
                                  '2025-02-03T00:00:00Z/2025-01-06T00:00:00Z', '2025-01-06/2025-02-03'])
def test_malformed_window_refuses(plan, spec):
    with pytest.raises(PR.PitRunError):
        PR.rex_window(spec, plan)


def test_window_up_to_the_cutoff_is_accepted(plan):
    lo, hi = PR.rex_window('2025-12-15T00:00:00Z/2026-09-28T00:00:00Z', plan)
    assert S.utc(hi) == '2026-09-28T00:00:00Z' and PR.rex_window(WINDOW, plan) == (ALO, HI)


def test_run_refuses_without_window_before_anything_is_written(plan, tmp_path):
    for w in (None, '2026-09-01T00:00:00Z/2026-09-28T04:00:00Z', '2020-12-28T00:00:00Z/2021-03-01T00:00:00Z'):
        with pytest.raises(PR.PitRunError):
            PR.run_rex(env(plan), str(tmp_path), window=w, clock=None)
    assert os.listdir(tmp_path) == []


# ------------------------------------------------------------------ preflight: pass, and fail per missing input
def test_preflight_passes_on_the_full_spec(plan):
    e = env(plan)
    assert pre(e) == []
    assert set(e['manifest']['series']) == set(PR.REX_INPUTS)
    assert 'quote_volume' in e['manifest']['series']['klines_4h']['columns']
    assert e['manifest']['rules'] == {s: dict(RS.RULE) for s in SYMS}
    assert e['manifest']['notice_register']['artifact_digest'] == e['notice_artifact']['digest']


def _no_qv():
    t = tape()
    x = t['BBB']
    t['BBB'] = RV.Series({k: b._replace(quote_volume=None) for k, b in x.bars.items()}, x.rule, x.funding, x.marks)
    return t


def _edit(m, f):
    m = json.loads(json.dumps(m))
    f(m)
    return PR.rex_seal_manifest(m)


def _drop_col(m, kind, col):
    return _edit(m, lambda d: d['series'][kind]['columns'].remove(col))


def _drop_series(m, kind):
    return _edit(m, lambda d: d['series'].pop(kind))


def _notice(m, **kw):
    return _edit(m, lambda d: d['notice_register'].update(kw))


def _strip(t, what):
    x = t['BBB']
    t['BBB'] = RV.Series(x.bars, x.rule, () if what == 'funding' else x.funding, {} if what == 'marks' else x.marks)
    return t


def _bbb_env(p, bars=None, funding=None, marks=None, rule=None):
    """The tape with BBB's rows edited AFTER the bounded load (the manifest is then built over the edited tape)."""
    t = tape()
    x = t['BBB']
    t['BBB'] = RV.Series(dict(x.bars) | (bars or {}), dict(rule or x.rule),
                         tuple(x.funding) + tuple(funding or ()), dict(x.marks) | (marks or {}))
    return env(p, t=t)


def _bar(o):
    return P.Bar(o, 100.0, 101.0, 99.0, 100.0, 1.0, 100.0, o + RV.TF)


def _fund(t, avail=None):
    return P.Funding(t, 0.0001, 8, t if avail is None else avail)


def _tampered_artifact():
    a = artifact()
    a['covers_from_ms'] -= 1                            # edited without re-digesting
    return a


def _env_other_rules(p):
    t = tape()
    m = PR.rex_tape_manifest(t, artifact(), symbols=SYMS)
    x = t['BBB']
    t['BBB'] = RV.Series(x.bars, dict(x.rule, step=0.01), x.funding, x.marks)    # rules edited after the manifest
    return env(p, t=t, man=m)


def _cut_bar(p):
    c = PR.rex_sealed_cutoff(p)
    return _bbb_env(p, bars={c: _bar(c)})


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
    # F1: the exchange rules are provenance
    'rules_missing': (lambda p: env(p, man=_edit(env(p)['manifest'], lambda d: d.pop('rules'))),
                      'exchange rules missing'),
    'rules_digest_mismatch': (lambda p: env(p, man=_edit(env(p)['manifest'],
                                                         lambda d: d.update(rules_digest='0' * 64))),
                              'exchange rules missing'),
    'rules_differ_from_attested': (_env_other_rules, 'exchange rules differ from the attested ones'),
    'rule_field_missing': (lambda p: _bbb_env(p, rule={'tick': 0.01, 'step': 0.001, 'min_qty': 0.001}),
                           'BBB: exchange rule incomplete'),
    # F2: rows bounded to the access window and the cutoff
    'kline_after_window_end': (lambda p: _bbb_env(p, bars={HI: _bar(HI)}), 'a kline outside the access window'),
    'kline_before_window_start': (lambda p: _bbb_env(p, bars={ALO - RV.TF: _bar(ALO - RV.TF)}),
                                  'a kline outside the access window'),
    'kline_at_holdout_cutoff': (_cut_bar, 'at / after the holdout cutoff'),
    'mark_after_window_end': (lambda p: _bbb_env(p, marks={HI: 100.0}), 'a 1h mark off the hour grid or outside'),
    'mark_off_grid': (lambda p: _bbb_env(p, marks={HI - RV.HOUR - 1: 100.0}), 'a 1h mark off the hour grid'),
    'funding_beyond_tail': (lambda p: _bbb_env(p, funding=[_fund(HI + 8 * RV.HOUR)]),
                            'a funding row outside the access window'),
    'funding_avail_beyond_tail': (lambda p: _bbb_env(p, funding=[_fund(HI, HI + TAIL + 1)]),
                                  'a funding row outside the access window'),
    'funding_before_window_start': (lambda p: _bbb_env(p, funding=[_fund(ALO - 8 * RV.HOUR)]),
                                    'a funding row outside the access window'),
    # the zb-delist-notice/1 artifact (ruling 3)
    'artifact_missing': (lambda p: dict(env(p), notice_artifact=None), 'artifact: missing'),
    'artifact_binding_missing': (lambda p: env(p, man=_edit(env(p)['manifest'], lambda d: d.pop('notice_register'))),
                                 'notice artifact binding missing'),
    'artifact_unattested': (lambda p: env(p, man=_notice(env(p)['manifest'], attested=False)), 'not attested'),
    'artifact_other_digest': (lambda p: env(p, man=_notice(env(p)['manifest'], artifact_digest='f' * 64)),
                              'binds another notice artifact'),
    'artifact_not_pinned': (lambda p: dict(env(p), notice_sha256='e' * 64), 'not the artifact the run command pinned'),
    'artifact_tampered': (lambda p: dict(env(p), notice_artifact=_tampered_artifact()),
                          'digest does not match its content'),
    'artifact_frozen_rebound': (lambda p: env(p, man=_notice(env(p)['manifest'], frozen=['AAA'])),
                                'binds another frozen symbol list'),
    'artifact_parser_differs': (lambda p: env(p, man=_notice(env(p)['manifest'], parser_sha='0' * 64)), 'parser_sha'),
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
        run(e, tmp_path)
    assert os.listdir(tmp_path) == []                 # refused before any progress / artifact


def test_f2_funding_tail_allowance_is_only_the_documented_60s(plan):
    assert TAIL == RV.FUNDING_TOL == 60_000
    e = _bbb_env(plan, funding=[_fund(HI, HI + TAIL)])  # calc_time up to 60 s after the nominal END event: allowed
    assert pre(e) == []


# ------------------------------------------------------------------ F1: rules are in the run identity
def test_f1_different_rules_give_a_different_manifest_digest_and_run_id(plan, tmp_path):
    a = env(plan)
    b = _bbb_env(plan, rule=dict(RS.RULE, min_notional=10.0))
    assert pre(b) == []
    assert a['manifest']['rules_digest'] != b['manifest']['rules_digest']
    assert a['manifest']['digest'] != b['manifest']['digest']
    ca, cb = PR.rex_run_config(a, ALO, HI), PR.rex_run_config(b, ALO, HI)
    assert ca['rules_digest'] != cb['rules_digest'] and PR.sha(ca) != PR.sha(cb)
    fa, fb = run(a, tmp_path / 'a'), run(b, tmp_path / 'b')
    assert PG.read(fa)['run'] != PG.read(fb)['run']


# ------------------------------------------------------------------ costs are frozen (ruling 5)
def test_only_base_and_fees_slip_x2_cost_rows(plan, tmp_path, monkeypatch):
    assert PR.REX_ROWS == PR.REX_COST_ROWS == ('base', 'fees_slip_x2')
    assert PR.rex_run_config(env(plan), ALO, HI)['rows'] == ['base', 'fees_slip_x2']
    for k in ('rows', 'stress_rows', 'cost_rows'):
        with pytest.raises(PR.PitRunError, match='cannot be configured'):
            run(dict(env(plan), **{k: ['base', '2x']}), tmp_path)
    monkeypatch.setattr(PR, 'REX_ROWS', ('base', 'fees_slip_x2', '2x'))
    with pytest.raises(PR.PitRunError, match='frozen'):
        run(env(plan), tmp_path)
    assert os.listdir(tmp_path) == []


# ------------------------------------------------------------------ marks: causal, never interpolated (ruling 4)
T_EV = RS.ms(4) - SHIFT                               # an 8h funding instant (00:00 / 08:00 / 16:00 UTC grid)


def _marks(m):
    return RV.Series({}, dict(RS.RULE), (), m)


def test_mark_exactly_at_the_event_is_used():
    assert T_EV % (8 * RV.HOUR) == 0
    assert RV.mark_at(_marks({T_EV - RV.HOUR: 101.0, T_EV: 999.0}), T_EV) == 101.0   # the bar ENDING at T


@pytest.mark.parametrize('m', [{T_EV - RV.HOUR + 1: 101.0},                         # available 1 ms after the event
                               {T_EV: 102.0},                                       # the next (future) bar only
                               {T_EV - 2 * RV.HOUR: 100.0, T_EV: 102.0},            # gap: no carry, no interpolation
                               {}])
def test_a_mark_after_the_event_or_a_gap_is_incomplete(m):
    with pytest.raises(RV.Incomplete) as ex:
        RV.mark_at(_marks(m), T_EV)
    assert ex.value.reason == 'incomplete.funding_mark' and ex.value.first_ms == T_EV


def test_a_missing_mark_under_a_held_lot_makes_the_rows_incomplete(plan, tmp_path):
    t = tape(vwap=RS.long_entry(5), close={12: 104.0})
    hold = range(RS.ms(5) - SHIFT, RS.ms(13) - SHIFT, RV.HOUR)
    x = t['BBB']
    t['BBB'] = RV.Series(x.bars, x.rule, x.funding, {k: v for k, v in x.marks.items() if k not in hold})
    e = env(plan, t=t)
    assert pre(e) == []                                # not a refusal: the affected rows are INCOMPLETE
    doc, summary, _ = docs(run(e, tmp_path))
    assert summary['status'] == RV.INCOMPLETE and doc['evidence'] == []
    assert {'split': WF3, 'side': 'long', 'config': RV.PRIMARY.name, 'row': 'base', 'reason': 'incomplete.funding_mark',
            'class': 'funding_mark_gap', 'secondary': False} in summary['incomplete']


# ------------------------------------------------------------------ end to end: STRAT-style provenance
def test_end_to_end_complete_run_is_sealed_with_evidence(plan, tmp_path):
    e = env(plan)
    final = run(e, tmp_path)
    doc, summary, detail = docs(final)
    d = os.path.dirname(final)
    assert doc['state'] == 'sealed' and os.path.basename(d) == 'CORE-10'
    assert [s['name'] for s in doc['stages']] == ['pit.open', 'pit.development']
    assert doc['identity']['manifest'] == e['manifest']['digest'] and doc['identity']['prereg'] is None
    run_id = doc['run']
    assert doc['evidence'] == [f'summary-{run_id}.json']
    assert summary['format'] == PR.REX_SUMMARY_FORMAT and detail['format'] == PR.REX_DETAIL_FORMAT
    assert summary['status'] == detail['status'] == RV.COMPLETE
    assert summary['window'] == {'start': S.utc(ALO), 'end': S.utc(HI)} and summary['label'] == PR.DEV_LABEL
    assert summary['first_decision'] == S.utc(LO) and summary['promotable'] is False
    assert summary['split'] == 'development' and summary['cost_rows'] == ['base', 'fees_slip_x2']
    names = [c['name'] for c in PR.rex_configs()]
    assert len(names) == 10 and set(summary['counts']) == set(RV.SIDES)
    assert all(set(summary['counts'][s]) == set(names) for s in RV.SIDES)
    assert all(set(summary['counts'][s][n]) == set(PR.REX_ROWS) for s in RV.SIDES for n in names)
    acc = detail['access']
    assert acc['kind'] == 'data_access' and acc['split'] == 'development'
    assert acc['window'] == {'start': S.utc(ALO), 'end': S.utc(HI)}
    assert acc['detail']['notice_artifact'] == e['notice_artifact']['digest']
    assert acc['detail']['rules_digest'] == e['manifest']['rules_digest'] and acc['detail']['promotable'] is False
    assert not any(k in json.dumps(summary) for k in ('"trades": [', 'BBB', 'AAA'))   # no symbols / trades disclosed
    # same identity again: refuses without resume, returns the sealed final with it
    with pytest.raises(PR.PitRunError, match='already sealed'):
        run(env(plan), tmp_path)
    assert run(env(plan), tmp_path, resume=True) == final
    # another window is another identity: never aliases into the same evidence dir
    t2 = {s: clip(x, hi=HI - RV.TF) for s, x in tape().items()}
    with pytest.raises(PR.PitRunError, match='another run identity'):
        run(env(plan, t=t2), tmp_path, window=f'{S.utc(ALO)}/{S.utc(HI - RV.TF)}')


def test_end_to_end_with_trades_records_them_and_seals_status_consistently(plan, tmp_path):
    t = tape(vwap=RS.long_entry(5), close={12: 104.0})
    final = run(env(plan, t=t), tmp_path)
    doc, summary, detail = docs(final)
    prim = detail['results']['long'][RV.PRIMARY.name]['base']
    (tr,) = prim['trades']
    assert (tr['symbol'], tr['reason'], tr['exit_ms']) == ('BBB', 'exit.mean', RS.ms(13) - SHIFT)
    c = summary['counts']['long'][RV.PRIMARY.name]['base']
    assert (c['trades'], c['status'], c['splits']) == (1, RV.COMPLETE, {WF3: RV.COMPLETE})
    assert c['r'] == pytest.approx(tr['r']) and c['net'] == pytest.approx(tr['net']) and tr['split'] == WF3
    complete = summary['status'] == RV.COMPLETE
    assert doc['evidence'] == ([f'summary-{doc["run"]}.json'] if complete else [])
    if not complete:
        assert summary['incomplete'] and all(r['secondary'] is False for r in summary['incomplete'])
    # deterministic: an identical run in a fresh dir produces byte-identical artifacts
    final2 = run(env(plan, t=tape(vwap=RS.long_entry(5), close={12: 104.0})), tmp_path / 'b')
    assert PG.read(final2)['artifacts'] == doc['artifacts']


def test_split_end_censoring_is_incomplete_without_evidence(plan, tmp_path):
    t = tape(vwap=RS.long_entry(5))                   # the long lot is still open at the window end
    final = run(env(plan, t=t), tmp_path)
    doc, summary, _ = docs(final)
    assert doc['state'] == 'sealed' and doc['evidence'] == []
    assert summary['status'] == RV.INCOMPLETE
    assert {'split': WF3, 'side': 'long', 'config': RV.PRIMARY.name, 'row': 'base', 'reason': 'incomplete.split_end',
            'class': 'split_censoring', 'secondary': False} in summary['incomplete']
    assert not PR.is_complete(summary)
    assert G.pit_incomplete([final]) == [final]


# ------------------------------------------------------------------ the notice artifact feeds the book (ruling 3)
DELIST = 'Binance Futures Will Delist USDⓈ-M BBBUSDT Perpetual Contract'
REL = RS.ms(8) - SHIFT + 3_600_000


def _delist_env(plan, effective):
    t = tape(vwap=RS.long_entry(5))
    t = {'AAA': t['AAA'], 'BBBUSDT': t['BBB']}
    it = {'code': 'c1', 'title': DELIST, 'releaseDate': REL, 'url': 'https://www.binance.com/en/support/announcement/c1'}
    art = artifact(frozen=['AAA', 'BBBUSDT'], items=(OLD, it), effective={'c1': effective})
    return env(plan, t=t, art=art, symbols=['AAA', 'BBBUSDT'])


def test_artifact_schema_carries_every_required_field(plan):
    e = _delist_env(plan, HI + 7 * DAY)
    (n,) = e['notice_artifact']['notices']
    assert set(n) == {'symbol', 'published_at', 'effective_delist_ms', 'source_url', 'source_id', 'fetch_ts',
                      'raw_digest'}
    assert (n['symbol'], n['published_at'], n['effective_delist_ms'], n['source_id'], n['fetch_ts']) == (
        'BBBUSDT', REL, HI + 7 * DAY, 'c1', HI + 3_600_000)
    assert n['source_url'].startswith('https://') and len(n['raw_digest']) == 64
    a = e['notice_artifact']
    assert a['format'] == 'zb-delist-notice/1' and a['digest'] == NA.digest_of(a)
    assert NA.validate(a, expect_digest=a['digest']).register.digest() == a['register_digest']


def _resealed(a, f):
    a = json.loads(json.dumps(a))
    f(a)
    a['digest'] = NA.digest_of(a)
    return a


@pytest.mark.parametrize('edit, want', [
    (lambda a: a['notices'][0].pop('effective_delist_ms'), 'notice keys'),
    (lambda a: a['notices'][0].update(effective_delist_ms=REL - 1), 'effective delist before'),
    (lambda a: a['notices'][0].update(source_url='http://x'), 'https'),
    (lambda a: a['notices'][0].update(published_at=REL + 1), 'differ from the replayed register'),
    (lambda a: a['notices'].clear(), 'differ from the replayed register'),               # a notice silently dropped
    (lambda a: a['notices'].append(dict(a['notices'][0], source_id='c9')), 'differ from the replayed register'),
    (lambda a: a['fetches'][0].update(raw_digest='x'), '64-hex'),
    (lambda a: a.update(register_digest='0' * 64), 'register_digest differs'),
    (lambda a: a.update(parser_sha='0' * 64), 'parser_sha'),
    (lambda a: a.update(extra=1), 'keys must be exactly'),
    (lambda a: a.update(fetches=[]), 'no fetch documents'),
])
def test_artifact_schema_violations_refuse(plan, edit, want):
    a = _resealed(_delist_env(plan, HI + 7 * DAY)['notice_artifact'], edit)
    with pytest.raises(NA.ArtifactError, match=want):
        NA.validate(a, expect_digest=a['digest'])


def test_notice_artifact_feeds_the_book(plan, tmp_path):
    e = _delist_env(plan, HI + 7 * DAY)               # effective after the window: the data agrees
    assert NT.classify(DELIST, ('AAA', 'BBBUSDT'))[0] == 'include'
    assert pre(e) == []
    _, _, detail = docs(run(e, tmp_path))
    (tr,) = detail['results']['long'][RV.PRIMARY.name]['base']['trades']
    assert tr['reason'] == 'exit.delist' and tr['exit_ms'] == RS.ms(9) - SHIFT


def _reasons(summary):
    return {r['reason'] for r in summary.get('incomplete', ())}


def test_data_notice_disagreement_is_incomplete_not_filled(plan, tmp_path):
    e = _delist_env(plan, RS.ms(10) - SHIFT)          # klines continue after the effective delist time
    assert pre(e) == []
    doc, summary, _ = docs(run(e, tmp_path))
    assert summary['status'] == RV.INCOMPLETE and doc['evidence'] == []
    assert 'incomplete.notice_gap' in _reasons(summary)


def test_a_series_ending_without_a_notice_is_never_inferred_as_a_delisting(plan, tmp_path):
    t = tape()
    x = t['BBB']
    cut = RS.ms(10) - SHIFT
    t['BBB'] = RV.Series({k: b for k, b in x.bars.items() if k < cut}, x.rule,
                         tuple(r for r in x.funding if r.time_ms < cut), {k: v for k, v in x.marks.items() if k < cut})
    e = env(plan, t=t)
    assert pre(e) == [] and NA.disagreements([], 'BBB', t['BBB'], HI) == [cut]
    doc, summary, _ = docs(run(e, tmp_path))
    assert summary['status'] == RV.INCOMPLETE and 'incomplete.notice_gap' in _reasons(summary)


@pytest.mark.parametrize('kw', [{'fetch_ts': HI - RV.TF},                 # no successful fetch at / after the end
                                {'covers_from': ALO + DAY},               # coverage starts inside the window
                                {'items': ()}])                           # coverage unprovable (nothing paged)
def test_missing_or_unprovable_notice_coverage_is_incomplete(plan, tmp_path, kw):
    e = env(plan, art=artifact(**kw))
    assert pre(e) == []                                # attested and well-formed: not a refusal ...
    doc, summary, _ = docs(run(e, tmp_path))           # ... but the affected rows are INCOMPLETE (no fill)
    assert summary['status'] == RV.INCOMPLETE and doc['evidence'] == []
    assert _reasons(summary) == {'incomplete.notice_gap'}


# ------------------------------------------------------------------ run_r4 entry point
def test_run_r4_rex_requires_a_window(capsys):
    with pytest.raises(SystemExit) as ex:
        G.main(['rex', '--tier', 'CORE-10'])
    assert ex.value.code == 2 and '--window' in capsys.readouterr().err


def test_run_r4_rex_refuses_holdout_window_and_unbound_store(capsys, tmp_path):
    out = str(tmp_path / 'rex')
    assert G.main(['rex', '--tier', 'CORE-10', '--out-dir', out,
                   '--window', '2026-09-01T00:00:00Z/2026-10-05T00:00:00Z']) == 2
    assert 'sealed holdout' in capsys.readouterr().err
    assert G.main(['rex', '--tier', 'CORE-10', '--out-dir', out, '--window', WINDOW]) == 3
    assert 'no attested REX store' in capsys.readouterr().err
    assert G.main(['rex', '--tier', 'CORE-10', '--out-dir', out, '--window', WINDOW, '--store', str(tmp_path)]) == 2
    assert '--notice' in capsys.readouterr().err
    assert not os.path.exists(out)


def test_run_r4_rex_with_a_synthetic_loader_runs_end_to_end(plan, tmp_path):
    seen = []

    def loader(repo, tier, window):
        seen.append((tier, window))
        e = env(plan)
        return {k: e[k] for k in ('tape', 'manifest', 'notice_artifact', 'notice_sha256', 'symbols', 'commit',
                                  'universe')}
    final = G.run_rex(ROOT, window=WINDOW, tier='CORE-10', out_dir=str(tmp_path), loader=loader, clock=None)
    assert seen == [('CORE-10', WINDOW)] and PG.read(final)['state'] == 'sealed'
    assert G.pit_incomplete([final]) == []


def test_rex_profile_is_not_gate_routed():
    assert PR.REX_RUN.candidate_id == RV.CANDIDATE and PR.REX_RUN.family == RV.FAMILY
    assert RV.CANDIDATE not in PR.RUN_PROFILES and RV.CANDIDATE not in G.PROFILES
    assert PR.REX_PROFILES == {RV.CANDIDATE: PR.REX_RUN}


# ------------------------------------------------------------------ notice register: absence refuses (6172596212 a)
@pytest.mark.parametrize('bad', [None, {}, 'not-json', {'format': 'zb-delist-notice/1'}])
def test_a_missing_or_malformed_notice_artifact_is_a_preflight_refusal(plan, tmp_path, bad):
    e = dict(env(plan), notice_artifact=bad)
    assert pre(e)                                      # refused: absence is never INCOMPLETE
    with pytest.raises(PR.PitRunError, match='preflight refused'):
        run(e, tmp_path)
    assert os.listdir(tmp_path) == []                  # nothing written, no book pass


def test_a_digest_mismatched_notice_artifact_is_a_preflight_refusal(plan, tmp_path):
    for e in (dict(env(plan), notice_sha256='e' * 64), dict(env(plan), notice_artifact=_tampered_artifact())):
        with pytest.raises(PR.PitRunError, match='preflight refused'):
            run(e, tmp_path)
    assert os.listdir(tmp_path) == []


def test_a_valid_register_without_coverage_for_a_symbol_makes_rows_incomplete_not_a_refusal(plan, tmp_path):
    e = env(plan, art=artifact(frozen=['AAA']))       # attested register, BBB outside its frozen list
    assert pre(e) == []
    doc, summary, _ = docs(run(e, tmp_path))
    assert summary['status'] == RV.INCOMPLETE and doc['evidence'] == []
    assert _reasons(summary) == {'incomplete.notice_gap'}


# ------------------------------------------------------------------ split-aware evidence (6172596212 b)
B = S.parse_utc('2025-12-15T00:00:00Z')              # the committed WF3 / WF4 boundary
SHIFT2 = 49 * 7 * DAY                                # bar 42 of the synthetic tape opens exactly at B
NB2 = 120
ALO2, HI2 = RS.ms(-RV.W_BARS) - SHIFT2, RS.ms(NB2) - SHIFT2
WINDOW2 = f'{S.utc(ALO2)}/{S.utc(HI2)}'
WF4 = 'walk_forward[4]'


def tape2(**kw):
    return {'AAA': clip(shift(RS.build(n=NB2), SHIFT2), ALO2, HI2),
            'BBB': clip(shift(RS.build(n=NB2, **kw), SHIFT2), ALO2, HI2)}


def env2(plan, t=None, covers_from=None):
    t = tape2() if t is None else t
    art = artifact(fetch_ts=HI2 + RV.HOUR, covers_from=ALO2 - 30 * DAY if covers_from is None else covers_from)
    return env(plan, t=t, art=art)


def run2(e, d):
    return PR.run_rex(e, str(d), window=WINDOW2, clock=None)


def test_split_boundaries_come_from_the_committed_plan(plan):
    assert RS.ms(42) - SHIFT2 == B == plan.access_range(WF4)[0] == plan.access_range(WF3)[1]
    segs = PR.rex_segments(plan, ALO2, HI2)
    assert [(g['key'], g['lo'], g['hi'], g['first']) for g in segs] == [
        (WF3, ALO2, B, ALO2 + RV.WARMUP_MS), (WF4, B, HI2, B + RV.WARMUP_MS)]
    with pytest.raises(PR.PitRunError, match='per-split W x 4h warm-up'):   # a split sliver with no decision
        PR.rex_window(f'{S.utc(ALO2)}/{S.utc(B + 10 * RV.TF)}', plan)


def test_warm_up_and_tape_are_per_split(plan, tmp_path, monkeypatch):
    seen, real = [], RV.run_book

    def spy(tape, cfg, side, *, first, end, **kw):
        seen.append((first, end, min(min(x.bars) for x in tape.values()),
                     max(b.available_ms for x in tape.values() for b in x.bars.values()),
                     min(r.time_ms for x in tape.values() for r in x.funding)))
        return real(tape, cfg, side, first=first, end=end, **kw)
    monkeypatch.setattr(PR.RV, 'run_book', spy)
    run2(env2(plan), tmp_path)
    per = len(RV.SIDES) * len(PR.rex_configs()) * len(PR.REX_ROWS)
    assert len(seen) == 2 * per
    assert set(seen[:per]) == {(ALO2 + RV.WARMUP_MS, B, ALO2, B, ALO2)}     # WF3: nothing at / after B
    assert set(seen[per:]) == {(B + RV.WARMUP_MS, HI2, B, HI2, B)}         # WF4: warm-up read from B on


def test_a_position_open_at_a_boundary_does_not_carry_over(plan, tmp_path):
    e = env2(plan, t=tape2(vwap=RS.long_entry(38)))   # long lot opened at bar 39, held through the boundary (bar 42)
    doc, summary, detail = docs(run2(e, tmp_path))
    a, b = detail['splits']
    pa, pb = (x['results']['long'][RV.PRIMARY.name]['base'] for x in (a, b))
    assert pa['status'] == RV.INCOMPLETE and pa['censor']['reason'] == 'incomplete.split_end'
    assert set(pa['open_lots']) == {'BBB'} and pa['trades'] == []
    assert pb['status'] == RV.COMPLETE and pb['open_lots'] == {} and pb['trades'] == []
    agg = detail['results']['long'][RV.PRIMARY.name]['base']
    assert agg['status'] == RV.INCOMPLETE and agg['splits'] == {WF3: RV.INCOMPLETE, WF4: RV.COMPLETE}
    assert doc['evidence'] == [] and summary['status'] == RV.INCOMPLETE


def test_per_split_statuses_are_present_and_only_the_affected_split_is_incomplete(plan, tmp_path):
    e = env2(plan, covers_from=B - DAY)              # notice coverage proven only from just before the boundary
    doc, summary, detail = docs(run2(e, tmp_path))
    assert summary['split_keys'] == [WF3, WF4] and [s['key'] for s in summary['splits']] == [WF3, WF4]
    assert [s['key'] for s in detail['splits']] == [WF3, WF4]
    assert {s['key']: s['status'] for s in summary['splits']} == {WF3: RV.INCOMPLETE, WF4: RV.COMPLETE}
    assert {r['split'] for r in summary['incomplete']} == {WF3}
    assert {r['reason'] for r in summary['incomplete']} == {'incomplete.notice_gap'}
    s3, s4 = summary['splits']
    assert s3['window'] == {'start': S.utc(ALO2), 'end': S.utc(B)} and s4['first_decision'] == S.utc(B + RV.WARMUP_MS)
    assert summary['status'] == RV.INCOMPLETE and doc['evidence'] == []


def test_the_aggregate_is_derived_from_the_splits(plan, tmp_path):
    vw = dict(RS.long_entry(5))
    vw.update({20: 100.0})
    vw.update(RS.long_entry(110))
    t = tape2(vwap=vw, close={12: 104.0})
    _, summary, detail = docs(run2(env2(plan, t=t), tmp_path))
    for side in RV.SIDES:
        for c in PR.rex_configs():
            for row in PR.REX_ROWS:
                parts = [s['counts'][side][c['name']][row] for s in summary['splits']]
                agg = summary['counts'][side][c['name']][row]
                assert agg['trades'] == sum(p['trades'] for p in parts)
                assert agg['net'] == pytest.approx(sum(p['net'] for p in parts))
                assert agg['r'] == pytest.approx(sum(p['r'] for p in parts))
                assert agg['splits'] == {s['key']: p['status'] for s, p in zip(summary['splits'], parts)}
                assert (agg['status'] == RV.COMPLETE) == all(p['status'] == RV.COMPLETE for p in parts)
                res = detail['results'][side][c['name']][row]
                assert [tr['split'] for tr in res['trades']] == [
                    s['key'] for s in detail['splits'] for _ in s['results'][side][c['name']][row]['trades']]
    assert summary['counts']['long'][RV.PRIMARY.name]['base']['trades'] >= 1
    assert (summary['status'] == RV.COMPLETE) == all(s['status'] == RV.COMPLETE for s in summary['splits'])


# ------------------------------------------------------------------ provenance class (6172596212 c)
def test_a_synthetic_run_is_marked_non_evidence(plan, tmp_path):
    _, summary, detail = docs(run(env(plan), tmp_path))
    for d in (summary, detail):
        assert d['provenance']['class'] == 'synthetic' and d['provenance']['historical_evidence'] is False


def test_a_historical_env_must_carry_its_proven_clean_commit(plan, tmp_path):
    for pv in ({'class': 'historical', 'commit': 'b' * 40, 'tree': 'clean'},     # not the env commit
               {'class': 'historical', 'commit': COMMIT, 'tree': 'dirty'}):
        with pytest.raises(PR.PitRunError, match='clean commit SHA'):
            run(env(plan, provenance=pv), tmp_path)
    assert os.listdir(tmp_path) == []
    _, summary, _ = docs(run(env(plan, provenance={'class': 'historical', 'commit': COMMIT, 'tree': 'clean'}),
                             tmp_path))
    assert summary['provenance'] == {'class': 'historical', 'historical_evidence': True, 'commit': COMMIT,
                                     'tree': 'clean'}
