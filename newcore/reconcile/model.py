"""REC-02 records: the account view the fold reads, the venue snapshot, the policy, and the typed verdict.

Everything here is a frozen value. No IO, no clock, no randomness: the caller injects `now_ms` and every read carries
its own `observed_at_ms` (integer UTC ms). Quantities are Decimal; arithmetic uses the explicit contexts below.

Policy constants for questions Codex has NOT ruled yet (docs/newcore/REC02_TNET01_BUILD_PLAN.md section 9). Each is the
plan's recommendation, named so a ruling flips one constant and its tests:

    Q1_QUARANTINE_PER_SIDE    a foreign order / position quarantines its (symbol, side) only, not the whole account
    Q2_ADOPT_EXTERNAL_CHANGE  an external reduction proven by venue fills is ADOPTED as venue truth, and a FINAL record of
                              an owned client id supersedes an earlier `not_found_corroborated` decision. Journaling either
                              needs an NC-01 amendment (an `exchange_external` evidence / re-open rule): until then the
                              runner must treat these decisions as owner-confirmed actions.
    Q3_PROTECT_SURPLUS        HOLD may protect an unowned surplus reduce-only at the side's owned stop level (protection is
                              not adoption under A22); the surplus itself stays quarantined
    Q4_AUTO_CLEAR_HOLD        a NORMAL HOLD whose reasons are all in AUTO_CLEARABLE clears itself on a fresh, full match
                              with a confirmed binding (A08); every other HOLD needs the owner
"""
from __future__ import annotations

import enum
from dataclasses import dataclass, field
from decimal import Context, Decimal, Inexact, InvalidOperation, Overflow

from newcore.domain import EntriesMode, HoldKind, IntentState, Lookup, Ownership, Purpose, ReasonCode
from newcore.domain.orders import NOT_FOUND_WINDOW_MS, Evidence, PositionRead
from newcore.ports.venue import OrderOutcome, ReadOutcome

ZERO = Decimal(0)
# Exact arithmetic for sums / differences of venue quantities: an inexact step is a bug, never a rounding.
QCTX = Context(prec=60, traps=[InvalidOperation, Overflow, Inexact])
# The one inexact step: a volume-weighted average price.
PCTX = Context(prec=34, traps=[InvalidOperation, Overflow])

# ------------------------------------------------------------------------------------------------ unruled policy (flag)
Q1_QUARANTINE_PER_SIDE = True
Q2_ADOPT_EXTERNAL_CHANGE = True
Q3_PROTECT_SURPLUS = True
Q4_AUTO_CLEAR_HOLD = True
AUTO_CLEARABLE = frozenset({
    ReasonCode.RECONCILE_UNRECONCILED,          # the runner's umbrella reason; never alone decisive
    ReasonCode.CONNECTIVITY_EXCHANGE_OUTAGE,    # unreadable reads
    ReasonCode.EXEC_ENTRY_UNCONFIRMED,          # an ambiguous entry answer, since resolved
    ReasonCode.EXEC_ORDER_FAILED,               # an ambiguous close answer, since resolved
    ReasonCode.PROTECT_CHECKING,                # protection state was unknown, since confirmed
})


class Trigger(enum.StrEnum):
    STARTUP = 'startup'
    CYCLE = 'cycle'
    TICK = 'tick'
    TIMEOUT = 'timeout'
    UNCERTAIN = 'uncertain'
    EXTERNAL = 'external'
    OPERATOR = 'operator'


class DecisionKind(enum.StrEnum):
    """Declaration order is the execution priority (exposure risk first, then protection, the HOLD lift last)."""
    # cancel an OWNED stop that does not match its journal record: at once when it can ADD exposure (not reduce-only),
    # else only once a correct replacement is confirmed and covers the side (never removes the last protection)
    CANCEL_MISMATCHED_PROTECT = 'cancel_mismatched_protect'
    PROTECT_ONLY = 'protect_only'                    # place reduce-only protection for uncovered qty; adopts nothing
    RESOLVE_FILLED = 'resolve_filled'                # an owned intent executed qty (exchange evidence)
    RESOLVE_NOT_EXECUTED = 'resolve_not_executed'    # an owned intent ended with nothing executed (exchange evidence)
    ADOPT = 'adopt'                                  # take venue truth the journal lacks (external reduction, Q2)
    QUARANTINE = 'quarantine'                        # (symbol, side) blocked for new risk; the item is never touched
    HOLD = 'hold'                                    # fail closed: reason + actionable owner actions
    REREAD = 'reread'                                # bounded: more reads / queries before anything can be decided
    CLEAR_HOLD = 'clear_hold'                        # leave a NORMAL HOLD (Q4, A08 conditions)


