"""Own267 raw-available funding observations/holding proof, no source authority."""
from dataclasses import dataclass
from decimal import Decimal as D
from tools.research import c267_execution_model as K

H,E=K.H,K.ENVELOPE_MS


@dataclass(frozen=True)
class Event:
    symbol:str
    raw_time_ms:int
    nominal_slot_ms:int
    reported_interval_hours:int
    raw_rate:D
    @property
    def event_id(self):return f'{self.symbol}:{self.raw_time_ms}'
    @property
    def lower_ms(self):return self.nominal_slot_ms-E
    @property
    def upper_ms(self):return self.nominal_slot_ms+E
    @property
    def mark_open_ms(self):return self.raw_time_ms//H*H-H


def observed_event(symbol,raw,row):
    if (type(raw)is not int or row.time_ms!=raw or type(row.interval_hours)is not int or not 1<=row.interval_hours<=24
        or type(row.rate)is not D or not row.rate.is_finite()):raise K.ModelIncomplete('C267_INVALID_RAW_FUNDING_SCHEMA')
    slot=(raw+H//2)//H*H
    if abs(raw-slot)>E:raise K.ModelIncomplete('C267_RAW_FUNDING_OUTSIDE_15S_MODEL')
    return Event(symbol,raw,slot,row.interval_hours,row.rate)


@dataclass(frozen=True)
class StagedAnchor:
    symbol:str
    entry_ms:int
    anchor:Event
    inspected:tuple
    ignored_pre_anchor_raw_times:tuple


def stage_first_entry(symbol,t,rows):
    if type(t)is not int or t%H:raise K.ModelIncomplete('C267_CANONICAL_FIRST_ENTRY_REQUIRED')
    visible=[];previous=None
    for raw,row in rows.items():
        if type(raw)is not int or previous is not None and raw<=previous:raise K.ModelIncomplete('C267_UNORDERED_RAW_FUNDING')
        if raw>t:break
        if (row.time_ms!=raw or type(row.interval_hours)is not int or not 1<=row.interval_hours<=24
            or type(row.rate)is not D or not row.rate.is_finite()):raise K.ModelIncomplete('C267_INVALID_VISIBLE_FUNDING_SCHEMA')
        visible.append((raw,row));previous=raw
    if not visible:raise K.ModelIncomplete('C267_MISSING_FIRST_ACTUAL_FUNDING_ANCHOR')
    latest=visible[-1][0];latest_slot=(latest+H//2)//H*H;items=[];slots=set()
    for raw,row in visible:
        slot=(raw+H//2)//H*H
        if slot!=latest_slot and not slot-E<=t<=slot+E:continue
        item=observed_event(symbol,raw,row)
        if slot in slots:raise K.ModelIncomplete('C267_FIRST_ANCHOR_SLOT_COLLISION')
        slots.add(slot);items.append(item)
    return StagedAnchor(symbol,t,next(v for v in items if v.raw_time_ms==latest),tuple(items),tuple(raw for raw,_ in visible if raw<latest))


class Observer:
    def __init__(self):self.events={};self.cadence_diagnostics=[]
    def commit(self,stage,*,actual_entry_ms,previous_actual_symbol):
        if (type(stage)is not StagedAnchor or stage.entry_ms!=actual_entry_ms or previous_actual_symbol or self.events.get(stage.symbol)):
            raise K.ModelIncomplete('C267_FIRST_ACTUAL_ANCHOR_NEVER_RESET')
        self.events[stage.symbol]=[stage.anchor]
    def observe(self,symbol,rows,t,*,prove):
        current=self.events.get(symbol)
        if not current:raise K.ModelIncomplete('C267_ACTUAL_SYMBOL_ANCHOR_REQUIRED')
        last=current[-1];previous=None
        for raw,row in rows.items():
            if type(raw)is not int or previous is not None and raw<=previous:raise K.ModelIncomplete('C267_UNORDERED_RAW_FUNDING')
            previous=raw
            if raw>t:break
            if raw<=last.raw_time_ms:continue
            item=observed_event(symbol,raw,row);delta=item.nominal_slot_ms-last.nominal_slot_ms
            if delta<=0:raise K.ModelIncomplete('C267_NOMINAL_FUNDING_SLOT_COLLISION')
            proofs=prove(symbol,last.raw_time_ms,raw)
            if not proofs:raise K.ModelIncomplete('C267_PUBLISHED_FUNDING_PROOF_REQUIRED')
            if delta!=item.reported_interval_hours*H:
                self.cadence_diagnostics.append({'symbol':symbol,'raw_time_ms':raw,'reported_interval_hours':item.reported_interval_hours,
                    'observed_nominal_delta_ms':delta,'period_semantics_assumed':False,'proof_paths':tuple(p.path for p in proofs)})
            current.append(item);last=item
        return tuple(current)


def reserve_events(events,marks):
    result=[]
    for item in events:
        mark=marks.get(item.mark_open_ms)
        if mark is None:raise K.ModelIncomplete('C267_MISSING_RAW_CAUSAL_FUNDING_MARK')
        result.append(K.KnownFunding(item.symbol,item.raw_time_ms,item.reported_interval_hours,item.raw_rate,item.mark_open_ms,mark.close))
    return tuple(result)


def eligibility(item,*,side,entry_ms,earliest_exit_ms,latest_exit_ms,known_through_ms):
    possible=entry_ms<=item.upper_ms and (latest_exit_ms is None or latest_exit_ms>=item.lower_ms)
    certain_end=known_through_ms if earliest_exit_ms is None else earliest_exit_ms
    definite=entry_ms<=item.lower_ms and certain_end>=item.upper_ms
    if not possible:return 'NOT_EXPOSED'
    cash_sign=-D(side)*item.raw_rate
    if cash_sign<0:return 'DEBIT'
    if cash_sign==0:return 'ZERO'
    if definite and known_through_ms>=item.upper_ms:return 'CREDIT'
    if entry_ms>item.lower_ms or earliest_exit_ms is not None and earliest_exit_ms<item.upper_ms:return 'WITHHOLD_CREDIT'
    return 'PENDING_CREDIT_PROOF'
