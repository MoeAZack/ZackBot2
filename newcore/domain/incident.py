"""Incidents (r3 DRAFT item 1): what went wrong or what an emergency action did, journaled as a typed record.

Step 0 dropped `incident_recorded`; S3 (reconciliation incidents) and the hard-HOLD emergency actions need to journal
them. An Incident names its kind from the reason registry (never an entry / exit reason: those are decisions and
fills), the records it concerns by opaque id (intents, lots, positions), the evidence it rests on (result /
reconciliation / decision / event ids) and its integer UTC-ms time. Free text is display only. It is journaled through
events.IncidentRecorded and has no effect on ownership: it never changes a lot, an intent or a mode by itself.
"""
from __future__ import annotations

import re

from .base import Record, check_ascii_text, check_id, check_symbol, record, req
from .orders import Side
from .reasons import ReasonCode

NOT_INCIDENT_KINDS = frozenset({'entry', 'exit'})        # those are decisions / fills, never incidents
EVIDENCE_PREFIXES = ('res', 'rec', 'dec', 'evt', 'inc')  # what an incident can rest on (never acct / pf / pos / ...)
MAX_REFS = 32                # PR #44 (Cowork 1): per reference tuple; the largest valid incident event is < 16 KiB
MAX_DETAIL = 160
# PR #44 (Cowork 2): display text is constrained, never scrubbed (a record is never silently altered). A run of 20+
# key / token characters is how API keys, secrets, bot tokens and base64 blobs look - refused, so a secret cannot be
# journaled through `detail`. Ids belong in the *_refs fields, not in the text.
KEY_SHAPED = re.compile(r'[A-Za-z0-9+/=_-]{20,}')


def _refs(values, path, *prefixes):
    req(len(values) <= MAX_REFS, path, f'at most {MAX_REFS} references')
    for i, v in enumerate(values):
        check_id(v, f'{path}[{i}]', *prefixes)
    req(len(set(values)) == len(values), path, 'duplicate reference')


def check_detail(v, path):
    """Optional display text: None, or 1..MAX_DETAIL printable ASCII characters, not blank, with no key-shaped token."""
    if v is None:
        return
    check_ascii_text(v, path, MAX_DETAIL)
    req(KEY_SHAPED.search(v) is None, path, 'a key-shaped token (20+ key characters) is never journaled as text')


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
    detail: str | None                       # display only (check_detail), never parsed; None when absent

    def _validate(self, p):
        check_id(self.incident_id, p + '.incident_id', 'inc')
        p = f'{p}[{self.incident_id}]'
        check_id(self.account_id, p + '.account_id', 'acct')
        req(self.kind.namespace not in NOT_INCIDENT_KINDS, p + '.kind', f'{self.kind} is a decision / fill reason')
        if self.symbol is not None:
            check_symbol(self.symbol, p + '.symbol')
        req(self.side is None or self.symbol is not None, p + '.side', 'a side only with its symbol')
        _refs(self.intent_refs, p + '.intent_refs', 'int')
        _refs(self.lot_refs, p + '.lot_refs', 'lot')
        _refs(self.position_refs, p + '.position_refs', 'pos')
        _refs(self.evidence, p + '.evidence', *EVIDENCE_PREFIXES)
        check_detail(self.detail, p + '.detail')
