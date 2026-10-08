"""Typed store failures. Messages name the operation and the file NAME only: never a full path (it holds the user name)
and never file content (A16)."""
from __future__ import annotations

import os

from newcore.ports.journal import JournalConflict, JournalUnavailable


def _os_kind(cause):
    if cause is None:
        return 'failed'
    win = getattr(cause, 'winerror', None)
    return f'{type(cause).__name__}(errno={getattr(cause, "errno", None)}' + (f', winerror={win})' if win is not None
                                                                              else ')')


class DurabilityUnavailable(JournalUnavailable):
    """The store could not make a record durable: a create, write, fsync or directory flush failed.

    Contract (A21 / A23 / A24, hard HOLD): the record is NOT known to be durable, so whatever it described must not be
    acted on (an intent is never sent, a result is never applied), and nothing was committed to the gate (r3 store
    atomicity). The failed handle is never reused (its dirty pages may already be gone): the journal seals off whatever
    reached the file and rolls to a new segment, so a restart equals the in-process state and a retry of the same event
    lands exactly once. The account-level reaction is NC-02 policy: the caller enters hard HOLD
    (`newcore.store.hold.durability_hold`) and may only use the NC-01 emergency set until reconciliation."""

    def __init__(self, op, path, cause=None):
        self.op = op
        self.name = os.path.basename(path) if path else ''
        self.errno = getattr(cause, 'errno', None)
        self.winerror = getattr(cause, 'winerror', None)
        self.cause_type = type(cause).__name__ if cause is not None else None      # safe fields only (A16)
        self.cipher_unavailable = _cipher_unavailable(cause)                       # typed, never from a message
        super().__init__(f'store {op} on {self.name or "<store>"}: {_os_kind(cause)}')


def _cipher_unavailable(ex):
    """True when `ex` or anything on its __cause__ / __context__ chain IS a CipherUnavailable (or a DurabilityUnavailable
    that recorded one). Classification by type only - never by matching an exception's message (Codex P2)."""
    from .cipher import CipherUnavailable
    seen, c = set(), ex
    while c is not None and id(c) not in seen:
        seen.add(id(c))
        if isinstance(c, CipherUnavailable) or getattr(c, 'cipher_unavailable', None) is True:
            return True
        c = c.__cause__ or c.__context__
    return False


CIPHER_UNAVAILABLE_REASON = ('no evidence cipher on this platform (CipherUnavailable; the production cipher is DPAPI, '
                             'Windows only): the evidence copy fails closed')


def failure_reason(ex):
    """(kind, text) naming WHY the store could not write - never silent (Cowork N6) and never the exception's own
    message (Codex P2: a message can carry a full path / user name or wrapped secret-shaped text into durable findings).
    kind: 'cipher_unavailable' (typed, from the exception or its cause chain) or 'unwritable'. text: a fixed public
    reason plus safe fields only - the exception type, errno, winerror."""
    if _cipher_unavailable(ex):
        return 'cipher_unavailable', CIPHER_UNAVAILABLE_REASON
    if isinstance(ex, DurabilityUnavailable):
        return 'unwritable', (f'store write failed ({ex.cause_type or "no cause"}, errno={ex.errno}, '
                              f'winerror={ex.winerror})')
    return 'unwritable', (f'store write failed ({type(ex).__name__}, errno={getattr(ex, "errno", None)}, '
                          f'winerror={getattr(ex, "winerror", None)})')


class SequenceConflict(JournalConflict):
    """A different event at an already-used sequence, or an event id re-used with other bytes (G2 / G3, NC-01
    invariant 12). The journal is unchanged; the event is never retried as it is."""


class JournalLocked(Exception):
    """Another writer holds journal/.lock: this process must not open the journal (refuse start; not a HOLD).
    Interim NC-02a writer fence (Cowork finding 3); NC-02b's run lock supersedes it."""


class JournalExists(Exception):
    """create_journal on a directory that already holds segments: open it with recover_journal instead."""
