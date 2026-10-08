"""NC-07 seeded stdlib properties (no Hypothesis): random plans, random live fill / refusal sequences and random candle
paths through the simulator. Every failure message names the seed, so `run_live(seed)` reproduces it exactly."""
import dataclasses
import random
from decimal import Decimal as D

import pytest

from mg_factories import GOLDEN_COSTS, H4, LONG, SHORT, T0, ZERO_COSTS, K, rules
from newcore.domain import DomainError, Side
from newcore.domain.instrument import Rounding
from newcore.management import (ActionKind as AK, Cancelled, Candle, ConfirmedFill, Leg, PlanRefused, Rejected, Stage,
                                Tier, build_plan, exit_ledger, initial_state, planned_risk, realized_pnl, risk_to_stop,
                                run, step)
from newcore.management.actions import MARKET, sort_key

SEEDS = range(250)


def seed_of(rng):
    """A deterministic venue fill id from the sequence's own generator."""
    return format(rng.getrandbits(64), '016x')
STEPS = ('0.001', '0.1', '1', '5')


def gen_plan(rng, side=None, costs=None, allow_trail=True):
    side = side or rng.choice((LONG, SHORT))
    st = rng.choice(STEPS)
    r = rules(step=st, min_notional='5')
    s = 1 if side is LONG else -1
    e = D(rng.randint(9000, 11000)) / 100
    dist = D(rng.randint(50, 500)) / 100
    stop = e - s * dist
    qty = D(st) * rng.randint(1, 12) if D(st) >= 1 else D(st) * rng.randint(max(1, int(5 / D(st) / 100)), 4000)
    add = scale = None
    if rng.random() < 0.7:
        frac = D(rng.randint(10, 90)) / 100
        add = (e - s * dist * frac) if rng.random() < 0.8 else (e + s * dist * frac)     # DCA or pyramid
        add = r.quantize_price(add, Rounding.NEAREST)
        scale = D(rng.choice(('0.5', '1', '1.5')))
    tp1 = rng.random() < 0.7
    tp1_off = D(rng.randint(20, 200)) / 100 if tp1 else None
    tp2 = rng.random() < 0.8
    tp2_off = (tp1_off or 0) + D(rng.randint(20, 300)) / 100 if tp2 else None
    costs = costs or rng.choice((GOLDEN_COSTS, ZERO_COSTS))
    risk = qty * dist * D('1.2') + qty * e * D('0.002')
    cap = (risk * D(rng.randint(80, 300)) / 100).quantize(D('0.01')) + D('0.01')
    try:
        b = build_plan(rules=r, side=side, entry_price=e, entry_qty=qty, entry_candle_open_ms=T0, candle_seconds=14400,
                       stop_price=stop, risk_cap=cap, costs=costs, add_price=add, add_scale=scale,
                       tp1_frac=D(rng.choice(('0.25', '0.5', '0.75'))) if tp1 else None, tp1_offset=tp1_off,
                       tp2_offset=tp2_off, be_after_tp1=tp1 and rng.random() < 0.8,
                       time_exit_candles=rng.choice((None, 1, 3, 12)),
                       trail_offset=D(rng.randint(50, 300)) / 100 if allow_trail and rng.random() < 0.3 else None)
    except PlanRefused:
        return None
    return b.plan


@pytest.mark.parametrize('seed', range(400))
def test_planned_risk_never_exceeds_the_cap(seed):
    p = gen_plan(random.Random(seed))
    if p is None:
        return
    assert planned_risk(p) <= p.risk_cap, seed
    assert p.add_qty is None or p.add_qty <= p.add_scale * p.entry_qty, seed


# ------------------------------------------------------------------------------------------------ live sequences
# Cowork's attack seeds on fbd618b (PR #38: m4_pure_attack.py): 285 / 6868 (retained TP1 on the loss side), 5458 / 5502 /
# 9993 / 17317 / 18548 (stop loosened after a STOP + ADD batch). Re-run here through this generator, plus the default
# seeds every commit and a 20,000-sequence slow run.
COWORK_SEEDS = (285, 6868, 5458, 5502, 9993, 17317, 18548)
OPEN_KINDS = (AK.PLACE_STOP, AK.REPLACE_STOP, AK.PLACE_TARGET, AK.REPLACE_TARGET, AK.PLACE_ADD)


def avg_of(st):
    return st.basket_cost / st.basket_qty


