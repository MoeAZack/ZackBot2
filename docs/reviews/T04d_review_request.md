# T04d: faster CI. Review request

## Current state

| Item | Value |
|---|---|
| Branch | `t04d-faster-ci`, from protected `master` f9778ca. **Pipeline mode** (owner, 2026-10-06) |
| Head for review | the PR head. The full SHA is in the READY FOR CODEX comment |
| Class | CI/verification only. No bot, installer or trading change. `verify.py` is unchanged |
| Owner approval | "Faster CI" speed-up, selected by the owner on 2026-10-06 (relayed on PR #5): docs-only changes skip the heavy steps but still report the required checks; full is split into parallel jobs |
| Overlap | `tests/test_verify.py`: one assertion (runs-on count) and one pin added, in the T04b block. T03b (PR #5) adds a test elsewhere in that file; git merges the two hunks cleanly. Rebased onto master if T03b merges first |

## Why

Run #94 (T03b, b8dadb2, a Codex docs-only status commit) took 24 min: fast 3m20s, then full 20m15s run serially. Full is tests 376 s, replay 1 212 s, replay 2 363 s and UI 207 s, after waiting for fast.

## The change

1. **Parallel full.** The `full` job is split into three slices that run at the same time, each in its own job, through `verify_ci.py part`:
   - `tests` (all of tests/);
   - `replay2`;
   - `replay1-ui` (the UI harness seeds its closed trades from replay 1 of the same run, as in verify.py).
   The required **"verify full"** job (same name, so branch protection is unchanged) runs `verify_ci.py merge`. It passes only if:
   - exactly one summary per slice is present;
   - every slice ran on this commit;
   - every expected step is present and passed.
   Otherwise it fails (it runs with `always()`, so a failed or cancelled slice fails the check rather than skipping it). The merged `latest_full.json` has the same shape as `verify.py full`, including the off-Windows `installer buildcheck` skip.
   Full no longer waits for fast. Expected: about 8–9 min for full instead of 20, and about 9 min end to end instead of 24.
2. **Docs-only reuse.** A `plan` job decides, and the fast and full jobs re-decide independently (`verify_ci.py reused` does not trust the plan output). The heavy steps are reused only if ALL of these hold:
   - push or pull_request event (never `workflow_dispatch`), with a real previous commit (`github.event.before`; empty or all zeros means no reuse);
   - `before` is an ancestor of the new head (no force-push);
   - every changed file is a document under `docs/`: no code or config extension (.py .ps1 .bat .yml .json .html …), no `..`;
   - the SAME check ("verify fast" / "verify full"), from the `github-actions` app, completed with `success` on `before` (check-runs API, read-only token, `checks: read`).
   Then the job still runs the static compile check, the secret scan (which covers docs/) and the dataset manifest. Every heavy step is recorded as **SKIPPED** with `REUSED: docs-only change; passed on <sha> (<run url>)` and `reused_from`. Any error or unknown answer means no reuse, so the slow path runs.
   - A PR that changes code is never docs-only, because the diff is from the previous PR head, so a branch update that merges master is not docs-only either.
   - What it speeds up: status and review-doc commits on top of an already green head (Codex's PROJECT_STATUS/review commits, my review requests).

## Why reuse is safe

- Nothing executed reads `docs/`. `test_nothing_executed_reads_docs` scans every .py/.ps1/.bat/.html/.js/.spec outside docs/ and data folders. pytest collects only tests/, and the build does not include docs/.
- The checks that do look at docs/ (secret scan, compile) are re-run, not reused.

## Tests (`tests/test_ci.py`, 16; `tests/test_verify.py` pin test updated)

- The slices cover exactly verify.py full: same step names, no duplicates. A source check fails if verify.py full's sequence changes.
- Each slice runs only its own steps; replay 1 and the UI share one output folder.
- Merge: passes with all slices on this commit; fails on a missing slice, a failed step, another commit, a duplicate summary or a missing step.
- docs_only: rejects root files, code/config extensions under docs/, `..` and mixed changes.
- decide (in a real git repo): reuses docs-only on top of a passed check. It refuses:
  - code changes;
  - docs+code;
  - workflow_dispatch;
  - a zero or empty `before`;
  - a non-ancestor (including a sibling whose diff is docs-only);
  - an unknown commit;
  - no prior success.
  It also looks up the same check on `before`.
- prior_success: only completed + success + github-actions + same sha + same name. Network error or no token gives None.
- reused: re-checks, labels every skipped step REUSED with the commit, and really runs the static checks. If the conditions fail, the check FAILS.
- plan writes both outputs. The workflow keeps both required job names; the full job uses needs [plan, full-part] + always(), merge, fail-fast false, read-only permissions plus checks: read, and pinned download-artifact v8.0.1 (3e5f45b).

**Mutation proof: 8/8 caught.** (The ancestry mutation first survived; the sibling-branch case was added and now catches it.)
- code extension allowed under docs/;
- app slug ignored;
- any conclusion accepted;
- slice commit ignored;
- duplicate summaries allowed;
- ancestry ignored;
- reuse on workflow_dispatch;
- `reused` trusting the plan.

## Evidence (Claude sandbox, stand-in runner, NOT official pytest)

- tests/test_ci.py: 16 passed.
- Full tests/: see the READY FOR CODEX comment.
- The secret scan is clean. The workflow parses as YAML with jobs plan / fast / full-part / full. actionlint is not available in the sandbox; GitHub's own parse is the first real check.

## Questions for Codex

1. Is reuse of the same check on `before` acceptable when push and PR runs both report "verify fast" on the same sha? Either one counts; both run the same gate, but the PR run tests the merge commit.
2. Should docs-only reuse also cover root `ROADMAP.md`? It is excluded for now; only `docs/` is reused.
