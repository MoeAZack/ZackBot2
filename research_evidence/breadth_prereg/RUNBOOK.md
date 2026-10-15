# breadth_static40.v1: registration and evaluation runbook

The prereg is `breadth_static40.v1.json` in this folder (format `zb-breadth-prereg/1`). The validator is
`tools/research/breadth_prereg.py`, and the evaluator is `tools/research/breadth_eval.py`.

**Holdout window:** 2026-10-19T00:00:00Z (03:00 Cairo, 19 Oct 2026) to 2027-10-18T00:00:00Z (03:00 Cairo, 18 Oct 2027). The end is exclusive.

## Registration (must land on master before 2026-10-19T00:00:00Z)

1. **Codex accepts and merges the prereg PR** (#116, stacked on #115) into master.
   - Let `X` be the full SHA of the master commit that holds the accepted prereg, validator and evaluator.
   - `X` is the `accepted_commit`, and `X^{tree}` is the `accepted_tree`.
2. **Build registers from a clean checkout of `X`**, without changing any file first:
   `python tools/research/breadth_prereg.py register --accepted-commit X`
   - The time is taken internally, to the millisecond. No time argument exists, and a supplied time is refused.
   - Registration is refused at or after the holdout start.
   - It is refused if `X` is later than the clock (backdating).
   - It is refused if the prereg, validator or evaluator bytes differ from `X`.
   - It is refused if the family is already registered.
3. **Build commits exactly two files on a branch from `X`:**
   - `research_evidence/ledger/breadth_static40.jsonl` (the 12 records);
   - `research_evidence/ledger/REGISTRY.jsonl` (one appended `family` declaration line).

   **Touching REGISTRY.jsonl is by design.** A new zb-ledger/1 family is declared there, so this is not a violation.

   Build then pushes the branch and opens the registration PR.
4. **Codex checks the registration PR:**
   - `python tools/research/breadth_prereg.py verify` passes;
   - the diff from `X` touches only those two files.

   Codex then **squash-merges** it straight away, with **no other merge to master in between**.
   - `verify_registration` requires `X` to be an ancestor of the commit that introduces the records.
   - It also requires the diff from `X` to be ledger-only.
   - Any other change merged in between breaks both conditions.
5. **The authoritative registration time is when the registration commit lands on master** (the GitHub merge time), not a git committer date. Committer dates can be set by hand.
   - A squash merge makes the commit date equal to the merge time.
   - `verify_registration` also checks that `registered_at_utc` ≤ the commit time < the holdout start.

   Land it well before 2026-10-19T00:00:00Z (03:00 Cairo, 19 Oct). Aim for 18 Oct during the day, Cairo time.

## Evaluation (only after 2027-10-18T00:00:00Z)

Run everything from a clean detached checkout of `X`, with `--ledger-ref origin/master`:

1. `python tools/research/breadth_eval.py collect --cache DIR --manifest FILE`
   - This collects the warm-up plus the holdout with the frozen bounded collector.
   - The cache stays outside the repo, and the manifest is committed in a review PR before evaluation.
2. `python tools/research/breadth_eval.py packets --cache DIR --out DIR`
   - This runs one frozen-runner packet per hypothesis.
3. `python tools/research/breadth_eval.py evaluate --packets DIR --out DIR`
   - This computes the sign-flip, Holm and gate rows, and writes `evaluation.json` once (it never overwrites).

**Every command refuses before touching any data path when:**
- the clock is before 2027-10-18T00:00:00Z;
- there is no registration at `--ledger-ref`;
- the worktree is dirty, or HEAD's commit or tree differs from the registered `X`;
- the family already holds a `holdout_reveal`, or an `evaluation.json` already exists.

The reveal is then recorded as one zb-ledger/1 `holdout_reveal` for the family: one spend, one evaluation.
