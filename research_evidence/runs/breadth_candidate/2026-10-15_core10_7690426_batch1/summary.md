# Breadth backtest batch 1 — CORE-10 RANGE/VWAPREV/BREAKOUT (defaults)

REVIEW-ONLY research evidence. non_promotable. Development window only; not evidence of edge.

- commit: `7690426103544e09df1f008397a28b02c6c3d836`
- window: 2024-10-14T00:00:00Z -> 2026-09-28T00:00:00Z (end-exclusive, 4284 4h bars; warm-up 220 bars from 2024-09-07T08:00:00Z)
- runner sha256: `run_breadth_candidate.py` f934786fc7aacae7de3e3f8b2ff952d8622fe62d08600cfb610f66ce3f8ced15 · `run_vwaprev_candidate.py` c2f43564cc1da4cd79e12253ece82d10b3f82c1ecdbbc11aeafa80202060d23e
- universe: static-top40-minhist365-contract-20261011 tier CORE-10 (digest 1c06c18d2fc82413…); run symbols: BTCUSDT, ETHUSDT, ZECUSDT, SOLUSDT, XRPUSDT, NEARUSDT, DOGEUSDT, UNIUSDT, SUIUSDT
- costs: taker fee 0.0005 per side, slippage 0.0002 per side, funding 0.000050 of notional per 4h bar; market (taker) entries; legacy exchange rules
- account (app defaults): start 500.0, risk 1%/trade, max_pos 4, max_lev 10, daily halt 8%
- mgmt: `defaults` = frozen strategy mgmt (empty override). profit_capture variant and the 15m keys are pending Codex.

| key | side | mgmt | status | trades | net R | net PnL | PF | win % | max DD (R) | max DD (equity) | fee share | MFE R | MAE R* | giveback R | R above / below BTC EMA200 |
|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|
| exp_breakout | long | defaults | ok | 489 | 86.69 | 513.20 | 1.29 | 33.7% | 36.63 | -32.7% | 11.8% | 1.21 | 0.82 | 1.03 | 70.8 (323) / 15.9 (166) |
| exp_breakout | short | defaults | ok | 420 | -22.16 | -132.79 | 0.87 | 31.4% | 43.38 | -38.2% | n/a (gross<=0) | 1.12 | 0.79 | 1.17 | 7.3 (185) / -29.5 (235) |
| exp_range | long | defaults | ok | 606 | -11.07 | -87.63 | 0.94 | 48.2% | 42.88 | -39.2% | 283.1% | 0.85 | 0.87 | 0.87 | 11.2 (230) / -22.3 (376) |
| exp_range | short | defaults | ok | 653 | -20.00 | -121.42 | 0.91 | 50.1% | 36.96 | -33.6% | n/a (gross<=0) | 0.82 | 0.91 | 0.85 | -46.6 (465) / 26.6 (188) |
| exp_vwaprev | long | defaults | ok | 623 | -30.12 | -158.63 | 0.89 | 46.4% | 61.24 | -49.2% | n/a (gross<=0) | 0.84 | 0.91 | 0.89 | 8.7 (236) / -38.8 (387) |
| exp_vwaprev | short | defaults | ok | 658 | -25.47 | -142.33 | 0.89 | 49.7% | 42.45 | -37.3% | n/a (gross<=0) | 0.82 | 0.92 | 0.86 | -53.9 (464) / 28.4 (194) |
| exp_scalp | long | defaults | BLOCKED: no 15m cache | – | – | – | – | – | – | – | – | – | – | – | – |
| exp_scalp | short | defaults | BLOCKED: no 15m cache | – | – | – | – | – | – | – | – | – | – | – | – |
| exp_structure | long | defaults | BLOCKED: no 15m cache | – | – | – | – | – | – | – | – | – | – | – | – |
| exp_structure | short | defaults | BLOCKED: no 15m cache | – | – | – | – | – | – | – | – | – | – | – | – |

