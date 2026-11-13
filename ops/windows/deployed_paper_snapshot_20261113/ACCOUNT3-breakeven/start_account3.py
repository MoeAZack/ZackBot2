"""Isolated PAPER-only account 3 launcher."""
import os, sys
from pathlib import Path
root = Path(__file__).resolve().parent
config = root / 'runtime' / 'ZackBot' / 'config.env'
if not config.is_file():
    raise SystemExit('Account 3 config missing: refusing legacy credential import')
os.environ['LOCALAPPDATA'] = str(root / 'runtime')
sys.path.insert(0, str(root / 'source'))
import app
app.PORT = 8767
app.HOSTS = ('127.0.0.1:8767', 'localhost:8767')
app.ORIGINS = ('http://127.0.0.1:8767', 'http://localhost:8767')
original_load, original_write = app.load_cfg, app.write_cfg
def paper_load():
    if not config.is_file():
        raise RuntimeError('Account 3 config missing: refusing legacy credential import')
    cfg = original_load()
    if cfg.get('MODE') != 'paper':
        raise RuntimeError('Account 3 is restricted to PAPER')
    return cfg
def paper_write(updates):
    if updates.get('MODE') not in (None, 'paper') or updates.get('LIVE_CONFIRM'):
        raise ValueError('Account 3 is restricted to PAPER')
    return original_write(updates)
app.load_cfg, app.write_cfg = paper_load, paper_write
original_handle = app.handle
def paper_handle(path, body):
    if path == '/api/testnet/prepare-protected-cohort':
        if body:
            raise ValueError('Cohort preparation takes no parameters')
        import json
        accepted = json.loads((root / 'prot333-acceptance.json').read_text())
        if accepted.get('head') != 'bdd0cc7a3de1c5a17e0991ce169758eeea610e9f' or accepted.get('status') != 'accepted':
            raise ValueError('Exact-head protection acceptance required')
        from account3_activation import prepare_cohort
        return prepare_cohort(app.APP.engine, app.PORT, app.ENG.TESTNET, root / 'cohort-archives')
    if path != '/api/testnet/reconcile':
        return original_handle(path, body)
    if body:
        raise ValueError('Reconciliation takes no parameters')
    engine = app.APP.engine
    with engine.lock:
        if app.PORT != 8767 or engine.live or engine.trade.base != app.ENG.TESTNET or not engine.S.get('ENTRIES_PAUSED'):
            raise ValueError('Isolated paused testnet session required')
        try:
            positions = engine.trade.positions(critical=True, retry=False)
            orders = engine.trade.open_orders_all()
            hedge = engine.trade.hedge_mode()
            balance = float(engine.trade.account()['totalMarginBalance'])
        except Exception:
            raise ValueError('Authenticated testnet reconciliation failed') from None
        if not isinstance(positions, dict) or not isinstance(orders, list):
            raise ValueError('Exchange returned unexpected reconciliation data')
        return dict(prot_loaded=hasattr(app.ENG, 'PROT'), positions=len(positions), working_orders=len(orders), hedge=bool(hedge), balance_at_least_4000=balance >= 4000)
app.handle = paper_handle

app.main()
