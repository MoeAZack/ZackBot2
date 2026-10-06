# T03c: automatic leverage handling. Review request

## Codex round 2 integration (2026-10-06 13:23 Cairo)

Codex independently implemented the round-1 fixes while Claude was working, then reviewed Claude's stronger account-wide
implementation at exact head `a8e7b79260157c5fc6e0dea8247e03159d072de0`. The integration keeps Claude's complete fresh
snapshot, future DCA/pyramid/grid reservation, bracket validation and 138-test adversarial suite. Six additional gaps were
confirmed and fixed:

| Priority | Confirmed gap | Integrated fix |
|---|---|---|
| P1 | An above-cap exceptional maker order could rest after its one-time proof and fill later after the account deteriorated. There is no reliable pre-fill recheck for a resting exchange order. | Exceptional entries bypass maker mode and execute immediately after the fresh proof. Normal within-cap entries still use maker mode. Legacy/persisted exceptional maker remainder paths remain blocked. |
| P1 | Stop proof matched tag, symbol, side, size and trigger, but did not prove `STOP_MARKET`, `MARK_PRICE`, open status or quantity-mode semantics. A take-profit order with the same tag fields could be mistaken for downside protection. | Classic/algo order snapshots now expose order type, working type, status and `closePosition`; every managed stop must exactly match the bot's protective order shape. Unknown/missing fields reject. |
| P1 | `_lev` was trusted indefinitely. A user/external process could raise Binance leverage after the bot cached the configured cap. | Every cached reuse re-reads current leverage. Unknown or above-cap state invalidates the cache and re-enters the normal set/fallback proof. |
| P2 | Client parsing truncated fractional leverage (`20.5 -> 20`) and converted booleans to numbers before the engine could reject them. | Strict finite positive-integer parsing in both leverage readers; bool, fractional, zero, negative, NaN and infinity become unknown. Position/order numeric adapters reject bool and non-finite values before normalization. |
| P2 | `bool('false')` is true in Python, so malformed string order flags could hide an exposure-increasing order as reduce-only. Missing boolean fields were also silently defaulted. | Exchange boolean fields must be present and actual booleans. Unknown answer shapes fail closed. |
| P2 | Binance's account-specific `notionalCoef` was ignored by the bracket adapter. | Effective floor, cap and cumulative maintenance are scaled consistently before the existing continuity/maintenance checks. |

Additional focused evidence: **335/335 passed** across leverage, safety, grid, fill and maker-engine tests. The six new guard
groups add direct regression probes for stale maker approval, external cache changes, exact stop semantics, strict leverage
and boolean parsing, and `notionalCoef`. Syntax compilation and `git diff --check` pass. On the final code tree, the complete
official suite passed **514/514 in 7:01**, and the isolated causality suite passed **13/13 in 3:35**. Code commit: `50c10fa`.
GitHub fast/CodeQL and the single exact-head full gate remain before code acceptance.

Runtime remains unchanged: no installer, bot restart or testnet order/canary is authorized by this code review. Installation
and a bounded testnet canary require the owner's separate confirmation after code acceptance.

## Round 1 dispositions (Codex review `docs/reviews/T03c_review_gpt.md`)

All seven findings are confirmed and fixed. The original request is kept unchanged below; where it conflicts with this section, this section is current.

