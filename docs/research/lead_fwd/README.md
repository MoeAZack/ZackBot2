# LEADFWD-01: public-data forward books for six LONGSCOUT leads (research only)

Owner choice: moe, 15 Nov 2026 ("Do both": keep scouting and start forward books). Codex GO: PR #347 comment 6888567553.
Frozen in `research/lead_fwd/freeze.json` (sha256 in `freeze.sha256`) before the first forward bar.

## What it does
- **Six independent books**, one per rule, each a hypothetical $8-risk, max-one-position, $3,333.33-cap book (rule v1 accounting):
  - leads (thin WIDE165 passes): `l24_breakaway_day_4h`, `s26_weekly_close_breakdown_4h`, `l40_dual_momentum_4h`, `s51_momentum_accel_4h`;
  - count-failed exploratory controls: `s36_max_effect_4h`, `s40_relative_weakness_4h`.
- **Start:** `t_fwd = 2026-11-16 00:00 UTC` (02:00 Cairo). Every book starts flat there. Bars before it are indicator history only.
- **Code path:** the unchanged `longscout_run.worker -> sieve_v1_panel.worker` with each lead's own frozen source commit (git archive).
  The one change: `backtest.run` gets `t0 = t_fwd`. Rules, params, sizing, costs, liquidity rule and coin order are unchanged.
- **Clock (unchanged):** daily conditions use completed UTC days. The signal sits on the 00:00 UTC 4h candle, and the modeled entry fills at the
  next bar open (04:00 UTC = 06:00 Cairo). Rows record the signal's decision bar and the trade's fill bar.
- **Universe:** the same 165 coins as the scouting runs (fresh134 + extra31). BTC and the original nine are never traded.
- **Costs:** R at 15 bp (primary), 7 bp and 23 bp, plus the backtest's adverse funding proxy. No funding prices are used, and the
  STOPPED funding audit is not resumed.

## Data
- Base history: the two pinned caches on the owner PC (fresh manifest `fb0c0572…`, extra31 manifest `99af9bfb…`), ending 2026-10-04 04:00 UTC.
- Forward data: `fetch_fwd.py` on the owner PC (the cloud gets 403). It fetches public 1h klines from 2026-10-01 to the boundary B
  (latest 00:00 UTC) and keeps only closed bars. The overlap with the base must match exactly.
- A missing price for any coin makes the row **INCOMPLETE**, and the coin is named. BTC missing stops the books.

## Rows
`research/lead_fwd/rows/<B_ms>/row.json` + `summary.md` + `receipts.json`. Rows are written once and never rewritten.
Each row is a pure re-run from `t_fwd`. The latest earlier COMPLETE row must be reproduced exactly (all earlier closed trades and
signals present and identical), otherwise the row is **NONPARITY**. Each row lists closed trades, open positions (entry, stop, target,
mark), signals since `t_fwd`, the 40-order spread and the worst-intrabar view. The pooled six-book line is a labelled diagnostic only.
Rows with B ≤ t_fwd go to `rows/dryrun/` (pipeline checks, not evidence).

## Review rule (frozen)
- Decision at **30 forward closes** per book (order 0, 15 bp): mean R15 > 0 and mean R23 ≥ 0 gives FORWARD_CONSISTENT; anything else gives FORWARD_KILLED.
  FORWARD_CONSISTENT is still research. PAPER needs Codex and moe.
- At 10 or more closes, mean R15 ≤ −0.30 raises an EARLY_WARNING (report only).
- **Speed, plainly:** the screen produced about 0.3 to 1.5 closes per week per book, or about 4 a week across all six. Reaching 30 closes takes
  about 5 months for l24 and over a year for s36.

## Run (owner PC, from the repo root on this branch)
```
python research/lead_fwd/fetch_fwd.py C:/Dev/ZackBot_research_cache/leadfwd01_fetch
python research/lead_fwd/run_fwd.py C:/Dev/ZackBot_research_cache/leadfwd01_fetch research/lead_fwd/rows --boundary-ms <B printed by the fetch>
```
No scheduler is installed by this code. A daily scheduled task on the PC needs moe's approval written in the project thread.

## Tests (cloud, non-official)
`python -m pytest -q research/lead_fwd/test_lead_fwd.py`: unit tests for merge, coverage, closed-bar fetch, parity, flags and open positions,
plus one integration test. That test runs all six leads on sample data at two boundaries and checks that the later row reproduces the earlier
one exactly (prefix invariance) and that a row is never rewritten. It also runs under a CP1252 shim. A mutation check covered the key lines.

No account, key, order, PAPER, runtime, scheduler or installer action. Account 1 and the account-4 R6 canary are not touched.