RANK = {k: i for i, k in enumerate(DecisionKind)}


class Outcome(enum.StrEnum):
    FLAT = 'flat'                # fresh OK reads: no position, no open order, nothing owned
    PROTECTED = 'protected'      # every position owned and covered by confirmed owned protection, nothing ambiguous
    HOLD = 'hold'                # explicit fail-closed HOLD with owner actions
    PENDING = 'pending'          # NOT terminal: apply the decisions / reads, then reconcile again (bounded by attempts)


# ------------------------------------------------------------------------------------------------ inputs
@dataclass(frozen=True, slots=True, kw_only=True)
class IntentFact:
    intent_id: str
    purpose: Purpose
    symbol: str
    side: str
    client_id: str
    route: str
    qty: Decimal
    stop_price: Decimal | None
    state: IntentState
    owner_id: str | None
    sent_at_ms: int | None
    exchange_order_id: str | None          # the last exchange order id a result named
    last_lookup: Lookup | None             # lookup of the last result (NOT_FOUND / UNREADABLE) when it was UNKNOWN
    final_executed: Decimal | None
    final_evidence: Evidence | None
    final_at_ms: int | None

    @property
    def opening(self):
        return self.purpose in (Purpose.ENTRY, Purpose.ADD)

    @property
    def live(self):
        return self.state not in (IntentState.FILLED, IntentState.CANCELLED, IntentState.REJECTED,
                                  IntentState.NOT_SENT)


@dataclass(frozen=True, slots=True, kw_only=True)
class LotFact:
    lot_id: str
    symbol: str
    side: str
    qty: Decimal                           # open quantity
    opened_at_ms: int
    entry_intent_id: str
    stop_intent_id: str | None             # the live protect intent, if any
    stop_price: Decimal | None             # the lot's stop level (its first protect, else the runner's hint)


@dataclass(frozen=True, slots=True, kw_only=True)
class AccountView:
    """Read-only projection of the runner's journal fold (view.view_from_fold builds it)."""
    account_id: str
    binding_confirmed: bool
    has_history: bool                      # any journaled event at all (False = first run: INIT rules)
    mode: EntriesMode
    hold_kind: HoldKind | None
    hold_reasons: tuple                    # ReasonCode, ...
    lots: tuple                            # LotFact, ... (open lots only)
    intents: tuple                         # IntentFact, ... (every intent ever recorded)
    newest_result_ms: int | None
    corroboration: tuple = ()              # ((intent_id, (PositionRead, ...)), ...) earlier agreeing reads
    stop_hints: tuple = ()                 # (((symbol, side), Decimal), ...) stop level of a side with no lot stop


@dataclass(frozen=True, slots=True, kw_only=True)
class TradeWindow:
    """userTrades of one (symbol, side) since from_ms (every order id, owned or not), as one read."""
    from_ms: int
    read: ReadOutcome


class DuplicateEvidence(ValueError):
    """One key carries two DIFFERENT pieces of evidence (two by-id answers for one client id, two fill reads for one
    order id, two trade windows for one side): never resolved by input order (Codex P1-3)."""


def unique_by_key(pairs):
    """({key: value} where identical duplicates collapse once, {key: (value, ...)} of the CONFLICTING keys).
    Never last-wins: the result does not depend on the order of `pairs`."""
    seen = {}
    for key, value in pairs:
        vals = seen.setdefault(key, [])
        if value not in vals:
            vals.append(value)
    return ({k: v[0] for k, v in seen.items() if len(v) == 1},
            {k: tuple(v) for k, v in seen.items() if len(v) > 1})


@dataclass(frozen=True, slots=True, kw_only=True)
class VenueSnapshot:
    positions: ReadOutcome
    orders: ReadOutcome
    queries: tuple = ()                    # ((client_id, OrderOutcome), ...) by-client-id answers for this pass
    fills: tuple = ()                      # ((exchange_order_id, ReadOutcome), ...) VenueFill tuples
    trades: tuple = ()                     # (((symbol, side), TradeWindow), ...)

    @staticmethod
    def _one(pairs, key):
        good, bad = unique_by_key(pairs)
        if key in bad:
            raise DuplicateEvidence(f'{key!r}: {len(bad[key])} different values')
        return good.get(key)

    def query(self, cid) -> OrderOutcome | None:
        return self._one(self.queries, cid)

    def fills_of(self, eoid) -> ReadOutcome | None:
        return self._one(self.fills, eoid)

    def trades_of(self, key) -> TradeWindow | None:
        return self._one(self.trades, key)


