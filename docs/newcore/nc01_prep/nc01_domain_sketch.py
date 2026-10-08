"""NC-01 domain sketch (PRE-STAGE, scratch only - not product code).

Stdlib only. Frozen, slotted, keyword-only dataclasses; every invariant is checked in __post_init__ so an invalid record
cannot be constructed (and therefore cannot be decoded either: from_dict goes through the constructor).
Quantities / prices are Decimal (exact, JSON as decimal strings - the golden pack's spelling).
"""
from __future__ import annotations

import enum
import types
import re
import typing
import uuid
from functools import cache
from dataclasses import dataclass, fields, is_dataclass
from decimal import Decimal
from typing import Optional

SCHEMA_VERSION = 1


# ----------------------------------------------------------------------------------------------------------- errors
class DomainError(ValueError):
    """Any rejected record. .path names the field (e.g. 'portfolio.positions[0].lots[1].qty')."""
    def __init__(self, path, msg):
        super().__init__(f'{path}: {msg}'); self.path = path


class InvalidRecord(DomainError):
    """Current-schema damage: wrong type, unknown/missing key, broken invariant. NC-02 preserves evidence + fails closed."""


class FutureSchema(DomainError):
    """schema_version newer than this build. Raised BEFORE any other check; NC-02 must abort WITHOUT touching any file."""


def _req(cond, path, msg):
    if not cond:
        raise InvalidRecord(path, msg)


def _dec(v, path, *, gt=None, ge=None):
    _req(isinstance(v, Decimal) and v.is_finite(), path, f'not a finite Decimal ({type(v).__name__})')
    if gt is not None: _req(v > gt, path, f'must be > {gt}')
    if ge is not None: _req(v >= ge, path, f'must be >= {ge}')


def _on_grid(v, step, path):
    _req(step > 0 and v % step == 0, path, f'{v} is not a multiple of {step}')


# ----------------------------------------------------------------------------------------------------------- enums
class Side(enum.StrEnum):
    LONG = 'LONG'; SHORT = 'SHORT'

    @property
    def sign(self): return 1 if self is Side.LONG else -1


class Environment(enum.StrEnum):
    BACKTEST = 'backtest'; SIM = 'sim'; TESTNET = 'testnet'; MAINNET = 'mainnet'


class EntriesMode(enum.StrEnum):
    ACTIVE = 'active'; PAUSED = 'paused'; HALTED = 'halted'; FLATTENING = 'flattening'


class Ownership(enum.StrEnum):
    KNOWN = 'known'          # the store proved what this account owns (fresh install or a valid generation)
    UNKNOWN = 'unknown'      # failed closed: NOT the same as empty - lots / intents are None, never {}


class LotSource(enum.StrEnum):
    STRATEGY = 'strategy'; MANUAL = 'manual'; ADOPTED = 'adopted'   # adopted = taken over from an exchange position


class Purpose(enum.StrEnum):
    ENTRY = 'entry'; ADD = 'add'; CLOSE = 'close'; REDUCE = 'reduce'; STOP = 'stop'; CANCEL = 'cancel'


class OrderType(enum.StrEnum):
    MARKET = 'market'; LIMIT_POST_ONLY = 'limit_gtx'; STOP_MARKET = 'stop_market'


class ResultPhase(enum.StrEnum):
    UNKNOWN = 'unknown'      # sent, answer lost / not readable yet (AUD-03 'lost answer')
    KNOWN = 'known'          # the exchange has it, not final (NEW / PARTIALLY_FILLED): executed qty is NOT trusted
    FINAL = 'final'          # executed qty decided


class Evidence(enum.StrEnum):
    EXCHANGE_FINAL = 'exchange_final'               # final order record (FILLED / EXPIRED / CANCELED / REJECTED ...)
    EXCHANGE_REFUSED = 'exchange_refused'           # synchronous rejection: nothing executed
    NOT_FOUND_CORROBORATED = 'not_found_corroborated'   # not found AND position unchanged across >= 2 reads past the window
    POSITION_ADOPTED = 'position_adopted'           # no record in the evidence window; size adopted at mark (flagged)


class ProtectionKind(enum.StrEnum):
    STOP = 'stop'; TARGET = 'target'


