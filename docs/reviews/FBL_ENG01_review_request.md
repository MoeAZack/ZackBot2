# FBL-ENG01: routine exchange verification of protective stops. Review request (local, on T05a head a0dfee7)

Fable audit ticket FBL-ENG01, severity **High**, issue #13. TESTNET only. Branch `fbl-eng01-stop-verify`. Not pushed.

## Finding

`engine.reconcile()` reads a symbol's open orders only when the position has **shrunk**
(`if have >= expected - tol: continue` comes before `open_stop_tags(sym)`). A stop that disappears while the position stays
the same size is never noticed. Examples: a stop cancelled by hand on Binance, or a stop that EXPIRED when it triggered
instead of filling. The panel and Telegram then keep showing "✓ stop on Binance". This lasts until the position changes size
or the app restarts, and a restart does not fix it either. T05b's `_stops_reconfirmed()` runs only after an outage, and it only
reports `stop-unseen`. It changes nothing.

## Design

### 1. When the verifier runs (`Engine.verify_stops`)
One budgeted entry point is called from three places:

| Trigger | Where | Rule |
|---|---|---|
| startup full pass | first `manage()` after start | nothing is scheduled yet, so every held symbol is due |
| every pass (routine) | `manage()`, after `reconcile()` and `_after_confirmed()`, before the per-lot loop | a symbol is read only when its next-due time has passed |
| candle close | `cycle()`, right after `reconcile()` and **before any entry decision** | `min_age=10 s`: re-read unless read in the last 10 s |
| after an order action | `_replace_stop` success (entry stop, trailing move, add/partial-close resize, restore, manual move) | brings the symbol forward to now + `STOP_SETTLE_S` (3 s), but **never sooner than `STOP_ACTION_MIN_S` (30 s) after that symbol's last read** (review D4) |
| after an outage | `_stops_reconfirmed()` sees a recorded stop missing | the symbol is due in the same pass |

Every successful open-order read, from any of the three readers (verifier, reconcile's shrink path, T05b recovery), counts as
**positive evidence** (`_stops_seen`). Each lot whose recorded stop is listed gets `stop_confirmed_t = now`. Its miss counter
is cleared. Its `stop-missing|<lot>` incident is closed. `stop-unseen|SYM|SIDE` is closed too, once every lot on that side is
listed.

### 2. Request-weight budget (the rationale)
- **Per held symbol:** one `GET /fapi/v1/openOrders?symbol=` (weight 1) plus one `GET /fapi/v1/openAlgoOrders?symbol=`
  (weight 1). Both reads go through the new `Futures.open_stop_orders(symbol)`. One read covers every lot on that symbol, both
  sides.
- **Frequency:** about once every `STOP_VERIFY_S` = 60 s, with a fixed per-coin jitter of ±20 % (period 48–72 s; the phase
  is `crc32(symbol)`). Held coins drift apart instead of bursting together. Runs stay deterministic, which T05a's
  `test_cycle_never_changes_trading_when_every_audit_function_raises` needs (a random jitter made it flaky).
- **Cost:** with N held symbols the routine cost is about 2N weight per minute. For 10 symbols that is 20/min, against
  Binance's 2400/min IP limit, which is under 1 %. A candle close adds at most one extra read per symbol (four timeframes
  closing together still give one read, because of the 10 s floor).
- **Order actions (review D4):** a stop that moves on every 8 s pass (a trailing runner) used to pull a re-read to +3 s on
  every pass: 38 reads in 10 minutes for that coin. The pull-forward is now floored at last read + 30 s, so the worst case is
  about one read per 32 s (19 reads in 10 minutes, about 4 weight/min per trailing coin). 10 coins all trailing is about
  40 weight/min, under 2 %. Placement itself is already a confirmation (Binance returned the id), so nothing is lost.
- **Why per-symbol and not account-wide:** `openOrders` without a symbol costs **40**. Per-symbol reads are cheaper up to
  about 20 held symbols. They also fit the per-symbol failure handling already used for `open-orders|SYM`.
