"""STRAT-01: frozen research strategies as a NEWCORE ``EntrySource`` (PAPER / TESTNET only).

This module lives **outside** ``newcore/runtime`` on purpose: it imports the legacy ``strategies.py`` (pandas), which the
runtime's import boundary forbids.  The runtime only ever sees the ``EntrySource`` protocol.

For every configured ``StrategySlot`` and every candle that has just closed:

1. the ``CandleFeed`` returns closed candles only; the slot is evaluated only when every candle of the window is
   contiguous, the newest one is the candle that closed most recently at the wall clock, and it is younger than
   ``max_signal_age_ms`` (a restart never enters hours late);
2. the unchanged research path runs: ``strategies.indicators`` -> ``strategies.build_context`` over the slot's whole
   universe -> ``strategies.signals``.  The decision is the signal on the **last closed** candle only;
3. a signal becomes one ``EntryCandidate``: the NC-01 ``decision_key`` (name@tf, version, symbol, side, candle close,
   ENTRY) and an NC-06 ``RiskEvaluation`` whose snapshots come from the injected ``RiskContextProvider``.  The request
   asks for ``equity x base_risk_fraction x risk_mult`` with the stop at ``stop_atr x ATR`` of the decision candle
   (the strategy's own ATR series when it supplies one, exactly like ``backtest._sig_atr``), rounded away from entry
   to the tick size.

A DecisionKey is re-offered at most every ``reoffer_ms`` while its candle is inside ``max_signal_age_ms``, so a transient
refusal (sink busy, stale snapshot) can still succeed; the journaling sink's ``claim_signal`` is the durable once-only
gate.  An incomplete evaluation (late feed, bad window) is retried every ``retry_ms`` instead of being cached.  Every refusal is fail-closed (no candidate) and counted in ``reasons`` for observability.

Only entries are produced.  Exits other than the protective stop (signal exits, take-profit, max-bars) are NOT wired
here; see docs/newcore/STRAT01_ENTRY_SOURCE.md.
"""
from __future__ import annotations

import hashlib
import math
import re
from collections import Counter
from dataclasses import dataclass
from decimal import ROUND_CEILING, ROUND_FLOOR, Decimal, InvalidOperation
from typing import Callable, Protocol, runtime_checkable

import numpy as np
import pandas as pd

import strategies as ST
from newcore import risk as nc06
from newcore.domain.orders import Purpose
from newcore.ports.keys import TIMEFRAMES, decision_key
from newcore.runtime.ports import EntryCandidate

NAME_RE = re.compile(r'[a-z0-9_]{1,24}')
SYMBOL_RE = re.compile(r'[A-Z0-9]{2,24}USDT')
CROSS_SECTIONAL = frozenset({'rotation_long_h2'})   # signals depend on the rank of the whole universe
# Only keys whose signals need nothing beyond strategies.build_context and whose last-bar parity is tested.  Others
# (rank/breadth keys, keys reading mark/order-flow context) are refused rather than run on inputs they never had.
SUPPORTED = frozenset({'rotation_long_h2', 'exp_range', 'exp_vwaprev', 'evening_star_short_1h',
                       'starc_band_breakout_long_1h', 'kalman_innovation_recovery_long_1h'})
MAX_RISK_MULT = Decimal('1')
MAX_BASE_RISK = Decimal('0.01')                       # 1% of equity per trade before the multiplier, hard ceiling
MAX_LEVERAGE = Decimal('3')                           # owner testnet bound
MIN_WARMUP = 400
CANDLE_COLUMNS = ('t', 'o', 'h', 'l', 'c', 'v')
NEEDS_QV = frozenset({'exp_vwaprev'})                 # VWAPREV reads quote volume; without it it can never fire


class StrategyConfigError(ValueError):
    def __init__(self, code: str):
        self.code = code
        super().__init__(code)


def _dec(value, code):
    try:
        out = Decimal(value) if type(value) is str else None
    except InvalidOperation:
        out = None
    if out is None or not out.is_finite():
        raise StrategyConfigError(code)
    return out


