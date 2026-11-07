"""Default-OFF C259 causal intent preparation, with no venue/runtime side effects.

Inputs are already-verified in-memory collector/activation attestations. This
module checks their content, timestamps and cross-bindings; it does not verify
external PASS receipts, install a sleeve, persist reservations or submit orders.
The native integration gap is described by ``native_integration_gap``.
"""
from __future__ import annotations

from dataclasses import dataclass, fields, is_dataclass
from datetime import datetime, timezone
from decimal import Decimal
import hashlib
import json
from typing import Mapping
from zoneinfo import ZoneInfo

from tools.research import btc_residual_leadlag_continuation_c259_stagea as E

ADAPTER_VERSION="c259-paper-intent/1"
EVALUATOR_SOURCE_HEAD="5c332bd2faf2f06591a6d621965770ba3cdaecee"
STRATEGY_NAME="c259_btc_leadlag"
STRATEGY_VERSION="v1"
TESTNET_ENDPOINT="https://testnet.binancefuture.com"
APPROVED_TESTNET_ORIGINS=(TESTNET_ENDPOINT,"https://demo-fapi.binance.com")
PAPER_RISK_R=Decimal("0.25")
PAPER_OPEN_RISK_R=Decimal("0.50")
PAPER_MAX_POSITIONS=2


class AdapterRefusal(ValueError):
    pass


def _canonical(value):
    if isinstance(value,Decimal):
        if not value.is_finite():raise AdapterRefusal("NONFINITE_DECIMAL")
        return str(value)
    if is_dataclass(value):return {item.name:_canonical(getattr(value,item.name)) for item in fields(value)}
    if isinstance(value,Mapping):return {str(key):_canonical(item) for key,item in sorted(value.items())}
    if isinstance(value,(tuple,list)):return [_canonical(item) for item in value]
    return value


def digest(value) -> str:
    return hashlib.sha256(json.dumps(_canonical(value),sort_keys=True,separators=(",", ":"),allow_nan=False).encode()).hexdigest()


def _hash(value):
    return isinstance(value,str) and len(value)==64 and set(value)<=set("0123456789abcdef") and value!="0"*64


def _positive(*values):
    return all(type(value) is Decimal and value.is_finite() and value>0 for value in values)


def _nonnegative(*values):
    return all(type(value) is Decimal and value.is_finite() and value>=0 for value in values)


def side_book_id(side: int) -> str:
    if type(side) is not int or side not in E.SIDES:raise AdapterRefusal("INVALID_SIDE")
    return f"c259:{E.SIDES[side]}"


@dataclass(frozen=True)
class AdapterConfig:
    enabled: bool=False
    version: str=ADAPTER_VERSION
    evaluator_source_head: str=EVALUATOR_SOURCE_HEAD
    risk_multiplier: Decimal=PAPER_RISK_R
    testnet_origin: str=TESTNET_ENDPOINT


@dataclass(frozen=True)
class EconomicAcceptance:
    side: int
    evidence_sha256: str
    development_pass: bool=False
    forward_pass: bool=False
    holdout_pass: bool=False
    independent_exact_head_pass: bool=False
    report_acceptance_pass: bool=False
    primary_capital_usd: int=E.PRIMARY_CAPITAL
    candidate_id: int=259
    contract_sha256: str=E.CONTRACT_SHA256
    evaluator_source_head: str=EVALUATOR_SOURCE_HEAD
    paper_risk_multiplier_cap: Decimal=PAPER_RISK_R


@dataclass(frozen=True)
class PaperEnvironment:
    account_fingerprint: str
    observed_ms: int
    generation: int
    mode: str="PAPER"
    endpoint: str=TESTNET_ENDPOINT
    live: bool=False
    confirmed_testnet: bool=False
    reconciliation_complete: bool=False


@dataclass(frozen=True)
class Mark:
    close_time_ms: int
    price: Decimal
    source_sha256: str


