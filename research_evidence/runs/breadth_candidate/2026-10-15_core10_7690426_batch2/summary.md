# Breadth backtest batch 2 — CORE-10 remaining 4h keys (Trend/Momentum/Dip-DCA/legacy, defaults)

REVIEW-ONLY research evidence. non_promotable. Development window only; not evidence of edge.

- commit: `7690426103544e09df1f008397a28b02c6c3d836`
- window: 2024-10-14T00:00:00Z -> 2026-09-28T00:00:00Z (end-exclusive, 4284 4h bars; warm-up 220 bars from 2024-09-07T08:00:00Z)
- runner sha256: `run_breadth_candidate.py` f934786fc7aacae7de3e3f8b2ff952d8622fe62d08600cfb610f66ce3f8ced15 · `run_vwaprev_candidate.py` c2f43564cc1da4cd79e12253ece82d10b3f82c1ecdbbc11aeafa80202060d23e
- universe: static-top40-minhist365-contract-20261011 tier CORE-10 (digest 1c06c18d2fc82413…); run symbols: BTCUSDT, ETHUSDT, ZECUSDT, SOLUSDT, XRPUSDT, NEARUSDT, DOGEUSDT, UNIUSDT, SUIUSDT
- costs: taker fee 0.0005 per side, slippage 0.0002 per side, funding 0.000050 of notional per 4h bar; market (taker) entries; legacy exchange rules
- account (app defaults): start 500.0, risk 1%/trade, max_pos 4, max_lev 10, daily halt 8%
- mgmt: `defaults` = frozen strategy mgmt (empty override). profit_capture and the 15m keys came later: #107, #108, #109.

