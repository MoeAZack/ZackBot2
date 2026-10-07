# TRATE: rate-limit hazards (HTTP 429 under the engine lock, HTTP 418 IP ban). Review request

Branch `rate-limit-hazards`, base `874cfd8` (same tree as protected master `3b86a03`). TESTNET only. This is the smallest safe fix. The full order budget and priority queue come later in **T10**.

## Findings (both confirmed on the base commit)

Reproduced on `874cfd8` with the `tests/test_outage.py` fake client and a patched `time.sleep`:

| | Scenario | Base behaviour |
|---|---|---|
| H1 | `cancel()` (DELETE) gets HTTP 429 with `Retry-After: 30` | 4 network calls, **90 s slept** inside `_req`. `manage()` holds `engine.lock` during the orphan loop and `_cancel_or_park`, so stop restoration, flatten and panel actions wait ~90 s. |
| H2 | `cancel()` gets HTTP 418 with `Retry-After: 7200` | `retry_after()` clamps to 60 s, so the client **retries 3 times while banned** (30 s sleeps). Every retry and every later request (a POST order was still sent) can extend the ban. Non-critical reads also re-probe through the outage circuit about every 60 s. |

## Fix

`binance_client.py`
- `RateLimitBan(ExchangeUnavailable)`: code `-418`, `kind='ban'`, `.retry_in`, and a stable message with no countdown (lines coalesce). It is transient for `is_transient()` / `engine._exchange_down()`.
- `ban_seconds(headers)`: reads the **raw** Retry-After (seconds or HTTP-date). The result is always finite and within `[0, BAN_MAX_S = 3 days]`. A huge value or `inf` gives 3 days. A missing value, garbage, `nan` or `-inf` gives `BAN_DEFAULT_S = 60`. It never raises.
- `ExchangeHealth.ban(seconds)` / `banned(count=True)` keep `ban_until` on the circuit clock. A later, shorter answer never shortens a ban. The snapshot gains `ban_s` (seconds left), `bans` and `ban_refused`, so they show up in `/api/status` → `health.exchange_circuit`.
- `_req`:
  - **Ban gate first, for every method.** While banned, `RateLimitBan('... not sent')` is raised before the circuit check and before any network call.
  - **418 answer: never retried.** The ban is recorded and `RateLimitBan` is raised at once. It is never `AmbiguousOrder`, because Binance refused the request. An order POST refused by the gate never left the client, so it is a definite failure. `_order()` only turns `RequestException` / `AmbiguousOrder` into lookups, so a `RateLimitBan` passes straight through with no client-id lookup and no resend.
  - **429 (or code -1003/-1015) is bounded.** At most `RATE_LIMIT_SLEEP_MAX = 2 s` is slept in total per `_req` call. If the next wait (Retry-After, or backoff) would go over the bound, the call fails fast now with `BinanceError(-1003)`. A short Retry-After (≤ 2 s) is still honoured, so the existing `test_get_retries_429_with_backoff` is unchanged. The same 2 s bound applies to critical reads (`get_order` confirmation, critical positions/marks).
  - Order POST behaviour is unchanged: `retry=False`, one attempt; a 429 on a POST is a definite `BinanceError`; 5xx stays `AmbiguousOrder` and is confirmed by client id.

