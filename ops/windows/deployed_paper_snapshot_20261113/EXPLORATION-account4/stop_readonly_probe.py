"""Account4-only read-only exchange evidence for two owner-named AVAX stops."""
from datetime import datetime,timezone
from urllib.parse import urlsplit
FIELDS=('algoId','symbol','side','positionSide','orderType','type','quantity','closePosition','reduceOnly','triggerPrice','workingType','algoStatus','actualOrderId','actualQty','triggerTime','updateTime')
IDS=('1000000284177013','1000000284342653')
def collect(engine,port,testnet):
    host=urlsplit(str(engine.trade.base)).hostname
    if port!=8768 or engine.live or engine.trade.base!=testnet or host not in ('testnet.binancefuture.com','demo-fapi.binance.com'):
        raise ValueError('Account4 PAPER/testnet only')
    rows=[]
    with engine.lock:
        for oid in IDS:
            try:
                raw=engine.trade._req('GET','/fapi/v1/algoOrder',dict(algoId=oid),signed=True,retry=False)
            except Exception:
                raise ValueError('Read-only exchange stop lookup failed') from None
            if not isinstance(raw,dict) or str(raw.get('algoId'))!=oid or raw.get('symbol')!='AVAXUSDT':
                raise ValueError('Unexpected exchange order identity')
            rows.append({k:raw[k] for k in FIELDS if k in raw})
    return dict(at_utc=datetime.now(timezone.utc).isoformat(),testnet_host=host,orders=rows)