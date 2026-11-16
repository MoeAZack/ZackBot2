"""Isolated OLD3 PAPER-only launcher. Refuses an absent configuration."""
import os, sys
from pathlib import Path

root = Path(__file__).resolve().parent
data_root = root / 'runtime'
config = data_root / 'ZackBot' / 'config.env'
if not config.is_file():
    raise SystemExit('OLD3 config missing: refusing legacy credential import')
os.environ['LOCALAPPDATA'] = str(data_root)
sys.path.insert(0, str(root / 'source'))
import app

app.PORT = 8766
app.HOSTS = ('127.0.0.1:8766', 'localhost:8766')
app.ORIGINS = ('http://127.0.0.1:8766', 'http://localhost:8766')
original_load = app.load_cfg
original_write = app.write_cfg
def paper_load():
    if not config.is_file():
        raise RuntimeError('OLD3 config missing: refusing legacy credential import')
    cfg = original_load()
    if cfg.get('MODE') != 'paper':
        raise RuntimeError('OLD3 is restricted to PAPER')
    return cfg
def paper_write(updates):
    if updates.get('MODE') not in (None, 'paper') or updates.get('LIVE_CONFIRM'):
        raise ValueError('OLD3 is restricted to PAPER')
    return original_write(updates)
app.load_cfg = paper_load
app.write_cfg = paper_write
original_handle = app.handle
def paper_handle(path, body):
    if path != '/api/old3/reconcile':
        return original_handle(path, body)
    if body:
        raise ValueError('Reconciliation takes no parameters')
    engine = app.APP.engine
    with engine.lock:
        if app.PORT != 8766 or engine.live or engine.trade.base != app.ENG.TESTNET or not engine.S.get('ENTRIES_PAUSED'):
            raise ValueError('OLD3 paused testnet session required')
        try:
            positions = engine.trade.positions(critical=True, retry=False)
            orders = engine.trade.open_orders_all()
            hedge = engine.trade.hedge_mode()
            balance = float(engine.trade.account()['totalMarginBalance'])
        except Exception:
            raise ValueError('Authenticated testnet reconciliation failed') from None
        if not isinstance(positions, dict) or not isinstance(orders, list):
            raise ValueError('Exchange returned unexpected reconciliation data')
        return dict(positions=len(positions), working_orders=len(orders), hedge=bool(hedge), balance_at_least_4000=balance >= 4000)
app.handle = paper_handle

app.main()
