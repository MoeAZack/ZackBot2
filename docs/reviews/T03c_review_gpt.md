# T03c automatic leverage handling — Codex review, round 1

*2026-10-06 11:42 Africa/Cairo*

## Verdict

**Changes requested. Do not add `full-ready`, install, or run the testnet canary yet.**

Reviewed exact head `047d5dfa32e704b3b1664cc370ec923370d84ebd`, which is the original T03c behavior rebased by merge onto accepted T04d/master `cb5092b4801f7106e26d9243fbcf86e6236fa405`. The five-file behavior diff is preserved and the T04d workflow files are intact.

The Binance `marginType` / `leverage` adapter is compatible with the V2 response and mixed or missing rows fail closed. The blockers are in the exceptional above-cap proof: the current calculation does not yet prove account-wide exposure, confirmed protection, or post-stop margin safety.

## Confirmed findings

### P1 — The proof omits account exposure and accepts unprotected/in-flight state

`engine.py::_exposure_check` derives exposure only from `state['lots']`, accepts a positive numeric `stop` as proof of protection, and ignores other exchange exposure and working maker entries. `entry_block` rejects an untracked position only on the requested symbol/side.

Direct probes returned `ok=True` with each of these states:

- an untracked 50,000 USDT ETH position while checking a BTC entry;
- an existing lot with `stop_dirty=True` and no `stop_id`;
- a 50,000 USDT resting maker entry that had not become a lot.

This invalidates both advertised invariants: “account effective leverage” and “every open lot stopped out.” Use a fresh exchange-backed snapshot of all current position notionals, reserve all exposure-increasing working entries, require the engine/exchange quantities to reconcile, and require every managed lot to have a confirmed exchange stop. If any of those facts is unavailable, reject this exceptional fallback.

### P1 — Current margin balance is combined with the wrong loss and notional basis

`totalMarginBalance` already includes current unrealized P&L, but the calculation subtracts `_lot_risk`, which measures average-entry-to-stop risk and becomes zero after breakeven. It also values gross exposure at `qty * avg` rather than current mark/notional.

A profitable long can therefore keep its unrealized gain in the projected remaining balance even though a stop would give that gain back, while its current gross notional is understated. Compute current gross notional from fresh mark/exchange notional and project each long/short from the current mark to its confirmed stop. Keep entry-to-stop risk as a separate portfolio-risk metric.

### P1 — Adds and maker remainder execution can bypass the exceptional gate

`_ensure_leverage` runs for the initial entry only. DCA/pyramid additions go through `_add_block` / `_add_qty` without the new account-level proof, and maker remainder handling can execute after the original snapshot is stale. Initial DCA risk reservation does not reserve all future notional/maintenance; pyramids are not reserved.

Re-run the same fresh exposure/protection gate immediately before every exposure-increasing add and market remainder while a symbol remains accepted through the above-cap exception. A simpler safe alternative is to disable additive management and maker fallback for such lots until Binance leverage is back within the cap.

### P1 — `1 / leverage_max` is not a bracket-aware maintenance calculation

`Futures.leverage_max` returns only the first bracket's maximum initial leverage. The added position can cross into a higher notional bracket with a larger maintenance rate, and the bracket's cumulative maintenance adjustment is ignored. The claim that this proxy is conservative is not established for the applicable post-entry bracket.

Fetch and retain the full leverage-bracket schedule, then calculate projected maintenance for aggregate post-entry symbol notional (both hedge sides), including tier transitions and cumulative adjustment. Otherwise fail closed.

### P2 — Non-finite and invalid numeric data can fail open

The gate accepts negative maintenance margin and accepts `totalMaintMargin='NaN'`; the latter records a `nan` ratio and can also produce invalid status JSON. Non-finite balance/lot/bracket inputs and invalid leverage values need equivalent handling.

Require every input and derived value to be finite and within a sensible range (`balance > 0`, `maintenance >= 0`, quantities/notionals/risks nonnegative, leverage/bracket values positive). Any invalid value must reject the fallback.

### P2 — Decision telemetry does not preserve the real reason

Unknown-leverage and isolated/unknown-margin skips leave `exposure=None`, so the panel can show only “skipped.” A cooldown evaluation increments the refusal count and overwrites the latest refusal error even though no leverage POST happened.

Separate API refusal attempts from cooldown evaluations, preserve `last_api_error`, and record an explicit outcome/reason for every path. Cover each status and escaped panel rendering in tests.

### P3 — Cooldown still performs avoidable venue calls

The cooldown suppresses `/leverage`, but `_ensure_leverage` still attempts the margin-type POST and fetches brackets before checking the cooldown. Move the cooldown decision before repeat writes and cache static bracket data while retaining fresh position/account safety reads.

## Required regression evidence

Add tests for:

1. unrelated untracked/manual positions, filled-but-unreconciled positions, and one or more resting entries;
2. `stop_dirty`, missing `stop_id`, and stale/unconfirmed stop proof on another symbol;
3. profitable and losing long/short positions, including breakeven/trailing stops, using current mark-to-stop loss;
4. DCA, pyramid, and maker-remainder rechecks after account state changes;
5. exact bracket boundaries and an entry that crosses a tier;
6. `NaN`, infinity, negative maintenance, invalid leverage/bracket data, and invalid lot numeric fields;
7. separate refusal/cooldown/outcome telemetry and every panel display path.

