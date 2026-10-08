"""Deterministic builders of VALID NC-01 records for the tests. Every id comes from a seeded random.Random, so a seed
reproduces the exact records (recorded seeds; no wall clock, no uuid4)."""
import dataclasses
import random
from decimal import Decimal as D

from newcore.domain import (Account, AccountBinding, Action, Arming, Authority, BindingConfirmation, BindingState,
                            Capability, Decision, EntriesMode, Environment, Fill, HoldKind, InstrumentId, InstrumentRules,
                            IntentState, Lot, LotSource, MissPhase, OrderIntent, OrderType, Ownership, OwnershipProof,
                            Portfolio, Position, ProofKind, Protection, Purpose, ReasonCode, Side, StopMiss,
                            confirmation_phrase, make_id, protect_key)

T0 = 1_791_400_000_000                      # 2026-10-07 UTC, integer ms
DIGEST = '0123456789abcdef'
SYMBOLS = ('SOLUSDT', 'XRPUSDT', 'ETHUSDT', 'BTCUSDT', 'DOGEUSDT', 'ADAUSDT', 'LINKUSDT', 'AVAXUSDT')
STOP_STATES = ('none', 'pending', 'unverified', 'confirmed', 'releasing', 'checking', 'restoring', 'owner_check',
               'replacing')


class Ids:
    def __init__(self, seed):
        self.rng = random.Random(seed)

    def id(self, prefix):
        return make_id(prefix, self.rng.getrandbits(128))

    def cid(self, kind='e'):
        return f'z{kind}{self.rng.getrandbits(88):022x}'


def binding(digest=DIGEST, env=Environment.TESTNET):
    return AccountBinding(venue='binance-usdm', environment=env, settlement_asset='USDT',
                          base_url='https://testnet.binancefuture.com', key_digest=digest)


def account(acct, digest=DIGEST, state=BindingState.CONFIRMED):
    conf = None if state is BindingState.UNCONFIRMED else BindingConfirmation(
        account_id=acct, old_key_digest=None, new_key_digest=digest, typed_phrase=confirmation_phrase(acct, digest),
        confirmed_at_ms=T0)
    return Account(account_id=acct, label='testnet main', hedge_mode=True, binding=binding(digest), binding_state=state,
                   confirmation=conf)


def rules(symbol='SOLUSDT', caps=(Capability.HEDGE_MODE, Capability.POST_ONLY, Capability.STOP_MARKET,
                                  Capability.REDUCE_ONLY)):
    return InstrumentRules(instrument=InstrumentId(venue='binance-usdm', symbol=symbol), tick_size=D('0.01'),
                           step_size=D('0.01'), min_qty=D('0.01'), max_qty=D('10000'), min_notional=D('5'),
                           capabilities=tuple(caps))


DEFAULT_REASON = {Purpose.ENTRY: ReasonCode.ENTRY_SIGNAL, Purpose.ADD: ReasonCode.ENTRY_PYRAMID,
                  Purpose.REDUCE: ReasonCode.EXIT_TP1, Purpose.CLOSE: ReasonCode.EXIT_TIME,
                  Purpose.PROTECT: ReasonCode.PROTECT_PLACE}


def intent(ids, acct, purpose, symbol='SOLUSDT', side=Side.LONG, qty=D('1.5'), *, state=IntentState.SUBMITTED,
           order_type=OrderType.MARKET, owner_id=None, price=None, stop_price=None, arm=None, seen_qty=None,
           created=T0, decision_id=None, slot_id=None, reason=None, authorized_by=None, alt=None):
    reason = reason or DEFAULT_REASON[purpose]
    key = None
    if purpose is Purpose.PROTECT:
        order_type = OrderType.STOP_MARKET
        key = protect_key(acct, owner_id, side, qty, stop_price)
    if purpose is Purpose.ENTRY and slot_id is None and reason is ReasonCode.ENTRY_SIGNAL:
        slot_id = 'S1'
    return OrderIntent(intent_id=ids.id('int'), account_id=acct, decision_id=decision_id or ids.id('dec'),
                       client_order_id=ids.cid(purpose.value[0]), purpose=purpose, order_type=order_type, state=state,
                       symbol=symbol, side=side, qty=qty, reason=reason, created_at_ms=created, owner_id=owner_id,
                       slot_id=slot_id, price=price, stop_price=stop_price, arm=arm, idempotency_key=key,
                       alt_client_order_id=alt, seen_qty=seen_qty, authorized_by=authorized_by)


def stop_level(side, price):
    return max((price * D('0.95') if side is Side.LONG else price * D('1.05')).quantize(D('0.01')), D('0.01'))


