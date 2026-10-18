"""#148 Phase-C report (tools/research/phase_c_report.py): frozen-snapshot preflight, write-once outputs, id scrub,
executed / shadow / rejected / counterfactual / Wave-2 / defect sections and every shortlist boundary. Fakes only."""
import json, os, sys

import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
sys.path.insert(0, os.path.join(ROOT, 'tools', 'research'))
import phase_c_report as P                    # noqa: E402
import run_pack as RP                         # noqa: E402
import strategy_shadow as SSH                 # noqa: E402
import true_books as TB                       # noqa: E402

START = '2026-10-19T00:00:00+00:00'
SNAP = '2026-10-21T09:00:00+00:00'
RAW_ID = 'BTCUSDT-RAWID-778899'
ORDER = '99887766554433'


def cohort(start=START):
    return RP.new_cohort(start, code_sha='c', settings_sha='s', book_version='v', fee_model_sha='f', book_cap=4)


def row(i, cid, key='exp_scalp', sym='BTCUSDT', gross=10.0, fee=1.0, fund=0.0, r=0.9, risk=10.0, **kw):
    d = dict(id=f'{sym}-{key}-{i}', book=TB.book_of(key), strategy_key=key, symbol=sym, side='LONG', tf='15m',
             opened=f'2026-10-19T{i % 24:02d}:00:00+00:00', closed=f'2026-10-19T{i % 24:02d}:30:00+00:00',
             pnl_gross=gross, fees=fee, fees_actual=fee, funding_actual=fund, r=r, risk_usd=risk, notional=100.0,
             exit_reason='target', cohort_id=cid, cost_refs=[ORDER], fills=[dict(orderId=ORDER)])
    d.update(kw)
    return d


def live_dir(tmp, rows=(), audit=(), lots=None, rec=None, shadow=None, ml=None, extra=True):
    """A fake PAPER data dir. shadow: callable(ShadowBook) filling the cohort's ledger."""
    d = tmp / 'live'; d.mkdir()
    rec = rec or cohort()
    (d / 'history.json').write_text(json.dumps(list(rows)))
    (d / 'cohort_rows.jsonl').write_text(''.join(json.dumps(r) + '\n' for r in rows))
    (d / 'cohort.json').write_text(json.dumps(rec))
    (d / 'state.json').write_text(json.dumps(dict(cohort=rec, lots=lots or {}, funding_unowned=[], api_key='NOPE')))
    (d / 'trade_audit.jsonl').write_text(''.join(json.dumps(a) + '\n' for a in audit))
    if extra:
        (d / 'config.env').write_text('API_KEY=secret\n'); (d / 'settings.json').write_text('{}')
    if shadow:
        sb = SSH.ShadowBook(d / 'strategy_shadow' / f'{rec["id"]}.jsonl', rec['id'], P.SHADOW_MODEL, min_n=20)
        shadow(sb)
    if ml:
        (d / 'ml_shadow').mkdir()
        (d / 'ml_shadow' / 'decisions.jsonl').write_text(''.join(json.dumps(x) + '\n' for x in ml[0]))
        (d / 'ml_shadow' / 'labels.jsonl').write_text(''.join(json.dumps(x) + '\n' for x in ml[1]))
    return d, rec


def frozen(tmp, **kw):
    d, rec = live_dir(tmp, **kw)
    s = tmp / 'snap'
    P.freeze(str(d), str(s), SNAP)
    return s, s / 'MANIFEST.phase-c-snapshot.json', rec


# ------------------------------------------------------------------ shadow fixtures (real reducer)
def slot(key='exp_scalp', sid='A'):
    return dict(id=sid, key=key, tf='4h', sides='both', risk=0.02, max_pos=1, share=1.0, params={}, mgmt=dict(stop_atr=1.0, tp_r=1.0))


def iso(m):
    from datetime import datetime, timedelta, timezone
    return (datetime(2026, 10, 19, 1, 0, tzinfo=timezone.utc) + timedelta(minutes=m)).isoformat()


def episodes(n, key='exp_scalp', win=True, status='skipped', sym='BTCUSDT', t0=0, sid='A'):
    def go(b):
        for k in range(n):
            t = t0 + 20 * k
            b.on_signal(slot=slot(key, sid), symbol=sym, side='LONG', signal=dict(time=iso(t), close=100, atr=2),
                        mark=dict(time=iso(t), price=100), live_decision=dict(status=status, reason='' if status == 'taken' else 'max lots'))
            hi, lo = (104, 99.5) if win else (100.5, 97)
            b.on_bar(tf='4h', bars={sym: dict(time=iso(t + 10), o=100, h=hi, l=lo, c=hi - 1 if win else lo + 1)}, signals={})
    return go


def both(*fs):
    def go(b):
        for f in fs: f(b)
    return go


