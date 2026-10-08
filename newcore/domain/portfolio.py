"""Portfolio, Position, Lot and Fill: the account-keyed ownership aggregate (contract 2, invariants 1, 4, 5, 8, 9, 10).

Trust is explicit. `ownership` is UNKNOWN, KNOWN or KNOWN_EMPTY:

- UNKNOWN: positions / intents / entry stops are None - never empty - and every accessor raises OwnershipUnknown. The
  entries mode is HOLD.
- KNOWN_EMPTY: nothing is owned, proven by a FLAT_SNAPSHOT proof: a fresh flat exchange snapshot taken under the
  confirmed binding (key digest recorded) and accepted by the reconciliation gate (rec_ id). `check_account_portfolio`
  checks the binding is that confirmed one. An empty portfolio is never just "KNOWN".
  (Contract invariant 1; the r2 brief's "first-run provenance + flat exchange" is this flat-snapshot proof.)
- KNOWN: something is owned, proven by the journal, a reconciliation match or an owner adoption.
There is no legacy-import proof: NEWCORE starts flat.

The constructor checks every cross-record invariant: unique ids and client ids, one position per symbol/side, every lot /
protection / intent reference resolves to a record of the same account, each lot's fill ledger adds up to its quantity,
protection never exceeds exposure, an owned order that no record carries any more is an ORPHAN and may only be cancelling,
and the drain rule: outside ACTIVE every resting maker / armed trailing opening intent is cancelling, and no intent created
after the mode change is one the permitted-action table forbids (manual entries get no exception).
"""
from __future__ import annotations

import enum
from decimal import Decimal

from .base import CTX, ZERO, Record, check_id, check_symbol, check_text, non_negative, positive, record, req
from .errors import OwnershipUnknown
from .modes import EntriesMode, HoldKind, Op, Permission, permitted
from .orders import LIVE, IntentState, OrderIntent, OrderType, OwnerFamily, OwnerKind, Purpose, Side
from .protection import PROTECTING, Protection, check_protection, protection_status, target_coverage
from .reasons import ReasonCode


class LotSource(enum.StrEnum):
    STRATEGY = 'strategy'
    MANUAL = 'manual'
    ADOPTED = 'adopted'          # explicitly adopted position or late fill (operator / reconciliation decision)


@record
class Fill(Record):
    """One booked execution of a lot. Opening fills carry an entry.* reason, closing fills an exit.* reason.
    Booked only from a FINAL OrderResult (result_id) or an explicit adoption / reconcile decision (decision_id)."""
    at_ms: int
    reason: ReasonCode
    qty: Decimal
    price: Decimal
    fee: Decimal
    result_id: str | None
    decision_id: str | None

    def _validate(self, p):
        req(self.reason.namespace in ('entry', 'exit'), p + '.reason', 'a fill opens (entry.*) or closes (exit.*)')
        positive(self.qty, p + '.qty')
        positive(self.price, p + '.price')
        req((self.result_id is None) != (self.decision_id is None), p + '.result_id',
            'booked from exactly one source: a final order result or a decision')
        if self.result_id is not None:
            check_id(self.result_id, p + '.result_id', 'res')
        else:
            check_id(self.decision_id, p + '.decision_id', 'dec')

    @property
    def opening(self):
        return self.reason.namespace == 'entry'