class ProtectionStatus(enum.StrEnum):
    NEEDS_PLACEMENT = 'needs_placement'     # legacy stop_dirty / no stop_id
    PLACEMENT_PENDING = 'placement_pending' # legacy prov_pending: answer lost, owned by client ids (classic + algo)
    OWNED_UNVERIFIED = 'owned_unverified'   # accepted (id known), not listed yet (STOP_LIST_GRACE_S)
    OWNED_CONFIRMED = 'owned_confirmed'     # listed / status NEW at confirmed_at
    MISSING_CHECKING = 'missing_checking'   # legacy stop_miss_why='checking'
    MISSING_RESTORING = 'missing_restoring' # legacy 'restoring'
    OWNER_CHECK = 'owner_check'             # legacy 'owner_check': foreign stop covers it / own stop unreadable


ENTRY_BLOCKING = frozenset(ProtectionStatus) - {ProtectionStatus.OWNED_CONFIRMED, ProtectionStatus.OWNED_UNVERIFIED}


class Action(enum.StrEnum):
    # priority order = plan rule 7: protect > close > reduce > reconcile > add > enter > (hold / skip)
    PROTECT = 'protect'; CLOSE = 'close'; REDUCE = 'reduce'; RECONCILE = 'reconcile'; ADD = 'add'; ENTER = 'enter'
    PAUSE = 'pause'; HALT = 'halt'; FLATTEN = 'flatten'; CANCEL_ENTRY = 'cancel_entry'; HOLD = 'hold'; SKIP = 'skip'


PRIORITY = {a: i for i, a in enumerate((Action.PROTECT, Action.FLATTEN, Action.CLOSE, Action.REDUCE, Action.HALT,
                                        Action.PAUSE, Action.CANCEL_ENTRY, Action.RECONCILE, Action.ADD, Action.ENTER,
                                        Action.HOLD, Action.SKIP))}


class ReasonCode(enum.StrEnum):
    """Value = '<stage>.<code>'. Append-only: a value is never renamed or reused (deprecate instead)."""
    # gate (not taken) - seeded from trade_audit._EXACT / _PREFIX / _WRAP
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
    CAPACITY_IN_TRADE = 'capacity.in_trade'
    CAPACITY_ENTRY_WORKING = 'capacity.entry_working'
    CAPACITY_LEVERAGE_CAP = 'capacity.leverage_cap'
    CAPACITY_MAX_POSITIONS = 'capacity.max_positions'
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
    # exits - legacy exit_reason / lot fills 'why'; golden projection in GOLDEN_EXIT
    EXIT_STOP = 'exit.stop'
    EXIT_STOP_CROSSED = 'exit.stop_crossed'
    EXIT_TIME = 'exit.time_exit'
    EXIT_SIGNAL = 'exit.exit_signal'
    EXIT_TARGET = 'exit.take_profit'
    EXIT_TP1 = 'exit.take_profit_1'
    EXIT_LADDER = 'exit.take_profit_ladder'
    EXIT_BASKET_TP = 'exit.basket_tp'
    EXIT_BASKET_TP_PART = 'exit.basket_tp_part'
    EXIT_LIQUIDATED = 'exit.liquidated'
    EXIT_FLATTEN = 'exit.flatten'
    EXIT_RESYNC = 'exit.resync'
    EXIT_STOP_FAILED = 'exit.stop_failed'
    EXIT_MANUAL = 'exit.manual'
    # protection / recovery / order outcome (AUD-03/04/05 + Audit2)
    PROTECT_PLACE = 'protect.place'
    PROTECT_CHECKING = 'protect.checking'
    PROTECT_RESTORING = 'protect.restoring'
    PROTECT_OWNER_CHECK = 'protect.owner_check'
    RECOVERY_SCHEMA_FUTURE = 'recovery.schema_future'
    RECOVERY_SCHEMA_INVALID = 'recovery.schema_invalid'
    RECOVERY_STATE_MISSING = 'recovery.state_missing_initialized'
    RECOVERY_RESTORED_BACKUP = 'recovery.restored_from_backup'
    RECOVERY_ACCOUNT_MISMATCH = 'recovery.account_mismatch'
    ORDER_NOT_FOUND_UNCORROBORATED = 'order.not_found_uncorroborated'
    ORDER_LATE_FILL_NOT_ACTIVE = 'order.late_fill_not_active'
    OPERATOR_PAUSE = 'operator.pause'
    OPERATOR_FLATTEN = 'operator.flatten'

    @property
    def stage(self): return self.value.split('.', 1)[0]


