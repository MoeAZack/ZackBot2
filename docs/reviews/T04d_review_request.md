# T04d: faster CI. Review request

## Current state

| Item | Value |
|---|---|
| Branch | `t04d-faster-ci`, **merged with protected `master` 566ed56** (T05) in round 2 (T05 preserved unchanged). **Pipeline mode** |
| Head for review | the PR head. The full SHA is in the FIXED FOR CODEX comment (round 1 reviewed 3481f12 → Codex d74932f; round 2 reviewed 99a276e → Codex PR comment "ROUND 2 — FIXES REQUESTED"; round 4 blocker reported in 6ac349e) |
| Class | CI/verification only. No bot, installer or trading change. `verify.py`: the full level now runs one canonical `FULL_PLAN` (same gates, same order) |
| Owner approval | "Faster CI" speed-up, selected by the owner on 2026-10-06 (relayed on PR #5) |

## Round 4 disposition (Codex blocker in 6ac349e: dispatch-only full not counted by protection)

**P1: the successful dispatched `verify full` was on the commit's check runs but absent from PR #7's `statusCheckRollup`, so the PR stayed BLOCKED.** Confirmed (GitHub does not associate `workflow_dispatch` runs with a PR). Fixed with a PR-associated trigger:
- `verify.yml` now triggers ONLY on `pull_request: types: [labeled], branches: [master]` (no `workflow_dispatch`). Codex adds the label **`full-ready`** to the PR once code review is clean; a `pull_request` run is associated with the PR and appears in its rollup.
- Ordinary `opened` / `synchronize` / `reopened` events never start `verify.yml`: they produce exactly one `verify fast` (verify-fast.yml, default types) and no `verify full` in any state. Adding a label does not re-run `verify fast`.
- **Only the real aggregator can be `verify full`.** Its job name is `${{ github.event.label.name == 'full-ready' && 'verify full' || 'full gate not requested' }}`, with `needs: full-part` + `if: always()` (never skipped). For any other label (including `Full-Ready`, `full-ready `, `verify full`, empty) the slices are skipped under their non-required names and the aggregator runs as **"full gate not requested"**, does nothing (every working step requires the label), and so never publishes a successful or skipped `verify full`.
- **Exact head.** `ZB_HEAD_SHA = github.event.pull_request.head.sha` (the PR head, not the `refs/pull/N/merge` commit that `GITHUB_SHA` points to on PR events). In every job: first working step = label + 40-hex format check; then `actions/checkout` with `ref: <head sha>`; then `verify_ci.py guard --expected "$ZB_HEAD_SHA"`, which now compares the CHECKED-OUT commit (`git rev-parse HEAD`), never `GITHUB_SHA`. Every slice records that commit; artifacts are named by the head SHA; the merge re-checks it (`--expected-sha`, step "exact approved head") and requires every slice on the same commit.
- **Invalidation.** `synchronize` does not trigger `verify.yml`, so the old run's `verify full` stays on the old SHA and a later commit has none; protection blocks until Codex **removes and re-adds** `full-ready` (a new deliberate trigger on the new head).
- Concurrency is per PR number + label name, so adding an unrelated label never cancels a running full; re-adding `full-ready` replaces the previous full.

Tests (tests/test_ci.py): a small event simulator reads the three workflow files and computes, per event, which check names each workflow publishes and whether each job runs or is skipped (GitHub default PR types, `types:` filters, the job `if`, the label-dependent name; any unmodelled expression fails the test). New/changed:
- `test_each_required_name_has_exactly_one_producer` (over all PR actions × labels and push);
- `test_triggers_one_fast_run_per_feature_commit_and_no_full_on_pr` (opened/synchronize/reopened → exactly `[verify fast run]`; no dispatch/schedule/pull_request_target anywhere);
- `test_only_the_full_ready_label_publishes_verify_full` (full-ready → slices + `verify full`; 6 wrong labels → "full gate not requested", no required name in any state; unlabel → nothing);
- `test_the_not_requested_aggregator_does_no_work`;
- `test_full_gate_binds_to_the_exact_pr_head` (guard first, explicit head checkout, post-checkout guard, head-SHA artifacts, merge `--expected-sha`, no `github.sha`, concurrency key);
- `test_a_new_commit_invalidates_the_full_result`, `test_guard_command` (checked-out commit wins over a different `GITHUB_SHA`), `test_required_jobs_are_never_conditionally_skipped` (the only job-level `if` on the slices is the label; the aggregator's is `always()`; no `write` permission).
actionlint 1.7.7: clean on all three workflows.

**Mutation proof (round 4): 12/12 caught.** Static `verify full` name; `synchronize` or `opened` added to the types; types removed; slices without the label condition; checkout without the head ref; post-checkout guard removed; merge without `--expected-sha`; merge step unconditional; concurrency without the label key; guard reading `GITHUB_SHA`; `workflow_dispatch` re-added.

**P2 (CodeQL alert 10, `py/redos`, recorded in 9503634): the multiline `head_sha` input regex in `tests/test_ci.py`.** Confirmed. Fixed: that test and regex are gone (there is no dispatch input any more), and the new job-block reader `_job_blocks` uses plain line parsing, not a regex. The remaining test regexes are single-line and linear (anchored literals with `[^']+`, `[^\]]+` or one `.+` per line).

**Process:** rejected iteration → focused + one `verify fast`. Review-clean candidate → Codex adds `full-ready` (about 8–9 min of job time); a later commit → remove + re-add.

## Round 2 dispositions (Codex PR review on 99a276e + owner speed decision)

1. **P1: full ran on every PR iteration; no review-clean trigger.** Confirmed. Fixed:
   - `verify.yml` is now `workflow_dispatch` ONLY, with a required `head_sha` input. It holds the three full slices plus the required `verify full` aggregator.
   - The FIRST step of every full job fails unless `head_sha` is a full 40-character SHA AND `GITHUB_SHA == head_sha`. The merge re-checks it (`verify_ci.py merge --expected-sha`, step "exact approved head").
   - Codex dispatches it on the PR branch once code review is clean (`gh workflow run verify.yml --ref t04d-faster-ci -f head_sha=<sha>`, or the Actions UI). The workflow is already registered on master, so dispatching on the branch runs the branch's version.
   - Rejected iterations cost only `verify fast`.
   - Invalidation: a new commit has no `verify full` at all (nothing else creates it), so branch protection blocks the merge until Codex dispatches again, and a dispatch naming an old SHA refuses to run on the moved branch.
   - The aggregator keeps `if: always()`. That is not a skip condition: it makes a failed or cancelled slice FAIL `verify full` instead of skipping it. No other job has a job-level `if`.
2. **P2: two fast runs per feature commit.** Confirmed (push run + PR run on 99a276e). Fixed:
   - `verify-push.yml` now triggers on `push: branches: [master]` only;
   - the new `verify-fast.yml` (`pull_request` into master) is the only producer of the required `verify fast`, so a feature commit gets exactly one fast run.
3. **P1: a fresh Git-for-Windows checkout failed the manifest gate (CRLF).** Confirmed. Fixed:
   - a new `.gitattributes` sets `text eol=lf` for `data/**/*.csv`, `data1h/**/*.csv`, `data_long/**/*.csv` and `DATA_MANIFEST.json`;
   - the index was already LF, so no content changes, and the manifest is not regenerated or weakened;
   - the 7 CRLF `.bat` launchers are deliberately untouched.
   Tests:
   - `git check-attr eol` gives `lf` for all 65 paths;
   - a brand-new `git -c core.autocrlf=true clone` reproduces every manifest hash and passes `manifest_check` (marked slow: it runs in full, not fast);
   - removing `.gitattributes` makes both tests fail.

Tests added or changed (tests/test_ci.py):
- exactly one producer per required name;
- one fast run per feature commit and no full on PR/push;
- the exact-head guard is the first step of every full job and the merge is bound to it;
- new-commit invalidation;
- the `guard` command;
- merge refuses an unapproved head;
- no conditional skips (fast, slices);
- both manifest line-ending tests.
`tests/test_verify.py:265` (action pins) now covers all three workflow files.

**Mutation proof (round 2): 7/7 caught.**
- full on PR again;
- push on all branches;
- guard without the SHA comparison;
- `head_sha` optional;
- `head_problem` ignoring a mismatch;
- merge not bound to the SHA;
- `.gitattributes` removed.

**Process this enables (owner speed decision):**
- rejected code iteration: focused + `verify fast` only (about 4–5 min);
- final candidate: Codex dispatches one exact-head parallel full (about 8–9 min of job time).
- Prepare review and acceptance text before dispatching, so no post-green commit invalidates the result.
- Docs-only reuse for the required full is not attempted (see round 1); it exists only for master pushes.

## Round 1 dispositions (Codex review d74932f on 3481f12)

1. **P1: a skipped push job published the required `verify full` name.** Confirmed defect (it predated T04d, but T04d rewrote the workflow and must not keep it). Fixed by splitting the workflow:
   - `verify.yml` runs only on `pull_request` (into master) and `workflow_dispatch` (no inputs) and is the ONLY producer of `verify fast` / `verify full`;
   - in it every job runs: no job-level `if` on fast or the slices, and the `verify full` aggregator is `if: always()` with `needs: full-part`, so a failed or cancelled slice fails it instead of skipping it;
   - pushes run the new non-required `verify-push.yml`, whose only job is named `push fast`.
   Tests assert job names, triggers, the absence of job conditions and the absence of any reuse in the required workflow. After the push, GitHub will show no `verify full` check on push runs.
2. **P1: the parallel plan could silently omit a gate; the merge was permissive.** Confirmed defect. Fixed:
   - `verify.FULL_PLAN` is the one canonical ordered gate list. `verify.py full` runs it (`run_full_plan`), and `verify_ci.py part` runs exact slices of it.
   - `partition_problems()` proves the slices are an exact, duplicate-free partition. A slice refuses to run, and the merge fails, if not.
   - The merge requires exactly one summary per slice, the exact level `full-<slice>`, this commit, `passed is True`, exactly the slice's ordered step names, and every step `passed`. Unexpected slice summaries also fail it.
   Tests and mutations cover:
   - a new gate in the plan but in no slice;
   - an extra failed step;
   - a duplicate step;
   - `passed` false or missing;
   - wrong level, reordered steps, malformed steps;
   - other commit, missing slice, duplicate summary, failed step;
   - an unexpected slice.
3. **P2: unit tests started the real Windows builder.** Confirmed defect. The merge helper now mocks `verify.installer_mode` and asserts it was asked for `buildcheck` exactly once. No unit test can start a build.
4. **P2: reuse did not bind provenance.** Confirmed. Fixed by your first option: **no reuse for required checks** (the PR workflow always runs everything; parallel full is about 8 min). Docs-only reuse remains only in the non-required push workflow and only from the same context: a successful run of `verify-push.yml` (path checked), from a `push` event, on exactly `before`. The summary records the run id and url. Tests cover event mismatch, another workflow path, another commit, failure, in-progress, network error and no token.
5. **P3: the docs guard omitted workflows/config.** Accepted. The scan now covers .py .pyw .ps1 .psm1 .bat .cmd .html .js .spec .yml .yaml .toml .ini .cfg, and asserts that both workflow files were scanned.

**Mutation proof (round 1): 8/8 caught.**
- slice `passed` ignored;
- subset instead of exact steps;
- merge ignoring the plan partition;
- reuse on PR events;
- any workflow accepted;
- any event accepted;
- slice commit ignored;
- slice level ignored.

**Trade-off for the owner:** docs-only commits on a PR (status and review commits) now run the full 8-minute required check instead of 45 s. They still benefit from the parallel speed-up (24 → 8 min).

## Why

Run #94 (T03b, b8dadb2, a Codex docs-only status commit) took 24 min: fast 3m20s, then full 20m15s run serially. Full is tests 376 s, replay 1 212 s, replay 2 363 s and UI 207 s, after waiting for fast.

## The change (round 1 design; workflows superseded by round 2 above)

1. **Parallel full in the required workflow (`verify.yml`, pull_request + workflow_dispatch only).**
   - Three slices of `verify.FULL_PLAN` run at the same time through `verify_ci.py part`: `tests`, `replay2`, and `replay1-ui` (the UI harness seeds its closed trades from replay 1 of the same run).
   - The required `verify full` job runs `verify_ci.py merge` with the strict rules in disposition 2. The merged `latest_full.json` lists the gates in plan order, with the off-Windows `installer buildcheck` skip as before.
   - `verify fast` runs `verify.py fast` unchanged. Full no longer waits for fast.
2. **Push check (`verify-push.yml`, non-required, job `push fast`).**
   - It runs `verify.py fast` on every push.
   - A docs-only change on top of a commit where this same push workflow succeeded (push event, exact `before`, ancestor) reuses that run. It still runs the static compile check, the secret scan and the manifest. The heavy steps are recorded as SKIPPED with `REUSED: … push run <id> (<url>)`, and `reused` re-decides independently of the plan step.

## Why push reuse is safe

- Nothing executed reads `docs/`. The guard scans every code, script, page, workflow and config extension outside docs/ and data folders, and asserts that both workflows are covered.
- Secret scan and compile, which do look at docs/, are re-run.
- It never feeds a required check.

## Tests (`tests/test_ci.py`: 16 test functions (27 runner entries: the 12-case merge-deviation test is parametrized); `tests/test_verify.py` pin test now covers both workflows)

- Plan and partition: the exact partition; a new gate missing from the slices fails both the merge and the slice; sequential full runs the plan in order; each slice runs only its steps, in plan order; replay 1 and the UI share one folder.
- Merge: passes when everything is exact. It rejects, each tested separately:
  - a missing slice;
  - a failed step;
  - another commit;
  - a duplicate summary;
  - a missing step;
  - an extra failed step;
  - a duplicate step;
  - `passed` false or missing;
  - wrong level;
  - reordered steps;
  - malformed steps;
  - an unexpected slice.
  buildcheck is mocked and asked for exactly once.
- Workflows: required names only in verify.yml; triggers are pull_request + dispatch with no inputs and no push; no job-level conditions on required jobs; aggregator always() + needs; no reuse in the required workflow; push workflow names and permissions.
- Push reuse:
  - docs_only rules;
  - decide is push-only (PR, dispatch, empty and pull_request_target events refused);
  - code, docs+code, zero or empty `before`, non-ancestor (including a docs-only sibling), unknown commit and no prior run are all refused;
  - prior_push_success requires the same workflow path, a push event, the same sha and success, and fails closed on network or token problems;
  - `reused` labels and re-checks, and fails when the conditions do not hold;
  - plan output.

## Evidence (Claude sandbox, stand-in runner, NOT official pytest)

- tests/test_ci.py: 27 passed in the stand-in runner. Full tests/: see the FIXED FOR CODEX comment.
- Both workflows parse as YAML (jobs: fast / full-part / full; push-fast).

## GitHub evidence (round 0, before the workflow split)

- PR run #101 on 828cb8f: plan 7 s (no reuse: PR opened, no previous commit); verify fast 198 s; slices in parallel: tests 225 s, replay2 193 s, replay1-ui 451 s; **verify full (merge) 22 s: PASS**, all four heavy steps passed on merge commit 51e1013, buildcheck skipped off Windows. Whole run **8 min 11 s** (04:11:02 -> 04:19:13 UTC), against 24 min for run #94 on the old workflow.
- Push run #100: plan + verify fast PASS; full not run on push (unchanged).
- This docs-only commit is itself the live test of reuse: its PR run should show plan REUSE for fast and full, with the steps SKIPPED/REUSED from 828cb8f.
- The secret scan is clean. The workflow parses as YAML with jobs plan / fast / full-part / full. actionlint is not available in the sandbox; GitHub's own parse is the first real check.

## Questions for Codex

1. Both round 0 questions are answered by the redesign: required checks are never reused, and `ROADMAP.md` is not docs-only.
