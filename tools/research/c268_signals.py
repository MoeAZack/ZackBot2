"""Own268 source-bound current-open downside-beta frames; no execution seal."""
from dataclasses import dataclass
import hashlib
import json
from types import MappingProxyType
from tools.research import c268_source as S
from tools.research import c268_signal as PURE

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
        if type(source)is not S.DevelopmentSource:raise S.C.C268Locked('C268_FIXED_OWN_SOURCE_REQUIRED')
        source._ensure_authority();self.source=source
        self._frames={};self._issued={};self._closes={};self._issued_closes={}
        regimes={};state=E.EMA200()
        for bar in source.native_candles('BTCUSDT').values():regimes[bar.open_ms+H]=state.update(bar.signal_bar())
        self._regimes=MappingProxyType(regimes)
        self._binding=(self.source,self._frames,self._issued,self._closes,self._issued_closes,self._regimes)

    def _ensure(self):
        self.source._ensure_authority()
        if any(a is not b for a,b in zip((self.source,self._frames,self._issued,self._closes,self._issued_closes,self._regimes),self._binding)):
            raise S.C.C268Locked('C268_SOURCE_OR_SIGNAL_CACHE_REPLACED')

    def closes(self,symbol):
        self._ensure()
        if symbol not in self._closes:
            self._closes[symbol]=self._issued_closes[symbol]=MappingProxyType({t:PURE.HourlyClose(t,t+H-1,float(b.close)) for t,b in self.source.native_candles(symbol).items()})
        if self._closes[symbol] is not self._issued_closes.get(symbol):raise S.C.C268Locked('C268_SOURCE_CLOSE_CACHE_REPLACED')
        return self._closes[symbol]

    def validate_frame(self,frame):
        self._ensure()
        if type(frame)is not DecisionFrame or self._issued.get(frame.entry_ms)is not frame:
            raise S.C.C268Locked('C268_OWN_FRAME_IDENTITY_REQUIRED')

    def frame(self,t):
        self._ensure()
        if type(t)is not int or not PURE.decision_clock(t) or not self.source.start_ms<=t<self.source.end_ms:
            raise S.C.C268Locked('C268_FIXED_CURRENT_UTC_SIX_HOUR_OPEN_REQUIRED')
        if t in self._frames:
            if self._issued.get(t)is not self._frames[t]:raise S.C.C268Locked('C268_FRAME_CACHE_REPLACED')
            return self._frames[t]
        members=self.source.membership_and_filters(decision_ms=t)[0]
        def finish(status,fits=(),indexed=(),history=(),undefined=(),digests=()):
            frame=DecisionFrame(t,t,1,members,self._regimes.get(t) or 'UNCLASSIFIED',tuple(fits),tuple(indexed),tuple(history),tuple(undefined),status,tuple(digests))
            self._frames[t]=self._issued[t]=frame
            return frame
        if t<self.source.view.eligible_start_ms:return finish('FILTER_AUTHORITY_UNAVAILABLE')
        if t<self.source.start_ms+169*H:return finish('SPLIT_RESET_169_CLOSE_WARMUP')
        if t+48*H>self.source.end_ms:return finish('FLAT_SPLIT_BOUNDARY')
        times=tuple(range(t-169*H,t,H));btc=self.closes('BTCUSDT')
        if any(u not in btc for u in times):raise S.SourceIncomplete('C268_REQUIRED_BTC_169_CLOSE_GAP')
        btc_tape=tuple(btc[u] for u in times);ready={};history=[]
        # Validate every required source even if fewer than16 fit or no scores qualify.
        for symbol in members:
            if symbol=='BTCUSDT':continue
            index=self.closes(symbol)
            if not index or times[0]<min(index):
                history.append({'symbol':symbol,'reason':'INITIAL_169_CLOSE_HISTORY_UNAVAILABLE'});continue
            if any(u not in index for u in times):raise S.SourceIncomplete(f'C268_REQUIRED_169_CLOSE_GAP:{symbol}:{t}')
            ready[symbol]=tuple(index[u] for u in times)
        ranked=PURE.rank_signals(entry_ms=t,window_start_ms=self.source.start_ms,window_end_ms=self.source.end_ms,
            btc_closes=btc_tape,pit_filter_members=tuple(ready),alt_closes=ready)
        fits=[];digests=[]
        for symbol,tape in ready.items():
            if symbol in ranked.undefined_symbols:continue
            rows=PURE.paired_returns(btc_tape,tape,entry_ms=t,window_start_ms=self.source.start_ms,window_end_ms=self.source.end_ms)
            fit=PURE.fit_paired_returns(symbol,rows,entry_ms=t,window_start_ms=self.source.start_ms,window_end_ms=self.source.end_ms)
            fits.append((symbol,fit))
            identity=[(p.open_ms,p.close_ms,p.close.hex(),q.close.hex()) for p,q in zip(btc_tape,tape)]
            digests.append((symbol,hashlib.sha256(json.dumps(identity,separators=(',',':')).encode()).hexdigest()))
        return finish(ranked.status,fits,ranked.indexed,history,ranked.undefined_symbols,digests)

    def atr_prefix(self,symbol,decision):
        index=self.source.native_candles(symbol);t=decision-H;segment=[]
        while t>=self.source.start_ms and t in index:segment.append(index[t].signal_bar());t-=H
        segment.reverse();return tuple(segment)
