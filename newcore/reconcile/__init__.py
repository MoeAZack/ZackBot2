"""NEWCORE REC-02: integrated exchange-truth reconciliation, as one pure account-level fold.

    model   AccountView / IntentFact / LotFact (journal side), VenueSnapshot / TradeWindow (venue side), RecPolicy and the
            unruled-question policy constants (Q1-Q4), RecDecision / Verdict (output)
    view    view_from_fold(runner fold) -> AccountView (duck-typed; never imports newcore.runner)
    fold    reconcile(view, snapshot, now_ms=, trigger=, attempt=, policy=) -> Verdict;  plan_reads(view, ...) -> ReadPlan

Pure: no IO, no network, no clock, no keys; Decimal arithmetic in explicit contexts; integer UTC ms.
Spec: docs/newcore/REC02_TNET01_BUILD_PLAN.md (prep/rec02-tnet01), rows R01-R19.
"""
from .fold import is_emergency_client_id, plan_reads, reconcile, reconciliation_id
from .model import (AUTO_CLEARABLE, Q1_QUARANTINE_PER_SIDE, Q2_ADOPT_EXTERNAL_CHANGE, Q3_PROTECT_SURPLUS,
                    Q4_AUTO_CLEAR_HOLD, AccountView, DecisionKind, IntentFact, LotFact, Outcome, ReadPlan, RecDecision,
                    RecPolicy, TradeWindow, Trigger, VenueSnapshot, Verdict)
from .view import view_from_fold

__all__ = ['AUTO_CLEARABLE', 'AccountView', 'DecisionKind', 'IntentFact', 'LotFact', 'Outcome',
           'Q1_QUARANTINE_PER_SIDE', 'Q2_ADOPT_EXTERNAL_CHANGE', 'Q3_PROTECT_SURPLUS', 'Q4_AUTO_CLEAR_HOLD',
           'ReadPlan', 'RecDecision', 'RecPolicy', 'TradeWindow', 'Trigger', 'VenueSnapshot', 'Verdict',
           'is_emergency_client_id', 'plan_reads', 'reconcile', 'reconciliation_id', 'view_from_fold']
