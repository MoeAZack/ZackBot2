"""RISK-FILL-01 (#179, pre-staged): post-fill risk budget from the measured entry. Exact PAPER fixtures from Codex
(XRP 6330701070 severe, AAVE 6331284896 moderate). Skipped until risk_fill.py lands (after #180; Codex rules tolerance,
ceiling and trim-vs-re-anchor first - the defaults here are Build's proposal, read from the module)."""
import os, sys
import pytest
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
RF = pytest.importorskip('risk_fill')


def test_xrp_short_measured_risk_and_trim():
    """XRP SC-QBANK short 3,507.8, stop 1.3608, planned 1.35507997 -> 20.07; measured 1.3524780803 -> 29.19 (+45%)."""
    q, stop = 3507.8, 1.3608
    planned = RF.price_risk('SHORT', q, 1.35507997, stop)
    m = RF.price_risk('SHORT', q, 1.3524780803, stop)
    assert planned == pytest.approx(20.07, abs=0.01) and m == pytest.approx(29.19, abs=0.01)
    d = RF.decide('SHORT', q, 1.3524780803, stop, planned_risk=planned, entry_fees=1.897689)
    assert d['action'] == 'trim' and 0 < d['trim_qty'] < q
    left = q - d['trim_qty']
    assert RF.price_risk('SHORT', left, 1.3524780803, stop) + d['fee_risk_left'] <= planned * (1 + RF.TOL) + 1e-9


def test_aave_short_moderate_overshoot():
    """AAVE SC-MBURST short, stop 152.13, planned 150.6526992 -> 20.68; measured 150.4417142857 -> 23.64 (+14.3%)."""
    q, stop = 14.0, 152.13
    planned = RF.price_risk('SHORT', q, 150.6526992, stop)
    assert planned == pytest.approx(20.68, abs=0.01)
    assert RF.price_risk('SHORT', q, 150.4417142857, stop) == pytest.approx(23.64, abs=0.01)
    d = RF.decide('SHORT', q, 150.4417142857, stop, planned_risk=planned, entry_fees=0.8424736)
    assert d['action'] in ('trim', 'ok')               # +14.3% sits just above a 10% tolerance: trim if TOL < 0.143


@pytest.mark.parametrize('px,action', [(100.0, 'ok'), (99.9, 'ok'), (101.0, 'ok')])
def test_favorable_or_flat_fill_never_tops_up(px, action):
    """A LONG filled at or below plan: risk shrank or held - recorded only, never an add."""
    d = RF.decide('LONG', 10.0, min(px, 100.0), 98.0, planned_risk=20.0, entry_fees=0.0)
    assert d['action'] == action and d.get('trim_qty', 0) == 0


def test_fill_through_the_stop_unwinds():
    """A LONG filled at/below its stop has no valid stop distance: full unwind, never a trim to a nonsense size."""
    assert RF.decide('LONG', 10.0, 97.5, 98.0, planned_risk=20.0, entry_fees=0.0)['action'] == 'unwind'


def test_above_hard_ceiling_unwinds():
    d = RF.decide('LONG', 10.0, 101.0, 98.0, planned_risk=10.0, entry_fees=0.0)     # 30 vs 10 planned = 3x
    assert d['action'] == 'unwind'
