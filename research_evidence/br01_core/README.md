# BR-01 CORE-10 / CORE-20 dev-window packet (research only, no edge claim)

- Code: PR #126 head `c27e9cc348a692e9a9be766beb0dbd257505bf29`. Window 2024-10-14T00:00:00Z → 2026-09-28T00:00:00Z, end-exclusive; the holdout is sealed. Fixed-R view (`--start 1e9 --risk 1e-6`), defaults mgmt, frozen cost model.
- `br01_bt.py` is a thin wrapper that reproduces `tools/research/run_breadth_candidate.py` exactly and only adds `mgmt['br01']` to the slot. Without br01 it reproduces the frozen runner bit-for-bit on CORE-10 range_scalp long: file sha256 `d608efb8…`, 3471 trades, −1020.435726 R.
  - The wrapper keeps the BR-01 trade columns out of `enrich()`, because their `fees` name clashes with the runner's.
  - It records only the pnl line in the fee probe. Without that, the frozen FeeProbe would count exit fees twice for staged positions, because #126's `close()` has a second `q*px*FEE` product.
- Grid: 13 variant × base pairs × 2 sides × 2 tiers = 52 cells, plus 16 single-entry controls. `summary.json` holds per-cell metrics, 2× fee stress, the per-trade gross / slippage / fee / funding decomposition, leg-fill distribution, refusals, deadline exits, holds, quarters, BTC regimes and digests.
- **Result:** 0 of 52 cells has net R > 0 or PF > 1 at either 1× or 2× fees. Every variant loses in all 8 quarters and both regimes.
