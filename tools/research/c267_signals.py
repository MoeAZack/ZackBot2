"""Own267 fixed source→original259 signal frames, never an execution seal."""
from dataclasses import dataclass
import math
import statistics
from types import MappingProxyType
from tools.research import c267_source as S

E,H=S.P,S.H


@dataclass(frozen=True)
class DecisionFrame:
    decision_ms:int
    entry_ms:int
    side:int
    members:tuple
    regime:str|None
    fits:tuple
    history_ineligible:tuple
    status:str
    btc_impulse:float|None=None
    impulse_threshold:float|None=None


class DecisionProvider:
    def __init__(self,source):
        if type(source)is not S.DevelopmentSource:raise S.C.C267Locked('C267_FIXED_ISSUED_SIGNAL_SOURCE_REQUIRED')
        source._ensure_authority();self.source=source
        self._frames={};self._issued={};self._closes={};self._issued_closes={}
        regimes={};state=E.EMA200()
        for bar in source.native_candles('BTCUSDT').values():
            regimes[bar.open_ms+H]=state.update(bar.signal_bar())
        self._regimes=MappingProxyType(regimes)
        self._binding=(self.source,self._frames,self._issued,self._closes,self._issued_closes,self._regimes)

    def _ensure(self):
        self.source._ensure_authority()
        if any(a is not b for a,b in zip((self.source,self._frames,self._issued,self._closes,self._issued_closes,self._regimes),self._binding)):
            raise S.C.C267Locked('C267_SIGNAL_PROVIDER_CACHE_OR_SOURCE_REPLACED')

    def closes(self,symbol):
        self._ensure()
        if symbol not in self._closes:
            self._closes[symbol]=self._issued_closes[symbol]=MappingProxyType({t+H:E.ClosePrice(t+H,float(b.close)) for t,b in self.source.native_candles(symbol).items()})
        if self._closes[symbol] is not self._issued_closes.get(symbol):raise S.C.C267Locked('C267_SIGNAL_CLOSE_STREAM_REPLACED')
        return self._closes[symbol]

    def validate_frame(self,frame):
        self._ensure()
        if type(frame)is not DecisionFrame or self._issued.get(frame.decision_ms)is not frame:
            raise S.C.C267Locked('C267_FIXED_CAUSAL_FRAME_IDENTITY_REQUIRED')

    def frame(self,decision):
        self._ensure()
        if type(decision)is not int or decision%H or not self.source.start_ms<=decision<self.source.end_ms:
            raise S.C.C267Locked('C267_FIXED_DEVELOPMENT_DECISION_REQUIRED')
        if decision in self._frames:
            frame=self._frames[decision]
            if self._issued.get(decision)is not frame:raise S.C.C267Locked('C267_CAUSAL_FRAME_CACHE_REPLACED')
            return frame
        entry=decision+H;regime=self._regimes.get(decision)
        members=self.source.membership_and_filters(decision_ms=decision)[0]
        def finish(status,fits=(),history=(),impulse=None,threshold=None):
            frame=DecisionFrame(decision,entry,self.source.side,members,regime,tuple(fits),tuple(history),status,impulse,threshold)
            self._frames[decision]=self._issued[decision]=frame
            return frame
        if decision<self.source.view.eligible_start_ms:return finish('FILTER_AUTHORITY_UNAVAILABLE')
        if decision<self.source.start_ms+722*H or regime is None:return finish('SPLIT_RESET_WARMUP')
        if entry+12*H>self.source.end_ms:return finish('FLAT_SPLIT_BOUNDARY')
        btc=self.closes('BTCUSDT');times=tuple(range(decision-721*H,decision,H))
        if any(t not in btc for t in(*times,decision)):return finish('CONTIGUOUS_BTC_RETURN_WARMUP')
        prior=tuple(btc[t] for t in times);returns=E._prior_returns(prior,decision)
        impulse=math.log(btc[decision].price/prior[-1].price)
        scale=statistics.median(abs(v) for v in returns[-168:])
        fits=[];history=[];ready=[]
        # Authenticate/schema/grid all currently required symbol streams even
        # when shared BTC impulse short-circuits expensive economic fitting.
        for symbol in members:
            if symbol=='BTCUSDT':continue
            closes=self.closes(symbol)
            if not closes or times[0]<min(closes):
                history.append({'symbol':symbol,'reason':'INITIAL_HISTORY_INELIGIBLE_720_PRIOR_RETURNS'});continue
            if any(t not in closes for t in(*times,decision)):
                raise S.SourceIncomplete(f'C267_REQUIRED_SIGNAL_PRICE_GAP:{symbol}:{decision}')
            ready.append((symbol,closes))
        if not math.isfinite(scale) or scale<=0:return finish('UNDEFINED_SHARED_BTC_SCALE',history=history)
        threshold=max(.0075,3.0*scale)
        if abs(impulse)<threshold:
            return finish('SHARED_BTC_NO_IMPULSE_COEFFICIENTS_NOT_ESTIMATED',history=history,impulse=impulse,threshold=threshold)
        for symbol,closes in ready:
            try:fit=E.fit_signal(prior,tuple(closes[t] for t in times),btc_decision=btc[decision],alt_decision=closes[decision])
            except E.IncompleteAuthority as exc:
                history.append({'symbol':symbol,'reason':'FROZEN_ESTIMATOR_UNDEFINED','detail':str(exc)});continue
            fits.append((symbol,fit))
        return finish('FIXED_ORIGINAL_259_FITS',fits,history,impulse,threshold)

    def atr_prefix(self,symbol,decision):
        index=self.source.native_candles(symbol);t=decision-H;segment=[]
        while t>=self.source.start_ms and t in index:segment.append(index[t].signal_bar());t-=H
        segment.reverse()
        return tuple(segment)
