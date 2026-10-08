"""Deterministic builders of VALID NC-01 records for the tests (stdlib only; no Hypothesis, contract r2 6.2).

Every id comes from a seeded random.Random, so a seed reproduces the exact records; nothing reads a clock or uuid4.
The domain has NO field defaults (one strict dialect); `build()` is a TEST-ONLY convenience that passes an explicit
None / () / False / 0 for optional fields a builder leaves out, so the factories stay readable."""
import dataclasses
import random
import typing
from decimal import Decimal as D

from newcore.domain import (Account, AccountBinding, Action, Arming, Authority, BindingConfirmation, BindingState,
                            Capability, Decision, EntriesMode, Environment, Fill, HoldKind, InstrumentId, InstrumentRules,
                            IntentState, Lot, LotSource, MissPhase, OrderIntent, OrderResult, OrderType, OwnerKind,
                            Ownership,
                            OwnershipProof, Portfolio, Position, PositionRead, ProofKind, Protection, Purpose, ReasonCode,
                            Side, StopMiss, Venue, confirmation_phrase, make_id)
from newcore.domain.base import field_spec

T0 = 1_791_400_000_000                      # 2026-10-07 UTC, integer ms
DIGEST = '0123456789abcdef'
SYMBOLS = ('SOLUSDT', 'XRPUSDT', 'ETHUSDT', 'BTCUSDT', 'DOGEUSDT', 'ADAUSDT', 'LINKUSDT', 'AVAXUSDT')
STOP_STATES = ('none', 'pending', 'unverified', 'confirmed', 'releasing', 'checking', 'restoring', 'owner_check',
               'replacing')
_EMPTY = {bool: False, int: 0}


def build(cls, **kw):
    """TEST-ONLY: construct `cls` with an explicit empty value for every optional / collection / flag field left out."""
    for name, tp, _ in field_spec(cls):
        if name in kw:
            continue
        args = typing.get_args(tp)
        if type(None) in args:
            kw[name] = None
        elif typing.get_origin(tp) is tuple:
            kw[name] = ()
        elif tp in _EMPTY and name in ('tp1_done', 'adds_done', 'hedge_mode'):
            kw[name] = _EMPTY[tp]
        elif tp is str and name in ('detail', 'policy_version'):
            kw[name] = ''
    return cls(**kw)


def replace(rec, **kw):
    return dataclasses.replace(rec, **kw)


class Ids:
    def __init__(self, seed):
        self.rng = random.Random(seed)

    def id(self, prefix):
        return make_id(prefix, self.rng.getrandbits(128))

    def cid(self, kind='e'):
        return f'z{kind}{self.rng.getrandbits(88):022x}'


def pf_id(acct):
    return make_id('pf', int(acct[5:], 16))


def binding(digest=DIGEST, env=Environment.TESTNET):
    return build(AccountBinding, venue=Venue.BINANCE_USDM, environment=env, settlement_asset='USDT', key_digest=digest)


def account(acct, digest=DIGEST, state=BindingState.CONFIRMED):
    conf = None if state is BindingState.UNCONFIRMED else BindingConfirmation(
        account_id=acct, old_key_digest=None, new_key_digest=digest, typed_phrase=confirmation_phrase(acct, digest),
        confirmed_at_ms=T0)
    return build(Account, account_id=acct, label='testnet main', hedge_mode=True, binding=binding(digest),
                 binding_state=state, confirmation=conf)


def rules(symbol='SOLUSDT', caps=(Capability.HEDGE_MODE, Capability.POST_ONLY, Capability.STOP_MARKET,
                                  Capability.REDUCE_ONLY)):
    return InstrumentRules(instrument=InstrumentId(venue=Venue.BINANCE_USDM, symbol=symbol), tick_size=D('0.01'),
                           step_size=D('0.01'), min_qty=D('0.01'), max_qty=D('10000'), min_notional=D('5'),
                           capabilities=tuple(caps))


