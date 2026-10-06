# T03b: Codex review of the PowerShell installer

*Reviewed 2026-10-06 04:47 Cairo (Africa/Cairo).*

## Verdict

**Changes required. Do not run the normal installer or rollback drill yet.**

Reviewed branch `t03b-installer-powershell`, exact implementation head `9ee9d28284693787917e1c5aed1bfa5c2ba8d190`, against accepted `master` `f9778ca9cfe8daac482c8a1fa1c0c1d53bdd4bfb`. The CMD-to-PowerShell structure is a strong improvement and the Windows flow/build checks pass, but two post-stop recovery paths are not yet fail-closed and the structured record can overstate recovery.

## Findings

### P1 — An invalid rollback drill leaves the new build installed and running

**Affected:** `installer.ps1:419-425`, `installer.ps1:455-492`; missing assertion in `tests/test_installer.py:411-425`.

When a drill-launched executable answers even though it received `--simulate-failed-launch`, line 424 calls `Stop-Install`. Because this is a controlled failure (`FailWhy` is set), `Invoke-Main` does not enter its unexpected-error rollback path. The drill exits 1 while the deliberately untrusted new executable remains installed and running.

Confirmed on Windows PowerShell 5.1 using the ticket's real flow with recording fakes:

```text
rc=1, installed=NEWEXE, rollback_called=false,
verdict=BUILD_FAILED, rollback.attempted=false
```

This contradicts the drill banner and its safety purpose: regardless of whether the forced failure works, the previous verified build must be restored. In the invalid-scenario case the final result should still be a non-zero `DRILL_INVALID`/`BUILD_FAILED`, not `DRILL_PASSED`, after restoration is verified.

**Required fix:** route this path through rollback, retain an explicit invalid-drill reason, restore and HMAC-verify the old build, then return failure. Add an assertion that the old executable is installed, rollback is attempted and verified, the new process is stopped, and `DRILL_PASSED` is absent.

### P1 — A failed stop attempt is not followed by runtime verification or recovery

**Affected:** `installer.ps1:404-410`, `installer.ps1:530-550`; `tests/test_installer.py:450-467`.

`Invoke-Step8Install` marks the stop boundary before calling `Stop-Bot`, but when `Stop-Bot` returns false it resets `Stopped` to false and throws a controlled failure. `Stop-Bot` has already attempted to kill every matching process. The installer therefore cannot safely claim that nothing changed, yet it neither proves the previous build still answers nor restarts/verifies it.

The fake-flow result confirms the recovery gap: `stopOk=false` exits 1, records no rollback, and performs no old-build start or HMAC ping. The executable file is unchanged, but the runtime state is unproven. A partial stop on a real machine can leave the bot offline while positions remain open.

**Required fix:** once a stop attempt begins, treat runtime state as needing verification. If stopping fails, do not swap files; prove the old build still answers. If it does not, perform one bounded recovery of the already verified old executable/backup and require its build-specific HMAC response. Add tests for both “old still answers” and “old was stopped and must be restarted” outcomes.

### P2 — The structured record reports unverified states as installed/recovered

**Affected:** `installer.ps1:101-117`, `installer.ps1:404-410`, `installer.ps1:455-483`.

There are two confirmed provenance errors:

- `installed_after` is populated whenever the restored file hash equals `OldHash`, even if the restored build never answers and `rollback.verified` is false.
- `bot_stopped` is sourced from `StopCalled`, so a failed stop attempt is recorded as `bot_stopped: true`.

Windows fake-flow evidence showed an unverified rollback with `installed_after={build: OLD1, ...}` and a failed stop with `bot_stopped=true`, `rollback.attempted=false`, and no start/ping calls. These fields will be consumed by later automation, so optimistic values can create false acceptance evidence.

**Required fix:** populate `installed_after` only when the corresponding build is verified running (or explicitly rename/split it into file-installed and runtime-verified fields). Record stop attempted and stop confirmed separately. Add negative assertions for failed ping and failed stop.

### P3 — The batch launcher re-expands its mode without quoting

**Affected:** `build_app.bat:14-16`; missing launcher assertion in `tests/test_installer.py:214-227`.

`%~1` is assigned to `ZBMODE` and later expanded unquoted into a CMD command line. A mode containing CMD metacharacters is interpreted by `cmd.exe` before `installer.ps1` can reject the unknown mode. Normal double-click and CI calls use fixed safe values, so this is not remotely exploitable, but it is an avoidable local command-injection/robustness defect in the new thin launcher.

**Required fix:** pass the mode as `"%ZBMODE%"` and add a static or harmless subprocess test proving metacharacters stay one argument and are rejected with exit 2.

## Evidence

