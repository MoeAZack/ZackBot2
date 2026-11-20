# EBI step A: aggTrades data feasibility — DATA_INADEQUATE

Contract: Codex staged GO, PR #337 comment 6831737001 / #219 6831735253, section A.
Source frozen before the run at b370696f: `research/ebi/feasibility.py` (sha256 a4e9536d…), 9 synthetic tests (mutation-checked, CP1252 shim).
Inputs: Codex manifest 1416d4eb…, BTC zip 8ee24321…, ETH zip 3c3440d0… (official checksums verified by Codex). Run on the owner's PC VM.

| coin | usable 30s bins | fraction | raw trade-ID gap pairs | agg-ID gaps | ts reversals | silences >5s |
|---|---|---|---|---|---|---|
| BTC | 72 / 2880 | 2.5% | 14,723 | 0 | 0 | 0 |
| ETH | 27 / 2880 | 0.9% | 18,880 | 0 | 0 | 3 |

Nearly all lost clock time is caused by raw trade-ID gaps (BTC 84,240 s, ETH 85,590 s of bins touched); they occur about every 5–6 s,
so almost every 30 s span touches one. Gate needs >=80% on each coin: **DATA_INADEQUATE**. Per the contract this stops EBI:
no threshold change, no larger download, no gap attribution, no economic verdict. Not an economic rejection.

Independent cross-check vs Codex feasibility result.json (b8b1227a): rows, raw-gap events (14,723 / 18,880), zero agg gaps,
zero time reversals and max event gaps (4,878 / 5,296 ms) all match. No prices, notionals or PnL were read.
