"""Event admission: sequence ordering and idempotent re-apply (contract invariant 12).

An EventCursor is the pure record of what one aggregate has applied: the last sequence and the canonical digest of every
applied event. `admit(cursor, event)`:

- APPLY when the event's sequence is exactly last_sequence + 1 and its event_id is new;
- ALREADY_APPLIED (an idempotent no-op, cursor unchanged) when the same event_id was applied at the same sequence with
  identical canonical bytes;
- EventOrderError (a typed hard failure) for anything else: the same event_id with different bytes, another event at an
  already-used sequence, a gap, or an event of another account / aggregate.
The digest is codec.contract_sha256 of the event, so "identical" means identical canonical bytes, never equal-looking.
"""
from __future__ import annotations

import enum
import re

from .base import Record, check_id, record, req
from .codec import contract_sha256
from .errors import EventOrderError
from .events import EVENT_TYPES

SHA_RE = re.compile(r'[0-9a-f]{64}')


class Admission(enum.StrEnum):
    APPLY = 'apply'
    ALREADY_APPLIED = 'already_applied'


@record
class EventDigest(Record):
    event_id: str
    sequence: int
    sha256: str

    def _validate(self, p):
        check_id(self.event_id, p + '.event_id', 'evt')
        req(self.sequence >= 1, p + '.sequence', '>= 1')
        req(SHA_RE.fullmatch(self.sha256) is not None, p + '.sha256', 'lowercase hex SHA-256')


@record
class EventCursor(Record):
    account_id: str
    aggregate_id: str
    last_sequence: int
    applied: tuple[EventDigest, ...]          # applied[i] has sequence i + 1

    def _validate(self, p):
        check_id(self.account_id, p + '.account_id', 'acct')
        check_id(self.aggregate_id, p + '.aggregate_id', 'pf')
        req(self.last_sequence == len(self.applied), p + '.last_sequence', 'one digest per applied sequence')
        req(all(d.sequence == i + 1 for i, d in enumerate(self.applied)), p + '.applied', 'digests out of sequence')
        ids = [d.event_id for d in self.applied]
        req(len(ids) == len(set(ids)), p + '.applied', 'an event id applied twice')


def admit(cursor, event):
    """Pure: (new cursor, Admission). Raises EventOrderError for every non-idempotent conflict."""
    req(isinstance(event, EVENT_TYPES), 'event', 'not a DomainEvent')
    if (event.account_id, event.aggregate_id) != (cursor.account_id, cursor.aggregate_id):
        raise EventOrderError('event.aggregate_id', 'event of another account / aggregate')
    digest = contract_sha256(event)
    for d in cursor.applied:
        if d.event_id == event.event_id:
            if d.sequence == event.sequence and d.sha256 == digest:
                return cursor, Admission.ALREADY_APPLIED
            raise EventOrderError('event.event_id', f'{event.event_id} was applied with different bytes / sequence')
    expected = cursor.last_sequence + 1
    if event.sequence != expected:
        what = 'a gap' if event.sequence > expected else 'an already-used sequence'
        raise EventOrderError('event.sequence', f'sequence {event.sequence} is {what}; expected {expected}')
    new = EventDigest(event_id=event.event_id, sequence=event.sequence, sha256=digest)
    return EventCursor(account_id=cursor.account_id, aggregate_id=cursor.aggregate_id, last_sequence=expected,
                       applied=cursor.applied + (new,)), Admission.APPLY
