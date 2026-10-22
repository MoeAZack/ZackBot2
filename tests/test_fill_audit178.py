"""#178 canary (Codex 6342665810): the close trade_audit carries the row's ONE measured net, and a fully closed lot
reconciles in the cohort ledger. SAND = tp1 partial then exchange stop; AIN = direct exchange stop; adverse and
favorable measured-price corrections on both."""
import json, os, sys
import pytest
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))
import actual_costs as AC                                                # noqa: E402
import trade_audit as TA                                                 # noqa: E402
from test_safety import mk_engine, opened                                # noqa: E402


def _walk(e, marks):
    for m in marks:
        e.trade.mark['BTCUSDT'] = m; e.manage(e.trade.marks())


def _audits(e, rid):
    assert e._auditw.flush(5)
    p = e.F['audit']
    rows = [json.loads(x) for x in open(p)] if os.path.exists(p) else []
    return [r for r in rows if r.get('kind') == 'trade_audit' and r.get('id') == rid]


def _stop_out(e):
    e.trade.pos[('BTCUSDT', 'LONG')] = 0; e.trade.stops.clear(); e.reconcile(500)
    assert e.history[-1]['exit_reason'] == 'stop'
    return e.history[-1]


def _measure(e, row, adj, fees_extra=0.02):
    row.update(fees_actual=(row.get('fees') or 0.0) + fees_extra, fees_actual_partial=False, fill_px_partial=False,
               pnl_fill_adj=adj)
    e._row_changed(row)


def _sand(e):
    k = opened(e); _walk(e, (101.0, 104.0))
    e._market_close(e.state['lots'][k], 0.5, 'take_profit_1', 104.0)
    _walk(e, (100.0,))
    return k, _stop_out(e)


def _ain(e):
    k = opened(e); _walk(e, (100.5, 99.0))
    return k, _stop_out(e)


@pytest.mark.parametrize('path', [_sand, _ain], ids=['sand_partial_then_stop', 'ain_direct_stop'])
@pytest.mark.parametrize('adj', [+23.0, -0.6, +0.4], ids=['sign_flip', 'adverse', 'favorable'])
def test_close_audit_is_the_measured_row_truth(path, adj):
    import engine as E
    e, _ = mk_engine()
    try:
        k, row = path(e)
        assert AC.row_view(row)['status'] == 'pending' and _audits(e, k) == []     # never written from the booked net
        _measure(e, row, adj)
        v, m = AC.row_view(row), AC.measured(row)
        assert v['status'] == 'measured'
        au = _audits(e, k)
        assert len(au) == 1
        a = au[0]
        assert a['net_pnl'] == pytest.approx(m['net'], abs=1e-6) and a['net_basis'] == 'measured'
        assert a['pnl_booked'] == row['pnl'] and a['pnl_fill_adj'] == adj and a['fill_px_partial'] is False
        assert a['r'] == pytest.approx(m['net'] / row['risk_usd'], abs=1e-4)
        if adj == 23.0: assert row['pnl'] < 0 < a['net_pnl']                      # the SAND sign error cannot recur
        for c in a['counterfactuals']:                                           # every vs_actual on the same net
            if c.get('value') is not None and c.get('vs_actual') is not None:
                assert c['vs_actual'] == pytest.approx(c['value'] - m['net'], abs=1e-5)
        if a.get('giveback') is not None and a.get('peak_pnl') is not None:
            assert a['giveback'] == pytest.approx(a['peak_pnl'] - m['net'], abs=1e-5)
        assert a['qty_mismatch'] is None and not a['unmatched_close_qty']             # the final stop fill is in the ledger
        assert sum(c['open'] for c in a['cohorts']) == pytest.approx(0.0, abs=1e-12)
        assert 'cohort ledger does not reconcile' not in (a['coverage_why'] or ())
        assert not any('unknown fill kind' in x for x in a['limitations'])
        _measure(e, row, adj)                                                    # a restatement never writes it again
        assert len(_audits(e, k)) == 1 and row['id'] not in e.state.get('measured_wait', {})
    finally:
        E.close_fill_writer(e.F['audit'])


