"""Typed management actions and their one deterministic order (protect > close > reduce > add > release).

An action is what the core wants the venue to hold or do; the runner turns it into OrderIntents (NC-01: PLACE / REPLACE
stop = a PROTECT intent, the add = an ADD intent, targets and closes = reduce-only REDUCE / CLOSE intents). Order:

    PROTECT  PLACE_STOP, REPLACE_STOP, CANCEL_ADD     protection first; withdrawing opening risk is protective
    CLOSE    TIME_EXIT, CLOSE                         market close of the remainder
    REDUCE   REDUCE, CANCEL_TARGET, REPLACE_TARGET, PLACE_TARGET
    ADD      PLACE_ADD                                risk-adding work last
    RELEASE  CANCEL_STOP                              only once the position is flat

Within a tier: kind order as listed, then leg order (stop, add, tp1, tp2, close). `ordered` is the only sort.
"""
from __future__ import annotations

import enum
from decimal import Decimal

from ..domain.base import Record, positive, record, req
from ..domain.reasons import ReasonCode
from .state import Leg


class Tier(enum.IntEnum):
    PROTECT = 0
    CLOSE = 1
    REDUCE = 2
    ADD = 3
    RELEASE = 4


class ActionKind(enum.StrEnum):
    PLACE_STOP = 'place_stop'
    REPLACE_STOP = 'replace_stop'
    CANCEL_ADD = 'cancel_add'
    TIME_EXIT = 'time_exit'          # market close of the remainder at the holding-time limit
    CLOSE = 'close'                  # market close of the remainder (stop crossed / stop failed / requested exit)
    REDUCE = 'reduce'                # market close of a PART (flatten an add the stop cannot cover / a late add)
    CANCEL_TARGET = 'cancel_target'
    REPLACE_TARGET = 'replace_target'
    PLACE_TARGET = 'place_target'
    PLACE_ADD = 'place_add'
    CANCEL_STOP = 'cancel_stop'


K = ActionKind
TIER = {K.PLACE_STOP: Tier.PROTECT, K.REPLACE_STOP: Tier.PROTECT, K.CANCEL_ADD: Tier.PROTECT,
        K.TIME_EXIT: Tier.CLOSE, K.CLOSE: Tier.CLOSE,
        K.REDUCE: Tier.REDUCE, K.CANCEL_TARGET: Tier.REDUCE, K.REPLACE_TARGET: Tier.REDUCE, K.PLACE_TARGET: Tier.REDUCE,
        K.PLACE_ADD: Tier.ADD, K.CANCEL_STOP: Tier.RELEASE}
KIND_RANK = {k: i for i, k in enumerate(ActionKind)}
LEG_RANK = {g: i for i, g in enumerate(Leg)}
LEG_OF = {K.PLACE_STOP: (Leg.STOP,), K.REPLACE_STOP: (Leg.STOP,), K.CANCEL_STOP: (Leg.STOP,),
          K.PLACE_ADD: (Leg.ADD,), K.CANCEL_ADD: (Leg.ADD,),
          K.PLACE_TARGET: (Leg.TP1, Leg.TP2), K.REPLACE_TARGET: (Leg.TP1, Leg.TP2), K.CANCEL_TARGET: (Leg.TP1, Leg.TP2),
          K.TIME_EXIT: (Leg.CLOSE,), K.CLOSE: (Leg.CLOSE,), K.REDUCE: (Leg.CLOSE,)}
CANCELS = frozenset({K.CANCEL_ADD, K.CANCEL_TARGET, K.CANCEL_STOP})
MARKET = frozenset({K.TIME_EXIT, K.CLOSE, K.REDUCE})


@record
class ManagementAction(Record):
    kind: ActionKind
    leg: Leg
    qty: Decimal | None          # None for a cancel
    price: Decimal | None        # trigger price; None for a cancel or a market close
    reason: ReasonCode

    def _validate(self, p):
        req(self.leg in LEG_OF[self.kind], p + '.leg', f'{self.kind} does not act on {self.leg}')
        if self.kind in CANCELS:
            req(self.qty is None and self.price is None, p + '.qty', 'a cancel has no qty / price')
            return
        positive(self.qty, p + '.qty')
        if self.kind in MARKET:
            req(self.price is None, p + '.price', 'a market close has no trigger price')
        else:
            positive(self.price, p + '.price')

    @property
    def tier(self):
        return TIER[self.kind]


def sort_key(a):
    return (TIER[a.kind], KIND_RANK[a.kind], LEG_RANK[a.leg])


def ordered(actions):
    return tuple(sorted(actions, key=sort_key))
