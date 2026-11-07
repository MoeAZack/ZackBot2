"""C267 pure, default-locked risk/cash/lifecycle components.

No source, receipt, claim, entry intent, settlement fill or execution authority
is provided. Known funding and price inputs require a future own verifier.
"""
from dataclasses import dataclass
from decimal import Decimal,localcontext
from fractions import Fraction
import math

from tools.research import c267_prereg as C
from tools.research import btc_residual_leadlag_continuation_c259_stagea as E

D=Decimal
H=E.HOUR_MS
ENVELOPE_MS=15000
EXECUTION_AUTHORIZED=False


class ModelIncomplete(RuntimeError):pass


@dataclass(frozen=True)
class KnownFunding:
    symbol:str
    raw_time_ms:int
    reported_interval_hours:int
    raw_rate:D
    mark_open_ms:int
    causal_mark:D

    @property
    def event_id(self):return f'{self.symbol}:{self.raw_time_ms}'


@dataclass(frozen=True)
class EntryReserve:
    symbol:str
    side:int
    entry_ms:int
    event_ids:tuple[str,...]
    adverse_per_unit:D


def known_entry_reserve(symbol,side,entry_ms,events):
    if side not in(-1,1) or type(entry_ms)is not int or entry_ms%H or type(events)is not tuple or not events:
        raise ModelIncomplete('C267_KNOWN_ANCHORED_ENTRY_REQUIRED')
    debit=D(0);ids=[];slots=set();previous=None
    for item in events:
        if type(item)is not KnownFunding or type(item.raw_time_ms)is not int:raise ModelIncomplete('C267_TYPED_NATIVE_FUNDING_REQUIRED')
        raw=item.raw_time_ms
        if raw>entry_ms:raise ModelIncomplete('C267_FUTURE_RAW_REFUSED_BEFORE_FIELDS')
        if previous is not None and raw<=previous:raise ModelIncomplete('C267_DUPLICATE_OR_UNORDERED_KNOWN_EVENTS')
        previous=raw;slot=(raw+H//2)//H*H
        if (item.symbol!=symbol or abs(raw-slot)>ENVELOPE_MS or slot in slots
            or type(item.reported_interval_hours)is not int or not 1<=item.reported_interval_hours<=24
            or item.mark_open_ms!=raw//H*H-H or type(item.raw_rate)is not D or not item.raw_rate.is_finite()
            or type(item.causal_mark)is not D or not item.causal_mark.is_finite() or item.causal_mark<=0):
            raise ModelIncomplete('C267_RAW_SLOT_SCHEMA_OR_CAUSAL_MARK')
        slots.add(slot);ids.append(item.event_id)
        if slot-ENVELOPE_MS<=entry_ms<=slot+ENVELOPE_MS:
            debit+=max(D(0),D(side)*item.causal_mark*item.raw_rate)
    return EntryReserve(symbol,side,entry_ms,tuple(ids),debit)


def causal_volume_total(rows,asof_ms):
    rows=tuple(rows);expected=tuple(range(asof_ms-24*H,asof_ms,H))
    if (type(asof_ms)is not int or asof_ms%H or len(rows)!=24
        or tuple(r.open_ms for r in rows)!=expected
        or any(not math.isfinite(r.quote_volume) or r.quote_volume<0 for r in rows)):
        raise ModelIncomplete('C267_EXACT_24_NONNEGATIVE_NATIVE_VOLUMES_REQUIRED')
    total=math.fsum(r.quote_volume for r in rows)
    if not math.isfinite(total) or total<=0:raise ModelIncomplete('C267_ZERO_OR_INVALID_AGGREGATE_LIQUIDITY')
    return total


def liquidity_ratio_diagnostic(decision_quote_volume,prior_24):
    values=tuple(prior_24)
    if len(values)!=24 or not math.isfinite(decision_quote_volume) or decision_quote_volume<=0 or any(not math.isfinite(v) or v<0 for v in values):
        raise ModelIncomplete('C267_INVALID_DECISION_VOLUME_DIAGNOSTIC')
    median=E.statistics.median(values)
    return {'eligible_original_gate':decision_quote_volume>=median,
        'ratio':decision_quote_volume/median if median else None,'status':'DEFINED' if median else 'ZERO_BASELINE'}


def liquidity_admission(*,decision_quote_volume,preceding_quote_volumes,raw_entry_notional,causal_quote_volume_24h):
    diagnostic=liquidity_ratio_diagnostic(decision_quote_volume,preceding_quote_volumes)
    if (type(raw_entry_notional)is not D or not raw_entry_notional.is_finite() or raw_entry_notional<=0
        or not math.isfinite(causal_quote_volume_24h) or causal_quote_volume_24h<=0):
        raise ModelIncomplete('C267_INVALID_CAUSAL_LIQUIDITY_TOTAL_OR_NOTIONAL')
    return diagnostic['eligible_original_gate'] and D(str(causal_quote_volume_24h))>=100*raw_entry_notional


@dataclass(frozen=True)
class ReservedPosition:
    symbol:str
    side:int
    entry:D
    stop:D
    filters:E.Filters
    side_book_equity:D
    entry_impact_asof_ms:int
    entry_volume_rows:tuple
    quote_volume_24h:float
    reserve:EntryReserve
    quantity:D
    initial_risk_cash:D
    requested_risk_cash:D
    raw_notional:D


def size_reserved(*,symbol,side,entry,stop,filters,side_book_equity,entry_impact_asof_ms,entry_volume_rows,reserve):
    """Original single-leg floor/decrement/impact solve plus 1.0 known debit."""
    if (type(reserve)is not EntryReserve or reserve.symbol!=symbol or reserve.side!=side
        or reserve.entry_ms!=entry_impact_asof_ms or type(reserve.adverse_per_unit)is not D
        or not reserve.adverse_per_unit.is_finite() or reserve.adverse_per_unit<0):
        raise ModelIncomplete('C267_EXACT_SIDE_ENTRY_RESERVE_REQUIRED')
    values=(entry,stop,side_book_equity,filters.tick,filters.step,filters.min_qty,filters.max_qty,filters.min_notional)
    if side not in(-1,1) or any(type(v)is not D or not v.is_finite() or v<=0 for v in values):
        raise ModelIncomplete('C267_EXACT_DECIMAL_SINGLE_LEG_INPUTS')
    rows=tuple(entry_volume_rows);volume=causal_volume_total(rows,entry_impact_asof_ms)
    requested=side_book_equity*D('.01');fee=D(str(E.FEE_BPS/10000));slip=D(str(E.STRESS_SLIPPAGE_BPS/10000))
    entry_exec=entry*(1+D(side)*slip);stop_exec=stop*(1-D(side)*slip)
    base=abs(entry-stop)+abs(entry_exec)*fee+abs(stop_exec)*fee+(entry+stop)*slip
    augmented=base+reserve.adverse_per_unit  # EXACTLY1.0, same funding in stress.
    quantity=E._floor(min(requested/augmented,side_book_equity*D('.5')/entry),filters.step)
    while quantity>=filters.min_qty:
        notional=quantity*entry
        try:impact=E.impact_drag(quantity,entry,volume)+E.impact_drag(quantity,stop,volume)
        except E.IncompleteEpisode:quantity-=filters.step;continue
        risk=quantity*augmented+impact
        if (quantity<=filters.max_qty and notional>=filters.min_notional and notional<=side_book_equity*D('.5') and risk<=requested):
            return ReservedPosition(symbol,side,entry,stop,filters,side_book_equity,entry_impact_asof_ms,rows,volume,
                reserve,quantity,risk,requested,notional)
        quantity-=filters.step
    return None


@dataclass(frozen=True)
class LifecycleAssessment:
    veto:bool
    reason:str
    coverage_unknown:bool
    entry_authorized:bool=False


def assess_current_lifecycle(*,symbol,contract_identity,entry_ms,scheduled_due_ms):
    """Only pinned derived-current effective events, NEVER advance-news veto."""
    if type(entry_ms)is not int or type(scheduled_due_ms)is not int or scheduled_due_ms<entry_ms:
        raise ModelIncomplete('C267_INVALID_LIFECYCLE_CLOCK')
    for pin,event in C.load_catalog_metadata():
        if event['symbol']!=symbol or event['contract_identity']!=contract_identity:continue
        if entry_ms>=event['effective_no_new_entry_ms']:
            return LifecycleAssessment(True,'OFFICIAL_EFFECTIVE_ENTRY_RESTRICTION_MODEL',True)
        # The displayed original publication time has NO proven availability.
        # Even a horizon crossing settlement cannot use it to avoid a trade.
        return LifecycleAssessment(False,'NO_PRE_EFFECTIVE_HORIZON_VETO_WITHOUT_ASOF_PROOF',True)
    return LifecycleAssessment(False,'NO_MATCHING_KNOWN_EVENT_COVERAGE_UNKNOWN',True)


def exposed_settlement_requirement(*,symbol,contract_identity,asof_ms):
    for pin,event in C.load_catalog_metadata():
        if event['symbol']==symbol and event['contract_identity']==contract_identity and asof_ms>=event['effective_settlement_ms']:
            return 'DATA_INCOMPLETE_AUTHENTIC_SETTLEMENT_EXECUTION_AND_COST_PROOF_REQUIRED'
    return 'NO_MATCHING_REACHED_SETTLEMENT_KNOWN_COVERAGE_LIMITED'


PHASES={'INTRABAR_PRIOR_BOUNDARY':0,'FUNDING':1,'STOP_GAP':2,'TARGET_GAP':3,'SCHEDULED':4,
        'PROTECTION_FAILURE':5,'ENTRY':6,'MARK':7}


@dataclass(frozen=True)
class CashEvent:
    timestamp_ms:int
    phase:str
    symbol:str
    attempt_id:str
    sequence:int
    components:tuple[float|D,...]
    event_id:str
    raw_time_ms:int|None=None


class SideCash:
    """Small pure side-local chronological component ledger, not a book seal.

    Exact rational sums of the fixed Decimal/binary64 components reconcile
    without a Decimal accumulation dust flag or an epsilon hiding a lost fee.
    Reported cash rounds once, equivalently to the frozen fsum component sum.
    """
    def __init__(self,side,capital=2000):
        if side not in(-1,1) or capital not in(1000,2000,3000,4000):raise ModelIncomplete('C267_FIXED_SIDE_CAPITAL')
        self.side=side;self.capital=capital;self._events=[];self._ids=set();self._funding_allocations=set();self._last=None;self.positions={}
        self._cumulative=Fraction(0);self._per_attempt={}

    @property
    def events(self):return tuple(self._events)

    def append(self,event):
        if (type(event)is not CashEvent or type(event.timestamp_ms)is not int or event.phase not in PHASES
            or not event.symbol or not event.attempt_id or type(event.sequence)is not int or event.sequence<0
            or type(event.components)is not tuple or not event.components
            or event.phase=='FUNDING' and event.raw_time_ms is None
            or any(type(v)not in(float,D) or not (v.is_finite() if type(v)is D else math.isfinite(v)) for v in event.components)):
            raise ModelIncomplete('C267_INVALID_CHRONOLOGICAL_COMPONENT_EVENT')
        key=(event.timestamp_ms,PHASES[event.phase],event.symbol,event.attempt_id,event.sequence)
        if key in self._ids or self._last is not None and key<self._last:raise ModelIncomplete('C267_DUPLICATE_OR_BACKDATED_CASH_EVENT')
        if event.raw_time_ms is not None:
            if (type(event.raw_time_ms)is not int or event.raw_time_ms>event.timestamp_ms
                or event.event_id!=f'{event.symbol}:{event.raw_time_ms}'
                or (event.event_id,event.attempt_id) in self._funding_allocations):
                raise ModelIncomplete('C267_FUTURE_OR_DUPLICATE_EVENT_ATTEMPT_FUNDING')
            self._funding_allocations.add((event.event_id,event.attempt_id))
        self._ids.add(key);self._last=key;self._events.append(event)
        delta=self._exact(event.components)
        self._cumulative+=delta
        self._per_attempt[event.attempt_id]=self._per_attempt.get(event.attempt_id,Fraction(0))+delta

    @staticmethod
    def _exact(values):
        return sum((Fraction(v) for v in values),Fraction(0))

    def exact_components(self,attempt_id=None):
        return self._cumulative if attempt_id is None else self._per_attempt.get(attempt_id,Fraction(0))

    @property
    def settled_cash_exact(self):
        exact=Fraction(self.capital)+self.exact_components();denominator=exact.denominator;a=b=0
        while denominator%2==0:denominator//=2;a+=1
        while denominator%5==0:denominator//=5;b+=1
        if denominator!=1:raise ModelIncomplete('C267_NONTERMINATING_CASH_COMPONENT')
        with localcontext() as context:
            context.prec=max(28,len(str(abs(exact.numerator)))+max(a,b)+2)
            return D(exact.numerator)/D(exact.denominator)

    @property
    def settled_cash(self):return float(Fraction(self.capital)+self.exact_components())

    def reconciliation(self,*,expected_components=None):
        ids={e.attempt_id for e in self.events}
        grouped=sum((self.exact_components(identifier) for identifier in ids),Fraction(0))
        independently_recomputed=self._exact(v for event in self.events for v in event.components)
        by_attempt={}
        for event in self.events:by_attempt[event.attempt_id]=by_attempt.get(event.attempt_id,Fraction(0))+self._exact(event.components)
        return {'component_identity_reconciled':grouped==self._cumulative==independently_recomputed
                and by_attempt==self._per_attempt and len(self.events)==len(self._ids),
            'expected_component_total_reconciled':None if expected_components is None else self._exact(expected_components)==self.exact_components(),
            'source_binding_checked':expected_components is not None,
            'exact_cash':self.settled_cash_exact,
            'reported_cash':self.settled_cash,'rounding_difference_vs_grouped_reported_trade_cash':
                self.settled_cash-math.fsum([float(self.capital),*(float(self.exact_components(identifier)) for identifier in sorted(ids))])}

    def mark_equity(self,marks):
        # Positions are an owning book's persistent open exposures. A phase
        # with no new mark never silently drops old unrealized exposure.
        if set(marks)!=set(self.positions):raise ModelIncomplete('C267_EXACT_OWN_SIDE_OPEN_MARKS_REQUIRED')
        components=[float(self.capital),*(v for event in self.events for v in event.components)]
        for symbol,(quantity,reference) in self.positions.items():
            mark=marks[symbol]
            if any(type(v)is not D or not v.is_finite() or v<=0 for v in(quantity,reference,mark)):
                raise ModelIncomplete('C267_FINITE_NATIVE_SIDE_EXPOSURE_MARK')
            components.append(self.side*float(quantity)*float(mark-reference))
        return math.fsum(components)
