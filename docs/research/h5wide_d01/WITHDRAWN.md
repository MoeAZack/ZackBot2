# WITHDRAWN: D01 outputs from runner v1 (run_d01.py at c1de983a)

`d01_result.json` and the `*.order0.trades.csv` files in this folder came from runner v1. That runner has the defects
listed in Codex review #219 comment 6826926628: a hard-coded Cairo offset, a loss-only daily halt, no slot reserved for
an open terminal lot, gap and 48-hour handling, all-in costs charged as fees, no marked DD, and no explicit exclusions.
They are kept unchanged for the record. They are not accepted results, and `docs/research/h5wide_d01_result.md` is
withdrawn with them. The bug-fix runner is `research/h5wide_d01/run_d01_v2.py`. Its outcomes are produced only after
Codex has checked the frozen v2 runner and its hostile tests. Nothing is tuned between v1 and v2.
