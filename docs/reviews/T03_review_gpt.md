# T03: Installer rollback drill — Codex review

*Reviewed 2026-10-05 20:36 Cairo (Africa/Cairo). Branch `t03-rollback-drill` at `35c1fea`; implementation tip `24ff38b`; base `master` at `f67d373`.*

## Verdict

**Conditionally approved.** I found no open defect in the T03 installer implementation. The forced-failure rollback path has strong Windows evidence and the live bot recovered cleanly. Do not mark T03 accepted or fast-forward `master` yet: the normal successful-install path still needs its one real Windows run and post-install verification.

No P1 findings.

## Findings

### P2 — The canonical review summary points at obsolete T03 states

The beginning of `T03_review_request.md` still identifies `b72b78b` as the branch tip, reports 199 tests, and later tells the owner to expect `6aa4405`. The actual implementation is `24ff38b`, the review-document commit is `35c1fea`, and the current Windows repository result is 207 passed. The later appendices explain the history, but a reviewer following the canonical sections from the top can review or import the wrong state.

**Recommended fix:** put a short “current final state” block immediately after the title with the current branch/document commit, implementation commit, base commit, Windows results, drill result, and remaining owner step. Mark the older `b72b78b` and `6aa4405` sections explicitly as historical superseded rounds, or move them under a clearly labelled chronology. Update the acceptance instructions so only one current commit and one current sequence are presented as authoritative.

### P2 — The normal success path remains unproven on Windows

The rollback drill deliberately makes the new executable fail, so it proves backup, stop, swap, failure detection, hash-verified restoration, restart, and HMAC health confirmation. It does **not** reach the changed success-only section that updates the source mirror and shortcuts after the new build answers. The installed runtime is therefore still build `20261005-103320`, as expected after the drill.

**Required acceptance evidence:** run `build_app.bat` normally once; require `BUILD_DONE` / “installed and confirmed running”; confirm the running build is the newly generated build; confirm the source mirror and shortcuts were updated; then verify engine/exchange health, no unprotected or orphaned positions, and all tracked positions retain stops. Record that evidence in the review request or a T03 fix/acceptance report before marking T03 complete.

### P3 — Protect `master` after T04 introduces required checks

GitHub currently reports the ticket branch as unprotected, and there are no required CI checks yet. This is consistent with T04 being next and does not block T03. After T04 creates fast/full/release checks, protect `master`, require the appropriate checks before merge, and disable force-pushes/deletion on `master`. Continue using ticket branches for assistant work.

## Evidence independently checked

- `tests/test_installer.py`: **15 passed** on Windows.
- Complete repository suite: **207 passed in 305.18 seconds** on Windows, including the real `cmd.exe` → `build_app.bat preflight` → Windows PowerShell integration test.
- Rollback drill build `20261005-202028`: safety set **194 passed, 7 skipped, 6 deselected**; new executable self-test passed; forced launch failed as designed.
- Restored executable SHA-256: `67528ACA8CFB23D535EF6D926B22DE1A01FAEE2FC2F1AB9306D3068E9B79B5D6`, identical to the pre-install executable.
- Restored build `20261005-103320` answered the authenticated HMAC ping.
- Live check at 20:32 Cairo: paper mode; engine `ok`; exchange `ok`; zero errors; zero unprotected positions; zero orphans; four tracked positions, all reporting active stop protection.
- Git working tree was clean and synchronized with `origin/t03-rollback-drill` at the start of this review.

## Code assessment

The implementation fails closed before touching the running bot when preflight, dependencies, tests, build, self-test, or backup verification fails. The simulated failure exits before opening the server, writing the session, starting the engine, or contacting the exchange. Rollback verifies the restored file hash and the identity/build of the restarted process. Moving the mirror and shortcut update after the authenticated launch check corrects the stale-mirror failure mode.

No broad cleanup should be mixed into T03. The planned T03b migration from a large batch script to structured PowerShell remains the right place to improve installer maintainability, after T04 CI is established.