def check_step(p, before, r, placed_adds, seed, batch):
    st, s = r.state, 1 if p.side is LONG else -1
    assert list(r.actions) == sorted(r.actions, key=sort_key), seed
    if st.stage is Stage.ACTIVE:
        sq = st.stop.qty if st.stop else D(0)
        assert st.qty - st.closing <= sq <= st.qty, seed            # the stop covers the position, never more
    if before.stage in (Stage.EXITING, Stage.DONE):                # a terminal exit never re-opens the plan
        assert st.stage in (Stage.EXITING, Stage.DONE), seed
        for a in r.actions:                                        # only shrinking a still-working stop is allowed
            assert a.kind not in OPEN_KINDS or (a.kind is AK.REPLACE_STOP and before.stop is not None
                                                and a.qty <= before.stop.qty), seed
    refused_stop = any(type(e) is Rejected and e.leg is Leg.STOP for e in batch)
    if before.stop is not None and st.stop is not None and not refused_stop:
        assert s * (st.stop.price - before.stop.price) >= 0, seed  # the stop is never loosened
    for a in r.actions:
        if a.kind in (AK.PLACE_STOP, AK.REPLACE_STOP) and before.stop is not None and not refused_stop:
            assert s * (a.price - before.stop.price) >= 0, seed
    rq = p.rules
    for a in r.actions:
        if a.qty is None:
            continue
        assert rq.on_step(a.qty), seed
        if a.kind is AK.PLACE_ADD:
            assert a.qty <= p.add_qty and not before.tp1_confirmed and not st.tp1_confirmed, seed
        else:
            assert a.qty <= st.qty, seed
        if a.leg is Leg.TP1 and a.kind is not AK.CANCEL_TARGET and not st.tp1_confirmed:
            assert a.qty <= p.tp1_frac * (st.qty - st.closing), seed   # floor, never round up
    live = st.qty - st.closing
    for o in (st.tp1, st.tp2):
        if o is not None:
            assert s * (o.price - avg_of(st)) > 0, seed               # targets on the profitable side
            if o.qty != live:
                assert o.qty >= rq.min_qty and o.qty * o.price >= rq.min_notional, seed   # venue-feasible
    if st.tp1 is not None and st.tp2 is not None:
        assert live - st.tp1.qty == st.tp2.qty, seed
    if any(type(e) is ConfirmedFill and e.leg is Leg.ADD for e in batch) and st.stage is Stage.ACTIVE and st.stop:
        assert risk_to_stop(p, st, st.stop.price, live) <= p.risk_cap, seed   # never carry above-cap risk
    if st.stage is Stage.DONE:
        z = realized_pnl(p, st)
        assert not (z == 0 and (z.is_signed() or str(z) != '0')), seed
        assert exit_ledger(p, st).realized_net == z, seed
    placed_adds += sum(a.kind is AK.PLACE_ADD for a in r.actions)
    assert placed_adds <= 1, seed                                  # the add cap is one, in the core
    return placed_adds


def outstanding(st):
    """(leg, max qty) of every leg that may fill now: open orders, racing allowances, a requested close."""
    out = [(g, getattr(st, g.value).qty) for g in (Leg.STOP, Leg.ADD, Leg.TP1, Leg.TP2) if getattr(st, g.value)]
    out += [(r.leg, r.qty) for r in st.racing if all(r.leg is not g for g, _ in out)]
    if st.closing > 0:
        out.append((Leg.CLOSE, st.closing))
    return out


def a_fill(rng, p, st, leg, cap, price):
    n = int(cap / p.rules.step_size)
    if n == 0:
        return None
    q = p.rules.step_size * rng.choice((n, n, rng.randint(1, n)))      # full fills twice as likely as partial ones
    o = getattr(st, leg.value) if leg is not Leg.CLOSE else None
    px = o.price if o is not None else (p.add_price if leg is Leg.ADD else price)
    if leg is Leg.ADD and rng.random() < 0.4:                         # an adverse / gapped add fill
        px = max(px + (1 if p.side is LONG else -1) * D(rng.randint(0, 400)) / 100, D(1))
    fee = (p.costs.taker_fee * q * px).quantize(D('1e-10'))
    return ConfirmedFill(fill_id=f'r{seed_of(rng)}', leg=leg, qty=q, price=px, fee=fee)


