from __future__ import annotations

import copy
import dataclasses
from decimal import Decimal

import pytest
import newcore.account_context as account_context_module

from newcore.account_context import (
    ACCOUNT_ORDER_LIMIT_PATH,
    ACCOUNT_SOURCE,
    ENDPOINT_COSTS_V1,
    ENDPOINT_TABLE_VERSION,
    EXCHANGE_INFO_PATH,
    MARKET_DATA_SOURCE,
    SOURCE_RETRIEVED_ON,
    TRADE_SOURCE,
    AccountBudget,
    AccountContext,
    AdmissionRefused,
    BudgetConfigurationError,
    BudgetRefused,
    ConfiguredLimit,
    FillsRequest,
    LimitAuthority,
    Limiter,
    OpenOrdersRequest,
    Operation,
    Pool,
    PositionsRequest,
    RawLimitPayload,
    VenueWork,
    WorkState,
    load_verified_limits,
    reservation_for,
    validate_distinct_egress,
)
from newcore.domain import Action
from newcore.ports.rate_blocks import RateBlockRecord, RateBlockState
from newcore.ports.venue import MarketOrder, OrderRef, StopOrder
from newcore.venue.binance_usdm_testnet import RateEvidence


ACCT = 'acct_' + 'a' * 32
EGRESS = 'egress-a'
WALL = 1_800_000_000_000


class Clock:
    def __init__(self, value=1_000_000):
        self.value = value

    def __call__(self):
        return self.value

    def advance(self, ms):
        self.value += ms


class MemoryRateJournal:
    def __init__(self):
        self.records = []
        self.load_error = None

    def append_rate_block(self, record):
        self.records.append(record)

    def load_rate_blocks(self, account_id, egress_id):
        if self.load_error:
            raise self.load_error
        return tuple(x for x in self.records if (x.account_id, x.egress_id) == (account_id, egress_id))


class FakeVenue:
    def __init__(self, callback=None, raises=False):
        self.calls = []
        self.callback = callback
        self.raises = raises

    def _call(self, name, *args):
        self.calls.append((name, args))
        if self.callback:
            self.callback(name, *args)
        if self.raises:
            raise RuntimeError('fake venue failure')
        return (name, args)

    def submit_market(self, order): return self._call('submit_market', order)
    def submit_stop(self, order): return self._call('submit_stop', order)
    def cancel(self, ref): return self._call('cancel', ref)
    def query(self, ref): return self._call('query', ref)
    def positions(self, symbol=None): return self._call('positions', symbol)
    def open_orders(self, symbol=None): return self._call('open_orders', symbol)
    def fills(self, symbol, exchange_order_id): return self._call('fills', symbol, exchange_order_id)


def limit_evidence(req=100, o10=20, o1m=100):
    exchange = RawLimitPayload(
        authority=LimitAuthority.EXCHANGE_INFO, method='GET', path=EXCHANGE_INFO_PATH,
        source_url=MARKET_DATA_SOURCE, retrieved_on=SOURCE_RETRIEVED_ON,
        payload={'rateLimits': [
            {'rateLimitType': 'REQUEST_WEIGHT', 'interval': 'MINUTE', 'intervalNum': 1, 'limit': req},
        ]})
    account = RawLimitPayload(
        authority=LimitAuthority.USER_RATE_LIMIT, method='GET', path=ACCOUNT_ORDER_LIMIT_PATH,
        source_url=ACCOUNT_SOURCE, retrieved_on=SOURCE_RETRIEVED_ON,
        payload=[
            {'rateLimitType': 'ORDERS', 'interval': 'SECOND', 'intervalNum': 10, 'limit': o10},
            {'rateLimitType': 'ORDERS', 'interval': 'MINUTE', 'intervalNum': 1, 'limit': o1m},
        ])
    return exchange, account


def budget(mono=None, wall=None, journal=None, **values):
    mono, wall = mono or Clock(), wall or Clock(WALL)
    exchange, account = limit_evidence(**values)
    return AccountBudget(account_id=ACCT, egress_id=EGRESS, exchange_info=exchange,
                         account_order_limits=account,
                         monotonic_clock=mono, wall_clock=wall, journal=journal)


