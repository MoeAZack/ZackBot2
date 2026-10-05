# T03: Installer rollback drill. Review request (round 2)

*2026-10-05, Cairo time. Claude. Please review before T04 (CI) starts.*

**Ticket basis.** The owner's verbatim T03 prompt was not retained in my session. I worked from:
- ROADMAP 0.4: "Rollback drill pending; needs a 'simulate failed launch' switch";
- the T03 row: "Deployment only";
- the 10 standing rules.

If the original prompt asked for more, please list it in the review.

## Git

| Ref | Commit | Notes |
|---|---|---|
| `master` | `f67d373` | Fast-forwarded to accepted T02 `2fc8642`, plus a roadmap commit (T02 ✅, T03 ◐) |
| `t03-rollback-drill` | **`b72b78b`** | One commit on master |
| Bundle | `zackbot_T03.bundle` | Heads `master=f67d373`, `t03-rollback-drill=b72b78b`; requires `2fc8642` |
| Import | `t03_git_sync.bat` | Pointer moves only; requires the files to be byte-identical; otherwise restores `t02-ui-baseline`/`ab4fb3e` |
| Manifest | `T03_tree.txt` | 156 tracked files at `b72b78b` |

The import was rehearsed on a copy of the PC's repo state:
- clean: ends on `b72b78b`, master `f67d373`;
- one changed file: detected and fully restored.

## Changed files

| File | Change |
|---|---|
| `app.py` (+3 lines) | `--simulate-failed-launch`: right after `--selftest` and before the port check, logs one warning and calls `sys.exit(3)`. Nothing else runs: no port, no `session.json`, no engine or exchange. Read in exactly one place; inert without the flag. |
| `build_app.bat` | **Drill mode** (`build_app.bat drill`). Every install also gets the **verified rollback** and the **reordered mirror and shortcuts**. Details below. |
| `rollback_drill.bat` (new) | Double-click wrapper for `build_app.bat drill`, because a double-click can't pass an argument. Excluded from staging. |
| `tests/test_installer.py` (new, 7 tests) | Details below. |
| `README.md` | Rollback behaviour and how to run the drill. |

### `build_app.bat` changes

**Drill mode** (`build_app.bat drill`):
- Refuses before touching anything if no `ZackBot.exe` is installed, or if the old build id can't be read.
- Otherwise runs the full normal pipeline: staging, pins, tests, build, new-exe self-test, verified backup, stop, swap, hash check.
- Launches the new exe **with the switch** and a 20 s ping. The ping must fail, so the rollback runs.
- **Passes only if** `ZackBot.prev.exe` is restored with SHA-256 equal to the pre-install exe **and** the previous build id answers the HMAC ping again.
- If the new exe answers despite the switch, the drill is reported **INVALID**.

**Verified rollback (all installs).** Before stopping the bot, the installer reads the previous build id from `ZackBot.prev.exe --selftest`. After any rollback it pings that build id with the same HMAC proof. The result is one of:
- `confirmed running`;
- `did NOT confirm` (shown to the owner);
- `not verified` (build id unreadable).

Before this, the rollback restarted the old exe but never proved it came back.

**Mirror and shortcuts after the confirmed launch (all installs).** The source mirror (`%LOCALAPPDATA%\ZackBot\src`) and the shortcuts are now updated only **after** the new build's ping succeeds. Before this, a rollback left the mirror describing the failed build.

### `tests/test_installer.py` (7 tests)

**All `.bat` files:**
- CRLF and ASCII;
- every `goto`/`call` target exists.

**`build_app.bat`:**
- the mirror and shortcuts come after the confirmed launch, and never on the rollback path;
- the switch reaches the exe only through `if "%DRILL%"=="1"`; exactly two launch lines, and the restore launch never carries the switch;
- the rollback order is restore → hash check → start → ping of the old build; the drill refuses early.

**`app.py`:**
- the real app module with the switch exits with code 3, binds nothing and writes no `session.json`;
- the switch is read in exactly one place, between `--selftest` and the port check.

**Fail-safe.** If the switch were ever broken, the test's app copy uses a private port, and App/engine start-up is replaced by an immediate error. It fails in under 1 s instead of serving on 8765 on the build PC. This was found by mutant M1, which originally ran a real instance for 180 s.

