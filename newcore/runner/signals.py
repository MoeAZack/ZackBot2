"""Signal sources the Runner consumes: what to do at the close of the last closed candle.

A SignalSource sees CLOSED bars only (the BarSource window the Runner reads, at most `window` candles) and returns typed
signals. It is stateless: the same bars give the same signals, which is what makes a restart able to re-derive an
entry's stop distance from the klines (exchange truth) instead of storing it.

    EmaMomSignals    newcore.strategy.ema_mom (trend_ema_mom v1); instance name 'trend_ema_mom@<tf>'
    InjectedSignals  signals named per candle (golden cases, mechanics tests); stop = stop_atr x Wilder ATR14
    NoSignals        the strategy disabled: the loop still syncs, reconciles and protects

Window. The live kline read is capped at 1500 candles, so the live strategy sees a 1500-candle window, while the
research evaluates the whole history. EMA200 / ATR seeds decay to below float noise well inside 1500 candles
(measured on BTCUSDT 4h data_long: 0 signal differences over 10,279 decision candles).
"""
from __future__ import annotations

from dataclasses import dataclass, field
from decimal import Decimal
from typing import Protocol

import newcore.strategy as NS
from newcore.domain import ReasonCode
from newcore.strategy import indicators as IND

ENTER = 'enter'
CLOSE = 'close'


@dataclass(frozen=True, slots=True)
class Signal:
    action: str                       # 'enter' | 'close'
    side: str                         # position side: 'LONG' | 'SHORT'
    candle_close_ms: int              # the signal candle's close (= DecisionKey.candle_close_ms)
    stop_distance: Decimal | None     # enter only: price distance fill -> stop
    reason: ReasonCode


class SignalSource(Protocol):
    strategy: str                     # DecisionKey.strategy (instance, timeframe included)
    version: str                      # DecisionKey.strategy_version
    window: int                       # closed candles to read

    def decide(self, symbol: str, bars: tuple, as_of_ms: int) -> tuple[Signal, ...]: ...


class _FloatCache:
    """Bar -> float OHLC, converted once per candle (the hot path re-reads the same window every cycle)."""

    def __init__(self):
        self._c = {}

    def series(self, symbol, bars):
        out = []
        for b in bars:
            k = (symbol, b.open_ms)
            v = self._c.get(k)
            if v is None:
                v = self._c[k] = (float(b.open), float(b.high), float(b.low), float(b.close))
            out.append(v)
        return tuple(b.open_ms for b in bars), tuple(zip(*out)) if out else ((), (), (), ())


@dataclass
class EmaMomSignals:
    tf_ms: int
    tf_label: str = '4h'
    params: NS.Params = field(default_factory=NS.Params)
    window: int = 1500
    enabled: bool = True

    def __post_init__(self):
        rule, version = NS.RULE_ID.rsplit('.', 1)
        self.strategy, self.version = f'{rule}@{self.tf_label}', version
        self._cache = _FloatCache()

    def decide(self, symbol, bars, as_of_ms):
        if not self.enabled or not bars:
            return ()
        t, (o, h, l, c) = self._cache.series(symbol, bars)
        out = NS.decide(NS.Bars(symbol, self.tf_ms, t, o, h, l, c), self.params, as_of_ms=as_of_ms)
        return tuple(Signal(action=ENTER if d.action is NS.SignalAction.ENTER else CLOSE, side=str(d.side),
                            candle_close_ms=d.signal_time_ms, stop_distance=d.stop_distance, reason=ReasonCode(d.reason))
                     for d in out)


@dataclass
class InjectedSignals:
    """signals: {(symbol, candle_close_ms): (('enter' | 'close', 'LONG' | 'SHORT'), ...)}."""
    signals: dict
    stop_atr: Decimal
    tf_label: str = '4h'
    atr_len: int = 14
    window: int = 1500
    strategy: str = ''
    version: str = 'v1'

    def __post_init__(self):
        self.strategy = self.strategy or f'injected@{self.tf_label}'
        self._cache = _FloatCache()

    def decide(self, symbol, bars, as_of_ms):
        if not bars:
            return ()
        close_ms = bars[-1].close_ms
        todo = self.signals.get((symbol, close_ms), ())
        if not todo:
            return ()
        _, (o, h, l, c) = self._cache.series(symbol, bars)
        atr = IND.wilder_atr(h, l, c, self.atr_len)[-1]
        dist = NS.stop_distance(atr, self.stop_atr)
        order = sorted(todo, key=lambda x: (x[0] != CLOSE, x[1]))        # closes first, as ema_mom
        return tuple(Signal(action=a, side=s, candle_close_ms=close_ms, stop_distance=dist if a == ENTER else None,
                            reason=ReasonCode.ENTRY_SIGNAL if a == ENTER else ReasonCode.EXIT_SIGNAL) for a, s in order)


@dataclass
class NoSignals:
    strategy: str = 'disabled@4h'
    version: str = 'v1'
    window: int = 1

    def decide(self, symbol, bars, as_of_ms):
        return ()
