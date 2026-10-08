"""ManagementDriver fault-injection fuzz (feeds REC-02 / TNET-01). A seeded venue stub injects:
lost answers (UNKNOWN now, a later FINAL / KNOWN by query, or NOT_FOUND when the order never reached the venue); late
fills after a confirmed cancel; duplicate and out-of-order fills; partial fills on stop / close / add / target; REJECTED
(-2021) on a stop replacement; the old stop filling while its replacement is in flight; and a crash + restart (fold of
the durable event log) at event boundaries.

Invariants after every driver call:
- once a stop was confirmed, the position is covered by CONFIRMED stops, except while a stop placement / replacement is
  in flight (the old stop still live), while the uncovered part is being closed at market, or while a lost answer is
  reported for reconcile;
- never above the risk cap after an add fill;
- no duplicate client id; no venue trade booked twice; a stop request is never loosened (outside a refused replace);
- fold(log) == the live state;
- at the end, after the venue delivered everything: the core's position equals the venue's, and the result is flat,
  protected, or reported for reconcile - never silently wrong.
"""
import random
from decimal import Decimal as D

import pytest

import test_properties as TP
from mg_factories import T0
from newcore.domain import Side, make_id
from newcore.domain.instrument import Rounding
from newcore.management import Candle, Leg, Stage, risk_to_stop
from newcore.management import driver as DR
from newcore.management.plan import market_fill
from newcore.ports.venue import OrderOutcome, OrderRef, OutcomeKind, VenueFill

ACCT = make_id('acct', 3)
LOT = make_id('lot', 5)
H4 = 14_400_000
FAST_SEEDS = range(500)


class Violation(AssertionError):
    pass


