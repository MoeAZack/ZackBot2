# T03b: Installer logic from CMD to PowerShell. Review request

## Current state

| Item | Value |
|---|---|
| Branch | `t03b-installer-powershell` (opened by Codex at a1963f1, from master f9778ca) |
| Head for review | the PR head. The full SHA is in the READY FOR CODEX comment |
| Class | Deployment only. No trading, engine, panel or API change |
| Runtime | **Nothing installed, no drill run, ZackBot untouched.** A real install plus the rollback drill needs separate owner approval |

## The change

- **`installer.ps1` (new):** the whole installer as PowerShell functions.
  - The steps are the same: stage, build id, checksum preflight, venv + pinned pip, libs, safety tests, PyInstaller, self-test, hash, verified backup, old build id, stop, swap (5 tries), hash check, launch, HMAC ping, mirror and shortcuts after the confirmed launch, verified rollback, drill report.
  - The modes are the same (`install`, `drill`, `preflight`, `buildcheck`) with the same staging folders, logs and markers (`build id`, `new exe sha256`, `PREFLIGHT_OK`, `BUILDCHECK_OK`, `BUILD_DONE`, `ROLLBACK_RESULT`, `DRILL_PASSED`, `BUILD_FAILED: <same reasons>`), so `verify.py` is unchanged.
  - Like `installer_check.ps1`, it uses **no cmdlets** (only .NET and the language). A foreign PSModulePath cannot break it (the T03 lesson), and a test enforces this.
- **`build_app.bat`:** now a launcher of about 10 code lines. It clears PSModulePath, then runs `powershell -NoProfile -ExecutionPolicy Bypass -File installer.ps1 <mode>` (no argument means `install`). It pauses only if `installer.ps1` itself could not run. `rollback_drill.bat` is unchanged (`build_app.bat drill`).
- **Real error handling:**
  - One `Stop-Install <reason>` path, caught once.
  - **New:** an *unexpected* exception after the old bot was stopped runs the verified rollback instead of leaving the bot down. Under CMD this case had no handler.
  - An unknown mode is refused (exit 2). CMD silently treated it as a normal install.
- **Testable:**
  - Every outside effect (processes, copies, the checksum/ping helper, stopping the bot, shortcuts, pauses) is one small function.
  - `tests/test_installer.py` dot-sources `installer.ps1 -NoRun`, replaces those functions with recording fakes in a temp LOCALAPPDATA, and **executes the real flow**. This replaces the T03 tests that pinned CMD line order.
- **Kept as is:** `installer_check.ps1` (only its header comment changed). The checksum preflight still runs the staging copy of the helper before anything else.
- **Structured JSON log (from the handoff):** each run writes `build.json` / `build_preflight.json` / `build_check.json` next to the readable log. It is rewritten after every step, so a crash keeps the steps done so far. Fields:
  - `schema`, `mode`, `started` / `finished` (Cairo, ISO with offset), `source_dir`, `log`;
  - `build_id`, `new_exe_sha256`, `previous {build, sha256}`, `bot_stopped`, `installed_after {build, sha256}` (new build on success, restored old one after a verified rollback, otherwise null);
  - `steps[]` with `{step, ok, started, finished, detail}`: drill_precheck, stage, checksum_helper, build_env, libs, safety_tests, pyinstaller, selftest_new, hash_new, backup, stop, swap, launch, mirror, shortcuts, rollback. A failed step's `detail` is the exact BUILD_FAILED reason;
  - `warnings[]`, `rollback {attempted, verified, restored_sha256, note}`, `verdict` (BUILD_DONE / BUILD_FAILED / DRILL_PASSED / PREFLIGHT_OK / BUILDCHECK_OK), `reason`, `exit_code`.

  It has no keys, tokens or settings (a test enforces this). It is written by a small serializer, because `ConvertTo-Json` would be a cmdlet. A failed JSON write is logged and never blocks the install.
- **Cleanup:** the CMD `goto` and label maze is gone. The staging copy now also excludes `installer.ps1`.

## Tests (`tests/test_installer.py`)