def restore_budget(*, mono, wall, journal, **values):
    exchange, account = limit_evidence(**values)
    return AccountBudget.restore(account_id=ACCT, egress_id=EGRESS, exchange_info=exchange,
        account_order_limits=account, monotonic_clock=mono, wall_clock=wall, journal=journal)


def ref(route='classic', suffix='1'):
    return OrderRef(symbol='BTCUSDT', client_id=f'zbn1{"a" if route == "algo" else "o"}-{suffix}', route=route)


def market(*, reduce=False, suffix='1'):
    return MarketOrder(ref=ref('classic', suffix), position_side='LONG', qty=Decimal('0.001'), reduce=reduce)


def stop(suffix='1', route='algo'):
    return StopOrder(ref=ref(route, suffix), position_side='LONG', qty=Decimal('0.001'),
                     stop_price=Decimal('50000'))


def work(work_id, action, operation, payload, at=1_000_000, deadline=1_010_000):
    return VenueWork(work_id=work_id, action=action, operation=operation, payload=payload,
                     enqueued_at_ms=at, deadline_ms=deadline)


def entry_res(suffix='1'):
    return reservation_for(Action.ENTER, Operation.SUBMIT_MARKET, market(suffix=suffix))


def protect_res(suffix='1'):
    return reservation_for(Action.PROTECT, Operation.SUBMIT_STOP, stop(suffix=suffix))


def test_versioned_endpoint_table_is_exact_and_source_pinned():
    assert len(ENDPOINT_COSTS_V1) == 9
    assert {x.table_version for x in ENDPOINT_COSTS_V1} == {ENDPOINT_TABLE_VERSION}
    assert {x.retrieved_on for x in ENDPOINT_COSTS_V1} == {SOURCE_RETRIEVED_ON}
    assert {x.source_url for x in ENDPOINT_COSTS_V1} <= {TRADE_SOURCE, ACCOUNT_SOURCE}
    keyed = {(x.operation, x.route): x for x in ENDPOINT_COSTS_V1}
    assert keyed[Operation.SUBMIT_MARKET, 'classic'].documented_costs == {
        Limiter.REQUEST_WEIGHT_1M: 0, Limiter.ORDERS_10S: 1, Limiter.ORDERS_1M: 1}
    assert keyed[Operation.SUBMIT_STOP, 'algo'].documented_costs == {
        Limiter.REQUEST_WEIGHT_1M: 0, Limiter.ORDERS_10S: 1, Limiter.ORDERS_1M: 1}
    assert keyed[Operation.OPEN_ORDERS, None].request_weight_1m == 1
    assert keyed[Operation.POSITIONS, None].request_weight_1m == 5
    assert keyed[Operation.FILLS, None].request_weight_1m == 5


def test_derived_limit_compatibility_paths_are_fail_closed():
    exchange, account = limit_evidence()
    with pytest.raises(BudgetConfigurationError, match='limits_unverified'):
        AccountBudget(account_id=ACCT, egress_id=EGRESS, limits=(1, 2, 3),
                      monotonic_clock=Clock(), wall_clock=Clock(WALL))
    with pytest.raises(BudgetConfigurationError, match='limit_source_unverified'):
        AccountBudget(account_id=ACCT, egress_id=EGRESS,
                      monotonic_clock=Clock(), wall_clock=Clock(WALL))
    with pytest.raises(BudgetConfigurationError, match='limits_unverified'):
        ConfiguredLimit(limiter=Limiter.ORDERS_10S, limit=20,
                        authority=LimitAuthority.USER_RATE_LIMIT)
    with pytest.raises(BudgetConfigurationError, match='derived_limits_private'):
        load_verified_limits(exchange_info=exchange, account_order_limits=account)
    with pytest.raises(TypeError, match='closed'):
        class EvilConfiguredLimit(ConfiguredLimit):
            def assert_verified(self):
                return None


