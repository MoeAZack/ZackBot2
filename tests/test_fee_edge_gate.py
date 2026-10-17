"""PAPER fee economics: high-turnover entries must have room for conservative round-trip costs."""
from test_safety import mk_engine, SL, SG


def test_fee_gate_off_preserves_existing_entry():
    e, _ = mk_engine(FEE_EDGE_GATE=False)
    assert e.open_lot(SL, 'BTCUSDT', 'LONG', dict(SG, atr=0.01), None, e.equity())


def test_fee_gate_refuses_cost_larger_than_one_third_stop_risk():
    e, _ = mk_engine(FEE_EDGE_GATE=True, FEE_EDGE_MULT=3.0)
    assert not e.open_lot(SL, 'BTCUSDT', 'LONG', dict(SG, atr=0.01), None, e.equity())
    assert e.last_skip.startswith('fee_edge:') and '3x' in e.last_skip and not e.state['lots']


def test_fee_gate_accepts_economic_entry_and_manual_is_unchanged():
    e, _ = mk_engine(FEE_EDGE_GATE=True, FEE_EDGE_MULT=3.0)
    assert e.open_lot(SL, 'BTCUSDT', 'LONG', SG, None, e.equity())
    e2, _ = mk_engine(FEE_EDGE_GATE=True, FEE_EDGE_MULT=20.0)
    assert e2.open_lot(None, 'BTCUSDT', 'LONG', dict(SG, atr=0.01), None, e2.equity(),
                       risk=0.01, manual=True, stop_atr=2.0, tp_r=3.0)
