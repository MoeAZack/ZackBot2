"""Fixed C260 development replay, requiring context-owned source authority.

The real CLI has no source override, forward or holdout path. Synthetic fixtures
remain separate and cannot authorize real reads. No PAPER qualification or
runtime adapter exists. PACK supplies adverse fill/stop-only path semantics.
"""
from __future__ import annotations

import argparse
from dataclasses import asdict, dataclass, replace
from datetime import datetime, timezone
from decimal import Decimal, ROUND_FLOOR
import json
import math
import os
from pathlib import Path

from tools.research import c260_low_idio_variance as C
from tools.research import c260_authority as AUTH
from tools.research import c260_statistics as S
from tools.research.c260_archive import (DevelopmentArchive, SyntheticArchive, SourceIncomplete,
                                        TRADE_BAR_DERIVATION, milliseconds)
from tools.research import btc_residual_leadlag_continuation_c259_stagea as X

P = C.PACK
H = C.HOUR_MS
MANAGEMENT = {'stop_atr': 2.}


@dataclass
class Lot:
    attempt_id: str
    symbol: str
    original_rank: int
    decision_ms: int
    entry_ms: int
    due_ms: int
    position: P.Position
    rule: P.ExchangeRule
    initial_risk: float
    mark: float
    costs: float
    funding: float = 0.


def _dd(values):
    peak = values[0]
    maximum = 0.
    for value in values:
        peak = max(peak, value)
        maximum = max(maximum, peak - value)
    return maximum


