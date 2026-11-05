"""STRAT-01 risk context for PAPER and TESTNET runs, built from the account's own NC-02 journal (read-only).

``JournalRiskContext`` implements ``RiskContextProvider``.  It reads the runtime's own live journal through the
read-only ``JournalView`` that ``paper_run`` hands an entries factory as ``journal`` (``read``/``state``/
``last_sequence`` only; no append, no close).  The cycle hook calls ``candidates`` on the account's runner thread,
which is also the journal's writer, so every read is consistent without a lock.  No view (before boot, or a factory
called without one) gives no context: fail closed, and the strategy source re-offers the key later.

From the journal it takes:

* the CONFIRMED binding (``JournalProjection.load_journal``).  It must match the provider's ``environment``: SIM for
  ``paper`` (the default), TESTNET for ``testnet``.  MAINNET is never accepted.  On testnet the operator's equity is a
  capital cap of at most $2,000 (the owner's testnet bound), not a venue balance; the venue rejects what the real
  balance cannot margin;
* the booked positions per (symbol, side), priced at the current mark.  Risk per unit is (mark - stop) for a LONG
  (stop - mark for a SHORT) using the worst WORKING stop, only when WORKING stops cover the whole quantity.  A stop
  that is unsent, unacknowledged, being cancelled, unreadable or already past the mark counts as no stop, and an
  unprotected position counts its whole notional as risk, so it blocks new risk instead of hiding it;
* the owning slots: the quantity is split over the ENTRY intents that executed on that (symbol, side), newest first
  (the slot is the decision-key strategy ``name@tf``, journaled as the intent's ``slot_id``); what no executed entry
  explains is booked as ``manual``;
* any live (non-terminal) ENTRY intent: no context while one is in flight (the sink would refuse ``entry_busy``).

Generations: account and portfolio = the journal's last sequence; policy and filters = 1 (static for the run).

Equity is the operator's PAPER capital (``equity``).  The PAPER venue keeps no balance, so neither realised nor
unrealised P&L is applied: caps stay sized on starting capital while the book is underwater (known gap); the daily loss breaker (#295) is NC-06 policy, not this module.  Filters come from a pinned Binance USD-M
``exchangeInfo`` snapshot (MARKET_LOT_SIZE for quantity, PRICE_FILTER tick, MIN_NOTIONAL), with ``max_notional`` and
``max_leverage`` from the operator bounds.  Everything is Decimal.
"""
from __future__ import annotations

import json
from collections import Counter
from dataclasses import dataclass
from decimal import Decimal, InvalidOperation
from typing import Callable

from newcore import risk as nc06
from newcore.domain import Purpose
from newcore.domain.orders import IntentState
from newcore.domain.events import DecisionRecorded, IntentRecorded, ResultObserved
from newcore.domain.account import Environment as BindingEnvironment
from newcore.ports.keys import strategy_instance
from newcore.runtime.projection import JournalProjection

from newcore_strategies.entry_source import RiskContext, StrategySlot

ZERO = Decimal('0')
ONE = Decimal('1')
MAX_LEVERAGE = Decimal('3')
MAX_TESTNET_CAPITAL = Decimal('2000')       # owner bound for the testnet run: <= $2k aggregate
ENVIRONMENTS = {'paper': BindingEnvironment.SIM, 'testnet': BindingEnvironment.TESTNET}


class RiskSetupError(ValueError):
    def __init__(self, code: str):
        super().__init__(code)
        self.code = code


def _dec(value, code: str, *, positive=True, at_most: Decimal | None = None) -> Decimal:
    try:
        d = value if type(value) is Decimal else Decimal(str(value))
    except (InvalidOperation, ValueError, TypeError):
        raise RiskSetupError(code) from None
    if not d.is_finite() or (positive and d <= 0) or d < 0 or (at_most is not None and d > at_most):
        raise RiskSetupError(code)
    return d