class FaultVenue:
    def __init__(self, seed, p, fault=0.25):
        self.rng, self.p, self.fault = random.Random(10 ** 6 + seed), p, fault
        self.side, self.long = p.side, p.side is Side.LONG
        self.pos = p.entry_qty                      # venue truth
        self.mark = p.entry_price
        self.orders = {}                            # client_id -> dict(kind, qty, price, xid, filled, live, reduce)
        self.pending = []                           # deliveries not yet given to the driver
        self.n = 0
        self.log = []
        self.i = 0                                  # candle index
        self.ds = None
        self.client_ids = []
        self.reported = set()
        self.ever_confirmed = False

    # ------------------------------------------------------------------------------------------- plumbing
    def nid(self, tag):
        self.n += 1
        return f'{tag}{self.n}'

    def at(self):
        return T0 + 1000 + self.n

    def ref(self, cid):
        return OrderRef(symbol=self.p.symbol, client_id=cid)

    def chance(self, k=1.0):
        return self.rng.random() < self.fault * k

    def outcome(self, cid, kind, **kw):
        return OrderOutcome(kind=kind, ref=self.ref(cid), observed_at_ms=self.at(), **kw)

    def rows(self, o, q, price):
        """q as 1-3 venue trades (a partial-fill sequence), each on the step."""
        step = self.p.rules.step_size
        n = int(q / step)
        parts = []
        if n >= 2 and self.chance():
            cut = step * self.rng.randint(1, n - 1)
            parts = [cut, q - cut]
        else:
            parts = [q]
        out = []
        for part in parts:
            out.append(VenueFill(trade_id=self.nid('t'), exchange_order_id=o['xid'], symbol=self.p.symbol,
                                 position_side=self.side.value, qty=part, price=price,
                                 fee=(self.p.costs.taker_fee * part * price).quantize(D('1e-12')), fee_asset='USDT',
                                 realized_pnl=D(0), maker=False, at_ms=self.at()))
        return out

    def queue_fills(self, o, rows):
        for r in rows:
            self.pending.append(('fills', (r,), o['xid']))
            if self.chance(0.3):
                self.pending.append(('fills', (r,), o['xid']))                 # a duplicate delivery

    # ------------------------------------------------------------------------------------------- the venue
    def execute(self, o, q, price):
        """Fill q of order o at price against the venue position (reduce-only orders never exceed it)."""
        if o['reduce']:
            q = min(q, self.pos)
        if q <= 0:
            return D(0)
        self.pos = self.pos - q if o['reduce'] else self.pos + q
        o['filled'] += q
        self.queue_fills(o, self.rows(o, q, price))
        return q

    def accept(self, d):
        cid = d.client_id
        assert cid not in self.client_ids, f'duplicate client id {cid}'
        self.client_ids.append(cid)
        stop = d.stop_price is not None
        o = dict(kind='stop' if stop else 'market', qty=d.qty, price=d.stop_price, xid=self.nid('x'), filled=D(0),
                 live=True, reduce=d.reduce_only, leg=d.leg)
        lost = self.chance(0.4)
        if lost and self.chance(0.5):                         # never reached the venue
            self.answer(cid, self.outcome(cid, OutcomeKind.UNKNOWN), True)
            self.pending.append(('outcome', self.outcome(cid, OutcomeKind.NOT_FOUND, error_code=-2013), False, cid))
            return
        if stop and d.route == 'classic' and self.chance(0.6):     # -4120: this venue wants the algo route (TNET-01)
            self.answer(cid, self.outcome(cid, OutcomeKind.REJECTED, error_code=-4120), True)
            return
        replacing = stop and any(x['kind'] == 'stop' and x['live'] for x in self.orders.values())
        if replacing and self.chance(0.4):                    # -2021: the replacement would trigger immediately
            self.answer(cid, self.outcome(cid, OutcomeKind.REJECTED, error_code=-2021), True)
            return
        if replacing and self.chance(0.4):                    # the old stop fills while the new one is in flight
            for ocid, old in list(self.orders.items()):
                if old['kind'] == 'stop' and old['live']:
                    self.trigger_stop(ocid, old)
        self.orders[cid] = o
        if stop:
            first = self.outcome(cid, OutcomeKind.KNOWN, status='NEW', exchange_order_id=o['xid'])
        else:
            q = d.qty
            if self.chance(0.5):                              # a short market fill (IOC remainder expired)
                q = max(self.p.rules.quantize_qty(d.qty * D(self.rng.randint(1, 9)) / 10, Rounding.DOWN),
                        self.p.rules.step_size)          # a market order with liquidity fills at least one step
            px = market_fill(self.mark, self.side, self.p.costs.slip, opening=not d.reduce_only)
            done = self.execute(o, q, px) if q > 0 else D(0)
            o['live'] = False
            first = self.outcome(cid, OutcomeKind.FINAL, status='FILLED' if done == d.qty else 'EXPIRED',
                                 exchange_order_id=o['xid'], executed_qty=done, avg_price=px if done > 0 else None)
        if lost:
            self.answer(cid, self.outcome(cid, OutcomeKind.UNKNOWN), True)
            self.pending.append(('outcome', first, False, cid))
        else:
            self.answer(cid, first, True)

    def trigger_stop(self, cid, o):
        px = market_fill(o['price'], self.side, self.p.costs.slip, opening=False)
        done = self.execute(o, o['qty'], px)
        o['live'] = False
        self.pending.append(('outcome', self.outcome(cid, OutcomeKind.FINAL, status='FILLED' if done else 'EXPIRED',
                                                     exchange_order_id=o['xid'], executed_qty=done,
                                                     avg_price=px if done else None), False, cid))

    def cancel(self, c):
        o = self.orders.get(c.client_id)
        if o is None or not o['live']:
            self.answer(c.client_id, self.outcome(c.client_id, OutcomeKind.REJECTED, error_code=-2011), False)
            return
        if o['kind'] == 'stop' and self.chance(0.3) and self.pos > 0:      # it partly executed just before the cancel
            step = self.p.rules.step_size
            n = int(min(o['qty'], self.pos) / step)
            if n >= 1:
                q = step * self.rng.randint(1, n)
                px = market_fill(o['price'], self.side, self.p.costs.slip, opening=False)
                q = self.execute(o, q, px)
        o['live'] = False
        fin = self.outcome(c.client_id, OutcomeKind.FINAL, status='CANCELED', exchange_order_id=o['xid'],
                           executed_qty=o['filled'],
                           avg_price=None if o['filled'] == 0 else market_fill(o['price'], self.side,
                                                                                self.p.costs.slip, opening=False))
        self.answer(c.client_id, fin, False)

    def answer(self, cid, outcome, submit):
        self.pending.insert(0, ('outcome', outcome, submit, cid))

    # ------------------------------------------------------------------------------------------- the driver
    def handle(self, drive):
        self.ds = drive.state
        for r in drive.reconcile:
            self.reported.add(r)
        for d in drive.submits:
            self.accept(d)
        for c in drive.cancels:
            self.cancel(c)

    def feed(self, ev):
        before = self.ds
        self.log.append(ev)
        kind = ev[0]
        if kind == 'fills':
            drv = DR.on_fills(self.ds, ev[1])
        elif kind == 'outcome':
            drv = DR.on_outcome(self.ds, ev[1], submit=ev[2])
        elif kind == 'mark':
            drv = DR.on_mark(self.ds, ev[1])
        else:
            drv = DR.on_candle(self.ds, ev[1], close_request=ev[2], funding=ev[3])
        self.handle(drv)
        check(self, before, ev, drv)

    def deliverable(self):
        """Outcomes always; fills once the driver knows their exchange order id (the runner reads fills per order
        id), and duplicates of already-booked trades (to exercise dedupe)."""
        known = {b.exchange_order_id for b in self.ds.bindings if b.exchange_order_id}
        booked = set(self.ds.trade_ids)
        return [k for k, e in enumerate(self.pending) if e[0] == 'outcome' or e[2] in known or
                e[1][0].trade_id in booked]

    def deliver_one(self, rand=True):
        ks = self.deliverable()
        if not ks:
            return False
        k = self.rng.choice(ks) if rand else ks[0]
        e = self.pending.pop(k)
        if e[0] == 'outcome':
            self.feed(('outcome', e[1], e[2]))
        else:
            self.feed(('fills', e[1]))
        return True

    def move(self):
        step = D(self.rng.randint(-120, 120)) / 100
        self.mark = max(self.mark + step, D('1'))
        for cid, o in list(self.orders.items()):
            if o['kind'] == 'stop' and o['live'] and ((self.mark <= o['price']) if self.long else
                                                      (self.mark >= o['price'])):
                self.trigger_stop(cid, o)
        if self.ds.pos.stage is not Stage.DONE:
            self.feed(('mark', self.mark))

    def close_candle(self):
        c = Candle(open_ms=self.p.entry_candle_open_ms + self.i * H4, open=self.mark, high=self.mark + D('0.01'),
                   low=max(self.mark - D('0.01'), D('0.5')), close=self.mark)
        self.i += 1
        self.feed(('candle', c, None, None))

    def crash_and_restart(self):
        replay = DR.fold(self.p, account_id=ACCT, lot_id=LOT, entry_fee=self.fee, events=self.log)
        if replay[-1].state != self.ds:
            raise Violation('fold(log) != live state')
        self.ds = replay[-1].state