@dataclass(frozen=True)
class OpenPosition:
    intent_id: str
    symbol: str
    side: int
    quantity: Decimal
    entry_reference: Decimal
    initial_risk_cash: Decimal
    mark: Mark
    protection_confirmed: bool=False


@dataclass(frozen=True)
class SideBook:
    side: int
    book_id: str
    account_fingerprint: str
    observed_ms: int
    generation: int
    settled_cash: Decimal
    positions: tuple[OpenPosition,...]=()
    pending_intent_ids: tuple[str,...]=()
    reconciled: bool=False
    actual_costs_complete: bool=False
    allocated_capital: Decimal=Decimal(E.PRIMARY_CAPITAL)
    cashflow_net: Decimal=Decimal(0)
    source_sha256: str=""


@dataclass(frozen=True)
class C259SideCapacity:
    """Collector-attested open AND reserved C259 exposure, including pending claims.

    Both isolated books are required in the same account/generation snapshot.
    Risk uses each book's own marked equity; capitals are never pooled.
    """
    side: int
    book_sha256: str
    marked_equity: Decimal
    position_and_reservation_count: int
    open_and_reserved_risk_cash: Decimal


@dataclass(frozen=True)
class SharedCapacity:
    account_fingerprint: str
    observed_ms: int
    generation: int
    available_collateral_cash: Decimal
    available_gross_notional: Decimal
    additional_position_slots: int
    reservations_complete: bool=False
    c259_side_capacity: tuple[C259SideCapacity,...]=()
    c259_reservations_complete: bool=False


@dataclass(frozen=True)
class PITCell:
    monday_ms: int
    available_ms: int
    members: tuple[str,...]
    source_sha256: str


@dataclass(frozen=True)
class SymbolSnapshot:
    symbol: str
    bars: tuple[E.Bar,...]
    filters: E.Filters
    filters_observed_ms: int
    filters_sha256: str
    entry_reference: Decimal
    entry_open_ms: int
    entry_available_ms: int
    entry_mark: Mark
    market_sha256: str


@dataclass(frozen=True)
class CohortSnapshot:
    decision_ms: int
    entry_open_ms: int
    observed_ms: int
    generation: int
    segment_start_ms: int
    btc_bars: tuple[E.Bar,...]
    symbols: tuple[SymbolSnapshot,...]
    pit_cells: tuple[PITCell,...]
    btc_source_sha256: str
    funding_source_sha256: str
    funding_available_through_ms: int
    funding_complete: bool=False


@dataclass(frozen=True)
class PaperIntent:
    intent_id: str
    candidate_id: int
    adapter_version: str
    evaluator_source_head: str
    contract_sha256: str
    strategy_name: str
    strategy_version: str
    book_id: str
    account_fingerprint: str
    side: int
    symbol: str
    original_rank: int
    decision_ms: int
    decision_candle_close_ms: int
    entry_due_ms: int
    scheduled_exit_ms: int
    entry_due_cairo: str
    quantity: Decimal
    entry_reference: Decimal
    stop: Decimal
    target: Decimal
    side_book_equity: Decimal
    modeled_risk_cash: Decimal
    requested_risk_cash_ceiling: Decimal
    gross_notional: Decimal
    collateral_cash_ceiling: Decimal
    estimated_entry_drag_reserve: Decimal
    regime: str
    config_sha256: str
    inputs_sha256: str
    signal_window_sha256: str
    source_digests: tuple[tuple[str,str],...]
    requires_atomic_shared_reservation: bool=True
    execution_authorized: bool=False


@dataclass(frozen=True)
class IntentBatch:
    intents: tuple[PaperIntent,...]=()
    skips: tuple[tuple[str,str],...]=()
    rejection: str | None=None
    runtime_installed: bool=False
    activation_performed: bool=False