- **Only when a stop is missing:** one order-status lookup by id (`GET /fapi/v1/order` or `/fapi/v1/algoOrder`, weight 1).
  Then, only right before a restore, one **critical** `positionRisk` read (weight 5).
- After a miss or an unknown read, the symbol is re-checked after `STOP_RECHECK_S` = 10 s, not 60 s. This keeps the
  unprotected window short.
- **No reads at all:** in dry mode, when not connected, or while the T05b circuit is in `outage`. A read that fails fast,
  or fails as transient, gives **no conclusion**: nothing is counted, changed or alerted. A non-transient failure gets the
  existing keyed `open-orders|SYM` incident.

### 3. Fresh confirmation for "protected" (`stop_confirmed_t`)
- New lot field `stop_confirmed_t` (epoch seconds). It is set when Binance **accepts** a stop (`_replace_stop` success: the
  order id is known) and when the stop is seen in an open-order read.
- **Migration:** on load, every lot gets `stop_confirmed_t = None`. A proof from before the restart is not fresh evidence,
  so this is deliberately not kept. `stop_miss` defaults to 0 and **is** kept, so a coin stays blocked until it is
  re-verified.
- `Engine.stop_view(lot)` returns `protected`, `stop_state` and `stop_note`. `protected` is true only when the state is
  `confirmed`: a recorded, clean stop **and** a confirmation within `STOP_FRESH_S` = 300 s (5 missed routine reads).
  The other states:
  - `placing`: no stop id yet, or a dirty lot;
  - `missing`: a miss is recorded;
  - `unverified`: never confirmed in this process;
  - `stale`: last confirmed more than 5 minutes ago.
- The panel (`app._stop_view`: lot rows, Unprotected chip, safety list) and Telegram (`TelegramControl._protected`) use the
  same rule. If the view raises, the lot is shown as not protected (fail closed). The panel shows `stop_note` as the reason
  text or tooltip. `/api/status` health gets the `stop_verify` counters (reads, lookups, unknown, misses, restored,
  adopted, extras_cancelled, deferred).

### 4. A missing stop: fail closed, restore with race protection
For each lot on the symbol that has a recorded stop and no pending order:

1. **Listed** → confirmed (see §1).
2. **Dirty lot** (a replace already failed) → skipped. `manage()` already retries `_replace_stop` every pass.
3. **Placed less than `STOP_LIST_GRACE_S` (10 s) ago and not listed** → *not* a miss (delayed visibility). It is re-checked
   in 10 s.
4. Otherwise it is a **miss**: `stop_miss += 1`, and the stop is looked up directly by its id (`stop_status`):
   - `NEW` → it is live, only listed late. It is confirmed and the miss is cleared.
   - `CANCELED`, `EXPIRED`, `REJECTED` or `EXPIRED_IN_MATCH` (**gone**) → eligible for restore now.
   - `FILLED`, `PARTIALLY_FILLED`, `TRIGGERING`, `TRIGGERED` or `FINISHED` (**fired**) → no restore. Reconcile books the
     stop once the position shows smaller. It becomes eligible only at the **3rd** consecutive miss with the position still
     intact (the fired status was not reflected in the position).
   - Unknown (no lookup, not found, or the lookup failed) → eligible only at the **2nd consecutive** miss.
   - **Algo stop (`a:`/`ac:`) with an unknown status** (review D2) → **never** auto-replaced. From the 2nd miss there is a
     keyed `stop-missing|<lot>` alert ("status cannot be read - not replaced automatically ... check it on Binance") with one
     Telegram message. The coin stays entry/add-blocked. A readable gone status (CANCELED/EXPIRED...) still restores it.
   - **Algo endpoint "unsupported" while holding an algo stop** (review D2): `_stop_rows` calls
     `open_stop_orders(sym, strict_algo=True)` whenever a lot on the coin holds an `a:`/`ac:` stop. A 404/-1404/-5000 then
     raises and the read is **unknown** (no miss), instead of "no algo orders". This prevents restoring a classic stop next
     to a live algo stop.