def test_pending_close_survives_a_restart_and_audits_once_measured():
    import engine as E
    e, _ = mk_engine()
    try:
        k, row = _ain(e)
        w = e.state['measured_wait'][row['id']]
        assert w['audit_lot']['fills'][-1][1] == 'stop' and 'ml_decision_id' not in w['audit_lot']
        json.loads(json.dumps(e.state['measured_wait']))                         # persisted with state.json
        _measure(e, row, -0.6)
        assert len(_audits(e, k)) == 1 and 'audit_lot' not in json.dumps(e.state.get('measured_wait') or {})
    finally:
        E.close_fill_writer(e.F['audit'])


def test_unreconciled_ledger_is_never_full_coverage():
    l = dict(side='LONG', avg=100.0, qty=0.0, realized=0.0, fees=0.0, risk_usd=5.0,
             fills=[['t0', 'entry', 1.0, 100.0], ['t1', 'stop', 0.4, 101.0]])           # a close fill is missing
    rec = TA.close_record(l, 1.0)
    assert rec['qty_mismatch'] is not None and rec['coverage'] == 'partial'
    assert 'cohort ledger does not reconcile' in rec['coverage_why']


# ---------------------------------------------------------------- already-emitted booked lines: append-only revision
def _booked_line(e, row):
    """What the installed fd2cad4 build wrote at the close: the audit on the booked net (simulated from the wait copy)."""
    lot = e.state['measured_wait'][row['id']].pop('audit_lot')
    au = TA.close_record(dict(lot, qty=0.0), row['pnl'])
    au.update(kind='trade_audit', id=row['id'], exit_reason='stop', closed=row['closed'], t=row['closed'])
    e._audit_emit(au)
    assert e._auditw.flush(5)
    return au


def test_booked_line_gets_one_appended_revision_and_the_original_is_untouched():
    import engine as E
    e, _ = mk_engine()
    try:
        k, row = _sand(e)
        orig = _booked_line(e, row)
        _measure(e, row, -23.0)
        net = AC.measured(row)['net']
        au = _audits(e, k)
        assert len(au) == 2 and au[0]['net_pnl'] == pytest.approx(orig['net_pnl']) and 'revision' not in au[0]
        rv = au[1]
        assert rv['revision'] == 1 and rv['net_pnl'] == pytest.approx(net, abs=1e-6) and rv['net_basis'] == 'measured'
        assert rv['revised_from_net'] == au[0]['net_pnl'] and rv['revises_t'] == au[0]['t'] and rv['pnl_booked'] == row['pnl']
        assert rv['r'] == pytest.approx(net / row['risk_usd'], abs=1e-4) and rv['metrics']['net_usd'] == pytest.approx(net, abs=1e-6)
        for c in rv['counterfactuals']:
            if c.get('vs_actual') is not None: assert c['vs_actual'] == pytest.approx(c['value'] - net, abs=1e-5)
        assert e._tb_excursions()[k]['giveback_r'] == rv['giveback_r']           # the consumer takes the latest line
        _measure(e, row, -23.0)                                                  # same net again: nothing appended
        assert len(_audits(e, k)) == 2
    finally:
        E.close_fill_writer(e.F['audit'])


def test_install_reconcile_revises_once_and_a_restart_never_duplicates(tmp_path):
    import engine as E
    e, _ = mk_engine(str(tmp_path))
    k, row = _ain(e)
    _booked_line(e, row)
    e._audit_revise = lambda r: False                                            # measured under the old build
    _measure(e, row, -0.6); e._save_aux('history', e.history); e.save_state()
    assert len(_audits(e, k)) == 1
    E.close_fill_writer(e.F['audit'])
    for n in (2, 2):                                                             # install, then a plain restart
        e2, _ = mk_engine(str(tmp_path))
        try: assert len(_audits(e2, k)) == n and _audits(e2, k)[-1]['net_pnl'] == pytest.approx(AC.measured(row)['net'], abs=1e-6)
        finally: E.close_fill_writer(e2.F['audit'])


