# Breadth backtest batch 3 — CORE-10 profit_capture vs defaults (TREND/RANGE/BREAKOUT/VWAPREV)

REVIEW-ONLY research evidence. non_promotable. Development window only; not evidence of edge.

- commit: `339ba688cf4218af4b520fc2e4e92ed441e40740`
- window: 2024-10-14T00:00:00Z -> 2026-09-28T00:00:00Z (end-exclusive, 4284 4h bars; warm-up 220 bars from 2024-09-07T08:00:00Z)
- runner sha256: `run_breadth_candidate.py` de3a9666da556224ed384b24f58ac1a3fb6ea457e2148837bf322d0d56454c8a · `run_vwaprev_candidate.py` c2f43564cc1da4cd79e12253ece82d10b3f82c1ecdbbc11aeafa80202060d23e
- universe: static-top40-minhist365-contract-20261011 tier CORE-10 (digest 1c06c18d2fc82413…); run symbols: BTCUSDT, ETHUSDT, ZECUSDT, SOLUSDT, XRPUSDT, NEARUSDT, DOGEUSDT, UNIUSDT, SUIUSDT
- costs: taker fee 0.0005 per side, slippage 0.0002 per side, funding 0.000050 of notional per 4h bar; market (taker) entries; legacy exchange rules
- account (app defaults): start 500.0, risk 1%/trade, max_pos 4, max_lev 10, daily halt 8%
- mgmt: `defaults` = frozen strategy mgmt (empty override). `profit_capture` exp_breakout: override {"be_r": 1.0, "tp1_frac": 0.5, "tp1_r": 1.25} -> merged {"be_r": 1.0, "max_bars": 60, "stop_atr": 2.0, "tp1_frac": 0.5, "tp1_r": 1.25, "tp_r": 3.0}; `profit_capture` exp_range: override {"be_r": 0.5, "max_bars": 18, "tp1_frac": 0.5, "tp1_r": 0.5} -> merged {"be_r": 0.5, "max_bars": 18, "stop_atr": 1.5, "tp1_frac": 0.5, "tp1_r": 0.5}; `profit_capture` exp_trend: override {"be_r": 1.0, "tp1_frac": 0.33, "tp1_r": 1.5} -> merged {"be_r": 1.0, "max_bars": 180, "stop_atr": 2.5, "tp1_frac": 0.33, "tp1_r": 1.5, "tp_r": 3.0}; `profit_capture` exp_vwaprev: override {"be_r": 0.5, "max_bars": 18, "tp1_frac": 0.5, "tp1_r": 0.5} -> merged {"be_r": 0.5, "max_bars": 18, "stop_atr": 1.5, "tp1_frac": 0.5, "tp1_r": 0.5}; Source: https://github.com/MoeAZack/ZackBot2/issues/13#issuecomment-6198970276

