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

    H(tag, parts...) = sha256(b'zackbot.newcore.slice.' + tag + b'.v1' + (b'\\x00' + part)...), first 32 hex
Reported as an interface item: these belong in newcore.ports.keys (with pinned vectors) once Codex rules on them.
"""
from __future__ import annotations

import hashlib

from newcore.ports.keys import (client_id_for, decision_key, derive_child_intent_id, derive_decision_id,
                                derive_intent_id, derive_lot_id, is_newcore_client_id)

__all__ = ['client_id_for', 'decision_key', 'derive_child_intent_id', 'derive_decision_id', 'derive_intent_id',
           'derive_lot_id', 'is_newcore_client_id', 'event_id', 'result_id', 'position_id', 'child_decision_id',
           'reconciliation_id', 'operator_decision_id', 'risk_decision_id']


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
