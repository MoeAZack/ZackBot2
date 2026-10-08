"""Portfolio, Position, Lot, Fill and the orphan-cancel queue (rulings 3, 4, 8, 9, 10).

A Portfolio is everything one account owns; it is the unit NC-02 snapshots. Ownership is either KNOWN, with a typed
proof, or UNKNOWN. UNKNOWN means positions / intents / entry stops / orphan cancels are None - never empty - and every
accessor raises OwnershipUnknown instead of returning nothing. A KNOWN empty portfolio needs a proof that can establish
emptiness (a proven first run against a flat exchange, a journaled result chain or an owner adoption); a reconciliation
"match" alone never makes an empty candidate KNOWN. There is no legacy-import proof: NEWCORE starts flat (ruling 10).

The constructor checks every cross-record invariant: one position per symbol/side, unique ids and client ids, every lot /
protection / intent reference resolves to an owned record of the same account, each lot's fill ledger adds up to its
quantity, and the drain rule: outside ACTIVE every resting maker / armed trailing opening intent is cancelling, and no
intent created after the mode change is one the permitted-action table forbids.
"""
from __future__ import annotations

import enum
from decimal import Decimal

from .base import ZERO, Record, check_cid, check_id, check_symbol, non_negative, positive, record, req
from .errors import OwnershipUnknown
from .modes import EntriesMode, HoldKind, Op, Permission, permitted
from .orders import CANCEL_STATES, OrderIntent, OrderType, OwnerFamily, Purpose, Side
from .protection import Protection, ProtectionStatus, check_protection, protection_status
from .reasons import ReasonCode


class LotSource(enum.StrEnum):
    STRATEGY = 'strategy'
    MANUAL = 'manual'
    ADOPTED = 'adopted'          # explicitly adopted position or late fill (operator / reconciliation decision)


@record
class Fill(Record):
    """One booked execution of a lot. Opening fills carry an entry.* reason, closing fills an exit.* reason.
    Booked only from a FINAL OrderResult (intent_id) or an explicit adoption / reconcile decision (decision_id)."""
    at_ms: int
    reason: ReasonCode
    qty: Decimal
    price: Decimal
    fee: Decimal
    intent_id: str | None = None
    decision_id: str | None = None

    def _validate(self, p):
        req(self.reason.stage in ('entry', 'exit'), p + '.reason', 'a fill opens (entry.*) or closes (exit.*)')
        positive(self.qty, p + '.qty')
        positive(self.price, p + '.price')
        req((self.intent_id is None) != (self.decision_id is None), p + '.intent_id',
            'booked from exactly one source: a final order result or a decision')
        if self.intent_id is not None:
            check_id(self.intent_id, p + '.intent_id', 'int')
        else:
            check_id(self.decision_id, p + '.decision_id', 'dec')

    @property
    def opening(self):
        return self.reason.stage == 'entry'


