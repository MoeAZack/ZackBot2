"""REC-02: the account-level exchange-truth reconciliation fold (pure).

    reconcile(view, snapshot, *, now_ms, trigger, attempt, policy) -> Verdict
    plan_reads(view, *, now_ms, policy) -> ReadPlan            (the by-id queries a pass should carry)

The caller (the runner) takes the reads, calls `reconcile`, journals what the verdict decides (RESOLVE_* / ADOPT /
CLEAR_HOLD as a RECONCILE decision + results, HOLD / QUARANTINE as a durable mode change), sends PROTECT_ONLY through
its journal-first protect path, and reconciles again while the outcome is PENDING (bounded by policy.max_attempts).

Rules, in order (each decision names its REC-02 matrix row):
  0. values  every quantity / price in the view and the snapshot must be a finite Decimal with |adjusted exponent| <= 18
             and <= 38 significant digits (the NC-01 dialect); anything else is a HOLD item (R15 value_out_of_range),
             never an exception. Sums are exact (QCTX); only a VWAP is rounded (PCTX).
  1. reads   positions and open orders must both be OK (R15/R19: UNKNOWN is never empty) and fresh (R14: younger than
             policy.freshness_ms and not older than the newest journaled result). Unreadable -> HOLD. Stale -> REREAD,
             then HOLD after the attempts. A stale or unreadable pass decides nothing else: no resolution, no adoption,
             no CLEAR_HOLD, no FLAT.
  2. orders  the same client id twice in one read is a HOLD item (R10), never last-wins. Every other open order is
             classified by client id: journaled + live (a PROTECT counts as cover only as a reduce-only STOP_MARKET on
             the intent's symbol and side with the journaled qty / price, R18; listed while its answer is UNKNOWN it
             still counts and is confirmed by query, never re-placed), journaled + terminal (orphan, R10), A23 emergency
             id (HOLD, handover), NEWCORE-shaped but unjournaled (QUARANTINE, R05), foreign (QUARANTINE, R04).
  3. intents every live sent intent is settled from exchange evidence only (A19): a by-id FINAL record (checked against
             its fills, R17), an algo stop's child fills (R13), or - for lost ENTRY / ADD intents past the visibility
             window - policy.corroboration_reads agreeing position reads (R01), attributed only when provable (one lost
             intent, or several that all executed nothing / everything). NOT_FOUND alone proves nothing; an unanswered or
             UNKNOWN query leaves its side unjudged and asks for a re-read. A FINAL record of an owned client id later
             supersedes a corroborated not-found (R08, Q2). Every order id and venue trade id an owned intent's evidence
             uses is consumed: one fill explains one thing.
  4. sides   for each (symbol, side) not left unjudged by step 3: venue qty vs owned qty + what step 3 explained.
             A deficit proven by userTrades of order ids no owned intent names (and trades no explanation consumed) is
             ADOPTED as an external reduction only against remaining venue exposure and only for journal-owned
             quantity (R11, Q2); a flat venue is explained history for the owner (R03), anything else a HOLD.
             A surplus is QUARANTINED (R06 foreign, R12 manual add, R19 first run non-flat).
  5. protect every non-zero side must be covered by confirmed owned protection. A gap gets PROTECT_ONLY at the side's
             owned stop level (the unowned surplus only under Q3); no level -> HOLD. Nothing is ever cancelled,
             replaced or loosened by this fold: the only order-creating decision is an additional reduce-only stop.
  6. outcome HOLD if anything needs the owner; PENDING while actions / reads are outstanding; else FLAT or PROTECTED.
             A NORMAL HOLD clears (CLEAR_HOLD) only on a clean, fresh pass with a confirmed binding and reasons that
             are all auto-clearable (Q4); otherwise the HOLD is restated with the owner action. In a hard HOLD
             (DURABILITY_UNAVAILABLE) the verdict is advisory: no resolution, adoption or clear (nothing can be made
             durable); only owned-lot protection, the NC-02 A24 emergency set, stays as an action.
Ownership is never guessed: KNOWN_EMPTY only with a FLAT outcome from fresh OK reads.
"""
from __future__ import annotations

import hashlib
import re
from decimal import Decimal

from newcore.domain import EntriesMode, HoldKind, IntentState, Lookup, Ownership, Purpose, ReasonCode
from newcore.domain.orders import Evidence, PositionRead
from newcore.ports.keys import is_newcore_client_id
from newcore.ports.venue import OutcomeKind, ReadKind

from .model import (PCTX, QCTX, ZERO, AccountView, DecisionKind, Outcome, ReadPlan, RecDecision, RecPolicy, Trigger,
                    Verdict, VenueSnapshot)

K = DecisionKind
R = ReasonCode
# The A23 emergency-stop client id shape (newcore/runner/ids.py EMERGENCY_CID_RE). Repeated here, shape only, so this
# package never imports the runner (the runner imports this package).
EMERGENCY_CID_RE = re.compile(r'zbn1e-[a-z2-7]{26}')
PRICE_TOLERANCE = Decimal('1E-8')          # relative: venue avgPrice vs the VWAP of its fills
MAX_ADJUSTED = 18                          # the NC-01 decimal dialect (newcore.domain.base)
MAX_DIGITS = 38
OPEN_STATES = frozenset({IntentState.SUBMITTED, IntentState.WORKING, IntentState.UNKNOWN, IntentState.CANCELLING})
COVER_STATES = OPEN_STATES                 # a listed owned stop protects whatever its journal answer still says
HOLD_KINDS = frozenset({K.HOLD, K.QUARANTINE})
ACTION_KINDS = frozenset({K.PROTECT_ONLY, K.RESOLVE_FILLED, K.RESOLVE_NOT_EXECUTED, K.ADOPT})
ADVISORY_IN_HARD_HOLD = frozenset({K.RESOLVE_FILLED, K.RESOLVE_NOT_EXECUTED, K.ADOPT, K.CLEAR_HOLD})


class ValueOutOfRange(ValueError):
    pass


def is_emergency_client_id(cid):
    return isinstance(cid, str) and EMERGENCY_CID_RE.fullmatch(cid) is not None


