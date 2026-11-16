# Completed funding re-cost: eight leads and PROT-CMP-01

Research only. **4,494/4,494 fixed-book result rows are complete, with no missing or incomplete jobs.** These use public historical funding rates, native settlement marks where present, and the owner's explicitly approved, labelled one-minute mark-candle proxies elsewhere. They are conditional on the declared candle-fill timing models; they are not measured exchange payments or a profitability qualification.

## Coverage and gap handling

- 174 symbols; 619,468 returned funding events; 723 exhaustively paginated, hash-verified public REST responses.
- 44,175 missing settlement-mark timestamps in the conservative exposure windows received verified public 1m candles. 41,525 are used by at least one closed-book primary or timing scenario; the remainder are retained as conservative-window source evidence.
- LUNC: 467 source proxies, 446 used. This includes the original four gaps and the subsequently approved additional gaps.
- Every selected candle contains its event timestamp; maximum event-to-candle-open gap is **31 milliseconds**. No unresolved required marks, source gaps or returned-cadence discontinuities remain.
- Every proxy has its symbol, funding timestamp, Cairo time, candle timestamp, signed timestamp gap, open value, full OHLC, source URL, checksum/hash and usage flags in [mark_proxy_ledger.csv.gz](completed/mark_proxy_ledger.csv.gz).
- All returned rates and native mark fields, including empty marks outside required exposure windows, remain in [public_funding_events.csv.gz](completed/public_funding_events.csv.gz). Those unused empty fields are labelled explicitly; they are not dropped or set to zero.

## Original four LUNC proxies