@record
class Lot(Record):
    lot_id: str
    account_id: str
    symbol: str
    side: Side
    source: LotSource
    slot_id: str | None              # strategy slot; None for manual / adopted
    opened_at_ms: int
    qty: Decimal                     # > 0: a finished lot is removed, never stored at 0
    avg_price: Decimal
    initial_qty: Decimal
    max_qty: Decimal
    risk_distance: Decimal           # price distance entry -> stop at entry (legacy R)
    risk_usd: Decimal
    stop: Protection
    fills: tuple[Fill, ...]
    in_flight: str | None = None     # the ONE open add/reduce/close intent of this lot
    adopted_by: str | None = None    # dec_ of the adoption (ADOPTED only)
    tp1_done: bool = False
    ladder_done: tuple[int, ...] = ()
    adds_done: int = 0

    def _validate(self, p):
        p = f'{p}[{self.lot_id}]'
        check_id(self.lot_id, p + '.lot_id', 'lot')
        check_id(self.account_id, p + '.account_id', 'acct')
        check_symbol(self.symbol, p + '.symbol')
        req((self.source is LotSource.STRATEGY) == (self.slot_id is not None), p + '.slot_id', 'set exactly for a strategy lot')
        req((self.source is LotSource.ADOPTED) == (self.adopted_by is not None), p + '.adopted_by', 'set exactly when adopted')
        if self.adopted_by is not None:
            check_id(self.adopted_by, p + '.adopted_by', 'dec')
        for f in ('qty', 'avg_price', 'initial_qty', 'max_qty', 'risk_distance'):
            positive(getattr(self, f), f'{p}.{f}')
        non_negative(self.risk_usd, p + '.risk_usd')
        req(self.stop.owner_id == self.lot_id, p + '.stop', 'a lot carries its own stop record')
        if self.in_flight is not None:
            check_id(self.in_flight, p + '.in_flight', 'int')
        req(list(self.ladder_done) == sorted(set(self.ladder_done)) and all(i >= 0 for i in self.ladder_done),
            p + '.ladder_done', 'sorted unique level indexes')
        req(self.adds_done >= 0, p + '.adds_done', '>= 0')
        # the fill ledger is the truth the quantities must agree with (a quantity-only change is detectable)
        fills = self.fills
        req(len(fills) > 0 and fills[0].opening, p + '.fills', 'a lot starts with its opening fill')
        f0 = fills[0]
        req(f0.at_ms == self.opened_at_ms and f0.qty == self.initial_qty, p + '.initial_qty', 'differs from the opening fill')
        if self.source is LotSource.ADOPTED:
            req(f0.decision_id == self.adopted_by, p + '.fills[0]', 'an adopted lot opens with its adoption')
        else:
            req(f0.intent_id is not None, p + '.fills[0]', 'a traded lot opens from a final order result')
        run, top, prev = ZERO, ZERO, f0.at_ms
        for i, f in enumerate(fills):
            req(f.at_ms >= prev, f'{p}.fills[{i}].at_ms', 'fills out of time order')
            prev = f.at_ms
            run = run + f.qty if f.opening else run - f.qty
            req(run > 0, f'{p}.fills[{i}]', 'closes more than the lot held (a finished lot is never stored)')
            top = max(top, run)
        req(run == self.qty, p + '.qty', f'{self.qty} differs from the fill ledger ({run})')
        req(top == self.max_qty, p + '.max_qty', f'{self.max_qty} differs from the fill ledger peak ({top})')
        opens = [f.price for f in fills if f.opening]
        req(min(opens) <= self.avg_price <= max(opens), p + '.avg_price', 'outside the opening fill prices')


@record
class Position(Record):
    """All lots of one account / symbol / side (hedge-mode position). `qty` is derived, never stored."""
    symbol: str
    side: Side
    lots: tuple[Lot, ...]

    def _validate(self, p):
        p = f'{p}[{self.symbol}|{self.side}]'
        check_symbol(self.symbol, p + '.symbol')
        req(len(self.lots) > 0, p + '.lots', 'an empty position is not stored')
        req(all(x.symbol == self.symbol and x.side is self.side for x in self.lots), p + '.lots', 'lot of another symbol / side')

    @property
    def qty(self):
        return sum((x.qty for x in self.lots), ZERO)


@record
class OrphanCancel(Record):
    """An OWNED order (bot client id) that no record carries any more, queued for cancellation. Foreign orders never
    enter this queue: they are an owner decision (NC-02 A22)."""
    account_id: str
    symbol: str
    client_order_id: str
    queued_at_ms: int
    reason: ReasonCode
    attempts: int = 0

    def _validate(self, p):
        check_id(self.account_id, p + '.account_id', 'acct')
        check_symbol(self.symbol, p + '.symbol')
        check_cid(self.client_order_id, p + '.client_order_id')
        req(self.attempts >= 0, p + '.attempts', '>= 0')


