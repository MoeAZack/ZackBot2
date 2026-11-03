"""NC-04 account-local worker, endpoint admission table and conservative budget.

The module owns no thread, sleep, retry, credential, runtime wiring or portfolio
mutation.  ``run_next`` is the explicit worker step, making queue races and calls
fully deterministic for the later NC-05 scheduler.
"""
from __future__ import annotations

import enum
import heapq
import hmac
import math
import threading
from collections import deque
from dataclasses import dataclass, field
from typing import Callable, Iterable

from newcore.domain import Action
from newcore.domain.decision import PRIORITY
from newcore.ports.rate_blocks import RateBlockJournalPort, RateBlockRecord, RateBlockState
from newcore.ports.values import check_id, check_int, check_ms, check_symbol, check_text, plain, req
from newcore.ports.venue import (MarketOrder, OrderRef, ReadOutcome, StopOrder, VenuePort)
from newcore.venue.binance_usdm_testnet import RateEvidence


TRADE_SOURCE = ('https://developers.binance.com/en/docs/catalog/core-trading-derivatives-trading-'
                'usd-s-m-futures/api/rest-api/trade')
ACCOUNT_SOURCE = ('https://developers.binance.com/en/docs/catalog/core-trading-derivatives-trading-'
                  'usd-s-m-futures/api/rest-api/account')
GENERAL_SOURCE = 'https://developers.binance.com/en/docs/products/derivatives-trading-usds-futures/general-info'
MARKET_DATA_SOURCE = ('https://developers.binance.com/en/docs/catalog/core-trading-derivatives-trading-'
                      'usd-s-m-futures/api/rest-api/market-data')
SOURCE_RETRIEVED_ON = '2026-11-03'
ENDPOINT_TABLE_VERSION = 'binance-usdm-v1-2026-11-03'
EXCHANGE_INFO_PATH = '/fapi/v1/exchangeInfo'
ACCOUNT_ORDER_LIMIT_PATH = '/fapi/v1/rateLimit/order'


class Operation(enum.StrEnum):
    SUBMIT_MARKET = 'submit_market'
    SUBMIT_STOP = 'submit_stop'
    CANCEL = 'cancel'
    QUERY = 'query'
    POSITIONS = 'positions'
    OPEN_ORDERS = 'open_orders'
    FILLS = 'fills'


class Pool(enum.StrEnum):
    GENERAL = 'general'
    PROTECTION = 'protection'


class Limiter(enum.StrEnum):
    REQUEST_WEIGHT_1M = 'request_weight_1m'
    ORDERS_10S = 'orders_10s'
    ORDERS_1M = 'orders_1m'


class AdmissionRefused(Exception):
    def __init__(self, code: str):
        self.code = code
        super().__init__(code)


class BudgetConfigurationError(AdmissionRefused):
    pass


class BudgetRefused(AdmissionRefused):
    pass


WINDOW_MS = {
    Limiter.REQUEST_WEIGHT_1M: 60_000,
    Limiter.ORDERS_10S: 10_000,
    Limiter.ORDERS_1M: 60_000,
}


class LimitAuthority(enum.StrEnum):
    EXCHANGE_INFO = 'exchange_info'
    USER_RATE_LIMIT = 'user_rate_limit'


_LIMIT_PROOF_KEY = b'zackbot.nc04.binance-limit-proof.v1'


def _limit_proof(*, limiter: Limiter, limit: int, authority: LimitAuthority,
                 source_path: str, source_url: str, retrieved_on: str) -> bytes:
    material = '\x1f'.join((plain(limiter), str(limit), plain(authority), source_path,
                            source_url, retrieved_on)).encode('utf-8')
    return hmac.digest(_LIMIT_PROOF_KEY, material, 'sha256')


@dataclass(frozen=True, slots=True, kw_only=True)
class RawLimitPayload:
    """Raw response plus the exact Binance route and primary-document provenance."""

    authority: LimitAuthority
    method: str
    path: str
    source_url: str
    retrieved_on: str
    payload: object

    def __post_init__(self):
        object.__setattr__(self, 'authority', LimitAuthority(plain(self.authority)))
        expected = {
            LimitAuthority.EXCHANGE_INFO: ('GET', EXCHANGE_INFO_PATH, MARKET_DATA_SOURCE),
            LimitAuthority.USER_RATE_LIMIT: ('GET', ACCOUNT_ORDER_LIMIT_PATH, ACCOUNT_SOURCE),
        }[self.authority]
        if (self.method, self.path, self.source_url) != expected or self.retrieved_on != SOURCE_RETRIEVED_ON:
            raise BudgetConfigurationError('limit_source_unverified')