@record
class Lot(Record):
    lot_id: str
    account_id: str
    symbol: str
    side: Side
    source: LotSource
    slot_id: str | None              # strategy slot; None for manual / adopted
    timeframe: str | None            # strategy timeframe of the slot (e.g. '4h'); None for manual / adopted
    opened_at_ms: int
    qty: Decimal                     # > 0: a finished lot is removed, never stored at 0
    avg_price: Decimal
    initial_qty: Decimal
    max_qty: Decimal
    risk_distance: Decimal           # price distance entry -> stop at entry (legacy R)
    risk_usd: Decimal
    stop: Protection
    fills: tuple[Fill, ...]
    in_flight: str | None     # the ONE open add / reduce / close intent of this lot
    adopted_by: str | None    # dec_ of the adoption (ADOPTED only)
    tp1_done: bool
    ladder_done: tuple[int, ...]
    adds_done: int

    def _validate(self, p):
        p = f'{p}[{self.lot_id}]'
        check_id(self.lot_id, p + '.lot_id', 'lot')
        check_id(self.account_id, p + '.account_id', 'acct')
        check_symbol(self.symbol, p + '.symbol')
        strategy = self.source is LotSource.STRATEGY
        req(strategy == (self.slot_id is not None) == (self.timeframe is not None), p + '.slot_id',
            'slot and timeframe are set exactly for a strategy lot')
        if strategy:
            check_text(self.slot_id, p + '.slot_id', 32)
            check_text(self.timeframe, p + '.timeframe', 8)
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
            req(f0.result_id is not None, p + '.fills[0]', 'a traded lot opens from a final order result')
        run, top, prev = ZERO, ZERO, f0.at_ms
        for i, f in enumerate(fills):
            req(f.at_ms >= prev, f'{p}.fills[{i}].at_ms', 'fills out of time order')
            prev = f.at_ms
            run = CTX.add(run, f.qty) if f.opening else CTX.subtract(run, f.qty)
            req(run > 0, f'{p}.fills[{i}]', 'closes more than the lot held (a finished lot is never stored)')
            top = max(top, run)
        req(run == self.qty, p + '.qty', f'{self.qty} differs from the fill ledger ({run})')
        req(top == self.max_qty, p + '.max_qty', f'{self.max_qty} differs from the fill ledger peak ({top})')
        opens = [f.price for f in fills if f.opening]
        req(min(opens) <= self.avg_price <= max(opens), p + '.avg_price', 'outside the opening fill prices')


@record
class Position(Record):
    """All lots of one account / symbol / side (hedge-mode position). `qty` is derived, never stored."""
    position_id: str
    symbol: str
    side: Side
    lots: tuple[Lot, ...]

    def _validate(self, p):
        p = f'{p}[{self.symbol}|{self.side}]'
        check_id(self.position_id, p + '.position_id', 'pos')
        check_symbol(self.symbol, p + '.symbol')
        req(len(self.lots) > 0, p + '.lots', 'an empty position is not stored')
        req(len({x.lot_id for x in self.lots}) == len(self.lots), p + '.lots', 'duplicate lot id (qty would double count)')
        ordered = tuple(sorted(self.lots, key=lambda x: x.lot_id))     # Codex ruling 4: one canonical lot order
        if ordered != self.lots:
            object.__setattr__(self, 'lots', ordered)
        req(all(x.symbol == self.symbol and x.side is self.side for x in self.lots), p + '.lots', 'lot of another symbol / side')

    @property
    def qty(self):
        out = ZERO
        for x in self.lots:
            out = CTX.add(out, x.qty)
        return out


class Ownership(enum.StrEnum):
    UNKNOWN = 'unknown'
    KNOWN_EMPTY = 'known_empty'
    KNOWN = 'known'


class ProofKind(enum.StrEnum):
    FLAT_SNAPSHOT = 'flat_snapshot'      # KNOWN_EMPTY: fresh flat exchange snapshot under the confirmed binding, gate-accepted
    JOURNAL = 'journal'                  # KNOWN: derived from a proven generation by the journaled event chain
    RECONCILED = 'reconciled'            # KNOWN: a reconciliation record matched this non-empty candidate
    OWNER_ADOPTED = 'owner_adopted'      # KNOWN: the owner resolved every difference (adopt / close / cancel), per item


