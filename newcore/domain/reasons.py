"""Stable machine reason codes (ruling 6).

Every value is `<stage>.<code>`. The registry is APPEND-ONLY within a schema version: a value is never renamed, reused or
deleted. A retired value is deprecated (DEPRECATED: code -> replacement or None) and stays decodable. The declaration
order is pinned by tests/newcore/reason_codes_v1.txt, so an insertion, removal or rename fails.

Free text is display only: it lives next to a code (Decision.detail) and is never parsed back into a code. The legacy
skip-reason texts (trade_audit._EXACT/_PREFIX/_WRAP, engine.RISK_RULE_DEFAULTS) seeded the gate codes 1:1, so the
values equal legacy `reason_info` stage/code pairs. That mapping is checked by tests only; NEWCORE never classifies text.

GOLDEN_EXIT maps every exit code explicitly onto the zb-golden/1 exit-code registry (GOLDEN_EXIT_CODES, pinned here in
its append-only order, including STOP_CROSSED / RESYNC / STOP_FAILED / BASKET_TP_PART added by AUD-08). None means the
exit has no golden code yet.
"""
from __future__ import annotations

import enum


class ReasonCode(enum.StrEnum):
    # --- gate stages, seeded 1:1 from legacy trade_audit reason_info (connectivity .. trailing)
    CONNECTIVITY_NOT_CONNECTED = 'connectivity.not_connected'
    CONNECTIVITY_EXCHANGE_OUTAGE = 'connectivity.exchange_outage'
    INPUT_BAD_DIRECTION = 'input.bad_direction'
    SIDE_MASK_HEDGE_OFF = 'side_mask.hedge_off'
    FILTER_HALT = 'filter.halt'
    FILTER_PAUSED = 'filter.paused'
    FILTER_COIN_OFF = 'filter.coin_off'
    FILTER_SLOT_OFF = 'filter.slot_off'
    FILTER_SLOT_REMOVED = 'filter.slot_removed'
    FILTER_HOURS = 'filter.hours'
    FILTER_VOLATILITY = 'filter.volatility'
    FILTER_WARNING = 'filter.warning'                       # deprecated: a warning is an observe event, not a gate
    FILTER_AI_VETO = 'filter.ai_veto'                       # deprecated: NEWCORE has no AI filter
    CAPACITY_IN_TRADE = 'capacity.in_trade'
    CAPACITY_ENTRY_WORKING = 'capacity.entry_working'
    CAPACITY_LEVERAGE_CAP = 'capacity.leverage_cap'
    CAPACITY_MAX_POSITIONS = 'capacity.max_positions'
    CAPACITY_MANUAL_NO_ADDS = 'capacity.manual_no_adds'
    REGIME_MISMATCH = 'regime.regime'
    REGIME_UNKNOWN = 'regime.regime_unknown'
    RISK_STOP_UNCONFIRMED = 'risk_gateway.stop_unconfirmed'
    RISK_UNTRACKED_POSITION = 'risk_gateway.untracked_position'
    RISK_PUMP_GUARD = 'risk_gateway.pump_guard'
    RISK_NOT_TRADABLE = 'risk_gateway.not_tradable'
    RISK_STATE_UNTRUSTED = 'risk_gateway.state_untrusted'
    RISK_ACCOUNT_UNCONFIRMED = 'risk_gateway.account_unconfirmed'
    RISK_COIN_CAP = 'risk_gateway.coin_cap'
    RISK_OPEN_RISK_CAP = 'risk_gateway.open_risk_cap'
    RISK_CORRELATED_CAP = 'risk_gateway.correlated_cap'
    RISK_BTC_BREAKER = 'risk_gateway.btc_breaker'
    RISK_FUNDING_FILTER = 'risk_gateway.funding_filter'
    CONFIG_DCA_NO_STOP = 'config.dca_no_stop'
    CONFIG_DCA_RANGE = 'config.dca_range'
    EXEC_ENTRY_UNCONFIRMED = 'execution.entry_unconfirmed'
    EXEC_ENTRY_UNFILLED = 'execution.entry_unfilled'
    EXEC_ENTRY_UNCONFIRMED_WAIT = 'execution.entry_unconfirmed_wait'
    EXEC_STOP_FAILED = 'execution.stop_failed'
    EXEC_MAKER_UNFILLED = 'execution.maker_unfilled'
    EXEC_MAKER_FALLBACK_BLOCKED = 'execution.maker_fallback_blocked'
    EXEC_SIZE_MIN = 'execution.size_min'
    EXEC_WRITE_AHEAD_FAILED = 'execution.write_ahead_failed'
    EXEC_ORDER_FAILED = 'execution.order_failed'
    EXEC_LEVERAGE = 'execution.leverage'
    TRAILING_EXPIRED = 'trailing.expired'
    # --- exits (legacy exit `why`); golden projection in GOLDEN_EXIT
    EXIT_STOP = 'exit.stop'
    EXIT_STOP_CROSSED = 'exit.stop_crossed'
    EXIT_TIME = 'exit.time_exit'
    EXIT_SIGNAL = 'exit.exit_signal'
    EXIT_TAKE_PROFIT = 'exit.take_profit'
    EXIT_TP1 = 'exit.take_profit_1'
    EXIT_LADDER = 'exit.take_profit_ladder'
    EXIT_BASKET_TP = 'exit.basket_tp'
    EXIT_BASKET_TP_PART = 'exit.basket_tp_part'
    EXIT_LIQUIDATED = 'exit.liquidated'
    EXIT_FLATTEN = 'exit.flatten'
    EXIT_RESYNC = 'exit.resync'
    EXIT_STOP_FAILED = 'exit.stop_failed'
    EXIT_MANUAL = 'exit.manual'
    # --- opening risk (ENTRY / ADD intents and opening fills)
    ENTRY_SIGNAL = 'entry.signal'
    ENTRY_MANUAL = 'entry.manual'
    ENTRY_DCA_LEVEL = 'entry.dca_level'
    ENTRY_PYRAMID = 'entry.pyramid'
    ENTRY_ONE_SHOT = 'entry.one_shot'
    ENTRY_ADOPTED = 'entry.adopted'                         # opening fill of an explicitly adopted position / late fill
    # --- protection lifecycle (legacy STOP_MISS_ORDER + placement)
    PROTECT_PLACE = 'protect.place'
    PROTECT_RESIZE = 'protect.resize'
    PROTECT_CHECKING = 'protect.checking'
    PROTECT_RESTORING = 'protect.restoring'
    PROTECT_OWNER_CHECK = 'protect.owner_check'
    # --- recovery / store (AUD-05 incidents, NC-02)
    RECOVERY_SCHEMA_FUTURE = 'recovery.schema_future'
    RECOVERY_SCHEMA_INVALID = 'recovery.schema_invalid'
    RECOVERY_STATE_MISSING = 'recovery.state_missing'
    RECOVERY_STATE_UNREADABLE = 'recovery.state_unreadable'
    RECOVERY_GENERATION_ROLLBACK = 'recovery.generation_rollback'
    RECOVERY_UNRECONCILED = 'recovery.unreconciled'
    RECOVERY_DURABILITY_UNAVAILABLE = 'recovery.durability_unavailable'
    # --- order outcomes (Audit2)
    ORDER_NOT_FOUND_UNCORROBORATED = 'order.not_found_uncorroborated'
    ORDER_LATE_FILL_NOT_ACTIVE = 'order.late_fill_not_active'
    # --- operator commands
    OPERATOR_PAUSE = 'operator.pause'
    OPERATOR_RESUME = 'operator.resume'
    OPERATOR_HALT = 'operator.halt'
    OPERATOR_FLATTEN = 'operator.flatten'
    OPERATOR_ONE_SHOT = 'operator.one_shot'
    OPERATOR_ADOPT = 'operator.adopt'
    # --- account identity (ruling 5)
    ACCOUNT_BINDING_UNCONFIRMED = 'account.binding_unconfirmed'
    ACCOUNT_ROTATION_PENDING = 'account.rotation_pending'
    ACCOUNT_RECONCILING = 'account.reconciling'
    ACCOUNT_BINDING_MISMATCH = 'account.binding_mismatch'
    # --- drain (ruling 9)
    DRAIN_ENTRIES_NOT_ACTIVE = 'drain.entries_not_active'

    @property
    def stage(self):
        return self.value.split('.', 1)[0]

    @property
    def deprecated(self):
        return self in DEPRECATED


