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
4. **Rows carry timestamps.** Each row has `first_unaccountable_ms` and `last_valid_ms`. The row-level `censor`
   records the class, phase, cycle, wallet, MTM, gross exposure and unrealised P&L at the refusal, with
   `wallet + unrealised == mtm`.
5. **Explicit delist notice** (`run_book(delist={symbol: notice_ms})`):
   - A held lot exits at the first bar open **strictly after** the notice (`exit.delist`; shorts use
     `delist_cover`), and the row stays COMPLETE if that open exists.
   - No entry is taken once a notice is known.
   - No notice source is committed yet, so `real_env` supplies none. Wiring a timestamped observation source is a
     later data task.
6. **Flat symbols.** An absence or funding gap is irrelevant, and the row stays COMPLETE, only when no decision,
   position or later accounting depends on it.

## Status contract (fail closed)

- Every new result carries `status` (`COMPLETE` | `INCOMPLETE`) and the policy id: run_book, account, account_unit,
  b1_short_book, detail, metrics/aggregate, summary.
- A missing status, a COMPLETE that still carries incomplete rows / excluded symbols / a censor, or secondary rows
  without a primary row are all refused (`pit_run.require_complete`).
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