class Ownership(enum.StrEnum):
    KNOWN = 'known'
    UNKNOWN = 'unknown'


class ProofKind(enum.StrEnum):
    FIRST_RUN_FLAT = 'first_run_flat'    # INIT: no store existed and a fresh exchange snapshot was flat
    JOURNAL = 'journal'                  # derived from a proven generation by the journaled event chain
    RECONCILED = 'reconciled'            # a reconciliation record matched a non-empty candidate
    OWNER_ADOPTED = 'owner_adopted'      # the owner resolved every difference (adopt / close / cancel), per item


EMPTY_PROOFS = frozenset({ProofKind.FIRST_RUN_FLAT, ProofKind.JOURNAL, ProofKind.OWNER_ADOPTED})


@record
class OwnershipProof(Record):
    kind: ProofKind
    at_ms: int
    reconciliation_id: str | None = None     # rec_: FIRST_RUN_FLAT (the flat snapshot) / RECONCILED
    decision_id: str | None = None           # dec_: OWNER_ADOPTED
    through_seq: int | None = None           # JOURNAL: the last applied event

    def _validate(self, p):
        k = self.kind
        need = {ProofKind.FIRST_RUN_FLAT: 'reconciliation_id', ProofKind.RECONCILED: 'reconciliation_id',
                ProofKind.OWNER_ADOPTED: 'decision_id', ProofKind.JOURNAL: 'through_seq'}[k]
        for f in ('reconciliation_id', 'decision_id', 'through_seq'):
            req((getattr(self, f) is not None) == (f == need), f'{p}.{f}', f'a {k} proof sets exactly {need}')
        if self.reconciliation_id is not None:
            check_id(self.reconciliation_id, p + '.reconciliation_id', 'rec')
        if self.decision_id is not None:
            check_id(self.decision_id, p + '.decision_id', 'dec')
        if self.through_seq is not None:
            req(self.through_seq >= 1, p + '.through_seq', '>= 1')


@record
class Portfolio(Record):
    account_id: str
    generation: int                          # strictly increasing per durable write (NC-02)
    ownership: Ownership
    proof: OwnershipProof | None             # None iff UNKNOWN
    entries_mode: EntriesMode
    mode_since_ms: int
    pause_reasons: tuple[ReasonCode, ...]    # non-empty iff not ACTIVE
    positions: tuple[Position, ...] | None   # None iff UNKNOWN (never "empty" by default)
    intents: tuple[OrderIntent, ...] | None  # open (non-final) intents: the write-ahead set
    entry_stops: tuple[Protection, ...] | None   # provisional stops of unresolved market entries
    orphan_cancels: tuple[OrphanCancel, ...] | None
    hold_kind: HoldKind | None = None        # set iff entries_mode is HOLD

    def _validate(self, p):
        check_id(self.account_id, p + '.account_id', 'acct')
        req(self.generation >= 0, p + '.generation', '>= 0')
        unknown = self.ownership is Ownership.UNKNOWN
        cols = (self.positions, self.intents, self.entry_stops, self.orphan_cancels)
        req(all(c is None for c in cols) if unknown else all(c is not None for c in cols), p + '.positions',
            'UNKNOWN ownership <=> positions / intents / entry_stops / orphan_cancels are None (unknown is never empty)')
        req((self.proof is None) == unknown, p + '.proof', 'KNOWN ownership needs a proof; UNKNOWN has none')
        req(not unknown or self.entries_mode is EntriesMode.HOLD, p + '.entries_mode', 'UNKNOWN ownership is HOLD')
        req((self.hold_kind is not None) == (self.entries_mode is EntriesMode.HOLD), p + '.hold_kind', 'set exactly in HOLD')
        req((len(self.pause_reasons) == 0) == (self.entries_mode is EntriesMode.ACTIVE), p + '.pause_reasons',
            'non-empty exactly when entries are not ACTIVE')
        req(len(set(self.pause_reasons)) == len(self.pause_reasons), p + '.pause_reasons', 'duplicate reason')
        if unknown:
            return
        _check_known(self, p)

    # ------------------------------------------------------------------------------------------------ accessors
    def _known(self):
        if self.ownership is Ownership.UNKNOWN:
            raise OwnershipUnknown('Portfolio.ownership', 'ownership is UNKNOWN: nothing can be listed as owned')
        return self

    @property
    def lots(self):
        return tuple(x for pos in self._known().positions for x in pos.lots)

    @property
    def is_empty(self):
        k = self._known()
        return not (k.positions or k.intents or k.entry_stops or k.orphan_cancels)

    def intents_by_id(self):
        return {i.intent_id: i for i in self._known().intents}

    def permits(self, purpose, op, *, one_shot=False):
        return permitted(self.entries_mode, self.hold_kind, purpose, op, one_shot=one_shot) is Permission.ALLOWED