def _validate_bars(bars,start,entry):
    if type(bars) is not tuple or tuple(bar.open_ms for bar in bars)!=tuple(range(start,entry,E.HOUR_MS)):
        raise AdapterRefusal("INCOMPLETE_OR_NONCAUSAL_CLOSED_BAR_PREFIX")
    for bar in bars:
        if type(bar) is not E.Bar or type(bar.open_ms) is not int:raise AdapterRefusal("INVALID_BAR_TYPE")
        bar.validate()
    return {bar.open_ms:bar for bar in bars}


def _validate_mark(mark,entry):
    if (type(mark) is not Mark or mark.close_time_ms!=entry-1
            or not _positive(mark.price) or not _hash(mark.source_sha256)):
        raise AdapterRefusal("STALE_OR_UNVERIFIED_MARK")


def _gates(config,acceptance,env,cohort,book,shared,now):
    if config.enabled is not True:raise AdapterRefusal("DEFAULT_OFF")
    if (config.version!=ADAPTER_VERSION or config.evaluator_source_head!=EVALUATOR_SOURCE_HEAD
            or not _positive(config.risk_multiplier) or config.risk_multiplier>PAPER_RISK_R):
        raise AdapterRefusal("UNBOUND_ADAPTER_CONFIG")
    side_book_id(book.side)
    if (acceptance.side!=book.side or acceptance.candidate_id!=259
            or acceptance.contract_sha256!=E.CONTRACT_SHA256
            or acceptance.evaluator_source_head!=EVALUATOR_SOURCE_HEAD
            or acceptance.primary_capital_usd!=E.PRIMARY_CAPITAL
            or not _hash(acceptance.evidence_sha256)
            or not _positive(acceptance.paper_risk_multiplier_cap)
            or not all(value is True for value in (acceptance.development_pass,acceptance.forward_pass,
                acceptance.holdout_pass,acceptance.independent_exact_head_pass,acceptance.report_acceptance_pass))):
        raise AdapterRefusal("UNQUALIFIED_SIDE_ECONOMICS")
    if (config.testnet_origin not in APPROVED_TESTNET_ORIGINS or env.endpoint!=config.testnet_origin
            or env.mode!="PAPER" or env.live is not False
            or env.confirmed_testnet is not True or env.reconciliation_complete is not True
            or not _hash(env.account_fingerprint)):
        raise AdapterRefusal("UNCONFIRMED_PAPER_TESTNET_ENVIRONMENT")
    if (type(now) is not int or now%E.HOUR_MS or cohort.entry_open_ms!=now
            or cohort.decision_ms+E.HOUR_MS!=now or cohort.decision_ms%E.HOUR_MS
            or cohort.segment_start_ms%E.HOUR_MS or cohort.segment_start_ms>cohort.decision_ms):
        raise AdapterRefusal("WRONG_CANONICAL_ENTRY_CLOCK")
    if (type(cohort.generation) is not int or cohort.generation<=0
            or any(row.observed_ms!=now or row.generation!=cohort.generation for row in (cohort,env,book,shared))):
        raise AdapterRefusal("STALE_OR_MIXED_EVIDENCE_GENERATION")
    if (book.account_fingerprint!=env.account_fingerprint or shared.account_fingerprint!=env.account_fingerprint
            or book.book_id!=side_book_id(book.side) or book.reconciled is not True
            or book.actual_costs_complete is not True or book.pending_intent_ids):
        raise AdapterRefusal("SIDE_BOOK_RECONCILIATION_REQUIRED")
    if not _positive(book.settled_cash) or not _nonnegative(shared.available_collateral_cash,shared.available_gross_notional):
        raise AdapterRefusal("INVALID_BOOK_OR_SHARED_CAPITAL")
    if (book.allocated_capital!=Decimal(E.PRIMARY_CAPITAL)
            or type(book.cashflow_net) is not Decimal or not book.cashflow_net.is_finite()
            or book.settled_cash!=book.allocated_capital+book.cashflow_net or not _hash(book.source_sha256)):
        raise AdapterRefusal("UNVERIFIED_ISOLATED_PRIMARY_CAPITAL")
    if (shared.reservations_complete is not True or type(shared.additional_position_slots) is not int
            or shared.additional_position_slots<0):raise AdapterRefusal("UNCONFIRMED_SHARED_RESERVATIONS")
    if (cohort.funding_complete is not True or cohort.funding_available_through_ms<now
            or not _hash(cohort.funding_source_sha256) or not _hash(cohort.btc_source_sha256)):
        raise AdapterRefusal("INCOMPLETE_FUNDING_OR_BENCHMARK_AUTHORITY")
    if len({row.symbol for row in book.positions})!=len(book.positions):raise AdapterRefusal("DUPLICATE_BOOK_SYMBOL")
    for position in book.positions:
        if (position.side!=book.side or not _hash(position.intent_id) or position.protection_confirmed is not True
                or not _positive(position.quantity,position.entry_reference,position.initial_risk_cash)):
            raise AdapterRefusal("UNKNOWN_OR_UNPROTECTED_SIDE_POSITION")
        _validate_mark(position.mark,now)