@dataclass(frozen=True, slots=True, kw_only=True)
class ConfiguredLimit:
    limiter: Limiter
    limit: int
    authority: LimitAuthority
    source_path: str
    source_url: str
    retrieved_on: str
    _proof: bytes = field(repr=False, compare=False)

    def __post_init__(self):
        object.__setattr__(self, 'limiter', Limiter(plain(self.limiter)))
        object.__setattr__(self, 'authority', LimitAuthority(plain(self.authority)))
        check_int(self.limit, 'ConfiguredLimit.limit', 1, 2 ** 63 - 1)
        expected = (LimitAuthority.EXCHANGE_INFO if self.limiter is Limiter.REQUEST_WEIGHT_1M
                    else LimitAuthority.USER_RATE_LIMIT)
        req(self.authority is expected, 'ConfiguredLimit.authority', f'{self.limiter} requires {expected}')
        expected_path = EXCHANGE_INFO_PATH if expected is LimitAuthority.EXCHANGE_INFO else ACCOUNT_ORDER_LIMIT_PATH
        expected_url = MARKET_DATA_SOURCE if expected is LimitAuthority.EXCHANGE_INFO else ACCOUNT_SOURCE
        if (self.source_path, self.source_url, self.retrieved_on) != (expected_path, expected_url, SOURCE_RETRIEVED_ON):
            raise BudgetConfigurationError('limit_source_unverified')
        self.assert_verified()

    def assert_verified(self) -> None:
        expected = _limit_proof(limiter=self.limiter, limit=self.limit, authority=self.authority,
                                source_path=self.source_path, source_url=self.source_url,
                                retrieved_on=self.retrieved_on)
        if type(self._proof) is not bytes or not hmac.compare_digest(self._proof, expected):
            raise BudgetConfigurationError('limits_unverified')


def _verified_limit(limiter: Limiter, limit: int, raw: RawLimitPayload) -> ConfiguredLimit:
    proof = _limit_proof(limiter=limiter, limit=limit, authority=raw.authority,
                         source_path=raw.path, source_url=raw.source_url, retrieved_on=raw.retrieved_on)
    return ConfiguredLimit(limiter=limiter, limit=limit, authority=raw.authority,
                           source_path=raw.path, source_url=raw.source_url, retrieved_on=raw.retrieved_on,
                           _proof=proof)


def _one_limit(rows: object, *, kind: str, interval: str, interval_num: int) -> int:
    if type(rows) is not list or not all(type(row) is dict for row in rows):
        raise BudgetConfigurationError('limits_unknown')
    for row in rows:
        if (type(row.get('rateLimitType')) is not str or type(row.get('interval')) is not str
                or type(row.get('intervalNum')) is not int or type(row.get('limit')) is not int):
            raise BudgetConfigurationError('limits_unknown')
    matches = [row for row in rows if (row.get('rateLimitType'), row.get('interval'), row.get('intervalNum'))
               == (kind, interval, interval_num)]
    if len(matches) != 1:
        raise BudgetConfigurationError('limits_unknown')
    value = matches[0].get('limit')
    if type(value) is not int or value <= 0:
        raise BudgetConfigurationError('limits_unknown')
    return value


def load_verified_limits(*, exchange_info: RawLimitPayload,
                         account_order_limits: RawLimitPayload) -> tuple[ConfiguredLimit, ...]:
    """Parse the two authoritative raw Binance payloads; examples/defaults are forbidden."""
    if (not isinstance(exchange_info, RawLimitPayload)
            or exchange_info.authority is not LimitAuthority.EXCHANGE_INFO
            or not isinstance(account_order_limits, RawLimitPayload)
            or account_order_limits.authority is not LimitAuthority.USER_RATE_LIMIT):
        raise BudgetConfigurationError('limit_source_unverified')
    if type(exchange_info.payload) is not dict:
        raise BudgetConfigurationError('limits_unknown')
    request = _one_limit(exchange_info.payload.get('rateLimits'), kind='REQUEST_WEIGHT',
                         interval='MINUTE', interval_num=1)
    orders_10s = _one_limit(account_order_limits.payload, kind='ORDERS', interval='SECOND', interval_num=10)
    orders_1m = _one_limit(account_order_limits.payload, kind='ORDERS', interval='MINUTE', interval_num=1)
    return (_verified_limit(Limiter.REQUEST_WEIGHT_1M, request, exchange_info),
            _verified_limit(Limiter.ORDERS_10S, orders_10s, account_order_limits),
            _verified_limit(Limiter.ORDERS_1M, orders_1m, account_order_limits))


