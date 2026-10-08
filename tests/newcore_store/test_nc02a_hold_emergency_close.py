"""NC-02a hold contract: hard_hold_permits keeps the A24 emergency set and passes ONE narrow exception through - the
deterministic reduce-only emergency close after protection failed (Codex ruling, F3 / P1-3)."""
from newcore.domain import Op, Purpose
from newcore.domain.modes import Permission
from newcore.store.hold import hard_hold_permits


def test_an_ordinary_close_or_management_stays_forbidden_in_hard_hold():
    assert hard_hold_permits(Purpose.CLOSE, Op.PLACE) is Permission.FORBIDDEN
    assert hard_hold_permits(Purpose.CLOSE, Op.MANAGE) is Permission.FORBIDDEN
    assert hard_hold_permits(Purpose.REDUCE, Op.PLACE) is Permission.FORBIDDEN


def test_the_emergency_close_is_the_one_exception():
    assert hard_hold_permits(Purpose.CLOSE, Op.PLACE, emergency_close=True) is Permission.ALLOWED
    for purpose, op in ((Purpose.REDUCE, Op.PLACE), (Purpose.CLOSE, Op.MANAGE), (Purpose.ENTRY, Op.PLACE),
                        (Purpose.ADD, Op.PLACE), (Purpose.PROTECT, Op.CANCEL)):
        assert hard_hold_permits(purpose, op, emergency_close=True) is hard_hold_permits(purpose, op)
    assert hard_hold_permits(Purpose.PROTECT, Op.PLACE) is Permission.ALLOWED
