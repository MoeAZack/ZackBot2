from __future__ import annotations

import ast
import inspect
from dataclasses import replace
from decimal import Decimal

import pytest

import newcore.reconciliation as reconciliation_module
from newcore.domain.account import (Account, AccountBinding, BindingConfirmation, BindingState, Environment, Venue,
                                    confirmation_phrase)
from newcore.domain.orders import IntentState, Purpose, Side
from newcore.domain.reasons import ReasonCode
from newcore.reconciliation import (ActionKind, DurableState, EvidencePage, FindingCode, LocalOrder, LocalPosition,
                                    PlannerConfig, PlanStatus, ReconciliationError, SnapshotComponent, TradeEffect,
                                    VenueOrder, VenueOrderKind, VenueOrderState, VenuePosition, VenueSnapshot, VenueTrade,
                                    plan_reconciliation)


D = Decimal
NOW = 2_000_000_010_000
OBSERVED = 2_000_000_009_000
DIGEST = "0123456789abcdef"


def _id(prefix: str, number: int) -> str:
    return f"{prefix}_{number:032x}"


def _binding(digest: str = DIGEST) -> AccountBinding:
    return AccountBinding(venue=Venue.BINANCE_USDM, environment=Environment.TESTNET, settlement_asset="USDT",
                          key_digest=digest, exchange_uid="testnet-user")


def _account(*, state: BindingState = BindingState.CONFIRMED, digest: str = DIGEST) -> Account:
    account_id = _id("acct", 1)
    binding = _binding(digest)
    confirmation = None
    if state is not BindingState.UNCONFIRMED:
        confirmation = BindingConfirmation(account_id=account_id, old_key_digest=None, new_key_digest=digest,
                                           typed_phrase=confirmation_phrase(account_id, digest),
                                           confirmed_at_ms=NOW - 20_000)
    return Account(account_id=account_id, label="paper", hedge_mode=True, binding=binding, binding_state=state,
                   proposed_binding=None, confirmation=confirmation)


def _position(qty: str = "5", side: Side = Side.LONG) -> LocalPosition:
    return LocalPosition("BTCUSDT", side, D(qty))


def _local_order(*, number: int = 1, purpose: Purpose = Purpose.PROTECT, qty: str = "5",
                 executed: str = "0", side: Side = Side.LONG, exchange_id: str | None = "100",
                 state: IntentState = IntentState.WORKING) -> LocalOrder:
    return LocalOrder(_id("int", number), f"cid-{number}", exchange_id, "BTCUSDT", side, purpose, state, D(qty),
                      D(executed))


def _state(*, positions=(), orders=(), account: Account | None = None, generation: int = 7,
           last_observed: int = OBSERVED - 1_000) -> DurableState:
    return DurableState(account or _account(), generation, last_observed, tuple(positions), tuple(orders))


def _venue_position(qty: str = "5", side: Side = Side.LONG) -> VenuePosition:
    return VenuePosition("BTCUSDT", side, D(qty))


def _venue_order(*, client: str = "cid-1", exchange_id: str = "100", qty: str = "5",
                 executed: str = "0", side: Side = Side.LONG, kind: VenueOrderKind = VenueOrderKind.STOP_MARKET,
                 reduce_only: bool = True) -> VenueOrder:
    state = VenueOrderState.PARTIALLY_FILLED if D(executed) else VenueOrderState.OPEN
    return VenueOrder(exchange_id, client, "BTCUSDT", side, kind, state, reduce_only, D(qty), D(executed))


def _trade(*, number: int = 1, client: str | None = None, qty: str = "1", side: Side = Side.LONG,
           effect: TradeEffect = TradeEffect.REDUCE, at_ms: int = OBSERVED) -> VenueTrade:
    return VenueTrade(f"trade-{number}", client, "BTCUSDT", side, effect, D(qty), D("100"), at_ms)


def _page(component: SnapshotComponent, records=(), *, number: int = 1, complete: bool = True,
          generation: int = 3, observed: int = OBSERVED, binding: AccountBinding | None = None,
          account_id: str | None = None) -> EvidencePage:
    return EvidencePage(component, account_id or _id("acct", 1), binding or _binding(), generation, number, observed,
                        complete, tuple(records))


