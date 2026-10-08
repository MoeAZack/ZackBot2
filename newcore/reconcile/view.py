"""AccountView from the runner's journal fold (duck-typed: newcore.reconcile never imports newcore.runner).

The runner's `Fold` exposes `intents` (intent_id -> IntentView with .intent / .state / .sent_at_ms / .results /
.final), `open_lots()` (LotView with .lot_id / .symbol / .side / .qty / .opened_at_ms / .entry / .protects /
.live_stop), `mode`, `hold`, `mode_reasons` and `last_sequence`. Only those attributes are read.
"""
from __future__ import annotations

from newcore.domain import ResultPhase
from newcore.ports.keys import route_of

from .model import AccountView, IntentFact, LotFact


def intent_fact(iv) -> IntentFact:
    it = iv.intent
    eoid = None
    for r in iv.results:
        if r.exchange_order_id is not None:
            eoid = r.exchange_order_id
    last = iv.results[-1] if iv.results else None
    lookup = last.lookup if last is not None and last.phase is ResultPhase.UNKNOWN else None
    fin = iv.final
    return IntentFact(intent_id=it.intent_id, purpose=it.purpose, symbol=it.symbol, side=str(it.side),
                      client_id=it.client_order_id, route=route_of(it.intent_id, it.client_order_id) or 'classic',
                      qty=it.qty, stop_price=it.stop_price, state=iv.state, owner_id=it.owner_id,
                      sent_at_ms=iv.sent_at_ms, exchange_order_id=eoid, last_lookup=lookup,
                      final_executed=None if fin is None else fin.executed_qty,
                      final_evidence=None if fin is None else fin.evidence,
                      final_at_ms=None if fin is None else fin.observed_at_ms)


def lot_fact(lot, hints) -> LotFact:
    """The lot's stop level: its carrier stop (management's replace-then-cancel keeps the OLD stop carrying until the
    new one is confirmed; `carrier` exists on the M4 fold), else its live stop, else its first protect, else the hint."""
    live = getattr(lot, 'carrier', None) or lot.live_stop
    if live is not None:
        price = live.intent.stop_price
    elif lot.protects:
        price = lot.protects[0].intent.stop_price
    else:
        price = hints.get((lot.symbol, lot.side))
    return LotFact(lot_id=lot.lot_id, symbol=lot.symbol, side=lot.side, qty=lot.qty, opened_at_ms=lot.opened_at_ms,
                   entry_intent_id=lot.entry.intent_id, stop_intent_id=None if live is None else live.intent_id,
                   stop_price=price)


def view_from_fold(fold, *, binding_confirmed=True, corroboration=None, stop_hints=None) -> AccountView:
    """corroboration: {intent_id: (PositionRead, ...)} the runner already took; stop_hints: {(symbol, side): level}."""
    hints = dict(stop_hints or {})
    intents = tuple(intent_fact(iv) for _, iv in sorted(fold.intents.items()))
    newest = None
    for iv in fold.intents.values():
        for r in iv.results:
            newest = r.observed_at_ms if newest is None else max(newest, r.observed_at_ms)
    lots = tuple(sorted((lot_fact(x, hints) for x in fold.open_lots()), key=lambda x: x.lot_id))
    return AccountView(account_id=fold.account_id, binding_confirmed=bool(binding_confirmed),
                       has_history=fold.last_sequence > 0, mode=fold.mode, hold_kind=fold.hold,
                       hold_reasons=tuple(fold.mode_reasons), lots=lots, intents=intents, newest_result_ms=newest,
                       corroboration=tuple(sorted((k, tuple(v)) for k, v in (corroboration or {}).items())),
                       stop_hints=tuple(sorted(hints.items())))
