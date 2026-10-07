# T05b exchange-outage resilience — Codex review

*2026-10-07 00:30 Africa/Cairo; exact behavior head `450f025cc619c73a6870b042c1100da4a32b2b73`*

## Verdict

**Changes requested — one confirmed P1 runtime finding. Do not merge.** Static review and the focused/full gates passed,
but the approved exact-head testnet canary found that a process which starts during a Binance read outage cannot recover
without another process restart.

### Fix review at `6a07f72` (03:22 Africa/Cairo)

The P1 design is now addressed: the same engine retries transient start-up failures with bounded jitter/backoff, observes
the circuit's probe floor, keeps one incident open until positions and open orders were re-read, and does not retry an
authentication refusal. The new focused Windows set passed **112/112**.

**P2 — the supposedly capped retry calculation overflows after a long outage.** `_connect_failed()` evaluates
`5.0 * 2 ** (n - 1)` before applying `min(60.0, ...)`. At `n=1025` this raises `OverflowError: int too large to convert to
float`, confirmed directly with the exact expression. Once reached, retry scheduling itself fails and the engine can no
longer recover automatically. At the 60–72 second cap this is reachable after roughly 17–20 hours of continuous start-up
outage. Cap the exponent/count before exponentiation (or use a branch once the cap is reached) and add a regression test
with a very large persisted/current retry count. Re-run the focused set; the runtime canary can wait for that small fix.

### P1 — startup outage permanently leaves the engine disconnected

At 03:23:58 Africa/Cairo, installed build `20261007-031714` was restarted in PAPER mode with the exact-TESTNET-only
`ZB_TESTNET_FAULTS=read_outage:180` injector. Startup reconciliation immediately drove the shared circuit to `outage`
after three injected HTTP 503 / `-1007` reads, but `App.start_engine()` caught `eng.connect()` once, stored the error and
returned. `App.loop()` only works when `e.connected` is already true and `e.error` is empty, so it made no further status
read, probe or reconnect attempt. After 39 seconds the observable state was still `engine=stopped`, `exchange=error`,
`circuit=outage`, `probes=0`, `recoveries=0`, with no open incident. Because the injector window starts on an eligible
read and the stopped engine makes no more eligible reads, merely waiting past 180 seconds cannot recover the process.

Required fix: add a bounded, backoff-controlled reconnect path when initial `connect()` fails. It must reuse the shared
circuit/probe rules, never infer empty positions/stops from a failed read, coalesce one `exchange-down` incident, and only
mark recovery after positions and protective orders have been re-read successfully. Add a regression test that starts the
real app/loop with the first reconciliation unavailable, advances beyond the injected window, and proves automatic
recovery without restarting the process. Then repeat this exact runtime canary.

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
- Exact-head installer passed all eight stages and installed build `20261007-031714` before the canary. The injected run
  sent no new entry and reported zero unprotected, untracked or orphaned positions. It was stopped after the startup-retry
  defect was confirmed. A clean restart without the injector restored `engine=ok`, `exchange=ok`, circuit `ok`, and all
  three original lots with identical quantities, stop prices and stop ids.

## Non-blocking follow-up

`Test connection` currently performs an uncircuit-gated time read and then may fail-fast on the account read. A later UX
cleanup may make this explicit user-requested diagnostic use a critical account read so it always performs a real probe.
This does not affect automated safety or recovery behavior.