DEFAULT_REASON = {Purpose.ENTRY: ReasonCode.ENTRY_SIGNAL, Purpose.ADD: ReasonCode.ENTRY_PYRAMID,
                  Purpose.REDUCE: ReasonCode.EXIT_TP1, Purpose.CLOSE: ReasonCode.EXIT_TIME,
                  Purpose.PROTECT: ReasonCode.PROTECT_PLACE}


def intent(ids, acct, purpose, symbol='SOLUSDT', side=Side.LONG, qty=D('1.5'), *, state=IntentState.SUBMITTED,
           order_type=OrderType.MARKET, owner_id=None, price=None, stop_price=None, arm=None, seen_qty=None,
           created=T0, decision_id=None, slot_id=None, reason=None, authorized_by=None, alt=None, owner_kind=None,
           replaces=None, stop_distance=None):
    reason = reason or DEFAULT_REASON[purpose]
    if owner_id is None:
        owner_kind = None
    elif owner_kind is None:
        owner_kind = OwnerKind.LOT                  # the explicit kind; tests pass ENTRY_INTENT / PORTFOLIO when meant
    if purpose is Purpose.PROTECT:
        order_type = OrderType.STOP_MARKET
    if purpose is Purpose.ENTRY and slot_id is None and reason is ReasonCode.ENTRY_SIGNAL:
        slot_id = 'S1'
    return OrderIntent(intent_id=ids.id('int'), account_id=acct, decision_id=decision_id or ids.id('dec'),
                       client_order_id=ids.cid(purpose.value[0]), purpose=purpose, order_type=order_type, state=state,
                       symbol=symbol, side=side, qty=qty, reason=reason, created_at_ms=created, owner_id=owner_id,
                       owner_kind=owner_kind,
                       slot_id=slot_id, price=price, stop_price=stop_price, arm=arm, alt_client_order_id=alt,
                       seen_qty=seen_qty, authorized_by=authorized_by, replaces_intent_id=replaces,
                       stop_distance=(stop_distance or D('5')) if purpose is Purpose.ENTRY else stop_distance)


def stop_level(side, price):
    return max((price * D('0.95') if side is Side.LONG else price * D('1.05')).quantize(D('0.01')), D('0.01'))


def miss(phase, count=1, foreign=None):
    return StopMiss(phase=phase, count=count, since_ms=T0 + 1000, foreign_order_id=foreign)


def protection(ids, acct, owner_id, symbol, side, qty, price, stop_state, owner_kind=OwnerKind.LOT):
    """(Protection, [its PROTECT intents]) for one of STOP_STATES."""
    level = stop_level(side, price)
    order_state = {'pending': IntentState.SUBMITTED, 'unverified': IntentState.WORKING, 'confirmed': IntentState.WORKING,
                   'releasing': IntentState.CANCELLING, 'checking': IntentState.WORKING,
                   'replacing': IntentState.WORKING}.get(stop_state)
    extra, order, repl = [], None, None
    if order_state is not None:
        it = intent(ids, acct, Purpose.PROTECT, symbol, side, qty, state=order_state, owner_id=owner_id, stop_price=level,
                    owner_kind=owner_kind)
        extra.append(it)
        order = it.intent_id
    if stop_state == 'replacing':
        new = intent(ids, acct, Purpose.PROTECT, symbol, side, qty, state=IntentState.SUBMITTED, owner_id=owner_id,
                     stop_price=level + D('0.01'), reason=ReasonCode.PROTECT_REPLACE, owner_kind=owner_kind)
        extra.append(new)
        repl = new.intent_id
    m = {'checking': miss(MissPhase.CHECKING), 'restoring': miss(MissPhase.RESTORING, 2),
         'owner_check': miss(MissPhase.OWNER_CHECK, 3, '8123456789')}.get(stop_state)
    prot = Protection(owner_id=owner_id, price=level, qty=qty, order=order, replacement=repl,
                      confirmed_at_ms=T0 + 2000 if stop_state in ('confirmed', 'replacing') else None, miss=m)
    return prot, extra


