# T04 Codex review

*Reviewed 2026-10-05 23:15 Cairo (Africa/Cairo). Target: remote branch `t04-ci` at `b884a790845fc90d15a69133313950c90378fb4f`, based on `master` `8b6641f440e826bc50d2271a28f09b92fec76d2d`.*

## Verdict

**Changes requested.** The overall fast/full/release structure is useful and the change stays out of trading code, but the exact handoff is not ready for the owner's Windows full run or acceptance. The required GitHub full check is red and three additional release-gate defects are confirmed below.

## Findings

### P1 — the required GitHub full check failed on the exact handoff

GitHub run `37364698491` for PR merge commit `e60bb98` (head `b884a79`) completed with **168/171** UI checks, so `verify full` failed. The failures were:

- mobile Signals tab: 22 px horizontal overflow;
- mobile Research tab: 12 px horizontal overflow;
- Trades calendar-day flow: click timed out after 30 seconds.

The unit tests and both strict replays passed. This is not an Actions-host acquisition incident: the job ran and the verification gate itself failed.

**Required fix:** diagnose each result from the uploaded UI summary/screenshots, fix real responsive defects, and make the calendar flow deterministic without weakening its assertion. If a result is platform-specific, preserve the same user-facing requirement and remove the environmental nondeterminism. Re-run the pull-request full check to green.

### P1 — the new verification tests fail on Windows

`tests/test_verify.py::test_manifest_check_catches_missing_and_changed_files` writes the fixture with `Path.write_text()`, which translates `\n` to CRLF on Windows, but hashes the LF-only byte string. The supposedly unchanged `ok.csv` is therefore reported as changed.

**Evidence:** on the owner's PC, the exact reviewed commit produced `1 failed, 23 passed` for `tests/test_verify.py tests/test_installer.py`; the failing assertion showed both `data/ok.csv` and `data/changed.csv` in `changed`.

**Required fix:** write the fixture as exact bytes (or otherwise make the hash and file bytes identical across platforms), add the regression expectation, and rerun the targeted Windows tests. Do not ask the owner to run `verify.bat full` until this is green.

### P1 — release verification can stop a live-mode bot before proving testnet

In `verify.py`, the release path calls `installer_mode(rep, 'drill', ...)` before `reconcile_step(rep)`. The drill stops and restarts ZackBot. The later reconciliation records `mode`, but its pass condition never requires `mode == 'PAPER'`. A healthy `LIVE` instance could therefore be stopped by the drill and still receive a passing reconciliation.

**Required fix:** perform a read-only pre-drill runtime gate that requires the authenticated status to report exactly `PAPER`, along with the existing engine/exchange/protection/orphan checks. If that gate fails, do not call the drill. After a successful drill, repeat the reconciliation and require `PAPER` again. Add a test proving a mocked `LIVE` status prevents `installer_mode(..., 'drill', ...)` from being called.

### P1 — mandatory full/release gates can be skipped while the run still reports PASS

`--skip-replays` and `--skip-ui` create `skipped` steps, and `Report.finish()` treats every skipped step as success. Consequently a command named `verify full` or `verify release` can omit required replay/UI gates and still print `VERIFY FULL: PASS` or `VERIFY RELEASE: PASS`. This contradicts the stated level definitions and the module claim that gates are never weakened.

**Required fix:** a named full or release run must not pass when a mandatory gate is omitted. Remove the skip switches from those levels, reject the combination, or mark the run incomplete/failed with a non-zero exit. If partial diagnostic runs are useful, give them an explicitly non-release name and ensure their summaries cannot be mistaken for `latest_full.json` or `latest_release.json`. Add regression tests for both skip switches.

### P2 — secret scanning has a documented coverage gap

The private-file check only checks five filenames at the repository root, while the content scan skips every `.json` file. A nested `config.env` or a differently named JSON credential file can therefore be committed without this check detecting the filename or its contents. It also does not scan Git history.