def _candidate_capacity(book,shared,equity,open_risk):
    rows=shared.c259_side_capacity
    if (shared.c259_reservations_complete is not True or type(rows) is not tuple
            or len(rows)!=len(E.SIDES) or any(type(row) is not C259SideCapacity for row in rows)
            or {row.side for row in rows}!=set(E.SIDES)):
        raise AdapterRefusal("INCOMPLETE_ALL_C259_RESERVATIONS")
    for row in rows:
        if (type(row) is not C259SideCapacity or not _hash(row.book_sha256)
                or not _positive(row.marked_equity) or not _nonnegative(row.open_and_reserved_risk_cash)
                or type(row.position_and_reservation_count) is not int or row.position_and_reservation_count<0
                or (row.position_and_reservation_count==0)!=(row.open_and_reserved_risk_cash==0)):
            raise AdapterRefusal("INVALID_ALL_C259_RESERVATIONS")
    local=next(row for row in rows if row.side==book.side)
    if (local.book_sha256!=digest(book) or local.marked_equity!=equity
            or local.position_and_reservation_count<len(book.positions)
            or local.open_and_reserved_risk_cash<open_risk):
        raise AdapterRefusal("C259_RESERVATION_BOOK_MISMATCH")
    other_r=sum((row.open_and_reserved_risk_cash/(row.marked_equity*Decimal("0.01"))
                 for row in rows if row.side!=book.side),Decimal(0))
    return sum(row.position_and_reservation_count for row in rows),local.open_and_reserved_risk_cash,other_r


def _pit(cohort):
    d=cohort.decision_ms
    stamp=datetime.fromtimestamp(d/1000,tz=timezone.utc)
    current_monday=int(stamp.replace(hour=0,minute=0,second=0,microsecond=0).timestamp()*1000)-stamp.weekday()*24*E.HOUR_MS
    if len({cell.monday_ms for cell in cohort.pit_cells})!=len(cohort.pit_cells):raise AdapterRefusal("DUPLICATE_PIT_CELL")
    available=[cell for cell in cohort.pit_cells if cell.monday_ms<=d]
    cell=max(available,key=lambda item:item.monday_ms,default=None)
    if (cell is None or cell.monday_ms!=current_monday or not cell.monday_ms<=cell.available_ms<=d
            or type(cell.members) is not tuple or not cell.members or len(set(cell.members))!=len(cell.members)
            or len(cell.members)>40 or not _hash(cell.source_sha256)):
        raise AdapterRefusal("STALE_INCOMPLETE_OR_FUTURE_PIT_AUTHORITY")
    return cell


def _risk(sizing,q):
    side=sizing.side;entry=sizing.entry;stop=sizing.stop
    slip=Decimal(str(E.STRESS_SLIPPAGE_BPS))/10000
    fee=Decimal(str(E.FEE_BPS))/10000
    drag=abs(entry*(1+side*slip))*fee+abs(stop*(1-side*slip))*fee+(entry+stop)*slip
    return q*(abs(entry-stop)+drag)+E.impact_drag(q,entry,sizing.quote_volume_24h)+E.impact_drag(q,stop,sizing.quote_volume_24h)