- Exact remote head verified after fetch: `9ee9d28284693787917e1c5aed1bfa5c2ba8d190`; checkout clean and equal to the remote branch.
- GitHub: both fast checks and the required full check passed with zero annotations.
- Windows PowerShell 5.1 focused installer suite: **53 passed**.
- Windows bare pytest: **269 passed** in 5 min 38 s; no installer side effect reached the real bot.
- Real non-installing `build_app.bat buildcheck`: **BUILDCHECK_OK**, build `20261006-044225`, eight structured steps, zero failed steps, `bot_stopped=false`, no rollback.
- Installed executable remained byte-identical to the accepted T03a build: SHA-256 `72ABCA7CC25FC7859C93A62F4518AE3659A2C8B9B524F3ACA203798E769F430D`.
- No normal install, rollback drill, bot stop/restart, trading action or credential operation was performed.

## Answers to the review request

1. Rolling back after an unexpected post-stop exception is correct. Apply the same safety principle to every path where stop has been attempted and runtime health is uncertain.
2. Rejecting unknown modes with exit 2 is correct; quote the batch argument so CMD cannot interpret it first.
3. The real flow and buildcheck passed under Windows PowerShell 5.1, including process argument quoting, `System.Management` loading in the tested flow, structured JSON, preflight and the packaged executable self-test. `WScript.Shell` remains unexercised until an owner-approved real install, so its runtime proof belongs to the later installer gate.

## Next handoff

Claude should fix the four findings without installing, drilling or stopping ZackBot, rerun the Windows-independent tests/CI available there, and post `FIXED FOR CODEX` with the exact new head. Codex will rerun the focused Windows tests and non-installing buildcheck before deciding whether the ticket is ready for a separately owner-approved runtime gate.

## Round 1 fix review

*Reviewed 2026-10-06 05:14 Cairo. Fix head `8dc74c352262c36c9fa21121113e6ddf47dc9bd5`.*

The two P1 findings are fixed. Windows PowerShell 5.1 confirms that an invalid drill stops the new executable, restores the previous executable, verifies its hash and build-specific HMAC response, then retains a failed/invalid verdict. A failed stop now avoids the swap and either proves the old process still answers, restarts the verified old executable once, or records an unverified failure. The optimistic `installed_after` and stop-status fields are also corrected.

Two smaller findings remain, so the verdict is still **changes required**:

### P2 — `installed_file_sha256` can describe a file that is absent or known to differ

**Affected:** `installer.ps1:101-121`, `installer.ps1:441-460`, `installer.ps1:501-530` at fix head `8dc74c3`.

The new field is described as the current installed executable's hash, but it is a cached value and is not refreshed on every terminal path. Two Windows fake-flow cases confirm incorrect records:

- A first install whose new executable fails to answer removes `ZackBot.exe`, but `installed_file_sha256` still reports the removed new executable's hash.
- Failed-stop recovery detects that the executable hash changed and refuses to restart it, but `installed_file_sha256` still reports the previous trusted hash rather than the detected current hash.

**Required fix:** refresh the installed-file state after every copy/delete and immediately before the final verdict. Use null when the file does not exist, the actual verified hash when it does, and explicitly verify/report a failed first-install deletion. Add assertions for both cases above.

### P3 — Embedded quotes still escape the batch launcher's protection

**Affected:** `build_app.bat:14-17`; `tests/test_installer.py` launcher injection test.

Quoting `"%ZBMODE%"` fixes an ampersand in an otherwise ordinary argument, but `%~1` may itself contain a quote. A harmless Windows probe confirmed that `x"&echo MARK_B` reaches PowerShell only as `x"`, then CMD executes `echo MARK_B`; the launcher returns 0. The new test checks only `x&echo ...`, so it misses this escape.

**Required fix:** do not re-expand untrusted free-form text into a CMD command line. Prefer fixed literal launch paths/modes (for example, separate tiny fixed-mode launchers or direct PowerShell calls for the internal preflight/buildcheck modes), or otherwise reject without interpolating arbitrary text. Add the embedded-quote case and require no marker execution plus a non-zero result.

### Documentation follow-up

The top structured-record section in `T03b_review_request.md` still lists removed `bot_stopped` and omits `bot_stop_attempted`, `bot_stop_confirmed`, `installed_file_sha256`, and `stop_recovery`. Its older test-count bullets are also stale. Refresh these while making the two code/test fixes.

### Round 1 evidence

- Windows PowerShell 5.1 focused installer suite: **58 passed**.
- Confirmed invalid-drill result: old executable restored, rollback attempted and verified, final exit 1.
- Confirmed three failed-stop outcomes and the hash guard.
- No normal installer, rollback drill, ZackBot stop/restart, trading action or credential operation was performed.

## Round 2 fix review

*Reviewed 2026-10-06 05:41 Cairo. Fix head `c3a625baaba1db392def66594bad3b938a17361a`.*

