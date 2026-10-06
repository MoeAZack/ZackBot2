# T04d: Faster CI — Codex round-four review

*2026-10-06, Africa/Cairo. Reviewed exact stable head `b3e63dbf98d7db38f4c5201fc0f1e836e09a7754` against protected master `566ed5641b4bacdff72b97e9f1488ce0ddb2e3fd`.*

## Decision: fixes requested

The three round-two implementation blockers are fixed, all focused tests pass, and the explicitly dispatched parallel full run passed. One P1 integration blocker remains: in this repository, GitHub does not accept the `workflow_dispatch` run as the pull request's required `verify full` result. The protected merge remains blocked and correctly refused without an override.

## Finding

### P1 — Dispatched full passes on the commit but does not satisfy the PR protection rollup

Stable head `b3e63db` passed its single PR `verify fast` in 5m24s. Codex then dispatched `verify.yml` on that exact branch/head. Every exact-head guard passed, all slices passed, and the strict `verify full` aggregator passed.

The commit check-runs API shows successful GitHub Actions checks named `verify fast` and `verify full` on `b3e63db`. However, PR #7's `statusCheckRollup` contains only `verify fast`; `mergeStateStatus` remains `BLOCKED`. A normal protected rebase merge with `--match-head-commit b3e63db...` was refused by the base-branch policy. No administrator override was used.

This makes the dispatch-only design operationally unusable even though the verification itself is correct: it cannot complete the protected PR merge it was designed to gate.

**Required fix:** trigger the final full through a pull-request-associated event that GitHub counts for this PR's required-check rollup, while preserving all existing fail-closed properties:

- ordinary open/synchronize/reopen PR events run one `verify fast` and create no `verify full`;
- Codex deliberately triggers the final phase only after review is clean (a `full-ready` label is acceptable);
- the real aggregator remains the only producer of the required `verify full` identity;
- no nonmatching event may publish a skipped/successful `verify full`;
- the event's exact PR head SHA is recorded, explicitly checked out, validated in every slice, and rechecked by the aggregator;
- a later commit has no valid full result and remains blocked until a new deliberate trigger;
- adding an unrelated label either creates no required identity or fails closed; it must never satisfy protection;
- focused tests prove the trigger contract, head movement invalidation, wrong-label behavior, and exclusive check-name ownership.

A label-driven `pull_request: types: [labeled]` workflow can satisfy this if it runs the full jobs only for the deliberate `full-ready` transition without exposing a skipped required identity; another design is acceptable if a live PR proof shows GitHub includes its successful `verify full` in `statusCheckRollup`.

## Evidence

- Windows focused review on implementation head `b6d4e81`: **51/51 passed** in 14.88 seconds, including a fresh `core.autocrlf=true` clone and byte-exact manifest proof.
- Stable candidate fast run: <https://github.com/MoeAZack/ZackBot2/actions/runs/37430110451> — success, 5m24s; no duplicate push run and no automatic full run.
- Exact-head dispatched full: <https://github.com/MoeAZack/ZackBot2/actions/runs/37430712018> — all guards and slices passed; tests 4m02s, replay1/UI 6m23s, replay2 6m31s, aggregator 31s.
- Commit check-runs API: both required names successful from GitHub Actions.
- PR rollup: only `verify fast`; merge status `BLOCKED`.
- Protected rebase merge: refused by base-branch policy. No `--admin`, protection change, installation, bot stop, exchange call, order, credential, or runtime action occurred.

## Acceptance conditions

1. Preserve the fixed one-fast-run, deterministic line-ending, canonical-plan and strict-merge behavior.
2. Replace the dispatch-only final trigger with a deliberate PR-associated trigger that appears as `verify full` in PR #7's live status rollup.
3. Run focused tests and one PR fast check on the new exact head.
4. Codex triggers exactly one final full; all slices and the aggregator pass on the exact reviewed head.
5. GitHub reports both required checks successful in the PR rollup, `mergeStateStatus` is no longer blocked, and a normal protected linear merge succeeds without override.
