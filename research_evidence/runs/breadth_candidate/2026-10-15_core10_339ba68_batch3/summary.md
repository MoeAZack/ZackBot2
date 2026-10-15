# Breadth backtest batch 3 — CORE-10 profit_capture vs defaults (TREND/RANGE/BREAKOUT/VWAPREV), all rows on runner c8c7b92

REVIEW-ONLY research evidence. non_promotable. Development window only; not evidence of edge.

- commit: `c8c7b927ee575ba71a9724ce84d23f72774b07b8` for the comparison rows, which are the `parity/*_rerun_c8c7b92.json` packets. The 8 top-level profit_capture packets listed under **Packets** were produced on runner commit `339ba68`; their numbers are identical to the c8c7b92 reruns (see `parity/`). Provenance wording clarified per Cowork 6199914527.
- window: 2024-10-14T00:00:00Z -> 2026-09-28T00:00:00Z (end-exclusive, 4284 4h bars; warm-up 220 bars from 2024-09-07T08:00:00Z)
- runner sha256: `run_breadth_candidate.py` dad619aaf2df00fd79ce9a2ca2d4848f298b745920fe7f6c56898e7b4f6922f2 · `run_vwaprev_candidate.py` c2f43564cc1da4cd79e12253ece82d10b3f82c1ecdbbc11aeafa80202060d23e
- universe: static-top40-minhist365-contract-20261011 tier CORE-10 (digest 1c06c18d2fc82413…); run symbols: BTCUSDT, ETHUSDT, ZECUSDT, SOLUSDT, XRPUSDT, NEARUSDT, DOGEUSDT, UNIUSDT, SUIUSDT
- costs: taker fee 0.0005 per side, slippage 0.0002 per side, funding 5e-05 of notional per 4h bar; market (taker) entries; legacy exchange rules
- account (app defaults): start 500.0, risk 1%/trade, max_pos 4, max_lev 10, daily halt 8%
- mgmt: `defaults` = frozen strategy mgmt (empty override). `profit_capture` exp_breakout: override {"be_r": 1.0, "tp1_frac": 0.5, "tp1_r": 1.25} -> merged {"be_r": 1.0, "max_bars": 60, "stop_atr": 2.0, "tp1_frac": 0.5, "tp1_r": 1.25, "tp_r": 3.0}; `profit_capture` exp_range: override {"be_r": 0.5, "max_bars": 18, "tp1_frac": 0.5, "tp1_r": 0.5} -> merged {"be_r": 0.5, "max_bars": 18, "stop_atr": 1.5, "tp1_frac": 0.5, "tp1_r": 0.5}; `profit_capture` exp_trend: override {"be_r": 1.0, "tp1_frac": 0.33, "tp1_r": 1.5} -> merged {"be_r": 1.0, "max_bars": 180, "stop_atr": 2.5, "tp1_frac": 0.33, "tp1_r": 1.5, "tp_r": 3.0}; `profit_capture` exp_vwaprev: override {"be_r": 0.5, "max_bars": 18, "tp1_frac": 0.5, "tp1_r": 0.5} -> merged {"be_r": 0.5, "max_bars": 18, "stop_atr": 1.5, "tp1_frac": 0.5, "tp1_r": 0.5}; Source: https://github.com/MoeAZack/ZackBot2/issues/13#issuecomment-6198970276

