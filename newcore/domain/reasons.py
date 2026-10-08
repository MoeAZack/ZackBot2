"""The versioned machine reason-code registry (contract section 4, ruling 6).

Every value is `<namespace>.<code>`, lowercase. The registry is APPEND-ONLY within a schema version: a value is never
renamed, reused or deleted; a retired value is deprecated (DEPRECATED: code -> replacement or None) and stays reserved and
decodable. The declaration order is pinned by tests/newcore/reason_codes_v1.txt, and every code has a one-line semantic
entry in MEANING. Free text is display only (Decision.detail) and never drives a transition.

Namespaces (the contract's catalog -> namespace):
  ownership .......... ownership.*                  binding ............ binding.*
  lifecycle .......... lifecycle.*, trailing.*      exchange evidence .. evidence.*
  protection ......... protect.*                    risk / capacity .... risk_gateway.*, capacity.*, config.*, side_mask.*
  strategy decision .. entry.*, exit.*, filter.*, regime.*, input.*
  recovery ........... recovery.*                   reconciliation ..... reconcile.*
  operator authority . operator.*                   venue / execution .. connectivity.*, execution.*
  position management  manage.* (r3 draft: the runner's per-lot management tick; a WAIT about one lot)
The gate namespaces keep the legacy trade_audit.reason_info stage/code values 1:1 (tests check the seed; NEWCORE never
classifies text). GOLDEN_EXIT / GOLDEN_SIGNAL map the zb-golden/1 vocabulary explicitly; legacy `UNMAPPED:*` values have
no member and so can never enter a NEWCORE record.
"""
from __future__ import annotations

import enum

REGISTRY_VERSION = 1


class ReasonCode(enum.StrEnum):
    # ---- legacy gate seed (trade_audit _EXACT / _PREFIX / _WRAP, engine.RISK_RULE_DEFAULTS)
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
    FILTER_WARNING = 'filter.warning'
    FILTER_AI_VETO = 'filter.ai_veto'
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
    # ---- exits (strategy / protection outcomes); projection in GOLDEN_EXIT
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
    # ---- opening risk
    ENTRY_SIGNAL = 'entry.signal'
    ENTRY_MANUAL = 'entry.manual'
    ENTRY_DCA_LEVEL = 'entry.dca_level'
    ENTRY_PYRAMID = 'entry.pyramid'
    ENTRY_ONE_SHOT = 'entry.one_shot'
    ENTRY_ADOPTED = 'entry.adopted'
    # ---- ownership / binding / lifecycle / exchange evidence
    OWNERSHIP_UNKNOWN = 'ownership.unknown'
    OWNERSHIP_UNTRACKED_POSITION = 'ownership.untracked_position'
    OWNERSHIP_FOREIGN_ORDER = 'ownership.foreign_order'
    BINDING_UNCONFIRMED = 'binding.unconfirmed'
    BINDING_MISMATCH = 'binding.mismatch'
    BINDING_ROTATION_PENDING = 'binding.rotation_pending'
    BINDING_RECONCILING = 'binding.reconciling'
    LIFECYCLE_DRAIN = 'lifecycle.drain_not_active'
    LIFECYCLE_ORPHAN_CANCEL = 'lifecycle.orphan_cancel'
    LIFECYCLE_NOT_DURABLE = 'lifecycle.not_durable'
    EVIDENCE_NOT_FOUND_UNCORROBORATED = 'evidence.not_found_uncorroborated'
    EVIDENCE_LATE_FILL_NOT_ACTIVE = 'evidence.late_fill_not_active'
    EVIDENCE_PARTIAL_FILL = 'evidence.partial_fill'
    # ---- protection
    PROTECT_PLACE = 'protect.place'
    PROTECT_RESIZE = 'protect.resize'
    PROTECT_REPLACE = 'protect.replace'
    PROTECT_CHECKING = 'protect.checking'
    PROTECT_RESTORING = 'protect.restoring'
    PROTECT_OWNER_CHECK = 'protect.owner_check'
    PROTECT_UNDERSIZED = 'protect.undersized'
    # ---- recovery / reconciliation
    RECOVERY_SCHEMA_FUTURE = 'recovery.schema_future'
    RECOVERY_SCHEMA_INVALID = 'recovery.schema_invalid'
    RECOVERY_STATE_MISSING = 'recovery.state_missing'
    RECOVERY_STATE_UNREADABLE = 'recovery.state_unreadable'
    RECOVERY_GENERATION_ROLLBACK = 'recovery.generation_rollback'
    RECOVERY_DURABILITY_UNAVAILABLE = 'recovery.durability_unavailable'
    RECONCILE_UNRECONCILED = 'reconcile.unreconciled'
    RECONCILE_MATCH = 'reconcile.match'
    RECONCILE_OWNER_RESOLVED = 'reconcile.owner_resolved'
    RECONCILE_FLAT_SNAPSHOT = 'reconcile.flat_snapshot'
    # ---- operator authority
    OPERATOR_PAUSE = 'operator.pause'
    OPERATOR_RESUME = 'operator.resume'
    OPERATOR_HALT = 'operator.halt'
    OPERATOR_FLATTEN = 'operator.flatten'
    OPERATOR_ONE_SHOT = 'operator.one_shot'
    OPERATOR_ADOPT = 'operator.adopt'
    # ---- appended after v1 publication (append-only: new codes go at the end)
    RISK_COST_TO_STOP = 'risk_gateway.cost_to_stop'
    # ---- r3 draft: S4 risk halts and the REC-02 reconcile vocabulary (append-only)
    RISK_DRAWDOWN_KILL = 'risk_gateway.drawdown_kill'
    RISK_DAILY_HALT = 'risk_gateway.daily_halt'
    RECONCILE_FOREIGN_QUARANTINE = 'reconcile.foreign_quarantine'
    RECONCILE_MANUAL_CLOSE = 'reconcile.manual_close'
    RECONCILE_MANUAL_ADD = 'reconcile.manual_add'
    RECONCILE_STALE_READ = 'reconcile.stale_read'
    RECONCILE_LATE_FILL = 'reconcile.late_fill_after_not_found'
    # ---- r3 DRAFT item 5: the runner's durable per-position management step
    MANAGE_TICK = 'manage.tick'

    @property
    def namespace(self):
        return self.value.split('.', 1)[0]

    @property
    def deprecated(self):
        return self in DEPRECATED