# ------------------------------------------------------------------ freeze / preflight / write-once
def test_freeze_copies_only_the_whitelist_and_pins_every_file(tmp_path):
    s, man, _ = frozen(tmp_path, rows=[row(1, 'x')], shadow=episodes(1))
    m = json.loads(man.read_text())
    assert not (s / 'config.env').exists() and not (s / 'settings.json').exists()
    assert set(m['files']) == {'history.json', 'cohort_rows.jsonl', 'cohort.json', 'state.json', 'trade_audit.jsonl'} | \
        {k for k in m['files'] if k.startswith('strategy_shadow/')}
    assert any(k.startswith('strategy_shadow/') for k in m['files'])
    for rel, v in m['files'].items(): assert P.sha_file(s / rel) == v['sha256']
    with pytest.raises(P.PhaseCError, match='never overwritten'): P.freeze(str(tmp_path / 'live'), str(s), SNAP)


@pytest.mark.parametrize('breakit, msg', [
    (lambda s, m: (s / 'history.json').write_text('[]'), 'hash mismatch'),
    (lambda s, m: (s / 'cohort.json').unlink(), 'missing'),
    (lambda s, m: m.write_text(json.dumps(dict(json.loads(m.read_text()), format='x'))), 'wrong format'),
    (lambda s, m: m.write_text(json.dumps(dict(json.loads(m.read_text()), snapshot_utc=None))), 'snapshot_utc'),
    (lambda s, m: m.write_text(json.dumps(dict(json.loads(m.read_text()),
                                               files=dict(json.loads(m.read_text())['files'], **{'../x': dict(sha256='0')}))))
     , 'escapes'),
])
def test_preflight_refusals_write_an_incomplete_only_record(tmp_path, breakit, msg):
    s, man, rec = frozen(tmp_path, rows=[row(1, None)])
    breakit(s, man)
    out = tmp_path / 'out'
    with pytest.raises(P.PhaseCError, match=msg): P.run(str(s), str(man), str(out), [rec['id']])
    assert not (out / 'report.json').exists()
    inc = json.loads((out / 'INCOMPLETE.json').read_text())
    assert inc['report_status'] == 'INCOMPLETE' and inc['non_promotable'] is True


def test_an_unpinned_file_or_a_live_dir_is_refused(tmp_path):
    s, man, rec = frozen(tmp_path, rows=[row(1, None)])
    (s / 'ml_shadow').mkdir(); (s / 'ml_shadow' / 'decisions.jsonl').write_text('{"strategy":"x","ts_utc":"2026-10-20"}\n')
    with pytest.raises(P.PhaseCError, match='unpinned file'): P.build(str(s), str(man), [rec['id']])
    with pytest.raises(P.PhaseCError, match='unpinned file'): P.build(str(tmp_path / 'live'), str(man), [rec['id']])


def test_output_inside_the_snapshot_is_refused_and_the_snapshot_is_never_written(tmp_path):
    cid = cohort()['id']
    s, man, _ = frozen(tmp_path, rows=[row(i, cid) for i in range(3)], shadow=episodes(2))
    before = {str(p): (P.sha_file(p), p.stat().st_mtime_ns) for p in s.rglob('*') if p.is_file()}
    with pytest.raises(P.PhaseCError, match='inside the snapshot'): P.run(str(s), str(man), str(s / 'out'), [cid])
    P.run(str(s), str(man), str(tmp_path / 'o'), [cid])
    assert {str(p): (P.sha_file(p), p.stat().st_mtime_ns) for p in s.rglob('*') if p.is_file()} == before


def test_duplicate_audit_lines_count_once_and_are_reported(tmp_path):
    cid = cohort()['id']
    rows = [row(1, cid)]
    a = dict(kind='coverage', id=rows[0]['id'], symbol='BTCUSDT', t='2026-10-19T02:00:00+00:00')
    s, man, _ = frozen(tmp_path, rows=rows, audit=[a, a, a])
    q = {(x['source'], x['code']): x for x in P.build(str(s), str(man), [cid])['rank'][0]['defects']['queue']}
    assert q[('trade_audit_coverage', 'coverage_partial')]['count'] == 1 and q[('snapshot', 'duplicate_audit_lines')]['count'] == 2


def test_closes_after_the_snapshot_or_untimed_are_excluded_and_counted(tmp_path):
    cid = cohort()['id']
    rows = [row(1, cid), row(2, cid, closed='2026-10-22T00:00:00+00:00'), row(3, cid, closed=None)]
    s, man, _ = frozen(tmp_path, rows=rows)
    ex = P.build(str(s), str(man), [cid])['rank'][0]['executed']
    assert ex['excluded_outside_window_or_untimed'] == 2 and {f['family']: f['closed'] for f in ex['families']}['B1_FAST'] == 1


def test_commission_missing_is_incomplete():
    s = P.exec_stats([row(i, 'c') for i in range(20)] + [row(20, 'c', commission_missing=True)])
    assert s['incomplete'] == {'commission_missing': 1} and not s['rankable']


