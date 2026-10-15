# Breadth backtest batch 5 — CORE-20 4h, defaults (all 4h keys) + profit_capture (TREND/RANGE/BREAKOUT/VWAPREV)

REVIEW-ONLY research evidence. non_promotable. Development window only; not evidence of edge.

- commit: `339ba688cf4218af4b520fc2e4e92ed441e40740`
- window: 2024-10-14T00:00:00Z -> 2026-09-28T00:00:00Z (end-exclusive, 4284 4h bars; warm-up 220 bars from 2024-09-07T08:00:00Z)
- runner sha256: `run_breadth_candidate.py` de3a9666da556224ed384b24f58ac1a3fb6ea457e2148837bf322d0d56454c8a · `run_vwaprev_candidate.py` c2f43564cc1da4cd79e12253ece82d10b3f82c1ecdbbc11aeafa80202060d23e
- universe: static-top40-minhist365-contract-20261011 tier CORE-20 (digest 1c06c18d2fc82413…); run symbols: BTCUSDT, ETHUSDT, ZECUSDT, SOLUSDT, XRPUSDT, NEARUSDT, DOGEUSDT, UNIUSDT, SUIUSDT, BNBUSDT, ADAUSDT, AVAXUSDT, LINKUSDT
- costs: taker fee 0.0005 per side, slippage 0.0002 per side, funding 0.000050 of notional per 4h bar; market (taker) entries; legacy exchange rules
- account (app defaults): start 500.0, risk 1%/trade, max_pos 4, max_lev 10, daily halt 8%
- mgmt: `defaults` = frozen strategy mgmt (empty override). `profit_capture` exp_breakout: override {"be_r": 1.0, "tp1_frac": 0.5, "tp1_r": 1.25} -> merged {"be_r": 1.0, "max_bars": 60, "stop_atr": 2.0, "tp1_frac": 0.5, "tp1_r": 1.25, "tp_r": 3.0}; `profit_capture` exp_range: override {"be_r": 0.5, "max_bars": 18, "tp1_frac": 0.5, "tp1_r": 0.5} -> merged {"be_r": 0.5, "max_bars": 18, "stop_atr": 1.5, "tp1_frac": 0.5, "tp1_r": 0.5}; `profit_capture` exp_trend: override {"be_r": 1.0, "tp1_frac": 0.33, "tp1_r": 1.5} -> merged {"be_r": 1.0, "max_bars": 180, "stop_atr": 2.5, "tp1_frac": 0.33, "tp1_r": 1.5, "tp_r": 3.0}; `profit_capture` exp_vwaprev: override {"be_r": 0.5, "max_bars": 18, "tp1_frac": 0.5, "tp1_r": 0.5} -> merged {"be_r": 0.5, "max_bars": 18, "stop_atr": 1.5, "tp1_frac": 0.5, "tp1_r": 0.5}; Source: https://github.com/MoeAZack/ZackBot2/issues/13#issuecomment-6198970276