R = ReasonCode
MEANING = {
    R.CONNECTIVITY_NOT_CONNECTED: 'no exchange session: nothing can be sent',
    R.CONNECTIVITY_EXCHANGE_OUTAGE: 'the exchange does not answer: no new entries until it does',
    R.INPUT_BAD_DIRECTION: 'the signal named no valid side',
    R.SIDE_MASK_HEDGE_OFF: 'shorts need hedge mode, which is off',
    R.FILTER_HALT: 'the daily-loss halt is active',
    R.FILTER_PAUSED: 'entries are paused',
    R.FILTER_COIN_OFF: 'the coin is switched off',
    R.FILTER_SLOT_OFF: 'the strategy slot is switched off',
    R.FILTER_SLOT_REMOVED: 'the strategy slot was removed or replaced',
    R.FILTER_HOURS: 'outside the slot entry hours',
    R.FILTER_VOLATILITY: 'the volatility filter refused the entry',
    R.FILTER_WARNING: 'DEPRECATED: a warning is an observe event, not a gate',
    R.FILTER_AI_VETO: 'DEPRECATED: NEWCORE has no AI filter',
    R.CAPACITY_IN_TRADE: 'already in a trade on this coin',
    R.CAPACITY_ENTRY_WORKING: 'an entry is already working on this coin',
    R.CAPACITY_LEVERAGE_CAP: 'the slot leverage cap is reached',
    R.CAPACITY_MAX_POSITIONS: 'the maximum number of positions is reached',
    R.CAPACITY_MANUAL_NO_ADDS: 'a manual trade receives no strategy adds',
    R.REGIME_MISMATCH: 'the market regime does not fit the strategy',
    R.REGIME_UNKNOWN: 'the market regime cannot be determined',
    R.RISK_STOP_UNCONFIRMED: 'an open trade on this coin waits for its stop to be confirmed',
    R.RISK_UNTRACKED_POSITION: 'the exchange holds a position this account does not own',
    R.RISK_PUMP_GUARD: 'the pump guard refused the entry',
    R.RISK_NOT_TRADABLE: 'the instrument is not tradable',
    R.RISK_STATE_UNTRUSTED: 'the ownership state could not be made durable / trusted',
    R.RISK_ACCOUNT_UNCONFIRMED: 'the account binding is not confirmed',
    R.RISK_COIN_CAP: 'risk rule: notional on one coin above its cap',
    R.RISK_OPEN_RISK_CAP: 'risk rule: open risk to stops above its cap',
    R.RISK_CORRELATED_CAP: 'risk rule: too many correlated same-direction trades',
    R.RISK_BTC_BREAKER: 'risk rule: the BTC move breaker is active',
    R.RISK_FUNDING_FILTER: 'risk rule: funding is against the side',
    R.CONFIG_DCA_NO_STOP: 'a DCA basket without a hard stop is refused',
    R.CONFIG_DCA_RANGE: 'DCA settings out of range',
    R.EXEC_ENTRY_UNCONFIRMED: 'the entry order outcome is not confirmed',
    R.EXEC_ENTRY_UNFILLED: 'the entry order ended with nothing executed',
    R.EXEC_ENTRY_UNCONFIRMED_WAIT: 'an earlier entry on this coin/side is not confirmed yet',
    R.EXEC_STOP_FAILED: 'the protective stop failed right after entry; the trade was closed',
    R.EXEC_MAKER_UNFILLED: 'the maker entry did not fill (no market fallback)',
    R.EXEC_MAKER_FALLBACK_BLOCKED: 'the maker entry did not fill and the market fallback was blocked',
    R.EXEC_SIZE_MIN: 'the size is below the venue minimum',
    R.EXEC_WRITE_AHEAD_FAILED: 'the intent could not be made durable, so nothing was sent',
    R.EXEC_ORDER_FAILED: 'the venue refused or failed the order',
    R.EXEC_LEVERAGE: 'leverage / margin could not be set',
    R.TRAILING_EXPIRED: 'an armed trailing entry expired without triggering',
    R.EXIT_STOP: 'the protective stop RESTING ON THE EXCHANGE filled',
    R.EXIT_STOP_CROSSED: 'bot market close: the computed protective level was already crossed (never an exchange stop fill)',
    R.EXIT_TIME: 'bot market close at the holding-time limit',
    R.EXIT_SIGNAL: 'bot market close on the strategy exit signal',
    R.EXIT_TAKE_PROFIT: 'bot market close of the whole position at the take-profit level',
    R.EXIT_TP1: 'the first partial take-profit',
    R.EXIT_LADDER: 'a take-profit ladder level',
    R.EXIT_BASKET_TP: 'bot market close of the whole DCA basket at its target',
    R.EXIT_BASKET_TP_PART: 'the partial (runner split) close of a DCA basket at its target',
    R.EXIT_LIQUIDATED: 'the exchange liquidated the position',
    R.EXIT_FLATTEN: 'bot market close on an owner / safety flatten command',
    R.EXIT_RESYNC: 'the position was gone / smaller on the exchange with no recorded cause (booked only from evidence)',
    R.EXIT_STOP_FAILED: 'the protective stop could not be placed after entry, so the position was closed at market',
    R.EXIT_MANUAL: 'the owner closed the position outside the bot',
    R.ENTRY_SIGNAL: 'strategy entry signal',
    R.ENTRY_MANUAL: 'owner-requested entry (never bypasses pause / HOLD)',
    R.ENTRY_DCA_LEVEL: 'a DCA safety level add',
    R.ENTRY_PYRAMID: 'a pyramid add',
    R.ENTRY_ONE_SHOT: 'an entry under an audited operator one-shot authorization',
    R.ENTRY_ADOPTED: 'opening of an explicitly adopted position or late / partial fill',
    R.OWNERSHIP_UNKNOWN: 'ownership is UNKNOWN: nothing is listed as owned until reconciled',
    R.OWNERSHIP_UNTRACKED_POSITION: 'an exchange position no owned record explains',
    R.OWNERSHIP_FOREIGN_ORDER: 'an exchange order this account did not create (never auto-handled)',
    R.BINDING_UNCONFIRMED: 'the account binding waits for the owner typed confirmation',
    R.BINDING_MISMATCH: 'the keys reach a different binding than the confirmed one',
    R.BINDING_ROTATION_PENDING: 'a new binding was proposed for this account and is unconfirmed',
    R.BINDING_RECONCILING: 'a confirmed rotation waits for its reconciliation record',
    R.LIFECYCLE_DRAIN: 'entries are not active: resting / armed opening intents are cancelled',
    R.LIFECYCLE_ORPHAN_CANCEL: 'an owned order no record carries any more is cancelled',
    R.LIFECYCLE_NOT_DURABLE: 'a planned intent never became durable and was abandoned unsent',
    R.EVIDENCE_NOT_FOUND_UNCORROBORATED: 'the order lookup found nothing; that alone proves nothing',
    R.EVIDENCE_LATE_FILL_NOT_ACTIVE: 'an opening order filled after entries stopped being active',
    R.EVIDENCE_PARTIAL_FILL: 'an order ended partially filled',
    R.PROTECT_PLACE: 'place the protective stop',
    R.PROTECT_RESIZE: 'replace the stop to match a changed exposure',
    R.PROTECT_REPLACE: 'replace the stop at a new level (the old one stays until the new one is confirmed)',
    R.PROTECT_CHECKING: 'the owned stop is not listed: re-checking',
    R.PROTECT_RESTORING: 'the owned stop is gone: restoring it',
    R.PROTECT_OWNER_CHECK: 'a foreign order covers the position or the own stop is unreadable',
    R.PROTECT_UNDERSIZED: 'the stop covers less than the exposure',
    R.RECOVERY_SCHEMA_FUTURE: 'stored state has a newer / unknown schema version',
    R.RECOVERY_SCHEMA_INVALID: 'stored state is damaged',
    R.RECOVERY_STATE_MISSING: 'stored state is missing while the account has history',
    R.RECOVERY_STATE_UNREADABLE: 'stored state cannot be read (I/O)',
    R.RECOVERY_GENERATION_ROLLBACK: 'stored state is older than the committed high-water mark',
    R.RECOVERY_DURABILITY_UNAVAILABLE: 'nothing can be made durable: hard HOLD',
    R.RECONCILE_UNRECONCILED: 'no reconciliation record covers the current generation',
    R.RECONCILE_MATCH: 'a fresh exchange snapshot matched the owned state',
    R.RECONCILE_OWNER_RESOLVED: 'the owner resolved every difference item by item',
    R.RECONCILE_FLAT_SNAPSHOT: 'a fresh exchange snapshot under the confirmed binding was flat',
    R.OPERATOR_PAUSE: 'the owner paused entries',
    R.OPERATOR_RESUME: 'the owner resumed entries',
    R.OPERATOR_HALT: 'the owner halted trading',
    R.OPERATOR_FLATTEN: 'the owner ordered a flatten',
    R.OPERATOR_ONE_SHOT: 'the owner authorized exactly one opening intent while paused',
    R.OPERATOR_ADOPT: 'the owner adopted an exchange position / fill as owned',
    R.RISK_COST_TO_STOP: 'risk rule: the round-trip trading cost is too large a share of the distance to the stop',
    R.RISK_DRAWDOWN_KILL: 'risk rule: the account drawdown kill switch stopped all new risk',
    R.RISK_DAILY_HALT: 'risk rule: the daily loss limit halted new risk for the rest of the trading day',
    R.RECONCILE_FOREIGN_QUARANTINE: 'a foreign order / position is quarantined (left untouched) until the owner acts',
    R.RECONCILE_MANUAL_CLOSE: 'an owned position was closed / reduced outside the bot; booked from venue trade evidence',
    R.RECONCILE_MANUAL_ADD: 'the venue holds more than is owned (a manual add); never adopted without the owner',
    R.RECONCILE_STALE_READ: 'a venue read older than the newest recorded result: re-read, never compared',
    R.RECONCILE_LATE_FILL: 'a FINAL record of an owned order supersedes its earlier corroborated not-found',
    R.MANAGE_TICK: 'a durable management step of one open lot (a closed-candle tick or an intra-candle mark)',
}
del R