| # | Finding | Disposition |
|---|---|---|
| P1 | The proof omits account exposure and accepts unprotected or in-flight state | **Fixed.** `_exposure_proof` now runs on a FRESH exchange snapshot taken for each decision: `account()`, the new `Futures.position_risk()` (every symbol and both hedge sides, all positions including manual or untracked ones), the new `Futures.open_orders_all()` (classic + algo, account-wide; a failed algo read raises instead of reading as empty) and `marks()`. It rejects (`check`) on any of these: `self.untracked` is not empty, a lot is `pending`/`force_close`, or a grid op is unconfirmed (`reconcile`); any exchange position on any symbol or side differs from the bot's lots beyond the step tolerance, where only the FILLED part of a working bot maker entry may explain extra quantity (`reconcile`); such a filled part has no stop yet and is then rejected as `unprotected` (see the internal adversarial pass); a lot whose stop is not numeric, `stop_dirty`, has no `stop_id`, whose stop is not open on Binance, or whose stop is on the wrong symbol or side or covers less than the lot (`stops`); any exposure-increasing open order that is not a bot maker entry, matched by client id (`working_orders`); any failed snapshot read (`snapshot`). Reserved exposure (notional + stop loss): every working bot maker entry (`state['resting_entries']`, full quantity), DCA safety orders not yet filled, pyramid adds left, and active grids at full-fill notional (×2 for neutral). `pending_entries` (trailing entries) place no order until they trigger, and they then go through `open_lot` → `_ensure_leverage` with their own proof, so they are not reserved. |
| P1 | Wrong loss and notional basis | **Fixed.** Gross notional = Σ exchange notional (max of the reported `notional` and qty×mark) + reserved + entry. Post-stop balance = `totalMarginBalance` (unrealized P&L included) − Σ qty × max(0, current mark → stop) × 1.5 − reserved losses × 1.5 − entry risk × 1.5. A stop at or beyond the mark counts 0, never as a gain. A short's notional grows to its slipped stop for the maintenance calculation. Entry-to-stop risk (`_lot_risk`) is reported only as `open_risk`. |
| P1 | Adds and the maker remainder bypass the gate | **Fixed (the simpler alternative the review offered).** An entry accepted through the exception marks its plan and lot `lev_exception`. `_lev_exception_block(sym)` then pauses, with a recorded reason: DCA and pyramid adds (`_add_block` → `add_blocked` + a missed-list record), the maker market fallback and the partial-fill remainder (`fallback_blocked` in the fill record + a missed-list record), and grid adds on that coin (`grid._can_add`). The pause lasts until the bot sets the leverage (`_lev[sym] == cap`) or a read-only `margin_state` re-check, at most once per `LEV_EXC_RECHECK_S` (60 s), shows the coin at or below the cap. |
| P1 | `1/leverage_max` is not bracket-aware | **Fixed.** The new `Futures.leverage_brackets(sym)` returns the full schedule (floor, cap, maintMarginRatio, cum, initialLeverage). `check_brackets` validates it: contiguous `[floor, cap)` tiers from 0, 0 < mmr < 1 and non-decreasing, leverage ≥ 1 and non-increasing, cum ≥ 0 and continuous. `bracket_maint` = notional × mmr − cum of the tier holding it, and fails beyond the last tier. Maintenance after stops = `totalMaintMargin` + Σ over each symbol whose notional grows [bracket_maint(worst-case aggregate notional, both sides) − bracket_maint(current)]. The new entry's symbol is always included. Schedules are cached per symbol for `LEV_BRACKET_TTL_S` (1 h). A failed, missing or malformed schedule rejects (`brackets`) and is never cached, and an expired schedule is never reused. The coin maximum comes from the cached first tier. |
| P2 | Non-finite or invalid numbers fail open | **Fixed.** Every input goes through `lev_num`: finite, not bool, balance > 0, maintenance ≥ 0, quantities/notionals/risks ≥ 0, prices/marks/leverage > 0. That covers the account fields, position rows, lot qty/avg/stop/DCA/pyramid fields, maker entries, grid metrics, entry size and the cap. Derived values are checked too. An invalid current leverage (NaN, 0, negative, text) counts as unknown leverage. Every reported number goes through `_fin` (None instead of NaN/inf), so `json.dumps(lev_refusals, allow_nan=False)` succeeds on every path. |
| P2 | Telemetry loses the real reason | **Fixed.** `lev_refusals[sym]`: `api_refusals` (refused POSTs, with `last_api_error`/`last_api_time`; `last_error` is kept as an alias and set only by a real refusal) and `cooldown_checks` (decisions without a POST) are separate. `count` stays the number of decisions (= proceeded + skipped) for T03a compatibility. Each decision records `outcome` (`went_ahead`/`skipped`), `reason` (`within_cap`, `exposure_ok`, `leverage_unknown`, `margin_isolated`, `margin_unknown`, `exposure_rejected`), `detail`, `via` (`refused`/`cooldown`), `cooldown_left`, and `exposure` with `check`. The panel `levDlg` shows each path, the refusal/cooldown counts and the failed check, all escaped. |
| P3 | Cooldown still performs avoidable venue calls | **Fixed.** The cooldown is decided first, before the margin-type POST and before any bracket fetch (the cap during a cooldown uses the cached schedule only). Fresh account, position, order and margin-state reads still happen on every decision. |

