"""Stack interaction: T05a (trade audit / not-taken funnel) on top of T05b (outage circuit, incidents) and T03c.
Outage skips get their own funnel code (not other/other); the status health dict carries BOTH the T05b incident
view and the T05a audit summary; T03c's legacy maker text keeps its why as the funnel detail."""
import os, sys
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from test_outage import _outage_engine                         # noqa: E402
from test_safety import SL, SG, opened                          # noqa: E402
import engine as E                                              # noqa: E402
import trade_audit as TA                                        # noqa: E402


def test_an_outage_skip_is_coded_in_the_funnel_and_status_shows_incidents_and_audit():
    import app as A
    e, h, clk, down = _outage_engine()
    try:
        assert opened(e); e.manage(e.trade.marks())
        down['v'] = True
        for _ in range(4): e.manage(e.trade.marks())
        why = e.entry_block(SL, 'BTCUSDT', 'LONG')
        assert why == 'Binance outage - no new entries until it answers again'
        e.miss(SL, 'BTCUSDT', 'LONG', SG, why)
        m = e.missed[-1]
        assert (m['stage'], m['code']) == ('connectivity', 'exchange_outage') and m['reason'] == why
        srv = A.App.__new__(A.App); srv.loop_ok = 0.0
        out = A.App.health(srv, e, [])
        assert [i['key'] for i in out['incidents']] == ['exchange-down'] and out['exchange_circuit']['state'] == 'outage'
        assert out['audit'] is not None and out['audit']['funnel'].get('connectivity/exchange_outage') == 1
    finally:
        E.close_fill_writer(e.F['audit'])


def test_wrapped_outage_and_legacy_maker_texts_keep_their_inner_cause():
    i = TA.reason_info('maker entry not filled; market fallback blocked: Binance outage - no new entries until it answers again')
    assert (i['stage'], i['code'], i['detail']) == ('execution', 'maker_fallback_blocked', 'connectivity/exchange_outage')
    i = TA.reason_info('maker entry not filled; ' + E.LEGACY_LEV_EXC_MAKER)
    assert (i['stage'], i['code']) == ('execution', 'maker_unfilled') and i['detail'] == E.LEGACY_LEV_EXC_MAKER
    assert TA.reason_code('maker entry not filled (no market fallback)') == ('execution', 'maker_unfilled')