def protection(ids, acct, owner_id, symbol, side, qty, price, stop_state):
    """(Protection, [its PROTECT intents]) for one of STOP_STATES."""
    level = stop_level(side, price)
    order_state = {'pending': IntentState.SUBMITTED, 'unverified': IntentState.WORKING, 'confirmed': IntentState.WORKING,
                   'releasing': IntentState.CANCELLING, 'checking': IntentState.WORKING,
                   'replacing': IntentState.WORKING}.get(stop_state)
    extra, order, repl = [], None, None
    if order_state is not None:
        it = intent(ids, acct, Purpose.PROTECT, symbol, side, qty, state=order_state, owner_id=owner_id, stop_price=level)
        extra.append(it)
        order = it.intent_id
    if stop_state == 'replacing':
        new = intent(ids, acct, Purpose.PROTECT, symbol, side, qty, state=IntentState.SUBMITTED, owner_id=owner_id,
                     stop_price=level + D('0.01'), reason=ReasonCode.PROTECT_REPLACE)
        extra.append(new)
        repl = new.intent_id
    miss = {'checking': StopMiss(phase=MissPhase.CHECKING, count=1, since_ms=T0 + 1000),
            'restoring': StopMiss(phase=MissPhase.RESTORING, count=2, since_ms=T0 + 1000),
            'owner_check': StopMiss(phase=MissPhase.OWNER_CHECK, count=3, since_ms=T0 + 1000,
                                    foreign_order_id='8123456789')}.get(stop_state)
    prot = Protection(owner_id=owner_id, price=level, qty=qty, order=order, replacement=repl,
                      confirmed_at_ms=T0 + 2000 if stop_state in ('confirmed', 'replacing') else None, miss=miss)
    return prot, extra


def lot(ids, acct, symbol='SOLUSDT', side=Side.LONG, qty=D('1.5'), price=D('100'), *, stop_state='confirmed',
        in_flight=None, source=LotSource.STRATEGY, partial_close=None):
    """(Lot, [intents it carries]). in_flight: None | 'add' | 'reduce' | 'close'. partial_close: the lot opens with
    qty + partial_close, then closes partial_close."""
    lot_id = ids.id('lot')
    adopted = ids.id('dec') if source is LotSource.ADOPTED else None
    open_qty = qty + (partial_close or 0)
    fills = [Fill(at_ms=T0, reason=ReasonCode.ENTRY_ADOPTED if adopted else ReasonCode.ENTRY_SIGNAL, qty=open_qty,
                  price=price, fee=D('0.075'), result_id=None if adopted else ids.id('res'), decision_id=adopted)]
    if partial_close:
        fills.append(Fill(at_ms=T0 + 60_000, reason=ReasonCode.EXIT_TP1, qty=partial_close, price=price + 1, fee=D('0.07'),
                          result_id=ids.id('res')))
    prot, extra = protection(ids, acct, lot_id, symbol, side, qty, price, stop_state)
    flight = None
    if in_flight:
        purpose = {'add': Purpose.ADD, 'reduce': Purpose.REDUCE, 'close': Purpose.CLOSE}[in_flight]
        q = qty if purpose is Purpose.CLOSE else max((qty / 2).quantize(D('0.01')), D('0.01'))
        fl = intent(ids, acct, purpose, symbol, side, q, state=IntentState.UNKNOWN, owner_id=lot_id, created=T0 + 3000)
        extra.append(fl)
        flight = fl.intent_id
    strategy = source is LotSource.STRATEGY
    lt = Lot(lot_id=lot_id, account_id=acct, symbol=symbol, side=side, source=source,
             slot_id='S1' if strategy else None, timeframe='4h' if strategy else None, opened_at_ms=T0, qty=qty,
             avg_price=price, initial_qty=open_qty, max_qty=open_qty, risk_distance=D('5'), risk_usd=D('7.5'), stop=prot,
             fills=tuple(fills), in_flight=flight, adopted_by=adopted)
    return lt, extra


def position(ids, lots):
    return Position(position_id=ids.id('pos'), symbol=lots[0].symbol, side=lots[0].side, lots=tuple(lots))


def proof(kind=ProofKind.JOURNAL, ids=None, digest=DIGEST):
    ids = ids or Ids(0)
    if kind is ProofKind.JOURNAL:
        return OwnershipProof(kind=kind, at_ms=T0, through_seq=1)
    if kind is ProofKind.OWNER_ADOPTED:
        return OwnershipProof(kind=kind, at_ms=T0, decision_id=ids.id('dec'))
    if kind is ProofKind.FLAT_SNAPSHOT:
        return OwnershipProof(kind=kind, at_ms=T0, reconciliation_id=ids.id('rec'), key_digest=digest)
    return OwnershipProof(kind=kind, at_ms=T0, reconciliation_id=ids.id('rec'))


