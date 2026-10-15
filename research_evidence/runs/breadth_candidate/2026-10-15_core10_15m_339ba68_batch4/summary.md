# Breadth backtest batch 4 — CORE-10 15m SCALP/STRUCTURE, defaults + profit_capture

> **Headline superseded (Cowork #109 6199440196):** every slice here BUSTS (equity <= 10% of the $500 start, Nov 2024 - Jan 2025) and the original runner kept trading the dead account. The bust-split rerun on runner c8c7b92 (identical trades) is in `rerun_c8c7b92/`; read its `summary.md`, which shows pre-bust stats only. The table below is the original full-run view, kept for the record.

REVIEW-ONLY research evidence. non_promotable. Development window only; not evidence of edge.

- commit: `339ba688cf4218af4b520fc2e4e92ed441e40740`
- window: 2024-10-14T00:00:00Z -> 2026-09-28T00:00:00Z (end-exclusive, 68544 15m bars; warm-up 220 bars from 2024-10-11T17:00:00Z)
- runner sha256: `run_breadth_candidate.py` de3a9666da556224ed384b24f58ac1a3fb6ea457e2148837bf322d0d56454c8a · `run_vwaprev_candidate.py` c2f43564cc1da4cd79e12253ece82d10b3f82c1ecdbbc11aeafa80202060d23e
- universe: static-top40-minhist365-contract-20261011 tier CORE-10 (digest 1c06c18d2fc82413…); run symbols: BTCUSDT, ETHUSDT, ZECUSDT, SOLUSDT, XRPUSDT, NEARUSDT, DOGEUSDT, UNIUSDT, SUIUSDT
- costs: taker fee 0.0005 per side, slippage 0.0002 per side, funding 3.125e-06 of notional per 15m bar; market (taker) entries; legacy exchange rules
- account (app defaults): start 500.0, risk 1%/trade, max_pos 4, max_lev 10, daily halt 8%
- mgmt: `defaults` = frozen strategy mgmt (empty override). `profit_capture` exp_scalp: override {"be_r": 0.75, "tp1_frac": 0.5, "tp1_r": 0.75} -> merged {"be_r": 0.75, "max_bars": 16, "stop_atr": 1.0, "tp1_frac": 0.5, "tp1_r": 0.75, "tp_r": 1.5}; `profit_capture` exp_structure: override {"be_r": 0.75, "tp1_frac": 0.5, "tp1_r": 1.0} -> merged {"be_r": 0.75, "max_bars": 24, "stop_atr": 1.0, "tp1_frac": 0.5, "tp1_r": 1.0, "tp_r": 2.0}; Source: https://github.com/MoeAZack/ZackBot2/issues/13#issuecomment-6198970276

| key | side | registry_sides | mgmt | status | bust (time · first dead trade # · post-bust trades / net R, excluded) | trades | net R | net PnL | PF | win % | max DD (R) | max DD (equity) | fee share | MFE R | MAE R* | giveback R | R above / below BTC EMA200 |
|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|
| exp_scalp | long | both | defaults | ok | not recorded (runner before c8c7b92) | 1671 | -371.64 | -490.15 | 0.71 | 39.1% | 386.09 | -98.3% | n/a (gross<=0) | 0.67 | 1.03 | 0.89 | -345.1 (1498) / -26.5 (173) |
| exp_scalp | long | both | profit_capture | ok | not recorded (runner before c8c7b92) | 1789 | -377.59 | -489.92 | 0.56 | 53.2% | 383.28 | -98.1% | n/a (gross<=0) | 0.66 | 0.90 | 0.87 | -358.0 (1622) / -19.6 (167) |
| exp_scalp | short | both | defaults | ok | not recorded (runner before c8c7b92) | 1689 | -365.95 | -489.97 | 0.67 | 39.3% | 365.95 | -98.0% | n/a (gross<=0) | 0.69 | 1.02 | 0.90 | -286.1 (1401) / -79.9 (288) |
| exp_scalp | short | both | profit_capture | ok | not recorded (runner before c8c7b92) | 1697 | -376.55 | -489.97 | 0.54 | 53.2% | 377.23 | -98.0% | n/a (gross<=0) | 0.66 | 0.89 | 0.88 | -326.5 (1505) / -50.0 (192) |
| exp_structure | long | both | defaults | ok | not recorded (runner before c8c7b92) | 1636 | -368.87 | -489.98 | 0.70 | 31.2% | 378.36 | -98.2% | n/a (gross<=0) | 0.81 | 1.02 | 1.03 | -367.8 (1502) / -1.1 (134) |
| exp_structure | long | both | profit_capture | ok | not recorded (runner before c8c7b92) | 1654 | -377.89 | -489.96 | 0.57 | 40.3% | 379.44 | -98.0% | n/a (gross<=0) | 0.75 | 0.90 | 0.98 | -363.6 (1558) / -14.3 (96) |
| exp_structure | short | both | defaults | ok | not recorded (runner before c8c7b92) | 2170 | -345.98 | -490.04 | 0.76 | 33.7% | 347.71 | -98.0% | 8776.9% | 0.86 | 1.02 | 1.02 | -159.7 (1319) / -186.3 (851) |
| exp_structure | short | both | profit_capture | ok | not recorded (runner before c8c7b92) | 2194 | -367.56 | -489.96 | 0.66 | 43.9% | 368.79 | -98.0% | n/a (gross<=0) | 0.82 | 0.88 | 0.99 | -205.3 (1465) / -162.2 (729) |

\* MAE is a bar-extreme approximation over [entry bar, exit bar] (the exit bar may include movement after the exit). Busted slices (equity <= 10% of the start) show PRE-BUST stats in every row and headline; the post-bust trades are listed in the bust column and excluded. MFE uses the backtest path best price. Both in stop-distance R. Fee share = fees / gross (gross = price P&L after slippage, before fees and funding); n/a when gross <= 0.

## INCOMPLETE symbols (excluded from every aggregate, never filled)

- HYPEUSDT: 22150 of 68764 15m candles missing in [warm-up start, END) (2024-10-11T17:00:00Z .. 2025-05-30T10:15:00Z), off-grid 0, duplicates 0

## Headline (registry sides only)

Registry-side slices with trades: 8. Off-registry slices excluded from this headline: 0 (none).

| key | side | mgmt | trades | net R | PF |
|---|---|---|---|---|---|
| exp_structure | short | defaults | 2170 | -345.98 | 0.76 |
| exp_scalp | short | defaults | 1689 | -365.95 | 0.67 |
| exp_structure | short | profit_capture | 2194 | -367.56 | 0.66 |
| exp_structure | long | defaults | 1636 | -368.87 | 0.70 |
| exp_scalp | long | defaults | 1671 | -371.64 | 0.71 |
| exp_scalp | short | profit_capture | 1697 | -376.55 | 0.54 |
| exp_scalp | long | profit_capture | 1789 | -377.59 | 0.56 |
| exp_structure | long | profit_capture | 1654 | -377.89 | 0.57 |

registry_sides = strategies.STRATEGIES[key]['sides'] as recorded in each packet. An off-registry side is one the registry does not list for that key: the runner can still simulate it (the signal function emits it), but it is hypothetical and is excluded from every headline line. Not evidence of edge.

## Per-symbol net R

| key | side | BTCUSDT | ETHUSDT | ZECUSDT | SOLUSDT | XRPUSDT | NEARUSDT | DOGEUSDT | UNIUSDT | SUIUSDT |
|---|---|---|---|---|---|---|---|---|---|---|
| exp_scalp | long | -81.3 (201) | -105.7 (208) | -15.8 (156) | -45.4 (173) | -41.1 (188) | -13.2 (166) | -26.6 (201) | -5.3 (193) | -37.1 (185) |
| exp_scalp | long | -67.0 (191) | -86.8 (207) | -11.1 (179) | -56.7 (198) | -36.0 (204) | -21.9 (174) | -40.6 (220) | -17.1 (216) | -40.5 (200) |
| exp_scalp | short | -90.7 (158) | -45.6 (187) | -43.8 (210) | -36.6 (176) | -39.9 (180) | -27.0 (217) | 1.9 (169) | -23.0 (181) | -61.3 (211) |
| exp_scalp | short | -84.6 (160) | -64.9 (190) | -24.6 (225) | -42.9 (175) | -46.6 (180) | -36.9 (215) | -12.7 (175) | -13.3 (175) | -50.2 (202) |
| exp_structure | long | -86.7 (192) | -57.8 (185) | -8.7 (194) | -61.3 (198) | -36.6 (202) | -55.3 (167) | -22.1 (173) | -47.7 (174) | 7.3 (151) |
| exp_structure | long | -91.1 (197) | -50.6 (179) | -15.1 (197) | -64.4 (202) | -42.0 (201) | -35.7 (164) | -23.2 (164) | -35.2 (185) | -20.6 (165) |
| exp_structure | short | -75.6 (198) | -24.5 (246) | -41.1 (305) | -26.0 (255) | -59.3 (233) | -21.3 (266) | -15.9 (171) | -46.8 (254) | -35.6 (242) |
| exp_structure | short | -88.0 (206) | -57.3 (247) | -34.5 (306) | -44.3 (266) | -55.2 (227) | -30.8 (268) | -5.3 (177) | -25.7 (255) | -26.5 (242) |

## Packets

| file | packet_sha256 (canonical body) | file sha256 |
|---|---|---|
| exp_scalp_long_defaults.json | `efe1edcce7663c90a22c39c8200d9cf4df7887f5f1c59ad4225f26ba7a88ecdb` | `88ed74642f60071b84631ae61c4eb71e280a068d426399ca8776d08e188f2ee0` |
| exp_scalp_long_profit_capture.json | `250469ef28dab01ac933d80b6350a83a4614a43208c49e2a4fef747dae60c3bb` | `78e2bdc0a8a0d56adc4fc04c0bcdc4573f629b2eee0a755c88b75ba40c797d53` |
| exp_scalp_short_defaults.json | `6efab37c8cda073b0d4ac83227c65b76386de5f0a6596604932b9d6f0c285f17` | `5d4a6d632123401d489c24d25605b6616f75364bfdbc23e4845d48e93a4bd6f9` |
| exp_scalp_short_profit_capture.json | `ea4857437e226dc8a720b9dd9baff11a1427a9fdf62869a47bc23c8f26790a9a` | `75b57dfbcc9ea8c4497cee2b9cf6f71c2502993b514bd7d9417e9f287e89ce4d` |
| exp_structure_long_defaults.json | `829db2dc54967c06c48e64e506a5308e3216648394a767a036d75fb0f7d578f4` | `b8a88ff7bb12bccc5754dbacd1de7356c8759f79e51885ee09a66851f07fe684` |
| exp_structure_long_profit_capture.json | `78bbede1a0b843f8f7a05261f55444f14b9c2a1aa73972fab24e09899b03b27f` | `260c4ada6ed6ec4439100c768d52c5b7bf1b03d912b4fdfa9fe87bdd0765ecba` |
| exp_structure_short_defaults.json | `02a31a8857e366a76286d48205380e3f78e4b5c53c1adee3d85149d1dd382892` | `68adcf7fb6df0e546eeb9b0597604e77ada58d170d0661e111846fb3c94df274` |
| exp_structure_short_profit_capture.json | `fd791cad5e3358f556b1eec39427421d3b30dc8c3ae1883ae0768e9bbef7be1e` | `28d5555f06051876d4411dabef3e4f96f99e5164e621a40ef021bb35ee18c971` |
