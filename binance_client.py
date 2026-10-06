"""Minimal Binance USD-M futures REST client (requests + HMAC), hedge-mode aware."""
import hashlib, hmac, math, os, random, time, urllib.parse, uuid
import requests

MAINNET = 'https://fapi.binance.com'
TESTNET = 'https://testnet.binancefuture.com'


class BinanceError(Exception):
    def __init__(self, code, msg):
        super().__init__(f'{code}: {msg}')
        self.code, self.msg = code, msg


def testnet_faults(base, kind):
    """T03c exceptional-path canary: symbols for which a controlled testnet failure of `kind` is injected, from the env
    flag ZB_TESTNET_FAULTS="lev_refuse:SOLUSDT,lev_refuse:ETHUSDT". Always empty unless `base` is exactly TESTNET, so the
    flag can never change a mainnet client. Malformed entries are ignored."""
    if base != TESTNET: return frozenset()
    out = set()
    for item in (os.environ.get('ZB_TESTNET_FAULTS') or '').split(','):
        k, _, sym = item.strip().partition(':')
        if k == kind and sym.isalnum() and sym.isupper(): out.add(sym)
    return frozenset(out)


class AmbiguousOrder(Exception):
    """An order request may or may not have reached Binance and its status could not be confirmed.
    .tag is a cancel tag for it ('c:<clientOrderId>' or 'ac:<clientAlgoId>') when one is known."""
    def __init__(self, msg, tag=None):
        super().__init__(msg); self.tag = tag


TRANSIENT = (-1001, -1003, -1006, -1007, -1008, -1015)      # disconnected / rate limit / timeout / server busy
SAFE_METHODS = ('GET', 'DELETE')


def new_cid(prefix='zb'):
    return f'{prefix}{uuid.uuid4().hex[:22]}'


def _finite_number(value, *, positive=False, nonnegative=False):
    """Strict exchange number: bool, missing and NaN/Inf are never data."""
    if isinstance(value, bool) or value in (None, ''): raise ValueError('missing or boolean number')
    number = float(value)
    if not math.isfinite(number): raise ValueError('non-finite number')
    if positive and number <= 0: raise ValueError('number is not positive')
    if nonnegative and number < 0: raise ValueError('number is negative')
    return number


class BracketSchedule(list):
    """T03c r2 follow-up. A leverage-bracket schedule (a plain list of tier dicts) that also carries, for telemetry, the raw
    notionalCoef Binance returned and the factor applied (always 1: any other coef is rejected)."""
    def __init__(self, rows=(), notional_coef=1.0, coef_applied=1.0):
        super().__init__(rows); self.notional_coef, self.coef_applied = notional_coef, coef_applied


def _positive_int(value):
    number = _finite_number(value, positive=True)
    if not number.is_integer(): raise ValueError('number is not an integer')
    return int(number)


_MISSING = object()


def _bool_field(row, key, default=_MISSING):
    value = row.get(key, default)
    if value is _MISSING: raise ValueError(f'{key}: missing boolean')
    if not isinstance(value, bool): raise ValueError(f'{key}: expected boolean')
    return value


