# Breadth backtest batch 4 RERUN on c8c7b92 (bust split) — CORE-10 15m SCALP/STRUCTURE, defaults + profit_capture

REVIEW-ONLY research evidence. non_promotable. Development window only; not evidence of edge.

- commit: `c8c7b927ee575ba71a9724ce84d23f72774b07b8`
- window: 2024-10-14T00:00:00Z -> 2026-09-28T00:00:00Z (end-exclusive, 68544 15m bars; warm-up 220 bars from 2024-10-11T17:00:00Z)
- runner sha256: `run_breadth_candidate.py` dad619aaf2df00fd79ce9a2ca2d4848f298b745920fe7f6c56898e7b4f6922f2 · `run_vwaprev_candidate.py` c2f43564cc1da4cd79e12253ece82d10b3f82c1ecdbbc11aeafa80202060d23e
- universe: static-top40-minhist365-contract-20261011 tier CORE-10 (digest 1c06c18d2fc82413…); run symbols: BTCUSDT, ETHUSDT, ZECUSDT, SOLUSDT, XRPUSDT, NEARUSDT, DOGEUSDT, UNIUSDT, SUIUSDT
- costs: taker fee 0.0005 per side, slippage 0.0002 per side, funding 3.125e-06 of notional per 15m bar; market (taker) entries; legacy exchange rules
- account (app defaults): start 500.0, risk 1%/trade, max_pos 4, max_lev 10, daily halt 8%
- mgmt: `defaults` = frozen strategy mgmt (empty override). `profit_capture` exp_scalp: override {"be_r": 0.75, "tp1_frac": 0.5, "tp1_r": 0.75} -> merged {"be_r": 0.75, "max_bars": 16, "stop_atr": 1.0, "tp1_frac": 0.5, "tp1_r": 0.75, "tp_r": 1.5}; `profit_capture` exp_structure: override {"be_r": 0.75, "tp1_frac": 0.5, "tp1_r": 1.0} -> merged {"be_r": 0.75, "max_bars": 24, "stop_atr": 1.0, "tp1_frac": 0.5, "tp1_r": 1.0, "tp_r": 2.0}; Source: https://github.com/MoeAZack/ZackBot2/issues/13#issuecomment-6198970276

