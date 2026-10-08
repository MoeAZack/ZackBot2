"""REC-02 fold properties over seeded random accounts (no hypothesis dependency: random.Random(seed), reproducible).

  P1 determinism        same inputs -> the identical Verdict, whatever the order of the input tuples
  P2 same snapshot      the same snapshot reconciled twice gives the same decisions
  P3 stale never clears a stale positions / orders read never yields CLEAR_HOLD, FLAT, PROTECTED, KNOWN_EMPTY or a
                        resolution; a stale by-id answer never resolves its intent
  P4 protection         no decision cancels or loosens protection: the only order-creating decision is PROTECT_ONLY
                        (an extra reduce-only stop at the side's owned level, never more than the position); a stop the
                        venue lists as resting is never resolved as gone
  P5 never empty        KNOWN_EMPTY only with fresh OK reads showing no position and no order
"""
import random
from decimal import Decimal as D

import pytest

from newcore.domain import EntriesMode, HoldKind, IntentState, Lookup, Ownership, ReasonCode
from newcore.reconcile import DecisionKind as K
from newcore.reconcile import Outcome, RecPolicy, TradeWindow, reconcile
from rec_helpers import (SYM, T, cid, fact, fill, final, known, lot, not_found, ok, order, pos, snap, unknown, view)

SEEDS = range(400)
OTHER = 'BTCUSDT'
FRESH = RecPolicy().freshness_ms
CLEARABLE = (ReasonCode.EXEC_ENTRY_UNCONFIRMED, ReasonCode.CONNECTIVITY_EXCHANGE_OUTAGE, ReasonCode.PROTECT_CHECKING)
OWNER_ONLY = (ReasonCode.OWNERSHIP_UNTRACKED_POSITION, ReasonCode.EXEC_STOP_FAILED)