def fill(reason, qty, price, *, at=T0, result_id=None, decision_id=None, fee=D('0.075')):
    return Fill(at_ms=at, reason=reason, qty=qty, price=price, fee=fee, result_id=result_id, decision_id=decision_id)


def lot(ids, acct, symbol='SOLUSDT', side=Side.LONG, qty=D('1.5'), price=D('100'), *, stop_state='confirmed',
        in_flight=None, source=LotSource.STRATEGY, partial_close=None):
    """(Lot, [intents it carries]). in_flight: None | 'add' | 'reduce' | 'close'. partial_close: the lot opens with
    qty + partial_close, then closes partial_close."""
    lot_id = ids.id('lot')
    adopted = ids.id('dec') if source is LotSource.ADOPTED else None
    open_qty = qty + (partial_close or 0)
    fills = [fill(ReasonCode.ENTRY_ADOPTED if adopted else ReasonCode.ENTRY_SIGNAL, open_qty, price,
                  result_id=None if adopted else ids.id('res'), decision_id=adopted)]
    if partial_close:
        fills.append(fill(ReasonCode.EXIT_TP1, partial_close, price + 1, at=T0 + 60_000, result_id=ids.id('res'),
                          fee=D('0.07')))
    prot, extra = protection(ids, acct, lot_id, symbol, side, qty, price, stop_state)
    flight = None
    if in_flight:
        purpose = {'add': Purpose.ADD, 'reduce': Purpose.REDUCE, 'close': Purpose.CLOSE}[in_flight]
        q = qty if purpose is Purpose.CLOSE else max((qty / 2).quantize(D('0.01')), D('0.01'))
        fl = intent(ids, acct, purpose, symbol, side, q, state=IntentState.UNKNOWN, owner_id=lot_id, created=T0 + 3000)
        extra.append(fl)
        flight = fl.intent_id
    strategy = source is LotSource.STRATEGY
    lt = build(Lot, lot_id=lot_id, account_id=acct, symbol=symbol, side=side, source=source,
               slot_id='S1' if strategy else None, timeframe='4h' if strategy else None, opened_at_ms=T0, qty=qty,
               avg_price=price, initial_qty=open_qty, max_qty=open_qty, risk_distance=D('5'), risk_usd=D('7.5'),
               stop=prot, fills=tuple(fills), in_flight=flight, adopted_by=adopted)
    return lt, extra


def position(ids, lots):
    return Position(position_id=ids.id('pos'), symbol=lots[0].symbol, side=lots[0].side, lots=tuple(lots))


def proof(kind=ProofKind.JOURNAL, ids=None, digest=DIGEST):
    ids = ids or Ids(0)
    if kind is ProofKind.JOURNAL:
        return build(OwnershipProof, kind=kind, at_ms=T0, through_sequence=1)
    if kind is ProofKind.OWNER_ADOPTED:
        return build(OwnershipProof, kind=kind, at_ms=T0, decision_id=ids.id('dec'))
    if kind is ProofKind.FLAT_SNAPSHOT:
        return build(OwnershipProof, kind=kind, at_ms=T0, reconciliation_id=ids.id('rec'), key_digest=digest)
    return build(OwnershipProof, kind=kind, at_ms=T0, reconciliation_id=ids.id('rec'))


def portfolio(acct, positions=(), intents=(), *, entry_stops=(), mode=EntriesMode.ACTIVE, hold_kind=None, reasons=None,
              generation=7, prf=None, since=T0 + 10_000):
    if reasons is None:
        reasons = () if mode is EntriesMode.ACTIVE else (ReasonCode.OPERATOR_PAUSE,)
    if mode is EntriesMode.HOLD and hold_kind is None:
        hold_kind = HoldKind.NORMAL
    if hold_kind is HoldKind.DURABILITY_UNAVAILABLE and ReasonCode.RECOVERY_DURABILITY_UNAVAILABLE not in reasons:
        reasons = tuple(reasons) + (ReasonCode.RECOVERY_DURABILITY_UNAVAILABLE,)
    empty = not (positions or intents or entry_stops)
    own = Ownership.KNOWN_EMPTY if empty else Ownership.KNOWN
    prf = prf or proof(ProofKind.FLAT_SNAPSHOT if empty else ProofKind.JOURNAL)
    return Portfolio(portfolio_id=pf_id(acct), account_id=acct, generation=generation, ownership=own, proof=prf,
                     entries_mode=mode, mode_since_ms=since, pause_reasons=tuple(reasons), positions=tuple(positions),
                     intents=tuple(intents), entry_stops=tuple(entry_stops), hold_kind=hold_kind)


