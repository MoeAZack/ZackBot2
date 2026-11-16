# Lane 3 mainnet-readiness red-team

Audited base: `42ef787f6b6e8c9363705b76df13cdb487f840f0` (`master`).
Date: 16 November 2026, Africa/Cairo.

ROADMAP FIT: owner-approved lane 3, engine/risk mainnet-readiness evidence only.
DEFERRED: implementation fixes (one separate small PR per finding), activation,
integration acceptance, and all account/runtime/order work.

This is an offline source audit, not mainnet certification. Production files are
unchanged. No account, key, runtime, settings, scheduler, watchdog, or exchange
order action was taken. Tests use synthetic values, fake transports/exchanges,
temporary fixture state, and a network-denial fixture. Other lanes and protected
workstation resources were not accessed.

## Ranked findings

### F1 — HIGH: ambiguous market order can be submitted twice

`binance_client.py:519–528`: after a POST timeout, `_order` queries the client ID.
A single absent result immediately causes a second POST. Absence at one instant
does not prove the first request was rejected or never executed. The regression
models a first accepted request with a lost acknowledgement and delayed lookup;
the current client makes two submissions. This proves the unsafe resend decision,
not an observed Binance double fill. Same-CID reuse is not assumed to provide a
durable idempotency guarantee after an order has completed.

Reproducer: `test_ambiguous_market_is_not_resent_after_one_not_found_lookup` in
`tests/test_lane3_recovery_redteam.py`. Expected one POST; observed two.

Fix contract: preserve the unresolved intent and reconcile it; do not infer
non-execution from one absent lookup. Add explicit bounded reconciliation proof
before any new risk request. Keep this fix in its own PR.

### F2 — HIGH: stop ID presence falsely confirms insufficient protection

`engine.py:5638–5642,5771–5772,5789`: detailed stop rows are reduced to IDs,
`_stops_seen` refreshes confirmation by ID, and the verifier skips a lot when its
recorded ID is listed. The reproduction changes the fake exchange's listed stop
to cover half the lot. The verifier still reports `protected=True`.

Reproducer: `test_recorded_stop_with_insufficient_quantity_is_not_confirmed` in
`tests/test_lane3_risk_redteam.py`. Expected unconfirmed protection; observed
protected. This does not establish how a mismatch arose on an actual exchange.

Fix contract: validate symbol, side, order semantics, remaining coverage and
trigger against the owned lot before refreshing confirmation. Handle shared
coverage without double counting. Keep this fix in its own PR.

### F3 — HIGH: final order without execution quantity becomes a zero fill

`newcore/venue/binance_usdm_testnet.py:268`: a final response missing
`executedQty` defaults to zero. A canceled order can have executed partially;
missing execution evidence must remain unknown. The regression passes an
otherwise identified canceled response without the quantity and receives FINAL,
zero executed quantity, instead of UNKNOWN.

Reproducer: `test_final_order_missing_execution_quantity_is_unknown` in
`tests/test_lane3_exchange_redteam.py`.

Fix contract: require explicit valid execution quantity in final records and
preserve ambiguity on malformed/missing evidence. Separate fix PR.

### F4 — HIGH: partial open order without execution quantity is trusted

`newcore/venue/binance_usdm_testnet.py:397`: classic open-order parsing also
defaults missing `executedQty` to zero. A PARTIALLY_FILLED order with original
quantity 1 is returned as an OK snapshot with remaining quantity 1, despite the
missing information required to derive remaining exposure/coverage.

Reproducer: `test_open_partial_order_missing_execution_quantity_is_unknown` in
`tests/test_lane3_exchange_redteam.py`.

Fix contract: reject incomplete partial-fill records as UNKNOWN and reconcile
before using them for exposure or protection. Separate fix PR. F3 and F4 share a
default-value mistake at distinct response boundaries; neither test uses an
actual exchange response or establishes current venue integration into mainnet.

### F5 — HIGH: same-second manual fills overwrite lot ownership

`engine.py:7680,7713`: lot identity uses sleeve/symbol/side plus integer wall-clock
seconds. Manual entry admission returns before the automatic same-symbol holding
check (`engine.py:6511–6526`). Two completed manual fills for one symbol/side in
the same second both use the MAN key. The second lot replaces the first, losing
its quantity and stop ownership from the lot map. Clock rollback can also reuse
an earlier key. The reproduction invokes the real lot-construction path twice,
with only persistence, protection, telemetry and notifications replaced by fakes.

Reproducer: `test_same_second_manual_fills_preserve_both_lots` in
`tests/test_lane3_recovery_redteam.py`. Expected two lots totalling 3 units;
observed one lot containing only the second fill's 2 units.

