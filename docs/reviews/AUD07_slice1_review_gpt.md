# AUD-07 slice 1 Codex review

**Verdict:** accepted test/CI gate at implementation head `132f18d95329040e3fa5de3de18b386508367d56`.

## Scope

This slice adds the reusable golden execution-contract pack and the C13f replay gap-open correction. It does not change
live order placement. Product corrections for C11, C13a, C13c, C13d and C13g remain in the following AUD-07 slice.

## Review rounds

- r1 required fail-closed immutable-base evidence, full contract hashing, observable C13c behavior, strict final-state
  capabilities, path-specific tolerances, an honest C13g divergence, and separate runtime limits.
- r2 closed those gaps but still allowed non-finite values to bypass comparisons, treated the 120-second limit as
  post-run accounting, accepted unknown runner selections, and weakly bound divergence pointers.
- r3 rejects non-finite expectations, outputs, tolerances and resolutions; runs the pack in a process tree with a real
  deadline; rejects empty or unknown runner selections; binds new/changed divergences to contract-bearing ledger records;
  and adds adapter-isolation and mutation tests.

## Evidence

- Codex Windows focused run: **86 passed, 1 expected bootstrap skip, 14 strict xfails** in 35.98 seconds.
- Claude Windows focused run: **86 passed, 1 expected bootstrap skip, 14 strict xfails**.
- Cowork Linux adversarial re-attack found no remaining r2 defect and independently reproduced the five recorded product
  divergences.
- Exact-head fast and CodeQL checks passed; `git diff --check` is clean.

## Follow-up boundary

The next AUD-07 product slice must add direct mutation tests proving `exit`, `side`, and `final.lots` each fail when it is
the sole differing compared field, alongside its CORR-0004+ records. This is a coverage follow-up, not a defect in the
current comparator; direct comparison already rejects those mismatches.

Protected full CI is required on the final documentation head before merge.