def _snapshot(*, positions=(), orders=(), trades=(), generation: int = 3,
              observed: int = OBSERVED) -> VenueSnapshot:
    return VenueSnapshot((
        _page(SnapshotComponent.POSITIONS, positions, generation=generation, observed=observed),
        _page(SnapshotComponent.OPEN_ORDERS, orders, generation=generation, observed=observed),
        _page(SnapshotComponent.USER_TRADES, trades, generation=generation, observed=observed),
    ))


def _config(*, now: int = NOW, age: int = 5) -> PlannerConfig:
    return PlannerConfig(now, age)


def _codes(plan):
    return {x.code for x in plan.findings}


def _actions(plan):
    return {x.kind for x in plan.actions}


def test_flat_snapshot_is_closed_and_proven():
    plan = plan_reconciliation(_state(), _snapshot(), _config())
    assert plan.status is PlanStatus.FLAT_CONFIRMED
    assert plan.reason is ReasonCode.RECONCILE_FLAT_SNAPSHOT
    assert plan.findings == plan.actions == ()
    assert plan.reconciliation_id.startswith("rec_")
    assert len(plan.evidence_sha256) == 64


@pytest.mark.parametrize("side", [Side.LONG, Side.SHORT])
def test_owned_fully_protected_position_is_closed(side):
    local_order = _local_order(side=side)
    plan = plan_reconciliation(_state(positions=(_position(side=side),), orders=(local_order,)),
                               _snapshot(positions=(_venue_position(side=side),),
                                         orders=(_venue_order(side=side),)), _config())
    assert plan.status is PlanStatus.PROTECTED_CONFIRMED
    assert plan.reason is ReasonCode.RECONCILE_MATCH


def test_same_evidence_is_idempotent_and_input_order_is_canonical():
    long = LocalPosition("BTCUSDT", Side.LONG, D("2"))
    short = LocalPosition("BTCUSDT", Side.SHORT, D("3"))
    lo1 = _local_order(number=1, qty="2", side=Side.LONG)
    lo2 = _local_order(number=2, qty="3", side=Side.SHORT, exchange_id="200")
    vp1 = _venue_position("2", Side.LONG)
    vp2 = _venue_position("3", Side.SHORT)
    vo1 = _venue_order(qty="2", side=Side.LONG)
    vo2 = _venue_order(client="cid-2", exchange_id="200", qty="3", side=Side.SHORT)
    a = plan_reconciliation(_state(positions=(long, short), orders=(lo1, lo2)),
                            _snapshot(positions=(vp1, vp2), orders=(vo1, vo2)), _config())
    pages = _snapshot(positions=(vp2, vp1), orders=(vo2, vo1)).pages
    b = plan_reconciliation(_state(positions=(short, long), orders=(lo2, lo1)),
                            VenueSnapshot(tuple(reversed(pages))), _config())
    assert a == b


def test_evidence_digest_and_plan_id_bind_snapshot_generation():
    a = plan_reconciliation(_state(), _snapshot(generation=3), _config())
    b = plan_reconciliation(_state(), _snapshot(generation=4), _config())
    assert (a.evidence_sha256, a.reconciliation_id) != (b.evidence_sha256, b.reconciliation_id)


def test_equal_decimal_values_have_equal_canonical_plan_identity():
    a = plan_reconciliation(_state(positions=(_position("5.0"),), orders=(_local_order(qty="5.00"),)),
                            _snapshot(positions=(_venue_position("5.000"),),
                                      orders=(_venue_order(qty="5.0"),)), _config())
    b = plan_reconciliation(_state(positions=(_position("5"),), orders=(_local_order(qty="5"),)),
                            _snapshot(positions=(_venue_position("5"),), orders=(_venue_order(qty="5"),)), _config())
    assert (a.evidence_sha256, a.reconciliation_id) == (b.evidence_sha256, b.reconciliation_id)


