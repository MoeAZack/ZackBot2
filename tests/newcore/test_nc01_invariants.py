"""NC-01 invalid states rejected: one table row per constructor / cross-record invariant (contract 6.1).

Every row starts from a VALID record, breaks exactly one rule, and expects InvalidRecord whose path names the field."""
from decimal import Decimal as D

import pytest

import nc01_factories as F
from nc01_factories import T0, replace
from newcore.domain import (Account, AccountBinding, Action, Arming, Authority, BindingChanged, BindingConfirmation,
                            BindingState, Capability, EntriesMode, Environment, Evidence, ExchangeStatus,
                            HoldKind, IntentRecorded, IntentState, IntentStateChanged, InvalidRecord, Lookup, LotSource,
                            MissPhase, ModeChanged, OrderType, Ownership, OwnershipProof, Position, PositionRead,
                            ProofKind, Protection, Purpose, ReasonCode, Side, Snapshot, StopMiss,
                            Venue, confirmation_phrase, require_environment)

ids = F.Ids(1234)
ACCT = ids.id('acct')
OTHER = ids.id('acct')
ENTRY = F.market_entry(ids, ACCT)
ADD = F.intent(ids, ACCT, Purpose.ADD, owner_id=ids.id('lot'))
STOP = F.intent(ids, ACCT, Purpose.PROTECT, owner_id=ids.id('lot'), stop_price=D('95'), state=IntentState.WORKING)
MAKER = F.maker_entry(ids, ACCT)
RES = F.results(ids, ACCT, ENTRY)
PF, _ = F.single_lot_portfolio(7, stop_state='confirmed', in_flight='add')
LOT = PF.lots[0]
POS = PF.positions[0]
ACC = F.account(ACCT)
B = F.binding()
DEC = F.decision_with_intents(ids, ACCT, Action.ENTER, ReasonCode.ENTRY_SIGNAL)
PROT = LOT.stop
FILL = LOT.fills[0]


def prot_pf(**kw):
    """PF with its lot stop replaced (the carrying intent kept)."""
    lt = replace(LOT, stop=replace(PROT, **kw))
    return replace(PF, positions=(replace(POS, lots=(lt,)),))


def with_intents(*extra, base=PF, **kw):
    return replace(base, intents=base.intents + tuple(extra), **kw)


def paused(**kw):
    kw.setdefault('entries_mode', EntriesMode.PAUSED)
    kw.setdefault('pause_reasons', (ReasonCode.OPERATOR_PAUSE,))
    return kw