All four use the [official January 2023 LUNC 1m mark archive](https://data.binance.vision/data/futures/um/monthly/markPriceKlines/1000LUNCUSDT/1m/1000LUNCUSDT-1m-2023-01.zip), SHA-256 `7d8ade08e231a6a7f42558c560f3f0aeefebbf9c9a881b7d88b5adbe1e9c2411`. Their historical rates remain unchanged.

| Funding time (Cairo) | Proxy open | Gap from candle open | Candle low?high sensitivity |
|---|---:|---:|---:|
| 2023-01-08T02:00:00.015000+02:00 | 0.15323921 | 15 ms | 0.15323477?0.15350000 |
| 2023-01-08T10:00:00+02:00 | 0.15374002 | 0 ms | 0.15371946?0.15394258 |
| 2023-01-08T18:00:00.010000+02:00 | 0.15495283 | 10 ms | 0.15492966?0.15513434 |
| 2023-01-09T02:00:00.003000+02:00 | 0.15840000 | 3 ms | 0.15840000?0.15886954 |

## Eight leads: 15bp/side, original path

Values are medians across the original 40 orders of mean closed-trade R, using the published fixed **$8 reporting denominator**. Slightly varying historical execution risk/quantities remain unchanged. “Funding delta” below is the difference between the funded and fee/slippage-only medians; the CSV also retains median per-order deltas, all cost levels, both paths, and order-zero totals.

| Lead | Old proxy-funded | Fee/slip only | Re-costed | Funding delta vs fee/slip | Change vs old | LUNC proxy touched |
|---|---:|---:|---:|---:|---:|---|
| r6_regime_flip_4h | +0.331701 | +0.360576 | +0.354471 | -0.006105 | +0.022770 | YES |
| s54_double_top_4h | +0.152595 | +0.168151 | +0.174151 | +0.006000 | +0.021556 | NO |
| l24_breakaway_day_4h | +0.059980 | +0.067773 | +0.085722 | +0.017949 | +0.025742 | YES |
| s26_weekly_close_breakdown_4h | +0.081250 | +0.104901 | +0.111376 | +0.006474 | +0.030126 | YES |
| l40_dual_momentum_4h | +0.090585 | +0.107636 | +0.110657 | +0.003021 | +0.020072 | NO |
| s51_momentum_accel_4h | +0.063070 | +0.078937 | +0.079449 | +0.000513 | +0.016379 | YES |
| s76_uptrend_line_break_4h | +0.034636 | +0.050424 | +0.047764 | -0.002660 | +0.013128 | YES |
| r74_prior_month_break_4h | +0.053725 | +0.066865 | +0.061622 | -0.005243 | +0.007897 | YES |

All eight improve against the old always-positive funding-cost proxy at 15bp, but signed funding reduces the fee/slippage-only median for R6, R74 and S76. This does not establish an untouched out-of-sample edge. No thresholds, universe order, signal, trade selection or original selection caveat changed.

| Lead | Proxy candle-range sensitivity | Late intrabar timing | Scheduled-boundary-before-fill timing |
|---|---:|---:|---:|
| r6 | +0.353838 to +0.354503 | +0.354466 | +0.353875 |
| s54 | +0.174149 to +0.174154 | +0.174340 | +0.174411 |
| l24 | +0.085635 to +0.085735 | +0.087154 | +0.086057 |
| s26 | +0.111372 to +0.111382 | +0.111395 | +0.111469 |
| l40 | +0.110593 to +0.110668 | +0.110994 | +0.110274 |
| s51 | +0.079447 to +0.079458 | +0.079512 | +0.079891 |
| s76 | +0.047761 to +0.047767 | +0.047813 | +0.048718 |
| r74 | +0.061598 to +0.061626 | +0.061639 | +0.061684 |

All eight lead summaries touch at least one approved mark proxy. The LUNC column is a separate flag, not the general proxy flag. [lead_summary.csv](completed/lead_summary.csv) contains all 48 strategy/cost/path summaries; [all_job_deltas.json.gz](completed/all_job_deltas.json.gz) flags each individual order result.

## Order variability after funding

The 40 order variants overlap; they are not independent trials. Positive medians retain material order sensitivity. The table shows original-path mean R/trade after funding. Full 7/15/23bp and both-path diagnostics are in [lead_order_robustness.csv](completed/lead_order_robustness.csv). Original early/late, concentration and selection gates are not reclassified by this cost-attribution audit.

| Lead | Positive orders at 15bp | Minimum at 15bp | Positive orders at 23bp | Minimum at 23bp |
|---|---:|---:|---:|---:|
| r6 | 40/40 | +0.049630 | 40/40 | +0.030631 |
| s54 | 40/40 | +0.107502 | 40/40 | +0.082104 |
| l24 | 40/40 | +0.017676 | 40/40 | +0.004150 |
| s26 | 37/40 | -0.074405 | 34/40 | -0.098023 |
| l40 | 40/40 | +0.001982 | 39/40 | -0.013062 |
| s51 | 40/40 | +0.019939 | 38/40 | -0.006188 |
| s76 | 40/40 | +0.004566 | 32/40 | -0.020176 |
| r74 | 38/40 | -0.010867 | 35/40 | -0.030731 |

## PROT: all 13 strategies, real 1h path, 15bp/side

Totals below use each PROT trade's original risk denominator. “P1” is the pre-existing `P1_own` comparison, not a newly selected setting. All 2,574 published variant/cost/period/path jobs were re-costed, including the other protection variants and runner references. No PROT job touches LUNC; every row in this older-history panel touches other approved proxies.

| Strategy | BASE old | BASE fee/slip only | BASE re-costed | BASE signed funding | P1 re-costed | New P1−BASE |
|---|---:|---:|---:|---:|---:|---:|
| bear_breakdown | +12.509 | +20.930 | +20.215 | -0.715 | +30.225 | +10.010 |
| breakout_pyramid | +235.889 | +262.168 | +250.042 | -12.127 | +173.220 | -76.821 |
| donchian_ens | +73.110 | +91.563 | +85.901 | -5.663 | +83.343 | -2.558 |
| ema_mom | +68.509 | +78.612 | +74.928 | -3.685 | +52.058 | -22.870 |
| ema_st | +37.575 | +58.440 | +54.173 | -4.267 | +133.233 | +79.059 |
| exp_breakout | -99.792 | -71.217 | -77.436 | -6.219 | -90.766 | -13.329 |
| exp_mom_short | -1.069 | +4.664 | +4.049 | -0.615 | +18.121 | +14.072 |
| exp_range | -181.944 | -172.072 | -170.517 | +1.555 | -167.578 | +2.940 |
| exp_trend | -7.871 | -2.718 | -3.919 | -1.201 | -2.511 | +1.408 |
| hot_coin | +24.055 | +31.869 | +27.935 | -3.934 | +35.323 | +7.388 |
| pullback_rsi | -74.025 | -56.049 | -57.961 | -1.912 | -55.069 | +2.892 |
| rotation | +108.573 | +132.544 | +117.966 | -14.578 | +116.228 | -1.739 |
| squeeze_tp | -7.419 | +7.522 | +3.500 | -4.021 | +15.406 | +11.906 |

For the eight strategies already recommended in PROT-CMP-01, summed P1−BASE at 15bp:

| Original test | Old difference R | Re-costed difference R | Proxy candle-range sensitivity R |
|---|---:|---:|---:|
| 4h A | +72.966 | +72.977 | +72.977 to +72.977 |
| 4h B | +68.461 | +67.589 | +67.589 to +67.589 |
| 4h L | +20.404 | +21.569 | +21.506 to +21.632 |
| Real 1h path | +103.740 | +104.440 | +104.395 to +104.486 |
| 1h worst path | +57.291 | +58.350 | +58.304 to +58.397 |
| 4h worst A | +26.558 | +27.642 | +27.642 to +27.642 |
| 4h worst B | +64.236 | +66.266 | +66.266 to +66.266 |
| 4h worst L | -20.157 | -16.401 | -16.461 to -16.339 |

The earlier aggregate protection finding remains in these fixed books: real-1h-path P1−BASE is +104.440R versus +103.740R before. The harsh 4h-worst older-period result remains negative (−16.401R); no blanket robustness claim is made. The improvement remains concentrated in ema_st (+79.059R of +104.440R). This audit does not reselect the eight, accept a strategy, or authorize a configuration change.

[prot_all_variants.csv](completed/prot_all_variants.csv) contains per-strategy funding deltas for every original variant at 7/15/23bp; [prot_BASE_vs_P1.csv](completed/prot_BASE_vs_P1.csv) contains all 351 paired comparisons, proxy flags and timing/price sensitivities.

## Accounting and sensitivity conventions

The prior results already included proxy funding: the engine charged `quantity × bar_close × 0.00005` per managed 4h bar, on both sides. The funding-description error in PROT was corrected at `ecaff93d`. We removed the **actual recorded old proxy charge per trade** before adding `−side × held_quantity × historical_mark × historical_rate`. Fixed historical quantities, fees, slippage, partial closes, adds and risk denominators are preserved; there is no native-funding equity-feedback re-sizing.

Primary timing places ordinary taker entries/gap fills at candle open, signal/time exits at the final millisecond, and other intrabar fills at open. Late timing moves uncertain intrabar fills to the final millisecond. The separate boundary stress settles funding on the scheduled minute before equal-time fills, rather than treating the API's few-millisecond jitter as proof of actual execution eligibility. Primary timing retains exact returned timestamps. Full conventions and the owner's written proxy extension are in [REPRICE_CONVENTIONS.md](REPRICE_CONVENTIONS.md).

**Proxy sensitivity is a candle-range substitution sensitivity, not a guaranteed bound on the true settlement mark or actual paid funding.** It holds primary eligibility and rates fixed and evaluates the selected candle's low/high with the correct cash-flow sign. Timing sensitivities are separate. Small floating-point differences at approximately 1e−12R are treated as numerical round-off. The paired PROT envelope is conservative because it combines each book's extremes independently.

These are closed-trade books. Open-position P&L/costs, portfolio interactions, native-equity-feedback sizing, actual exchange fill eligibility, selection bias and the original unresolved 1h-path candles are not resolved by this audit. No consumed period becomes a sealed holdout and no positive result is a live-profitability claim.

## Validation and evidence

- 57 targeted audit tests passed, zero failures/errors/skips. [validation.json](completed/validation.json) records the tested code head; no full engine suite or runtime action was performed.
- All 640 lead order/path replays passed trade-by-trade identity and 7/15/23bp P&L parity. All 2,574 PROT replays matched published counts and total R. [replay_parity/](completed/replay_parity/) records provenance.
- Independent read-only accounting and summary checks reproduced the lead baselines/medians, all 2,574 PROT keys/counts/totals, all reported sensitivity envelopes, and the aggregate comparisons above. These checks are not independent event-by-event funding reproduction or economic acceptance.
- [matrix_inventory.json](completed/matrix_inventory.json): 4,494 expected and actual jobs; no missing, extra or incomplete result. No funding rate or required mark was zeroed or silently omitted.
- [public_coverage.json](completed/public_coverage.json) and [public_source_manifest.json.gz](completed/public_source_manifest.json.gz) retain every query range, raw-response hash and mark source. Large raw archives, replay tapes and per-trade deltas remain cached in the isolated clone, with hashes and sizes in [local_artifact_manifest.json](completed/local_artifact_manifest.json).

The earlier four-window source probe and byte-preserved baseline inputs remain as historical evidence. Its prior incomplete economics are superseded by this approved, explicitly proxied full-basket result, not relabelled as native-price observations.

## Reproduce on this workstation

Use the pinned source/data manifests and cached fixed-book tapes. A new clone must use `git clone -c core.autocrlf=false`. Public acquisition is serially paced globally for REST funding requests, bounded for archive downloads, checksum-verified and stops on rate-limit responses.

```
python tools/research/lane2_funding_history.py --jobs 2
python tools/research/lane2_mark_sources.py --once --jobs 4
python tools/research/lane2_funding_recost.py --tapes <complete tapes> --allow-all-labelled-proxies --out <new output>
python tools/research/lane2_funding_report.py --jobs <leads/job_deltas.json> <prot/job_deltas.json> --out <new report>
python tools/research/lane2_funding_package.py --out <new report> --details <leads/trade_deltas.jsonl.gz> <prot/trade_deltas.jsonl.gz>
```

The committed conservative [funding_windows.json](completed/funding_windows.json) can be copied into `research_cache/lane2/` before a fresh public fetch. Lead replay helper stages its pinned source and verifies shared-cache hashes. PROT source is staged from `ecaff93d272deca7fa80eb9decc1620c23286ed0` into `.git/prot_replay_source`; `lane2_prot_replay.py` verifies source/data fingerprints and published parity, and its `--consolidate` mode inventories the complete published matrix. Cached inputs and exact replay hashes are in the two re-cost manifests and local-artifact manifest.

ROADMAP FIT: complete the bounded historical funding audit for r6/s54/l24/s26/l40/s51/s76/r74 and all PROT-CMP-01 strategies/variants.
DEFERRED: strategy reselection or qualification, forward/PAPER activation, native-equity-feedback replay, and all account/key/runtime/settings/scheduler/watchdog/order/install actions. No merge or mark-ready.
LANES: Build — fixed-book reconstruction complete; Evidence — public coverage, proxy ledger and independent summary checks complete; Integration — draft result handoff to Claude, no operational change.
