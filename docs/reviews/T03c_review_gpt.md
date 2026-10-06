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

## Runtime-gate addendum — installer stopped safely

*2026-10-06 17:42 Africa/Cairo*

The owner approved the T03c install and bounded testnet canary. The pre-install status was PAPER/testnet and healthy: three
protected lots, engine/exchange ok, zero errors, unprotected, untracked or orphan orders. The installer then failed at staged
safety tests **before stopping or replacing the running build**. Cause: T04d CI tests validate every `DATA_MANIFEST.json`
file, while the installer intentionally excluded `data/`, `data1h/` and `data_long/` from its staging copy.

Fix: keep the 33 MB of manifest-controlled research data only through staged pytest, delete those three temporary staging
directories fail-closed before PyInstaller, and continue excluding them from the executable and installed source mirror.
Direct regression evidence: installer + CI tests **100/100 passed in 3:19**. A non-mutating build gate and independent review
must pass before the already-approved install is retried.

---

# T03c automatic leverage handling — Codex review, round 4

*2026-10-06 18:45 Africa/Cairo; exact behavior head `1f02e3340c854b9f28a799638cb477e76f6fb9dd`*

## Verdict

**Focused code review clean.** The exceptional-path canary injector is inert by default and activates only when the trading
client base equals the exact Binance Futures TESTNET URL and `ZB_TESTNET_FAULTS` contains a valid symbol-scoped
`lev_refuse:<SYMBOL>` entry. It cannot alter a MAINNET, empty, trailing-slash or look-alike client, even when the environment
flag is present. The injected refusal occurs before any leverage request is sent; all exposure-proof reads, entry and stop
operations remain real testnet calls.

Malformed and unknown entries are ignored, other symbols retain the normal request path, and the startup warning contains
only the affected symbols. Official Windows focused evidence: **334/334 passed** across the injector, leverage, safety,
grid and fill suites; `git diff --check` passed. No blocking finding remains. Require fast/CodeQL and one exact-head full
gate before reinstalling and running the owner-approved PAPER canary procedure.

---

# T03c automatic leverage handling — Codex review, round 5

*2026-10-06, Africa/Cairo; exact behavior head `3214257e263311f2f15e79bf96712e0e2cfebba7`*

## Verdict

**Telemetry correction accepted; no new P1/P2 finding.** The runtime canary exposed that two failed leverage requests were
reported as one API refusal. `_lev_api_refused` now records each failed request at the point it occurs, while
`count`/`proceeded`/`skipped` remain fallback-decision counters and cooldown checks still add no API refusal. A first-attempt
failure followed by retry success records one refusal and no fallback decision, as intended.

The most recent error aliases remain compatible, the last two request errors are retained in a bounded JSON-safe list, and
no error text is logged beyond the existing 160-character limit. Focused Windows evidence on the exact behavior head:
**291/291 passed in 17.17 s** across leverage, safety, grid and testnet-fault suites; `git diff --check` passed.

Require fast/CodeQL and the protected exact-head full gate after this documentation commit. The currently open SOL testnet
canary remains protected on the prior installed build; rebuilding, restarting and repeating the bounded canary are separate
runtime actions and must not occur until the gates are green.

---

# T03c automatic leverage handling — final runtime acceptance

*2026-10-06 23:40 Africa/Cairo; accepted head `ee564c101ac6866acaaac4febcd0fddef3b8c199`; installed build `20261006-233104`*

The owner approved the guarded rebuild/install/restart and bounded SOL testnet canary. The installer completed every safety
test and executable self-test before stopping the previous bot, installed SHA-256
`1156603D33D2A8BA1DDBFC605758866C7B57F0CEAD5DE9AD27EC2E917C3C8F6B`, and proved the same build running with no warning.
After reconciliation, all four pre-existing lots were protected and health was clean.

With the exact-testnet, SOL-only refusal injector enabled, the old disposable SOL canary was closed cleanly and one new
minimum-risk SOL LONG was opened through the exceptional path. Runtime evidence: `api_refusals=2`, decision `count=1`,
`proceeded=1`, `skipped=0`, `accepted=exposure`, `outcome=went_ahead`, current leverage 20x, margin type `CROSSED`, effective
account leverage 0.13x and worst-case margin ratio 0.0015. Both bounded error-history entries were retained. The real fill
had a numeric exchange stop, `protected=true`, and its persisted lot carried `lev_exception=true`.

The lot and stop were adopted across a second injector-enabled restart with zero errors, unprotected/untracked positions or
orphans. Cleanup then closed the canary, restarted without the injector, opened one minimum-size normal protected SOL entry
to restore the configured 10x leverage path, and closed it. Final state: PAPER, build `20261006-233104`, engine/exchange OK,
no SOL lot, three original lots all protected, and zero errors, unprotected, untracked or orphan orders.

**Final verdict: accepted.** No open T03c P1/P2 finding remains. The separate ordinary-maker leverage-change race stays on
the follow-up backlog; it does not affect exceptional entries, which never rest as maker orders.
