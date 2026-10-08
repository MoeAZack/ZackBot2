"""Typed store failures. Messages name the operation and the file NAME only: never a full path (it holds the user name)
and never file content (A16)."""
from __future__ import annotations

import os

from newcore.ports.journal import JournalConflict, JournalUnavailable


def _os_kind(cause):
    if cause is None:
        return 'failed'
    win = getattr(cause, 'winerror', None)
    return f'{type(cause).__name__}(errno={cause.errno}' + (f', winerror={win})' if win is not None else ')')


class DurabilityUnavailable(JournalUnavailable):
    """The store could not make a record durable: a create, write, fsync or directory flush failed.

    Contract (A21 / A23 / A24, hard HOLD): the record is NOT known to be durable, so whatever it described must not be
    acted on (an intent is never sent, a result is never applied). The journal that raised it is poisoned for the rest of
    the process (a failed fsync is never retried on the same handle: its dirty pages may already be gone). The caller
    enters hard HOLD (`newcore.store.hold.durability_hold`) and may only use the NC-01 emergency set
    (`modes.permitted(HOLD, DURABILITY_UNAVAILABLE, ...)`). Leaving it needs a restart with a writable store, then the
    normal HOLD path (reconciliation)."""

    def __init__(self, op, path, cause=None):
        self.op = op
        self.name = os.path.basename(path) if path else ''
        self.errno = getattr(cause, 'errno', None)
        self.winerror = getattr(cause, 'winerror', None)
        super().__init__(f'store {op} on {self.name or "<store>"}: {_os_kind(cause)}')


class SequenceConflict(JournalConflict):
    """A different event at an already-used sequence, or an event id re-used with other bytes (G2 / G3, NC-01
    invariant 12). The journal is unchanged; the event is never retried as it is."""


class JournalExists(Exception):
    """create_journal on a directory that already holds segments: open it with recover_journal instead."""
