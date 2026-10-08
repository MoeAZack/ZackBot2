"""Smoke test of nc01_domain_sketch.py: round trip + the Audit2 negative fixtures are rejected. Run with -I from nc01prep."""
import copy
import dataclasses
import json
import os
import random
import sys
import time
from decimal import Decimal as D

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import nc01_domain_sketch as M  # noqa: E402

A = M.new_id('acct')


def cid(p='zb'): return f'{p}{random.getrandbits(88):022x}'


def lot(qty=D('1.5'), in_flight=None):
    lid = M.new_id('lot')
    st = M.Protection(owner_id=lid, kind=M.ProtectionKind.STOP, status=M.ProtectionStatus.OWNED_CONFIRMED, price=D('95.1'),
                      qty=qty, tag='c:' + cid(), confirmed_at=time.time())
    return M.Lot(lot_id=lid, account_id=A, symbol='SOLUSDT', side=M.Side.LONG, source=M.LotSource.STRATEGY, slot_id='S1',
                 strategy_key='ema_st', tf='4h', opened_at='2026-10-08T10:00:00+00:00', qty=qty, avg_price=D('100'),
                 entry_price=D('100'), anchor_price=D('100'), initial_qty=qty, max_qty=qty, risk_distance=D('4.9'),
                 risk_usd=D('10'), stop=st, in_flight=in_flight,
                 fills=(M.Fill(at='2026-10-08T10:00:00+00:00', reason=M.ReasonCode.EXIT_STOP, qty=qty, price=D('100'), fee=D('0.075')),))


def portfolio(**kw):
    base = dict(account_id=A, generation=7, ownership=M.Ownership.KNOWN, entries_mode=M.EntriesMode.ACTIVE, pause_reasons=(),
                positions=(M.Position(symbol='SOLUSDT', side=M.Side.LONG, lots=(lot(),)),), entries=(), intents=())
    base.update(kw)
    return M.Portfolio(**base)


def expect_reject(name, fn, exc=M.InvalidRecord):
    try:
        fn()
    except exc as ex:
        print(f'  REJECTED {name}: {ex}'); return
    raise SystemExit(f'FAIL: {name} was accepted')


p = portfolio()
doc = M.encode_document(p)
txt = json.dumps(doc, sort_keys=True)
back = M.decode_document(json.loads(txt))
assert back == p, 'round trip changed the record'
assert json.dumps(M.encode_document(back), sort_keys=True) == txt, 'canonical re-encode differs'
print('round trip ok,', len(txt), 'bytes')

t0 = time.perf_counter()
for _ in range(2000): M.decode_document(json.loads(txt))
print(f'decode+validate: {(time.perf_counter() - t0) / 2000 * 1e6:.0f} us per 1-lot portfolio')

print('Audit2 negative fixtures:')
# 1 future schema (both copies): FutureSchema raised before anything else is read
fut = dict(doc, schema_version=2, body={'garbage': True})
expect_reject('future schema', lambda: M.decode_document(fut), M.FutureSchema)
# 2 current-schema damage
bad = copy.deepcopy(doc); bad['body']['positions'][0]['lots'][0]['qty'] = 'NaN'
expect_reject('current-schema damage (NaN qty)', lambda: M.decode_document(bad))
bad2 = copy.deepcopy(doc); bad2['body']['positions'][0]['lots'][0]['surprise'] = 1
expect_reject('current-schema damage (unknown key)', lambda: M.decode_document(bad2))
# 3 qty-only pending: an in-flight marker with no durable intent / an intent without a client id
expect_reject('qty-only pending (in_flight without intent)',
              lambda: portfolio(positions=(M.Position(symbol='SOLUSDT', side=M.Side.LONG, lots=(lot(in_flight=M.new_id('int')),)),)))
expect_reject('qty-only pending (intent without client id)',
              lambda: M.OrderIntent(intent_id=M.new_id('int'), account_id=A, client_order_id='', purpose=M.Purpose.ADD,
                                    symbol='SOLUSDT', side=M.Side.LONG, order_type=M.OrderType.MARKET, qty=D('0.5'), reduce_only=False,
                                    reason=M.ReasonCode.EXIT_STOP, owner_id=M.new_id('lot'), created_at=0.0))