5. **Restore** (`_restore_missing_stop`):
   - (a) **Adopt** a bot stop that no lot owns, on the same side, with the same qty (±½ step) and the same stop price
     (±½ tick). This covers a placement whose answer was lost. Nothing is placed.
   - (b) Otherwise, **re-read the position critically right before acting.** If it is smaller than recorded, the stop
     probably triggered: nothing is placed and reconcile books it (`deferred`). If the read fails, nothing is placed
     (unknown).
   - (c) Position intact → one keyed incident `stop-missing|<lot key>` (one Telegram alert per incident), `stop_dirty = True`,
     then the existing **stop-first** `_replace_stop`: the new stop is placed first, then the old id is cancelled (or parked
     as an orphan). If that fails, the lot stays dirty and `manage()` retries every pass. A -2021 sets `force_close` (exit at
     market). The incident closes when the new stop is seen listed.
6. **Never two live stops.** "Extras" are open orders whose client id has the **exact** `new_cid` shape
   (`^z[ab][0-9a-f]{22}$`: `zb` classic, `za` algo; review gap), whose type is `STOP_MARKET`/`STOP`, which no lot owns, and
   which are **not queued in `state['orphans']`** by tag, `c:<clientId>` or `ac:<clientId>` (review D1). Queued orphans are
   never adopted, neither directly nor on the restore path, and never cancelled by the verifier: the orphan sweep owns them.
   The verifier places a fresh stop instead (fail closed). They are cancelled (keyed `stop-extra|SYM|tag` line) only
   when **every** lot on that symbol has its own stop listed in the same read, so the bot never cancels down to zero stops.
   They are also kept while Binance holds more than the lots on that side (`untracked` / `over_seen`), because that stop may
   be protecting the untracked size. Orders with other client ids (manual or web orders) are **never** touched.

### 5. Entry / add block, exits untouched
- `entry_block` (automatic, Take now, trailing, maker re-check, manual) and `_add_block` (DCA, pyramid) return the stable
  text `STOP_MISSING_BLOCK` = *"protective stop missing on Binance for this coin - restoring it; no new entries or adds until
  it is confirmed"* while any lot **on the coin** (either side) has `stop_miss > 0`. The block lifts when the stop is seen
  again, adopted, or replaced (a stop accepted by Binance clears the miss).
- Exits (`close_lot`, `_market_close`, flatten, take profits, `force_close`) never consult it.

### 6. What did not change
- T05a observe-only hooks: untouched.
- T05b circuit, incident and recovery semantics: unchanged. `_stops_reconfirmed` still only reports. It now also feeds
  positive evidence and schedules the verifier.
- `reconcile()` decision logic: unchanged. It only records the positive evidence of its own read.
- Dry mode: no reads, no orders. `stop_view` shows `unverified`, never protected.

## Files
- `binance_client.py`:
  - `open_stop_orders(symbol)`: detailed rows, with the same "unknown algo ≠ none" contract as before;
  - `open_stop_tags` is now derived from `open_stop_orders` (same requests, same errors);
  - `stop_status(symbol, tag)`: `o:`/`c:` → `/fapi/v1/order`, `a:`/`ac:` → `/fapi/v1/algoOrder`. "Not found" or an
    unsupported endpoint returns None (unknown); a transient failure raises.
- `engine.py`:
  - constants `STOP_*` and `BOT_STOP_CIDS`;
  - lot migration;
  - verifier state (`clock`, `_stopv`, `_stopv_last`, `stopv_stats`);
  - `_stops_seen`, `_stop_rows`, `_stop_lookup`, `stop_missing_on`, `stop_view`, `_stopv_period`, `_stopv_due`,
    `verify_stops`, `_verify_symbol`, `_adopt_stop`, `_restore_missing_stop`;
  - hooks in `manage`, `cycle`, `reconcile`, `_stops_reconfirmed`, `_replace_stop`, `entry_block`, `_add_block`.
- `app.py`: `_stop_view` (lot view) and the `stop_verify` health counters. `telegram_ctl.py`: `_protected`. `panel.html`:
  reason text and tooltips from `stop_note`, and the Unprotected dialog wording.