@dataclass(frozen=True, slots=True, kw_only=True)
class StrategySlot:
    """One frozen research strategy, one side, one universe.  Values are strings/tuples so a JSON config maps 1:1."""
    key: str                    # strategies.STRATEGIES key, e.g. 'rotation_long_h2'
    name: str                   # NEWCORE decision name [a-z0-9_]{1,24}
    version: str                # 'v1'
    tf: str                     # must equal the strategy's frozen timeframe
    side: nc06.PositionSide
    symbols: tuple[str, ...]    # must contain BTCUSDT (build_context needs it)
    risk_mult: Decimal          # 0 < m <= 1, the DD-policy cap
    strategy_id: str            # NC-06 allocation identity
    book_id: str

    def __post_init__(self):
        if type(self.key) is not str or self.key not in SUPPORTED:
            raise StrategyConfigError('strategy_unsupported')
        if self.key not in ST.STRATEGIES:
            raise StrategyConfigError('strategy_unavailable')
        st = ST.STRATEGIES[self.key]
        if type(self.name) is not str or NAME_RE.fullmatch(self.name) is None:
            raise StrategyConfigError('name_invalid')
        if type(self.version) is not str or re.fullmatch(r'v(0|[1-9][0-9]{0,5})', self.version) is None:
            raise StrategyConfigError('version_invalid')
        if self.tf not in TIMEFRAMES or self.tf != st.get('tf'):
            raise StrategyConfigError('tf_mismatch')
        if type(self.side) is not nc06.PositionSide:
            raise StrategyConfigError('side_invalid')
        allowed = {'long': {nc06.PositionSide.LONG}, 'short': {nc06.PositionSide.SHORT},
                   'both': {nc06.PositionSide.LONG, nc06.PositionSide.SHORT}}.get(st.get('sides'), set())
        if self.side not in allowed:
            raise StrategyConfigError('side_not_allowed')
        if (type(self.symbols) is not tuple or not 2 <= len(self.symbols) <= 40
                or len(set(self.symbols)) != len(self.symbols)
                or any(type(s) is not str or SYMBOL_RE.fullmatch(s) is None for s in self.symbols)
                or 'BTCUSDT' not in self.symbols):
            raise StrategyConfigError('symbols_invalid')
        if type(self.risk_mult) is not Decimal or not self.risk_mult.is_finite() \
                or not Decimal('0') < self.risk_mult <= MAX_RISK_MULT:
            raise StrategyConfigError('risk_mult_invalid')
        stop_atr = (st.get('mgmt') or {}).get('stop_atr')
        if type(stop_atr) not in (int, float) or not math.isfinite(stop_atr) or stop_atr <= 0:
            raise StrategyConfigError('stop_atr_missing')
        for value in (self.strategy_id, self.book_id):
            if type(value) is not str or not 1 <= len(value) <= 64:
                raise StrategyConfigError('allocation_identity_invalid')

    @property
    def tf_ms(self) -> int:
        return TIMEFRAMES[self.tf]

    @property
    def stop_atr(self) -> float:
        return float(ST.STRATEGIES[self.key]['mgmt']['stop_atr'])


@dataclass(frozen=True, slots=True, kw_only=True)
class RiskContext:
    """Fresh NC-06 snapshots for one account/symbol, supplied by the runtime owner (never built from guesses here)."""
    account: nc06.AccountRiskSnapshot
    portfolio: nc06.PortfolioRiskSnapshot
    policy: nc06.RiskPolicySnapshot
    filters: nc06.VenueRiskFilters
    followers: tuple[nc06.FollowerProfile, ...] = ()


@runtime_checkable
class CandleFeed(Protocol):
    def closed(self, symbol: str, tf: str, bars: int, now_wall_ms: int) -> pd.DataFrame | None:
        """Up to ``bars`` CLOSED candles (columns t,o,h,l,c,v; t = UTC open time), oldest first, or None."""


@runtime_checkable
class RiskContextProvider(Protocol):
    def risk_context(self, account_id: str, slot: StrategySlot, symbol: str) -> RiskContext | None: ...


@dataclass(frozen=True, slots=True)
class Signal:
    slot: StrategySlot
    symbol: str
    close_ms: int
    entry: Decimal
    atr: float