def unknown_portfolio(acct, hold_kind=HoldKind.NORMAL):
    reasons = (ReasonCode.RECOVERY_SCHEMA_INVALID,)
    if hold_kind is HoldKind.DURABILITY_UNAVAILABLE:
        reasons += (ReasonCode.RECOVERY_DURABILITY_UNAVAILABLE,)
    return Portfolio(portfolio_id=pf_id(acct), account_id=acct, generation=3, ownership=Ownership.UNKNOWN, proof=None,
                     entries_mode=EntriesMode.HOLD, mode_since_ms=T0, pause_reasons=reasons, positions=None, intents=None,
                     entry_stops=None, hold_kind=hold_kind)


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


def orphan_stop(ids, acct, symbol='LINKUSDT', side=Side.SHORT):
    """Cancel-only work: an owned stop whose lot is gone, re-owned by the portfolio aggregate."""
    return intent(ids, acct, Purpose.PROTECT, symbol, side, D('1'), state=IntentState.CANCELLING, owner_id=pf_id(acct),
                  stop_price=D('9.5'), reason=ReasonCode.PROTECT_RESIZE, owner_kind=OwnerKind.PORTFOLIO)


# ----------------------------------------------------------------------------------------------------------- random
def gen_portfolio(rng, *, max_positions=6):
    """A random VALID portfolio from a seeded random.Random."""
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
                                         rng.choice(('pending', 'confirmed', 'none')), OwnerKind.ENTRY_INTENT)
                entry_stops.append(prov)
                intents += extra
    for _ in range(rng.randint(0, 2)):
        intents.append(orphan_stop(ids, acct, rng.choice(SYMBOLS), rng.choice(tuple(Side))))
    rng.shuffle(intents)
    hold = rng.choice(tuple(HoldKind)) if mode is EntriesMode.HOLD else None
    return portfolio(acct, positions, intents, entry_stops=entry_stops, mode=mode, hold_kind=hold,
                     generation=rng.randint(1, 10_000))


def typical_portfolio(seed=42):
    """A representative live portfolio: 8 positions, 11 lots, ~18 open intents."""
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


def decision(ids, acct, action, reason, intents=(), *, dec_id=None, at=T0):
    return build(Decision, decision_id=dec_id or ids.id('dec'), account_id=acct, at_ms=at, action=action, reason=reason,
                 authority=authority_for(action, reason), intents=tuple(intents))


def decision_key(symbol='SOLUSDT', side=Side.LONG, purpose=Purpose.ENTRY, candle=T0 - 60_000):
    from newcore.domain import DecisionKey
    return DecisionKey(strategy='ema_st', strategy_version='v3', symbol=symbol, side=side, candle_close_ms=candle,
                       purpose=purpose)


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
    return decision(ids, acct, action, reason, mk(), dec_id=dec_id)