class DevelopmentBook:
    def __init__(self, source, *, capital: int, stress: bool = False):
        if type(source) not in (SyntheticArchive, DevelopmentArchive) or capital not in (1000, 2000, 3000, 4000):
            raise C.C260Locked('FIXED_AUTHENTICATED_SOURCE_AND_CAPITAL_ONLY')
        source._ensure_authority()
        self.source, self.capital, self.stress = source, capital, stress
        self.slippage = 10. if stress else 2.
        self.cash = float(capital)
        self.lots = {}
        self.attempts, self.events, self.closed, self.hourly = [], [], [], []
        self.equity_points = [(source.start_ms, -1, float(capital))]
        self.failures = []
        self.seen_decisions = set()

    def equity(self):
        # Open movements persist through every cost/funding phase. A new event
        # timestamp never replaces unrealized PnL with zero.
        return self.cash + math.fsum(lot.position.initial_qty * (lot.mark - lot.position.entry_reference)
                                     for lot in self.lots.values())

    def _point(self, t, phase):
        value = self.equity()
        if not math.isfinite(value):
            raise SourceIncomplete('NONFINITE_CAUSAL_EQUITY')
        self.equity_points.append((t, phase, value))

    def _qv(self, symbol, asof):
        bars = self.source.klines(symbol)
        rows = [bars.get(t) for t in range(asof - 24 * H, asof, H)]
        if any(row is None for row in rows):
            raise SourceIncomplete('MISSING_CAUSAL_IMPACT_VOLUME')
        value = math.fsum(row.quote_volume for row in rows)
        if not math.isfinite(value) or value <= 0:
            raise SourceIncomplete('NONPOSITIVE_CAUSAL_IMPACT_VOLUME')
        return value

    def _impact(self, symbol, qty, reference, asof):
        return float(X.impact_drag(Decimal(str(qty)), Decimal(str(reference)), self._qv(symbol, asof)))

    def _fill(self, symbol, qty, reference, rule, t, phase, reason):
        action = 'buy' if reason == 'entry' else 'sell'
        self._grid(reference, rule.tick)
        fill = P.make_fill((t - self.source.start_ms) // H, t, action, qty, reference, rule,
                           reason, slippage_bps=self.slippage)
        # A prior-range fill is known at the next boundary. Its fill's volume
        # information set is the beginning of that range, never its final qv.
        asof = t - H if phase == 0 else t
        impact = self._impact(symbol, qty, reference, asof)
        self._feasible(fill.qty, fill.executable_price, rule)
        return fill, impact, asof

    @staticmethod
    def _grid(value, step):
        if not math.isfinite(value) or value <= 0 or not math.isfinite(step) or step <= 0:
            raise SourceIncomplete('INVALID_FILTER_GRID')
        # Decimal filter arithmetic plus at most two binary64 ULPs for the
        # parser/product conversion. Relative tolerances could accept a whole
        # step at large quantity/price magnitudes. Reject unresolved precision.
        if step <= 2 * math.ulp(value):
            raise SourceIncomplete('BINARY64_FILTER_GRID_UNRESOLVED')
        units = (Decimal(str(value)) / Decimal(str(step))).to_integral_value()
        exact = units * Decimal(str(step))
        if abs(value - float(exact)) > 2 * math.ulp(value):
            raise SourceIncomplete('RAW_OR_EXECUTABLE_FILTER_GRID')
        return exact

    @staticmethod
    def _feasible(qty, price, rule):
        q = DevelopmentBook._grid(qty, rule.step)
        p = DevelopmentBook._grid(price, rule.tick)
        if (q < Decimal(str(rule.min_qty)) or rule.max_qty is None
                or q > Decimal(str(rule.max_qty)) or q * p < Decimal(str(rule.min_notional))):
            raise SourceIncomplete('MANDATORY_EXCHANGE_FILL_INFEASIBLE')

    def _close(self, lot, reference, t, phase, reason):
        qty = lot.position.initial_qty
        fill, impact, asof = self._fill(lot.symbol, qty, reference, lot.rule, t, phase, reason)
        costs = fill.fee_cash + fill.slippage_cash + impact
        movement = qty * (reference - lot.position.entry_reference)
        self.cash += movement - costs
        lot.position.qty = 0.
        del self.lots[lot.symbol]
        net = movement - lot.costs - costs + lot.funding
        closed = {'attempt_id': lot.attempt_id, 'symbol': lot.symbol, 'original_rank': lot.original_rank,
                  'decision_ms': lot.decision_ms, 'entry_ms': lot.entry_ms, 'exit_ms': t,
                  'due_ms': lot.due_ms, 'reason': reason, 'quantity': qty,
                  'entry_reference': lot.position.entry_reference, 'exit_reference': reference,
                  'stop_reference': lot.position.stop, 'initial_risk_cash': lot.initial_risk,
                  'gross_cash': movement, 'cost_cash': lot.costs + costs, 'funding_cash': lot.funding,
                  'net_cash': net, 'net_R': net / lot.initial_risk, 'holding_hours': (t - lot.entry_ms) / H}
        self.closed.append(closed)
        self.events.append({'time_ms': t, 'phase': phase, 'attempt_id': lot.attempt_id,
                            'kind': reason, 'fill': asdict(fill), 'impact_cash': impact,
                            'impact_asof_ms': asof, 'cash_after': self.cash})
        self._point(t, phase)

    def _funding(self, lot, t):
        rows = self.source.funding(lot.symbol)
        prior = [row for time, row in rows.items() if time < t]
        if not prior:
            raise SourceIncomplete('FUNDING_CADENCE_WARMUP')
        last = prior[-1]
        event = rows.get(t)
        interval = event.interval_hours if event else last.interval_hours
        # REST's first row has unknown cadence. A verified observed interval is
        # learned only after a past settlement, never defaulted to eight hours.
        if interval is None:
            history = prior + ([event] if event else [])
            if len(history) < 2:
                raise SourceIncomplete('FUNDING_CADENCE_UNKNOWN')
            delta = history[-1].time_ms - history[-2].time_ms
            if delta % H or not 0 < delta <= 24 * H:
                raise SourceIncomplete('FUNDING_CADENCE_INVALID')
            interval = delta // H
        if t - last.time_ms > interval * H or (t - last.time_ms == interval * H and event is None):
            raise SourceIncomplete('MISSING_REQUIRED_FUNDING_SETTLEMENT')
        if event:
            marks = self.source.marks(lot.symbol)
            # Accepted source convention: the raw mark bar opening T-H is
            # completed/available at T. Do not add another hour of latency by
            # feeding availability timestamps to a raw-open-time helper.
            mark = marks.get(t - H)
            if mark is None:
                raise SourceIncomplete('MISSING_FUNDING_COMPLETED_MARK')
            cash = X.funding_cash(1, lot.position.initial_qty, mark.close, event.rate)
            self.cash += cash
            lot.funding += cash
            self.events.append({'time_ms': t, 'phase': 1, 'attempt_id': lot.attempt_id,
                                'kind': 'funding', 'rate': event.rate, 'mark_open_ms': mark.open_ms,
                                'mark_available_ms': t, 'mark': mark.close, 'cash': cash, 'cash_after': self.cash})
            self._point(t, 1)

    def _sized(self, symbol, reference, atr, t, rule):
        self._grid(reference, rule.tick)
        equity = self.equity()
        if equity <= 0:
            return None, 'NONPOSITIVE_EQUITY'
        entry = P.executable_price(reference, 'buy', rule.tick, self.slippage)
        raw_stop = entry - 2 * atr
        # Root's pre-data execution clarification: make the inherited adverse
        # SELL direction explicit for the actual installed trigger. This is the
        # frozen binary64 raw stop rounded DOWN with Decimal filter arithmetic;
        # no additional target/factor/parameter alternative was evaluated.
        tick = Decimal(str(rule.tick))
        stop = float((Decimal(str(raw_stop)) / tick).to_integral_value(rounding=ROUND_FLOOR) * tick)
        mark = self.source.marks(symbol).get(t - H)
        if mark is None:
            raise SourceIncomplete('MISSING_ENTRY_CAUSAL_MARK')
        if not 0 < stop < entry or stop >= mark.close:
            return None, 'PROTECTION_COLLAPSED'
        stop_exec = P.executable_price(stop, 'sell', rule.tick, self.slippage)
        gross = math.fsum(l.position.initial_qty * l.mark for l in self.lots.values())
        open_risk = math.fsum(l.initial_risk for l in self.lots.values())
        risk_budget = min(.01 * equity, .03 * equity - open_risk)
        gross_budget = 1.5 * equity - gross
        if risk_budget <= 0 or gross_budget <= 0:
            return None, 'BOOK_RISK_OR_GROSS_CAP'
        # The frozen conservative initial risk uses stressed adverse ticked
        # round-trip fills and both participation impacts. Find the largest
        # floor-step quantity within risk/gross; never clip maxQty to rescue it.
        stress_entry = P.executable_price(reference, 'buy', rule.tick, 10.)
        stress_exit = P.executable_price(stop, 'sell', rule.tick, 10.)
        per_unit = (reference - stop + (stress_entry - reference) + (stop - stress_exit)
                    + (stress_entry + stress_exit) * .0005)
        if per_unit <= 0:
            return None, 'INVALID_STRESSED_RISK'
        upper = math.floor(min(risk_budget / per_unit, gross_budget / entry) / rule.step + 1e-12)
        def risk(units):
            qty = units * rule.step
            try:
                return qty * per_unit + self._impact(symbol, qty, reference, t) + self._impact(symbol, qty, stop, t)
            except X.IncompleteEpisode:
                return math.inf
        low, high = 0, upper
        while low < high:
            middle = (low + high + 1) // 2
            if risk(middle) <= risk_budget:
                low = middle
            else:
                high = middle - 1
        qty = low * rule.step
        if qty <= 0 or qty < rule.min_qty or qty * entry < rule.min_notional or qty * stop_exec < rule.min_notional:
            return None, 'MINIMUM_OR_STOP_NOTIONAL'
        if Decimal(low) * Decimal(str(rule.step)) > Decimal(str(rule.max_qty)):
            return None, 'MAX_QTY_NO_CLIPPING'
        # PACK creates the stop-only position at the accepted floor quantity.
        # Its default two-bps fill is replaced for a complete stressed replay;
        # this changes costs, stop anchor, sizing and subsequent admissions.
        pos = P.open_position(side='long', entry_index=(t - self.source.start_ms) // H, entry_time_ms=t,
                              reference_open=reference, atr_entry=atr, risk_cash=(qty + .1 * rule.step) * 2 * atr,
                              leverage_notional_cap=(qty + .1 * rule.step) * max(entry, stress_entry),
                              management=MANAGEMENT, rule=rule)
        if pos is None or not math.isclose(pos.initial_qty, qty, rel_tol=0, abs_tol=1e-10):
            raise SourceIncomplete('PACK_FLOOR_QUANTITY_PARITY')
        pos.entry_exec, pos.stop = entry, stop
        pos.best_price = entry
        self._feasible(qty, stop_exec, rule)
        return (pos, risk(low)), None

    def _admit(self, ranking, t):
        if ranking.entry_due_ms != t or t != ranking.decision_ms + H:
            raise SourceIncomplete('NONCANONICAL_ENTRY_EVENT')
        if ranking.decision_ms in self.seen_decisions:
            self.attempts.append({'decision_ms': ranking.decision_ms, 'entry_ms': t,
                                  'symbol': None, 'reason': 'DUPLICATE_DECISION_REFUSED'})
            return
        self.seen_decisions.add(ranking.decision_ms)
        for symbol, reason in ranking.skips:
            self.attempts.append({'decision_ms': ranking.decision_ms, 'entry_ms': t,
                                  'symbol': symbol, 'reason': reason, 'original_rank': None})
        if ranking.scheduled_exit_ms >= self.source.end_ms:
            self.attempts.append({'decision_ms': ranking.decision_ms, 'entry_ms': t,
                                  'symbol': None, 'reason': 'FOLD_EXIT_BOUNDARY', 'original_rank': None})
            return
        for rank, score in enumerate(ranking.ranked, 1):
            symbol = score.symbol
            attempt = {'decision_ms': ranking.decision_ms, 'entry_ms': t, 'symbol': symbol,
                       'original_rank': rank, 'score': score.residual_variance}
            self.attempts.append(attempt)
            if symbol in self.lots:
                attempt['reason'] = 'OCCUPIED'
                continue
            if len(self.lots) >= 3:
                attempt['reason'] = 'CONCURRENT_CAP'
                continue
            bars = self.source.klines(symbol)
            raw = bars.get(t)
            if raw is None:
                raise SourceIncomplete(f'MISSING_ENTRY_OPEN:{symbol}:{t}')
            history = tuple(X.Bar(b.open_ms, b.open, b.high, b.low, b.close, b.quote_volume)
                            for b in bars.values() if b.open_ms < ranking.decision_ms)
            try:
                atr = X.atr24_wilder(history, ranking.decision_ms)
            except X.IncompleteAuthority:
                attempt['reason'] = 'ATR_WARMUP_OR_MISSING_PAST'
                continue
            r = self.source.filters.rules[symbol]
            rule = P.ExchangeRule(**{key: float(r[key]) for key in ('tick', 'step', 'min_qty', 'min_notional', 'max_qty')})
            try:
                self._grid(raw.open, rule.tick)
            except SourceIncomplete:
                # The pre-read execution clarification distinguishes a known
                # pre-entry exchange infeasibility from a missing/corrupt
                # source or an infeasible mandatory exit after exposure.
                attempt['reason'] = 'ENTRY_PRICE_OFF_TICK'
                continue
            sized, reason = self._sized(symbol, raw.open, atr, t, rule)
            if sized is None:
                attempt['reason'] = reason
                continue
            pos, initial_risk = sized
            # Funding/marks must exist before taking exposure. First REST row
            # alone cannot authorize a cadence.
            funds = self.source.funding(symbol)
            past = [f for time, f in funds.items() if time <= t]
            if not past or (past[-1].interval_hours is None and len(past) < 2):
                attempt['reason'] = 'FUNDING_CADENCE_WARMUP'
                continue
            interval = past[-1].interval_hours
            if interval is None:
                delta = past[-1].time_ms - past[-2].time_ms
                if delta % H or not 0 < delta <= 24 * H:
                    raise SourceIncomplete('ENTRY_FUNDING_CADENCE_INVALID')
                interval = delta // H
            if t - past[-1].time_ms >= interval * H:
                raise SourceIncomplete('ENTRY_FUNDING_AUTHORITY_STALE')
            marks = self.source.marks(symbol)
            if t - H not in marks:
                raise SourceIncomplete('MISSING_ENTRY_CAUSAL_MARK')
            fill, impact, asof = self._fill(symbol, pos.initial_qty, raw.open, rule, t, 5, 'entry')
            costs = fill.fee_cash + fill.slippage_cash + impact
            attempt_id = f'C260:development:{self.capital}:{int(self.stress)}:{ranking.decision_ms}:{symbol}'
            lot = Lot(attempt_id, symbol, rank, ranking.decision_ms, t, ranking.scheduled_exit_ms,
                      pos, rule, initial_risk, raw.open, costs)
            self.cash -= costs
            self.lots[symbol] = lot
            attempt.update(reason='ADMITTED', attempt_id=attempt_id, quantity=pos.initial_qty,
                           initial_risk_cash=initial_risk, equity_before=self.equity() + costs,
                           atr=atr, stop=pos.stop, due_ms=lot.due_ms)
            self.events.append({'time_ms': t, 'phase': 5, 'attempt_id': attempt_id, 'kind': 'entry',
                                'fill': asdict(fill), 'impact_cash': impact, 'impact_asof_ms': asof,
                                'cash_after': self.cash})
            self._point(t, 5)
            break

    def _manage_boundary(self, t):
        # Phase zero: the prior raw bar's range becomes available at T.
        range_exits = []
        for lot in sorted(tuple(self.lots.values()), key=lambda l: (l.symbol, l.attempt_id)):
            prior = self.source.klines(lot.symbol).get(t - H)
            if prior is None:
                raise SourceIncomplete('MISSING_HELD_PRIOR_BAR')
            fills = P.walk_bar(lot.position, prior.pack(), (t - self.source.start_ms) // H - 1,
                               MANAGEMENT, lot.rule)
            if fills:
                if len(fills) != 1 or fills[0].reason != 'stop':
                    raise SourceIncomplete('UNEXPECTED_PACK_STOP_ONLY_EVENT')
                range_exits.append((lot, fills[0].reference_price))
        self._mark_phase_exposures(t, range_exits)
        for lot, reference in range_exits:
            self._close(lot, reference, t, 0, 'STOP_RANGE')
        self._point(t, 0)
        for lot in sorted(tuple(self.lots.values()), key=lambda l: (l.symbol, l.attempt_id)):
            self._funding(lot, t)
        # Gap stops precede due exits; both precede current bar H/L/C.
        for lot in sorted(tuple(self.lots.values()), key=lambda l: (l.symbol, l.attempt_id)):
            raw = self.source.klines(lot.symbol).get(t)
            if raw is None:
                raise SourceIncomplete('MISSING_HELD_RAW_OPEN')
            if raw.open <= lot.position.stop:
                self._close(lot, raw.open, t, 2, 'STOP_GAP')
        for lot in sorted(tuple(self.lots.values()), key=lambda l: (l.symbol, l.attempt_id)):
            if t == lot.due_ms:
                bars = self.source.klines(lot.symbol)
                # PACK's fixed exit sees only the due raw open. The synthetic
                # open-only view is intentional; no due bar range is inspected.
                raw = bars[t]
                prefix = [bars[x].pack() for x in range(lot.entry_ms, t, H)]
                prefix.append(P.Bar(t, raw.open, raw.open, raw.open, raw.open))
                local_position = replace(lot.position, entry_index=0, fills=list(lot.position.fills))
                fill = P.time_exit(local_position, prefix, 72, lot.rule)
                self._close(lot, fill.reference_price, t, 3, 'TIME_72H')
        self._point(t, 4)

    def run(self):
        pending = {}
        try:
            btc = self.source.klines('BTCUSDT')
            for t in range(self.source.start_ms, self.source.end_ms, H):
                self._manage_boundary(t)
                if t in pending:
                    self._admit(pending.pop(t), t)
                if t % C.DAY_MS == 0:
                    eligible = self.source.eligible(t)
                    wanted = range(t - 721 * H, t, H)
                    histories = {}
                    for symbol in eligible:
                        if symbol != 'BTCUSDT':
                            rows = self.source.klines(symbol)
                            histories[symbol] = tuple(rows[x].pack() for x in wanted if x in rows)
                    ranking = C.rank_supplied_closed_cohort(decision_ms=t, fold_start_ms=self.source.start_ms,
                        pit_filter_eligible_symbols=eligible,
                        btc_bars=tuple(btc[x].pack() for x in wanted if x in btc),
                        bars_by_symbol=histories)
                    pending[ranking.entry_due_ms] = ranking
                    self.attempts.append({'decision_ms': t, 'symbol': None, 'reason': 'DECISION',
                                          'eligible_count': len(eligible), 'ranked_count': len(ranking.ranked)})
                self._point(t, 6)
                self.hourly.append((t, self.equity()))
            if self.lots:
                raise SourceIncomplete('UNRESOLVED_EXPOSURE_AT_FOLD_END')
        except (OSError, SourceIncomplete, X.C259Error, P.RunnerError, ValueError, KeyError) as exc:
            # A missing/unreadable checksum cache is an incomplete data result,
            # including after an admission. Retain all known costs and open
            # lots so the one-shot CLI can persist its canonical failure report.
            self.failures.append(str(exc))
        return self.report()

    def _mark_phase_exposures(self, t, range_exits):
        """Mark the complete live book before recording this boundary's cash.

        An intrabar-stopped lot never acquires that bar's later close. Pending
        stop fills at the same boundary carry their known stop reference until
        each deterministic symbol-ordered fill is settled. Survivors use the
        latest completed mark bar, including during stop/funding/exit phases.
        """
        exiting = {lot.symbol: reference for lot, reference in range_exits}
        for lot in self.lots.values():
            if lot.symbol in exiting:
                lot.mark = exiting[lot.symbol]
                continue
            mark = self.source.marks(lot.symbol).get(t - H)
            if mark is None:
                raise SourceIncomplete('MISSING_HELD_CAUSAL_MARK')
            lot.mark = mark.close

    def report(self):
        net = math.fsum(row['net_cash'] for row in self.closed)
        # Incomplete lots retain already paid costs and funding. Never imply
        # their net is zero or manufacture a favorable final close.
        open_known = math.fsum(-lot.costs + lot.funding for lot in self.lots.values())
        expected = self.capital + net + open_known
        reconciled = math.isclose(self.cash, expected, rel_tol=0, abs_tol=1e-7)
        if not reconciled:
            self.failures.append('CASHFLOW_RECONCILIATION')
        closed_r = [row['net_R'] for row in self.closed]
        daily_r, daily_count = [], []
        for day in range(self.source.start_ms, self.source.end_ms, C.DAY_MS):
            values = [row['net_R'] for row in self.closed if day <= row['exit_ms'] < day + C.DAY_MS]
            daily_r.append(math.fsum(values)); daily_count.append(len(values))
        wins = math.fsum(value for value in closed_r if value > 0)
        losses = -math.fsum(value for value in closed_r if value < 0)
        return {'capital': self.capital, 'stress': self.stress, 'closed_count': len(self.closed),
                'data_complete': not self.failures, 'failures': list(self.failures), 'cash_reconciled': reconciled,
                'settled_cash': self.cash, 'known_open_cash': open_known, 'marked_equity': self.equity(),
                'net_cash_closed': net, 'net_R_closed': math.fsum(closed_r),
                'profit_factor': wins / losses if losses else None, 'gross_profit_R': wins, 'gross_loss_R': losses,
                'marked_drawdown_R': _dd([point[2] for point in self.equity_points]) / (.01 * self.capital),
                'closed_R_drawdown': _dd([0., *[math.fsum(closed_r[:i]) for i in range(1, len(closed_r) + 1)]]),
                'attempts': self.attempts, 'events': self.events, 'closed': self.closed,
                'unresolved_exposures': [{'attempt_id': lot.attempt_id, 'symbol': lot.symbol,
                                         'quantity': lot.position.initial_qty, 'entry_ms': lot.entry_ms,
                                         'known_cost_cash': lot.costs, 'known_funding_cash': lot.funding,
                                         'last_mark': lot.mark} for lot in self.lots.values()],
                'hourly_equity': self.hourly, 'phase_equity': self.equity_points,
                'daily_net_R': daily_r, 'daily_closed_count': daily_count}