def test_local_only_position_holds_without_guessing_flat_or_flattening():
    plan = plan_reconciliation(_state(positions=(_position(),)), _snapshot(), _config())
    assert plan.status is PlanStatus.HOLD
    assert FindingCode.LOCAL_POSITION_ONLY in _codes(plan)
    assert ActionKind.FLATTEN_REQUIRED not in _actions(plan)


def test_venue_only_position_is_quarantined_not_adopted_or_flattened():
    plan = plan_reconciliation(_state(), _snapshot(positions=(_venue_position(),)), _config())
    assert FindingCode.VENUE_POSITION_ONLY in _codes(plan)
    assert _actions(plan) == {ActionKind.QUARANTINE, ActionKind.OWNER_REVIEW}
    assert all(x.kind is not ActionKind.FLATTEN_REQUIRED for x in plan.actions)


def test_manual_add_is_quarantined_and_protected_but_never_adopted():
    local_stop = _local_order(qty="7")
    plan = plan_reconciliation(_state(positions=(_position("5"),), orders=(local_stop,)),
                               _snapshot(positions=(_venue_position("7"),), orders=(_venue_order(qty="7"),)),
                               _config())
    assert FindingCode.MANUAL_ADD in _codes(plan)
    assert ActionKind.QUARANTINE in _actions(plan)
    assert ActionKind.ADOPT_EXTERNAL_REDUCTION not in _actions(plan)


def test_exact_trade_corroborates_external_reduction():
    plan = plan_reconciliation(_state(positions=(_position("5"),)),
                               _snapshot(positions=(_venue_position("3"),), trades=(_trade(qty="2"),)), _config())
    assert FindingCode.EXTERNAL_REDUCTION in _codes(plan)
    action = next(x for x in plan.actions if x.kind is ActionKind.ADOPT_EXTERNAL_REDUCTION)
    assert action.qty == D("2")


def test_uncorroborated_position_delta_is_hold_only():
    plan = plan_reconciliation(_state(positions=(_position("5"),)),
                               _snapshot(positions=(_venue_position("3"),)), _config())
    assert FindingCode.CONTRADICTORY_POSITION in _codes(plan)
    assert ActionKind.ADOPT_EXTERNAL_REDUCTION not in _actions(plan)
    assert ActionKind.FLATTEN_REQUIRED not in _actions(plan)


def test_owned_stop_filled_while_offline_proposes_durable_adoption():
    stop = _local_order(state=IntentState.UNKNOWN)
    plan = plan_reconciliation(_state(positions=(_position(),), orders=(stop,)),
                               _snapshot(trades=(_trade(client="cid-1", qty="5"),)), _config())
    assert FindingCode.STOP_FILLED_OFFLINE in _codes(plan)
    assert ActionKind.ADOPT_EXTERNAL_REDUCTION in _actions(plan)
    assert ActionKind.ADOPT_LATE_FILL in _actions(plan)


def test_partial_owned_stop_fill_with_corroborated_residual_requires_flatten():
    stop = _local_order(state=IntentState.UNKNOWN)
    plan = plan_reconciliation(_state(positions=(_position(),), orders=(stop,)),
                               _snapshot(positions=(_venue_position("2"),),
                                         trades=(_trade(client="cid-1", qty="3"),)), _config())
    flatten = next(x for x in plan.actions if x.kind is ActionKind.FLATTEN_REQUIRED)
    assert (flatten.symbol, flatten.side, flatten.qty) == ("BTCUSDT", Side.LONG, D("2"))


def test_missing_owned_protection_requires_protection_not_flatten():
    plan = plan_reconciliation(_state(positions=(_position(),)),
                               _snapshot(positions=(_venue_position(),)), _config())
    assert FindingCode.PROTECTION_MISSING in _codes(plan)
    assert _actions(plan) == {ActionKind.PROTECT_REQUIRED}