GATE_STAGES = frozenset({'connectivity', 'input', 'side_mask', 'filter', 'capacity', 'regime', 'risk_gateway', 'config',
                         'execution', 'trailing', 'recovery', 'protect'})
GOLDEN_EXIT = {   # NC reason -> zb-golden/1 EXIT_CODES (projection only; None = not expressible in golden v1 yet)
    ReasonCode.EXIT_STOP: 'STOP_HIT', ReasonCode.EXIT_STOP_CROSSED: 'STOP_HIT', ReasonCode.EXIT_TIME: 'TIME_EXIT',
    ReasonCode.EXIT_SIGNAL: 'SIGNAL_EXIT', ReasonCode.EXIT_TARGET: 'TP_FULL', ReasonCode.EXIT_BASKET_TP: 'TP_BASKET',
    ReasonCode.EXIT_TP1: 'TP_PARTIAL', ReasonCode.EXIT_BASKET_TP_PART: 'TP_PARTIAL', ReasonCode.EXIT_LADDER: 'TP_LADDER',
    ReasonCode.EXIT_LIQUIDATED: 'LIQUIDATED', ReasonCode.EXIT_FLATTEN: 'FLATTEN', ReasonCode.EXIT_RESYNC: None,
    ReasonCode.EXIT_STOP_FAILED: None, ReasonCode.EXIT_MANUAL: None}

# ----------------------------------------------------------------------------------------------------------- ids
SYM_RE = re.compile(r'[A-Z0-9]{2,30}')
CID_RE = re.compile(r'z[a-z][0-9a-f]{22}')          # new_cid(prefix) shape; any other client id is never ours
ID_RE = re.compile(r'[a-z]{1,4}_[0-9a-f]{32}')


def new_id(prefix):            # uuid4, never time-derived (legacy lot keys collide within one second)
    return f'{prefix}_{uuid.uuid4().hex}'


def _id(v, path, prefix):
    _req(isinstance(v, str) and ID_RE.fullmatch(v) and v.startswith(prefix + '_'), path, f'not a {prefix}_<32 hex> id')


R = dict(frozen=True, slots=True, kw_only=True)


# ----------------------------------------------------------------------------------------------------------- records
@dataclass(**R)
class AccountBinding:
    """Non-secret evidence of WHICH exchange account the keys reach (legacy install.json account)."""
    venue: str                       # 'binance-usdm'
    environment: Environment
    base_url: str
    key_digest: str                  # 16-hex PBKDF2 digest (legacy account_fingerprint)
    exchange_uid: Optional[str] = None

    def __post_init__(self):
        _req(self.venue and isinstance(self.venue, str), 'binding.venue', 'empty')
        _req(isinstance(self.environment, Environment), 'binding.environment', 'not an Environment')
        _req(re.fullmatch(r'[0-9a-f]{16}', self.key_digest or '') is not None, 'binding.key_digest', 'not 16 hex')


@dataclass(**R)
class Account:
    account_id: str                  # acct_<uuid>: stable, owner-assigned; NOT derived from the key
    label: str
    binding: AccountBinding
    hedge_mode: bool

    def __post_init__(self):
        _id(self.account_id, 'account.account_id', 'acct')
        _req(isinstance(self.binding, AccountBinding), 'account.binding', 'not an AccountBinding')