@dataclass(frozen=True, slots=True, kw_only=True)
class RecPolicy:
    freshness_ms: int = 30_000             # a read older than this (vs now) is stale
    visibility_ms: int = NOT_FOUND_WINDOW_MS   # a NOT_FOUND earlier than sent + this proves nothing
    corroboration_reads: int = 2           # agreeing position reads that resolve a lost entry
    max_attempts: int = 3                  # PENDING passes before an unresolved episode becomes HOLD
    settle_ms: int = 0                     # a diff on a side with a FINAL this recent re-reads first (R14 b; 0 = off)
    reopen_window_ms: int = 86_400_000     # how long a corroborated not-found may still be superseded (Q2)
    quarantine_per_side: bool = Q1_QUARANTINE_PER_SIDE
    adopt_external_change: bool = Q2_ADOPT_EXTERNAL_CHANGE
    protect_surplus: bool = Q3_PROTECT_SURPLUS
    auto_clear_hold: bool = Q4_AUTO_CLEAR_HOLD
    auto_clearable: frozenset = AUTO_CLEARABLE


# ------------------------------------------------------------------------------------------------ outputs
@dataclass(frozen=True, slots=True, kw_only=True)
class RecDecision:
    kind: DecisionKind
    row: str                               # the REC-02 matrix row that produced it (R01..R19)
    symbol: str | None = None
    side: str | None = None
    intent_id: str | None = None
    client_id: str | None = None
    lot_id: str | None = None
    qty: Decimal | None = None             # executed qty (RESOLVE_FILLED / ADOPT) or stop qty (PROTECT_ONLY)
    price: Decimal | None = None           # average price (RESOLVE_FILLED / ADOPT) or stop level (PROTECT_ONLY)
    detail: str = ''                       # stable token, e.g. 'external_reduce', 'stop_vanished', 'stale_read'
    reasons: tuple = ()                    # ReasonCode, ...
    evidence: tuple = ()                   # stable tokens: 'order:<id>', 'trade:<id>', 'read:positions@<ms>', ...
    owner_actions: tuple = ()              # non-empty on HOLD / QUARANTINE
    # HOLD / QUARANTINE only: inc_ + 32 hex, a pure function of (account, kind, row, detail, subject ids, evidence) -
    # never of the clock, attempt or trigger - so the same item after a restart has the same id and a journaled
    # Incident (NC-01 r3a IncidentRecorded) is recognised as a duplicate. '' on every other kind.
    incident_id: str = ''

    def key(self):
        return (RANK[self.kind], self.symbol or '', self.side or '', self.intent_id or '', self.client_id or '',
                self.row, self.detail)


@dataclass(frozen=True, slots=True, kw_only=True)
class ReadPlan:
    queries: tuple = ()                    # client ids to query by id
    fills: tuple = ()                      # exchange order ids whose fills are needed
    trades: tuple = ()                     # ((symbol, side, from_ms), ...)


@dataclass(frozen=True, slots=True, kw_only=True)
class Verdict:
    reconciliation_id: str
    at_ms: int
    trigger: Trigger
    attempt: int
    outcome: Outcome
    ownership: Ownership
    decisions: tuple                       # RecDecision, ... in execution order
    hold_reasons: tuple                    # ReasonCode, ... (empty unless outcome HOLD)
    quarantined: tuple                     # ((symbol, side), ...); (None, None) = the whole account
    needs: ReadPlan = field(default_factory=ReadPlan)

    def of(self, kind):
        return tuple(d for d in self.decisions if d.kind is kind)

    @property
    def rows(self):
        return tuple(sorted({d.row for d in self.decisions}))


__all__ = ['AUTO_CLEARABLE', 'AccountView', 'DecisionKind', 'DuplicateEvidence', 'IntentFact', 'LotFact', 'Outcome',
           'PCTX', 'unique_by_key',
           'PositionRead', 'QCTX', 'Q1_QUARANTINE_PER_SIDE', 'Q2_ADOPT_EXTERNAL_CHANGE', 'Q3_PROTECT_SURPLUS',
           'Q4_AUTO_CLEAR_HOLD', 'RANK', 'ReadPlan', 'RecDecision', 'RecPolicy', 'TradeWindow', 'Trigger',
           'VenueSnapshot', 'Verdict', 'ZERO']
