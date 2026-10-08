"""Incidents (r3 DRAFT item 1): what went wrong or what an emergency action did, journaled as a typed record.

Step 0 dropped `incident_recorded`; S3 (reconciliation incidents) and the hard-HOLD emergency actions need to journal
them. An Incident names its kind from the reason registry (never an entry / exit reason: those are decisions and
fills), the records it concerns by opaque id (intents, lots, positions), the evidence it rests on (result /
reconciliation / decision / event ids) and its integer UTC-ms time. Its `detail` is NOT text: it is an
IncidentDetail from the closed DetailCode vocabulary (detail.py) with typed, bounded fields only, so no free text - and
so no secret, however split or disguised - can be journaled through an incident. `detail=None` means no detail. It is
journaled through events.IncidentRecorded and has no effect on ownership: it never changes a lot, an intent or a mode
by itself.

References are OPAQUE: each is a well-formed id of its family (int_ / lot_ / pos_, evidence res_ / rec_ / dec_ / evt_ /
inc_), but whether the referenced record EXISTS is the reconciler's job - neither the record nor check_event_chain
resolves them (a ghost id passes). The one structural rule: an incident never cites its own id as evidence.
"""
from __future__ import annotations

from .base import tag
from .base import Record, check_id, check_symbol, record, req
from .detail import IncidentDetail
from .orders import Side
from .reasons import ReasonCode

NOT_INCIDENT_KINDS = frozenset({'entry', 'exit'})        # those are decisions / fills, never incidents
EVIDENCE_PREFIXES = ('res', 'rec', 'dec', 'evt', 'inc')  # what an incident can rest on (never acct / pf / pos / ...)
MAX_REFS = 32                # PR #44 (Cowork 1): per reference tuple; the largest valid incident event is < 16 KiB


def _refs(values, path, *prefixes):
    req(len(values) <= MAX_REFS, path, f'at most {MAX_REFS} references')
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
    detail: IncidentDetail | None            # closed vocabulary (detail.py), never text; None when absent

    def _validate(self, p):
        check_id(self.incident_id, p + '.incident_id', 'inc')
        p = f'{p}[{tag(self.incident_id)}]'
        check_id(self.account_id, p + '.account_id', 'acct')
        req(self.kind.namespace not in NOT_INCIDENT_KINDS, p + '.kind', f'{self.kind} is a decision / fill reason')
        if self.symbol is not None:
            check_symbol(self.symbol, p + '.symbol')
        req(self.side is None or self.symbol is not None, p + '.side', 'a side only with its symbol')
        _refs(self.intent_refs, p + '.intent_refs', 'int')
        _refs(self.lot_refs, p + '.lot_refs', 'lot')
        _refs(self.position_refs, p + '.position_refs', 'pos')
        _refs(self.evidence, p + '.evidence', *EVIDENCE_PREFIXES)
        req(self.incident_id not in self.evidence, p + '.evidence', 'an incident is never its own evidence')
