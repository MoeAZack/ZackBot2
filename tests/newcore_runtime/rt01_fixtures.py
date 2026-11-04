"""Shared RT-01 test fixtures: a real NC-02 journal on disk, a real NC-04 AccountContext with a scripted venue, and
REC-02 truth built from typed records.  All text I/O is utf-8."""
from __future__ import annotations

import dataclasses
import threading
import time
from decimal import Decimal

from newcore.account_context import (ACCOUNT_ORDER_LIMIT_PATH, ACCOUNT_SOURCE, EXCHANGE_INFO_PATH,
                                     MARKET_DATA_SOURCE, SOURCE_RETRIEVED_ON, AccountBudget, AccountContext,
                                     LimitAuthority, OpenOrdersRequest, Operation, PositionsRequest, RawLimitPayload,
                                     VenueWork)
from newcore.domain import Action
from newcore.domain.account import (Account, AccountBinding, BindingConfirmation, BindingState, Environment, Venue,
                                    confirmation_phrase)
from newcore.domain.orders import IntentState, Purpose, Side
from newcore.reconciliation import (DurableState, EvidencePage, LocalOrder, LocalPosition, SnapshotComponent,
                                    VenueOrder, VenueOrderKind, VenueOrderState, VenuePosition, VenueSnapshot)
from newcore.runtime import AccountPorts, AccountSpec, RuntimeConfig, RuntimeEnvironment, boot
from newcore.store import create_journal

D = Decimal
WALL0 = 2_000_000_000_000
DIGEST = '0123456789abcdef'


def acct(n: int) -> str:
    return f'acct_{n:032x}'


def pf(n: int) -> str:
    return f'pf_{n:032x}'


class Clock:
    def __init__(self, value: int):
        self.value = value
        self._lock = threading.Lock()

    def __call__(self) -> int:
        with self._lock:
            return self.value

    def advance(self, ms: int) -> None:
        with self._lock:
            self.value += ms


class MemoryRateJournal:
    def __init__(self):
        self.records = []

    def append_rate_block(self, record):
        self.records.append(record)

    def load_rate_blocks(self, account_id, egress_id):
        return tuple(x for x in self.records if (x.account_id, x.egress_id) == (account_id, egress_id))


def limits():
    exchange = RawLimitPayload(
        authority=LimitAuthority.EXCHANGE_INFO, method='GET', path=EXCHANGE_INFO_PATH,
        source_url=MARKET_DATA_SOURCE, retrieved_on=SOURCE_RETRIEVED_ON,
        payload={'rateLimits': [
            {'rateLimitType': 'REQUEST_WEIGHT', 'interval': 'MINUTE', 'intervalNum': 1, 'limit': 2400}]})
    account = RawLimitPayload(
        authority=LimitAuthority.USER_RATE_LIMIT, method='GET', path=ACCOUNT_ORDER_LIMIT_PATH,
        source_url=ACCOUNT_SOURCE, retrieved_on=SOURCE_RETRIEVED_ON,
        payload=[{'rateLimitType': 'ORDERS', 'interval': 'SECOND', 'intervalNum': 10, 'limit': 300},
                 {'rateLimitType': 'ORDERS', 'interval': 'MINUTE', 'intervalNum': 1, 'limit': 1200}])
    return exchange, account


class ScriptedVenue:
    """A VenuePort whose reads return a sentinel; any order-placing call is recorded and fails the test later."""

    def __init__(self):
        self.calls = []
        self.raise_reads = False
        self.block: threading.Event | None = None
        self.entered = threading.Event()

    def _read(self, name, *args):
        self.calls.append((name, args))
        self.entered.set()
        if self.block is not None:
            self.block.wait(5)
        if self.raise_reads:
            raise RuntimeError('venue down')
        return ('read', name)

    def _write(self, name, *args):
        self.calls.append((name, args))
        return ('write', name)

    def submit_market(self, order): return self._write('submit_market', order)
    def submit_stop(self, order): return self._write('submit_stop', order)
    def cancel(self, ref): return self._write('cancel', ref)
    def query(self, ref): return self._read('query', ref)
    def positions(self, symbol=None): return self._read('positions', symbol)
    def open_orders(self, symbol=None): return self._read('open_orders', symbol)
    def fills(self, symbol, exchange_order_id): return self._read('fills', symbol, exchange_order_id)

    def writes(self):
        return [c for c in self.calls if c[0] in ('submit_market', 'submit_stop', 'cancel')]


