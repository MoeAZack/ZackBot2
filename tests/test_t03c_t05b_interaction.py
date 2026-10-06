"""Stack interaction: T03c (auto-leverage exception proof) on top of T05b (exchange-outage circuit).
The T03c reads are plain status checks, so during a known outage they fail fast with ExchangeUnavailable - and every
such failure must REJECT the above-cap exception (never be read as 'no positions / no orders'), and keep adds paused."""
import os, sys
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from test_outage import client, Clock, BUSY, _nosleep          # noqa: E402
from test_leverage_auto import refusing, add_lot, check, entry_skipped, _no_sleep   # noqa: E402,F401
import binance_client as BC                                     # noqa: E402


def _outage_client(monkeypatch):
    clk = Clock(); _nosleep(monkeypatch, clk)
    c, calls = client(lambda *a: BUSY(), clk)
    try: c._req('GET', '/fapi/v2/positionRisk', signed=True)
    except BC.BinanceError: pass
    assert c.health.state == 'outage'
    return c, calls


def test_t03c_reads_fail_fast_with_exchange_unavailable_during_an_outage(monkeypatch):
    c, calls = _outage_client(monkeypatch); n = len(calls)
    for read in (c.position_risk, c.open_orders_all, lambda: c.leverage_brackets('BTCUSDT'), lambda: c.margin_state('BTCUSDT')):
        try: read(); raise AssertionError('must fail fast')
        except BC.ExchangeUnavailable as e: assert BC.is_transient(e)
    assert len(calls) == n                                     # no network traffic while waiting for the probe


def test_exposure_proof_rejects_on_snapshot_when_the_reads_hit_the_outage_circuit(monkeypatch):
    c, _ = _outage_client(monkeypatch)
    e = refusing(); add_lot(e)
    assert check(e)[0]                                         # control: the same account is accepted on real answers
    e.trade.position_risk = c.position_risk
    ok, why, n = check(e)
    assert not ok and n['check'] == 'snapshot' and 'outage' in why
    e = refusing(); add_lot(e); e.trade.open_orders_all = c.open_orders_all
    ok, why, n = check(e)
    assert not ok and n['check'] == 'snapshot'


def test_unknown_leverage_from_the_outage_circuit_skips_and_keeps_adds_paused(monkeypatch):
    c, _ = _outage_client(monkeypatch)
    e = refusing(); e.trade.margin_state = c.margin_state
    r = entry_skipped(e, 'current leverage unknown')
    assert r['reason'] == 'leverage_unknown' and r['outcome'] == 'skipped' and r['exposure'] is None
    e = refusing(); add_lot(e, sym='BTCUSDT', lev_exception=True); e.dry = False
    e.trade.margin_state = c.margin_state
    assert 'paused' in (e._lev_exception_block('BTCUSDT') or '')
    assert any(l.get('lev_exception') for l in e.state['lots'].values())     # the flag is never cleared on an unknown read
