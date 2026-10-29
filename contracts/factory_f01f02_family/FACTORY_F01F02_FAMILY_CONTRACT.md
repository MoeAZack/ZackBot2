# FACTORY-F01F02-FAMILY: correlated-family contract (C-F01 `ema_mom` / C-F02 `exp_trend` long)

**Status: DRAFT — unsigned, pending Integration signature.** `factory_f01f02_family_contract.v1.json` is authoritative;
the JSON wins any conflict.

Assignment: Codex ruling 6507317942 (contract/repro only). **Amendment 1** (per 6508380081) encodes the resolved
Integration rulings 6507748888, 6508164005 and 6508707059 (OQ1–OQ6) against the FACTORY-CAND-02 engine (PR #247,
`2683be7`). Builds on the SIGNED `contracts/factory_f01f02_repro` (160 F01 / 171 F02 closed trades, 131 shared round
trips, Jaccard 0.655; entry stream 160 / 171; 228 raw long signals each). Citations are file:function at `2683be7`
(re-cite the master merge commit once #247 merges; the engine content is identical). Seven candidate functions are
pinned by source sha256 in the JSON; a mismatch means this contract must be re-drafted.

## Family identity

- One correlated family `trend_long` (`engine.CANDIDATE_FAMILY`), **C-F01 primary**, C-F02 secondary.
- Never two full risk budgets: both slots count against **one** cap of `CANDIDATE_MAX_POS` = 4.
- `exp_trend` = `_one_side(sig_ema_mom)` (strategies.py:sig_exp_trend), so both slots see the same long signals on
  every bar. They differ only by management: F02 adds a 3R target and a 180-bar cap.
- **Enable policy (OQ4): C-F01 only.** C-F02 stays OFF as the reference / alternative-management cell. It may get its
  own separately authorized evaluation later. It must never run beside C-F01 just to record zero trades.

## Collision semantics (as the engine implements them)

- **Claims** (engine.py:Engine._claims): open lots plus pending, resting, unconfirmed and **deferred** entries. OQ5:
  an ORDER-SYM-01 `_deferred` reservation counts too. The entry being re-decided in engine.py:Engine._reconsider_one
  (`_defer_cur`) never counts against itself.
- **Isolation** (engine.py:Engine.candidate_block): a new entry is refused on a coin another slot claims when either
  slot is a candidate. C-F02 can never enter a coin C-F01 claims, and the reverse also holds.
- **Shared cap** (engine.py:Engine.candidate_block): every family claim counts, on any coin. The 4th claim blocks
  both slots. Non-family lots never count. Each slot's own max_pos of 4 still applies on top.
- **Slot priority** (engine.py:Engine.cycle): the outer loop runs over slots in `S['SLEEVES']` order and the inner loop
  over coins in `CAND_FIXED18` order. `sync_candidate_slots` appends C-F01 before C-F02. **C-F01 therefore wins every
  same-bar collision.** It decides all its signals before C-F02 decides any, and `candidate_guard` runs first.
- **Order pins:**
  - OQ2 (engine.py:Engine.candidate_guard): with more than one member enabled, the `SLEEVES` order must be exactly
    C-F01 before C-F02. A mismatch is family drift and fails closed.
  - OQ3 (engine.py:Engine.candidate_drift): symbols compare as the exact ordered `CAND_FIXED18` list, not a sorted set.
- **The 131 shared fills, if both slots were enabled:** each one is executed once, as a C-F01 lot under F01
  management. C-F02 is refused by isolation. This also holds for all 160 shared entries.

## Auto-disable (engine.py:Engine.candidate_guard, every cycle, before entries)

| Trigger | Condition |
|---|---|
| drift | the slot differs from its frozen card on any of key, sides, risk, max_pos, symbols (exact order), mgmt or tf |
| family order | two or more members enabled and out of C-F01, C-F02 order (OQ2) |
| protection | a lot has `stop_miss` with why `restoring` or `owner_check`; `checking` alone does not trigger it |
| accounting | a closed trade's costs are still `pending` at least 6 h (21,600 s) after close. The guard scans **all** of history plus the durable pending index (OQ6) |
| guard_error | any exception in the guard switches **every** enabled candidate off (fails closed) |

What it does:
- **Family-level fail closed (OQ1):** any member's trigger switches every enabled member of `trend_long` off in the
  same pass. A member without its own trigger records `family trend_long fail-closed by <member>: <why>`. There is
  no silent fallback to C-F02 with different management.
- New entries stop: `enabled` is set to False, and `state['candidate_disabled']` records the why and the time.
- Open lots keep their stop and management.
- **Only Integration re-enables a slot.** There is no automatic path back on.

### Durable pending-cost index (OQ6; rulings 6508164005, 6508707059)

- **Key:** `state['candidate_cost_pending']` = {history row id: {sleeve, symbol, closed}}.
- **Set** at close in engine.py:Engine._finish (via `_candidate_pending_note`) when a candidate row's costs are still
  pending. An unreadable status is indexed anyway. The entry survives the `history[-3000:]` eviction and restarts.
- **Cleared exactly once** when the row settles, as `measured` or `acknowledged`. The FILL-PNL-02 operator
  acknowledgement counts as settlement. The clear is done by engine.py:Engine._candidate_pending_clear, from
  engine.py:Engine._cost_final (row kind, reached from `apply_costs`) or from the guard's reconciliation in
  engine.py:Engine._candidate_pending_why.
- **Persisted immediately:** every clear calls `save_state()`. If the save fails, the entry stays on disk, so the
  family stays fail-closed after a restart.
- **Evicted, unsettleable row:** keeps the family OFF on its stored close time until a future explicit Integration
  acknowledgement path clears it. It is never cleared by time.

## Family-book replay (DERIVED from existing packets, non-promotable, not an evaluation)

The facts are unchanged by amendment 1. This replay merges the two corrected_core20 packets, with sha256 verified and
every trade before the cutoff. Both packets are on `evidence/f01f02-core20-packets` @ `1ef6f11`, and their blob
hashes equal the pins. The assumed setup:
- one account;
- one cap of 4;
- isolation;
- F01 decides first on each bar;
- identical signals.

By induction, C-F02 never opens. Every reason C-F01 skips a signal also refuses C-F02: it holds the coin, the cap is
full, the halt is active, or the same floor/sizing applies. This is the basis for the OQ4 C-F01-only policy.

| | value |
|---|---:|
| Family trades | **160** (all C-F01) |
| Family net R / PF / max DD R | +101.091624 / 2.773052 / 7.551214 (= F01 packet) |
| C-F02 trades taken / suppressed | 0 / **171** (standalone net R +56.692730) |
| shared round trips executed once by C-F01 | 131 (Σ R −41.799091) |
| same entry, F02 exits at tp, C-F01 keeps its own exit | 29 (F02 Σ R +85.403283 vs F01 +142.890708) |
| F02-only entries refused by cap/isolation | 11 (Σ R +13.088532): 8 proven from closed trades; 3 (Sep 18-19, 2026) need C-F01 lots still open at END, which only an authorized run can confirm |

Limits: runtime sizes each slot on its own 1/6 share. Runtime-only refusals are not modelled: order failure and
exchange rules. A guard disable of C-F01 no longer hands the family to C-F02 (OQ1).

## Forbidden

- Any read at or after 2026-09-28T00:00Z.
- A new market evaluation or backtest.
- Enabling or registering a slot, or runtime wiring.
- A dual full-risk allocation.
- Promoting the replay.
- Writing order or trade ids.
- Credentials or mainnet.
- Tuning.

## Resolved rulings (Integration 6507748888 / 6508164005 / 6508707059)

- **OQ1:** family-level fail closed. Either member's disable switches the whole enabled family off.
- **OQ2:** C-F01 must come before C-F02 when both are enabled. A mismatch is drift.
- **OQ3:** the exact ordered `CAND_FIXED18` list is pinned.
- **OQ4:** C-F01 only. C-F02 stays OFF as the reference cell.
- **OQ5:** deferred reservations are counted in `_claims`.
- **OQ6:** the 3,000-row window is rejected. The guard uses a whole-history scan plus the durable index, cleared once
  on settlement and persisted immediately. An evicted row with unsettled costs stays OFF until Integration
  acknowledges it.

Deferred: an Integration acknowledgement path for evicted entries, a separately authorized C-F02 evaluation, and
PAPER enablement.
