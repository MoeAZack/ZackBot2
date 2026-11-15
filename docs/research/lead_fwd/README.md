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
- A missing price for any coin makes the row **INCOMPLETE** and names the coin. The coin is left out of that row's traded universe, and no price is ever invented.
  If leaving it out changes an earlier COMPLETE row's past, the row is **NONPARITY**. A missing BTC price stops the books.
- A boundary is fetched only 10 minutes after it passes, so the last kline is final.

## Rows
`research/lead_fwd/rows/<B_ms>/row.json` + `summary.md` + `receipts.json`. Rows are written once and never rewritten.
Each row is a pure re-run from `t_fwd`. The past of the latest earlier COMPLETE row must be reproduced exactly: the same closed trades and
signals before its boundary, with nothing missing, changed or added, and each of its open positions still open or closed with the same entry.
Otherwise the row is **NONPARITY**. A real row also requires the runner code to match the shas pinned in the freeze. Each row lists closed trades, open positions (entry, stop, target,
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
Before the start, one **dry run** at B = 2026-11-15 00:00 UTC (1794700800000) checks the base caches, the overlap with the fresh fetch and the
runtime. It is written under `rows/dryrun/` and is not evidence.
No scheduler is installed by this code. A daily scheduled task on the PC needs moe's approval written in the project thread.

## Amendment A0 (before t_fwd, no row existed)
An adversarial review found these problems in the first freeze (eb093eeb), and A0 fixes them:
- a cut coin crashed every worker, because the shared `cut_ctx` assumes that every coin runs to the end;
- parity checked only that earlier items were still present;
- the runner code was unpinned;
- a coin with no forward data kept bars at or after B;
- the stop and target of open positions used the price before slippage;
- `--orders` was not treated as a test switch;
- `_work/` was not cleaned;
- the fetch could run before the last kline was final.

Leads, coins, t_fwd, costs and rule v1 are unchanged.

## Tests (cloud, non-official)
`python -m pytest -q research/lead_fwd/test_lead_fwd.py` covers:
- unit tests for merge, coverage, closed-bar fetch with settle time, two-way parity, flags, open positions, and the freeze and code pins;
- an integration test that runs all six leads on sample data (8 public coins renamed onto universe symbols) at two boundaries. It checks exact
  replay and that a row is never rewritten.
- a cut-coin integration test: the row is INCOMPLETE, the books still run, and the result is NONPARITY exactly when the dropped coin had traded.

The suite was also run with an external CP1252 locale shim (a `sitecustomize` that forces cp1252 as the default encoding). It is not part of the test
file. The two controls (s36 and s40) produce no signals on the sample data, so the replay test does not exercise their signal paths.
The mutation check on the key lines killed every mutant (see the PR).

No account, key, order, PAPER, runtime, scheduler or installer action. Account 1 and the account-4 R6 canary are not touched.