CASES = {
    # ---- values (contract invariants 2-4)
    'float qty': (lambda: replace(ENTRY, qty=1.5), 'qty'),
    'bool qty': (lambda: replace(ENTRY, qty=True), 'qty'),
    'int qty': (lambda: replace(ENTRY, qty=2), 'qty'),
    'NaN qty': (lambda: replace(ENTRY, qty=D('NaN')), 'qty'),
    'Infinity price': (lambda: replace(MAKER, price=D('Infinity')), 'price'),
    'huge qty 1e300': (lambda: replace(ENTRY, qty=D('1e300')), 'qty'),
    'adjusted exponent below -18': (lambda: replace(ENTRY, qty=D('0.0000000000000000001')), 'qty'),
    'adjusted exponent above 18': (lambda: replace(ENTRY, qty=D('10000000000000000000')), 'qty'),
    '39 significant digits': (lambda: replace(ENTRY, qty=D('1.' + '1' * 38)), 'qty'),
    'sNaN qty': (lambda: replace(ENTRY, qty=D('sNaN')), 'qty'),
    'negative zero fee': (lambda: replace(FILL, fee=D('-0')), 'fee'),
    'zero qty': (lambda: replace(ENTRY, qty=D('0')), 'qty'),
    'negative qty': (lambda: replace(ENTRY, qty=D('-1')), 'qty'),
    'timestamp in seconds': (lambda: replace(ENTRY, created_at_ms=1_791_400_000), 'created_at_ms'),
    'timestamp as ISO text': (lambda: replace(ENTRY, created_at_ms='2026-10-07T00:00:00Z'), 'created_at_ms'),
    'timestamp as float': (lambda: replace(ENTRY, created_at_ms=float(T0)), 'created_at_ms'),
    'timestamp as bool': (lambda: replace(FILL, at_ms=True), 'at_ms'),
    'time-derived lot id': (lambda: replace(LOT, lot_id='S1|SOLUSDT|LONG|1791400000'), 'lot_id'),
    'wrong id prefix': (lambda: replace(LOT, lot_id=ids.id('int')), 'lot_id'),
    'uppercase id hex': (lambda: replace(LOT, lot_id=LOT.lot_id.upper().replace('LOT_', 'lot_')), 'lot_id'),
    'qty-only pending: no client id': (lambda: replace(ADD, client_order_id=''), 'client_order_id'),
    'client id too long': (lambda: replace(ADD, client_order_id='x' * 37), 'client_order_id'),
    'lowercase symbol': (lambda: replace(ENTRY, symbol='solusdt'), 'symbol'),
    'side as text': (lambda: replace(ENTRY, side='LONG'), 'side'),
    'reason as text': (lambda: replace(ENTRY, reason='entry.signal'), 'reason'),
    # ---- instrument rules
    'min above max': (lambda: replace(F.rules(), min_qty=D('20000')), 'max_qty'),
    'min off step': (lambda: replace(F.rules(), min_qty=D('0.015')), 'min_qty'),
    'negative tick': (lambda: replace(F.rules(), tick_size=D('-0.01')), 'tick_size'),
    'duplicate capability': (lambda: replace(F.rules(), capabilities=(Capability.POST_ONLY,) * 2), 'capabilities'),
    # ---- account binding / identity (ruling 5)
    'venue as text': (lambda: replace(B, venue='binance_usdm'), 'venue'),
    'environment as text': (lambda: replace(B, environment='testnet'), 'environment'),
    'testnet identity on mainnet config': (lambda: require_environment(B, Environment.MAINNET), 'environment'),
    'digest not hex16': (lambda: replace(B, key_digest='0123456789ABCDEF'), 'key_digest'),
    'settlement asset lowercase': (lambda: replace(B, settlement_asset='usdt'), 'settlement_asset'),
    'wrong typed phrase': (lambda: replace(ACC.confirmation, typed_phrase='yes'), 'typed_phrase'),
    'rotation to the same key': (lambda: replace(ACC.confirmation, old_key_digest=F.DIGEST), 'new_key_digest'),
    'confirmed with a proposal': (lambda: replace(ACC, proposed_binding=F.binding('fedcba9876543210')), 'proposed_binding'),
    'unconfirmed with confirmation': (lambda: replace(ACC, binding_state=BindingState.UNCONFIRMED), 'confirmation'),
    'confirmation of another key': (lambda: replace(ACC, binding=F.binding('fedcba9876543210')), 'confirmation'),
    'rotation changes environment': (lambda: replace(ACC, binding_state=BindingState.ROTATION_PENDING,
                                                     proposed_binding=F.binding('fedcba9876543210', Environment.MAINNET)),
                                     'proposed_binding'),
    'mismatch equal to binding': (lambda: replace(ACC, binding_state=BindingState.MISMATCH, proposed_binding=F.binding()),
                                  'proposed_binding'),
    'reconciling without a rotation': (lambda: replace(ACC, binding_state=BindingState.RECONCILING), 'confirmation'),
    # ---- order intent (one lifecycle family)
    'entry with exit reason': (lambda: replace(ENTRY, reason=ReasonCode.EXIT_STOP), 'reason'),
    'close with entry reason': (lambda: F.intent(ids, ACCT, Purpose.CLOSE, owner_id=LOT.lot_id,
                                                 reason=ReasonCode.ENTRY_SIGNAL), 'reason'),
    'protect with exit reason': (lambda: replace(STOP, reason=ReasonCode.EXIT_STOP), 'reason'),
    'entry with an owner': (lambda: replace(ENTRY, owner_id=LOT.lot_id), 'owner_id'),
    'add without a lot owner': (lambda: replace(ADD, owner_id=ids.id('int')), 'owner_id'),
    'protect owned by a decision': (lambda: replace(STOP, owner_id=ids.id('dec')), 'owner_id'),
    'slot on an add': (lambda: replace(ADD, slot_id='S1'), 'slot_id'),
    'protect as market order': (lambda: replace(STOP, order_type=OrderType.MARKET), 'order_type'),
    'entry as stop order': (lambda: replace(ENTRY, order_type=OrderType.STOP_MARKET, stop_price=D('1')), 'order_type'),
    'maker close': (lambda: F.intent(ids, ACCT, Purpose.CLOSE, owner_id=LOT.lot_id, order_type=OrderType.LIMIT_POST_ONLY,
                                     price=D('1')), 'order_type'),
    'limit without price': (lambda: replace(MAKER, price=None), 'price'),
    'market with price': (lambda: replace(ENTRY, price=D('1')), 'price'),
    'stop without stop price': (lambda: replace(STOP, stop_price=None), 'stop_price'),
    'stop price on market': (lambda: replace(ENTRY, stop_price=D('1')), 'stop_price'),
    'orphan intent still working': (lambda: replace(STOP, owner_id=F.pf_id(ACCT), owner_kind=F.OwnerKind.PORTFOLIO), 'state'),
    'orphan add submitted': (lambda: replace(ADD, owner_id=F.pf_id(ACCT), owner_kind=F.OwnerKind.PORTFOLIO), 'state'),
    'algo id on an entry': (lambda: replace(ENTRY, alt_client_order_id='zz1'), 'alt_client_order_id'),
    'algo id equals primary': (lambda: replace(STOP, alt_client_order_id=STOP.client_order_id), 'alt_client_order_id'),
    'armed maker': (lambda: replace(MAKER, arm=Arming(trigger_price=D('1'), expires_at_ms=T0 + 1)), 'arm'),
    'arm expires before created': (lambda: replace(F.trailing_entry(ids, ACCT), arm=Arming(trigger_price=D('1'),
                                                                                          expires_at_ms=T0)), 'expires_at_ms'),
    'seen qty on an add': (lambda: replace(ADD, seen_qty=D('1')), 'seen_qty'),
    'seen qty above request': (lambda: replace(ENTRY, seen_qty=D('501')), 'seen_qty'),
    'one-shot on a stop': (lambda: replace(STOP, authorized_by=ids.id('dec')), 'authorized_by'),
    # ---- order result (not-found ambiguity, evidence)
    'not-found carries executed qty': (lambda: replace(RES['not_found'], executed_qty=D('0')), 'OrderResult'),
    'unknown carries evidence': (lambda: replace(RES['unknown'], evidence=Evidence.NOT_FOUND_CORROBORATED), 'OrderResult'),
    'unknown carries status': (lambda: replace(RES['unknown'], exchange_status=ExchangeStatus.NEW), 'exchange_status'),
    'known books partial qty': (lambda: replace(RES['known'], executed_qty=D('1')), 'OrderResult'),
    'known without order id': (lambda: replace(RES['known'], exchange_order_id=None), 'exchange_status'),
    'known with a lookup': (lambda: replace(RES['known'], lookup=Lookup.NOT_FOUND), 'lookup'),
    'final without evidence': (lambda: replace(RES['filled'], evidence=None), 'evidence'),
    'final with a lookup': (lambda: replace(RES['refused'], lookup=Lookup.NOT_FOUND), 'lookup'),
    'executed above requested': (lambda: replace(RES['filled'], executed_qty=D('501')), 'executed_qty'),
    'corroborated not-found books a fill': (lambda: replace(RES['corroborated'], executed_qty=D('1'), avg_price=D('1')),
                                            'evidence'),
    'refusal books a fill': (lambda: replace(RES['refused'], executed_qty=D('1'), avg_price=D('1')), 'evidence'),
    'execution without price': (lambda: replace(RES['filled'], avg_price=None), 'avg_price'),
    'nothing executed with a price': (lambda: replace(RES['refused'], avg_price=D('1')), 'avg_price'),
    'FILLED but partial': (lambda: replace(RES['filled'], executed_qty=D('1')), 'executed_qty'),
    'REJECTED with execution': (lambda: replace(RES['partial_cancel'], exchange_status=ExchangeStatus.REJECTED),
                                'executed_qty'),
    'final record with open status': (lambda: replace(RES['filled'], exchange_status=ExchangeStatus.NEW), 'exchange_status'),
    'refusal with an exchange order': (lambda: replace(RES['refused'], exchange_order_id='12'), 'exchange_status'),
    'corroboration without decision': (lambda: replace(RES['corroborated'], resolved_by=None), 'resolved_by'),
    'corroboration with one read': (lambda: replace(RES['corroborated'], corroboration=RES['corroborated'].corroboration[:1]),
                                    'corroboration'),
    'corroboration reads disagree': (lambda: replace(RES['corroborated'], corroboration=(
        PositionRead(at_ms=T0 + 25_000, qty=D('1')), PositionRead(at_ms=T0 + 26_000, qty=D('2')))), 'corroboration'),
    'corroboration reads out of order': (lambda: replace(RES['corroborated'], corroboration=tuple(
        reversed(RES['corroborated'].corroboration))), 'corroboration'),
    'adoption of nothing': (lambda: replace(RES['adopted'], executed_qty=D('0'), avg_price=None), 'evidence'),
    'adoption above the position': (lambda: replace(RES['adopted'], corroboration=(
        PositionRead(at_ms=T0 + 25_000, qty=D('1')), PositionRead(at_ms=T0 + 26_000, qty=D('1')))), 'executed_qty'),
    'decision on an exchange final': (lambda: replace(RES['filled'], resolved_by=ids.id('dec')), 'resolved_by'),
    'reads on an exchange final': (lambda: replace(RES['filled'], corroboration=RES['corroborated'].corroboration),
                                   'corroboration'),
    # ---- protection (own facts, bounds)
    'confirmed and missing': (lambda: replace(PROT, miss=F.miss(MissPhase.OWNER_CHECK)),
                              'confirmed_at_ms'),
    'confirmed without order': (lambda: replace(PROT, order=None), 'confirmed_at_ms'),
    'checking without order': (lambda: F.build(Protection, owner_id=LOT.lot_id, price=D('1'), qty=D('1'),
                                                   miss=F.miss(MissPhase.CHECKING)), 'miss'),
    'foreign id outside owner check': (lambda: StopMiss(phase=MissPhase.RESTORING, count=1, since_ms=T0,
                                                        foreign_order_id='77'), 'foreign_order_id'),
    'miss count zero': (lambda: F.miss(MissPhase.RESTORING, 0), 'count'),
    'replacement is the order': (lambda: replace(PROT, replacement=PROT.order), 'replacement'),
    # ---- lot (fill ledger)
    'ghost lot qty 0': (lambda: replace(LOT, qty=D('0')), 'qty'),
    'slot on a manual lot': (lambda: replace(LOT, source=LotSource.MANUAL), 'slot_id'),
    'strategy lot without timeframe': (lambda: replace(LOT, timeframe=None), 'slot_id'),
    'adopted without decision': (lambda: replace(LOT, source=LotSource.ADOPTED, slot_id=None, timeframe=None), 'adopted_by'),
    'stop of another lot': (lambda: replace(LOT, stop=replace(PROT, owner_id=ids.id('lot'))), 'stop'),
    'unsorted ladder': (lambda: replace(LOT, ladder_done=(2, 1)), 'ladder_done'),
    'no fills': (lambda: replace(LOT, fills=()), 'fills'),
    'opens with a close fill': (lambda: replace(LOT, fills=(replace(FILL, reason=ReasonCode.EXIT_STOP),)), 'fills'),
    'qty differs from ledger': (lambda: replace(LOT, qty=D('2')), 'qty'),
    'max qty differs from ledger': (lambda: replace(LOT, max_qty=D('9')), 'max_qty'),
    'closes more than held': (lambda: replace(LOT, fills=(FILL, replace(FILL, reason=ReasonCode.EXIT_STOP, qty=D('9')))),
                              'fills[1]'),
    'fills out of time order': (lambda: replace(LOT, fills=(FILL, replace(FILL, at_ms=T0 - 1, qty=D('0.5'),
                                                                         reason=ReasonCode.EXIT_TP1))), 'at_ms'),
    'avg outside opening prices': (lambda: replace(LOT, avg_price=D('1')), 'avg_price'),
    'traded lot opens from a decision': (lambda: replace(LOT, fills=(replace(FILL, result_id=None,
                                                                             decision_id=ids.id('dec')),)), 'fills[0]'),
    'fill from two sources': (lambda: replace(FILL, decision_id=ids.id('dec')), 'result_id'),
    'fill with a protect reason': (lambda: replace(FILL, reason=ReasonCode.PROTECT_PLACE), 'reason'),
    # ---- position
    'empty position': (lambda: replace(POS, lots=()), 'lots'),
    'lot of the other side': (lambda: replace(POS, side=Side.SHORT), 'lots'),
    'bad position id': (lambda: replace(POS, position_id=LOT.lot_id), 'position_id'),
    # ---- portfolio: trust (invariant 1)
    'unknown stored as empty': (lambda: replace(F.unknown_portfolio(ACCT), positions=()), 'positions'),
    'known with None': (lambda: replace(PF, positions=None), 'positions'),
    'unknown but active': (lambda: replace(F.unknown_portfolio(ACCT), entries_mode=EntriesMode.ACTIVE, pause_reasons=(),
                                           hold_kind=None), 'entries_mode'),
    'unknown with a proof': (lambda: replace(F.unknown_portfolio(ACCT), proof=F.proof()), 'proof'),
    'known without proof': (lambda: replace(PF, proof=None), 'proof'),
    'empty but KNOWN': (lambda: replace(F.portfolio(ACCT), ownership=Ownership.KNOWN, proof=F.proof()), 'ownership'),
    'KNOWN_EMPTY owning a position': (lambda: replace(PF, ownership=Ownership.KNOWN_EMPTY), 'ownership'),
    'KNOWN_EMPTY by journal': (lambda: replace(F.portfolio(ACCT), proof=F.proof(ProofKind.JOURNAL)), 'proof'),
    'KNOWN by flat snapshot': (lambda: replace(PF, proof=F.proof(ProofKind.FLAT_SNAPSHOT)), 'proof'),
    'flat proof without digest': (lambda: F.build(OwnershipProof, kind=ProofKind.FLAT_SNAPSHOT, at_ms=T0,
                                                         reconciliation_id=ids.id('rec')), 'key_digest'),
    'journal proof with a decision': (lambda: replace(F.proof(), decision_id=ids.id('dec')), 'decision_id'),
    # ---- portfolio: modes
    'hold kind outside HOLD': (lambda: replace(PF, hold_kind=HoldKind.NORMAL), 'hold_kind'),
    'HOLD without kind': (lambda: replace(PF, entries_mode=EntriesMode.HOLD, pause_reasons=(ReasonCode.OPERATOR_PAUSE,)),
                          'hold_kind'),
    'paused without reason': (lambda: replace(PF, entries_mode=EntriesMode.PAUSED), 'pause_reasons'),
    'active with reason': (lambda: replace(PF, pause_reasons=(ReasonCode.OPERATOR_PAUSE,)), 'pause_reasons'),
    'duplicate pause reason': (lambda: replace(PF, **paused(pause_reasons=(ReasonCode.OPERATOR_PAUSE,) * 2)),
                               'pause_reasons'),
    'hard HOLD without its cause': (lambda: replace(PF, entries_mode=EntriesMode.HOLD, hold_kind=HoldKind.DURABILITY_UNAVAILABLE,
                                                    pause_reasons=(ReasonCode.OPERATOR_PAUSE,)), 'pause_reasons'),
    # ---- portfolio: identity / uniqueness
    'two positions for one symbol/side': (lambda: replace(PF, positions=(POS, replace(POS, position_id=ids.id('pos')))),
                                          'positions'),
    'lot of another account': (lambda: replace(PF, account_id=OTHER, portfolio_id=PF.portfolio_id), 'account_id'),
    'intent of another account': (lambda: with_intents(F.maker_entry(ids, OTHER)), 'account_id'),
    'duplicate intent id': (lambda: with_intents(PF.intents[0]), 'intent'),
    'duplicate client id': (lambda: with_intents(replace(MAKER, account_id=PF.account_id,
                                                         client_order_id=PF.intents[0].client_order_id)),
                            'client_order_id'),
    'planned intent owned': (lambda: with_intents(replace(MAKER, account_id=PF.account_id, state=IntentState.PLANNED)),
                             'state'),
    'terminal intent owned': (lambda: with_intents(replace(MAKER, account_id=PF.account_id, state=IntentState.FILLED)),
                              'state'),
    # ---- portfolio: one lifecycle / references (qty-only pending, orphans)
    'in-flight names no intent': (lambda: replace(PF, intents=tuple(i for i in PF.intents if i.purpose is not Purpose.ADD)),
                                  'in_flight'),
    'in-flight names a stop intent': (lambda: replace(PF, positions=(replace(POS, lots=(replace(LOT, in_flight=PROT.order),)),)),
                                      'in_flight'),
    'uncarried lot intent still working': (lambda: replace(PF, positions=(replace(POS, lots=(replace(LOT, in_flight=None),)),)),
                                           'state'),
    'uncarried stop still working': (lambda: prot_pf(order=None, confirmed_at_ms=None), 'state'),
    'stop order differs from protection': (lambda: prot_pf(price=D('94')), 'order'),
    'protection above exposure': (lambda: _oversized(), 'qty'),
    'confirmed but not working': (lambda: _stop_state(IntentState.SUBMITTED), 'confirmed_at_ms'),
    'replacement keeps no old stop': (lambda: _replacement(old_state=IntentState.CANCELLING), 'order'),
    'replacement above exposure': (lambda: _replacement(qty=D('99')), 'replacement'),
    'replacement on another side': (lambda: _replacement(side=Side.SHORT), 'owner_id'),          # a stop on the other side has another owner side
    'foreign id is an owned id': (lambda: prot_pf(confirmed_at_ms=None, miss=F.miss(
        MissPhase.OWNER_CHECK, 1, PF.intents[0].client_order_id)),
        'foreign_order_id'),
    'entry stop above seen size': (lambda: _entry_stop(D('401')), 'qty'),
    'entry stop of a maker': (lambda: _entry_stop(D('1'), owner=MAKER), 'entry_stop'),
    # ---- portfolio: drain + permitted table (rulings 9, hard HOLD)
    'maker working while paused': (lambda: with_intents(replace(MAKER, account_id=PF.account_id), **paused()), 'state'),
    'trailing armed while held': (lambda: with_intents(F.trailing_entry(ids, PF.account_id), entries_mode=EntriesMode.HOLD,
                                                       hold_kind=HoldKind.NORMAL,
                                                       pause_reasons=(ReasonCode.RECONCILE_UNRECONCILED,)), 'state'),
    'manual entry after pause': (lambda: with_intents(F.market_entry(ids, PF.account_id, created=T0 + 20_000,
                                                                     reason=ReasonCode.ENTRY_MANUAL), **paused()), 'intent'),
    'one-shot entry inside HOLD': (lambda: with_intents(F.market_entry(ids, PF.account_id, created=T0 + 20_000,
                                                                       reason=ReasonCode.ENTRY_ONE_SHOT,
                                                                       authorized_by=ids.id('dec')),
                                                        entries_mode=EntriesMode.HOLD, hold_kind=HoldKind.NORMAL,
                                                        pause_reasons=(ReasonCode.RECONCILE_UNRECONCILED,)), 'intent'),
    'reduce placed in hard HOLD': (lambda: _hard_hold_reduce(), 'intent'),
    # ---- decision
    'skip sends intents': (lambda: replace(DEC, action=Action.SKIP, reason=ReasonCode.FILTER_HOURS), 'intents'),
    'skip with an exit reason': (lambda: replace(DEC, action=Action.SKIP, reason=ReasonCode.EXIT_STOP, intents=()), 'reason'),
    'close with entry reason': (lambda: replace(DEC, action=Action.CLOSE), 'reason'),
    'enter with two intents': (lambda: replace(DEC, intents=DEC.intents + (replace(DEC.intents[0], intent_id=ids.id('int')),)),
                               'intents'),
    'enter without intents': (lambda: replace(DEC, intents=()), 'intents'),
    'enter with a close intent': (lambda: replace(DEC, intents=(F.intent(ids, ACCT, Purpose.CLOSE, owner_id=LOT.lot_id,
                                                                         state=IntentState.PLANNED,
                                                                         decision_id=DEC.decision_id),)), 'intents'),
    'intent of another decision': (lambda: replace(DEC, intents=(replace(DEC.intents[0], decision_id=ids.id('dec')),)),
                                   'decision_id'),
    'decision intent already durable': (lambda: replace(DEC, intents=(replace(DEC.intents[0], state=IntentState.DURABLE),)),
                                        'state'),
    'operator reason, strategy authority': (lambda: replace(DEC, action=Action.PAUSE, reason=ReasonCode.OPERATOR_PAUSE,
                                                            intents=()), 'authority'),
    'manual entry, strategy authority': (lambda: replace(DEC, reason=ReasonCode.ENTRY_MANUAL, intents=(
        replace(DEC.intents[0], reason=ReasonCode.ENTRY_MANUAL),)), 'authority'),
    'strategy entry, operator authority': (lambda: replace(DEC, authority=Authority.OPERATOR), 'authority'),
    'resume by strategy': (lambda: replace(DEC, action=Action.RESUME, reason=ReasonCode.OPERATOR_RESUME, intents=()),
                           'authority'),
    'detail too long': (lambda: replace(DEC, detail='x' * 161), 'detail'),
    'detail with newline': (lambda: replace(DEC, detail='a\nb'), 'detail'),
    'evidence not an id': (lambda: replace(DEC, evidence=('see log',)), 'evidence'),
    'duplicate evidence': (lambda: replace(DEC, evidence=(LOT.lot_id, LOT.lot_id)), 'evidence'),
    # ---- events / snapshot
    'recorded intent already sent': (lambda: F.event(IntentRecorded, ids, ACCT, 1, at=T0, reason=ENTRY.reason, intent=ENTRY), 'state'),
    'recorded before created': (lambda: F.event(IntentRecorded, ids, ACCT, 1, at=T0 - 1, reason=ENTRY.reason,
                                                       intent=replace(ENTRY, state=IntentState.DURABLE)), 'at_ms'),
    'terminal returns to working': (lambda: F.event(IntentStateChanged, ids, ACCT, 1, intent_id=ENTRY.intent_id,
                                                               from_state=IntentState.FILLED,
                                                               to_state=IntentState.WORKING), 'to_state'),
    'leave HOLD without reconciliation': (lambda: F.event(ModeChanged, ids, ACCT, 1, from_mode=EntriesMode.HOLD,
                                                              to_mode=EntriesMode.PAUSED, from_hold=HoldKind.NORMAL,
                                                              reasons=(ReasonCode.OPERATOR_PAUSE,), reason=ReasonCode.OPERATOR_PAUSE), 'reconciliation_id'),
    'resume without decision': (lambda: F.event(ModeChanged, ids, ACCT, 1, from_mode=EntriesMode.PAUSED,
                                                    to_mode=EntriesMode.ACTIVE, reasons=(), reason=ReasonCode.OPERATOR_RESUME), 'decision_id'),
    'hard HOLD event without cause': (lambda: F.event(ModeChanged, ids, ACCT, 1, from_mode=EntriesMode.ACTIVE,
                                                          to_mode=EntriesMode.HOLD, to_hold=HoldKind.DURABILITY_UNAVAILABLE,
                                                          reasons=(ReasonCode.OPERATOR_PAUSE,), reason=ReasonCode.OPERATOR_PAUSE), 'reasons'),
    'binding skips confirmation': (lambda: F.event(BindingChanged, ids, ACCT, 1, from_state=BindingState.CONFIRMED,
                                                          to_state=BindingState.RECONCILING, binding=B), 'to_state'),
    'rotation without typed confirmation': (lambda: F.event(BindingChanged, ids, ACCT, 1,
                                                                   from_state=BindingState.ROTATION_PENDING,
                                                                   to_state=BindingState.RECONCILING, binding=B),
                                            'confirmation'),
    'rotation confirmed without reconciliation': (lambda: F.event(BindingChanged, ids, ACCT, 1,
                                                                         from_state=BindingState.RECONCILING,
                                                                         to_state=BindingState.CONFIRMED, binding=B),
                                                  'reconciliation_id'),
    'snapshot of another account': (lambda: Snapshot(account_id=OTHER, generation=PF.generation, last_sequence=5, written_at_ms=T0,
                                                     writer_build='b', portfolio=PF), 'portfolio'),
    'snapshot generation differs': (lambda: Snapshot(account_id=PF.account_id, generation=PF.generation + 1, last_sequence=5,
                                                     written_at_ms=T0, writer_build='b', portfolio=PF), 'generation'),
    'journal proof beyond snapshot': (lambda: Snapshot(account_id=PF.account_id, generation=PF.generation, last_sequence=0,
                                                       written_at_ms=T0, writer_build='b', portfolio=PF), 'proof'),
}