@dataclass(frozen=True, slots=True, kw_only=True)
class EndpointCost:
    table_version: str
    operation: Operation
    route: str | None
    method: str
    path: str
    request_weight_1m: int
    orders_10s: int | None
    orders_1m: int | None
    source_url: str
    retrieved_on: str

    def __post_init__(self):
        req(self.table_version == ENDPOINT_TABLE_VERSION, 'EndpointCost.table_version', ENDPOINT_TABLE_VERSION)
        object.__setattr__(self, 'operation', Operation(plain(self.operation)))
        req(self.route in (None, 'classic', 'algo'), 'EndpointCost.route', 'classic, algo or None')
        req(self.method in ('GET', 'POST', 'DELETE'), 'EndpointCost.method', 'GET, POST or DELETE')
        req(self.path.startswith('/fapi/'), 'EndpointCost.path', 'a versioned /fapi route')
        check_int(self.request_weight_1m, 'EndpointCost.request_weight_1m', 0, 1000)
        for name in ('orders_10s', 'orders_1m'):
            value = getattr(self, name)
            if value is not None:
                check_int(value, f'EndpointCost.{name}', 0, 1000)
        req(self.source_url in (TRADE_SOURCE, ACCOUNT_SOURCE), 'EndpointCost.source_url', 'an accepted official source')
        req(self.retrieved_on == SOURCE_RETRIEVED_ON, 'EndpointCost.retrieved_on', SOURCE_RETRIEVED_ON)

    @property
    def documented_costs(self) -> dict[Limiter, int | None]:
        return {Limiter.REQUEST_WEIGHT_1M: self.request_weight_1m,
                Limiter.ORDERS_10S: self.orders_10s,
                Limiter.ORDERS_1M: self.orders_1m}


def _cost(operation, route, method, path, weight, o10=None, o1m=None, source=TRADE_SOURCE):
    return EndpointCost(table_version=ENDPOINT_TABLE_VERSION, operation=operation, route=route, method=method,
                        path=path, request_weight_1m=weight, orders_10s=o10, orders_1m=o1m,
                        source_url=source, retrieved_on=SOURCE_RETRIEVED_ON)


ENDPOINT_COSTS_V1 = (
    _cost(Operation.SUBMIT_MARKET, 'classic', 'POST', '/fapi/v1/order', 0, 1, 1),
    _cost(Operation.SUBMIT_STOP, 'algo', 'POST', '/fapi/v1/algoOrder', 0, 1, 1),
    _cost(Operation.CANCEL, 'classic', 'DELETE', '/fapi/v1/order', 1),
    _cost(Operation.CANCEL, 'algo', 'DELETE', '/fapi/v1/algoOrder', 1),
    _cost(Operation.QUERY, 'classic', 'GET', '/fapi/v1/order', 1),
    _cost(Operation.QUERY, 'algo', 'GET', '/fapi/v1/algoOrder', 1),
    _cost(Operation.POSITIONS, None, 'GET', '/fapi/v2/positionRisk', 5),
    _cost(Operation.OPEN_ORDERS, None, 'GET', '/fapi/v1/openOrders', 1),
    _cost(Operation.FILLS, None, 'GET', '/fapi/v1/userTrades', 5),
)
_ENDPOINTS = {(x.operation, x.route): x for x in ENDPOINT_COSTS_V1}


@dataclass(frozen=True, slots=True, kw_only=True)
class PositionsRequest:
    symbol: str | None = None

    def __post_init__(self):
        if self.symbol is not None:
            check_symbol(self.symbol, 'PositionsRequest.symbol')


@dataclass(frozen=True, slots=True, kw_only=True)
class OpenOrdersRequest:
    symbol: str | None

    def __post_init__(self):
        if self.symbol is not None:
            check_symbol(self.symbol, 'OpenOrdersRequest.symbol')


@dataclass(frozen=True, slots=True, kw_only=True)
class FillsRequest:
    symbol: str
    exchange_order_id: str

    def __post_init__(self):
        check_symbol(self.symbol, 'FillsRequest.symbol')
        check_text(self.exchange_order_id, 'FillsRequest.exchange_order_id', 64)
        req(self.exchange_order_id.isascii() and self.exchange_order_id.isdigit()
            and int(self.exchange_order_id) > 0, 'FillsRequest.exchange_order_id', 'a positive ASCII integer')


@dataclass(frozen=True, slots=True, kw_only=True)
class Reservation:
    endpoint: EndpointCost
    pool: Pool
    costs: tuple[tuple[Limiter, int], ...]

    def cost(self, limiter: Limiter) -> int:
        return dict(self.costs)[limiter]


_NO_WORK = frozenset({Action.WAIT, Action.SKIP, Action.RESUME, Action.HALT, Action.PAUSE})


