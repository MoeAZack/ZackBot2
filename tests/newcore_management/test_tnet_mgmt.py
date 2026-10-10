"""TNET-01 management scenarios T05-T08 through the ManagementDriver on a scripted FakeVenue.

Each scenario is a `zb-newcore-tnet-scenario/1` spec (tests/newcore_venue/fixtures/tnet/mgmt/<name>_<side>.json,
validated by newcore.venue.tnet_spec) - the exact order sequence the driver must produce on the venue, with the allowed
answer per step and the end state - plus a driver-level script (<name>.script.json, zb-newcore-tnet-mgmt/1): the plan,
the venue behaviour (classic stops refused with -4120 like testnet, partial fills, an acknowledged-only or in-flight
stop, held fills) and the price path. This executor runs the script, checks every step against the spec, runs the
fault-fuzz invariants (test_driver_faults.check) after every driver call, and checks the end state and that the durable
event log folds to the identical driver state. The runner-driven TNET harness runs the same specs on testnet.

Driver stop_route_policy is the default `per_attempt` (journal rule G6): EVERY new stop - the first protection, each
resize, the break-even - is tried on the classic route, refused (-4120), and only then re-sent on the algo route. The
specs therefore carry a `<step>_classic` (expect rejected) before every algo stop; the a0acf99 snapshot (sticky route:
only the first stop tried classic) is superseded."""
import glob
import json
import os
from decimal import Decimal as D

import pytest

import test_driver_faults as F
from mg_factories import GOLDEN_COSTS, T0, rules
from newcore.domain import Side, make_id
from newcore.domain.instrument import Rounding
from newcore.management import Leg, Stage, build_plan
from newcore.management import driver as DR
from newcore.management.plan import market_fill
from newcore.ports.venue import OrderOutcome, OrderRef, OutcomeKind, VenueFill
from newcore.venue.tnet_spec import load_spec

HERE = os.path.dirname(os.path.abspath(__file__))
FIX = os.path.join(os.path.dirname(HERE), 'newcore_venue', 'fixtures', 'tnet', 'mgmt')
ACCT, LOT = make_id('acct', 41), make_id('lot', 42)
SCRIPTS = sorted(os.path.basename(p)[:-len('.script.json')] for p in glob.glob(os.path.join(FIX, '*.script.json')))


