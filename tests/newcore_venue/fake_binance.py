"""A stateful fake of the Binance USD-M Futures TESTNET REST endpoints NEWCORE uses, for end-to-end tests through the real
transport (no network). Answers in Binance's documented shapes; knobs model the open probe questions:

  reuse_ids         False: a client id ever used is refused with -4116 forever (FakeVenue's assumption)
                    True : a FINAL order's id may be used again (only an OPEN order's id is refused)
  classic_stops     'algo_required' (-4120 on a classic STOP_MARKET) or 'accept'
  position_lag_reads  positionRisk shows a change only after this many further reads (P2 read lag)
  order_lag_reads     openOrders / openAlgoOrders show a new order only after this many further reads
"""
import json
from decimal import Decimal
from urllib.parse import parse_qsl

from newcore.venue.wire import HttpResponse

from ncv_support import fixture

D = Decimal
TAKER = D('0.0005')


def ok(obj, weight=1):
    return HttpResponse(200, {'X-MBX-USED-WEIGHT-1M': str(weight)}, json.dumps(obj).encode())


def err(code, msg, status=400):
    return HttpResponse(status, {}, json.dumps({'code': code, 'msg': msg}).encode())


class FakeBinance:
    def __init__(self, *, now_ms=1759917600000, price='220', dual=True, reuse_ids=False, classic_stops='algo_required',
                 position_lag_reads=0, order_lag_reads=0, foreign_orders=(), refuse_closes=False):
        self.now, self.price, self.dual = now_ms, D(price), dual
        self.reuse_ids, self.classic_stops = reuse_ids, classic_stops
        self.position_lag, self.order_lag = position_lag_reads, order_lag_reads
        self.refuse_closes = refuse_closes
        self.pos, self.visible_pos, self.pos_reads_left = {}, {}, 0
        self.orders, self.algos, self.used, self.fills = {}, {}, set(), []
        self.order_seen = {}                       # cid -> reads left before it shows in open lists
        self.next_id, self.requests = 7000, []
        for cid in foreign_orders:
            self._rest_classic(cid, 'SOLUSDT', 'SELL', 'LONG', D('1'), D('100'), used=False)

    # ---- helpers
    def _id(self):
        self.next_id += 1
        return self.next_id

    def _changed_positions(self):
        if self.position_lag:
            self.pos_reads_left = self.position_lag
        else:
            self.visible_pos = dict(self.pos)

    def _order_rec(self, o):
        return {'orderId': o['id'], 'symbol': o['symbol'], 'status': o['status'], 'clientOrderId': o['cid'],
                'price': '0', 'avgPrice': str(o.get('avg', '0')), 'origQty': str(o['qty']),
                'executedQty': str(o.get('executed', '0')), 'cumQuote': str(o.get('quote', '0')),
                'timeInForce': 'GTC', 'type': o['type'], 'origType': o['type'], 'reduceOnly': False,
                'closePosition': False, 'side': o['side'], 'positionSide': o['ps'],
                'stopPrice': str(o.get('stop', '0')), 'workingType': 'MARK_PRICE', 'updateTime': self.now}

    def _algo_rec(self, a):
        return {'algoId': a['id'], 'clientAlgoId': a['cid'], 'algoType': 'CONDITIONAL', 'orderType': 'STOP_MARKET',
                'symbol': a['symbol'], 'side': a['side'], 'positionSide': a['ps'], 'quantity': str(a['qty']),
                'algoStatus': a['status'], 'triggerPrice': str(a['trigger']), 'workingType': 'MARK_PRICE',
                'reduceOnly': True, 'closePosition': False, 'createTime': self.now, 'updateTime': self.now}

    def _cid_refused(self, cid):
        open_ids = {o['cid'] for o in self.orders.values() if o['status'] == 'NEW'} | \
            {a['cid'] for a in self.algos.values() if a['status'] == 'NEW'}
        return cid in open_ids or (cid in self.used and not self.reuse_ids)

    def _rest_classic(self, cid, symbol, side, ps, qty, stop, used=True):
        o = dict(id=self._id(), cid=cid, symbol=symbol, side=side, ps=ps, qty=qty, type='STOP_MARKET', status='NEW',
                 stop=stop)
        self.orders[cid] = o
        self.order_seen[cid] = self.order_lag
        if used:
            self.used.add(cid)
        return o

    # ---- dispatcher
    def __call__(self, request):
        self.requests.append(request)
        path = request.url.split('binancefuture.com', 1)[1]
        q = dict(parse_qsl(request.query))
        h = getattr(self, f"_{request.method.lower()}_{path.strip('/').replace('/', '_')}", None)
        if h is None:
            return err(-5000, f'Path {path}, Method {request.method} is invalid')
        return h(q)

    # ---- reads
    def _get_fapi_v1_time(self, q):
        return ok({'serverTime': self.now})

    def _get_fapi_v1_exchangeInfo(self, q):
        return fixture('exchange_info')

    def _get_fapi_v1_klines(self, q):
        rows = []
        for i in range(3, 0, -1):
            o = (self.now // 60_000 - i) * 60_000
            p = str(self.price)
            rows.append([o, p, p, p, p, '1', o + 59_999, p, 1, '0', '0', '0'])
        return ok(rows)

    def _get_fapi_v2_account(self, q):
        return fixture('account_v2')

    def _get_fapi_v1_positionSide_dual(self, q):
        return ok({'dualSidePosition': self.dual})

    def _get_fapi_v2_positionRisk(self, q):
        if self.pos_reads_left > 0:
            self.pos_reads_left -= 1
            if self.pos_reads_left == 0:
                self.visible_pos = dict(self.pos)
        sym = q.get('symbol')
        rows = []
        for s in ([sym] if sym else sorted({k[0] for k in self.visible_pos} | {'SOLUSDT'})):
            for side in ('LONG', 'SHORT'):
                amt = self.visible_pos.get((s, side), D(0))
                rows.append({'symbol': s, 'positionAmt': str(-amt if side == 'SHORT' else amt), 'entryPrice':
                             str(self.price if amt else 0), 'markPrice': str(self.price), 'unRealizedProfit': '0',
                             'liquidationPrice': '0', 'leverage': '3', 'marginType': 'cross', 'positionSide': side,
                             'notional': str(amt * self.price), 'updateTime': self.now})
        return ok(rows)

    def _visible(self, cid):
        left = self.order_seen.get(cid, 0)
        if left > 0:
            self.order_seen[cid] = left - 1
            return False
        return True

    def _get_fapi_v1_openOrders(self, q):
        sym = q.get('symbol')
        return ok([self._order_rec(o) for o in self.orders.values() if o['status'] == 'NEW'
                   and sym in (None, o['symbol']) and self._visible(o['cid'])])

    def _get_fapi_v1_openAlgoOrders(self, q):
        sym = q.get('symbol')
        return ok([self._algo_rec(a) for a in self.algos.values() if a['status'] == 'NEW'
                   and sym in (None, a['symbol']) and self._visible(a['cid'])])

    def _get_fapi_v1_userTrades(self, q):
        # Binance semantics: fromId -> trades with id >= fromId (time bounds not applied); else startTime / endTime
        # bound the rows; ascending ids; at most `limit` (default 500) rows.
        rows = [f for f in self.fills if f['symbol'] == q['symbol'] and
                ('orderId' not in q or f['orderId'] == int(q['orderId']))]
        if 'fromId' in q:
            rows = [f for f in rows if f['id'] >= int(q['fromId'])]
        else:
            if 'startTime' in q:
                rows = [f for f in rows if f['time'] >= int(q['startTime'])]
            if 'endTime' in q:
                rows = [f for f in rows if f['time'] <= int(q['endTime'])]
        rows.sort(key=lambda f: f['id'])
        return ok(rows[:int(q.get('limit', 500))])

    def _get_fapi_v1_income(self, q):
        return ok([])

    def _get_fapi_v1_order(self, q):
        o = self.orders.get(q['origClientOrderId'])
        return ok(self._order_rec(o)) if o else err(-2013, 'Order does not exist.')

    def _get_fapi_v1_algoOrder(self, q):
        a = self.algos.get(q['clientAlgoId'])
        return ok(self._algo_rec(a)) if a else err(-2013, 'Order does not exist.')

    # ---- writes
    def _post_fapi_v1_order(self, q):
        cid, sym, side, ps, qty = q['newClientOrderId'], q['symbol'], q['side'], q['positionSide'], D(q['quantity'])
        if self._cid_refused(cid):
            return err(-4116, 'ClientOrderId is duplicated.')
        if q['type'] == 'STOP_MARKET':
            if self.classic_stops == 'algo_required':
                return err(-4120, 'Order type not supported for this endpoint. Please use the Algo Order API.')
            return ok(self._order_rec(self._rest_classic(cid, sym, side, ps, qty, D(q['stopPrice']))))
        closing = (ps == 'LONG') == (side == 'SELL')
        have = self.pos.get((sym, ps), D(0))
        if closing and (qty > have or self.refuse_closes):
            return err(-2022, 'ReduceOnly Order is rejected.')
        self.used.add(cid)
        self.pos[(sym, ps)] = have - qty if closing else have + qty
        self._changed_positions()
        o = dict(id=self._id(), cid=cid, symbol=sym, side=side, ps=ps, qty=qty, type='MARKET', status='FILLED',
                 executed=qty, avg=self.price, quote=qty * self.price)
        self.orders[cid] = o
        self.fills.append({'buyer': side == 'BUY', 'commission': str(qty * self.price * TAKER), 'commissionAsset':
                           'USDT', 'id': self._id(), 'maker': False, 'orderId': o['id'], 'price': str(self.price),
                           'qty': str(qty), 'quoteQty': str(qty * self.price), 'realizedPnl': '0', 'side': side,
                           'positionSide': ps, 'symbol': sym, 'time': self.now})
        return ok(self._order_rec(o))

    def _delete_fapi_v1_order(self, q):
        o = self.orders.get(q['origClientOrderId'])
        if not o or o['status'] != 'NEW':
            return err(-2011, 'Unknown order sent.')
        o['status'] = 'CANCELED'
        return ok(self._order_rec(o))

    def _post_fapi_v1_algoOrder(self, q):
        cid = q['clientAlgoId']
        if self._cid_refused(cid):
            return err(-4116, 'ClientOrderId is duplicated.')
        self.used.add(cid)
        a = dict(id=self._id(), cid=cid, symbol=q['symbol'], side=q['side'], ps=q['positionSide'],
                 qty=D(q['quantity']), trigger=D(q['triggerPrice']), status='NEW')
        self.algos[cid] = a
        self.order_seen[cid] = self.order_lag
        return ok(self._algo_rec(a))

    def _delete_fapi_v1_algoOrder(self, q):
        a = self.algos.get(q['clientAlgoId'])
        if not a or a['status'] != 'NEW':
            return err(-2011, 'Unknown order sent.')
        a['status'] = 'CANCELED'
        return ok({'algoId': a['id'], 'clientAlgoId': a['cid'], 'code': '200', 'msg': 'success'})

    # ---- test helpers
    def flat(self):
        return all(v == 0 for v in self.pos.values())

    def open_cids(self):
        return {o['cid'] for o in self.orders.values() if o['status'] == 'NEW'} | \
            {a['cid'] for a in self.algos.values() if a['status'] == 'NEW'}
