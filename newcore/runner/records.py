"""NC-01 record builders for the S1 Runner: planned intents and venue outcome -> OrderResult mapping.

The mapping is the one STEP0_INTERFACE.md section 4 fixes:
  FINAL                -> FINAL, evidence exchange_final, the venue status, executed qty and average price
  REJECTED on a submit -> FINAL, evidence exchange_refused, nothing executed
  UNKNOWN              -> UNKNOWN (no lookup)
  NOT_FOUND            -> UNKNOWN + lookup not_found: books NOTHING (never "never filled")
  KNOWN                -> KNOWN with the open exchange status
  ACKNOWLEDGED / REJECTED on a query or cancel -> no result (the order is unchanged; confirm with query)
"""
from __future__ import annotations

from decimal import Decimal

from newcore.domain import (Evidence, ExchangeStatus, IntentState, Lookup, OrderIntent, OrderResult, OrderType, OwnerKind,
                            Purpose,
                            ResultPhase, Side)
from newcore.ports.venue import OutcomeKind

from .ids import client_id_for


def planned_intent(*, intent_id, account_id, decision_id, purpose, symbol, side, qty, reason, at_ms, owner_id=None,
                   stop_price=None, slot_id=None, owner_kind=None, route='classic'):
    purpose = Purpose(purpose)
    protect = purpose is Purpose.PROTECT
    return OrderIntent(intent_id=intent_id, account_id=account_id, decision_id=decision_id,
                       client_order_id=client_id_for(intent_id, route), purpose=purpose,
                       order_type=OrderType.STOP_MARKET if protect else OrderType.MARKET, state=IntentState.PLANNED,
                       symbol=symbol, side=Side(side), qty=qty, reason=reason, created_at_ms=at_ms, owner_id=owner_id,
                       owner_kind=None if owner_id is None else (owner_kind or OwnerKind.LOT),
                       slot_id=slot_id, price=None, stop_price=stop_price if protect else None, arm=None,
                       alt_client_order_id=None, seen_qty=None, authorized_by=None, replaces_intent_id=None)


def _result(intent, rid, at_ms, **kw):
    base = dict(result_id=rid, intent_id=intent.intent_id, account_id=intent.account_id,
                client_order_id=intent.client_order_id, requested_qty=intent.qty, observed_at_ms=at_ms,
                exchange_order_id=None, exchange_status=None, lookup=None, executed_qty=None, avg_price=None,
                evidence=None, corroboration=(), resolved_by=None)
    base.update(kw)
    return OrderResult(**base)


def result_from(outcome, intent, rid, *, submit) -> OrderResult | None:
    """The OrderResult an outcome proves, or None when it proves nothing new about the order."""
    k, at = outcome.kind, max(outcome.observed_at_ms, intent.created_at_ms)
    if k is OutcomeKind.FINAL:
        return _result(intent, rid, at, phase=ResultPhase.FINAL, evidence=Evidence.EXCHANGE_FINAL,
                       exchange_order_id=outcome.exchange_order_id, exchange_status=ExchangeStatus(outcome.status),
                       executed_qty=outcome.executed_qty, avg_price=outcome.avg_price)
    if k is OutcomeKind.REJECTED:
        if not submit:
            return None
        return _result(intent, rid, at, phase=ResultPhase.FINAL, evidence=Evidence.EXCHANGE_REFUSED,
                       executed_qty=Decimal(0))
    if k is OutcomeKind.UNKNOWN:
        return _result(intent, rid, at, phase=ResultPhase.UNKNOWN)
    if k is OutcomeKind.NOT_FOUND:
        return _result(intent, rid, at, phase=ResultPhase.UNKNOWN, lookup=Lookup.NOT_FOUND)
    if k is OutcomeKind.KNOWN:
        return _result(intent, rid, at, phase=ResultPhase.KNOWN, exchange_order_id=outcome.exchange_order_id,
                       exchange_status=ExchangeStatus(outcome.status))
    return None                                                  # ACKNOWLEDGED


def not_sent_result(intent, rid, at_ms) -> OrderResult:
    return _result(intent, rid, max(at_ms, intent.created_at_ms), phase=ResultPhase.FINAL, evidence=Evidence.NOT_SENT,
                   executed_qty=Decimal(0))