def reservation_for(action: Action, operation: Operation, payload: object) -> Reservation:
    """Total Action x operation x payload mapping.  Refusal is an explicit result."""
    try:
        action, operation = Action(plain(action)), Operation(plain(operation))
    except (TypeError, ValueError):
        raise AdmissionRefused('unmapped_work') from None
    if action in _NO_WORK:
        raise AdmissionRefused('no_work_action')

    route = None
    if operation is Operation.SUBMIT_MARKET:
        if not isinstance(payload, MarketOrder):
            raise AdmissionRefused('payload_type')
        route = payload.ref.route
        if not payload.reduce and action in (Action.ENTER, Action.ADD):
            pool = Pool.GENERAL
        elif payload.reduce and action in (Action.FLATTEN, Action.CLOSE, Action.REDUCE):
            pool = Pool.PROTECTION
        else:
            raise AdmissionRefused('ambiguous_submit_intent')
    elif operation is Operation.SUBMIT_STOP:
        if not isinstance(payload, StopOrder):
            raise AdmissionRefused('payload_type')
        route = payload.ref.route
        if action is not Action.PROTECT:
            raise AdmissionRefused('ambiguous_submit_intent')
        pool = Pool.PROTECTION
    elif operation in (Operation.CANCEL, Operation.QUERY):
        if not isinstance(payload, OrderRef):
            raise AdmissionRefused('payload_type')
        route = payload.route
        if operation is Operation.CANCEL:
            if action in (Action.PROTECT, Action.RECONCILE):
                pool = Pool.PROTECTION
            elif action is Action.CANCEL_ENTRY:
                pool = Pool.GENERAL
            else:
                raise AdmissionRefused('ambiguous_cancel_intent')
        else:
            pool = Pool.PROTECTION if action in (Action.PROTECT, Action.RECONCILE) else Pool.GENERAL
    elif operation is Operation.POSITIONS:
        if not isinstance(payload, PositionsRequest):
            raise AdmissionRefused('payload_type')
        pool = Pool.PROTECTION if action in (Action.PROTECT, Action.RECONCILE) else Pool.GENERAL
    elif operation is Operation.OPEN_ORDERS:
        if not isinstance(payload, OpenOrdersRequest):
            raise AdmissionRefused('payload_type')
        if payload.symbol is None:
            raise AdmissionRefused('open_orders_requires_symbol')
        pool = Pool.PROTECTION if action in (Action.PROTECT, Action.RECONCILE) else Pool.GENERAL
    elif operation is Operation.FILLS:
        if not isinstance(payload, FillsRequest):
            raise AdmissionRefused('payload_type')
        pool = Pool.PROTECTION if action in (Action.PROTECT, Action.RECONCILE) else Pool.GENERAL
    else:  # pragma: no cover - enum exhaustiveness guard
        raise AdmissionRefused('unmapped_work')

    endpoint = _ENDPOINTS.get((operation, route))
    if endpoint is None:
        raise AdmissionRefused('endpoint_unverified')
    documented = endpoint.documented_costs
    # D3 conservative policy: a cancel reserves one local order token in every
    # order window.  This is explicitly policy, not a Binance-documented cost.
    if operation is Operation.CANCEL:
        documented[Limiter.ORDERS_10S] = documented[Limiter.ORDERS_1M] = 1
    costs = tuple((limiter, int(documented[limiter] or 0)) for limiter in Limiter)
    return Reservation(endpoint=endpoint, pool=pool, costs=costs)


@dataclass(frozen=True, slots=True, kw_only=True)
class BudgetSnapshot:
    state: RateBlockState
    cause: str
    blocked_until_monotonic_ms: int | None
    usage: tuple[tuple[Limiter, int], ...]
    limits: tuple[tuple[Limiter, int], ...]
    reserves: tuple[tuple[Limiter, int], ...]


