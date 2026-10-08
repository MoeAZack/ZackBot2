"""(1) Journal rule G6 vs the stop route policy, (2) the trail definition (G-STOP-CROSSED, pending a Codex ruling).

(1) G6 allows an algo stop ONLY right after a refused classic attempt for the same protection. `stop_route_policy`
`per_attempt` (the default) is G6-legal: every new stop - placement, resize, break-even, trail, restore - is tried
classic first. `sticky_after_refusal` (kept for a possible G6 amendment) sends later stops straight to the algo route.
A multi-replace flow on a testnet-like venue (every classic stop refused with -4120) must produce only G6-legal
sequences, keep the fault-fuzz invariants after every driver call, end flat with no live stop, and fold to the identical
driver state at every event boundary.

(2) `trail_mode`: `close_offset` (default: close -/+ trail_offset) and `highest_high_atr` (legacy chandelier: highest
high since entry - lowest low for a short - minus / plus trail_offset x the CURRENT ATR supplied with each candle).
Both only ever tighten the stop; long and short mirror exactly."""
import dataclasses
import random
from decimal import Decimal as D

import pytest

import test_driver_faults as F
from mg_factories import K, LONG, SHORT, T0, ZERO_COSTS, candle, plan, px
from newcore.domain import DomainError, Side, make_id
from newcore.management import Leg, Stage, TrailMode, initial_state, step
from newcore.management import driver as DR
from newcore.management.plan import market_fill
from newcore.ports.venue import OrderOutcome, OrderRef, OutcomeKind, VenueFill

SIDES = (LONG, SHORT)
ACCT, LOT = make_id('acct', 61), make_id('lot', 62)


