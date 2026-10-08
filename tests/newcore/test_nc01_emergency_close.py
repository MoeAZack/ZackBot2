"""NC-01 permitted(): the one emergency-close cell (Codex ruling on F3 / P1-3). Under HOLD + DURABILITY_UNAVAILABLE the
A24 emergency set allows queries, protective stop placement, draining opening risk and adopting race fills - and,
ONLY with emergency_close=True, (CLOSE, PLACE): a deterministic reduce-only close after protection failed. The flag
changes no other cell of the table."""
import itertools

from newcore.domain import EntriesMode, HoldKind, Op, Purpose
from newcore.domain.modes import Permission, permitted

HARD = (EntriesMode.HOLD, HoldKind.DURABILITY_UNAVAILABLE)


def test_close_place_in_hard_hold_needs_the_emergency_flag():
    assert permitted(*HARD, Purpose.CLOSE, Op.PLACE) is Permission.FORBIDDEN
    assert permitted(*HARD, Purpose.CLOSE, Op.PLACE, emergency_close=True) is Permission.ALLOWED


def test_the_flag_changes_no_other_cell():
    modes = [(EntriesMode.ACTIVE, None), (EntriesMode.PAUSED, None), (EntriesMode.HALTED, None),
             (EntriesMode.FLATTENING, None), (EntriesMode.HOLD, HoldKind.NORMAL), HARD]
    for (mode, hold), purpose, op in itertools.product(modes, Purpose, Op):
        if (mode, hold) == HARD and (purpose, op) == (Purpose.CLOSE, Op.PLACE):
            continue
        assert permitted(mode, hold, purpose, op, emergency_close=True) is permitted(mode, hold, purpose, op), \
            (mode, hold, purpose, op)