| key | side | registry_sides | mgmt | status | trades | net R | net PnL | PF | win % | max DD (R) | max DD (equity) | fee share | MFE R | MAE R* | giveback R | R above / below BTC EMA200 |
|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|
| bear_breakdown | long | short — off-registry (hypothetical) | defaults | not emitted | 0 | 0.00 | 0.00 | n/a | n/a | n/a | 0.0% | n/a | n/a | n/a | n/a | 0.0 (0) / 0.0 (0) |
| bear_breakdown | short | short | defaults | ok | 267 | 8.95 | -4.01 | 0.99 | 33.0% | 27.95 | -27.1% | 66.1% | 1.32 | 0.69 | 1.28 | 0.0 (0) / 9.0 (267) |
| breakout_pyramid | long | long | defaults | ok | 283 | 151.90 | 1161.52 | 1.72 | 26.1% | 20.25 | -23.1% | 6.8% | 1.87 | 0.76 | 1.33 | 91.9 (248) / 60.0 (35) |
| breakout_pyramid | short | long — off-registry (hypothetical) | defaults | ok | 270 | 86.77 | 334.69 | 1.30 | 23.7% | 33.22 | -33.0% | 13.1% | 1.53 | 0.76 | 1.21 | 61.5 (66) / 25.2 (204) |
| dca_dip | long | long | defaults | ok | 187 | -4.60 | -23.38 | 0.78 | 88.2% | 8.50 | -8.8% | n/a (gross<=0) | 0.09 | 0.38 | 0.12 | -7.2 (134) / 2.6 (53) |
| dca_dip | short | long — off-registry (hypothetical) | defaults | not emitted | 0 | 0.00 | 0.00 | n/a | n/a | n/a | 0.0% | n/a | n/a | n/a | n/a | 0.0 (0) / 0.0 (0) |
| donchian_ens | long | long | defaults | ok | 215 | 54.71 | 303.44 | 1.56 | 35.3% | 21.22 | -24.2% | 5.9% | 1.72 | 0.62 | 1.47 | 43.0 (152) / 11.7 (63) |
| donchian_ens | short | long — off-registry (hypothetical) | defaults | ok | 178 | 32.75 | 162.82 | 1.44 | 45.5% | 13.73 | -15.8% | 7.6% | 1.42 | 0.62 | 1.24 | 13.8 (72) / 18.9 (106) |
| ema_mom | long | long | defaults | ok | 97 | 59.92 | 345.78 | 2.73 | 36.1% | 5.03 | -17.9% | 3.5% | 2.19 | 0.48 | 1.57 | 25.4 (81) / 34.5 (16) |
| ema_mom | short | long — off-registry (hypothetical) | defaults | ok | 101 | -4.69 | -26.30 | 0.86 | 30.7% | 11.46 | -15.8% | n/a (gross<=0) | 1.11 | 0.58 | 1.16 | -1.6 (30) / -3.1 (71) |
| ema_st | long | long | defaults | ok | 132 | 92.03 | 575.97 | 2.23 | 33.3% | 12.32 | -24.4% | 2.8% | 2.86 | 0.72 | 2.16 | 45.0 (100) / 47.0 (32) |
| ema_st | short | long — off-registry (hypothetical) | defaults | ok | 152 | -13.57 | -71.27 | 0.80 | 32.9% | 20.14 | -23.6% | n/a (gross<=0) | 1.63 | 0.74 | 1.72 | 1.2 (60) / -14.8 (92) |
| exp_mom_short | long | short — off-registry (hypothetical) | defaults | not emitted | 0 | 0.00 | 0.00 | n/a | n/a | n/a | 0.0% | n/a | n/a | n/a | n/a | 0.0 (0) / 0.0 (0) |
| exp_mom_short | short | short | defaults | ok | 261 | 1.47 | -6.93 | 0.99 | 37.9% | 21.34 | -21.0% | 79.4% | 1.16 | 0.65 | 1.15 | 10.5 (112) / -9.1 (149) |
| exp_rip_sell_dca | long | short — off-registry (hypothetical) | defaults | not emitted | 0 | 0.00 | 0.00 | n/a | n/a | n/a | 0.0% | n/a | n/a | n/a | n/a | 0.0 (0) / 0.0 (0) |
| exp_rip_sell_dca | short | short | defaults | ok | 301 | -12.74 | -61.58 | 0.68 | 87.0% | 14.40 | -14.3% | n/a (gross<=0) | 0.09 | 0.39 | 0.14 | -6.5 (188) / -6.3 (113) |
| exp_trend | long | both | defaults | ok | 101 | 27.45 | 147.03 | 1.80 | 36.6% | 4.91 | -8.0% | 7.2% | 0.98 | 0.48 | 0.70 | 26.2 (85) / 1.2 (16) |
| exp_trend | short | both | defaults | ok | 101 | 0.32 | -3.29 | 0.98 | 31.7% | 13.35 | -12.9% | 70.4% | 0.81 | 0.57 | 0.80 | -2.3 (30) / 2.7 (71) |
| hot_coin | long | long | defaults | ok | 313 | 74.67 | 503.91 | 1.57 | 41.2% | 14.98 | -16.0% | 8.4% | 1.61 | 0.63 | 1.37 | 48.1 (222) / 26.6 (91) |
| hot_coin | short | long — off-registry (hypothetical) | defaults | not emitted | 0 | 0.00 | 0.00 | n/a | n/a | n/a | 0.0% | n/a | n/a | n/a | n/a | 0.0 (0) / 0.0 (0) |
| pullback_rsi | long | long | defaults | ok | 201 | 15.82 | 72.82 | 1.14 | 53.2% | 15.13 | -16.0% | 20.5% | 1.20 | 0.87 | 1.12 | -0.0 (158) / 15.8 (43) |
| pullback_rsi | short | long — off-registry (hypothetical) | defaults | ok | 250 | 9.17 | 27.40 | 1.04 | 52.4% | 23.65 | -23.3% | 38.8% | 1.22 | 0.86 | 1.18 | -0.1 (102) / 9.3 (148) |
| rotation | long | long | defaults | ok | 785 | 115.22 | 711.43 | 1.50 | 19.0% | 26.53 | -26.8% | 11.7% | 0.93 | 0.31 | 0.78 | 87.6 (383) / 27.6 (402) |
| rotation | short | long — off-registry (hypothetical) | defaults | not emitted | 0 | 0.00 | 0.00 | n/a | n/a | n/a | 0.0% | n/a | n/a | n/a | n/a | 0.0 (0) / 0.0 (0) |
| squeeze_tp | long | long | defaults | ok | 205 | 24.12 | 106.04 | 1.18 | 36.6% | 18.36 | -20.1% | 20.2% | 1.78 | 0.74 | 1.66 | 8.3 (167) / 15.8 (38) |
| squeeze_tp | short | long — off-registry (hypothetical) | defaults | ok | 226 | 34.40 | 178.52 | 1.27 | 45.1% | 21.61 | -21.9% | 16.4% | 1.58 | 0.70 | 1.43 | 19.0 (72) / 15.4 (154) |