**Recommendation:** either extend the current check to tracked files at any depth and scan small JSON files outside the market-data/evidence directories, or explicitly scope this check as a lightweight guard and make the stronger current-tree plus history scan part of T04b. Add planted fixtures so the advertised behavior is testable.

## Evidence reviewed

- Exact remote branch/head and clean local fast-forward were verified.
- Complete diff from `origin/master` was inspected, including `verify.py`, the GitHub workflow, installer modes, launchers and tests.
- `git diff --check` found line-ending/trailing-whitespace noise in the changed batch files; this is cleanup, not a release blocker.
- Targeted Windows command on the owner's PC: `tests/test_verify.py tests/test_installer.py` → **1 failed, 23 passed**.
- GitHub `verify fast` for the handoff commit passed. GitHub `verify full` then **failed**: 168/171 UI checks, with two mobile overflow failures and one calendar-flow timeout. Its 215/216 test result (one skip) and both strict replays passed.

## Re-review gate

Claude should post `FIXED FOR CODEX` with a new full commit SHA and evidence for:

1. targeted Windows tests passing;
2. tests proving LIVE mode blocks the drill before any stop/restart;
3. tests proving full/release cannot pass with required gates skipped;
4. all three GitHub UI failures resolved without relaxing the checks;
5. refreshed GitHub fast/full results for the new head, both green.

The owner's long `verify.bat full` run remains deferred until these code-level findings are fixed.

---

## Re-review round 2

*Reviewed 2026-10-06 00:17 Cairo (Africa/Cairo). Target: remote branch `t04-ci` at `494d12d4064ac2f31ebdc8e56d8d04a5062c698a`; last code commit `94d23d4`.*

### Verdict

**Changes requested: one safety-gate defect remains.** The round-1 fixes for the Windows fixture, mandatory gates, current-tree secret coverage, UI seed data and mobile overflow are implemented and their targeted Windows tests pass. The release pre-drill gate now checks PAPER before the drill and again afterwards, but its safety-schema validation is fail-open.

### P1 — an incomplete or errored health response still authorizes the rollback drill

`runtime_ok()` uses falsey checks such as `not h.get('unprotected')`, `not h.get('untracked')` and `not h.get('orphans')`, while `lots` defaults to an empty list. Missing safety fields therefore look identical to explicitly safe values. It also records `health.errors` but does not require the list to be empty.

Confirmed on the exact reviewed code:

- `{mode: PAPER, health: {engine: ok, exchange: ok}}` → `runtime_ok(...) == True`;
- the complete healthy shape with a non-empty `health.errors` list → `runtime_ok(...) == True`.

That means a truncated/incompatible status response, or a bot reporting recent engine errors, can authorize the step that stops and restarts ZackBot. The unattended runtime gate requires zero errors and must fail closed when any required safety field is absent or malformed.

**Required fix:** validate the complete status shape and exact safe values before the drill. At minimum require:

- `mode == 'PAPER'`;
- `health.engine == 'ok'` and `health.exchange == 'ok'`;
- `health.errors` is present and empty;
- `health.unprotected` is present and empty;
- `health.untracked` is present and empty;
- `health.orphans` is present and exactly zero;
- `lots` is present as a list and every lot explicitly has `protected is True`.

Add planted tests for missing `lots`, each missing health field, non-empty errors, malformed values and a lot without the `protected` key. Every case must prevent `installer_mode(..., 'drill', ...)` from being called.

### Round-2 evidence

- Exact remote head and full round-1 fix diff were inspected.
- `git diff --check 860e0b6..494d12d` is clean.
- Owner's Windows build environment, exact head: `tests/test_verify.py tests/test_installer.py` → **30 passed in 23.45 s**.
- Direct planted status checks reproduced both fail-open cases above.
- GitHub fast/full for `494d12d` are queued during the reported hosted-runner outage; no green result is claimed.

### Next re-review gate

Post `FIXED FOR CODEX` with the new SHA, the fail-closed schema tests, the targeted Windows result, and green GitHub fast/full results. The long Windows `verify.bat full` remains deferred.
