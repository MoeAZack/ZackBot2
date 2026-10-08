"""Reconciliation (acceptance draft section 1 "Match", A08, A19, A22; design 4.4). Pure: no IO, no clock.

The exchange is never called by the store: the caller hands it an ExchangeView whose snapshot() returns an
ExchangeSnapshot (or raises ExchangeDown). A verdict compares ONE candidate portfolio with ONE snapshot:

  MATCH needs all of (R-MATCH):
   1. identity: the account binding is CONFIRMED and the snapshot was taken under the same key digest;
   2. freshness: 0 <= now - taken_ms < MATCH_MAX_AGE_MS (checked again at promotion);
   3. per symbol / side the aggregate owned quantity (sum of the lots) equals the exchange position within the venue
      step tolerance (abs(diff) < step / 2);
   4. every live owned intent that was sent is on the exchange (by its client ids) and the protective quantity covers
      the position; a written-but-never-sent intent is an item (it can be closed NOT_SENT, never assumed);
   5. no unowned position and no unowned open order (foreign orders are owner items, never auto-handled: A22).
  The ownership of the candidate must be proven: UNKNOWN is never a match; KNOWN_EMPTY matches only a flat exchange and,
  outside INIT, only with the owner's explicit confirmation (trivially-empty, A08).
Each difference is a HoldItem (scope account / symbol:side, cause, opaque ref): the per-item list of rule 7.
"""
from __future__ import annotations

import enum
from dataclasses import dataclass
from decimal import Decimal

from newcore.domain import BindingState, IntentState, Ownership

MATCH_MAX_AGE_MS = 5_000                    # proposal in the acceptance draft; Codex sets it (open question 3)


class ExchangeDown(Exception):
    """No exchange snapshot can be taken: no INIT, no promotion, no reconciliation claim (rule 5)."""


@dataclass(frozen=True)
class ExPosition:
    symbol: str
    side: str                               # 'LONG' / 'SHORT'
    qty: Decimal


@dataclass(frozen=True)
class ExOrder:
    client_id: str                          # opaque; compared for equality only
    symbol: str
    side: str
    qty: Decimal
    reduce_only: bool
    kind: str                               # 'stop' | 'limit' | 'market'
    status: str                             # 'working' | 'triggered'


@dataclass(frozen=True)
class ExchangeSnapshot:
    key_digest: str                         # the identity the venue reports for the key in use
    taken_ms: int
    positions: tuple
    orders: tuple
    steps: dict                             # symbol -> Decimal quantity step (venue rules)

    @property
    def flat(self):
        return not any(p.qty for p in self.positions) and not self.orders

    def canonical(self):
        """A stable text form for hashing into the reconciliation record (no secrets: ids, quantities, states)."""
        ps = sorted(f'{p.symbol}:{p.side}:{p.qty}' for p in self.positions if p.qty)
        os_ = sorted(f'{o.client_id}:{o.symbol}:{o.side}:{o.qty}:{o.reduce_only}:{o.kind}:{o.status}'
                     for o in self.orders)
        return f'{self.key_digest}|{self.taken_ms}|{";".join(ps)}|{";".join(os_)}'.encode('ascii')


class VerdictKind(enum.StrEnum):
    MATCH = 'match'
    DIFF = 'diff'
    IDENTITY = 'identity'                   # binding unconfirmed or another account: never auto-promotes
    STALE = 'stale'
    UNKNOWN_OWNERSHIP = 'unknown_ownership'
    TRIVIALLY_EMPTY = 'trivially_empty'     # an empty candidate whose emptiness is not proven here


@dataclass(frozen=True)
class HoldItem:
    scope: str                              # 'account' or '<SYMBOL>:<SIDE>'
    cause: str
    ref: str = ''

    def doc(self):
        return {'scope': self.scope, 'cause': self.cause, 'ref': self.ref}


@dataclass(frozen=True)
class Verdict:
    kind: VerdictKind
    items: tuple

    @property
    def match(self):
        return self.kind is VerdictKind.MATCH


SENT_STATES = frozenset({IntentState.SUBMITTED, IntentState.WORKING, IntentState.UNKNOWN, IntentState.CANCELLING})


