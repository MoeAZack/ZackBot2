"""Prepare account-3 native cohort while PAUSED. Unpause is a separate normal app action."""
import math

def prepare_cohort(engine, port, testnet, destination):
    with engine.lock:
        if port != 8767 or engine.live or engine.trade.base != testnet or not engine.S.get('ENTRIES_PAUSED'):
            raise ValueError('Paused account 3 testnet required')
        if engine.trade.positions(critical=True, retry=False) or engine.trade.open_orders_all():
            raise ValueError('Account 3 must be flat with no working orders')
        balance = float(engine.trade.account()['totalMarginBalance'])
        if not engine.trade.hedge_mode() or not math.isfinite(balance) or balance < 4000:
            raise ValueError('Account 3 hedge/balance gate failed')
        if len(engine.S.get('SLEEVES',[])) != 3 or any(not s.get('mgmt',{}).get('prot') for s in engine.S['SLEEVES']):
            raise ValueError('Protected three-sleeve settings required')
        if engine.save_settings() is False:
            raise OSError('Paused settings were not durably saved')
        # Never persist an unpaused state here. Native boundary WAL handles a crash;
        # ordinary /api/settings unpause is only allowed after this gate succeeds.
        return engine.cohort_boundary(str(destination), note='NEW3-PROT-v1 prepared paused; manual drill archived; expected later activation-flag drift recorded separately; PR333 bdd0cc7a', accept_drift=True)