| key | side | mgmt | status | trades | net R | net PnL | PF | win % | max DD (R) | max DD (equity) | fee share | MFE R | MAE R* | giveback R | R above / below BTC EMA200 |
|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|
| bear_breakdown | long | defaults | not emitted | 0 | 0.00 | 0.00 | n/a | n/a | n/a | 0.0% | n/a | n/a | n/a | n/a | 0.0 (0) / 0.0 (0) |
| bear_breakdown | short | defaults | ok | 286 | 7.32 | -11.36 | 0.98 | 33.6% | 27.45 | -26.6% | 76.8% | 1.29 | 0.67 | 1.27 | 0.0 (0) / 7.3 (286) |
| breakout_pyramid | long | defaults | ok | 321 | 164.22 | 1292.17 | 1.70 | 26.8% | 22.20 | -26.6% | 7.3% | 1.85 | 0.76 | 1.34 | 99.9 (271) / 64.3 (50) |
| breakout_pyramid | short | defaults | ok | 310 | 85.54 | 239.50 | 1.18 | 21.9% | 39.37 | -37.5% | 18.8% | 1.48 | 0.77 | 1.21 | 67.7 (90) / 17.8 (220) |
| dca_dip | long | defaults | ok | 241 | -4.11 | -21.20 | 0.83 | 88.8% | 8.12 | -8.7% | n/a (gross<=0) | 0.09 | 0.39 | 0.11 | -7.1 (171) / 3.0 (70) |
| dca_dip | short | defaults | not emitted | 0 | 0.00 | 0.00 | n/a | n/a | n/a | 0.0% | n/a | n/a | n/a | n/a | 0.0 (0) / 0.0 (0) |
| donchian_ens | long | defaults | ok | 249 | 47.20 | 243.81 | 1.37 | 34.1% | 18.07 | -21.6% | 8.5% | 1.65 | 0.65 | 1.46 | 32.3 (171) / 14.9 (78) |
| donchian_ens | short | defaults | ok | 196 | 37.25 | 179.76 | 1.40 | 42.9% | 15.49 | -16.8% | 7.9% | 1.42 | 0.64 | 1.23 | 31.7 (84) / 5.5 (112) |
| ema_mom | long | defaults | ok | 133 | 79.20 | 485.25 | 2.55 | 33.8% | 8.03 | -19.6% | 3.6% | 2.13 | 0.53 | 1.54 | 40.5 (108) / 38.7 (25) |
| ema_mom | short | defaults | ok | 149 | -16.41 | -82.10 | 0.72 | 27.5% | 22.25 | -23.0% | n/a (gross<=0) | 1.11 | 0.61 | 1.22 | -2.2 (49) / -14.2 (100) |
| ema_st | long | defaults | ok | 159 | 83.42 | 473.11 | 1.80 | 28.9% | 19.51 | -29.9% | 3.9% | 2.63 | 0.81 | 2.11 | 32.4 (116) / 51.0 (43) |
| ema_st | short | defaults | ok | 189 | -16.74 | -92.86 | 0.80 | 31.2% | 27.41 | -27.5% | n/a (gross<=0) | 1.60 | 0.77 | 1.69 | -0.4 (82) / -16.3 (107) |
| exp_breakout | long | defaults | ok | 533 | 84.29 | 477.95 | 1.22 | 33.0% | 39.54 | -36.8% | 14.8% | 1.20 | 0.82 | 1.04 | 68.0 (346) / 16.3 (187) |
| exp_breakout | long | profit_capture | ok | 559 | 39.41 | 181.55 | 1.11 | 41.7% | 36.46 | -33.4% | 27.8% | 1.17 | 0.77 | 1.10 | 24.3 (364) / 15.1 (195) |
| exp_breakout | short | defaults | ok | 445 | -20.59 | -129.65 | 0.88 | 31.7% | 43.64 | -38.3% | n/a (gross<=0) | 1.12 | 0.80 | 1.17 | 1.9 (197) / -22.5 (248) |
| exp_breakout | short | profit_capture | ok | 464 | -8.45 | -69.71 | 0.94 | 39.9% | 32.46 | -31.0% | 395.1% | 1.10 | 0.76 | 1.11 | -0.9 (208) / -7.6 (256) |
| exp_mom_short | long | defaults | not emitted | 0 | 0.00 | 0.00 | n/a | n/a | n/a | 0.0% | n/a | n/a | n/a | n/a | 0.0 (0) / 0.0 (0) |
| exp_mom_short | short | defaults | ok | 278 | 11.66 | 33.24 | 1.05 | 37.4% | 22.21 | -22.0% | 38.7% | 1.23 | 0.67 | 1.19 | 11.9 (117) / -0.2 (161) |
| exp_range | long | defaults | ok | 728 | -23.95 | -145.22 | 0.91 | 48.1% | 58.05 | -48.7% | n/a (gross<=0) | 0.84 | 0.88 | 0.87 | 8.1 (273) / -32.0 (455) |
| exp_range | long | profit_capture | ok | 862 | -19.51 | -109.82 | 0.91 | 67.1% | 47.82 | -40.9% | 510.7% | 0.74 | 0.70 | 0.77 | -2.1 (327) / -17.4 (535) |
| exp_range | short | defaults | ok | 803 | -21.12 | -134.22 | 0.92 | 50.1% | 43.52 | -38.8% | n/a (gross<=0) | 0.83 | 0.90 | 0.85 | -35.7 (558) / 14.5 (245) |
| exp_range | short | profit_capture | ok | 926 | -55.10 | -228.39 | 0.81 | 63.4% | 55.31 | -46.3% | n/a (gross<=0) | 0.75 | 0.75 | 0.81 | -51.8 (655) / -3.3 (271) |
| exp_rip_sell_dca | long | defaults | not emitted | 0 | 0.00 | 0.00 | n/a | n/a | n/a | 0.0% | n/a | n/a | n/a | n/a | 0.0 (0) / 0.0 (0) |
| exp_rip_sell_dca | short | defaults | ok | 372 | -12.01 | -58.71 | 0.72 | 88.2% | 12.27 | -12.0% | n/a (gross<=0) | 0.10 | 0.38 | 0.13 | -7.9 (223) / -4.1 (149) |
| exp_trend | long | defaults | ok | 141 | 36.97 | 202.30 | 1.74 | 34.8% | 10.24 | -12.5% | 7.5% | 0.97 | 0.52 | 0.70 | 35.8 (116) / 1.2 (25) |
| exp_trend | long | profit_capture | ok | 141 | 37.01 | 209.05 | 1.96 | 37.6% | 8.06 | -10.0% | 7.6% | 0.96 | 0.45 | 0.69 | 37.2 (116) / -0.1 (25) |
| exp_trend | short | defaults | ok | 150 | -11.85 | -63.13 | 0.79 | 28.7% | 23.43 | -23.2% | n/a (gross<=0) | 0.77 | 0.61 | 0.85 | -3.6 (49) / -8.2 (101) |
| exp_trend | short | profit_capture | ok | 150 | -11.53 | -59.05 | 0.77 | 26.7% | 17.81 | -18.0% | n/a (gross<=0) | 0.73 | 0.56 | 0.81 | -4.3 (49) / -7.2 (101) |
| exp_vwaprev | long | defaults | ok | 743 | -37.60 | -189.90 | 0.88 | 46.6% | 69.01 | -54.1% | n/a (gross<=0) | 0.85 | 0.91 | 0.90 | 4.6 (280) / -42.2 (463) |
| exp_vwaprev | long | profit_capture | ok | 886 | -40.29 | -183.64 | 0.84 | 65.3% | 66.99 | -51.3% | n/a (gross<=0) | 0.74 | 0.74 | 0.79 | -8.5 (339) / -31.8 (547) |
| exp_vwaprev | short | defaults | ok | 816 | -25.72 | -151.96 | 0.91 | 49.6% | 47.26 | -41.3% | n/a (gross<=0) | 0.83 | 0.91 | 0.86 | -40.8 (565) / 15.0 (251) |
| exp_vwaprev | short | profit_capture | ok | 915 | -45.41 | -200.51 | 0.84 | 64.0% | 45.63 | -40.4% | n/a (gross<=0) | 0.76 | 0.75 | 0.81 | -45.7 (640) / 0.3 (275) |
| hot_coin | long | defaults | ok | 354 | 60.72 | 373.94 | 1.35 | 37.9% | 16.72 | -17.5% | 12.3% | 1.52 | 0.65 | 1.34 | 43.1 (245) / 17.7 (109) |
| hot_coin | short | defaults | not emitted | 0 | 0.00 | 0.00 | n/a | n/a | n/a | 0.0% | n/a | n/a | n/a | n/a | 0.0 (0) / 0.0 (0) |
| pullback_rsi | long | defaults | ok | 239 | 23.85 | 116.15 | 1.19 | 54.0% | 18.52 | -20.6% | 17.2% | 1.22 | 0.87 | 1.12 | -1.0 (176) / 24.8 (63) |
| pullback_rsi | short | defaults | ok | 282 | 1.35 | -15.74 | 0.98 | 51.1% | 27.55 | -26.3% | 77.8% | 1.21 | 0.86 | 1.20 | 3.7 (116) / -2.3 (166) |
| rotation | long | defaults | ok | 795 | 79.83 | 428.42 | 1.36 | 21.8% | 29.45 | -30.2% | 15.2% | 0.90 | 0.33 | 0.79 | 64.1 (398) / 15.7 (397) |
| rotation | short | defaults | not emitted | 0 | 0.00 | 0.00 | n/a | n/a | n/a | 0.0% | n/a | n/a | n/a | n/a | 0.0 (0) / 0.0 (0) |
| squeeze_tp | long | defaults | ok | 250 | 20.59 | 79.73 | 1.11 | 36.8% | 23.49 | -23.5% | 27.1% | 1.69 | 0.76 | 1.61 | 6.6 (200) / 14.0 (50) |
| squeeze_tp | short | defaults | ok | 264 | 26.98 | 127.03 | 1.17 | 42.8% | 24.24 | -24.0% | 22.8% | 1.51 | 0.72 | 1.41 | 11.2 (94) / 15.7 (170) |

