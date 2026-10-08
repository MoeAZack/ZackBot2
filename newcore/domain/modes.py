"""Entries modes, HOLD kinds and the pure permitted-action table (rulings 9 and the hard-HOLD ruling).

ACTIVE is the only mode that opens risk on its own. Every other mode DRAINS: resting maker and armed trailing intents
must be cancelling (Portfolio invariant), manual entries get no exception, and opening risk needs an explicit resume or
an audited one-shot authorization (PAUSED only). Protect, close and reduce stay permitted by priority.

HOLD is the reconciliation quarantine (NC-02); only a reconciliation record leaves it, never a resume. It has two kinds:

- NORMAL: no new risk; protective orders are kept (never cancelled); risk-reducing closes are allowed; no ordinary
  strategy management until reconciled.
- DURABILITY_UNAVAILABLE ("hard HOLD"): the store cannot make anything durable, so only the EMERGENCY set is allowed:
  1. a narrow query for owned protective and risk-adding intents,
  2. place / confirm deterministic-cid reduce-only protection BEFORE any old protection would be removed,
  3. cancel or drain resting ENTRY / ADD intents (idempotent),
  4. adopt a race or partial fill (then protect it without adding exposure, staying in HOLD),
  5. an external best-effort incident (not an order operation; not modelled here).
  It never cancels or reprices protection, never reprices or falls back an entry, never sends ordinary management and
  never resumes risk.
"""
from __future__ import annotations

import enum

from .base import req
from .orders import OPENING, Purpose


class EntriesMode(enum.StrEnum):
    ACTIVE = 'active'
    PAUSED = 'paused'
    HALTED = 'halted'            # daily-loss / breaker halt
    FLATTENING = 'flattening'
    HOLD = 'hold'                # reconciliation quarantine (see HoldKind)


class HoldKind(enum.StrEnum):
    NORMAL = 'normal'
    DURABILITY_UNAVAILABLE = 'durability_unavailable'


class Op(enum.StrEnum):
    """What the shell wants to do with an order of a given purpose."""
    QUERY = 'query'              # read owned orders / positions
    PLACE = 'place'              # send a new order
    CANCEL = 'cancel'            # cancel a resting order
    REPRICE = 'reprice'          # move a resting order (maker reprice, stop trail) = cancel + place
    FALLBACK = 'fallback'        # maker entry falls back to market
    MANAGE = 'manage'            # ordinary strategy management (TP / time / signal / trail decisions)
    ADOPT = 'adopt'              # take ownership of a race / partial fill of an opening intent
    RESUME = 'resume'            # re-open entries


class Permission(enum.StrEnum):
    ALLOWED = 'allowed'
    FORBIDDEN = 'forbidden'


EMERGENCY_SET = frozenset(
    {(u, Op.QUERY) for u in Purpose}
    | {(Purpose.PROTECT, Op.PLACE)}
    | {(u, Op.CANCEL) for u in OPENING}
    | {(u, Op.ADOPT) for u in OPENING})

RISK_INCREASING_OPS = frozenset({Op.PLACE, Op.REPRICE, Op.FALLBACK, Op.MANAGE})


def is_risk_increasing(purpose, op):
    return op is Op.RESUME or (purpose in OPENING and op in RISK_INCREASING_OPS)


def is_protective_removal(purpose, op):
    return purpose is Purpose.PROTECT and op in (Op.CANCEL, Op.REPRICE)


def permitted(mode, hold_kind, purpose, op, *, one_shot=False):
    """Pure table: may the shell perform `op` on an order of `purpose` while the portfolio is in `mode` / `hold_kind`?
    `one_shot` = the opening intent carries an audited operator one-shot authorization."""
    mode, purpose, op = EntriesMode(mode), Purpose(purpose), Op(op)
    req((mode is EntriesMode.HOLD) == (hold_kind is not None), 'permitted.hold_kind', 'set exactly in HOLD')
    allowed = _permitted(mode, None if hold_kind is None else HoldKind(hold_kind), purpose, op, one_shot)
    return Permission.ALLOWED if allowed else Permission.FORBIDDEN


def _permitted(mode, hold, purpose, op, one_shot):
    opening = purpose in OPENING
    if op is Op.QUERY:
        return True
    if op is Op.ADOPT:
        return opening                       # late / race fills of opening risk are always adopted, in every mode
    if op is Op.FALLBACK and not opening:
        return False                         # only a maker entry falls back
    if hold is HoldKind.DURABILITY_UNAVAILABLE:
        return (purpose, op) in EMERGENCY_SET
    if mode is EntriesMode.ACTIVE:
        return True
    if op is Op.RESUME:
        return mode is not EntriesMode.HOLD  # only reconciliation leaves HOLD
    if opening:
        if op is Op.CANCEL:
            return True
        return op is Op.PLACE and one_shot and mode is EntriesMode.PAUSED
    if mode is EntriesMode.HOLD:             # NORMAL hold: keep protection, allow risk-reducing closes, no management
        if purpose is Purpose.PROTECT:
            return op is Op.PLACE
        return op in (Op.PLACE, Op.CANCEL)
    return True                              # PAUSED / HALTED / FLATTENING: protect, close and reduce by priority