def test_wave2_expected_pin_mismatch_and_pin_without_packet_refuse(tmp_path):
    cid = cohort()['id']
    s, man, _ = frozen(tmp_path, rows=[row(1, cid)])
    w = str(wave2_packet(tmp_path))
    with pytest.raises(P.PhaseCError, match='expected pin'): P.build(str(s), str(man), [cid], wave2=w, wave2_sha256='0' * 64)
    with pytest.raises(P.PhaseCError, match='without a packet'): P.build(str(s), str(man), [cid], wave2_sha256='0' * 64)
    assert P.build(str(s), str(man), [cid], wave2=w, wave2_sha256=P.sha_file(os.path.join(w, 'summary.json')))['usdt_vs_usdc']['available']


def test_outputs_are_write_once_and_byte_identical_on_rerun(tmp_path):
    cid = cohort()['id']
    s, man, rec = frozen(tmp_path, rows=[row(i, cid) for i in range(3)], shadow=episodes(2))
    P.run(str(s), str(man), str(tmp_path / 'o1'), [cid])
    P.run(str(s), str(man), str(tmp_path / 'o2'), [cid])
    assert (tmp_path / 'o1' / 'report.json').read_bytes() == (tmp_path / 'o2' / 'report.json').read_bytes()
    assert (tmp_path / 'o1' / 'report.md').read_bytes() == (tmp_path / 'o2' / 'report.md').read_bytes()
    with pytest.raises(P.PhaseCError, match='never overwritten'): P.run(str(s), str(man), str(tmp_path / 'o1'), [cid])


def test_exchange_ids_never_reach_the_output(tmp_path):
    cid = cohort()['id']
    rows = [row(1, cid, id=RAW_ID)]
    audit = [dict(kind='trade_audit', id=RAW_ID, symbol='BTCUSDT', t='2026-10-19T01:30:00+00:00', coverage_why=['restart gap'],
                  counterfactuals=[dict(kind='causal_policy', rule='trail', vs_actual=1.0)])]
    s, man, _ = frozen(tmp_path, rows=rows, audit=audit, shadow=episodes(1))
    P.run(str(s), str(man), str(tmp_path / 'o'), [cid])
    txt = (tmp_path / 'o' / 'report.json').read_text() + (tmp_path / 'o' / 'report.md').read_text()
    assert RAW_ID not in txt and ORDER not in txt and 'NOPE' not in txt
    with pytest.raises(P.PhaseCError, match='forbidden'): P.scrub_check(dict(a=[dict(orderId=1)]))


def test_shadow_model_is_the_engines():
    import engine as E
    import trade_audit as TA
    assert P.SHADOW_MODEL == dict(SSH.MODEL_V1, fee_rate=E.FEE_EST, adverse_slippage_bps=TA.EXEC_SLIP_BPS)


# ------------------------------------------------------------------ section 1: executed
def test_executed_net_after_actual_fees_and_funding_and_rank_boundary():
    rows = [row(i, 'c', gross=10, fee=1, fund=-0.5) for i in range(19)]
    s = P.exec_stats(rows)
    assert s['closed'] == 19 and s['net'] == pytest.approx(19 * 8.5) and s['funding'] == pytest.approx(-9.5)
    assert s['complete'] and not s['rankable'] and s['note'] == 'insufficient n (19/20)'
    assert P.exec_stats(rows + [row(19, 'c')])['rankable']


def test_estimated_fee_and_unknown_risk_make_the_stream_incomplete_never_rankable():
    rows = [row(i, 'c') for i in range(25)]
    rows[3] = dict(rows[3], fees_actual=None); rows[4] = dict(rows[4], r=None)
    s = P.exec_stats(rows)
    assert not s['complete'] and not s['rankable']
    assert s['incomplete']['cost_incomplete'] == 1 and s['incomplete']['risk_unknown'] == 1
    assert s['net'] == pytest.approx(25 * 9)                       # the estimate is shown, never silently zero


def test_max_drawdown_on_the_closed_trade_curve():
    rows = [row(0, 'c', gross=101, fee=1), row(1, 'c', gross=-209, fee=1), row(2, 'c', gross=11, fee=1)]
    s = P.exec_stats(rows)
    assert s['max_dd'] == pytest.approx((2100 - 1890) / -2100, abs=1e-4)


def test_quote_split_is_descriptive_per_quote():
    ex = P.executed_section([row(0, 'c', sym='ETHUSDT'), row(1, 'c', sym='ETHUSDC')])
    assert {(x['key'], x['quote'], x['closed']) for x in ex['quote_split']} == {('exp_scalp', 'USDT', 1), ('exp_scalp', 'USDC', 1)}


# ------------------------------------------------------------------ section 4: classes and every boundary
def X(closed=20, exp=1.0, pf=1.5, complete=True):
    return dict(closed=closed, complete=complete, rankable=complete and closed >= 20, expectancy=exp, pf=pf, avg_r=exp, net=exp * closed)


