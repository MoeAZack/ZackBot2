"""Own267 causal single-leg SIDE book; no arbitrary tape/evidence adapter."""
from dataclasses import dataclass,field
from decimal import Decimal as D
from fractions import Fraction
import math

from tools.research import c267_source as S
from tools.research import c267_execution_model as K
from tools.research import c267_signals as SIGNAL
from tools.research import c267_funding as F

E,H=S.P,S.H


@dataclass
class Lot:
    attempt_id:str
    symbol:str
    side:int
    decision_ms:int
    entry_ms:int
    regime:str
    fit:E.SignalFit
    sizing:K.ReservedPosition
    target:D
    admission_ordinal:int=0
    fills:list=field(default_factory=list)
    funding:list=field(default_factory=list)
    earliest_exit_ms:int|None=None
    latest_exit_ms:int|None=None
    exit_kind:str|None=None
    funding_complete:bool=False
    protection_failure:bool=False

    @property
    def active(self):return self.latest_exit_ms is None


class SideBook:
    def __init__(self,provider,capital=2000,*,stress=False):
        if type(provider)is not SIGNAL.DecisionProvider:raise S.C.C267Locked('C267_FIXED_OWN_PROVIDER_REQUIRED')
        provider._ensure()
        self.provider=provider;self.source=provider.source;self.side=self.source.side
        if capital not in(1000,2000,3000,4000) or type(stress)is not bool:raise S.C.C267Locked('C267_FIXED_ISOLATED_REPLAY')
        self.capital=capital;self.stress=stress;self.cash=K.SideCash(self.side,capital)
        self.lots=[];self.active={};self.actual_symbols=set();self.observer=F.Observer()
        self.allocated=set();self.withheld=set();self.sequences={};self.decisions=[];self.snapshots=[];self.failures=[]
        self.last_boundary=None;self.complete=True;self._defer=0;self._executed=set();self._entry_ordinals={}

    def marks(self,t):
        marks={}
        for symbol in self.active:
            point=self.source.native_marks(symbol).get(t//H*H-H)
            if point is None:raise S.SourceIncomplete('C267_MISSING_OWN_SIDE_CAUSAL_MARK')
            marks[symbol]=point.close
        return marks

    def equity(self,t):return D(str(self.cash.mark_equity(self.marks(t))))

    def snapshot(self,t,phase):
        marks=self.marks(t);equity=self.cash.mark_equity(marks)
        gross=sum((lot.sizing.quantity*marks[symbol] for symbol,lot in self.active.items()),D(0))
        self.snapshots.append({'timestamp_ms':t,'phase':phase,'equity':equity,'cash':self.cash.settled_cash,
            'cash_exact':self.cash.settled_cash_exact,'gross':gross,'active_count':len(self.active),
            'open_initial_risk':sum((l.sizing.initial_risk_cash for l in self.active.values()),D(0))})

    def post(self,lot,t,phase,components,*,raw=None,event_id=None,sequence=None):
        key=(t,phase,lot.symbol,lot.attempt_id)
        seq=self.sequences.get(key,0) if sequence is None else sequence
        self.sequences[key]=seq+1
        self.cash.append(K.CashEvent(t,phase,lot.symbol,lot.attempt_id,seq,tuple(components),
            event_id or f'{lot.attempt_id}:{phase}:{seq}',raw))
        if not self._defer:self.snapshot(t,phase)

    def _volume_bars(self,symbol,asof):
        index=self.source.native_candles(symbol)
        try:bars=tuple(index[t].signal_bar() for t in range(asof-24*H,asof,H))
        except KeyError as exc:raise S.SourceIncomplete('C267_REQUIRED_FILL_VOLUME_GAP') from exc
        K.causal_volume_total(tuple(E.VolumePoint(b.open_ms,b.quote_volume) for b in bars),asof)
        return bars

    def make_fill(self,lot,t,reference,phase):
        filters=lot.sizing.filters;quantity=lot.sizing.quantity
        if reference<=0 or reference%filters.tick or quantity%filters.step or not filters.min_qty<=quantity<=filters.max_qty or quantity*reference<filters.min_notional:
            raise S.SourceIncomplete('C267_OWNED_FILL_NATIVE_FILTER_INFEASIBLE')
        asof=t-H if phase in('STOP_RANGE','TARGET_RANGE') else t
        bars=self._volume_bars(lot.symbol,asof)
        return E.make_fill(timestamp_ms=t,phase=phase,symbol=lot.symbol,side=lot.side if phase=='ENTRY' else -lot.side,
            quantity=float(quantity),reference=float(reference),movement_from=None if phase=='ENTRY' else float(lot.sizing.entry),
            volume_bars=bars,slippage_bps=10. if self.stress else 2.)

    def stage(self,symbol,t):
        rows=self.source.funding(symbol)
        if symbol not in self.actual_symbols:
            stage=F.stage_first_entry(symbol,t,rows);events=stage.inspected
        else:
            stage=None;events=self.observer.observe(symbol,rows,t,prove=self.source.funding_exhaustivity)
        self.source.funding_exhaustivity(symbol,max(self.source.start_ms,t-K.ENVELOPE_MS),t)
        known=F.reserve_events(events,self.source.native_marks(symbol))
        return stage,K.known_entry_reserve(symbol,self.side,t,known)

    def admit(self,frame,symbol,t,*,protection_failure=False):
        self.provider.validate_frame(frame)
        if self.stress:raise S.C.C267Locked('C267_STRESS_MUST_REPLAY_BASE_SELECTION')
        if frame.entry_ms!=t or frame.side!=self.side:raise S.C.C267Locked('C267_FROZEN_SIGNAL_TO_ENTRY_CLOCK')
        if symbol in self.active or len(self.active)>=3:return None,'SIDE_CAP_OR_OCCUPIED'
        if t+12*H>self.source.end_ms:return None,'SPLIT_END_VETO'
        fit=dict(frame.fits).get(symbol)
        if fit is None or not fit.qualifies or fit.side!=self.side:raise S.C.C267Locked('C267_FIXED_QUALIFYING_SOURCE_FIT_REQUIRED')
        lifecycle=K.assess_current_lifecycle(symbol=symbol,contract_identity=f'BINANCE_USDM_PERPETUAL_{symbol}',entry_ms=t,scheduled_due_ms=t+12*H)
        if lifecycle.veto:return None,lifecycle.reason
        reference=self.source.exact_native_bar(symbol,t).open;filters,_=self.source.filters.rules[symbol]
        if reference%filters.tick:return None,'OFF_GRID_RAW_ENTRY'
        try:atr=E.atr24_wilder(self.provider.atr_prefix(symbol,frame.decision_ms),frame.decision_ms)
        except E.IncompleteAuthority:return None,'ATR_WARMUP_OR_COLLAPSED_PROTECTION'
        stop,target=E.protection_prices(reference,D(str(atr)),self.side,filters.tick)
        if not E.cost_admission(entry=reference,stop=stop,forecast=fit.forecast):return None,'COST_INFEASIBLE'
        equity=self.equity(t)
        if not equity.is_finite() or equity<=0:raise S.SourceIncomplete('C267_NONPOSITIVE_SIDE_EQUITY')
        rows=tuple(E.VolumePoint(b.open_ms,b.quote_volume) for b in self._volume_bars(symbol,t))
        stage,reserve=self.stage(symbol,t)
        sized=K.size_reserved(symbol=symbol,side=self.side,entry=reference,stop=stop,filters=filters,
            side_book_equity=equity,entry_impact_asof_ms=t,entry_volume_rows=rows,reserve=reserve)
        if sized is None:return None,'EXCHANGE_OR_KNOWN_RISK_INFEASIBLE'
        if min(sized.quantity*stop,sized.quantity*target)<filters.min_notional:return None,'PROTECTION_EXIT_MIN_NOTIONAL'
        native=self.source.native_candles(symbol);d=frame.decision_ms
        try:preceding=tuple(float(native[u].quote_volume) for u in range(d-25*H,d-H,H));decision_qv=float(native[d-H].quote_volume)
        except KeyError as exc:raise S.SourceIncomplete('C267_DECISION_LIQUIDITY_HISTORY_GAP') from exc
        if not K.liquidity_admission(decision_quote_volume=decision_qv,preceding_quote_volumes=preceding,
            raw_entry_notional=sized.raw_notional,causal_quote_volume_24h=sized.quote_volume_24h):return None,'LIQUIDITY_INFEASIBLE'
        if (sum((l.sizing.initial_risk_cash for l in self.active.values()),D(0))+sized.initial_risk_cash>equity*D('.03')
            or sum((l.sizing.raw_notional for l in self.active.values()),D(0))+sized.raw_notional>equity*D('1.5')):
            return None,'SIDE_CURRENT_EQUITY_CAP'
        identifier=f'c267:{self.side}:{t}:{symbol}'
        if any(l.attempt_id==identifier for l in self.lots):raise S.SourceIncomplete('C267_DUPLICATE_ACTUAL_ENTRY')
        ordinal=self._entry_ordinals.get(t,0)
        lot=Lot(identifier,symbol,self.side,d,t,frame.regime,fit,sized,target,admission_ordinal=ordinal,protection_failure=protection_failure)
        fill=self.make_fill(lot,t,reference,'ENTRY')
        self.lots.append(lot);self.active[symbol]=lot;self.cash.positions[symbol]=(sized.quantity,sized.entry);lot.fills.append(fill)
        previous=symbol in self.actual_symbols
        if stage is not None:self.observer.commit(stage,actual_entry_ms=t,previous_actual_symbol=previous)
        self.actual_symbols.add(symbol)
        self._entry_ordinals[t]=ordinal+1
        self.post(lot,t,'ENTRY',(-fill.fee_drag,-fill.slippage_drag,-fill.impact_drag),sequence=2*ordinal)
        self.reconcile(t,phase='ENTRY')
        if protection_failure:self.complete=False;self.failures.append('C267_PROTECTION_FAILURE_ACTUAL_EXPOSURE')
        return lot,None

    def close(self,lot,t,reference,fill_phase,*,earliest):
        fill=self.make_fill(lot,t,reference,fill_phase)
        lot.fills.append(fill);lot.earliest_exit_ms=earliest;lot.latest_exit_ms=t;lot.exit_kind=fill_phase
        del self.active[lot.symbol];del self.cash.positions[lot.symbol]
        phase='INTRABAR_PRIOR_BOUNDARY' if fill_phase.endswith('_RANGE') else fill_phase
        self.post(lot,t,phase,(fill.movement_cash,-fill.fee_drag,-fill.slippage_drag,-fill.impact_drag))

    def prior_ranges(self,t):
        self._defer+=1;changed=False
        try:
            for symbol,lot in sorted(tuple(self.active.items())):
                if t<=lot.entry_ms:continue
                bar=self.source.exact_native_bar(symbol,t-H);stop=lot.sizing.stop;target=lot.target
                stop_hit=bar.low<=stop if self.side==1 else bar.high>=stop
                target_hit=bar.high>=target if self.side==1 else bar.low<=target
                if stop_hit or target_hit:
                    self.close(lot,t,stop if stop_hit else target,'STOP_RANGE' if stop_hit else 'TARGET_RANGE',earliest=t-H);changed=True
        finally:self._defer-=1
        if changed:self.snapshot(t,'INTRABAR_PRIOR_BOUNDARY_COMPLETE')

    def open_exits(self,t):
        planned=[]
        for symbol,lot in sorted(tuple(self.active.items())):
            requirement=K.exposed_settlement_requirement(symbol=symbol,contract_identity=f'BINANCE_USDM_PERPETUAL_{symbol}',asof_ms=t)
            if requirement.startswith('DATA_INCOMPLETE'):raise S.SourceIncomplete(requirement)
            bar=self.source.boundary_open(symbol) if t==self.source.end_ms else self.source.exact_native_bar(symbol,t)
            stop,target=lot.sizing.stop,lot.target
            if bar.open<=stop if self.side==1 else bar.open>=stop:phase,reference='STOP_GAP',bar.open
            elif bar.open>=target if self.side==1 else bar.open<=target:phase,reference='TARGET_GAP',target
            elif t>=lot.entry_ms+12*H:phase,reference='SCHEDULED',bar.open
            elif lot.protection_failure and t>=lot.entry_ms+H:phase,reference='PROTECTION_FAILURE',bar.open
            else:continue
            planned.append((K.PHASES[phase],symbol,lot,phase,reference))
        for _,symbol,lot,phase,reference in sorted(planned,key=lambda p:(p[0],p[1],p[2].attempt_id)):
            self.close(lot,t,reference,phase,earliest=t)

    def reconcile(self,t,*,phase='FUNDING',terminal=False):
        allocations=[]
        for symbol in sorted(self.actual_symbols):
            lots=[l for l in self.lots if l.symbol==symbol and not l.funding_complete]
            if not lots:continue
            through=t if any(l.active for l in lots) else min(t,max(l.latest_exit_ms+K.ENVELOPE_MS for l in lots))
            rows=dict(self.source.funding(symbol))
            if terminal and through>=self.source.end_ms:
                for point in self.source.funding_tail(symbol,old_entry_ms=min(l.entry_ms for l in lots)):
                    if point.time_ms in rows:raise S.SourceIncomplete('C267_DUPLICATE_TERMINAL_FUNDING')
                    rows[point.time_ms]=point
            self.source.funding_exhaustivity(symbol,max(self.source.start_ms,min(l.entry_ms for l in lots)-K.ENVELOPE_MS),through)
            events=self.observer.observe(symbol,dict(sorted(rows.items())),through,prove=self.source.funding_exhaustivity)
            marks=self.source.native_marks(symbol)
            for lot in lots:
                for item in events:
                    key=(item.event_id,lot.attempt_id)
                    if key in self.allocated or key in self.withheld:continue
                    eligibility=F.eligibility(item,side=self.side,entry_ms=lot.entry_ms,earliest_exit_ms=lot.earliest_exit_ms,
                        latest_exit_ms=lot.latest_exit_ms,known_through_ms=min(t,self.source.end_ms) if terminal and lot.active else t)
                    if eligibility=='WITHHOLD_CREDIT':self.withheld.add(key);continue
                    if eligibility not in('DEBIT','CREDIT','ZERO'):continue
                    mark=marks.get(item.mark_open_ms)
                    if mark is None:raise S.SourceIncomplete('C267_REQUIRED_RAW_FUNDING_MARK_MISSING')
                    cash=E.funding_cash(self.side,float(lot.sizing.quantity),float(mark.close),float(item.raw_rate))
                    allocations.append((symbol,lot.attempt_id,item.raw_time_ms,lot,item,mark,cash,eligibility))
                if not lot.active and t>=lot.latest_exit_ms+K.ENVELOPE_MS:lot.funding_complete=True
        for symbol,_,raw,lot,item,mark,cash,eligibility in sorted(allocations,key=lambda a:(a[0],a[1],a[2])):
            if phase=='ENTRY' and lot.entry_ms!=t:raise K.ModelIncomplete('C267_OLD_FUNDING_CANNOT_BACKDATE_AFTER_ENTRY_PHASE')
            lot.funding.append({'event_id':item.event_id,'raw_time_ms':raw,'nominal_slot_ms':item.nominal_slot_ms,
                'eligibility_lower_ms':item.lower_ms,'eligibility_upper_ms':item.upper_ms,'cash_applied_ms':t,
                'mark_open_ms':item.mark_open_ms,'mark_price':mark.close,'raw_rate':item.raw_rate,'cash':cash,'eligibility':eligibility})
            self.allocated.add((item.event_id,lot.attempt_id))
            self.post(lot,t,phase,(cash,),raw=raw,event_id=item.event_id,
                sequence=2*lot.admission_ordinal+1 if phase=='ENTRY' else None)

    def replay_admission(self,original,t):
        if not self.stress or original.entry_ms!=t:raise S.C.C267Locked('C267_STRESS_FIXED_ADMISSIONS_ONLY')
        stage,reserve=self.stage(original.symbol,t)
        if reserve!=original.sizing.reserve:raise S.SourceIncomplete('C267_STRESS_KNOWN_FUNDING_RESERVE_MISMATCH')
        lot=Lot(original.attempt_id,original.symbol,self.side,original.decision_ms,t,original.regime,original.fit,
            original.sizing,original.target,admission_ordinal=original.admission_ordinal,protection_failure=original.protection_failure)
        fill=self.make_fill(lot,t,lot.sizing.entry,'ENTRY')
        self.lots.append(lot);self.active[lot.symbol]=lot;self.cash.positions[lot.symbol]=(lot.sizing.quantity,lot.sizing.entry);lot.fills.append(fill)
        if stage is not None:self.observer.commit(stage,actual_entry_ms=t,previous_actual_symbol=lot.symbol in self.actual_symbols)
        self.actual_symbols.add(lot.symbol)
        self.post(lot,t,'ENTRY',(-fill.fee_drag,-fill.slippage_drag,-fill.impact_drag),sequence=2*lot.admission_ordinal)
        self.reconcile(t,phase='ENTRY')

    def run(self,*,base=None):
        if self.last_boundary is not None:raise S.C.C267Locked('C267_SIDE_BOOK_SINGLE_PASS')
        if self.stress and (type(base)is not SideBook or base.source is not self.source or base.side!=self.side or base.capital!=self.capital or base.stress):
            raise S.C.C267Locked('C267_STRESS_BASE_SCOPE_AND_SIDE_REQUIRED')
        pending={};fixed={}
        if base is not None:
            for lot in base.lots:fixed.setdefault(lot.entry_ms,[]).append(lot)
        upper=min(self.source.end_ms,base.last_boundary or self.source.start_ms) if base is not None else self.source.end_ms
        try:
            for t in range(self.source.start_ms,upper+H,H):
                self.last_boundary=t;self.prior_ranges(t);self.reconcile(t);self.open_exits(t)
                if self.stress:
                    for lot in sorted(fixed.get(t,()),key=lambda l:l.admission_ordinal):self.replay_admission(lot,t)
                elif t in pending:
                    frame=pending.pop(t);ranked=E.select_candidates(frame.fits,self.side,decision_close_ms=frame.decision_ms,limit=None)
                    row={'decision_ms':frame.decision_ms,'entry_ms':t,'side':self.side,'status':frame.status,'eligible':len(ranked),'admitted':[],'skipped':[]}
                    for rank,symbol in enumerate(ranked,1):
                        if len(row['admitted'])>=3:break
                        lot,reason=self.admit(frame,symbol,t)
                        if lot:row['admitted'].append({'attempt_id':lot.attempt_id,'symbol':symbol,'original_rank':rank})
                        else:row['skipped'].append({'symbol':symbol,'original_rank':rank,'reason':reason})
                    self.decisions.append(row)
                self.snapshot(t,'HOUR_END')
                if not self.stress and t<self.source.end_ms:
                    frame=self.provider.frame(t);pending[frame.entry_ms]=frame
            if upper<self.source.end_ms:raise S.SourceIncomplete('C267_BASE_STOPPED_BEFORE_END')
            if self.active:raise S.SourceIncomplete('C267_EXPOSED_EPISODE_NOT_FLAT_AT_END')
            self.reconcile(self.source.end_ms+K.ENVELOPE_MS,terminal=True)
            self.snapshot(self.source.end_ms+K.ENVELOPE_MS,'TERMINAL_OLD_LIABILITY_ACCOUNTING')
        except (S.C.C267Locked,S.SourceIncomplete,K.ModelIncomplete,E.C259Error,OSError,ValueError,KeyError) as exc:
            self.complete=False;self.failures.append(str(exc))
        if base is not None:self.decisions=list(base.decisions)
        return self