def test_undersized_owned_protection_requires_exact_shortfall():
    stop = _local_order(qty="3")
    plan = plan_reconciliation(_state(positions=(_position(),), orders=(stop,)),
                               _snapshot(positions=(_venue_position(),), orders=(_venue_order(qty="3"),)), _config())
    assert FindingCode.PROTECTION_UNDERSIZED in _codes(plan)
    protect = next(x for x in plan.actions if x.kind is ActionKind.PROTECT_REQUIRED)
    assert protect.qty == D("2")


def test_foreign_stop_never_counts_as_owned_coverage():
    foreign = _venue_order(client="foreign-cid", exchange_id="999")
    plan = plan_reconciliation(_state(positions=(_position(),)),
                               _snapshot(positions=(_venue_position(),), orders=(foreign,)), _config())
    assert {FindingCode.FOREIGN_ORDER, FindingCode.PROTECTION_MISSING} <= _codes(plan)
    assert ActionKind.PROTECT_REQUIRED in _actions(plan)


def test_local_only_live_order_requires_query():
    order = _local_order(purpose=Purpose.ENTRY, state=IntentState.UNKNOWN, exchange_id=None)
    plan = plan_reconciliation(_state(orders=(order,)), _snapshot(), _config())
    assert FindingCode.LOCAL_ORDER_ONLY in _codes(plan)
    assert _actions(plan) == {ActionKind.QUERY_ORDER}


def test_venue_only_order_is_foreign_and_quarantined():
    plan = plan_reconciliation(_state(), _snapshot(orders=(_venue_order(client="foreign"),)), _config())
    assert FindingCode.FOREIGN_ORDER in _codes(plan)
    assert _actions(plan) == {ActionKind.QUARANTINE}


def test_exchange_order_identity_mismatch_holds():
    local = _local_order(exchange_id="100")
    venue = _venue_order(exchange_id="101")
    plan = plan_reconciliation(_state(orders=(local,)), _snapshot(orders=(venue,)), _config())
    assert FindingCode.ORDER_ID_MISMATCH in _codes(plan)


def test_order_quantity_mismatch_holds():
    plan = plan_reconciliation(_state(orders=(_local_order(qty="5"),)),
                               _snapshot(orders=(_venue_order(qty="4"),)), _config())
    assert FindingCode.ORDER_QUANTITY_MISMATCH in _codes(plan)


def test_opening_order_can_never_publish_flat_or_protected_closed_state():
    local = _local_order(purpose=Purpose.ENTRY, exchange_id="100")
    venue = _venue_order(kind=VenueOrderKind.LIMIT, reduce_only=False)
    plan = plan_reconciliation(_state(orders=(local,)), _snapshot(orders=(venue,)), _config())
    assert plan.status is PlanStatus.HOLD
    assert FindingCode.OPENING_ORDER_PENDING in _codes(plan)


def test_reduce_only_order_without_position_is_orphaned_and_held():
    local = _local_order(purpose=Purpose.CLOSE, exchange_id="100")
    venue = _venue_order(kind=VenueOrderKind.LIMIT, reduce_only=True)
    plan = plan_reconciliation(_state(orders=(local,)), _snapshot(orders=(venue,)), _config())
    assert FindingCode.ORPHAN_ORDER in _codes(plan)
    assert ActionKind.QUERY_ORDER in _actions(plan)


def test_owned_protection_identity_does_not_override_wrong_venue_semantics():
    local = _local_order(purpose=Purpose.PROTECT)
    venue = _venue_order(kind=VenueOrderKind.LIMIT, reduce_only=False)
    plan = plan_reconciliation(_state(positions=(_position(),), orders=(local,)),
                               _snapshot(positions=(_venue_position(),), orders=(venue,)), _config())
    assert FindingCode.ORDER_SEMANTICS_MISMATCH in _codes(plan)
    assert FindingCode.PROTECTION_MISSING in _codes(plan)