\* MAE is a bar-extreme approximation over [entry bar, exit bar] (the exit bar may include movement after the exit). MFE uses the backtest path best price. Both in stop-distance R. Fee share = fees / gross (gross = price P&L after slippage, before fees and funding); n/a when gross <= 0.

## INCOMPLETE symbols (excluded from every aggregate, never filled)

- 1000PEPEUSDT: no cache file for this timeframe (all 4504 candles missing; never fetched or substituted)
- ENAUSDT: no cache file for this timeframe (all 4504 candles missing; never fetched or substituted)
- HYPEUSDT: 1590 of 4504 4h candles missing in [warm-up start, END) (2024-09-07T08:00:00Z .. 2025-05-30T04:00:00Z), off-grid 0, duplicates 0
- LSKUSDT: no cache file for this timeframe (all 4504 candles missing; never fetched or substituted)
- PUMPUSDT: no cache file for this timeframe (all 4504 candles missing; never fetched or substituted)
- QNTUSDT: no cache file for this timeframe (all 4504 candles missing; never fetched or substituted)
- WLDUSDT: no cache file for this timeframe (all 4504 candles missing; never fetched or substituted)

## Per-symbol net R

| key | side | BTCUSDT | ETHUSDT | ZECUSDT | SOLUSDT | XRPUSDT | NEARUSDT | DOGEUSDT | UNIUSDT | SUIUSDT | BNBUSDT | ADAUSDT | AVAXUSDT | LINKUSDT |
|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|
| bear_breakdown | long | 0.0 (0) | 0.0 (0) | 0.0 (0) | 0.0 (0) | 0.0 (0) | 0.0 (0) | 0.0 (0) | 0.0 (0) | 0.0 (0) | 0.0 (0) | 0.0 (0) | 0.0 (0) | 0.0 (0) |
| bear_breakdown | short | 6.7 (33) | -5.9 (33) | -2.2 (24) | -3.9 (34) | 6.3 (27) | 0.1 (31) | 6.3 (19) | 3.3 (20) | 2.3 (24) | -0.6 (9) | -1.4 (15) | -1.9 (8) | -1.7 (9) |
| breakout_pyramid | long | 8.0 (37) | 39.1 (37) | 58.5 (44) | 9.6 (25) | 30.9 (17) | 5.1 (23) | 8.9 (13) | 2.0 (27) | 2.9 (22) | 0.6 (31) | -1.0 (8) | -0.7 (14) | 0.5 (23) |
| breakout_pyramid | short | 14.7 (27) | 1.2 (26) | -5.3 (29) | 3.0 (26) | 10.4 (38) | 1.6 (33) | 6.4 (19) | 29.2 (27) | 25.1 (29) | 0.0 (6) | 14.1 (22) | -10.6 (20) | -4.3 (8) |
| dca_dip | long | 0.0 (0) | 0.0 (0) | -1.4 (17) | -2.0 (31) | -2.4 (27) | 2.2 (26) | -0.9 (20) | 0.8 (26) | -2.1 (28) | 1.8 (33) | 0.0 (15) | -0.1 (18) | 0.0 (0) |
| dca_dip | short | 0.0 (0) | 0.0 (0) | 0.0 (0) | 0.0 (0) | 0.0 (0) | 0.0 (0) | 0.0 (0) | 0.0 (0) | 0.0 (0) | 0.0 (0) | 0.0 (0) | 0.0 (0) | 0.0 (0) |
| donchian_ens | long | 1.2 (31) | 3.5 (23) | 11.5 (30) | 4.6 (22) | 3.3 (20) | 6.5 (16) | 3.3 (12) | 16.3 (13) | -8.2 (14) | 3.5 (21) | 5.5 (13) | -9.1 (18) | 5.4 (16) |
| donchian_ens | short | 4.2 (16) | 0.8 (10) | -2.7 (18) | 0.5 (14) | 13.4 (18) | 5.8 (21) | 3.7 (16) | 3.7 (18) | -1.5 (21) | -3.1 (6) | 9.3 (20) | 5.5 (11) | -2.3 (7) |
| ema_mom | long | 3.6 (13) | 16.2 (9) | 31.6 (12) | 8.2 (8) | -2.2 (7) | -2.8 (8) | 1.4 (3) | 2.5 (14) | -2.3 (16) | 12.2 (12) | 1.8 (10) | 5.2 (12) | 3.7 (9) |
| ema_mom | short | -2.7 (10) | 2.1 (10) | 1.2 (6) | -2.7 (11) | -2.6 (12) | -1.5 (9) | -2.6 (11) | 2.6 (9) | 1.9 (16) | -3.7 (12) | 0.8 (14) | -7.8 (18) | -1.4 (11) |
| ema_st | long | 15.6 (14) | 10.8 (15) | 54.7 (19) | 5.4 (9) | -0.1 (18) | -1.8 (13) | 7.9 (6) | -3.0 (14) | -0.2 (11) | 2.9 (13) | -6.5 (12) | -1.3 (7) | -1.0 (8) |
| ema_st | short | 1.4 (4) | -5.4 (20) | 2.3 (10) | -5.3 (10) | 2.4 (15) | 0.0 (13) | -1.9 (15) | -3.7 (21) | 0.6 (15) | 2.0 (16) | 0.6 (21) | -10.3 (21) | 0.5 (8) |
| exp_breakout | long | -4.0 (67) | 3.8 (59) | 27.4 (74) | 5.0 (44) | 23.2 (39) | 12.2 (43) | 8.1 (22) | 11.0 (36) | -6.3 (32) | 3.9 (42) | 5.4 (22) | -5.9 (28) | 0.4 (25) |
| exp_breakout | long | 1.2 (68) | -3.1 (64) | 17.7 (74) | 4.4 (47) | 9.8 (40) | 6.9 (47) | 4.2 (24) | 7.2 (36) | 2.0 (32) | -6.2 (48) | 3.8 (25) | -3.9 (29) | -4.6 (25) |
| exp_breakout | short | -7.7 (54) | -1.6 (38) | -2.5 (47) | 0.9 (52) | -8.2 (43) | -3.2 (43) | 5.1 (31) | 8.0 (34) | -9.2 (36) | -1.4 (17) | 5.9 (23) | -5.9 (15) | -0.8 (12) |
| exp_breakout | short | -1.5 (55) | -6.2 (37) | -2.9 (53) | 0.5 (53) | 1.5 (44) | -3.6 (45) | 2.0 (31) | 8.1 (35) | -1.9 (40) | -3.2 (19) | 7.5 (23) | -6.4 (16) | -2.4 (13) |
| exp_mom_short | long | 0.0 (0) | 0.0 (0) | 0.0 (0) | 0.0 (0) | 0.0 (0) | 0.0 (0) | 0.0 (0) | 0.0 (0) | 0.0 (0) | 0.0 (0) | 0.0 (0) | 0.0 (0) | 0.0 (0) |
| exp_mom_short | short | -2.1 (7) | -2.7 (11) | -0.9 (24) | 5.5 (20) | 6.3 (15) | 3.5 (33) | -4.4 (24) | 3.1 (31) | -6.2 (43) | -2.1 (7) | 5.5 (25) | 0.2 (31) | 6.1 (7) |
| exp_range | long | 6.8 (62) | 1.3 (65) | 8.1 (51) | -3.1 (57) | -6.7 (79) | -9.2 (64) | -2.4 (66) | -4.0 (48) | -2.4 (56) | 5.1 (50) | -5.5 (49) | -7.1 (47) | -4.9 (34) |
| exp_range | long | -2.0 (75) | 0.7 (75) | 4.2 (68) | 3.0 (66) | -5.9 (92) | -3.7 (76) | 0.1 (75) | -1.4 (56) | -6.2 (66) | 5.2 (65) | -4.6 (54) | -6.4 (51) | -2.6 (43) |
| exp_range | short | -3.3 (77) | 5.2 (60) | -5.3 (98) | -4.3 (67) | 5.7 (55) | -8.3 (68) | -0.6 (57) | 0.2 (53) | -7.2 (60) | -4.3 (76) | -4.9 (43) | 6.4 (48) | -0.4 (41) |
| exp_range | short | -11.0 (85) | 3.2 (71) | -7.9 (104) | -1.8 (76) | 1.9 (62) | -9.5 (83) | -4.7 (72) | -0.3 (65) | -15.8 (67) | -11.1 (83) | 2.8 (51) | 0.6 (56) | -1.6 (51) |
| exp_rip_sell_dca | long | 0.0 (0) | 0.0 (0) | 0.0 (0) | 0.0 (0) | 0.0 (0) | 0.0 (0) | 0.0 (0) | 0.0 (0) | 0.0 (0) | 0.0 (0) | 0.0 (0) | 0.0 (0) | 0.0 (0) |
| exp_rip_sell_dca | short | 0.0 (0) | 0.0 (0) | -1.4 (34) | 3.1 (47) | -1.0 (44) | -2.4 (51) | -5.5 (43) | -1.9 (25) | -1.8 (35) | 1.7 (29) | -2.4 (34) | -0.3 (30) | 0.0 (0) |
| exp_trend | long | 1.8 (13) | 4.6 (11) | 0.7 (12) | 10.0 (9) | -2.8 (8) | -2.8 (8) | 1.3 (4) | 8.8 (15) | 0.2 (17) | 6.6 (13) | 4.2 (10) | 1.3 (12) | 3.1 (9) |
| exp_trend | long | 3.0 (13) | 5.1 (11) | 0.4 (12) | 9.0 (9) | -2.5 (8) | -2.8 (8) | 1.2 (4) | 9.6 (15) | 1.6 (17) | 8.1 (13) | 1.4 (10) | 0.7 (12) | 2.1 (9) |
| exp_trend | short | -2.7 (10) | 2.2 (10) | 0.0 (6) | -0.9 (11) | 0.5 (12) | -1.2 (10) | -2.6 (11) | 4.5 (9) | 0.3 (16) | -2.8 (12) | -0.3 (14) | -7.8 (18) | -1.0 (11) |
| exp_trend | short | -2.6 (10) | -0.5 (10) | -0.2 (6) | -0.9 (11) | -0.1 (12) | 0.7 (10) | -2.2 (11) | 1.0 (9) | -0.2 (16) | -3.6 (12) | 0.3 (14) | -3.7 (18) | 0.3 (11) |
| exp_vwaprev | long | 0.4 (65) | -5.6 (64) | 0.6 (51) | -1.4 (56) | -2.1 (76) | -10.0 (66) | -6.8 (65) | -1.5 (57) | -1.5 (60) | 1.1 (55) | 2.4 (46) | -12.0 (46) | -1.2 (36) |
| exp_vwaprev | long | -3.4 (77) | -6.5 (74) | 2.4 (69) | -1.7 (69) | -5.3 (92) | -9.0 (81) | -3.4 (75) | -0.6 (65) | -7.7 (70) | 5.3 (67) | 2.9 (51) | -9.2 (51) | -4.2 (45) |
| exp_vwaprev | short | -7.1 (81) | 3.1 (61) | -3.7 (98) | 0.3 (68) | 3.3 (55) | -9.3 (70) | -2.9 (63) | -5.5 (55) | -5.1 (59) | -3.9 (73) | -2.7 (43) | 4.4 (50) | 3.4 (40) |
| exp_vwaprev | short | -14.7 (85) | 8.5 (69) | -6.9 (100) | 2.6 (75) | 1.3 (63) | -10.8 (84) | -7.9 (74) | -4.2 (66) | -9.6 (64) | -8.2 (82) | 2.7 (51) | 1.1 (55) | 0.5 (47) |
| hot_coin | long | -2.1 (20) | 3.3 (27) | 19.2 (57) | -3.5 (22) | 7.0 (32) | 3.8 (30) | 12.9 (21) | 5.0 (39) | 11.5 (17) | 1.4 (16) | 9.3 (25) | -7.9 (22) | 0.8 (26) |
| hot_coin | short | 0.0 (0) | 0.0 (0) | 0.0 (0) | 0.0 (0) | 0.0 (0) | 0.0 (0) | 0.0 (0) | 0.0 (0) | 0.0 (0) | 0.0 (0) | 0.0 (0) | 0.0 (0) | 0.0 (0) |
| pullback_rsi | long | 1.1 (27) | 3.9 (26) | 9.1 (24) | 4.8 (19) | -0.6 (16) | 3.1 (19) | -1.0 (16) | -0.1 (19) | 1.4 (19) | 4.9 (23) | -8.4 (11) | 0.7 (8) | 4.7 (12) |
| pullback_rsi | short | -3.6 (28) | 2.4 (28) | 0.0 (23) | -5.4 (21) | 3.5 (27) | -2.0 (28) | 6.4 (27) | 8.5 (21) | -3.6 (21) | 2.2 (14) | 1.3 (14) | -2.3 (18) | -6.1 (12) |
| rotation | long | 19.1 (88) | 12.8 (89) | 35.3 (83) | 7.0 (66) | 21.0 (43) | 1.8 (49) | 7.0 (46) | -9.5 (71) | -5.2 (47) | 4.6 (88) | -4.4 (32) | -6.2 (35) | -3.4 (58) |
| rotation | short | 0.0 (0) | 0.0 (0) | 0.0 (0) | 0.0 (0) | 0.0 (0) | 0.0 (0) | 0.0 (0) | 0.0 (0) | 0.0 (0) | 0.0 (0) | 0.0 (0) | 0.0 (0) | 0.0 (0) |
| squeeze_tp | long | 2.0 (28) | 3.5 (35) | 9.5 (24) | 4.7 (27) | -6.5 (18) | 0.6 (18) | 8.8 (11) | 3.3 (22) | 1.3 (8) | -4.2 (19) | -2.5 (11) | -1.6 (14) | 1.7 (15) |
| squeeze_tp | short | 1.4 (28) | 9.0 (18) | 5.0 (16) | -3.1 (20) | 13.5 (29) | 5.0 (29) | 2.3 (21) | 1.5 (19) | -1.8 (27) | 0.1 (16) | -4.4 (15) | 0.8 (15) | -2.4 (11) |

