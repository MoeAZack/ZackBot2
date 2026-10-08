"""ScriptedSignals: the TNET-01 signal source. The harness ARMS an action (enter / close, side) for a symbol; the next
decide() on a fresh closed candle fires it, keyed on that candle like any strategy decision, so the Runner's whole
decision path (keys, durability, sizing, gates) runs unchanged.

- name 'tnet_' + the 8-hex run nonce (fits keys' [a-z0-9_]{1,24}): every run has its own DecisionKeys, hence its own
  intent and client ids - a re-run never collides with an earlier run's orders on the venue.
- tf_label '1m' (any keys.TIMEFRAMES label is accepted).
- Deterministic per candle: what fired on (symbol, close) is returned again for that candle (the Runner re-derives an
  entry's stop distance from the klines after a restart, newcore/runner/runner.py _entry_distance).
- Fires only on a candle newer than every candle it has seen (never back-dates an armed action onto an old candle).
- Stop distance: {'pct': p} -> close x p / 100; {'atr': k} -> k x Wilder ATR14 of the window (needs >= 15 candles;
  fewer -> the action stays armed and `starved` counts the miss).
"""
import re
from dataclasses import dataclass, field
from decimal import Decimal

import newcore.strategy as NS
from newcore.domain import ReasonCode
from newcore.runner.signals import CLOSE, ENTER, Signal
from newcore.strategy import indicators as IND

NONCE_RE = re.compile(r'[0-9a-f]{8}')


@dataclass
class ScriptedSignals:
    nonce: str
    stop: dict
    tf_label: str = '1m'
    window: int = 30
    version: str = 'v1'
    atr_len: int = 14
    last_close: dict = field(default_factory=dict)       # symbol -> Decimal (the last closed candle's close)
    starved: int = 0

    def __post_init__(self):
        if not isinstance(self.nonce, str) or NONCE_RE.fullmatch(self.nonce) is None:
            raise ValueError('nonce must be 8 lowercase hex')
        if not isinstance(self.stop, dict) or len(self.stop) != 1 or not set(self.stop) <= {'pct', 'atr'}:
            raise ValueError("stop must be {'pct': x} or {'atr': k}")
        self.name = 'tnet_' + self.nonce
        self._armed = {}             # symbol -> [(action, side), ...]
        self._fired = {}             # (symbol, close_ms) -> tuple[Signal]
        self._newest = {}            # symbol -> newest close_ms seen

    def arm(self, symbol, action, side):
        if action not in (ENTER, CLOSE) or side not in ('LONG', 'SHORT'):
            raise ValueError(f'bad action {action!r} / side {side!r}')
        self._armed.setdefault(symbol, []).append((action, side))

    def armed(self, symbol):
        return tuple(self._armed.get(symbol, ()))

    def fired(self):
        return dict(self._fired)

    def _distance(self, bars):
        (kind, v), = self.stop.items()
        v = Decimal(v)
        if kind == 'pct':
            return bars[-1].close * v / 100
        if len(bars) < self.atr_len + 1:
            return None
        h = [float(b.high) for b in bars]
        l = [float(b.low) for b in bars]
        c = [float(b.close) for b in bars]
        return NS.stop_distance(IND.wilder_atr(h, l, c, self.atr_len)[-1], v)

    def decide(self, symbol, bars, as_of_ms):
        if not bars:
            return ()
        close_ms = bars[-1].close_ms
        key = (symbol, close_ms)
        if key in self._fired:
            return self._fired[key]
        newest = self._newest.get(symbol)
        if newest is not None and close_ms <= newest:
            return ()
        self._newest[symbol] = close_ms
        self.last_close[symbol] = bars[-1].close
        todo = self._armed.get(symbol)
        if not todo:
            return ()
        dist = None
        if any(a == ENTER for a, _ in todo):
            dist = self._distance(bars)
            if dist is None or dist <= 0:
                self.starved += 1
                return ()
        self._armed[symbol] = []
        order = sorted(todo, key=lambda x: (x[0] != CLOSE, x[1]))        # closes first, as the strategies do
        out = tuple(Signal(action=a, side=s, candle_close_ms=close_ms, stop_distance=dist if a == ENTER else None,
                           reason=ReasonCode.ENTRY_SIGNAL if a == ENTER else ReasonCode.EXIT_SIGNAL) for a, s in order)
        self._fired[key] = out
        return out
