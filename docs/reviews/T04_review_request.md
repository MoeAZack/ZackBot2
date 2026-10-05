# T04: Verification levels and CI. Review request

## Current state (authoritative)

| Item | Value |
|---|---|
| Branch | `t04-ci`, on `master` `8b6641f` (T03 accepted + Codex owner overview) |
| Implementation | `35d2efd`, plus the staging-safety fix `5ab2984` and this document |
| Owner decision (2026-10-05) | GitHub runs `verify fast` on every push and `verify full` on pull requests into `master` and on demand. `verify release` runs on the PC. |
| Behaviour change | **None** to trading, engine, backtest, exchange or panel code: `git diff master` on those files is empty. The installer gains two **opt-in** modes plus an unattended flag. |
| Dev-sandbox evidence | `verify fast` PASS; `verify full` PASS; bare `pytest` 215 passed and 1 skipped (Windows-only); installer staging set 200 passed and 10 skipped |
| Pending | First GitHub Actions run on push (owner pushes); owner's `verify.bat full` on Windows (adds the build check); review; then the owner protects `master` |

## What T04 adds

| File | What |
|---|---|
| `verify.py` | **One runner, three levels, one summary per run.** Details below. |
| `.github/workflows/verify.yml` | Details below. |
| `build_app.bat` | **`buildcheck`:** steps 1–6 (staging, pins, tests, PyInstaller, exe self-test, hash) in its own `staging_buildcheck` folder and `build_check.log`, then `exit /b 0` **before step 7**: nothing is backed up, stopped or installed. **`ZB_NOPAUSE`:** every `pause` is skipped, so `verify.py` can drive the installer unattended. The normal and drill paths are otherwise unchanged. |
| `verify.bat`, `verify_release.bat` | PC launchers. They use `uienv` (which has Playwright) or fall back to `buildenv`, and set the fixed Chromium path. `run_checks.bat`/`.sh` now just call `verify full`. |
| `tests/test_verify.py` (7) | Details below. |
| `tests/test_installer.py` (+2) | `buildcheck` exits after the self-test and hash, before step 7, with its own staging and log, and failures don't pause. Every `pause` honours `ZB_NOPAUSE`. |
| `README.md`, `docs/reviews/README.md`, `ROADMAP.md` | The verification table; merging via a pull request; **branch-protection steps for the owner**; T04 ◐. |

### `verify.py` levels

| Level | Checks | Where it runs |
|---|---|---|
| **fast** | Every `.py` compiles. No private files (`config.env`, `session.json`, `state.json`, `settings.json`, `trades.csv`) and no 64-character key-like strings in the tree; hex checksums and fixtures are excluded, and planting a fake key is caught. Every data file matches `DATA_MANIFEST.json`. Tests with `-m "not slow"`. Installer `preflight`. | Linux: GitHub on every push. Windows: the PC. |
| **full** | The same static and dataset checks. **All** tests. **Both strict replays at 24 steps** (gate and thresholds from `replay.STRICT`, unchanged). The **full UI harness**. On Windows, `build_app.bat buildcheck`. | GitHub (Linux, no build check) and the PC (Windows, with build check). |
| **release** | Full checks, plus the **rollback drill** (which builds, self-tests and does a verified restore), plus a **read-only reconciliation** of the running bot via its authenticated `/api/status`. | The Windows PC only. |

**The reconciliation requires all of these:**
- engine `ok` and exchange `ok`;
- no unprotected lots, and every lot `protected`;
- no untracked positions;
- no orphan orders.

After the drill's restart it waits up to 180 s for the price feed to warm up, polling every 10 s and reporting the last state. It never relaxes a condition.

