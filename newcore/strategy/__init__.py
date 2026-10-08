"""NEWCORE strategies: pure signal functions over closed candles (no IO, clock or randomness).

`ema_mom` is the VS-01 trend rule (long candidate; its short mirror is a mechanics test only). `research/` holds
evidence scripts and is never imported by the runtime.
"""
from .ema_mom import (RULE_ID, REASON_ENTRY, REASON_EXIT, Bars, BadBars, Evaluation, FormingCandle, Params, Side,
                      SignalAction, SignalDecision, decide, decisions_at, entries, evaluate, lookback_bars, stop_distance)

__all__ = ['RULE_ID', 'REASON_ENTRY', 'REASON_EXIT', 'Bars', 'BadBars', 'Evaluation', 'FormingCandle', 'Params', 'Side',
           'SignalAction', 'SignalDecision', 'decide', 'decisions_at', 'entries', 'evaluate', 'lookback_bars',
           'stop_distance']
