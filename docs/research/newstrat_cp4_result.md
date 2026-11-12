# NEWSTRAT-CP4: five hourly long/short candidates for account 4, none passes (2026-11-12)

Source: Claude Projects. research_only, default-OFF, not registered. Rules and gate frozen at `13040a84` before any run
(`research/newstrat_cp4.py`, runner `research/newstrat_cp4_run.py`). One pre-run design fix (Z1 volume filter, made from
signal counts only) is recorded in the file. Unchanged `BT.run` (master `7b5beecb`). 8 coins with 1h files from
2022-08-26 to 2026-10-04 (BTC ETH XRP SOL DOGE BNB LINK AVAX; **ADA missing**: no long 1h file). $4,000, risk 0.002 ($8 at
start; sizing compounds with equity, not fixed $8), 1 position per sleeve, testnet filters, funding proxy 0.01%/8h charged
on both sides (not native funding). Fixed exits: 3 ATR stop, target, time cap; no protection mover, so worst-fill and
normal-fill results are identical. Periods are consumedDEV early (<2025) and consumedDEV later (>=2025); neither is an
untouched holdout. Turnover fallback used: 0 trades in every case. Costs are per executed side.

| ID | Candidate | closes/wk | early n / R@15 | later n / R@15 | R@7 | R@15 | R@23 | cost/R @15 | closed-R DD@15 | Verdict |
|---|---|---|---|---|---|---|---|---|---|---|
| Z1 | Low-volume impulse fade | 1.1 | 139 / -0.064 | 105 / -0.026 | +0.012 | -0.048 | -0.107 | 9% | 15R | REJECT |
| Z2 | 48h regression t-stat trend | 3.6 | 459 / -0.155 | 312 / +0.034 | -0.012 | -0.078 | -0.145 | 11% | 84R | REJECT |
| Z3 | Ichimoku cloud breakout | 4.0 | 504 / +0.002 | 361 / -0.091 | +0.033 | -0.037 | -0.106 | 11% | 46R | REJECT |
| Z4 | Parabolic SAR flip + ADX>=25 | 4.2 | 524 / -0.041 | 369 / -0.061 | +0.011 | -0.049 | -0.109 | 10% | 51R | REJECT |
| Z5 | Daily time-series momentum on 1h | 4.0 | 490 / -0.027 | 368 / -0.127 | +0.012 | -0.070 | -0.151 | 14% | 78R | REJECT |

**Read.** Gross edge is about zero for all five (at most +0.03R per trade at 7 bp). Costs are 9-14% of R, so the
losses come from missing edge, not from fees. Together with Codex C1H (13 hypotheses) and PR #334 (4 hourly ideas),
single-coin hourly price patterns on these 8 coins show no tradeable edge. H5 volume-impulse long stays the only hourly
survivor.
