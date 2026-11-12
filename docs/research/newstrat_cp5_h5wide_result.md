# CP5 hourly families and H5 on 11 new coins (2026-11-12)

Source: Claude Projects. Research only; no account, key, or #338 change. Costs per executed side, funding proxy 0.01%/8h on
both sides (not native funding), $4,000 with $8 initial risk (compounding), 1 position per sleeve. All periods are
consumedDEV; none is a holdout. Turnover fallback trades: 0 everywhere. Fixed exits, so worst fills = normal fills.

## CP5 (frozen at `8ee7e8a5`; 8 coins 2022-08..2026-10, ADA missing)
| ID | Candidate | closes/wk | early n/R@15 | later n/R@15 | R@7 | R@15 | R@23 | cost/R | closed-R DD | Verdict |
|---|---|---|---|---|---|---|---|---|---|---|
| V1 | Value-area re-entry (80% rule) | 6.2 | 780/-0.112 | 557/-0.219 | -0.091 | -0.156 | -0.222 | 11% | 220R | REJECT |
| V2 | Walk-forward hour-of-day seasonality | 16.0 | 1490/-0.048 | 1927/-0.102 | -0.018 | -0.078 | -0.139 | 11% | 289R | REJECT |

## H5-WIDE (frozen at `f3a9abae`): merged H5 long module, unchanged, on 11 coins outside its selection universe
Coins: 1000PEPE ENA HYPE LSK NEAR PUMP QNT SUI UNI WLD ZEC (owner PC `candles_1h_fib02`, input hashes in
`research/h5wide/input_sha256.txt`). Split at 2025-10-01. Caveat: the coin list was assembled for earlier research, so it is
a survivor set.

| Run | window | early n/R@15 | later n/R@15 | R@7 | R@15 | R@23 | cost/R | ex-best R@15 | top-5 share | closed-R DD | closes/wk | Gate |
|---|---|---|---|---|---|---|---|---|---|---|---|---|
| Frozen, 11 coins | 2025-07-10..2026-09-27* | 31/+0.636 | 153/+0.170 | +0.283 | +0.249 | +0.214 | 6% | +0.185 | 43% | 15.6R | 2.9 | PASS |
| Post-hoc, 9 coins (no HYPE, PUMP) | 2024-10-04..2026-09-27 | 129/+0.025 | 136/+0.140 | +0.118 | +0.084 | +0.050 | 6% | +0.027 | 89% | 19.4R | 2.6 | PASS |

\* `BT.Book` aligns all coins to the latest listing (PUMP), so the frozen 11-coin run only covers 15 months. The post-hoc
9-coin run drops the two late listings to recover the full window. It was added after the frozen result was seen; it is a
sensitivity check, not a new selection. In the full window the early half is near flat (+0.025R; -0.008R at 23 bp),
2025 is about -0.8R, 2026 is +19R, and NEAR plus ZEC carry most of the profit.

**Read.** H5 carries over to new coins, after costs, in both runs. That is the first out-of-universe support for any
hourly rule here. The edge is thin and lumpy: about +0.08R per trade over the full window, concentrated in 2026 and a few
coins. Adding these coins as a second H5 sleeve would give about 2.6-2.9 more trades a week. Single-coin hourly
pattern families remain dead (19 of 19 rejected: Codex 13, PR #334 4, CP4 5, CP5 2, counting families once each).