NAMESPACES = ('connectivity', 'input', 'side_mask', 'filter', 'capacity', 'regime', 'risk_gateway', 'config', 'execution',
              'trailing', 'exit', 'entry', 'ownership', 'binding', 'lifecycle', 'evidence', 'protect', 'recovery', 'reconcile',
              'operator', 'manage')
GATE_NAMESPACES = frozenset({'connectivity', 'input', 'side_mask', 'filter', 'capacity', 'regime', 'risk_gateway', 'config',
                             'execution', 'trailing', 'ownership', 'binding', 'recovery', 'reconcile', 'protect'})

DEPRECATED = {ReasonCode.FILTER_WARNING: None, ReasonCode.FILTER_AI_VETO: None}

# zb-golden/1 exit-code registry in its append-only order, pinned from origin/golden-short-mirrors @ 0ef7402
# (tests/golden/goldenlib/schema.EXIT_CODES in the tree must be a prefix of it).
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
    ReasonCode.EXIT_MANUAL: None,           # the owner closed outside the bot: no zb-golden/1 code
}

# zb-golden/1 signal (decision) kinds -> (reason, position side)
GOLDEN_SIGNAL = {
    'enter_long': (ReasonCode.ENTRY_SIGNAL, 'LONG'),
    'enter_short': (ReasonCode.ENTRY_SIGNAL, 'SHORT'),
    'exit_long': (ReasonCode.EXIT_SIGNAL, 'LONG'),
    'exit_short': (ReasonCode.EXIT_SIGNAL, 'SHORT'),
}


def golden_exit(code):
    """zb-golden/1 exit code for an exit reason (or None). KeyError for a non-exit code: the projection is explicit."""
    return GOLDEN_EXIT[ReasonCode(code)]