def scenario(seed, *, force_stale=False):
    rnd = random.Random(seed)
    lots, intents, positions, orders, queries, fills, trades, hints = [], [], [], [], [], [], [], []
    n = 0
    used = set()
    for _ in range(rnd.randint(0, 3)):
        sym, side = rnd.choice([(SYM, 'LONG'), (SYM, 'SHORT'), (OTHER, 'LONG'), (OTHER, 'SHORT')])
        if (sym, side) in used:
            continue
        used.add((sym, side))
        n += 2
        q = D(rnd.choice(['1', '2', '3']))
        lvl = '90' if side == 'LONG' else '110'
        st = fact(n + 1, 'protect', side=side, symbol=sym, qty=str(q), stop=lvl, owner='lot_' + f'{n:032x}',
                  state=rnd.choice([IntentState.WORKING] * 4 + [IntentState.SUBMITTED, IntentState.UNKNOWN]),
                  eoid=str(7000 + n))
        intents += [fact(n, 'entry', side=side, symbol=sym, qty=str(q), state=IntentState.FILLED, executed=str(q),
                         eoid=str(6000 + n)), st]
        lots.append(lot(n, qty=str(q), side=side, symbol=sym, entry=n,
                        stop_intent=st.intent_id if st.state is IntentState.WORKING else None, stop=lvl))
        pq = q + rnd.choice([D(0)] * 5 + [D(-1), D(1), -q])
        if pq > 0:
            positions.append(pos(str(pq), side=side, symbol=sym))
        if rnd.random() < 0.75:
            orders.append(order(st.client_id, side=side, qty=str(q) if rnd.random() < 0.9 else '0.5',
                                stop=lvl if rnd.random() < 0.9 else '95', symbol=sym, eoid=str(7000 + n)))
        r = rnd.random()
        if r < 0.25:
            queries.append((st.client_id, final(n + 1, '0', eoid=str(7000 + n), symbol=sym,
                                                at=T - (FRESH * 2 if rnd.random() < 0.2 else 0))))
        elif r < 0.45:
            queries.append((st.client_id, final(n + 1, str(q), avg=lvl, eoid=str(7000 + n), symbol=sym)))
            fills.append((str(7000 + n), ok([fill(str(7000 + n), str(q) if rnd.random() < 0.8 else '0.1', lvl,
                                                  trade=str(n), side=side, symbol=sym)])))
        elif r < 0.6:
            queries.append((st.client_id, known(n + 1)))
        if rnd.random() < 0.3:
            trades.append(((sym, side), TradeWindow(from_ms=T - 1, read=ok(
                [fill('9' + str(n), '1', '100', trade='x' + str(n), side=side, symbol=sym)]))))
    if rnd.random() < 0.3:                                     # a lost entry
        e = fact(90, 'entry', state=IntentState.UNKNOWN, lookup=Lookup.NOT_FOUND,
                 sent=T - rnd.choice([1000, 60_000]))
        intents.append(e)
        a = rnd.random()
        if a < 0.3:
            queries.append((e.client_id, not_found(90)))
        elif a < 0.6:
            queries.append((e.client_id, final(90, '1', eoid='6090')))
            fills.append(('6090', ok([fill('6090', '1')])))
    if rnd.random() < 0.2:
        orders.append(order('web_manual_' + str(seed), side='LONG', reduce=False, type_='LIMIT', stop=None))
    if rnd.random() < 0.2:
        positions.append(pos('5', side='SHORT', symbol='ETHUSDT'))
    if rnd.random() < 0.3:
        hints.append(((SYM, 'LONG'), D('91')))
    hold = rnd.random() < 0.4
    reasons = tuple(rnd.sample(CLEARABLE + OWNER_ONLY, rnd.randint(1, 2))) if hold else ()
    vw = view(lots=lots, intents=intents, mode=EntriesMode.HOLD if hold else EntriesMode.ACTIVE,
              hold_kind=HoldKind.NORMAL if hold else None, reasons=reasons, history=rnd.random() < 0.9,
              newest=T - 5000, hints=hints, binding=rnd.random() < 0.9)
    stale_at = T - FRESH - 1
    pos_at = stale_at if force_stale or rnd.random() < 0.1 else T
    ord_at = stale_at if rnd.random() < 0.1 else T
    pr = unknown(T) if rnd.random() < 0.05 else ok(positions, pos_at)
    s = snap(pos_read=pr, ord_read=ok(orders, ord_at), queries=queries, fills=fills, trades=trades)
    return vw, s


def shuffled(vw, s, seed):
    rnd = random.Random(seed ^ 0x5EED)

    def sh(t):
        t = list(t)
        rnd.shuffle(t)
        return tuple(t)

    from dataclasses import replace
    from newcore.ports.venue import ReadOutcome
    vw2 = replace(vw, lots=sh(vw.lots), intents=sh(vw.intents))

    def rs(r):
        return r if r.value is None else ReadOutcome(kind=r.kind, observed_at_ms=r.observed_at_ms, value=sh(r.value))
    s2 = replace(s, positions=rs(s.positions), orders=rs(s.orders), queries=sh(s.queries), fills=sh(s.fills),
                 trades=sh(s.trades))
    return vw2, s2


@pytest.mark.parametrize('seed', SEEDS)
def test_P1_determinism_and_input_order_independence(seed):
    vw, s = scenario(seed)
    a = reconcile(vw, s, now_ms=T)
    assert a == reconcile(vw, s, now_ms=T)
    assert a == reconcile(*shuffled(vw, s, seed), now_ms=T)
    assert a.outcome in Outcome


@pytest.mark.parametrize('seed', SEEDS)
def test_P2_the_same_snapshot_twice_gives_the_same_decisions(seed):
    vw, s = scenario(seed)
    first = reconcile(vw, s, now_ms=T, attempt=1)
    second = reconcile(vw, s, now_ms=T, attempt=1)
    assert first.decisions == second.decisions and first.needs == second.needs
    assert first.reconciliation_id == second.reconciliation_id


