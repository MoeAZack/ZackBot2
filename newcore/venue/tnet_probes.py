"""TNET-01 probes P1 and P2 (REC02_TNET01_BUILD_PLAN section 4), venue level, minimum size, bounded.

P1 client-id reuse (decides Q5): is a client id accepted again once its order is FINAL?
  1. open a minimum LONG with id Y (FILLED);
  2. place a far reduce-only stop with id X on the requested route (30 % below the fill: it never triggers);
  3. cancel X and confirm it FINAL;  4. resubmit X unchanged -> stop_id_after_cancel = accepted | refused:<why>;
     (accepted -> X rests again: cancel + confirm it);
  5. resubmit Y as a reduce-only CLOSE of the same quantity -> filled_id_reused = accepted | refused:<why>.
     Re-using the FILLED entry's id for a reduce-only close cannot add exposure: if accepted it closes the probe's own
     position; if refused, the position is closed under a fresh id.
P2 read lag (sets the R14 settle delay): per sample, the time from the entry FINAL answer until positionRisk shows the
  position, from the stop's KNOWN answer until the open-order list shows it, and from the close FINAL until positionRisk
  is flat. Polls every poll_ms up to max_wait_ms; a sample that never converges is recorded as None (INCONCLUSIVE).
  The algo trigger -> child visibility lag needs a triggered stop and is reported 'not_measured' here (T04 measures it).
Both raise ProbeAborted when the account could be left exposed (the harness's guarded cleanup then runs).
"""
import hashlib
from dataclasses import dataclass, field
from decimal import Decimal

from newcore.ports import keys as K
from newcore.ports import venue as P

from .smoke_trade import _floor_to, min_feasible_qty

FAR_STOP = Decimal('0.70')                 # 30 % below the fill: a probe stop that never triggers


class ProbeAborted(Exception):
    def __init__(self, message, exposure_possible=False):
        super().__init__(message)
        self.exposure_possible = exposure_possible


def probe_ref(run_id, label, symbol, route='classic'):
    intent = 'int_' + hashlib.sha256(f'zackbot.newcore.tnet.probe.v1\x00{run_id}\x00{label}'.encode('ascii')
                                     ).hexdigest()[:32]
    return P.OrderRef(symbol=symbol, client_id=K.client_id_for(intent, route), route=route)


def _classify_reuse(out):
    k = out.kind
    if k in (P.OutcomeKind.KNOWN, P.OutcomeKind.FINAL):
        return 'accepted'
    if k is P.OutcomeKind.UNKNOWN and out.detail == 'duplicate_client_id':
        return 'refused:duplicate_client_id'
    if k is P.OutcomeKind.REJECTED:
        return f'refused:{out.error_code}'
    return f'unknown:{out.detail or k.value}'


def _final(venue, ref, out):
    """FINAL as is; otherwise ONE query by client id."""
    if out.kind is P.OutcomeKind.FINAL:
        return out
    if out.kind is P.OutcomeKind.REJECTED:
        return None
    q = venue.query(ref)
    return q if q.kind is P.OutcomeKind.FINAL else None


def _cancel_confirmed(venue, ref):
    c = venue.cancel(ref)
    if c.kind is P.OutcomeKind.FINAL:
        return True
    q = venue.query(ref)
    return q.kind is P.OutcomeKind.FINAL


def _position_qty(venue, symbol, side):
    r = venue.positions(symbol)
    if r.kind is not P.ReadKind.OK:
        return None
    return sum((p.qty for p in r.value if p.side == side), Decimal(0))


def _open_long(venue, ref, qty, steps):
    sent = venue.submit_market(P.MarketOrder(ref=ref, position_side='LONG', qty=qty, reduce=False))
    steps.append(f'open {ref.client_id}: {sent.kind.value}')
    if sent.kind is P.OutcomeKind.REJECTED:
        raise ProbeAborted(f'the probe entry was refused (code {sent.error_code}); nothing executed')
    fin = _final(venue, ref, sent)
    if fin is None:
        raise ProbeAborted(f'probe entry {ref.client_id} not FINAL', exposure_possible=True)
    if fin.executed_qty == 0:
        raise ProbeAborted('the probe entry did not fill; nothing executed')
    return fin


