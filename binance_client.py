"""Minimal Binance USD-M futures REST client (requests + HMAC), hedge-mode aware."""
import email.utils, hashlib, hmac, math, os, random, re, threading, time, urllib.parse, uuid
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


READ_OUTAGE_MAX_S = 600
_READ_OUTAGE_PREFIX = re.compile(r'/fapi/[A-Za-z0-9/]+')


def testnet_read_outage(base):
    """T05b runtime canary: (seconds, path_prefix) of a controlled read outage from ZB_TESTNET_FAULTS="read_outage:<s>" or
    "read_outage:<s>:<path-prefix>" (e.g. read_outage:90:/fapi/v2/positionRisk), else None. Same gate as testnet_faults:
    None unless `base` is exactly TESTNET. <s> is a whole number 1..READ_OUTAGE_MAX_S; anything else is ignored (inert).
    Only non-critical GET status reads are affected (see Futures._injected_read_outage); orders never are."""
    if base != TESTNET: return None
    for item in (os.environ.get('ZB_TESTNET_FAULTS') or '').split(','):
        k, _, rest = item.strip().partition(':')
        if k != 'read_outage': continue
        sec, _, prefix = rest.partition(':')
        if not re.fullmatch(r'[1-9][0-9]{0,2}', sec) or int(sec) > READ_OUTAGE_MAX_S: continue
        if prefix and not _READ_OUTAGE_PREFIX.fullmatch(prefix): continue
        return int(sec), prefix or '/fapi/'
    return None


class _InjectedBusy:
    """What _req sees for an injected read: Binance answered HTTP 503 (code -1007). No network call is made."""
    status_code, headers = 503, {}


FINAL_STATUS = ('FILLED', 'EXPIRED', 'CANCELED', 'REJECTED', 'EXPIRED_IN_MATCH')   # AUD-03: executedQty is final


class AmbiguousOrder(Exception):
    """An order request may or may not have reached Binance and its status could not be confirmed.
    .tag is a cancel tag for it ('c:<clientOrderId>' or 'ac:<clientAlgoId>') when one is known."""
    def __init__(self, msg, tag=None):
        super().__init__(msg); self.tag = tag


class ExchangeUnavailable(BinanceError):
    """T05b: a READ was not sent because Binance is in a known outage (shared cooldown). No order is involved.
    The message is stable (no countdown) so repeated activity lines coalesce; the seconds are in .retry_in."""
    def __init__(self, msg, retry_in=None):
        super().__init__(-1007, msg); self.retry_in = retry_in; self.kind = 'read'


RETRY_AFTER_MAX = 60.0


def retry_after(headers, now=None):
    """Retry-After as finite seconds in [0, RETRY_AFTER_MAX]: numeric seconds or an HTTP-date; anything else -> 0.
    Never raises (it is parsed inside the order path, where an exception would skip the client-id lookup)."""
    try:
        v = (headers or {}).get('Retry-After')
        if v is None or str(v).strip() == '': return 0.0
        v = str(v).strip()
        try:
            sec = float(v)
        except ValueError:
            dt = email.utils.parsedate_to_datetime(v)
            sec = dt.timestamp() - (time.time() if now is None else now)
        if not math.isfinite(sec): return 0.0
        return min(RETRY_AFTER_MAX, max(0.0, sec))
    except Exception:
        return 0.0


_SIGNED_QS = re.compile(r'(/[A-Za-z0-9_./-]*)\?[^\s\'")]*')


def scrub(text):
    """Drop URL query strings (timestamp / signature / ids) from an error text: signatures are never shown, and a
    per-request timestamp would make every repeat of the same failure look like a new message."""
    return _SIGNED_QS.sub(r'\1', str(text))


def _scrubbed(ex):
    """The same requests exception type, with the query string removed and NO chained cause/context: requests chains the
    urllib3 error whose text holds the full signed URL, and a logged traceback (panel action / job) would print it."""
    msg = scrub(ex)
    try: clean = type(ex)(msg)
    except Exception: clean = requests.ConnectionError(msg)
    clean.__cause__, clean.__suppress_context__ = None, True
    return clean