def account_record(account_id: str, *, environment=Environment.TESTNET, state=BindingState.CONFIRMED) -> Account:
    binding = AccountBinding(venue=Venue.BINANCE_USDM, environment=environment, settlement_asset='USDT',
                             key_digest=DIGEST, exchange_uid='testnet-user')
    confirmation = None
    if state is not BindingState.UNCONFIRMED:
        confirmation = BindingConfirmation(account_id=account_id, old_key_digest=None, new_key_digest=DIGEST,
                                           typed_phrase=confirmation_phrase(account_id, DIGEST),
                                           confirmed_at_ms=WALL0 - 60_000)
    return Account(account_id=account_id, label='rt01', hedge_mode=True, binding=binding, binding_state=state,
                   proposed_binding=None, confirmation=confirmation)


class Truth:
    """Mutable world: what the durable journal says and what the venue shows."""

    def __init__(self, account_id: str, wall: Clock):
        self.account_id, self.wall = account_id, wall
        self.account = account_record(account_id)
        self.local_positions: tuple = ()
        self.local_orders: tuple = ()
        self.venue_positions: tuple = ()
        self.venue_orders: tuple = ()
        self.load_error: Exception | None = None
        self.loads = 0

    def protected_long(self, qty='5'):
        self.local_positions = (LocalPosition('BTCUSDT', Side.LONG, D(qty)),)
        self.local_orders = (LocalOrder('int_' + '1' * 32, 'cid-protect-1', '100', 'BTCUSDT', Side.LONG,
                                        Purpose.PROTECT, IntentState.WORKING, D(qty), D('0')),)
        self.venue_positions = (VenuePosition('BTCUSDT', Side.LONG, D(qty)),)
        self.venue_orders = (VenueOrder('100', 'cid-protect-1', 'BTCUSDT', Side.LONG, VenueOrderKind.STOP_MARKET,
                                        VenueOrderState.OPEN, True, D(qty), D('0')),)


class StateSource:
    def __init__(self, truth: Truth):
        self.truth = truth
        self.journal_states = []

    def load(self, account_id, journal_state):
        self.truth.loads += 1
        self.journal_states.append(journal_state)
        if self.truth.load_error is not None:
            raise self.truth.load_error
        return DurableState(self.truth.account, 1, WALL0 - 10_000, self.truth.local_positions,
                            self.truth.local_orders)


class Evidence:
    def __init__(self, truth: Truth):
        self.truth = truth
        self.forbidden_work = False
        self.extra_work = None
        self.assembled = 0
        self.assemble_result = None

    def read_work(self, account_id, generation, now_mono, deadline_mono):
        prefix = f'rt-{account_id[-6:]}-{generation}'
        rows = [VenueWork(work_id=f'{prefix}-p', action=Action.RECONCILE, operation=Operation.POSITIONS,
                          payload=PositionsRequest(), enqueued_at_ms=now_mono, deadline_ms=deadline_mono),
                VenueWork(work_id=f'{prefix}-o', action=Action.RECONCILE, operation=Operation.OPEN_ORDERS,
                          payload=OpenOrdersRequest(symbol='BTCUSDT'), enqueued_at_ms=now_mono,
                          deadline_ms=deadline_mono)]
        if self.forbidden_work:
            from newcore.ports.venue import MarketOrder, OrderRef
            rows.append(VenueWork(
                work_id=f'{prefix}-x', action=Action.ENTER, operation=Operation.SUBMIT_MARKET,
                payload=MarketOrder(ref=OrderRef(symbol='BTCUSDT', client_id='zbn1o-1', route='classic'),
                                    position_side='LONG', qty=D('1'), reduce=False),
                enqueued_at_ms=now_mono, deadline_ms=deadline_mono))
        if self.extra_work is not None:
            rows.append(self.extra_work(prefix, now_mono, deadline_mono))
        return tuple(rows)

    def assemble(self, account_id, generation, results):
        self.assembled += 1
        if self.assemble_result is not None:
            return self.assemble_result
        t = self.truth
        observed = t.wall()
        binding = t.account.binding

        def page(component, records):
            return EvidencePage(component, account_id, binding, generation, 1, observed, True, tuple(records))

        return VenueSnapshot((page(SnapshotComponent.POSITIONS, t.venue_positions),
                              page(SnapshotComponent.OPEN_ORDERS, t.venue_orders),
                              page(SnapshotComponent.USER_TRADES, ())))