# ---------------------------------------------------------------------------------------------------- filters
def filters_from_exchange_info(raw: dict, symbols, *, max_notional: Decimal, max_leverage: Decimal,
                               filter_id: str = 'binance_um_exchangeinfo') -> dict[str, nc06.VenueRiskFilters]:
    """VenueRiskFilters per symbol from a Binance USD-M exchangeInfo payload.  A symbol that is missing, not TRADING, or
    lacks PRICE_FILTER / MARKET_LOT_SIZE / MIN_NOTIONAL is left out (no filters = no entry)."""
    max_notional = _dec(max_notional, 'max_notional_invalid')
    max_leverage = _dec(max_leverage, 'max_leverage_invalid', at_most=MAX_LEVERAGE)
    if max_leverage < ONE:
        raise RiskSetupError('max_leverage_invalid')
    rows = {r.get('symbol'): r for r in (raw.get('symbols') or []) if isinstance(r, dict)}
    out = {}
    for symbol in symbols:
        r = rows.get(symbol)
        if r is None or r.get('status') != 'TRADING' or r.get('contractType') != 'PERPETUAL':
            continue
        f = {x.get('filterType'): x for x in r.get('filters') or [] if isinstance(x, dict)}
        try:
            tick = _dec(f['PRICE_FILTER']['tickSize'], 'tick')
            lot = f['MARKET_LOT_SIZE']
            step, min_q, max_q = _dec(lot['stepSize'], 'step'), _dec(lot['minQty'], 'minq'), _dec(lot['maxQty'], 'maxq')
            min_notional = _dec(f['MIN_NOTIONAL']['notional'], 'minn')
        except (KeyError, TypeError, RiskSetupError):
            continue
        out[symbol] = nc06.VenueRiskFilters(
            filter_id=filter_id, symbol=symbol, generation=1, fresh=True, verified=True, step_size=step,
            tick_size=tick, min_quantity=min_q, max_quantity=max_q, min_notional=min_notional,
            max_notional=max_notional, max_leverage=max_leverage)
    return out


def load_exchange_info(path: str) -> dict:
    with open(path, encoding='utf-8') as handle:
        raw = json.load(handle)
    if not isinstance(raw, dict) or not isinstance(raw.get('symbols'), list):
        raise RiskSetupError('exchange_info_invalid')
    return raw


# ---------------------------------------------------------------------------------------------------- policy
@dataclass(frozen=True, slots=True)
class PaperPolicy:
    """Account-level NC-06 ceilings for a PAPER strategy run (fractions of equity)."""
    max_trade_risk_fraction: Decimal = Decimal('0.01')
    max_portfolio_risk_fraction: Decimal = Decimal('0.03')
    max_symbol_side_risk_fraction: Decimal = Decimal('0.01')
    max_gross_multiple: Decimal = Decimal('3')
    max_net_multiple: Decimal = Decimal('2')
    max_symbol_fraction: Decimal = Decimal('1')
    max_symbol_side_fraction: Decimal = Decimal('1')
    max_leverage: Decimal = MAX_LEVERAGE
    slot_risk_fraction: Decimal = Decimal('0.02')      # per (strategy, book) open risk
    slot_notional_fraction: Decimal = Decimal('1')
    correlation_fraction: Decimal = Decimal('1')       # one 'crypto' bucket for the whole universe

    def __post_init__(self):
        for name in ('max_trade_risk_fraction', 'max_portfolio_risk_fraction', 'max_symbol_side_risk_fraction',
                     'max_symbol_fraction', 'max_symbol_side_fraction', 'slot_risk_fraction',
                     'slot_notional_fraction', 'correlation_fraction'):
            _dec(getattr(self, name), f'{name}_invalid', at_most=ONE)
            if type(getattr(self, name)) is not Decimal:
                raise RiskSetupError(f'{name}_invalid')
        if type(self.max_leverage) is not Decimal or not ONE <= self.max_leverage <= MAX_LEVERAGE:
            raise RiskSetupError('max_leverage_invalid')
        for name in ('max_gross_multiple', 'max_net_multiple'):         # never more exposure than the leverage cap
            if type(getattr(self, name)) is not Decimal:
                raise RiskSetupError(f'{name}_invalid')
            _dec(getattr(self, name), f'{name}_invalid', at_most=self.max_leverage)

    def snapshot(self, account_id: str, equity: Decimal, slots, symbols,
                 environment: nc06.Environment = nc06.Environment.PAPER) -> nc06.RiskPolicySnapshot:
        books = sorted({(s.strategy_id, s.book_id) for s in slots})
        return nc06.RiskPolicySnapshot(
            policy_id=f'strat01_{environment.value}_v1', account_id=account_id, environment=environment, generation=1,
            fresh=True, capital_cap=equity, max_trade_risk_fraction=self.max_trade_risk_fraction,
            max_portfolio_risk_fraction=self.max_portfolio_risk_fraction,
            max_symbol_side_risk_fraction=self.max_symbol_side_risk_fraction,
            max_gross_multiple=self.max_gross_multiple, max_net_multiple=self.max_net_multiple,
            max_symbol_fraction=self.max_symbol_fraction, max_symbol_side_fraction=self.max_symbol_side_fraction,
            max_leverage=self.max_leverage,
            allocations=tuple(nc06.Allocation(strategy_id=s, book_id=b, notional_fraction=self.slot_notional_fraction,
                                              risk_fraction=self.slot_risk_fraction) for s, b in books),
            correlation_map=tuple(nc06.CorrelationAssignment(symbol=x, bucket='crypto') for x in sorted(symbols)),
            correlation_caps=(nc06.CorrelationCap(bucket='crypto', exposure_fraction=self.correlation_fraction),))


