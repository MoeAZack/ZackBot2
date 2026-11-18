"""CAPS-01 r9-r11 test helper: an offline venue fake for stop orders by client id (classic + algo fallback), duplicate-id
refusal, lost answers, typed status lookups (stop_state) and the detailed open-order listing, installed on top of the
FakeX / Book fakes. No network."""
import binance_client as BC


class Venue:
    """Stop orders by client id on top of the FakeX/Book fake. mode 'classic' or 'algo' (classic refused -4120, then the
    algo endpoint with clientAlgoId); lose='placed' -> placed but the answer is lost, 'unplaced' -> lost before Binance."""
    def __init__(self, x, mode='classic'):
        self.x, self.mode, self.lose, self.n, self.by_id, self.st, self.max_live = x, mode, None, 0, {}, {}, 0
        self.fail_cancel = set()                                        # resolved tags whose cancel raises (network)
        x.stop, x.stop_status, x.open_stop_orders, x.stop_state = self.stop, self.stop_status, self.open_stop_orders, self.stop_state
        bk = x.cancel.__self__ if hasattr(x.cancel, '__self__') and hasattr(x.cancel.__self__, 'orders') else None
        if bk is not None: bk._cancel = self.cancel
        else: x.cancel = self.cancel
    def live(self, sym='BTCUSDT', side='LONG'):
        return [(t, v) for t, v in self.x.stops.items() if v[0] == sym and v[1] == side and self.st.get(t) == 'NEW']
    def stop(self, s, ps, q, p, cid=None, acid=None):
        self.x._f('stop')
        lose, self.lose = self.lose, None
        if self.mode == 'classic' and cid:
            if cid in self.by_id: raise BC.BinanceError(-4116, 'ClientOrderId is duplicated.')
            if lose == 'unplaced': raise BC.AmbiguousOrder('answer lost (never reached Binance)', f'c:{cid}')
            tag = self._put('o', s, ps, q, p, cid)
            if lose == 'placed': raise BC.AmbiguousOrder('answer lost', f'c:{cid}')
            return tag
        if self.mode == 'algo' and acid:
            if acid in self.by_id: raise BC.BinanceError(-4116, 'clientAlgoId is duplicated.')
            if lose == 'unplaced': raise BC.AmbiguousOrder('algo stop unconfirmed', f'ac:{acid}')
            tag = self._put('a', s, ps, q, p, acid)
            if lose == 'placed': raise BC.AmbiguousOrder('algo stop unconfirmed', f'ac:{acid}')
            return tag
        return self._put('o', s, ps, q, p, None)                       # a lot's own stop (no client id from the engine)
    def _put(self, k, s, ps, q, p, cid):
        self.n += 1; tag = f'{k}:{self.n}'
        self.x.stops[tag] = (s, ps, float(q), float(p)); self.st[tag] = 'NEW'
        if cid: self.by_id[cid] = tag
        self.max_live = max(self.max_live, len(self.live(s, ps)))
        return tag
    def _tag(self, tag):
        kind, _, oid = tag.partition(':')
        return self.by_id.get(oid) if kind in ('c', 'ac') else tag
    def cancel(self, s, tag):
        self.x._f('cancel'); t = self._tag(tag)
        if t in self.fail_cancel: raise BC.BinanceError(-1001, 'injected: cancel not answered')
        if t in self.st and self.st[t] == 'NEW': self.st[t] = 'CANCELED'; self.x.stops.pop(t, None)
        else: self.x.stops.pop(tag, None)
        return True
    def stop_status(self, s, tag, retry=None):
        t = self._tag(tag)
        if t is None: return None
        if t in self.st: return self.st[t]
        return 'NEW' if t in self.x.stops else None
    def stop_state(self, s, tag):
        st = self.stop_status(s, tag)
        if st is None: return 'absent', None                           # the venue's explicit no-such-order
        return {'NEW': 'live', 'CANCELED': 'gone', 'FILLED': 'fired'}.get(st, 'unknown'), st
    def open_stop_orders(self, s, strict_algo=False, retry=None):
        cid = {v: k for k, v in self.by_id.items()}
        return [dict(tag=t, type='STOP_MARKET', position_side=v[1], side='SELL' if v[1] == 'LONG' else 'BUY', qty=v[2],
                     stop_price=v[3], client_id=cid.get(t), status='NEW', close_position=False, reduce_only=False)
                for t, v in self.x.stops.items() if v[0] == s and self.st.get(t, 'NEW') == 'NEW']