**Staging.** The installer runs the tests in a staging copy that deliberately excludes `build_app.bat`. The 3 `build_app`-specific tests skip there with a reason; they run in the repo (bare `pytest`, `run_checks`, CI). Without this skip, every build would have aborted. Proven by emulating the staging copy: **189 passed, 4 skipped** (3 new skips plus 1 that already existed).

### Mutation proof

Each deliberate defect was caught by its test:

| # | Defect | Caught by |
|---|---|---|
| M1 | Switch removed | Exits/no-bind test (now fails fast with `REACHED_APP_START`) and the inert-switch test |
| M2 | Mirror moved before the launch | Ordering test |
| M3 | Rollback label renamed | `goto`-target test |
| M4 | Switch passed on every launch | Drill-only test |
| M5 | Rollback ping of the old build removed | Rollback-order test |
| M6 | LF line endings | CRLF test plus 3 dependent tests |

After the mutation runs, the originals were restored byte-identical (hash-checked).

## Evidence (dev sandbox)

| Check | Before (`f67d373`) | After (`b72b78b`) |
|---|---|---|
| Full test suite | 192 passed | **199 passed** (192 + 7) |
| Installer fast set in emulated staging | — | 189 passed, 4 skipped |
| Strict replay 1 (24 steps) | 100%, median 0.017, p95 0.118, 2.81 pp, 0.03 pp, PASS | **identical** |
| Strict replay 2 (24 steps) | 99.7%, median 0.019, p95 0.096, 2.17 pp, 0.97 pp, PASS | **identical** |
| UI harness, full | 171/171 (T02) | **171/171** |

**Not runnable here:** `cmd.exe`. The batch logic was reviewed line by line, and is guarded by the static tests above. **The acceptance test is the Windows drill.**

## Owner's Windows drill (acceptance)

1. Run `C:\Dev\ZackBot2_git\t03_git_sync.bat`. Expected: `HEAD -> t03-rollback-drill` at **`6aa4405`** (round 2; see the end of this document), clean.
2. Double-click `C:\Dev\ZackBot2\rollback_drill.bat`. It takes about 6–10 min: tests about 4 min, build 1–3 min. **The bot is stopped for about 1 min during step 8**; exchange stops stay on Binance.

   Expected in the window: `ROLLBACK DRILL PASSED: the new build <new> failed its launch on purpose, and the installer restored build 20261005-103320 - same SHA-256 as before - and proved it is running again.`

   Expected in `%LOCALAPPDATA%\ZackBot\build.log`:
   - `ROLLBACK DRILL requested`;
   - `previous build 20261005-103320`;
   - `DRILL: launching the new exe with --simulate-failed-launch`;
   - `ping FAILED after 20 s`;
   - `ROLLBACK: ...`;
   - `restored exe sha256 ... equals the pre-install hash`;
   - `ping ok: version 3.2 build 20261005-103320`;
   - `ROLLBACK_RESULT verified=1 ...`;
   - `DRILL_PASSED ...`.

   Expected in `bot.log`: one line, `simulate-failed-launch: exiting before the panel/engine start`.
3. Check:
   - Settings still shows build `20261005-103320`;
   - `%LOCALAPPDATA%\ZackBot\src` is unchanged by the drill;
   - open lots are reconciled with their stops.
4. **Optional second exercise:** run `build_app.bat` normally. This installs the new build (T02 panel fix plus T03 installer) through the reordered success path. Expected: `installed and confirmed running`, and Settings shows the new build id.

## Unresolved risks

- **Very old installed exes:** before v3.2-rc2, an exe doesn't know `--selftest`. Asking it for its build id would briefly open the panel, and the rollback would be "not verified". The PC's build supports it.
- **The drill stops the testnet bot for about 1 minute.** Positions keep their exchange stops; the existing reconcile runs on restart.
- **Downtime during the drill's ping:** in a real failure, total downtime is about the 60 s ping plus the restore. In the drill it is 20 s plus the restore.

## Rollback of T03

- **Git:** `zbgit checkout t02-ui-baseline`.
- **Runtime:** nothing to roll back. The drill itself ends with the pre-drill exe installed (hash-verified).

**Next ticket after acceptance:** T04 (CI fast/full pipelines; moved before fill telemetry).


---

## Review 1: rejected. Fixed in commit `6aa4405`

### The finding

`rollback_drill.bat` failed twice at step 6 (builds `20261005-193306` and `-193735`):
- the new exe built and self-tested OK;
- `installer_check.ps1 hash` returned nothing, because `Get-FileHash` was unavailable in that launch environment and the `catch` was silent.

