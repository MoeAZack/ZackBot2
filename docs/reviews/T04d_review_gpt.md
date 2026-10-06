# T04d: Faster CI. Codex review

*2026-10-06, Africa/Cairo. Reviewed exact implementation head `3481f1293c801f2707cac07225014f71d73ce85b`.*

## Decision: fixes requested

The parallel slicing is valuable: the real GitHub run completed in about eight minutes and the slice merge correctly rejects missing, failed, duplicate-summary and wrong-commit fixtures. The current branch is verification-only and nothing was installed or run against the exchange. It is not ready to merge because two fail-closed properties are not yet established, and the focused Windows tests unexpectedly invoke the real builder.

## Findings

### P1 — A skipped push job publishes the same name as the required full gate

`master` requires the GitHub Actions checks named `verify fast` and `verify full`. The workflow runs on every push, but its job named `verify full` is conditionally skipped for push events (`.github/workflows/verify.yml:130-135`). Real push runs on this PR therefore publish a `verify full` check with conclusion `skipped` on the same head SHA and from the same GitHub Actions app.

GitHub explicitly treats a conditionally skipped job as successful for required-check purposes. Publishing a skipped check with the exact required name makes the protection ambiguous and can satisfy the named context without the pull-request full gate being the result that proved the commit. This pattern predates part of T04d, but T04d rewrites this workflow and must not preserve an unsafe required-check identity.

**Required fix:** ensure a push run never creates a check named `verify full` unless it actually executes the full gate. The simplest robust design is separate workflows/jobs: push fast may have a non-required distinct name, while the pull-request/on-demand workflow is the only producer of the required `verify full` name. Keep the required aggregator on `always()` so failed/cancelled slice dependencies make it fail rather than skip.

Add an assertion over the workflow structure and verify on GitHub that a push has no skipped `verify full`; a PR must show exactly one required full result from the intended PR workflow. Do not rely on `skipped` being treated as a failure—it is not. Reference: <https://docs.github.com/en/pull-requests/how-tos/merge-and-close-pull-requests/troubleshooting-required-status-checks#handling-skipped-but-required-checks>.

### P1 — The parallel plan can silently omit a future full gate

`verify_ci.FULL_STEPS` and `PARTS` are a second, manually maintained definition of `verify.py full`. `test_the_slices_cover_exactly_the_full_level` checks selected source substrings, but it does not prove that no additional full step exists. Adding a new safety call to `verify.py` would leave that test green while the GitHub parallel path silently omits the new gate.

The final merge is also permissive: `cmd_merge()` checks only the expected names. It does not require the slice's overall `passed` value to be true, does not reject extra/duplicate step names inside one summary, and does not require the exact ordered step list. A slice containing all expected passes plus an additional failed safety step can therefore be merged as PASS.

**Required fix:** use one canonical full-step plan shared by sequential `verify.py full` and `verify_ci.py`, then prove the parallel partitions are an exact, duplicate-free partition of that plan. At merge time require: one summary per slice, exact level/commit, `passed is True`, and an exact ordered set of step names with every status passed. Reject any extra, duplicate, missing, failed or malformed step.

Add mutations/tests for (a) a new full gate added to the canonical plan but absent from slices, (b) an extra failed step in an otherwise passing slice, (c) duplicate step names inside one summary, and (d) a false/missing slice `passed` value.

### P2 — Focused T04d tests start the real Windows executable builder repeatedly

The `_merge()` test helper calls `verify_ci.main(['merge', ...])` without replacing `verify.installer_mode`. On Windows, `cmd_merge()` calls the real `installer buildcheck` (`verify_ci.py:185`). Consequently, each of the six merge tests starts the expensive non-installing build. The focused test run reached the first build after three quick tests; Codex stopped it to avoid repeating that build for every case. No installation or bot stop occurred.

**Required fix:** mock `V.installer_mode` in the merge unit-test helper and assert the intended synthetic Windows/Linux result. Keep one separately named integration check for a real buildcheck where appropriate; a unit fixture must not launch it repeatedly. The 16 focused tests should finish quickly and leave no `zb_test_*` staging folders.

### P2 — Reuse does not bind evidence to the event and merge context that produced it

`prior_success()` accepts any successful same-name GitHub Actions check on `before`. For `verify fast`, that may be a push run on the branch tree or a pull-request run whose checkout tested a synthetic merge tree. Those are not interchangeable provenances. For PR full reuse, safety also depends on external strict/up-to-date branch protection; the summary does not record and revalidate the base SHA/non-document tree it actually tested.

Current strict protection correctly marks PR #7 `BEHIND` after T03b changed `master`, so this PR cannot merge until updated. That is a useful external mitigation, not proof inside the reuse decision.

**Required fix:** either disable docs-only reuse for pull-request required checks (parallel full already reduces them to about eight minutes), or bind reuse to the exact event, base SHA and tested tree/merge context. Push reuse must accept only evidence from the same push context, not an arbitrary same-name check. Record that provenance in the summary and test base-advance/event-mismatch cases.

### P3 — The “nothing executed reads docs” guard omits workflow/config sources

`test_nothing_executed_reads_docs` scans `.py`, `.ps1`, `.bat`, `.html`, `.js` and `.spec`, but not executed `.yml`/`.yaml` workflows, `.cmd`, or `.psm1`. The current tree has no such reference, but the test does not enforce the broad claim made by the review request.

**Recommended fix:** cover every executed source/config extension in the repository, particularly `.github/workflows/*.yml`, or replace the broad claim with a narrower, explicitly maintained allowlist and test it.

## Evidence

- Exact reviewed head: `3481f1293c801f2707cac07225014f71d73ce85b`.
- GitHub's implementation run and documentation-only reuse run are green; the parallel run completed in about eight minutes.
- Repository protection is strict and currently requires `verify fast` + `verify full` from GitHub Actions; PR #7 is correctly `BEHIND` after T03b merged.
- The local Windows focused run passed its first three pure tests, then entered a real buildcheck from the first merge test. It was interrupted before repeated builds; this is test isolation failure, not a product/runtime failure.
- No ZackBot install, rollback drill, exchange call or runtime stop occurred.

## Acceptance conditions

1. Only an actually executed full aggregator can publish the required `verify full` identity.
2. Sequential and parallel full use one canonical gate plan; the merger rejects every deviation, including extra failed steps.
3. The focused Windows T04d unit suite performs no real build and leaves no staging folders.
4. Reuse provenance cannot cross event or base/merge contexts; any uncertainty runs fresh checks.
5. PR #7 is updated onto protected `master`, then fresh (not reused) fast/full checks pass on the new exact head.
