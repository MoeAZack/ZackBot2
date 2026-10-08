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


MODES = [(EntriesMode.ACTIVE, None), (EntriesMode.PAUSED, None), (EntriesMode.HALTED, None),
         (EntriesMode.FLATTENING, None), (EntriesMode.HOLD, HoldKind.NORMAL), HARD]


def _allowed_cells(emergency_close):
    return {(mode, hold, purpose, op, one_shot)
            for (mode, hold), purpose, op, one_shot in itertools.product(MODES, Purpose, Op, (False, True))
            if permitted(mode, hold, purpose, op, one_shot=one_shot, emergency_close=emergency_close)
            is Permission.ALLOWED}


def test_exactly_one_cell_is_newly_allowed_across_every_mode_action_and_one_shot():
    """The full permission table (every mode x hold kind x purpose x op x one-shot): the flag adds exactly the
    hard-HOLD (CLOSE, PLACE) cell and removes nothing."""
    without, with_flag = _allowed_cells(False), _allowed_cells(True)
    assert without <= with_flag                                                 # the flag never forbids anything
    assert with_flag - without == {(*HARD, Purpose.CLOSE, Op.PLACE, False), (*HARD, Purpose.CLOSE, Op.PLACE, True)}


def test_hard_hold_with_the_flag_is_the_emergency_set_plus_one_close_place():
    from newcore.domain.modes import EMERGENCY_SET
    allowed = {(purpose, op) for purpose, op in itertools.product(Purpose, Op)
               if permitted(*HARD, purpose, op, emergency_close=True) is Permission.ALLOWED}
    assert allowed == set(EMERGENCY_SET) | {(Purpose.CLOSE, Op.PLACE)}


def test_ordinary_and_management_closes_stay_forbidden_in_hard_hold():
    """Without the flag no close is placed in hard HOLD; with it, only the PLACE of a CLOSE - never a REDUCE, a
    management action, a reprice or a fallback of a close."""
    for purpose, op in itertools.product((Purpose.CLOSE, Purpose.REDUCE), Op):
        if op is Op.QUERY:
            continue
        assert permitted(*HARD, purpose, op) is Permission.FORBIDDEN, (purpose, op)
        expected = Permission.ALLOWED if (purpose, op) == (Purpose.CLOSE, Op.PLACE) else Permission.FORBIDDEN
        assert permitted(*HARD, purpose, op, emergency_close=True) is expected, (purpose, op)
    assert permitted(*HARD, Purpose.ADD, Op.PLACE, emergency_close=True) is Permission.FORBIDDEN
    assert permitted(*HARD, Purpose.ENTRY, Op.PLACE, emergency_close=True) is Permission.FORBIDDEN
