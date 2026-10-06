# T04d: faster CI. Review request

## Current state

| Item | Value |
|---|---|
| Branch | `t04d-faster-ci`, **merged with protected `master` d68d6ef** (T03b) in this round. **Pipeline mode** |
| Head for review | the PR head. The full SHA is in the FIXED FOR CODEX comment (round 1 reviewed 3481f12; Codex review d74932f) |
| Class | CI/verification only. No bot, installer or trading change. `verify.py`: the full level now runs one canonical `FULL_PLAN` (same gates, same order) |
| Owner approval | "Faster CI" speed-up, selected by the owner on 2026-10-06 (relayed on PR #5) |

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

## The change (current design, after round 1)

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
