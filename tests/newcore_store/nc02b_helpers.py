"""Builders for the NC-02b store tests: a configured account, a fake exchange view, owned portfolios, a MemFs base."""
import dataclasses
import os
import sys
from decimal import Decimal as D

from newcore.domain import (Account, AccountBinding, BindingConfirmation, BindingState, Environment, Venue,
                            confirmation_phrase)
from newcore.store.cipher import InsecureTestCipher
from newcore.store.reconcile import ExchangeDown, ExchangeSnapshot, ExOrder, ExPosition
from newcore.store.store import aggregate_for

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), 'newcore'))
import nc01_factories as F  # noqa: E402

ACCT = 'acct_' + 'e5' * 16
DIGEST = '0123456789abcdef'
OTHER_DIGEST = 'fedcba9876543210'
T = 1_791_400_000_000
MEM_BASE = os.path.join(os.path.abspath(os.sep), 'nc02b-mem')
CIPHER = InsecureTestCipher(test_only=True)
STEPS = {'BTCUSDT': D('0.001'), 'ETHUSDT': D('0.001'), 'SOLUSDT': D('0.01')}


def account(digest=DIGEST, state=BindingState.CONFIRMED, acct=ACCT):
    binding = AccountBinding(venue=Venue.BINANCE_USDM, environment=Environment.TESTNET, settlement_asset='USDT',
                             key_digest=digest, exchange_uid=None)
    conf = None if state is BindingState.UNCONFIRMED else BindingConfirmation(
        account_id=acct, old_key_digest=None, new_key_digest=digest, typed_phrase=confirmation_phrase(acct, digest),
        confirmed_at_ms=T - 1000)
    return Account(account_id=acct, label='testnet', hedge_mode=True, binding=binding, binding_state=state,
                   proposed_binding=None, confirmation=conf)


class FakeExchange:
    """ExchangeView: snapshot() of positions / orders taken at `now`; `down` raises; `between` mutates the exchange
    after the n-th snapshot (to attack the promotion re-check)."""

    def __init__(self, positions=(), orders=(), digest=DIGEST, now=T):
        self.positions, self.orders, self.digest, self.now = list(positions), list(orders), digest, now
        self.down, self.calls, self.between = False, 0, {}

    def snapshot(self):
        if self.down:
            raise ExchangeDown('down')
        self.calls += 1
        snap = ExchangeSnapshot(key_digest=self.digest, taken_ms=self.now, positions=tuple(self.positions),
                                orders=tuple(self.orders), steps=dict(STEPS))
        if self.calls in self.between:
            self.between.pop(self.calls)(self)
        return snap


def owned(seed=7, symbol='BTCUSDT', qty=D('1'), acct=ACCT):
    """(KNOWN portfolio with one lot + its confirmed stop, matching exchange positions, matching exchange orders)."""
    ids = F.Ids(seed)
    lt, extra = F.lot(ids, acct, symbol, F.Side.LONG, qty, D('100'))
    pf = F.portfolio(acct, (F.position(ids, [lt]),), extra)
    pf = dataclasses.replace(pf, portfolio_id=aggregate_for(acct))
    stop = next(i for i in extra)
    pos = [ExPosition(symbol, 'LONG', qty)]
    orders = [ExOrder(stop.client_order_id, symbol, 'LONG', qty, True, 'stop', 'working')]
    return pf, pos, orders


def mem():
    from nc02a_memfs import MemFs
    return MemFs(MEM_BASE)


def data_files(fs, base=MEM_BASE):
    """Snapshot of everything under data\\ (bytes + mtimes + listing)."""
    root = os.path.normpath(os.path.join(base, 'data'))
    return {k: v for k, v in fs.snapshot().items() if k == root or k.startswith(root + os.sep)}


def outside_files(fs, base=MEM_BASE):
    root = os.path.normpath(os.path.join(base, 'data'))
    return {k: v for k, v in fs.snapshot().items() if not (k == root or k.startswith(root + os.sep))}