@dataclass(**R)
class Protection:
    owner_id: str                    # lot_ or ent_ (provisional stop of an unconfirmed entry)
    kind: ProtectionKind
    status: ProtectionStatus
    price: Decimal
    qty: Decimal
    tag: Optional[str] = None        # exchange tag of the OWNED order ('c:'/'ac:'/'o:'/'a:' + id)
    alt_tag: Optional[str] = None    # algo fallback client id while PLACEMENT_PENDING
    foreign_tag: Optional[str] = None
    placed_at: Optional[float] = None
    confirmed_at: Optional[float] = None
    miss_count: int = 0

    def __post_init__(self):
        p = f'protection[{self.owner_id}]'
        _dec(self.price, p + '.price', gt=0); _dec(self.qty, p + '.qty', gt=0)
        _req(isinstance(self.miss_count, int) and not isinstance(self.miss_count, bool) and self.miss_count >= 0, p + '.miss_count', '>= 0')
        S = ProtectionStatus
        if self.status in (S.OWNED_UNVERIFIED, S.OWNED_CONFIRMED, S.MISSING_CHECKING, S.MISSING_RESTORING):
            _req(bool(self.tag), p + '.tag', f'{self.status} needs the owned order tag')
        if self.status is S.PLACEMENT_PENDING:
            _req(bool(self.tag) and self.tag.startswith('c:'), p + '.tag', 'pending placement is owned by its client id (c:)')
        if self.status is S.OWNER_CHECK:
            _req(bool(self.tag) or bool(self.foreign_tag), p, 'owner_check names the own or the foreign order')
        if self.foreign_tag is not None:
            _req(self.status is S.OWNER_CHECK, p + '.foreign_tag', 'only in owner_check')
            cid = self.foreign_tag.split(':', 1)[-1]
            _req(not CID_RE.fullmatch(cid), p + '.foreign_tag', 'a bot client id is never foreign')
        _req((self.confirmed_at is not None) == (self.status is S.OWNED_CONFIRMED), p + '.confirmed_at',
             'set exactly when OWNED_CONFIRMED')
        _req((self.miss_count > 0) <= (self.status in (S.MISSING_CHECKING, S.MISSING_RESTORING, S.OWNER_CHECK)), p + '.miss_count',
             'misses only in a missing / owner_check state')

    @property
    def blocks_entries(self): return self.status in ENTRY_BLOCKING


@dataclass(**R)
class OrderIntent:
    intent_id: str
    account_id: str
    client_order_id: str             # REQUIRED: a qty-only / id-less in-flight order is unrepresentable
    purpose: Purpose
    symbol: str
    side: Side                       # position side
    order_type: OrderType
    qty: Decimal
    reduce_only: bool
    reason: ReasonCode
    owner_id: str                    # lot_ / ent_ the result belongs to
    created_at: float
    price: Optional[Decimal] = None
    stop_price: Optional[Decimal] = None
    alt_client_order_id: Optional[str] = None   # algo-stop fallback id, owned from the WAL on

    def __post_init__(self):
        p = f'intent[{self.intent_id}]'
        _id(self.intent_id, p + '.intent_id', 'int'); _id(self.account_id, p + '.account_id', 'acct')
        _req(isinstance(self.client_order_id, str) and CID_RE.fullmatch(self.client_order_id), p + '.client_order_id',
             'missing or not a bot client id')
        _req(SYM_RE.fullmatch(self.symbol or '') is not None, p + '.symbol', 'bad symbol')
        _dec(self.qty, p + '.qty', gt=0)
        closing = self.purpose in (Purpose.CLOSE, Purpose.REDUCE, Purpose.STOP)
        _req(self.reduce_only == closing, p + '.reduce_only', f'{self.purpose} must{"" if closing else " not"} be reduce-only')
        if self.order_type is OrderType.LIMIT_POST_ONLY: _dec(self.price, p + '.price', gt=0)
        else: _req(self.price is None, p + '.price', 'only a limit order has a price')
        if self.order_type is OrderType.STOP_MARKET: _dec(self.stop_price, p + '.stop_price', gt=0)
        else: _req(self.stop_price is None, p + '.stop_price', 'only a stop has a stop price')
        _req((self.purpose is Purpose.STOP) == (self.order_type is OrderType.STOP_MARKET), p + '.order_type', 'stop <-> stop_market')
        _req(self.purpose is not Purpose.ENTRY or self.owner_id.startswith('ent_'), p + '.owner_id', 'an entry belongs to an EntryIntent')
        _req(self.purpose not in (Purpose.ADD, Purpose.CLOSE, Purpose.REDUCE) or self.owner_id.startswith('lot_'), p + '.owner_id',
             'add / close / reduce belong to a lot')
        if self.reason.stage == 'exit': _req(self.purpose in (Purpose.CLOSE, Purpose.REDUCE), p + '.reason', 'exit reason on a non-close')