def random_inputs(rng, p, st, last_actions, price, i, used):
    """One live input batch for `st` (1-3 events, authorised fills, refusals, cancel confirmations, duplicates), or a
    closed candle. Returns (batch, candle, price, expect_refusal)."""
    roll = rng.random()
    if roll < 0.25:
        c = max(price + D(rng.randint(-150, 150)) / 100, D('1'))
        hi, lo = max(price, c) + D(rng.randint(0, 80)) / 100, min(price, c) - D(rng.randint(0, 80)) / 100
        return (), Candle(open_ms=T0 + i * H4, open=price, high=hi, low=max(lo, D('0.5')), close=c), c, False
    if roll < 0.32 and last_actions:
        legs = {a.leg for a in last_actions if a.kind not in (AK.CANCEL_ADD, AK.CANCEL_TARGET, AK.CANCEL_STOP)}
        ok = [g for g in legs if (g is Leg.CLOSE and st.closing > 0) or (g is not Leg.CLOSE and
                                                                         getattr(st, g.value) is not None)]
        if ok:
            return (Rejected(leg=rng.choice(sorted(ok))),), None, price, False
    if roll < 0.36 and st.racing:
        return (Cancelled(leg=rng.choice(st.racing).leg),), None, price, False
    if roll < 0.40 and used:
        return (rng.choice(used),), None, price, False                   # the same venue fill again: a no-op
    if roll < 0.43:                                                      # a fill on a leg that cannot fill
        bad = [g for g in (Leg.TP1, Leg.TP2, Leg.ADD, Leg.CLOSE) if g not in {x for x, _ in outstanding(st)}]
        if bad:
            leg = rng.choice(bad)
            q = p.rules.step_size
            return (ConfirmedFill(fill_id=f'x{seed_of(rng)}', leg=leg, qty=q, price=price, fee=D(0)),), None, price, True
    batch, left, pos = [], dict(outstanding(st)), st.qty
    for _ in range(rng.choice((1, 1, 2, 3))):                            # a batch only consumes what was outstanding
        legs = [(g, q) for g, q in left.items() if q > 0]
        if not legs:
            break
        leg, cap = rng.choice(legs)
        if leg is not Leg.ADD:
            cap = min(cap, pos)
        f = a_fill(rng, p, st, leg, cap, price)
        if f is None:
            break
        batch.append(f)
        left[leg] -= f.qty
        pos = pos + f.qty if leg is Leg.ADD else pos - f.qty
        if Leg.CLOSE in left:
            left[Leg.CLOSE] = min(left[Leg.CLOSE], pos)
    return tuple(batch), None, price, False


def run_live(seed, steps=80):
    rng = random.Random(seed)
    p = None
    while p is None:
        p = gen_plan(rng)
    st0 = initial_state(p, (p.costs.taker_fee * p.entry_qty * p.entry_price).quantize(D('1e-10')))
    r = step(p, st0)
    placed = check_step(p, st0, r, 0, seed, ())
    st, price, i, last, used = r.state, p.entry_price, 0, r.actions, []
    for _ in range(steps):
        if st.stage is Stage.DONE and not st.racing:
            break
        conf, candle, price, refuse = random_inputs(rng, p, st, last, price, i, used)
        if st.stage is Stage.DONE and candle is None and not any(type(e) is ConfirmedFill and e.leg is Leg.ADD
                                                                     for e in conf):
            if not conf or type(conf[0]) is not Cancelled:
                continue
        if candle is not None:
            i += 1
        before = st
        if refuse:
            with pytest.raises(DomainError):
                step(p, before, conf, candle)
            SEEN.add(('refused', conf[0].leg))
            continue
        r = step(p, before, conf, candle)
        assert step(p, before, conf, candle) == r, seed            # deterministic
        rebuilt = type(before)(**{f.name: getattr(before, f.name) for f in dataclasses.fields(before)})
        assert step(p, rebuilt, conf, candle) == r, seed           # same inputs from a persisted copy
        placed = check_step(p, before, r, placed, seed, conf)
        fills = tuple(e for e in conf if type(e) is ConfirmedFill)
        if fills:
            again = step(p, r.state, fills)                        # the same venue fills again: idempotent
            assert again.state.qty == r.state.qty and again.state.fills == r.state.fills, seed
        used.extend(fills)
        _observe(before, r, conf)
        st, last = r.state, r.actions
    return p, st


def _observe(before, r, conf):
    legs = [e.leg for e in conf if type(e) is ConfirmedFill]
    for e in conf:
        if type(e) is Rejected:
            SEEN.add(('rejected', e.leg))
        elif type(e) is Cancelled:
            SEEN.add(('cancelled', e.leg))
        elif e in before.fills:
            SEEN.add(('duplicate', e.leg))
        elif e.leg in (Leg.ADD, Leg.TP1) and getattr(before, e.leg.value) is not None and \
                e.qty < getattr(before, e.leg.value).qty:
            SEEN.add(('partial', e.leg))
        elif e.leg is Leg.ADD and before.add is None:
            SEEN.add(('racing', e.leg))
    if Leg.STOP in legs and Leg.ADD in legs:
        SEEN.add(('same_batch', 'stop+add'))
    if r.state.stage is Stage.EXITING:
        SEEN.add(('stage', 'exiting'))
    if any(a.kind is AK.REDUCE and a.reason.value == 'exit.flatten' for a in r.actions) and Leg.ADD in legs:
        SEEN.add(('action', 'add_cut'))
    SEEN.update(('action', a.kind) for a in r.actions)


SEEN = set()


@pytest.mark.parametrize('seed', COWORK_SEEDS + tuple(SEEDS))
def test_live_sequences_keep_every_invariant(seed):
    run_live(seed)


