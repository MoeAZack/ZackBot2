# T04d: Faster CI — Codex round-five review

*2026-10-06, Africa/Cairo. Reviewed exact round-four implementation head `99b8a6a4ad71047b8017db2d7817a5a574750201` against protected master `566ed5641b4bacdff72b97e9f1488ce0ddb2e3fd`.*

## Decision: review-clean; final labelled full gate required

No code finding remains. The final-full workflow is now PR-associated, exact-head, and deliberately triggered by the `full-ready` label. Ordinary PR updates still run one fast check and no full suite. The CodeQL finding is fixed with deterministic line parsing.

This review and the owner status page are prepared before the final gate. The resulting documentation head must pass its single fast and CodeQL checks; Codex will then add `full-ready` exactly once. No later commit may be added before merge.

## Round-four dispositions

### Fixed — the final full result is now associated with the pull request

`.github/workflows/verify.yml` now listens only for `pull_request` label events into `master`. The intended `full-ready` label runs the three slices and the real aggregator named `verify full`. Ordinary opened, synchronized, and reopened events never start this workflow; they run only `verify fast` through `verify-fast.yml`.

Every full job takes the PR head SHA from the event, validates its format, checks out that SHA explicitly, then verifies the checked-out commit. Slice artifacts are keyed by that SHA, and the merger rechecks it. A later commit therefore has no full result until `full-ready` is removed and deliberately added again.

### Fixed — unrelated labels cannot satisfy the required full identity

For any label other than the exact lowercase `full-ready`, slice jobs are skipped under non-required names and the aggregator is named `full gate not requested`. Its only running step records that no gate was requested; all verification steps are skipped. It never publishes `verify full` as successful or skipped.

Codex tested this live by adding the existing `question` label to PR #7. Run 37435296506 completed in seconds with only `full gate not requested` successful and the non-required slice name skipped. No `verify full` check existed. The probe label was removed immediately.

### Fixed — CodeQL inefficient-regex alert

The multiline regex identified by alert 10 (`py/redos`) is gone. Workflow job blocks are parsed line-by-line, and the old dispatch-input test no longer exists. CodeQL's actions and Python analyses and its security summary all pass on `99b8a6a`.

## Evidence

- Remote branch contains current protected master as an ancestor and is mergeable.
- Exact implementation head `99b8a6a`: one PR `verify fast` passed in 5m14s; no feature push run and no automatic full run.
- CodeQL: actions analysis, Python analysis, and security summary all successful; the prior alert is absent from the current head.
- Windows focused review: **53/53 passed** in 17.70 seconds for `tests/test_ci.py tests/test_verify.py`.
- Focused coverage includes event/action simulation, exclusive required-check ownership, wrong-label behavior, exact PR-head checkout, post-checkout guard, new-head invalidation, strict slice merging, action pins, and fresh `core.autocrlf=true` clone proof.
- Live wrong-label probe: <https://github.com/MoeAZack/ZackBot2/actions/runs/37435296506>.
- No administrator override, protection change, installation, bot stop, exchange call, order, credential, or runtime action occurred.

## Final acceptance gate

1. Commit this review and the refreshed owner status only; executable/configuration files must remain identical to `99b8a6a`.
2. The resulting exact head must pass its single `verify fast` check and clean CodeQL summary.
3. Codex adds `full-ready` once. The PR-associated run must execute all three full slices and publish `verify full` on the exact head.
4. All slices and the strict aggregator pass; PR #7's rollup must contain both required checks and GitHub must remove the protection block.
5. Record final acceptance in a PR comment, then perform a normal protected rebase merge without override.

Until those live steps complete, the verdict is **review-clean but not yet accepted or merged**.