def portfolio(acct, positions=(), intents=(), *, entry_stops=(), mode=EntriesMode.ACTIVE, hold_kind=None, reasons=None,
              generation=7, prf=None, since=T0 + 10_000, pf_id=None):
    if reasons is None:
        reasons = () if mode is EntriesMode.ACTIVE else (ReasonCode.OPERATOR_PAUSE,)
    if mode is EntriesMode.HOLD and hold_kind is None:
        hold_kind = HoldKind.NORMAL
    if hold_kind is HoldKind.DURABILITY_UNAVAILABLE and ReasonCode.RECOVERY_DURABILITY_UNAVAILABLE not in reasons:
        reasons = tuple(reasons) + (ReasonCode.RECOVERY_DURABILITY_UNAVAILABLE,)
    empty = not (positions or intents or entry_stops)
    own = Ownership.KNOWN_EMPTY if empty else Ownership.KNOWN
    prf = prf or proof(ProofKind.FLAT_SNAPSHOT if empty else ProofKind.JOURNAL)
    return Portfolio(portfolio_id=pf_id or make_id('pf', int(acct[5:], 16)), account_id=acct, generation=generation,
                     ownership=own, proof=prf, entries_mode=mode, mode_since_ms=since, pause_reasons=tuple(reasons),
                     positions=tuple(positions), intents=tuple(intents), entry_stops=tuple(entry_stops),
                     hold_kind=hold_kind)


def unknown_portfolio(acct, hold_kind=HoldKind.NORMAL):
    reasons = (ReasonCode.RECOVERY_SCHEMA_INVALID,)
    if hold_kind is HoldKind.DURABILITY_UNAVAILABLE:
        reasons += (ReasonCode.RECOVERY_DURABILITY_UNAVAILABLE,)
    return Portfolio(portfolio_id=make_id('pf', int(acct[5:], 16)), account_id=acct, generation=3,
                     ownership=Ownership.UNKNOWN, proof=None, entries_mode=EntriesMode.HOLD, mode_since_ms=T0,
                     pause_reasons=reasons, positions=None, intents=None, entry_stops=None, hold_kind=hold_kind)


def single_lot_portfolio(seed=1, **kw):
    ids = Ids(seed)
    acct = ids.id('acct')
    lt, extra = lot(ids, acct, **kw)
    return portfolio(acct, (position(ids, [lt]),), extra), ids


def maker_entry(ids, acct, symbol='XRPUSDT', state=IntentState.WORKING, created=T0, reason=None):
    return intent(ids, acct, Purpose.ENTRY, symbol, Side.LONG, D('10'), state=state, order_type=OrderType.LIMIT_POST_ONLY,
                  price=D('2.01'), created=created, reason=reason)


def trailing_entry(ids, acct, symbol='ADAUSDT', state=IntentState.DURABLE, created=T0):
    return intent(ids, acct, Purpose.ENTRY, symbol, Side.SHORT, D('100'), state=state,
                  arm=Arming(trigger_price=D('0.71'), expires_at_ms=created + 3_600_000), created=created)


def market_entry(ids, acct, symbol='DOGEUSDT', state=IntentState.UNKNOWN, seen=D('400'), created=T0, reason=None,
                 authorized_by=None):
    return intent(ids, acct, Purpose.ENTRY, symbol, Side.LONG, D('500'), state=state, seen_qty=seen, created=created,
                  reason=reason, authorized_by=authorized_by)