@pytest.mark.parametrize('ex, sh, defects, cls', [
    (X(), X(), set(), 'keep_executable'),
    (X(pf=1.09), X(), set(), 'UNDECIDED'),
    (X(), X(pf=1.09), set(), 'UNDECIDED'),
    (X(pf=1.10), X(pf=1.10), set(), 'keep_executable'),
    (X(closed=19), X(), set(), 'shadow_only'),
    (X(complete=False), X(), set(), 'shadow_only'),
    (None, X(), set(), 'shadow_only'),
    (X(), X(), {'shadow:funding_unread'}, 'repair'),
    (X(exp=-1), X(), set(), 'repair'),
    (X(exp=-1, pf=0.5), X(exp=-1, pf=0.5), set(), 'retire'),
    (X(exp=-1), X(closed=19, exp=-1), set(), 'UNDECIDED'),
    (X(closed=5), X(closed=5), set(), 'UNDECIDED'),
    (X(exp=0.0), X(), set(), 'UNDECIDED'),
    (X(exp=0.0), X(exp=-1), set(), 'UNDECIDED'),
    (dict(X(exp=-0.1, pf=0.9), win_rate=0.8), X(exp=-0.1, pf=0.9), set(), 'retire'),
    (X(), X(closed=19), set(), 'UNDECIDED'),
])
def test_class_boundaries(ex, sh, defects, cls):
    got, why, ins = P.classify('k', ex, [sh] if sh else [], defects)
    assert got == cls, why
    assert ins['defects'] == sorted(defects)


def test_no_losing_close_is_an_unbounded_pf_not_a_missing_one():
    assert P.classify('k', X(pf=None), [X(pf=None)], set())[0] == 'keep_executable'
    assert P.classify('k', X(pf=None, exp=0.0), [X(pf=None)], set())[0] == 'UNDECIDED'


def test_keep_requires_executed_rankable_and_multiple_lineages_are_undecided():
    assert P.classify('k', X(closed=19), [X()], set())[0] == 'shadow_only'
    assert P.classify('k', X(), [X(), X()], set())[0] == 'UNDECIDED'


def test_slow_strategy_below_n_is_undecided_not_failed(tmp_path):
    cid = cohort()['id']
    s, man, _ = frozen(tmp_path, rows=[row(i, cid, key='exp_trend', gross=-5, r=-0.6) for i in range(3)], shadow=episodes(1))
    sl = {r['key']: r for r in P.build(str(s), str(man), [cid])['shortlist']['rows']}
    assert sl['exp_trend']['cls'] == 'UNDECIDED' and 'not failed' in sl['exp_trend']['note']


# ------------------------------------------------------------------ cohorts
def test_no_clean_rank_cohort_is_incomplete_and_undecided(tmp_path):
    cid = cohort()['id']
    s, man, _ = frozen(tmp_path, rows=[row(i, cid) for i in range(25)], shadow=episodes(25, win=True))
    b = P.build(str(s), str(man), [], [cid])
    assert b['report_status'] == 'INCOMPLETE' and b['cohort_status'] == ['no_clean_rank_cohort']
    assert b['diagnostic'][0]['use'].startswith('DIAGNOSTIC') and b['rank'] == []
    assert {r['cls'] for r in b['shortlist']['rows']} == {'UNDECIDED'}


def test_inherited_open_lots_block_the_rank_cohort(tmp_path):
    cid = cohort()['id']
    s, man, _ = frozen(tmp_path, rows=[row(i, cid) for i in range(25)], lots={'L': dict(cohort_id='CH-old')}, shadow=episodes(25))
    b = P.build(str(s), str(man), [cid], ['CH-old'])
    assert 'inherited_positions_open' in b['cohort_status'] and b['report_status'] == 'INCOMPLETE'
    assert {r['cls'] for r in b['shortlist']['rows']} == {'UNDECIDED'}


def test_rank_cohort_must_validate_and_cannot_also_be_diagnostic(tmp_path):
    s, man, rec = frozen(tmp_path, rows=[row(1, 'x')])
    assert 'rank_cohort_record_missing' in P.build(str(s), str(man), ['CH-unknown'])['cohort_status']
    with pytest.raises(P.PhaseCError, match='both'): P.build(str(s), str(man), [rec['id']], [rec['id']])
    bad = dict(rec); bad.pop('code_sha')
    s2 = tmp_path / 'b'; s2.mkdir()
    s3, man3, _ = frozen(s2, rows=[row(1, rec['id'])], rec=bad)
    assert 'rank_cohort_invalid' in P.build(str(s3), str(man3), [rec['id']])['cohort_status']


def test_diagnostic_rows_never_feed_the_shortlist(tmp_path):
    rec = cohort()
    rows = [row(i, rec['id']) for i in range(25)] + [row(100 + i, 'CH-old', key='range_scalp', gross=-9, r=-1) for i in range(25)]
    s, man, _ = frozen(tmp_path, rows=rows, rec=rec, shadow=episodes(25))
    b = P.build(str(s), str(man), [rec['id']], ['CH-old'])
    sl = {r['key']: r for r in b['shortlist']['rows']}
    assert sl['range_scalp']['inputs']['executed'] is None
    diag = {x['key']: x for x in b['diagnostic'][0]['executed']['strategies']}
    assert diag['range_scalp']['closed'] == 25


