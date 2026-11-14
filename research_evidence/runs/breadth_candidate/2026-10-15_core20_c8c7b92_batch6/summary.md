# Breadth backtest batch 6 — CORE-20 4h full coverage (gap symbols from the bounded research cache), defaults + profit_capture, bust split

REVIEW-ONLY research evidence. non_promotable. Development window only; not evidence of edge.

- commit: `c8c7b927ee575ba71a9724ce84d23f72774b07b8`
- window: 2024-10-14T00:00:00Z -> 2026-09-28T00:00:00Z (end-exclusive, 4284 4h bars; warm-up 220 bars from 2024-09-07T08:00:00Z)
- runner sha256: `run_breadth_candidate.py` dad619aaf2df00fd79ce9a2ca2d4848f298b745920fe7f6c56898e7b4f6922f2 · `run_vwaprev_candidate.py` c2f43564cc1da4cd79e12253ece82d10b3f82c1ecdbbc11aeafa80202060d23e
- universe: static-top40-minhist365-contract-20261011 tier CORE-20 (digest 1c06c18d2fc82413…); run symbols: BTCUSDT, ETHUSDT, ZECUSDT, SOLUSDT, XRPUSDT, NEARUSDT, DOGEUSDT, UNIUSDT, SUIUSDT, QNTUSDT, BNBUSDT, LSKUSDT, ENAUSDT, 1000PEPEUSDT, WLDUSDT, ADAUSDT, AVAXUSDT, LINKUSDT
- costs: taker fee 0.0005 per side, slippage 0.0002 per side, funding 5e-05 of notional per 4h bar; market (taker) entries; legacy exchange rules
- account (app defaults): start 500.0, risk 1%/trade, max_pos 4, max_lev 10, daily halt 8%
- mgmt: `defaults` = frozen strategy mgmt (empty override). `profit_capture` exp_breakout: override {"be_r": 1.0, "tp1_frac": 0.5, "tp1_r": 1.25} -> merged {"be_r": 1.0, "max_bars": 60, "stop_atr": 2.0, "tp1_frac": 0.5, "tp1_r": 1.25, "tp_r": 3.0}; `profit_capture` exp_range: override {"be_r": 0.5, "max_bars": 18, "tp1_frac": 0.5, "tp1_r": 0.5} -> merged {"be_r": 0.5, "max_bars": 18, "stop_atr": 1.5, "tp1_frac": 0.5, "tp1_r": 0.5}; `profit_capture` exp_trend: override {"be_r": 1.0, "tp1_frac": 0.33, "tp1_r": 1.5} -> merged {"be_r": 1.0, "max_bars": 180, "stop_atr": 2.5, "tp1_frac": 0.33, "tp1_r": 1.5, "tp_r": 3.0}; `profit_capture` exp_vwaprev: override {"be_r": 0.5, "max_bars": 18, "tp1_frac": 0.5, "tp1_r": 0.5} -> merged {"be_r": 0.5, "max_bars": 18, "stop_atr": 1.5, "tp1_frac": 0.5, "tp1_r": 0.5}; Source: https://github.com/MoeAZack/ZackBot2/issues/13#issuecomment-6198970276