| key | side | mgmt | status | trades | net R | net PnL | PF | win % | max DD (R) | max DD (equity) | fee share | MFE R | MAE R* | giveback R | R above / below BTC EMA200 |
|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|
| exp_breakout | long | defaults (#105) | ok | 489 | 86.69 | 513.20 | 1.29 | 33.7% | 36.63 | -32.7% | 11.8% | 1.21 | 0.82 | 1.03 | 70.8 (323) / 15.9 (166) |
| exp_breakout | long | profit_capture | ok | 506 | 43.03 | 212.15 | 1.15 | 43.5% | 26.72 | -25.4% | 22.1% | 1.18 | 0.77 | 1.09 | 23.6 (338) / 19.5 (168) |
| exp_breakout | short | defaults (#105) | ok | 420 | -22.16 | -132.79 | 0.87 | 31.4% | 43.38 | -38.2% | n/a (gross<=0) | 1.12 | 0.79 | 1.17 | 7.3 (185) / -29.5 (235) |
| exp_breakout | short | profit_capture | ok | 439 | -7.41 | -62.48 | 0.94 | 39.4% | 28.39 | -27.7% | 303.1% | 1.10 | 0.74 | 1.11 | 4.3 (194) / -11.7 (245) |
| exp_range | long | defaults (#105) | ok | 606 | -11.07 | -87.63 | 0.94 | 48.2% | 42.88 | -39.2% | 283.1% | 0.85 | 0.87 | 0.87 | 11.2 (230) / -22.3 (376) |
| exp_range | long | profit_capture | ok | 702 | -17.87 | -98.10 | 0.90 | 66.0% | 36.57 | -32.7% | 809.4% | 0.74 | 0.71 | 0.76 | -2.0 (267) / -15.8 (435) |
| exp_range | short | defaults (#105) | ok | 653 | -20.00 | -121.42 | 0.91 | 50.1% | 36.96 | -33.6% | n/a (gross<=0) | 0.82 | 0.91 | 0.85 | -46.6 (465) / 26.6 (188) |
| exp_range | short | profit_capture | ok | 725 | -47.00 | -200.48 | 0.80 | 63.4% | 47.00 | -40.3% | n/a (gross<=0) | 0.74 | 0.76 | 0.80 | -53.1 (519) / 6.1 (206) |
| exp_trend | long | defaults (#106) | ok | 101 | 27.45 | 147.03 | 1.80 | 36.6% | 4.91 | -8.0% | 7.2% | 0.98 | 0.48 | 0.70 | 26.2 (85) / 1.2 (16) |
| exp_trend | long | profit_capture | ok | 102 | 30.90 | 172.19 | 2.22 | 41.2% | 4.75 | -7.0% | 6.6% | 0.98 | 0.42 | 0.67 | 30.1 (86) / 0.8 (16) |
| exp_trend | short | defaults (#106) | ok | 101 | 0.32 | -3.29 | 0.98 | 31.7% | 13.35 | -12.9% | 70.4% | 0.81 | 0.57 | 0.80 | -2.3 (30) / 2.7 (71) |
| exp_trend | short | profit_capture | ok | 101 | -7.73 | -39.11 | 0.76 | 24.8% | 11.35 | -11.8% | n/a (gross<=0) | 0.74 | 0.52 | 0.81 | -2.2 (30) / -5.6 (71) |
| exp_vwaprev | long | defaults (#105) | ok | 623 | -30.12 | -158.63 | 0.89 | 46.4% | 61.24 | -49.2% | n/a (gross<=0) | 0.84 | 0.91 | 0.89 | 8.7 (236) / -38.8 (387) |
| exp_vwaprev | long | profit_capture | ok | 723 | -36.38 | -165.99 | 0.83 | 64.5% | 54.41 | -43.7% | n/a (gross<=0) | 0.73 | 0.74 | 0.78 | -9.3 (275) / -27.1 (448) |
| exp_vwaprev | short | defaults (#105) | ok | 658 | -25.47 | -142.33 | 0.89 | 49.7% | 42.45 | -37.3% | n/a (gross<=0) | 0.82 | 0.92 | 0.86 | -53.9 (464) / 28.4 (194) |
| exp_vwaprev | short | profit_capture | ok | 720 | -42.90 | -188.04 | 0.82 | 63.5% | 43.60 | -37.8% | n/a (gross<=0) | 0.75 | 0.77 | 0.81 | -52.8 (510) / 9.9 (210) |

\* MAE is a bar-extreme approximation over [entry bar, exit bar] (the exit bar may include movement after the exit). MFE uses the backtest path best price. Both in stop-distance R. Fee share = fees / gross (gross = price P&L after slippage, before fees and funding); n/a when gross <= 0.

## INCOMPLETE symbols (excluded from every aggregate, never filled)

- HYPEUSDT: 1590 of 4504 4h candles missing in [warm-up start, END) (2024-09-07T08:00:00Z .. 2025-05-30T04:00:00Z), off-grid 0, duplicates 0

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
| exp_breakout_long_profit_capture.json | `f5f61384039ac27a0c203399780661644483631e90d7d35c38d679bade489c3f` | `8960c4bb5ed789f05dbc4e4a1c842b83a965bb5ecbdfeec42891fa0d5bc03465` |
| exp_breakout_short_profit_capture.json | `98eaad0a345c250ba30d7d1f57a0c11a63bed63c9bb6e50d317163f9f0639847` | `e8a278b8ad5c23f3d788de55a68aa1a7f02da7d431f33b5aa047245022f29791` |
| exp_range_long_profit_capture.json | `889fe9f3aeee74b5a2a1b1a46fcd4b50612dd6c7817f77244738a938509ca7cf` | `00f5de12dd5ac21f7a5ef388d0ddd71dd1c6eb10f0b794000b3a541cf37afab0` |
| exp_range_short_profit_capture.json | `38eeb98fcadf581b40df6c42bedd297b2c1fe72815bafcd822650805dd2bb337` | `7632df29ecc437a7934dc252f577a7adaded429afb8f96f2f5f8001ffe1a4263` |
| exp_trend_long_profit_capture.json | `ebc8162f49a64e29b339b69397e9e2f62d9379c843ade05c850ec7dbe8068d01` | `284027584a9dd0bf491e456ffa1a698c630bdf3381b4362631b589385864bbae` |
| exp_trend_short_profit_capture.json | `2486affb4654774551623d43006730ba944550d229844e860a8f8cbfa8fdffbb` | `9e819d585d202c0eccdac8d100d20911e151f7d1a8418ac8bb6ac729618af3bc` |
| exp_vwaprev_long_profit_capture.json | `35391663c3bcef88ba3563e2219205fa2ec8e135c17eca00e0830dbe42436dbe` | `e723a62cfdf3d762fbd9e3fed13a526cf37b5f26eccfdcbe28c9d4828fb3b276` |
| exp_vwaprev_short_profit_capture.json | `4c1d730a507a467f2b3f0a56e6ca2c2ec6719a6ba0841bdb2b6b977524672582` | `ce0650ec1d50407fa67e9acb2f1151ee4fbdcd7620d15b324e1fe142b3d06ccb` |
