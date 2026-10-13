# Missing-data policy v1 (`zb-missing-data/1`) — R4b research runner

Status: **proposal, implemented by M1** (Codex M1 release, #13 comment 6153717648). This file versions the research
accounting rule. It does not change any frozen strategy, prereg, ledger history or holdout. Any later change gets a
new file (`..._v2.md`) and a new `POLICY` id in `tools/research/pit_run.py`.

## What it replaces

Before M1, `pit_run.account` re-ran a book without every symbol that later produced an INCOMPLETE lot, and repeated
this until no INCOMPLETE was left. A gap *after* a decision could therefore rewrite the historical universe, earlier
trades, cash and capacity. That uses future knowledge, so it is removed. `account_unit` (B1 long) likewise dropped
every trade of an affected symbol; that is removed too.

## The rule

1. **One causal pass per book row.** No re-runs, and no symbol is excluded.
2. **First unaccountable event wins.** The event's cycle is rolled back and the row **halts** there. Everything
   through the last fully accounted cycle stays exactly as the same tape without the event would give it:
   decisions, fills, trades, wallet, exposure, capacity and the MTM series.
   - No later cycle is played, and no other symbol is sized from a wallet or capacity that can no longer be known.
   - Nothing is forward-filled, flat-lined or forced to a mark.
3. **Primary reasons.** Each one is a distinct class:

   | reason | class | when |
   |---|---|---|
   | `incomplete.gap` | missing_bar | a held symbol has no candle (phase `bar`); a valid entry has no next-open candle (phase `next_open`, Codex ruling 4: not a refusal, no fill); a fill lacks slip history (phase `exit`/`fill`) |
   | `incomplete.delisted` | delisting | as `incomplete.gap`, but the symbol has no later bar in the tape (end of series) and no delist notice. The label is chosen with hindsight, but the accounting is identical to a missing candle. |
   | `incomplete.funding` | funding_rate_gap | the funding-rate sequence of a held lot cannot be proved complete through the played bar. This is proved per bar, from the rows known by then. |
   | `incomplete.funding_mark` | funding_mark_gap | a funding event of a held lot has no mark |
   | `incomplete.split_end` | split_censoring | a lot is still open at the split end with no observable next open. There is nothing after it to halt. |

   Other lots that were open when the row halted get the **secondary** label `incomplete.book_halt`, with
   `primary_ref = <symbol>@<first_unaccountable_ms>`. They are never given the primary cause.
   - **Exactly one primary per row.** Same-cycle faults resolve deterministically: in the shared book, by the frozen
     symbol order; in the unit book, by (cycle, in-cycle phase bar < exit < next_open/fill < funding < decision,
     symbol). All other open lots are `incomplete.book_halt`.
   - **Decisions at the halt cycle are not booked.** A decision taken at `last_valid_ms` whose fill falls at the
     unaccountable instant (another symbol's signal exit, a B1 rebalance at tau == `last_valid_ms`) is rolled back
     with that cycle. Its lot is reported as halted, not as a booked trade. "Identical through `last_valid_ms`"
     covers the fills, trades, wallet and series up to and including that cycle.
4. **Rows carry timestamps.** Each row has `first_unaccountable_ms` and `last_valid_ms`. The row-level `censor`
   records the class, phase, cycle, wallet, MTM, gross exposure and unrealised P&L at the refusal, with
   `wallet + unrealised == mtm`.
5. **Explicit delist notice** (`run_book(delist={symbol: notice_ms})`):
   - A held lot exits at the first bar open **strictly after** the notice (`exit.delist`; shorts use
     `delist_cover`), and the row stays COMPLETE if that open exists.
   - No entry is taken once a notice is known.
   - No notice source is committed yet, so `real_env` supplies none. Wiring a timestamped observation source is a
     later data task.
6. **Exact scheduled inputs (Codex 6154894668).** Wherever an input is causally needed, the exact scheduled window
   is required. An older row is never substituted, and a membership, weight or schedule never shrinks or shifts
   silently.
   - **Slippage:** the fill at open `bo` uses exactly the bars closing at `bo - (n-1)*4h .. bo` (TR-SMA14: 15;
     Wilder: the read window). A hole inside that window is `incomplete.gap` (phase `fill` / `exit`). Too little
     history from the series start (warm-up) stays the causal `entry_no_slip_history` refusal.
   - **Scheduled decisions** (B1 long Mondays, B1 short rebalances at tau): a listed symbol needs its decision
     candle at tau and a hole-free `T.WINDOW`-bar input read behind it. Otherwise the row halts at tau (phase
     `decision`). A symbol not listed yet (no bar before tau) is warm-up, not missing.
   - **B1 short execution:** the execution open is required only for a non-zero trade (`abs(target - current)`). A
     flat member whose target floors to 0 needs none.
7. **Flat symbols.** An absence or funding gap is irrelevant, and the row stays COMPLETE, only when no decision,
   sizing, fill, position or later accounting depends on it.
   - Signal-triggered configurations (the 13 signal configs and B2) read their decision inputs from the attested
     tape. After a hole, the tape layer recomputes signals on the contiguous tail only. A missing bar for a flat
     symbol therefore removes any entry it would have carried, and the row stays COMPLETE.
   - That is correct only while the tape layer guarantees a decision-bearing bar is never absent without a record.
   - Holes before the tape start are invisible to the runner. Both are tape-layer guarantees, outside this runner.

## Status contract (fail closed)

- Every new result carries `status` (`COMPLETE` | `INCOMPLETE`) and the policy id: run_book, account, account_unit,
  b1_short_book, detail, metrics/aggregate, summary.
- One predicate, `tools/research/missing_data.py` (`check` / `check_tree`), is shared by the runner
  (`pit_run.require_complete`), the report gate (`report.refuse_incomplete`) and readiness. It refuses:
  - a missing status, or INCOMPLETE;
  - a COMPLETE that still carries incomplete rows, excluded symbols, any censor, `incomplete_rows` or an INCOMPLETE
    verdict;
  - secondary rows without a primary row.
- Tier and detail artifacts carry `status` and `missing_data_policy`, derived from their rows.
- A report results payload must declare a top-level `status` COMPLETE and `missing_data_policy`. Every nested dict
  that carries a status must pass too.
- Readiness opens every named evidence artifact, checks its sealed sha256 and requires it to pass the predicate.
  - M3 repro reports declare no missing-data status, so readiness refuses them (fail closed) until Codex rules how
    they declare one.
- An INCOMPLETE row gives `None` book figures (net % equity, MTM drawdown, underwater, month R). Its closed trades
  stay visible for audit only.
- A tier is INCOMPLETE if any primary, neighbour or baseline row is. Its verdict is `INCOMPLETE`, and its computed
  EDGE rows become `INCOMPLETE`.
  - The uncapped development-only variant is split-end censored by design (prereg), can never be evidence, and is
    reported separately.
- An INCOMPLETE tier is sealed for audit with **no evidence**:
  - `run_r4 pit` exits 4.
  - `r4_readiness` refuses an evidence-less final.
  - `report.make_report` refuses any non-COMPLETE payload before anything is sealed, so no holdout_reveal ledger
    record can come from it.
  - The PIT path appends only `data_access` records.
- On complete data, outputs are identical to master `4f7e3b4` apart from the new `status` / policy fields. The proof
  is `tests/test_m1_causal_gaps.py::test_m1_complete_data_digest_equals_master_4f7e3b4`.
