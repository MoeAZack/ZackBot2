"""Fixed270 outgoing-display denominator salience. Pure, default OFF."""
from dataclasses import dataclass
import math

HOUR_MS=3_600_000
HISTORY=25
THRESHOLD=.005
EXECUTION_AUTHORIZED=False


class SignalIncomplete(ValueError):pass


@dataclass(frozen=True)
class CompletedClose:
    open_ms:int
    close_ms:int
    close:float


@dataclass(frozen=True)
class ExpiryFit:
    symbol:str
    entry_ms:int
    old_denominator_close:float
    next_denominator_close:float
    last_completed_close:float
    outgoing_log_return:float
    score:float
    current_display_log_return:float
    status:str='DEFINED'

    @property
    def positive_eligible(self):return self.status=='DEFINED' and self.score>=THRESHOLD


@dataclass(frozen=True)
class IndexedSignal:
    index:int
    fit:ExpiryFit


def decision_clock(t):return type(t)is int and t%(6*HOUR_MS)==0


def fit_signal(symbol,rows,*,entry_ms,window_start_ms,window_end_ms):
    if (not decision_clock(entry_ms) or any(type(t)is not int or t%HOUR_MS for t in(window_start_ms,window_end_ms))
        or not window_start_ms<=entry_ms-HISTORY*HOUR_MS or not entry_ms<window_end_ms
        or type(symbol)is not str or not symbol or symbol=='BTCUSDT'):
        raise SignalIncomplete('C270_EXACT_CURRENT_CLOCK_AND_RESET_ALT_REQUIRED')
    rows=tuple(rows);times=tuple(range(entry_ms-25*HOUR_MS,entry_ms,HOUR_MS))
    if len(rows)!=25:raise SignalIncomplete('C270_EXACT_25_COMPLETED_INWINDOW_CLOSES_REQUIRED')
    for bar,t in zip(rows,times):
        if (type(bar)is not CompletedClose or type(bar.open_ms)is not int or bar.open_ms!=t
            or type(bar.close_ms)is not int or bar.close_ms!=t+HOUR_MS-1
            or type(bar.close)is not float or not math.isfinite(bar.close) or bar.close<=0):
            raise SignalIncomplete('C270_INVALID_NATIVE_COORDINATE_OR_CLOSE')
    # Native C_u denotes the close of hour OPENu. Index0 is B-25H,
    # index1 is B-24H, index24 is the last completed hour B-H.
    old,next_close,last=rows[0].close,rows[1].close,rows[-1].close
    outgoing_ratio=next_close/old;display_ratio=last/old
    if any(not math.isfinite(v) or v<=0 for v in(outgoing_ratio,display_ratio)):
        raise SignalIncomplete('C270_NONFINITE_CAUSAL_LOG_RATIO')
    outgoing=math.log(outgoing_ratio);display=math.log(display_ratio)
    return ExpiryFit(symbol,entry_ms,old,next_close,last,outgoing,-outgoing,display)


def rank_fits(fits):
    fits=tuple(fits)
    if (any(type(f)is not ExpiryFit for f in fits) or len({f.symbol for f in fits})!=len(fits)
        or len({f.entry_ms for f in fits})>1):raise SignalIncomplete('C270_UNIQUE_SAME_CLOCK_SOURCE_FITS_REQUIRED')
    ranked=sorted((f for f in fits if f.positive_eligible),key=lambda f:(-f.score,f.symbol))
    return tuple(IndexedSignal(i+1,fit) for i,fit in enumerate(ranked[:3]))