The installer failed closed: the bot, the exe, the source mirror, the positions and the stops were all unchanged.

### Correction from Claude

In chat I first read the `bot.log` lines at 19:27 (`simulate-failed-launch`) and 19:28 (restart) as the drill's rollback. **That was wrong.** Both drill runs stopped at step 6, before step 8 launches anything. Those two lines did not come from the installer.

### Root cause (best explanation)

In Windows PowerShell 5.1, `Get-FileHash` is a *script* function inside the `Microsoft.PowerShell.Utility` module, not a compiled cmdlet. A `PSModulePath` inherited from another shell, typically PowerShell 7, can make it unavailable. Today's 10:33 install, started differently, hashed fine.

The fix does not depend on this explanation being right:

| # | Fix | Detail |
|---|---|---|
| 1 | `installer_check.ps1` uses **no cmdlets at all**, only .NET and the language | **hash:** `IO.FileStream` with shared read and `SHA256`, upper-case hex; 5 × 1 s retry for scanner locks; a missing file fails at once. **ping:** `HttpWebRequest` plus regex, same build-id and HMAC rules. **Every failure** prints the exception, PowerShell version/edition and first `PSModulePath` entry to stderr. |
| 2 | `build_app.bat`: `set "PSModulePath="` | At the top, for that window only, so Windows PowerShell uses its own module paths for every PowerShell call (self-test JSON, stop, shortcuts). |
| 3 | `build_app.bat`: **checksum preflight** | Right after staging. A broken helper now fails in seconds, before the 4-minute tests and the build. |
| 4 | `build_app.bat`: `:hash` sends the helper's stderr to `build.log` | `2^>^>"%LOG%"` |
| 5 | **Integration tests** | Details below. |

**Integration tests (fix 5):**
- They run the real helper the way the installer does. On Windows that is `cmd /c powershell -NoProfile -ExecutionPolicy Bypass -File ...`; in dev it is `pwsh`.
- What they check:
  - the hash equals Python's SHA-256;
  - the hash is still right with a **PowerShell-7-style `PSModulePath`**;
  - a missing file → exit 1, reason on stderr, under 30 s;
  - the helper contains no cmdlet calls (static);
  - `ping` needs the right build id **and** HMAC proof (fake ZackBot on a private port; never 8765);
  - installer order: reset before the first PowerShell call, preflight before the tests, stderr logged.
- **They run inside the installer's own test step**: in staging, 194 passed and 5 skipped. A recurrence would now stop the build at step 4 with the reason, not at step 6.

### Evidence (dev sandbox, plus PowerShell 7.4.6)

