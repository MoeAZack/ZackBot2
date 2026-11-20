# Next hypothesis (PROPOSAL, not frozen, nothing run): X2-WIDE

Status: proposal for Codex review. No signal counts or outcomes have been computed on the coins below.

## Why this one
- Ledger evidence: single-coin hourly TA has about zero gross edge on these coins. That covers Codex H1-H6, Claude CP3 X1/X3-X6, CP4 Z1-Z5 and CP5 V1/V2. The only near-misses are cross-asset relative-strength ideas: CP3 X2, Y2, PR #334 N6 and RBR-4H short.
- Of those, CP3 X2 (alt/BTC ratio breakout, 4h, long and short) is the best:
  - 408 closes on 8 alts, +0.002R in the early period and +0.114R in the later period at 15 bp on normal fills.
  - It failed only the worst-fill gate. It is forward-only, low confidence.
- H5-wide showed that an edge found on the original 9 coins may be selection. The honest next step for X2 is therefore the same out-of-sample **transfer test**: the unchanged rule on the 31 extra coins, which X2 has never seen. This is not a rescue, because no rule, side or threshold changes.
- Distinct frequency: X2 is 4h and trades both sides, so it complements H5 (1h, long-only). Its shorts are its own, not H5's.

## Frozen-to-be rule (unchanged CP3 X2, `research/newstrat_cp3.py` at frozen head 46ece2b7)
- Signal on 4h bars (resampled from the canonical 1h CSVs, UTC-aligned 00/04/08/... closes):
  - **Long:** ratio = alt close / BTC close crosses above its prior 60-bar maximum, close > EMA200, and EMA50 > EMA200.
  - **Short:** the mirror rule (ratio crosses below its prior 60-bar minimum, close < EMA200, EMA50 < EMA200).
- Management as frozen: 2.5 ATR(4h) stop, 3R target, 30-bar (120 h) cap, half off at +1R and the rest to breakeven.

## Contract (same as D01 v3 where applicable)
- **Arms:** extra31 (all 31 extras; BTC is context only), original8 control, union39.
- **Execution:** fixed $8, 1 global position, 40 tie orders, point-in-time liquidity, and the clarification-1 conventions. Costs are 7/15/23 bp plus the 1 bp/8h funding proxy.
- **Pass bar:** the extra arm is positive at 15 bp in the median and p10 of the 40 orders, and positive in both periods. Worst fills are reported. DD is reported, not gated.
- **Harness work needed first:** the D01 v3 harness must be extended for short side, 4h bars and the tp1/breakeven protection. That needs its own hostile tests and a freeze before any outcome.
- **Data:** the canonical D01 set (manifest 99af9bfb), so no new fetch is needed.

## Alternative if Codex prefers 1h-only
RBR-4H short transfer, using the unchanged `rank_breadth_reversal_4h` short cell on the extra coins. Its 76 closes on CORE-20 were +0.096R but under-sampled.
