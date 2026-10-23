"""TP-SLIP-01 (#189) measurement-first response (Codex 6363163458): every measured fill's slippage against its booked
price, per-symbol entry / exit telemetry for the dashboard and an alert at SLIP_ALERT_BPS adverse. Measurement only:
the measured fill is the authority and nothing about orders, lots or the booked numbers changes."""
import copy, os, sys
import pytest
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from test_fill_pnl01 import scenario, settle                             # noqa: E402
import actual_costs as AC                                                # noqa: E402

E_ = AC.OPENING_FILLS


@pytest.mark.parametrize('side,kind,booked,measured,want', [
    ('SHORT', 'take_profit_1', 0.03812705, 0.0383298596, 53.1931),       # the AIN TP1 (Codex: 53.19 bps): bought back above the booked price
    ('LONG', 'take_profit_1', 100.0, 99.5, 50.0),                        # long exit sold below booked: adverse
    ('LONG', 'entry', 100.0, 100.2, 20.0),                               # long entry bought above booked: adverse
    ('SHORT', 'entry', 100.0, 100.2, -20.0),                             # short entry sold above booked: favorable
    ('LONG', 'stop', 100.0, 100.1, -10.0),                               # long exit sold above booked: favorable
])
def test_slip_bps_sign_is_adverse_positive(side, kind, booked, measured, want):
    assert AC.slip_bps(side, kind, booked, measured, E_) == pytest.approx(want, abs=1e-3)


@pytest.mark.parametrize('args', [('LONG', 'entry', None, 1.0), ('LONG', 'entry', 0, 1.0), ('LONG', 'entry', 1.0, 'x'),
                                  ('FLAT', 'entry', 1.0, 1.0)])
def test_slip_bps_unreadable_is_none_and_never_counted(args):
    side, kind, b, m = args
    bps = AC.slip_bps(side, kind, b, m, E_)
    st = {}
    assert bps is None and AC.slip_note(st, 'X', 'exit', bps, 25.0) is False and st == {}


def test_slip_note_folds_and_alerts_at_the_threshold_with_a_bounded_recent_list():
    st = {}
    assert AC.slip_note(st, 'AINUSDT', 'exit', 24.99, 25.0) is False
    assert AC.slip_note(st, 'AINUSDT', 'exit', 25.0, 25.0) is True
    assert AC.slip_note(st, 'AINUSDT', 'exit', -5.0, 25.0) is False
    x = st['symbols']['AINUSDT']['exit']
    assert (x['n'], x['alerts'], x['worst_bps']) == (3, 1, 25.0) and x['sum_bps'] == pytest.approx(44.99)
    for i in range(AC.SLIP_RECENT + 5):
        AC.slip_note(st, 'ZROUSDT', 'exit', 30.0 + i, 25.0, kind='take_profit_1')
    assert len(st['_recent']) == AC.SLIP_RECENT and st['_recent'][-1]['bps'] == 30.0 + AC.SLIP_RECENT + 4


def test_a_measured_tp1_beyond_the_alert_notifies_and_reaches_the_dashboard_without_touching_the_lot():
    e, k, lot, q = scenario('SHORT', 100.0, 98.0, 100.2)                 # TP1 booked 98.0, measured 100.2: 224.5 bps
    sent = []
    e.notify = sent.append
    settle(e)
    lot = e.state['lots'][k]
    before = copy.deepcopy({x: lot[x] for x in ('realized', 'fills', 'qty', 'avg')})
    st = e.slippage_status()
    ex = st['symbols']['BTCUSDT']['exit']
    assert ex['n'] == 1 and ex['alerts'] == 1 and ex['worst_bps'] == pytest.approx((100.2 - 98.0) / 98.0 * 1e4, abs=1e-3)
    assert st['symbols']['BTCUSDT']['entry']['alerts'] == 0              # the entry filled 0 bps from its booking
    assert st['alert_bps'] == e.SLIP_ALERT_BPS and st['recent'][-1]['kind'] == 'take_profit_1'
    assert [m for m in sent if 'SLIPPAGE' in m and 'take_profit_1' in m and 'measured is the authority' in m]
    assert {x: lot[x] for x in before} == before                         # measurement only: the lot is untouched
    assert lot['realized'] + lot['pnl_fill_adj'] == pytest.approx(-0.2 * q)
    settle(e)                                                            # a re-applied record never double counts
    assert e.slippage_status()['symbols']['BTCUSDT']['exit']['n'] == 1


def test_a_small_measured_slip_is_counted_but_not_alerted():
    e, k, lot, q = scenario('LONG', 100.0, 103.0, 102.9)                 # 9.7 bps adverse on the TP1
    sent = []
    e.notify = sent.append
    settle(e)
    ex = e.slippage_status()['symbols']['BTCUSDT']['exit']
    assert ex['n'] == 1 and ex['alerts'] == 0 and ex['worst_bps'] == pytest.approx(0.1 / 103.0 * 1e4, abs=1e-3)
    assert not [m for m in sent if 'SLIPPAGE' in m]