Fix contract: stable collision-resistant fill/intent identity, with an explicit
replay-versus-new-fill decision; never overwrite a distinct owned fill. Separate
fix PR. This is a source/identity reproduction, not an actual manual order action.

### F6 — MEDIUM: non-decimal-power steps can increase quantity off-grid

`feasibility.py:37–40,197–205`: `round_step` derives decimal places from the
step's order of magnitude. For quantity 0.76 and step 0.25, flooring gives 0.75
but rounding it to one decimal produces 0.8. `size_check` accepts 0.8, exceeding
the risk-sized input and violating the step grid.

Reproducer: `test_non_power_of_ten_step_is_floored_without_leaving_grid` in
`tests/test_lane3_exchange_redteam.py`. Expected 0.75; observed 0.8.

Fix contract: exact decimal step arithmetic with the postconditions quantity <=
input and quantity an integral multiple of the step. Separate fix PR. The filter
is synthetic; no claim that a currently traded Binance symbol uses step 0.25.

## Coverage and unresolved readiness questions

* Risk caps: reviewed `newcore/risk.py` configured trade ceiling, daily realized
  plus unrealized loss budget, reservations, and peak-equity drawdown checks.
  The requested 2.5% / 3% / 10% must be established in the eventual mainnet policy
  and its caller. They are configurable, not unconditional global clamps in this
  source. Legacy defaults include an 8% daily halt and disabled drawdown halt
  (`engine.py:898`); the pure newcore gateway has no non-test callers found by
  this audit. This is an unresolved integration/policy question, not a claim that
  a live account currently has those settings. No settings were read or changed.
* Stops: reviewed placement/write-ahead ownership, provisional protection, replace
  before cancel, and missing-stop verification. F2 blocks trusting ID-only status.
* Filters: tick/step/minimum quantity/minimum notional/max quantity are represented;
  F6 breaks a general step invariant. PERCENT_PRICE is absent from the reviewed
  feasibility/newcore filter model. Applicability and consequences for the actual
  market/STOP_MARKET route were not established offline; this remains a coverage
  gap, not an independently reproduced exchange rejection.
* Restart/crash/replay: reviewed legacy durable entry intents, settlement, and
  newcore journal staged append/fsync/recovery/idempotency. Existing synthetic
  crash drills and recovery tests were selected; no process, bot or watchdog was
  stopped/restarted. F1 and F5 are the new replay/identity findings.
* Clock: reviewed midpoint/fastest-sample synchronization, stale resync, bounded
  -1021 entry retry and protective retry behavior. No new concrete clock defect
  confirmed. F5 also exposes wall-clock identity reuse.
* Partial fills: F3/F4 cover malformed execution evidence. Existing final/partial
  fill, unknown-result and reconciliation coverage was selected for baseline.

## Evidence

All six regression tests intentionally fail on the audited implementation; none
are marked xfail or skipped. Command:

```text
python -m pytest -q tests/test_lane3_recovery_redteam.py tests/test_lane3_risk_redteam.py tests/test_lane3_exchange_redteam.py --tb=short
```

Result: **6 failed**, each at its intended safety assertion, no collection errors.
Existing offline baselines: **839 passed** in two non-overlapping runs:

* 666 passed: `test_clock_1021.py`, `test_bt02_exchange_filters.py`,
  `test_risk_fill01.py`, `test_aud05_r2_wal_schema.py`,
  `newcore_risk/test_nc06_risk.py`,
  `newcore_reconciliation/test_rec02_reconciliation.py`,
  `newcore_store/test_nc02a_recovery.py`,
  `newcore_store/test_nc02a_crash_drills.py`, and
  `newcore_venue/test_nc03a_binance_usdm_testnet.py`.
* 173 passed: `newcore_risk/test_risk_loss01.py` and `test_aud04_stop_verify.py`.
  Loss-policy fixtures explicitly cover the 3% daily and 10% drawdown boundaries;
  these do not establish a mainnet runtime binding to that policy.

The full
repository suite is not a green acceptance gate here: this is an intentionally
failing evidence-only draft, with no production fix or merge authorization.

Exchange assumptions were cross-checked against Binance's official
[general API guidance](https://developers.binance.com/en/docs/products/derivatives-trading-usds-futures/general-info)
on unknown execution and duplicate avoidance, and its
[USD-M order reference](https://developers.binance.com/en/docs/catalog/core-trading-derivatives-trading-usd-s-m-futures/api/rest-api/trade)
on order and execution fields. No authenticated exchange API was contacted.

Open items: independently review these reproductions and integration reachability;
then implement accepted findings as separate small PRs with passing regressions.
Do not use this report to authorize mainnet or to describe an unverified policy
gap as a demonstrated live exploit.