@pytest.mark.parametrize('mutate', ('missing_weight', 'string_weight', 'missing_10s', 'missing_1m', 'duplicate'))
def test_raw_limit_loader_rejects_missing_malformed_or_ambiguous_values(mutate):
    exchange_payload = {'rateLimits': [
        {'rateLimitType': 'REQUEST_WEIGHT', 'interval': 'MINUTE', 'intervalNum': 1, 'limit': 100},
    ]}
    order_payload = [
        {'rateLimitType': 'ORDERS', 'interval': 'SECOND', 'intervalNum': 10, 'limit': 20},
        {'rateLimitType': 'ORDERS', 'interval': 'MINUTE', 'intervalNum': 1, 'limit': 100},
    ]
    if mutate == 'missing_weight': exchange_payload['rateLimits'] = []
    if mutate == 'string_weight': exchange_payload['rateLimits'][0]['limit'] = '100'
    if mutate == 'missing_10s': order_payload.pop(0)
    if mutate == 'missing_1m': order_payload.pop(1)
    if mutate == 'duplicate': order_payload.append(dict(order_payload[0]))
    exchange = RawLimitPayload(authority=LimitAuthority.EXCHANGE_INFO, method='GET', path=EXCHANGE_INFO_PATH,
        source_url=MARKET_DATA_SOURCE, retrieved_on=SOURCE_RETRIEVED_ON, payload=exchange_payload)
    account = RawLimitPayload(authority=LimitAuthority.USER_RATE_LIMIT, method='GET',
        path=ACCOUNT_ORDER_LIMIT_PATH, source_url=ACCOUNT_SOURCE, retrieved_on=SOURCE_RETRIEVED_ON,
        payload=order_payload)
    with pytest.raises(BudgetConfigurationError, match='limits_unknown'):
        AccountBudget(account_id=ACCT, egress_id=EGRESS, exchange_info=exchange,
            account_order_limits=account, monotonic_clock=Clock(), wall_clock=Clock(WALL))


@pytest.mark.parametrize('target,field,value', (
    ('exchange', 'intervalNum', True), ('exchange', 'limit', False),
    ('exchange', 'interval', 1), ('account', 'intervalNum', True),
    ('account', 'limit', False), ('account', 'rateLimitType', ['ORDERS']),
))
def test_raw_limit_loader_requires_exact_json_field_types(target, field, value):
    exchange_payload = {'rateLimits': [
        {'rateLimitType': 'REQUEST_WEIGHT', 'interval': 'MINUTE', 'intervalNum': 1, 'limit': 100},
    ]}
    order_payload = [
        {'rateLimitType': 'ORDERS', 'interval': 'SECOND', 'intervalNum': 10, 'limit': 20},
        {'rateLimitType': 'ORDERS', 'interval': 'MINUTE', 'intervalNum': 1, 'limit': 100},
    ]
    row = exchange_payload['rateLimits'][0] if target == 'exchange' else order_payload[0]
    row[field] = value
    exchange = RawLimitPayload(authority=LimitAuthority.EXCHANGE_INFO, method='GET', path=EXCHANGE_INFO_PATH,
        source_url=MARKET_DATA_SOURCE, retrieved_on=SOURCE_RETRIEVED_ON, payload=exchange_payload)
    account = RawLimitPayload(authority=LimitAuthority.USER_RATE_LIMIT, method='GET',
        path=ACCOUNT_ORDER_LIMIT_PATH, source_url=ACCOUNT_SOURCE, retrieved_on=SOURCE_RETRIEVED_ON,
        payload=order_payload)
    with pytest.raises(BudgetConfigurationError, match='limits_unknown'):
        AccountBudget(account_id=ACCT, egress_id=EGRESS, exchange_info=exchange,
            account_order_limits=account, monotonic_clock=Clock(), wall_clock=Clock(WALL))


def test_private_limit_rows_are_final_and_cannot_reenter_budget_by_replace_or_copy():
    exchange, account = limit_evidence()
    rows = account_context_module._parse_verified_limits(
        exchange_info=exchange, account_order_limits=account)
    with pytest.raises((TypeError, ValueError), match='takes no arguments|init=False'):
        dataclasses.replace(rows[0], limit=rows[0].limit + 1)
    for copier in (copy.copy, copy.deepcopy):
        forged = copier(rows[1])
        object.__setattr__(forged, 'limit', forged.limit + 1)
        with pytest.raises(BudgetConfigurationError, match='limits_unverified'):
            AccountBudget(account_id=ACCT, egress_id=EGRESS,
                exchange_info=exchange, account_order_limits=account,
                limits=(rows[0], forged, rows[2]), monotonic_clock=Clock(), wall_clock=Clock(WALL))

    with pytest.raises(TypeError, match='final'):
        class EvilParsedLimit(account_context_module._VerifiedLimit):
            pass