# ===================================================================================================== (1) G6
class G6Venue:
    """Testnet-like: classic stops are refused (-4120), algo stops rest until crossed, market orders fill in full,
    cancels of live orders are confirmed. Answers are delivered in order after the event that caused them."""

    def __init__(self, p, policy):
        self.p, self.side, self.long, self.policy = p, p.side, p.side is Side.LONG, policy
        self.pos, self.mark, self.n = p.entry_qty, p.entry_price, 0
        self.stops, self.log, self.trace, self.inbox, self.busy = {}, [], [], [], True
        self.reported, self.ever_confirmed, self.stop_drafts = set(), False, []    # (draft, answer)
        self.fee = D(0)
        self.ds = None
        self.handle(DR.start(p, account_id=ACCT, lot_id=LOT, entry_fee=self.fee, stop_route_policy=policy))
        self.busy = False
        self.feed(None)

    def nid(self, tag):
        self.n += 1
        return f'{tag}{self.n}'

    def out(self, d, kind, **kw):
        return OrderOutcome(kind=kind, ref=OrderRef(symbol=self.p.symbol, client_id=d.client_id, route=d.route),
                            observed_at_ms=T0 + self.n, **kw)

    def feed(self, ev):
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
                if ev[0] == 'fills':
                    drv = DR.on_fills(self.ds, ev[1])
                elif ev[0] == 'outcome':
                    drv = DR.on_outcome(self.ds, ev[1], submit=ev[2])
                elif ev[0] == 'mark':
                    drv = DR.on_mark(self.ds, ev[1])
                else:
                    drv = DR.on_candle(self.ds, ev[1], close_request=ev[2], funding=ev[3])
                self.reported.update(drv.reconcile)
                F.check(self, before, ev, drv)
                self.trace.append(drv.state)
                self.handle(drv)
        finally:
            self.busy = False

    def handle(self, drive):
        self.ds = drive.state
        for d in drive.submits:
            self.submit(d)
        for c in drive.cancels:
            st = self.stops.pop(c.client_id, None)
            kind = OutcomeKind.FINAL if st else OutcomeKind.REJECTED
            kw = dict(status='CANCELED', exchange_order_id=st[2], executed_qty=D(0)) if st else dict(error_code=-2011)
            self.feed(('outcome', OrderOutcome(kind=kind, ref=OrderRef(symbol=self.p.symbol, client_id=c.client_id,
                                                                       route=c.route),
                                               observed_at_ms=T0 + self.n, **kw), False))

    def submit(self, d):
        if d.stop_price is not None:
            if d.route == 'classic':
                self.stop_drafts.append((d, 'rejected'))
                self.feed(('outcome', self.out(d, OutcomeKind.REJECTED, error_code=-4120), True))
                return
            self.stop_drafts.append((d, 'known'))
            xid = self.nid('x')
            self.stops[d.client_id] = (d.qty, d.stop_price, xid, d.route)
            self.feed(('outcome', self.out(d, OutcomeKind.KNOWN, status='NEW', exchange_order_id=xid), True))
            return
        q = min(d.qty, self.pos) if d.reduce_only else d.qty
        self.pos = self.pos - q if d.reduce_only else self.pos + q
        xid, price = self.nid('x'), market_fill(self.mark, self.side, D(0), opening=not d.reduce_only)
        self.feed(('outcome', self.out(d, OutcomeKind.FINAL, status='FILLED', exchange_order_id=xid, executed_qty=q,
                                       avg_price=price), True))
        self.feed(('fills', (self.fill(xid, q, price),)))

    def fill(self, xid, q, price):
        return VenueFill(trade_id=self.nid('t'), exchange_order_id=xid, symbol=self.p.symbol,
                         position_side=self.side.value, qty=q, price=price, fee=D(0), fee_asset='USDT',
                         realized_pnl=D(0), maker=False, at_ms=T0 + self.n)

    def trigger(self, adverse):
        for cid, (q, price, xid, route) in sorted(self.stops.items()):
            if (adverse <= price) if self.long else (adverse >= price):
                del self.stops[cid]
                q = min(q, self.pos)
                self.pos -= q
                ref = OrderRef(symbol=self.p.symbol, client_id=cid, route=route)
                self.feed(('outcome', OrderOutcome(kind=OutcomeKind.FINAL, ref=ref, observed_at_ms=T0 + self.n,
                                                   status='FILLED' if q else 'EXPIRED', exchange_order_id=xid,
                                                   executed_qty=q, avg_price=price if q else None), False))
                if q:
                    self.feed(('fills', (self.fill(xid, q, price),)))

    def to(self, p):
        self.mark = px(self.side, p)
        self.trigger(self.mark)
        if self.ds.pos.stage is not Stage.DONE:
            self.feed(('mark', self.mark))

    def close(self, c):
        self.mark = c.close
        self.trigger(c.low if self.long else c.high)
        if self.ds.pos.stage is not Stage.DONE:
            self.feed(('candle', c, None, None))


def g6_sequence_ok(stop_drafts):
    """Over the whole run: every algo stop is immediately preceded by a refused classic attempt of the same stop."""
    for k, (d, _) in enumerate(stop_drafts):
        if d.route == 'algo':
            if k == 0:
                return False
            prev, ans = stop_drafts[k - 1]
            if prev.route != 'classic' or ans != 'rejected' or (prev.stop_price, prev.qty) != (d.stop_price, d.qty):
                return False
    return True


def atr_candle(i, o, h, lo, c, side, atr='1'):
    return dataclasses.replace(candle(i, o, h, lo, c, side), atr=D(atr))


def multi_replace_flow(side, policy, trail_mode):
    p = plan(side, add='99.02', scale='1', cap='20', tp1_frac='0.5', tp1_off='1', tp2_off='4', be=True, trail='1.5',
             costs=ZERO_COSTS, trail_mode=trail_mode)
    v = G6Venue(p, policy)
    v.to('98.9')                                                   # the add (behind the confirmed algo stop) -> resize
    v.to('100.8')                                                  # TP1 -> break-even replace
    for i, bar in enumerate([(100.8, 101.6, 100.7, 101.5), (101.5, 102.4, 101.4, 102.3), (102.3, 103.3, 102.2, 103.2),
                             (103.2, 103.4, 102.8, 103.0)], start=1):
        v.close(atr_candle(i, *(str(x) for x in bar), side))      # trail ratchets (replacements)
    v.close(atr_candle(5, '103.0', '103.1', '98.0', '98.5', side))  # through the stop
    return v


