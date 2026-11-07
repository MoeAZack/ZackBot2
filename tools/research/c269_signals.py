"""Own269 source-bound current-open overhead-notional frames; no execution seal."""
from dataclasses import dataclass
import hashlib
import json
from types import MappingProxyType
from tools.research import c269_source as S
from tools.research import c269_signal as PURE

E,H=S.P,S.H


@dataclass(frozen=True)
class DecisionFrame:
    decision_ms:int
    entry_ms:int
    side:int
    members:tuple
    regime:str
    fits:tuple
    indexed:tuple
    history_ineligible:tuple
    undefined_symbols:tuple
    status:str
    window_digests:tuple


class DecisionProvider:
    def __init__(self,source):
        if type(source)is not S.DevelopmentSource:raise S.C.C269Locked('C269_FIXED_OWN_SOURCE_REQUIRED')
        source._ensure_authority();self.source=source
        self._frames={};self._issued={};self._closes={};self._issued_closes={}
        regimes={};state=E.EMA200()
        for bar in source.native_candles('BTCUSDT').values():regimes[bar.open_ms+H]=state.update(bar.signal_bar())
        self._regimes=MappingProxyType(regimes)
        self._binding=(self.source,self._frames,self._issued,self._closes,self._issued_closes,self._regimes)

    def _ensure(self):
        self.source._ensure_authority()
        if any(a is not b for a,b in zip((self.source,self._frames,self._issued,self._closes,self._issued_closes,self._regimes),self._binding)):
            raise S.C.C269Locked('C269_SOURCE_OR_SIGNAL_CACHE_REPLACED')

    def closes(self,symbol):
        self._ensure()
        if symbol not in self._closes:
            self._closes[symbol]=self._issued_closes[symbol]=MappingProxyType({t:PURE.CompletedBar(t,t+H-1,*(float(v) for v in(b.open,b.high,b.low,b.close,b.quote_volume))) for t,b in self.source.native_candles(symbol).items()})
        if self._closes[symbol] is not self._issued_closes.get(symbol):raise S.C.C269Locked('C269_SOURCE_CLOSE_CACHE_REPLACED')
        return self._closes[symbol]

    def validate_frame(self,frame):
        self._ensure()
        if type(frame)is not DecisionFrame or self._issued.get(frame.entry_ms)is not frame:
            raise S.C.C269Locked('C269_OWN_FRAME_IDENTITY_REQUIRED')

    def frame(self,t):
        self._ensure()
        if type(t)is not int or not PURE.decision_clock(t) or not self.source.start_ms<=t<self.source.end_ms:
            raise S.C.C269Locked('C269_FIXED_CURRENT_UTC_SIX_HOUR_OPEN_REQUIRED')
        if t in self._frames:
            if self._issued.get(t)is not self._frames[t]:raise S.C.C269Locked('C269_FRAME_CACHE_REPLACED')
            return self._frames[t]
        members=self.source.membership_and_filters(decision_ms=t)[0]
        def finish(status,fits=(),indexed=(),history=(),undefined=(),digests=()):
            frame=DecisionFrame(t,t,-1,members,self._regimes.get(t) or 'UNCLASSIFIED',tuple(fits),tuple(indexed),tuple(history),tuple(undefined),status,tuple(digests))
            self._frames[t]=self._issued[t]=frame
            return frame
        if t<self.source.view.eligible_start_ms:return finish('FILTER_AUTHORITY_UNAVAILABLE')
        if t<self.source.start_ms+168*H:return finish('SPLIT_RESET_168_BAR_WARMUP')
        if t+24*H>self.source.end_ms:return finish('FLAT_SPLIT_BOUNDARY')
        times=tuple(range(t-168*H,t,H));history=[];fits=[];digests=[];undefined=[]
        for symbol in members:
            if symbol=='BTCUSDT':continue
            index=self.closes(symbol)
            if not index or times[0]<min(index):
                history.append({'symbol':symbol,'reason':'INITIAL_168_BAR_HISTORY_UNAVAILABLE'});continue
            if any(u not in index for u in times):raise S.SourceIncomplete(f'C269_REQUIRED_168_BAR_GAP:{symbol}:{t}')
            tape=tuple(index[u] for u in times)
            try:atr=E.atr24_wilder(self.atr_prefix(symbol,t),t)
            except E.IncompleteAuthority as exc:
                if str(exc)!='invalid ATR':raise S.SourceIncomplete(str(exc)) from exc
                atr=0. # Fully observed valid zero-range domain is protection-infeasible.
            fit=PURE.fit_signal(symbol,tape,atr=atr,entry_ms=t,window_start_ms=self.source.start_ms,window_end_ms=self.source.end_ms)
            fits.append((symbol,fit))
            if fit.status!='DEFINED':undefined.append({'symbol':symbol,'reason':fit.status})
            identity=[(b.open_ms,b.close_ms,*(v.hex() for v in(b.open,b.high,b.low,b.close,b.quote_volume))) for b in tape]
            digests.append((symbol,hashlib.sha256(json.dumps(identity,separators=(',',':')).encode()).hexdigest()))
        indexed=PURE.rank_fits(tuple(f for _,f in fits))
        return finish('FIXED_OVERHEAD_NOTIONAL_SIGNALS',fits,indexed,history,undefined,digests)

    def atr_prefix(self,symbol,decision):
        index=self.source.native_candles(symbol);t=decision-H;segment=[]
        while t>=self.source.start_ms and t in index:segment.append(index[t].signal_bar());t-=H
        segment.reverse();return tuple(segment)