def test_raw_payload_subclass_cannot_override_provenance_validation():
    class EvilRawLimitPayload(RawLimitPayload):
        def __post_init__(self):
            pass

    _, account = limit_evidence()
    evil = EvilRawLimitPayload(authority=LimitAuthority.EXCHANGE_INFO, method='POST', path='/forged',
        source_url='https://attacker.invalid', retrieved_on='never', payload={'rateLimits': []})
    with pytest.raises(BudgetConfigurationError, match='limit_source_unverified'):
        AccountBudget(account_id=ACCT, egress_id=EGRESS, exchange_info=evil,
            account_order_limits=account, monotonic_clock=Clock(), wall_clock=Clock(WALL))


@pytest.mark.parametrize('field,value', (
    ('method', 'POST'), ('path', '/fapi/v1/exchangeInfo-typo'),
    ('source_url', ACCOUNT_SOURCE), ('retrieved_on', '2026-11-02'),
))
def test_raw_limit_payload_rejects_unverified_source_provenance(field, value):
    values = dict(authority=LimitAuthority.EXCHANGE_INFO, method='GET', path=EXCHANGE_INFO_PATH,
                  source_url=MARKET_DATA_SOURCE, retrieved_on=SOURCE_RETRIEVED_ON,
                  payload={'rateLimits': []})
    values[field] = value
    with pytest.raises(BudgetConfigurationError, match='limit_source_unverified'):
        RawLimitPayload(**values)


def test_20_order_10s_burst_preserves_two_token_protection_reserve():
    b = budget()
    for i in range(18):
        b.reserve(entry_res(str(i)))
    with pytest.raises(BudgetRefused, match='budget_saturated'):
        b.reserve(entry_res('x'))
    b.reserve(protect_res('p1'))
    b.reserve(protect_res('p2'))
    with pytest.raises(BudgetRefused, match='budget_saturated'):
        b.reserve(protect_res('p3'))
    snap = b.snapshot()
    assert dict(snap.usage)[Limiter.ORDERS_10S] == 20
    assert dict(snap.reserves)[Limiter.ORDERS_10S] == 2


def test_sliding_windows_replenish_only_after_full_window():
    mono = Clock()
    b = budget(mono=mono, o10=10)
    for i in range(9):
        b.reserve(protect_res(str(i)))
    with pytest.raises(BudgetRefused):
        b.reserve(entry_res('blocked'))
    mono.advance(9_999)
    with pytest.raises(BudgetRefused):
        b.reserve(entry_res('still'))
    mono.advance(1)
    b.reserve(entry_res('open'))


def test_used_weight_evidence_can_raise_but_not_lower_usage():
    b = budget(req=100)
    b.observe(RateEvidence(200, None, 95, WALL))
    b.observe(RateEvidence(200, None, 1, WALL + 1))
    with pytest.raises(BudgetRefused):
        b.reserve(reservation_for(Action.ENTER, Operation.POSITIONS, PositionsRequest(symbol='BTCUSDT')))
    b.reserve(reservation_for(Action.RECONCILE, Operation.POSITIONS, PositionsRequest(symbol='BTCUSDT')))
    assert dict(b.snapshot().usage)[Limiter.REQUEST_WEIGHT_1M] == 100


@pytest.mark.parametrize('action', tuple(Action))
@pytest.mark.parametrize('operation,payload', (
    (Operation.SUBMIT_MARKET, market()),
    (Operation.SUBMIT_STOP, stop()),
    (Operation.CANCEL, ref()),
    (Operation.QUERY, ref()),
    (Operation.POSITIONS, PositionsRequest(symbol='BTCUSDT')),
    (Operation.OPEN_ORDERS, OpenOrdersRequest(symbol='BTCUSDT')),
    (Operation.FILLS, FillsRequest(symbol='BTCUSDT', exchange_order_id='123')),
))
def test_action_operation_mapping_is_total(action, operation, payload):
    try:
        out = reservation_for(action, operation, payload)
        assert out.pool in Pool and len(out.costs) == 3
    except AdmissionRefused as ex:
        assert ex.code in {'no_work_action', 'ambiguous_submit_intent', 'ambiguous_cancel_intent'}