The installed-file evidence fix is correct: every install/drill verdict now re-reads the executable, absence is represented as false/null, a changed file reports its actual hash, and a failed first-install deletion is explicit. Those tests passed under Windows PowerShell 5.1.

The launcher fix is not accepted. The focused Windows suite is **62 passed, 1 failed**: its own embedded-quote test executes `MARK_B` and returns 0. This also clarifies the boundary: once a caller gives `cmd.exe /c` a malformed command string whose embedded quote exposes `&`, the outer CMD parser can execute the following command before or after the batch file. Code inside `build_app.bat` cannot retroactively make that caller-controlled command line a security boundary.

### P2 — Raw-command-line parsing can select a destructive mode from malformed extra arguments

**Affected:** `installer.ps1:Get-LauncherMode`, the new `CMDCMDLINE` hand-off in `build_app.bat`, and its tests.

`Get-LauncherMode` uses the last textual occurrence of `build_app.bat`. That means an otherwise invalid invocation can override the real launcher occurrence:

```text
cmd /c C:\Dev\ZackBot2\build_app.bat typo build_app.bat          -> install
cmd /c C:\Dev\ZackBot2\build_app.bat x build_app.bat drill       -> drill
cmd /c C:\Dev\ZackBot2\build_app.bat typo C:\x\build_app.bat preflight -> preflight
```

The last case is non-destructive, but the first two can select installation or the rollback drill even though the original invocation contains extra/unknown arguments. This violates the stated rule that anything except no argument or one known mode is refused.

**Required fix:** remove the last-occurrence ambiguity. Prefer simplifying the design to fixed literal entry points for destructive modes, or identify the actual first launcher token with an unambiguous boundary and reject everything after it unless it is exactly one supported token. Add the three cases above and require refusal.

### P3 — The Windows injection test asserts a guarantee the batch file cannot provide

**Affected:** `tests/test_installer.py:test_launcher_refuses_embedded_quotes_and_metacharacters`; launcher documentation.

The exact new Windows test fails with `x"&echo MARK_B`: return code 0 and `MARK_B` appears. This command is already syntactically hostile to the *outer* `cmd /c` parser. A local caller able to supply raw CMD syntax can run local commands directly, so the batch file is not the security boundary.

**Required fix:** replace the impossible assertion with the real contract:

- supported internal/double-click invocations select exactly one fixed mode;
- normal quoted arguments are not re-expanded a second time by the launcher;
- unknown or extra ordinary tokens are refused before staging;
- hostile raw CMD syntax is explicitly out of scope because it is parsed by the invoking shell.

Keep a harmless Windows integration test for the supported modes and ordinary unknown/extra tokens. Do not claim the launcher can neutralize an already malformed outer CMD command line.

### Round 2 verdict

**Changes required.** Do not run the installer or drill. The file-evidence issue is closed; the new launcher parser and failing Windows-only test need one focused correction. No runtime operation was performed during this review.

## Round 3 fix review

*Reviewed 2026-10-06 06:10 Cairo. Fix head `86c24368f3a48756a15a5a587891a94bfd9bbdfb`.*

**No remaining code finding. The code/static stage is approved, subject to the final GitHub full check and the separately owner-approved runtime gate.**

The parser now anchors on the first token that is actually `build_app.bat`, so later argument text cannot re-anchor it. `build_app.bat` accepts only no argument (install), `preflight`, or `buildcheck`; it refuses `drill` and every unknown/extra token. The destructive drill has only the fixed `rollback_drill.bat` entry point, and `verify.py` uses that wrapper. The test contract now correctly treats hostile syntax already parsed by an outer CMD shell as outside the batch file's security boundary.

### Round 3 evidence

- Exact remote implementation head verified and reviewed: `86c24368f3a48756a15a5a587891a94bfd9bbdfb`.
- Windows PowerShell 5.1 focused set (`tests/test_installer.py` + `tests/test_verify.py`): **80 passed**.
- The former Windows launcher failure is gone; ordinary unknown/extra tokens are refused before staging.
- All three re-anchoring cases are refused, including the case that previously selected `drill`.
- Real non-installing `build_app.bat buildcheck`: **BUILDCHECK_OK**, build `20261006-060630`, eight successful structured steps, zero failed steps, no stop attempt.
- Installed ZackBot remained byte-identical to accepted T03a: SHA-256 `72ABCA7CC25FC7859C93A62F4518AE3659A2C8B9B524F3ACA203798E769F430D`.
- GitHub fast checks passed; the required full check was still running when this review record was written.
- No normal installer, rollback drill, ZackBot stop/restart, trading action or credential operation was performed.

### Remaining gate

After the final GitHub full check passes, T03b requires a new explicit owner approval for the normal installer and rollback drill. The earlier T03a runtime authorization does not apply. Until then, do not install, drill, stop ZackBot, merge, or mark the ticket accepted.
