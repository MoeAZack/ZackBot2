# T04: Verification levels and CI. Review request

## Current state (authoritative)

| Item | Value |
|---|---|
| Updated | 2026-10-05 22:40 Cairo (Africa/Cairo) |
| Branch | `t04-ci`, on `master` `8b6641f` (T03 accepted + Codex owner overview); 0 behind `master` |
| Commits | `68faa88` verify.py + GitHub workflow + PC launchers; `c8a59c4` staging-safety fix; `17b7191`, `706fd33` review request; then this documentation update. (Earlier drafts of this file named `35d2efd` / `5ab2984`: those were the dev-sandbox ids before the branch was rebuilt on `8b6641f`; the content is the same.) |
| Code reviewed | `706fd33` for code; commits after it on this branch change **documentation only** (`docs/`). The exact head to review is named in the **READY FOR CODEX** pull-request comment. |
| Owner decision (2026-10-05) | GitHub runs `verify fast` on every push and `verify full` on pull requests into `master` and on demand. `verify release` runs on the PC. |
| Behaviour change | **None** to trading, engine, backtest, exchange or panel code: `git diff master` on those files is empty. The installer gains two **opt-in** modes plus an unattended flag. |
| GitHub evidence | Run #1 (push of `706fd33`): **verify fast PASS** (3 min 20 s), artifact `verify-fast-706fd33…`; verify full correctly not run on a push. Results of the pull-request run are in the PR comment. |
| Dev-sandbox evidence | `verify fast` PASS; `verify full` PASS; bare `pytest` 215 passed and 1 skipped (Windows-only); installer staging set 200 passed and 10 skipped |
| Pending | `verify full` on the pull request; Codex review; owner's `verify.bat full` on Windows (adds the build check; nothing installed, bot not stopped); then the owner protects `master` and the PR is merged |

## Review round 1 (Codex, `b884a79`, `T04_review_gpt.md`): dispositions

*2026-10-06 00:20 Cairo. All commits below are on `t04-ci`; the exact head for re-review is named in the **FIXED FOR CODEX** comment on PR #1.*

| # | Finding | Class | Disposition | Commits | Evidence |
|---|---|---|---|---|---|
| 1a | GitHub full: Trades calendar-day flow timed out | Confirmed defect (CI data) | **Fixed.** Root cause: `dev_out/seed_*.json` is gitignored, so a clean checkout had no closed trades to click. `verify full` now hands the harness the seeds that **replay 1 of the same run** wrote (fallback `dev_out/`; source recorded as `ui.seeds`). The flow and its assertion are unchanged. | `3bb5656`, `ab4be38` | Sandbox: no seeds → 168/171 (calendar timeout, reproduced); with seeds → 171/171. New test `test_ui_harness_gets_seed_history_on_a_clean_checkout` |
| 1b | GitHub full: mobile Signals +22 px, Research +12 px | Confirmed defect (responsive) | **Fixed.** Cause: segmented filter buttons (`.seg`, inline-flex, no wrap) are wider than 390 px with the runner's fallback font (DejaVu Sans; the PC and the sandbox have Segoe UI / Inter). One rule in the existing ≤640 px block: `.seg{flex-wrap:wrap;max-width:100%}`. The ≤1 px check is unchanged; the harness now also names the offending elements in the failure detail. | `e04ed2f` (harness detail), `6b14726` (CSS) | Sandbox with GitHub's font forced via fontconfig: before 161/163 (sig 22 px, res 12 px — identical to GitHub), after 163/163 quick and **171/171 full**; with the normal font also **171/171** |
| 2 | Manifest test fails on Windows (CRLF) | Confirmed defect (test) | **Fixed.** Fixture written with `write_bytes`. | `e785ac4` | **Owner's PC, `94d23d4`: `tests/test_verify.py tests/test_installer.py` → 30 passed in 23 s** |
| 3 | Release drill could stop a LIVE bot before proving testnet | Safety concern | **Fixed.** `release_steps`: read-only gate first (mode must be exactly `PAPER`, engine/exchange ok, every lot protected, nothing untracked, no orphans); if it fails the drill is **not started** and the run fails. After the drill the same gate must pass again (a bot that comes back LIVE fails). A LIVE status ends the wait immediately. | `2a77d45` (+ tests `e785ac4`) | Tests: `test_live_bot_blocks_the_drill_before_any_stop` (LIVE, degraded, unprotected lot, orphans, bot unreachable → drill never called), `test_paper_bot_is_drilled_then_checked_again`, `test_runtime_gate_requires_paper_mode` |
| 4 | full/release could PASS with `--skip-ui` / `--skip-replays` | Confirmed defect | **Fixed** by removing both switches: a named level always runs all its gates; the only skips left are Windows-only steps on other systems. Quick looks use pytest / the scripts directly. | `2a77d45` (+ tests `e785ac4`) | `test_full_and_release_have_no_skip_switches`: exit 2, no `latest_full.json` / `latest_release.json` written |
| 5 | Secret scan: root-only names, JSON skipped | Safety improvement | **Fixed (current tree).** Private file names at any depth; content scan of every text file ≤2 MB incl. JSON (market-data folders and gitignored local folders excluded); patterns: 64-char key-like strings, private-key blocks, Telegram bot tokens, DPAPI blobs. **Git-history scan stays in T04b.** | `2a77d45` (+ tests `e785ac4`) | `test_secret_scan_planted_files` (nested `config.env`, key in JSON, private key, Telegram token caught; checksums, fixtures, data folder, `dev_out` not flagged); the real tree scans clean |
| — | `git diff --check` whitespace noise in batch files | Cleanup (Codex: not a blocker) | Deferred to T03b (the installer moves to PowerShell there). | — | — |