@record
class OwnershipProof(Record):
    kind: ProofKind
    at_ms: int                               # FLAT_SNAPSHOT: when the flat snapshot was taken
    reconciliation_id: str | None     # rec_: FLAT_SNAPSHOT / RECONCILED
    key_digest: str | None            # FLAT_SNAPSHOT: the confirmed binding the snapshot was taken under
    decision_id: str | None           # dec_: OWNER_ADOPTED
    through_sequence: int | None           # JOURNAL: the last applied event

    def _validate(self, p):
        need = {ProofKind.FLAT_SNAPSHOT: {'reconciliation_id', 'key_digest'}, ProofKind.RECONCILED: {'reconciliation_id'},
                ProofKind.OWNER_ADOPTED: {'decision_id'}, ProofKind.JOURNAL: {'through_sequence'}}[self.kind]
        for f in ('reconciliation_id', 'key_digest', 'decision_id', 'through_sequence'):
            req((getattr(self, f) is not None) == (f in need), f'{p}.{f}', f'a {self.kind} proof sets exactly {sorted(need)}')
        if self.reconciliation_id is not None:
            check_id(self.reconciliation_id, p + '.reconciliation_id', 'rec')
        if self.key_digest is not None:
            req(len(self.key_digest) == 16 and all(c in '0123456789abcdef' for c in self.key_digest), p + '.key_digest',
                'not 16 lowercase hex')
        if self.decision_id is not None:
            check_id(self.decision_id, p + '.decision_id', 'dec')
        if self.through_sequence is not None:
            req(self.through_sequence >= 1, p + '.through_sequence', '>= 1')


@record
class Portfolio(Record):
    portfolio_id: str
    account_id: str
    generation: int                          # strictly increasing per durable write (NC-02)
    ownership: Ownership
    proof: OwnershipProof | None             # None iff UNKNOWN
    entries_mode: EntriesMode
    mode_since_ms: int
    pause_reasons: tuple[ReasonCode, ...]    # non-empty iff not ACTIVE
    positions: tuple[Position, ...] | None   # None iff UNKNOWN (never "empty" by default)
    intents: tuple[OrderIntent, ...] | None  # open (live) intents: the write-ahead set, orphan cancels included
    entry_stops: tuple[Protection, ...] | None   # provisional stops of unresolved market entries
    hold_kind: HoldKind | None        # set iff entries_mode is HOLD

    def _validate(self, p):
        check_id(self.portfolio_id, p + '.portfolio_id', 'pf')
        check_id(self.account_id, p + '.account_id', 'acct')
        req(self.generation >= 0, p + '.generation', '>= 0')
        unknown = self.ownership is Ownership.UNKNOWN
        cols = (self.positions, self.intents, self.entry_stops)
        req(all(c is None for c in cols) if unknown else all(c is not None for c in cols), p + '.positions',
            'UNKNOWN ownership <=> positions / intents / entry_stops are None (unknown is never empty)')
        req((self.proof is None) == unknown, p + '.proof', 'trusted ownership needs a proof; UNKNOWN has none')
        req(not unknown or self.entries_mode is EntriesMode.HOLD, p + '.entries_mode', 'UNKNOWN ownership is HOLD')
        req((self.hold_kind is not None) == (self.entries_mode is EntriesMode.HOLD), p + '.hold_kind', 'set exactly in HOLD')
        req((len(self.pause_reasons) == 0) == (self.entries_mode is EntriesMode.ACTIVE), p + '.pause_reasons',
            'non-empty exactly when entries are not ACTIVE')
        req(len(set(self.pause_reasons)) == len(self.pause_reasons), p + '.pause_reasons', 'duplicate reason')
        if self.positions is not None:                  # Codex P2: one canonical order (UNKNOWN stays None)
            _canonical(self, 'positions', lambda x: (x.symbol, x.side.value))
            _canonical(self, 'intents', lambda x: x.intent_id)
        req((self.hold_kind is HoldKind.DURABILITY_UNAVAILABLE) <= (ReasonCode.RECOVERY_DURABILITY_UNAVAILABLE in
                                                                    self.pause_reasons), p + '.pause_reasons',
            'a hard HOLD names its cause')
        if unknown:
            return
        empty = not (self.positions or self.intents or self.entry_stops)
        req(empty == (self.ownership is Ownership.KNOWN_EMPTY), p + '.ownership',
            'empty <=> KNOWN_EMPTY (an empty portfolio is never just KNOWN; KNOWN_EMPTY owns nothing)')
        req((self.proof.kind is ProofKind.FLAT_SNAPSHOT) == empty, p + '.proof',
            'KNOWN_EMPTY is proven exactly by a flat snapshot; KNOWN by journal / reconciliation / adoption')
        _check_known(self, p)

    # ------------------------------------------------------------------------------------------------ accessors
    def _known(self):
        if self.ownership is Ownership.UNKNOWN:
            raise OwnershipUnknown('Portfolio.ownership', 'ownership is UNKNOWN: nothing can be listed as owned')
        return self

    @property
    def lots(self):
        return tuple(x for pos in self._known().positions for x in pos.lots)

    def intents_by_id(self):
        return {i.intent_id: i for i in self._known().intents}

    def permits(self, purpose, op, *, one_shot=False):
        return permitted(self.entries_mode, self.hold_kind, purpose, op, one_shot=one_shot) is Permission.ALLOWED