class World:
    """One to four accounts with real journals in tmp_path, booted through newcore.runtime.boot."""

    def __init__(self, tmp_path, n=1, *, create=True, environment=RuntimeEnvironment.TESTNET, **config):
        self.mono, self.wall = Clock(1_000_000), Clock(WALL0)
        self.specs, self.truths, self.venues, self.evidence, self.sources = [], {}, {}, {}, {}
        self.extra_ports = {}
        for i in range(1, n + 1):
            account_id = acct(i)
            spec = AccountSpec(account_id=account_id, aggregate_id=pf(i), account_dir=str(tmp_path / f'a{i}'),
                               egress_id=f'egress-{i}')
            if create:
                create_journal(spec.account_dir, account_id, spec.aggregate_id).close()
            self.specs.append(spec)
            truth = Truth(account_id, self.wall)
            self.truths[account_id] = truth
            self.venues[account_id] = ScriptedVenue()
            self.evidence[account_id] = Evidence(truth)
            self.sources[account_id] = StateSource(truth)
        self.config = RuntimeConfig(environment=environment, accounts=tuple(self.specs), **config)
        self.runtime = None

    def ports(self):
        out = {}
        for spec in self.specs:
            exchange, account = limits()
            budget = AccountBudget(account_id=spec.account_id, egress_id=spec.egress_id, exchange_info=exchange,
                                   account_order_limits=account, monotonic_clock=self.mono, wall_clock=self.wall,
                                   journal=MemoryRateJournal())
            context = AccountContext(account_id=spec.account_id, egress_id=spec.egress_id,
                                     venue=self.venues[spec.account_id], budget=budget, monotonic_clock=self.mono)
            values = dict(context=context, state_source=self.sources[spec.account_id],
                          evidence=self.evidence[spec.account_id])
            values.update(self.extra_ports.get(spec.account_id, {}))
            out[spec.account_id] = AccountPorts(**values)
        return out

    def boot(self, **kwargs):
        self.runtime = boot(self.config, self.ports(), boot_id='boot-rt01', monotonic_clock=self.mono,
                            wall_clock=self.wall, **kwargs)
        return self.runtime

    def settle(self, predicate, *, advance_ms=0, tries=400):
        """Step the owner loop until predicate(snapshot) holds; runner threads need a little real time."""
        snap = None
        for _ in range(tries):
            if advance_ms:
                self.mono.advance(advance_ms)
                self.wall.advance(advance_ms)
            snap = self.runtime.step()
            if predicate(snap):
                return snap
            time.sleep(0.003)
        raise AssertionError(f'never settled: {snap}')

    def shutdown(self):
        if self.runtime is None:
            return
        self.runtime.stop(self.mono() + 1_000)
        self.settle(lambda s: s.scheduler.state.value == 'stopped')
        self.runtime.close()


def account_view(snapshot, account_id):
    return next(row for row in snapshot.accounts if row.account_id == account_id)


def replace(row, **changes):
    return dataclasses.replace(row, **changes)
