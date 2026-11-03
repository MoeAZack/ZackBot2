# Candidate 47 / bullish outside-bar expansion preregistration

Status: **FROZEN / NOT EVALUATED / RESEARCH ONLY**; frozen 3 November 2026 Cairo before results.  
Key: `outside_bar_expansion_long_1h`; 1h long only.

## Duplicate and correlation identity

Before freezing, the complete Candidate 1–46 append-only ledger/risk register, current tree, local/remote refs and tags,
Git history, preregistrations, results and research tools were searched for outside bars, engulfing ranges and range-
expansion bars. No tested strategy, packet or result matched. Mechanically adjacent families were also inspected: C32
requires compression inside a mother bar and then a later break; C34 requires a three-red-bar exhaustion sequence;
15m momentum burst requires a large body but not full range engulfment; liquidity-sweep rejection closes back inside
the prior range. C47 instead requires the decision candle itself to engulf both prior extremes and close beyond the
prior high. The correlation risk is trend/breakout beta, disclosed by the EMA50/200 uptrend gate, but the entry event is
not a channel, equal-level, compression, oscillator, VWAP or recovery rule. One cell only; no rescue or variant grid.

## Frozen causal rule and costs

At completed 1h bar `t`, require `high[t] > high[t-1]`, `low[t] < low[t-1]`, range >=1.25 current ATR, close above the
prior high and in the top 25% of the current range, EMA50>EMA200, and quote volume >=1.25 times its prior 48-bar median.
A 2 ATR stop must be >=5x modeled round-trip cost. Fill the next open; exit below EMA20 or if EMA50<=EMA200, at the 2
ATR stop, 2R target, or 16 bars. Costs are 5 bp taker +2 bp adverse slippage per fill plus checksum-verified actual
signed funding. All inputs are completed-bar causal.

CORE-20 development `2024-10-14T00:00:00Z..2025-04-01T00:00:00Z`. Stage-A economic PASS requires pre-cost gross>0,
mean net R>0, PF>1.05, >=100 closes, >=8 symbols and >=20 closes in each BTC regime. Drawdown is reported/classified
under the prospective policy rather than used to rewrite economics. Candidate 47 increases the numbered family to 47;
any later promotion must use family-wise alpha <=`0.05/47` and chronological OOS. PAPER eligibility remains BLOCK
regardless of the development result. No rescue, folds, holdout, PAPER or runtime action is authorized; the post-
2026-09-28 holdout stays sealed.

ROADMAP FIT: distinct default-OFF causal price-structure hypothesis.  
DEFERRED: short mirror, thresholds, folds, holdout, Stage B and all activation.