# 4 false not-found: 'nothing executed' needs a final record, a refusal or a CORROBORATED not-found
expect_reject('false not-found (final, no evidence)',
              lambda: M.OrderResult(intent_id=M.new_id('int'), client_order_id=cid(), phase=M.ResultPhase.FINAL, requested_qty=D('0.5'),
                                    executed_qty=D('0'), evidence=None))
expect_reject('false not-found (corroborated not-found books an execution)',
              lambda: M.OrderResult(intent_id=M.new_id('int'), client_order_id=cid(), phase=M.ResultPhase.FINAL, requested_qty=D('0.5'),
                                    executed_qty=D('0.5'), avg_price=D('100'), evidence=M.Evidence.NOT_FOUND_CORROBORATED))
expect_reject('non-final result with an executed qty',
              lambda: M.OrderResult(intent_id=M.new_id('int'), client_order_id=cid(), phase=M.ResultPhase.KNOWN, requested_qty=D('0.5'),
                                    executed_qty=D('0.2')))
# 5 resting maker survives pause / flatten
iid = M.new_id('int'); eid = M.new_id('ent')
oi = M.OrderIntent(intent_id=iid, account_id=A, client_order_id=cid('zm'), purpose=M.Purpose.ENTRY, symbol='XRPUSDT', side=M.Side.LONG,
                   order_type=M.OrderType.LIMIT_POST_ONLY, price=D('2.01'), qty=D('10'), reduce_only=False,
                   reason=M.ReasonCode.CAPACITY_ENTRY_WORKING, owner_id=eid, created_at=0.0)
maker = M.EntryIntent(entry_id=eid, account_id=A, kind=M.EntryKind.MAKER, state=M.EntryState.WORKING, symbol='XRPUSDT',
                      side=M.Side.LONG, slot_id='S1', planned_qty=D('10'), planned_price=D('2.01'), stop_distance=D('0.1'),
                      created_at=0.0, order=iid)
portfolio(entries=(maker,), intents=(oi,))              # fine while active
expect_reject('resting maker WORKING while flattening',
              lambda: portfolio(entries_mode=M.EntriesMode.FLATTENING, pause_reasons=(M.ReasonCode.OPERATOR_FLATTEN,),
                                entries=(maker,), intents=(oi,)))
portfolio(entries_mode=M.EntriesMode.FLATTENING, pause_reasons=(M.ReasonCode.OPERATOR_FLATTEN,),
          entries=(dataclasses.replace(maker, state=M.EntryState.CANCELLING),), intents=(oi,))   # drained: fine
# 6 empty managed account
expect_reject('unknown ownership stored as empty', lambda: portfolio(ownership=M.Ownership.UNKNOWN, entries_mode=M.EntriesMode.PAUSED,
                                                                     pause_reasons=(M.ReasonCode.RECOVERY_SCHEMA_INVALID,)))
expect_reject('unknown ownership but active', lambda: portfolio(ownership=M.Ownership.UNKNOWN, positions=None, entries=None, intents=None))
u = portfolio(ownership=M.Ownership.UNKNOWN, entries_mode=M.EntriesMode.PAUSED, pause_reasons=(M.ReasonCode.RECOVERY_SCHEMA_INVALID,),
              positions=None, entries=None, intents=None)
assert M.decode_document(M.encode_document(u)) == u
print('  unknown-ownership portfolio round-trips with lots=None (never iterable as empty)')
# extra: duplicate lot ids / foreign bot cid / provisional stop larger than seen
l1 = lot()
expect_reject('duplicate lot id', lambda: M.Position(symbol='SOLUSDT', side=M.Side.LONG, lots=(l1, l1)))
expect_reject('bot client id recorded as foreign',
              lambda: M.Protection(owner_id=l1.lot_id, kind=M.ProtectionKind.STOP, status=M.ProtectionStatus.OWNER_CHECK,
                                   price=D('1'), qty=D('1'), foreign_tag='c:' + cid(), miss_count=1))
print('ALL OK')
