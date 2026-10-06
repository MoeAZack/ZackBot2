# T05b: exchange-outage resilience. Review request (local, rebased onto T03c)

## Executive summary

1. **What:** one shared, thread-safe outage circuit per Binance client. During a known outage, status checks (non-critical GETs) fail fast, with one probe per cooldown (2 to 60 s). Repeats coalesce into one Recent Activity incident, with one recovery line.
2. **Never skipped:** orders, an order's own `get_order` confirmation, a protective mark/position read, and cancels. `_order()` semantics are unchanged (idempotent client id; `AmbiguousOrder` → pending → `reconcile()`).
3. **Fail closed:** nothing is resized or closed on an unknown read. No new entries, grid adds or DCA/pyramid adds while the circuit is open. An unknown algo-stop state raises.
4. **T03c:** the exception proof's reads (`account`, `position_risk`, `open_orders_all`, `marks`, `margin_state`, `leverage_brackets`) fail fast during an outage, and every such failure REJECTS the above-cap exception. The 60 s add-pause re-check keeps adds paused on an unknown read.
5. **Logs:** signed query strings are scrubbed from exception texts AND from chained tracebacks. Fail-fast and leverage-cooldown messages are stable, so they coalesce.
6. **Alerts:** one Telegram "management failing" alert per cause per 15 min, so a flapping outage no longer sends one alert per flap.
7. **Evidence:** 53 outage tests (42 + 3 interaction + 8 final pass) plus the 182 T03c `test_leverage_auto` tests, all green. Mutation proof: 14/14, then 18/18, then 8/8 caught. Full suite: 594 passed, 2 skipped, 1 known env-only failure (`test_verify` provenance).
8. **Base:** `t05b-next` = T03c head `001a032` + the T05b merge (`3059cff`, one import-line conflict) + interaction tests (`c124a68`) + the final pass.
9. **Open questions:** see the end (thresholds, the alert window, orders bypassing the circuit, test-connection during an outage).
10. **Scope:** testnet only; no order-path change; panel changes are display-only (Exchange chip, Engine popup).

## Design (short)

**Circuit (`binance_client.ExchangeHealth`)**
- **States:** `ok` → `degraded` (a transient failure) → `outage` (3 in a row, or 30 s without success) → `ok` (any answer from Binance, a business error included).
- **Transient** = the `TRANSIENT` codes, 418/429/5xx, non-JSON 5xx, and network exceptions.
- **What drives it:** only status checks (non-critical GETs) drive the state and cooldown. Order, cancel and confirmation failures only count in `other_fails`.
- **Retry-After:** numeric seconds or an HTTP-date, clamped to a finite [0, 60]. It never raises in the order path.
- **Recovery:** sets `recovered` once. `ExchangeUnavailable` (a `BinanceError`, `kind='read'`) has a stable text; the seconds are in `.retry_in`.

**Incidents (engine)**
- `err(msg, key=None)`: a repeat of an open incident updates its one line (`… (x36 since 10:01:12 UTC)`). The key defaults to the exact scrubbed message.
- After a 15 min quiet gap a repeat starts a new line. Unkeyed incidents close after 15 min idle; keyed ones (`exchange-down`, `open-orders|SYM`) close only via `resolve()`. At most 200 are kept.
- The store is guarded by `INCIDENT_LOCK`, because the loop and HTTP threads both write.

**Engine behaviour**
- **Reconcile failure** from an unreachable Binance becomes one `exchange-down` incident in plain words ("no order was sent - positions and stops are left as they are").
- **Recovery:** the next successful reconcile (positions re-read) writes one recovery line, but not if the circuit re-opened during that same pass.
- **Last confirmed:** `health.confirmed.positions` and `confirmed.stops[sym]` move only on real reads. They show on the Exchange chip and in the Engine popup.
- **Blocked:** `entry_block`, `grid._can_add` and `_add_block` (DCA/pyramid) refuse while the circuit is open. Exits, stops and cancels are unaffected.
- **Manual stop move:** `_protective_mark()` uses a critical mark read, else a cache at most 120 s old, else refuses (the previous stop stays). The side check is never skipped.
- **Main loop** (`app.py`): mark-price, guard and equity-record steps route transient errors to `exchange-down` (no `main loop:` error, no 20 s sleep). Non-transient errors behave as before.
- **`/api/status` health:** `exchange_circuit`, `confirmed`, `incidents` (open ones, the live outage first).

## Review history

**Round 1 (draft):** 16 tests, then 42 after review. Mutation proof 14/14.

