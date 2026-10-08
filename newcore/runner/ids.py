"""Restart-stable ids the S1 Runner needs beyond the step-0 derivations (newcore.ports.keys).

Step 0 derives keyed decision ids, keyed intent ids, child intent ids and client ids. The slice also needs ids for
records no candle key names: events, results, lots, positions, the protect decision of a child intent, reconciliation
records and operator decisions. They follow the same rule: a pure function of journaled data, no clock, no randomness.

    H(tag, parts...) = sha256(b'zackbot.newcore.slice.' + tag + b'.v1' + (b'\\x00' + part)...), first 32 hex
Reported as an interface item: these belong in newcore.ports.keys (with pinned vectors) once Codex rules on them.
"""
from __future__ import annotations

import hashlib

from newcore.ports.keys import (client_id_for, derive_child_intent_id, derive_decision_id, derive_intent_id,
                                is_newcore_client_id)

__all__ = ['client_id_for', 'derive_child_intent_id', 'derive_decision_id', 'derive_intent_id', 'is_newcore_client_id',
           'event_id', 'result_id', 'lot_id', 'position_id', 'child_decision_id', 'reconciliation_id',
           'operator_decision_id']


def _hex(tag, *parts):
    h = hashlib.sha256(b'zackbot.newcore.slice.' + tag.encode('ascii') + b'.v1')
    for p in parts:
        b = str(p).encode('ascii')
        if b'\x00' in b:
            raise ValueError('an id part cannot contain NUL')
        h.update(b'\x00' + b)
    return h.hexdigest()[:32]


def event_id(aggregate_id, sequence):
    """One event per (aggregate, sequence): G2 makes the sequence the event's identity."""
    return 'evt_' + _hex('event_id', aggregate_id, sequence)


def result_id(intent_id, n):
    """The n-th result (0-based) recorded for an intent."""
    return 'res_' + _hex('result_id', intent_id, n)


def lot_id(entry_intent_id):
    """The lot an entry's fill creates (one lot per entry intent)."""
    return 'lot_' + _hex('lot_id', entry_intent_id)


def position_id(account_id, symbol, side):
    return 'pos_' + _hex('position_id', account_id, symbol, side)


def child_decision_id(child_intent_id):
    """The unkeyed decision (PROTECT / stop-failed close) that creates exactly one child intent."""
    return 'dec_' + _hex('child_decision_id', child_intent_id)


def reconciliation_id(account_id, at_ms, n):
    """The n-th reconciliation snapshot taken at at_ms (no NC-01 record yet: NC-02 owns the record)."""
    return 'rec_' + _hex('reconciliation_id', account_id, at_ms, n)


def operator_decision_id(account_id, action, at_ms):
    return 'dec_' + _hex('operator_decision_id', account_id, action, at_ms)