- `tests/test_stop_verify.py` (new, 42 tests: 30 original + 12 from review round 1).
- Two existing tests updated: `test_t05b_startup_outage._prot` and `test_testnet_faults` (canary window). Their "lots
  unchanged" comparisons now exclude `stop_confirmed_t`, which a successful re-read refreshes. No order is involved, and
  quantities and stop ids are still compared exactly.

## Tests (`tests/test_stop_verify.py`, 42; the round-1 additions are listed in the review section above)
- **Restart and startup:** after a restart all lots are `unverified` and not protected (engine and panel). The first pass
  makes exactly one read per held symbol and both become protected.
- **Freshness:** after more than 300 s without a successful read the lot is `stale` and not protected (panel and Telegram),
  but it is **not** missing. Nothing is restored or blocked. It is protected again after the next good read.
- **Budget:**
  - over 10 simulated minutes of 8 s passes, 8–13 reads per symbol;
  - the per-coin periods stay inside ±20 % and differ between coins;
  - a candle close is skipped when the symbol was read 5 s earlier and re-reads it at 11 s;
  - a stop move is re-verified on the next pass.
- **External cancel:**
  - the lookup by id returns CANCELED, the stop is restored once, and there is one keyed incident and one alert;
  - the incident closes when the new stop is listed;
  - 20 more passes place and alert nothing.
- **Unknown status:**
  - the first miss places nothing, and the lot shows `missing`;
  - entries are blocked on the coin (both sides, manual too), adds are blocked, other coins are unaffected;
  - the second consecutive miss restores the stop.
- **Exits:** a close still works while the stop is missing.
- **Failed restore:** the lot stays dirty and blocked. `manage`'s retry then places exactly one stop.
- **EXPIRED-on-trigger and fired stops:**
  - FILLED with the position closed: no restore, and reconcile books it as `stop`;
  - EXPIRED with the position already reduced (the critical re-read catches the race): deferred, nothing placed, no alarm;
  - EXPIRED with the position intact: restored;
  - FILLED with the position intact: restored only at the 3rd miss;
  - position read fails right before the restore: nothing placed, the coin stays blocked.
- **Delayed visibility:**
  - inside the 10 s grace a not-listed stop is not a miss and gets no lookup;
  - after the grace, a lookup returning NEW confirms it, and no second stop is ever placed;
  - without a status lookup, a late listing before the 2nd miss cancels the miss, and 2 real misses restore and cancel the
    old id (one stop on the exchange);
  - the base `FakeX` (tags only) works through the fallback.
- **Duplicates:**
  - an extra bot stop is cancelled, and a foreign (`web_…`) stop is untouched;
  - an extra is kept while Binance holds untracked size on that side;
  - an extra is kept while the lot's own stop is missing, and goes after the restore (exactly one stop left);
  - a matching unowned stop is adopted for a lot with no stop id, and for a lot whose stop vanished (no new order).
- **Algo and classic:**
  - `a:` stops are verified, looked up and restored as `a:`;
  - real client: rows parsed for classic and algo (including detail-less rows), and the symbol-scoped weight-1 form;
  - `stop_status` routing and its results: CANCELED/TRIGGERED, not found → None, unsupported → None, busy → raises,
    junk tag → None;
  - a busy algo read raises; it is never treated as an empty list.
- **Outage:**
  - circuit in outage: zero reads and no miss, so the lot is `stale`, not `missing`;
  - through the real client: 8 minutes of 503s give no miss, no order and one exchange-down line, and the lot is protected
    again after recovery;
  - a stop cancelled during an outage is restored in the recovery pass, and T05b's `stop-unseen` closes once the new stop is
    listed.
- **Dry mode:** a dry engine with a lot whose stop is gone makes zero reads, orders and lookups, and never shows protected.
- **T05a:** a broken audit (`TA.observe` raises) does not affect verification or restore.

Windows: the new file passes under the CP1252 shim. All text I/O in the new code is explicit UTF-8 or not file-based.