class AccountBudget:
    """Injected-clock sliding-window budget.  It never sleeps or refunds."""

    def __init__(self, *, account_id: str, egress_id: str, limits: Iterable[ConfiguredLimit],
                 monotonic_clock: Callable[[], int], wall_clock: Callable[[], int],
                 journal: RateBlockJournalPort | None = None):
        check_id(account_id, 'AccountBudget.account_id', 'acct')
        check_text(egress_id, 'AccountBudget.egress_id', 64)
        req(callable(monotonic_clock) and callable(wall_clock), 'AccountBudget.clock', 'two callables')
        rows = tuple(limits)
        req(all(isinstance(x, ConfiguredLimit) for x in rows), 'AccountBudget.limits', 'ConfiguredLimit rows')
        for row in rows:
            row.assert_verified()
        by = {x.limiter: x for x in rows}
        if len(by) != len(rows) or set(by) != set(Limiter):
            raise BudgetConfigurationError('limits_unknown')
        if journal is not None and not isinstance(journal, RateBlockJournalPort):
            raise BudgetConfigurationError('journal_invalid')
        self.account_id, self.egress_id = account_id, egress_id
        self._limits = {k: v.limit for k, v in by.items()}
        self._mono, self._wall, self._journal = monotonic_clock, wall_clock, journal
        self._events = {k: deque() for k in Limiter}
        self._evidence = deque()
        self._state = RateBlockState.OPEN
        self._cause = 'initial'
        self._deadline_mono: int | None = None
        self._lock = threading.RLock()
        # Largest admissible protection call plus D3 cancel order-token policy.
        self._reserves = {
            limiter: min(self._limits[limiter], max(
                5 if limiter is Limiter.REQUEST_WEIGHT_1M else 1,
                math.ceil(self._limits[limiter] * 0.10)))
            for limiter in Limiter
        }

    @classmethod
    def restore(cls, *, account_id: str, egress_id: str, limits: Iterable[ConfiguredLimit],
                monotonic_clock: Callable[[], int], wall_clock: Callable[[], int],
                journal: RateBlockJournalPort):
        budget = cls(account_id=account_id, egress_id=egress_id, limits=limits,
                     monotonic_clock=monotonic_clock, wall_clock=wall_clock, journal=journal)
        mono, wall = budget._now_mono(), budget._now_wall()
        try:
            records = journal.load_rate_blocks(account_id, egress_id)
            req(type(records) is tuple and all(isinstance(x, RateBlockRecord) for x in records),
                'rate_blocks', 'a tuple of RateBlockRecord')
            req(all((x.account_id, x.egress_id) == (account_id, egress_id) for x in records),
                'rate_blocks', 'records for this account and egress')
        except Exception:
            budget._set_block(RateBlockState.BLOCKED_UNKNOWN_DURATION, 'record_unreadable', mono, wall, persist=True)
            return budget
        if not records:
            budget._set_block(RateBlockState.BLOCKED_UNKNOWN_DURATION, 'restart_no_record', mono, wall, persist=True)
            return budget
        last = records[-1]
        if wall < last.observed_wall_ms:
            budget._set_block(RateBlockState.BLOCKED_UNKNOWN_DURATION, 'backward_wall_clock', mono, wall, persist=True)
        elif not last.clean_shutdown:
            budget._set_block(RateBlockState.BLOCKED_UNKNOWN_DURATION, 'unclean_restart', mono, wall, persist=True)
        elif last.state is RateBlockState.BLOCKED_UNTIL and last.wall_deadline_ms > wall:
            budget._state, budget._cause = last.state, last.cause
            budget._deadline_mono = mono + (last.wall_deadline_ms - wall)
        elif last.state is RateBlockState.BLOCKED_UNKNOWN_DURATION:
            budget._state, budget._cause = last.state, last.cause
        else:
            budget._state, budget._cause, budget._deadline_mono = RateBlockState.OPEN, 'restored_open', None
        return budget

    def _now_mono(self):
        value = self._mono()
        check_int(value, 'monotonic_clock', 0, 2 ** 63 - 1)
        return value

    def _now_wall(self):
        value = self._wall()
        check_ms(value, 'wall_clock')
        return value

    def _append(self, *, clean_shutdown: bool, wall_deadline_ms: int | None = None):
        if self._journal is None:
            return
        record = RateBlockRecord(schema_version=1, account_id=self.account_id, egress_id=self.egress_id,
                                 state=self._state, wall_deadline_ms=wall_deadline_ms,
                                 observed_wall_ms=self._now_wall(), observed_monotonic_ms=self._now_mono(),
                                 cause=self._cause, clean_shutdown=clean_shutdown)
        self._journal.append_rate_block(record)

    def _set_block(self, state: RateBlockState, cause: str, mono: int, wall: int, *,
                   deadline_mono: int | None = None, persist: bool):
        self._state, self._cause, self._deadline_mono = state, cause, deadline_mono
        if persist:
            deadline_wall = None if deadline_mono is None else wall + (deadline_mono - mono)
            try:
                self._append(clean_shutdown=False, wall_deadline_ms=deadline_wall)
            except Exception:
                self._state, self._cause, self._deadline_mono = (
                    RateBlockState.BLOCKED_UNKNOWN_DURATION, 'rate_block_persist_failed', None)
                raise

    def _prune(self, now: int):
        for limiter, items in self._events.items():
            edge = now - WINDOW_MS[limiter]
            while items and items[0][0] <= edge:
                items.popleft()
        edge = now - WINDOW_MS[Limiter.REQUEST_WEIGHT_1M]
        while self._evidence and self._evidence[0][0] <= edge:
            self._evidence.popleft()

    def _usage(self, limiter: Limiter) -> int:
        local = sum(cost for _at, cost in self._events[limiter])
        if limiter is Limiter.REQUEST_WEIGHT_1M and self._evidence:
            return max(local, max(value + max(0, local - local_at)
                                  for _at, value, local_at in self._evidence))
        return local

    def _release_elapsed(self, mono: int, wall: int):
        if (self._state is RateBlockState.BLOCKED_UNTIL and self._deadline_mono is not None
                and mono >= self._deadline_mono):
            self._set_block(RateBlockState.OPEN, 'deadline_elapsed', mono, wall, persist=True)

    def reserve(self, reservation: Reservation, now_ms: int | None = None) -> None:
        req(isinstance(reservation, Reservation), 'reservation', 'a Reservation')
        with self._lock:
            now = self._now_mono() if now_ms is None else now_ms
            check_int(now, 'now_ms', 0, 2 ** 63 - 1)
            wall = self._now_wall()
            self._release_elapsed(now, wall)
            if self._state is RateBlockState.BLOCKED_UNKNOWN_DURATION:
                if self._cause.startswith('ban_418') or reservation.pool is Pool.GENERAL:
                    raise BudgetRefused('blocked_unknown_duration')
            elif self._state is RateBlockState.BLOCKED_UNTIL and reservation.pool is Pool.GENERAL:
                raise BudgetRefused('blocked_until')
            self._prune(now)
            for limiter, cost in reservation.costs:
                projected = self._usage(limiter) + cost
                ceiling = self._limits[limiter]
                if reservation.pool is Pool.GENERAL:
                    ceiling -= self._reserves[limiter]
                if projected > ceiling:
                    raise BudgetRefused('budget_saturated')
            for limiter, cost in reservation.costs:
                if cost:
                    self._events[limiter].append((now, cost))

    def observe(self, evidence: RateEvidence, *, now_ms: int | None = None, wall_now_ms: int | None = None) -> None:
        req(isinstance(evidence, RateEvidence), 'evidence', 'RateEvidence')
        with self._lock:
            mono = self._now_mono() if now_ms is None else now_ms
            wall = self._now_wall() if wall_now_ms is None else wall_now_ms
            check_int(mono, 'now_ms', 0, 2 ** 63 - 1); check_ms(wall, 'wall_now_ms')
            self._prune(mono)
            if evidence.used_weight_1m is not None:
                local = sum(cost for _at, cost in self._events[Limiter.REQUEST_WEIGHT_1M])
                self._evidence.append((mono, evidence.used_weight_1m, local))
            if evidence.http_status == 418:
                self._set_block(RateBlockState.BLOCKED_UNKNOWN_DURATION, 'ban_418', mono, wall, persist=True)
            elif evidence.http_status == 429:
                if evidence.retry_after_ms is None:
                    self._set_block(RateBlockState.BLOCKED_UNKNOWN_DURATION, 'rate_429_no_deadline', mono, wall,
                                    persist=True)
                elif self._state is not RateBlockState.BLOCKED_UNKNOWN_DURATION:
                    deadline = mono + evidence.retry_after_ms
                    if self._deadline_mono is not None:
                        deadline = max(deadline, self._deadline_mono)
                    self._set_block(RateBlockState.BLOCKED_UNTIL, 'rate_429_retry_after', mono, wall,
                                    deadline_mono=deadline, persist=True)

    def release_after_reconciliation(self) -> None:
        with self._lock:
            mono, wall = self._now_mono(), self._now_wall()
            self._set_block(RateBlockState.OPEN, 'audited_release', mono, wall, persist=True)

    def record_shutdown(self, clean: bool) -> None:
        req(type(clean) is bool, 'clean', 'a bool')
        with self._lock:
            deadline_wall = None
            if self._deadline_mono is not None:
                mono, wall = self._now_mono(), self._now_wall()
                deadline_wall = wall + max(0, self._deadline_mono - mono)
            self._append(clean_shutdown=clean, wall_deadline_ms=deadline_wall)

    def snapshot(self, now_ms: int | None = None) -> BudgetSnapshot:
        with self._lock:
            now = self._now_mono() if now_ms is None else now_ms
            check_int(now, 'now_ms', 0, 2 ** 63 - 1)
            self._release_elapsed(now, self._now_wall())
            self._prune(now)
            return BudgetSnapshot(state=self._state, cause=self._cause,
                                  blocked_until_monotonic_ms=self._deadline_mono,
                                  usage=tuple((x, self._usage(x)) for x in Limiter),
                                  limits=tuple(self._limits.items()), reserves=tuple(self._reserves.items()))