def build_intents(*,cohort: CohortSnapshot,book: SideBook,acceptance: EconomicAcceptance,
                  environment: PaperEnvironment,shared: SharedCapacity,now_ms: int,
                  config: AdapterConfig=AdapterConfig()) -> IntentBatch:
    """Prepare proposals at the exact entry event; no wall-clock grace or sending.

    A native collector must bind ``now_ms`` to the actual canonical event and
    reject delayed dispatch independently. Activation must verify the supplied
    acceptance/environment attestations. The batch reserves nothing durably.
    """
    try:
        _gates(config,acceptance,environment,cohort,book,shared,now_ms)
        marked=sum((Decimal(position.side)*position.quantity*(position.mark.price-position.entry_reference)
                    for position in book.positions),Decimal(0))
        open_risk=sum((position.initial_risk_cash for position in book.positions),Decimal(0))
        candidate_count,open_risk,other_r=_candidate_capacity(book,shared,book.settled_cash+marked,open_risk)
        cell=_pit(cohort)
        btc=_validate_bars(cohort.btc_bars,cohort.segment_start_ms,now_ms)
        if cohort.decision_ms<cohort.segment_start_ms+722*E.HOUR_MS:raise AdapterRefusal("SPLIT_RESET_WARMUP")
        if len({row.symbol for row in cohort.symbols})!=len(cohort.symbols):raise AdapterRefusal("DUPLICATE_SNAPSHOT_SYMBOL")
        by_symbol={row.symbol:row for row in cohort.symbols}
        # Snapshots outside the selected PIT set never enter ranking or sizing.
        eligible=tuple(symbol for symbol in cell.members if symbol!="BTCUSDT")
        if any(symbol not in by_symbol for symbol in eligible):raise AdapterRefusal("INCOMPLETE_PIT_COHORT")
        indexes={}
        for symbol in eligible:
            snapshot=by_symbol[symbol]
            if (snapshot.entry_open_ms!=now_ms or snapshot.entry_available_ms!=now_ms
                    or snapshot.filters_observed_ms!=now_ms or not _hash(snapshot.filters_sha256)
                    or not _hash(snapshot.market_sha256) or not _positive(snapshot.entry_reference)):
                raise AdapterRefusal("STALE_OR_UNVERIFIED_SYMBOL_INPUTS")
            f=snapshot.filters
            if not _positive(f.tick,f.step,f.min_qty,f.max_qty,f.min_notional) or f.min_qty>f.max_qty:
                raise AdapterRefusal("INVALID_EXCHANGE_FILTERS")
            _validate_mark(snapshot.entry_mark,now_ms)
            indexes[symbol]=_validate_bars(snapshot.bars,cohort.segment_start_ms,now_ms)
        d=cohort.decision_ms
        prior=tuple(E.ClosePrice(t,btc[t-E.HOUR_MS].close) for t in range(d-721*E.HOUR_MS,d,E.HOUR_MS))
        fits=[]
        for symbol in eligible:
            index=indexes[symbol]
            fit=E.fit_signal(prior,tuple(E.ClosePrice(t,index[t-E.HOUR_MS].close) for t in range(d-721*E.HOUR_MS,d,E.HOUR_MS)),
                btc_decision=E.ClosePrice(d,btc[d-E.HOUR_MS].close),alt_decision=E.ClosePrice(d,index[d-E.HOUR_MS].close))
            fits.append((symbol,fit))
        regime_state=E.EMA200();regime=None
        for bar in cohort.btc_bars:
            if bar.open_ms+E.HOUR_MS<=d:regime=regime_state.update(bar)
        if regime is None:raise AdapterRefusal("INCOMPLETE_CAUSAL_REGIME")
        occupied={position.symbol for position in book.positions}
        ranked=E.select_candidates(fits,book.side,decision_close_ms=d,limit=None)
        cash=book.settled_cash
        gross=sum((position.quantity*position.mark.price for position in book.positions),Decimal(0))
        available_cash=shared.available_collateral_cash;available_gross=shared.available_gross_notional
        intents=[];skips=[]
        source_input=digest({"cohort":cohort,"book":book,"shared":shared,"environment":environment,"acceptance":acceptance})
        fit_map=dict(fits)
        for rank,symbol in enumerate(ranked,1):
            if symbol in occupied:skips.append((symbol,"ALREADY_OCCUPIED"));continue
            if candidate_count+len(intents)>=PAPER_MAX_POSITIONS or len(intents)>=shared.additional_position_slots:
                skips.append((symbol,"PAPER_POSITION_CAP"));continue
            snapshot=by_symbol[symbol];index=indexes[symbol];f=snapshot.filters
            entry=snapshot.entry_reference
            if entry%f.tick:skips.append((symbol,"ENTRY_OFF_TICK"));continue
            atr=E.atr24_wilder(snapshot.bars,d)
            try:stop,target=E.protection_prices(entry,Decimal(str(atr)),book.side,f.tick)
            except E.C259Error:
                skips.append((symbol,"PROTECTION_INFEASIBLE"));continue
            if not E.cost_admission(entry=entry,stop=stop,forecast=fit_map[symbol].forecast):
                skips.append((symbol,"COST_INFEASIBLE"));continue
            equity=cash+marked
            if equity<=0:raise AdapterRefusal("NONPOSITIVE_ISOLATED_SIDE_EQUITY")
            volumes=tuple(E.VolumePoint(t,index[t].quote_volume) for t in range(now_ms-24*E.HOUR_MS,now_ms,E.HOUR_MS))
            sized=E.size_position(side=book.side,entry=entry,stop=stop,filters=f,side_book_equity=equity,
                entry_impact_asof_ms=now_ms,entry_volume_rows=volumes)
            if sized is None:skips.append((symbol,"EXCHANGE_OR_SIZE_INFEASIBLE"));continue
            remaining_r=PAPER_OPEN_RISK_R-other_r
            # Entry drag cannot exceed the modeled entry+stop risk. Reserve that
            # upper bound while sizing so post-entry equity still respects the
            # BOTH-side 0.50R cap, including same-open fees and pending claims.
            risk_ceiling=min(equity*Decimal("0.01")*min(config.risk_multiplier,acceptance.paper_risk_multiplier_cap),
                (equity*Decimal("0.01")*remaining_r-open_risk)/(1+max(Decimal(0),remaining_r)*Decimal("0.01")))
            if risk_ceiling<=0:skips.append((symbol,"PAPER_OPEN_RISK_CAP"));continue
            q=E._floor(sized.quantity*min(Decimal(1),risk_ceiling/sized.initial_risk_cash),f.step)
            if q<f.min_qty or q>f.max_qty or min(q*entry,q*stop,q*target)<f.min_notional:
                skips.append((symbol,"ROUNDED_PAPER_EXCHANGE_INFEASIBLE"));continue
            risk=_risk(sized,q);notional=q*entry
            if risk>risk_ceiling or gross+notional>equity*Decimal("1.5"):
                skips.append((symbol,"ISOLATED_SIDE_CAP"));continue
            if not E.liquidity_admission(decision_quote_volume=index[d-E.HOUR_MS].quote_volume,
                preceding_quote_volumes=tuple(index[t].quote_volume for t in range(d-25*E.HOUR_MS,d-E.HOUR_MS,E.HOUR_MS)),
                raw_entry_notional=notional,causal_quote_volume_24h=sized.quote_volume_24h):
                skips.append((symbol,"LIQUIDITY_INFEASIBLE"));continue
            if notional>available_cash or notional>available_gross:
                skips.append((symbol,"INSUFFICIENT_SHARED_CAPACITY"));continue
            entry_fill=E.make_fill(timestamp_ms=now_ms,phase="ENTRY",symbol=symbol,side=book.side,quantity=float(q),
                reference=float(entry),movement_from=None,volume_bars=snapshot.bars)
            reserve=Decimal(str(entry_fill.fee_drag+entry_fill.slippage_drag+entry_fill.impact_drag))
            if (reserve>risk or equity<=reserve
                    or other_r+(open_risk+risk)/((equity-reserve)*Decimal("0.01"))>PAPER_OPEN_RISK_R):
                skips.append((symbol,"PAPER_OPEN_RISK_CAP"));continue
            identity=digest({"candidate":259,"adapter_version":ADAPTER_VERSION,"contract":E.CONTRACT_SHA256,
                "account":environment.account_fingerprint,"side":book.side,"decision_ms":d,"symbol":symbol})
            intent=PaperIntent(identity,259,ADAPTER_VERSION,EVALUATOR_SOURCE_HEAD,E.CONTRACT_SHA256,STRATEGY_NAME,
                STRATEGY_VERSION,book.book_id,environment.account_fingerprint,book.side,symbol,rank,d,d-1,now_ms,
                now_ms+12*E.HOUR_MS,datetime.fromtimestamp(now_ms/1000,ZoneInfo("Africa/Cairo")).isoformat(),
                q,entry,stop,target,equity,risk,risk_ceiling,notional,notional,reserve,regime,digest(config),source_input,
                fit_map[symbol].window_identity_sha256,tuple(sorted({"market":snapshot.market_sha256,"pit":cell.source_sha256,
                    "filters":snapshot.filters_sha256,"BTC":cohort.btc_source_sha256,"funding":cohort.funding_source_sha256,
                    "economic_acceptance":acceptance.evidence_sha256}.items())))
            intents.append(intent);occupied.add(symbol)
            cash-=reserve;gross+=notional;open_risk+=risk
            available_cash-=notional;available_gross-=notional
        return IntentBatch(tuple(intents),tuple(skips))
    except (AdapterRefusal,E.C259Error,KeyError,ValueError,TypeError) as exc:
        return IntentBatch(rejection=str(exc))