def _replay_development(source) -> dict:
    source._ensure_authority()
    primary = DevelopmentBook(source, capital=2000).run()
    stress = DevelopmentBook(source, capital=2000, stress=True).run()
    inference = S.inference((S.DailyPairs('development', tuple(primary['daily_net_R']),
                                         tuple(primary['daily_closed_count'])),), scope='development')
    base_dd = max(primary['marked_drawdown_R'], primary['closed_R_drawdown'])
    stress_dd = max(stress['marked_drawdown_R'], stress['closed_R_drawdown'])
    tier = S.drawdown_tier(primary['net_R_closed'], base_dd, stress['net_R_closed'], stress_dd)
    gates = {'data_complete': primary['data_complete'] and stress['data_complete'],
             'cash_reconciled': primary['cash_reconciled'] and stress['cash_reconciled'],
             'sample': primary['closed_count'] >= 50,
             'positive_net': primary['net_cash_closed'] > 0 and primary['net_R_closed'] > 0,
             'profit_factor': (primary['profit_factor'] is not None and primary['profit_factor'] >= 1.2)
                              or (primary['gross_profit_R'] > 0 and primary['gross_loss_R'] == 0),
             'inference': inference['inference_gate_pass'],
             'stress_positive': stress['net_cash_closed'] > 0 and stress['net_R_closed'] > 0,
             'drawdown_tier': tier['pass']}
    # The forward loader does not exist in this bounded slice. The dev veto is
    # concrete, before any possible forward source read, never a success claim.
    diagnostics = [DevelopmentBook(source, capital=capital).run() for capital in (1000, 3000, 4000)]
    missing = ['forward staged runner and aggregate stitched risk report',
               'complete controls, attribution, correlation and capacity disclosures']
    if source.synthetic_only:
        missing.insert(0, 'production C260 registration/source/code/coverage receipt and persistent identity')
    verdict = ('DATA_INCOMPLETE' if not gates['data_complete'] else
               'PASS' if all(gates.values()) else 'REJECT')
    return {'format': ('zb-c260-SYNTHETIC-DEVELOPMENT-NON-EVIDENCE/1' if source.synthetic_only
                       else 'zb-c260-development-research/1'), 'candidate_id': 260,
            'proposal_sha256': C.PREREG_SHA256, 'source_digests': dict(source.digests),
            'source_reads': source.source_reads, 'synthetic_only': source.synthetic_only,
            'trade_bar_derivation': ('SYNTHETIC_NATIVE_1H_COMPONENT_FIXTURE' if source.synthetic_only
                                     else TRADE_BAR_DERIVATION),
            'period_coverage_is_not_native_trade_1h_proof': True,
            'primary': primary, 'stress': stress, 'diagnostic_books': diagnostics,
            'inference': inference, 'drawdown_tier': tier, 'development_gates': gates,
            'economic_development_verdict': verdict,
            'forward_state': 'PENDING_IMPLEMENTATION' if all(gates.values()) else 'VETOED_BEFORE_FORWARD_READ',
            'report_acceptance_complete': False,
            'missing_acceptance': missing,
            'execution_authorized': False, 'promotable': False, 'paper_qualified': False,
            'historical_global_variant_total': 'UNKNOWN', 'global_FWER_claimed': False}