**Tests** (`tests/test_leverage_auto.py`, 61 functions / 107 runner entries at the first round-1 commit, now 82 / 138 after the internal adversarial pass below; the original 17 T03c tests are kept and adapted to the fresh-snapshot fake):
1. untracked ETH position, engine untracked flag, lot over/under/missing on the exchange, step tolerance, pending order / unconfirmed grid op, one large and several resting entries reserved, partial maker fill on the exchange, unknown vs reduce-only vs bot working orders, grid + DCA + pyramid reservation;
2. `stop_dirty`, missing `stop_id`, stale stop not open on Binance (another symbol), wrong symbol/side or too small a stop, non-numeric stops (None/0/text/NaN/inf/bool), snapshot read failure; client tests for `open_orders_all` (raises on a failed algo read) and `position_risk`;
3. profitable and losing long and short with trailing/breakeven stops (exact mark-to-stop numbers), a stop through the mark, a large unrealized gain given back to a breakeven stop (rejected; the old basis accepted it), short notional growing to its stop;
4. DCA and pyramid adds blocked even after the account becomes safer, pyramid in `manage` sends no order, resume after leverage set or a read-only re-check (throttled), grid adds blocked, maker remainder after a partial fill and market fallback when unfilled both blocked, fallback runs normally once leverage was set;
5. exact bracket boundaries (continuity at 50 000 / 250 000), beyond the last tier, an entry crossing a tier (exact maintenance), both hedge sides aggregating into a steep tier, cache + TTL refetch failure, 9 malformed schedules (never cached), client schedule parsing, coin maximum from the cached schedule;
6. NaN/inf/negative/missing/text account fields, invalid lot fields, invalid entry size, invalid position rows, invalid current leverage, invalid cap/bracket leverage, valid status JSON;
7. outcome/reason/counts on all six paths, cooldown decided before the margin POST and bracket fetch with fresh reads kept, panel source, and a node-rendered `levDlg` for every path with hostile strings escaped.

**Mutation proof:** 32/32 caught (24 round-1 fail-opens planted back one at a time, plus the 8 still-applicable round-0 classes), each restored exactly and verified (anchor check + `git diff`): reconcile ignored; resting entries not reserved; unknown working order accepted; stop_dirty/missing stop_id accepted; stale stop accepted; entry-to-stop risk as the loss basis; gross at qty×avg; adds not blocked; unfilled maker fallback not blocked; maker remainder not blocked; first-tier rate only; brackets unavailable treated as pass; malformed brackets accepted; NaN/inf accepted; negative maintenance accepted (first missed: the derived-value check caught -5, so a -0.1 case was added, now caught); cooldown counted as an API refusal; cooldown overwrites last_api_error; margin POST before the cooldown decision; bracket cache disabled; invalid current leverage not validated; grid adds not blocked; future DCA/pyramid not reserved; short notional not grown to its stop; panel detail not escaped; and round 0: no margin-type check, no effective-leverage check, no worst-case check, cooldown ignored, cooldown never set, exception treated as pass, missing account data defaulted, cooldown not cleared on success.

**Evidence (Claude sandbox, stand-in runner, NOT official pytest):** test_leverage_auto + test_safety + test_fills + test_v31_engine: 276 passed. test_grid: 14, test_telegram: 23, test_lab: 27 passed. The T03a tests in `tests/test_safety.py` are unchanged and pass. Their FakeX has no `leverage_brackets`/snapshot methods, so above-cap cases there fail closed, as they expect. Panel JS parses (`node --check`).

### Internal adversarial pass (independent verifier, before re-review)

An independent verifier found 5 more fail-open cases in round 1 (10 failing repros). All are confirmed and fixed test-first: the repros were copied into `tests/test_leverage_auto.py` (`test_av*`, descriptive names), and each one failed before its fix and passes after it.