def test_corroborated_partial_fill_proposes_owned_adoption():
    local = _local_order(purpose=Purpose.ENTRY, qty="5", executed="1")
    venue = _venue_order(kind=VenueOrderKind.LIMIT, reduce_only=False, qty="5", executed="2")
    trade = _trade(client="cid-1", qty="1", effect=TradeEffect.ADD)
    plan = plan_reconciliation(_state(orders=(local,)), _snapshot(orders=(venue,), trades=(trade,)), _config())
    assert FindingCode.PARTIAL_FILL in _codes(plan)
    assert ActionKind.ADOPT_LATE_FILL in _actions(plan)


def test_late_owned_fill_after_order_disappears_proposes_adoption():
    local = _local_order(purpose=Purpose.ENTRY, qty="5", state=IntentState.UNKNOWN, exchange_id=None)
    trade = _trade(client="cid-1", qty="2", effect=TradeEffect.ADD)
    plan = plan_reconciliation(_state(orders=(local,)), _snapshot(trades=(trade,)), _config())
    assert FindingCode.LATE_FILL in _codes(plan)
    assert ActionKind.ADOPT_LATE_FILL in _actions(plan)


@pytest.mark.parametrize(
    "trade_at,code",
    [
        (OBSERVED - 1_000, FindingCode.TRADE_STALE),
        (OBSERVED + 1, FindingCode.TRADE_FUTURE),
    ],
)
def test_adoption_trade_must_be_inside_exact_causal_window(trade_at, code):
    local = _local_order(purpose=Purpose.ENTRY, state=IntentState.UNKNOWN, exchange_id=None)
    trade = _trade(client="cid-1", qty="1", effect=TradeEffect.ADD, at_ms=trade_at)
    plan = plan_reconciliation(_state(orders=(local,)), _snapshot(trades=(trade,)), _config())
    assert code in _codes(plan)
    assert plan.actions == ()


def test_owned_trade_effect_must_match_durable_order_purpose():
    local = _local_order(purpose=Purpose.ENTRY, state=IntentState.UNKNOWN, exchange_id=None)
    wrong = _trade(client="cid-1", qty="1", effect=TradeEffect.REDUCE)
    plan = plan_reconciliation(_state(orders=(local,)), _snapshot(trades=(wrong,)), _config())
    assert FindingCode.TRADE_PURPOSE_MISMATCH in _codes(plan)
    assert ActionKind.ADOPT_LATE_FILL not in _actions(plan)


def test_partial_fill_requires_exact_new_execution_delta_not_greater_than():
    local = _local_order(purpose=Purpose.ENTRY, qty="5", executed="1")
    venue = _venue_order(kind=VenueOrderKind.LIMIT, reduce_only=False, qty="5", executed="2")
    too_much = _trade(client="cid-1", qty="2", effect=TradeEffect.ADD)
    plan = plan_reconciliation(_state(orders=(local,)), _snapshot(orders=(venue,), trades=(too_much,)), _config())
    assert FindingCode.TRADE_DELTA_MISMATCH in _codes(plan)
    assert ActionKind.ADOPT_LATE_FILL not in _actions(plan)


def test_open_order_adoption_refuses_total_fill_above_requested_quantity():
    local = _local_order(purpose=Purpose.ENTRY, qty="5", executed="4")
    venue = _venue_order(kind=VenueOrderKind.LIMIT, reduce_only=False, qty="5", executed="4.5")
    overflow = _trade(client="cid-1", qty="1.5", effect=TradeEffect.ADD)
    plan = plan_reconciliation(_state(orders=(local,)), _snapshot(orders=(venue,), trades=(overflow,)), _config())
    assert FindingCode.FILL_EXCEEDS_REQUESTED in _codes(plan)
    assert ActionKind.ADOPT_LATE_FILL not in _actions(plan)


def test_vanished_order_adoption_is_bounded_by_requested_quantity():
    local = _local_order(purpose=Purpose.ENTRY, qty="5", executed="4", state=IntentState.UNKNOWN, exchange_id=None)
    overflow = _trade(client="cid-1", qty="2", effect=TradeEffect.ADD)
    plan = plan_reconciliation(_state(orders=(local,)), _snapshot(trades=(overflow,)), _config())
    assert FindingCode.FILL_EXCEEDS_REQUESTED in _codes(plan)
    assert ActionKind.ADOPT_LATE_FILL not in _actions(plan)


