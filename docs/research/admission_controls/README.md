# ADMISSION-CONTROLS: H5 and EMA-long alone at actual capacity (descriptive, consumedDEV, no label)

Contract: Codex GO PR #337 6835533713. Source, tests, contract and trial record frozen at 29d76073 before outcomes. Reuses BASKET-OLD3-H5 @347c4b71 (data c11ed256, rules d1ef2a9c, window 2024-11-17 12:00 to 2026-09-28 00:00 UTC, fixed $8 / $3,333.33).
Native engine runs with max_pos 1 (H5) / 2 (EMA) from the start, native 3% Cairo-day halt and 2.5% open-risk guard on, fixed sizing helper. 24 cases, all retained in `results/`.

| Sleeve | Order | Cost | Net $ (terminal) | Net % | Admitted | Entries/wk | Marked DD % | Before 2025-10-01 $ | After $ | Top coin share | Ex-best-coin $ |
|---|---|---|---|---|---|---|---|---|---|---|---|
| EMA | alpha | 7 | +218 | +5.45 | 73 | 0.75 | 4.6 | +46 | +82 | 0.54 (ETHUSDT) | +99 |
| EMA | cohort | 7 | +221 | +5.52 | 72 | 0.74 | 4.0 | +18 | +113 | 0.54 (ETHUSDT) | +102 |
| EMA | alpha | 15 | +195 | +4.88 | 73 | 0.75 | 4.9 | +36 | +69 | 0.59 (ETHUSDT) | +80 |
| EMA | cohort | 15 | +198 | +4.95 | 72 | 0.74 | 4.2 | +9 | +100 | 0.58 (ETHUSDT) | +83 |
| EMA | alpha | 23 | +172 | +4.31 | 73 | 0.75 | 5.2 | +26 | +56 | 0.65 (ETHUSDT) | +61 |
| EMA | cohort | 23 | +175 | +4.37 | 72 | 0.74 | 4.5 | -1 | +87 | 0.64 (ETHUSDT) | +63 |
| H5 | alpha | 7 | +273 | +6.82 | 209 | 2.15 | 2.6 | +92 | +180 | 0.36 (LINKUSDT) | +175 |
| H5 | cohort | 7 | +286 | +7.14 | 207 | 2.13 | 2.2 | +72 | +218 | 0.34 (LINKUSDT) | +189 |
| H5 | alpha | 15 | +158 | +3.95 | 209 | 2.15 | 3.3 | +37 | +121 | 0.58 (LINKUSDT) | +66 |
| H5 | cohort | 15 | +169 | +4.22 | 207 | 2.13 | 2.9 | +16 | +156 | 0.54 (LINKUSDT) | +77 |
| H5 | alpha | 23 | +44 | +1.09 | 209 | 2.15 | 4.2 | -19 | +62 | 2.00 (LINKUSDT) | -43 |
| H5 | cohort | 23 | +52 | +1.31 | 207 | 2.13 | 3.7 | -39 | +95 | 1.67 (LINKUSDT) | -35 |

Worst path is identical to path for both sleeves at every cost (no intrabar stop moves in either).

## Parity and checks
- Native identity (fills cash - native funding == trade P&L) held for every closed lot; max entry risk $8.00; no exchange skips; no partials (neither sleeve has tp1).
- Guards: rerunning every case with the halt and open-risk guard off changed **0** entries; native rule blocks 0. NONPARITY: the target replay refused **0** native lots in all 24 cases.
- Native vs target funding (closed lots, 15bp alpha): H5 28.28 vs 28.44; EMA 25.18 vs 26.38 (4h cadence vs hourly proxy).
- Elapsed holds: H5 median 35 h, max 49 h (48 native bars + exit-bar end convention); EMA median 68-74 h, max 448 h.

## Versus the basket order-0 overlay (alpha order, same cost/path)
- EMA: identical, 73/73 lots in common; the phantom approximation did not affect EMA.
- H5: 209 admitted here vs 203 in the overlay; 183 in common, **26 newly admitted, 20 missed**. Economics close: 15bp +$158 here vs +$151 overlay.

No promotion label, no DD threshold. A positive consumedDEV control does not authorize activation or count as a new strategy; Integration decides any forward-evidence step. BEAR/HOT path attribution remains deferred.