\* MAE is a bar-extreme approximation over [entry bar, exit bar] (the exit bar may include movement after the exit). MFE uses the backtest path best price. Both in stop-distance R. Fee share = fees / gross (gross = price P&L after slippage, before fees and funding); n/a when gross <= 0.

## INCOMPLETE symbols (excluded from every aggregate, never filled)

- HYPEUSDT: 1590 of 4504 4h candles missing in [warm-up start, END) (2024-09-07T08:00:00Z .. 2025-05-30T04:00:00Z), off-grid 0, duplicates 0

## Headline (registry sides only)

Registry-side slices with trades: 14. Off-registry slices excluded from this headline: 12 (bear_breakdown long, breakout_pyramid short, dca_dip short, donchian_ens short, ema_mom short, ema_st short, exp_mom_short long, exp_rip_sell_dca long, hot_coin short, pullback_rsi short, rotation short, squeeze_tp short).

| key | side | mgmt | trades | net R | PF |
|---|---|---|---|---|---|
| breakout_pyramid | long | defaults | 283 | 151.90 | 1.72 |
| rotation | long | defaults | 785 | 115.22 | 1.50 |
| ema_st | long | defaults | 132 | 92.03 | 2.23 |
| hot_coin | long | defaults | 313 | 74.67 | 1.57 |
| ema_mom | long | defaults | 97 | 59.92 | 2.73 |
| donchian_ens | long | defaults | 215 | 54.71 | 1.56 |
| exp_trend | long | defaults | 101 | 27.45 | 1.80 |
| squeeze_tp | long | defaults | 205 | 24.12 | 1.18 |
| pullback_rsi | long | defaults | 201 | 15.82 | 1.14 |
| bear_breakdown | short | defaults | 267 | 8.95 | 0.99 |
| exp_mom_short | short | defaults | 261 | 1.47 | 0.99 |
| exp_trend | short | defaults | 101 | 0.32 | 0.98 |
| dca_dip | long | defaults | 187 | -4.60 | 0.78 |
| exp_rip_sell_dca | short | defaults | 301 | -12.74 | 0.68 |

registry_sides = strategies.STRATEGIES[key]['sides'] as recorded in each packet. An off-registry side is one the registry does not list for that key: the runner can still simulate it (the signal function emits it), but it is hypothetical and is excluded from every headline line. Not evidence of edge.

## Per-symbol net R

