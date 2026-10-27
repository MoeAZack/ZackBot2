# EDGE-04 B0 amendment v1

Status: **DRAFT - awaiting Integration (Codex) signature**. Drafted 27 October 2026, 16:47 Cairo, by Build (Claude
Code) under ticket EDGE04-B0-01. It does not take effect, and no Stage-A rerun may start, until Codex signs it. The
machine-readable authority is `EDGE04_B0_AMENDMENT_v1.json`. It supplements the orchestration addendum and does not
rewrite the preregistration.

## Defect

The B0 generator drew one whole book per index and marked the index invalid if the shared wallet refused any episode.
It then required all 10,000 indices to be valid. On complete WF3 primaries only H1 38, H2 0, H3 2,407 and H4 1,269 of
10,000 base books were valid. That is a gap in baseline generation, not strategy evidence.

## Frozen null rule (`edge04-b0-capacity-rejection/1`)

- **The draw is unchanged.** Each complete primary episode supplies one template: symbol, side, holding bars and risk
  fraction. Entries are uniform and without replacement inside a book. The book is routed through the same shared
  wallet, capacity gates and costs as before.
- **Acceptance.** A draw is valid only if the shared book admits every template and every admitted episode is COMPLETE.
- **Resampling.** Only the classified refusal reasons listed under *Refusal groups* are resampled. Draw `d` tries attempts `a = 0, 1, ...` and keeps the first valid book.
  - Attempt 0 uses the frozen addendum seed `sha256(candidate_id|hypothesis|split|book_index)`.
  - Attempt `a >= 1` uses `sha256(candidate_id|hypothesis|split|book_index|attempt=a)`.
  - In both cases the seed is the first 64 bits of the hash.
- **Fail closed, never resampled.** Data or episode incompleteness stops the stress at once and makes it INCOMPLETE,
  with `failed = {draw, attempt, reason}`. This covers `PR.Incomplete` (`data.<reason>`), orchestration failures
  (`data.orchestration`), cost failures (`data.cost`) and an admitted episode that is not COMPLETE
  (`episode.incomplete`). The same applies to any refusal reason outside the two groups, including
  `capacity.unknown`, which is recorded as `unclassified.refusal: <reasons>`. Unclassified reasons fail closed;
  they are never resampled.
- **Per stress.** Each stress resamples on its own, using the same deterministic proposal seed sequence. Stress rows
  are therefore conditionally sampled and **not paired outcome-by-outcome**. Every output carries this disclosure
  in `pairing`.
- **Budget.** Each draw gets at most 10,000 attempts. The budget is deterministic and there is no wall-clock cap. The first draw that runs out stops its stress. That stress, and
  the B0 row, become INCOMPLETE, with the draw index and reason recorded.

The accepted books are the original whole-book null, conditioned on the absence of every resampled refusal. They stay
exchangeable across draws and keep the same risk-matched template shape. When attempt 0 is valid for every draw, the output equals the pre-amendment generator
exactly. A golden-digest test enforces this.

## Refusal groups

- **STRUCTURAL**: `capacity.one_per_symbol`, `capacity.max_positions`, `proposal.collision`
- **PATH_DEPENDENT**: `risk.daily_loss`, `execution.size_min`

STRUCTURAL refusals depend only on the proposed entry times, symbols and holding periods.

**The rule is not P&L-independent.** A daily-loss halt depends on the marks of earlier legs, and a size refusal
depends on the wallet path. Resampling PATH_DEPENDENT refusals therefore conditions the null on avoiding drawdown
halts and size refusals. This is a conservative upward selection: it biases mean B0 P&L **up** and makes the null
harder to beat.

## Pre-registered interpretation (per stress x hypothesis)

`path_dependent_share` = `path_dependent_refusals` / `total_resampled_refusals`, or 0 when the denominator is 0.
Both counts are taken over individual refused rows.

| Share | Label | Meaning |
|---|---|---|
| 0% | `CLEAN_ON_DIAGNOSTIC` | No path-dependent conditioning was observed. |
| more than 0% and below 5% | `CONDITIONED_NULL` | The null is mildly conditioned, upward. |
| 5% or more | `MATERIAL_CONDITIONING` | The B0 p-value cannot be used as clean-null promotion evidence. |

## Recorded diagnostics (per stress)

Each stress records:

- `attempts_per_draw` and `rejected_attempts`;
- `rejection_reasons`: the sorted refusal set of each resampled attempt;
- `refusal_reason_histogram`: raw counts of each refusal reason;
- `structural_refusals`, `path_dependent_refusals`, `total_resampled_refusals`, `path_dependent_share` and
  `conditioning`, which holds the label;
- `failed` or `exhausted`, with the reason, when the stress fails closed;
- the `pairing` disclosure;
- `acceptance_rate` and `total_attempts`;
- attempts as quantiles, a histogram and a digest;
- accepted-book trade-count quantiles;
- a histogram of how many episodes rejected books admitted;
- the existing `outcomes_digest` and `mean_net_pnl`.

## Independence and boundaries

This rule was chosen without looking at any candidate or B0 P&L, although the null itself depends on the P&L path,
as set out under *Refusal groups*. The only result-derived inputs seen were the
valid-book counts listed above. The following are unchanged: primary episodes, hypotheses, windows, costs, seed root,
stress definitions, the 10,000-book count and ledger history. The holdout stays sealed. No market data, PAPER state or
runtime state was opened, and no rerun was started.

## Revision

r2, 27 October 2026: Codex requested changes at `e86aefa`.

- Data and episode incompleteness now fail closed instead of being resampled.
- Unpaired stress rows are disclosed.
- The 10,000-attempt budget stays deterministic, with no wall-clock cap.

H2's 0/10,000 valid base books mean it is expected to exhaust the budget and fail closed.

r3, 27 October 2026: Codex upheld a Cowork blocker at `97d74d9`.

- The claim that the null is P&L-independent is withdrawn.
- Refusals are split into STRUCTURAL and PATH_DEPENDENT groups.
- Unclassified refusals fail closed.
- The conditioning labels above are pre-registered.