## Review round 1 (333ca56): fixes in 024ea47 (new commit, no history rewrite)
| # | Sev | Defect (repro in `scratchpad/eng01rev/`) | Fix | Regression test(s) |
|---|---|---|---|---|
| D1 | Medium | `r1_orphan_adopt.py`: the entry stop's answer was lost (c:<cid> orphan) and the safety close failed, so the verifier **adopted** the orphan. The next sweep cancelled it, leaving 40–72 s with zero live stops while the panel said protected | orphans (tag / `c:`+cid / `ac:`+cid) are excluded from extras and adoption, so a fresh stop is placed | `test_d1_a_stop_queued_for_cancellation_is_never_adopted` (0 s unprotected, one stop), `test_d1_restore_never_adopts_a_queued_orphan_either` |
| D2 | Medium | `r3_algo404.py`: algo endpoints answering 404/-1404 made `open_stop_orders` return "no algo" and `stop_status('a:..')` return None. After 2 misses a classic stop was placed next to the live `a:77`: two stops | `strict_algo` when the coin holds an algo stop (unknown, no miss); an algo stop with None status is never auto-replaced (alert and block instead) | `test_d2_algo_endpoint_unsupported_while_holding_an_algo_stop_is_unknown_never_a_second_stop` (real client), `test_d2_real_client_strict_algo_raises_on_unsupported`, `test_d2_an_algo_stop_with_an_unreadable_status_is_alerted_never_auto_replaced` |
| D3 | Low | `r2_incidents.py`: `stop-extra|SYM|tag` and `stop-missing|<closed lot>` stayed open forever (keyed incidents are not idle-swept) | resolved after a successful extra cancel (kept open while it is parked), and in `_finish` | `test_d3_extra_cancel_and_closed_lot_incidents_are_closed`, `test_d3_an_extra_whose_cancel_fails_stays_open_until_it_is_gone` |
| D4 | Low | `r4_trail_weight.py`: a stop trailing every pass gave 38 reads in 10 min | action re-verify floored at last read + `STOP_ACTION_MIN_S` (30 s): 19 reads; budget text corrected | `test_d4_a_stop_trailing_every_pass_is_not_re_read_every_pass` (≤ 20) |

The repros were re-run on the fixed tree:
- r1: 0 s unprotected, no adoption, one live stop;
- r3: `a:77` stays the only stop, no orphan, no false alert;
- r2: no open incident after the lot is closed (r2(a) now uses a non-bot client id, so it is untouched, as it should be);
- r4: 19 reads in 10 min.

Test gaps added:
- `test_restore_whose_new_stop_answer_is_lost_leaves_no_lasting_duplicate` (the AmbiguousOrder restore leaves exactly one
  stop after the sweep);
