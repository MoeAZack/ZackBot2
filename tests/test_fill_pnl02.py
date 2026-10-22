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