@dataclass(frozen=True, slots=True, kw_only=True)
class VenueWork:
    work_id: str
    action: Action
    operation: Operation
    payload: object
    enqueued_at_ms: int
    deadline_ms: int

    def __post_init__(self):
        check_text(self.work_id, 'VenueWork.work_id', 64)
        object.__setattr__(self, 'action', Action(plain(self.action)))
        object.__setattr__(self, 'operation', Operation(plain(self.operation)))
        check_int(self.enqueued_at_ms, 'VenueWork.enqueued_at_ms', 0, 2 ** 63 - 1)
        check_int(self.deadline_ms, 'VenueWork.deadline_ms', 0, 2 ** 63 - 1)
        req(self.deadline_ms >= self.enqueued_at_ms, 'VenueWork.deadline_ms', 'not before enqueue')


class WorkState(enum.StrEnum):
    QUEUED = 'queued'
    COMPLETED = 'completed'
    REFUSED = 'refused'
    CANCELLED = 'cancelled'
    EXPIRED = 'expired'
    UNKNOWN = 'unknown'


@dataclass(frozen=True, slots=True, kw_only=True)
class WorkResult:
    work_id: str
    state: WorkState
    detail: str
    value: object | None = None


@dataclass(frozen=True, slots=True, kw_only=True)
class AccountSnapshot:
    account_id: str
    queued: tuple[str, ...]
    in_flight: str | None
    results: tuple[WorkResult, ...]
    stopping: bool
    reconcile_before_restart: bool
    budget: BudgetSnapshot