def synthetic_development(root: Path) -> dict:
    source = SyntheticArchive(root)
    # Synthetic namespace only. This receipt cannot substitute for the real
    # context's common-Git one-shot claim, even if the fixture is renamed.
    identity_path = source.root / '.c260-synthetic-development-spent.json'
    with identity_path.open('x', encoding='utf-8') as handle:
        json.dump({'format': 'zb-c260-SYNTHETIC-SPEND/1', 'candidate_id': 260,
                   'proposal_sha256': C.PREREG_SHA256, 'artifacts': source.digests}, handle, sort_keys=True)
    return _replay_development(source)


def development() -> dict:
    """The single fixed real entry: verifier, durable claim, parser, same book."""
    with AUTH.development_authority() as authority:
        source = DevelopmentArchive(authority)
        report = _replay_development(source)
        report['development_authority'] = {
            'implementation_head': authority.implementation_head,
            'receipt_sha256': authority.receipt_sha256,
            'claim_sha256': authority.claim_sha256,
            'scope': 'ONE_SHOT_DEVELOPMENT_ONLY_NO_FORWARD_OR_HOLDOUT'}
        report['development_source_authorized'] = True
        # The candidate is consumed regardless of export success. Publish the
        # complete canonical record durably while the same authority is live.
        canonical = authority.claim_path.with_name('candidate-260-development.report.json')
        report['canonical_report_path'] = str(canonical)
        AUTH.validate_issued(authority)
        with canonical.open('xb') as handle:
            handle.write(_report_bytes(report))
            handle.flush()
            os.fsync(handle.fileno())
        return report


def _report_bytes(report):
    return (json.dumps(report, sort_keys=True, separators=(',', ':'), allow_nan=False) + '\n').encode('utf-8')


def _preflight_export(path):
    # Probe the exact requested target exclusively before consuming the source.
    # Remove our own empty probe before the verifier's clean-tree check. This
    # is not a reservation: the canonical claim-sibling report protects against
    # any later export race/failure without overwriting another result.
    with path.open('xb'):
        pass
    path.unlink()


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('stage', choices=('development',))
    parser.add_argument('--synthetic-root', type=Path)
    parser.add_argument('--out', type=Path, required=True)
    args = parser.parse_args(argv)
    _preflight_export(args.out)
    report = development() if args.synthetic_root is None else synthetic_development(args.synthetic_root)
    with args.out.open('xb') as handle:
        handle.write(_report_bytes(report))
        handle.flush()
        os.fsync(handle.fileno())
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
