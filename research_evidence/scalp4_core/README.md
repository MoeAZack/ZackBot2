# SCALP-4 CORE-10 / CORE-20 dev-window packet (research only, no edge claim)

- Code: PR #125 head `a53a649cc228d74a943f05ab462be6fcfb278473`. Runner sha256 `dad619aaf2df00fd79ce9a2ca2d4848f298b745920fe7f6c56898e7b4f6922f2`, strategies.py blob `f086864`.
- Window 2024-10-14T00:00:00Z → 2026-09-28T00:00:00Z, end-exclusive; the holdout is sealed. Frozen costs: taker fee 0.0005, slippage 2 bps per fill, funding 3.125e-6 per 15m candle.
- 15m cache digests (sha256 over sorted "SYMBOL sha256" lines): CORE-10 `ac297be1…4e84`, CORE-20 `04f88127…1c74`. BTC 4h regime file `f97d65b5…5cd9`. Full values are in summary.json.
- `run_all.sh` contains the exact commands (set REPO and OUT). `analyze.py` turns the 32 packets into `summary.json`, including per-quarter and per-regime figures and the gross / slippage / fee / funding decomposition.
- Slippage R is derived as 0.4 × fee R, because the packets carry no fill prices.
- Momentum_burst was re-run on the C1 fix head `bb980f3` (fixed-R view, both tiers and both sides). Each packet is identical to the a53a649 packet except for its commit field. The real caches have no zero-volume impulse bars.
