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