Also fixed while testing: the two drill-gate tests waited 60 s per case in real time (+4 min on `verify fast`); they now use a fake clock (`94d23d4`).

**Verification by Claude for this round** (sandbox, Linux): UI harness 171/171 twice (GitHub font and normal font) on the fixed code; `test_verify.py` all pass except the provenance test, which needs pytest installed (the sandbox cannot install it - stated, not hidden; it passed on the PC). The official runs are GitHub fast + full on the new head and the PC run above.

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

- **`verify fast` takes about 3 minutes on 2 cores, not under 2.** 160 of its 177 s of tests are the seven core look-ahead and parity tests. They are also the installer's gate and must stay. `pytest-xdist` was measured and gave no gain, because the tests already saturate both cores. It was not added. (GitHub run #1: 3 min 20 s.)
- **The GitHub `verify full` cannot build the Windows exe.** The build and exe self-test run in the PC's `verify full` (`buildcheck`) and in the release drill.
- **How Claude reaches GitHub in this session:** through the owner's signed-in Chrome (reading branches, runs, PRs; writing documentation commits, the PR and comments). This session has no git push access, so documentation commits on this branch show the owner's account as author. Code changes still travel as a verified bundle (`zb_update.bat`).

## GitHub annotations seen on run #1 (not failures)

- **Node 20 deprecation:** `actions/checkout@v4`, `actions/setup-python@v5` and `actions/upload-artifact@v4` target Node 20; GitHub currently forces them onto Node 24.
- **Runner image:** `ubuntu-latest` moves to Ubuntu 26 from 2026-10-19. A new image can change system libraries that Playwright's `--with-deps` installs.
- **Proposal:** a small follow-up (T04b, CI maintenance): pin `runs-on: ubuntu-24.04` and move to the Node 24 releases of the three actions, proven by one green fast + full run. Not done inside T04, so the reviewed workflow stays exactly the one that passed. Codex: say if you would rather have it in T04.

## Owner's steps

1. ✅ `zb_update.bat` created `t04-ci` on the PC and pushed it (run #1, verify fast PASS).
2. ✅ Pull request `t04-ci` → `master` (opened by Claude through the owner's Chrome). It runs **verify fast** and **verify full**.
3. On the PC, run `verify.bat full` (about 40–60 min, including the build check; nothing is installed and the bot keeps running). `verify_release.bat` (about 60–80 min, the bot is stopped for about 1–2 min by the drill) only with the owner's go-ahead.
4. After Codex accepts and the PR is green: protect `master` as described in `docs/reviews/README.md` (require **verify fast** and **verify full**, no force-push, no deletion), then merge the PR.
5. On the PC afterwards: `zbgit.bat pull` so the local history includes the documentation commits made on GitHub.

## Rollback

- **Git:** close the pull request and delete or abandon the branch; `master` is unchanged.
- **Runtime:** nothing. The new installer modes only run when explicitly requested.

**Next ticket after acceptance:** T03a (the leverage-refusal fallback), now under CI.