Existing evidence remains useful but does not cover these states: the original 18 T03c tests pass, and the combined targeted leverage/safety set passed 96 tests locally. Direct adversarial probes are what exposed the fail-open cases.

## Gate and runtime boundary

After the fixes, publish a new exact head and run the single fast review gate. Keep `full-ready` off until Codex accepts the code; then run one final labelled full gate. T03c changes order admission, so installation and any testnet canary still require a separate owner confirmation at that stage. The earlier T03a canary approval does not automatically authorize T03c runtime testing.

---

# T03c automatic leverage handling — Codex review, round 2

*2026-10-06 13:19 Africa/Cairo*

## Verdict

**Round-1 findings are resolved in the integrated code; final acceptance remains gated on the complete exact-head test/CI
evidence. Do not install or run the T03c canary yet.**

Claude's round-1b head `a8e7b79260157c5fc6e0dea8247e03159d072de0` replaces the original local proof with a stronger
account-wide model: exact answer shapes, all live positions and open orders, confirmed exchange stops, mark-to-stop loss,
future maker/DCA/pyramid/grid reserves, short notional growth and validated bracket maintenance. Its 42 planted mutations
and internal adversarial repros cover the round-1 findings.

Codex independently reproduced and closed six remaining integration gaps:

1. **P1 stale exceptional maker admission:** an exchange maker order can fill after its one-time account proof becomes stale.
   Above-cap exceptional entries now bypass maker mode and execute immediately after the fresh proof. Normal entries retain
   maker behavior; legacy exceptional maker remainder paths remain blocked.
2. **P1 incomplete stop semantics:** tag/side/quantity/trigger matching did not distinguish a real mark-price stop-market
   from another conditional order. The proof now requires `STOP_MARKET`, `MARK_PRICE`, open status, quantity mode, side,
   quantity and trigger. Missing or malformed order fields reject.
3. **P1 stale leverage cache:** `_lev` could survive an external leverage change. Cache reuse now performs a read-only
   current-leverage check; unknown/above-cap state re-enters the normal set/fallback path.
4. **P2 numeric adapter truncation:** fractional or boolean leverage could become an apparently valid integer. Both client
   readers now require finite positive integers, and position/order numeric adapters reject bool/NaN/Inf before normalization.
5. **P2 malformed boolean flags:** Python treated the string `"false"` as true, which could hide an exposure-increasing order
   as reduce-only. Required exchange flags must now be present and actual booleans.
6. **P2 account-specific bracket coefficient:** `notionalCoef` was ignored. The first integration attempted to scale the
   schedule; round 3 supersedes that behavior because Binance does not document whether returned rows are already adjusted.

## Evidence so far

- Focused Windows gate: **335/335 passed** (`test_leverage_auto`, safety, grid, fills and maker engine).
- Additional Windows gate: **103/103 passed** (Telegram, Lab, CI and verification-runner tests).
- T03c file alone: **153/153 passed**, including the added round-2 probes.
- Python compilation and `git diff --check`: pass.
- Official complete suite: **514/514 passed in 7:01** on the final code tree.
- Independent causality isolation: **13/13 passed in 3:35**.

The integration intentionally does not require `totalOpenOrderInitialMargin == 0`: Claude's stronger snapshot identifies and
reserves every exposure-increasing open order, including the bot's own maker orders. Rejecting all positive reserved margin
would safely but unnecessarily disable the exception whenever a fully accounted maker order exists.

## Remaining gates

1. Push code commit `50c10fa` plus this evidence update, then require fast/CodeQL on the exact remote head.
2. If review remains clean, apply `full-ready` once for that exact head and require the protected full gate.
3. Only after code acceptance: ask the owner separately before any build/install, ZackBot restart or bounded testnet canary.

---

# T03c automatic leverage handling — Codex review, round 3

*2026-10-06 14:00 Africa/Cairo; exact head `001a0322c24c771762b4124850823ab4ce3c2068`*

## Verdict

**Code review clean. Apply `full-ready` once to this exact documentation head after the normal fast/CodeQL checks pass. Do
not install or run a testnet canary without the owner's separate approval.**

Claude's independent review of round 2 found two valid follow-ups, both resolved conservatively:

1. Binance does not document whether `notionalCoef` has already been applied to the returned bracket rows. The earlier
   multiplication could therefore understate maintenance. The client now accepts only an omitted/unit coefficient and
   rejects every other or malformed coefficient; the exception consequently fails closed and the schedule is not cached.
2. A legacy persisted exceptional maker record can no longer be re-priced on stale approval. Any still-open order is
   cancelled immediately, a terminal partial fill is protected through the normal lot path, and no market remainder is sent.

Exact-head Windows evidence: leverage/safety/grid/fills **320/320 passed in 27.43 s**; Python compilation and diff validation
pass. GitHub exact-head fast passed in 4:38 and both CodeQL analyses plus the summary are green. No open P1/P2 finding remains.

The pre-existing normal maker/external-leverage race is not represented as fixed: an external actor can change exchange
leverage while an ordinary maker order rests. Track that separately as a general maker-admission hardening item; it does not
weaken the new above-cap exception, which never rests as maker.