- `test_two_lots_same_coin_side_qty_and_price_keep_their_own_stops` (a twin lot's stop is never adopted or cancelled);
- `test_only_exact_bot_client_ids_count_as_extras` (8 near-miss ids untouched);
- `test_real_client_status_minus_2013_is_unknown_for_classic_and_algo`.

The existing tests that waited `STOP_SETTLE_S + 1` for the post-action re-read now wait `STOP_ACTION_MIN_S + 1`. The fake's
default client ids now have the `new_cid` shape.

## Mutation check (`scratchpad/eng01/mutate.py`, scratch copy, `tests/test_stop_verify.py`): 35/35 killed (re-run on 024ea47)
The mutants:
- M1: no verify in `manage`;
- M2: no freshness window;
- M3: one unknown miss restores;
- M4: no critical position re-check;
- M5: no listing grace;
- M6: live status ignored;
- M7: extras cancelled while unprotected;
- M8: foreign orders treated as bot stops;
- M9: entries not blocked;
- M10: adds not blocked;
- M11: verifies during outage;
- M12: restart keeps the old proof;
- M13: no adoption;
- M14: fired treated as unknown;
- M15: no budget;
- M16: no candle-close verify;
- M17: order action not re-verified;
- M18: panel legacy rule;
- M19: gone status not trusted;
- M20: no alert on restore;
- M21: old stop not cancelled on restore;
- M22: positive evidence ignored;
- M23: unknown read counted as miss;
- M24: `stop-unseen` never closed;
- M25: Telegram legacy rule;
- M26: extras cancelled over untracked size;
- M27 (D1): orphans adoptable;
- M28 (D2): engine not strict;
- M29 (D2): algo unknown status restores;
- M30 (D2): client ignores `strict_algo`;
- M31 (D3): extra incident never closed;
- M32 (D3): closed-lot incident kept;
- M33 (D4): no action floor;
- M34: loose bot client-id match;
- M35 (D3): extra closed even while parked.

M8 and M17 were re-targeted to the new code (exact-id filter; floored pull-forward).

## Suites (mini-pytest, file by file, re-run on 024ea47)
| Suite | Result |
|---|---|
| test_stop_verify | 42 passed (also passes under the CP1252 shim) |
| test_safety | 78 passed |
| test_outage | 42 passed |
| test_outage_final | 8 passed |
| test_t05b_startup_outage | 13 passed |
| test_trade_audit | 137 passed |
| test_t05a_t05b_interaction | 2 passed |
| test_t03c_t05b_interaction | 3 passed |
| test_leverage_auto | 185 passed |
| test_fills | 46 passed |
| test_grid | 14 passed |
| test_v31_engine | 45 passed |
| test_telegram | 23 passed |
| test_testnet_faults | 47 passed |
| test_ci | 36 passed |
| test_verify | 16 passed, 1 known env-only failure (`pandas`/`pytest` provenance) |

Note on `test_trade_audit`: during round 0, one run under CPU contention (another agent's suite on the same 2 cores) failed
only the timing assertion `summary < 0.1 s` (0.104 s). It passed 137/137 when run alone and in both later sequential runs.

## Limits
- **The protection gap is bounded, not zero.** A stop cancelled right after a read is noticed within about 48–72 s. A restore
  then needs a gone status (same pass), a 2nd miss (+10 s), or, for a fired status, a 3rd miss (+20 s).
- **A FILLED/TRIGGERED status with an intact position is restored at the 3rd miss.** If Binance's position report lags for
  more than about 20 s after a real trigger, a stop could be placed for a position that is already closed. In hedge mode a
  STOP_MARKET for a position side with no position cannot open one: it is rejected or expires when it triggers. Reconcile then sees the
  lot's (new) stop still listed with a smaller position and resyncs after two sightings. `_finish(k, 'resync')` cancels that
  stop, but the trade is recorded as `resync` instead of `stop` (see open question 2).
- **Adoption needs the detailed rows** (`open_stop_orders`). With a tags-only client (old fakes, replay), only confirmation,
  misses and restore work. There is no adoption and no extras cleanup.
- **Extras are detected by the exact `new_cid` shape** (`z[ab]` + 22 lowercase hex). Bot stops with any other id are never
  cleaned up automatically (they surface only through T03c's exposure proof). Another program using the same id shape on
  this account would be treated as the bot.
- **An algo stop whose status stays unreadable is not auto-replaced** (D2). The owner gets one alert and the coin stays
  blocked until a read confirms it (seen, or a readable gone status).
- **reconcile's shrink path still uses non-strict `open_stop_tags`** (pre-existing T05b behaviour): with the algo endpoint
  unsupported, a shrunk position whose lot holds an algo stop can be booked as `stop`. This is out of FBL-ENG01's scope;
  flagged for a follow-up.
- **`stop_confirmed_t` is not persisted across restarts** (by design). Every restart shows the lots `unverified` until the
  first pass, about one manage tick.

## Open questions for Codex
1. Is 300 s (`STOP_FRESH_S`) right for the "protected" badge, or should it be tighter, for example 2× the verify period
   (about 150 s)?
2. Should a **fired** (FILLED/TRIGGERED) stop with an intact position wait longer than 3 misses, or never auto-restore and
   alert instead? The current choice favours "an open position is never left without a stop".
3. Should the entry block be per coin **and side** (like the existing `stop_dirty` gate) instead of per coin? It is per
   coin now, which is the conservative choice in hedge mode.
4. Should extras with a bot prefix but on a side with **no lot at all** be cancelled? They are cancelled now unless Binance
   holds untracked size there.
5. Should a live canary be added? For example: on testnet, cancel one lot's stop in the Binance UI and expect one
   `stop-missing` line, a new stop within about 80 s, no second stop, and the entry block text while it is missing.