## Packets

| file | packet_sha256 (canonical body) | file sha256 |
|---|---|---|
| bear_breakdown_long_defaults.json | `1f1246841a12f096d86281c1907c06f71822a0dc93971b63962b94d0f2395335` | `cbcd394eada981f5fc916438790c6dd3e3081ffadb7d9f8bb4f2bd3ea2baf94b` |
| bear_breakdown_short_defaults.json | `c11d50405744cfe34a0c657a2701d9a1b6e4c63fc9e8ca052451b90a1cc707bd` | `c384bdc7cf398286059bdd31a6815060ff622ac79ce908b083ee44b9e27ddfd1` |
| breakout_pyramid_long_defaults.json | `a040b95e2b865dec17c348e3ccc69a6dc1d60da71ef18a1daee034dbfc9713d9` | `560ffded84775c7d38a89804496384b033f9f3cae74d6823fdcc9ba16d768e2e` |
| breakout_pyramid_short_defaults.json | `1a58250e8464f6dbf634451183524d8fe8df6768c5925fa1dc1445ab9483be13` | `989ebce7eb2968b3d09e4d4f1dac48ccbe91665f271a9ec636eb6d50eaa5c66d` |
| dca_dip_long_defaults.json | `b932a1eb3bc2812528406fff260e119b53d9b7877b5a92f6a64cca1a47ca6c34` | `dccdcc580c20cf92bd019ccad2c9a72114ea22f0e6ea1b019d62755147c60f93` |
| dca_dip_short_defaults.json | `675d9d212ce56babc1dcfa0fb0d5ba76fccf60b063f2f9c6b5db26c56a09598c` | `b28e0c015f7f17f6f0f6053a7f59e364f40b05adaf544dd47cb4193e942279a4` |
| donchian_ens_long_defaults.json | `0381e822549a309adea531abc44083421fa6e5bdd0749035bd7d06fad1844b7d` | `035eb6c09dac3842e34c07737757380a1db8f130fb486da15bb4c7faf8d86cde` |
| donchian_ens_short_defaults.json | `bcc7e67c09966468df4f0e3d2e4c0a38c28bb02f9e6c3aa3aa1c96d2dbf5d143` | `33fbaab829baa772f7c8e1ea8e08f76605d3c4c5e63588c244d2eaf0a361f142` |
| ema_mom_long_defaults.json | `ca39576c4d640e7ed3993cc9e7d32a9e7c15e1b605236c7d7a4ccc42667bda14` | `235f3ce2a61501673f27d3088835e69b8e17c3e94bda7bca42ff5e129e968d10` |
| ema_mom_short_defaults.json | `fe59fe28863b2b87d729ea189228f9eeafdfa635dd5076ef9da35958fe8bcc03` | `0d4166e31c491742207e59b1c602988e847d11b47ce0adf801c57923ff225836` |
| ema_st_long_defaults.json | `a0b092fd61a93c5b222500e1f3866e1483169cf2d89df0e7ec5594877cccfc22` | `4853f6b64bcde6e7d4a6a4e6e6ec1c6f0c6ce3b5554974af669377bc838937a6` |
| ema_st_short_defaults.json | `abb53365f9faa70fe7603a8f2883f1a6e33ffc8ace825e637930025b1810ad62` | `929bddbc2a60b003b87487f437dfe551e2d23331c57f304601c0ac4b947514ba` |
| exp_breakout_long_defaults.json | `acea613e7d9e303bd24e5a228bdb698cef6ce786f2054a901aee4cdc623d4b75` | `69d5b822687ae85999cca764cd8b12d8269d04a2b715b565ddfdf89e70873117` |
| exp_breakout_long_profit_capture.json | `0da605af056071cd13d63d50df08ecf7c81f06a7d40bc45a0de7ba7fdce7bda3` | `963049eb4137ca9af9b8e5eacf60324a0dbc7b4dc5e925061ee1ad9dc771cd00` |
| exp_breakout_short_defaults.json | `c31659b565f33aa542b78c176b3e7b7ccf36accd819611719c1d51f90fac9a5c` | `5c48afd317045827aea180d7225a9fb64ce189aaa199bdb32cb282c094ae9603` |
| exp_breakout_short_profit_capture.json | `08614bf7a50babd9ee261457ea1d3eb8c0c709ca110a07524a4f5c4d41ad52e2` | `57aaaba0462188f4d877cdaa44376aad8af6f18aba6632e2ffa01af5399d99d0` |
| exp_mom_short_long_defaults.json | `42f8cc1e69f6c266af12fe2b4d03ebbdddd7dc0ac742e7c7d1b4638bfca2549c` | `de331ed824170de665fbe1b55f1a99b763051db8243a2cec08b1139d66c391c7` |
| exp_mom_short_short_defaults.json | `701dbd6b76b9286ae79aad021c8e9acbee6a87c53651cb4929b5ff7471dc475f` | `1f90acb3f84670aaa1a3430a97fc92444cc1ccb5c0b91c5d2da086b6275a3228` |
| exp_range_long_defaults.json | `593cb268806e4df1e29e73c7411631c1a0f956d12d40befbd60e00d01b077a6b` | `7c524672706e1681ded042bdc41e0fd42f53f76d5a819df09db32b1a95a4d342` |
| exp_range_long_profit_capture.json | `230c67e4b81570a738257f8636027bd12228ca879ce03cae18d6b2ac2b696939` | `cd8cf01714bfd92f4c8f0fbb8bedba9589fe6a6cc601e9023a141619d468cd3c` |
| exp_range_short_defaults.json | `f36834fb4d84db8001d365a1406b17556f04be8c00c75022a1c90c39af685910` | `32637ae778c5cd19b36eeaf1391ac38b6099b67cf0e562b307549f9588c6ceb2` |
| exp_range_short_profit_capture.json | `76b09af7a918e126b1e9b9d1ba4b86405d6e435215a21f233763b96064b32b00` | `c5b1a917d5160eccc97eac0263f677fe75a61ff3afc87a0fd53783f740ba60f9` |
| exp_rip_sell_dca_long_defaults.json | `d79a1ad20df105f167767ea6adb2e7c5ed91c25d762548ca105410c003f71509` | `844cd312657ab6fbf42f2df26569d8cad901e6ba26bef2a1ced081087fc2cb41` |
| exp_rip_sell_dca_short_defaults.json | `8685ed402fe7e86fa9ee65b1bcf410e05be52906edafc87500d7330a6abb81f0` | `eae3f5ff019f8ecd9b3145d99e1f89e1899f85f3e7545738f2752f12955f4247` |
| exp_trend_long_defaults.json | `1c1133875d608f163f2fe58c772163d5e6e3ad6ebaeb7a317b5f383393b026f4` | `a013bc4f5a15d4ccac22b6596b713a9fa0ea0b9beebfb2547d367331d4a9f8ec` |
| exp_trend_long_profit_capture.json | `81f64a08bce84491da9146021ebfd9cd48b73141836ce160e6b97047eedca7b9` | `5725c22b09bef9b66ebe50d77ad8a60f80baab06a6f54f1f94a3c83a9c300a16` |
| exp_trend_short_defaults.json | `cff5c84a114e0013a1e29398b0758e56c2c8f2dddb75ee4a738e7cc0158f0caf` | `fc55f36d89f32df864fea198ec5d72621211f81a67e11f31314a1e2eb44e93d3` |
| exp_trend_short_profit_capture.json | `3386a8c7c8f914a5579f01a2cb2580a3900ec90934bc37528311b443bb5f1ea5` | `2e237ee3d937604d97aa14228b7fe7a2f772e82954598d04ed0c65913dce3c64` |
| exp_vwaprev_long_defaults.json | `f1346120307b50ed16b18502053ba076b3164d2acba5639b7fb4b17d644318e1` | `5963c1c9571e7d7815490086ec84011dfdf5b29d6f6ea114be515b7e2958d4f7` |
| exp_vwaprev_long_profit_capture.json | `0165e6f1030b76ff3efbc6bed2dde2bee09b175316d0c40593ceef97aef93f08` | `0d5ebd2fcb8d603265ff7d128b450d020fcb46752fd7efe568ac48d6d516a7bf` |
| exp_vwaprev_short_defaults.json | `5efcddbc38b09a194cb211d78f9074255c16a90ae66f9f0e94edf597d228a8ca` | `2c9411b55d13c54fac6f9812bff7c26ff2dbda672848dba63cbb38adddf932fb` |
| exp_vwaprev_short_profit_capture.json | `68cad46f655e9b66ceb3cdb40f2a018dd9535ba623811b1e8d6fed71060dd5a4` | `59b0962f838cfc589207afda6cef77f3ddbc5f1a7ed8a99068a6781d7df2fda0` |
| hot_coin_long_defaults.json | `533c1f907bc254d625d65359e24ea95fcfd0f404bdadf650443a208da42d8a51` | `d9ac84363e5cf14eac5290c216efb36cdd18c0d83f218d3f4005b62cd7d36038` |
| hot_coin_short_defaults.json | `b85e4601c941d5f6d96a978ac8faa26db2eba38712c1ba0165300529af860979` | `9809f2f509da2d917593f468327bd63de465e0bc06b6c0f7e751420520685ecf` |
| pullback_rsi_long_defaults.json | `69369aa2bc3bcc2cff8792898eb6aea34681de91826c08a4790d700131248f2a` | `34834bab85ff7b13fa13029a77874fc2a229e3f64f83f96c92ec2f86d59bdd8a` |
| pullback_rsi_short_defaults.json | `ea87861ac647ce9f59f5de54e730d80c7cc2a325095596637456e06caabdbeed` | `aac37980cb8c14a910c4401b23ba7b8f6c2e450653b83668da8584b14eb280bb` |
| rotation_long_defaults.json | `24c3eea1b136e7067291820efc31c100a0682afc0e662619b68c649ebef32163` | `514675e034529f3d542a94ce721252623a3775c7e86f68196610e74245df2cf1` |
| rotation_short_defaults.json | `67162bee139d83012996733f6772527baa886070842a6c90c803ff497b2197a9` | `8a2618fcb780178ec4180fbb88e088a5aac98f7bc40979f173b55aaaf670d232` |
| squeeze_tp_long_defaults.json | `1f27e25b4a02cba0fa5ab5171789d11a9d3c1604ec7c1408014cde3235f07034` | `8da557179171901f0c924ef6c23973d8a4d57b69ca07f35c2c7fd38cca8cc5d0` |
| squeeze_tp_short_defaults.json | `a8a0544a59cb2948cd2f170f0f34461fc5a70894519c200b46948700b47421ab` | `3ebc08f458da933eff385bb433e6b3d7c06b20d3f1465c8c9e1c5a2f1b0ac028` |