def verdict(account, portfolio, snap, now_ms, *, empty_proven=False, max_age_ms=MATCH_MAX_AGE_MS):
    """The R-MATCH verdict of `portfolio` against `snap`. `empty_proven`: a KNOWN_EMPTY candidate whose emptiness is
    proven for this decision (INIT on a flat exchange, or the owner's explicit adoption of "empty")."""
    if account.binding_state is not BindingState.CONFIRMED or snap.key_digest != account.binding.key_digest:
        return Verdict(VerdictKind.IDENTITY, (HoldItem('account', 'identity', 'binding unconfirmed or another key'),))
    age = now_ms - snap.taken_ms
    if not 0 <= age < max_age_ms:
        return Verdict(VerdictKind.STALE, (HoldItem('account', 'stale_snapshot', f'{age} ms'),))
    if portfolio is None or portfolio.ownership is Ownership.UNKNOWN:
        return Verdict(VerdictKind.UNKNOWN_OWNERSHIP, (HoldItem('account', 'ownership_unknown'),) +
                       _exchange_items(snap, set()))
    if portfolio.ownership is Ownership.KNOWN_EMPTY:
        if not snap.flat:
            return Verdict(VerdictKind.DIFF, (HoldItem('account', 'empty_candidate_vs_exchange'),) +
                           _exchange_items(snap, set()))
        if not empty_proven:
            return Verdict(VerdictKind.TRIVIALLY_EMPTY, (HoldItem('account', 'trivially_empty'),))
        return Verdict(VerdictKind.MATCH, ())
    items = []
    owned = {}
    for pos in portfolio.positions:
        owned[(pos.symbol, str(pos.side))] = sum((lot.qty for lot in pos.lots), Decimal(0))
    expos = {}
    for p in snap.positions:
        if p.qty:
            expos[(p.symbol, p.side)] = expos.get((p.symbol, p.side), Decimal(0)) + p.qty
    for key in sorted(set(owned) | set(expos)):
        o, e = owned.get(key, Decimal(0)), expos.get(key, Decimal(0))
        step = snap.steps.get(key[0])
        scope = f'{key[0]}:{key[1]}'
        if key not in owned:
            items.append(HoldItem(scope, 'unowned_position', str(e)))
        elif step is None:
            items.append(HoldItem(scope, 'no_step_rule'))
        elif abs(o - e) * 2 >= step:
            items.append(HoldItem(scope, 'quantity_differs', f'owned {o} exchange {e}'))
    by_cid = {o.client_id: o for o in snap.orders}
    owned_cids = set()
    for it in portfolio.intents:
        owned_cids.update(it.client_ids)
        scope = f'{it.symbol}:{it.side}'
        if it.state is IntentState.DURABLE:
            items.append(HoldItem(scope, 'durable_not_sent', it.intent_id))
        elif it.state in SENT_STATES and not any(c in by_cid for c in it.client_ids):
            items.append(HoldItem(scope, 'owned_order_missing', it.intent_id))
    for key, qty in owned.items():
        cover = sum((o.qty for o in snap.orders if (o.symbol, o.side) == key and o.reduce_only and o.kind == 'stop'
                     and o.client_id in owned_cids), Decimal(0))
        if cover < qty:
            items.append(HoldItem(f'{key[0]}:{key[1]}', 'protection_short', f'{cover} < {qty}'))
    stops = {(o.symbol, o.side) for o in snap.orders if o.client_id in owned_cids and o.kind == 'stop'}
    for key in sorted(stops - set(owned)):
        items.append(HoldItem(f'{key[0]}:{key[1]}', 'protection_without_lot'))
    items.extend(i for i in _exchange_items(snap, owned_cids) if i.cause == 'unowned_order')
    return Verdict(VerdictKind.MATCH if not items else VerdictKind.DIFF, tuple(items))


def _exchange_items(snap, owned_cids):
    out = [HoldItem(f'{p.symbol}:{p.side}', 'unowned_position', str(p.qty)) for p in snap.positions if p.qty]
    out += [HoldItem(f'{o.symbol}:{o.side}', 'unowned_order', o.client_id) for o in snap.orders
            if o.client_id not in owned_cids]
    return tuple(out)


def same_verdict(a, b):
    return a.kind is b.kind and a.items == b.items


__all__ = ['MATCH_MAX_AGE_MS', 'ExchangeDown', 'ExPosition', 'ExOrder', 'ExchangeSnapshot', 'VerdictKind', 'HoldItem',
           'Verdict', 'verdict', 'same_verdict']