`engine.py`
- `RATE_BAN` message and `_down_why(ex)`. A `RateLimitBan` gives the single keyed incident **`rate-ban`**; anything else stays `exchange-down`. Callers that do not pass the error (`exchange_down_incident(where)` from the app's guards / equity / connect steps) are classified from a ban still recorded on `self.trade` / `self.data`.
- `manage()` reconcile failure uses `_down_why(e)`.
- `_after_confirmed()` also runs for an open `rate-ban`. It closes it with ONE line, `Binance IP ban over - positions re-read and reconciled, stops re-confirmed`, under the same rule as exchange-down: positions and stops must be re-read first.

`app.py`
- The main-loop mark-price failure uses `e._down_why(ex)`.
- The "account readable" recovery (no lots) also resolves `rate-ban`.
- The other call sites are untouched (state-based classification), so the existing source-string tests and stubs still hold.

### T05b ExchangeHealth semantics: unchanged
Only non-critical GETs drive the outage circuit. A 418 answer still calls `health.fail(..., read=read)` as before, so a banned status read counts toward degraded/outage exactly like any busy answer. The ban is a **separate gate in front of** the circuit; it does not change state, cooldown or probes. Requests refused by the gate are not counted as circuit failures or probes, so the circuit is not driven to outage by our own refusals, and no probe goes out during the ban. `snapshot()['state']` is deliberately left alone, which means `entry_block` / `_add_block` do not treat a ban as an outage. That is not needed for safety: every entry/add path needs a request, which is refused unsent (definite error, nothing opened).

## Tests: `tests/test_rate_limit_hazards.py` (12, all pass)
- 418 with Retry-After 7200 makes exactly 1 network call and sleeps 0. Over t = 1…7199 s, 28 attempts (status read, critical positions/marks, `get_order`, cancel, `open_stop_tags`, leverage POST) make **zero** network calls. The snapshot shows `ban_s`/`bans`/`ban_refused`. At t = 7201 requests resume.
- `open` / `close` / `stop` during the ban raise `RateLimitBan` (a `BinanceError`), **never `AmbiguousOrder`**, and send no POST and no lookup.
- A 418 answer to an order POST is definite: a single POST, no lookup, no resend.
- Retry-After parsing: `nan`, `-inf`, garbage, empty, `inf`, `1e400`, `9e18`, negative and HTTP-date (future/past) are all finite and bounded, also through `_req`. A later shorter ban never shortens the recorded one, and a huge ban is capped at 3 days.
- A 429 on DELETE (Retry-After 30 / 3 / none) sleeps ≤ 2 s in total (0 s and 1 call when Retry-After is beyond the bound). It never drives the circuit and is not a ban.
- A 429 on a status read fails fast; a 2 s Retry-After is still honoured.
- Critical `get_order` still resolves through a short 429 (one POST). With long 429s the lookups fail fast, and `_order`'s own 1..4 s spacing still confirms (one POST, no 30 s sleep).
- **Engine, real client:** an orphan DELETE gets 429 with Retry-After 30 in a manage pass that also has a `stop_dirty` lot. Total sleep is ≤ 2 s, the **stop POST is sent in the same pass** (`stop_id` updated, not dirty), the old stop and the orphan are kept for retry, and no exchange-down incident opens.
- **Engine, 418:** 51 manage passes over 400 s make 1 network call in total and sleep 0. Lots are unchanged, there is ONE `rate-ban` activity line (count 51) and no exchange-down. When the ban ends, positions and open orders are re-read and there is exactly ONE "IP ban over … stops re-confirmed" line, never repeated.
- Steps that do not pass the error (guards/equity) coalesce into `rate-ban` while banned and go back to `exchange-down` after the ban.
- App main loop: a mark-price `RateLimitBan` goes to the `rate-ban` incident.

## Mutation check: 15/15 caught
no ban gate · 418 handled as plain busy (retried) · ban clamped to 60 s (`retry_after` instead of `ban_seconds`) · NaN not defaulted · huge not capped · POST during ban raised as AmbiguousOrder · 429 bound removed · bound raised to 60 s · slept budget not accumulated · later ban shortens (`min`) · engine never uses `rate-ban` · `rate-ban` never resolved · recovery not triggered by `rate-ban` · state-based lookup ignored · app mark-price site keyed `exchange-down`.

## Existing suites (mini-runner, file by file, each < 2 min)
test_outage 42 ✓ · test_outage_final 8 ✓ · test_testnet_faults 47 ✓ · test_t05b_startup_outage 13 ✓ · test_t03c_t05b_interaction 3 ✓ · test_safety 78 ✓ · test_fills 46 ✓ · test_leverage_auto 185 ✓ · test_grid 14 ✓ · test_v31_engine 45 ✓ · test_ci 36 ✓ · test_telegram 23 ✓ · test_verify 16 ✓ / 1 ✗ (`test_summary_has_the_required_provenance_fields`: known env-only failure, fails identically on the base).

## Limits / not changed (by design, for T10 or a follow-up)
- **5xx / network backoff is unchanged.** A DELETE or critical read that gets **503 with Retry-After 30** still sleeps up to 3×30 s under the lock (the same shape as H1, but not a rate limit). Non-critical reads are protected by the circuit (outage after 3). This is recommended as a follow-up, or as part of T10.
- **No 429 cooldown.** After a fail-fast 429 the next pass (~8 s) may send again. If Binance escalates to 418, the ban gate now stops everything at once. A real request-weight budget (`X-MBX-USED-WEIGHT-*`) and priority (protective orders first) is T10.
- **`_order` confirmation loop unchanged.** If a POST was ambiguous (5xx) and a ban starts during its lookups, the lookups fail fast but the loop still spaces them 1+2+3+4 s, then raises `AmbiguousOrder(tag)`. That is the correct outcome: the order may exist, so the lot goes pending and is settled from the position later. Short-circuiting it was not done, because a short ban could end inside the loop and still confirm.
- **The ban is per client instance and in memory.** `self.trade` (testnet) and `self.data` (mainnet market data) are different hosts anyway. The app's public `PUB` client learns its own ban on its first 418. A restart clears the ban (at most one more 418, which re-records it).
- `sync_time()` (only on -1021 or at construction) and mainnet-only `api_restrictions()` bypass `_req` and are not gated. Neither can run during a ban in normal operation.
- No panel change. The panel colour still follows `exchange_circuit.state`. The ban is visible as the `rate-ban` incident line and in `exchange_circuit.ban_s`.

## Relation to T10
TRATE removes the two acute failure modes: lock-held sleeps on 429, and retrying into a 418. It adds no budget, queue or priority logic. T10 should replace the fixed 2 s bound and the per-call view with a shared weight/order budget that reserves capacity for protective orders, and should also take over the 5xx Retry-After sleeps noted above.