def _oversized():
    """A placed stop bigger than the lot it protects, no resize in flight."""
    st = F.intent(ids, PF.account_id, Purpose.PROTECT, LOT.symbol, LOT.side, D('9'), state=IntentState.WORKING,
                  owner_id=LOT.lot_id, stop_price=PROT.price)
    lt = replace(LOT, stop=replace(PROT, qty=D('9'), order=st.intent_id))
    rest = tuple(i for i in PF.intents if i.intent_id != PROT.order)
    return replace(PF, positions=(replace(POS, lots=(lt,)),), intents=rest + (st,))


def _stop_state(state):
    its = tuple(replace(i, state=state) if i.intent_id == PROT.order else i for i in PF.intents)
    return replace(PF, intents=its)


def _replacement(old_state=IntentState.WORKING, qty=None, side=None):
    new = F.intent(ids, PF.account_id, Purpose.PROTECT, LOT.symbol, side or LOT.side, qty or LOT.qty, owner_id=LOT.lot_id,
                   stop_price=PROT.price + 1, reason=ReasonCode.PROTECT_REPLACE)
    its = tuple(replace(i, state=old_state) if i.intent_id == PROT.order else i for i in PF.intents) + (new,)
    conf = PROT.confirmed_at_ms if old_state is IntentState.WORKING else None
    lt = replace(LOT, stop=replace(PROT, replacement=new.intent_id, confirmed_at_ms=conf))
    return replace(PF, positions=(replace(POS, lots=(lt,)),), intents=its)


