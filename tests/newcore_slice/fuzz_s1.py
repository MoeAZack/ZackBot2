"""A seeded systematic fuzz equivalent to Cowork's 20k h_fuzz (PR #37: '907 unclean = 618 entries=0 + 289
unprotected-in-HOLD with no stop'; NEW-3: an orphan reduce-only stop on a flat account). Cowork's scripts are not in
the repo; this reproduces the described space over the slice World and checks the same invariants.

One scenario per seed (deterministic): side, entry candle, a crash around the entry / its stop (or none), the lazy
store losing its last 0..4 durable events, a restart in the same or a later candle, the strategy able or not to
re-derive its signal; then random later faults: a lost stop answer, an external cancel of the lot's stop, the store
going down (hard HOLD) and the position flattened by hand while it is down.

Verdicts per seed (classify()):
  ok_covered    exposure covered by our reduce-only stops (cover >= position)
  ok_flat       flat and clean (no emergency stop left; outside hard HOLD none of ours)
  surfaced      a position nothing can prove ours (every record lost AND no re-derivable signal): a durable HOLD with
                an owner item - the contract, never ACTIVE over it
  FAIL_naked    exposure in HOLD / hard HOLD with no stop and nothing surfaced: Cowork's 289
  FAIL_orphan   an emergency stop resting on a flat side (NEW-3), or any of our stops on a flat side outside hard HOLD
  FAIL_entry    a risk-adding order sent during hard HOLD
  FAIL_raise    anything but the injected Crash escaped a cycle
"""
import random
import traceback
from decimal import Decimal as D

from newcore.adapters import MemoryJournal
from newcore.domain import EntriesMode
from newcore.runner import InjectedSignals, NoSignals, ids
from slice_helpers import ACCOUNT_ID, H4, PORTFOLIO_ID, Crash, World, flat_bars

SYM = 'SOLUSDT'
T0 = flat_bars(1)[0].open_ms
LAST_BAR = 16


def _sig(side, bar):
    return InjectedSignals({(SYM, T0 + (bar + 1) * H4): (('enter', side),)}, stop_atr=D('2'))


def _position(w, side):
    return sum((p.qty for p in w.venue.positions().value if p.side == side), D(0))


def _stops(w, side):
    return [o for o in w.venue.open_orders().value if o.reduce and o.position_side == side]


def plan(seed):
    rng = random.Random(seed)
    p = {'seed': seed, 'side': rng.choice(('LONG', 'SHORT')), 'bar': rng.randint(3, 7),
         'crash': rng.choice((None, (1, 'after'), (2, 'after'), (1, 'before'), (2, 'before'))),
         'lost': rng.choice((0, 1, 1, 2, 2, 3, 4)), 'delay': rng.choice((0, 0, 1, 2, 3)),
         'provable': rng.random() < 0.85}
    later = p['bar'] + p['delay'] + 1
    p['lose_stop'] = rng.randint(later, LAST_BAR - 2) if rng.random() < 0.3 else None
    p['ext_cancel'] = rng.randint(later, LAST_BAR - 2) if rng.random() < 0.3 else None
    p['hard_hold'] = rng.randint(later, LAST_BAR - 3) if rng.random() < 0.4 else None
    p['flatten'] = (p['hard_hold'] + rng.randint(1, 2)) if p['hard_hold'] is not None and rng.random() < 0.5 else None
    return p


def run_seed(seed):
    """-> (verdict, plan, detail)."""
    p = plan(seed)
    side = p['side']
    w = World(flat_bars(LAST_BAR + 4), _sig(side, p['bar']), strict=False)
    sent_in_hold = []
    try:
        w.run(p['bar'] - 1)
        t = w.close_ms(p['bar'])
        if p['crash'] is not None:
            w.port.crash(*p['crash'])
            w.venue.advance_to(t)
            try:
                w.runner.cycle(t)
            except Crash:
                pass
            w.port.disarm()
            evs = w.journal.read()
            keep = evs[:max(0, len(evs) - p['lost'])]
            w.journal = MemoryJournal(ACCOUNT_ID, PORTFOLIO_ID)
            for e in keep:
                w.journal.append(e)
            if not p['provable']:
                w.signals = NoSignals(tf_label='4h')
            w.runner = w.new_runner()
            if p['delay'] == 0:
                w.runner.cycle(t)
            start = p['bar'] + max(p['delay'], 1)
        else:
            w.venue.advance_to(t)
            w.runner.cycle(t)
            start = p['bar'] + 1
        held_from = None
        for b in range(start, LAST_BAR + 1):
            if b == p['lose_stop']:
                w.port.lose('stop')
            if b == p['ext_cancel']:
                for o in _stops(w, side):
                    if not ids.is_emergency_client_id(o.ref.client_id):
                        w.venue.external_cancel(o.ref.client_id)
            if b == p['hard_hold']:
                w.journal.fail_writes(10 ** 9)
                w.runner.store_unavailable('fuzz: ENOSPC')
                held_from = len(w.venue.orders_submitted())
            if b == p['flatten']:
                w.venue._positions.pop((SYM, side), None)
            t = w.close_ms(b)
            w.venue.advance_to(t)
            w.runner.cycle(t)
        if held_from is not None:
            sent_in_hold = [o for o in w.venue.orders_submitted()[held_from:] if not o.reduce]
    except Exception:
        return 'FAIL_raise', p, traceback.format_exc(limit=6)
    return classify(w, p, sent_in_hold)


def classify(w, p, sent_in_hold):
    r, side = w.runner, p['side']
    if sent_in_hold:
        return 'FAIL_entry', p, repr(sent_in_hold)
    pos, stops = _position(w, side), _stops(w, side)
    cover = sum((o.qty for o in stops), D(0))
    if pos > 0:
        if cover >= pos:
            return 'ok_covered', p, ''
        items = list(r.last_rec.items) if r.last_rec is not None else []
        if r.fold.mode is EntriesMode.HOLD and not r.fold.open_lots() and items:
            return 'surfaced', p, f'items={items}'
        return 'FAIL_naked', p, (f'pos={pos} cover={cover} mode={r.fold.mode} hard={r.hard_hold} '
                                 f'lots={[(x.lot_id, x.qty) for x in r.fold.open_lots()]} '
                                 f'incidents={r.incidents[-3:]}')
    em = [o for o in stops if ids.is_emergency_client_id(o.ref.client_id)]
    if em or (stops and r.hard_hold is None):
        return 'FAIL_orphan', p, f'flat with stops {[(o.ref.client_id, o.qty) for o in stops]} hard={r.hard_hold}'
    return 'ok_flat', p, ''