def test_flatten_needs_exact_owned_stop_delta_not_mere_stop_trade_presence():
    stop = _local_order(purpose=Purpose.PROTECT, state=IntentState.UNKNOWN)
    partial_stop = _trade(number=1, client="cid-1", qty="1", effect=TradeEffect.REDUCE)
    external_reduce = _trade(number=2, client=None, qty="2", effect=TradeEffect.REDUCE)
    plan = plan_reconciliation(_state(positions=(_position("5"),), orders=(stop,)),
                               _snapshot(positions=(_venue_position("2"),),
                                         trades=(partial_stop, external_reduce)), _config())
    assert ActionKind.FLATTEN_REQUIRED not in _actions(plan)
    assert ActionKind.PROTECT_REQUIRED in _actions(plan)


def test_flatten_needs_owned_stop_trade_on_exact_instrument_and_side():
    stop = _local_order(purpose=Purpose.PROTECT, state=IntentState.UNKNOWN)
    wrong_side = _trade(client="cid-1", qty="3", side=Side.SHORT, effect=TradeEffect.REDUCE)
    external_reduce = _trade(number=2, client=None, qty="3", effect=TradeEffect.REDUCE)
    plan = plan_reconciliation(_state(positions=(_position("5"),), orders=(stop,)),
                               _snapshot(positions=(_venue_position("2"),),
                                         trades=(wrong_side, external_reduce)), _config())
    assert FindingCode.TRADE_PURPOSE_MISMATCH in _codes(plan)
    assert ActionKind.FLATTEN_REQUIRED not in _actions(plan)


@pytest.mark.parametrize(
    "snapshot,code",
    [
        (_snapshot(positions=(_venue_position(), _venue_position())), FindingCode.DUPLICATE_POSITION),
        (_snapshot(orders=(_venue_order(), _venue_order())), FindingCode.DUPLICATE_ORDER),
        (_snapshot(trades=(_trade(), _trade())), FindingCode.DUPLICATE_TRADE),
    ],
)
def test_duplicate_evidence_holds_before_actions(snapshot, code):
    plan = plan_reconciliation(_state(), snapshot, _config())
    assert plan.status is PlanStatus.HOLD
    assert code in _codes(plan)
    assert plan.actions == ()


def _mutate_page(snapshot: VenueSnapshot, index: int, **changes) -> VenueSnapshot:
    pages = list(snapshot.pages)
    pages[index] = replace(pages[index], **changes)
    return VenueSnapshot(tuple(pages))


@pytest.mark.parametrize(
    "snapshot,config,code",
    [
        (VenueSnapshot(_snapshot().pages[:-1]), _config(), FindingCode.SNAPSHOT_COMPONENT_MISSING),
        (_mutate_page(_snapshot(), 1, snapshot_generation=4), _config(), FindingCode.SNAPSHOT_MIXED_GENERATION),
        (_mutate_page(_snapshot(), 0, complete=False), _config(), FindingCode.SNAPSHOT_NOT_COMPLETE),
        (_mutate_page(_snapshot(), 0, observed_at_ms=OBSERVED - 1_001), _config(), FindingCode.SNAPSHOT_BACKWARD),
        (_snapshot(observed=OBSERVED - 10_000), _config(), FindingCode.SNAPSHOT_BACKWARD),
        (_snapshot(observed=NOW + 1), _config(), FindingCode.SNAPSHOT_FUTURE),
        (_snapshot(observed=OBSERVED - 1_000), _config(age=1), FindingCode.SNAPSHOT_STALE),
        (_mutate_page(_snapshot(), 0, records=(_trade(),)), _config(), FindingCode.SNAPSHOT_RECORD_KIND),
    ],
)
def test_incoherent_snapshot_fails_closed_without_actions(snapshot, config, code):
    plan = plan_reconciliation(_state(), snapshot, config)
    assert plan.status is PlanStatus.HOLD
    assert code in _codes(plan)
    assert plan.actions == ()


