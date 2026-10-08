"""BookRunner (slice S4): ONE book across symbols on shared equity, with the book risk policy (newcore.risk).

Same loop as Runner (sync, reconcile, protect, decide, reconcile + invariants); only the decide phase differs:
  1. read the closed candle of every symbol; MTM equity = wallet + open lots at these closes;
  2. risk.observe(decision time, MTM): Cairo-day roll, daily-loss halt, drawdown kill (each journaled, see below);
  3. every CLOSE signal of every symbol, in the configured symbol order (risk is reduced before any is opened);
  4. ONE equity snapshot (the wallet after those closes): every entry of this cycle is sized on it;
  5. every ENTER signal in the configured symbol order (deterministic priority; the research book's order), each
     gated by: entries mode, one lot per symbol/side, working entry, Cairo-day halt, max_positions, then sized with
     the 3x cap on open notional at this cycle's closes.
Journal: a halt is a WAIT decision (authority RISK, reason filter.halt) - durable, so a restart on the same Cairo day
stays halted; a kill is a HALT decision + ModeChanged -> HOLD naming it; HOLD is left only by an operator resume.
A restart re-measures the day start and the peak from the first cycle after it (no NC-01 record holds them: S3 /
NC-02 snapshot item).
"""
from __future__ import annotations

import dataclasses
from decimal import Decimal

from newcore.domain import Action, Authority, EntriesMode, HoldKind, ModeChanged, ReasonCode
from newcore.ports.venue import ReadKind
from newcore.risk import BookPolicy, BookRisk, cairo_day

from . import ids
from .runner import Runner
from .signals import CLOSE, ENTER
from .sizing import SizingPolicy

ZERO = Decimal(0)
KILL_REASON = ReasonCode.FILTER_HALT          # no drawdown-kill code in the NC-01 registry yet (reported)