| # | Case | Fix | Tests |
|---|---|---|---|
| AV1 | **Answer shape.** `open_orders_all`/`position_risk` read an unexpected answer as empty: a dict without `orders` was iterated over its keys, and `{}` with `or []` read as "no orders"/"no positions". A wrapped algo list therefore hid a manual 1000-ETH conditional entry, and the gate accepted. | Only the exact shapes count: a list for `openOrders` and `positionRisk`; a list, or a dict whose `orders` is a list, for `openAlgoOrders`. A malformed algo row raises. Anything else raises, and the proof rejects (`snapshot`). Unknown never means empty. | `test_av1_*` (wrapped/empty/None/text shapes, malformed rows, valid list and `{'orders': [...]}`, end-to-end reject) |
| AV2 | **Filled maker part** was valued at the planned stop distance, so its unrealized gain counted as cushion. | The filled part is projected like a lot: current mark → its stop (fill − side × stop distance) × 1.5, and a short grows to its stop. | `test_av2_*` |
| AV3 | **Reserved shorts** (maker, DCA, pyramid, grid) did not grow to their stop in the maintenance projection. | Every reserve carries its side. A short, or a grid (`side=None`, either side possible), adds notional + its loss to stop (= notional at the slipped stop) to the worst-case symbol notional, exactly like a lot. | `test_av3_*` (maker short vs the same lot, tier crossing, short DCA, short grid) |
| AV4 | **Maker tolerance.** A working maker entry hid an unprotected position up to its full quantity. | The reconcile tolerance is only the maker entry's `filled` part (`filled ≤ qty` is checked). Any other extra quantity rejects. | `test_av4_*` |
| AV5 | **Shared stop order.** Coverage was checked per lot, so one 10-qty stop "protected" two 10-qty lots. | Coverage is checked per stop ORDER: the lots referencing one order must together be ≤ its quantity (step tolerance per lot). | `test_av5_*` (shared beyond size rejects, 5+5 under a 10-stop passes) |
| a | Stop price | A lot's `stop` must match the exchange order's stop price within one tick, else reject (`stops`). | `test_ava_*` |
| b | Stop already past the mark | Still never a gain, but charged its distance from the mark as slippage (0 when exactly at the mark), for longs and shorts. | `test_avb_*` |
| c | Partial maker fill without a stop | Rejected as `unprotected` while any maker entry has a filled part (it becomes a protected lot only at finalize). The numbers are still computed and reported. My own round-1 test, which accepted this state, now asserts the rejection (`test_g1_partially_filled_resting_entry_reconciles_but_is_unprotected`). | as named |

The verifier's other probes (account answer shapes, network errors in the snapshot or margin-state read, leverage strings/junk, the add pause never lifting on a bad read and re-checking fresh) already passed. They are kept as regression tests.

**Mutation proof (this pass): 10/10 caught, one per fix: positionRisk / openOrders / algo shape not enforced (3); filled maker part at the planned stop distance; reserved shorts not grown to their stop; maker tolerance using the full qty; stop coverage per lot only; stop price not compared; stop past the mark charged nothing; unprotected maker fill accepted. All 32 earlier round-1 mutations were re-run on the new code and are still caught (32/32). Three of them were re-anchored to the rewritten lines (maker reserve, mark-to-stop loss, short lot growth). Total 42/42. Each was planted alone and restored exactly (anchor check + identical worker copies).**

**Evidence:** test_leverage_auto 138 passed (82 functions); test_safety 78, test_grid 14, test_fills 46, test_v31_engine 45 passed (stand-in runner, explicit files).

**Open design questions (round 1):**
1. Algo-order read: `open_orders_all` fails closed when `/fapi/v1/openAlgoOrders` (account-wide) errors. If testnet rejects that endpoint, the exception path never accepts on testnet. That is safe, but the canary would show `check: snapshot`.
2. Any untracked position, including dust below Binance's minimum (which `reconcile` does not alarm on), blocks the exception. Strict on purpose.
3. Reserves are conservative and may double count (a grid's full-fill notional on top of its filled lot, a maker entry's filled part on top of its exchange position). Acceptable, or should these be netted?
4. Adds on an exception coin are paused rather than re-proven. Re-proving each add against a fresh snapshot is possible later if blocked adds become a practical problem.

---

## Original request (round 0)

### Current state

| Item | Value |
|---|---|
| Branch | `t03c-auto-leverage`, now merged with T05 (accepted code, `e58f0c2` + Codex `1be0bda`) and therefore with master `d68d6ef`. If T05 merges to master first, the branch is merged with master again before the merge gate. The head is in the READY FOR CODEX comment |
| Roadmap | Not yet in ROADMAP.md: the owner asked Claude for it in chat on 2026-10-06 ("Yes q t03c", after confirming the problem can occur on mainnet). Codex owns ROADMAP.md / PROJECT_STATUS.md; please place it (suggested: right after T05, before T06) |
| Class | **Order path (entry gate).** It changes which entries may go ahead after Binance refuses a leverage change. No new order types and no change to sizing, stops or exits |
| Owner | Asked for this (2026-10-06): leverage handling must be automatic, because more venues are coming (tradfi, stocks). The owner confirmed it is in scope because it can happen on mainnet too |
| Runtime | Needs CI green + Codex code approval, then a bounded testnet canary under the owner's standing testnet approval (never mainnet) |

### Problem

T03a lets an entry proceed after a leverage refusal only if the coin is ALREADY at or below the cap. A coin stuck above the cap (SOL at 20x with a 10x cap on testnet, which answers -1000) is skipped forever until someone changes it by hand in Binance. Mainnet can refuse too: open orders or positions above the new bracket, or venue-specific rules.

### The change

