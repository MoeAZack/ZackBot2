"""Restart-stable ids the Runner needs beyond step 0 r2 (newcore.ports.keys).

Step 0 r2 derives: keyed decision ids, the one keyed intent id, lot ids (derive_lot_id), lineage child intent ids
(Grammar.next_child_intent_id / derive_child_intent_id) and client ids. Not covered there, so derived here with the
same rule (a pure function of journaled data, no clock, no randomness):

    event_id           one per (aggregate, sequence): G2 makes the sequence the event's identity
    result_id          the n-th result recorded for an intent
    position_id        one per (account, symbol, side)
    child_decision_id  the unkeyed decision (PROTECT / stop-failed CLOSE) that authorizes exactly one child intent
    reconciliation_id  the n-th reconciliation snapshot at at_ms (NC-02 owns the record)
    operator_decision_id  an operator decision (RESUME) at at_ms
    risk_decision_id   a risk-authority decision (HALT / kill) at at_ms
    resolution_decision_id  the reconciliation decision resolving a lost intent from position reads
    emergency_stop_client_id  the A23 hard-HOLD emergency stop (exchange truth only: never journaled)

    H(tag, parts...) = sha256(b'zackbot.newcore.slice.' + tag + b'.v1' + (b'\\x00' + part)...), first 32 hex
Reported as an interface item: these belong in newcore.ports.keys (with pinned vectors) once Codex rules on them.
"""
from __future__ import annotations

import hashlib
import re

from newcore.domain.base import dec_str

from newcore.ports.keys import (client_id_for, decision_key, derive_child_intent_id, derive_decision_id,
                                derive_intent_id, derive_lot_id, is_newcore_client_id)

__all__ = ['client_id_for', 'decision_key', 'derive_child_intent_id', 'derive_decision_id', 'derive_intent_id',
           'derive_lot_id', 'is_newcore_client_id', 'event_id', 'result_id', 'position_id', 'child_decision_id',
           'reconciliation_id', 'operator_decision_id', 'risk_decision_id', 'resolution_decision_id',
           'emergency_stop_client_id', 'is_emergency_client_id', 'tick_decision_id', 'marker_decision_id',
           'mark_decision_id', 'mg_input_decision_id']


def _hex(tag, *parts):
    h = hashlib.sha256(b'zackbot.newcore.slice.' + tag.encode('ascii') + b'.v1')
    for p in parts:
        b = str(p).encode('ascii')
        if b'\x00' in b:
            raise ValueError('an id part cannot contain NUL')
        h.update(b'\x00' + b)
    return h.hexdigest()[:32]


def event_id(aggregate_id, sequence):
    return 'evt_' + _hex('event_id', aggregate_id, sequence)


def result_id(intent_id, n):
    return 'res_' + _hex('result_id', intent_id, n)


def position_id(account_id, symbol, side):
    return 'pos_' + _hex('position_id', account_id, symbol, side)


def child_decision_id(child_intent_id):
    return 'dec_' + _hex('child_decision_id', child_intent_id)


def reconciliation_id(account_id, at_ms, n):
    return 'rec_' + _hex('reconciliation_id', account_id, at_ms, n)


def operator_decision_id(account_id, action, at_ms):
    return 'dec_' + _hex('operator_decision_id', account_id, action, at_ms)


def risk_decision_id(account_id, action, at_ms):
    return 'dec_' + _hex('risk_decision_id', account_id, action, at_ms)


def resolution_decision_id(intent_id):
    """The explicit reconciliation decision that resolves a lost (UNKNOWN + NOT_FOUND) intent from position reads."""
    return 'dec_' + _hex('resolution_decision_id', intent_id)


EMERGENCY_PREFIX = 'zbn1e-'
EMERGENCY_CID_RE = re.compile(r'zbn1e-[a-z2-7]{26}')


def emergency_stop_client_id(account_id, symbol, side, qty, generation=0):
    """A23 emergency stop placed while the store cannot journal anything: its id is a pure function of EXCHANGE
    truth (account, symbol, side, the uncovered quantity it protects) and a cover GENERATION (Cowork F1 / F2: a second
    gap of the same size, or an id already used by a stop that ended, takes the next generation; generation 0 is the
    original id). A restart re-derives the same ids, finds a live one and never duplicates it (M53). 32 chars,
    `zbn1e-` + 26 base32 (STEP0 section 6 item 6)."""
    parts = (account_id, symbol, side, dec_str(qty)) + ((generation,) if generation else ())
    digest = hashlib.sha256(b'zackbot.newcore.slice.emergency_stop.v1' + b''.join(
        b'\x00' + str(p).encode('ascii') for p in parts)).digest()
    n, b32 = int.from_bytes(digest, 'big'), 'abcdefghijklmnopqrstuvwxyz234567'
    return EMERGENCY_PREFIX + ''.join(b32[(n >> (256 - 5 * (i + 1))) & 31] for i in range(26))


def is_emergency_client_id(client_id):
    """Shape test only (owned-vs-foreign triage), a full match like ports.is_newcore_client_id: never parsed."""
    return isinstance(client_id, str) and EMERGENCY_CID_RE.fullmatch(client_id) is not None


def tick_decision_id(lot_id, candle_open_ms):
    """The management tick of one lot at one closed candle (M4): a WAIT decision that makes the driver's candle / mark
    input durable BEFORE it is applied, so a restart folds the same driver inputs in the same order."""
    return 'dec_' + _hex('tick_decision_id', lot_id, candle_open_ms)


def incident_id(account_id, kind, *parts):
    """A durable incident (NC-01 r3a IncidentRecorded): one per (kind, the records it concerns) - restart-stable."""
    return 'inc_' + _hex('incident_id', account_id, kind, *parts)


def marker_decision_id(kind, intent_id):
    """A management route marker of one refused classic stop ('mg fallback' / 'mg refused'): at most one per intent."""
    return 'dec_' + _hex('marker_decision_id', kind, intent_id)


def mg_input_decision_id(kind, lot_id, *parts):
    """A durable management input of one lot ('start' once; 'fill' once per venue trade id)."""
    return 'dec_' + _hex('mg_input_decision_id', kind, lot_id, *parts)


def mark_decision_id(lot_id, at_ms, sequence):
    """An intra-candle mark that fired a trigger of the lot (the journal sequence it was decided after: unique)."""
    return 'dec_' + _hex('mark_decision_id', lot_id, at_ms, sequence)