def _canon(x):
    """A canonical text of a frozen input value (order-independent for the tuples that carry no order)."""
    return repr(x)


def snapshot_digest(view, snap):
    """sha256 over a canonical, order-independent text of the view and the snapshot (C9: the reconciliation id is a
    function of what was reconciled, not only of the clock)."""
    h = hashlib.sha256(b'zackbot.newcore.reconcile.digest.v1')

    def put(tag, items):
        h.update(b'\x01' + tag.encode('ascii'))
        for s in sorted(_canon(i) for i in items):
            h.update(b'\x00' + s.encode('utf-8', 'backslashreplace'))
    put('view', [view.account_id, view.binding_confirmed, view.has_history, view.mode, view.hold_kind,
                 view.hold_reasons, view.newest_result_ms])
    put('lots', view.lots)
    put('intents', view.intents)
    put('corroboration', view.corroboration)
    put('hints', view.stop_hints)
    for tag, r in (('positions', snap.positions), ('orders', snap.orders)):
        put(tag + ':read', [r.kind, r.observed_at_ms, r.error_code, r.detail])
        put(tag, r.value or ())
    put('queries', snap.queries)
    put('fills', [(e, r.kind, r.observed_at_ms, tuple(sorted(_canon(x) for x in (r.value or ())))) for e, r in snap.fills])
    put('trades', [(k, w.from_ms, w.read.kind, w.read.observed_at_ms, tuple(sorted(_canon(x) for x in (w.read.value or ()))))
                   for k, w in snap.trades])
    return h.hexdigest()


def reconciliation_id(account_id, now_ms, attempt, trigger, digest=''):
    h = hashlib.sha256(b'zackbot.newcore.reconcile.v2')
    for p in (account_id, now_ms, attempt, trigger, digest):
        h.update(b'\x00' + str(p).encode('ascii'))
    return 'rec_' + h.hexdigest()[:32]


def _in_range(v):
    if v is None:
        return True
    if type(v) is not Decimal or not v.is_finite():
        return False
    if v.is_zero():
        return True
    digits = v.as_tuple().digits                     # context-free: no ambient Decimal context is ever consulted
    n = len(digits)
    while n > 1 and digits[n - 1] == 0:
        n -= 1
    return abs(v.adjusted()) <= MAX_ADJUSTED and n <= MAX_DIGITS


def _values(view, snap):
    """Every Decimal the fold computes with, labelled (for the value gate)."""
    for lot in view.lots:
        yield 'lot.qty', lot.qty
        yield 'lot.stop_price', lot.stop_price
    for f in view.intents:
        yield 'intent.qty', f.qty
        yield 'intent.stop_price', f.stop_price
        yield 'intent.final_executed', f.final_executed
    for _, reads in view.corroboration:
        for r in reads:
            yield 'corroboration.qty', r.qty
    for _, h in view.stop_hints:
        yield 'hint', h
    for name, r in (('position', snap.positions), ('order', snap.orders)):
        for x in (r.value or ()) if r.kind is ReadKind.OK else ():
            if name == 'position':
                yield 'position.qty', x.qty
                yield 'position.entry_price', x.entry_price
            else:
                yield 'order.qty', x.qty
                yield 'order.stop_price', x.stop_price
    for _, q in snap.queries:
        yield 'query.executed_qty', q.executed_qty
        yield 'query.avg_price', q.avg_price
    for _, r in snap.fills:
        for x in (r.value or ()) if r.kind is ReadKind.OK else ():
            yield 'fill.qty', x.qty
            yield 'fill.price', x.price
    for _, w in snap.trades:
        for x in (w.read.value or ()) if w.read.kind is ReadKind.OK else ():
            yield 'trade.qty', x.qty
            yield 'trade.price', x.price


def _sum(xs):
    t = ZERO
    for x in xs:
        t = QCTX.add(t, x)
    return t


def _sub(a, b):
    return QCTX.subtract(a, b)


def _vwap(fills):
    qty = _sum(f.qty for f in fills)
    if qty <= 0:
        return None
    notional = ZERO
    for f in fills:
        notional = PCTX.add(notional, PCTX.multiply(f.qty, f.price))
    return PCTX.divide(notional, qty)


def _price_close(a, b):
    if a is None or b is None:
        return a is b
    return PCTX.abs(PCTX.subtract(a, b)) <= PCTX.multiply(PCTX.abs(b), PRICE_TOLERANCE)


def plan_reads(view: AccountView, *, now_ms: int, policy: RecPolicy = RecPolicy()) -> ReadPlan:
    """By-id queries a pass needs: every live sent intent, and every opening intent resolved `not_found_corroborated`
    inside policy.reopen_window_ms (a late FINAL supersedes it, Q2)."""
    q = set()
    for f in view.intents:
        if f.live and f.state is not IntentState.DURABLE:
            q.add(f.client_id)
        elif (f.opening and f.final_evidence is Evidence.NOT_FOUND_CORROBORATED and f.final_at_ms is not None
              and now_ms - f.final_at_ms <= policy.reopen_window_ms):
            q.add(f.client_id)
    return ReadPlan(queries=tuple(sorted(q)))