**Summary file:** `<out>/<level>_<Cairo-time>.json` and `latest_<level>.json`. It records:
- commit, branch and dirty flag (normal clone, GitHub, or the PC's external `history.git`);
- build id;
- dataset manifest SHA-256 and the missing or changed files;
- the versions of every pinned dependency;
- JUnit pass/fail/skip counts;
- replay metrics (matched %, median and p95 |dR|, return gap, DD gap, position mismatches, gate);
- the UI check count and evidence path;
- the exe SHA-256 from `buildcheck` or the drill;
- Cairo start and finish times.

**Exit code:** 1 if any step fails. Skipped steps are listed with the reason.

### GitHub workflow (`verify.yml`, `actionlint` clean)

- **Jobs:** `verify fast` runs on every push and pull request. `verify full` runs on pull requests into `master` and through **Run workflow**, and depends on fast.
- **Environment:** Python 3.14, as on the PC, with pip cache; the `verify full` job also installs the pinned UI requirements and Chromium.
- **Outputs:** artifacts `verify-<level>-<sha>` (summary plus evidence, kept 30 days).
- **Safety:** `permissions: contents: read`; a superseded run on the same ref is cancelled.

### `tests/test_verify.py` (7 tests)

- Replay metrics are parsed from real output lines.
- A gate `FAIL`, missing metrics or a non-zero exit fails the step.
- JUnit counts are read correctly, and **zero collected tests is a failure**.
- One failed step makes the exit code 1 and `passed: false`, with Cairo time and offset.
- The manifest check catches a **changed** and a **missing** file (self-contained).
- Provenance fields come from a real `main()` run. This test skips in staging, which has no data folders.
- `release` is refused off Windows.

## Evidence (dev sandbox, Linux, 2 cores)

| Run | Result |
|---|---|
| `verify fast` | **PASS** in about 3:00. Fast tests 202 passed and 1 skipped. Preflight skipped (Windows only). |
| `verify full` | **PASS** in 19:24 (21:16:58 → 21:36:22 Cairo). Details below. |
| Bare `pytest` | **215 passed, 1 skipped** |
| Installer fast set in an emulated staging copy | **200 passed, 10 skipped**, the skips being the repo-only checks |

**`verify full` breakdown:**
- tests: 209, all passing except 1 skip (before `test_verify.py` existed);
- **replay 1:** 100%, 0.017, 0.118, 2.81 pp, 0.03 pp, PASS;
- **replay 2:** 99.7%, 0.019, 0.096, 2.17 pp, 0.97 pp, PASS. Both are identical to the T02/T03 baselines;
- **UI:** 171/171;
- build check skipped (Windows only).

**Found and fixed during T04:**
- **A summary bug:** pytest's `skipped` count collided with the step's own skip flag, so a step that ran was reported as SKIPPED. The flag was renamed, and the counts are now nested. Covered by the counts test.
- **A staging trap:** the provenance test runs the real dataset check, but the installer's staging copy has no data folders, which would have blocked every install. The test now skips there with a reason, and a self-contained manifest test runs everywhere instead.

## Limits, stated rather than hidden

- **`verify fast` takes about 3 minutes on 2 cores, not under 2.** 160 of its 177 s of tests are the seven core look-ahead and parity tests. They are also the installer's gate and must stay. `pytest-xdist` was measured and gave no gain, because the tests already saturate both cores. It was not added.
- **The GitHub `verify full` cannot build the Windows exe.** The build and exe self-test run in the PC's `verify full` (`buildcheck`) and in the release drill.
- **This session cannot trigger or read GitHub Actions.** The first CI results come from the owner's push: GitHub → **Actions** → **verify**.

## Owner's steps

1. `zb_update.bat` creates `t04-ci` on the PC (files are already identical) and pushes it. That triggers **verify fast** on GitHub.
2. Open a **pull request** `t04-ci` → `master` on GitHub. That triggers **verify fast** and **verify full**.
3. On the PC, run `verify.bat full` (about 40–60 min, including the build check). Optionally run `verify_release.bat` (about 60–80 min, including the drill; the bot is stopped for about 1 min).
4. After acceptance and a green PR: protect `master` as described in `docs/reviews/README.md` (require **verify fast** and **verify full**, no force-push, no deletion), then merge the PR.

## Rollback

- **Git:** delete or abandon the branch; `master` is unchanged.
- **Runtime:** nothing. The new installer modes only run when explicitly requested.

**Next ticket after acceptance:** T03a (the leverage-refusal fallback), now under CI.
