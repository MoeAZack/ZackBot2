# Breadth backtest batch 7 — CORE-20 15m SCALP/STRUCTURE, defaults + profit_capture, bust split

REVIEW-ONLY research evidence. non_promotable. Development window only; not evidence of edge.

- commit: `c8c7b927ee575ba71a9724ce84d23f72774b07b8`
- window: 2024-10-14T00:00:00Z -> 2026-09-28T00:00:00Z (end-exclusive, 68544 15m bars; warm-up 220 bars from 2024-10-11T17:00:00Z)
- runner sha256: `run_breadth_candidate.py` dad619aaf2df00fd79ce9a2ca2d4848f298b745920fe7f6c56898e7b4f6922f2 · `run_vwaprev_candidate.py` c2f43564cc1da4cd79e12253ece82d10b3f82c1ecdbbc11aeafa80202060d23e
- universe: static-top40-minhist365-contract-20261011 tier CORE-20 (digest 1c06c18d2fc82413…); run symbols: BTCUSDT, ETHUSDT, ZECUSDT, SOLUSDT, XRPUSDT, NEARUSDT, DOGEUSDT, UNIUSDT, SUIUSDT, QNTUSDT, BNBUSDT, LSKUSDT, ENAUSDT, 1000PEPEUSDT, WLDUSDT, ADAUSDT, AVAXUSDT, LINKUSDT
- costs: taker fee 0.0005 per side, slippage 0.0002 per side, funding 3.125e-06 of notional per 15m bar; market (taker) entries; legacy exchange rules
- account (app defaults): start 500.0, risk 1%/trade, max_pos 4, max_lev 10, daily halt 8%
- mgmt: `defaults` = frozen strategy mgmt (empty override). `profit_capture` exp_scalp: override {"be_r": 0.75, "tp1_frac": 0.5, "tp1_r": 0.75} -> merged {"be_r": 0.75, "max_bars": 16, "stop_atr": 1.0, "tp1_frac": 0.5, "tp1_r": 0.75, "tp_r": 1.5}; `profit_capture` exp_structure: override {"be_r": 0.75, "tp1_frac": 0.5, "tp1_r": 1.0} -> merged {"be_r": 0.75, "max_bars": 24, "stop_atr": 1.0, "tp1_frac": 0.5, "tp1_r": 1.0, "tp_r": 2.0}; Source: https://github.com/MoeAZack/ZackBot2/issues/13#issuecomment-6198970276

