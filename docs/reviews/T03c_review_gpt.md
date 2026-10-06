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
