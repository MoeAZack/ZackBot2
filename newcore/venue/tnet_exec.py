"""Venue-level executor for zb-newcore-tnet-scenario/1 specs (tnet_spec.py): drives TestnetVenue step by step, injects the
spec's faults at the HTTP seam (tnet_seams.FaultHttp), and checks the expected outcome kinds and the end state.
(The RUNNER-driven T01-T12 scenarios live on nc-tnet01; this is the port-level counterpart used by
tools/newcore_tnet.py --scenario.)

Step semantics:
  market  MARKET order on (symbol, side); reduce=true closes; qty 'min' | 'filled:<step>' | decimal string
  stop    reduce-only STOP_MARKET on (symbol, side) for qty; trigger = <from step> fill avg x (1 + offset %), floored
          to the tick; route classic | algo
  query   query the order a market / stop step placed (by its client id)
  cancel  cancel it
  wait    sleep N seconds (bounded by the spec, 600 s max)
Client ids: one per step, unique per run (probe_ref(run_id, 'spec:<name>:<step>')).
"""
from dataclasses import dataclass
from decimal import Decimal

from newcore.ports import venue as P

from .smoke_trade import _floor_to, min_feasible_qty
from .tnet import ScenarioOutcome, is_newcore_cid
from .tnet_probes import probe_ref


@dataclass
class StepRecord:
    id: str
    op: str
    outcome: object = None           # port outcome kind value, or None for wait
    detail: object = None
    executed: object = None
    avg: object = None
    ref: object = None


def run_spec(spec, venue, *, rules_by_symbol, price_by_symbol, run_id, seam=None, sleep=None):
    """-> (ScenarioOutcome, [StepRecord]). Never raises for a venue answer; a bad spec raises before anything is sent."""
    records, by_id, assertions = [], {}, []
    faults = {}
    for f in spec.get('faults', []):
        faults[f['at']] = (f['kind'], f.get('times', 1))
    expect = spec['expect']
    for st in spec['steps']:
        sid, op = st['id'], st['op']
        rec = StepRecord(sid, op)
        if op == 'wait':
            (sleep or (lambda s: None))(st['seconds'])
            records.append(rec)
            by_id[sid] = rec
            continue
        if sid in faults and seam is not None:
            seam.arm(*faults[sid])
        try:
            out = _do(st, venue, by_id, rules_by_symbol, price_by_symbol, run_id, spec['name'])
        finally:
            if seam is not None:
                seam.disarm()
        rec.outcome, rec.detail = out.kind.value, out.detail
        rec.executed, rec.avg = out.executed_qty, out.avg_price
        rec.ref = out.ref
        records.append(rec)
        by_id[sid] = rec
        if op == 'query' and out.kind is P.OutcomeKind.FINAL:      # a resolving query fills in a lost answer
            target = by_id[st['ref']]
            if target.op == 'market' and target.executed is None:
                target.executed, target.avg = out.executed_qty, out.avg_price
        allowed = expect.get('outcomes', {}).get(sid)
        if allowed is not None:
            assertions.append((f'{sid}: outcome {rec.outcome} in {allowed}', rec.outcome in allowed))
    end = expect['end_state']
    flat, orders_left = _end_state(venue, spec['symbols'])
    assertions.append((f'end state flat == {end["flat"]} (observed {flat})', flat == end['flat']))
    assertions.append((f'open NEWCORE orders == {end["open_newcore_orders"]} (observed {orders_left})',
                       orders_left == end['open_newcore_orders']))
    passed = all(ok for _, ok in assertions)
    return ScenarioOutcome(spec['name'], passed, tuple(assertions)), records


def _qty(st, by_id, rules, price):
    q = st['qty']
    if q == 'min':
        return min_feasible_qty(rules, price)
    if q.startswith('filled:'):
        ref = by_id[q[len('filled:'):]]
        return ref.executed if ref.executed else None
    return Decimal(q)


def _do(st, venue, by_id, rules_by_symbol, price_by_symbol, run_id, name):
    op = st['op']
    if op in ('query', 'cancel'):
        ref = by_id[st['ref']].ref
        return venue.query(ref) if op == 'query' else venue.cancel(ref)
    sym = st['symbol']
    rules, price = rules_by_symbol[sym], price_by_symbol[sym]
    qty = _qty(st, by_id, rules, price)
    route = st.get('route', 'classic')
    ref = probe_ref(run_id, f'spec:{name}:{st["id"]}', sym, route)
    if qty is None or qty <= 0:
        return P.OrderOutcome(kind=P.OutcomeKind.REJECTED, ref=ref, observed_at_ms=1_000_000_000_000,
                              error_code=-1, detail='no_quantity')
    if op == 'market':
        return venue.submit_market(P.MarketOrder(ref=ref, position_side=st['side'], qty=qty, reduce=st['reduce']))
    base = by_id[st['from']].avg or price
    trigger = _floor_to(base * (1 + Decimal(st['trigger_offset_pct']) / 100), rules.tick_size)
    return venue.submit_stop(P.StopOrder(ref=ref, position_side=st['side'], qty=qty, stop_price=trigger))


def _end_state(venue, symbols):
    flat, orders = True, 0
    for sym in symbols:
        pos = venue.positions(sym)
        if pos.kind is not P.ReadKind.OK or any(p.qty != 0 for p in pos.value):
            flat = False
        oo = venue.open_orders(sym)
        if oo.kind is not P.ReadKind.OK:
            orders = -1
        elif orders >= 0:
            orders += sum(1 for o in oo.value if is_newcore_cid(o.ref.client_id))
    return flat, orders