| key | side | BTCUSDT | ETHUSDT | ZECUSDT | SOLUSDT | XRPUSDT | NEARUSDT | DOGEUSDT | UNIUSDT | SUIUSDT |
|---|---|---|---|---|---|---|---|---|---|---|
| bear_breakdown | long | 0.0 (0) | 0.0 (0) | 0.0 (0) | 0.0 (0) | 0.0 (0) | 0.0 (0) | 0.0 (0) | 0.0 (0) | 0.0 (0) |
| bear_breakdown | short | 2.0 (38) | -7.3 (35) | -3.4 (27) | -3.9 (34) | 10.7 (31) | 0.2 (31) | 6.9 (23) | 2.6 (21) | 1.2 (27) |
| breakout_pyramid | long | 7.7 (39) | 33.0 (44) | 58.6 (47) | 12.3 (28) | 28.7 (25) | 0.7 (27) | 10.7 (18) | -1.6 (32) | 1.8 (23) |
| breakout_pyramid | short | 15.0 (27) | -1.9 (29) | -5.7 (30) | 1.7 (29) | 12.2 (38) | 0.2 (35) | 6.3 (22) | 32.3 (28) | 26.6 (32) |
| dca_dip | long | 0.0 (0) | 0.0 (0) | -1.3 (18) | -2.0 (33) | -2.2 (28) | 2.3 (26) | -0.3 (25) | 0.9 (28) | -2.0 (29) |
| dca_dip | short | 0.0 (0) | 0.0 (0) | 0.0 (0) | 0.0 (0) | 0.0 (0) | 0.0 (0) | 0.0 (0) | 0.0 (0) | 0.0 (0) |
| donchian_ens | long | 4.4 (32) | 7.0 (32) | 14.4 (36) | 7.5 (27) | 3.1 (24) | 5.4 (19) | 2.7 (17) | 20.5 (12) | -10.3 (16) |
| donchian_ens | short | 2.1 (19) | -0.5 (18) | -2.7 (18) | 4.4 (17) | 12.2 (22) | 6.6 (22) | 8.4 (20) | 3.1 (19) | -0.9 (23) |
| ema_mom | long | 3.6 (13) | 16.1 (10) | 31.6 (12) | 9.5 (9) | -1.8 (8) | -2.8 (8) | 0.6 (6) | 5.4 (15) | -2.3 (16) |
| ema_mom | short | -3.0 (11) | 4.1 (11) | 0.0 (8) | -2.7 (11) | -2.8 (13) | -1.2 (10) | -2.6 (11) | 1.6 (10) | 1.9 (16) |
| ema_st | long | 16.0 (15) | 11.2 (16) | 54.7 (19) | 8.0 (11) | -0.6 (19) | -1.8 (13) | 8.7 (10) | -4.3 (16) | 0.2 (13) |
| ema_st | short | -2.5 (13) | -5.2 (21) | 2.5 (11) | -3.6 (14) | 1.0 (19) | 0.8 (16) | -1.9 (15) | -4.2 (25) | -0.5 (18) |
| exp_mom_short | long | 0.0 (0) | 0.0 (0) | 0.0 (0) | 0.0 (0) | 0.0 (0) | 0.0 (0) | 0.0 (0) | 0.0 (0) | 0.0 (0) |
| exp_mom_short | short | -4.7 (12) | 2.2 (17) | -3.4 (28) | 5.5 (28) | 3.5 (24) | 1.5 (38) | -1.9 (34) | 3.9 (35) | -5.1 (45) |
| exp_rip_sell_dca | long | 0.0 (0) | 0.0 (0) | 0.0 (0) | 0.0 (0) | 0.0 (0) | 0.0 (0) | 0.0 (0) | 0.0 (0) | 0.0 (0) |
| exp_rip_sell_dca | short | 0.0 (0) | 0.0 (0) | -1.3 (36) | 3.4 (49) | -0.6 (46) | -4.4 (53) | -6.3 (47) | -2.0 (30) | -1.5 (40) |
| exp_trend | long | 1.8 (13) | 4.6 (11) | 0.7 (12) | 12.9 (10) | 0.7 (8) | -3.2 (9) | 1.0 (6) | 8.8 (15) | 0.2 (17) |
| exp_trend | short | -3.0 (11) | 5.2 (11) | -1.2 (8) | -0.9 (11) | 0.3 (13) | -1.2 (10) | -2.6 (11) | 3.4 (10) | 0.3 (16) |
| hot_coin | long | 1.5 (24) | 10.6 (34) | 23.8 (60) | 2.7 (30) | 11.3 (32) | 1.5 (34) | 12.0 (28) | 2.8 (44) | 8.5 (27) |
| hot_coin | short | 0.0 (0) | 0.0 (0) | 0.0 (0) | 0.0 (0) | 0.0 (0) | 0.0 (0) | 0.0 (0) | 0.0 (0) | 0.0 (0) |
| pullback_rsi | long | 0.1 (28) | 5.7 (30) | 6.1 (27) | 4.8 (19) | -2.1 (16) | 3.5 (23) | -3.6 (20) | -0.1 (19) | 1.4 (19) |
| pullback_rsi | short | -2.7 (30) | 1.8 (30) | -3.5 (25) | -2.5 (24) | 5.5 (28) | -2.6 (30) | 6.1 (33) | 10.4 (25) | -3.3 (25) |
| rotation | long | 18.7 (115) | 15.2 (112) | 46.4 (92) | 1.0 (99) | 18.6 (79) | 2.4 (62) | 13.2 (80) | -7.4 (79) | 7.0 (67) |
| rotation | short | 0.0 (0) | 0.0 (0) | 0.0 (0) | 0.0 (0) | 0.0 (0) | 0.0 (0) | 0.0 (0) | 0.0 (0) | 0.0 (0) |
| squeeze_tp | long | 0.9 (29) | 4.9 (36) | 9.3 (25) | 4.1 (28) | -6.5 (18) | -0.3 (21) | 8.8 (11) | 1.6 (24) | 1.4 (13) |
| squeeze_tp | short | 1.4 (28) | 8.4 (21) | 3.3 (18) | -3.5 (24) | 14.0 (30) | 4.9 (30) | 5.1 (24) | 1.9 (21) | -1.0 (30) |

