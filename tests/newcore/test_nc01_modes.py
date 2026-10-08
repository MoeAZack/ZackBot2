"""NC-01 entries modes, HOLD kinds and the pure permitted-action table (ruling 9, hard-HOLD ruling, contract invariant 9).

Includes the Hypothesis properties the hard-HOLD ruling asks for: hard HOLD never permits a risk-increasing action or a
protective cancel / reprice; no HOLD permits risk; manual entry never bypasses pause or HOLD."""
from itertools import product

import pytest
from hypothesis import given, seed, strategies as st

from newcore.domain import EntriesMode, HoldKind, InvalidRecord, Op, Permission, Purpose, permitted
from newcore.domain.modes import EMERGENCY_SET, is_protective_removal, is_risk_increasing
from newcore.domain.orders import OPENING

MODES = [(m, None) for m in EntriesMode if m is not EntriesMode.HOLD] + [(EntriesMode.HOLD, k) for k in HoldKind]
ALL = list(product(MODES, Purpose, Op, (False, True)))


def ok(mode, hold, purpose, op, one_shot=False):
    return permitted(mode, hold, purpose, op, one_shot=one_shot) is Permission.ALLOWED


def test_hard_hold_is_exactly_the_emergency_set():
    allowed = {(u, o) for u, o in product(Purpose, Op) if ok(EntriesMode.HOLD, HoldKind.DURABILITY_UNAVAILABLE, u, o)}
    assert allowed == EMERGENCY_SET
    assert EMERGENCY_SET == ({(u, Op.QUERY) for u in Purpose} | {(Purpose.PROTECT, Op.PLACE)}
                             | {(u, Op.CANCEL) for u in OPENING} | {(u, Op.ADOPT) for u in OPENING})


@pytest.mark.parametrize('op', [Op.CANCEL, Op.REPRICE, Op.MANAGE])
def test_hard_hold_never_touches_existing_protection(op):
    assert not ok(EntriesMode.HOLD, HoldKind.DURABILITY_UNAVAILABLE, Purpose.PROTECT, op)


@pytest.mark.parametrize('purpose,op', [(Purpose.ENTRY, Op.REPRICE), (Purpose.ENTRY, Op.FALLBACK), (Purpose.ADD, Op.MANAGE),
                                        (Purpose.CLOSE, Op.MANAGE), (Purpose.REDUCE, Op.MANAGE), (Purpose.ENTRY, Op.RESUME)])
def test_hard_hold_forbids_ordinary_management_and_resume(purpose, op):
    assert not ok(EntriesMode.HOLD, HoldKind.DURABILITY_UNAVAILABLE, purpose, op)


def test_normal_hold_keeps_protection_and_allows_risk_reduction():
    h = (EntriesMode.HOLD, HoldKind.NORMAL)
    assert ok(*h, Purpose.PROTECT, Op.PLACE) and not ok(*h, Purpose.PROTECT, Op.CANCEL)
    assert ok(*h, Purpose.CLOSE, Op.PLACE) and ok(*h, Purpose.REDUCE, Op.PLACE)
    assert ok(*h, Purpose.ENTRY, Op.CANCEL) and ok(*h, Purpose.ADD, Op.CANCEL)
    for u, o in product(Purpose, (Op.REPRICE, Op.MANAGE, Op.RESUME)):
        assert not ok(*h, u, o)
    assert not ok(*h, Purpose.ENTRY, Op.PLACE, one_shot=True)


def test_active_allows_everything_meaningful():
    for u, o in product(Purpose, Op):
        expected = not ((o is Op.ADOPT or o is Op.FALLBACK) and u not in OPENING)
        assert ok(EntriesMode.ACTIVE, None, u, o) is expected


@pytest.mark.parametrize('mode', [EntriesMode.PAUSED, EntriesMode.HALTED, EntriesMode.FLATTENING])
def test_drain_modes_drain_and_protect(mode):
    for u in OPENING:
        assert ok(mode, None, u, Op.CANCEL)
        assert not any(ok(mode, None, u, o) for o in (Op.PLACE, Op.REPRICE, Op.FALLBACK, Op.MANAGE))
    for u, o in product((Purpose.PROTECT, Purpose.CLOSE, Purpose.REDUCE), (Op.PLACE, Op.CANCEL, Op.REPRICE, Op.MANAGE)):
        assert ok(mode, None, u, o)
    assert ok(mode, None, Purpose.ENTRY, Op.RESUME)          # explicit resume is how opening risk comes back


def test_one_shot_only_in_paused():
    assert ok(EntriesMode.PAUSED, None, Purpose.ENTRY, Op.PLACE, one_shot=True)
    for m, h in MODES:
        if m not in (EntriesMode.PAUSED, EntriesMode.ACTIVE):
            assert not ok(m, h, Purpose.ENTRY, Op.PLACE, one_shot=True)


def test_hold_kind_is_set_exactly_in_hold():
    with pytest.raises(InvalidRecord):
        permitted(EntriesMode.HOLD, None, Purpose.ENTRY, Op.QUERY)
    with pytest.raises(InvalidRecord):
        permitted(EntriesMode.PAUSED, HoldKind.NORMAL, Purpose.ENTRY, Op.QUERY)


# ----------------------------------------------------------------------------------------------------------- properties
cells = st.tuples(st.sampled_from(MODES), st.sampled_from(list(Purpose)), st.sampled_from(list(Op)), st.booleans())


@seed(2026100801)
@given(cells)
def test_property_hard_hold_never_increases_risk_or_removes_protection(cell):
    (mode, hold), purpose, op, one_shot = cell
    if hold is HoldKind.DURABILITY_UNAVAILABLE and ok(mode, hold, purpose, op, one_shot):
        assert not is_risk_increasing(purpose, op)
        assert not is_protective_removal(purpose, op)
        assert (purpose, op) in EMERGENCY_SET


@seed(2026100802)
@given(cells)
def test_property_no_hold_permits_risk_and_only_paused_one_shot_opens_outside_active(cell):
    (mode, hold), purpose, op, one_shot = cell
    allowed = ok(mode, hold, purpose, op, one_shot)
    if mode is EntriesMode.HOLD:
        assert not (allowed and is_risk_increasing(purpose, op))
    if mode is not EntriesMode.ACTIVE and allowed and is_risk_increasing(purpose, op):
        assert (mode, purpose, op, one_shot) in {(EntriesMode.PAUSED, u, Op.PLACE, True) for u in OPENING} | \
            {(m, u, Op.RESUME, s) for m in (EntriesMode.PAUSED, EntriesMode.HALTED, EntriesMode.FLATTENING)
             for u in Purpose for s in (False, True)}


@seed(2026100803)
@given(cells)
def test_property_hard_hold_is_never_wider_than_normal_hold(cell):
    _, purpose, op, one_shot = cell
    if ok(EntriesMode.HOLD, HoldKind.DURABILITY_UNAVAILABLE, purpose, op, one_shot):
        assert ok(EntriesMode.HOLD, HoldKind.NORMAL, purpose, op, one_shot) or op is Op.ADOPT or op is Op.QUERY or \
            (purpose is Purpose.PROTECT and op is Op.PLACE)


def test_exhaustive_table_is_total_and_pure():
    """Every cell answers ALLOWED or FORBIDDEN, and the same input always gives the same answer."""
    first = {c: permitted(c[0][0], c[0][1], c[1], c[2], one_shot=c[3]) for c in ALL}
    again = {c: permitted(c[0][0], c[0][1], c[1], c[2], one_shot=c[3]) for c in ALL}
    assert first == again and set(first.values()) <= set(Permission)