@pytest.mark.parametrize('trail_mode', ('close_offset', 'highest_high_atr'))
@pytest.mark.parametrize('side', SIDES)
def test_per_attempt_multi_replace_flow_is_g6_legal_and_replays(side, trail_mode):
    v = multi_replace_flow(side, 'per_attempt', trail_mode)
    algo = [d for d, a in v.stop_drafts if d.route == 'algo']
    assert len(algo) >= 5                                       # placement, resize, break-even, >= 2 trail moves
    assert g6_sequence_ok(v.stop_drafts)
    assert all(a == 'rejected' for d, a in v.stop_drafts if d.route == 'classic')
    assert v.ds.pos.stage is Stage.DONE and v.ds.pos.qty == 0 == v.pos and not v.stops     # flat, no stop left
    assert v.ds.stop_route == 'classic'
    replay = DR.fold(v.p, account_id=ACCT, lot_id=LOT, entry_fee=v.fee, events=v.log, stop_route_policy='per_attempt')
    assert len(replay) == len(v.trace) + 1
    for k, live in enumerate(v.trace):
        assert replay[k + 1].state == live, k


@pytest.mark.parametrize('side', SIDES)
def test_sticky_policy_is_not_g6_legal_the_checker_has_teeth(side):
    v = multi_replace_flow(side, 'sticky_after_refusal', 'close_offset')
    assert not g6_sequence_ok(v.stop_drafts)
    assert sum(1 for d, _ in v.stop_drafts if d.route == 'classic') == 1        # only the very first stop tried it
    assert v.ds.pos.stage is Stage.DONE and not v.stops
    replay = DR.fold(v.p, account_id=ACCT, lot_id=LOT, entry_fee=v.fee, events=v.log,
                     stop_route_policy='sticky_after_refusal')
    assert replay[-1].state == v.ds


@pytest.mark.parametrize('side', SIDES)
def test_flat_orphan_fix_still_cancels_the_replacement_under_per_attempt(side):
    """a0acf99 kept: the old algo stop fills the rest while the break-even replacement is confirmed; flat -> cancel."""
    p = plan(side, tp1_frac='0.5', tp1_off='1', tp2_off='2', be=True, costs=ZERO_COSTS)
    v = G6Venue(p, 'per_attempt')
    v.to('101.1')                                                  # TP1 -> break-even placed (classic refused, algo)
    old = [cid for cid, s in v.stops.items()]
    assert len(old) == 1                                           # the replacement is live, the old one cancelled
    v.to('97.9')                                                   # ... the live (break-even) stop fills
    assert v.ds.pos.qty == 0 and not v.stops


# ===================================================================================================== (2) trail
def run_candles(p, cs):
    st = step(p, initial_state(p, D(0))).state
    stops = [st.stop.price]
    for c in cs:
        st = step(p, st, (), c).state
        stops.append(st.stop.price if st.stop is not None else None)
    return st, stops


BARS = [('100', '101', '99.5', '100.5', '1'), ('100.5', '103', '100', '102', '1'), ('102', '102.5', '101.6', '102.2', '2'),
        ('102.2', '105', '102.1', '104.4', None), ('104.4', '104.6', '104.3', '104.5', '0.5')]


@pytest.mark.parametrize('side', SIDES)
def test_highest_high_atr_chandelier(side):
    p = plan(side, trail='1.5', costs=ZERO_COSTS, trail_mode='highest_high_atr')
    assert p.trail_mode is TrailMode.HIGHEST_HIGH_ATR
    cs = [atr_candle(i, o, h, lo, c, side, a) if a else candle(i, o, h, lo, c, side)
          for i, (o, h, lo, c, a) in enumerate(BARS)]
    st, stops = run_candles(p, cs)
    want = ['98.02', '99.5', '101.5', '101.5', '101.5', '104.25']
    # c0: HH 101 - 1.5 x 1; c1: HH 103 - 1.5; c2: HH 103 - 1.5 x 2 = 100 < 101.5 (ATR expanded: never loosens);
    # c3: no ATR at this close - the trail holds (HH 105 still recorded); c4: HH 105 - 1.5 x 0.5 = 104.25
    assert stops == [px(side, x) for x in want]
    assert st.trail_extreme == px(side, '105')