1. **Read the margin type too.** New `Futures.margin_state(sym)` (read-only positionRisk) returns `{leverage, margin_type: CROSSED|ISOLATED|None}`. A mixed or missing answer gives None. Older clients without it fall back to `current_leverage` with an unknown margin type.
2. **New acceptance path (`_exposure_check`).** The coin is above the cap. The entry may go ahead only if ALL of these hold; any unknown means skip:
   - margin type is `CROSSED`. Under isolated margin the coin's leverage sets its own liquidation price, so isolated is never accepted;
   - account effective leverage after the entry, (open bot lots + this entry) / totalMarginBalance, is ≤ MAX_LEVERAGE;
   - worst case: every open lot and this entry stop out, this entry with 1.5× slippage, and open risk includes unfilled DCA safety orders (`_lot_risk`). The account margin ratio, (totalMaintMargin + entry × 1/coin-max-leverage) / remaining balance, must be ≤ 50%. 1/max-leverage is the initial-margin rate, which is above Binance's maintenance rate, so the check is conservative. This guarantees the stops fire long before an account liquidation;
   - every open lot has a numeric stop, and the account answer has both fields.
3. **The entry size reaches the check.** `open_lot` passes `notional = qty × px` and the entry risk to `_ensure_leverage`.
4. **Cooldown.** After a refusal, no new POST /leverage for that coin for 30 min (`LEV_REFUSAL_COOLDOWN_S`). Entries in that window still go through every read-only check. A later successful change clears the cooldown and caches as before.
5. **Reporting.** `lev_refusals[sym]` adds `by_exposure`, `accepted` (`within_cap` / `exposure` / None), `margin_type` and `exposure {effective_leverage, worst_margin_ratio, margin_balance, ok, why}`. The panel's leverage-refusals dialog shows the decision per coin (escaped). Its text no longer says "No trade ever runs above your cap", which would now be misleading about the coin setting. It says trade sizes never exceed the cap.

Unchanged: the T03a within-cap path, the skip on unknown leverage, dry mode (it never reaches this path), sizing, stops, exits and adds.

### Dropped from the design draft

- A retry ladder of lower leverage values. Refusals are not value-specific (testnet -1000, mainnet open-order/bracket errors), so extra POSTs add API load without a path to success. The cooldown handles repeated attempts instead.
- Liquidation price per coin. Under cross margin, liquidation is account-wide, so the worst-case margin ratio is the account-level equivalent and needs no per-coin bracket math.

### Tests (`tests/test_leverage_auto.py`, 17 test functions, 18 runner entries with the parametrized margin-type case; T03a tests in `tests/test_safety.py` unchanged and passing)

- Cross above cap with a safe exposure: enters with its stop, recorded as `exposure`, never cached.
- The entry size reaches the check.
- Skips:
  - isolated or unknown margin type;
  - missing account fields;
  - effective leverage above the cap (existing 50k lot);
  - worst-case ratio above 50%;
  - open risk of existing lots tipping the worst case;
  - an exception inside the check;
  - unknown current leverage.
- A lot without a numeric stop (None, 0, text) makes the check fail.
- The within-cap path needs no exposure check.
- Cooldown: no new POST inside 30 min, POST again after it, every safety check still applied, cleared on success.
- Client parsing: cross, isolated, mixed (unknown), no rows, another coin's rows ignored.
- Dry mode never reaches the path.
- Panel dialog content.

**Mutation proof: 10/10 caught.**
- no margin-type check;
- no effective-leverage check;
- no worst-case check;
- open risk ignored;
- cooldown ignored;
- cooldown never set;
- exception treated as pass;
- mixed margin type accepted;
- missing account data defaulted;
- cooldown not cleared on success.

### Evidence (Claude sandbox, stand-in runner, NOT official pytest)

- On master + T05 + T03c: tests/test_leverage_auto.py + tests/test_fills.py: 64 passed. The full suite is in the READY comment.
- Merge with T05: two textual conflicts (module constants next to the T05 writer, and the new `_lev_cool` attribute next to `_fillw`), both resolved by keeping both sides; no logic overlap.
- The panel JS parses (node). The UI harness runs on GitHub (verify full).

### Questions for Codex

1. Are a 50% worst-case margin ratio and 1.5× entry slippage acceptable defaults, or should they be settings? They are module constants for now, to keep the settings surface unchanged.
2. Canary plan: one bounded testnet entry on SOL (20x, cross) after install, verifying `accepted='exposure'`, the stop placed, and the effective leverage reported ≤ 10x. Then the normal exit rules apply. Is that enough, or do you want a manual close right after?