def test_mapping_pool_rulings_and_forbidden_routes():
    assert reservation_for(Action.ENTER, Operation.SUBMIT_MARKET, market()).pool is Pool.GENERAL
    assert reservation_for(Action.CLOSE, Operation.SUBMIT_MARKET, market(reduce=True)).pool is Pool.PROTECTION
    assert reservation_for(Action.PROTECT, Operation.SUBMIT_STOP, stop()).pool is Pool.PROTECTION
    assert reservation_for(Action.CANCEL_ENTRY, Operation.CANCEL, ref()).pool is Pool.GENERAL
    assert reservation_for(Action.RECONCILE, Operation.CANCEL, ref()).pool is Pool.PROTECTION
    assert reservation_for(Action.RECONCILE, Operation.QUERY, ref()).pool is Pool.PROTECTION
    with pytest.raises(AdmissionRefused, match='open_orders_requires_symbol'):
        reservation_for(Action.RECONCILE, Operation.OPEN_ORDERS, OpenOrdersRequest(symbol=None))
    with pytest.raises(AdmissionRefused, match='endpoint_unverified'):
        reservation_for(Action.PROTECT, Operation.SUBMIT_STOP, stop(route='classic'))


def test_cancel_uses_documented_weight_and_conservative_local_order_tokens():
    out = reservation_for(Action.PROTECT, Operation.CANCEL, ref('algo'))
    assert out.endpoint.orders_10s is None and out.endpoint.orders_1m is None
    assert dict(out.costs) == {Limiter.REQUEST_WEIGHT_1M: 1, Limiter.ORDERS_10S: 1, Limiter.ORDERS_1M: 1}


def test_429_retry_after_blocks_general_not_reserved_protection_then_expires():
    mono, wall, journal = Clock(), Clock(WALL), MemoryRateJournal()
    b = budget(mono=mono, wall=wall, journal=journal)
    b.observe(RateEvidence(429, 5_000, 7, WALL))
    with pytest.raises(BudgetRefused, match='blocked_until'):
        b.reserve(entry_res())
    b.reserve(protect_res())
    mono.advance(5_000); wall.advance(5_000)
    b.reserve(entry_res('after'))
    assert b.snapshot().state is RateBlockState.OPEN
    assert [x.state for x in journal.records][-1] is RateBlockState.OPEN


def test_429_without_deadline_is_unknown_but_keeps_protection_path():
    b = budget()
    b.observe(RateEvidence(429, None, None, WALL))
    with pytest.raises(BudgetRefused, match='blocked_unknown_duration'):
        b.reserve(entry_res())
    b.reserve(protect_res())
    assert b.snapshot().cause == 'rate_429_no_deadline'


def test_418_quarantines_all_calls_until_explicit_release():
    b = budget()
    b.observe(RateEvidence(418, 1_000, None, WALL))
    with pytest.raises(BudgetRefused, match='blocked_unknown_duration'):
        b.reserve(entry_res())
    with pytest.raises(BudgetRefused, match='blocked_unknown_duration'):
        b.reserve(protect_res())
    b.release_after_reconciliation()
    b.reserve(protect_res('released'))


@pytest.mark.parametrize('status', (500, 502, 503, 504))
def test_5xx_does_not_invent_rate_block(status):
    b = budget()
    b.observe(RateEvidence(status, None, None, WALL))
    assert b.snapshot().state is RateBlockState.OPEN


def test_clean_restart_restores_deadline_against_wall_clock():
    mono, wall, journal = Clock(), Clock(WALL), MemoryRateJournal()
    b = budget(mono=mono, wall=wall, journal=journal)
    b.observe(RateEvidence(429, 5_000, None, WALL))
    b.record_shutdown(clean=True)
    wall.advance(2_000)
    restored = restore_budget(mono=Clock(50), wall=wall, journal=journal)
    snap = restored.snapshot()
    assert snap.state is RateBlockState.BLOCKED_UNTIL
    assert snap.blocked_until_monotonic_ms == 3_050