def _entry_stop(qty, owner=None):
    me = owner or F.market_entry(ids, PF.account_id)
    me = replace(me, account_id=PF.account_id)
    s = F.build(Protection, owner_id=me.intent_id, price=D('0.1'), qty=qty)
    return replace(PF, intents=PF.intents + (me,), entry_stops=(s,))


def _hard_hold_reduce():
    """A REDUCE created after the portfolio entered hard HOLD (only the emergency set may be placed there)."""
    red = F.intent(ids, PF.account_id, Purpose.REDUCE, LOT.symbol, LOT.side, D('0.5'), owner_id=LOT.lot_id,
                   created=T0 + 20_000)
    its = tuple(i for i in PF.intents if i.intent_id != LOT.in_flight) + (red,)
    lt = replace(LOT, in_flight=red.intent_id)
    return replace(PF, positions=(replace(POS, lots=(lt,)),), intents=its, entries_mode=EntriesMode.HOLD,
                   hold_kind=HoldKind.DURABILITY_UNAVAILABLE, pause_reasons=(ReasonCode.RECOVERY_DURABILITY_UNAVAILABLE,))


@pytest.mark.parametrize('name', sorted(CASES))
def test_invalid_state_is_rejected(name):
    build, path_part = CASES[name]
    with pytest.raises(InvalidRecord) as e:
        build()
    assert path_part in e.value.path, f'{name}: rejected at {e.value.path!r} ({e.value.msg}), expected {path_part!r}'


