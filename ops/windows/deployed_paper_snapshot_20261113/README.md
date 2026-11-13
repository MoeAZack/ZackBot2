# Deployed PAPER operations snapshot — 13 November 2026

This review records the Windows launchers and watchdogs that were outside Git. It does not install or restart any trading instance. Files are captured from their deployed locations with exact SHA-256 hashes in source_inventory.json. No config.env, credentials, sessions, settings, state, logs, private acceptance receipts or shortcut binaries are included. Do not execute this snapshot from the repository: paths and source copies describe the current PC, not a relocatable installer.

## Running instances

| Instance | Port | Data folder beneath F:/ZackBot2_data | Process started, Cairo | Observed identity |
|---|---|---|---|---|
| Account 1 | 8765 | cohort_launch_20261108/runtime/ZackBot | 11 Nov 23:56 | Installed ZackBot 3.2 build 20261103-004246; owner PID6292, parent2512. Executable hash F373EBA5C39A60A1B18E71FBCB3991051DA6C21AEA610DEE0775C16501B5CC26; Git commit unproven. |
| Account 2 | 8766 | OLD3-account2/runtime/ZackBot | 12 Nov 07:56 | PID37428, dev build; 48 top-level source Python files match 640292a20527ab42d523f359462b30042d3066fe. Launcher overrides port and data root. |
| Account 3 | 8767 | ACCOUNT3-breakeven/runtime/ZackBot | 12 Nov 10:19 | PID40420, dev build; 49 top-level Python files match the source tree also present at 0547b96f. Separate protected-cohort helper and launcher are captured. |
| Account 4 | 8768 | EXPLORATION-account4/runtime/ZackBot | 12 Nov 14:33 | PID35112, dev build; 48 top-level Python files match 640292a. Additional H5 registration and read-only stop-probe helpers are captured. |
| Overview | 8769 | dashboard-hub | 12 Nov 08:28 | hub.py processes36984/40780; script hash and HTML snapshot captured. No separate trading book. |

All five loopback ports responded HTTP401 without credentials, proving reachable listeners. The four public /api/ping endpoints reported the build identities above. Safe session-owner PID/port/start fields and complete captured program-file hashes are in runtime_manifest.json; no token values are stored. A source-file match is not proof of dependencies, complete reproducible builds or bytes loaded by a process. Account1's source commit and all three dev build receipts remain unproven; do not infer mainnet readiness from this snapshot.

The external launcher paths are the corresponding folders shown here. The common watchdog is F:/ZackBot2_data/ZackBot-PAPER-watchdog.ps1; the account4 helper watchdog is inside EXPLORATION-account4. The task name is ZackBot-PAPER-Instance-Watchdog. Its exported definition has BootTrigger and LogonTrigger, S4U, and restart-on-failure every1minute up to10times. The redacted XML is review evidence, not an importable owner identity. No scheduled task definition was changed and no reboot drill was performed.

## H5 research retirement

The hourly H5 worker had appended eight input files during an attempt whose ADA fetch failed, before its result publication/cursor commit. On retry, validation compared the changed current files against the preceding cursor hashes and raised cursor_data_changed. This is a resumability failure, not proof that historical candles were revised.

The user authorized repair or retirement. The standalone H5 worker was retired; a RETIRED.json marker preserves the record and points to Claude's FWD-BOOKS H5, original freeze8f83494b and A1. The research guard now honors that marker and does not respawn H5. Only this research guard process was restarted; the four account processes and both other forward workers were not restarted. All original H5 rows, cursor, freeze, source and heartbeat hashes matched the pre-retirement capture. The two forward research tasks remain running. Their existing schedulers were not edited. Canonical H5 evidence is docs/research/fwd_books/rows, not the retired folder. FWD-BOOKS remains manually published by Claude; this does not turn it into an automatic service.

## Validation and limits

All captured Python sources parse without execution. PowerShell files are syntax-parsed without running their actions. The snapshot excludes prohibited credential/runtime-state files and is scanned for obvious literal keys/private-key material. Hashes match deployed sources; account settings/install/cohort hashes were unchanged during the audit and retirement. This is a draft traceability repair, not a trading-code merge, safety acceptance or deployment.

ROADMAP FIT: source-to-runtime traceability and the explicitly authorized duplicate research-worker retirement. DEFERRED: account source/settings changes, trading tests, native engine integration, reboot proof and mainnet.