class ExchangeHealth:
    """T05b (local draft): one shared, bounded circuit for transient Binance failures, per client.
    ok -> degraded (a transient failure) -> outage (OUTAGE_FAILS in a row, or no success for OUTAGE_S) -> ok (any success).
    While in outage a read inside the cooldown fails fast without touching the network; one probe per window is let
    through (half-open). Orders are never refused here: they always go through _order() (idempotent by client id)."""
    OUTAGE_FAILS, OUTAGE_S, COOL_MIN, COOL_MAX = 3, 30.0, 2.0, 60.0

    def __init__(self, clock=time.monotonic):
        self.clock, self.lock = clock, threading.Lock()
        self.state, self.since, self.fails, self.first_fail = 'ok', None, 0, None
        self.cool, self.next_probe, self.last_ok, self.last_fail = 0.0, 0.0, None, None
        self.fail_fast = self.probes = self.recoveries = 0
        self.other_fails = 0                        # telemetry only: order / cancel / confirmation failures
        self.recovered = False                      # set on outage -> ok; the engine clears it after its reconcile

    def admit_read(self):
        """None = send the read; otherwise the seconds until the next probe (the read must fail fast)."""
        with self.lock:
            if self.state != 'outage': return None
            now = self.clock()
            if now >= self.next_probe:
                self.next_probe = now + self.cool; self.probes += 1
                return None
            self.fail_fast += 1
            return round(self.next_probe - now, 1)

    def ok(self):
        with self.lock:
            now = self.clock()
            if self.state == 'outage': self.recovered = True; self.recoveries += 1
            self.state, self.since, self.fails, self.first_fail, self.cool, self.last_ok = 'ok', None, 0, None, 0.0, now

    def fail(self, what, retry_after=0.0, read=True):
        """A transient failure. Only status checks (read=True: non-critical GETs) drive the circuit: an order, a cancel
        or an order's own confirmation lookup failing is counted for telemetry but never opens it or moves next_probe
        (otherwise one orphan cancel failing every pass would keep positions from ever being re-read)."""
        with self.lock:
            now = self.clock()
            try: retry_after = float(retry_after or 0)
            except (TypeError, ValueError): retry_after = 0.0
            if not math.isfinite(retry_after): retry_after = 0.0
            retry_after = min(RETRY_AFTER_MAX, max(0.0, retry_after))
            if not read:
                self.other_fails += 1; self.last_fail = dict(t=now, what=str(what)[:120], read=False)
                return
            self.fails += 1; self.last_fail = dict(t=now, what=str(what)[:120], read=True)
            if self.first_fail is None: self.first_fail = now
            if self.state == 'ok': self.state, self.since = 'degraded', now
            if self.state == 'degraded' and (self.fails >= self.OUTAGE_FAILS or now - self.first_fail >= self.OUTAGE_S):
                self.state, self.since, self.cool = 'outage', now, self.COOL_MIN
                self.next_probe = now + max(self.cool, retry_after)
            elif self.state == 'outage':
                self.cool = min(self.COOL_MAX, max(self.COOL_MIN, self.cool * 2))
                self.next_probe = now + max(self.cool * (0.8 + 0.4 * random.random()), retry_after)

    def snapshot(self):
        with self.lock:
            return dict(state=self.state, since=self.since, consecutive_fail=self.fails, last_ok=self.last_ok,
                        last_fail=self.last_fail, cooldown_s=self.cool, fail_fast=self.fail_fast, probes=self.probes,
                        recoveries=self.recoveries, other_fails=self.other_fails)


ALGO_UNSUPPORTED = (-5000, -1404, -404)    # endpoint unknown here: -5000 "Path ..., Method GET is invalid" / plain 404
TRANSIENT = (-1001, -1003, -1006, -1007, -1008, -1015)      # disconnected / rate limit / timeout / server busy
SAFE_METHODS = ('GET', 'DELETE')