def test_unclean_backward_clock_and_missing_records_restore_unknown():
    for journal, wall, expected in (
        (MemoryRateJournal(), Clock(WALL), 'restart_no_record'),
        (MemoryRateJournal(), Clock(WALL), 'unclean_restart'),
        (MemoryRateJournal(), Clock(WALL - 1), 'backward_wall_clock'),
    ):
        if expected != 'restart_no_record':
            journal.records.append(RateBlockRecord(schema_version=1, account_id=ACCT, egress_id=EGRESS,
                state=RateBlockState.OPEN, wall_deadline_ms=None, observed_wall_ms=WALL,
                observed_monotonic_ms=100, cause='open', clean_shutdown=(expected == 'backward_wall_clock')))
        restored = restore_budget(mono=Clock(50), wall=wall, journal=journal)
        assert restored.snapshot().state is RateBlockState.BLOCKED_UNKNOWN_DURATION
        assert restored.snapshot().cause == expected


def test_unreadable_record_restores_unknown_not_open():
    journal = MemoryRateJournal(); journal.load_error = OSError('bad record')
    restored = restore_budget(mono=Clock(), wall=Clock(WALL), journal=journal)
    assert restored.snapshot().cause == 'record_unreadable'


def test_priority_protection_runs_before_earlier_entry():
    mono = Clock()
    venue = FakeVenue()
    ctx = AccountContext(account_id=ACCT, egress_id=EGRESS, venue=venue, budget=budget(mono=mono),
                         monotonic_clock=mono)
    ctx.submit(work('entry', Action.ENTER, Operation.SUBMIT_MARKET, market(suffix='e')))
    ctx.submit(work('protect', Action.PROTECT, Operation.SUBMIT_STOP, stop('p')))
    assert ctx.run_next().work_id == 'protect'
    assert venue.calls[0][0] == 'submit_stop'


def test_budget_refusal_dials_zero_times_and_venue_exception_is_one_shot_unknown():
    mono, venue = Clock(), FakeVenue()
    b = budget(mono=mono, o10=1)
    b.reserve(protect_res('full'))
    ctx = AccountContext(account_id=ACCT, egress_id=EGRESS, venue=venue, budget=b, monotonic_clock=mono)
    ctx.submit(work('blocked', Action.ENTER, Operation.SUBMIT_MARKET, market(suffix='b')))
    assert ctx.run_next().state is WorkState.REFUSED and venue.calls == []

    failing = FakeVenue(raises=True)
    ctx2 = AccountContext(account_id=ACCT, egress_id=EGRESS, venue=failing,
                          budget=budget(mono=mono), monotonic_clock=mono)
    ctx2.submit(work('once', Action.RECONCILE, Operation.QUERY, ref(suffix='q')))
    result = ctx2.run_next()
    assert result.state is WorkState.UNKNOWN and result.detail == 'venue_exception'
    assert len(failing.calls) == 1


def test_cancel_and_deadline_at_dequeue_send_nothing_same_time_cancel_wins():
    mono, venue = Clock(), FakeVenue()
    ctx = AccountContext(account_id=ACCT, egress_id=EGRESS, venue=venue,
                         budget=budget(mono=mono), monotonic_clock=mono)
    ctx.submit(work('cancelled', Action.RECONCILE, Operation.QUERY, ref(suffix='c')))
    assert ctx.cancel('cancelled', at_ms=mono.value)
    out = ctx.run_next()
    assert out.state is WorkState.CANCELLED and out.detail == 'cancelled_before_dequeue' and venue.calls == []
    ctx.submit(work('expired', Action.RECONCILE, Operation.QUERY, ref(suffix='d'), deadline=mono.value + 1))
    mono.advance(1)
    assert ctx.run_next().state is WorkState.EXPIRED and venue.calls == []


def test_cancel_after_dequeue_cannot_pretend_to_cancel_one_shot():
    mono = Clock()
    holder = {}
    def callback(_name, *_args):
        assert holder['ctx'].cancel('inflight') is False
    venue = FakeVenue(callback=callback)
    holder['ctx'] = AccountContext(account_id=ACCT, egress_id=EGRESS, venue=venue,
        budget=budget(mono=mono), monotonic_clock=mono)
    holder['ctx'].submit(work('inflight', Action.RECONCILE, Operation.QUERY, ref(suffix='i')))
    assert holder['ctx'].run_next().state is WorkState.COMPLETED
    assert len(venue.calls) == 1