def test_duplicate_position_id():
    lt, _ = F.lot(ids, PF.account_id, 'ETHUSDT', stop_state='none')
    other = Position(position_id=POS.position_id, symbol='ETHUSDT', side=lt.side, lots=(lt,))
    with pytest.raises(InvalidRecord, match='duplicate position id'):
        replace(PF, positions=(POS, other))


def test_one_stop_order_cannot_carry_two_protections():
    lt2, _ = F.lot(ids, PF.account_id, LOT.symbol, LOT.side, LOT.qty, stop_state='none')
    lt2 = replace(lt2, stop=replace(lt2.stop, order=PROT.order, price=PROT.price))
    with pytest.raises(InvalidRecord):
        replace(PF, positions=(replace(POS, lots=(LOT, lt2)),))


def test_every_base_record_is_valid():
    """The table's starting points are valid, so each row breaks exactly what it names."""
    for rec in (ENTRY, ADD, STOP, MAKER, PF, LOT, POS, ACC, B, DEC, PROT, FILL, *RES.values()):
        assert replace(rec) == rec
    assert B.venue is Venue.BINANCE_USDM
    assert confirmation_phrase(ACCT, F.DIGEST) == ACC.confirmation.typed_phrase
    assert isinstance(ACC, Account) and isinstance(B, AccountBinding) and isinstance(ACC.confirmation, BindingConfirmation)