# ------------------------------------------------------------------ section 2: shadow, rejected, counterfactuals
def test_real_shadow_ledger_replays_and_ranks_at_20(tmp_path):
    cid = cohort()['id']
    rows = [row(i, cid, gross=10, fee=1) for i in range(16)] + [row(i, cid, gross=-5, fee=1, r=-0.6) for i in range(16, 20)]
    s, man, _ = frozen(tmp_path, rows=rows, shadow=both(episodes(16), episodes(4, win=False, t0=1000)))
    b = P.build(str(s), str(man), [cid])
    sh = {r['key']: r for r in b['rank'][0]['shadow']['strategies']}
    assert sh['exp_scalp']['closed'] == 20 and sh['exp_scalp']['complete'] and sh['exp_scalp']['rankable']
    sl = {r['key']: r for r in b['shortlist']['rows']}
    assert sl['exp_scalp']['cls'] == 'keep_executable', sl['exp_scalp']
    assert b['report_status'] == 'INCOMPLETE'                         # no Wave-2 packet supplied


def test_shadow_19_closes_is_not_rankable(tmp_path):
    cid = cohort()['id']
    s, man, _ = frozen(tmp_path, rows=[row(i, cid) for i in range(20)], shadow=episodes(19))
    sl = {r['key']: r for r in P.build(str(s), str(man), [cid])['shortlist']['rows']}
    assert sl['exp_scalp']['cls'] == 'UNDECIDED'


def test_foreign_model_ledger_is_incomplete(tmp_path, monkeypatch):
    cid = cohort()['id']
    s, man, _ = frozen(tmp_path, rows=[row(i, cid) for i in range(20)], shadow=episodes(20))
    monkeypatch.setattr(P, 'SHADOW_MODEL', dict(P.SHADOW_MODEL, fee_rate=0.0004))
    b = P.build(str(s), str(man), [cid])
    assert not b['rank'][0]['shadow']['coverage']['complete'] and 'model_mismatch' in b['rank'][0]['shadow']['coverage']['gaps']
    assert b['report_status'] == 'INCOMPLETE'


def test_missing_shadow_ledger_is_reported_not_guessed(tmp_path):
    cid = cohort()['id']
    s, man, _ = frozen(tmp_path, rows=[row(i, cid) for i in range(20)])
    b = P.build(str(s), str(man), [cid])
    assert b['rank'][0]['shadow']['available'] is False and b['report_status'] == 'INCOMPLETE'
    assert {r['key']: r for r in b['shortlist']['rows']}['exp_scalp']['cls'] == 'UNDECIDED'


def test_rejected_signals_compare_skipped_with_taken_on_the_same_coin(tmp_path):
    cid = cohort()['id']
    s, man, _ = frozen(tmp_path, rows=[row(1, cid)],
                       shadow=both(episodes(3, status='taken'), episodes(2, win=False, status='skipped', t0=1000)))
    rej = P.build(str(s), str(man), [cid])['rank'][0]['rejected_signals']['rows']
    by = {r['decision']: r for r in rej}
    assert by['taken']['closed'] == 3 and by['taken']['avg_r'] > 0
    sk = next(r for d, r in by.items() if d.startswith('skipped:'))
    assert sk['closed'] == 2 and sk['avg_r'] < 0 and sk['avg_r_vs_taken'] == pytest.approx(sk['avg_r'] - by['taken']['avg_r'])


def test_counterfactuals_separate_causal_from_hindsight_and_dedupe(tmp_path):
    rows = [row(1, 'c', risk=10.0)]
    a = dict(kind='trade_audit', id=rows[0]['id'], counterfactuals=[
        dict(kind='causal_policy', rule='trail', vs_actual=2.0), dict(kind='causal_policy', rule='be', vs_actual=None),
        dict(kind='hindsight_ceiling', rule='best observed mark', vs_actual=9.0)])
    cf = P.counterfactual_section(rows, [a, a, dict(a, id='other')])
    pol = {p['rule']: p for p in cf['policies']}
    assert cf['audited_closes'] == 1 and pol['trail']['evaluated'] == 1 and pol['trail']['mean_vs_actual_r'] == pytest.approx(0.2)
    assert pol['be']['unevaluated'] == 1 and pol['be']['evaluated'] == 0
    assert [h['rule'] for h in cf['hindsight_ceiling']] == ['best observed mark'] and 'best observed mark' not in pol


