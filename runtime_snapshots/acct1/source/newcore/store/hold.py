"""What the store tells the Runner to do: pure mapping from a store outcome to NC-01 HOLD semantics.

Store-failure contract (A21 / A23 / A24; the Runner's reaction is S1's, not implemented here):
- `FileJournal.append` raises `DurabilityUnavailable` when a write or fsync fails. The event is NOT durable and NOT
  committed to the gate; whatever reached the file is sealed off at once (evidence copy + new segment), so a restart
  reads exactly the in-process state. The Runner must:
    1. not send the intent / not apply the result the event described;
    2. enter hard HOLD at once: `durability_hold()` = EntriesMode.HOLD + HoldKind.DURABILITY_UNAVAILABLE with reason
       recovery.durability_unavailable (NC-01 ModeChanged requires exactly that reason for this hold kind). The
       ModeChanged itself cannot be journaled: nothing can;
    3. act only inside the NC-01 emergency set, `hard_hold_permits(purpose, op)` (= modes.permitted for that hold kind):
       narrow query, place reduce-only protection before any old one is removed, drain resting ENTRY / ADD, adopt
       race / partial fills; never an entry, add, reprice, fallback, management or resume. ONE narrow exception
       (Codex ruling, F3 / P1-3): when no emergency stop can be confirmed for exposed quantity, a deterministic
       reduce-only EMERGENCY close of that quantity - `hard_hold_permits(CLOSE, PLACE, emergency_close=True)`; an
       ordinary close or any management close stays forbidden;
    4. stay in hard HOLD even if a later append succeeds: the journal accepts a retry (the same event lands exactly
       once, r3), but leaving hard HOLD is reconciliation's job (A08), never a successful write. The store does not
       decide HOLD; the Runner (S1) does, from this contract.
- Every append before the venue call is write-ahead: IntentRecorded before the first send, `sent`
  (IntentStateChanged -> SUBMITTED) BEFORE the venue call, ResultObserved before the result is applied. Recovery relies
  on this: an intent with no `sent` record was provably never sent (DURABLE_NOT_SENT); one with `sent` and no final
  result is UNKNOWN until the venue is queried by its deterministic client id.
- recover_journal outcomes: CLEAN / REPAIRED proceed (a torn tail is not damage, A15); DAMAGED / UNREADABLE / MISSING
  are HOLD with zero writes (the damaged bytes stay damaged, so every restart re-derives the same HOLD: A05 without a
  write); ABORT_RO creates no AccountContext at all; DURABILITY_UNAVAILABLE (the repair or the append handle failed)
  is hard HOLD.
"""
from __future__ import annotations

import enum
from dataclasses import dataclass

from newcore.domain import EntriesMode, HoldKind, ReasonCode, permitted


class Verdict(enum.StrEnum):
    CLEAN = 'clean'
    REPAIRED = 'repaired'                        # torn tail / interrupted create: evidence copied, sealed, rolled
    DAMAGED = 'damaged'                          # mid-segment damage, invalid record, broken chain: HOLD, zero writes
    UNREADABLE = 'unreadable'                    # I/O error or a non-file at a member path: HOLD, zero writes
    MISSING = 'missing'                          # no journal (or an empty journal dir): HOLD, zero writes
    ABORT_RO = 'abort_ro'                        # future / unknown format anywhere: no AccountContext, zero writes
    DURABILITY_UNAVAILABLE = 'durability_unavailable'   # the store cannot write: hard HOLD


class StoreOutcome(enum.StrEnum):
    PROCEED = 'proceed'
    HOLD = 'hold'
    HARD_HOLD = 'hard_hold'
    ABORT_RO = 'abort_ro'


@dataclass(frozen=True, slots=True)
class StoreDirective:
    outcome: StoreOutcome
    hold_kind: HoldKind | None
    reason: ReasonCode | None

    @property
    def account_context(self):
        """False for ABORT-RO: the account is not loaded at all (R-NO-MANAGE)."""
        return self.outcome is not StoreOutcome.ABORT_RO

    @property
    def mode(self):
        return EntriesMode.HOLD if self.outcome in (StoreOutcome.HOLD, StoreOutcome.HARD_HOLD) else None


def durability_hold():
    return StoreDirective(StoreOutcome.HARD_HOLD, HoldKind.DURABILITY_UNAVAILABLE,
                          ReasonCode.RECOVERY_DURABILITY_UNAVAILABLE)


_BY_VERDICT = {
    Verdict.CLEAN: StoreDirective(StoreOutcome.PROCEED, None, None),
    Verdict.REPAIRED: StoreDirective(StoreOutcome.PROCEED, None, None),
    Verdict.DAMAGED: StoreDirective(StoreOutcome.HOLD, HoldKind.NORMAL, ReasonCode.RECOVERY_SCHEMA_INVALID),
    Verdict.UNREADABLE: StoreDirective(StoreOutcome.HOLD, HoldKind.NORMAL, ReasonCode.RECOVERY_STATE_UNREADABLE),
    Verdict.MISSING: StoreDirective(StoreOutcome.HOLD, HoldKind.NORMAL, ReasonCode.RECOVERY_STATE_MISSING),
    Verdict.ABORT_RO: StoreDirective(StoreOutcome.ABORT_RO, None, ReasonCode.RECOVERY_SCHEMA_FUTURE),
}


def directive_for(verdict):
    verdict = Verdict(verdict)
    return durability_hold() if verdict is Verdict.DURABILITY_UNAVAILABLE else _BY_VERDICT[verdict]


def hard_hold_permits(purpose, op, *, emergency_close=False):
    """The A23 / A24 emergency set, straight from NC-01: modes.permitted(HOLD, DURABILITY_UNAVAILABLE, ...);
    `emergency_close=True` asks for the one protection-failure exception (CLOSE, PLACE) and nothing else."""
    return permitted(EntriesMode.HOLD, HoldKind.DURABILITY_UNAVAILABLE, purpose, op, emergency_close=emergency_close)