def results(ids, acct, it):
    """A FINAL / KNOWN / UNKNOWN result for each evidence family, all valid for intent `it` (submitted at T0)."""
    from newcore.domain import Evidence, ExchangeStatus, Lookup, ResultPhase
    base = dict(intent_id=it.intent_id, account_id=acct, client_order_id=it.client_order_id, requested_qty=it.qty,
                observed_at_ms=T0 + 30_000)
    reads = (PositionRead(at_ms=T0 + 25_000, qty=it.qty), PositionRead(at_ms=T0 + 26_000, qty=it.qty))
    R = lambda **kw: build(OrderResult, result_id=ids.id('res'), **base, **kw)        # noqa: E731
    return {
        'unknown': R(phase=ResultPhase.UNKNOWN),
        'not_found': R(phase=ResultPhase.UNKNOWN, lookup=Lookup.NOT_FOUND),
        'known': R(phase=ResultPhase.KNOWN, exchange_order_id='991', exchange_status=ExchangeStatus.PARTIALLY_FILLED),
        'filled': R(phase=ResultPhase.FINAL, exchange_order_id='991', exchange_status=ExchangeStatus.FILLED,
                    executed_qty=it.qty, avg_price=D('100.5'), evidence=Evidence.EXCHANGE_FINAL),
        'partial_cancel': R(phase=ResultPhase.FINAL, exchange_order_id='991', exchange_status=ExchangeStatus.CANCELED,
                            executed_qty=(it.qty / 3).quantize(D('0.01')), avg_price=D('100.5'),
                            evidence=Evidence.EXCHANGE_FINAL),
        'refused': R(phase=ResultPhase.FINAL, executed_qty=D('0'), evidence=Evidence.EXCHANGE_REFUSED),
        'corroborated': R(phase=ResultPhase.FINAL, executed_qty=D('0'), evidence=Evidence.NOT_FOUND_CORROBORATED,
                          corroboration=reads, resolved_by=ids.id('dec')),
        'adopted': R(phase=ResultPhase.FINAL, executed_qty=it.qty, avg_price=D('100'), evidence=Evidence.POSITION_ADOPTED,
                     corroboration=reads, resolved_by=ids.id('dec')),
    }


def event(cls, ids, acct, sequence, *, at=T0, reason=ReasonCode.LIFECYCLE_DRAIN, aggregate=None, event_id=None, **kw):
    return build(cls, event_id=event_id or ids.id('evt'), account_id=acct, aggregate_id=aggregate or pf_id(acct),
                 sequence=sequence, at_ms=at, reason=reason, **kw)


def samples(seed=5):
    """One valid instance of every document record type (codec round trips / snapshots)."""
    from newcore.domain import (BindingChanged, DecisionRecorded, HighWater, IntentRecorded, IntentStateChanged,
                                ModeChanged, ResultObserved, Snapshot)
    ids = Ids(seed)
    pf = typical_portfolio(seed)
    acct = pf.account_id
    it = market_entry(ids, acct)
    res = results(ids, acct, it)
    dec = decision_with_intents(ids, acct, Action.ENTER, ReasonCode.ENTRY_SIGNAL)
    durable = replace(dec.intents[0], state=IntentState.DURABLE)
    return [account(acct), rules(), it, pf, dec, decision_key(), unknown_portfolio(acct), *res.values(),
            Snapshot(account_id=acct, generation=7, last_sequence=4, written_at_ms=T0, writer_build='nc01-test',
                     portfolio=pf),
            HighWater(account_id=acct, generation=7, last_sequence=4, writer_build='nc01-test'),
            event(IntentRecorded, ids, acct, 1, intent=durable, reason=durable.reason),
            event(IntentStateChanged, ids, acct, 2, at=T0 + 1, intent_id=it.intent_id, from_state=IntentState.DURABLE,
                  to_state=IntentState.SUBMITTED),
            event(ResultObserved, ids, acct, 3, at=T0 + 2, result=res['filled']),
            event(DecisionRecorded, ids, acct, 4, decision=dec, reason=dec.reason),
            event(ModeChanged, ids, acct, 5, from_mode=EntriesMode.ACTIVE, to_mode=EntriesMode.HOLD,
                  reasons=(ReasonCode.RECOVERY_DURABILITY_UNAVAILABLE,), to_hold=HoldKind.DURABILITY_UNAVAILABLE,
                  reason=ReasonCode.RECOVERY_DURABILITY_UNAVAILABLE),
            event(BindingChanged, ids, acct, 6, from_state=BindingState.UNCONFIRMED, to_state=BindingState.CONFIRMED,
                  binding=binding(), confirmation=account(acct).confirmation, reason=ReasonCode.BINDING_UNCONFIRMED),
            *standalone_samples(ids, pf, res),
            incident_event(ids, acct, 7, pf),
            management_input_event(ids, acct, 8, pf)]