| key | side | registry_sides | mgmt | status | bust (time · first dead trade # · post-bust trades / net R, excluded) | trades | net R | net PnL | PF | win % | max DD (R) | max DD (equity) | fee share | MFE R | MAE R* | giveback R | R above / below BTC EMA200 |
|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|
| exp_scalp | long | both | defaults | ok | 2024-12-05T16:30:00Z · #1540 · 341 / -155.27 | 1539 | -208.79 | -450.12 | 0.72 | 41.8% | 228.71 | -91.8% | n/a (gross<=0) | 0.67 | 1.01 | 0.81 | -208.8 (1539) / 0.0 (0) |
| exp_scalp | long | both | profit_capture | ok | 2024-11-20T11:00:00Z · #1121 · 718 / -157.75 | 1120 | -219.58 | -449.71 | 0.56 | 53.2% | 224.43 | -90.5% | n/a (gross<=0) | 0.64 | 0.88 | 0.84 | -219.6 (1120) / 0.0 (0) |
| exp_scalp | short | both | defaults | ok | 2024-12-03T11:15:00Z · #1130 · 897 / -146.32 | 1129 | -212.16 | -450.76 | 0.70 | 40.0% | 213.39 | -90.2% | n/a (gross<=0) | 0.68 | 1.02 | 0.87 | -212.2 (1129) / 0.0 (0) |
| exp_scalp | short | both | profit_capture | ok | 2024-11-23T10:00:00Z · #1079 · 1037 / -150.26 | 1078 | -219.70 | -450.06 | 0.54 | 53.1% | 221.06 | -90.1% | n/a (gross<=0) | 0.65 | 0.88 | 0.86 | -219.7 (1078) / 0.0 (0) |
| exp_structure | long | both | defaults | ok | 2024-12-02T02:30:00Z · #1208 · 546 / -156.19 | 1207 | -210.07 | -449.04 | 0.74 | 32.4% | 229.08 | -91.5% | n/a (gross<=0) | 0.80 | 1.04 | 0.98 | -210.1 (1207) / 0.0 (0) |
| exp_structure | long | both | profit_capture | ok | 2024-11-24T05:30:00Z · #1225 · 662 / -157.49 | 1224 | -217.68 | -449.13 | 0.59 | 41.7% | 224.78 | -90.5% | n/a (gross<=0) | 0.75 | 0.91 | 0.93 | -217.7 (1224) / 0.0 (0) |
| exp_structure | short | both | defaults | ok | 2024-12-27T23:45:00Z · #1367 · 1049 / -138.30 | 1366 | -198.34 | -449.74 | 0.75 | 34.2% | 201.07 | -90.2% | n/a (gross<=0) | 0.85 | 0.99 | 0.99 | -159.1 (1301) / -39.2 (65) |
| exp_structure | short | both | profit_capture | ok | 2024-12-14T22:15:00Z · #1204 · 916 / -153.25 | 1203 | -214.57 | -448.79 | 0.63 | 43.6% | 216.35 | -90.2% | n/a (gross<=0) | 0.81 | 0.87 | 0.99 | -214.6 (1203) / 0.0 (0) |

\* MAE is a bar-extreme approximation over [entry bar, exit bar] (the exit bar may include movement after the exit). Busted slices (equity <= 10% of the start) show PRE-BUST stats in every row and headline; the post-bust trades are listed in the bust column and excluded. MFE uses the backtest path best price. Both in stop-distance R. Fee share = fees / gross (gross = price P&L after slippage, before fees and funding); n/a when gross <= 0.

## INCOMPLETE symbols (excluded from every aggregate, never filled)

- HYPEUSDT: 22150 of 68764 15m candles missing in [warm-up start, END) (2024-10-11T17:00:00Z .. 2025-05-30T10:15:00Z), off-grid 0, duplicates 0
- PUMPUSDT: 26074 of 68764 15m candles missing in [warm-up start, END) (2024-10-11T17:00:00Z .. 2025-07-10T07:15:00Z), off-grid 0, duplicates 0

## Headline (registry sides only)

Registry-side slices with trades: 8. Off-registry slices excluded from this headline: 0 (none).

| key | side | mgmt | trades | net R | PF |
|---|---|---|---|---|---|
| exp_structure | short | defaults | 1366 | -198.34 | 0.75 |
| exp_scalp | long | defaults | 1539 | -208.79 | 0.72 |
| exp_structure | long | defaults | 1207 | -210.07 | 0.74 |
| exp_scalp | short | defaults | 1129 | -212.16 | 0.70 |
| exp_structure | short | profit_capture | 1203 | -214.57 | 0.63 |
| exp_structure | long | profit_capture | 1224 | -217.68 | 0.59 |
| exp_scalp | long | profit_capture | 1120 | -219.58 | 0.56 |
| exp_scalp | short | profit_capture | 1078 | -219.70 | 0.54 |

registry_sides = strategies.STRATEGIES[key]['sides'] as recorded in each packet. An off-registry side is one the registry does not list for that key: the runner can still simulate it (the signal function emits it), but it is hypothetical and is excluded from every headline line. Not evidence of edge.

## Per-symbol net R

| key | side | BTCUSDT | ETHUSDT | ZECUSDT | SOLUSDT | XRPUSDT | NEARUSDT | DOGEUSDT | UNIUSDT | SUIUSDT | QNTUSDT | BNBUSDT | LSKUSDT | ENAUSDT | 1000PEPEUSDT | WLDUSDT | ADAUSDT | AVAXUSDT | LINKUSDT |
|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|
| exp_scalp | long | -36.5 (111) | -44.5 (99) | 2.5 (84) | -11.4 (95) | -6.2 (99) | -6.2 (89) | -1.0 (113) | -9.5 (96) | -18.8 (92) | -28.3 (80) | -12.0 (74) | -20.0 (86) | 3.1 (85) | -20.6 (75) | -7.1 (63) | -0.2 (77) | 10.8 (62) | -2.8 (59) |
| exp_scalp | long | -27.2 (79) | -27.0 (63) | 1.4 (68) | -20.6 (85) | -10.5 (69) | -5.2 (58) | -4.5 (78) | -8.7 (72) | -9.7 (72) | -22.0 (63) | -19.3 (64) | -12.6 (58) | -4.4 (64) | -18.1 (47) | -1.2 (43) | -17.0 (61) | -8.2 (33) | -4.6 (43) |
| exp_scalp | short | -42.3 (61) | -17.8 (82) | -16.8 (75) | -19.6 (70) | -17.5 (60) | -2.8 (83) | 3.2 (55) | -6.2 (59) | -20.7 (78) | -30.9 (78) | -13.2 (46) | 3.4 (48) | -10.2 (59) | -9.9 (58) | -2.9 (72) | -8.7 (47) | 6.3 (51) | -5.6 (47) |
| exp_scalp | short | -18.3 (49) | -16.4 (83) | -16.3 (73) | -24.0 (55) | -24.9 (60) | -13.3 (72) | -6.5 (50) | -3.5 (60) | -16.9 (74) | -29.9 (69) | -15.4 (50) | -0.8 (51) | 0.8 (65) | -3.7 (59) | -5.8 (74) | -16.6 (44) | -2.3 (44) | -5.8 (46) |
| exp_structure | long | -37.7 (105) | -21.9 (82) | 1.1 (107) | -9.4 (93) | -9.4 (100) | -9.7 (66) | 5.7 (75) | -22.3 (74) | -8.2 (55) | -7.9 (60) | -27.7 (53) | -6.0 (54) | -15.7 (52) | -13.1 (35) | -5.9 (43) | 7.5 (67) | -10.4 (33) | -19.4 (53) |
| exp_structure | long | -33.8 (114) | -13.9 (81) | -2.3 (93) | -30.1 (109) | -15.8 (100) | -15.0 (73) | 2.6 (89) | -11.4 (67) | -12.3 (57) | -7.1 (60) | -26.7 (54) | -11.3 (61) | -0.4 (46) | -3.9 (38) | -3.8 (37) | -6.1 (74) | -3.2 (23) | -23.1 (48) |
| exp_structure | short | -24.2 (98) | -2.2 (97) | -11.5 (120) | -19.5 (92) | -21.9 (82) | 5.6 (97) | -10.9 (56) | -41.8 (87) | -12.8 (101) | -19.4 (87) | -23.1 (66) | 6.7 (70) | -0.3 (88) | -11.9 (49) | -7.3 (60) | 5.0 (37) | 1.7 (44) | -10.6 (35) |
| exp_structure | short | -25.7 (69) | -12.1 (83) | -13.5 (102) | -10.8 (80) | -30.8 (68) | 0.1 (81) | -0.2 (53) | -16.1 (73) | 6.1 (96) | -32.6 (87) | -33.4 (66) | -4.0 (65) | -3.6 (70) | -8.6 (40) | -13.8 (60) | 5.1 (32) | -8.2 (43) | -12.6 (35) |

## Packets

| file | packet_sha256 (canonical body) | file sha256 |
|---|---|---|
| exp_scalp_long_defaults.json | `a812fbf7aa47005efbb271a0566b8d75b688e3d37f32d6bbdc917385736f6999` | `94a1741f682876d0f2e199b588c01c121aacd2b8f8ca1755990732bad8b7aa29` |
| exp_scalp_long_profit_capture.json | `175222c1f13dfeadfade7c413a0489225ab0cfaeb6517233ca04c52b2698ee94` | `db54acb27ad67ab524e033cd352068dc5476771a1e4d9716c0c425c2c7989957` |
| exp_scalp_short_defaults.json | `4f4e973b2c0daea6ee974249e97dce0cb3401c956e19f6a03198bf19378c276c` | `3d9eb17f3be22537263c5e474febc5a26604c228c0c0d2b70478770b880036fc` |
| exp_scalp_short_profit_capture.json | `80cd36d3d39f8ae4c74822a538ddc93c5524af29cb3be13eb657a2c68aaa8d51` | `584ba2a0998cb9385ec446e03760d646ffc5b062323380e1e8b8229de6756bfe` |
| exp_structure_long_defaults.json | `816ff23ce2e4ec35dc7cd1f0261a38684e6b59ae4012de97b14fad10c8d85664` | `8f5c7a13de11280a355ed70e1e18290c9fa7a36d347b12150644ff594beafabc` |
| exp_structure_long_profit_capture.json | `fd6be4acb46a7ec65a2713e49b9728d135cfab3f388c4a48e1279162d287a8b2` | `06bdfd62969805f160ccfc8e5339e8d7442b31a873c85b55281cd1a8d496727c` |
| exp_structure_short_defaults.json | `2447d405181fe4acc4f8465a1f02428afc16f39195e9aa79999f415443151ef0` | `f45b82826c2b49d6bddb544ae523558a4c4b01a91fc639702ff567bada82c064` |
| exp_structure_short_profit_capture.json | `1772273ad11771a450d14aae5346660948dfe1a6d50a7827a605fac74af44ad2` | `3ba26fae89fba0ef8e5791d3a687bc9c6477d4422a58c5f6fa8b03600458d6af` |
