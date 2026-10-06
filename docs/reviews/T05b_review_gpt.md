# T05b exchange-outage resilience — Codex review

*2026-10-07 00:30 Africa/Cairo; exact behavior head `450f025cc619c73a6870b042c1100da4a32b2b73`*

## Verdict

**Code review clean; no open P1/P2 finding.** Apply `full-ready` once after fast/CodeQL pass on the final documentation
head. A build/install/restart and bounded testnet read-outage canary require the owner's separate confirmation after the
exact-head full gate passes.

## Safety review

- Only non-critical GET status reads drive and obey the circuit. Orders, cancels, client-id confirmation reads and
  protective position/mark reads continue to reach Binance; this is required so an outage cannot prevent protection,
  closing, orphan cleanup or ambiguous-order resolution.
- A confirmed outage blocks every new-exposure route: normal/manual entries, grids, DCA and pyramid adds. T03c account-wide
  proof reads fail closed, and an unknown leverage/add re-check never clears an exception flag.
- Reconciliation never interprets an unreadable position or algo-stop answer as empty. Existing lots, quantities and stop
  ids remain unchanged until a real read succeeds.
- The retry policy remains idempotent: POST orders are not blindly retried, and a possible order is resolved by client id.
  DELETE remains retryable/idempotent. `Retry-After` parsing is bounded and cannot escape the ambiguous-order path.
- Circuit and incident state are bounded and thread-safe on mutation. Repeated failures coalesce, recovery is recorded only
  after positions were re-read, and same-cause Telegram alerts are rate-limited without suppressing a different cause.
- Signed query strings and chained request exceptions are scrubbed before they can reach logs or `/api/logs`.
- The canary injector is exact-TESTNET-only, affects only validated non-critical GET path prefixes, is one-shot and bounded
  to 1–600 seconds, and cannot intercept writes, critical reads, MAINNET or look-alike hosts.

The proposed defaults—outage after three consecutive failures or 30 seconds, cooldown from 2 to 60 seconds, and 15-minute
incident/alert re-arm—are suitable conservative starting values. Tune them later from telemetry rather than exposing them
as user settings now.

## Evidence

- Git diff validation: pass; branch is based directly on accepted master `3eabdbc`.
- GitHub fast and both CodeQL analyses: pass on the behavior head.
- Official Windows focused gate: **377/377 passed in 31.22 s** across outage, final adversarial, T03c interaction,
  testnet-fault boundary, leverage, safety and grid suites.
- Review covered request retry classification, half-open transitions, protection/write bypasses, incident coalescing,
  reconciliation, add/entry gates, T03c interaction, log scrubbing, panel status and injector isolation.

## Non-blocking follow-up

`Test connection` currently performs an uncircuit-gated time read and then may fail-fast on the account read. A later UX
cleanup may make this explicit user-requested diagnostic use a critical account read so it always performs a real probe.
This does not affect automated safety or recovery behavior.
