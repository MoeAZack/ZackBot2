# LONGSCOUT V91: 0 of 2 pass (research only)

Two preregistered candidates, trials 282 and 283, were rejected. Cumulative ledger: **283 trials / 11 historical screen passes**. No candidate from this batch is worth forward-testing under the frozen gates. Neither is eligible for PAPER. No tuning, subset rescue, merge or mark-ready action.

Rules/tests/cost wrapper frozen at `f89d1eb73cd462dd092e86e5cf26c13859359acd`; cases/initial ledger at `df91f80`; final pre-run input-validation fixes at `f37892eb70f3a416288239c07bf8c8a58ccb5bc6`. All three commits were pushed before market outcomes. Separate branch: `codex/longscout-v91-batch-20261120`, parent PR #347 head `4189908d`. The PR branch was not changed.

L91 tests a positive daily impulse normalized by prior Rogers–Satchell volatility. R91 tests daily range fading conditional on negative daily return autocorrelation in R1 sideways. Related earlier shock, persistence and band-fade hypotheses are disclosed in the source; these are new combined rules, not claims of independent discoveries. Parameters were fixed before any market run.

## Frozen screen and costs

165 coins = fresh134 + extra31, 40 symbol orders, seed 20261112; one position, nominal $8 risk, $3,333.33 cap; existing liquidity/listing/warm-up rules and path/worst execution. Rule-v1 gates and both subset gates are unchanged. This is the shared **already-consumed** 2022–2026 calendar, not untouched holdout or forward evidence. No sealed holdout was accessed or tuned.

V91 explicitly charges **15bp fees + 2bp modeled slippage per side**; stress = 23bp fees + 2bp slippage. The inherited turnover repricing method is retained: native fee is 7bp, then 8bp or 16bp per-side reference turnover is deducted. Legacy fields `pnl15/meanR15` now denote the stricter primary cost scenario; `pnl23` is the stress scenario; `pnl7` is the native 7bp fee + 2bp slip diagnostic. Prior rounds' R15 included slippage inside 15bp and were not rewritten.

Native funding charges 0.00005 of held notional per 4h on both sides (timeframe scaled). It is a fixed model, **not realized historical per-symbol funding**. Exchange feasibility uses the frozen testnet snapshot, not a point-in-time historical rule series. ICPUSDT/TLMUSDT lack snapshot rules: fallback-rule path trades across the 40 repeated orders total L91 111 (order 0: 1), R91 59 (order 0: 2). Full exchange-rule coverage is not established. No coins were silently dropped or outcomes rescued.

## Results

All return figures below are medians of each order's mean R per closed trade, with R = net USD / nominal $8 risk. Early/late split and gates remain frozen.

| Candidate | Median closes | R15 fee + slip | R23 fee + slip | Early | Late | Ex-best coin | fresh134 | extra31 | Verdict |
|---|---:|---:|---:|---:|---:|---:|---:|---:|---|
| L91 long impulse | 277.5 | -0.012601 | -0.030451 | -0.047910 | +0.040634 | -0.039216 | -0.072328 | +0.153024 | FAIL |
| R91 range fade | 145 | -0.072711 | -0.097594 | +0.014057 | -0.214300 | -0.103515 | -0.035387 | -0.257207 | FAIL |

L91 fails primary return, early, ex-best coin, stress return and fresh134. R91 fails primary return, late, ex-best coin, stress return and both subsets. Both pass count and order-0 30-per-half gates. Positive primary-return orders: L91 17/40; R91 2/40.

## Non-bear and side diagnostics

BTC R1 at the signal preceding entry, using only information available then; these are descriptive slices, not new gates or qualified hypotheses.

| Candidate | BTC rising mean R15 | BTC sideways | BTC falling | Long-side mean R15 | Short-side mean R15 |
|---|---:|---:|---:|---:|---:|
| L91 | -0.089641 | +0.205045 | +0.009337 | -0.012601 | no trades |
| R91 | -0.040972 | -0.173990 | -0.076802 | -0.051064 | -0.102561 |

L91's sideways slice does not rescue its failed frozen rule; selecting that filter would be a new, separately preregistered trial on a consumed calendar. No such variant was run. Yearly diagnostics and counts are in `diagnostics.json`; the thin 2022 samples do not establish an economic result.

## Drawdown

Closed-equity drawdown includes initial zero and primary costs; it is not mark-to-market drawdown. Required labels: Standard <=15R, Elevated <=30R, High <=60R, Extreme >60R. No new DD acceptance gate was invented.

| Candidate | Order-0 DD | Median DD over orders | Largest DD over orders |
|---|---:|---:|---:|
| L91 | 24.811R Elevated | 25.349R Elevated | 38.763R High |
| R91 | 20.075R Elevated | 19.890R Elevated | 27.014R Elevated |

## Validation and reproduction

12 V91 synthetic tests passed before the rule freeze: independent loop formulas over three seeds with/without a removed day; historical-prefix comparisons; missing-day availability; first-bar timing; forced regime boundaries; invalid/constant data; and opt-in registration. Six planted source mutants were caught (current band, current volatility baseline, wrong persistence direction, accepting falling regime, bridging gaps, future-dependent return). Exact mutated source and failure logs are in `mutations/`.

Final Windows CP1252 verification: **16 passed in 13.88s** (the 12 V91 tests plus four relevant shared regime/day-availability/frozen-case checks). The optional broad historical file was stopped after discovering that its filename also contains V2–V90 suites; no full historical-suite result is claimed. The bounded checks above completed successfully.

An independent read-only reviewer checked timing, costs, opt-in behavior and day gaps. The review found missing-case and source/destination hash-validation gaps; all were resolved and rechecked before runs. All 166 copied CSVs match the pinned fresh/extra manifests. The report independently reconstructs all seven saved-tape gates, return medians and both subsets with zero mismatches; this is **not independent market-price replay** and not independent profitability acceptance.

Reproduce from this branch with `python research/extra31_screen/longscout_v91_run.py <fresh_dir> <extra_clean_dir> <new_private_output> --case <key> --orders 40` for each key in the frozen case file, then `python research/extra31_screen/longscout_v91_input_audit.py <fresh_dir> <extra_root> <private_output>` and `python research/extra31_screen/longscout_v91_report.py <private_output> <fresh_dir>`. Frozen cases and source SHA hashes are recorded in the artifacts. The cost wrapper, rather than the old V2 runner, is required.

ROADMAP FIT: new long/range economic evidence within the research-only LONGSCOUT contract. DEFERRED: adjacent features, account/runtime/scheduler changes, LEADFWD changes, PAPER/forward activation, mainnet, merge and mark-ready. Shared cache inputs were read-only; all new cache writes are confined to `C:/Dev/ZackBot_research_cache/longscout_codex_v91_20261120`.