@pytest.mark.parametrize('seed', SEEDS)
def test_P3_a_stale_read_never_clears_a_hold_or_resolves_anything(seed):
    vw, s = scenario(seed, force_stale=True)
    for attempt in (0, 1, 5):
        v = reconcile(vw, s, now_ms=T, attempt=attempt)
        assert not v.of(K.CLEAR_HOLD)
        assert v.outcome in (Outcome.PENDING, Outcome.HOLD)
        assert v.ownership is not Ownership.KNOWN_EMPTY
        assert not (v.of(K.RESOLVE_FILLED) + v.of(K.RESOLVE_NOT_EXECUTED) + v.of(K.ADOPT) + v.of(K.PROTECT_ONLY))
    # a stale by-id answer (fresh positions / orders) never resolves its own intent
    vw, s = scenario(seed)
    stale_ids = {c for c, q in s.queries if T - q.observed_at_ms > FRESH}
    v = reconcile(vw, s, now_ms=T)
    assert not [d for d in v.of(K.RESOLVE_FILLED) + v.of(K.RESOLVE_NOT_EXECUTED) if d.client_id in stale_ids]


@pytest.mark.parametrize('seed', SEEDS)
def test_P4_no_decision_cancels_or_loosens_protection(seed):
    vw, s = scenario(seed)
    v = reconcile(vw, s, now_ms=T)
    assert {d.kind for d in v.decisions} <= set(K)
    venue_qty = {}
    if s.positions.value is not None:
        for p in s.positions.value:
            venue_qty[(p.symbol, p.side)] = venue_qty.get((p.symbol, p.side), D(0)) + p.qty
    levels = {}
    for x in vw.lots:
        levels.setdefault((x.symbol, x.side), set()).add(x.stop_price)
    for k, h in vw.stop_hints:
        levels.setdefault(k, set()).add(h)
    added = {}
    for d in v.of(K.PROTECT_ONLY):
        k = (d.symbol, d.side)
        assert d.qty > 0 and d.price in levels[k]
        added[k] = added.get(k, D(0)) + d.qty
    for k, q in added.items():
        assert q <= venue_qty.get(k, D(0))
    resting = {o.ref.client_id for o in (s.orders.value or ()) if o.reduce}
    by_cid = {f.client_id: f for f in vw.intents}
    for d in v.of(K.RESOLVE_NOT_EXECUTED):
        f = by_cid[d.client_id]
        assert not (f.purpose.value == "protect" and f.client_id in resting)
    for d in v.decisions:
        if d.kind in (K.HOLD, K.QUARANTINE):
            assert d.owner_actions


@pytest.mark.parametrize('seed', SEEDS)
def test_P5_never_guesses_an_empty_account(seed):
    vw, s = scenario(seed)
    v = reconcile(vw, s, now_ms=T)
    if v.ownership is Ownership.KNOWN_EMPTY:
        assert s.positions.kind.value == 'ok' and s.orders.kind.value == 'ok'
        assert all(p.qty == 0 for p in s.positions.value) and not s.orders.value
        assert T - s.positions.observed_at_ms <= FRESH and T - s.orders.observed_at_ms <= FRESH
    if v.outcome is Outcome.FLAT:
        assert v.ownership is Ownership.KNOWN_EMPTY


def test_scenarios_cover_every_outcome_and_decision_kind():
    seen_out, seen_kind = set(), set()
    for seed in SEEDS:
        v = reconcile(*scenario(seed), now_ms=T)
        seen_out.add(v.outcome)
        seen_kind |= {d.kind for d in v.decisions}
    assert seen_out == set(Outcome)
    assert seen_kind >= {K.PROTECT_ONLY, K.RESOLVE_FILLED, K.RESOLVE_NOT_EXECUTED, K.QUARANTINE, K.HOLD, K.REREAD,
                         K.CLEAR_HOLD}
