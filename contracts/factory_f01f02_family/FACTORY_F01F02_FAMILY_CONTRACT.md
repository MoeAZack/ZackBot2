# FACTORY-F01F02-FAMILY: correlated-family contract (C-F01 `ema_mom` / C-F02 `exp_trend` long)

**Status: DRAFT — unsigned.** `factory_f01f02_family_contract.v1.json` is authoritative; the JSON wins any conflict.

Assignment: Codex ruling 6507317942 (contract/repro only). Builds on the SIGNED
`contracts/factory_f01f02_repro` (160 F01 / 171 F02 closed trades, 131 shared round trips, Jaccard 0.655; entry
stream 160 / 171; 228 raw long signals each). Citations are file:function at master `0dc9f05`. The four candidate
functions are pinned by source sha256 in the JSON; a mismatch means this contract must be re-drafted.

## Family identity

- One correlated family `trend_long` (`engine.CANDIDATE_FAMILY`), **C-F01 primary**, C-F02 secondary.
- Never two full risk budgets: both slots count against **one** cap of `CANDIDATE_MAX_POS` = 4.
- `exp_trend` = `_one_side(sig_ema_mom)` (strategies.py:sig_exp_trend), so both slots see the same long signals on
  every bar. They differ only by management: F02 adds a 3R target and a 180-bar cap.

## Collision semantics (as the engine implements them)

- **Claims** (engine.py:Engine._claims): open lots plus pending, resting and unconfirmed entries.
- **Isolation** (engine.py:Engine.candidate_block): a new entry is refused on a coin another slot claims when either
  slot is a candidate. C-F02 can never enter a coin C-F01 claims, and the reverse also holds.
- **Shared cap** (engine.py:Engine.candidate_block): every family claim counts, on any coin. The 4th claim blocks
  both slots. Non-family lots never count. Each slot's own max_pos of 4 still applies on top.
- **Slot priority** (engine.py:Engine.cycle): the outer loop runs over slots in `S['SLEEVES']` order and the inner loop
  over coins in `CAND_FIXED18` order. `sync_candidate_slots` appends C-F01 before C-F02. **C-F01 therefore wins every
  same-bar collision.** It decides all its signals before C-F02 decides any, and `candidate_guard` runs first.
- **The 131 shared fills, with both slots enabled:** each one is executed once, as a C-F01 lot under F01 management.
  C-F02 is refused by isolation. This also holds for all 160 shared entries.
- **When C-F02 can trade:** only when C-F01 is off, or when C-F01 is refused for a runtime reason that does not also
  refuse C-F02.

## Auto-disable (engine.py:Engine.candidate_guard, every cycle, before entries)

| Trigger | Condition |
|---|---|
| drift | the slot differs from its frozen card on any of key, sides, risk, max_pos, symbols (compared as a set), mgmt or tf |
| protection | a lot has `stop_miss` with why `restoring` or `owner_check`; `checking` alone does not trigger it |
| accounting | a closed trade's costs are still `pending` at least 6 h (21,600 s) after close; only the last 3,000 history rows are scanned |
| guard_error | any exception in the guard switches **every** enabled candidate off (fails closed) |

What it does:
- New entries stop: `enabled` is set to False, and `state['candidate_disabled']` records the why and the time.
- Open lots keep their stop and management.
- Each slot is handled on its own.
- **Only Integration re-enables a slot.** There is no automatic path back on.

## Family-book replay (DERIVED from existing packets, non-promotable, not an evaluation)

This replay merges the two corrected_core20 packets, with sha256 verified and every trade before the cutoff. The
assumed setup:
- one account;
- one cap of 4;
- isolation;
- F01 decides first on each bar;
- identical signals.

By induction, C-F02 never opens. Every reason C-F01 skips a signal also refuses C-F02: it holds the coin, the cap is
full, the halt is active, or the same floor/sizing applies.

| | value |
|---|---:|
| Family trades | **160** (all C-F01) |
| Family net R / PF / max DD R | +101.091624 / 2.773052 / 7.551214 (= F01 packet) |
| C-F02 trades taken / suppressed | 0 / **171** (standalone net R +56.692730) |
| shared round trips executed once by C-F01 | 131 (Σ R −41.799091) |
| same entry, F02 exits at tp, C-F01 keeps its own exit | 29 (F02 Σ R +85.403283 vs F01 +142.890708) |
| F02-only entries refused by cap/isolation | 11 (Σ R +13.088532): 8 proven from closed trades; 3 (Sep 18-19, 2026) need C-F01 lots still open at END, which only an authorized run can confirm |

Limits: runtime sizes each slot on its own 1/6 share. Runtime-only refusals are not modelled: order failure, exchange
rules, and a guard disable of C-F01.

## Forbidden

- Any read at or after 2026-09-28T00:00Z.
- A new market evaluation or backtest.
- Enabling or registering a slot, or runtime wiring.
- A dual full-risk allocation.
- Promoting the replay.
- Writing order or trade ids.
- Credentials or mainnet.
- Tuning.

## Open questions for Integration

- **OQ1:** a guard disable of C-F01 leaves C-F02 enabled, and C-F02 then inherits the family. Is that intended, or
  should the disable apply to the whole family?
- **OQ2:** slot priority depends on the persisted SLEEVES order, and drift does not check it. Should F01-before-F02 be
  pinned?
- **OQ3:** the symbol order decides who gets the last cap slot, but drift ignores order. Is that acceptable?
- **OQ4:** C-F02 takes 0 trades while C-F01 is on. Should only C-F01 be enabled?
- **OQ5:** `_deferred` entries are not in `_claims`. We believe this is unreachable for candidates, which have no true
  book. Please confirm.
- **OQ6:** the accounting scan covers only `history[-3000:]`. Is that window acceptable?
