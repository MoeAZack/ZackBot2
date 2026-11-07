"""Fixed269 quote-weighted overhead-price mass; pure, no source authority."""
from dataclasses import dataclass
import math
from typing import Sequence

HOUR_MS=3_600_000
HISTORY=168
EXECUTION_AUTHORIZED=False


class SignalIncomplete(ValueError):pass


@dataclass(frozen=True)
class CompletedBar:
    open_ms:int
    close_ms:int
    open:float
    high:float
    low:float
    close:float
    quote_volume:float


@dataclass(frozen=True)
class OverheadFit:
    symbol:str
    entry_ms:int
    close:float
    atr:float
    total_quote_volume:float
    overhead_quote_volume:float
    score:float|None
    status:str

    @property
    def positive_eligible(self):return self.status=='DEFINED' and self.score>0.0


@dataclass(frozen=True)
class IndexedSignal:
    index:int
    fit:OverheadFit


def decision_clock(t):return type(t)is int and t%(6*HOUR_MS)==0


def fit_signal(symbol,rows:Sequence[CompletedBar],*,atr,entry_ms,window_start_ms,window_end_ms):
    if (not decision_clock(entry_ms) or any(type(t)is not int or t%HOUR_MS for t in(window_start_ms,window_end_ms))
        or not window_start_ms<=entry_ms-HISTORY*HOUR_MS or not entry_ms<window_end_ms
        or type(symbol)is not str or not symbol or symbol=='BTCUSDT'):
        raise SignalIncomplete('C269_EXACT_CAUSAL_SPLIT_CLOCK_AND_ALT_REQUIRED')
    rows=tuple(rows);times=tuple(range(entry_ms-HISTORY*HOUR_MS,entry_ms,HOUR_MS))
    if len(rows)!=HISTORY:raise SignalIncomplete('C269_EXACT_168_COMPLETED_BARS_REQUIRED')
    for bar,t in zip(rows,times):
        if (type(bar)is not CompletedBar or type(bar.open_ms)is not int or bar.open_ms!=t
            or type(bar.close_ms)is not int or bar.close_ms!=t+HOUR_MS-1
            or any(type(v)is not float or not math.isfinite(v) or v<=0 for v in(bar.open,bar.high,bar.low,bar.close))
            or type(bar.quote_volume)is not float or not math.isfinite(bar.quote_volume) or bar.quote_volume<0
            or not bar.low<=min(bar.open,bar.close)<=max(bar.open,bar.close)<=bar.high):
            raise SignalIncomplete('C269_NATIVE_PRICE_NONNEGATIVE_VOLUME_OR_COORDINATE_ERROR')
    if type(atr)is not float or not math.isfinite(atr) or atr<0:raise SignalIncomplete('C269_INVALID_CAUSAL_ATR')
    close=rows[-1].close
    try:
        total=math.fsum(b.quote_volume for b in rows)
        if not math.isfinite(total):raise SignalIncomplete('C269_NONFINITE_TOTAL_QUOTE')
        if total==0:return OverheadFit(symbol,entry_ms,close,atr,total,0.,None,'ZERO_TOTAL_LIQUIDITY_INFEASIBLE')
        if atr==0:return OverheadFit(symbol,entry_ms,close,atr,total,0.,None,'ZERO_ATR_PROTECTION_INFEASIBLE')
        upper=close+atr
        if not math.isfinite(upper):raise SignalIncomplete('C269_NONFINITE_OVERHEAD_BAND')
        selected=[]
        for bar in rows:
            typical=math.fsum((bar.high,bar.low,bar.close))/3.0
            if not math.isfinite(typical):raise SignalIncomplete('C269_NONFINITE_TYPICAL_PRICE')
            if close<typical<=upper:selected.append(bar.quote_volume)
        overhead=math.fsum(selected);score=overhead/total
    except OverflowError as exc:raise SignalIncomplete('C269_NONFINITE_ORDERED_ARITHMETIC') from exc
    return OverheadFit(symbol,entry_ms,close,atr,total,overhead,score,'DEFINED')


def rank_fits(fits):
    """First3 indexed only. Owning execution cannot replace a skipped index."""
    fits=tuple(fits)
    if (any(type(f)is not OverheadFit for f in fits) or len({f.symbol for f in fits})!=len(fits)
        or len({f.entry_ms for f in fits})>1):raise SignalIncomplete('C269_EXACT_UNIQUE_SAME_CLOCK_FITS_REQUIRED')
    ranked=sorted((f for f in fits if f.positive_eligible),key=lambda f:(-f.score,f.symbol))
    return tuple(IndexedSignal(i+1,fit) for i,fit in enumerate(ranked[:3]))
