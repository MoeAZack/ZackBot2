"""MemoryJournal: the in-memory JournalPort of slice S1 (STEP0_INTERFACE.md section 3, r2).

Append-only list of NC-01 DomainEvents for ONE (account, aggregate). Every append is admitted by the ONE step-0
JournalGate (header_of -> grammar G1-G10 -> NC-01 per-record checks) before it lands; a refused event raises the gate's
JournalConflict unchanged and lands nothing; an identical re-append is ALREADY_APPLIED. `gate()` exposes the admission
state (claim_signal, next_child_intent_id). Passes tests/newcore_ports/journal_contract.py.

"Durable" = in this object: the journal survives a Runner restart (a new Runner over the same journal), not a process
exit; NC-02a is the file store. `fail_writes(n, after=k)` makes appends raise JournalUnavailable (store failure) after k
more successful appends: the crash-point injector of the S1 restart tests. `reopen()` = a restart: a new MemoryJournal
whose gate is rebuilt from the durable events.
"""
from __future__ import annotations

from newcore.ports.journal import Admission, JournalGate, JournalUnavailable


class MemoryJournal:
    def __init__(self, account_id: str, aggregate_id: str, events=()):
        self.account_id, self.aggregate_id = account_id, aggregate_id
        self._gate = JournalGate.rebuild(account_id, aggregate_id, events)
        self._events = list(events)
        self._decisions = {e.decision.decision_id: e for e in self._events if hasattr(e, 'decision')}
        self._fail = 0
        self._fail_after = 0

    def reopen(self) -> 'MemoryJournal':
        """The same journal after a process restart: the gate is rebuilt from the durable events."""
        return MemoryJournal(self.account_id, self.aggregate_id, self._events)

    def fail_writes(self, n=1, *, after=0):
        """Fault hook: after `after` more successful appends, the next n appends raise JournalUnavailable."""
        self._fail += n
        self._fail_after = after

    # ------------------------------------------------------------------------------------------------ JournalPort
    def append(self, event) -> Admission:
        if self._fail > 0 and self._fail_after == 0:
            self._fail -= 1
            raise JournalUnavailable('memory journal: injected store failure')
        adm = self._gate.admit(event)                    # raises JournalConflict (refused, nothing changed)
        if adm is Admission.APPLY:
            self._events.append(event)                   # the durable write
            d = getattr(event, 'decision', None)
            if d is not None:
                self._decisions[d.decision_id] = event
            if self._fail > 0 and self._fail_after > 0:
                self._fail_after -= 1
        return adm

    def last_sequence(self) -> int:
        return len(self._events)

    def read(self, after_sequence: int = 0):
        return tuple(self._events[after_sequence:])

    def find_decision(self, decision_id: str):
        return self._decisions.get(decision_id)

    def gate(self) -> JournalGate:
        return self._gate
