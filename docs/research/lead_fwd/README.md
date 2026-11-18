# LEADFWD-01: public-data forward books for six LONGSCOUT leads (research only)

Owner choice: moe, 15 Nov 2026 ("Do both": keep scouting and start forward books). Codex GO: PR #347 comment 6888567553.
Frozen in `research/lead_fwd/freeze.json` (sha256 in `freeze.sha256`) before the first forward bar.

## What it does
- **Six independent books** (seven since A1, see below), one per rule, each a hypothetical $8-risk, max-one-position, $3,333.33-cap book (rule v1 accounting):
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
No scheduler is installed by this code. The approved PC task is described under "Daily task (PC)" below.

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

## Amendment A1: seventh book s54_double_top_4h (before t_fwd, no row existed)
moe added `s54_double_top_4h` on 15 Nov at 04:06 UTC. Codex reproduced it in #347 6888751194, with 111/111 order-0 trades. The screen result was a WIDE165 pass: +0.153 R15 with a median of 112 closes, among about 201 consumed-calendar trials.
- s54 is frozen separately in `research/lead_fwd/freeze_a1_s54.json`, which is pinned by sha in `freeze.json`. It uses its own commits: rules 3fc8b5e5 and cases 347775d8.
- It has its own t_fwd of 2026-11-16 00:00 UTC. It starts flat, with no backfill.
- The runner refuses any addendum that changes an existing book or starts before the main t_fwd. The six original books are unchanged.

## Amendment A2: integrity guards (before t_fwd, no row existed)
Codex put the books on HOLD at 047349c4 (#351 comment 6902262210) over two pre-start gaps, and A2 closes both:
- **Entry facts.** A position that was open in an earlier row must reappear, still open or now closed, with the same entry facts: side, reference and fill price, quantity, risk, stop distance, initial stop and target. Closed records now carry the same facts, taken from the fill log. Marks, age and exits can still change.
- **Overlap.** Every base bar from the fetch start to the base's last bar must appear in the forward data and match it, and there must be at least one such bar. Otherwise the coin is OVERLAP_MISSING, which counts as missing prices: the row is INCOMPLETE and the coin is left out. If BTC is affected, the books stop. No price is invented.

`freeze.json` and `freeze_a1_s54.json` are byte-identical to their parents. A2 is a separate file, `freeze_amendment_a2.json`, with its own sha, and it records the parent shas, its UTC time and the new runner and test shas. The runner refuses A2 unless the parents match. t_fwd and all seven books are unchanged.

## Amendment A3: integrity guards, second pass (before t_fwd, no row existed)
Codex put A2 on HOLD (#351 comment 6902417655) over two remaining gaps, and A3 closes both:
- **Entry facts must really exist.** Each immutable entry fact must be present and valid on both the earlier and the later record. A missing value, null, NaN or a non-number never counts as "equal". Side must be +1 or -1. Prices, quantity, risk and stop distance must be finite and above zero. Stop and target must be finite: none of them can be null, because every frozen book has both.
- **A real mismatch wins.** Overlapping bars are compared first. Any bar that differs is a MISMATCH, which stops every book for that row, even if other overlap bars are missing.

A3 is a separate file, `freeze_amendment_a3.json`. It binds the shas of `freeze.json`, `freeze_a1_s54.json` and `freeze_amendment_a2.json`, all of which are byte-identical to before. The runner checks every link in that chain.

## Amendment A4: two new books, s76 and r74 (after the main t_fwd; their own later start)
moe approved adding the two leads that Codex reproduced in #347 6901683042 ("do the 4", 16 Nov 08:36 UTC):
- `s76_uptrend_line_break_4h` (short): rules b528105d, cases 8ab4ab22. Screen +0.035 R15, 223 closes, ex-top5 +0.018, every year positive. The edge is small.
- `r74_prior_month_break_4h` (both sides): rules 8d675af2, cases 3fbb578e. Screen +0.054 R15, 215 closes. It is **fragile**: order 0 without its best 3 trades is -0.005, and the shorts carry it while the longs are negative. It runs exactly as frozen, on both sides. A short-only variant would be a new hypothesis, and it is not run.

How A4 works:
- The two books are frozen in `freeze_a4_s76_r74.json` with **their own t_fwd of 2026-11-17 00:00 UTC**. They start flat, with no backfill.
- The main start has already passed, so `freeze.json` stays byte-identical. A4 is an integrity amendment, `freeze_amendment_a4.json`. It pins the new addendum by sha and binds the shas of freeze.json, A1, A2 and A3. It also pins the new runner code.
- The runner accepts a later addendum only if all of these hold:
  - its sha matches the pin;
  - it touches no existing book;
  - it starts after the main t_fwd, on a 00:00 UTC boundary;
  - it starts after the amendment's own time.
- In a row at boundary B, a book added later whose own t_fwd is at or after B is recorded as **NOT_STARTED**. It is not run or scored. The seven original books run exactly as before, including in dry runs. A book that was scored in an earlier row can never become NOT_STARTED or vanish; if that happens, the row is NONPARITY.
- If a book is scored for the first time after an earlier row whose boundary was already past its start (for example, because the PC moved to the A4 head only after review), the row adds a **note** saying so. Its bars since t_fwd are replayed. That replay is valid only because the addendum was frozen before t_fwd. The proof is the amendment time together with the git push time.
- The loader also checks that each `t_fwd_utc` matches its `t_fwd_ms`, and that the amendment's `at_utc` matches its `at_ms`.
- Rows also record the ids of the integrity amendments.
- The seven original books, the main t_fwd, coins, costs, rule v1 and the review rule are unchanged. A test checks this against the A3 view.
- The 16 Nov dry run at B = t_fwd stays a pipeline check. The first scored row for the seven books is B = 17 Nov 00:00 UTC. s76 and r74 are first scored at B = 18 Nov 00:00 UTC, and only once Codex has passed A4. Until then, the PC keeps running the A3 head, and these two books are simply absent. Each row re-runs from each book's own t_fwd, so adding them later loses no forward bar.

## Daily task (PC)
moe approved in this thread on 16 Nov 08:37 UTC: "approve daily LEADFWD task at 02:15 Cairo on my PC". The Windows task is `ZackBot-LEADFWD-Daily`, and it runs `C:\Dev\ZackBot_research_cache\leadfwd01_daily\run_daily.ps1` daily at 02:15 Cairo.
- It fetches the latest settled 00:00 UTC boundary and then runs `run_fwd.py` on the worktree's checked-out head.
- It never rewrites a row and never commits or pushes. Rows are reviewed and pushed separately.
- It runs as the logged-on user with StartWhenAvailable, and touches no account or runtime.

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
