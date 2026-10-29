# FIB-PULLBACK-01 preregistration

Status: **FROZEN / NOT EVALUATED / RESEARCH ONLY**  
Frozen: 28 October 2026, Cairo  
Strategy key: `fib_pullback`  
Timeframe: 4h  
Order authority: none; excluded from PAPER rotation and live execution

## Hypothesis

After a confirmed directional impulse in an established EMA50/EMA200 trend, a retracement into the 0.382-0.618
Fibonacci zone followed by a close back through 0.5 has positive net expectancy after Binance fees, slippage and
funding. Long and short are separate evaluation cells. Fibonacci levels alone are not presumed predictive.

## Frozen rule

- Confirm swing pivots with three closed candles on each side. A pivot becomes available only after the third candle to
  its right closes; historical signals must not move when later candles are appended.
- Long impulse: the latest confirmed swing low precedes the latest confirmed swing high, EMA50 is above EMA200, and the
  impulse spans at least 4 ATR(14). Short is mirrored.
- Arm when price overlaps the 0.382-0.618 retracement zone.
- Long entry decision: a bullish closed candle crosses above the 0.5 retracement after arming. Short is mirrored.
- One entry per confirmed impulse. Initial structural invalidation is 0.786; the backtester enters at the next candle
  open using its normal adverse slippage and sizes from the decision candle's distance to that invalidation.
- Management: 50% at +1R, breakeven at +1R, full target +2R, maximum 30 candles. Existing backtester stop-first intrabar
  ordering, taker fees, slippage and funding remain unchanged.

## Evaluation plan

1. Unit gates: long/short symmetry, delayed pivot confirmation, prefix causality, structural-stop distance, exclusion
   from PAPER rotation and deterministic rerun.
2. Development screen on the declared Binance historical universe. Report every attempted long/short cell, gross and
   net results, turnover, fees, funding, trade count, drawdown, profit factor, Sharpe/Sortino and per-regime results.
3. Anchored walk-forward evaluation. Parameter values above remain frozen; no grid search is authorized by this record.
4. One untouched holdout evaluation only after the development and walk-forward packet is signed. Multiplicity must
   include this hypothesis alongside the other strategy-factory trials.
5. Promotion requires positive after-cost expectancy, adequate trades across more than one market regime, stable
   walk-forward behavior and independent review. Otherwise reject or revise under a new version; do not tune this record.

## Boundaries

No PAPER slot, runtime setting, cohort, order, mainnet path, exchange credential, TradingView alert or ML authority is
changed by FIB-PULLBACK-01. A future PAPER canary requires a separately reviewed promotion record.

ROADMAP FIT: Binance strategy discovery and cost-aware historical validation.
DEFERRED: PAPER activation, parameter optimization, untouched holdout, mainnet and non-Binance venues.