class ScriptVenue:
    """A deterministic venue for one scripted scenario (attributes shared with the fault-fuzz invariants)."""

    def __init__(self, name, side):
        with open(os.path.join(FIX, f'{name}.script.json'), encoding='utf-8') as f:
            self.script = json.load(f)
        self.spec = load_spec(os.path.join(FIX, f'{name}_{side.value.lower()}.json'))
        self.ops_spec = [s for s in self.spec['steps'] if s['op'] != 'wait']
        self.side, self.long = side, side is Side.LONG
        self.venue = self.script['venue']
        self.n, self.log, self.ops, self.queue, self.stops = 0, [], [], [], {}
        self.inbox, self.busy, self.trace = [], False, []
        self.reported, self.ever_confirmed, self.pending_in_flight = set(), False, {}
        self.mark = self.px('100')
        r = rules()
        e = market_fill(self.mark, side, GOLDEN_COSTS.slip, opening=True)
        s = 1 if self.long else -1
        pl = self.script['plan']
        q = lambda x: r.quantize_price(x, Rounding.NEAREST)    # noqa: E731
        self.p = build_plan(
            rules=r, side=side, entry_price=e, entry_qty=D(5), entry_candle_open_ms=T0, candle_seconds=14400,
            stop_price=q(e - s * D(pl['stop'])), risk_cap=D(pl.get('cap', '20')), costs=GOLDEN_COSTS,
            add_price=q(e - s * D(pl['add'])) if 'add' in pl else None,
            add_scale=D(pl['scale']) if 'scale' in pl else None,
            tp1_frac=D(pl['tp1_frac']) if 'tp1_frac' in pl else None,
            tp1_offset=D(pl['tp1_off']) if 'tp1_off' in pl else None,
            tp2_offset=D(pl['tp2']) if 'tp2' in pl else None, be_after_tp1=bool(pl.get('be')),
            time_exit_candles=None, trail_offset=None).plan
        self.pos = D(5)
        self.record('market', side=side.value, reduce=False, qty=D(5), executed=D(5), answer='final', cid='entry')
        self.fee = (GOLDEN_COSTS.taker_fee * 5 * e).quantize(D('1e-10'))
        self.ds, self.busy = None, True
        self.handle(DR.start(self.p, account_id=ACCT, lot_id=LOT, entry_fee=self.fee))
        self.busy = False
        self.feed(None)

    # ----------------------------------------------------------------------------------------------- plumbing
    def px(self, v):
        v = D(v)
        return v if self.long else D(200) - v

    def nid(self, tag):
        self.n += 1
        return f'{tag}{self.n}'

    def label(self):
        k = len(self.ops)
        return self.ops_spec[k]['id'] if k < len(self.ops_spec) else f'extra{k}'

    def record(self, op, **kw):
        self.ops.append(dict(op=op, **kw))

    def ref(self, cid, route='classic'):
        return OrderRef(symbol=self.p.symbol, client_id=cid, route=route)

    def out(self, cid, kind, route='classic', **kw):
        return OrderOutcome(kind=kind, ref=self.ref(cid, route), observed_at_ms=T0 + self.n, **kw)

    def feed(self, ev):
        """Deliver an event; the venue's answers to the orders it causes are delivered after it, in order."""
        if ev is not None:
            self.inbox.append(ev)
        if self.busy:
            return
        self.busy = True
        try:
            while self.inbox:
                ev = self.inbox.pop(0)
                before = self.ds
                self.log.append(ev)
                k = ev[0]
                if k == 'fills':
                    drv = DR.on_fills(self.ds, ev[1])
                elif k == 'outcome':
                    drv = DR.on_outcome(self.ds, ev[1], submit=ev[2])
                else:
                    drv = DR.on_mark(self.ds, ev[1])
                self.reported.update(drv.reconcile)
                F.check(self, before, ev, drv)
                self.trace.append(drv.state)
                self.handle(drv)
        finally:
            self.busy = False

    def fill(self, xid, q, price):
        return VenueFill(trade_id=self.nid('t'), exchange_order_id=xid, symbol=self.p.symbol,
                         position_side=self.side.value, qty=q, price=price,
                         fee=(GOLDEN_COSTS.taker_fee * q * price).quantize(D('1e-12')),
                         fee_asset='USDT', realized_pnl=D(0), maker=False, at_ms=T0 + self.n)

    # ----------------------------------------------------------------------------------------------- the venue
    def handle(self, drive):
        self.ds = drive.state
        if any(b.leg is Leg.STOP and b.state is DR.BindState.WORKING for b in self.ds.bindings):
            self.ever_confirmed = True
        for d in drive.submits:
            self.submit(d)
        for c in drive.cancels:
            self.cancel(c)

    def crossed(self, price):
        return self.mark <= price if self.long else self.mark >= price

    def submit(self, d):
        label = self.label()
        if d.stop_price is not None:
            self.record('stop', side=d.side.value, route=d.route, qty=d.qty, cid=d.client_id, answer=None)
            rec = self.ops[-1]
            if d.route == 'classic' and self.venue.get('classic_stops') == 'algo_only':
                rec['answer'] = 'rejected'
                self.feed(('outcome', self.out(d.client_id, OutcomeKind.REJECTED, d.route, error_code=-4120), True))
                return
            if label in self.venue.get('in_flight', []):          # not at the venue yet: answered on 'confirm'
                self.pending_in_flight[label] = (d, rec)
                return
            xid = self.nid('x')
            self.stops[d.client_id] = dict(qty=d.qty, price=d.stop_price, xid=xid, route=d.route)
            if label in self.venue.get('acknowledge_only', []):
                rec['answer'] = 'acknowledged'
                self.pending_in_flight[label] = (d, rec)
                self.feed(('outcome', self.out(d.client_id, OutcomeKind.ACKNOWLEDGED, d.route), True))
                return
            rec['answer'] = 'known'
            self.feed(('outcome', self.out(d.client_id, OutcomeKind.KNOWN, d.route, status='NEW',
                                           exchange_order_id=xid), True))
            return
        q = D(self.venue.get('partial', {}).get(label, d.qty))
        q = min(q, self.pos) if d.reduce_only else q
        self.pos = self.pos - q if d.reduce_only else self.pos + q
        xid = self.nid('x')
        price = market_fill(self.mark, self.side, GOLDEN_COSTS.slip, opening=not d.reduce_only)
        self.record('market', side=d.side.value, reduce=d.reduce_only, qty=d.qty, executed=q, cid=d.client_id,
                    answer='final')
        self.feed(('outcome', self.out(d.client_id, OutcomeKind.FINAL, d.route,
                                       status='FILLED' if q == d.qty else 'EXPIRED', exchange_order_id=xid,
                                       executed_qty=q, avg_price=price if q > 0 else None), True))
        if q > 0:
            self.feed(('fills', (self.fill(xid, q, price),)))

    def cancel(self, c):
        st = self.stops.pop(c.client_id, None)
        if st is None:
            self.record('cancel', cid=c.client_id, answer='rejected')
            self.feed(('outcome', self.out(c.client_id, OutcomeKind.REJECTED, c.route, error_code=-2011), False))
            return
        self.record('cancel', cid=c.client_id, answer='final')
        self.feed(('outcome', self.out(c.client_id, OutcomeKind.FINAL, c.route, status='CANCELED',
                                       exchange_order_id=st['xid'], executed_qty=D(0)), False))

    def trigger(self):
        for cid, st in list(self.stops.items()):
            if not self.crossed(st['price']):
                continue
            del self.stops[cid]
            q = min(st['qty'], self.pos)
            self.pos -= q
            price = market_fill(st['price'], self.side, GOLDEN_COSTS.slip, opening=False)
            evs = [('outcome', self.out(cid, OutcomeKind.FINAL, st['route'], status='FILLED' if q else 'EXPIRED',
                                        exchange_order_id=st['xid'], executed_qty=q,
                                        avg_price=price if q else None), False)]
            if q:
                evs.append(('fills', (self.fill(st['xid'], q, price),)))
            if self.venue.get('hold_fills'):
                self.queue.extend(evs)
            else:
                for ev in evs:
                    self.feed(ev)

    def confirm(self, label):
        d, rec = self.pending_in_flight.pop(label)
        if label in self.venue.get('in_flight', []):         # the late answer to the submit
            if self.crossed(d.stop_price):
                rec['answer'] = 'rejected'
                self.feed(('outcome', self.out(d.client_id, OutcomeKind.REJECTED, d.route, error_code=-2021), True))
                return
            xid = self.nid('x')
            self.stops[d.client_id] = dict(qty=d.qty, price=d.stop_price, xid=xid, route=d.route)
            rec['answer'] = 'known'
            self.feed(('outcome', self.out(d.client_id, OutcomeKind.KNOWN, d.route, status='NEW',
                                           exchange_order_id=xid), True))
            return
        st = self.stops[d.client_id]                           # acknowledged earlier: the runner's query
        self.record('query', cid=d.client_id, answer='known')
        self.feed(('outcome', self.out(d.client_id, OutcomeKind.KNOWN, d.route, status='NEW',
                                       exchange_order_id=st['xid']), False))

    def run(self):
        for stepx in self.script['path']:
            if 'mark' in stepx:
                self.mark = self.px(stepx['mark'])
                self.trigger()
                if self.ds.pos.stage is not Stage.DONE:
                    self.feed(('mark', self.mark))
            elif 'confirm' in stepx:
                self.confirm(stepx['confirm'])
            elif 'deliver' in stepx:
                while self.queue:
                    self.feed(self.queue.pop(0))
        return self