def is_transient(e):
    """T05b: the request failed because Binance was unreachable/busy - its answer is UNKNOWN, not a business refusal.
    _req raises BinanceError(-status) for a busy HTTP answer and -1000-status when the body was not JSON."""
    if isinstance(e, ExchangeUnavailable): return True
    if not isinstance(e, BinanceError): return False
    c = e.code if isinstance(e.code, int) else None
    return c is not None and (c in TRANSIENT or c in (-418, -429) or -599 <= c <= -500 or -1599 <= c <= -1500)


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

    @property
    def health(self):
        """The shared outage circuit (created on first use, so clients built without __init__ get one too)."""
        h = self.__dict__.get('_health')
        if h is None: h = self.__dict__['_health'] = ExchangeHealth()
        return h

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

    def _injected_read_outage(self, path):
        """T05b canary: True while the testnet read-outage window is open for this path. The window starts at the first
        eligible read and ends for good after its seconds (one-shot per process); the circuit's own clock is used."""
        cfg = testnet_read_outage(self.base)
        if cfg is None or not path.startswith(cfg[1]): return False
        w = self.__dict__.setdefault('_read_outage_window', {})
        now = self.health.clock()
        if 'until' not in w: w['until'] = now + cfg[0]
        return now < w['until']

    def _req(self, method, path, params=None, signed=False, retry=None, critical=False):
        """GET/DELETE are retried on network errors, rate limits and server errors (with backoff and Retry-After).
        POST is never blindly retried: order placement goes through _order(), which confirms by client order id."""
        params = {k: v for k, v in (params or {}).items() if v is not None}
        retry = (method in SAFE_METHODS) if retry is None else retry
        url = self.base + path
        read = method == 'GET' and not critical          # T05b: only status checks drive / obey the outage circuit
        if read:                                         # reads share one cooldown during a known outage
            wait = self.health.admit_read()
            if wait is not None:
                raise ExchangeUnavailable(f'Binance outage: status check {path} not sent (waiting for the next probe)', wait)
        for attempt in range(4 if retry else 2):
            last = attempt == (3 if retry else 1)
            try:
                if read and self._injected_read_outage(path):     # T05b canary: inert unless TESTNET + the env flag
                    r, data = _InjectedBusy(), {'code': -1007, 'msg': 'injected testnet read outage (ZB_TESTNET_FAULTS)'}
                else:
                    r, data = self._once(method, url, params, signed)
            except requests.RequestException as ex:
                self.health.fail(f'{method} {path}: {type(ex).__name__}', read=read)
                if not retry or last or (read and self.health.state == 'outage'): raise _scrubbed(ex) from None
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
                ra = retry_after(r.headers)
                self.health.fail(f'{method} {path}: HTTP {r.status_code} code {code}', ra, read=read)
                if retry and not last and read and self.health.state == 'outage':
                    raise ExchangeUnavailable(f'Binance outage: status check {path} failed (HTTP {r.status_code}, code {code})',
                                              self.health.cool)
                if not retry or last:
                    if method not in SAFE_METHODS and (r.status_code >= 500 or code in (-1001, -1006, -1007)):
                        raise AmbiguousOrder(f'HTTP {r.status_code} on {path}')
                    raise BinanceError(code or -r.status_code, data.get('msg') if isinstance(data, dict) else f'HTTP {r.status_code}')
                wait = ra or min(8, 0.5 * 2 ** attempt + random.random())
                time.sleep(min(wait, 30)); continue
            self.health.ok()                             # Binance answered (even a business error proves it is up)
            if isinstance(data, dict) and code not in (None, 0, 200):
                raise BinanceError(code, data.get('msg'))
            self.last_ok = time.time()
            return data

    def get_order(self, symbol, cid):
        try:
            return self._req('GET', '/fapi/v1/order', dict(symbol=symbol, origClientOrderId=cid), signed=True,
                             critical=True)      # T05b: an order's own confirmation is never skipped by the circuit
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
                if params.get('type') == 'MARKET' and o.get('status') not in FINAL_STATUS:
                    time.sleep(1)
                    try: o = self.get_order(params['symbol'], cid) or o
                    except (requests.RequestException, BinanceError, AmbiguousOrder): pass
                    if o.get('status') not in FINAL_STATUS:
                        raise AmbiguousOrder(f"market order {cid} status {o.get('status')}", tag)
                return o                        # AUD-03: a FINAL record (also EXPIRED / CANCELED) carries the executed qty
            raise AmbiguousOrder(f'order {cid} unconfirmed after {first}', tag)

    # ---------- public ----------
    def exchange_info(self):
        return self._req('GET', '/fapi/v1/exchangeInfo')

    def klines(self, symbol, interval='4h', limit=1500, end_time=None, start_time=None):
        return self._req('GET', '/fapi/v1/klines', dict(symbol=symbol, interval=interval, limit=limit,
                                                          endTime=end_time, startTime=start_time))

    def marks(self, critical=False):
        """critical=True (a protective action such as a manual stop move) is never skipped by the outage circuit."""
        return {x['symbol']: float(x['markPrice']) for x in self._req('GET', '/fapi/v1/premiumIndex', critical=critical)}

    def premium(self, symbol):
        return self._req('GET', '/fapi/v1/premiumIndex', dict(symbol=symbol))

    def tickers_24h(self):
        return self._req('GET', '/fapi/v1/ticker/24hr')

    # ---------- account ----------
    def account(self):
        return self._req('GET', '/fapi/v2/account', signed=True)

    def positions(self, critical=False):
        """{(symbol, 'LONG'|'SHORT'): abs qty}. critical=True (sizing an order) is never skipped by the outage circuit."""
        out = {}
        for p in self._req('GET', '/fapi/v2/positionRisk', signed=True, critical=critical):
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
        except BinanceError as e:                        # unknown algo-stop state is NOT "no algo stops" (T05b review):
            if isinstance(e, ExchangeUnavailable) or e.code not in ALGO_UNSUPPORTED: raise
        return tags                                      # only an explicit "no such endpoint" means no algo stops

    def cancel_all(self, symbol):
        for path in ('/fapi/v1/allOpenOrders', '/fapi/v1/algoOpenOrders'):
            try: self._req('DELETE', path, dict(symbol=symbol), signed=True)
            except BinanceError: pass