@dataclass(**R)
class OrderResult:
    intent_id: str
    client_order_id: str
    phase: ResultPhase
    requested_qty: Decimal
    executed_qty: Optional[Decimal] = None   # None unless FINAL (a non-final executedQty is never booked)
    avg_price: Optional[Decimal] = None
    exchange_status: Optional[str] = None
    evidence: Optional[Evidence] = None
    observed_at: float = 0.0

    def __post_init__(self):
        p = f'result[{self.intent_id}]'
        _dec(self.requested_qty, p + '.requested_qty', gt=0)
        if self.phase is ResultPhase.FINAL:
            _req(self.evidence is not None, p + '.evidence', 'a final result names its evidence')
            _dec(self.executed_qty, p + '.executed_qty', ge=0)
            _req(self.executed_qty <= self.requested_qty, p + '.executed_qty', 'more than requested')
            if self.executed_qty > 0:
                _dec(self.avg_price, p + '.avg_price', gt=0)
                _req(self.evidence in (Evidence.EXCHANGE_FINAL, Evidence.POSITION_ADOPTED), p + '.evidence',
                     'only a final record or an adoption can book an execution')
            else:
                _req(self.evidence in (Evidence.EXCHANGE_FINAL, Evidence.EXCHANGE_REFUSED, Evidence.NOT_FOUND_CORROBORATED),
                     p + '.evidence', 'nothing executed needs a final record, a refusal or a CORROBORATED not-found')
        else:
            _req(self.executed_qty is None and self.avg_price is None and self.evidence is None, p,
                 f'{self.phase} result carries no executed qty / price / evidence')


@dataclass(**R)
class Fill:
    at: str                          # ISO UTC
    reason: ReasonCode
    qty: Decimal
    price: Decimal
    fee: Decimal
    intent_id: Optional[str] = None  # None only for an exchange-side stop fill booked by reconcile

    def __post_init__(self):
        _dec(self.qty, 'fill.qty', gt=0); _dec(self.price, 'fill.price', gt=0); _dec(self.fee, 'fill.fee')


@dataclass(**R)
class Lot:
    lot_id: str
    account_id: str
    symbol: str
    side: Side
    source: LotSource
    slot_id: Optional[str]           # strategy slot; None for manual / adopted
    strategy_key: Optional[str]
    tf: str
    opened_at: str
    qty: Decimal
    avg_price: Decimal
    entry_price: Decimal             # legacy entry0 (first fill)
    anchor_price: Decimal            # legacy e0 (moves on basket_tp_part)
    initial_qty: Decimal             # legacy q0
    max_qty: Decimal                 # legacy qty_max
    risk_distance: Decimal           # legacy R (price distance)
    risk_usd: Decimal
    stop: Protection
    target: Optional[Protection] = None
    in_flight: Optional[str] = None  # intent_id of THE one unresolved order of this lot (legacy `pending`)
    tp1_done: bool = False
    ladder_done: tuple = ()
    adds_done: int = 0
    dca_filled: int = 0
    realized: Decimal = Decimal(0)
    fees: Decimal = Decimal(0)
    fills: tuple = ()
    restored_unverified: bool = False   # legacy restored_from_bak / restored_mismatch
    add_cooldown_until: Optional[float] = None

    def __post_init__(self):
        p = f'lot[{self.lot_id}]'
        _id(self.lot_id, p + '.lot_id', 'lot'); _id(self.account_id, p + '.account_id', 'acct')
        _req(SYM_RE.fullmatch(self.symbol or '') is not None, p + '.symbol', 'bad symbol')
        _req(isinstance(self.side, Side), p + '.side', 'not a Side')
        _req((self.source is LotSource.STRATEGY) == (self.slot_id is not None), p + '.slot_id', 'set exactly for a strategy lot')
        _dec(self.qty, p + '.qty', gt=0)            # a zero-qty lot is finished, never stored (no ghost lot)
        for f in ('avg_price', 'entry_price', 'anchor_price', 'initial_qty', 'max_qty', 'risk_distance'):
            _dec(getattr(self, f), f'{p}.{f}', gt=0)
        _dec(self.risk_usd, p + '.risk_usd', ge=0)
        _req(self.qty <= self.max_qty and self.initial_qty <= self.max_qty, p + '.max_qty', 'below qty / initial_qty')
        _req(isinstance(self.stop, Protection) and self.stop.kind is ProtectionKind.STOP and self.stop.owner_id == self.lot_id,
             p + '.stop', 'a lot always carries its own stop record (status says whether it is on the exchange)')
        _req(self.stop.qty == self.qty or self.stop.status is ProtectionStatus.NEEDS_PLACEMENT or self.in_flight is not None,
             p + '.stop.qty', 'a placed stop covers exactly the lot qty (resize pending = needs_placement)')
        if self.target is not None:
            _req(self.target.kind is ProtectionKind.TARGET and self.target.owner_id == self.lot_id, p + '.target', 'own target')
        _req(list(self.ladder_done) == sorted(set(self.ladder_done)) and all(isinstance(i, int) and i >= 0 for i in self.ladder_done),
             p + '.ladder_done', 'sorted unique level indexes')
        _req(self.adds_done >= 0 and self.dca_filled >= 0, p, 'add counters >= 0')
        if self.in_flight is not None: _id(self.in_flight, p + '.in_flight', 'int')