# ---------------------------------------------------------------------------------------------------- journal
class JournalRiskContext:
    """``RiskContextProvider`` for one PAPER account.  ``journal_reader()`` returns the runtime's read-only live
    ``JournalView`` (``paper_run`` passes it to the entries factory as ``journal``) or None before boot;
    ``marks(symbol)`` the current mark (Decimal) or raises."""

    def __init__(self, *, account_id: str, journal_reader: Callable[[], object], marks: Callable[[str], Decimal],
                 equity: Decimal, filters: dict, slots, policy: PaperPolicy = PaperPolicy(), leverage: Decimal,
                 environment: str = 'paper'):
        if environment not in ENVIRONMENTS:
            raise RiskSetupError('environment_invalid')        # never mainnet
        self.environment = nc06.Environment(environment)
        self._binding_env = ENVIRONMENTS[environment]
        self.account_id = account_id
        self._reader, self._marks = journal_reader, marks
        self.equity = _dec(equity, 'equity_invalid')
        if type(equity) is not Decimal:
            raise RiskSetupError('equity_invalid')
        if self.environment is nc06.Environment.TESTNET and self.equity > MAX_TESTNET_CAPITAL:
            raise RiskSetupError('equity_above_testnet_cap')
        self._leverage = _dec(leverage, 'leverage_invalid', at_most=policy.max_leverage)
        self._filters = dict(filters)
        self._slots = tuple(slots)
        if not self._slots or any(type(s) is not StrategySlot for s in self._slots):
            raise RiskSetupError('slots_invalid')
        self._books = {strategy_instance(s.name, s.tf): (s.strategy_id, s.book_id) for s in self._slots}
        symbols = sorted({x for s in self._slots for x in s.symbols})
        self._policy = policy.snapshot(account_id, self.equity, self._slots, symbols, self.environment)
        self._projection = JournalProjection(label='paper')
        self.reasons: Counter = Counter()

    def _refuse(self, code: str) -> None:
        self.reasons[code] += 1
        return None

    def risk_context(self, account_id: str, slot: StrategySlot, symbol: str) -> RiskContext | None:
        try:
            return self._context(account_id, slot, symbol)
        except Exception as ex:                                  # never raise into the strategy source
            return self._refuse(f'error:{type(ex).__name__}')

    @staticmethod
    def _entry_owners(journal) -> dict[tuple[str, str], list[tuple[str, Decimal]]]:
        """(symbol, side) -> [(slot_id, executed qty)] of the ENTRY intents that executed something, oldest first.
        Terminal intents leave the live state, so this reads the events."""
        entries, executed = {}, {}
        for ev in journal.read():
            if isinstance(ev, IntentRecorded):
                intents = (ev.intent,)
            elif isinstance(ev, DecisionRecorded):
                intents = ev.decision.intents
            elif isinstance(ev, ResultObserved):
                q = ev.result.executed_qty or ZERO
                if q > 0:
                    executed[ev.result.intent_id] = max(q, executed.get(ev.result.intent_id, ZERO))
                continue
            else:
                continue
            for it in intents:
                if it.purpose is Purpose.ENTRY and it.intent_id not in entries:
                    entries[it.intent_id] = (ev.sequence, it.symbol, it.side.value, it.slot_id)
        owners: dict[tuple[str, str], list] = {}
        for intent_id, (seq, sym, side, slot_id) in sorted(entries.items(), key=lambda kv: kv[1][0]):
            if intent_id in executed:
                owners.setdefault((sym, side), []).append((slot_id, executed[intent_id]))
        return owners

    def _split(self, qty: Decimal, lots) -> list[tuple[tuple[str, str], Decimal]]:
        """Splits a booked quantity over its owning slots, newest entry first (older lots are assumed closed
        first); whatever no executed entry explains is ``manual``."""
        out, left = {}, qty
        for slot_id, q in reversed(lots or []):
            if left <= 0:
                break
            take = min(q, left)
            book = self._books.get(slot_id, ('manual', 'manual'))
            out[book] = out.get(book, ZERO) + take
            left -= take
        if left > 0:
            out[('manual', 'manual')] = out.get(('manual', 'manual'), ZERO) + left
        return list(out.items())

    def _context(self, account_id, slot, symbol):
        if account_id != self.account_id:
            return self._refuse('account_mismatch')
        filters = self._filters.get(symbol)
        if filters is None:
            return self._refuse('filters_missing')
        journal = self._reader()
        if journal is None:
            return self._refuse('journal_unreadable')
        try:
            durable = self._projection.load_journal(account_id, journal)
        except ValueError as ex:
            return self._refuse(f'journal_{ex}')
        if durable.account.binding.environment is not self._binding_env:
            return self._refuse(f'binding_not_{self.environment.value}')
        state = journal.state()
        generation = state.last_sequence
        if type(generation) is not int or generation < 1:
            return self._refuse('journal_empty')
        stops: dict[tuple[str, str], list] = {}
        for view in state.intents:                       # live intents only
            it = view.intent
            if it.purpose is Purpose.ENTRY:
                return self._refuse('entry_in_flight')
            # Only an acknowledged, resting stop protects: a stop being cancelled, unread, unsent or unacknowledged
            # counts as no stop at all.
            if (it.purpose is Purpose.PROTECT and it.stop_price is not None and view.state is IntentState.WORKING
                    and view.final_result is None):
                stops.setdefault((it.symbol, it.side.value), []).append((it.stop_price, it.qty))
        owners = self._entry_owners(journal)
        exposures, used_margin = [], ZERO
        for pos in durable.positions:
            sym, side, qty = pos.symbol, pos.side.value, pos.qty
            mark = self._marks(sym)
            if type(mark) is not Decimal or not mark.is_finite() or mark <= 0:
                return self._refuse('mark_missing')
            notional = qty * mark
            per_unit = mark                               # unprotected: the whole notional is at risk
            live = stops.get((sym, side), [])
            protected = sum((q for _, q in live), ZERO)
            if live and protected >= qty:
                worst = min(p for p, _ in live) if side == 'LONG' else max(p for p, _ in live)
                gap = (mark - worst) if side == 'LONG' else (worst - mark)
                if gap > 0:                               # a stop past the mark has failed: count it as no stop
                    per_unit = gap
            for (strategy_id, book_id), q in self._split(qty, owners.get((sym, side))):
                exposures.append(nc06.Exposure(symbol=sym, side=nc06.PositionSide(side), strategy_id=strategy_id,
                                               book_id=book_id, quantity=q, notional=q * mark, risk=q * per_unit))
            used_margin += notional / self._leverage
        account = nc06.AccountRiskSnapshot(
            account_id=account_id, environment=self.environment, generation=generation, confirmed=True,
            fresh=True, hedge_mode=durable.account.hedge_mode, equity=self.equity,
            available_margin=max(ZERO, self.equity - used_margin), reserved_capital=ZERO)
        portfolio = nc06.PortfolioRiskSnapshot(account_id=account_id, generation=generation, fresh=True,
                                               exposures=tuple(exposures), reservations=())
        self.reasons['ok'] += 1
        return RiskContext(account=account, portfolio=portfolio, policy=self._policy, filters=filters)


__all__ = ['JournalRiskContext', 'PaperPolicy', 'RiskSetupError', 'filters_from_exchange_info', 'load_exchange_info']