| key | side | registry_sides | mgmt | status | bust (time · first dead trade # · post-bust trades / net R, excluded) | trades | net R | net PnL | PF | win % | max DD (R) | max DD (equity) | fee share | MFE R | MAE R* | giveback R | R above / below BTC EMA200 |
|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|
| exp_breakout | long | both | defaults (c8c7b92) | ok | no | 489 | 86.69 | 513.20 | 1.29 | 33.7% | 36.63 | -32.7% | 11.8% | 1.21 | 0.82 | 1.03 | 70.8 (323) / 15.9 (166) |
| exp_breakout | long | both | profit_capture | ok | no | 506 | 43.03 | 212.15 | 1.15 | 43.5% | 26.72 | -25.4% | 22.1% | 1.18 | 0.77 | 1.09 | 23.6 (338) / 19.5 (168) |
| exp_breakout | short | both | defaults (c8c7b92) | ok | no | 420 | -22.16 | -132.79 | 0.87 | 31.4% | 43.38 | -38.2% | n/a (gross<=0) | 1.12 | 0.79 | 1.17 | 7.3 (185) / -29.5 (235) |
| exp_breakout | short | both | profit_capture | ok | no | 439 | -7.41 | -62.48 | 0.94 | 39.4% | 28.39 | -27.7% | 303.1% | 1.10 | 0.74 | 1.11 | 4.3 (194) / -11.7 (245) |
| exp_range | long | both | defaults (c8c7b92) | ok | no | 606 | -11.07 | -87.63 | 0.94 | 48.2% | 42.88 | -39.2% | 283.1% | 0.85 | 0.87 | 0.87 | 11.2 (230) / -22.3 (376) |
| exp_range | long | both | profit_capture | ok | no | 702 | -17.87 | -98.10 | 0.90 | 66.0% | 36.57 | -32.7% | 809.4% | 0.74 | 0.71 | 0.76 | -2.0 (267) / -15.8 (435) |
| exp_range | short | both | defaults (c8c7b92) | ok | no | 653 | -20.00 | -121.42 | 0.91 | 50.1% | 36.96 | -33.6% | n/a (gross<=0) | 0.82 | 0.91 | 0.85 | -46.6 (465) / 26.6 (188) |
| exp_range | short | both | profit_capture | ok | no | 725 | -47.00 | -200.48 | 0.80 | 63.4% | 47.00 | -40.3% | n/a (gross<=0) | 0.74 | 0.76 | 0.80 | -53.1 (519) / 6.1 (206) |
| exp_trend | long | both | defaults (c8c7b92) | ok | no | 101 | 27.45 | 147.03 | 1.80 | 36.6% | 4.91 | -8.0% | 7.2% | 0.98 | 0.48 | 0.70 | 26.2 (85) / 1.2 (16) |
| exp_trend | long | both | profit_capture | ok | no | 102 | 30.90 | 172.19 | 2.22 | 41.2% | 4.75 | -7.0% | 6.6% | 0.98 | 0.42 | 0.67 | 30.1 (86) / 0.8 (16) |
| exp_trend | short | both | defaults (c8c7b92) | ok | no | 101 | 0.32 | -3.29 | 0.98 | 31.7% | 13.35 | -12.9% | 70.4% | 0.81 | 0.57 | 0.80 | -2.3 (30) / 2.7 (71) |
| exp_trend | short | both | profit_capture | ok | no | 101 | -7.73 | -39.11 | 0.76 | 24.8% | 11.35 | -11.8% | n/a (gross<=0) | 0.74 | 0.52 | 0.81 | -2.2 (30) / -5.6 (71) |
| exp_vwaprev | long | both | defaults (c8c7b92) | ok | no | 623 | -30.12 | -158.63 | 0.89 | 46.4% | 61.24 | -49.2% | n/a (gross<=0) | 0.84 | 0.91 | 0.89 | 8.7 (236) / -38.8 (387) |
| exp_vwaprev | long | both | profit_capture | ok | no | 723 | -36.38 | -165.99 | 0.83 | 64.5% | 54.41 | -43.7% | n/a (gross<=0) | 0.73 | 0.74 | 0.78 | -9.3 (275) / -27.1 (448) |
| exp_vwaprev | short | both | defaults (c8c7b92) | ok | no | 658 | -25.47 | -142.33 | 0.89 | 49.7% | 42.45 | -37.3% | n/a (gross<=0) | 0.82 | 0.92 | 0.86 | -53.9 (464) / 28.4 (194) |
| exp_vwaprev | short | both | profit_capture | ok | no | 720 | -42.90 | -188.04 | 0.82 | 63.5% | 43.60 | -37.8% | n/a (gross<=0) | 0.75 | 0.77 | 0.81 | -52.8 (510) / 9.9 (210) |

\* MAE is a bar-extreme approximation over [entry bar, exit bar] (the exit bar may include movement after the exit). Busted slices (equity <= 10% of the start) show PRE-BUST stats in every row and headline; the post-bust trades are listed in the bust column and excluded. MFE uses the backtest path best price. Both in stop-distance R. Fee share = fees / gross (gross = price P&L after slippage, before fees and funding); n/a when gross <= 0.

## INCOMPLETE symbols (excluded from every aggregate, never filled)

- HYPEUSDT: 1590 of 4504 4h candles missing in [warm-up start, END) (2024-09-07T08:00:00Z .. 2025-05-30T04:00:00Z), off-grid 0, duplicates 0

## Headline (registry sides only)

Registry-side slices with trades: 16. Off-registry slices excluded from this headline: 0 (none).

| key | side | mgmt | trades | net R | PF |
|---|---|---|---|---|---|
| exp_breakout | long | defaults (c8c7b92) | 489 | 86.69 | 1.29 |
| exp_breakout | long | profit_capture | 506 | 43.03 | 1.15 |
| exp_trend | long | profit_capture | 102 | 30.90 | 2.22 |
| exp_trend | long | defaults (c8c7b92) | 101 | 27.45 | 1.80 |
| exp_trend | short | defaults (c8c7b92) | 101 | 0.32 | 0.98 |
| exp_breakout | short | profit_capture | 439 | -7.41 | 0.94 |
| exp_trend | short | profit_capture | 101 | -7.73 | 0.76 |
| exp_range | long | defaults (c8c7b92) | 606 | -11.07 | 0.94 |
| exp_range | long | profit_capture | 702 | -17.87 | 0.90 |
| exp_range | short | defaults (c8c7b92) | 653 | -20.00 | 0.91 |
| exp_breakout | short | defaults (c8c7b92) | 420 | -22.16 | 0.87 |
| exp_vwaprev | short | defaults (c8c7b92) | 658 | -25.47 | 0.89 |
| exp_vwaprev | long | defaults (c8c7b92) | 623 | -30.12 | 0.89 |
| exp_vwaprev | long | profit_capture | 723 | -36.38 | 0.83 |
| exp_vwaprev | short | profit_capture | 720 | -42.90 | 0.82 |
| exp_range | short | profit_capture | 725 | -47.00 | 0.80 |

registry_sides = strategies.STRATEGIES[key]['sides'] as recorded in each packet. An off-registry side is one the registry does not list for that key: the runner can still simulate it (the signal function emits it), but it is hypothetical and is excluded from every headline line. Not evidence of edge.

## Per-symbol net R

| key | side | BTCUSDT | ETHUSDT | ZECUSDT | SOLUSDT | XRPUSDT | NEARUSDT | DOGEUSDT | UNIUSDT | SUIUSDT |
|---|---|---|---|---|---|---|---|---|---|---|
| exp_breakout | long | -1.1 (74) | -8.5 (73) | 12.4 (80) | 10.6 (60) | 11.5 (50) | 6.2 (56) | 6.7 (34) | 1.1 (42) | 4.2 (37) |
| exp_breakout | short | 0.3 (61) | -12.9 (43) | -2.3 (58) | 3.0 (56) | -2.1 (49) | 0.6 (50) | 3.9 (37) | 6.9 (38) | -4.8 (47) |
| exp_range | long | -0.3 (81) | -3.3 (79) | 4.7 (72) | 2.6 (74) | -5.2 (97) | -6.7 (83) | 0.2 (82) | -4.2 (63) | -5.6 (71) |
| exp_range | short | -11.0 (88) | 0.2 (76) | -7.5 (108) | 1.5 (79) | 2.7 (66) | -9.2 (90) | -6.6 (77) | -1.5 (69) | -15.6 (72) |
| exp_trend | long | 3.0 (13) | 5.5 (12) | 0.4 (12) | 11.4 (10) | 0.5 (8) | -2.8 (9) | 1.6 (6) | 9.6 (15) | 1.6 (17) |
| exp_trend | short | -2.9 (11) | -0.5 (11) | -1.4 (8) | -0.9 (11) | -0.4 (13) | 0.7 (10) | -2.2 (11) | -0.0 (10) | -0.2 (16) |
| exp_vwaprev | long | -3.3 (84) | -8.1 (77) | 2.9 (73) | -2.8 (74) | -3.7 (97) | -11.3 (88) | -0.5 (82) | -2.3 (71) | -7.4 (77) |
| exp_vwaprev | short | -12.5 (88) | 5.2 (73) | -6.4 (105) | 3.8 (80) | 3.1 (66) | -11.2 (91) | -7.0 (75) | -4.4 (69) | -13.6 (73) |

## Packets

| file | packet_sha256 (canonical body) | file sha256 |
|---|---|---|
| exp_breakout_long_profit_capture.json | `771b66397d86b3c2745f38b08700d382e2fc14c2f5c8d2402b860d5e83f0dc96` | `be4e3d9655c54d07b81a6eb9f6bdf40b35abee9f86b3a66319521ef8aa74483f` |
| exp_breakout_short_profit_capture.json | `00f8cd0a023cbe68dea7e8c3e305fe2973474ea6a95c0257d6049887ff47a444` | `7ad27e696f703eb3a763f560e09a3aeda42bf3f04dff1951252c63cc8ab73705` |
| exp_range_long_profit_capture.json | `0b823cbb1e5a95089b54268a1df726e5dea19b8ff4456f2e75a63bc59f483d24` | `ed44dccdff1a1d426f259d39755551c60769d862501b9f8f30aaeba874fa1e6a` |
| exp_range_short_profit_capture.json | `aef2cccf2b9face6fa490bd29aebd14da5f4e84f704fb7b39b7dd11d0c0a52c7` | `9e9bbb06340920ba13059b93a7868ac6c6d543a0465277fc3cb3563f1d57416c` |
| exp_trend_long_profit_capture.json | `9d7dc15a60eb850e0129da2d438f1600efcc7870dd958eed740b2f648f8cbf3e` | `119608c0785ef1e415bdbdc8c003e9b3c28f8c74e0af61601c6b8b48a3e0a484` |
| exp_trend_short_profit_capture.json | `a12ebb735239a04f809fa91f6d0c826363dd92ada50a5253c36da52de2a61819` | `07a623610a4fe62ce0bab79dec873f5f6d0097079fbf53aeb0403a7d85ce4946` |
| exp_vwaprev_long_profit_capture.json | `fd9691ca2370b3b8489c0f842059c5eaa939491913e9fb7f418a96ae0fe58cb5` | `a56fd0058d812c50d3476b00a435f9ab05ce5f0126938a9811e14b08a3b0ad45` |
| exp_vwaprev_short_profit_capture.json | `edf5fa1c02b27826fcec97d2b63981d041dfdfac2e807f8e23163c34c6311f7d` | `b342870354200945754cc770bb8014d3a70081137bf694bab48f0fdc253e5438` |