| key | side | registry_sides | mgmt | status | bust (time · first dead trade # · post-bust trades / net R, excluded) | trades | net R | net PnL | PF | win % | max DD (R) | max DD (equity) | fee share | MFE R | MAE R* | giveback R | R above / below BTC EMA200 |
|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|
| bear_breakdown | long | short — off-registry (hypothetical) | defaults | not emitted | no | 0 | 0.00 | 0.00 | n/a | n/a | n/a | 0.0% | n/a | n/a | n/a | n/a | 0.0 (0) / 0.0 (0) |
| bear_breakdown | short | short | defaults | ok | no | 316 | 23.99 | 82.19 | 1.12 | 36.1% | 18.53 | -21.0% | 25.9% | 1.29 | 0.63 | 1.21 | 0.0 (0) / 24.0 (316) |
| breakout_pyramid | long | long | defaults | ok | no | 365 | 324.16 | 3895.37 | 2.75 | 26.8% | 27.69 | -28.6% | 2.9% | 2.06 | 0.76 | 1.17 | 272.0 (302) / 52.2 (63) |
| breakout_pyramid | short | long — off-registry (hypothetical) | defaults | ok | no | 379 | 63.44 | 105.63 | 1.08 | 26.1% | 39.97 | -39.5% | 30.3% | 1.46 | 0.75 | 1.29 | 88.9 (134) / -25.4 (245) |
| dca_dip | long | long | defaults | ok | no | 283 | -3.92 | -20.83 | 0.86 | 89.4% | 10.23 | -10.7% | n/a (gross<=0) | 0.09 | 0.38 | 0.11 | -8.4 (194) / 4.5 (89) |
| dca_dip | short | long — off-registry (hypothetical) | defaults | not emitted | no | 0 | 0.00 | 0.00 | n/a | n/a | n/a | 0.0% | n/a | n/a | n/a | n/a | 0.0 (0) / 0.0 (0) |
| donchian_ens | long | long | defaults | ok | no | 291 | 149.16 | 922.82 | 2.27 | 33.0% | 21.82 | -24.7% | 2.7% | 1.98 | 0.65 | 1.47 | 131.5 (192) / 17.6 (99) |
| donchian_ens | short | long — off-registry (hypothetical) | defaults | ok | no | 233 | 27.97 | 124.80 | 1.26 | 42.9% | 10.92 | -13.7% | 10.6% | 1.31 | 0.64 | 1.19 | 16.7 (100) / 11.2 (133) |
| ema_mom | long | long | defaults | ok | no | 160 | 101.09 | 686.74 | 2.77 | 35.0% | 7.55 | -19.7% | 3.1% | 2.13 | 0.51 | 1.50 | 57.4 (126) / 43.7 (34) |
| ema_mom | short | long — off-registry (hypothetical) | defaults | ok | no | 188 | 2.62 | 4.31 | 1.01 | 34.6% | 13.52 | -16.8% | 47.4% | 1.12 | 0.52 | 1.10 | -0.9 (81) / 3.5 (107) |
| ema_st | long | long | defaults | ok | no | 181 | 80.54 | 432.34 | 1.65 | 27.6% | 24.26 | -34.8% | 4.3% | 2.58 | 0.83 | 2.14 | 31.0 (123) / 49.5 (58) |
| ema_st | short | long — off-registry (hypothetical) | defaults | ok | no | 210 | -14.35 | -80.55 | 0.85 | 32.4% | 26.90 | -28.5% | n/a (gross<=0) | 1.58 | 0.77 | 1.65 | -5.4 (100) / -8.9 (110) |
| exp_breakout | long | both | defaults | ok | no | 586 | 81.89 | 454.64 | 1.20 | 32.3% | 42.39 | -37.2% | 14.7% | 1.18 | 0.82 | 1.04 | 74.7 (364) / 7.2 (222) |
| exp_breakout | long | both | profit_capture | ok | no | 620 | 52.21 | 268.59 | 1.14 | 41.9% | 32.73 | -31.0% | 22.8% | 1.16 | 0.76 | 1.08 | 34.0 (386) / 18.2 (234) |
| exp_breakout | short | both | defaults | ok | no | 481 | -8.03 | -83.47 | 0.93 | 34.3% | 36.31 | -35.3% | 664.6% | 1.15 | 0.78 | 1.16 | 7.6 (223) / -15.7 (258) |
| exp_breakout | short | both | profit_capture | ok | no | 509 | 3.00 | -19.92 | 0.98 | 41.7% | 25.85 | -26.0% | 76.6% | 1.11 | 0.75 | 1.11 | 3.1 (239) / -0.1 (270) |
| exp_mom_short | long | short — off-registry (hypothetical) | defaults | not emitted | no | 0 | 0.00 | 0.00 | n/a | n/a | n/a | 0.0% | n/a | n/a | n/a | n/a | 0.0 (0) / 0.0 (0) |
| exp_mom_short | short | short | defaults | ok | no | 285 | 15.80 | 60.09 | 1.10 | 41.1% | 19.44 | -20.3% | 26.4% | 1.21 | 0.63 | 1.15 | 11.1 (122) / 4.7 (163) |
| exp_range | long | both | defaults | ok | no | 905 | -19.95 | -142.89 | 0.93 | 47.8% | 68.88 | -55.2% | 34569.3% | 0.84 | 0.87 | 0.86 | 3.6 (351) / -23.6 (554) |
| exp_range | long | both | profit_capture | ok | no | 1084 | -35.62 | -173.45 | 0.87 | 65.8% | 71.64 | -53.9% | n/a (gross<=0) | 0.74 | 0.70 | 0.78 | -3.9 (429) / -31.7 (655) |
| exp_range | short | both | defaults | ok | no | 934 | -24.18 | -150.11 | 0.92 | 49.9% | 45.16 | -37.5% | 49398.3% | 0.83 | 0.92 | 0.85 | -47.4 (644) / 23.3 (290) |
| exp_range | short | both | profit_capture | ok | no | 1094 | -47.35 | -208.31 | 0.85 | 64.9% | 47.57 | -41.9% | n/a (gross<=0) | 0.74 | 0.75 | 0.79 | -58.6 (763) / 11.3 (331) |
| exp_rip_sell_dca | long | short — off-registry (hypothetical) | defaults | not emitted | no | 0 | 0.00 | 0.00 | n/a | n/a | n/a | 0.0% | n/a | n/a | n/a | n/a | 0.0 (0) / 0.0 (0) |
| exp_rip_sell_dca | short | short | defaults | ok | no | 490 | -3.06 | -17.57 | 0.93 | 90.2% | 10.97 | -11.0% | 987.6% | 0.09 | 0.37 | 0.10 | -0.1 (285) / -3.0 (205) |
| exp_trend | long | both | defaults | ok | no | 171 | 56.69 | 344.78 | 2.00 | 36.8% | 7.62 | -11.1% | 5.7% | 0.99 | 0.50 | 0.66 | 51.5 (137) / 5.2 (34) |
| exp_trend | long | both | profit_capture | ok | no | 171 | 48.70 | 290.47 | 2.08 | 38.0% | 7.81 | -9.1% | 6.7% | 0.96 | 0.43 | 0.67 | 47.1 (137) / 1.6 (34) |
| exp_trend | short | both | defaults | ok | no | 189 | 10.19 | 42.91 | 1.12 | 35.4% | 13.49 | -16.4% | 25.5% | 0.82 | 0.51 | 0.76 | -1.1 (81) / 11.3 (108) |
| exp_trend | short | both | profit_capture | ok | no | 190 | 4.85 | 18.06 | 1.06 | 32.1% | 14.28 | -15.2% | 38.7% | 0.79 | 0.49 | 0.76 | -2.4 (81) / 7.2 (109) |
| exp_vwaprev | long | both | defaults | ok | no | 909 | -23.64 | -154.61 | 0.92 | 46.8% | 70.84 | -56.0% | n/a (gross<=0) | 0.86 | 0.89 | 0.88 | 11.1 (353) / -34.7 (556) |
| exp_vwaprev | long | both | profit_capture | ok | no | 1101 | -39.30 | -185.66 | 0.87 | 65.0% | 74.59 | -55.4% | n/a (gross<=0) | 0.75 | 0.73 | 0.79 | -3.8 (441) / -35.5 (660) |
| exp_vwaprev | short | both | defaults | ok | no | 940 | -50.46 | -231.56 | 0.87 | 48.5% | 54.66 | -46.6% | n/a (gross<=0) | 0.82 | 0.93 | 0.87 | -61.6 (643) / 11.1 (297) |
| exp_vwaprev | short | both | profit_capture | ok | no | 1069 | -42.33 | -193.37 | 0.87 | 65.1% | 43.69 | -38.9% | n/a (gross<=0) | 0.76 | 0.76 | 0.80 | -52.6 (737) / 10.3 (332) |
| hot_coin | long | long | defaults | ok | no | 394 | 83.90 | 579.87 | 1.48 | 37.6% | 19.21 | -19.6% | 8.0% | 1.63 | 0.68 | 1.42 | 62.5 (264) / 21.4 (130) |
| hot_coin | short | long — off-registry (hypothetical) | defaults | not emitted | no | 0 | 0.00 | 0.00 | n/a | n/a | n/a | 0.0% | n/a | n/a | n/a | n/a | 0.0 (0) / 0.0 (0) |
| pullback_rsi | long | long | defaults | ok | no | 268 | 25.81 | 126.54 | 1.19 | 53.7% | 15.87 | -18.8% | 17.1% | 1.20 | 0.87 | 1.11 | -2.4 (195) / 28.3 (73) |
| pullback_rsi | short | long — off-registry (hypothetical) | defaults | ok | no | 340 | -1.95 | -36.51 | 0.96 | 49.7% | 31.84 | -30.6% | 121.5% | 1.20 | 0.88 | 1.20 | 3.2 (158) / -5.1 (182) |
| rotation | long | long | defaults | ok | no | 858 | 82.61 | 448.42 | 1.35 | 21.4% | 34.47 | -32.4% | 13.6% | 1.05 | 0.34 | 0.95 | 70.7 (415) / 11.9 (443) |
| rotation | short | long — off-registry (hypothetical) | defaults | not emitted | no | 0 | 0.00 | 0.00 | n/a | n/a | n/a | 0.0% | n/a | n/a | n/a | n/a | 0.0 (0) / 0.0 (0) |
| squeeze_tp | long | long | defaults | ok | no | 291 | 37.29 | 181.01 | 1.21 | 38.8% | 22.73 | -23.1% | 18.1% | 1.73 | 0.75 | 1.61 | 24.5 (222) / 12.8 (69) |
| squeeze_tp | short | long — off-registry (hypothetical) | defaults | ok | no | 327 | 39.50 | 194.54 | 1.19 | 42.8% | 25.06 | -24.3% | 19.5% | 1.53 | 0.73 | 1.41 | 29.3 (126) / 10.2 (201) |

\* MAE is a bar-extreme approximation over [entry bar, exit bar] (the exit bar may include movement after the exit). Busted slices (equity <= 10% of the start) show PRE-BUST stats in every row and headline; the post-bust trades are listed in the bust column and excluded. MFE uses the backtest path best price. Both in stop-distance R. Fee share = fees / gross (gross = price P&L after slippage, before fees and funding); n/a when gross <= 0.

## INCOMPLETE symbols (excluded from every aggregate, never filled)

- HYPEUSDT: 1590 of 4504 4h candles missing in [warm-up start, END) (2024-09-07T08:00:00Z .. 2025-05-30T04:00:00Z), off-grid 0, duplicates 0
- PUMPUSDT: 1835 of 4504 4h candles missing in [warm-up start, END) (2024-09-07T08:00:00Z .. 2025-07-10T00:00:00Z), off-grid 0, duplicates 0

## Headline (registry sides only)

Registry-side slices with trades: 28. Off-registry slices excluded from this headline: 12 (bear_breakdown long, breakout_pyramid short, dca_dip short, donchian_ens short, ema_mom short, ema_st short, exp_mom_short long, exp_rip_sell_dca long, hot_coin short, pullback_rsi short, rotation short, squeeze_tp short).

| key | side | mgmt | trades | net R | PF |
|---|---|---|---|---|---|
| breakout_pyramid | long | defaults | 365 | 324.16 | 2.75 |
| donchian_ens | long | defaults | 291 | 149.16 | 2.27 |
| ema_mom | long | defaults | 160 | 101.09 | 2.77 |
| hot_coin | long | defaults | 394 | 83.90 | 1.48 |
| rotation | long | defaults | 858 | 82.61 | 1.35 |
| exp_breakout | long | defaults | 586 | 81.89 | 1.20 |
| ema_st | long | defaults | 181 | 80.54 | 1.65 |
| exp_trend | long | defaults | 171 | 56.69 | 2.00 |
| exp_breakout | long | profit_capture | 620 | 52.21 | 1.14 |
| exp_trend | long | profit_capture | 171 | 48.70 | 2.08 |
| squeeze_tp | long | defaults | 291 | 37.29 | 1.21 |
| pullback_rsi | long | defaults | 268 | 25.81 | 1.19 |
| bear_breakdown | short | defaults | 316 | 23.99 | 1.12 |
| exp_mom_short | short | defaults | 285 | 15.80 | 1.10 |
| exp_trend | short | defaults | 189 | 10.19 | 1.12 |
| exp_trend | short | profit_capture | 190 | 4.85 | 1.06 |
| exp_breakout | short | profit_capture | 509 | 3.00 | 0.98 |
| exp_rip_sell_dca | short | defaults | 490 | -3.06 | 0.93 |
| dca_dip | long | defaults | 283 | -3.92 | 0.86 |
| exp_breakout | short | defaults | 481 | -8.03 | 0.93 |
| exp_range | long | defaults | 905 | -19.95 | 0.93 |
| exp_vwaprev | long | defaults | 909 | -23.64 | 0.92 |
| exp_range | short | defaults | 934 | -24.18 | 0.92 |
| exp_range | long | profit_capture | 1084 | -35.62 | 0.87 |
| exp_vwaprev | long | profit_capture | 1101 | -39.30 | 0.87 |
| exp_vwaprev | short | profit_capture | 1069 | -42.33 | 0.87 |
| exp_range | short | profit_capture | 1094 | -47.35 | 0.85 |
| exp_vwaprev | short | defaults | 940 | -50.46 | 0.87 |

registry_sides = strategies.STRATEGIES[key]['sides'] as recorded in each packet. An off-registry side is one the registry does not list for that key: the runner can still simulate it (the signal function emits it), but it is hypothetical and is excluded from every headline line. Not evidence of edge.

## Per-symbol net R

| key | side | BTCUSDT | ETHUSDT | ZECUSDT | SOLUSDT | XRPUSDT | NEARUSDT | DOGEUSDT | UNIUSDT | SUIUSDT | QNTUSDT | BNBUSDT | LSKUSDT | ENAUSDT | 1000PEPEUSDT | WLDUSDT | ADAUSDT | AVAXUSDT | LINKUSDT |
|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|
| bear_breakdown | long | 0.0 (0) | 0.0 (0) | 0.0 (0) | 0.0 (0) | 0.0 (0) | 0.0 (0) | 0.0 (0) | 0.0 (0) | 0.0 (0) | 0.0 (0) | 0.0 (0) | 0.0 (0) | 0.0 (0) | 0.0 (0) | 0.0 (0) | 0.0 (0) | 0.0 (0) | 0.0 (0) |
| bear_breakdown | short | 8.0 (28) | -9.9 (24) | 0.7 (21) | 6.6 (28) | 7.3 (26) | 1.5 (26) | 8.3 (16) | 4.2 (18) | 4.6 (18) | -6.9 (19) | -0.6 (9) | -1.8 (20) | 2.4 (23) | -1.4 (6) | 0.5 (13) | 1.0 (9) | -1.0 (7) | 0.5 (5) |
| breakout_pyramid | long | 9.4 (36) | 40.8 (34) | 56.9 (43) | 9.6 (25) | 17.4 (18) | 6.1 (22) | 9.8 (12) | 1.9 (26) | 4.0 (20) | 0.2 (16) | 3.6 (29) | 159.9 (9) | -1.3 (18) | -6.0 (15) | -3.3 (7) | 12.0 (9) | -3.8 (10) | 6.9 (16) |
| breakout_pyramid | short | 13.2 (21) | -6.3 (13) | -6.4 (27) | 5.1 (22) | 11.9 (33) | 0.6 (22) | 6.9 (16) | 15.5 (24) | 33.5 (24) | -19.4 (35) | 0.0 (6) | 16.5 (35) | 15.1 (27) | -3.5 (14) | -7.2 (34) | -8.2 (11) | -4.8 (13) | 0.8 (2) |
| dca_dip | long | 0.0 (0) | 0.0 (0) | -1.4 (16) | -2.4 (29) | -2.6 (23) | 1.7 (23) | -1.3 (18) | 0.8 (25) | -1.1 (25) | -0.6 (17) | 1.7 (31) | -0.9 (13) | 0.9 (14) | -0.1 (16) | 1.3 (12) | -0.0 (9) | 0.3 (12) | 0.0 (0) |
| dca_dip | short | 0.0 (0) | 0.0 (0) | 0.0 (0) | 0.0 (0) | 0.0 (0) | 0.0 (0) | 0.0 (0) | 0.0 (0) | 0.0 (0) | 0.0 (0) | 0.0 (0) | 0.0 (0) | 0.0 (0) | 0.0 (0) | 0.0 (0) | 0.0 (0) | 0.0 (0) | 0.0 (0) |
| donchian_ens | long | -0.9 (29) | 3.1 (21) | 12.5 (29) | 6.1 (20) | -3.2 (17) | 6.2 (13) | 4.3 (11) | 15.0 (13) | -4.6 (11) | -3.6 (20) | -4.4 (20) | 116.2 (15) | -6.4 (14) | 2.1 (9) | 8.8 (11) | 1.0 (8) | -8.0 (16) | 5.1 (14) |
| donchian_ens | short | 3.4 (14) | 2.6 (8) | -0.9 (15) | 2.5 (10) | 15.1 (16) | 2.3 (19) | 2.2 (11) | 2.5 (14) | 3.8 (13) | -1.1 (18) | -1.5 (3) | -0.2 (23) | -4.2 (18) | -1.6 (14) | -4.3 (19) | 1.6 (10) | 7.2 (4) | -1.6 (4) |
| ema_mom | long | 3.6 (13) | 13.6 (8) | 31.6 (12) | 8.2 (8) | -2.3 (5) | -2.3 (7) | 1.4 (3) | 2.5 (14) | -1.7 (15) | 1.7 (10) | 12.2 (12) | 0.8 (5) | 16.4 (9) | -1.9 (7) | 6.4 (4) | 0.8 (9) | 5.3 (11) | 4.8 (8) |
| ema_mom | short | -2.4 (8) | 2.1 (10) | 1.2 (6) | -2.9 (8) | -1.5 (11) | 1.1 (6) | -2.0 (10) | 3.9 (7) | 4.4 (11) | 3.5 (11) | -1.3 (8) | 0.2 (19) | 0.7 (17) | -2.6 (11) | 3.1 (12) | 1.4 (11) | -6.8 (13) | 0.6 (9) |
| ema_st | long | 15.0 (12) | 12.4 (13) | 54.7 (19) | 5.4 (9) | 0.9 (17) | 0.0 (11) | 4.2 (4) | -2.4 (13) | -0.2 (11) | -5.4 (12) | 4.0 (12) | -3.5 (4) | -2.4 (6) | -1.7 (6) | 6.9 (8) | -7.0 (11) | -0.3 (6) | -0.2 (7) |
| ema_st | short | 1.4 (4) | -1.6 (15) | 2.2 (8) | -4.5 (7) | 1.5 (13) | -4.0 (10) | 0.3 (12) | -2.7 (20) | -0.7 (11) | -3.3 (11) | 0.3 (12) | 4.3 (21) | 0.3 (12) | 4.5 (9) | -5.2 (15) | -3.7 (11) | -6.9 (14) | 3.5 (5) |
| exp_breakout | long | 0.2 (57) | 1.3 (59) | 29.1 (67) | 6.7 (40) | 14.5 (34) | 17.6 (38) | 9.7 (20) | 13.4 (33) | -10.4 (27) | -4.9 (35) | 4.5 (37) | 11.8 (34) | -8.1 (27) | -2.0 (17) | 2.3 (16) | 6.6 (11) | -6.5 (17) | -3.8 (17) |
| exp_breakout | long | 2.2 (62) | 0.5 (61) | 19.3 (67) | 9.0 (44) | 6.1 (35) | 10.3 (38) | 2.7 (22) | 9.1 (34) | -4.1 (28) | -2.1 (38) | -2.3 (42) | 3.3 (37) | -5.1 (26) | -1.6 (19) | 2.0 (18) | 6.1 (15) | -0.9 (17) | -2.1 (17) |
| exp_breakout | short | -11.7 (46) | 2.6 (28) | -2.9 (44) | 3.7 (37) | 4.8 (32) | 0.3 (31) | 10.8 (23) | 9.0 (30) | -5.9 (29) | -10.0 (36) | 0.9 (14) | 0.8 (29) | 5.0 (29) | -1.1 (16) | -4.0 (25) | 0.9 (14) | -7.8 (10) | -3.6 (8) |
| exp_breakout | short | -6.3 (46) | 0.3 (29) | -2.5 (48) | 8.4 (38) | 7.7 (33) | -4.6 (36) | 5.2 (24) | 7.8 (31) | -0.4 (31) | -8.8 (39) | 0.5 (16) | 0.8 (31) | 6.1 (32) | -1.3 (17) | -1.6 (25) | 2.8 (14) | -5.9 (10) | -5.5 (9) |
| exp_mom_short | long | 0.0 (0) | 0.0 (0) | 0.0 (0) | 0.0 (0) | 0.0 (0) | 0.0 (0) | 0.0 (0) | 0.0 (0) | 0.0 (0) | 0.0 (0) | 0.0 (0) | 0.0 (0) | 0.0 (0) | 0.0 (0) | 0.0 (0) | 0.0 (0) | 0.0 (0) | 0.0 (0) |
| exp_mom_short | short | -0.9 (2) | -3.8 (6) | -0.9 (20) | 4.1 (11) | -1.7 (10) | 0.4 (18) | -1.2 (12) | 2.5 (21) | 1.4 (24) | -1.9 (15) | -1.6 (5) | 1.2 (14) | 9.9 (36) | -0.9 (31) | 2.2 (30) | 0.7 (15) | 3.2 (12) | 3.1 (3) |
| exp_range | long | 6.3 (62) | 3.0 (60) | 3.9 (49) | -3.7 (54) | -5.6 (78) | -10.3 (64) | -2.4 (59) | -3.8 (48) | -3.5 (48) | 4.5 (60) | 1.3 (42) | -4.0 (53) | 6.7 (52) | 0.2 (39) | -4.0 (51) | 2.2 (34) | -2.9 (32) | -8.0 (20) |
| exp_range | long | -1.3 (72) | 2.5 (70) | 1.3 (66) | 0.4 (63) | -4.9 (91) | -6.6 (73) | -3.1 (72) | -1.4 (56) | -6.7 (59) | 2.8 (65) | 3.8 (55) | -3.5 (64) | 2.1 (58) | -5.5 (49) | -5.5 (61) | -1.5 (41) | -6.0 (39) | -2.7 (30) |
| exp_range | short | -0.7 (78) | 4.6 (59) | -4.9 (94) | -4.7 (61) | 10.2 (53) | -10.4 (58) | -3.3 (46) | -2.4 (44) | -2.2 (54) | 6.5 (54) | -5.2 (66) | -8.5 (36) | 8.4 (51) | 4.5 (40) | -9.8 (51) | -4.9 (28) | 4.4 (32) | -5.7 (29) |
| exp_range | short | -9.7 (81) | 4.4 (70) | -6.4 (99) | -2.6 (71) | 4.7 (59) | -9.0 (76) | -4.2 (60) | -2.7 (53) | -11.1 (63) | 3.2 (62) | -10.6 (77) | -1.6 (40) | 3.2 (65) | -0.0 (45) | -6.9 (57) | 4.2 (35) | 4.1 (45) | -6.3 (36) |
| exp_rip_sell_dca | long | 0.0 (0) | 0.0 (0) | 0.0 (0) | 0.0 (0) | 0.0 (0) | 0.0 (0) | 0.0 (0) | 0.0 (0) | 0.0 (0) | 0.0 (0) | 0.0 (0) | 0.0 (0) | 0.0 (0) | 0.0 (0) | 0.0 (0) | 0.0 (0) | 0.0 (0) | 0.0 (0) |
| exp_rip_sell_dca | short | 0.0 (0) | 0.0 (0) | -1.0 (35) | 4.5 (48) | 1.4 (42) | -2.4 (48) | -4.7 (37) | -1.7 (25) | -0.4 (29) | 2.0 (45) | 1.1 (25) | -2.5 (37) | 0.1 (18) | 0.3 (17) | 1.8 (37) | -3.0 (27) | 1.4 (20) | 0.0 (0) |
| exp_trend | long | 1.8 (13) | 1.7 (10) | 0.7 (12) | 7.1 (8) | -2.3 (7) | -2.3 (7) | 1.3 (4) | 8.8 (15) | -2.1 (15) | 5.4 (10) | 6.6 (13) | 2.1 (5) | 11.8 (11) | 1.1 (8) | 5.5 (4) | 4.2 (10) | 1.4 (11) | 4.1 (8) |
| exp_trend | long | 3.0 (13) | 2.6 (10) | 0.4 (12) | 6.5 (8) | -2.4 (7) | -2.3 (7) | 1.2 (4) | 9.6 (15) | -0.2 (15) | 4.4 (10) | 8.1 (13) | 2.3 (5) | 7.4 (11) | 0.6 (8) | 2.0 (4) | 1.4 (10) | 0.8 (11) | 3.2 (8) |
| exp_trend | short | -2.4 (8) | 2.2 (10) | 0.0 (6) | -2.9 (8) | 1.6 (11) | 1.4 (7) | -2.0 (10) | 5.7 (7) | 2.7 (11) | 5.6 (11) | -0.5 (8) | 1.1 (19) | 0.7 (17) | -2.6 (11) | 5.0 (12) | 0.3 (11) | -6.8 (13) | 1.0 (9) |
| exp_trend | short | -2.3 (8) | -0.5 (10) | -0.2 (6) | -2.3 (8) | 0.9 (11) | 1.8 (7) | -1.6 (10) | 2.3 (7) | 1.5 (12) | 4.9 (11) | -0.9 (8) | 0.8 (19) | -0.3 (17) | -1.5 (11) | 4.2 (12) | -0.1 (11) | -2.8 (13) | 0.8 (9) |
| exp_vwaprev | long | 2.7 (63) | -0.1 (59) | -1.8 (51) | -3.2 (53) | 0.1 (74) | -11.2 (66) | -8.0 (58) | -0.1 (58) | -4.4 (52) | 7.3 (56) | 3.0 (45) | -6.6 (49) | 4.8 (52) | 0.8 (38) | -5.6 (49) | 10.0 (30) | -8.1 (34) | -3.3 (22) |
| exp_vwaprev | long | -2.6 (74) | -3.8 (69) | 1.1 (71) | -3.3 (63) | -4.5 (88) | -8.5 (77) | -4.7 (72) | -0.6 (65) | -8.9 (64) | 3.2 (62) | 0.8 (56) | 1.9 (60) | 2.0 (56) | -2.0 (48) | -7.9 (63) | 6.7 (40) | -5.2 (40) | -2.9 (33) |
| exp_vwaprev | short | -3.0 (79) | 3.0 (61) | -7.4 (94) | -3.3 (65) | 5.7 (52) | -11.7 (58) | -4.6 (51) | -10.1 (49) | -4.5 (55) | 7.2 (56) | -1.4 (63) | -9.1 (35) | 4.9 (50) | -0.9 (37) | -11.7 (48) | -2.0 (26) | 2.3 (35) | -4.1 (26) |
| exp_vwaprev | short | -13.5 (81) | 9.8 (68) | -9.0 (98) | 2.8 (73) | 5.0 (60) | -9.7 (71) | -3.8 (61) | -4.9 (57) | -9.2 (62) | -2.8 (61) | -2.9 (73) | -2.9 (40) | 1.7 (61) | -1.2 (40) | -4.8 (52) | 3.5 (34) | 5.5 (44) | -6.0 (33) |
| hot_coin | long | -0.6 (13) | 9.3 (14) | 18.4 (55) | -6.1 (17) | 6.7 (23) | 2.2 (26) | 10.8 (14) | 4.6 (32) | 8.7 (14) | 5.6 (23) | 1.0 (14) | 20.6 (13) | 0.7 (31) | -7.6 (26) | 4.8 (22) | 9.4 (19) | -5.0 (15) | 0.2 (23) |
| hot_coin | short | 0.0 (0) | 0.0 (0) | 0.0 (0) | 0.0 (0) | 0.0 (0) | 0.0 (0) | 0.0 (0) | 0.0 (0) | 0.0 (0) | 0.0 (0) | 0.0 (0) | 0.0 (0) | 0.0 (0) | 0.0 (0) | 0.0 (0) | 0.0 (0) | 0.0 (0) | 0.0 (0) |
| pullback_rsi | long | -0.8 (26) | 4.6 (24) | 9.7 (26) | 5.9 (18) | -3.5 (13) | 2.1 (20) | 1.1 (14) | 1.5 (16) | 1.4 (19) | 2.0 (16) | 6.9 (21) | -2.6 (4) | -2.2 (11) | -2.8 (10) | 3.4 (4) | -5.7 (7) | 0.2 (7) | 4.7 (12) |
| pullback_rsi | short | 2.8 (22) | 1.3 (19) | -0.2 (20) | -5.3 (18) | 0.7 (24) | -4.7 (22) | 8.6 (23) | 7.6 (19) | -5.9 (16) | 3.7 (28) | -1.1 (10) | -4.1 (31) | -0.8 (21) | 2.1 (19) | -3.0 (16) | 1.9 (12) | -3.1 (13) | -2.4 (7) |
| rotation | long | 15.8 (70) | 13.7 (68) | 34.2 (84) | 7.7 (51) | 5.1 (35) | 1.7 (45) | 10.6 (23) | -9.5 (65) | -0.2 (39) | -5.7 (65) | -2.3 (75) | 15.3 (29) | -10.8 (53) | 2.2 (42) | -1.7 (29) | 8.3 (22) | -3.4 (23) | 1.6 (40) |
| rotation | short | 0.0 (0) | 0.0 (0) | 0.0 (0) | 0.0 (0) | 0.0 (0) | 0.0 (0) | 0.0 (0) | 0.0 (0) | 0.0 (0) | 0.0 (0) | 0.0 (0) | 0.0 (0) | 0.0 (0) | 0.0 (0) | 0.0 (0) | 0.0 (0) | 0.0 (0) | 0.0 (0) |
| squeeze_tp | long | 2.0 (28) | 4.0 (34) | 9.6 (21) | 5.3 (26) | -5.4 (17) | 0.7 (18) | 10.4 (9) | 5.4 (20) | 1.3 (8) | 1.8 (16) | -2.2 (16) | 7.4 (11) | 0.5 (11) | 0.6 (14) | -3.1 (9) | -1.5 (8) | -0.4 (11) | 0.9 (14) |
| squeeze_tp | short | 2.8 (26) | 6.4 (14) | 5.0 (16) | -6.6 (18) | 12.1 (25) | 4.8 (23) | 2.7 (20) | 0.9 (15) | -0.2 (21) | 2.7 (20) | 0.3 (15) | 1.4 (33) | 3.3 (17) | 2.9 (13) | 4.1 (17) | -3.6 (14) | 2.8 (12) | -2.3 (8) |

## Packets

| file | packet_sha256 (canonical body) | file sha256 |
|---|---|---|
| bear_breakdown_long_defaults.json | `35da2df68e34ee8d591fe193882c7d3431b84fd60256db5cd1dabeebaa634d37` | `09623ec6d08a832d54a605ebd62aa393b6f12fd9c6803075902fcb4f7816aa27` |
| bear_breakdown_short_defaults.json | `887937a2d39d76512260b59f1e47782cc1ca2fcf50555986b64ba8293e9b15a4` | `b867867a80c5101907e254f82898af1294ca2cec4a7109d2e31eb9d9f2a96bc2` |
| breakout_pyramid_long_defaults.json | `91aadb252a6aaf8cc06a117945240a6944359394d203467e2e04fb8d0f2b46a2` | `6bb70f98d82ad53189f7dffd4890b06e007b6ff98eec8c183738fe8f292e9d5c` |
| breakout_pyramid_short_defaults.json | `fad612e7084242017140ebff1787473567b98158a8a25f80f2e581071a34b4e6` | `681880313f046226ec8204cf9af2a0ce034880c29032a06d9c053052688fc17a` |
| dca_dip_long_defaults.json | `ef00df96b0e20203d399d3fd5c87c6ceeb218e51c6a577af167efcc5acbba352` | `9c79998d1e6c3557a6cfcff23d764efcb4f699cb919ef67f4967f833e336ce30` |
| dca_dip_short_defaults.json | `e24a8875736d17564e7bf1baac45919b339b67c2fbe61ce32592a9ac738f19e6` | `44a9b936f0ad715a28e673bd93a889a50b998c79ad95c2923450fea862db6dc0` |
| donchian_ens_long_defaults.json | `4148b4594f7f212b74ea0d5d6449d9c98d49463b9f5b3dba94d0a188a93f426c` | `f6f8ad25ac80b44c34680d172b48051bef0d7559354da6579f8ddcbe9fdcad27` |
| donchian_ens_short_defaults.json | `d77f1b1f04258d4ef27a08e115f89975ad55322db1419180c02c7230bd2bae93` | `3ed6728073a31bcdb76a56a0d593d251a7c94ad1987cc0409febb436f277d81f` |
| ema_mom_long_defaults.json | `49fb8ad11d8a1c28b8fc884da6b09b1ca111ffd16d7a1100ff03529c739efbca` | `7dfeeaa380124733bcdcd6c6949995f61dc557abc0c4658409c9d5c9cbd06043` |
| ema_mom_short_defaults.json | `65dfe58749c93e3e933e410a80906e62ef05eb29a28ba054a9c2729e1a8725dd` | `12a0be8611ea80e447566815376cb9bab7bdb180bf21be59c4366a46d915446e` |
| ema_st_long_defaults.json | `117180127bd1cb38a131431aee2cdc6e23501f83d3d1e32ab2f9da0924fa5552` | `91fa8f7ea4e6734e32e7845b71e021a223212083e9cccdb3680b6d4fad85be7e` |
| ema_st_short_defaults.json | `adf9bc072848666f708e9b2eebaabf70f6944443c8562cd542efcd0e51d994f5` | `ea3dc0e02a868d0dbe21a144f5b1ab84eebfbd3e8c6376bd3d6350cc7d77b2bc` |
| exp_breakout_long_defaults.json | `0158d69af90e80e1b0223e69b8999d57ae4758f58579c93ad8968fd529e79bff` | `ed6253623efe359209171b12dc860a89376d21df60034a8b7376acec71128439` |
| exp_breakout_long_profit_capture.json | `93eee78a7e5a2279aba66f93493594ab66ef62f2311f3f2a3e9492efc8b68567` | `90ef91c031e85f40d3150b4f1993a47d65221259cbb705d1675de0a62a224b8f` |
| exp_breakout_short_defaults.json | `b46c8cde317e53dba6e0c039e3a44f51109447d4ae639af2ff3759bce78baab5` | `48bd48e0f1f1e240723a427f54f376f4b0b7affc65fa54e769adc2a1604b62c1` |
| exp_breakout_short_profit_capture.json | `9adc93499c1e09588824732acfac9112c739fec029058a5c65ee0f29bb666a06` | `1bf5bd996de18bff6d1881b91621d3fea6a1a5add3e16f3eb57ab6a199a8e9f6` |
| exp_mom_short_long_defaults.json | `13f57623bf404ebb4c1615fec1556ca13fa7221a2d064cb42d6780cb8f7c204a` | `fbee1be514390341c23013336de712e40ffe376ee83cf9b42c2990771be83208` |
| exp_mom_short_short_defaults.json | `f5ee3b92702fa04d8dccd471a396f172c3d4f3e914a55eff43c91ac6e2511a15` | `d8441eaa373278562dfdf562b8d533fe488353d5c163eabb1d5de784d63014f6` |
| exp_range_long_defaults.json | `a5e9b7a772bf9dd3f84eb758e2562aa4d7d51a212b04805633b7334873a90646` | `72e9d8a5b07fafc06b8c238b4d98397981e742d8ca0c3c0b07db801d6ee23f7a` |
| exp_range_long_profit_capture.json | `b0298ed3e9eb78c4324ab84026f5d1902db47aeaa71587f03c678fd854ec4e7c` | `bf2aa797747e2991efd1fb9c385e645b76fb07835c0743211499ec5be5f559ba` |
| exp_range_short_defaults.json | `b706632d32efe74c2159067ff89931fe2bdb6571bfc67a4a978d07c9ffb83162` | `f26258d2cad77feab864a8b3a7be2254217dde1cf30dcde4a07094ee09ceb2f7` |
| exp_range_short_profit_capture.json | `e27c396bc8bde382dee2a1ff0a9e447153d8981b59c7d3ac9b7e87abff39adb3` | `db2b87f4194be2f5dea509fefbb701397691d0de577cc897c4779a7432089ca8` |
| exp_rip_sell_dca_long_defaults.json | `63645c81e5be7ae07d828abc3e9d87ca44e88bf4d266638f65e3c2f1cbc048cd` | `9a5c6453ca928516021dc9c6f194656b71cd3bf0a45c825b2a53a394ca796074` |
| exp_rip_sell_dca_short_defaults.json | `69ef5b9fb5b393a4543b88e5f4c7c049812522df2ef095e711601b4b553781a0` | `6ecb6a12f5c3f6dc8b648be48f05540f8be89592cf9a1f5973dd4a6e8c806612` |
| exp_trend_long_defaults.json | `f6b9661953ed4fef93240419e6dbbcddbce2ab0420d9bd45d4526b2163090c48` | `58be231f8912c3a47ae0b54e57f3107a397d4ffe1828386f35c652af7b0af6fb` |
| exp_trend_long_profit_capture.json | `e673216b381ed4d42840c027253a26ef2a8d0e011e53f4ebcda1d6a54410744e` | `71c8daca70d16b64b4c68ed8539965c0b3d2d63333c7bce7808f51ef1ee70141` |
| exp_trend_short_defaults.json | `524d6c190a22de3ab531c53e1b07ed5aec475135a9b5fe0339b2a22156c439ae` | `02471609a83cc2405da37bf07bfb5800c7f7e17acc722035af78ea85a5111eae` |
| exp_trend_short_profit_capture.json | `d8843442e26596b2d1e6d3a85da16542b1979672ce3289a834feaed23436c2de` | `2f1e7ef9881aa7f60e34fbba28368b4cc9152cd487678647b82dc872a9c3ba21` |
| exp_vwaprev_long_defaults.json | `d13741da96a0e75f69f647ac24d4ffe285c7a0c880bf18f099cdb3fb887c1d60` | `4277491ad270c4b0c4964cd1ebe2b2b0f95b9655f679a4b7391daf29ef8c86e0` |
| exp_vwaprev_long_profit_capture.json | `ee782d36077379c3f7da57e96804725ff2459d25d06549a21139f69a7853b7e2` | `a95b317f72966ea8de7fac35bf041135da3507b01a0810882141550eeca47044` |
| exp_vwaprev_short_defaults.json | `ba28b1bc190a8ccb05443d930cba2074cabea5fc9e88802d973dafe3e3c8bd8a` | `e65bda530fe595af87f17829445676548ec7c06001b8ca5600132b9be1892fd1` |
| exp_vwaprev_short_profit_capture.json | `e8f8b904437c3ccb3bf998c95f89a8e5a2b35fa02f7880a7079010934160af63` | `7a3f39a6d5af6f8e53a0f4b0e37fd5daed402f82a7c58a9cd8c9f78cf921e64a` |
| hot_coin_long_defaults.json | `fefb2b919b1596c08bc4529b90c0ea20d4af55fc11e849ef45d922491bd12308` | `5250398513d8cedc7adf1764f4ac7590eed52c1db5ad4a7bfb4899aeb36aa9c7` |
| hot_coin_short_defaults.json | `a621782bad406800821d30b805721934baa0d480486ca300454c9a1d0e5da4b4` | `a3328c5e5342bbf8d59136fba96d15e69fb01965328fde3f172a8430f4b2585b` |
| pullback_rsi_long_defaults.json | `80be4aa780f4774bfe856c92a4c4133f7d284c9299824c3f59f71fec9ae96cd7` | `003f82f9418b687a80c15554efc2253fe05b1ffefeaf4b14dde2cf0c53c1c029` |
| pullback_rsi_short_defaults.json | `004fb0d6eb777e623322e98ad3d196a2f90a6dde1e491509da0c021ceabd4367` | `d9b406fb9f1b89a6be9aa032f3221a7c87cc8ebeeadbfd68a570551f8c93c8d5` |
| rotation_long_defaults.json | `3330d888a435c43caedc46b8be01eaa447b646b57a47c1e7387b31881e6af204` | `cda6ae750dcbf23ee7c0a601b40fe591b5a4434f761780b4e71fb355b32f744f` |
| rotation_short_defaults.json | `e594df68ca9a612b39d6574e5342fb12b2aca110a79d3d9f33ca552accb74c9a` | `63e80da9e58b81ad3ec72acec34518cdd78d835caef1290743b3542c89bdcbb1` |
| squeeze_tp_long_defaults.json | `291b402c74b3d5e0e653ee9b68b42f06a9047561c72b44d7a620f64112e729c0` | `128e7d1565683ef6ef28cb86627fa5cd23dcfb47336e660376df6f84f9461fd0` |
| squeeze_tp_short_defaults.json | `951c536dc2a47eb2745c9200f9f58645cbb897cb381687341b658c44ef989aaa` | `acd8ae51f60e7e0069817c9645cd086db6ad9fd8c4b8bf591eaa38486aa4223e` |