def _canonical(rec, name, key):
    """Sort a known collection by its stable domain key (duplicates are still refused by _check_known)."""
    ordered = tuple(sorted(getattr(rec, name), key=key))
    if ordered != getattr(rec, name):
        object.__setattr__(rec, name, ordered)


def _check_known(pf, p):
    acct = pf.account_id
    keys = [(x.symbol, x.side) for x in pf.positions]
    req(len(keys) == len(set(keys)), p + '.positions', 'one Position per symbol / side')
    pos_ids = [x.position_id for x in pf.positions]
    req(len(pos_ids) == len(set(pos_ids)), p + '.positions', 'duplicate position id')
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
        req(it.state in LIVE, ip + '.state', f'{it.state} is not a live state (planned / terminal intents are not owned)')
        for c in it.client_ids:
            req(c not in cids, ip + '.client_order_id', 'duplicate client order id')
            cids.add(c)
        intents[it.intent_id] = it
    # references agree (contract invariant 11): every owner resolves inside THIS portfolio, same symbol / side
    for it in intents.values():
        ip = f'{p}.intent[{it.intent_id}].owner_id'
        own = it.owner_id
        if own is None:
            continue
        if it.orphan:
            req(own == pf.portfolio_id, ip, 'an orphan cancel is owned by this portfolio aggregate')
            continue
        by_lot = it.owner_kind is OwnerKind.LOT
        owner = lots.get(own) if by_lot else intents.get(own)
        req(owner is not None, ip, 'names no lot / entry of this portfolio (a KNOWN portfolio has no orphan reference)')
        if not by_lot:
            req(owner.purpose is Purpose.ENTRY and owner.order_type is OrderType.MARKET, ip,
                'only an unresolved market ENTRY owns a provisional stop')
        req((owner.symbol, owner.side) == (it.symbol, it.side), ip, 'owner of another symbol / side')
    # cancel-replace links (Codex P1 on af4e5f3): a REDUCE / CLOSE successor names its predecessor explicitly by
    # replaces_intent_id - never inferred from decisions or order. Exactly one link per lot: same account / symbol /
    # side / lot, both reduce / close, the predecessor CANCELLING, the successor live (so no chain can form: a middle
    # link would have to be both cancelling and live).
    preds, linked_lots = {}, set()
    for it in intents.values():
        old_id = it.replaces_intent_id
        if old_id is None:
            continue
        ip = f'{p}.intent[{it.intent_id}].replaces_intent_id'
        old = intents.get(old_id)
        req(old is not None, ip, 'names no live predecessor of this portfolio')
        req(old.purpose in (Purpose.REDUCE, Purpose.CLOSE) and old.owner_kind is OwnerKind.LOT, ip,
            'the predecessor is not a lot reduce / close')
        req((old.account_id, old.symbol, old.side, old.owner_id) == (it.account_id, it.symbol, it.side, it.owner_id), ip,
            'the predecessor belongs to another account / instrument / side / lot')
        req(old.state is IntentState.CANCELLING, ip, 'the predecessor of a cancel-replace must be CANCELLING')
        req(it.state is not IntentState.CANCELLING, ip, 'the successor of a cancel-replace must be live, not cancelling')
        req(old_id not in preds and it.owner_id not in linked_lots, ip, 'at most one cancel-replace link per lot')
        preds[old_id] = it.intent_id
        linked_lots.add(it.owner_id)
    # reducing intents never exceed what they could reduce (Cowork S05): per lot, the live REDUCE / CLOSE legs together
    # <= the lot qty. A linked pair is ONE leg (its successor; the CANCELLING predecessor is superseded). A cancelling
    # leg - it can still fill - counts, at most the lot qty (reduce-only: it cannot reduce more than the lot). A live
    # leg larger than its lot (the survivor after part of the lot closed) must be retired: CANCELLING, or replaced.
    # Cumulative fills of both legs stay bounded by the lot: the fill ledger refuses closing more than is held, and
    # when the lot closes its remaining intents become portfolio-owned cancel-only work. Portfolio-owned orphans are
    # left out (cancel-only by construction; Cowork re-check of S05).
    per_lot = {}
    for it in intents.values():
        if it.purpose not in (Purpose.REDUCE, Purpose.CLOSE) or it.owner_kind is not OwnerKind.LOT or it.intent_id in preds:
            continue
        held = lots[it.owner_id].qty
        if it.state is IntentState.CANCELLING:
            leg = min(it.qty, held)
        else:
            req(it.qty <= held, f'{p}.intent[{it.intent_id}].qty',
                f'a live reduce of {it.qty} exceeds its lot ({held}): retire it (CANCELLING) or replace it')
            leg = it.qty
        per_lot[it.owner_id] = CTX.add(per_lot.get(it.owner_id, ZERO), leg)
    for lot_id, q in per_lot.items():
        req(q <= lots[lot_id].qty, f'{p}.lot[{lot_id}]', f'live reducing intents {q} exceed the lot qty {lots[lot_id].qty}')
    # carried: which intents a record carries; anything else (not an entry) is cancel-only work
    carried = set()
    for x in lots.values():
        if x.in_flight is not None:
            it = intents.get(x.in_flight)
            req(it is not None and it.family is OwnerFamily.LOT and it.owner_id == x.lot_id, f'{p}.lot[{x.lot_id}].in_flight',
                'names no open add / reduce / close intent of this lot')
            carried.add(it.intent_id)
    prots = []
    owners = set()
    for st in pf.entry_stops:
        sp = f'{p}.entry_stop[{st.owner_id}]'
        it = intents.get(st.owner_id)
        req(it is not None and it.purpose is Purpose.ENTRY and it.order_type is OrderType.MARKET, sp,
            'a provisional stop protects an open market ENTRY of this portfolio')
        req(st.owner_id not in owners, sp, 'two provisional stops for one entry')
        owners.add(st.owner_id)
        req(it.seen_qty is not None, sp, 'protects an entry with a seen (unresolved) size')
        prots.append((st, it.side, it.symbol, it.seen_qty, sp))
    for x in lots.values():
        prots.append((x.stop, x.side, x.symbol, x.qty, f'{p}.lot[{x.lot_id}].stop'))
    coverage, exposure = {}, {}
    for st, side, symbol, exp, sp in prots:
        check_protection(st, intents, side, symbol, exp, sp)
        for iid in (st.order, st.replacement):
            if iid is not None:
                req(iid not in carried, sp, 'one stop order carries two protections')
                carried.add(iid)
        if st.miss is not None and st.miss.foreign_order_id is not None:
            req(st.miss.foreign_order_id not in cids, sp + '.miss.foreign_order_id', 'an owned client id is never foreign')
        key = (symbol, side)
        coverage[key] = CTX.add(coverage.get(key, ZERO), target_coverage(st, intents))
        exposure[key] = CTX.add(exposure.get(key, ZERO), exp)
    for key, cov in coverage.items():                 # aggregate bound per symbol / side (contract invariant 11)
        req(cov <= exposure[key], f'{p}.protection[{key[0]}|{key[1]}]',
            f'target protective coverage {cov} exceeds the exposure {exposure[key]}')
    for it in intents.values():
        if it.family is not OwnerFamily.ENTRY and it.intent_id not in carried:
            req(it.state is IntentState.CANCELLING, f'{p}.intent[{it.intent_id}].state',
                'an owned order no record carries is cancel-only work')
    # drain (ruling 9) and the permitted-action table (HOLD kinds)
    if pf.entries_mode is not EntriesMode.ACTIVE:
        for it in intents.values():
            ip = f'{p}.intent[{it.intent_id}]'
            if it.pullable:
                req(it.state is IntentState.CANCELLING, ip + '.state',
                    f'a resting / armed opening intent must be cancelling while entries are {pf.entries_mode}')
            elif it.created_at_ms > pf.mode_since_ms and it.state is not IntentState.CANCELLING and \
                    it.reason is not ReasonCode.EXIT_MANUAL:     # a post-hoc booking places nothing
                req(pf.permits(it.purpose, Op.PLACE, one_shot=it.authorized_by is not None), ip,
                    f'a {it.purpose} intent created after entries became {pf.entries_mode} is not permitted')