def coverage_ok(v, ds):
    """The TRUE exposure (the venue's position) is covered by confirmed stops, or the gap is accounted for."""
    exposure = v.pos
    cov = DR.confirmed_coverage(ds)
    if exposure <= cov:
        return True
    closing = sum((b.qty - b.filled for b in ds.bindings if b.leg in (Leg.CLOSE, Leg.TP1, Leg.TP2) and b.current
                   and b.state in (DR.BindState.SENT, DR.BindState.FINAL)), D(0))   # FINAL short: retried once booked
    closing += sum((d.qty for d in ds.waiting), D(0))     # a close waiting for an in-flight reduce to settle
    adding = sum(((b.executed if b.executed is not None else b.qty) - b.filled for b in ds.bindings
                  if b.leg is Leg.ADD), D(0))
    if exposure - cov <= closing + adding:
        return True              # the gap is being closed at market, or is an add whose fill protection must follow
    in_flight = any(b.leg is Leg.STOP and b.current and b.state is DR.BindState.SENT for b in ds.bindings)
    if in_flight:
        return True        # one in-flight placement / replacement (its old stop is live, or it already filled its part)
    return bool(v.reported)                                  # a lost answer is out for reconcile


def check(v, before, ev, drv):
    ds = drv.state
    pos = ds.pos
    if any(b.leg is Leg.STOP and b.state is DR.BindState.WORKING for b in ds.bindings):
        v.ever_confirmed = True
    if v.ever_confirmed and not coverage_ok(v, ds):
        raise Violation(f'unprotected by confirmed coverage: qty {pos.qty} cov {DR.confirmed_coverage(ds)}')
    added = ev[0] == 'fills' and any(f.leg is Leg.ADD for f in pos.fills if f not in before.pos.fills)
    if added and pos.stage is Stage.ACTIVE and pos.stop is not None:
        live = pos.qty - pos.closing
        if risk_to_stop(v.p, pos, pos.stop.price, live) > v.p.risk_cap:
            raise Violation('above the risk cap after an add')
    ids = [f.fill_id for f in pos.fills]
    if len(ids) != len(set(ids)):
        raise Violation('a venue trade booked twice')
    refused_stop = any(s.state.stop_locked and not before.pos.stop_locked for s in drv.steps)
    if before.pos.stop is not None and pos.stop is not None and not refused_stop:
        s = 1 if v.long else -1
        if s * (pos.stop.price - before.pos.stop.price) < 0:
            raise Violation(f'stop loosened {before.pos.stop.price} -> {pos.stop.price}')