# ----------------------------------------------------------------------------------------------------------- random
def gen_portfolio(rng, *, max_positions=6):
    """A random VALID portfolio from a random.Random (seeded fixtures) or Hypothesis' st.randoms()."""
    ids = Ids(rng.getrandbits(64))
    acct = ids.id('acct')
    positions, intents, entry_stops = [], [], []
    keys = rng.sample([(s, sd) for s in SYMBOLS for sd in Side], rng.randint(0, max_positions))
    for sym, side in keys:
        lots = []
        for _ in range(rng.randint(1, 3)):
            qty = D(rng.randint(1, 5000)) / 100
            price = D(rng.randint(100, 900_000)) / 100
            pc = D(rng.randint(1, 300)) / 100 if rng.random() < 0.3 else None
            lt, extra = lot(ids, acct, sym, side, qty, price, stop_state=rng.choice(STOP_STATES),
                            in_flight=rng.choice((None, None, 'add', 'reduce', 'close')),
                            source=rng.choice((LotSource.STRATEGY, LotSource.MANUAL, LotSource.ADOPTED)), partial_close=pc)
            lots.append(lt)
            intents += extra
        positions.append(position(ids, lots))
    mode = rng.choice(tuple(EntriesMode))
    for _ in range(rng.randint(0, 3)):
        kind = rng.choice(('maker', 'trailing', 'market'))
        if kind == 'maker':
            intents.append(maker_entry(ids, acct, state=IntentState.WORKING if mode is EntriesMode.ACTIVE
                                       else IntentState.CANCELLING))
        elif kind == 'trailing':
            intents.append(trailing_entry(ids, acct, state=IntentState.DURABLE if mode is EntriesMode.ACTIVE
                                          else IntentState.CANCELLING))
        else:
            seen = D(rng.randint(0, 500))
            me = market_entry(ids, acct, seen=seen)
            intents.append(me)
            if seen > 0 and rng.random() < 0.5:
                prov, extra = protection(ids, acct, me.intent_id, me.symbol, me.side, seen, D('0.12'),
                                         rng.choice(('pending', 'confirmed', 'none')))
                entry_stops.append(prov)
                intents += extra
    for _ in range(rng.randint(0, 2)):          # orphan cancels: owned stop orders no record carries any more
        intents.append(intent(ids, acct, Purpose.PROTECT, rng.choice(SYMBOLS), rng.choice(tuple(Side)), D('1'),
                              state=IntentState.CANCELLING, owner_id=ids.id('lot'), stop_price=D('9.5'),
                              reason=ReasonCode.PROTECT_RESIZE))
    rng.shuffle(intents)
    hold = rng.choice(tuple(HoldKind)) if mode is EntriesMode.HOLD else None
    return portfolio(acct, positions, intents, entry_stops=entry_stops, mode=mode, hold_kind=hold,
                     generation=rng.randint(1, 10_000))


def typical_portfolio(seed=42):
    """A representative live portfolio: 8 positions, 11 lots, ~22 open intents."""
    ids = Ids(seed)
    acct = ids.id('acct')
    positions, intents = [], []
    for n, sym in enumerate(SYMBOLS):
        lots = []
        for k in range(1 + (n % 3 == 0)):
            lt, extra = lot(ids, acct, sym, Side.LONG if n % 2 else Side.SHORT, D('1.5') + k, D('100') + n,
                            in_flight='add' if k else None)
            lots.append(lt)
            intents += extra
        positions.append(position(ids, lots))
    intents += [maker_entry(ids, acct), trailing_entry(ids, acct)]
    return portfolio(acct, positions, intents)


AUTHORITY_OF = {'operator': Authority.OPERATOR, 'protect': Authority.PROTECTION, 'recovery': Authority.RECOVERY,
                'reconcile': Authority.RECONCILIATION}


def authority_for(action, reason):
    if reason.namespace == 'operator' or reason in (ReasonCode.ENTRY_MANUAL, ReasonCode.ENTRY_ONE_SHOT) \
            or action is Action.RESUME:
        return Authority.OPERATOR
    return AUTHORITY_OF.get(reason.namespace, Authority.STRATEGY)


def decision_with_intents(ids, acct, action, reason):
    """A decision of `action` with the minimal PLANNED intent set it needs (ids / timestamps wired)."""
    dec_id = ids.id('dec')
    lot_id = ids.id('lot')
    kw = dict(state=IntentState.PLANNED, decision_id=dec_id)
    mk = {
        Action.PROTECT: lambda: [intent(ids, acct, Purpose.PROTECT, owner_id=lot_id, stop_price=D('95'), **kw)],
        Action.CLOSE: lambda: [intent(ids, acct, Purpose.CLOSE, owner_id=lot_id, reason=reason, **kw)],
        Action.FLATTEN: lambda: [intent(ids, acct, Purpose.CLOSE, owner_id=lot_id, reason=reason, **kw)],
        Action.REDUCE: lambda: [intent(ids, acct, Purpose.REDUCE, owner_id=lot_id, reason=reason, **kw)],
        Action.ADD: lambda: [intent(ids, acct, Purpose.ADD, owner_id=lot_id, reason=reason, **kw)],
        Action.ENTER: lambda: [intent(ids, acct, Purpose.ENTRY, reason=reason, slot_id='S1', **kw)],
    }.get(action, lambda: [])
    return Decision(decision_id=dec_id, account_id=acct, at_ms=T0, action=action, reason=reason,
                    authority=authority_for(action, reason), intents=tuple(mk()))


def replace(rec, **kw):
    return dataclasses.replace(rec, **kw)
