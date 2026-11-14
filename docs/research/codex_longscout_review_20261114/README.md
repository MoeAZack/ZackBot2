# LONGSCOUT-V1 independent review — 14 November 2026

Reviewed PR #347 at exact head `0a54ad4c92f699dcf15635859dfd4766d4c25276`. Research only. No account, key, position, runtime, scheduler, installer or trading action. No merge or label of #347.

## Frozen source and inputs

The longscout rules blob is unchanged from `a4e3b7591dcb7538ff9eb5aeeb1a07d82b6276e2`; the cases/R1/pass-rule blob is unchanged from `465808a2289863beca314766f3cd55038004174b`. Both independent programs verified all 32 input CSV hashes (31 traded coins plus BTC context) against the published pinned input receipts. Expected manifest identity is `99af9bfb4a9b29ede80f8f3ff54541912d74d03b3b93d984432832c934bdfa95`. This review checks those actual CSV bytes; it does not independently establish that the published freeze preceded all outcomes or that these coins/calendar dates are untouched research holdouts.

## LC5 volume accumulation: independent execution replay PASS

`volume_review.py` imports no repository signal, indicator, runner, feasibility or backtest implementation. It independently computes complete daily aggregation, EMA50, ATR14, prior-volume median, BTC R1, D01 liquidity, chronological one-position admission, floor sizing/filters, gaps/stops/targets/time exits, slippage, fees, funding and cash. It retains the disclosed 10m-equity sizing device to compare the actual experiment, not a claim to simulate a real 4,000-dollar shared account.

- 504 raw signal tuples matched, 429 eligible; zero missing/extra, including eligibility.
- Order 0: 64 closed trades matched on coin, side, entry/exit indices and timestamps, and exit reason; zero missing/extra.
- Every compared trade's pnl7/pnl15/pnl23, R and turnover matched. Maximum P&L discrepancy 3.56e-15 dollars; maximum turnover discrepancy 1.43e-14.
- Order-0 net at 15 bp: +170.18184390009944 dollars; mean +0.3323864138673817 R; 36 early / 28 later closes.
- One GALA position is still open at the terminal bar. Closed-trade figures exclude its mark. The replay records that open position explicitly.

Independently recomputing gate arithmetic from all 40 supplied cash tapes confirms median 65 closes, mean R15 +0.3106714771, mean R23 +0.2994077015, early +0.2802478744, later +0.3563426721, ex-best +0.2265054220. It fails exactly `trades` and `order0_30_per_half`. All 40 mean R15 values are positive in the supplied result. Do not weaken either frozen count gate: positive but insufficient-sample research lead; no qualification or activation.

## exp_trend R1: independent signals and classification PASS

`regime_reproduce.py` independently reconstructs the EMA/momentum entries and D01 eligibility from the raw 1h candles. All 1,542 signal tuples match, including 1,320 eligible; zero missing/extra. R1 is reconstructed from complete 4h bins and complete UTC days, usable only after the daily close. The entry-time classification matches all 12,858 path trades across 40 orders for both the traded coin and BTC: 25,716 labels, zero disagreements. All 112 published regime aggregate values match.

Short-side median coin-regime contributions:

| Entry-time regime | Median closes | Median sum R15 |
|---|---:|---:|
| Rising | 27 | +18.74323425 |
| Sideways | 24 | +10.10268448 |
| Falling | 100 | +1.82920977 |
| Unknown | 12 | +4.41061255 |

Order-0 tape contains 323 closes and sums to +304.4558253287107 dollars at 15 bp, agreeing with #340's reported order-0 result. This exp_trend cash sum is checked from the supplied trade tape, not independently replayed execution; the independent execution replay in this review is LC5. Aggregate medians need not add to the median total.

The frozen structure-reading condition is not met: shorts do not earn mainly in falling entry-time regimes. This does not show that rising-regime short entries are inherently invalid, nor prove luck. Entry regime and later price path differ. Keep this as a consumed-data diagnostic, with no post-hoc filter or confidence upgrade. The original-nine claim was not independently replayed in this review.

## Disposition and limits

All eight failure labels agree with an independent recomputation of the frozen gates from their supplied all-order tables. That is not independent signal/cash validation of the other seven candidates. Claim 1's 0/8 disposition and LC5 exception, and the requested extra31 exp_trend R1 split, are supported to the stated scope. No frozen gate is amended. No new profitable replacement is qualified by this batch.

The two independent programs ran successfully on native Windows with CP1252 output. This was targeted research reproduction, not a full-suite run or an adversarial audit of every new rule. Historical testnet exchange-filter coverage, the large-equity proxy, funding proxy, closed-trade-only reporting and consumed-data selection remain limitations. No account changes are proposed from these results.
