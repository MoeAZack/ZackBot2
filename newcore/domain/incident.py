"""Incidents (r3 DRAFT item 1): what went wrong or what an emergency action did, journaled as a typed record.

Step 0 dropped `incident_recorded`; S3 (reconciliation incidents) and the hard-HOLD emergency actions need to journal
them. An Incident names its kind from the reason registry (never an entry / exit reason: those are decisions and
fills), the records it concerns by opaque id (intents, lots, positions), the evidence it rests on (result /
reconciliation / decision / event ids) and its integer UTC-ms time. Free text is display only. It is journaled through
events.IncidentRecorded and has no effect on ownership: it never changes a lot, an intent or a mode by itself.
"""
from __future__ import annotations

from .base import ID_PREFIXES, Record, check_id, check_symbol, record, req
from .orders import Side
from .reasons import ReasonCode

NOT_INCIDENT_KINDS = frozenset({'entry', 'exit'})        # those are decisions / fills, never incidents


def _refs(values, path, *prefixes):
    for i, v in enumerate(values):
        check_id(v, f'{path}[{i}]', *prefixes)
    req(len(set(values)) == len(values), path, 'duplicate reference')


@record
class Incident(Record):
    incident_id: str
    account_id: str
    kind: ReasonCode                         # what happened, from the registry
    at_ms: int                               # when it was observed (integer UTC ms)
    symbol: str | None                       # the instrument concerned, if one
    side: Side | None                        # set only with a symbol
    intent_refs: tuple[str, ...]             # int_ ids concerned
    lot_refs: tuple[str, ...]                # lot_ ids concerned
    position_refs: tuple[str, ...]           # pos_ ids concerned
    evidence: tuple[str, ...]                # opaque ids it rests on (res_ / rec_ / dec_ / evt_ / ...)
    detail: str                              # display only, <= 160 printable characters, never parsed

    def _validate(self, p):
        p = f'{p}[{self.incident_id}]'
        check_id(self.incident_id, p + '.incident_id', 'inc')
        check_id(self.account_id, p + '.account_id', 'acct')
        req(self.kind.namespace not in NOT_INCIDENT_KINDS, p + '.kind', f'{self.kind} is a decision / fill reason')
        if self.symbol is not None:
            check_symbol(self.symbol, p + '.symbol')
        req(self.side is None or self.symbol is not None, p + '.side', 'a side only with its symbol')
        _refs(self.intent_refs, p + '.intent_refs', 'int')
        _refs(self.lot_refs, p + '.lot_refs', 'lot')
        _refs(self.position_refs, p + '.position_refs', 'pos')
        _refs(self.evidence, p + '.evidence', *ID_PREFIXES)
        req(len(self.detail) <= 160 and self.detail.isprintable(), p + '.detail', 'at most 160 printable characters')