# --------------------------------------------------------------------------------------------------------- checks
def check_against_spec(v):
    spec_ops, ops = v.ops_spec, v.ops
    assert [s['op'] for s in spec_ops] == [o['op'] for o in ops], [(o['op'], o.get('route'), str(o.get('qty')))
                                                                  for o in ops]
    cid_of, executed = {}, {}
    outcomes = v.spec['expect'].get('outcomes', {})
    for s, o in zip(spec_ops, ops):
        if s['op'] in ('market', 'stop'):
            assert s['side'] == o['side'], s['id']
            q = s['qty']
            want = executed[q[len('filled:'):]] if q.startswith('filled:') else None if q == 'min' else D(q)
            assert want is None or want == o['qty'], (s['id'], want, o['qty'])
        if s['op'] == 'market':
            assert s['reduce'] == o['reduce'], s['id']
            executed[s['id']] = o['executed']
        if s['op'] == 'stop':
            assert s['route'] == o['route'], s['id']
        if s['op'] in ('cancel', 'query'):
            assert cid_of[s['ref']] == o['cid'], s['id']
        cid_of[s['id']] = o['cid']
        if s['id'] in outcomes:
            assert o['answer'] in outcomes[s['id']], (s['id'], o['answer'])
    for k, s in enumerate(spec_ops):        # journal rule G6 (stop_route_policy per_attempt): every algo stop is the
        if s['op'] == 'stop' and s['route'] == 'algo':      # re-send of the classic attempt just refused before it
            prev = spec_ops[k - 1]
            assert (prev['op'], prev['route'], prev['qty']) == ('stop', 'classic', s['qty']), s['id']
            assert outcomes[prev['id']] == ['rejected'] and ops[k - 1]['answer'] == 'rejected', s['id']
    end = v.spec['expect']['end_state']
    assert (v.pos == 0) == end['flat']
    assert len(v.stops) == end['open_newcore_orders']


@pytest.mark.parametrize('side', (Side.LONG, Side.SHORT))
@pytest.mark.parametrize('name', SCRIPTS)
def test_tnet_management_scenario(name, side):
    v = ScriptVenue(name, side).run()
    check_against_spec(v)
    pos = v.ds.pos
    assert pos.stage.value == v.script['end']['stage'] and pos.qty == v.pos
    assert [f.leg.value for f in pos.fills] == v.script['end']['legs']
    replay = DR.fold(v.p, account_id=ACCT, lot_id=LOT, entry_fee=v.fee, events=v.log)
    assert replay[-1].state == v.ds                                         # crash / restart at the end
    assert len(replay) == len(v.trace) + 1
    for k, live in enumerate(v.trace):                                      # ... and at every event boundary
        assert replay[k + 1].state == live, k


def test_every_scenario_has_both_sides_and_a_valid_spec():
    assert SCRIPTS == ['t05_target_exit', 't06_partial_close', 't07_bounded_dca', 't08_cancel_replace_race']
    for name in SCRIPTS:
        for side in ('long', 'short'):
            doc = load_spec(os.path.join(FIX, f'{name}_{side}.json'))
            assert doc['name'] == f'{name}_{side}' and doc['expect']['end_state'] == {'flat': True,
                                                                                    'open_newcore_orders': 0}