# ------------------------------------------------------------------ section 5: defects
def test_defects_attribute_and_window(tmp_path):
    rows = [row(1, 'c', fees_actual=None, exit_px_unconfirmed=True)]
    audit = [dict(kind='coverage', id=rows[0]['id'], symbol='BTCUSDT', t='2026-10-19T02:00:00+00:00'),
             dict(kind='coverage', id=rows[0]['id'], symbol='BTCUSDT', t='2026-10-18T02:00:00+00:00')]
    d = P.defects_section(rows, [dict(key='exp_scalp', incomplete=['funding_unread'])], dict(gaps=['torn_record']), audit,
                          dict(funding_unowned=[dict(symbol='ETHUSDT', time_ms=5)]), ['inherited_positions_open'],
                          {'trade_audit.jsonl': 2}, (START, SNAP))
    by = {(x['source'], x['code']): x for x in d['queue']}
    assert by[('executed_close', 'exit_px_unconfirmed')]['class_input'] and by[('executed_close', 'cost_incomplete')]['key'] == 'exp_scalp'
    assert by[('shadow', 'funding_unread')]['class_input']
    assert by[('trade_audit_coverage', 'coverage_partial')]['count'] == 1 and not by[('trade_audit_coverage', 'coverage_partial')]['class_input']
    assert ('funding_unowned', 'funding_unattributed') in by and ('cohort', 'inherited_positions_open') in by
    assert by[('snapshot', 'torn_lines:trade_audit.jsonl')]['count'] == 2 and ('shadow_ledger', 'torn_record') in by


def test_an_attributed_cost_gap_makes_an_otherwise_keep_strategy_repair(tmp_path):
    cid = cohort()['id']
    rows = [row(i, cid) for i in range(25)]
    rows[0] = dict(rows[0], exit_px_unconfirmed=True)
    s, man, _ = frozen(tmp_path, rows=rows, shadow=episodes(25))
    sl = {r['key']: r for r in P.build(str(s), str(man), [cid])['shortlist']['rows']}
    assert sl['exp_scalp']['cls'] == 'repair' and 'exit_px_unconfirmed' in sl['exp_scalp']['why']


# ------------------------------------------------------------------ section 3: Wave-2 packet
def wave2_packet(tmp, tamper=False):
    import run_quote_pair_wave2 as W2
    d = tmp / 'w2'; (d / 'cells').mkdir(parents=True)
    content = dict(name='candidate.x.long.15m', preflight_sha256='pf', status='complete', role='candidate', strategy='x',
                   side='long', tf='15m', scenarios=dict(equal_5bp=dict(status='COMPLETE', quotes=dict(
                       USDT=dict(closed_trades=3, fees=1.0, adverse_slippage=0.5, funding=0.1, net_pnl=2.0),
                       USDC=dict(closed_trades=3, fees=0.8, adverse_slippage=0.6, funding=0.1, net_pnl=2.1)),
                       paired=dict(metrics=dict(delta=0.1), sufficiency='low', n_paired=3, unmatched=dict(only_usdt=0, only_usdc=0),
                                   verdict_use='NONE'))))
    env = W2.envelope(content)
    if tamper: env['content']['strategy'] = 'y'
    (d / 'cells' / 'candidate.x.long.15m.json').write_text(json.dumps(env))
    summ = dict(format=P.WAVE2_FORMAT, run_id='r1', preflight_sha256='pf', packet_status='INCOMPLETE', verdict=None,
                usdc_better='USDC BETTER: UNAVAILABLE', global_verdict_available=False, non_promotable=True,
                controls_not_run=[dict(name='c')], cells={'candidate.x.long.15m': dict(content, cell_sha256=env['cell_sha256'])})
    (d / 'summary.json').write_text(json.dumps(summ))
    return d


def test_wave2_is_copied_verbatim_with_costs_separated(tmp_path):
    w = P.wave2_section(str(wave2_packet(tmp_path)))
    assert w['packet_status'] == 'INCOMPLETE' and w['usdc_better'] == 'USDC BETTER: UNAVAILABLE' and w['verdict'] is None
    c = w['cells'][0]
    assert c['quotes']['USDC']['adverse_slippage'] == 0.6 and c['quotes']['USDC']['fees'] == 0.8 and c['paired']['n_paired'] == 3


def test_tampered_wave2_cell_is_refused(tmp_path):
    import run_quote_pair_wave2 as W2
    with pytest.raises(W2.Wave2Error): P.wave2_section(str(wave2_packet(tmp_path, tamper=True)))


def test_complete_report_needs_clean_cohort_complete_shadow_and_wave2(tmp_path):
    cid = cohort()['id']
    s, man, _ = frozen(tmp_path, rows=[row(i, cid) for i in range(20)], shadow=episodes(20))
    b = P.build(str(s), str(man), [cid], wave2=str(wave2_packet(tmp_path)))
    assert b['report_status'] == 'COMPLETE' and b['pins']['wave2_summary_sha256'] and b['non_promotable']