def _check_known(pf, p):
    acct = pf.account_id
    if pf.is_empty:
        req(pf.proof.kind in EMPTY_PROOFS, p + '.proof',
            f'an empty portfolio is KNOWN only by {sorted(EMPTY_PROOFS)} (a trivially-empty candidate never auto-matches)')
    if pf.proof.kind is ProofKind.FIRST_RUN_FLAT:
        req(pf.generation == 0 and pf.is_empty, p + '.proof', 'FIRST_RUN_FLAT is generation 0 and empty')
    keys = [(x.symbol, x.side) for x in pf.positions]
    req(len(keys) == len(set(keys)), p + '.positions', 'one Position per symbol / side')
    lots = {}
    for x in pf.lots:
        req(x.lot_id not in lots, f'{p}.lot[{x.lot_id}]', 'duplicate lot id')
        req(x.account_id == acct, f'{p}.lot[{x.lot_id}].account_id', 'lot of another account')
        lots[x.lot_id] = x
    intents = {}
    cids = set()
    for it in pf.intents:
        ip = f'{p}.intent[{it.intent_id}]'
        req(it.intent_id not in intents, ip, 'duplicate intent id')
        req(it.account_id == acct, ip + '.account_id', 'intent of another account')
        for c in it.client_ids:
            req(c not in cids, ip + '.client_order_id', 'duplicate client order id')
            cids.add(c)
        intents[it.intent_id] = it
    for o in pf.orphan_cancels:
        req(o.account_id == acct, f'{p}.orphan[{o.client_order_id}].account_id', 'orphan of another account')
        req(o.client_order_id not in cids, f'{p}.orphan[{o.client_order_id}]', 'an orphan is not also owned by a record')
        cids.add(o.client_order_id)
    # LOT family: every add / reduce / close intent is THE in-flight order of an owned lot, and vice versa
    for x in lots.values():
        if x.in_flight is not None:
            it = intents.get(x.in_flight)
            req(it is not None and it.family is OwnerFamily.LOT and it.owner_id == x.lot_id, f'{p}.lot[{x.lot_id}].in_flight',
                'names no durable add / reduce / close intent of this lot')
    for it in intents.values():
        ip = f'{p}.intent[{it.intent_id}]'
        if it.family is OwnerFamily.LOT:
            owner = lots.get(it.owner_id)
            req(owner is not None and owner.in_flight == it.intent_id, ip + '.owner_id', 'not the in-flight intent of an owned lot')
            req((owner.symbol, owner.side) == (it.symbol, it.side), ip + '.symbol', 'differs from its lot')
    # PROTECTION family: one protection record per protective intent, derived lifecycle consistent
    entry_stop_owners = set()
    carried = {}
    for s in pf.entry_stops:
        sp = f'{p}.entry_stop[{s.owner_id}]'
        it = intents.get(s.owner_id)
        req(it is not None and it.purpose is Purpose.ENTRY and it.order_type is OrderType.MARKET, sp,
            'a provisional stop protects an open market ENTRY of this portfolio')
        req(s.owner_id not in entry_stop_owners, sp, 'two provisional stops for one entry')
        entry_stop_owners.add(s.owner_id)
        req(it.seen_qty is not None and s.qty <= it.seen_qty, sp + '.qty', 'larger than the unresolved size it protects')
        check_protection(s, intents, it.side, it.symbol, sp)
        if s.order is not None:
            carried[s.order] = s
    for x in lots.values():
        sp = f'{p}.lot[{x.lot_id}].stop'
        check_protection(x.stop, intents, x.side, x.symbol, sp)
        req(x.stop.order is None or x.stop.order not in carried, sp + '.order', 'one stop order carries two protections')
        if x.stop.order is not None:
            carried[x.stop.order] = x.stop
        status = protection_status(x.stop, intents)
        req(x.stop.qty == x.qty or x.in_flight is not None or status in (
            ProtectionStatus.NEEDS_PLACEMENT, ProtectionStatus.RELEASING, ProtectionStatus.MISSING_RESTORING),
            sp + '.qty', 'a placed stop covers exactly the lot qty (a resize is a new placement)')
    for it in intents.values():
        if it.purpose is Purpose.PROTECT:
            req(it.intent_id in carried, f'{p}.intent[{it.intent_id}]', 'a protective order no protection record carries')
    # drain (ruling 9) and the permitted-action table (hard HOLD)
    if pf.entries_mode is not EntriesMode.ACTIVE:
        for it in intents.values():
            ip = f'{p}.intent[{it.intent_id}]'
            if it.pullable:
                req(it.state in CANCEL_STATES, ip + '.state',
                    f'a resting / armed opening intent must be cancelling while entries are {pf.entries_mode}')
            elif it.created_at_ms > pf.mode_since_ms and it.state not in CANCEL_STATES:
                req(pf.permits(it.purpose, Op.PLACE, one_shot=it.authorized_by is not None), ip,
                    f'a {it.purpose} intent created after entries became {pf.entries_mode} is not permitted')