class Futures:
    def __init__(self, key='', secret='', base=TESTNET, recv_window=6000):
        self.key, self.secret, self.base, self.rw = key, (secret or '').encode(), base, recv_window
        self.s = requests.Session()
        if key: self.s.headers['X-MBX-APIKEY'] = key
        self.offset = 0
        self.last_ok = 0.0
        try: self.sync_time()
        except Exception: pass

    def sync_time(self):
        st = self.s.get(self.base + '/fapi/v1/time', timeout=10).json()['serverTime']
        self.offset = st - int(time.time() * 1000)

    def _once(self, method, url, params, signed):
        p = dict(params)
        if signed:                                   # re-signed on every attempt (fresh timestamp)
            p['timestamp'] = int(time.time() * 1000) + self.offset
            p['recvWindow'] = self.rw
            q = urllib.parse.urlencode(p)
            p['signature'] = hmac.new(self.secret, q.encode(), hashlib.sha256).hexdigest()
        r = self.s.request(method, url, params=p, timeout=15)
        try:
            data = r.json() if r.content else {}
        except ValueError:
            data = {'code': -1000 - r.status_code, 'msg': f'HTTP {r.status_code} (not JSON)'}
        return r, data

    def _req(self, method, path, params=None, signed=False, retry=None):
        """GET/DELETE are retried on network errors, rate limits and server errors (with backoff and Retry-After).
        POST is never blindly retried: order placement goes through _order(), which confirms by client order id."""
        params = {k: v for k, v in (params or {}).items() if v is not None}
        retry = (method in SAFE_METHODS) if retry is None else retry
        url = self.base + path
        for attempt in range(4 if retry else 2):
            last = attempt == (3 if retry else 1)
            try:
                r, data = self._once(method, url, params, signed)
            except requests.RequestException:
                if not retry or last: raise
                time.sleep(min(8, 0.5 * 2 ** attempt + random.random())); continue
            code = data.get('code') if isinstance(data, dict) else None
            if isinstance(code, str):                   # algo endpoints answer {"code":"200","msg":"success"}
                try: code = int(code)
                except ValueError: pass
            if isinstance(data, dict) and str(data.get('msg', '')).lower() == 'success' and code in (200, '200'):
                code = 200
            if code == -1021 and not last:              # clock drift: resync and re-sign (safe, request was rejected)
                self.sync_time(); continue
            busy = r.status_code in (418, 429) or r.status_code >= 500 or code in TRANSIENT
            if busy:
                if not retry or last:
                    if method not in SAFE_METHODS and (r.status_code >= 500 or code in (-1001, -1006, -1007)):
                        raise AmbiguousOrder(f'HTTP {r.status_code} on {path}')
                    raise BinanceError(code or -r.status_code, data.get('msg') if isinstance(data, dict) else f'HTTP {r.status_code}')
                wait = float(r.headers.get('Retry-After') or 0) or min(8, 0.5 * 2 ** attempt + random.random())
                time.sleep(min(wait, 30)); continue
            if isinstance(data, dict) and code not in (None, 0, 200):
                raise BinanceError(code, data.get('msg'))
            self.last_ok = time.time()
            return data

    def get_order(self, symbol, cid):
        try:
            return self._req('GET', '/fapi/v1/order', dict(symbol=symbol, origClientOrderId=cid), signed=True)
        except BinanceError as e:
            if e.code == -2013: return None             # order does not exist
            raise

    def _order(self, params):
        """Idempotent order placement: a client order id is attached; on a timeout / 5xx the order is looked up
        by that id instead of being re-sent, so a lost response can never create a second position.
        A MARKET order that cannot be confirmed as filled raises AmbiguousOrder (tag = its client id)."""
        params = dict(params); params.setdefault('newClientOrderId', new_cid())
        cid, tag = params['newClientOrderId'], f"c:{params['newClientOrderId']}"
        try:
            return self._req('POST', '/fapi/v1/order', params, signed=True, retry=False)
        except (requests.RequestException, AmbiguousOrder) as first:
            for i in range(4):
                time.sleep(1 + i)
                try:
                    o = self.get_order(params['symbol'], cid)
                except (requests.RequestException, BinanceError, AmbiguousOrder):
                    continue
                if o is None:                                   # never reached Binance -> safe to send once more
                    try:
                        return self._req('POST', '/fapi/v1/order', params, signed=True, retry=False)
                    except (requests.RequestException, AmbiguousOrder) as e:
                        raise AmbiguousOrder(f'order {cid} resend unconfirmed: {e}', tag)
                if params.get('type') == 'MARKET' and o.get('status') not in ('FILLED',):
                    time.sleep(1)
                    try: o = self.get_order(params['symbol'], cid) or o
                    except (requests.RequestException, BinanceError, AmbiguousOrder): pass
                    if o.get('status') != 'FILLED':
                        raise AmbiguousOrder(f"market order {cid} status {o.get('status')}", tag)
                return o
            raise AmbiguousOrder(f'order {cid} unconfirmed after {first}', tag)

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
        if symbol in testnet_faults(self.base, 'lev_refuse'):     # T03c canary only: inert unless TESTNET + the env flag
            raise BinanceError(-1000, 'injected testnet refusal (ZB_TESTNET_FAULTS)')
        return self._req('POST', '/fapi/v1/leverage', dict(symbol=symbol, leverage=int(lev)), signed=True)

    def current_leverage(self, symbol):
        """The coin's leverage as Binance has it now (read-only). None if Binance reports no row for the coin."""
        rows = self._req('GET', '/fapi/v2/positionRisk', dict(symbol=symbol), signed=True)
        try:
            levs = [_positive_int(r['leverage']) for r in (rows if isinstance(rows, list) else [rows])
                    if isinstance(r, dict) and r.get('symbol') == symbol and r.get('leverage') not in (None, '')]
            return max(levs) if levs else None
        except (TypeError, ValueError, OverflowError):
            return None

    def margin_state(self, symbol):
        """T03c. The coin's leverage and margin type as Binance has them now (read-only), from positionRisk:
        dict(leverage=int|None, margin_type='CROSSED'|'ISOLATED'|None). Unknown or mixed answers give None."""
        rows = self._req('GET', '/fapi/v2/positionRisk', dict(symbol=symbol), signed=True)
        rows = [r for r in (rows if isinstance(rows, list) else [rows]) if isinstance(r, dict) and r.get('symbol') == symbol]
        try: levs = [_positive_int(r['leverage']) for r in rows if r.get('leverage') not in (None, '')]
        except (TypeError, ValueError, OverflowError): levs = []
        kinds = {str(r.get('marginType', '')).lower() for r in rows}
        mtype = {'cross': 'CROSSED', 'crossed': 'CROSSED', 'isolated': 'ISOLATED'}.get(kinds.pop()) if len(kinds) == 1 else None
        return dict(leverage=max(levs) if levs else None, margin_type=mtype)

    def position_risk(self):
        """T03c round 1. Every symbol's open positions as Binance has them now (read-only, one positionRisk read):
        a list of dict(symbol, side 'LONG'|'SHORT', qty >= 0, mark, notional >= 0). Values are passed through as floats
        (the engine validates them: NaN / negative / missing must never be treated as safe). An answer that is not a list raises."""
        rows = self._req('GET', '/fapi/v2/positionRisk', signed=True)
        if not isinstance(rows, list): raise ValueError(f'positionRisk: unexpected answer shape ({type(rows).__name__})')
        out = []
        for p in rows:
            if not isinstance(p, dict): raise ValueError('positionRisk: malformed position row')
            amt = _finite_number(p.get('positionAmt'))
            if amt == 0: continue
            side = p.get('positionSide', 'BOTH')
            if side == 'BOTH': side = 'LONG' if amt > 0 else 'SHORT'
            out.append(dict(symbol=p['symbol'], side=side, qty=abs(amt), mark=_finite_number(p.get('markPrice'), positive=True),
                            notional=abs(_finite_number(p['notional'])) if p.get('notional') not in (None, '') else float('nan')))
        return out

    def open_orders_all(self):
        """T03c round 1. Every open order on the account (classic + algo/conditional), read-only. A list of
        dict(tag 'o:<orderId>'|'a:<algoId>', symbol, side, position_side, qty (unfilled), price, stop_price, reduce_only,
        client_id). Unlike open_stop_tags, a failed algo read RAISES: callers use this as proof, so unknown != empty.
        Only the exact shapes count: a list for openOrders; a list, or a dict whose 'orders' is a list, for openAlgoOrders."""
        rows = self._req('GET', '/fapi/v1/openOrders', signed=True)
        if not isinstance(rows, list): raise ValueError(f'openOrders: unexpected answer shape ({type(rows).__name__})')
        out = []
        for o in rows:
            if not isinstance(o, dict) or 'orderId' not in o: raise ValueError('openOrders: malformed order row')
            out.append(dict(tag=f"o:{o['orderId']}", symbol=o.get('symbol'), side=o.get('side'), position_side=o.get('positionSide', 'BOTH'),
                            qty=_finite_number(o.get('origQty') or 0, nonnegative=True) - _finite_number(o.get('executedQty') or 0, nonnegative=True),
                            price=_finite_number(o.get('price') or 0, nonnegative=True), stop_price=_finite_number(o.get('stopPrice') or 0, nonnegative=True),
                            client_id=o.get('clientOrderId'), reduce_only=_bool_field(o, 'reduceOnly') or _bool_field(o, 'closePosition'),
                            close_position=_bool_field(o, 'closePosition'), order_type=o.get('type'), working_type=o.get('workingType'),
                            status=o.get('status')))
        r = self._req('GET', '/fapi/v1/openAlgoOrders', signed=True)
        algo = r if isinstance(r, list) else r.get('orders') if isinstance(r, dict) else None
        if not isinstance(algo, list): raise ValueError(f'openAlgoOrders: unexpected answer shape ({type(r).__name__})')
        for o in algo:
            if not isinstance(o, dict) or 'algoId' not in o: raise ValueError('openAlgoOrders: malformed order row')
            out.append(dict(tag=f"a:{o['algoId']}", symbol=o.get('symbol'), side=o.get('side'), position_side=o.get('positionSide', 'BOTH'),
                            qty=_finite_number(o.get('quantity') or 0, nonnegative=True) - _finite_number(o.get('executedQty') or 0, nonnegative=True),
                            price=_finite_number(o.get('price') or 0, nonnegative=True), stop_price=_finite_number(o.get('triggerPrice') or 0, nonnegative=True),
                            client_id=o.get('clientAlgoId'), reduce_only=_bool_field(o, 'reduceOnly') or _bool_field(o, 'closePosition'),
                            close_position=_bool_field(o, 'closePosition'), order_type=o.get('orderType'), working_type=o.get('workingType'),
                            status=o.get('algoStatus')))
        return out

    def leverage_brackets(self, symbol):
        """T03c round 1. The coin's full leverage/maintenance bracket schedule (read-only), sorted by notional floor:
        a list of dict(floor, cap, mmr, cum, lev). Raises on a missing or malformed answer (never a partial schedule)."""
        r = self._req('GET', '/fapi/v1/leverageBracket', dict(symbol=symbol), signed=True)
        rows = [x for x in (r if isinstance(r, list) else [r]) if isinstance(x, dict) and x.get('symbol', symbol) == symbol]
        if len(rows) != 1 or not rows[0].get('brackets'): raise ValueError(f'no leverage brackets for {symbol}')
        # T03c r2 follow-up (Codex disposition): Binance does not say whether the returned brackets already include
        # notionalCoef, so the scaling direction is not guessed. A present coef must be exactly 1; anything else (any other
        # value, non-finite, non-positive, junk) rejects the schedule (fail closed) until a real adjusted-account payload
        # proves the semantics. An omitted coef is the normal, unadjusted case.
        raw_coef = _finite_number(rows[0]['notionalCoef'], positive=True) if 'notionalCoef' in rows[0] else 1.0
        if raw_coef != 1.0: raise ValueError(f'{symbol} notionalCoef {raw_coef} != 1: bracket scaling unknown (fail closed)')
        coef = 1.0
        out = [dict(floor=_finite_number(b['notionalFloor'], nonnegative=True) * coef,
                    cap=_finite_number(b['notionalCap'], positive=True) * coef,
                    mmr=_finite_number(b['maintMarginRatio'], positive=True),
                    cum=_finite_number(b['cum'], nonnegative=True) * coef,
                    lev=_positive_int(b['initialLeverage'])) for b in rows[0]['brackets']]
        return BracketSchedule(sorted(out, key=lambda b: b['floor']), notional_coef=raw_coef, coef_applied=coef)

    def set_margin_type(self, symbol, mtype='CROSSED'):
        try:
            return self._req('POST', '/fapi/v1/marginType', dict(symbol=symbol, marginType=mtype), signed=True)
        except BinanceError as e:
            if e.code == -4046: return None
            raise

    # ---------- orders (hedge mode: positionSide LONG/SHORT) ----------
    def open(self, symbol, pos_side, qty):
        side = 'BUY' if pos_side == 'LONG' else 'SELL'
        return self._order(dict(symbol=symbol, side=side, positionSide=pos_side, type='MARKET', quantity=qty, newOrderRespType='RESULT'))

    def close(self, symbol, pos_side, qty):
        side = 'SELL' if pos_side == 'LONG' else 'BUY'
        return self._order(dict(symbol=symbol, side=side, positionSide=pos_side, type='MARKET', quantity=qty, newOrderRespType='RESULT'))

    def stop(self, symbol, pos_side, qty, stop_price):
        """Exchange-side stop for qty of the given position side. Returns 'o:<id>' or 'a:<algoId>'."""
        side = 'SELL' if pos_side == 'LONG' else 'BUY'
        try:
            r = self._order(dict(symbol=symbol, side=side, positionSide=pos_side, type='STOP_MARKET',
                                 quantity=qty, stopPrice=stop_price, workingType='MARK_PRICE'))
            return f"o:{r['orderId']}"
        except BinanceError as e:
            if e.code not in (-4120, -1116, -1102, -4136):
                raise
            acid = new_cid('za')
            try:
                r = self._req('POST', '/fapi/v1/algoOrder', dict(algoType='CONDITIONAL', symbol=symbol, side=side, clientAlgoId=acid,
                              positionSide=pos_side, type='STOP_MARKET', quantity=qty, triggerPrice=stop_price,
                              workingType='MARK_PRICE'), signed=True, retry=False)
            except (requests.RequestException, AmbiguousOrder) as ex:
                raise AmbiguousOrder(f'algo stop unconfirmed: {ex}', f'ac:{acid}')
            return f"a:{r['algoId']}"

    def cancel(self, symbol, tag):
        """Cancel one stop. Returns True if cancelled or already gone; raises on other errors (rate limit, network)."""
        if not tag: return True
        kind, oid = tag.split(':', 1)
        try:
            if kind == 'o':
                self._req('DELETE', '/fapi/v1/order', dict(symbol=symbol, orderId=oid), signed=True)
            elif kind == 'c':
                self._req('DELETE', '/fapi/v1/order', dict(symbol=symbol, origClientOrderId=oid), signed=True)
            elif kind == 'ac':
                self._req('DELETE', '/fapi/v1/algoOrder', dict(clientAlgoId=oid), signed=True)
            else:
                self._req('DELETE', '/fapi/v1/algoOrder', dict(algoId=oid), signed=True)
        except BinanceError as e:
            if e.code in (-2011, -2013, -4120) or 'not exist' in str(e.msg).lower() or 'unknown order' in str(e.msg).lower():
                return True                                   # already filled / cancelled
            raise
        return True

    def leverage_max(self, symbol):
        try:
            r = self._req('GET', '/fapi/v1/leverageBracket', dict(symbol=symbol), signed=True)
            row = r[0] if isinstance(r, list) else r
            return int(row['brackets'][0]['initialLeverage'])
        except Exception:
            return None

    def api_restrictions(self):
        """Key permissions (mainnet only; lives on the spot API host)."""
        if self.base != MAINNET: return None
        url = 'https://api.binance.com/sapi/v1/account/apiRestrictions'
        p = dict(timestamp=int(time.time() * 1000) + self.offset, recvWindow=self.rw)
        p['signature'] = hmac.new(self.secret, urllib.parse.urlencode(p).encode(), hashlib.sha256).hexdigest()
        return self.s.get(url, params=p, timeout=10).json()

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
