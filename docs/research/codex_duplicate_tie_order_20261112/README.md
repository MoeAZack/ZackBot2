# Independent duplicate symbol tie-order replay — 12 Nov 2026

Research only; no runtime, account, keys, trading or policy changes. Reproduction targets PR 339 review head `09eb1e9ebd5e1ff0a9747a386faf24612c78a6b0`, point 2, using the independent evidence associated with `5121e791a41b60c24d375de4028130c1dea93983` (full evidence commit verified separately by integration). The independent tape generation source model head is `7b5beecb3a964d4ce36aa7187a734093ffe6818a`; these are different identifiers and must not be conflated. Exact PR 339 implementation or tape equivalence is not asserted.

Input: `F:\ZackBot2_data\DUP-COIN-SIDE-INDEPENDENT-20261112\overlay\overlay_sim.py` and frozen `tapes` (worst) / `tapes-path` (normal path), all hashes in manifest. Original simulator SHA256 `c8bacf9bf1ad206bd247816e2670c4e9869238c04a7a623d3bc02df5c4eac63c`, identical to byte-for-byte preserved copy; post-run rehash confirmed original and all 102 source tape files unchanged. Modified copy adds only an optional symbol-order argument and rank in the existing event-sort key. Timestamp, exit-before-entry, declared sleeve priority, permuted symbol and unique ID are the successive keys. Symbol ranking affects events tied on timestamp, event type AND sleeve, including exits; no other policy or tape rules changed. Baseline and policy run under the same order. Seed 0 alphabetical, seeds 1–39 shuffle sorted symbols independently with Python `random.Random(seed)`; orders recorded per row. Equity starts $4,000; fixed risk, no funding, exit-bar close, standard overlay guardrails; returns use closed-equity P&L. Cost in bp per executed side. No fresh signal generation or position-path feedback; fixed consumed DEV tapes, fixed universe and a finite deterministic sample, not point-in-time selection, holdout qualification, causal proof or evidence of live profitability.

Specific exclusion removes exactly one candidate from both paired policies: `ema_st_L|XRPUSDT|1|2024-11-06T12:00:00+00:00`, worst-tape 15bp net +70.863983R; it is not removal of all XRP. Counts are strictly positive comparisons out of 40; p10/p90 linear interpolation (pandas quantile). Percentage-point values are policy return minus paired baseline, not policy absolute profit.

| Tape / book / policy | 15bp wins | median pp | p10 | p90 | minimum | maximum |
|---|---:|---:|---:|---:|---:|---:|
| worst A4 4h 2y cap2 | 40/40 | +0.583 | +0.359 | +0.952 | +0.319 | +0.962 |
| worst A4 4h long cap2 | 37/40 | +0.728 | +0.087 | +1.131 | -0.542 | +1.131 |
| path A4 4h 2y cap2 | 40/40 | +0.583 | +0.359 | +0.952 | +0.319 | +0.962 |
| path A4 4h long cap2 | 40/40 | +1.277 | +1.272 | +1.640 | +1.211 | +1.915 |
| worst OLD3 2y refuse | 29/40 | +1.085 | -0.436 | +2.689 | -1.184 | +3.919 |
| path OLD3 2y refuse | 36/40 | +1.902 | +0.152 | +3.535 | -0.918 | +4.877 |
| worst OLD3 long refuse, XRP candidate removed | 40/40 | +10.025 | +5.676 | +14.601 | +2.183 | +17.139 |
| path OLD3 long refuse, XRP candidate removed | 40/40 | +7.440 | +3.487 | +10.567 | +0.805 | +16.023 |

At 15bp the A4 result supports a frequent small positive difference across sampled orders, but worst long is negative for three orders. The OLD3 long result supports the 40/40 positive direction after removing that exact lot but the magnitude differs from Claude's reported +7.9 median, +3.4 p10, +15.1 p90. OLD3 2y does not always favor refusal. At 7bp, OLD3 long excluding XRP has one negative order in each path, so 15bp consistency is not universal across costs. 7/23bp details and all 1,200 paired outcomes reside in results.csv.

Verification: all 30 groups contain 40 orders; all 102 input hashes rechecked after run. Alphabetical seed 0 reproduces saved validated baseline and policy terminal returns exactly for all eight 15bp tape/book cases. Command: `python -u F:\ZackBot2_data\DUP-TIE-REPRO-20261112\tie_repro.py`. The driver overwrites its own output files; retain the saved results before rerunning. No merge or enablement decision follows from this study.

## Separate 15bp funding stress

The following retains each paired order and policy but adds 3bp per 8h funding debit for both long and short positions. It is a stress proxy, not native historical funding: the simulator charges at close based on the initial notional-to-risk ratio and the number of 8h boundaries spanned, without changing notional with market price or equity, and without settlement-time cash flows. Sizing remains fixed initial-equity risk (not compounding). Differences are relative to baseline, not evidence of absolute profit. No causal interpretation or deployment inference is warranted.

| Tape / book / policy | wins | median pp | p10 | p90 | minimum | maximum |
|---|---:|---:|---:|---:|---:|---:|
| worst A4 4h 2y cap2 | 40/40 | +0.607 | +0.374 | +0.978 | +0.320 | +0.986 |
| worst A4 4h long cap2 | 37/40 | +0.757 | +0.175 | +1.154 | -0.493 | +1.154 |
| path A4 4h 2y cap2 | 40/40 | +0.607 | +0.374 | +0.978 | +0.320 | +0.986 |
| path A4 4h long cap2 | 40/40 | +1.301 | +1.297 | +1.661 | +1.231 | +1.946 |
| worst OLD3 2y refuse | 36/40 | +1.568 | +0.132 | +3.376 | -0.606 | +5.771 |
| path OLD3 2y refuse | 38/40 | +2.366 | +0.652 | +4.068 | -0.365 | +5.291 |
| worst OLD3 long refuse, XRP candidate removed | 40/40 | +13.247 | +8.485 | +17.030 | +3.632 | +21.551 |
| path OLD3 long refuse, XRP candidate removed | 40/40 | +8.886 | +5.012 | +12.142 | +2.291 | +17.439 |

Full 400 paired observations and exact permutations: `funding_results.csv`; independent manifest: `funding-stress/manifest.json`. Run command: `python -u F:\ZackBot2_data\DUP-TIE-REPRO-20261112\funding_repro.py`. Its funding argument is `funding_bps=3`; primary no-funding output is separate and preserved.