@dataclass(**R)
class Position:
    """All lots of one account / symbol / side (hedge-mode position). qty is derived, never stored."""
    symbol: str
    side: Side
    lots: tuple

    def __post_init__(self):
        p = f'position[{self.symbol}|{self.side}]'
        _req(len(self.lots) > 0, p, 'an empty position is not stored')
        ids = [l.lot_id for l in self.lots]
        _req(len(ids) == len(set(ids)), p + '.lots', 'duplicate lot id')
        _req(all(l.symbol == self.symbol and l.side == self.side for l in self.lots), p + '.lots', 'lot of another symbol / side')

    @property
    def qty(self): return sum((l.qty for l in self.lots), Decimal(0))


class EntryKind(enum.StrEnum):
    MARKET = 'market'                # sent; answer unknown/known -> legacy unconfirmed_entries
    MAKER = 'maker'                  # resting post-only -> legacy resting_entries
    TRAILING = 'trailing'            # armed, nothing on the exchange -> legacy pending_entries


class EntryState(enum.StrEnum):
    ARMED = 'armed'; WORKING = 'working'; CANCELLING = 'cancelling'; CANCEL_UNKNOWN = 'cancel_unknown'; UNRESOLVED = 'unresolved'


@dataclass(**R)
class EntryIntent:
    entry_id: str
    account_id: str
    kind: EntryKind
    state: EntryState
    symbol: str
    side: Side
    slot_id: Optional[str]
    planned_qty: Decimal
    planned_price: Decimal
    stop_distance: Decimal
    created_at: float
    expires_at: Optional[float] = None
    order: Optional[str] = None          # intent_id currently on the exchange
    filled_qty: Decimal = Decimal(0)     # exchange-final fills not yet a lot
    seen_qty: Decimal = Decimal(0)       # unresolved size seen on the position (legacy seen_qty)
    provisional_stop: Optional[Protection] = None

    def __post_init__(self):
        p = f'entry[{self.entry_id}]'
        _id(self.entry_id, p + '.entry_id', 'ent')
        for f in ('planned_qty', 'planned_price', 'stop_distance'): _dec(getattr(self, f), f'{p}.{f}', gt=0)
        _dec(self.filled_qty, p + '.filled_qty', ge=0); _dec(self.seen_qty, p + '.seen_qty', ge=0)
        _req(self.filled_qty <= self.planned_qty, p + '.filled_qty', 'more than planned')
        if self.kind is EntryKind.TRAILING:
            _req(self.state is EntryState.ARMED and self.order is None and self.expires_at is not None, p, 'trailing = armed, no order, expiry')
        else:
            _req(self.state is not EntryState.ARMED, p + '.state', 'only a trailing entry is armed')
            _req(self.order is not None, p + '.order', 'a sent entry is owned by its order intent')
        if self.provisional_stop is not None:
            _req(self.kind is EntryKind.MARKET and self.provisional_stop.owner_id == self.entry_id, p + '.provisional_stop', 'market entries only')
            _req(self.provisional_stop.qty <= self.seen_qty, p + '.provisional_stop.qty',
                 'never larger than the unresolved size it protects (AUD-03 r1)')