class _Pass:
    """One reconciliation pass. Internal; `reconcile` is the API."""

    def __init__(self, view, snap, now_ms, trigger, attempt, policy):
        if type(now_ms) is not int or type(attempt) is not int or attempt < 0:
            raise ValueError('now_ms and attempt must be ints (attempt >= 0)')
        self.v, self.s, self.now, self.trigger, self.attempt, self.p = view, snap, now_ms, trigger, attempt, policy
        self.out = []
        self.needs_q, self.needs_f, self.needs_t = set(), set(), set()
        self.ambiguous = set()
        self.delta = {}
        self.cover = {}
        self.by_cid = {}
        self.dup_cids = set()
        self.intents = tuple(sorted(view.intents, key=lambda x: x.intent_id))    # input order never matters
        for f in self.intents:
            if f.client_id in self.by_cid:
                self.dup_cids.add(f.client_id)
            self.by_cid[f.client_id] = f
        self.owned = {}                   # filled by prepare(), after the value gate: no arithmetic before it
        self.lots_by_side = {}
        for lot in sorted(view.lots, key=lambda x: x.lot_id):
            self.lots_by_side.setdefault((lot.symbol, lot.side), []).append(lot)
        self.open_lot_ids = {lot.lot_id for lot in view.lots}
        self.owned_eoids = {f.exchange_order_id for f in view.intents if f.exchange_order_id is not None}
        self.used_trades = set()          # venue trade ids an explanation consumed (C1: one fill, one explanation)
        self.restored = set()             # sides where a stop ended unexecuted this pass (R09 restore)
        self.partial = set()
        self.surplus = {}
        self.listed = {}
        self.hard = view.mode is EntriesMode.HOLD and view.hold_kind is HoldKind.DURABILITY_UNAVAILABLE

    # ------------------------------------------------------------------------------------------------ helpers
    def add(self, kind, row, **kw):
        self.out.append(RecDecision(kind=kind, row=row, **kw))

    def stale(self, r):
        newest = self.v.newest_result_ms
        return (r.observed_at_ms > self.now or self.now - r.observed_at_ms > self.p.freshness_ms
                or (newest is not None and r.observed_at_ms < newest))

    def fresh_query(self, cid):
        q = self.s.query(cid)
        if q is None or self.stale(q):
            return None
        if q.exchange_order_id is not None:
            self.owned_eoids.add(q.exchange_order_id)       # an owned order id this pass learned (C1 / C2)
        return q

    def bump(self, k, qty):
        self.delta[k] = QCTX.add(self.delta.get(k, ZERO), qty)

    def owns_anything(self):
        return bool(self.v.lots) or any(f.live for f in self.v.intents)

    def level(self, k):
        for lot in self.lots_by_side.get(k, ()):
            if lot.stop_price is not None:
                return lot.stop_price, lot.lot_id
        hint = dict(self.v.stop_hints).get(k)
        lots = self.lots_by_side.get(k, ())
        return hint, (lots[0].lot_id if lots else None)       # a lot with no stop level yet: the hint, owned

    def consume(self, rows):
        for x in rows:
            self.used_trades.add(x.trade_id)

    def verdict(self, outcome, ownership=None):
        decisions = tuple(sorted(self.out, key=RecDecision.key))
        holds = [d for d in decisions if d.kind in HOLD_KINDS]
        reasons = []
        for d in holds:
            for r in d.reasons:
                if r not in reasons:
                    reasons.append(r)
        quarantined = sorted({(d.symbol, d.side) for d in decisions if d.kind is K.QUARANTINE},
                             key=lambda x: (x[0] or '', x[1] or ''))
        if quarantined and not self.p.quarantine_per_side:
            quarantined = [(None, None)]
        if ownership is None:
            if outcome is Outcome.FLAT:
                ownership = Ownership.KNOWN_EMPTY
            elif outcome is Outcome.PROTECTED or self.owns_anything():
                ownership = Ownership.KNOWN
            else:
                ownership = Ownership.UNKNOWN
        try:
            digest = snapshot_digest(self.v, self.s)
        except Exception:                                    # a digest never decides anything: fall back to the clock
            digest = 'undigestible'
        return Verdict(reconciliation_id=reconciliation_id(self.v.account_id, self.now, self.attempt, self.trigger,
                                                           digest),
                       at_ms=self.now, trigger=self.trigger, attempt=self.attempt, outcome=outcome,
                       ownership=ownership, decisions=decisions,
                       hold_reasons=tuple(reasons) if outcome is Outcome.HOLD else (),
                       quarantined=tuple(quarantined),
                       needs=ReadPlan(queries=tuple(sorted(self.needs_q)), fills=tuple(sorted(self.needs_f)),
                                      trades=tuple(sorted(self.needs_t))))

    # ------------------------------------------------------------------------------------------------ 0 / 1. gates
    def values_gate(self):
        bad = sorted({name for name, v in _values(self.v, self.s) if not _in_range(v)})
        if not bad:
            return None
        self.add(K.HOLD, 'R15', detail='value_out_of_range', reasons=(R.RECONCILE_UNRECONCILED,),
                 evidence=tuple(f'field:{b}' for b in bad), owner_actions=('investigate_inputs',))
        return self.verdict(Outcome.HOLD, ownership=Ownership.KNOWN if self.owns_anything() else Ownership.UNKNOWN)

    def reads_gate(self):
        pos, oo = self.s.positions, self.s.orders
        ev = (f'read:positions:{pos.kind}@{pos.observed_at_ms}', f'read:orders:{oo.kind}@{oo.observed_at_ms}')
        if pos.kind is not ReadKind.OK or oo.kind is not ReadKind.OK:
            self.add(K.HOLD, 'R15', detail='unreadable', reasons=(R.CONNECTIVITY_EXCHANGE_OUTAGE,), evidence=ev,
                     owner_actions=('wait_for_exchange', 'check_connectivity'))
            return self.verdict(Outcome.HOLD)
        if self.stale(pos) or self.stale(oo):
            if self.attempt + 1 < self.p.max_attempts:
                self.add(K.REREAD, 'R14', detail='stale_read', evidence=ev + (f'now:{self.now}',))
                return self.verdict(Outcome.PENDING)
            self.add(K.HOLD, 'R14', detail='stale_read', reasons=(R.RECONCILE_UNRECONCILED,), evidence=ev,
                     owner_actions=('check_exchange_clock', 'reread'))
            return self.verdict(Outcome.HOLD)
        return None

    # ------------------------------------------------------------------------------------------------ 2. orders
    def classify_orders(self):
        for cid in sorted(self.dup_cids):
            self.add(K.HOLD, 'R10', client_id=cid, detail='duplicate_client_id', reasons=(R.RECONCILE_UNRECONCILED,),
                     evidence=(f'journal:client_id:{cid}',), owner_actions=('investigate_journal',))
        groups = {}
        for o in self.s.orders.value:
            groups.setdefault(o.ref.client_id, []).append(o)
        for cid in sorted(groups):
            os_ = sorted(groups[cid], key=lambda o: (o.exchange_order_id, o.ref.route, o.ref.symbol, o.position_side))
            if len(os_) == 1:
                self.listed[cid] = os_[0]
                continue
            k = (os_[0].ref.symbol, os_[0].position_side)                     # C4: never last-wins
            self.add(K.HOLD, 'R10', symbol=k[0], side=k[1], client_id=cid, detail='duplicate_listed_client_id',
                     reasons=(R.PROTECT_OWNER_CHECK,), evidence=tuple(f'order:{o.exchange_order_id}' for o in os_),
                     owner_actions=('investigate_venue_orders',))
            if cid in self.by_cid:
                self.ambiguous.add((self.by_cid[cid].symbol, self.by_cid[cid].side))
        for cid in sorted(self.listed):
            o = self.listed[cid]
            k = (o.ref.symbol, o.position_side)
            ev = (f'order:{o.exchange_order_id}', f'route:{o.ref.route}', f'type:{o.order_type}',
                  'reduce' if o.reduce else 'opening', f'qty:{o.qty}')
            f = self.by_cid.get(cid)
            if f is None:
                if is_emergency_client_id(cid):
                    if (o.reduce or o.close_position) and o.order_type == 'STOP_MARKET':
                        self.cover[k] = QCTX.add(self.cover.get(k, ZERO), o.qty)
                    self.add(K.HOLD, 'R05', symbol=k[0], side=k[1], client_id=cid, detail='emergency_stop',
                             reasons=(R.RECONCILE_UNRECONCILED,), evidence=ev,
                             owner_actions=('handover_emergency_stop',))
                elif is_newcore_client_id(cid):
                    self.add(K.QUARANTINE, 'R05', symbol=k[0], side=k[1], client_id=cid,
                             detail='unjournaled_newcore', reasons=(R.OWNERSHIP_FOREIGN_ORDER,), evidence=ev,
                             owner_actions=('adopt_order', 'cancel_order', 'leave'))
                else:
                    self.add(K.QUARANTINE, 'R04', symbol=k[0], side=k[1], client_id=cid, detail='foreign_order',
                             reasons=(R.OWNERSHIP_FOREIGN_ORDER,), evidence=ev, owner_actions=('leave', 'cancel_order'))
                continue
            if not f.live:
                self.add(K.HOLD, 'R10', symbol=k[0], side=k[1], client_id=cid, intent_id=f.intent_id,
                         detail='orphan_owned', reasons=(R.LIFECYCLE_ORPHAN_CANCEL,),
                         evidence=ev + (f'journal:{f.state}',), owner_actions=(f'cancel_orphan:{cid}',))
                continue
            if f.purpose is Purpose.PROTECT:
                self.owned_eoids.add(o.exchange_order_id)
                bad = self.protect_shape(f, o)
                if f.owner_id not in self.open_lot_ids:
                    # the lot closed, its stop's cancel is the runner's pending cancel-only work (a crash between the
                    # close and the cancel): wait for it while attempts remain, then it is an owner item
                    last = self.attempt + 1 >= self.p.max_attempts
                    self.add(K.HOLD if last else K.REREAD, 'R10', symbol=k[0], side=k[1], client_id=cid,
                             intent_id=f.intent_id, detail='protect_without_lot',
                             reasons=(R.LIFECYCLE_ORPHAN_CANCEL,) if last else (),
                             evidence=ev + (f'owner:{f.owner_id}',),
                             owner_actions=(f'cancel_order:{cid}',) if last else ())
                elif bad:                                                     # C6: an R18 item, never cover
                    self.add(K.HOLD, 'R18', symbol=f.symbol, side=f.side, client_id=cid, intent_id=f.intent_id,
                             detail='order_mismatch', reasons=(R.PROTECT_OWNER_CHECK,),
                             evidence=ev + tuple(f'mismatch:{b}' for b in bad) + (
                                 f'journal_qty:{f.qty}', f'journal_stop:{f.stop_price}', f'venue_stop:{o.stop_price}'),
                             owner_actions=('replace_stop_to_journal',))
                elif f.state in COVER_STATES:                                 # C3: listed = it rests and protects
                    self.cover[k] = QCTX.add(self.cover.get(k, ZERO), o.qty)

    @staticmethod
    def protect_shape(f, o):
        bad = []
        if o.order_type != 'STOP_MARKET':
            bad.append('type')
        if not (o.reduce or o.close_position):
            bad.append('not_reduce_only')
        if o.ref.symbol != f.symbol:
            bad.append('symbol')
        if o.position_side != f.side:
            bad.append('side')
        if o.qty != f.qty:
            bad.append('qty')
        if o.stop_price != f.stop_price:
            bad.append('stop_price')
        return bad

    # ------------------------------------------------------------------------------------------------ 3. intents
    def fills_check(self, f, eoid, executed, avg):
        """FINAL with executed > 0: the fills must add up (R17). Returns (ok, evidence) or None (re-read needed)."""
        fr = self.s.fills_of(eoid)
        if fr is None or fr.kind is not ReadKind.OK or self.stale(fr):
            self.needs_f.add(eoid)
            return None
        rows = sorted((x for x in fr.value if x.exchange_order_id == eoid), key=lambda x: x.trade_id)
        self.consume(rows)                                  # matched or not, these trades belong to this order
        qty, vwap = _sum(x.qty for x in rows), _vwap(rows)
        ev = tuple(f'trade:{x.trade_id}' for x in rows) + (f'order:{eoid}', f'fills_qty:{qty}')
        if qty != executed or (avg is not None and not _price_close(vwap, avg)):
            k = (f.symbol, f.side)
            self.ambiguous.add(k)
            self.add(K.HOLD, 'R17', symbol=k[0], side=k[1], intent_id=f.intent_id, client_id=f.client_id,
                     detail='fill_mismatch', reasons=(R.RECONCILE_UNRECONCILED,),
                     evidence=ev + (f'executed:{executed}', f'avg:{avg}', f'vwap:{vwap}'),
                     owner_actions=('compare_user_trades', 'confirm_executed_qty'))
            return False, ev
        return True, ev

    def resolve_final(self, f, q, row_default):
        k = (f.symbol, f.side)
        ex = q.executed_qty
        ev = (f'order:{q.exchange_order_id}', f'status:{q.status}', f'evidence:{Evidence.EXCHANGE_FINAL}')
        if ex == 0:
            self.add(K.RESOLVE_NOT_EXECUTED, row_default, symbol=k[0], side=k[1], intent_id=f.intent_id,
                     client_id=f.client_id, qty=ZERO, detail=f'final_{str(q.status).lower()}', evidence=ev)
            if f.purpose is Purpose.PROTECT:
                self.restored.add(k)
            return
        chk = self.fills_check(f, q.exchange_order_id, ex, q.avg_price)
        if chk is None:
            self.ambiguous.add(k)
            return
        ok, fev = chk
        if not ok:
            return
        row = row_default
        if f.purpose is not Purpose.PROTECT and ex < f.qty:
            row = 'R07'
            self.partial.add(k)
        elif f.purpose is not Purpose.PROTECT and f.state is IntentState.UNKNOWN:
            row = 'R08'
        self.add(K.RESOLVE_FILLED, row, symbol=k[0], side=k[1], intent_id=f.intent_id, client_id=f.client_id, qty=ex,
                 price=q.avg_price, detail='late_fill' if row == 'R08' else ('partial_final' if row == 'R07'
                                                                               else 'exchange_final'),
                 evidence=ev + fev)
        self.bump(k, ex if f.opening else QCTX.minus(ex))

    def settle_protect(self, f):
        k = (f.symbol, f.side)
        listed = f.client_id in self.listed
        q = self.fresh_query(f.client_id)
        if listed:
            if q is not None and q.kind is OutcomeKind.FINAL:       # by-id record says done, the read lists it:
                self.needs_q.add(f.client_id)                       # contradiction: re-read, never resolve it away
                self.ambiguous.add(k)
            elif f.state is not IntentState.WORKING and (q is None or q.kind is not OutcomeKind.KNOWN):
                self.needs_q.add(f.client_id)                       # C3: confirm it (KNOWN); it already counts
            return
        if q is None:
            self.needs_q.add(f.client_id)
            self.ambiguous.add(k)                                   # C2: an unresolved own stop leaves its side unjudged
            return
        if q.kind is OutcomeKind.FINAL:
            self.resolve_final(f, q, 'R13' if q.executed_qty > 0 else 'R09')
        elif q.kind is OutcomeKind.KNOWN and q.detail == 'algo_triggered':
            fr = self.s.fills_of(q.exchange_order_id)
            if fr is None or fr.kind is not ReadKind.OK or self.stale(fr) or not fr.value:
                self.needs_f.add(q.exchange_order_id)
                self.ambiguous.add(k)
                return
            rows = sorted((x for x in fr.value if x.exchange_order_id == q.exchange_order_id), key=lambda x: x.trade_id)
            self.consume(rows)
            qty = _sum(x.qty for x in rows)
            ev = tuple(f'trade:{x.trade_id}' for x in rows) + (f'child:{q.exchange_order_id}',)
            if qty == f.qty:
                self.add(K.RESOLVE_FILLED, 'R13', symbol=k[0], side=k[1], intent_id=f.intent_id,
                         client_id=f.client_id, qty=qty, price=_vwap(rows), detail='algo_stop_filled', evidence=ev)
                self.bump(k, QCTX.minus(qty))
            else:
                self.ambiguous.add(k)
                self.add(K.HOLD, 'R07', symbol=k[0], side=k[1], intent_id=f.intent_id, client_id=f.client_id,
                         qty=qty, detail='partial_child', reasons=(R.EVIDENCE_PARTIAL_FILL,), evidence=ev,
                         owner_actions=('wait_child_final', 'confirm_reduction'))
        elif q.kind is OutcomeKind.KNOWN:                    # the by-id record says open, the read does not: re-read
            self.needs_q.add(f.client_id)
            self.ambiguous.add(k)
        elif q.kind is OutcomeKind.NOT_FOUND:
            ev = (f'lookup:not_found:{q.error_code}', f'state:{f.state}')
            if f.state is IntentState.WORKING:
                self.add(K.HOLD, 'R09', symbol=k[0], side=k[1], intent_id=f.intent_id, client_id=f.client_id,
                         detail='stop_not_found', reasons=(R.PROTECT_CHECKING,), evidence=ev,
                         owner_actions=('check_stop_on_exchange',))
            else:
                self.add(K.HOLD, 'R02', symbol=k[0], side=k[1], intent_id=f.intent_id, client_id=f.client_id,
                         detail='reduce_only_not_found', reasons=(R.EXEC_ORDER_FAILED,), evidence=ev,
                         owner_actions=('resend_same_client_id',))
        else:
            self.needs_q.add(f.client_id)
            self.ambiguous.add(k)

    def agreeing(self, f, start, pos_qty, pos_at):
        reads = [r for r in dict(self.v.corroboration).get(f.intent_id, ()) if r.at_ms >= start]
        if not reads or reads[-1].at_ms < pos_at:
            reads.append(PositionRead(at_ms=pos_at, qty=pos_qty))
        agree = []
        for r in reads:                              # the trailing run of equal quantities
            agree = agree + [r] if agree and agree[-1].qty == r.qty else [r]
        return agree

    def corroborate(self, k, lost):
        """R01 / C5: the lost ENTRY / ADD intents of one side, resolved only when provable."""
        self.ambiguous.add(k)
        pos_qty, pos_at = self.pos_qty.get(k, ZERO), self.s.positions.observed_at_ms
        ids = ','.join(f.intent_id for f in lost)
        waits = [f for f in lost if f.sent_at_ms is None or self.now < f.sent_at_ms + self.p.visibility_ms]
        if waits:
            for f in waits:
                start = None if f.sent_at_ms is None else f.sent_at_ms + self.p.visibility_ms
                self.add(K.HOLD, 'R01', symbol=k[0], side=k[1], intent_id=f.intent_id, client_id=f.client_id,
                         detail='within_visibility_window', reasons=(R.EXEC_ENTRY_UNCONFIRMED,),
                         evidence=(f'sent:{f.sent_at_ms}', f'window_ends:{start}'), owner_actions=('wait',))
                self.add(K.REREAD, 'R01', symbol=k[0], side=k[1], intent_id=f.intent_id, detail=f'after_ms:{start}')
            return
        agree = {f.intent_id: self.agreeing(f, f.sent_at_ms + self.p.visibility_ms, pos_qty, pos_at) for f in lost}
        short = [f for f in lost if len(agree[f.intent_id]) < self.p.corroboration_reads]
        if short:
            for f in short:
                self.add(K.HOLD, 'R01', symbol=k[0], side=k[1], intent_id=f.intent_id, client_id=f.client_id,
                         detail='awaiting_corroboration', reasons=(R.EXEC_ENTRY_UNCONFIRMED,),
                         evidence=tuple(f'read:{r.at_ms}:{r.qty}' for r in agree[f.intent_id]), owner_actions=('wait',))
                self.add(K.REREAD, 'R01', symbol=k[0], side=k[1], intent_id=f.intent_id, detail='position_read')
            return
        surplus = _sub(_sub(pos_qty, self.owned.get(k, ZERO)), self.delta.get(k, ZERO))
        total = _sum(f.qty for f in lost)
        if surplus == 0:                                         # nothing beyond the owned lots: none executed
            for f in lost:
                self.add(K.RESOLVE_NOT_EXECUTED, 'R01', symbol=k[0], side=k[1], intent_id=f.intent_id,
                         client_id=f.client_id, qty=ZERO, detail=str(Evidence.NOT_FOUND_CORROBORATED),
                         evidence=tuple(f'read:{r.at_ms}:{r.qty}' for r in agree[f.intent_id]))
            self.ambiguous.discard(k)
        elif len(lost) == 1 and 0 < surplus <= total or len(lost) > 1 and surplus == total:
            for f in lost:                                       # one lost intent, or all of them executed in full
                q = surplus if len(lost) == 1 else f.qty
                self.add(K.RESOLVE_FILLED, 'R01', symbol=k[0], side=k[1], intent_id=f.intent_id,
                         client_id=f.client_id, qty=q, price=self.entry_price.get(k),
                         detail=str(Evidence.POSITION_ADOPTED),
                         evidence=tuple(f'read:{r.at_ms}:{r.qty}' for r in agree[f.intent_id]))
            self.bump(k, surplus)
            self.ambiguous.discard(k)
        else:
            self.add(K.HOLD, 'R01', symbol=k[0], side=k[1], qty=surplus,
                     detail='unattributable_multiple' if len(lost) > 1 else 'unattributable',
                     reasons=(R.EXEC_ENTRY_UNCONFIRMED, R.OWNERSHIP_UNTRACKED_POSITION),
                     evidence=(f'intents:{ids}', f'surplus:{surplus}', f'requested:{total}'),
                     owner_actions=('adopt_into_lot', 'close', 'leave'))

    def settle_intents(self):
        lost = {}
        for f in self.intents:
            k = (f.symbol, f.side)
            if not f.live:
                self.reopen(f)
                continue
            if f.state is IntentState.DURABLE:
                self.ambiguous.add(k)                # never sent: the runner sends or closes it NOT_SENT
                continue
            if f.purpose is Purpose.PROTECT:
                self.settle_protect(f)
                continue
            q = self.fresh_query(f.client_id)
            if q is None:
                if f.opening and f.last_lookup is Lookup.NOT_FOUND:
                    lost.setdefault(k, []).append(f)
                    continue
                self.needs_q.add(f.client_id)
                self.ambiguous.add(k)
                continue
            if q.kind is OutcomeKind.FINAL and f.client_id in self.listed:
                self.needs_q.add(f.client_id)           # contradiction between the by-id record and the open orders
                self.ambiguous.add(k)
            elif q.kind is OutcomeKind.FINAL:
                self.resolve_final(f, q, 'R15')
            elif q.kind is OutcomeKind.KNOWN:
                self.ambiguous.add(k)
                partial = q.status == 'PARTIALLY_FILLED'
                if partial:
                    self.partial.add(k)
                self.add(K.HOLD, 'R07' if partial else 'R15', symbol=k[0], side=k[1], intent_id=f.intent_id,
                         client_id=f.client_id, detail='partial_working' if partial else 'working',
                         reasons=(R.EVIDENCE_PARTIAL_FILL,) if partial else
                         ((R.EXEC_ENTRY_UNCONFIRMED,) if f.opening else (R.EXEC_ORDER_FAILED,)),
                         evidence=(f'order:{q.exchange_order_id}', f'status:{q.status}'),
                         owner_actions=('wait_final', 'cancel_remainder') if f.opening else ('wait_final',))
            elif q.kind is OutcomeKind.NOT_FOUND:
                if f.opening:
                    lost.setdefault(k, []).append(f)
                else:
                    self.ambiguous.add(k)
                    self.add(K.HOLD, 'R02', symbol=k[0], side=k[1], intent_id=f.intent_id, client_id=f.client_id,
                             detail='reduce_only_not_found', reasons=(R.EXEC_ORDER_FAILED,),
                             evidence=(f'lookup:not_found:{q.error_code}',), owner_actions=('resend_same_client_id',))
            else:
                self.needs_q.add(f.client_id)
                self.ambiguous.add(k)
        for k in sorted(lost):
            self.corroborate(k, lost[k])

    def reopen(self, f):
        """R08 / Q2: an opening intent decided `not_found_corroborated` whose own client id now shows a FINAL fill."""
        if not (f.opening and f.final_evidence is Evidence.NOT_FOUND_CORROBORATED):
            return
        q = self.fresh_query(f.client_id)
        if q is None or q.kind is not OutcomeKind.FINAL or not q.executed_qty:
            return
        k = (f.symbol, f.side)
        if not self.p.adopt_external_change:
            self.ambiguous.add(k)
            self.add(K.HOLD, 'R08', symbol=k[0], side=k[1], intent_id=f.intent_id, client_id=f.client_id,
                     qty=q.executed_qty, detail='late_fill_after_corroboration',
                     reasons=(R.EVIDENCE_LATE_FILL_NOT_ACTIVE,), evidence=(f'order:{q.exchange_order_id}',),
                     owner_actions=('adopt_late_fill', 'close'))
            return
        chk = self.fills_check(f, q.exchange_order_id, q.executed_qty, q.avg_price)
        if chk is None:
            self.ambiguous.add(k)
            return
        ok, fev = chk
        if ok:
            self.add(K.RESOLVE_FILLED, 'R08', symbol=k[0], side=k[1], intent_id=f.intent_id, client_id=f.client_id,
                     qty=q.executed_qty, price=q.avg_price, detail='supersedes_not_found_corroborated',
                     evidence=(f'order:{q.exchange_order_id}',) + fev)
            self.bump(k, q.executed_qty)

    # ------------------------------------------------------------------------------------------------ 4. sides
    def settling(self, k):
        """R14 (b): a side whose venue quantity may simply not show a just-recorded result yet (read lag after a fill):
        a FINAL of that side recorded within policy.settle_ms before the position read, or resolved in this pass.
        Re-read instead of judging, but only while attempts remain: the last attempt always judges."""
        if self.attempt + 1 >= self.p.max_attempts or self.p.settle_ms <= 0:
            return False
        if k in self.delta:
            return True
        at = self.s.positions.observed_at_ms
        return any((f.symbol, f.side) == k and f.final_at_ms is not None and 0 <= at - f.final_at_ms < self.p.settle_ms
                   and f.final_executed for f in self.intents)

    def compare_sides(self):
        keys = sorted(set(self.pos_qty) | set(self.owned) | set(self.delta))
        for k in keys:
            if k in self.ambiguous:
                continue
            P = self.pos_qty.get(k, ZERO)
            E = QCTX.add(self.owned.get(k, ZERO), self.delta.get(k, ZERO))
            if P == E:
                continue
            if self.settling(k):
                self.add(K.REREAD, 'R14', symbol=k[0], side=k[1], detail='settling',
                         evidence=(f'venue_qty:{P}', f'owned_qty:{E}', f'read:{self.s.positions.observed_at_ms}'))
                continue
            if P > E:
                surplus = _sub(P, E)
                self.surplus[k] = surplus
                if not self.v.has_history:
                    row, detail = 'R19', 'init_non_flat'
                elif E > 0:
                    row, detail = 'R12', 'manual_add'
                else:
                    row, detail = 'R06', 'foreign_position'
                self.add(K.QUARANTINE, row, symbol=k[0], side=k[1], qty=surplus, detail=detail,
                         reasons=(R.OWNERSHIP_UNTRACKED_POSITION,),
                         evidence=(f'venue_qty:{P}', f'owned_qty:{E}', f'entry_price:{self.entry_price.get(k)}'),
                         owner_actions=('adopt_into_lot', 'close_surplus', 'leave'))
                continue
            self.deficit(k, P, E)

    def deficit(self, k, P, E):
        gap = _sub(E, P)
        row = 'R03' if P == 0 else 'R11'
        lots = self.lots_by_side.get(k, ())
        since = min((x.opened_at_ms for x in lots), default=0)
        tw = self.s.trades_of(k)
        if tw is None or self.stale(tw.read):
            self.needs_t.add((k[0], k[1], since))
            return
        if tw.read.kind is not ReadKind.OK:
            self.add(K.HOLD, row, symbol=k[0], side=k[1], qty=gap, detail='trades_unreadable',
                     reasons=(R.CONNECTIVITY_EXCHANGE_OUTAGE,), evidence=(f'read:trades:{tw.read.kind}',),
                     owner_actions=('wait_for_exchange',))
            return
        ext = sorted((x for x in tw.read.value if x.symbol == k[0] and x.position_side == k[1]
                      and x.at_ms >= tw.from_ms and x.exchange_order_id not in self.owned_eoids
                      and x.trade_id not in self.used_trades), key=lambda x: x.trade_id)
        ext_qty = _sum(x.qty for x in ext)
        ev = tuple(f'trade:{x.trade_id}:order:{x.exchange_order_id}' for x in ext) + (f'venue_qty:{P}',
                                                                                       f'owned_qty:{E}')
        journal_owned = self.owned.get(k, ZERO)
        explained = bool(ext) and ext_qty == gap
        if explained and P == 0:
            # C8: no venue exposure is left to adopt against; the owner confirms the history (R03)
            self.add(K.HOLD, row, symbol=k[0], side=k[1], qty=gap, detail='external_close_explained',
                     reasons=(R.OWNERSHIP_UNTRACKED_POSITION,), evidence=ev,
                     owner_actions=('confirm_external_close', 'cancel_left_protection'))
            self.consume(ext)
            return
        if (explained and self.p.adopt_external_change and k not in self.delta and gap <= journal_owned):
            self.add(K.ADOPT, row, symbol=k[0], side=k[1], qty=gap, price=_vwap(ext), detail='external_reduce',
                     reasons=(R.EXIT_MANUAL,), evidence=ev)
            self.consume(ext)
            return
        self.add(K.HOLD, row, symbol=k[0], side=k[1], qty=gap,
                 detail='explained_history' if explained else 'unexplained_deficit',
                 reasons=(R.OWNERSHIP_UNTRACKED_POSITION,), evidence=ev,
                 owner_actions=('confirm_external_close', 'flatten'))

    # ------------------------------------------------------------------------------------------------ 5. protection
    def protect(self):
        for k in sorted(self.pos_qty):
            P = self.pos_qty[k]
            if P <= 0:
                continue
            gap = _sub(P, self.cover.get(k, ZERO))
            if gap <= 0:
                continue
            unowned = self.surplus.get(k, ZERO)
            allowed = gap if (unowned == 0 or self.p.protect_surplus) else max(ZERO, _sub(gap, unowned))
            level, lot_id = self.level(k)
            if k in self.restored:
                row = 'R09'
            elif unowned > 0:
                row = 'R12' if self.owned.get(k, ZERO) > 0 else ('R19' if not self.v.has_history else 'R06')
            elif k in self.partial:
                row = 'R07'
            else:
                row = 'R16'
            ev = (f'venue_qty:{P}', f'confirmed_cover:{self.cover.get(k, ZERO)}', f'unowned:{unowned}')
            if level is not None and allowed > 0:
                self.add(K.PROTECT_ONLY, row, symbol=k[0], side=k[1], lot_id=lot_id, qty=allowed, price=level,
                         detail='restore' if row == 'R09' else 'cover_gap', reasons=(R.PROTECT_RESTORING,),
                         evidence=ev)
            if level is None or allowed < gap:
                self.add(K.HOLD, row, symbol=k[0], side=k[1], qty=_sub(gap, allowed if level is not None else ZERO),
                         detail='unprotected', reasons=(R.PROTECT_CHECKING,), evidence=ev + (f'level:{level}',),
                         owner_actions=('set_stop_level', 'close'))

    # ------------------------------------------------------------------------------------------------ 6. outcome
    def advisory(self):
        """C7: hard HOLD. Nothing can be journaled, so no resolution / adoption / clear is an action; protection of an
        OWNED lot is the A24 emergency set (deterministic id, reduce-only) and stays; everything else is evidence."""
        kept, advised = [], []
        for d in self.out:
            if d.kind in ADVISORY_IN_HARD_HOLD or (d.kind is K.PROTECT_ONLY and d.lot_id is None):
                advised.append(d)
            elif d.kind is K.PROTECT_ONLY:
                kept.append(RecDecision(kind=d.kind, row=d.row, symbol=d.symbol, side=d.side, lot_id=d.lot_id,
                                        qty=d.qty, price=d.price, detail='a24_emergency', reasons=d.reasons,
                                        evidence=d.evidence))
            else:
                kept.append(d)
        self.out = kept
        self.add(K.HOLD, 'R15', detail='durability_unavailable', reasons=(R.RECOVERY_DURABILITY_UNAVAILABLE,),
                 evidence=tuple(f'advisory:{d.kind}:{d.row}:{d.detail}:{d.intent_id or d.symbol}' for d in advised),
                 owner_actions=('restart_with_writable_store',))
        return self.verdict(Outcome.HOLD)

    def run(self):
        early = self.values_gate() or self.reads_gate()
        if early is not None:
            return early
        for k, lots in self.lots_by_side.items():
            self.owned[k] = _sum(x.qty for x in lots)
        self.pos_qty, self.entry_price = {}, {}
        for p in self.s.positions.value:
            if p.qty > 0:
                k = (p.symbol, p.side)
                self.pos_qty[k] = QCTX.add(self.pos_qty.get(k, ZERO), p.qty)
                self.entry_price[k] = p.entry_price
        self.classify_orders()
        self.settle_intents()
        self.compare_sides()
        self.protect()
        if self.needs_q or self.needs_f or self.needs_t:
            self.add(K.REREAD, 'R15', detail='more_evidence', evidence=tuple(
                [f'query:{c}' for c in sorted(self.needs_q)] + [f'fills:{e}' for e in sorted(self.needs_f)] +
                [f'trades:{s}:{d}:{t}' for s, d, t in sorted(self.needs_t)]))
        if self.hard:
            return self.advisory()
        kinds = {d.kind for d in self.out}
        if kinds & HOLD_KINDS:
            return self.verdict(Outcome.HOLD)
        if kinds & (ACTION_KINDS | {K.REREAD}) or self.ambiguous:
            if self.attempt + 1 >= self.p.max_attempts:
                self.add(K.HOLD, 'R15', detail='unresolved_after_attempts', reasons=(R.RECONCILE_UNRECONCILED,),
                         evidence=(f'attempts:{self.attempt + 1}',), owner_actions=('investigate', 'reread'))
                return self.verdict(Outcome.HOLD)
            return self.verdict(Outcome.PENDING)
        live = [f for f in self.intents if f.live and f.purpose is not Purpose.PROTECT]
        flat = not self.pos_qty and not self.listed and not self.v.lots and not live
        outcome = Outcome.FLAT if flat else Outcome.PROTECTED
        if self.v.mode is EntriesMode.HOLD:
            clearable = (self.p.auto_clear_hold and self.v.hold_kind is HoldKind.NORMAL and self.v.binding_confirmed
                         and set(self.v.hold_reasons) <= set(self.p.auto_clearable))
            if clearable:
                self.add(K.CLEAR_HOLD, 'R15', detail='fresh_full_match',
                         reasons=(R.RECONCILE_MATCH,),
                         evidence=tuple(f'was:{r}' for r in self.v.hold_reasons) + (f'outcome:{outcome}',))
                return self.verdict(outcome)
            self.add(K.HOLD, 'R15', detail='owner_resume_required', reasons=tuple(self.v.hold_reasons) or
                     (R.RECONCILE_UNRECONCILED,), evidence=(f'exchange:{outcome}',),
                     owner_actions=('review_and_resume',))
            return self.verdict(Outcome.HOLD, ownership=Ownership.KNOWN_EMPTY if flat else Ownership.KNOWN)
        return self.verdict(outcome)


def reconcile(view: AccountView, snapshot: VenueSnapshot, *, now_ms: int, trigger: Trigger = Trigger.CYCLE,
              attempt: int = 0, policy: RecPolicy = RecPolicy()) -> Verdict:
    """One pure reconciliation pass (module docstring). Same inputs -> the identical Verdict. Never raises for a value:
    an arithmetic refusal the value gate did not foresee is a HOLD item too (C10)."""
    p = _Pass(view, snapshot, now_ms, Trigger(trigger), attempt, policy)
    try:
        return p.run()
    except ArithmeticError as ex:
        q = _Pass(view, snapshot, now_ms, Trigger(trigger), attempt, policy)
        q.add(K.HOLD, 'R15', detail='arithmetic_refused', reasons=(R.RECONCILE_UNRECONCILED,),
              evidence=(f'error:{type(ex).__name__}',), owner_actions=('investigate_inputs',))
        return q.verdict(Outcome.HOLD, ownership=Ownership.KNOWN if q.owns_anything() else Ownership.UNKNOWN)
