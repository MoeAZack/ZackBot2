# Dev-window parity: Static-40 at the #115 head vs #114 (REVIEW ONLY)

**Verdict: PARITY. All 13 slices are identical trade by trade and in every stat. The only fields that differ are `commit` and `packet_sha256`.** The comparison ignores exactly three fields: `commit`, `runner_sha256` and `packet_sha256`. Of those, `runner_sha256` turned out identical anyway.

Requested in Codex issue-13 comment 6204084063. This evidence supports re-freezing the `engine.py` pin to the accepted master 9206f17, which #117 (the breadth_rotation per-key mgmt overlay) changed on purpose.

**What was compared:**
- **Base:** the 13 #114 packets in the parent folder, run at c8c7b92.
- **New:** the same 13 slices re-run at the #115 head `9f1f16a32518b76b09ec6275d325e559abb51ea1`, which is eb93fb1 (#115 merged with master 9206f17) plus the `engine.py` pin re-freeze.

**Unchanged from #114:**
- **Runner:** `tools/research/run_breadth_candidate.py`, byte-identical to c8c7b92 (sha256 `dad619aa…`).
- **Inputs:**
  - the same caches, with the same per-file sha256 (the comparison checks all 40 `cache.files` digests);
  - the same window, 2024-10-14T00:00:00Z → 2026-09-28T00:00:00Z;
  - the same 220-bar warm-up and the same `--symbols` (the Static-40 committed list, in rank order).
- **Settings:** the same costs and account; `--mgmt defaults` throughout; no tuning.
- **Holdout:** stays closed. Nothing was read on or after 2026-09-28.
- **Cache handling:** the 14 primary-cache files were snapshot-copied to a scratch directory outside the repo, and their bytes were verified against the #114 digests. The research cache was read in place.

**Method:** `compare_parity.py` does an exact recursive comparison of every packet field. It covers each trade's symbol, side, t_in, t_out, prices, pnl, R, fees, funding, mae/mfe and why, plus `stats`, `bust`, `universe` and `cache`. Every difference would be listed in `parity.json`; there are none.

No edge claim. This is a development-window backtest only, not holdout evidence.