\* MAE is a bar-extreme approximation over [entry bar, exit bar] (the exit bar may include movement after the exit). MFE uses the backtest path best price. Both in stop-distance R. Fee share = fees / gross (gross = price P&L after slippage, before fees and funding); n/a when gross <= 0.

## INCOMPLETE symbols (excluded from every aggregate, never filled)

- HYPEUSDT: 1590 of 4504 4h candles missing in [warm-up start, END) (2024-09-07T08:00:00Z .. 2025-05-30T04:00:00Z), off-grid 0, duplicates 0

## Per-symbol net R

| key | side | BTCUSDT | ETHUSDT | ZECUSDT | SOLUSDT | XRPUSDT | NEARUSDT | DOGEUSDT | UNIUSDT | SUIUSDT |
|---|---|---|---|---|---|---|---|---|---|---|
| exp_breakout | long | -7.3 (73) | -5.7 (71) | 22.3 (79) | 13.9 (55) | 31.5 (49) | 12.0 (55) | 15.0 (31) | 5.6 (41) | -0.5 (35) |
| exp_breakout | short | -11.5 (59) | -6.4 (44) | -0.8 (52) | 5.9 (53) | -7.9 (48) | -2.7 (46) | 6.1 (38) | 6.3 (38) | -11.2 (42) |
| exp_range | long | 4.9 (67) | -3.4 (71) | 8.1 (57) | -1.0 (66) | -4.6 (86) | -13.5 (72) | -1.2 (73) | 0.3 (53) | -0.7 (61) |
| exp_range | short | -0.1 (83) | -1.0 (63) | -4.9 (101) | 1.6 (71) | 11.3 (61) | -13.1 (75) | -3.6 (73) | -3.9 (61) | -6.2 (65) |
| exp_vwaprev | long | -2.8 (69) | -7.7 (70) | 3.3 (59) | -0.0 (64) | -4.8 (87) | -14.5 (73) | -4.4 (73) | 2.4 (61) | -1.5 (67) |
| exp_vwaprev | short | -4.5 (86) | -2.1 (65) | -6.2 (101) | 3.3 (74) | 8.9 (61) | -10.8 (74) | 0.1 (68) | -8.0 (61) | -6.2 (68) |

## Packets

| file | packet_sha256 (canonical body) | file sha256 |
|---|---|---|
| exp_breakout_long_defaults.json | `4601e49918727ee7c06f64e736d8a4c2a311bb6e859cfa516ad19fdb2aaf032f` | `a9524925ce65fffb88b44b15a0a60dd5399bccf331abcb704d719f4156dd9a6b` |
| exp_breakout_short_defaults.json | `64b6fcb0a0428c3ff36311d83d66cb42311713450eccc2e2e87ecf5d8408acfe` | `471490f0e0b28963f698fd1871a8537dd10201827272d78cde048518bbc51147` |
| exp_range_long_defaults.json | `8ba7bf569cb1b53b3c0758419e251b4ec6b0dbd4c0bf2625b8e10050803a8333` | `ea2294b671da3c6292cd92ea8fed9c2619e7777c31fccf46d6f7c7f401770daf` |
| exp_range_short_defaults.json | `b9c3ceb8010fd98d5e0747027cdb624aad466badece5df0f8728d245abbd8f3d` | `8095f0cc0a6de500c3816b8d1387746419d49281dcd1d42a90d835234a8f2095` |
| exp_vwaprev_long_defaults.json | `77d26abc391d1d60cf4f548d7538e630bcb9750ffa9995ffa7835a10fd5eb9a3` | `eb00c96e58135386c5cca18841f0daed9f5a707573b57d5871f2e8380ee4b576` |
| exp_vwaprev_short_defaults.json | `0aa1bc280da2839cb22bbc26c0fbf2dbf2dd25d6fc3979ec51269284b17b3873` | `fa83017beae02b3a47aee99d04c694a2c960e390ea1b8c251b2ddda6cc143bff` |