def test_page_gap_and_early_completion_fail_closed():
    base = _snapshot().pages
    first = replace(base[0], complete=True)
    third = replace(base[0], page_number=3, complete=True)
    plan = plan_reconciliation(_state(), VenueSnapshot((first, third, base[1], base[2])), _config())
    assert {FindingCode.SNAPSHOT_PAGE_GAP, FindingCode.SNAPSHOT_NOT_COMPLETE} <= _codes(plan)
    assert plan.actions == ()


def test_contiguous_explicit_pagination_is_accepted():
    base = _snapshot().pages
    p1 = replace(base[0], complete=False)
    p2 = replace(base[0], page_number=2, complete=True, observed_at_ms=OBSERVED + 1)
    plan = plan_reconciliation(_state(), VenueSnapshot((p2, base[2], p1, base[1])),
                               _config(now=NOW + 1))
    assert plan.status is PlanStatus.FLAT_CONFIRMED


def test_unconfirmed_local_binding_holds_before_comparison():
    plan = plan_reconciliation(_state(account=_account(state=BindingState.UNCONFIRMED)), _snapshot(), _config())
    assert FindingCode.BINDING_NOT_CONFIRMED in _codes(plan)
    assert plan.actions == ()


def test_page_binding_mismatch_holds_before_comparison():
    snapshot = _mutate_page(_snapshot(), 0, binding=_binding("fedcba9876543210"))
    plan = plan_reconciliation(_state(), snapshot, _config())
    assert FindingCode.BINDING_MISMATCH in _codes(plan)
    assert plan.actions == ()


@pytest.mark.parametrize("field", ["now_ms", "max_snapshot_age_s"])
def test_config_requires_exact_integer_not_bool(field):
    values = {"now_ms": NOW, "max_snapshot_age_s": 5}
    values[field] = True
    with pytest.raises(ReconciliationError, match=f"validation.config.{field}"):
        PlannerConfig(**values)


@pytest.mark.parametrize("bad", [D("NaN"), D("Infinity"), D("-Infinity"), D("-0"), D("1e10001")])
def test_nonfinite_decimal_is_rejected_stably(bad):
    with pytest.raises(ReconciliationError, match="validation.venue_position.qty"):
        VenuePosition("BTCUSDT", Side.LONG, bad)


def test_forged_exact_nested_value_is_revalidated_at_planner_seam():
    forged = object.__new__(LocalPosition)
    object.__setattr__(forged, "symbol", "BTCUSDT")
    object.__setattr__(forged, "side", Side.LONG)
    object.__setattr__(forged, "qty", D("NaN"))
    local = DurableState(_account(), 7, OBSERVED - 1_000, (forged,), ())
    with pytest.raises(ReconciliationError, match="validation.local_position.qty"):
        plan_reconciliation(local, _snapshot(), _config())


def test_subclass_cannot_cross_exact_type_seam():
    class DerivedPosition(LocalPosition):
        pass

    with pytest.raises(ReconciliationError, match="validation.local.positions"):
        _state(positions=(DerivedPosition("BTCUSDT", Side.LONG, D("1")),))


def test_returned_plan_is_sealed_against_later_caller_mutation():
    source = _venue_position()
    snapshot = _snapshot(positions=(source,))
    plan = plan_reconciliation(_state(), snapshot, _config())
    before = (plan.reconciliation_id, plan.evidence_sha256, plan.findings, plan.actions)
    object.__setattr__(source, "qty", D("999"))
    assert (plan.reconciliation_id, plan.evidence_sha256, plan.findings, plan.actions) == before


def test_public_module_has_no_io_runtime_or_legacy_imports():
    tree = ast.parse(inspect.getsource(reconciliation_module))
    imports = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            imports.update(alias.name.split(".")[0] for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            imports.add(node.module.split(".")[0])
    assert imports.isdisjoint({"asyncio", "pathlib", "requests", "socket", "subprocess", "threading", "time"})
    assert not any(isinstance(node, ast.Call) and isinstance(node.func, ast.Name) and node.func.id == "open"
                   for node in ast.walk(tree))