@dataclass
class P1Result:
    route: str
    stop_id_after_cancel: str = 'skipped'
    filled_id_reused: str = 'skipped'
    steps: list = field(default_factory=list)

    @property
    def conclusive(self):
        return not any(v.startswith(('unknown', 'skipped')) for v in (self.stop_id_after_cancel, self.filled_id_reused))

    def as_dict(self):
        return {'route': self.route, 'cancelled_stop_id': self.stop_id_after_cancel,
                'filled_order_id': self.filled_id_reused, 'conclusive': self.conclusive, 'steps': list(self.steps)}


def probe_p1(venue, *, symbol, rules, price, run_id, route='algo'):
    res = P1Result(route=route)
    entry = probe_ref(run_id, 'p1-entry', symbol)
    stop = probe_ref(run_id, f'p1-stop-{route}', symbol, route)
    fallback_close = probe_ref(run_id, 'p1-close', symbol)
    qty = min_feasible_qty(rules, price)
    if qty is None:
        raise ProbeAborted('no feasible minimum quantity')
    before = _position_qty(venue, symbol, 'LONG')          # an ADOPTED foreign LONG is never the probe's to close
    if before is None:
        raise ProbeAborted('the LONG position is unreadable before the probe; nothing sent')
    fin = _open_long(venue, entry, qty, res.steps)
    filled = fin.executed_qty
    stop_price = _floor_to(fin.avg_price * FAR_STOP, rules.tick_size)
    order = P.StopOrder(ref=stop, position_side='LONG', qty=filled, stop_price=stop_price)
    placed = venue.submit_stop(order)
    res.steps.append(f'stop {stop.client_id}: {placed.kind.value}' + (f' ({placed.detail})' if placed.detail else ''))
    if placed.kind is P.OutcomeKind.KNOWN and _cancel_confirmed(venue, stop):
        res.steps.append('stop cancelled and confirmed FINAL')
        again = venue.submit_stop(order)
        res.stop_id_after_cancel = _classify_reuse(again)
        res.steps.append(f'resubmit {stop.client_id}: {res.stop_id_after_cancel}')
        if res.stop_id_after_cancel == 'accepted' and not _cancel_confirmed(venue, stop):
            res.steps.append('the re-accepted stop could not be confirmed cancelled')
    else:
        res.stop_id_after_cancel = f'skipped:stop_{placed.kind.value}'
    reuse = venue.submit_market(P.MarketOrder(ref=entry, position_side='LONG', qty=filled, reduce=True))
    res.filled_id_reused = _classify_reuse(reuse)
    if reuse.kind is P.OutcomeKind.UNKNOWN and reuse.detail != 'duplicate_client_id':
        now = _position_qty(venue, symbol, 'LONG')          # a query by Y returns the ORIGINAL fill: judge by position
        res.filled_id_reused = 'accepted' if now is not None and now <= before else f'unknown:{reuse.detail}'
    res.steps.append(f'resubmit {entry.client_id} as a reduce-only close: {res.filled_id_reused}')
    now = _position_qty(venue, symbol, 'LONG')
    if now is None:
        raise ProbeAborted('the LONG position is unreadable after the probe', exposure_possible=True)
    left = min(filled, max(now - before, Decimal(0)))       # ONLY what the probe itself still holds
    if left:
        close = venue.submit_market(P.MarketOrder(ref=fallback_close, position_side='LONG', qty=left, reduce=True))
        res.steps.append(f'close {fallback_close.client_id}: {close.kind.value}')
        if _final(venue, fallback_close, close) is None:
            raise ProbeAborted(f'probe close {fallback_close.client_id} not FINAL', exposure_possible=True)
    return res