# ----------------------------------------------------------------------------------------------------------- queries
def owned_client_ids(pf):
    """Every client order id this portfolio owns (intents, algo fallbacks, orphan cancels). Raises OwnershipUnknown for
    an UNKNOWN portfolio: unknown is never an empty set."""
    return frozenset(c for it in pf._known().intents for c in it.client_ids)


def ownership_families(pf):
    """{OwnerFamily: (intent ids...)}: the explicit ownership families (ORPHAN = portfolio-owned cancel-only work).
    Raises OwnershipUnknown when UNKNOWN."""
    out = {f: [] for f in OwnerFamily}
    for it in pf._known().intents:
        out[it.family].append(it.intent_id)
    return {f: tuple(v) for f, v in out.items()}


def entry_blocking_protections(pf):
    """Protections whose derived status blocks new entries on their symbol/side (replaces legacy stop_missing_block)."""
    k = pf._known()
    intents = k.intents_by_id()
    pairs = [(x.stop, x.qty) for x in k.lots] + [(s, intents[s.owner_id].seen_qty) for s in k.entry_stops]
    return tuple(s for s, exp in pairs if protection_status(s, intents, exp) not in PROTECTING)


def check_account_portfolio(account, pf):
    """The portfolio belongs to this account; an unconfirmed / rotating / mismatched binding keeps entries non-active;
    KNOWN_EMPTY was proven under the CONFIRMED binding. Equal positions prove nothing about identity."""
    req(pf.account_id == account.account_id, 'Portfolio.account_id', 'belongs to another account')
    if not account.entries_allowed:
        req(pf.entries_mode is not EntriesMode.ACTIVE, 'Portfolio.entries_mode',
            f'entries cannot be ACTIVE while the binding is {account.binding_state}')
    if pf.ownership is Ownership.KNOWN_EMPTY:
        req(account.entries_allowed and pf.proof.key_digest == account.binding.key_digest, 'Portfolio.proof',
            'KNOWN_EMPTY needs a flat snapshot taken under the confirmed binding')
        req(pf.proof.at_ms >= account.confirmation.confirmed_at_ms, 'Portfolio.proof.at_ms',
            'the flat snapshot predates the binding confirmation')


def check_flat_snapshot_fresh(account, pf, now_ms, max_age_ms):
    """The fresh-snapshot rule (contract invariant 1): a KNOWN_EMPTY claim is usable only while its flat exchange
    snapshot is younger than `max_age_ms` at the caller-supplied `now_ms` (the domain reads no clock; the age limit is
    NC-02 policy). Also re-checks the binding rules of check_account_portfolio. Raises InvalidRecord."""
    check_account_portfolio(account, pf)
    req(pf.ownership is Ownership.KNOWN_EMPTY, 'Portfolio.ownership', 'only a KNOWN_EMPTY claim rests on a flat snapshot')
    req(type(now_ms) is int and type(max_age_ms) is int and max_age_ms >= 0, 'now_ms', 'integer milliseconds')
    age = now_ms - pf.proof.at_ms
    req(age >= 0, 'Portfolio.proof.at_ms', 'the flat snapshot is from the future')
    req(age <= max_age_ms, 'Portfolio.proof.at_ms', f'the flat snapshot is {age} ms old (limit {max_age_ms} ms)')
