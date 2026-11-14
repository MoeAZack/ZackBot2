# LONGSCOUT-FRESH-V1 — independent reproduction and retirement

Reviewed PR #347 at `f1ee3f229511f7d60d8a2ccf7328eced631b5115`. Research only. No account, key, position, runtime, scheduler, installer or trading action. Account 1 untouched. No merge or label.

## Verdict

**FRESH FAIL independently confirmed. `lc_volume_accum_1d` is RETIRED as an unchanged research candidate.** Prior positive extra31 results are preserved as insufficient-sample evidence. Pooled results remain secondary and do not rescue a failed fresh-coin test. No rule, universe, gate or management change is proposed.

## Source and input authority

The freeze blob is unchanged from `200943c3`, and hashes to `8df402e3740419763bbcec0e177c8d6d54dfb0d2efd02c9003683b2b710fdcf5`. The candidate source is byte-identical to `a4e3b759`. The actual fresh manifest hashes to `fb0c0572b6c2bc5f569c64d89bd0ee2ca1dd9e3cf96b1ed8bd1110c341ed2f3e`; all 135 CSV hashes match (134 traded coins plus BTC context). The replay asserts the exact 134-coin manifest set and alphabetical order. This is not a separate audit of the exchangeInfo selection algorithm or proof that neither agent had any earlier exposure to those coins.

## Independent order-0 replay

`volume_review.py` reuses Codex's separate LC5 implementation, extended only to accept the fresh inputs and reference directory. It imports no repository indicator, signal, runner, feasibility or backtest code. Native Windows with CP1252 output: PASS.

- All 2,183 signal/eligibility tuples match, 1,352 eligible, zero missing/extra.
- All 91 closed trades match on symbol, side, entry/exit times and indices and reason, zero missing/extra.
- Independently calculated sizing, gaps/stops/targets/time exits, slippage, fees, funding and cash reproduce all trade pnl7/pnl15/pnl23 and R/turnover. Maximum P&L difference 3.56e-15 dollars; maximum turnover difference 1.43e-14.
- Order-0 net15 **-88.4872765223 dollars**, mean R15 **-0.1215484568**. Early/later counts 62/29. No terminal open position.
- 56 coins traded, 16 positive. Closed-trade DD 20.21627649R from the supplied chronological tape (the same reporting convention as the study).

## All-order gate reconciliation

Using the supplied 40-order path tapes, independent arithmetic reproduces all frozen gate decisions and medians. This is not 40 independent execution replays.

| Statistic | Fresh | Pooled secondary |
|---|---:|---:|
| Median closes | 85 | 90.5 |
| Mean R15 | -0.03739505 | +0.10030952 |
| Mean R23 | -0.04694642 | +0.09044102 |
| Early R15 | +0.11296550 | +0.11180394 |
| Later R15 | -0.30455956 | +0.15061725 |
| Ex-best R15 | -0.09515164 | +0.04250624 |
| R15 p10 | -0.08498735 | -0.03392780 |

Fresh fails trade count, meanR15, meanR23, later half, ex-best, and order-0 per-half count; early is positive. Pooled fails only median trade count. Both remain FAIL under the frozen rule. Positive pooled median economics do not supersede the fresh failure or justify threshold changes.

## Record and boundaries

`retirement.json` is the durable research disposition and points to the exact rule and outcome head. Runtime registries and account settings are deliberately unchanged. Survivorship, a shared consumed calendar window, the historical testnet filter snapshot/fallback behavior, funding proxy and the 10m-equity sizing device remain limitations; this is not a real 4,000-dollar shared-account simulation or mainnet evidence. No separate adversarial audit or full-suite run was needed for this read-only targeted replay. No tuning or further data acquisition occurred.