@dataclass(**R)
class Portfolio:
    account_id: str
    generation: int                  # strictly increasing per durable write (NC-02)
    ownership: Ownership
    entries_mode: EntriesMode
    pause_reasons: tuple             # ReasonCodes keeping entries non-active (empty iff ACTIVE)
    positions: Optional[tuple]       # None iff ownership UNKNOWN
    entries: Optional[tuple]
    intents: Optional[tuple]         # in-flight OrderIntents (WAL)
    orphan_cancels: tuple = ()       # (symbol, tag) owned orders queued for cancellation
    trading_day: Optional[str] = None      # Cairo date
    day_start_equity: Optional[Decimal] = None
    peak_equity: Optional[Decimal] = None

    def __post_init__(self):
        p = 'portfolio'
        _id(self.account_id, p + '.account_id', 'acct')
        _req(isinstance(self.generation, int) and self.generation >= 0, p + '.generation', '>= 0')
        unknown = self.ownership is Ownership.UNKNOWN
        _req(all(x is None for x in (self.positions, self.entries, self.intents)) == unknown, p,
             'ownership UNKNOWN <=> positions / entries / intents are None (unknown is never empty)')
        _req(not (unknown and self.entries_mode is EntriesMode.ACTIVE), p + '.entries_mode', 'UNKNOWN ownership cannot be active')
        _req((len(self.pause_reasons) == 0) == (self.entries_mode is EntriesMode.ACTIVE), p + '.pause_reasons',
             'non-empty exactly when not active')
        if unknown: return
        keys = [(x.symbol, x.side) for x in self.positions]
        _req(len(keys) == len(set(keys)), p + '.positions', 'one Position per symbol / side')
        lots = [l for x in self.positions for l in x.lots]
        _req(all(l.account_id == self.account_id for l in lots), p + '.positions', 'lot of another account')
        intents = {i.intent_id: i for i in self.intents}
        _req(len(intents) == len(self.intents), p + '.intents', 'duplicate intent id')
        cids = [i.client_order_id for i in self.intents]
        _req(len(cids) == len(set(cids)), p + '.intents', 'duplicate client order id')
        _req(all(i.account_id == self.account_id for i in self.intents), p + '.intents', 'intent of another account')
        for l in lots:
            if l.in_flight is not None:
                _req(l.in_flight in intents and intents[l.in_flight].owner_id == l.lot_id, f'{p}.lot[{l.lot_id}].in_flight',
                     'names a durable intent owned by this lot')
        for e in self.entries:
            if e.order is not None:
                _req(e.order in intents and intents[e.order].owner_id == e.entry_id, f'{p}.entry[{e.entry_id}].order', 'durable intent')
            if self.entries_mode is not EntriesMode.ACTIVE:
                # Audit2 NEW-ENG-11: pause / halt / flatten drains entries in the SAME transition
                _req(e.kind is EntryKind.MARKET or e.state in (EntryState.CANCELLING, EntryState.CANCEL_UNKNOWN), f'{p}.entry[{e.entry_id}]',
                     f'a {e.kind} entry stays {e.state} while entries are {self.entries_mode}')

    @property
    def lots(self): return () if self.positions is None else tuple(l for x in self.positions for l in x.lots)


@dataclass(**R)
class Decision:
    decision_id: str
    account_id: str
    at: float
    action: Action
    reason: ReasonCode
    symbol: Optional[str] = None
    side: Optional[Side] = None
    subject_id: Optional[str] = None     # lot_ / ent_
    detail: str = ''                     # sanitized, <= 160 chars, never parsed
    intents: tuple = ()
    policy_version: str = ''

    def __post_init__(self):
        p = f'decision[{self.decision_id}]'
        _id(self.decision_id, p + '.decision_id', 'dec')
        _req(len(self.detail) <= 160, p + '.detail', 'longer than 160')
        if self.action in (Action.SKIP, Action.HOLD):
            _req(not self.intents, p + '.intents', 'skip / hold sends nothing')
        if self.action is Action.SKIP:
            _req(self.reason.stage in GATE_STAGES, p + '.reason', 'a skip names a gate reason')
        if self.action in (Action.CLOSE, Action.REDUCE):
            _req(self.reason.stage == 'exit' and self.intents and all(i.purpose in (Purpose.CLOSE, Purpose.REDUCE) for i in self.intents),
                 p, 'close / reduce = exit reason + close intents')
        if self.action is Action.ENTER:
            _req(len(self.intents) <= 1 and all(i.purpose is Purpose.ENTRY for i in self.intents), p + '.intents', 'one entry intent')
        _req(all(i.account_id == self.account_id for i in self.intents), p + '.intents', 'intent of another account')

    @property
    def priority(self): return PRIORITY[self.action]