@pytest.mark.parametrize('side', SIDES)
def test_close_offset_stays_the_default_and_ignores_atr(side):
    p = plan(side, trail='1.5', costs=ZERO_COSTS)
    assert p.trail_mode is TrailMode.CLOSE_OFFSET
    cs = [atr_candle(i, o, h, lo, c, side, a or '7') for i, (o, h, lo, c, a) in enumerate(BARS)]
    st, stops = run_candles(p, cs)
    assert stops == [px(side, x) for x in ('98.02', '99', '100.5', '100.7', '102.9', '103')]
    assert st.trail_extreme is None


@pytest.mark.parametrize('side', SIDES)
def test_highest_high_since_entry_is_tracked_before_tp1_arms_the_trail(side):
    """The chandelier's best price runs from the entry; the trail itself waits for the complete TP1 leg."""
    from newcore.management import ConfirmedFill
    p = plan(side, trail='1', tp1_frac='0.5', tp1_off='1', tp2_off='6', costs=ZERO_COSTS, trail_mode='highest_high_atr')
    st = step(p, initial_state(p, D(0))).state
    st = step(p, st, (), atr_candle(0, '100', '104', '99.9', '100.5', side)).state
    assert st.stop.price == px(side, '98.02') and st.trail_extreme == px(side, '104')
    st = step(p, st, (ConfirmedFill(fill_id='f1', leg=Leg.TP1, qty=st.tp1.qty, price=st.tp1.price, fee=D(0)),)).state
    st = step(p, st, (), atr_candle(1, '101', '101.5', '100.9', '101.3', side)).state
    assert st.stop.price == px(side, '103')                       # 104 (from before TP1) - 1 x 1


def test_highest_high_atr_needs_a_trail_multiple():
    with pytest.raises(DomainError):
        plan(LONG, costs=ZERO_COSTS, trail_mode='highest_high_atr')


def test_candle_atr_must_be_positive():
    with pytest.raises(DomainError):
        atr_candle(0, '100', '101', '99', '100', LONG, '0')


@pytest.mark.parametrize('mode', ('close_offset', 'highest_high_atr'))
@pytest.mark.parametrize('seed', range(40))
def test_trail_never_loosens_and_long_short_mirror(seed, mode):
    rng = random.Random(seed)
    bars, c = [], D('100.5')
    for i in range(25):
        o = c
        c = max(min(o + D(rng.randint(-150, 200)) / 100, D(150)), D(60))
        h = max(o, c) + D(rng.randint(0, 80)) / 100
        lo = min(o, c) - D(rng.randint(0, 80)) / 100
        a = None if rng.random() < 0.15 else str(D(rng.randint(20, 300)) / 100)
        bars.append((str(o), str(h), str(lo), str(c), a))
    runs, trail = {}, str(D(rng.randint(5, 30)) / 10)
    for side in SIDES:
        p = plan(side, trail=trail, costs=ZERO_COSTS, trail_mode=mode, cap='40')
        cs = [atr_candle(i, o, h, lo, c_, side, a) if a else candle(i, o, h, lo, c_, side)
              for i, (o, h, lo, c_, a) in enumerate(bars)]
        st = step(p, initial_state(p, D(0))).state
        seq = [st.stop.price]
        for x in cs:
            if st.stage is Stage.DONE:
                break
            prev = st.stop.price if st.stop is not None else None
            st = step(p, st, (), x).state
            if prev is not None and st.stop is not None:
                assert (st.stop.price >= prev) if side is LONG else (st.stop.price <= prev), 'stop loosened'
            seq.append(st.stop.price if st.stop is not None else None)
        runs[side] = seq
    assert runs[SHORT] == [None if x is None else K - x for x in runs[LONG]]
