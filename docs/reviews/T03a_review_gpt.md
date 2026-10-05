# T03a Codex review

*Reviewed 2026-10-06 02:44 Cairo (Africa/Cairo). Target: `t03a-leverage-fallback-v2` at `fc292143b975097c7e06a6eae12679a788f8c1b0`, based on protected `master` `ad1d58405f17fa73a15e8f03c425a02f0530f0c2`.*

## Verdict

**Code review passed with no blocking findings. T03a is ready for its testnet runtime gate, but is not finally accepted or mergeable until the owner authorizes the install and the bounded canary proves the new path.**

## Safety conclusions

- The fallback remains fail-closed: after two failed leverage-change attempts, an entry proceeds only when a fresh signed position-risk response reports `1 <= current <= min(MAX_LEVERAGE, coin maximum)`. Missing, unreadable, zero, negative or above-cap values skip the entry.
- The cap must remain `min(setting, coin maximum)`. Ignoring a lower exchange maximum would weaken the configured execution boundary without providing a benefit.
- Taking the maximum leverage across hedge-mode rows is conservative and correct. Binance exposes leverage on each returned position row; accepting only the maximum ensures neither side can hide a higher value.
- Not caching a refused change is the safe choice. It costs one additional signed read only after both change attempts fail, while allowing later entries to recover automatically if Binance starts accepting the requested setting.
- Successful leverage changes follow the old path and do not perform the extra read. Above-cap and unknown results send no entry order.

## Evidence

- Exact remote head and complete six-file diff from protected `master` inspected; `git diff --check` is clean.
- Windows official pytest on the final head: `tests/test_safety.py tests/test_verify.py` — **94 passed in 5.50 seconds**.
- GitHub on final head `fc29214`: required `verify fast` and `verify full` both **PASS**, with full tests, both strict replays and the UI harness included.
- The eight focused tests cover within-cap, exactly-at-cap, above-cap, unknown/read failure, coin maximum, per-coin counts, manual entry and the client endpoint.
- Binance's current USD-M documentation confirms `GET /fapi/v2/positionRisk` is a signed read endpoint accepting `symbol` and returning `leverage` and `positionSide`: https://developers.binance.com/en/docs/catalog/core-trading-derivatives-trading-usd-s-m-futures/api/rest-api/trade
- No installer, restart, account call or order was performed during this review. The installed application remains unchanged.

## Non-blocking follow-up

- `lev_refusals` is an in-memory session counter. Its panel snapshot is not atomic with updates, so the first refusal for a new symbol could theoretically produce one inconsistent or failed status poll. This does not affect entry safety; fold it into the planned T06-T09 lock-free read model rather than expanding this execution ticket.
- Before mainnet, consider moving the read to Position Information V3 or the account stream only as part of the account-adapter work. V2 is currently documented and appropriate for this bounded fallback, while Binance recommends the user-data stream where stronger timeliness is required.

## Runtime gate required before acceptance

1. Owner authorizes one normal installer run because it stops/restarts ZackBot.
2. Re-prove PAPER/testnet, exact clean reviewed head, healthy exchange/engine, zero errors, zero unprotected/untracked/orphan positions, and all lots protected immediately before installation.
3. Install once, then verify build identity, health, reconciliation and existing testnet stops.
4. Run one bounded SOL or XRP canary only after recording its symbol, side, maximum risk/notional, expected refusal outcome, hard-stop assertion and cleanup rule. Confirm the panel counter agrees with the log and that any opened lot has an exchange-side stop.
5. If the refusal reports unknown or above-cap leverage, the canary must be skipped with no entry order. Any protection or reconciliation failure blocks acceptance and triggers the documented restore path.

