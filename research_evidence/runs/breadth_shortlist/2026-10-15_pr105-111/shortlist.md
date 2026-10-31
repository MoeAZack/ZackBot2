# Breadth shortlist packet: review PRs #105-#111 (REVIEW ONLY, no edge claim)

Built only from the committed packets of #105-#111 by `build_shortlist.py` (`git show <head>:<path>`). No candle was read and no number was recomputed. Development window 2024-10-14T00:00:00Z -> 2026-09-28T00:00:00Z (end exclusive, sealed cutoff 2026-09-28T00:00:00Z). No holdout, no tuning. `packet_sha256` = `3d150f7c5e6aeca2ac2b3a1bb434a578f997986ba7c246b103215fe9f7291ec7`.

## Status rules (only these; no other criterion)

- **shortlist-candidate**: registered side AND defaults net R > 0 in BOTH CORE-10 and CORE-20 (the strategy's own timeframe; 15m keys use pre-bust stats). "Positive" = net R (sum of per-trade R) > 0; no other criterion.
- **provisional (unregistered; needs new prereg, no backdating)**: Codex ruling 6199976344: exactly breakout_pyramid short, squeeze_tp short, donchian_ens short.
- **below-line (unregistered, more evidence needed)**: Codex ruling 6199976344: exactly pullback_rsi short, ema_mom short, ema_st short.
- **negative**: registered side that does not meet the shortlist-candidate rule.
- **blocked**: unregistered side not named by the Codex ruling (includes every side whose signal never fires: "not emitted").

## Columns

- `registered_sides`: formal eligibility, strategies.STRATEGIES[key]['sides'] (the strategy registry; equals the registry_sides field each packet records). The append-only research ledger research_evidence/ledger/REGISTRY.jsonl registers research families (range_bb_mr, short_breakdown, trend_ema_mom, trend_ema_mom_short, res01_infra), not these runtime keys, so no row below holds a holdout-eligible preregistration.
- `paper_experimental_sides`: engine.py @ 87f8465: L350: BREADTH_BOTH = frozenset({'ema_st', 'ema_mom', 'donchian_ens', 'breakout_pyramid', 'squeeze_tp', 'pullback_rsi'}). L348-349 comment (Codex ruling 3): long-default strategies whose signal function already emits both sides run 'both' in PAPER; every other strategy keeps its registry sides. L397-411: breadth_sleeves(), L411: sides='both' if k in BREADTH_BOTH else st['sides'] (one slot per registry key, breadth_groups L382, L386 keys = sorted(S.STRATEGIES)). PAPER experiment != formal registration (Codex 6199976344).
- Cells: `trades / PF / net R / maxDD R`. 4h keys: full-run stats (no 4h slice busts). **15m keys (exp_scalp, exp_structure): PRE-BUST ONLY**; every 15m slice busts (equity <= 10% of start) and only the trades before the bust are counted.
- `n/a` = no packet exists for that mgmt (profit_capture exists only for the six keys in Codex 6198970276).

| key | side | tf | registered_sides | paper_experimental_sides | CORE-10 defaults | CORE-20 defaults | CORE-10 profit_capture | CORE-20 profit_capture | status |
|---|---|---|---|---|---|---|---|---|---|
| bear_breakdown | long | 4h | short | short | 0 tr (not emitted) | 0 tr (not emitted) | n/a | n/a | blocked |
| bear_breakdown | short | 4h | short | short | 267 / 0.99 / +9.0 / 27.9 | 316 / 1.12 / +24.0 / 18.5 | n/a | n/a | shortlist-candidate |
| breakout_pyramid | long | 4h | long | both | 283 / 1.72 / +151.9 / 20.2 | 365 / 2.75 / +324.2 / 27.7 | n/a | n/a | shortlist-candidate |
| breakout_pyramid | short | 4h | long | both | 270 / 1.30 / +86.8 / 33.2 | 379 / 1.08 / +63.4 / 40.0 | n/a | n/a | provisional (unregistered; needs new prereg, no backdating) |
| dca_dip | long | 4h | long | long | 187 / 0.78 / -4.6 / 8.5 | 283 / 0.86 / -3.9 / 10.2 | n/a | n/a | negative |
| dca_dip | short | 4h | long | long | 0 tr (not emitted) | 0 tr (not emitted) | n/a | n/a | blocked |
| donchian_ens | long | 4h | long | both | 215 / 1.56 / +54.7 / 21.2 | 291 / 2.27 / +149.2 / 21.8 | n/a | n/a | shortlist-candidate |
| donchian_ens | short | 4h | long | both | 178 / 1.44 / +32.7 / 13.7 | 233 / 1.26 / +28.0 / 10.9 | n/a | n/a | provisional (unregistered; needs new prereg, no backdating) |
| ema_mom | long | 4h | long | both | 97 / 2.73 / +59.9 / 5.0 | 160 / 2.77 / +101.1 / 7.6 | n/a | n/a | shortlist-candidate |
| ema_mom | short | 4h | long | both | 101 / 0.86 / -4.7 / 11.5 | 188 / 1.01 / +2.6 / 13.5 | n/a | n/a | below-line (unregistered, more evidence needed) |
| ema_st | long | 4h | long | both | 132 / 2.23 / +92.0 / 12.3 | 181 / 1.65 / +80.5 / 24.3 | n/a | n/a | shortlist-candidate |
| ema_st | short | 4h | long | both | 152 / 0.80 / -13.6 / 20.1 | 210 / 0.85 / -14.4 / 26.9 | n/a | n/a | below-line (unregistered, more evidence needed) |
| exp_breakout | long | 4h | both | both | 489 / 1.29 / +86.7 / 36.6 | 586 / 1.20 / +81.9 / 42.4 | 506 / 1.15 / +43.0 / 26.7 | 620 / 1.14 / +52.2 / 32.7 | shortlist-candidate |
| exp_breakout | short | 4h | both | both | 420 / 0.87 / -22.2 / 43.4 | 481 / 0.93 / -8.0 / 36.3 | 439 / 0.94 / -7.4 / 28.4 | 509 / 0.98 / +3.0 / 25.8 | negative |
| exp_mom_short | long | 4h | short | short | 0 tr (not emitted) | 0 tr (not emitted) | n/a | n/a | blocked |
| exp_mom_short | short | 4h | short | short | 261 / 0.99 / +1.5 / 21.3 | 285 / 1.10 / +15.8 / 19.4 | n/a | n/a | shortlist-candidate |
| exp_range | long | 4h | both | both | 606 / 0.94 / -11.1 / 42.9 | 905 / 0.93 / -19.9 / 68.9 | 702 / 0.90 / -17.9 / 36.6 | 1084 / 0.87 / -35.6 / 71.6 | negative |
| exp_range | short | 4h | both | both | 653 / 0.91 / -20.0 / 37.0 | 934 / 0.92 / -24.2 / 45.2 | 725 / 0.80 / -47.0 / 47.0 | 1094 / 0.85 / -47.3 / 47.6 | negative |
| exp_rip_sell_dca | long | 4h | short | short | 0 tr (not emitted) | 0 tr (not emitted) | n/a | n/a | blocked |
| exp_rip_sell_dca | short | 4h | short | short | 301 / 0.68 / -12.7 / 14.4 | 490 / 0.93 / -3.1 / 11.0 | n/a | n/a | negative |
| exp_scalp | long | 15m | both | both | 1235 / 0.72 / -215.9 / 230.3 [pre-bust] | 1539 / 0.72 / -208.8 / 228.7 [pre-bust] | 1135 / 0.56 / -223.1 / 228.8 [pre-bust] | 1120 / 0.56 / -219.6 / 224.4 [pre-bust] | negative |
| exp_scalp | short | 15m | both | both | 929 / 0.66 / -216.0 / 216.0 [pre-bust] | 1129 / 0.70 / -212.2 / 213.4 [pre-bust] | 899 / 0.53 / -220.6 / 221.0 [pre-bust] | 1078 / 0.54 / -219.7 / 221.1 [pre-bust] | negative |
| exp_structure | long | 15m | both | both | 1053 / 0.70 / -216.7 / 226.2 [pre-bust] | 1207 / 0.74 / -210.1 / 229.1 [pre-bust] | 1090 / 0.58 / -221.4 / 222.6 [pre-bust] | 1224 / 0.59 / -217.7 / 224.8 [pre-bust] | negative |
| exp_structure | short | 15m | both | both | 1080 / 0.75 / -210.9 / 212.7 [pre-bust] | 1366 / 0.75 / -198.3 / 201.1 [pre-bust] | 1232 / 0.66 / -218.9 / 220.1 [pre-bust] | 1203 / 0.63 / -214.6 / 216.3 [pre-bust] | negative |
| exp_trend | long | 4h | both | both | 101 / 1.80 / +27.4 / 4.9 | 171 / 2.00 / +56.7 / 7.6 | 102 / 2.22 / +30.9 / 4.8 | 171 / 2.08 / +48.7 / 7.8 | shortlist-candidate |
| exp_trend | short | 4h | both | both | 101 / 0.98 / +0.3 / 13.3 | 189 / 1.12 / +10.2 / 13.5 | 101 / 0.76 / -7.7 / 11.4 | 190 / 1.06 / +4.9 / 14.3 | shortlist-candidate |
| exp_vwaprev | long | 4h | both | both | 623 / 0.89 / -30.1 / 61.2 | 909 / 0.92 / -23.6 / 70.8 | 723 / 0.83 / -36.4 / 54.4 | 1101 / 0.87 / -39.3 / 74.6 | negative |
| exp_vwaprev | short | 4h | both | both | 658 / 0.89 / -25.5 / 42.4 | 940 / 0.87 / -50.5 / 54.7 | 720 / 0.82 / -42.9 / 43.6 | 1069 / 0.87 / -42.3 / 43.7 | negative |
| hot_coin | long | 4h | long | long | 313 / 1.57 / +74.7 / 15.0 | 394 / 1.48 / +83.9 / 19.2 | n/a | n/a | shortlist-candidate |
| hot_coin | short | 4h | long | long | 0 tr (not emitted) | 0 tr (not emitted) | n/a | n/a | blocked |
| pullback_rsi | long | 4h | long | both | 201 / 1.14 / +15.8 / 15.1 | 268 / 1.19 / +25.8 / 15.9 | n/a | n/a | shortlist-candidate |
| pullback_rsi | short | 4h | long | both | 250 / 1.04 / +9.2 / 23.6 | 340 / 0.96 / -1.9 / 31.8 | n/a | n/a | below-line (unregistered, more evidence needed) |
| rotation | long | 4h | long | long | 785 / 1.50 / +115.2 / 26.5 | 858 / 1.35 / +82.6 / 34.5 | n/a | n/a | shortlist-candidate |
| rotation | short | 4h | long | long | 0 tr (not emitted) | 0 tr (not emitted) | n/a | n/a | blocked |
| squeeze_tp | long | 4h | long | both | 205 / 1.18 / +24.1 / 18.4 | 291 / 1.21 / +37.3 / 22.7 | n/a | n/a | shortlist-candidate |
| squeeze_tp | short | 4h | long | both | 226 / 1.27 / +34.4 / 21.6 | 327 / 1.19 / +39.5 / 25.1 | n/a | n/a | provisional (unregistered; needs new prereg, no backdating) |

## Completeness (INCOMPLETE symbols, excluded and never filled)

- CORE-10 15m: HYPEUSDT
- CORE-10 4h: HYPEUSDT
- CORE-20 15m: HYPEUSDT, PUMPUSDT
- CORE-20 4h: HYPEUSDT, PUMPUSDT
- Superseded: #108: CORE-20 4h on 13 of 20 symbols; superseded by #110 (full coverage, same runner family)

## 15m bust points (pre-bust headline)

- exp_scalp long CORE-10 defaults: bust 2024-12-11T00:30:00Z at trade 1236, 436 trades after (excluded)
- exp_scalp long CORE-10 profit_capture: bust 2024-11-29T23:00:00Z at trade 1136, 654 trades after (excluded)
- exp_scalp long CORE-20 defaults: bust 2024-12-05T16:30:00Z at trade 1540, 341 trades after (excluded)
- exp_scalp long CORE-20 profit_capture: bust 2024-11-20T11:00:00Z at trade 1121, 718 trades after (excluded)
- exp_scalp short CORE-10 defaults: bust 2024-12-07T11:30:00Z at trade 930, 760 trades after (excluded)
- exp_scalp short CORE-10 profit_capture: bust 2024-12-01T00:45:00Z at trade 900, 798 trades after (excluded)
- exp_scalp short CORE-20 defaults: bust 2024-12-03T11:15:00Z at trade 1130, 897 trades after (excluded)
- exp_scalp short CORE-20 profit_capture: bust 2024-11-23T10:00:00Z at trade 1079, 1037 trades after (excluded)
- exp_structure long CORE-10 defaults: bust 2024-12-03T10:45:00Z at trade 1054, 583 trades after (excluded)
- exp_structure long CORE-10 profit_capture: bust 2024-11-29T08:00:00Z at trade 1091, 564 trades after (excluded)
- exp_structure long CORE-20 defaults: bust 2024-12-02T02:30:00Z at trade 1208, 546 trades after (excluded)
- exp_structure long CORE-20 profit_capture: bust 2024-11-24T05:30:00Z at trade 1225, 662 trades after (excluded)
- exp_structure short CORE-10 defaults: bust 2024-12-31T08:00:00Z at trade 1081, 1090 trades after (excluded)
- exp_structure short CORE-10 profit_capture: bust 2025-01-01T05:15:00Z at trade 1233, 962 trades after (excluded)
- exp_structure short CORE-20 defaults: bust 2024-12-27T23:45:00Z at trade 1367, 1049 trades after (excluded)
- exp_structure short CORE-20 profit_capture: bust 2024-12-14T22:15:00Z at trade 1204, 916 trades after (excluded)

## Sources (every cell cites its packet; full per-cell citation with packet_sha256 in shortlist.json)

| cell | PR | head | path | packet_sha256 |
|---|---|---|---|---|
| bear_breakdown long core10 defaults | #106 | 4f12e27 | `research_evidence/runs/breadth_candidate/2026-10-15_core10_7690426_batch2/bear_breakdown_long_defaults.json` | `5db5790416287001349d51f2a8164ec559c8d155429dd49c795341a64d7d1e54` |
| bear_breakdown long core20 defaults | #110 | c808794 | `research_evidence/runs/breadth_candidate/2026-10-15_core20_c8c7b92_batch6/bear_breakdown_long_defaults.json` | `35da2df68e34ee8d591fe193882c7d3431b84fd60256db5cd1dabeebaa634d37` |
| bear_breakdown short core10 defaults | #106 | 4f12e27 | `research_evidence/runs/breadth_candidate/2026-10-15_core10_7690426_batch2/bear_breakdown_short_defaults.json` | `238383e5921eb59687dcad6429e1223042daab388d4d3a554d4da8f66cc80eac` |
| bear_breakdown short core20 defaults | #110 | c808794 | `research_evidence/runs/breadth_candidate/2026-10-15_core20_c8c7b92_batch6/bear_breakdown_short_defaults.json` | `887937a2d39d76512260b59f1e47782cc1ca2fcf50555986b64ba8293e9b15a4` |
| breakout_pyramid long core10 defaults | #106 | 4f12e27 | `research_evidence/runs/breadth_candidate/2026-10-15_core10_7690426_batch2/breakout_pyramid_long_defaults.json` | `24fce262182e0c359bb867f02ff4bc66b1d8720e4fd173e87b57b63e61094f6b` |
| breakout_pyramid long core20 defaults | #110 | c808794 | `research_evidence/runs/breadth_candidate/2026-10-15_core20_c8c7b92_batch6/breakout_pyramid_long_defaults.json` | `91aadb252a6aaf8cc06a117945240a6944359394d203467e2e04fb8d0f2b46a2` |
| breakout_pyramid short core10 defaults | #106 | 4f12e27 | `research_evidence/runs/breadth_candidate/2026-10-15_core10_7690426_batch2/breakout_pyramid_short_defaults.json` | `e682b432e8d3cc6ad6741a0ef87a7921f9ec3140349ae82af42e2f0a4ce16d65` |
| breakout_pyramid short core20 defaults | #110 | c808794 | `research_evidence/runs/breadth_candidate/2026-10-15_core20_c8c7b92_batch6/breakout_pyramid_short_defaults.json` | `fad612e7084242017140ebff1787473567b98158a8a25f80f2e581071a34b4e6` |
| dca_dip long core10 defaults | #106 | 4f12e27 | `research_evidence/runs/breadth_candidate/2026-10-15_core10_7690426_batch2/dca_dip_long_defaults.json` | `3921ac3af51c5d6ba6f9d497b594e642d6b02208be6c6114983dafdef9792c3a` |
| dca_dip long core20 defaults | #110 | c808794 | `research_evidence/runs/breadth_candidate/2026-10-15_core20_c8c7b92_batch6/dca_dip_long_defaults.json` | `ef00df96b0e20203d399d3fd5c87c6ceeb218e51c6a577af167efcc5acbba352` |
| dca_dip short core10 defaults | #106 | 4f12e27 | `research_evidence/runs/breadth_candidate/2026-10-15_core10_7690426_batch2/dca_dip_short_defaults.json` | `dfa1f26110e761e2d100b32d8d8d08884bcd6208b5a4339fce5bd69a20b1a8ae` |
| dca_dip short core20 defaults | #110 | c808794 | `research_evidence/runs/breadth_candidate/2026-10-15_core20_c8c7b92_batch6/dca_dip_short_defaults.json` | `e24a8875736d17564e7bf1baac45919b339b67c2fbe61ce32592a9ac738f19e6` |
| donchian_ens long core10 defaults | #106 | 4f12e27 | `research_evidence/runs/breadth_candidate/2026-10-15_core10_7690426_batch2/donchian_ens_long_defaults.json` | `e0c7b7a9cd1ebe53b942d15d4066bc11a81e7dcfc9c463431627b58bd266829e` |
| donchian_ens long core20 defaults | #110 | c808794 | `research_evidence/runs/breadth_candidate/2026-10-15_core20_c8c7b92_batch6/donchian_ens_long_defaults.json` | `4148b4594f7f212b74ea0d5d6449d9c98d49463b9f5b3dba94d0a188a93f426c` |
| donchian_ens short core10 defaults | #106 | 4f12e27 | `research_evidence/runs/breadth_candidate/2026-10-15_core10_7690426_batch2/donchian_ens_short_defaults.json` | `35738cd8f2e9d07c051eef17a6ec7ed38afdf731c185785b23f0bfcf7be36ab0` |
| donchian_ens short core20 defaults | #110 | c808794 | `research_evidence/runs/breadth_candidate/2026-10-15_core20_c8c7b92_batch6/donchian_ens_short_defaults.json` | `d77f1b1f04258d4ef27a08e115f89975ad55322db1419180c02c7230bd2bae93` |
| ema_mom long core10 defaults | #106 | 4f12e27 | `research_evidence/runs/breadth_candidate/2026-10-15_core10_7690426_batch2/ema_mom_long_defaults.json` | `53af447c52ef5495a22b672fdd5c1fe4236e1523378f29b88436d69aa21f5a0c` |
| ema_mom long core20 defaults | #110 | c808794 | `research_evidence/runs/breadth_candidate/2026-10-15_core20_c8c7b92_batch6/ema_mom_long_defaults.json` | `49fb8ad11d8a1c28b8fc884da6b09b1ca111ffd16d7a1100ff03529c739efbca` |
| ema_mom short core10 defaults | #106 | 4f12e27 | `research_evidence/runs/breadth_candidate/2026-10-15_core10_7690426_batch2/ema_mom_short_defaults.json` | `de276fc1ba372739744cca59a2d24eb8a55675a6db79cac055ae33d07dd79757` |
| ema_mom short core20 defaults | #110 | c808794 | `research_evidence/runs/breadth_candidate/2026-10-15_core20_c8c7b92_batch6/ema_mom_short_defaults.json` | `65dfe58749c93e3e933e410a80906e62ef05eb29a28ba054a9c2729e1a8725dd` |
| ema_st long core10 defaults | #106 | 4f12e27 | `research_evidence/runs/breadth_candidate/2026-10-15_core10_7690426_batch2/ema_st_long_defaults.json` | `f564edbee2d266d35535dd17549160fe1e6576e98207371d02674a52fde3c1b8` |
| ema_st long core20 defaults | #110 | c808794 | `research_evidence/runs/breadth_candidate/2026-10-15_core20_c8c7b92_batch6/ema_st_long_defaults.json` | `117180127bd1cb38a131431aee2cdc6e23501f83d3d1e32ab2f9da0924fa5552` |
| ema_st short core10 defaults | #106 | 4f12e27 | `research_evidence/runs/breadth_candidate/2026-10-15_core10_7690426_batch2/ema_st_short_defaults.json` | `d92ab460cc6f69720f627d3e18f2d85a7c0080dd50d6c96fbea6052e073fddb4` |
| ema_st short core20 defaults | #110 | c808794 | `research_evidence/runs/breadth_candidate/2026-10-15_core20_c8c7b92_batch6/ema_st_short_defaults.json` | `adf9bc072848666f708e9b2eebaabf70f6944443c8562cd542efcd0e51d994f5` |
| exp_breakout long core10 defaults | #105 | 9086e74 | `research_evidence/runs/breadth_candidate/2026-10-15_core10_7690426_batch1/exp_breakout_long_defaults.json` | `4601e49918727ee7c06f64e736d8a4c2a311bb6e859cfa516ad19fdb2aaf032f` |
| exp_breakout long core10 profit_capture | #107 | e9872eb | `research_evidence/runs/breadth_candidate/2026-10-15_core10_339ba68_batch3/exp_breakout_long_profit_capture.json` | `f5f61384039ac27a0c203399780661644483631e90d7d35c38d679bade489c3f` |
| exp_breakout long core20 defaults | #110 | c808794 | `research_evidence/runs/breadth_candidate/2026-10-15_core20_c8c7b92_batch6/exp_breakout_long_defaults.json` | `0158d69af90e80e1b0223e69b8999d57ae4758f58579c93ad8968fd529e79bff` |
| exp_breakout long core20 profit_capture | #110 | c808794 | `research_evidence/runs/breadth_candidate/2026-10-15_core20_c8c7b92_batch6/exp_breakout_long_profit_capture.json` | `93eee78a7e5a2279aba66f93493594ab66ef62f2311f3f2a3e9492efc8b68567` |
| exp_breakout short core10 defaults | #105 | 9086e74 | `research_evidence/runs/breadth_candidate/2026-10-15_core10_7690426_batch1/exp_breakout_short_defaults.json` | `64b6fcb0a0428c3ff36311d83d66cb42311713450eccc2e2e87ecf5d8408acfe` |
| exp_breakout short core10 profit_capture | #107 | e9872eb | `research_evidence/runs/breadth_candidate/2026-10-15_core10_339ba68_batch3/exp_breakout_short_profit_capture.json` | `98eaad0a345c250ba30d7d1f57a0c11a63bed63c9bb6e50d317163f9f0639847` |
| exp_breakout short core20 defaults | #110 | c808794 | `research_evidence/runs/breadth_candidate/2026-10-15_core20_c8c7b92_batch6/exp_breakout_short_defaults.json` | `b46c8cde317e53dba6e0c039e3a44f51109447d4ae639af2ff3759bce78baab5` |
| exp_breakout short core20 profit_capture | #110 | c808794 | `research_evidence/runs/breadth_candidate/2026-10-15_core20_c8c7b92_batch6/exp_breakout_short_profit_capture.json` | `9adc93499c1e09588824732acfac9112c739fec029058a5c65ee0f29bb666a06` |
| exp_mom_short long core10 defaults | #106 | 4f12e27 | `research_evidence/runs/breadth_candidate/2026-10-15_core10_7690426_batch2/exp_mom_short_long_defaults.json` | `8bab5a984a7a4cf19ba3f50d16f049f176bda02d0ecce654bcf2bde71a4c6b70` |
| exp_mom_short long core20 defaults | #110 | c808794 | `research_evidence/runs/breadth_candidate/2026-10-15_core20_c8c7b92_batch6/exp_mom_short_long_defaults.json` | `13f57623bf404ebb4c1615fec1556ca13fa7221a2d064cb42d6780cb8f7c204a` |
| exp_mom_short short core10 defaults | #106 | 4f12e27 | `research_evidence/runs/breadth_candidate/2026-10-15_core10_7690426_batch2/exp_mom_short_short_defaults.json` | `96530ae0bcee10aaf90e6993b0ff91c5f17f9707718aa3a8b226cad3407e84e9` |
| exp_mom_short short core20 defaults | #110 | c808794 | `research_evidence/runs/breadth_candidate/2026-10-15_core20_c8c7b92_batch6/exp_mom_short_short_defaults.json` | `f5ee3b92702fa04d8dccd471a396f172c3d4f3e914a55eff43c91ac6e2511a15` |
| exp_range long core10 defaults | #105 | 9086e74 | `research_evidence/runs/breadth_candidate/2026-10-15_core10_7690426_batch1/exp_range_long_defaults.json` | `8ba7bf569cb1b53b3c0758419e251b4ec6b0dbd4c0bf2625b8e10050803a8333` |
| exp_range long core10 profit_capture | #107 | e9872eb | `research_evidence/runs/breadth_candidate/2026-10-15_core10_339ba68_batch3/exp_range_long_profit_capture.json` | `889fe9f3aeee74b5a2a1b1a46fcd4b50612dd6c7817f77244738a938509ca7cf` |
| exp_range long core20 defaults | #110 | c808794 | `research_evidence/runs/breadth_candidate/2026-10-15_core20_c8c7b92_batch6/exp_range_long_defaults.json` | `a5e9b7a772bf9dd3f84eb758e2562aa4d7d51a212b04805633b7334873a90646` |
| exp_range long core20 profit_capture | #110 | c808794 | `research_evidence/runs/breadth_candidate/2026-10-15_core20_c8c7b92_batch6/exp_range_long_profit_capture.json` | `b0298ed3e9eb78c4324ab84026f5d1902db47aeaa71587f03c678fd854ec4e7c` |
| exp_range short core10 defaults | #105 | 9086e74 | `research_evidence/runs/breadth_candidate/2026-10-15_core10_7690426_batch1/exp_range_short_defaults.json` | `b9c3ceb8010fd98d5e0747027cdb624aad466badece5df0f8728d245abbd8f3d` |
| exp_range short core10 profit_capture | #107 | e9872eb | `research_evidence/runs/breadth_candidate/2026-10-15_core10_339ba68_batch3/exp_range_short_profit_capture.json` | `38eeb98fcadf581b40df6c42bedd297b2c1fe72815bafcd822650805dd2bb337` |
| exp_range short core20 defaults | #110 | c808794 | `research_evidence/runs/breadth_candidate/2026-10-15_core20_c8c7b92_batch6/exp_range_short_defaults.json` | `b706632d32efe74c2159067ff89931fe2bdb6571bfc67a4a978d07c9ffb83162` |
| exp_range short core20 profit_capture | #110 | c808794 | `research_evidence/runs/breadth_candidate/2026-10-15_core20_c8c7b92_batch6/exp_range_short_profit_capture.json` | `e27c396bc8bde382dee2a1ff0a9e447153d8981b59c7d3ac9b7e87abff39adb3` |
| exp_rip_sell_dca long core10 defaults | #106 | 4f12e27 | `research_evidence/runs/breadth_candidate/2026-10-15_core10_7690426_batch2/exp_rip_sell_dca_long_defaults.json` | `ecefac5f037c06c96fdf89625dcdb5061022cab7b4a7a1c41b728017a3169bd0` |
| exp_rip_sell_dca long core20 defaults | #110 | c808794 | `research_evidence/runs/breadth_candidate/2026-10-15_core20_c8c7b92_batch6/exp_rip_sell_dca_long_defaults.json` | `63645c81e5be7ae07d828abc3e9d87ca44e88bf4d266638f65e3c2f1cbc048cd` |
| exp_rip_sell_dca short core10 defaults | #106 | 4f12e27 | `research_evidence/runs/breadth_candidate/2026-10-15_core10_7690426_batch2/exp_rip_sell_dca_short_defaults.json` | `9096c5ac1e2745801db27245df7d578aebf0683cd7d50777ea43be2a7faf1048` |
| exp_rip_sell_dca short core20 defaults | #110 | c808794 | `research_evidence/runs/breadth_candidate/2026-10-15_core20_c8c7b92_batch6/exp_rip_sell_dca_short_defaults.json` | `69ef5b9fb5b393a4543b88e5f4c7c049812522df2ef095e711601b4b553781a0` |
| exp_scalp long core10 defaults | #109 | ee51985 | `research_evidence/runs/breadth_candidate/2026-10-15_core10_15m_339ba68_batch4/rerun_c8c7b92/exp_scalp_long_defaults.json` | `10bd58b374718da6fe18d28c30d2be49adc3a130b27fcd260b55988e1a835b00` |
| exp_scalp long core10 profit_capture | #109 | ee51985 | `research_evidence/runs/breadth_candidate/2026-10-15_core10_15m_339ba68_batch4/rerun_c8c7b92/exp_scalp_long_profit_capture.json` | `64f9736837465a035901597a352e469027658553cce0a95e8673f2548aa1421a` |
| exp_scalp long core20 defaults | #111 | 1a122b1 | `research_evidence/runs/breadth_candidate/2026-10-15_core20_15m_c8c7b92_batch7/exp_scalp_long_defaults.json` | `a812fbf7aa47005efbb271a0566b8d75b688e3d37f32d6bbdc917385736f6999` |
| exp_scalp long core20 profit_capture | #111 | 1a122b1 | `research_evidence/runs/breadth_candidate/2026-10-15_core20_15m_c8c7b92_batch7/exp_scalp_long_profit_capture.json` | `175222c1f13dfeadfade7c413a0489225ab0cfaeb6517233ca04c52b2698ee94` |
| exp_scalp short core10 defaults | #109 | ee51985 | `research_evidence/runs/breadth_candidate/2026-10-15_core10_15m_339ba68_batch4/rerun_c8c7b92/exp_scalp_short_defaults.json` | `53f9e7cef105df4732df64f7bc2af1cfb6ee6b92763b6dd82722495447acfc90` |
| exp_scalp short core10 profit_capture | #109 | ee51985 | `research_evidence/runs/breadth_candidate/2026-10-15_core10_15m_339ba68_batch4/rerun_c8c7b92/exp_scalp_short_profit_capture.json` | `48cbbeb501ad2ee75d6b12ab39d199df0f068f0cf1ee355f19855f7e6b6e0496` |
| exp_scalp short core20 defaults | #111 | 1a122b1 | `research_evidence/runs/breadth_candidate/2026-10-15_core20_15m_c8c7b92_batch7/exp_scalp_short_defaults.json` | `4f4e973b2c0daea6ee974249e97dce0cb3401c956e19f6a03198bf19378c276c` |
| exp_scalp short core20 profit_capture | #111 | 1a122b1 | `research_evidence/runs/breadth_candidate/2026-10-15_core20_15m_c8c7b92_batch7/exp_scalp_short_profit_capture.json` | `80cd36d3d39f8ae4c74822a538ddc93c5524af29cb3be13eb657a2c68aaa8d51` |
| exp_structure long core10 defaults | #109 | ee51985 | `research_evidence/runs/breadth_candidate/2026-10-15_core10_15m_339ba68_batch4/rerun_c8c7b92/exp_structure_long_defaults.json` | `e12c0bf9efc59b85a1735cf9f5e2dc4e2498d7810365b06264297065874cc64f` |
| exp_structure long core10 profit_capture | #109 | ee51985 | `research_evidence/runs/breadth_candidate/2026-10-15_core10_15m_339ba68_batch4/rerun_c8c7b92/exp_structure_long_profit_capture.json` | `ec312d8fcbcbdd8d5e5cdefcc9c6ae206b82753cd5893e31c57d1394e94b2f98` |
| exp_structure long core20 defaults | #111 | 1a122b1 | `research_evidence/runs/breadth_candidate/2026-10-15_core20_15m_c8c7b92_batch7/exp_structure_long_defaults.json` | `816ff23ce2e4ec35dc7cd1f0261a38684e6b59ae4012de97b14fad10c8d85664` |
| exp_structure long core20 profit_capture | #111 | 1a122b1 | `research_evidence/runs/breadth_candidate/2026-10-15_core20_15m_c8c7b92_batch7/exp_structure_long_profit_capture.json` | `fd6be4acb46a7ec65a2713e49b9728d135cfab3f388c4a48e1279162d287a8b2` |
| exp_structure short core10 defaults | #109 | ee51985 | `research_evidence/runs/breadth_candidate/2026-10-15_core10_15m_339ba68_batch4/rerun_c8c7b92/exp_structure_short_defaults.json` | `23b57add37978ea6480c1a2014be998cb35f817847484179b9437c427e800bb3` |
| exp_structure short core10 profit_capture | #109 | ee51985 | `research_evidence/runs/breadth_candidate/2026-10-15_core10_15m_339ba68_batch4/rerun_c8c7b92/exp_structure_short_profit_capture.json` | `719f309f1bdb473e4486eec35589bcfd0113f64285d923f93b6fc25af4584033` |
| exp_structure short core20 defaults | #111 | 1a122b1 | `research_evidence/runs/breadth_candidate/2026-10-15_core20_15m_c8c7b92_batch7/exp_structure_short_defaults.json` | `2447d405181fe4acc4f8465a1f02428afc16f39195e9aa79999f415443151ef0` |
| exp_structure short core20 profit_capture | #111 | 1a122b1 | `research_evidence/runs/breadth_candidate/2026-10-15_core20_15m_c8c7b92_batch7/exp_structure_short_profit_capture.json` | `1772273ad11771a450d14aae5346660948dfe1a6d50a7827a605fac74af44ad2` |
| exp_trend long core10 defaults | #106 | 4f12e27 | `research_evidence/runs/breadth_candidate/2026-10-15_core10_7690426_batch2/exp_trend_long_defaults.json` | `e27fbf44da487d3f3c846c6ca533387baa0a95fe24f02449cddef0b720ce7227` |
| exp_trend long core10 profit_capture | #107 | e9872eb | `research_evidence/runs/breadth_candidate/2026-10-15_core10_339ba68_batch3/exp_trend_long_profit_capture.json` | `ebc8162f49a64e29b339b69397e9e2f62d9379c843ade05c850ec7dbe8068d01` |
| exp_trend long core20 defaults | #110 | c808794 | `research_evidence/runs/breadth_candidate/2026-10-15_core20_c8c7b92_batch6/exp_trend_long_defaults.json` | `f6b9661953ed4fef93240419e6dbbcddbce2ab0420d9bd45d4526b2163090c48` |
| exp_trend long core20 profit_capture | #110 | c808794 | `research_evidence/runs/breadth_candidate/2026-10-15_core20_c8c7b92_batch6/exp_trend_long_profit_capture.json` | `e673216b381ed4d42840c027253a26ef2a8d0e011e53f4ebcda1d6a54410744e` |
| exp_trend short core10 defaults | #106 | 4f12e27 | `research_evidence/runs/breadth_candidate/2026-10-15_core10_7690426_batch2/exp_trend_short_defaults.json` | `f2d6a7bcbaccd77bcc1c05e2c03433342915389ac659fea9bd77e92703af9957` |
| exp_trend short core10 profit_capture | #107 | e9872eb | `research_evidence/runs/breadth_candidate/2026-10-15_core10_339ba68_batch3/exp_trend_short_profit_capture.json` | `2486affb4654774551623d43006730ba944550d229844e860a8f8cbfa8fdffbb` |
| exp_trend short core20 defaults | #110 | c808794 | `research_evidence/runs/breadth_candidate/2026-10-15_core20_c8c7b92_batch6/exp_trend_short_defaults.json` | `524d6c190a22de3ab531c53e1b07ed5aec475135a9b5fe0339b2a22156c439ae` |
| exp_trend short core20 profit_capture | #110 | c808794 | `research_evidence/runs/breadth_candidate/2026-10-15_core20_c8c7b92_batch6/exp_trend_short_profit_capture.json` | `d8843442e26596b2d1e6d3a85da16542b1979672ce3289a834feaed23436c2de` |
| exp_vwaprev long core10 defaults | #105 | 9086e74 | `research_evidence/runs/breadth_candidate/2026-10-15_core10_7690426_batch1/exp_vwaprev_long_defaults.json` | `77d26abc391d1d60cf4f548d7538e630bcb9750ffa9995ffa7835a10fd5eb9a3` |
| exp_vwaprev long core10 profit_capture | #107 | e9872eb | `research_evidence/runs/breadth_candidate/2026-10-15_core10_339ba68_batch3/exp_vwaprev_long_profit_capture.json` | `35391663c3bcef88ba3563e2219205fa2ec8e135c17eca00e0830dbe42436dbe` |
| exp_vwaprev long core20 defaults | #110 | c808794 | `research_evidence/runs/breadth_candidate/2026-10-15_core20_c8c7b92_batch6/exp_vwaprev_long_defaults.json` | `d13741da96a0e75f69f647ac24d4ffe285c7a0c880bf18f099cdb3fb887c1d60` |
| exp_vwaprev long core20 profit_capture | #110 | c808794 | `research_evidence/runs/breadth_candidate/2026-10-15_core20_c8c7b92_batch6/exp_vwaprev_long_profit_capture.json` | `ee782d36077379c3f7da57e96804725ff2459d25d06549a21139f69a7853b7e2` |
| exp_vwaprev short core10 defaults | #105 | 9086e74 | `research_evidence/runs/breadth_candidate/2026-10-15_core10_7690426_batch1/exp_vwaprev_short_defaults.json` | `0aa1bc280da2839cb22bbc26c0fbf2dbf2dd25d6fc3979ec51269284b17b3873` |
| exp_vwaprev short core10 profit_capture | #107 | e9872eb | `research_evidence/runs/breadth_candidate/2026-10-15_core10_339ba68_batch3/exp_vwaprev_short_profit_capture.json` | `4c1d730a507a467f2b3f0a56e6ca2c2ec6719a6ba0841bdb2b6b977524672582` |
| exp_vwaprev short core20 defaults | #110 | c808794 | `research_evidence/runs/breadth_candidate/2026-10-15_core20_c8c7b92_batch6/exp_vwaprev_short_defaults.json` | `ba28b1bc190a8ccb05443d930cba2074cabea5fc9e88802d973dafe3e3c8bd8a` |
| exp_vwaprev short core20 profit_capture | #110 | c808794 | `research_evidence/runs/breadth_candidate/2026-10-15_core20_c8c7b92_batch6/exp_vwaprev_short_profit_capture.json` | `e8f8b904437c3ccb3bf998c95f89a8e5a2b35fa02f7880a7079010934160af63` |
| hot_coin long core10 defaults | #106 | 4f12e27 | `research_evidence/runs/breadth_candidate/2026-10-15_core10_7690426_batch2/hot_coin_long_defaults.json` | `d4311778ef17725615952b001f46e49023e680adda3be6e8dc70ce1a028f1606` |
| hot_coin long core20 defaults | #110 | c808794 | `research_evidence/runs/breadth_candidate/2026-10-15_core20_c8c7b92_batch6/hot_coin_long_defaults.json` | `fefb2b919b1596c08bc4529b90c0ea20d4af55fc11e849ef45d922491bd12308` |
| hot_coin short core10 defaults | #106 | 4f12e27 | `research_evidence/runs/breadth_candidate/2026-10-15_core10_7690426_batch2/hot_coin_short_defaults.json` | `a916e10aa7aba08e1e1b0580d97ecc627379b438981871bcc133572e7c24b406` |
| hot_coin short core20 defaults | #110 | c808794 | `research_evidence/runs/breadth_candidate/2026-10-15_core20_c8c7b92_batch6/hot_coin_short_defaults.json` | `a621782bad406800821d30b805721934baa0d480486ca300454c9a1d0e5da4b4` |
| pullback_rsi long core10 defaults | #106 | 4f12e27 | `research_evidence/runs/breadth_candidate/2026-10-15_core10_7690426_batch2/pullback_rsi_long_defaults.json` | `8111da9ff76d96611f19628c5fe8aa2243f537c461cfc21087ce4a0ebcfffc57` |
| pullback_rsi long core20 defaults | #110 | c808794 | `research_evidence/runs/breadth_candidate/2026-10-15_core20_c8c7b92_batch6/pullback_rsi_long_defaults.json` | `80be4aa780f4774bfe856c92a4c4133f7d284c9299824c3f59f71fec9ae96cd7` |
| pullback_rsi short core10 defaults | #106 | 4f12e27 | `research_evidence/runs/breadth_candidate/2026-10-15_core10_7690426_batch2/pullback_rsi_short_defaults.json` | `96bd5a190f26d13195b57974efb87c37f723580b054debbdaae4f8bcec8808cf` |
| pullback_rsi short core20 defaults | #110 | c808794 | `research_evidence/runs/breadth_candidate/2026-10-15_core20_c8c7b92_batch6/pullback_rsi_short_defaults.json` | `004fb0d6eb777e623322e98ad3d196a2f90a6dde1e491509da0c021ceabd4367` |
| rotation long core10 defaults | #106 | 4f12e27 | `research_evidence/runs/breadth_candidate/2026-10-15_core10_7690426_batch2/rotation_long_defaults.json` | `c734d83d6a56e3f15d1a33781b42ef169fd26498a63435be37fc33cc1f861d5c` |
| rotation long core20 defaults | #110 | c808794 | `research_evidence/runs/breadth_candidate/2026-10-15_core20_c8c7b92_batch6/rotation_long_defaults.json` | `3330d888a435c43caedc46b8be01eaa447b646b57a47c1e7387b31881e6af204` |
| rotation short core10 defaults | #106 | 4f12e27 | `research_evidence/runs/breadth_candidate/2026-10-15_core10_7690426_batch2/rotation_short_defaults.json` | `31e284a0d8d9e077c455087d3e1f9caa5c5aeae04193a2fe526891e42b8a9edf` |
| rotation short core20 defaults | #110 | c808794 | `research_evidence/runs/breadth_candidate/2026-10-15_core20_c8c7b92_batch6/rotation_short_defaults.json` | `e594df68ca9a612b39d6574e5342fb12b2aca110a79d3d9f33ca552accb74c9a` |
| squeeze_tp long core10 defaults | #106 | 4f12e27 | `research_evidence/runs/breadth_candidate/2026-10-15_core10_7690426_batch2/squeeze_tp_long_defaults.json` | `db50d6963d62c8f469c10c096763ad0fe6618b589dcb6ae3115347d63376eb18` |
| squeeze_tp long core20 defaults | #110 | c808794 | `research_evidence/runs/breadth_candidate/2026-10-15_core20_c8c7b92_batch6/squeeze_tp_long_defaults.json` | `291b402c74b3d5e0e653ee9b68b42f06a9047561c72b44d7a620f64112e729c0` |
| squeeze_tp short core10 defaults | #106 | 4f12e27 | `research_evidence/runs/breadth_candidate/2026-10-15_core10_7690426_batch2/squeeze_tp_short_defaults.json` | `1fc9effc85644d64b5e3dcb1984ee0b3f7aa638d2023ceec0aceb8b64e91f063` |
| squeeze_tp short core20 defaults | #110 | c808794 | `research_evidence/runs/breadth_candidate/2026-10-15_core20_c8c7b92_batch6/squeeze_tp_short_defaults.json` | `951c536dc2a47eb2745c9200f9f58645cbb897cb381687341b658c44ef989aaa` |

No edge claim. Results are development-window backtests only; nothing here is promotable or holdout evidence.
