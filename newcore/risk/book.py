"""Book-level risk policy (slice S4): pure state fed once per cycle with the mark-to-market equity.

    risk_pct            fixed-fractional risk per trade on the cycle's equity snapshot (1%)
    max_positions       open lots + working entries across the book (4; None = no cap)
    max_leverage        gross notional cap: open notional + the new size <= max_leverage x equity (3x)
    daily_loss_pct      Cairo-day halt (3%): MTM equity <= (1 - pct) x the day's start MTM -> no new entries for the
                        rest of that Cairo day. The day is cairo_day(decision time = candle close); it rolls BEFORE the
                        halt test, so a candle that closes after Cairo midnight belongs to the new day (C13d).
    kill_drawdown_pct   drawdown kill (10%): MTM equity <= (1 - pct) x the running peak MTM -> HOLD. Never resumes by
                        itself: only an operator resume leaves HOLD, and it restarts the peak at that equity.
MTM equity = wallet balance + the open lots' unrealized P&L at the candle closes of this cycle (the legacy rule: the
halt includes open P&L).
"""
from __future__ import annotations

from dataclasses import dataclass
from decimal import Context, Decimal

from .cairo import cairo_day

RCTX = Context(prec=34)


@dataclass(frozen=True, slots=True)
class BookPolicy:
    risk_pct: Decimal = Decimal('0.01')
    max_positions: int | None = 4
    max_leverage: Decimal = Decimal('3')
    daily_loss_pct: Decimal | None = Decimal('0.03')
    kill_drawdown_pct: Decimal | None = Decimal('0.10')


@dataclass(frozen=True, slots=True)
class RiskEvent:
    kind: str                     # 'roll' | 'halt' | 'kill'
    day: str
    equity: Decimal
    reference: Decimal            # roll: the day start; halt: the day start; kill: the peak
    change: Decimal               # equity / reference - 1


class BookRisk:
    def __init__(self, policy: BookPolicy):
        self.policy = policy
        self.day = None
        self.day_start = None
        self.peak = None
        self.halted_day = None
        self.killed = False

    def observe(self, t_ms: int, equity: Decimal) -> list:
        """Feed the MTM equity at decision time t_ms. Returns the events this observation triggers, in order."""
        out = []
        day = cairo_day(t_ms)
        if day != self.day:
            self.day, self.day_start = day, equity
            out.append(RiskEvent('roll', day, equity, equity, Decimal(0)))
        self.peak = equity if self.peak is None else max(self.peak, equity)
        p = self.policy
        if p.daily_loss_pct is not None and self.halted_day != day and self.day_start > 0:
            ch = RCTX.subtract(RCTX.divide(equity, self.day_start), 1)
            if ch <= -p.daily_loss_pct:
                self.halted_day = day
                out.append(RiskEvent('halt', day, equity, self.day_start, ch))
        if p.kill_drawdown_pct is not None and not self.killed and self.peak > 0:
            ch = RCTX.subtract(RCTX.divide(equity, self.peak), 1)
            if ch <= -p.kill_drawdown_pct:
                self.killed = True
                out.append(RiskEvent('kill', day, equity, self.peak, ch))
        return out

    def halted(self, t_ms: int) -> bool:
        return self.halted_day is not None and self.halted_day == cairo_day(t_ms)

    def resumed(self, equity: Decimal):
        """An operator resume after a kill: the drawdown is measured from here again."""
        self.killed = False
        self.peak = equity

    def at_capacity(self, open_positions: int) -> bool:
        m = self.policy.max_positions
        return m is not None and open_positions >= m