def test_valid_alternatives_are_accepted():
    """Positive controls for the drain / one-shot / replacement rules."""
    acct = PF.account_id
    # a drained maker while paused, a market entry created BEFORE the pause, a one-shot market entry after it
    shot = F.market_entry(ids, acct, created=T0 + 20_000, reason=ReasonCode.ENTRY_ONE_SHOT, authorized_by=ids.id('dec'))
    with_intents(replace(MAKER, account_id=acct, state=IntentState.CANCELLING), F.market_entry(ids, acct), shot, **paused())
    # hard HOLD may place protection after it started
    _replacement()
    hard = dict(entries_mode=EntriesMode.HOLD, hold_kind=HoldKind.DURABILITY_UNAVAILABLE,
                pause_reasons=(ReasonCode.RECOVERY_DURABILITY_UNAVAILABLE,))
    new = F.intent(ids, acct, Purpose.PROTECT, LOT.symbol, LOT.side, LOT.qty, owner_id=LOT.lot_id,
                   stop_price=PROT.price + 1, reason=ReasonCode.PROTECT_REPLACE, created=T0 + 20_000)
    lt = replace(LOT, stop=replace(PROT, replacement=new.intent_id))
    replace(PF, positions=(replace(POS, lots=(lt,)),), intents=PF.intents + (new,), **hard)


