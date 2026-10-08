"""The runner-facing entry points of the store (NC-02b): open_store() at start, AccountStore.checkpoint() while running.

    boot = open_store(base, account, exchange=view, now_ms=now, aggregate_id=pf_id, fold=runner_fold)
    boot.outcome      INIT | MANAGE | HOLD | HOLD_INIT | ABORT_RO | REJECT
    boot.journal      the writable FileJournal (JournalPort) when the account context exists and the store can write
    boot.view         the read-only journal view (events, gate, state; appends refused) in hard HOLD
    boot.store        the AccountStore: checkpoint(portfolio, now) / checkpoint_due / promote(...)

Outcomes (the runner maps each to its own behaviour; the store never sends anything):
  INIT       first run on a flat exchange with a confirmed binding: the store was created, known-empty, entries allowed
             (MANAGE semantics, reported apart so the runner can log the first run).
  MANAGE     the committed generation is MANAGED and a fresh exchange snapshot matches it (or the runner's fold of an
             uncovered journal tail matches and was checkpointed at once).
  HOLD       quarantine (hold_kind NORMAL), or hard HOLD (hold_kind DURABILITY_UNAVAILABLE: the store cannot write;
             only the A23 / A24 emergency set; `view` carries the journal for pricing it). Leaving HOLD is promote().
  HOLD_INIT  a first run against a non-flat exchange (or an unconfirmed binding): owner adoption + promote().
  ABORT_RO   a future / unknown format somewhere: no account context, zero writes; exit.
  REJECT     not started, nothing written: another process holds this account (journal lock), or a first run must
             wait (the exchange is down, or a flat first run on an unwritable data folder).
Legacy files (rule 0) are reported in `legacy` whatever the outcome; they never change it.
"""
from __future__ import annotations

import enum
from dataclasses import dataclass

from newcore.domain import HoldKind

from .errors import JournalLocked
from .journal import ReadOnlyJournal
from .store import Mode, boot


class Outcome(enum.StrEnum):
    INIT = 'init'
    MANAGE = 'manage'
    HOLD = 'hold'
    HOLD_INIT = 'hold_init'
    ABORT_RO = 'abort_ro'
    REJECT = 'reject'


@dataclass
class StoreBoot:
    outcome: Outcome
    hold_kind: HoldKind | None
    reason: str | None
    store: object | None             # AccountStore (None for ABORT_RO / REJECT)
    journal: object | None           # writable FileJournal, or None
    view: object | None              # read-only journal view in hard HOLD, or None
    portfolio: object | None         # the current committed (or folded) portfolio
    candidate: object | None         # HOLD: the A07 candidate offered for promote()
    items: tuple
    legacy: tuple
    incidents: tuple
    findings: tuple

    @property
    def hard_hold(self):
        return self.hold_kind is HoldKind.DURABILITY_UNAVAILABLE

    @property
    def may_open_risk(self):
        """Only INIT and MANAGE ever allow new risk (the portfolio's own entries mode still applies)."""
        return self.outcome in (Outcome.INIT, Outcome.MANAGE)


def open_store(base, account, *, exchange, now_ms, aggregate_id=None, fold=None, fs=None, reader=None, cipher=None,
               settings=None):
    """Boot the account's store and classify the result. Never raises for a store condition (JournalLocked becomes
    REJECT); programming errors (bad ids) still raise."""
    try:
        r = boot(base, account, exchange=exchange, now_ms=now_ms, fs=fs, reader=reader, cipher=cipher,
                 settings=settings, aggregate_id=aggregate_id, fold=fold)
    except JournalLocked as ex:
        return StoreBoot(Outcome.REJECT, None, 'locked: another process holds this account', None, None, None, None,
                         None, (), (), (), ())
    first_run = 'init_commit' in r.writes and r.mode is Mode.MANAGE
    outcome = {Mode.MANAGE: Outcome.INIT if first_run else Outcome.MANAGE, Mode.HOLD: Outcome.HOLD,
               Mode.HOLD_INIT: Outcome.HOLD_INIT, Mode.ABORT_RO: Outcome.ABORT_RO,
               Mode.INIT_WAIT: Outcome.REJECT}[r.mode]
    st = r.store
    journal = st.journal if st is not None and r.hold_kind is not HoldKind.DURABILITY_UNAVAILABLE else None
    view = r.view
    if view is None and st is not None and r.hold_kind is HoldKind.DURABILITY_UNAVAILABLE and st.journal is not None:
        view = ReadOnlyJournal(st.journal._folder, 'hard HOLD: the store cannot write')
    reason = None if r.reason is None else str(r.reason)
    if outcome is Outcome.REJECT:
        reason = 'init waits: ' + ('; '.join(r.findings) or 'the exchange is down')
        st = journal = None
    return StoreBoot(outcome, r.hold_kind, reason, st, journal, view, r.portfolio, r.candidate, tuple(r.items),
                     tuple(r.legacy), tuple(r.incidents), tuple(r.findings))