New flow tests (PowerShell 7 here and on the GitHub runner; Windows PowerShell 5.1 on the PC):
- install succeeds; order stage < tests < build < self-test < backup < old self-test < stop < swap < launch < ping < mirror < shortcuts;
- first install without a previous version;
- a silent new build rolls back: restore, hash, start, ping old; no mirror or shortcuts; exact BUILD_FAILED reason;
- a restore copy that fails is refused (hash mismatch), so the old exe is never started blind;
- a swap that keeps failing rolls back after exactly 5 tries;
- no previous version: the failed exe is removed;
- drill: the launch carries `--simulate-failed-launch` with 20 s; PASS only when the old build is proven running; INVALID if the new exe answers; FAILED if the old one does not answer;
- drill refuses before step 1 without an installed version, and refuses before stopping without the old build id;
- preflight: only stage + checksum, own log, staging removed, nothing else touched;
- buildcheck: builds and self-tests, never stops, backs up, swaps or mirrors;
- 9 parametrised failures before the swap (stage, checksum helper, pip, libs, tests, PyInstaller, self-test, backup, stop): old exe untouched, never started, exact reason;
- an unexpected crash after the stop restores the previous version;
- pauses happen only for a person and never with ZB_NOPAUSE; preflight and buildcheck never pause, even on failure;
- an unknown mode is refused;
- no fakes: the real process runner starts the real helper (paths with spaces) and the hash matches Python; a missing file returns '' and logs the reason;
- JSON record: success (all 14 steps ok, Cairo timestamps, hashes, installed_after = new); rollback (launch fails, rollback verified, installed_after = old); warnings; drill, preflight, buildcheck and crash records; 9 parametrised failures each name the failed step with detail == reason; no secrets;
- one-attempt rules: exactly one new launch, one new ping, one build and one test run per mode;
- path quoting: `ConvertTo-ArgString` output splits back (CommandLineToArgvW rules) into the original arguments (spaces, trailing backslashes, quotes, empty, `;.` add-data);
- Windows PowerShell 5.1 syntax guard: no `??`, `?.`, `&&`/`||`, ternary, `-Parallel` or `clean` blocks;
- bare-pytest safety: every process-starting, stopping, shortcut or pause function is replaced in the fake world, so bare pytest cannot stop the bot or start an exe.

Static: the launcher stays a launcher (at most 10 code lines, PSModulePath cleared before the single PowerShell call, the only pause is guarded); `installer.ps1` is ASCII and cmdlet-free; the staging copy excludes the installer and private files. Unchanged: CRLF/ASCII .bat, labels, `--simulate-failed-launch` app tests, helper tests, and the Windows-only real `build_app.bat preflight` through cmd.exe.

**Mutation proof:** each of these breaks at least one test:
- skip the new-build ping (6 tests fail);
- drop the drill switch (1);
- skip the restore hash check (1);
- remove the unexpected-error rollback (1);
- unguarded pause (1);
- mirror before the stop (4);
- ignore failing safety tests (2);
- quoting without doubling trailing backslashes (1);
- no verdict written on failure (10).

One mutation is not detected on its own: removing the failed-step marker in `Stop-Install`. `Set-Verdict` marks the open step failed with the same reason as a second line of defence, so the record stays correct.

## Evidence so far (Claude sandbox: Linux, PowerShell 7.4.6, stand-in pytest runner, NOT official pytest)

- `tests/test_installer.py`: 52 passed, 1 skipped (the Windows-only cmd.exe preflight).
- Simulated staging copy (no `build_app.bat` / `installer.ps1`): only the app/helper tests run, and the installer tests skip as intended (re-run below).
- Full `tests/`: 250 passed, 1 failed (`test_summary_has_the_required_provenance_fields`, which needs real pytest: environment-only, known).
- `verify.secret_scan`: clean.

Pending (official): GitHub verify fast/full. A Windows targeted run (`tests/test_installer.py` under Windows PowerShell 5.1, plus `build_app.bat preflight` and `buildcheck`) needs owner approval. A real install plus `rollback_drill.bat` needs separate owner approval (stop condition).

## Questions for Codex

1. Is rolling back on an unexpected exception after the stop the right call (vs. failing and leaving the bot down as CMD effectively did)?
2. Refusing an unknown mode (exit 2) instead of installing: OK?
3. Windows PowerShell 5.1 specifics I can't run here: `ProcessStartInfo` quoting (CommandLineToArgvW rules, tested here), `System.Management` for the source-run app.py stop, and `WScript.Shell` via `[Activator]`. Please run the flow tests under 5.1 on the PC.
