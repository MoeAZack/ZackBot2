"""Durable account-wide facts of one event log (PR #44, Codex P1-a / P1-b / P2-1).

Some ids name FACTS that must stay unique for the whole life of an account, not only inside one record:

- a result id names exactly one OrderResult (P1-b): the same id again is a no-op only for the identical fact; other
  content under the same id is a conflict (a superseding late final therefore has its own id and names the prior fact
  in OrderResult.supersedes_result_id);
- an incident id names exactly one Incident (P2-1), with the same rule;
- a venue trade id booked by an external close (EXCHANGE_EXTERNAL) is CONSUMED (P1-a): no later booking may cite it
  again, so one venue trade can never reduce ownership or book PnL twice.

FactLedger is the one implementation both layers apply (ruling 3): events.check_event_chain and the step-0
JournalGate. FactIndex is its durable, codec-encodable form: folded from the journal on restart (fold_facts), and the
form a snapshot can carry forward (check_event_chain(..., facts=...)), so the consumed set survives compaction.
"""
from __future__ import annotations

import hashlib
import json
import re

from .base import Record, check_ascii_text, check_id, record, req

SHA_RE = re.compile(r'[0-9a-f]{64}')


def fact_sha256(rec):
    """The canonical digest of a nested record (same canonical JSON rules as codec.dumps, without the envelope)."""
    from .codec import to_json                     # imported late: codec imports the event types, which import this
    body = json.dumps(to_json(rec), sort_keys=True, separators=(',', ':'), ensure_ascii=True, allow_nan=False)
    return hashlib.sha256(body.encode('utf-8')).hexdigest()


@record
class FactDigest(Record):
    fact_id: str                  # res_ / inc_
    sha256: str

    def _validate(self, p):
        check_id(self.fact_id, p + '.fact_id', 'res', 'inc')
        req(SHA_RE.fullmatch(self.sha256) is not None, p + '.sha256', 'lowercase hex SHA-256')


@record
class ConsumedTrade(Record):
    trade_id: str                 # the venue trade id (ExternalTrade.trade_id)
    result_id: str                # the EXCHANGE_EXTERNAL result that booked it

    def _validate(self, p):
        check_ascii_text(self.trade_id, p + '.trade_id', 64)
        check_id(self.result_id, p + '.result_id', 'res')


def _sorted_unique(values, key, path, what):
    keys = [key(v) for v in values]
    req(all(a < b for a, b in zip(keys, keys[1:])), path, f'{what} sorted and unique')


@record
class FactIndex(Record):
    results: tuple[FactDigest, ...]          # every result id ever journaled, with its digest (sorted by id)
    incidents: tuple[FactDigest, ...]        # every incident id ever journaled (sorted by id)
    trades: tuple[ConsumedTrade, ...]        # every venue trade an external close consumed (sorted by trade id)

    def _validate(self, p):
        _sorted_unique(self.results, lambda d: d.fact_id, p + '.results', 'result ids')
        _sorted_unique(self.incidents, lambda d: d.fact_id, p + '.incidents', 'incident ids')
        _sorted_unique(self.trades, lambda t: t.trade_id, p + '.trades', 'trade ids')


EMPTY_FACTS = FactIndex(results=(), incidents=(), trades=())


class FactLedger:
    """The working set of a FactIndex. `check(event)` raises InvalidRecord on a conflict and returns the commit."""

    def __init__(self, facts=None):
        facts = facts if facts is not None else EMPTY_FACTS
        req(isinstance(facts, FactIndex), 'facts', 'not a FactIndex')
        self.results = {d.fact_id: d.sha256 for d in facts.results}
        self.incidents = {d.fact_id: d.sha256 for d in facts.incidents}
        self.trades = {t.trade_id: t.result_id for t in facts.trades}

    def check(self, event, p='event'):
        from .events import IncidentRecorded, ResultObserved
        if isinstance(event, ResultObserved):
            r = event.result
            digest = fact_sha256(r)
            known = self.results.get(r.result_id)
            if known is not None:
                req(known == digest, p + '.result.result_id', f'result id {r.result_id} used for a different fact')
                return _noop                                  # the identical fact again: nothing new
            for t in r.external_trades:
                owner = self.trades.get(t.trade_id)
                req(owner is None, p + '.result.external_trades',
                    f'venue trade {t.trade_id} is already booked by {owner}')

            def commit_result():
                self.results[r.result_id] = digest
                for t in r.external_trades:
                    self.trades[t.trade_id] = r.result_id
            return commit_result
        if isinstance(event, IncidentRecorded):
            inc = event.incident
            digest = fact_sha256(inc)
            known = self.incidents.get(inc.incident_id)
            if known is not None:
                req(known == digest, p + '.incident.incident_id',
                    f'incident id {inc.incident_id} used for a different fact')
                return _noop

            def commit_incident():
                self.incidents[inc.incident_id] = digest
            return commit_incident
        return _noop

    def index(self):
        return FactIndex(results=tuple(FactDigest(fact_id=k, sha256=v) for k, v in sorted(self.results.items())),
                         incidents=tuple(FactDigest(fact_id=k, sha256=v) for k, v in sorted(self.incidents.items())),
                         trades=tuple(ConsumedTrade(trade_id=k, result_id=v) for k, v in sorted(self.trades.items())))


def _noop():
    return None


def fold_facts(events, facts=None):
    """The FactIndex after `events` (applied in order on top of `facts`). Raises InvalidRecord on a conflict."""
    ledger = FactLedger(facts)
    for n, ev in enumerate(events):
        ledger.check(ev, f'events[{n}]')()
    return ledger.index()
