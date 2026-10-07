# Quantity-tolerance review — Codex

Reviewed exact head `bca5141ed0bb2d11071c4ac3584b5aacdcf9c5e1` on 2026-10-07 (Africa/Cairo).

## Verdict: changes requested

The tracked-lot fix is directionally correct and the focused suite passes (`21 passed`). The original small-lot stop-out bug is covered. One safety hole in the same tolerance path remains and should be fixed before merge.

### P1 — an untracked position of exactly one exchange step is still invisible

Affected area: `engine.py`, `Engine.reconcile()`, the no-lot/untracked branch near line 1571.

That branch still uses `have > step`. Therefore an exchange position equal to exactly one step is not added to `self.untracked`, even when it is well above Binance's minimum notional. It receives no bot stop and does not block a new entry on the same symbol/side.

Independent reproduction at this head:

- rules: BTC step `0.001`, minimum quantity `0.001`, minimum notional `$5`;
- mark: `$120,000`;
- Binance position: `0.001 BTC` (`$120` notional);
- engine lots: none;
- call `reconcile()` twice;
- observed: `self.untracked == {}` and no health error.

This is not harmless dust: it is a normal, tradable `$120` orphan without a bot stop. The PR body already notes the condition, but it is the untracked half of the same one-step boundary being corrected elsewhere in this PR.

Recommended narrow fix:

1. In the no-lot branch, compare against `_qty_tol(step)` rather than `step`, while retaining the existing resting-entry and dust exclusions.
2. Add a regression test proving an exactly-one-step, above-min-notional orphan is reported after the existing two observations.
3. Add companion boundaries: below-min-notional one-step dust remains ignored, and an exactly-one-step filled resting entry is not reported as orphaned.

No installer, bot stop/restart, credentials, or trading action was performed.