@pytest.mark.slow
def test_live_sequences_20k():
    for seed in range(20_000):
        run_live(seed)


def test_live_generator_covers_the_attack_surface():
    SEEN.clear()
    for seed in COWORK_SEEDS + tuple(SEEDS):
        run_live(seed)
    want = {('partial', Leg.ADD), ('partial', Leg.TP1), ('racing', Leg.ADD), ('rejected', Leg.STOP),
            ('rejected', Leg.ADD), ('rejected', Leg.TP1), ('rejected', Leg.CLOSE), ('cancelled', Leg.ADD),
            ('duplicate', Leg.TP1), ('refused', Leg.TP1), ('refused', Leg.CLOSE), ('same_batch', 'stop+add'),
            ('stage', 'exiting'), ('action', 'add_cut'), ('action', AK.REDUCE), ('action', AK.CLOSE),
            ('action', AK.TIME_EXIT)}
    assert want <= SEEN, sorted(map(str, want - SEEN))




# ------------------------------------------------------------------------------------------------ simulated paths
def gen_path(rng, p, n, gaps):
    out, c = [], p.entry_price.quantize(D('0.01'))
    for i in range(n):
        o = c + (D(rng.randint(-300, 300)) / 100 if gaps and rng.random() < 0.15 else 0)
        o = max(o, D(2))
        c = max(o + D(rng.randint(-250, 250)) / 100, D(2))
        h = max(o, c) + D(rng.randint(0, 150)) / 100
        l = max(min(o, c) - D(rng.randint(0, 150)) / 100, D(1))
        out.append(Candle(open_ms=T0 + i * H4, open=o, high=h, low=l, close=c))
    return out


@pytest.mark.parametrize('seed', SEEDS)
def test_simulated_paths_are_deterministic_and_bounded(seed):
    rng = random.Random(10_000 + seed)
    p = None
    while p is None:
        p = gen_plan(rng, allow_trail=True)
    gaps = rng.random() < 0.5
    candles = gen_path(rng, p, 30, gaps)
    a, b = run(p, candles), run(p, candles)
    assert a == b, seed
    st = a.state
    adds = [f for f in a.fills if f.leg is Leg.ADD]
    assert len(adds) <= 1 and sum(f.qty for f in adds) <= (p.add_qty or 0), seed
    tp1_at = next((k for k, f in enumerate(a.fills) if f.leg is Leg.TP1), None)
    if tp1_at is not None:
        assert all(f.leg is not Leg.ADD for f in a.fills[tp1_at:]), seed
    if st.stage is Stage.DONE and not gaps:
        assert realized_pnl(p, st) >= -p.risk_cap, seed            # no gap: the loss is bounded by the plan


def mirror_candle(c):
    return Candle(open_ms=c.open_ms, open=K - c.open, high=K - c.low, low=K - c.high, close=K - c.close)


@pytest.mark.parametrize('seed', range(120))
def test_zero_cost_short_is_the_exact_mirror_of_the_long(seed):
    rng = random.Random(20_000 + seed)
    p = None
    while p is None:
        p = gen_plan(rng, side=LONG, costs=ZERO_COSTS, allow_trail=False)
    q = dataclasses.replace(p, side=Side.SHORT, entry_price=K - p.entry_price, stop_price=K - p.stop_price,
                            add_price=None if p.add_price is None else K - p.add_price)
    candles = gen_path(rng, p, 25, rng.random() < 0.5)
    lo, sh = run(p, candles), run(q, [mirror_candle(c) for c in candles])
    assert [(f.leg, f.qty, K - f.price) for f in lo.fills] == [(f.leg, f.qty, f.price) for f in sh.fills], seed
    assert [(a.kind, a.leg, a.qty) for a in lo.actions] == [(a.kind, a.leg, a.qty) for a in sh.actions], seed
    if lo.state.stage is Stage.DONE:
        assert realized_pnl(p, lo.state) == realized_pnl(q, sh.state), seed


def test_generators_cover_the_mechanics():
    """The random workload actually exercises adds, TP1, BE, time exits, refusals and closes (not vacuous)."""
    seen = set()
    for seed in SEEDS:
        rng = random.Random(10_000 + seed)
        p = None
        while p is None:
            p = gen_plan(rng)
        res = run(p, gen_path(rng, p, 30, rng.random() < 0.5))
        seen |= {a.kind for a in res.actions} | {f.leg for f in res.fills}
    assert {AK.PLACE_ADD, AK.REPLACE_STOP, AK.TIME_EXIT, AK.REPLACE_TARGET, Leg.ADD, Leg.TP1, Leg.TP2, Leg.STOP,
            Leg.CLOSE} <= seen
    assert Tier.PROTECT < Tier.ADD and MARKET