def native_integration_gap() -> Mapping[str,object]:
    return {"installed":False,"runtime_head":"936628a63f7f54a18e04004a9215a40fd3d3e6c4",
        "entry_source_head":"8f81b266c8315d1a7c6991cdbacba472be920b1e",
        "entry_seam":"newcore.runtime.ports.EntrySource.candidates -> EntryCandidate(DecisionKey,RiskEvaluation)",
        "decision_key_clock":"Use intent.decision_ms (on-grid availability boundary), never decision_candle_close_ms (Binance raw close is boundary-1ms).",
        "risk_gap":"Map isolated side books and mandatory same-generation all-C259 open+pending reservations into NC-06; atomically enforce two positions/0.50R across BOTH sides and preserve quantity ceiling, with the one pinned approved testnet origin.",
        "durability_seam":"newcore.runtime.journaling.JournalingActionSink.accept_entry; durable DecisionKey claim before venue work",
        "management_gap":"Provide NC-07 ManagementSource for frozen target, 12h due-open exit and C259 funding/gap/range ordering; STRAT-01 currently wires entries/protective stops only.",
        "activation_gap":"Protected economic/environment attestation verifier and installed default-OFF launcher composition are still required; this module reads no outcome/identity files and performs no activation."}


def native_decision_key_fields(intent: PaperIntent) -> Mapping[str,object]:
    """Arguments for the existing NC-01 constructor; no duplicate key/executor."""
    if (type(intent) is not PaperIntent or intent.candidate_id!=259 or intent.adapter_version!=ADAPTER_VERSION
            or intent.side not in E.SIDES or intent.decision_ms%E.HOUR_MS):
        raise AdapterRefusal("INVALID_NATIVE_DECISION_PROPOSAL")
    return {"name":intent.strategy_name,"version":intent.strategy_version,"tf":"1h","symbol":intent.symbol,
            "side":"LONG" if intent.side==1 else "SHORT","candle_close_ms":intent.decision_ms,"purpose":"entry"}
