# T03b final acceptance

*2026-10-06 07:08 Cairo (Africa/Cairo). PR #5, accepted branch head before this evidence commit `b8dadb2b84faeeacf82fc26139841258a442d939`.*

## Verdict

**Accepted, pending required checks on this acceptance commit and protected merge.** The CMD-to-PowerShell installer rewrite, three fix rounds, Windows tests, GitHub checks, normal installation and rollback drill all passed. No blocking finding remains.

## Owner authorization and safety boundary

- The owner explicitly approved the T03b runtime gate in Claude chat at 06:13 Cairo: one normal installer and one rollback drill.
- Claude relayed that approval on PR #5 and held; Codex performed the only two runtime operations.
- Before each operation, the checkout was clean and exactly equal to the reviewed remote head. GitHub fast and full checks were green.
- Local configuration was `MODE=paper`; authenticated `/api/status` reported `PAPER`; reviewed `Engine` code maps every non-live mode to Binance Futures `TESTNET`.
- Runtime gate before the normal install: engine/exchange `ok`; zero errors, unprotected lots, untracked positions and orphan orders; four of four lots explicitly protected.
- The same fail-closed runtime gate passed again after the normal install and immediately before the drill.
- Mainnet mode, credentials, permissions, transfers and orders were not changed.

## Normal installation

- One attempt, build `20261006-064354`.
- Verdict `BUILD_DONE`; all 14 structured steps passed; no warnings; no rollback.
- Installed SHA-256: `203B155474942709B6AD40C3517BA32D058721664D245191C900068CBC4A60A6`.
- Previous accepted executable retained with SHA-256 `72ABCA7CC25FC7859C93A62F4518AE3659A2C8B9B524F3ACA203798E769F430D`.
- The installed file hash matched the structured record exactly.
- Installed source mirror: 120 expected files, 120 actual, zero missing, extra or changed.
- Desktop and Start-menu shortcuts both target the installed executable with the correct working directory.
- Authenticated post-install status reported build `20261006-064354`, PAPER, engine/exchange `ok`, four protected lots, and zero errors/unprotected/untracked/orphans.

## Rollback drill

- One attempt, deliberately failed candidate build `20261006-064800`.
- Candidate SHA-256: `244F4732B170DD0CD2688866A51C37993CB202764A6E45228AE9292B391A97A7`.
- Verdict `DRILL_PASSED`; all 14 structured steps passed as designed; rollback attempted and verified; no warnings.
- Restored build `20261006-064354` and SHA-256 `203B155474942709B6AD40C3517BA32D058721664D245191C900068CBC4A60A6`, byte-identical to the pre-drill executable.
- The restored build answered the build-specific authenticated HMAC check.
- Authenticated post-drill status again reported PAPER, engine/exchange `ok`, four protected lots, and zero errors/unprotected/untracked/orphans.

## Code and test evidence

- Review implementation head `86c24368f3a48756a15a5a587891a94bfd9bbdfb`: no remaining code finding.
- Windows PowerShell 5.1 focused installer/verification set: **80 passed**.
- Non-installing buildcheck: `BUILDCHECK_OK`, eight successful structured steps, no stop attempt.
- GitHub on review/status head `b8dadb2`: fast and full checks passed; PR was clean and mergeable before runtime execution.
- Earlier Windows complete suite on the rewrite: **269 passed**; every subsequent focused fix set passed except the intentionally rejected round-two launcher design, which was replaced and then passed in round three.

## Merge gate

Run required fast/full checks on this acceptance/status commit. If green and `origin/master` is still the accepted base `f9778ca9cfe8daac482c8a1fa1c0c1d53bdd4bfb`, squash-merge PR #5 through protected history, fetch, and verify local/remote equality. Then open T05 (fill telemetry). No additional installer, drill or bot restart is authorized.