def _percentile(values, pct):
    v = sorted(x for x in values if x is not None)
    if not v:
        return None
    k = max(0, min(len(v) - 1, -(-pct * len(v) // 100) - 1))
    return v[int(k)]


@dataclass
class P2Result:
    samples: int
    position_after_fill_ms: list = field(default_factory=list)
    order_visible_after_place_ms: list = field(default_factory=list)
    flat_after_close_ms: list = field(default_factory=list)
    steps: list = field(default_factory=list)

    @property
    def conclusive(self):
        series = (self.position_after_fill_ms, self.order_visible_after_place_ms, self.flat_after_close_ms)
        return all(s and None not in s for s in series)

    def as_dict(self):
        out = {'samples': self.samples, 'conclusive': self.conclusive, 'algo_child_visible': 'not_measured'}
        for name, s in (('position_after_fill', self.position_after_fill_ms),
                        ('order_visible_after_place', self.order_visible_after_place_ms),
                        ('flat_after_close', self.flat_after_close_ms)):
            out[f'{name}_ms'] = list(s)
            out[f'{name}_p50'] = _percentile(s, 50)
            out[f'{name}_p95'] = _percentile(s, 95)
        out['steps'] = list(self.steps)
        return out


def _poll(pred, monotonic, sleep, start, poll_ms, max_wait_ms):
    """ms from `start` until pred() is true, or None after max_wait_ms (bounded)."""
    while True:
        if pred():
            return int(round((monotonic() - start) * 1000))
        if (monotonic() - start) * 1000 >= max_wait_ms:
            return None
        sleep(poll_ms / 1000)


def probe_p2(venue, *, symbol, rules, price, run_id, route='algo', samples=3, poll_ms=200, max_wait_ms=10_000,
             monotonic, sleep):
    if type(samples) is not int or not 1 <= samples <= 10:
        raise ValueError('samples must be an int in 1..10')
    res = P2Result(samples=samples)
    qty = min_feasible_qty(rules, price)
    if qty is None:
        raise ProbeAborted('no feasible minimum quantity')
    for i in range(samples):
        entry, stop, close = (probe_ref(run_id, f'p2-{lbl}-{i}', symbol, r) for lbl, r in
                              (('entry', 'classic'), ('stop', route), ('close', 'classic')))
        before = _position_qty(venue, symbol, 'LONG') or Decimal(0)
        fin = _open_long(venue, entry, qty, res.steps)
        t = monotonic()
        res.position_after_fill_ms.append(_poll(
            lambda: (_position_qty(venue, symbol, 'LONG') or Decimal(0)) >= before + fin.executed_qty,
            monotonic, sleep, t, poll_ms, max_wait_ms))
        placed = venue.submit_stop(P.StopOrder(ref=stop, position_side='LONG', qty=fin.executed_qty,
                                               stop_price=_floor_to(fin.avg_price * FAR_STOP, rules.tick_size)))
        t = monotonic()
        if placed.kind is P.OutcomeKind.KNOWN:
            def visible():
                r = venue.open_orders(symbol)
                return r.kind is P.ReadKind.OK and any(o.ref.client_id == stop.client_id for o in r.value)
            res.order_visible_after_place_ms.append(_poll(visible, monotonic, sleep, t, poll_ms, max_wait_ms))
            if not _cancel_confirmed(venue, stop):
                res.steps.append(f'stop {stop.client_id} cancel not confirmed')
        else:
            res.order_visible_after_place_ms.append(None)
            res.steps.append(f'stop {stop.client_id}: {placed.kind.value}')
        out = venue.submit_market(P.MarketOrder(ref=close, position_side='LONG', qty=fin.executed_qty, reduce=True))
        if _final(venue, close, out) is None:
            raise ProbeAborted(f'probe close {close.client_id} not FINAL', exposure_possible=True)
        t = monotonic()
        res.flat_after_close_ms.append(_poll(
            lambda: (_position_qty(venue, symbol, 'LONG') or Decimal(0)) <= before, monotonic, sleep, t, poll_ms,
            max_wait_ms))
        res.steps.append(f'sample {i}: fill->position {res.position_after_fill_ms[-1]} ms, place->visible '
                         f'{res.order_visible_after_place_ms[-1]} ms, close->flat {res.flat_after_close_ms[-1]} ms')
    return res