# ----------------------------------------------------------------------------------------------------------- queries
def owned_client_ids(pf):
    """Every client order id this portfolio owns (intents, algo fallbacks, orphan queue). Raises OwnershipUnknown for an
    UNKNOWN portfolio: unknown is never an empty set."""
    k = pf._known()
    return frozenset(c for it in k.intents for c in it.client_ids) | frozenset(o.client_order_id for o in k.orphan_cancels)


def ownership_families(pf):
    """{OwnerFamily: (client ids...)}: the explicit ownership families. Raises OwnershipUnknown when UNKNOWN."""
    k = pf._known()
    out = {f: [] for f in OwnerFamily}
    for it in k.intents:
        out[it.family].extend(it.client_ids)
    out[OwnerFamily.ORPHAN].extend(o.client_order_id for o in k.orphan_cancels)
    return {f: tuple(v) for f, v in out.items()}


def entry_blocking_protections(pf):
    """Protections whose derived status blocks new entries on their symbol/side (replaces legacy stop_missing_block)."""
    k = pf._known()
    intents = k.intents_by_id()
    return tuple(s for s in [x.stop for x in k.lots] + list(k.entry_stops)
                 if protection_status(s, intents) not in (ProtectionStatus.OWNED_CONFIRMED, ProtectionStatus.OWNED_UNVERIFIED))


def check_account_portfolio(account, pf):
    """The portfolio belongs to this account, and an unconfirmed / rotating / mismatched binding keeps entries non-active.
    Equal positions prove nothing about identity."""
    req(pf.account_id == account.account_id, 'Portfolio.account_id', 'belongs to another account')
    if not account.entries_allowed:
        req(pf.entries_mode is not EntriesMode.ACTIVE, 'Portfolio.entries_mode',
            f'entries cannot be ACTIVE while the binding is {account.binding_state}')