class BookRunner(Runner):
    def __init__(self, config, *, policy: BookPolicy = BookPolicy(), **kw):
        config = dataclasses.replace(config, sizing=SizingPolicy(policy.risk_pct, policy.max_leverage,
                                                                 policy.cap_gap_buffer))
        super().__init__(config, **kw)
        self.policy = policy
        self.risk = BookRisk(policy)
        self._eq_snapshot = None
        self._close = {}
        self.events = []                                  # RiskEvents of this process (roll / halt / kill)
        halts = [d.at_ms for d in self.fold.decisions.values()
                 if d.action is Action.WAIT and d.reason is ReasonCode.FILTER_HALT]
        if halts:
            self.risk.halted_day = cairo_day(max(halts))  # restart: the durable halt of the day
        kills = [d for d in self.fold.decisions.values() if d.action is Action.HALT]
        self.risk.killed = bool(kills) and self.fold.mode is EntriesMode.HOLD

    # ----------------------------------------------------------------------------------------------- decide
    def _decide_all(self):
        bars = {s: self._bars_now(s) for s in self.cfg.symbols}
        for s, b in bars.items():
            if b is not None:
                self._close[s] = b[-1].close
        self._observe_risk()
        sigs = {s: (self.signals.decide(s, b, self.now) if b is not None else ()) for s, b in bars.items()}
        for s in self.cfg.symbols:
            for sig in sigs[s]:
                if sig.action == CLOSE and sig.side in self.cfg.sides:
                    self._exit(s, sig)
        self._eq_snapshot = self._cycle_snapshot()
        try:
            for s in self.cfg.symbols:
                for sig in sigs[s]:
                    if sig.action == ENTER and sig.side in self.cfg.sides:
                        self._enter(s, sig, bars[s][-1].close)
            snap = self._eq_snapshot
            self._check_cap_at_fill(snap.value[0] if snap.kind is ReadKind.OK else None)
        finally:
            self._eq_snapshot = None

    def _cycle_snapshot(self):
        """The wallet after this cycle's closes, before any of its entries: restart-stable, because the fees of entries
        this cycle already filled (before a crash) are added back from the venue's fills."""
        eq = self.reads.equity()
        if eq.kind is not ReadKind.OK:
            return eq
        back = ZERO
        for iv in self.fold.intents.values():
            if iv.purpose.value == 'entry' and iv.intent.created_at_ms == self.now and iv.final is not None \
                    and iv.executed > 0:
                back += self._fee(iv.intent.symbol, iv.final.exchange_order_id)
        return dataclasses.replace(eq, value=(eq.value[0] + back,)) if back else eq

    def mtm_equity(self):
        eq = self.reads.equity()
        if eq.kind is not ReadKind.OK:
            return None
        u = ZERO
        for lot in self.fold.open_lots():
            px = self._close.get(lot.symbol)
            if px is not None:
                u += (px - lot.avg_price) * lot.qty * (1 if lot.side == 'LONG' else -1)
        return eq.value[0] + u

    def _observe_risk(self):
        mtm = self.mtm_equity()
        if mtm is None:
            return
        for ev in self.risk.observe(self.now, mtm):
            self.events.append(ev)
            if ev.kind == 'halt':
                self._decision(decision_id=ids.risk_decision_id(self.acct, 'halt', self.now), action=Action.WAIT,
                               reason=ReasonCode.FILTER_HALT, authority=Authority.RISK, key=None, symbol=None,
                               side=None, detail=f'cairo day {ev.day} loss {ev.change:.6f}')
            elif ev.kind == 'kill':
                did = ids.risk_decision_id(self.acct, 'kill', self.now)
                self._decision(decision_id=did, action=Action.HALT, reason=KILL_REASON, authority=Authority.RISK,
                               key=None, symbol=None, side=None, detail=f'drawdown kill {ev.change:.6f} from peak')
                if self.fold.mode is not EntriesMode.HOLD:
                    self.counters.holds += 1
                    self._emit(ModeChanged, reason=KILL_REASON, from_mode=self.fold.mode, to_mode=EntriesMode.HOLD,
                               reasons=(KILL_REASON,), from_hold=self.fold.hold, to_hold=HoldKind.NORMAL,
                               decision_id=did, reconciliation_id=None)

    # ----------------------------------------------------------------------------------------------- gates / sizing
    def _entry_gate(self, symbol, side):
        if self.risk.killed and self.fold.mode is EntriesMode.HOLD:
            return KILL_REASON
        g = super()._entry_gate(symbol, side)
        if g is not None:
            return g
        if self.risk.halted(self.now):
            return ReasonCode.FILTER_HALT
        n = len(self.fold.open_lots()) + sum(1 for iv in self.fold.live_intents() if iv.purpose.value == 'entry')
        if self.risk.at_capacity(n):
            return ReasonCode.CAPACITY_MAX_POSITIONS
        return None

    def _sizing_equity(self):
        return self._eq_snapshot if self._eq_snapshot is not None else self.reads.equity()

    def _open_notional(self):
        """Open notional at this cycle's closes; a lot filled in THIS cycle counts at its fill (a gapped open)."""
        return sum((lot.qty * (lot.avg_price if lot.opened_at_ms == self.now else
                               self._close.get(lot.symbol, lot.avg_price)) for lot in self.fold.open_lots()), ZERO)

    def _check_cap_at_fill(self, equity):
        """Post-fill check: a gap beyond cap_gap_buffer can still push the book past the cap; surface it."""
        if equity is None or not any(lot.opened_at_ms == self.now for lot in self.fold.open_lots()):
            return                                         # no fill this cycle: later price drift is not a fill breach
        gross = self._open_notional()
        if gross > self.policy.max_leverage * equity:
            self.counters.cap_exceeded += 1
            self._incident(f'gross notional {gross} > {self.policy.max_leverage} x equity {equity} at the fill')

    # ----------------------------------------------------------------------------------------------- operator
    def resume(self, now_ms):
        ok = super().resume(now_ms)
        if ok:
            mtm = self.mtm_equity()
            if mtm is not None:
                self.risk.resumed(mtm)
        return ok