def run_faults(seed, steps=70, fault=0.25, crash=0.15):
    rng = random.Random(seed)
    p = None
    while p is None:
        p = TP.gen_plan(rng, allow_trail=False)
    v = FaultVenue(seed, p, fault)
    v.fee = (p.costs.taker_fee * p.entry_qty * p.entry_price).quantize(D('1e-10'))
    v.handle(DR.start(p, account_id=ACCT, lot_id=LOT, entry_fee=v.fee))
    for _ in range(steps):
        if v.ds.pos.stage is Stage.DONE and not v.pending:
            break
        r = v.rng.random()
        if r < 0.45:
            v.move()
        elif r < 0.85:
            v.deliver_one()
        elif r < 0.95 and v.ds.pos.stage is not Stage.DONE:
            v.close_candle()
        if v.rng.random() < crash:
            v.crash_and_restart()
    # drain: the venue delivers everything it owes; the driver may react with new orders (answered faithfully)
    v.fault = 0
    for _ in range(400):
        if not v.deliver_one(rand=False):
            break
    v.crash_and_restart()
    end_state(v)
    return v


def end_state(v):
    pos = v.ds.pos
    unmatched = [r for r in v.reported if r[0] == 'unmatched_fill']
    if unmatched:
        raise Violation(f'fills the driver could not book: {unmatched}')
    if pos.qty != v.pos:
        raise Violation(f'core position {pos.qty} != venue position {v.pos} after full delivery')
    if pos.stage is Stage.DONE or DR.protected(v.ds) or v.reported or pos.closing >= pos.qty:
        return
    if any(b.state is DR.BindState.SENT for b in v.ds.bindings):
        return                                              # an order in flight (its answer is a NOT_FOUND / UNKNOWN)
    raise Violation(f'open {pos.qty}, unprotected, nothing reported: silently wrong')


@pytest.mark.parametrize('seed', FAST_SEEDS)
def test_driver_survives_injected_venue_faults(seed):
    run_faults(seed)


@pytest.mark.slow
def test_driver_faults_20k():
    for seed in range(20_000):
        run_faults(seed)
