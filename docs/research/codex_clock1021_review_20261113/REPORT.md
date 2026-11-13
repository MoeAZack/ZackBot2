# CLOCK-1021 independent review — exact head 6dbb1db5

Reviewed head: 6dbb1db59dbedbced7eb599ad7183bdfc336d0cd. Base: 9cb4341f2d94c21434492515b8e85f2b42c148ce. **REQUEST CHANGES** for the issues below. No merge, deployment, account access, real key, runtime or scheduler action.

## Independent positive checks

The separately written harness uses real client methods on an entirely fake HTTP transport and fake clock. It does not import Claude's test fixtures. Stops, market closes and classic-to-algo stop fallback survive three definitive -1021 refusals then succeed with recvWindow 6000/10000/20000/30000 ms. The classic client order ID or algo client ID stays identical through each retry. Each resync uses two 3-second-timeout samples. Market open and the actual extracted engine maker method make exactly two POST attempts at 6000/6000 ms; exhausted requests are ClockRejected, with no ambiguous lookup or blind resend.

18,648 independently enumerated non-1021 cases match master in outcome, primary request fields, attempts and sleeps. They cover sequences of success, string-success, business error, busy, rate limit and network failure across GET/DELETE/POST, signed/unsigned, retry on/off. This comparison holds the offset fresh. Periodic stale-offset refresh intentionally adds public time requests and therefore is not literal old behavior for every stale-clock path.

The F01-pinned _create_lot source is exactly equal to master. Its source-segment hash is in independent_result.json; that hash depends on extraction/indent conventions and is not claimed to be another tool's differently normalized pin.

## Required corrections

1. **Alert failure interrupts safety handling (blocking).** engine.py:4135 calls _clock_alert before setting stop_dirty. _clock_alert calls err and notify without containing their exceptions. The real notify starts a thread outside its send-error try block. Injecting thread-start failure into the actual extracted notify method causes _replace_stop to raise RuntimeError with stop_dirty still false, rather than returning False. That bypasses _create_lot's immediate `if not _replace_stop: close_lot` path. The same unguarded call can mask the original close rejection at engine.py:4441. Mark safety state first and make clock telemetry best effort; preserve the original close exception. Add hostile err/notify failures proving the safety-close branch still runs.
2. **Close-only recovery leaves a permanent keyed incident.** Successful _replace_stop is the only new resolution at engine.py:4146–4147. A close refused once, then successfully retried, can close the final lot while clock-1021 stays open indefinitely; keyed incidents do not expire. The independent _market_close reproduction closes quantity to zero and leaves the incident open. Resolve it on the appropriate confirmed recovery, with a hostile last-lot-close test. This should not require changing the F01-pinned _create_lot.
3. **Provisional stops miss the clock alert.** _prov_set, engine.py:7510–7570, catches exhausted ClockRejected in its generic error path but never invokes _clock_alert. The independent first-provisional-stop rejection leaves the quantity unprotected and records only `prov|...`, not `clock-1021`. Include this protective-order path in the named incident, with best-effort telemetry and ownership/idempotency unchanged.
4. **The 20-second deadline is checked before resync only.** In binance_client.py's -1021 branch, a resync can cross the deadline and the next request is still sent. With legal 3-second /time samples and 3-second signed responses, retries start 6, 15 and **24 seconds** after the first rejection. Check the deadline again before sending after resync; do not advertise a hard 20-second retry bound from this head. Clarify whether remaining budget also caps the in-flight request timeout. Keep same-ID semantics.

## Windows gate

`verify.py full` is running serially on this exact head and then exact master, using separate app-data/output folders and the same Windows interpreter/dependencies. Buildcheck is the non-installing mode; no account folders or running bot ports are used. Full verdict/failure-set comparison will be appended when both finish. The Linux 39 failures / 124 errors is a reported baseline, not a Windows result.

ROADMAP FIT: confirmed stop/close execution-integrity defect. DEFERRED: unrelated engine work, installation, merging, accounts and mainnet.
