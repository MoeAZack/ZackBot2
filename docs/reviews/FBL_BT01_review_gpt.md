# FBL-BT01 intrabar path — Codex review

*2026-10-07 09:20 Africa/Cairo; exact reviewed head `30a68e15340cadb7a9f2673db9c98e7ec5a30a03`.*

## Verdict

**Changes requested — one Critical/P1 path defect remains.** The reported DCA TP look-ahead is fixed, but a candle that
crosses DCA safety orders and the pre-existing hard stop still closes the original quantity before walking the path. That
understates the basket loss and is not a conservative approximation.

## P1 — hard-stop precheck skips safety-order fills that occur before the stop

`backtest.run()` checks the stop against the whole candle and closes immediately before `walk_path()` runs. On a monotonic
adverse leg, however, the market reaches each configured safety-order level before reaching the deeper basket stop. The
live engine can therefore add exposure at those levels, and the exchange stop then closes the enlarged basket. The new
backtester instead closes only the initial unit and avoids the added losses and fees.

Independent deterministic reproducer using the branch's hand-built DCA fixture:

- entry `100.02`, safety levels `99.02`, `98.02`, `97.02`, hard stop `95.02`;
- candle `open=100`, `high=100.2`, `low=94`, `close=99`;
- declared long path is `open -> high -> low -> close`, so all three safety orders precede the stop on the high-to-low leg;
- reviewed code reports **`-0.209243R`**, proving it stopped the initial quantity without the safety fills;
- the declared basket risk is approximately 1R before costs, so the path-consistent result must include the three adds
  and be around `-1R` plus fees/slippage.

The same defect applies symmetrically to shorts. It can materially improve DCA results in volatile stop candles, so the
preset re-runs are not yet trustworthy.

### Required correction

1. Put the stop that existed at the candle open into the adverse-leg event queue. Safety levels encountered earlier on
   that leg must fill before the stop; a gap already through the stop still fills at the open.
2. Preserve the explicitly chosen stop-first convention only for the ambiguous case it is intended to cover (an
   already-existing favourable target and stop both reachable in the candle), without suppressing adverse safety-order
   events that precede the stop on the declared path.
3. Add deterministic long and short tests that assert every crossed safety order fills before the basket stop and that
   the resulting loss/fees reflect the enlarged quantity. Include a gap-through-stop case and a safety-level/stop tie.
4. Re-run the mutation checks and every affected preset only after this reproducer passes.

## Evidence run

The reproducer was executed in the official Windows build environment against exact head `30a68e1`; no installer,
runtime, settings or order action occurred. Full gates were not started because this Critical result blocks acceptance.

## Round 2 — accepted

*2026-10-07 10:46 Africa/Cairo; corrected implementation `d5ba3dc4f39030a4c769ed44a5fb411fff0802d0`, integrated with current master at `f9323785bd4aca33bccdb0617e18ac8a1e4ac33a` before this review update.*

**Code review clean.** The stop is now an adverse-leg event ordered with safety orders. A gap already through the stop
fills at the open without adds; every safety order reached before a deeper stop fills first; an exact safety/stop tie uses
the conservative add-then-stop result. Long, short, gap, tie, target-plus-stop, raised-stop and pyramid cases are covered.

Independent Windows evidence:

- the original deterministic fixture changed from `-0.209243R` to **`-1.038717R`**, including all three safety fills;
- `tests/test_bt_intrabar_path.py`: **23 passed**;
- `tests/test_causality.py` plus `tests/test_verify.py`: **30 passed** in 4m33s;
- diff validation is clean, and the only master integration is the docs-only `CLAUDE.md` working agreement.

The corrected preset results are materially worse and are now credible enough to guide later research: the current DCA
profiles must remain labelled unverified and must not be promoted from these results. BT02 exchange-filter feasibility is
the next realism ticket before COPY100/COPY200 calibration.

Proceed with the exact-head `verify fast`, CodeQL and one labelled `verify full` gate. This ticket changes research and
backtest behaviour only; it does not require an installer, runtime restart or exchange canary.
