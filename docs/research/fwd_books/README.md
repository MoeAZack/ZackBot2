# FWD-BOOKS: prospective H5 and EMA-long books (research only)

Contract: Codex FORWARD EVIDENCE GO, PR #337 6836143114; owner GO #219 6836306574. Frozen at 8f83494b (manifest `research/fwd_books/freeze.json`, sha256 389c466e...).

- Cutoff **T = 2026-11-12 22:00 UTC = 2026-11-13 00:00 Cairo** (first unopened 1h candle after the freeze at 21:36:44 UTC; pushed 21:36:46 UTC). EMA scores 4h bars from 2026-11-13 00:00 UTC.
- Two separate hypothetical $4,000 books, start flat at T: H5 long 1h max_pos 1, EMA (ema_st) long 4h max_pos 2. Definitions are admission-controls @29d76073, unchanged. Fixed $8 risk / $3,333.33 cap. Alphabetical priority is primary; cohort order is a diagnostic only.
- Inputs (read only): FWD-01 runner data `F:/ZackBot2_data/FWD-01/data` (1h from 2026-10-30 16:00 UTC; 4h klines) plus frozen canonical history (c11ed256, rules d1ef2a9c) for 4h warmup. All 29 overlapping 4h bars (2026-09-23 to 09-27) match exactly, and the merged 4h series has no gaps from 2024-10-11 20:00. FWD-01 1h rolled to 4h matches its native 4h prices exactly.
- Pre-T coverage check (`coverage_1794513600000.json`, inputs through 2026-11-12 20:00 UTC): 1h COMPLETE, 4h COMPLETE, no gaps; warmup 316 1h bars / 4,572 4h bars (220 needed).
- Rows: `rows/<boundary_ms>/row.json` + receipts, immutable, one per evaluated boundary, each a pure re-run from W0 over closed bars before the boundary. INCOMPLETE and NONPARITY are reported as such.
- Reporting gap (stated, not worked around): no scheduler was added. A row exists only when someone runs `run_books.py` on the FWD-01 files (they refresh every 4h). Rows are deterministic, so a late run gives the same row.
- Mutation check: 7 of 8 key-line mutants killed. The survivor (coverage counting a not-yet-closed bar) is equivalent here, because the evaluator already trims 4h inputs to closed bars.
- Hypothetical bar replay at 7/15/23 bp and 1 bp/8h funding. Not exchange fills, stop evidence or live parity. No accounts, keys, workers, schedulers or activation.