# ----------------------------------------------------------------------------------------------------------- codec
_DOC_TYPES = {'portfolio': Portfolio, 'account': Account, 'decision': Decision}


def to_dict(obj):
    if is_dataclass(obj):
        return {f.name: to_dict(getattr(obj, f.name)) for f in fields(obj)}
    if isinstance(obj, enum.Enum): return obj.value
    if isinstance(obj, Decimal): return str(obj)
    if isinstance(obj, tuple): return [to_dict(x) for x in obj]
    if obj is None or isinstance(obj, (str, int, float, bool)): return obj
    raise TypeError(f'not encodable: {type(obj).__name__}')


_ELEM = {('Position', 'lots'): Lot, ('Portfolio', 'positions'): Position, ('Portfolio', 'entries'): EntryIntent,
         ('Portfolio', 'intents'): OrderIntent, ('Lot', 'fills'): Fill, ('Decision', 'intents'): OrderIntent,
         ('Lot', 'ladder_done'): int, ('Portfolio', 'pause_reasons'): ReasonCode, ('Portfolio', 'orphan_cancels'): tuple}


def _from(tp, v, path, owner=None, name=None):
    origin = typing.get_origin(tp)
    if origin in (typing.Union, types.UnionType):
        args = [a for a in typing.get_args(tp) if a is not type(None)]
        return None if v is None else _from(args[0], v, path, owner, name)
    if tp is tuple:
        _req(isinstance(v, list), path, 'not a list')
        el = _ELEM[(owner, name)]
        return tuple(tuple(x) if el is tuple else _from(el, x, f'{path}[{i}]') for i, x in enumerate(v))
    if is_dataclass(tp): return from_dict(tp, v, path)
    if isinstance(tp, type) and issubclass(tp, enum.Enum):
        try: return tp(v)
        except ValueError: raise InvalidRecord(path, f'{v!r} not a {tp.__name__}') from None
    if tp is Decimal:
        _req(isinstance(v, str), path, 'a decimal is a JSON string')
        try: d = Decimal(v)
        except Exception: raise InvalidRecord(path, f'{v!r} not a decimal') from None
        _req(d.is_finite(), path, 'not finite'); return d
    if tp is float:
        _req(isinstance(v, (int, float)) and not isinstance(v, bool), path, 'not a number'); return float(v)
    if tp in (int, bool, str):
        _req(type(v) is tp, path, f'not {tp.__name__}'); return v
    raise InvalidRecord(path, f'unsupported field type {tp}')


@cache
def _hints(cls):
    return typing.get_type_hints(cls), frozenset(f.name for f in fields(cls))


def from_dict(cls, d, path=None):
    path = path or cls.__name__.lower()
    _req(isinstance(d, dict), path, 'not an object')
    hints, names = _hints(cls)
    unknown = set(d) - names
    _req(not unknown, path, f'unknown keys {sorted(unknown)}')
    kw = {k: _from(hints[k], d[k], f'{path}.{k}', cls.__name__, k) for k in d}
    try:
        return cls(**kw)
    except TypeError as ex:                       # missing required field
        raise InvalidRecord(path, str(ex)) from None


def decode_document(doc):
    """Envelope {schema_version, kind, body}. Future version -> FutureSchema FIRST (nothing else inspected)."""
    if not isinstance(doc, dict): raise InvalidRecord('document', 'not an object')
    v = doc.get('schema_version')
    if isinstance(v, int) and not isinstance(v, bool) and v > SCHEMA_VERSION:
        raise FutureSchema('document.schema_version', f'{v} is newer than this build ({SCHEMA_VERSION})')
    _req(v == SCHEMA_VERSION, 'document.schema_version', f'{v!r} is not {SCHEMA_VERSION}')
    _req(set(doc) == {'schema_version', 'kind', 'body'}, 'document', 'envelope keys')
    _req(doc['kind'] in _DOC_TYPES, 'document.kind', f'{doc["kind"]!r}')
    return from_dict(_DOC_TYPES[doc['kind']], doc['body'], doc['kind'])


def encode_document(obj):
    kind = next(k for k, c in _DOC_TYPES.items() if isinstance(obj, c))
    return dict(schema_version=SCHEMA_VERSION, kind=kind, body=to_dict(obj))