# ------------------------------------------------------------------ ML (descriptive only)
def test_ml_ledger_is_descriptive_and_never_changes_classes(tmp_path):
    cid = cohort()['id']
    dec = [dict(decision_id='d1', strategy='exp_scalp', ts_utc='2026-10-19T03:00:00+00:00', taken=True),
           dict(decision_id='d2', strategy='exp_scalp', ts_utc='2026-10-19T04:00:00+00:00', taken=True),
           dict(decision_id='d0', strategy='exp_scalp', ts_utc='2026-10-18T04:00:00+00:00', taken=True)]
    lab = [dict(decision_id='d1', net_r=-3.0)]
    rows = [row(i, cid) for i in range(20)]
    s1, m1, _ = frozen(tmp_path, rows=rows, shadow=episodes(20), ml=(dec, lab))
    t2 = tmp_path / 'b'; t2.mkdir()
    s2, m2, _ = frozen(t2, rows=rows, shadow=episodes(20))
    b1, b2 = P.build(str(s1), str(m1), [cid]), P.build(str(s2), str(m2), [cid])
    ml = b1['rank'][0]['ml']
    assert ml['decisions_in_window'] == 2 and ml['rows'][0]['taken_unlabelled'] == 1 and ml['complete'] is False
    assert b1['shortlist'] == b2['shortlist']
    assert '"d1"' not in json.dumps(b1)


# ------------------------------------------------------------------ review round 1 (adversarial review + mutation survivors)
def test_a_stray_book_never_replaces_a_strategys_stream_and_is_a_defect(tmp_path):
    cid = cohort()['id']
    rows = [row(i, cid) for i in range(16)] + [row(i, cid, gross=-5, r=-0.6) for i in range(16, 20)] + [row(20, cid, book=None)]
    s, man, _ = frozen(tmp_path, rows=rows, shadow=both(episodes(16), episodes(4, win=False, t0=1000)))
    b = P.build(str(s), str(man), [cid])
    ex = {x['key']: x for x in b['rank'][0]['executed']['strategies']}
    assert ex['exp_scalp']['closed'] == 21 and ex['exp_scalp']['book_mismatch'] == 1
    sl = {r['key']: r for r in b['shortlist']['rows']}
    assert sl['exp_scalp']['cls'] == 'repair' and 'book_mismatch' in sl['exp_scalp']['why']


def test_unlisted_strategy_is_discovery_like_the_engine():
    ex = P.executed_section([row(0, 'c', key='brand_new', book='B5_DISCOVERY')])
    x = next(s for s in ex['strategies'] if s['key'] == 'brand_new')
    assert x['family'] == 'B5_DISCOVERY' and x['mapped'] is False and x['book_mismatch'] == 0


@pytest.mark.parametrize('drop', [('state.json',), ('cohort.json',), ('trade_audit.jsonl',), ('history.json', 'cohort_rows.jsonl')])
def test_missing_required_inputs_make_the_rank_build_incomplete_and_undecided(tmp_path, drop):
    cid = cohort()['id']
    d, rec = live_dir(tmp_path, rows=[row(i, cid) for i in range(20)], lots={'L': dict(cohort_id='CH-old')}, shadow=episodes(20))
    for f in drop: (d / f).unlink()
    P.freeze(str(d), str(tmp_path / 'snap'), SNAP)
    s = tmp_path / 'snap'
    b = P.build(str(s), str(s / 'MANIFEST.phase-c-snapshot.json'), [cid], wave2=str(wave2_packet(tmp_path)))
    assert any(c.startswith('missing_input:') for c in b['cohort_status']) and b['report_status'] == 'INCOMPLETE'
    assert {r['cls'] for r in b['shortlist']['rows']} <= {'UNDECIDED'}


def test_naive_snapshot_time_is_refused(tmp_path):
    d, _ = live_dir(tmp_path, rows=[row(1, 'x')])
    with pytest.raises(P.PhaseCError, match='UTC offset'): P.freeze(str(d), str(tmp_path / 's'), '2026-10-21T09:00:00')
    (tmp_path / 'x').mkdir()
    s, man, rec = frozen(tmp_path / 'x', rows=[row(1, 'x')])
    man.write_text(json.dumps(dict(json.loads(man.read_text()), snapshot_utc='2026-10-21T09:00:00')))
    with pytest.raises(P.PhaseCError, match='UTC offset'): P.build(str(s), str(man), [rec['id']])


def test_an_unexpected_failure_still_writes_the_incomplete_record(tmp_path):
    cid = cohort()['id']
    d, rec = live_dir(tmp_path, rows=[row(1, cid)], shadow=episodes(1))
    led = d / 'strategy_shadow' / f'{cid}.jsonl'
    first = json.loads(led.read_text().splitlines()[0])
    with open(led, 'a') as f: f.write(json.dumps(dict(first, seq=999, kind='future_kind')) + '\n')
    P.freeze(str(d), str(tmp_path / 'snap'), SNAP)
    s = tmp_path / 'snap'
    with pytest.raises(P.PhaseCError, match='AttributeError'):
        P.run(str(s), str(s / 'MANIFEST.phase-c-snapshot.json'), str(tmp_path / 'o'), [cid])
    assert json.loads((tmp_path / 'o' / 'INCOMPLETE.json').read_text())['report_status'] == 'INCOMPLETE'