# ----------------------------------------------------------------------------------------------------------- r2 additions
def test_a_known_portfolio_has_no_dangling_owner_reference():
    """Contract invariant 11: an owner id must resolve inside this portfolio; orphan work is portfolio-owned."""
    acct = PF.account_id
    ghost = F.intent(ids, acct, Purpose.PROTECT, LOT.symbol, LOT.side, D('1'), state=IntentState.CANCELLING,
                     owner_id=ids.id('lot'), stop_price=D('9'))
    with pytest.raises(InvalidRecord, match='no orphan reference'):
        with_intents(ghost)
    with_intents(replace(ghost, owner_id=F.pf_id(acct), owner_kind=F.OwnerKind.PORTFOLIO))                       # re-owned by the aggregate: valid
    with pytest.raises(InvalidRecord, match='owned by this portfolio'):
        with_intents(replace(ghost, owner_id=F.pf_id(OTHER), owner_kind=F.OwnerKind.PORTFOLIO))


def test_owner_symbol_and_side_agree():
    acct = PF.account_id
    bad = F.intent(ids, acct, Purpose.REDUCE, 'ETHUSDT', LOT.side, D('0.5'), owner_id=LOT.lot_id)
    lt = replace(LOT, in_flight=bad.intent_id)
    its = tuple(i for i in PF.intents if i.intent_id != LOT.in_flight) + (bad,)
    with pytest.raises(InvalidRecord, match='owner of another symbol'):
        replace(PF, positions=(replace(POS, lots=(lt,)),), intents=its)