class AccountContext:
    """One account-local priority queue and explicit one-shot worker."""

    def __init__(self, *, account_id: str, egress_id: str, venue: VenuePort, budget: AccountBudget,
                 monotonic_clock: Callable[[], int]):
        check_id(account_id, 'AccountContext.account_id', 'acct')
        check_text(egress_id, 'AccountContext.egress_id', 64)
        req((budget.account_id, budget.egress_id) == (account_id, egress_id), 'AccountContext.budget',
            'same account and egress')
        req(callable(monotonic_clock), 'AccountContext.monotonic_clock', 'a callable')
        for name in ('submit_market', 'submit_stop', 'cancel', 'query', 'positions', 'open_orders', 'fills'):
            req(callable(getattr(venue, name, None)), 'AccountContext.venue', f'VenuePort.{name}')
        self.account_id, self.egress_id = account_id, egress_id
        self._venue, self._budget, self._clock = venue, budget, monotonic_clock
        self._heap: list[tuple[int, int, str]] = []
        self._work: dict[str, tuple[VenueWork, Reservation]] = {}
        self._results: dict[str, WorkResult] = {}
        self._cancelled: dict[str, int] = {}
        self._sequence = 0
        self._inflight: tuple[VenueWork, Reservation] | None = None
        self._stopping = False
        self._stop_deadline: int | None = None
        self._reconcile = False
        self._shutdown_recorded = False
        self._lock = threading.RLock()

    def _now(self):
        value = self._clock()
        check_int(value, 'monotonic_clock', 0, 2 ** 63 - 1)
        return value

    def submit(self, work: VenueWork) -> None:
        req(isinstance(work, VenueWork), 'work', 'VenueWork')
        reservation = reservation_for(work.action, work.operation, work.payload)
        with self._lock:
            if self._stopping:
                raise AdmissionRefused('worker_stopping')
            if work.work_id in self._work or work.work_id in self._results:
                raise AdmissionRefused('duplicate_work_id')
            self._sequence += 1
            self._work[work.work_id] = (work, reservation)
            heapq.heappush(self._heap, (PRIORITY[work.action], self._sequence, work.work_id))

    def cancel(self, work_id: str, at_ms: int | None = None) -> bool:
        check_text(work_id, 'work_id', 64)
        with self._lock:
            if (work_id not in self._work
                    or self._inflight is not None and self._inflight[0].work_id == work_id):
                return False
            at = self._now() if at_ms is None else at_ms
            check_int(at, 'at_ms', 0, 2 ** 63 - 1)
            self._cancelled[work_id] = min(at, self._cancelled.get(work_id, at))
            return True

    def _finish(self, result: WorkResult):
        self._results[result.work_id] = result
        self._work.pop(result.work_id, None)
        self._cancelled.pop(result.work_id, None)

    def _record_terminal_shutdown(self) -> None:
        if (self._stopping and self._inflight is None and not self._work
                and not self._shutdown_recorded):
            self._budget.record_shutdown(clean=not self._reconcile)
            self._shutdown_recorded = True

    def _dispatch(self, work: VenueWork):
        op, payload = work.operation, work.payload
        if op is Operation.SUBMIT_MARKET:
            return self._venue.submit_market(payload)
        if op is Operation.SUBMIT_STOP:
            return self._venue.submit_stop(payload)
        if op is Operation.CANCEL:
            return self._venue.cancel(payload)
        if op is Operation.QUERY:
            return self._venue.query(payload)
        if op is Operation.POSITIONS:
            return self._venue.positions(payload.symbol)
        if op is Operation.OPEN_ORDERS:
            return self._venue.open_orders(payload.symbol)
        if op is Operation.FILLS:
            return self._venue.fills(payload.symbol, payload.exchange_order_id)
        raise AssertionError(op)  # pragma: no cover

    def run_next(self) -> WorkResult | None:
        with self._lock:
            while self._heap:
                _priority, _seq, work_id = heapq.heappop(self._heap)
                item = self._work.get(work_id)
                if item is None:
                    continue
                work, reservation = item
                now = self._now()
                cancelled_at = self._cancelled.get(work_id)
                if cancelled_at is not None and cancelled_at <= now:  # same-time cancellation wins
                    out = WorkResult(work_id=work_id, state=WorkState.CANCELLED, detail='cancelled_before_dequeue')
                    self._finish(out); self._record_terminal_shutdown(); return out
                if now >= work.deadline_ms:
                    out = WorkResult(work_id=work_id, state=WorkState.EXPIRED, detail='deadline_before_dequeue')
                    self._finish(out); self._record_terminal_shutdown(); return out
                if self._stopping and self._stop_deadline is not None and now >= self._stop_deadline:
                    out = WorkResult(work_id=work_id, state=WorkState.CANCELLED, detail='shutdown_deadline')
                    self._finish(out); self._record_terminal_shutdown(); return out
                try:
                    self._budget.reserve(reservation, now)
                except BudgetRefused as ex:
                    out = WorkResult(work_id=work_id, state=WorkState.REFUSED, detail=ex.code)
                    self._finish(out); self._record_terminal_shutdown(); return out
                self._inflight = item
                break
            else:
                self._record_terminal_shutdown()
                return None

        try:
            value = self._dispatch(work)
            result = WorkResult(work_id=work.work_id, state=WorkState.COMPLETED, detail='one_shot', value=value)
        except Exception:
            result = WorkResult(work_id=work.work_id, state=WorkState.UNKNOWN, detail='venue_exception')
        with self._lock:
            finished_at = self._now()
            self._inflight = None
            shutdown_late = (self._stopping and self._stop_deadline is not None
                             and finished_at >= self._stop_deadline)
            if finished_at >= work.deadline_ms or shutdown_late:
                self._reconcile = True
                detail = 'shutdown_in_flight' if self._stopping else 'deadline_in_flight'
                result = WorkResult(work_id=work.work_id, state=WorkState.UNKNOWN, detail=detail)
            elif result.state is WorkState.UNKNOWN:
                self._reconcile = True
            self._finish(result)
            self._record_terminal_shutdown()
            return result

    def stop(self, deadline_ms: int) -> AccountSnapshot:
        check_int(deadline_ms, 'deadline_ms', 0, 2 ** 63 - 1)
        with self._lock:
            self._stopping, self._stop_deadline = True, deadline_ms
            now = self._now()
            for work_id, (work, reservation) in tuple(self._work.items()):
                if self._inflight is not None and work_id == self._inflight[0].work_id:
                    continue
                if reservation.pool is Pool.GENERAL or now >= deadline_ms:
                    self._finish(WorkResult(work_id=work_id, state=WorkState.CANCELLED, detail='shutdown_queued'))
            if self._inflight is not None and (now >= deadline_ms or now >= self._inflight[0].deadline_ms):
                self._reconcile = True
            self._record_terminal_shutdown()
            return self.snapshot()

    def result(self, work_id: str) -> WorkResult | None:
        return self._results.get(work_id)

    def snapshot(self) -> AccountSnapshot:
        with self._lock:
            queued = tuple(work_id for _p, _s, work_id in sorted(self._heap) if work_id in self._work)
            return AccountSnapshot(account_id=self.account_id, queued=queued,
                                   in_flight=None if self._inflight is None else self._inflight[0].work_id,
                                   results=tuple(self._results.values()), stopping=self._stopping,
                                   reconcile_before_restart=self._reconcile, budget=self._budget.snapshot())


