# Breadth candidates on Static-40, defaults only (REVIEW ONLY, no edge claim)

Runner `tools/research/run_breadth_candidate.py` @ c8c7b92, unchanged. Universe: the committed `symbols[]` of `research_evidence/universe/static-top40-minhist365-contract-20261011.json` (40, rank order), passed with `--symbols`; freeze in #113. Window 2024-10-14T00:00:00Z -> 2026-09-28T00:00:00Z (end exclusive, sealed cutoff), 220-bar warm-up. Costs: fee 0.0005, slip 0.0002, funding 0.00005 per 4h bar. No tuning, no profit_capture, no 15m, no holdout read.

INCOMPLETE (excluded, never filled): AKEUSDT, BRUSDT, HYPEUSDT, MUBARAKUSDT, PENGUUSDT, PUMPUSDT, TRUMPUSDT (7 of 40). Book: 33 symbols.

rotation ranks within the run universe (here the 33-symbol book), so its Static-40 row is not a superset of the CORE-10/20 rows.

`registered_sides` = strategies.STRATEGIES[key]['sides'] (formal eligibility). `paper_experimental_sides` = engine.py BREADTH_BOTH (L350 @ 87f8465) -> 'both' in the PAPER rotation. The 3 shorts are PAPER experiments: provisional, unregistered, need a new append-only prereg (no backdating) per Codex 6199976344. Stats are pre-bust when a slice busts (equity <= 10% of start); otherwise full-run.

CORE-10 / CORE-20 cells are copied from #112 `research_evidence/runs/breadth_shortlist/2026-10-15_pr105-111/shortlist.json` @ a867d3d (packet_sha256 `3d150f7c5e6aeca2ac2b3a1bb434a578f997986ba7c246b103215fe9f7291ec7`).

| key | side | registered_sides | paper_experimental_sides | label | Static-40 trades | PF | net R | net $ | maxDD R | maxDD eq | basis | CORE-10 (#112) tr/PF/R/DD | CORE-20 (#112) tr/PF/R/DD |
|---|---|---|---|---|---|---|---|---|---|---|---|---|---|
| breakout_pyramid | long | long | both | registered | 456 | 2.17 | +291.89 | +2614.59 | 33.02 | -0.357 | full (no bust) | 283 / 1.72 / +151.9 / 20.2 | 365 / 2.75 / +324.2 / 27.7 |
| donchian_ens | long | long | both | registered | 367 | 2.03 | +153.53 | +956.97 | 25.61 | -0.268 | full (no bust) | 215 / 1.56 / +54.7 / 21.2 | 291 / 2.27 / +149.2 / 21.8 |
| ema_mom | long | long | both | registered | 239 | 1.73 | +73.42 | +400.58 | 13.52 | -0.234 | full (no bust) | 97 / 2.73 / +59.9 / 5.0 | 160 / 2.77 / +101.1 / 7.6 |
| ema_st | long | long | both | registered | 246 | 1.35 | +65.76 | +285.14 | 34.48 | -0.395 | full (no bust) | 132 / 2.23 / +92.0 / 12.3 | 181 / 1.65 / +80.5 / 24.3 |
| exp_breakout | long | both | both | registered | 702 | 1.10 | +57.72 | +239.67 | 47.62 | -0.422 | full (no bust) | 489 / 1.29 / +86.7 / 36.6 | 586 / 1.20 / +81.9 / 42.4 |
| exp_trend | long | both | both | registered | 262 | 1.41 | +40.87 | +223.13 | 13.97 | -0.164 | full (no bust) | 101 / 1.80 / +27.4 / 4.9 | 171 / 2.00 / +56.7 / 7.6 |
| hot_coin | long | long | long | registered | 479 | 1.50 | +92.50 | +672.12 | 11.96 | -0.122 | full (no bust) | 313 / 1.57 / +74.7 / 15.0 | 394 / 1.48 / +83.9 / 19.2 |
| pullback_rsi | long | long | both | registered | 345 | 1.10 | +19.78 | +84.03 | 24.67 | -0.231 | full (no bust) | 201 / 1.14 / +15.8 / 15.1 | 268 / 1.19 / +25.8 / 15.9 |
| rotation | long | long | long | registered | 940 | 1.34 | +85.84 | +474.49 | 38.19 | -0.356 | full (no bust) | 785 / 1.50 / +115.2 / 26.5 | 858 / 1.35 / +82.6 / 34.5 |
| squeeze_tp | long | long | both | registered | 361 | 1.26 | +51.56 | +275.35 | 17.28 | -0.174 | full (no bust) | 205 / 1.18 / +24.1 / 18.4 | 291 / 1.21 / +37.3 / 22.7 |
| breakout_pyramid | short | long | both | PAPER experiment (provisional; unregistered, needs new prereg) | 459 | 0.96 | +25.92 | -63.79 | 62.76 | -0.511 | full (no bust) | 270 / 1.30 / +86.8 / 33.2 | 379 / 1.08 / +63.4 / 40.0 |
| donchian_ens | short | long | both | PAPER experiment (provisional; unregistered, needs new prereg) | 287 | 1.06 | +12.71 | +38.07 | 18.97 | -0.217 | full (no bust) | 178 / 1.44 / +32.7 / 13.7 | 233 / 1.26 / +28.0 / 10.9 |
| squeeze_tp | short | long | both | PAPER experiment (provisional; unregistered, needs new prereg) | 392 | 1.11 | +31.05 | +134.49 | 37.26 | -0.332 | full (no bust) | 226 / 1.27 / +34.4 / 21.6 | 327 / 1.19 / +39.5 / 25.1 |

No edge claim. Development-window backtest only; nothing here is promotable or holdout evidence.
