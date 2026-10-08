"""NC-07 seeded stdlib properties (no Hypothesis): random plans, random live fill / refusal sequences and random candle
paths through the simulator. Every failure message names the seed, so `run_live(seed)` reproduces it exactly."""
import dataclasses
import random
from decimal import Decimal as D

import pytest

from mg_factories import GOLDEN_COSTS, H4, LONG, SHORT, T0, ZERO_COSTS, K, rules
from newcore.domain import Side
from newcore.domain.instrument import Rounding
from newcore.management import (ActionKind as AK, Candle, ConfirmedFill, Leg, PlanRefused, Rejected, Stage, Tier,
                                build_plan, initial_state, planned_risk, realized_pnl, run, step)
from newcore.management.actions import MARKET, sort_key

SEEDS = range(250)
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
def check_step(p, before, r, placed_adds, seed):
    st = r.state
    assert list(r.actions) == sorted(r.actions, key=sort_key), seed
    tiers = [a.tier for a in r.actions]
    assert tiers == sorted(tiers), seed
    if st.stage is Stage.ACTIVE:
        sq = st.stop.qty if st.stop else D(0)
        assert st.qty - st.closing <= sq <= st.qty, seed            # the stop covers the position, never more
    rq = p.rules
    for a in r.actions:
        if a.qty is None:
            continue
        assert rq.on_step(a.qty), seed                             # every quantity is on the step
        if a.kind is AK.PLACE_ADD:
            assert a.qty <= p.add_qty, seed
            assert not before.tp1_confirmed and not st.tp1_confirmed, seed
        else:
            assert a.qty <= st.qty, seed                           # never more than the position
        if a.leg is Leg.TP1 and a.kind is not AK.CANCEL_TARGET and not st.tp1_confirmed:
            assert a.qty <= p.tp1_frac * (st.qty - st.closing), seed   # floor, never round up
    placed_adds += sum(a.kind is AK.PLACE_ADD for a in r.actions)
    assert placed_adds <= 1, seed                                  # the add cap is one, in the core
    return placed_adds


def random_inputs(rng, p, st, last_actions, price, i):
    """One plausible live input batch for `st` (fills on the step and within their orders), or a closed candle."""
    roll = rng.random()
    if roll < 0.3:
        c = price + D(rng.randint(-150, 150)) / 100
        c = max(c, D('1'))
        hi, lo = max(price, c) + D(rng.randint(0, 80)) / 100, min(price, c) - D(rng.randint(0, 80)) / 100
        return (), Candle(open_ms=T0 + i * H4, open=price, high=hi, low=max(lo, D('0.5')), close=c), c
    if roll < 0.4 and last_actions:
        legs = {a.leg for a in last_actions if a.kind not in (AK.CANCEL_ADD, AK.CANCEL_TARGET, AK.CANCEL_STOP)}
        ok = [g for g in legs if (g is Leg.CLOSE and st.closing > 0) or (g is not Leg.CLOSE and
                                                                         getattr(st, g.value) is not None)]
        if ok:
            return (Rejected(leg=rng.choice(sorted(ok))),), None, price
    orders = [(g, getattr(st, g.value)) for g in (Leg.STOP, Leg.ADD, Leg.TP1, Leg.TP2) if getattr(st, g.value)]
    if st.closing > 0:
        orders.append((Leg.CLOSE, None))
    late = p.has_add and st.add is None and st.add_filled < p.add_qty and st.add_phase.value == 'closed'
    if late and rng.random() < 0.1:
        q = p.add_qty - st.add_filled
        return (ConfirmedFill(leg=Leg.ADD, qty=q, price=p.add_price, fee=D(0)),), None, price
    if not orders:
        return (), None, price
    leg, o = rng.choice(orders)
    cap = o.qty if o is not None else st.closing
    if leg is not Leg.ADD:
        cap = min(cap, st.qty)
    n = int(cap / p.rules.step_size)
    if n == 0:
        return (), None, price
    q = p.rules.step_size * rng.randint(1, n)
    fp = o.price if o is not None else price
    return (ConfirmedFill(leg=leg, qty=q, price=fp, fee=D(0)),), None, price


def run_live(seed):
    rng = random.Random(seed)
    p = None
    while p is None:
        p = gen_plan(rng)
    st0 = initial_state(p, D(0))
    r = step(p, st0)
    placed = check_step(p, st0, r, 0, seed)
    st, price, i, last = r.state, p.entry_price, 0, r.actions
    for _ in range(80):
        if st.stage is Stage.DONE:
            break
        conf, candle, price = random_inputs(rng, p, st, last, price, i)
        if candle is not None:
            i += 1
        before = st
        r = step(p, before, conf, candle)
        assert step(p, before, conf, candle) == r, seed            # deterministic
        rebuilt = type(before)(**{f.name: getattr(before, f.name) for f in dataclasses.fields(before)})
        assert step(p, rebuilt, conf, candle) == r, seed           # same inputs from a persisted copy
        placed = check_step(p, before, r, placed, seed)
        if before.tp1_confirmed:
            assert r.state.stop is None or not any(a.kind is AK.PLACE_ADD for a in r.actions), seed
        for ev in conf:
            if type(ev) is Rejected:
                SEEN.add(('rejected', ev.leg))
            elif ev.leg in (Leg.ADD, Leg.TP1) and getattr(before, ev.leg.value) is not None and \
                    ev.qty < getattr(before, ev.leg.value).qty:
                SEEN.add(('partial', ev.leg))
            elif ev.leg is Leg.ADD and before.add is None:
                SEEN.add(('late', ev.leg))
        SEEN.update(('action', a.kind) for a in r.actions)
        st, last = r.state, r.actions
    return p, st


SEEN = set()


@pytest.mark.parametrize('seed', SEEDS)
def test_live_sequences_keep_every_invariant(seed):
    run_live(seed)


def test_live_generator_covers_partials_refusals_and_late_adds():
    SEEN.clear()
    for seed in SEEDS:
        run_live(seed)
    assert {('partial', Leg.ADD), ('partial', Leg.TP1), ('late', Leg.ADD), ('rejected', Leg.STOP),
            ('rejected', Leg.ADD), ('rejected', Leg.TP1), ('rejected', Leg.CLOSE), ('action', AK.REDUCE),
            ('action', AK.CLOSE), ('action', AK.TIME_EXIT)} <= SEEN, sorted(map(str, SEEN))


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
