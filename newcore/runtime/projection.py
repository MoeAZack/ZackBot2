"""RT-02 journal projection: NC-02 durable journal -> REC-02 ``DurableState``.

The projection reads only the account's own journal:

* the account binding is the last journaled ``BindingChanged``; only a CONFIRMED binding with its typed confirmation
  is projected (anything else raises, which the runtime holds as ``durable_state_invalid``);
* positions are NC-02's booked net quantity per (symbol, side), from FINAL results only; a negative book raises;
* orders are the journal's live intents (not yet terminal), with the exchange order id of their last result and the
  executed quantity of a FINAL result (a KNOWN result's quantity is never trusted);
* ``portfolio_generation`` is the journal's last sequence and ``last_venue_observed_at_ms`` the latest journaled
  result time.

``label`` and ``hedge_mode`` are not journaled facts, so the operator configures them.
"""
from __future__ import annotations

from decimal import Decimal

from newcore.domain import BindingChanged, ResultObserved, Side
from newcore.domain.account import Account, BindingState
from newcore.reconciliation import DurableState, LocalOrder, LocalPosition


class JournalProjection:
    """A ``DurableStateSource`` over the account's NC-02 journal (the runtime prefers ``load_journal``)."""

    def __init__(self, *, label: str = 'newcore', hedge_mode: bool = True):
        if type(label) is not str or not 0 < len(label) <= 64 or type(hedge_mode) is not bool:
            raise ValueError('projection_config_invalid')
        self.label, self.hedge_mode = label, hedge_mode

    def load(self, account_id: str, journal_state: object) -> DurableState:
        raise ValueError('journal_required')        # a bare JournalState has no binding: never guess one

    def load_journal(self, account_id: str, journal) -> DurableState:
        if journal.account_id != account_id:
            raise ValueError('journal_account_mismatch')
        binding = state = confirmation = None
        observed = 0
        for ev in journal.read():
            if isinstance(ev, BindingChanged):
                binding, state = ev.binding, ev.to_state
                if ev.confirmation is not None:
                    confirmation = ev.confirmation
            elif isinstance(ev, ResultObserved):
                observed = max(observed, ev.result.observed_at_ms)
        if state is not BindingState.CONFIRMED or confirmation is None \
                or confirmation.new_key_digest != binding.key_digest:
            raise ValueError('binding_not_confirmed')
        account = Account(account_id=account_id, label=self.label, hedge_mode=self.hedge_mode, binding=binding,
                          binding_state=BindingState.CONFIRMED, proposed_binding=None, confirmation=confirmation)
        jstate = journal.state()
        positions = []
        for (symbol, side), net in jstate.booked:
            if net < 0:
                raise ValueError('book_negative')
            if net > 0:
                positions.append(LocalPosition(symbol, Side(side), net))
        orders = []
        for view in jstate.intents:
            it, last, final = view.intent, view.last_result, view.final_result
            orders.append(LocalOrder(it.intent_id, it.client_order_id,
                                     None if last is None else last.exchange_order_id, it.symbol, it.side,
                                     it.purpose, view.state, it.qty,
                                     final.executed_qty if final is not None and final.executed_qty is not None
                                     else Decimal('0')))
        return DurableState(account, jstate.last_sequence, observed, tuple(positions), tuple(orders))


__all__ = ['JournalProjection']