def incident(ids, acct, pf=None, kind=ReasonCode.RECONCILE_MANUAL_CLOSE, **kw):
    from newcore.domain import Incident
    lt = pf.lots[0] if pf is not None else None
    base = dict(incident_id=ids.id('inc'), account_id=acct, kind=kind, at_ms=T0 + 500,
                symbol=lt.symbol if lt else None, side=lt.side if lt else None,
                intent_refs=(lt.in_flight,) if lt is not None and lt.in_flight else (),
                lot_refs=(lt.lot_id,) if lt else (), position_refs=(pf.positions[0].position_id,) if pf else (),
                evidence=(ids.id('rec'),), detail='position flat on the venue, lot open in the journal')
    base.update(kw)
    return Incident(**base)


def incident_event(ids, acct, sequence, pf=None, **kw):
    from newcore.domain import IncidentRecorded
    inc = incident(ids, acct, pf, **kw)
    return event(IncidentRecorded, ids, acct, sequence, at=T0 + 600, incident=inc, reason=inc.kind)


def candle_input(open_ms=T0, o='100', h='104', lo='98.5', c='103.25'):
    from newcore.domain import CandleInput
    return CandleInput(open_ms=open_ms, open=D(o), high=D(h), low=D(lo), close=D(c))


def fill_observation(trade_id='5001', at=T0 + 3_600_000, qty='0.75', price='103.5', fee='0.0388', asset='USDT',
                     xid='88001'):
    from newcore.domain import FillObservation
    return FillObservation(trade_id=trade_id, exchange_order_id=xid, at_ms=at, qty=D(qty), price=D(price), fee=D(fee),
                           fee_asset=asset)


def management_input(ids, acct, lot, decision_id=None, **kw):
    """A closed-candle tick of `lot` with one execution observed (r3 draft item 7)."""
    from newcore.domain import ManagementInput
    base = dict(account_id=acct, lot_id=lot.lot_id, decision_id=decision_id or ids.id('dec'), symbol=lot.symbol,
                side=lot.side, candle=candle_input(), close_request=ReasonCode.EXIT_SIGNAL, mark_price=None,
                fills=(fill_observation(),))
    base.update(kw)
    return ManagementInput(**base)


def management_input_event(ids, acct, sequence, pf, at=T0 + 4 * 3_600_000, **kw):
    from newcore.domain import ManagementInputRecorded
    x = management_input(ids, acct, pf.lots[0], **kw)
    return event(ManagementInputRecorded, ids, acct, sequence, at=at, input=x, reason=ReasonCode.MANAGE_TICK)


def standalone_samples(ids, pf, res):
    """The contract-2 required types that are otherwise nested (Cowork H05; Codex ruling: exactly these five)."""
    lt = pf.lots[0]
    return [binding(), rules().instrument, lt.stop, lt, pf.positions[0]]


SNAPSHOT_FILE = 'fixtures/nc01_samples_v1.jsonl'


def snapshot_lines():
    """The deterministic serialization snapshot: one canonical document per sample, in samples() order.
    Regenerate (only for an intended, reviewed format change) with:
        python -c "import sys; sys.path[:0]=['.','tests/newcore']; import nc01_factories as F; F.write_snapshot()"
    """
    from newcore.domain import dumps
    return [dumps(s) for s in samples()]


def write_snapshot():
    import os
    path = os.path.join(os.path.dirname(os.path.abspath(__file__)), SNAPSHOT_FILE)
    with open(path, 'w', encoding='ascii', newline='\n') as f:
        f.write('\n'.join(snapshot_lines()) + '\n')
