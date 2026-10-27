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
- **Resampling.** Draw `d` tries attempts `a = 0, 1, ...` and keeps the first valid book.
  - Attempt 0 uses the frozen addendum seed `sha256(candidate_id|hypothesis|split|book_index)`.
  - Attempt `a >= 1` uses `sha256(candidate_id|hypothesis|split|book_index|attempt=a)`.
  - In both cases the seed is the first 64 bits of the hash.
- **Per stress.** Each stress resamples on its own, using the same seed sequence.
- **Budget.** Each draw gets at most 10,000 attempts. The first draw that runs out stops its stress. That stress, and
  the B0 row, become INCOMPLETE, with the draw index and reason recorded.

The rule reads only admission, refusal and completeness, never P&L. The accepted books are therefore the original
whole-book null conditioned on capacity and data validity. They stay exchangeable across draws and keep the same
risk-matched template shape. When attempt 0 is valid for every draw, the output equals the pre-amendment generator
exactly. A golden-digest test enforces this.

## Recorded diagnostics (per stress)

Each stress records:

- `attempts_per_draw` and `rejected_attempts`;
- `rejection_reasons`: shared-router refusal reasons, `episode.incomplete`, `match.unmatched_template` or `data.*`;
- `acceptance_rate` and `total_attempts`;
- attempts as quantiles, a histogram and a digest;
- accepted-book trade-count quantiles;
- a histogram of how many episodes rejected books admitted;
- the existing `outcomes_digest` and `mean_net_pnl`.

## Independence and boundaries

This rule was chosen without looking at any candidate or B0 P&L. The only result-derived inputs seen were the
valid-book counts listed above. The following are unchanged: primary episodes, hypotheses, windows, costs, seed root,
stress definitions, the 10,000-book count and ledger history. The holdout stays sealed. No market data, PAPER state or
runtime state was opened, and no rerun was started.

## Open points for the signer

1. Data-incomplete attempts (`data.*`) are resampled, not failed closed. Primaries can only occur where data is
   complete, so this matches them, but the signer should confirm it.
2. Each stress resamples on its own, so B0 books are not paired across stresses.
3. The 10,000-attempt budget makes exhaustion negligible at H1's observed ~0.4% acceptance, which costs about
   2.6 million book simulations per stress. H2 at 0/10,000 is expected to fail closed.