def test_shutdown_inflight_deadline_is_unknown_and_requires_reconcile():
    mono = Clock()
    holder = {}
    def callback(_name, *_args):
        mono.advance(10_000)
        holder['ctx'].stop(mono.value)
    venue = FakeVenue(callback=callback)
    holder['ctx'] = AccountContext(account_id=ACCT, egress_id=EGRESS, venue=venue,
        budget=budget(mono=mono), monotonic_clock=mono)
    holder['ctx'].submit(work('inflight', Action.RECONCILE, Operation.QUERY, ref(suffix='s')))
    result = holder['ctx'].run_next()
    assert result.state is WorkState.UNKNOWN and result.detail == 'shutdown_in_flight'
    assert holder['ctx'].snapshot().reconcile_before_restart and len(venue.calls) == 1


def test_stop_cancels_general_queue_but_leaves_protection_until_bounded_drain():
    mono, journal = Clock(), MemoryRateJournal()
    ctx = AccountContext(account_id=ACCT, egress_id=EGRESS, venue=FakeVenue(),
        budget=budget(mono=mono, journal=journal), monotonic_clock=mono)
    ctx.submit(work('entry', Action.ENTER, Operation.SUBMIT_MARKET, market(suffix='e')))
    ctx.submit(work('protect', Action.PROTECT, Operation.SUBMIT_STOP, stop('p')))
    snap = ctx.stop(mono.value + 100)
    assert ctx.result('entry').state is WorkState.CANCELLED
    assert snap.queued == ('protect',)
    assert journal.records == []  # not terminal: protection still has a bounded drain opportunity
    assert ctx.run_next().state is WorkState.COMPLETED
    assert len(journal.records) == 1 and journal.records[-1].clean_shutdown


def test_late_queued_protection_persists_unclean_before_return_and_restart_fails_closed():
    mono, wall, journal = Clock(), Clock(WALL), MemoryRateJournal()

    def late_completion(_name, *_args):
        mono.advance(101)

    ctx = AccountContext(account_id=ACCT, egress_id=EGRESS, venue=FakeVenue(callback=late_completion),
        budget=budget(mono=mono, wall=wall, journal=journal), monotonic_clock=mono)
    ctx.submit(work('protect', Action.PROTECT, Operation.SUBMIT_STOP, stop('late'), deadline=mono.value + 1_000))
    ctx.stop(mono.value + 100)
    assert journal.records == []

    result = ctx.run_next()
    assert result.state is WorkState.UNKNOWN and result.detail == 'shutdown_in_flight'
    assert len(journal.records) == 1 and journal.records[-1].clean_shutdown is False

    restored = restore_budget(mono=Clock(50), wall=wall, journal=journal)
    snapshot = restored.snapshot()
    assert snapshot.state is RateBlockState.BLOCKED_UNKNOWN_DURATION
    assert snapshot.cause == 'unclean_restart'


def test_queued_protection_cancelled_at_stop_deadline_records_clean_terminal_drain():
    mono, journal = Clock(), MemoryRateJournal()
    ctx = AccountContext(account_id=ACCT, egress_id=EGRESS, venue=FakeVenue(),
        budget=budget(mono=mono, journal=journal), monotonic_clock=mono)
    ctx.submit(work('protect', Action.PROTECT, Operation.SUBMIT_STOP, stop('bounded'),
                    deadline=mono.value + 1_000))
    ctx.stop(mono.value + 100)
    mono.advance(100)
    result = ctx.run_next()
    assert result.state is WorkState.CANCELLED and result.detail == 'shutdown_deadline'
    assert len(journal.records) == 1 and journal.records[-1].clean_shutdown


def test_distinct_egress_isolation_and_shared_egress_mainnet_refusal():
    other = 'acct_' + 'b' * 32
    validate_distinct_egress(((ACCT, 'ip-a'), (other, 'ip-b')), environment='mainnet')
    validate_distinct_egress(((ACCT, 'shared'), (other, 'shared')), environment='testnet')
    with pytest.raises(AdmissionRefused, match='shared_egress_unsupported'):
        validate_distinct_egress(((ACCT, 'shared'), (other, 'shared')), environment='mainnet')
