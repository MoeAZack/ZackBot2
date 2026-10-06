# T03c: automatic leverage handling. Review request

## Current state

| Item | Value |
|---|---|
| Branch | `t03c-auto-leverage`, now merged with T05 (accepted code, `e58f0c2` + Codex `1be0bda`) and therefore with master `d68d6ef`. If T05 merges to master first, the branch is merged with master again before the merge gate. The head is in the READY FOR CODEX comment |
| Roadmap | Not yet in ROADMAP.md: the owner asked Claude for it in chat on 2026-10-06 ("Yes q t03c", after confirming the problem can occur on mainnet). Codex owns ROADMAP.md / PROJECT_STATUS.md; please place it (suggested: right after T05, before T06) |
| Class | **Order path (entry gate).** It changes which entries may go ahead after Binance refuses a leverage change. No new order types and no change to sizing, stops or exits |
| Owner | Asked for this (2026-10-06): leverage handling must be automatic, because more venues are coming (tradfi, stocks). The owner confirmed it is in scope because it can happen on mainnet too |
| Runtime | Needs CI green + Codex code approval, then a bounded testnet canary under the owner's standing testnet approval (never mainnet) |

## Problem

T03a lets an entry proceed after a leverage refusal only if the coin is ALREADY at or below the cap. A coin stuck above the cap (SOL at 20x with a 10x cap on testnet, which answers -1000) is skipped forever until someone changes it by hand in Binance. Mainnet can refuse too: open orders or positions above the new bracket, or venue-specific rules.

## The change

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

## Dropped from the design draft

- A retry ladder of lower leverage values. Refusals are not value-specific (testnet -1000, mainnet open-order/bracket errors), so extra POSTs add API load without a path to success. The cooldown handles repeated attempts instead.
- Liquidation price per coin. Under cross margin, liquidation is account-wide, so the worst-case margin ratio is the account-level equivalent and needs no per-coin bracket math.

## Tests (`tests/test_leverage_auto.py`, 17 test functions, 18 runner entries with the parametrized margin-type case; T03a tests in `tests/test_safety.py` unchanged and passing)

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

## Evidence (Claude sandbox, stand-in runner, NOT official pytest)

- On master + T05 + T03c: tests/test_leverage_auto.py + tests/test_fills.py: 64 passed. The full suite is in the READY comment.
- Merge with T05: two textual conflicts (module constants next to the T05 writer, and the new `_lev_cool` attribute next to `_fillw`), both resolved by keeping both sides; no logic overlap.
- The panel JS parses (node). The UI harness runs on GitHub (verify full).

## Questions for Codex

1. Are a 50% worst-case margin ratio and 1.5× entry slippage acceptable defaults, or should they be settings? They are module constants for now, to keep the settings surface unchanged.
2. Canary plan: one bounded testnet entry on SOL (20x, cross) after install, verifying `accepted='exposure'`, the stop placed, and the effective leverage reported ≤ 10x. Then the normal exit rules apply. Is that enough, or do you want a manual close right after?