| Check | Result |
|---|---|
| Old helper, with module auto-loading off | **Reproduces the reported symptom**: no output, exit 1, no reason |
| New helper, same conditions | Correct hash |
| New helper hash | Equals Python's |
| Missing file | Exit 1 in 1 s, with the reason |
| Ping: right build / wrong build / wrong proof / server down | Pass / fail / fail / fail |
| Mutants H1–H5 (old helper; PSModulePath reset removed; preflight removed; stderr not logged; ping ignores proof) | Each caught. Under `pwsh` the old helper's hash itself succeeds, because PS7 always finds its own modules; it is caught by the no-cmdlet, failure-reason and ping tests. The Windows-only `cmd → powershell.exe` tests are the exact reproduction. |
| Full test suite | **205 passed** (199 + 6) |
| Installer fast set in emulated staging | 194 passed, 5 skipped |
| Strict replays | Identical to baseline (see the owner's message) |

### Git

`t03-rollback-drill` = **`6aa4405`**, on master `f67d373`.

The bundle and `t03_git_sync.bat` were regenerated. One script handles both starting points:
- **update** from `b72b78b`, which is the PC's state;
- **fresh** from T02 `2fc8642`.

Both were rehearsed, plus a mismatch, which was correctly undone. Before handover, the PC files were checked against `T03_tree.txt`.

### ROADMAP

- T03 now records this rejection and its fix.
- New ticket **T03a: leverage-refusal fallback**, scheduled right after T03.
  - **Evidence:** Binance testnet returned `-1000` on *every* leverage change for SOLUSDT (1×) and XRPUSDT (3×) since 2026-10-04; every other coin succeeded first time. The current behaviour skips those signals, which is safe but makes the canary drift from the backtest.
  - **Proposal:** on refusal, read the coin's current leverage (read-only). Enter only if it is at or below the cap; otherwise skip as today. Count refusals per coin in the panel.
  - **Class:** E (order path). Tests and strict replays are required.

### Owner's re-run

1. Run `C:\Dev\ZackBot2_git\t03_git_sync.bat`. Expected: "starting point: update", ending at `6aa4405`, clean.
2. Double-click `rollback_drill.bat`. Expected in `build.log`:
   - `checksum helper ok` right after the build id;
   - then the full drill sequence listed above, ending with `DRILL_PASSED`.

   If the helper fails again, `build.log` now contains the exact reason.

---

## Round 2: Windows test runner (commits `6741ee3`, `24ff38b`)

### What happened

The owner's drill stopped at step 4 (`4 failed, 190 passed, 5 skipped`). The installer log already showed **`checksum helper ok`**, so the round-1 fix works in the real launcher.

The 4 failures were my new helper tests. They passed `['cmd', '/c', 'powershell ... -File "<path>" ...']` as a Python list, Python escaped the inner quotes, and PowerShell got a path containing literal quote characters. Nothing was backed up, stopped, swapped or rolled back; the running build stayed `20261005-103320`.

### Fix: the reviewer's recommendation, both parts

**1. Helper tests on Windows call PowerShell directly:**
`[powershell, -NoProfile, -ExecutionPolicy, Bypass, -File, installer_check.ps1, ...]`. No `cmd` string is built in Python.

**2. Batch-level integration uses the real batch file.** There is a new non-destructive mode, `build_app.bat preflight`:
- it uses its own staging folder (`staging_preflight`) and its own log (`build_preflight.log`);
- it stages the source, runs the checksum-helper check, prints `PREFLIGHT_OK` and **exits before step 2**;
- it never pauses: a failure exits with code 1, so a test can't hang.

`test_build_app_preflight_from_cmd` (Windows only):
- runs `cmd /d /c build_app.bat preflight` twice, the second time with a PowerShell-7-style `PSModulePath`;
- requires exit 0 and `PREFLIGHT_OK`;
- requires the **real** `build.log` and staging folder to be untouched, and `staging_preflight` to be removed.

A static test pins the preflight rules on every platform:
- own staging folder and log set before the first log write or cleanup;
- exit before step 2;
- no pause on failure.

Mutants caught: the exit removed, preflight pointed at the real staging folder, and the pause path restored.

**3. One test fix.** `drill refuses early` was anchored on "the first 40 lines" and broke when the file grew. It is now anchored on "before step 1", which is stricter.

### Evidence (dev sandbox)

| Check | Result |
|---|---|
| Installer tests | 14 passed, plus 1 skip (Windows-only `cmd` test) |
| Full suite | **206 passed, 1 skipped** |
| Emulated staging | 194 passed, 7 skipped |
| App, engine, helper | No change since `6aa4405`, so its strict replays (identical to baseline) still apply |

### Faster check on the PC

`C:\Dev\ZackBot2_git\check_installer_tests.bat` runs only `tests/test_installer.py`, including the real batch preflight, in about 30 s. Run it before the 6–10-minute drill.

### ROADMAP (per this review)

- **Order:** T03 → **T04 CI** (`verify fast/full/release`, one machine-readable summary per run) → **T03a** (an order-path change, so only under CI) → **T03b** (installer logic moved into a structured PowerShell script, with tiny `.bat` launchers) → T05 → T06–T09. No broad clean-up mixed into tickets.
- The private GitHub repository is recorded as a recommendation for the owner to decide, with the exclusions and the secret scan listed.

### Git

`t03-rollback-drill` = **`24ff38b`**, on master `f67d373`.

`t03_git_sync.bat` was rewritten. It accepts a fresh start from T02 `2fc8642`, or an update from any earlier T03 commit (`b72b78b`, `6aa4405`, `6741ee3`). The update from the PC's `6aa4405` was rehearsed, plus a mismatch, which was correctly undone.

### Owner's steps

1. `t03_git_sync.bat`: expect "starting point: update (6aa4405…)", ending at `24ff38b`, clean.
2. `check_installer_tests.bat`: expect all installer tests to pass, **including `test_build_app_preflight_from_cmd`**.
3. `rollback_drill.bat`: expect `ROLLBACK DRILL PASSED`.
4. `build_app.bat` normally: installs the new build. Then verify the positions and stops again.