**Second internal adversarial pass (all fixed, mutation 18/18):**
- non-reads opened the circuit, so an orphan cancel starved position reads;
- `float(Retry-After)` on an HTTP-date escaped `_order`, skipping the client-id lookup;
- a manual stop move was refused because of a fail-fast mark read;
- the countdown in the fail-fast text prevented coalescing;
- one-off incidents stayed open forever;
- the guard step raised into the main loop;
- grid adds were allowed during an outage;
- `open_stop_tags` swallowed every algo error. It now swallows only `-5000`/`-1404`/`-404` "endpoint unsupported".

## Rebased onto T03c

- **Merge** `3059cff`: one conflict (the `binance_client.py` import line, resolved as the union of both).
- **Interaction tests** `c124a68` (`tests/test_t03c_t05b_interaction.py`):
  - the T03c reads fail fast with no traffic;
  - the exposure proof rejects with `check='snapshot'`;
  - an unknown leverage skips the entry and keeps exception adds paused (the flag is never cleared).
- **Final adversarial pass** (`tests/test_outage_final.py`, 8 tests; each fix reverted alone is caught, 8/8):

| # | Defect (confirmed by a failing repro) | Fix |
|---|---|---|
| R1 | `requests` chains the urllib3 error whose text holds the full signed URL (`timestamp`, `signature`). A failed panel action logs `traceback.format_exc()`, so the URL reached the log file and `/api/logs`. | `_req` re-raises a scrubbed exception of the same type with no cause/context (`_scrubbed`, `from None`). |
| R2 | A flapping outage (6 failures, 1 success, repeated) sent one Telegram alert per flap. | The same cause (the incident key, or the text) is not re-sent within `MANAGE_ALERT_REARM_S` = 900 s. A different cause still alerts at once. |
| R3 | DCA/pyramid adds were not blocked while the circuit was open (a pass can reconcile positions, then have its open-orders read open the circuit). | `_add_block` returns `OUTAGE_ADD` in an outage. |
| R4 | The same pass then logged "Binance answering again" although the circuit was open again, so lines flapped. | `_after_confirmed` does nothing while the circuit is `outage` (the incident and the `recovered` flag stay). |
| R5 | Incident eviction raced between the loop and HTTP threads (`KeyError` on `pop`, reproduced with 6 threads). | `INCIDENT_LOCK` (an RLock) around err/resolve/sweep; `pop(k, None)`. |
| R6 | A mark-price outage in the main loop went to an unkeyed `mark prices: …` line, not to the outage incident. | A transient error goes to `exchange-down`; others keep their text. |
| R7 | The main loop was not driven by any test (a known limit). | It is now: one real `App.loop()` pass covers the guard and equity steps during an outage (sleep 2, not 20) and checks that a non-transient error still raises. |
| R8 | (T03c) The leverage-cooldown skip text embedded a countdown ("no new request for {wait}s"), so it never coalesced. | The text is now "(no new request during the cooldown)"; the seconds stay in `lev_refusals[sym].cooldown_left`. No T03c test asserted the old text; the panel already reads `cooldown_left`. |

**Checked and found OK:**
- T03c fresh-snapshot reads under an outage: `account()` raising outside the snapshot `try` is still caught by `_leverage_fallback`, so the entry is rejected.
- The cached `_lev` path pops on an unknown read (it re-proves, never assumes).
- `/api/status` builds its incident view from snapshots, and incident dict keys are fixed after creation.
- The Telegram send logs only the exception type (the bot token sits in its URL).
- `flatten` closes go through `_positions_critical`. Its post-check read reports "could not verify" on failure.

## Known limits

- During an outage, `test_connection` shows "Binance reachable" from `sync_time` (not circuit-gated, and it does not close the circuit), then the account check fails fast with the outage text.
- The 60 s T03c add-pause re-check timestamp is consumed by a fail-fast read. After recovery the unpause can lag up to 60 s (fail closed).
- Critical `get_order` polling for a resting maker entry still goes out every pass, by design.

## Open questions for Codex

1. Should orders, confirmations, protective reads and cancels always bypass the circuit?
2. Thresholds: 3 failures / 30 s; cooldown 2 → 60 s; incident idle 15 min; alert re-send window 15 min per cause?
3. Should `test_connection` read the account with a critical read, so a user-requested check is never refused?

## Evidence

- Targeted (`test_outage`, `test_t03c_t05b_interaction`, `test_leverage_auto`, `test_outage_final`): 235 passed.
- Full suite (`tests/test_*.py`): 594 passed, 2 skipped, 1 failed (`test_verify` provenance: known, environment only).