def _to_ms(ts) -> int:
    """UTC epoch ms of a candle open time.  Naive values are UTC; aware ones are converted.  Numbers are refused:
    ``pd.Timestamp(int)`` would read epoch milliseconds as nanoseconds."""
    if isinstance(ts, (bool, int, float, np.integer, np.floating)):
        raise ValueError('candle_time_numeric')
    stamp = pd.Timestamp(ts)
    if stamp is pd.NaT:
        raise ValueError('candle_time_missing')
    if stamp.tzinfo is not None:
        stamp = stamp.tz_convert('UTC').tz_localize(None)
    return int(stamp.value // 1_000_000)


def _sig_atr(sig, i, fallback) -> float:
    a = sig.get('atr')
    if a is not None and 0 <= i < len(a) and np.isfinite(a[i]) and a[i] > 0:
        return float(a[i])
    return float(fallback)


def _clean(frame, tf_ms, bars, expected_close_ms, need_qv=False):
    """A float64 copy of the window, or None unless it is contiguous closed candles ending at ``expected_close_ms``."""
    cols = list(CANDLE_COLUMNS) + (['qv'] if need_qv else [])
    if not isinstance(frame, pd.DataFrame) or any(c not in frame.columns for c in cols) or len(frame) < bars:
        return None
    frame = frame.iloc[-bars:]
    try:
        opens = [_to_ms(t) for t in frame['t']]
        values = np.array([[float(x) for x in frame[c]] for c in cols[1:]], dtype=float).T
    except Exception:
        return None
    if not np.isfinite(values).all() or (values[:, :4] <= 0).any() or (values[:, 4:] < 0).any():
        return None
    if (values[:, 1] < values[:, [0, 2, 3]].max(axis=1)).any() or (values[:, 2] > values[:, [0, 1, 3]].min(axis=1)).any():
        return None
    if any(b - a != tf_ms for a, b in zip(opens, opens[1:])) or opens[-1] + tf_ms != expected_close_ms:
        return None
    out = pd.DataFrame(values, columns=cols[1:])
    out.insert(0, 't', pd.to_datetime(opens, unit='ms'))
    return out


def _is_dec(x, positive=True) -> bool:
    return type(x) is Decimal and x.is_finite() and (x > 0 if positive else x >= 0)


class StrategyEntrySource:
    """``newcore.runtime.ports.EntrySource`` over frozen ``strategies.py`` signals."""

    def __init__(self, slots, *, feed: CandleFeed, risk: RiskContextProvider, wall_clock: Callable[[], int],
                 base_risk_fraction: Decimal, leverage: Decimal, warmup_bars: int = 1500,
                 max_signal_age_ms: int = 15 * 60_000, account_ids: tuple[str, ...] | None = None,
                 max_entries_per_candle: int = 1, retry_ms: int = 30_000, reoffer_ms: int = 60_000):
        slots = tuple(slots)
        if not slots or any(type(s) is not StrategySlot for s in slots):
            raise StrategyConfigError('slots_invalid')
        if len({s.name for s in slots}) != len(slots):
            raise StrategyConfigError('slot_name_duplicate')
        if not callable(wall_clock) or not isinstance(feed, CandleFeed) or not isinstance(risk, RiskContextProvider):
            raise StrategyConfigError('ports_invalid')
        if not _is_dec(base_risk_fraction) or not base_risk_fraction <= MAX_BASE_RISK:
            raise StrategyConfigError('base_risk_invalid')
        if type(leverage) is not Decimal or not leverage.is_finite() or not Decimal('1') <= leverage <= MAX_LEVERAGE:
            raise StrategyConfigError('leverage_invalid')
        if type(warmup_bars) is not int or not MIN_WARMUP <= warmup_bars <= 5000:
            raise StrategyConfigError('warmup_invalid')
        if type(max_signal_age_ms) is not int or not 0 < max_signal_age_ms <= min(s.tf_ms for s in slots) // 2:
            raise StrategyConfigError('max_signal_age_invalid')
        if account_ids is not None and (type(account_ids) is not tuple or not account_ids):
            raise StrategyConfigError('account_ids_invalid')
        if type(max_entries_per_candle) is not int or not 1 <= max_entries_per_candle <= 5:
            raise StrategyConfigError('max_entries_per_candle_invalid')
        for value, code in ((retry_ms, 'retry_ms_invalid'), (reoffer_ms, 'reoffer_ms_invalid')):
            if type(value) is not int or not 1_000 <= value <= max_signal_age_ms:
                raise StrategyConfigError(code)
        self.slots = slots
        self._feed, self._risk, self._wall = feed, risk, wall_clock
        self._base, self._lev = base_risk_fraction, leverage
        self._bars, self._max_age = warmup_bars, max_signal_age_ms
        self._accounts, self._per_candle = account_ids, max_entries_per_candle
        self._retry, self._reoffer = retry_ms, reoffer_ms
        self._signals: dict[str, tuple[int, tuple[Signal, ...]]] = {}   # slot -> (close ms, signals) once complete
        self._partial: dict[str, tuple[int, int, tuple[Signal, ...]]] = {}  # slot -> (close, attempt wall, signals)
        self._last_offer: dict[tuple[str, object], int] = {}            # (account id, DecisionKey) -> wall ms
        self.reasons: Counter = Counter()

    # ------------------------------------------------------------------------------------------------ signals
    def _evaluate(self, slot: StrategySlot, close_ms: int) -> tuple[tuple[Signal, ...], bool]:
        """(signals, complete).  Incomplete results are never cached, so a late or broken feed is retried."""
        frames, complete = {}, True
        for symbol in slot.symbols:
            try:
                raw = self._feed.closed(symbol, slot.tf, self._bars, close_ms)
            except Exception:
                raw = None
            frame = _clean(raw, slot.tf_ms, self._bars, close_ms, need_qv=slot.key in NEEDS_QV)
            if frame is not None:
                try:
                    frame = ST.indicators(frame)
                except Exception:
                    frame = None
            if frame is None:
                self.reasons[f'{slot.name}:candles_unusable'] += 1
                if slot.key in CROSS_SECTIONAL or symbol == 'BTCUSDT':
                    return (), False   # a partial universe changes ranks / the BTC context: no decision at all
                complete = False
                continue
            frames[symbol] = frame
        try:
            ctx = ST.build_context(frames)
        except Exception:
            self.reasons[f'{slot.name}:context_error'] += 1
            return (), False
        out = []
        for symbol, d in frames.items():
            try:
                sig = ST.signals(slot.key, d, ctx[symbol])
                i = len(d) - 1
                fired = bool(sig['le'][i] if slot.side is nc06.PositionSide.LONG else sig['se'][i])
                atr = _sig_atr(sig, i, d['atr'].iloc[i]) if fired else 0.0
                entry = Decimal(repr(float(d['c'].iloc[i])))
            except Exception:
                self.reasons[f'{slot.name}:signal_error'] += 1
                complete = False
                continue
            if not fired:
                continue
            if not math.isfinite(atr) or atr <= 0 or not entry > 0:
                self.reasons[f'{slot.name}:atr_invalid'] += 1
                continue
            out.append(Signal(slot=slot, symbol=symbol, close_ms=close_ms, entry=entry, atr=atr))
        self.reasons[f'{slot.name}:evaluated'] += 1
        return tuple(out), complete

    def _current(self, slot: StrategySlot, now_wall: int) -> tuple[Signal, ...]:
        close_ms = now_wall // slot.tf_ms * slot.tf_ms
        if now_wall - close_ms > self._max_age:
            return ()                  # too late for this candle; wait for the next close
        done = self._signals.get(slot.name)
        if done is not None and done[0] == close_ms:
            return done[1]
        part = self._partial.get(slot.name)
        if part is not None and part[0] == close_ms and now_wall - part[1] < self._retry:
            return part[2]
        signals, complete = self._evaluate(slot, close_ms)
        if complete:
            self._signals[slot.name] = (close_ms, signals)
            self._partial.pop(slot.name, None)
        else:
            self._partial[slot.name] = (close_ms, now_wall, signals)
        return signals

    # ------------------------------------------------------------------------------------------------ requests
    def _context_ok(self, ctx, account_id: str, symbol: str) -> bool:
        return (type(ctx) is RiskContext and type(ctx.account) is nc06.AccountRiskSnapshot
                and type(ctx.portfolio) is nc06.PortfolioRiskSnapshot and type(ctx.policy) is nc06.RiskPolicySnapshot
                and type(ctx.filters) is nc06.VenueRiskFilters and type(ctx.followers) is tuple
                and ctx.account.account_id == account_id and ctx.filters.symbol == symbol
                and _is_dec(ctx.account.equity) and _is_dec(ctx.filters.tick_size))

    def _candidate(self, account_id: str, s: Signal) -> EntryCandidate | None:
        slot = s.slot
        try:
            ctx = self._risk.risk_context(account_id, slot, s.symbol)
        except Exception:
            ctx = None
        if not self._context_ok(ctx, account_id, s.symbol):
            self.reasons[f'{slot.name}:risk_context_missing'] += 1
            return None
        tick = ctx.filters.tick_size
        entry = (s.entry / tick).to_integral_value() * tick          # reference price on the tick grid
        distance = Decimal(repr(slot.stop_atr * s.atr))
        if slot.side is nc06.PositionSide.LONG:
            stop = ((entry - distance) / tick).to_integral_value(ROUND_FLOOR) * tick
        else:
            stop = ((entry + distance) / tick).to_integral_value(ROUND_CEILING) * tick
        if not entry > 0 or not stop > 0 or stop == entry:
            self.reasons[f'{slot.name}:stop_invalid'] += 1
            return None
        risk = (ctx.account.equity * self._base * slot.risk_mult).quantize(Decimal('0.00000001'), ROUND_FLOOR)
        if not risk > 0:
            self.reasons[f'{slot.name}:risk_zero'] += 1
            return None
        key = decision_key(slot.name, slot.version, slot.tf, s.symbol, slot.side.value, s.close_ms, Purpose.ENTRY)
        digest = hashlib.sha256(b'zackbot.newcore_strategies.request.v1\x00' + account_id.encode('utf-8') + b'\x00'
                                + repr((key.strategy, key.strategy_version, key.symbol, key.side.value,
                                        key.candle_close_ms)).encode('utf-8')).hexdigest()
        request = nc06.RiskRequest(
            request_id='req_' + digest[:32], account_id=account_id, environment=ctx.account.environment,
            account_generation=ctx.account.generation, portfolio_generation=ctx.portfolio.generation,
            policy_generation=ctx.policy.generation, filter_generation=ctx.filters.generation,
            action=nc06.RiskAction.ENTRY, strategy_id=slot.strategy_id, book_id=slot.book_id, symbol=s.symbol,
            side=slot.side, entry_price=entry, stop_price=stop, requested_risk=risk, requested_leverage=self._lev)
        evaluation = nc06.RiskEvaluation(account=ctx.account, portfolio=ctx.portfolio, policy=ctx.policy,
                                         filters=ctx.filters, request=request, followers=ctx.followers)
        return EntryCandidate(key=key, evaluation=evaluation)

    def _slot_candidates(self, account_id: str, slot: StrategySlot, now_wall: int) -> list:
        out, chosen = [], 0
        for s in self._current(slot, now_wall):
            if chosen >= self._per_candle:
                self.reasons[f'{slot.name}:per_candle_cap'] += 1
                break                  # universe order: the cap always keeps the same first signals
            try:
                candidate = self._candidate(account_id, s)
            except Exception:
                self.reasons[f'{slot.name}:candidate_error'] += 1
                candidate = None
            if candidate is None:
                continue
            chosen += 1
            last = self._last_offer.get((account_id, candidate.key))
            if last is not None and now_wall - last < self._reoffer:
                continue               # offered recently; the sink's durable claim decides whether it was taken
            out.append(candidate)
        return out

    def candidates(self, account_id: str, now_mono: int) -> tuple[EntryCandidate, ...]:
        """Never raises: any slot failure is counted in ``reasons`` and gives no candidate for that slot."""
        if self._accounts is not None and account_id not in self._accounts:
            return ()
        now_wall = self._wall()
        if type(now_wall) is not int or now_wall <= 0:
            self.reasons['wall_clock_invalid'] += 1
            return ()
        out = []
        for slot in self.slots:
            try:
                found = self._slot_candidates(account_id, slot, now_wall)
            except Exception:
                self.reasons[f'{slot.name}:slot_error'] += 1
                continue
            out.extend(found)
        for candidate in out:          # recorded only once the whole answer is built
            self._last_offer[(account_id, candidate.key)] = now_wall
            self.reasons[f'{candidate.key.strategy.split("@")[0]}:offered'] += 1
        if len(self._last_offer) > 1_000:
            horizon = now_wall - 2 * max(s.tf_ms for s in self.slots)
            self._last_offer = {k: v for k, v in self._last_offer.items() if k[1].candle_close_ms >= horizon}
        return tuple(out)


def slot_from_dict(raw: dict) -> StrategySlot:
    """Build a slot from JSON-shaped values (strings for decimals and side)."""
    if type(raw) is not dict or set(raw) != {'key', 'name', 'version', 'tf', 'side', 'symbols', 'risk_mult',
                                             'strategy_id', 'book_id'}:
        raise StrategyConfigError('slot_fields_invalid')
    try:
        side = nc06.PositionSide(raw['side'])
    except ValueError:
        raise StrategyConfigError('side_invalid') from None
    symbols = raw['symbols']
    if type(symbols) is not list:
        raise StrategyConfigError('symbols_invalid')
    return StrategySlot(key=raw['key'], name=raw['name'], version=raw['version'], tf=raw['tf'], side=side,
                        symbols=tuple(symbols), risk_mult=_dec(raw['risk_mult'], 'risk_mult_invalid'),
                        strategy_id=raw['strategy_id'], book_id=raw['book_id'])


__all__ = ['CROSS_SECTIONAL', 'CandleFeed', 'RiskContext', 'RiskContextProvider', 'Signal', 'StrategyConfigError',
           'StrategyEntrySource', 'SUPPORTED', 'StrategySlot', 'slot_from_dict']