def test_closes_before_the_cohort_start_are_excluded_and_counted(tmp_path):
    cid = cohort()['id']
    s, man, _ = frozen(tmp_path, rows=[row(1, cid), row(2, cid, closed='2026-10-18T23:00:00+00:00')])
    assert P.build(str(s), str(man), [cid])['rank'][0]['executed']['excluded_outside_window_or_untimed'] == 1


def test_a_linked_pinned_file_is_refused(tmp_path):
    s, man, rec = frozen(tmp_path, rows=[row(1, 'x')])
    tgt = tmp_path / 'elsewhere.json'; tgt.write_bytes((s / 'history.json').read_bytes())
    (s / 'history.json').unlink()
    try: os.symlink(tgt, s / 'history.json')
    except (OSError, NotImplementedError): pytest.skip('symlinks not permitted here')
    with pytest.raises(P.PhaseCError, match='link'): P.build(str(s), str(man), [rec['id']])


def test_incomplete_shadow_stream_is_never_rankable(tmp_path, monkeypatch):
    class FakeBook:
        def __init__(self, *a, **k): self.episodes = {}
        def compute(self):
            return dict(model=dict(hash='h'), coverage=dict(complete=False, gaps=['x:1']), strategies=[dict(
                key='exp_scalp', config_digest='d', closed=25, open=0, held=0, episodes=25, realized=10.0, fees=1.0, funding=0.0,
                roi=0.0, max_dd=0.0, pf=2.0, expectancy=1.0, avg_r=0.5, net_r=12.5, win_rate=0.6, turnover=1.0,
                excursions_excluded=0, mfe_r={}, mae_r={}, giveback_r={}, exits={}, live_decisions={},
                incomplete=['funding_unread:BTCUSDT@x'], complete=False)])
    monkeypatch.setattr(P.SSH, 'ShadowBook', FakeBook)
    doc, _ = P.shadow_section('p', 'c')
    assert doc['strategies'][0]['rankable'] is False and doc['strategies'][0]['incomplete'] == ['funding_unread']


def test_shadow_only_needs_a_positive_shadow():
    assert P.classify('k', X(closed=5), [X(exp=-1, pf=0.5)], set())[0] == 'UNDECIDED'


def test_wave2_cell_must_match_its_summary_pin(tmp_path):
    d = wave2_packet(tmp_path)
    sm = json.loads((d / 'summary.json').read_text())
    sm['cells']['candidate.x.long.15m']['cell_sha256'] = '0' * 64
    (d / 'summary.json').write_text(json.dumps(sm))
    with pytest.raises(P.PhaseCError, match='summary pin'): P.wave2_section(str(d))


def test_unknown_counterfactual_kind_is_unevaluated_never_a_policy_result():
    rows = [row(1, 'c')]
    cf = P.counterfactual_section(rows, [dict(kind='trade_audit', id=rows[0]['id'],
                                              counterfactuals=[dict(kind='what_if', rule='cap', vs_actual=5.0)])])
    assert cf['policies'][0]['evaluated'] == 0 and cf['policies'][0]['unevaluated'] == 1


def test_incomplete_shadow_coverage_keeps_the_report_incomplete_even_with_wave2(tmp_path, monkeypatch):
    cid = cohort()['id']
    s, man, _ = frozen(tmp_path, rows=[row(i, cid) for i in range(20)], shadow=episodes(20))
    monkeypatch.setattr(P, 'SHADOW_MODEL', dict(P.SHADOW_MODEL, fee_rate=0.0004))
    b = P.build(str(s), str(man), [cid], wave2=str(wave2_packet(tmp_path)))
    assert b['cohort_status'] == [] and b['report_status'] == 'INCOMPLETE'


def test_a_pinned_file_reported_as_a_link_is_refused(tmp_path, monkeypatch):
    s, man, rec = frozen(tmp_path, rows=[row(1, 'x')])
    real = os.path.islink
    monkeypatch.setattr(P.os.path, 'islink', lambda p: p.endswith('history.json') or real(p))
    with pytest.raises(P.PhaseCError, match='link'): P.build(str(s), str(man), [rec['id']])


def test_missing_rank_shadow_ledger_is_a_named_missing_input(tmp_path):
    cid = cohort()['id']
    s, man, _ = frozen(tmp_path, rows=[row(i, cid) for i in range(20)])
    assert f'missing_input:strategy_shadow/{cid}.jsonl' in P.build(str(s), str(man), [cid])['cohort_status']


def test_a_manifest_entry_that_is_not_a_pin_record_is_refused(tmp_path):
    s, man, rec = frozen(tmp_path, rows=[row(1, 'x')])
    m = json.loads(man.read_text()); m['files']['history.json'] = 'deadbeef'; man.write_text(json.dumps(m))
    with pytest.raises(P.PhaseCError, match='not a pin record'): P.build(str(s), str(man), [rec['id']])