| key | side | registry_sides | mgmt | status | bust (time · first dead trade # · post-bust trades / net R, excluded) | trades | net R | net PnL | PF | win % | max DD (R) | max DD (equity) | fee share | MFE R | MAE R* | giveback R | R above / below BTC EMA200 |
|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|
| exp_scalp | long | both | defaults | ok | 2024-12-11T00:30:00Z · #1236 · 436 / -155.78 | 1235 | -215.86 | -449.86 | 0.72 | 40.6% | 230.31 | -91.2% | n/a (gross<=0) | 0.68 | 1.01 | 0.85 | -215.9 (1235) / 0.0 (0) |
| exp_scalp | long | both | profit_capture | ok | 2024-11-29T23:00:00Z · #1136 · 654 / -154.53 | 1135 | -223.06 | -450.42 | 0.56 | 53.7% | 228.75 | -90.4% | n/a (gross<=0) | 0.66 | 0.89 | 0.85 | -223.1 (1135) / 0.0 (0) |
| exp_scalp | short | both | defaults | ok | 2024-12-07T11:30:00Z · #930 · 760 / -149.96 | 929 | -215.98 | -449.64 | 0.66 | 39.0% | 215.98 | -90.0% | n/a (gross<=0) | 0.67 | 1.01 | 0.90 | -216.0 (929) / 0.0 (0) |
| exp_scalp | short | both | profit_capture | ok | 2024-12-01T00:45:00Z · #900 · 798 / -155.91 | 899 | -220.64 | -448.82 | 0.53 | 51.3% | 221.04 | -89.8% | n/a (gross<=0) | 0.64 | 0.90 | 0.88 | -220.6 (899) / 0.0 (0) |
| exp_structure | long | both | defaults | ok | 2024-12-03T10:45:00Z · #1054 · 583 / -152.21 | 1053 | -216.66 | -450.11 | 0.70 | 31.9% | 226.15 | -90.7% | n/a (gross<=0) | 0.79 | 1.02 | 1.00 | -216.7 (1053) / 0.0 (0) |
| exp_structure | long | both | profit_capture | ok | 2024-11-29T08:00:00Z · #1091 · 564 / -156.49 | 1090 | -221.40 | -450.03 | 0.58 | 41.4% | 222.61 | -90.2% | n/a (gross<=0) | 0.74 | 0.90 | 0.95 | -221.4 (1090) / 0.0 (0) |
| exp_structure | short | both | defaults | ok | 2024-12-31T08:00:00Z · #1081 · 1090 / -135.04 | 1080 | -210.94 | -450.95 | 0.75 | 32.9% | 212.67 | -90.2% | n/a (gross<=0) | 0.85 | 1.01 | 1.04 | -141.5 (968) / -69.5 (112) |
| exp_structure | short | both | profit_capture | ok | 2025-01-01T05:15:00Z · #1233 · 962 / -148.69 | 1232 | -218.87 | -450.85 | 0.66 | 43.5% | 220.10 | -90.1% | n/a (gross<=0) | 0.81 | 0.88 | 0.98 | -161.7 (1073) / -57.2 (159) |

\* MAE is a bar-extreme approximation over [entry bar, exit bar] (the exit bar may include movement after the exit). Busted slices (equity <= 10% of the start) show PRE-BUST stats in every row and headline; the post-bust trades are listed in the bust column and excluded. MFE uses the backtest path best price. Both in stop-distance R. Fee share = fees / gross (gross = price P&L after slippage, before fees and funding); n/a when gross <= 0.

## INCOMPLETE symbols (excluded from every aggregate, never filled)

- HYPEUSDT: 22150 of 68764 15m candles missing in [warm-up start, END) (2024-10-11T17:00:00Z .. 2025-05-30T10:15:00Z), off-grid 0, duplicates 0

## Headline (registry sides only)

Registry-side slices with trades: 8. Off-registry slices excluded from this headline: 0 (none).

| key | side | mgmt | trades | net R | PF |
|---|---|---|---|---|---|
| exp_structure | short | defaults | 1080 | -210.94 | 0.75 |
| exp_scalp | long | defaults | 1235 | -215.86 | 0.72 |
| exp_scalp | short | defaults | 929 | -215.98 | 0.66 |
| exp_structure | long | defaults | 1053 | -216.66 | 0.70 |
| exp_structure | short | profit_capture | 1232 | -218.87 | 0.66 |
| exp_scalp | short | profit_capture | 899 | -220.64 | 0.53 |
| exp_structure | long | profit_capture | 1090 | -221.40 | 0.58 |
| exp_scalp | long | profit_capture | 1135 | -223.06 | 0.56 |

registry_sides = strategies.STRATEGIES[key]['sides'] as recorded in each packet. An off-registry side is one the registry does not list for that key: the runner can still simulate it (the signal function emits it), but it is hypothetical and is excluded from every headline line. Not evidence of edge.

## Per-symbol net R

| key | side | BTCUSDT | ETHUSDT | ZECUSDT | SOLUSDT | XRPUSDT | NEARUSDT | DOGEUSDT | UNIUSDT | SUIUSDT |
|---|---|---|---|---|---|---|---|---|---|---|
| exp_scalp | long | -57.3 (152) | -71.3 (149) | -17.1 (117) | -18.9 (136) | -20.4 (135) | -7.9 (117) | -7.7 (149) | -5.3 (143) | -10.0 (137) |
| exp_scalp | long | -43.1 (136) | -53.1 (119) | -5.7 (107) | -28.7 (141) | -22.9 (128) | -13.5 (106) | -20.8 (147) | -9.8 (133) | -25.5 (118) |
| exp_scalp | short | -54.2 (92) | -31.6 (114) | -27.9 (107) | -31.9 (101) | -27.6 (98) | -9.5 (117) | 5.9 (88) | -3.8 (88) | -35.4 (124) |
| exp_scalp | short | -40.0 (86) | -38.8 (115) | -19.6 (115) | -28.7 (92) | -30.6 (93) | -17.9 (114) | -2.7 (79) | -6.7 (82) | -35.5 (123) |
| exp_structure | long | -46.8 (138) | -24.5 (109) | -10.5 (132) | -38.5 (131) | -28.8 (134) | -31.1 (104) | 0.1 (114) | -43.8 (115) | 7.2 (76) |
| exp_structure | long | -53.7 (143) | -22.5 (110) | -16.0 (132) | -41.0 (142) | -20.9 (132) | -18.9 (104) | -9.8 (117) | -26.6 (117) | -12.0 (93) |
| exp_structure | short | -53.2 (117) | 2.0 (119) | -16.4 (152) | -27.2 (117) | -32.0 (115) | -17.3 (134) | -14.7 (78) | -28.3 (121) | -23.9 (127) |
| exp_structure | short | -56.8 (134) | -24.3 (135) | -13.1 (172) | -27.9 (132) | -45.4 (128) | -20.6 (157) | -6.8 (97) | -13.2 (141) | -10.7 (136) |

## Packets

| file | packet_sha256 (canonical body) | file sha256 |
|---|---|---|
| exp_scalp_long_defaults.json | `10bd58b374718da6fe18d28c30d2be49adc3a130b27fcd260b55988e1a835b00` | `08ea329b7273e09e3ca96c510004fa874498ab1104c4b68f6110e671dd776d89` |
| exp_scalp_long_profit_capture.json | `64f9736837465a035901597a352e469027658553cce0a95e8673f2548aa1421a` | `4d285993a10d839cd18b05747818481173ccf541148b24db89c1781c9693bd78` |
| exp_scalp_short_defaults.json | `53f9e7cef105df4732df64f7bc2af1cfb6ee6b92763b6dd82722495447acfc90` | `1e578803b031a5305cdc64d06c92f24da33be43ad4a3b660bf2d2dff7c875a5d` |
| exp_scalp_short_profit_capture.json | `48cbbeb501ad2ee75d6b12ab39d199df0f068f0cf1ee355f19855f7e6b6e0496` | `4e300e80e4fa441b0b337df73d1daa74cd8f64229701e1c51feb6713fd248a06` |
| exp_structure_long_defaults.json | `e12c0bf9efc59b85a1735cf9f5e2dc4e2498d7810365b06264297065874cc64f` | `72ad7df61227aef5344e6a6146e2a763fdca1287989cf3a147abe300749d6b6f` |
| exp_structure_long_profit_capture.json | `ec312d8fcbcbdd8d5e5cdefcc9c6ae206b82753cd5893e31c57d1394e94b2f98` | `ce1989eb083492e94c3ed8efe72001809ebc56735ce3e09964d73a679282e81b` |
| exp_structure_short_defaults.json | `23b57add37978ea6480c1a2014be998cb35f817847484179b9437c427e800bb3` | `d08d529d8bcf337c1004e61aceaacaec871f8b82002b8dfdf30225108faef596` |
| exp_structure_short_profit_capture.json | `719f309f1bdb473e4486eec35589bcfd0113f64285d923f93b6fc25af4584033` | `68d262e19fbcdf5d9912437c87f914d9376e849d589f2ef363e993a8f40a1e5f` |