## Packets

| file | packet_sha256 (canonical body) | file sha256 |
|---|---|---|
| bear_breakdown_long_defaults.json | `5db5790416287001349d51f2a8164ec559c8d155429dd49c795341a64d7d1e54` | `a4964f3fd9e80f6e7e020d2f88bde00e5ed4edfbbae0ea36e61c3e9eac852af6` |
| bear_breakdown_short_defaults.json | `238383e5921eb59687dcad6429e1223042daab388d4d3a554d4da8f66cc80eac` | `dbb0ff4d7ca95475ef8af04d92011ad9f4ac8502ce7a6f7a038402a472b0c408` |
| breakout_pyramid_long_defaults.json | `24fce262182e0c359bb867f02ff4bc66b1d8720e4fd173e87b57b63e61094f6b` | `742dee5f608d173db49c786fe5d9962708c5d5016b07392b98e68cdcd06a39ac` |
| breakout_pyramid_short_defaults.json | `e682b432e8d3cc6ad6741a0ef87a7921f9ec3140349ae82af42e2f0a4ce16d65` | `dc530b4a3bb71ec6093f4214e17dbb8e8da0a60ccc9e7fd0388a3ee2e9deca8e` |
| dca_dip_long_defaults.json | `3921ac3af51c5d6ba6f9d497b594e642d6b02208be6c6114983dafdef9792c3a` | `2b85db302f8eb2d0af59edfedf6ef94163a533c1bc75794261c1b95269063da9` |
| dca_dip_short_defaults.json | `dfa1f26110e761e2d100b32d8d8d08884bcd6208b5a4339fce5bd69a20b1a8ae` | `dbf5b65287e01422585102b58022e0fbc81a91a121e5732f623a90ccc312740d` |
| donchian_ens_long_defaults.json | `e0c7b7a9cd1ebe53b942d15d4066bc11a81e7dcfc9c463431627b58bd266829e` | `0addbeb7e3dc75ea600d6ad2f0622eebc668678c12fdd11729fc4274a0bd44c8` |
| donchian_ens_short_defaults.json | `35738cd8f2e9d07c051eef17a6ec7ed38afdf731c185785b23f0bfcf7be36ab0` | `af1593befb8b28863c0ca51dae42ce78eb44efa005ae1b126d96f3de3be65aaa` |
| ema_mom_long_defaults.json | `53af447c52ef5495a22b672fdd5c1fe4236e1523378f29b88436d69aa21f5a0c` | `40d31d9e1e0ee6391490d9c3dfd5555ca9f9c478f8fb4769ec2833b5de0eb7b7` |
| ema_mom_short_defaults.json | `de276fc1ba372739744cca59a2d24eb8a55675a6db79cac055ae33d07dd79757` | `e85ac14f36ae961d748df94310b0a91feefd61e8ac8821711dd5bc27ea0c9968` |
| ema_st_long_defaults.json | `f564edbee2d266d35535dd17549160fe1e6576e98207371d02674a52fde3c1b8` | `9446118746babb841d52a8979e3c5c676a89fd48323f602b65c7830639c0c370` |
| ema_st_short_defaults.json | `d92ab460cc6f69720f627d3e18f2d85a7c0080dd50d6c96fbea6052e073fddb4` | `ead20771a6ffaf592ee76e02882b55f968cde6a468981477c0c2fb411eec53d9` |
| exp_mom_short_long_defaults.json | `8bab5a984a7a4cf19ba3f50d16f049f176bda02d0ecce654bcf2bde71a4c6b70` | `2304af6a37dfea8d69c6137c9bdd37efe773c428c68061dd1eedbdd74ca935cd` |
| exp_mom_short_short_defaults.json | `96530ae0bcee10aaf90e6993b0ff91c5f17f9707718aa3a8b226cad3407e84e9` | `0e942acef44d5dce80f043f78474edcd4e20d127d4fd3edb9937a7522f576642` |
| exp_rip_sell_dca_long_defaults.json | `ecefac5f037c06c96fdf89625dcdb5061022cab7b4a7a1c41b728017a3169bd0` | `9f443ee72ec2e6639049f961c4f607292252e9628160e45dd793a4e212c2f9f7` |
| exp_rip_sell_dca_short_defaults.json | `9096c5ac1e2745801db27245df7d578aebf0683cd7d50777ea43be2a7faf1048` | `36dfe15213c9eb2ddae61759ac58402bbc25d4b420b914e008188fba7c7fd20e` |
| exp_trend_long_defaults.json | `e27fbf44da487d3f3c846c6ca533387baa0a95fe24f02449cddef0b720ce7227` | `9db3f82628ebe41b64a61c2beffc7fcdd13bc4f41a3a155ea744085b4972df52` |
| exp_trend_short_defaults.json | `f2d6a7bcbaccd77bcc1c05e2c03433342915389ac659fea9bd77e92703af9957` | `80f0fab73a7d495f6951ac40044a9353e923cdc3134c3857213a0ebba43ff5bc` |
| hot_coin_long_defaults.json | `d4311778ef17725615952b001f46e49023e680adda3be6e8dc70ce1a028f1606` | `dfa8b6125e542fab4da314d33e4432278de8585a1f7446559100ffa18a32bc14` |
| hot_coin_short_defaults.json | `a916e10aa7aba08e1e1b0580d97ecc627379b438981871bcc133572e7c24b406` | `edf4bdec10f647df5eb8e5a840423bed1c16eb9de253e2d292beebef8fca2477` |
| pullback_rsi_long_defaults.json | `8111da9ff76d96611f19628c5fe8aa2243f537c461cfc21087ce4a0ebcfffc57` | `68d09ae6a70a5e5b2f812c38596698c0c0e7f698dbd136ed71db6e2b77a6f605` |
| pullback_rsi_short_defaults.json | `96bd5a190f26d13195b57974efb87c37f723580b054debbdaae4f8bcec8808cf` | `df4639f944e87bdf0dbf4c78f729c298cc11e15b6f7f581a46188d9e2d4605ac` |
| rotation_long_defaults.json | `c734d83d6a56e3f15d1a33781b42ef169fd26498a63435be37fc33cc1f861d5c` | `7337fa3f2a6f4e00e82273d2bb0c5b7b2f7b286a9cf6bfd8e4d156464ea42637` |
| rotation_short_defaults.json | `31e284a0d8d9e077c455087d3e1f9caa5c5aeae04193a2fe526891e42b8a9edf` | `d2aa62799ba92dced1db9999ad34dea32adee7345332ae8af8e3f748fae0fad3` |
| squeeze_tp_long_defaults.json | `db50d6963d62c8f469c10c096763ad0fe6618b589dcb6ae3115347d63376eb18` | `4b2041169525fca040aaa1835423a0a06a2e228fc35bfa5346e6ed2e04193766` |
| squeeze_tp_short_defaults.json | `1fc9effc85644d64b5e3dcb1984ee0b3f7aa638d2023ceec0aceb8b64e91f063` | `596c53fdea7a48356f1673e79c4a68294fb20e6ed05c0a15010431f3ea183c9c` |