STAGES = ('connectivity', 'input', 'side_mask', 'filter', 'capacity', 'regime', 'risk_gateway', 'config', 'execution',
          'trailing', 'exit', 'entry', 'protect', 'recovery', 'order', 'operator', 'account', 'drain')
GATE_STAGES = frozenset({'connectivity', 'input', 'side_mask', 'filter', 'capacity', 'regime', 'risk_gateway', 'config',
                         'execution', 'trailing', 'recovery', 'account', 'protect'})

DEPRECATED = {ReasonCode.FILTER_WARNING: None, ReasonCode.FILTER_AI_VETO: None}

# zb-golden/1 exit-code registry, append-only order (tests/golden/goldenlib/schema.EXIT_CODES must be a prefix of it)
GOLDEN_EXIT_CODES = ('STOP_HIT', 'TIME_EXIT', 'SIGNAL_EXIT', 'TP_FULL', 'TP_BASKET', 'TP_PARTIAL', 'TP_LADDER', 'LIQUIDATED',
                     'FLATTEN', 'STOP_CROSSED', 'RESYNC', 'STOP_FAILED', 'BASKET_TP_PART')

GOLDEN_EXIT = {
    ReasonCode.EXIT_STOP: 'STOP_HIT',
    ReasonCode.EXIT_STOP_CROSSED: 'STOP_CROSSED',
    ReasonCode.EXIT_TIME: 'TIME_EXIT',
    ReasonCode.EXIT_SIGNAL: 'SIGNAL_EXIT',
    ReasonCode.EXIT_TAKE_PROFIT: 'TP_FULL',
    ReasonCode.EXIT_TP1: 'TP_PARTIAL',
    ReasonCode.EXIT_LADDER: 'TP_LADDER',
    ReasonCode.EXIT_BASKET_TP: 'TP_BASKET',
    ReasonCode.EXIT_BASKET_TP_PART: 'BASKET_TP_PART',
    ReasonCode.EXIT_LIQUIDATED: 'LIQUIDATED',
    ReasonCode.EXIT_FLATTEN: 'FLATTEN',
    ReasonCode.EXIT_RESYNC: 'RESYNC',
    ReasonCode.EXIT_STOP_FAILED: 'STOP_FAILED',
    ReasonCode.EXIT_MANUAL: None,           # owner closed on the exchange; no golden code in zb-golden/1
}


def golden_exit(code):
    """zb-golden/1 exit code for an exit reason (or None). KeyError for a non-exit code: the projection is explicit."""
    return GOLDEN_EXIT[ReasonCode(code)]