def test_dashboard_history_shows_the_settled_net_never_the_booked_one():
    import app as A
    e, _ = mk_engine()
    import engine as E
    try:
        k, row = _ain(e)
        v = A._hist_view(row)
        assert v['pnl'] is None and v['r'] is None and v['pnl_status'] == 'pending' and v['pnl_booked'] == row['pnl']
        _measure(e, row, -0.6)
        v = A._hist_view(row)
        assert v['pnl'] == AC.row_view(row)['pnl'] != row['pnl'] and v['pnl_status'] == 'measured' and v['r'] == AC.row_view(row)['r']
        assert A._hist_view(dict(id='x', pnl=1.5, r=0.3)) == dict(id='x', pnl=1.5, r=0.3)        # legacy row unchanged
        assert 'pnl_status' not in row                                                         # the stored row is untouched
    finally:
        E.close_fill_writer(e.F['audit'])


def test_revise_close_recomputes_the_net_flags():
    orig = dict(kind='trade_audit', id='x', t='t0', net_pnl=1.0, peak_pnl=4.0, flags=['never_green_x'], flags_unknown=None,
                metrics=dict(peak_net_usd=4.0, executable_mfe_usd=3.0), counterfactuals=[dict(value=2.0, vs_actual=1.0), dict(value=None, vs_actual=None)])
    rv = TA.revise_close(orig, -2.0, 'measured', 4.0, 't1')
    assert rv['flags'] == ['never_green_x', 'green_to_red', 'gave_back_gt_50pct_mfe'] and rv['giveback'] == 6.0 and rv['r'] == -0.5
    assert rv['counterfactuals'][0]['vs_actual'] == 4.0 and rv['counterfactuals'][1]['vs_actual'] is None
    assert orig['net_pnl'] == 1.0 and orig['counterfactuals'][0]['vs_actual'] == 1.0                     # never edited
    assert TA.revise_close(dict(kind='funnel'), 1.0, 'measured', 1.0, 't') is None


def test_a_revision_supersedes_never_double_counts_in_attribution_or_segments():
    base = dict(kind='trade_audit', sleeve='A', tf='1h', side='LONG', symbol='SANDUSDT', coverage='full')
    recs = [dict(base, id='a', t='1', net_pnl=3.17, r=0.16), dict(base, id='b', t='2', net_pnl=-1.0, r=-0.1),
            dict(kind='cohort', id='a', t='2'), dict(base, id='a', t='3', net_pnl=-20.36, r=-1.02, revision=1)]
    assert [r.get('net_pnl') for r in TA.latest_closes(recs) if r.get('kind') == 'trade_audit'] == [-1.0, -20.36]
    a = TA.attribution(recs)
    assert a['closes'] == 2 and sum(x['n'] for x in a['groups']) == 2
    assert TA.segments(recs)['trades'] == 2
    win = [TA.slim_record(r) for r in recs if TA.slim_record(r)]
    assert [r['net_pnl'] for r in TA.latest_closes(win)] == [-1.0, -20.36]


def test_a_settling_row_refreshes_the_dashboard_history():
    """Cowork 6345088881 P2: the panel refetches /api/history only when revs()['history'] changes - a pending row that
    settles (or is restated) must change it although no trade was added."""
    import app as A
    import engine as E
    e, _ = mk_engine()
    try:
        k, row = _ain(e)
        before = A.App.revs(None, e)['history']
        _measure(e, row, -0.6)
        mid = A.App.revs(None, e)['history']
        assert mid != before
        _measure(e, row, -0.7)                                                   # a later restatement too
        assert A.App.revs(None, e)['history'] != mid
    finally:
        E.close_fill_writer(e.F['audit'])
