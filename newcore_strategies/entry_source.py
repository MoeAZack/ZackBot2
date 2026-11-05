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

Each DecisionKey is offered once per process; the journaling sink's ``claim_signal`` is the durable once-only gate
across restarts.  Every refusal is fail-closed (no candidate) and counted in ``reasons`` for observability.

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
        if type(self.key) is not str or self.key not in ST.STRATEGIES:
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
    return int(pd.Timestamp(ts).tz_localize(None).value // 1_000_000) if pd.Timestamp(ts).tzinfo else \
        int(pd.Timestamp(ts).value // 1_000_000)


def _sig_atr(sig, i, fallback) -> float:
    a = sig.get('atr')
    if a is not None and 0 <= i < len(a) and np.isfinite(a[i]) and a[i] > 0:
        return float(a[i])
    return float(fallback)


def _clean(frame, tf_ms, bars, expected_close_ms, need_qv=False):
    """The window, or None unless it is contiguous closed candles ending exactly at ``expected_close_ms``."""
    cols = list(CANDLE_COLUMNS) + (['qv'] if need_qv else [])
    if not isinstance(frame, pd.DataFrame) or any(c not in frame.columns for c in cols) or len(frame) < bars:
        return None
    frame = frame.loc[:, cols].iloc[-bars:].reset_index(drop=True)
    try:
        opens = [_to_ms(t) for t in frame['t']]
        values = frame[cols[1:]].to_numpy(dtype=float)
    except Exception:
        return None
    if not np.isfinite(values).all() or (values[:, :4] <= 0).any() or (values[:, 4:] < 0).any():
        return None
    if (values[:, 1] < values[:, [0, 2, 3]].max(axis=1)).any() or (values[:, 2] > values[:, [0, 1, 3]].min(axis=1)).any():
        return None
    if any(b - a != tf_ms for a, b in zip(opens, opens[1:])) or opens[-1] + tf_ms != expected_close_ms:
        return None
    frame['t'] = pd.to_datetime(opens, unit='ms')
    return frame


class StrategyEntrySource:
    """``newcore.runtime.ports.EntrySource`` over frozen ``strategies.py`` signals."""

    def __init__(self, slots, *, feed: CandleFeed, risk: RiskContextProvider, wall_clock: Callable[[], int],
                 base_risk_fraction: Decimal, leverage: Decimal, warmup_bars: int = 1500,
                 max_signal_age_ms: int = 15 * 60_000, account_ids: tuple[str, ...] | None = None,
                 max_entries_per_candle: int = 1):
        slots = tuple(slots)
        if not slots or any(type(s) is not StrategySlot for s in slots):
            raise StrategyConfigError('slots_invalid')
        if len({s.name for s in slots}) != len(slots):
            raise StrategyConfigError('slot_name_duplicate')
        if not callable(wall_clock) or not isinstance(feed, CandleFeed) or not isinstance(risk, RiskContextProvider):
            raise StrategyConfigError('ports_invalid')
        if type(base_risk_fraction) is not Decimal or not base_risk_fraction.is_finite() \
                or not Decimal('0') < base_risk_fraction <= MAX_BASE_RISK:
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
        self._per_candle = max_entries_per_candle
        self.slots = slots
        self._feed, self._risk, self._wall = feed, risk, wall_clock
        self._base, self._lev = base_risk_fraction, leverage
        self._bars, self._max_age = warmup_bars, max_signal_age_ms
        self._accounts = account_ids
        self._signals: dict[tuple[str, int], tuple[Signal, ...]] = {}    # (slot name, close ms) -> signals
        self._offered: set[tuple[str, object]] = set()                   # (account id, DecisionKey)
        self.reasons: Counter = Counter()

    # ------------------------------------------------------------------------------------------------ signals
    def _evaluate(self, slot: StrategySlot, close_ms: int) -> tuple[Signal, ...]:
        frames = {}
        for symbol in slot.symbols:
            try:
                raw = self._feed.closed(symbol, slot.tf, self._bars, close_ms)
            except Exception:
                raw = None
            frame = _clean(raw, slot.tf_ms, self._bars, close_ms, need_qv=slot.key in NEEDS_QV)
            if frame is None:
                self.reasons[f'{slot.name}:candles_unusable'] += 1
                if slot.key in CROSS_SECTIONAL or symbol == 'BTCUSDT':
                    return ()          # a partial universe changes ranks / the BTC context: no decision at all
                continue
            frames[symbol] = ST.indicators(frame)
        if 'BTCUSDT' not in frames:
            return ()
        ctx = ST.build_context(frames)
        out = []
        for symbol, d in frames.items():
            sig = ST.signals(slot.key, d, ctx[symbol])
            i = len(d) - 1
            fired = sig['le'][i] if slot.side is nc06.PositionSide.LONG else sig['se'][i]
            if not fired:
                continue
            atr = _sig_atr(sig, i, d['atr'].iloc[i])
            entry = Decimal(repr(float(d['c'].iloc[i])))
            if not math.isfinite(atr) or atr <= 0 or not entry > 0:
                self.reasons[f'{slot.name}:atr_invalid'] += 1
                continue
            out.append(Signal(slot=slot, symbol=symbol, close_ms=close_ms, entry=entry, atr=atr))
        self.reasons[f'{slot.name}:evaluated'] += 1
        return tuple(out)

    def _current(self, slot: StrategySlot, now_wall: int) -> tuple[Signal, ...]:
        close_ms = now_wall // slot.tf_ms * slot.tf_ms
        if now_wall - close_ms > self._max_age:
            return ()                  # too late for this candle; wait for the next close
        cache_key = (slot.name, close_ms)
        if cache_key not in self._signals:
            self._signals = {k: v for k, v in self._signals.items() if k[0] != slot.name}
            self._signals[cache_key] = self._evaluate(slot, close_ms)
        return self._signals[cache_key]

    # ------------------------------------------------------------------------------------------------ requests
    def _candidate(self, account_id: str, s: Signal) -> EntryCandidate | None:
        slot = s.slot
        try:
            ctx = self._risk.risk_context(account_id, slot, s.symbol)
        except Exception:
            ctx = None
        if type(ctx) is not RiskContext or ctx.account.account_id != account_id \
                or ctx.filters.symbol != s.symbol or not ctx.filters.tick_size > 0:
            self.reasons[f'{slot.name}:risk_context_missing'] += 1
            return None
        tick = ctx.filters.tick_size
        distance = Decimal(repr(slot.stop_atr * s.atr))
        if slot.side is nc06.PositionSide.LONG:
            stop = ((s.entry - distance) / tick).to_integral_value(ROUND_FLOOR) * tick
        else:
            stop = ((s.entry + distance) / tick).to_integral_value(ROUND_CEILING) * tick
        if not stop > 0 or stop == s.entry:
            self.reasons[f'{slot.name}:stop_invalid'] += 1
            return None
        risk = (ctx.account.equity * self._base * slot.risk_mult).quantize(Decimal('0.00000001'), ROUND_FLOOR)
        if not risk > 0:
            self.reasons[f'{slot.name}:risk_zero'] += 1
            return None
        key = decision_key(slot.name, slot.version, slot.tf, s.symbol, slot.side.value, s.close_ms, Purpose.ENTRY)
        digest = hashlib.sha256(b'zackbot.newcore_strategies.request.v1\x00' + account_id.encode('ascii') + b'\x00'
                                + repr((key.strategy, key.strategy_version, key.symbol, key.side.value,
                                        key.candle_close_ms)).encode('ascii')).hexdigest()
        request = nc06.RiskRequest(
            request_id='req_' + digest[:32], account_id=account_id, environment=ctx.account.environment,
            account_generation=ctx.account.generation, portfolio_generation=ctx.portfolio.generation,
            policy_generation=ctx.policy.generation, filter_generation=ctx.filters.generation,
            action=nc06.RiskAction.ENTRY, strategy_id=slot.strategy_id, book_id=slot.book_id, symbol=s.symbol,
            side=slot.side, entry_price=s.entry, stop_price=stop, requested_risk=risk, requested_leverage=self._lev)
        evaluation = nc06.RiskEvaluation(account=ctx.account, portfolio=ctx.portfolio, policy=ctx.policy,
                                         filters=ctx.filters, request=request, followers=tuple(ctx.followers))
        return EntryCandidate(key=key, evaluation=evaluation)

    def candidates(self, account_id: str, now_mono: int) -> tuple[EntryCandidate, ...]:
        if self._accounts is not None and account_id not in self._accounts:
            return ()
        now_wall = self._wall()
        if type(now_wall) is not int or now_wall <= 0:
            self.reasons['wall_clock_invalid'] += 1
            return ()
        out = []
        for slot in self.slots:
            for s in self._current(slot, now_wall):
                used = sum(1 for a, k in self._offered if a == account_id and k.strategy == f'{slot.name}@{slot.tf}'
                           and k.candle_close_ms == s.close_ms)
                if used >= self._per_candle:
                    self.reasons[f'{slot.name}:per_candle_cap'] += 1
                    break              # signals are in universe order: the cap keeps the first ones only
                candidate = self._candidate(account_id, s)
                if candidate is None or (account_id, candidate.key) in self._offered:
                    continue
                self._offered.add((account_id, candidate.key))
                self.reasons[f'{slot.name}:offered'] += 1
                out.append(candidate)
        if len(self._offered) > 10_000:       # keys are per candle; old ones can never recur
            horizon = now_wall - 2 * max(s.tf_ms for s in self.slots)
            self._offered = {x for x in self._offered if x[1].candle_close_ms >= horizon}
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
           'StrategyEntrySource', 'StrategySlot', 'slot_from_dict']