def test_aggregate_protective_coverage_is_bounded_by_exposure():
    """Two lots of one position: each stop within its own lot, and the side total within the position."""
    acct = PF.account_id
    lt2, extra = F.lot(ids, acct, LOT.symbol, LOT.side, D('2'), stop_state='confirmed')
    pos2 = replace(POS, lots=(LOT, lt2))
    ok = replace(PF, positions=(pos2,), intents=PF.intents + tuple(extra))
    assert ok.positions[0].qty == D('3.5')
    from newcore.domain import confirmed_coverage, target_coverage
    intents = ok.intents_by_id()
    assert sum(target_coverage(x.stop, intents) for x in ok.lots) == D('3.5')
    assert sum(confirmed_coverage(x.stop, intents) for x in ok.lots) == D('3.5')
    # an entry stop larger than the seen size breaks the bound for its symbol / side as well
    with pytest.raises(InvalidRecord):
        _entry_stop(D('400.01'))


def test_event_reason_matches_its_payload():
    dur = replace(ENTRY, state=IntentState.DURABLE)
    with pytest.raises(InvalidRecord, match="intent's reason"):
        F.event(IntentRecorded, ids, ACCT, 1, intent=dur, reason=ReasonCode.EXIT_STOP)
    with pytest.raises(InvalidRecord, match='names one of its reasons'):
        F.event(ModeChanged, ids, ACCT, 1, from_mode=EntriesMode.ACTIVE, to_mode=EntriesMode.PAUSED,
                reasons=(ReasonCode.OPERATOR_PAUSE,), reason=ReasonCode.FILTER_HALT)


@pytest.mark.parametrize('spelling', ['1', '1.0', '1.000', '10E-1', '0.1E+1', '1E0'])
def test_constructors_normalize_equal_decimals_to_one_value(spelling):
    it = replace(ADD, qty=D(spelling))
    assert it.qty.as_tuple() == D('1').as_tuple()
    from newcore.domain import canonical_bytes
    assert canonical_bytes(it) == canonical_bytes(replace(ADD, qty=D('1')))


def test_environment_boundary_accepts_its_own_environment():
    require_environment(B, Environment.TESTNET)
    for env in (Environment.MAINNET, Environment.SIM, Environment.BACKTEST):
        with pytest.raises(InvalidRecord):
            require_environment(B, env)
