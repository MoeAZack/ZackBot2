# breadth_static40.v1: registration and evaluation runbook

The prereg is `breadth_static40.v1.json` in this folder (format `zb-breadth-prereg/1`). The validator is
`tools/research/breadth_prereg.py`, and the evaluator is `tools/research/breadth_eval.py`.

**Holdout window:** 2026-10-19T00:00:00Z (03:00 Cairo, 19 Oct 2026) to 2027-10-18T00:00:00Z (03:00 Cairo, 18 Oct 2027). The end is exclusive.

## Registration (Y must land on master before 2026-10-19T00:00:00Z, 03:00 Cairo)

1. **#115 merges, then #116 is restacked on master** (merge-in, no force) and passes the exact-head gates.
2. **Codex merges #116.** Let `X` be the full SHA of the resulting master commit, which holds the accepted prereg, validator and evaluator.
   - `X` is the `accepted_commit`, and `X^{tree}` is the `accepted_tree`.
   - **X chronology authority:** the final master merge time of `X`. Author and committer dates are not authoritative.
   - **Master freeze starts here.** Nothing else merges to master until `Y` has landed.
3. **Build registers from a clean checkout of `X`**, without changing any file first:
   `python tools/research/breadth_prereg.py register --accepted-commit X`
   - The time is taken internally, to the millisecond. No time argument exists, and a supplied time is refused.
   - Registration is refused:
     - at or after the holdout start;
     - when HEAD is not `X`;
     - when `X` is later than the clock (backdating);
     - when the prereg, validator or evaluator bytes differ from `X`;
     - when the family is already registered.
4. **Build commits exactly two files**, as the only child of `X`, and opens the ledger-only PR `Y`:
   - `research_evidence/ledger/breadth_static40.jsonl` (the 12 records);
   - `research_evidence/ledger/REGISTRY.jsonl` (one appended `family` declaration line).

   **Touching REGISTRY.jsonl is by design.** A new zb-ledger/1 family is declared there, so this is not a violation.
5. **Codex checks the PR and merges it** with master still frozen, so `Y`'s only parent on master is `X`.
   - `python tools/research/breadth_prereg.py verify` must pass. It requires that `X` is `Y`'s only, direct parent and that `X..Y` touches only those two files.
   - After acceptance, **Codex creates an immutable tag on `Y`**. Build never self-binds `Y`.
   - `verify_registration` also checks that `registered_at_utc` ≤ the commit time of `Y` < the holdout start.
6. The master freeze ends when `Y` has landed and been tagged.

## Evaluation (only after 2027-10-18T00:00:00Z)

The tag on `Y` is passed to every command as `--registration TAG`.

1. **From a clean checkout of `Y`:**
   1. `python tools/research/breadth_eval.py collect --registration TAG --cache DIR --manifest FILE`
      - This collects the warm-up plus the holdout with the frozen bounded collector.
      - The cache stays outside the repo. The manifest is published for review.
   2. `python tools/research/breadth_eval.py packets --registration TAG --cache DIR --out PKTS`
      - This runs one frozen-runner packet per hypothesis. Each packet records `commit` = `Y` and a clean tree.
   3. `python tools/research/breadth_eval.py spend --registration TAG --packets PKTS --manifest FILE --out EVAL`
      - First, every packet and the manifest are validated (see the binding checks below).
      - Then exactly one family `window_spent` record (role `evaluation_spend`, split `holdout`) is appended. It binds `eval_digest` (the packet hashes, the manifest digest, the code digest and the flip count) and the output location `EVAL`.
      - No verdict is computed at this step.
2. **Commit the spend as `R`.** `R` touches the two ledger files only, and is `Y`'s only child.
   - Codex merges `R` the same way as `Y`, with master frozen.
   - `R` is what makes the evaluation spent: a second spend is refused at `R`, and also at `Y` in any clone holding `R`.
3. **From a clean checkout of `R`:**
   `python tools/research/breadth_eval.py evaluate --registration TAG --packets PKTS --manifest FILE --out EVAL`
   - This computes the sign-flip, Holm and gate rows, and writes `EVAL/evaluation.json` once, never overwriting it.

**Every command refuses before touching any data path when:**
- the clock is before 2027-10-18T00:00:00Z;
- the worktree is dirty;
- `TAG` does not resolve, is not the commit that introduced the 12 records, does not have `X` as its only parent, or differs from `X` outside the two ledger files;
- HEAD is not `Y` (for `collect`, `packets` and `spend`) or not `R` (for `evaluate`);
- `collect`, `packets` or `spend` runs after the family already holds its spend;
- for `evaluate`: the packets or manifest differ from what the spend bound, `--out` is not the recorded location, or `evaluation.json` already exists.

**Packet binding.** Each packet must:
- carry `commit` = `Y` and `tree` = `clean`;
- recompute its own `packet_sha256`;
- keep every `t_in`/`t_out` inside `[2026-10-19, 2027-10-18)`;
- recompute `stats.aggregate` from its trades:
  - counts and exits exactly;
  - sums and `max_dd_r` within 1e-6 × (n+1);
- carry cache file digests equal to the holdout manifest's;
- match the frozen key, side, mgmt, params, costs, runner sha256 and universe digest.

The manifest must be a 4h `zb-klines-research/1` manifest over exactly the holdout window.
