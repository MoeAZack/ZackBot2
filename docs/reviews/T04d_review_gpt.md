# T04d: Faster CI — Codex round-three review

*2026-10-06, Africa/Cairo. Reviewed exact implementation head `b6d4e8101cc7455110ebe24eb5943edb1db560c6` against protected master `566ed5641b4bacdff72b97e9f1488ce0ddb2e3fd`.*

## Decision: review-clean; final exact-head full gate required

The three round-two blockers are fixed. No code finding remains in the reviewed implementation. This document and the owner status page are prepared before the final gate so the next commit can be the stable candidate: it must pass the one PR fast check, then Codex may dispatch exactly one parallel full run for that exact SHA. No installation, bot stop, exchange call, or runtime change is part of T04d.

## Findings and dispositions

### Fixed — rejected PR iterations no longer run the full suite

`.github/workflows/verify.yml` now responds only to an explicit dispatch with a required 40-character `head_sha`. Every full slice and the final aggregator first compare `GITHUB_SHA` with the approved SHA, and the merger checks the same SHA again. A moved branch therefore fails closed, while an ordinary PR update creates no `verify full` result at all.

The required full identity remains exclusive to the real aggregator. It uses `if: always()` only so a failed or cancelled slice makes the aggregator fail rather than skip.

### Fixed — one fast run per feature-branch commit

The new `.github/workflows/verify-fast.yml` is the sole producer of `verify fast` and runs on pull requests into `master`. `.github/workflows/verify-push.yml` is restricted to pushes to `master` and publishes only the non-required `push fast` identity. Feature heads no longer pay for both push and PR fast runs.

### Fixed — fresh Git-for-Windows checkouts reproduce manifest bytes

`.gitattributes` pins LF checkout for all 64 manifest-governed CSV files and `DATA_MANIFEST.json`. It does not alter the CRLF batch launchers and does not regenerate or weaken the manifest. The focused Windows suite creates a clean clone with `core.autocrlf=true`, verifies every SHA-256 value, and passes the real manifest check.

## Evidence

- Remote branch contains current protected master as an ancestor and is mergeable.
- PR diff from master is limited to the T04d workflows, verification code/tests, `.gitattributes`, and review documents.
- Exact implementation head focused Windows review: `51 passed` in 14.88 seconds for `tests/test_ci.py tests/test_verify.py`.
- The focused suite includes exact required-check ownership, ordinary-PR versus dispatched-full triggers, stale-head refusal, strict slice merging, new-commit invalidation, action pinning, and the fresh `core.autocrlf=true` clone.
- The implementation head started one `verify fast` run and no duplicate push-fast or automatic full run.
- No application, installer, trading, engine, exchange, or runtime file is changed by the PR relative to current master.

## Final acceptance gate

1. Commit this review and the refreshed owner status without changing executable/configuration files.
2. The resulting exact head must pass its single `verify fast` check.
3. Codex dispatches `.github/workflows/verify.yml` on that branch with that exact 40-character head SHA.
4. All three slices and the required `verify full` aggregator must pass on that SHA.
5. Confirm no second feature-branch fast run appeared, the PR remains mergeable/current, and protected linear merge succeeds.

Until those steps complete, the verdict is **review-clean but not yet accepted or merged**.
