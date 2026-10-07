import os, sys
sys.path.insert(0, os.getcwd()); sys.path.insert(0, os.path.join(os.getcwd(), 'tests'))
import engine as E
from test_safety import SL, SG
from test_stop_verify import mk
from test_eng02_executed_qty import script

def test_verifier_keeps_provisional_stop_of_unconfirmed_sibling_entry():
    E.ORDER_LOOKUP_GAP_S = 0.0
    e, clk, _ = mk()
    fx = e.trade; SYM = 'BTCUSDT'
    slA = dict(SL, id='A', mgmt={}); slB = dict(SL, id='B', mgmt={}); e.S['SLEEVES'] = [slA, slB]
    assert e.open_lot(slA, SYM, 'LONG', dict(SG, close=fx.mark[SYM]), None, e.equity()), e.last_skip
    rec = dict(status='PARTIALLY_FILLED', executedQty='0.020', clientOrderId='zbB')
    fx.get_order = lambda s, c: dict(rec) if c == 'zbB' else None
    script(e, 'open', 0.02, 'PARTIALLY_FILLED', cid='zbB', executedQty='0.020')
    assert e.open_lot(slB, SYM, 'LONG', dict(SG, close=fx.mark[SYM]), None, e.equity()) is False
    prov = e.state['unconfirmed_entries']['zbB']['prov']
    assert prov and prov in fx.stops
    clk[0] += 10_000
    e.verify_stops('routine')
    errs = [x[1] for x in e.health['errors'] if 'extra bot stop' in x[1]]
    assert prov in fx.stops, ('provisional stop cancelled by ENG01 verifier', errs)

def test_churn_through_manage():
    E.ORDER_LOOKUP_GAP_S = 0.0
    e, clk, _ = mk()
    fx = e.trade; SYM = 'BTCUSDT'
    slA = dict(SL, id='A', mgmt={}); slB = dict(SL, id='B', mgmt={}); e.S['SLEEVES'] = [slA, slB]
    assert e.open_lot(slA, SYM, 'LONG', dict(SG, close=fx.mark[SYM]), None, e.equity())
    rec = dict(status='PARTIALLY_FILLED', executedQty='0.020', clientOrderId='zbB')
    fx.get_order = lambda s, c: dict(rec) if c == 'zbB' else None
    script(e, 'open', 0.02, 'PARTIALLY_FILLED', cid='zbB', executedQty='0.020')
    assert e.open_lot(slB, SYM, 'LONG', dict(SG, close=fx.mark[SYM]), None, e.equity()) is False
    hist = []
    for i in range(12):
        clk[0] += 400; e.manage(fx.marks())
        u = e.state['unconfirmed_entries'].get('zbB') or {}
        hist.append((u.get('prov'), u.get('prov') in fx.stops))
    extra = sum(1 for x in e.health['errors'] if 'extra bot stop' in x[1] and 'not owned' in x[1])
    print('HIST', hist, 'extra-cancels', extra, 'stops placed', fx.calls.count('stop'))
    assert extra == 0