def validate_distinct_egress(bindings: Iterable[tuple[str, str]], *, environment: str) -> None:
    """D6 deployment preflight: live/mainnet cannot claim isolation on shared IP egress."""
    rows = tuple(bindings)
    for account_id, egress_id in rows:
        check_id(account_id, 'account_id', 'acct'); check_text(egress_id, 'egress_id', 64)
    if environment in ('live', 'mainnet') and len({egress for _account, egress in rows}) != len(rows):
        raise AdmissionRefused('shared_egress_unsupported')


__all__ = [
    'ACCOUNT_ORDER_LIMIT_PATH', 'ACCOUNT_SOURCE', 'ENDPOINT_COSTS_V1', 'ENDPOINT_TABLE_VERSION', 'EXCHANGE_INFO_PATH',
    'GENERAL_SOURCE', 'MARKET_DATA_SOURCE', 'SOURCE_RETRIEVED_ON', 'TRADE_SOURCE', 'AccountBudget', 'AccountContext',
    'AccountSnapshot', 'AdmissionRefused', 'BudgetConfigurationError',
    'BudgetRefused', 'BudgetSnapshot', 'ConfiguredLimit', 'EndpointCost', 'FillsRequest', 'LimitAuthority', 'Limiter',
    'OpenOrdersRequest', 'Operation', 'Pool', 'PositionsRequest', 'RawLimitPayload', 'Reservation', 'VenueWork',
    'WorkResult', 'WorkState', 'load_verified_limits', 'reservation_for', 'validate_distinct_egress',
]
