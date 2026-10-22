"""FILL-PNL-02 (#178, pre-staged): measured P&L propagated to every consumer that claims actual performance. Exact
runtime fixtures from #176; the consumer assertions are added with the implementation (after #177 merges)."""
import os, sys
import pytest
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import actual_costs as AC                                                # noqa: E402
import trade_audit as TA                                                 # noqa: E402


def test_176_pepe_short_time_exit_measured_net():
    """Codex 6329983887: 1000PEPEUSDT SC-MBURST short, qty 420,579; measured entry 0.0046053641 (booked basis
    0.0046182), measured exit 0.0046228; fees 1.55246880 -> gross -7.33317339, net -8.88564219 (pre-fix row -2.8681)."""
    q = 420579.0
    t = dict(side='SHORT', fills=[['t0', 'entry', q, 0.0046182], ['t1', 'time_exit', q, 0.0046228]],
             fill_refs=[[0, 'c1-1-0'], [1, 'c1-1-1']])
    AC.apply_px(t, dict(cost_ref='c1-1-0', actual=0.0046053641)); AC.apply_px(t, dict(cost_ref='c1-1-1', actual=0.0046228))
    AC.finalize_px(t, -1, TA.ENTRY_KINDS)
    booked, _ = AC._replay(t['fills'], -1, TA.ENTRY_KINDS, {})
    assert t['fill_px_partial'] is False
    assert booked + t['pnl_fill_adj'] == pytest.approx(-7.33317339, abs=1e-6)
    assert booked + t['pnl_fill_adj'] - 1.55246880 == pytest.approx(-8.88564219, abs=1e-6)


# ---------------------------------------------------------------- #178 consumer contract (pre-staged; Codex ruling 6329976227)
# Skipped until the implementation (branched from merged #177) adds AC.measured / AC.capital_hold.
needs_impl = pytest.mark.skipif(not (hasattr(AC, 'measured') and hasattr(AC, 'capital_hold')), reason='#178 not implemented yet')


def _row(i, gross, fees_act=0.0, adj=None, partial=False, funding=None, closed='2026-10-22T00:00:00'):
    r = dict(id=f'r{i}', closed=closed, pnl_gross=gross, fees=abs(fees_act), fees_actual=fees_act, pnl=gross - fees_act)
    if adj is not None: r.update(fill_refs=[[0, f'c{i}']], pnl_fill_adj=adj, fill_px_partial=partial)
    if funding is not None: r['funding_actual'] = funding
    return r


@needs_impl
def test_measured_matches_true_books_net():
    """net = booked gross + pnl_fill_adj - fees_actual + funding_actual (true_books per-row formula)."""
    m = AC.measured(_row(1, -2.8681, fees_act=1.5524688, adj=-4.46507339, funding=0.25))
    assert m['complete'] is True and m['why'] == []
    assert m['net'] == pytest.approx(-2.8681 - 4.46507339 - 1.5524688 + 0.25, abs=1e-8)


@needs_impl
@pytest.mark.parametrize('bad,why', [(dict(fill_px_partial=['fill_px_pending']), 'fill_px_pending'),
                                     (dict(fees_actual=None), 'fees_actual_missing'),
                                     (dict(funding_partial=['funding_unattributed']), 'funding_unattributed')])
def test_measured_incomplete_is_never_actual(bad, why):
    r = _row(2, 5.0, fees_act=0.5, adj=0.0); r.update(bad)
    m = AC.measured(r)
    assert m['complete'] is False and why in m['why']


@needs_impl
def test_hold_at_checkpoint_until_every_new_row_is_measured():
    """Ruling 1: an incomplete row is never excluded while compounding continues - capital holds at the last fully
    reconciled checkpoint (base if none), exposes realized_unmeasured_n + the hold reason, then revises exactly once."""
    a, b = _row(1, 10.0, 1.0, adj=0.0), _row(2, -4.0, 0.5, adj=-1.0, partial=['fill_px_pending'])
    h = AC.capital_hold([a, b], None)
    assert h['realized'] == 0.0 and h['realized_unmeasured_n'] == 1 and h['hold']          # no checkpoint yet -> base
    c1 = AC.capital_hold([a], None)
    assert c1['realized'] == pytest.approx(9.0) and not c1['hold'] and c1['realized_unmeasured_n'] == 0
    h2 = AC.capital_hold([a, b], c1['checkpoint'])
    assert h2['realized'] == pytest.approx(9.0) and h2['hold'] and h2['checkpoint'] == c1['checkpoint']
    b.update(fill_px_partial=False)                                                     # reconciliation completes
    r1 = AC.capital_hold([a, b], h2['checkpoint'])
    assert r1['realized'] == pytest.approx(9.0 - 5.5) and not r1['hold']
    r2 = AC.capital_hold([a, b], r1['checkpoint'])                                      # idempotent: revised once
    assert r2['realized'] == r1['realized'] and r2['checkpoint'] == r1['checkpoint']


@needs_impl
def test_booked_pnl_never_reaches_capital_when_measured_differs():
    """AIN-shape: booked net looks small, measured net is worse - capital uses the measured value only."""
    r = _row(3, -0.30, 2.832, adj=-0.5877)                      # booked gross -0.30, measured gross -0.8877
    h = AC.capital_hold([r], None)
    assert h['realized'] == pytest.approx(-0.8877 - 2.832, abs=1e-6)


def test_179_xrp_short_partial_tp1_measured_net():
    """Codex 6330860693: XRPUSDT SC-QBANK short, entry 3,507.8 booked at the planned 1.35508 (measured 1.3524780803),
    TP1 closed 2,455.4 measured at 1.3512 -> gross +3.13819837; fees 1.32834984 + 1.32709459 -> net +0.48275394
    (pre-fix runtime booked +12.2278). The 1,052.4 remainder is still open."""
    t = dict(side='SHORT', fills=[['t0', 'entry', 3507.8, 1.35508], ['t1', 'take_profit_1', 2455.4, 1.3501]],
             fill_refs=[[0, 'c1-1-0'], [1, 'c1-1-1']])
    AC.apply_px(t, dict(cost_ref='c1-1-0', actual=1.3524780803)); AC.apply_px(t, dict(cost_ref='c1-1-1', actual=1.3512))
    AC.finalize_px(t, -1, TA.ENTRY_KINDS)
    booked, avg = AC._replay(t['fills'], -1, TA.ENTRY_KINDS, {})
    assert t['fill_px_partial'] is False
    assert booked + t['pnl_fill_adj'] == pytest.approx(3.13819837, abs=1e-6)
    assert booked + t['pnl_fill_adj'] - 1.32834984 - 1.32709459 == pytest.approx(0.48275394, abs=1e-6)
    assert avg + t['avg_fill_adj'] == pytest.approx(1.3524780803, abs=1e-10)       # remainder basis = measured entry
