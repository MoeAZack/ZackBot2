"""Minimal Binance USD-M futures REST client (requests + HMAC), hedge-mode aware."""
import hashlib, hmac, time, urllib.parse
import requests

MAINNET = 'https://fapi.binance.com'
TESTNET = 'https://testnet.binancefuture.com'


class BinanceError(Exception):
    def __init__(self, code, msg):
        super().__init__(f'{code}: {msg}')
        self.code, self.msg = code, msg


class Futures:
    def __init__(self, key='', secret='', base=TESTNET, recv_window=6000):
        self.key, self.secret, self.base, self.rw = key, (secret or '').encode(), base, recv_window
        self.s = requests.Session()
        if key: self.s.headers['X-MBX-APIKEY'] = key
        self.offset = 0
        try: self.sync_time()
        except Exception: pass

    def sync_time(self):
        st = self.s.get(self.base + '/fapi/v1/time', timeout=10).json()['serverTime']
        self.offset = st - int(time.time() * 1000)

    def _req(self, method, path, params=None, signed=False):
        params = {k: v for k, v in (params or {}).items() if v is not None}
        if signed:
            params['timestamp'] = int(time.time() * 1000) + self.offset
            params['recvWindow'] = self.rw
            q = urllib.parse.urlencode(params)
            params['signature'] = hmac.new(self.secret, q.encode(), hashlib.sha256).hexdigest()
        for attempt in range(3):
            try:
                r = self.s.request(method, self.base + path, params=params, timeout=15)
            except requests.RequestException:
                if attempt == 2: raise
                time.sleep(2); continue
            data = r.json() if r.content else {}
            if isinstance(data, dict) and 'code' in data and data['code'] not in (0, 200):
                if data['code'] == -1021 and attempt < 2:
                    self.sync_time(); continue
                raise BinanceError(data['code'], data.get('msg'))
            return data

    # ---------- public ----------
    def exchange_info(self):
        return self._req('GET', '/fapi/v1/exchangeInfo')

    def klines(self, symbol, interval='4h', limit=1500, end_time=None, start_time=None):
        return self._req('GET', '/fapi/v1/klines', dict(symbol=symbol, interval=interval, limit=limit,
                                                          endTime=end_time, startTime=start_time))

    def marks(self):
        return {x['symbol']: float(x['markPrice']) for x in self._req('GET', '/fapi/v1/premiumIndex')}

    def premium(self, symbol):
        return self._req('GET', '/fapi/v1/premiumIndex', dict(symbol=symbol))

    def tickers_24h(self):
        return self._req('GET', '/fapi/v1/ticker/24hr')

    # ---------- account ----------
    def account(self):
        return self._req('GET', '/fapi/v2/account', signed=True)

    def positions(self):
        """{(symbol, 'LONG'|'SHORT'): abs qty}"""
        out = {}
        for p in self._req('GET', '/fapi/v2/positionRisk', signed=True):
            amt = float(p['positionAmt'])
            if amt == 0: continue
            side = p.get('positionSide', 'BOTH')
            if side == 'BOTH': side = 'LONG' if amt > 0 else 'SHORT'
            out[(p['symbol'], side)] = out.get((p['symbol'], side), 0) + abs(amt)
        return out

    def hedge_mode(self):
        return bool(self._req('GET', '/fapi/v1/positionSide/dual', signed=True).get('dualSidePosition'))

    def set_hedge_mode(self, on=True):
        try:
            return self._req('POST', '/fapi/v1/positionSide/dual', dict(dualSidePosition='true' if on else 'false'), signed=True)
        except BinanceError as e:
            if e.code == -4059: return None       # already set
            raise

    def set_leverage(self, symbol, lev):
        return self._req('POST', '/fapi/v1/leverage', dict(symbol=symbol, leverage=int(lev)), signed=True)

    def set_margin_type(self, symbol, mtype='CROSSED'):
        try:
            return self._req('POST', '/fapi/v1/marginType', dict(symbol=symbol, marginType=mtype), signed=True)
        except BinanceError as e:
            if e.code == -4046: return None
            raise

    # ---------- orders (hedge mode: positionSide LONG/SHORT) ----------
    def open(self, symbol, pos_side, qty):
        side = 'BUY' if pos_side == 'LONG' else 'SELL'
        return self._req('POST', '/fapi/v1/order', dict(symbol=symbol, side=side, positionSide=pos_side, type='MARKET',
                                                         quantity=qty, newOrderRespType='RESULT'), signed=True)

    def close(self, symbol, pos_side, qty):
        side = 'SELL' if pos_side == 'LONG' else 'BUY'
        return self._req('POST', '/fapi/v1/order', dict(symbol=symbol, side=side, positionSide=pos_side, type='MARKET',
                                                         quantity=qty, newOrderRespType='RESULT'), signed=True)

    def stop(self, symbol, pos_side, qty, stop_price):
        """Exchange-side stop for qty of the given position side. Returns 'o:<id>' or 'a:<algoId>'."""
        side = 'SELL' if pos_side == 'LONG' else 'BUY'
        try:
            r = self._req('POST', '/fapi/v1/order', dict(symbol=symbol, side=side, positionSide=pos_side, type='STOP_MARKET',
                          quantity=qty, stopPrice=stop_price, workingType='MARK_PRICE'), signed=True)
            return f"o:{r['orderId']}"
        except BinanceError as e:
            if e.code not in (-4120, -1116, -1102, -4136):
                raise
            r = self._req('POST', '/fapi/v1/algoOrder', dict(algoType='CONDITIONAL', symbol=symbol, side=side,
                          positionSide=pos_side, type='STOP_MARKET', quantity=qty, triggerPrice=stop_price,
                          workingType='MARK_PRICE'), signed=True)
            return f"a:{r['algoId']}"

    def cancel(self, symbol, tag):
        if not tag: return
        kind, oid = tag.split(':', 1)
        try:
            if kind == 'o':
                self._req('DELETE', '/fapi/v1/order', dict(symbol=symbol, orderId=oid), signed=True)
            else:
                self._req('DELETE', '/fapi/v1/algoOrder', dict(algoId=oid), signed=True)
        except BinanceError:
            pass

    def open_stop_tags(self, symbol):
        """Tags of stop orders still open on the exchange for this symbol (classic + algo)."""
        tags = set()
        for o in self._req('GET', '/fapi/v1/openOrders', dict(symbol=symbol), signed=True) or []:
            tags.add(f"o:{o['orderId']}")
        try:
            r = self._req('GET', '/fapi/v1/openAlgoOrders', dict(symbol=symbol), signed=True)
            for o in (r.get('orders', r) if isinstance(r, dict) else r) or []:
                if isinstance(o, dict) and 'algoId' in o: tags.add(f"a:{o['algoId']}")
        except BinanceError:
            pass
        return tags

    def cancel_all(self, symbol):
        for path in ('/fapi/v1/allOpenOrders', '/fapi/v1/algoOpenOrders'):
            try: self._req('DELETE', path, dict(symbol=symbol), signed=True)
            except BinanceError: pass
