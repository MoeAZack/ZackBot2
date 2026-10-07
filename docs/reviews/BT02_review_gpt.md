# BT02 Codex review

Reviewed: 2026-10-07, Cairo

Implementation head: `ce5c40dbb0549e32105f040c02c2f78bc1ee59c4`

Verdict: **changes requested**. The shared parser/sizing function and live-engine parity refactor are sound, and the focused suite passes 29/29 on Windows. The current branch does not yet meet the owner-facing promise that the UI and advertised backtests tell whether all planned orders can execute.

## P1 — Unverified placeholder rules change default app backtest P&L

`app.run_backtest_job()` obtains the snapshot and its trust state at `app.py:288`, but passes the snapshot to `BT.run()` at `app.py:315` even when the state is `unverified`, `stale`, `invalid` or for the wrong environment. Therefore an untrusted estimate changes entries, skips, equity, PF and DD in the normal app result; the warning is only attached afterwards.

This is material, not cosmetic. I fetched the public testnet `exchangeInfo` independently at 12:00 Cairo. The branch placeholder differs substantially:

- BTC step/minQty: placeholder `0.001`; current testnet `0.0001` (10x difference);
- DOGE and 1000PEPE: placeholder `0.001`; current testnet `1` (1000x difference);
- LINK minNotional: placeholder `20`; current testnet `5`;
- most other tracked symbols also have different step/minQty values.

The report's placeholder-based `$6.5k` capital result is therefore not evidence and must not replace any card or preset number.

Required:

1. A canonical/app backtest may apply exchange rules only when `snapshot_state == ok` for the selected environment. Otherwise either block the canonical run or run legacy behavior while reporting a clearly separate, non-promotable estimate. Do not let unknown rules alter the headline equity/PF/DD.
2. Replace the placeholder with a freshly fetched, verified testnet capture before acceptance, or ship no default rule data. The direct public fetch works from the owner's PC and needs no credentials.
3. Add regression tests proving every non-OK state cannot silently change canonical results.

## P1 — Preflight says “Tradable” while a planned add cannot execute

`feasibility.preflight()` records undersized DCA/pyramid adds at `feasibility.py:222`, but `status` at line 227 depends only on the initial order. The panel maps `status=ok` to **“Tradable at your capital.”** `min_capital_all` likewise covers only the entry.

Deterministic reproduction from this branch:

- SOL, $500, entry risk 0.03%, pyramid fraction 0.5, 5 USDT minimum;
- result: `status=ok`, `executable_pct=100%`, `min_capital_all=500`;
- the same result contains one `add_undersized` because the pyramid order cannot execute.

That directly contradicts the owner's requested “tell me if all the trades will go through” behavior.

Required:

- distinguish `entry_executable_pct` from `plan_executable_pct`;
- a slot is fully tradable only when the initial order and every predeclared exposure-increasing DCA/pyramid order pass;
- `min_capital_all` must include the smallest planned add (and explain which leg sets the minimum);
- status/tag must be partial or not fully tradable when an add fails; never show the green “Tradable” tag in that state;
- retain the detailed per-leg explanation.

## P1 — Backtest partial exits still do not match the engine's step/dust behavior

The live engine floors every partial close in `Engine._market_close()` and its ladder closes the whole remainder rather than leave exchange dust. `backtest.close()` at `backtest.py:232` closes the raw fractional quantity. This can change remaining size, subsequent stops/TPs, trade duration and P&L for small accounts—the exact population BT02 exists to evaluate.

Required in BT02 rather than an unspecified follow-up:

- apply the same step rounding to TP1, ladder and partial basket/runner exits;
- reproduce the engine's no-dust whole-remainder rule;
- add long/short tests near a step boundary, including a partial that rounds to zero and a ladder remainder below the venue minimum;
- preserve full-stop/full-close behavior and document any Binance reduce-only minimum exemption separately from the engine-parity rule.

## P2 — Local-file import self-certifies environment and freshness

`exchange_rules.build_file()` marks any structurally valid JSON file `verified=True` and gives it the current time by default (`exchange_rules.py:56-61`). A saved mainnet response can therefore be labelled testnet and freshly verified merely by passing `--env testnet`; an old file is also restamped as current.

Required:

- only direct `fetch` may automatically produce a verified snapshot;
- file import defaults to unverified and must preserve/provide the real capture time;
- if an explicit trust override is retained, make it noisy in the CLI and record that provenance distinctly from a direct fetch;
- test wrong-environment and old-file imports cannot become green by default.

## P2 — “Current capital” preflight uses typical, not current, ATR

The UI describes whether orders are tradable at the current capital, but `preflight_market()` sizes with the median ATR/price ratio of the last 180 candles. The engine sizes with the latest closed-candle ATR. Near a minimum, the card can therefore disagree with the next real signal even with correct exchange rules.

Required:

- use latest closed-candle ATR for the primary `would execute now` status;
- optionally show the 180-bar median as a separate planning/sensitivity estimate;
- expose the price/ATR timestamp and basis in the response and tooltip.

## Decisions on the review-request questions

1. Do not accept or publish results from the placeholder. Use a verified direct testnet fetch first.
2. Lab/research runs that can feed a promoted number must use a versioned verified snapshot. Explicit exploratory legacy runs may remain available but must be labelled non-execution-realistic.
3. Partial-close parity belongs in BT02 for the reasons above.
4. Update preset cards only after BT01+BT02 reruns on the accepted verified snapshot; keep DCA presets unverified meanwhile.
5. Count per signal candle, matching the actual order opportunity; additionally report distinct signal episodes as a secondary diagnostic if useful.
6. Latest ATR drives current feasibility; the 180-candle median may be shown separately for planning.

Do not advance the stacked DCA-default or COPY100/COPY200 behavior PR until these truthfulness gaps are fixed. No runtime, installer, settings or order action is required for this review.

## Round 2 combined-head re-review — one P1 remains

Reviewed combined implementation head `2c95341ab081c2529e2dce1b099ffe65a28cab7e` on 2026-10-07 (Africa/Cairo).

The original 3 P1 and 2 P2 findings are fixed. The comparison merge correctly keeps the direct-fetch snapshot and replay
coverage, removes the local-file trust override, enforces trusted provenance, uses side-correct add prices, excludes
forming/stale candles from a green status, and preserves the entry/full-plan field meanings. The focused Windows suite
passes **48/48**.

### P1 — preflight can still show a green full plan when the live leverage gate must block every add

Affected: `feasibility.slot_order_legs`, `feasibility.preflight`, the Strategies-tab feasibility badge; live reference:
`engine._add_gate`.

The preflight checks each entry/DCA/pyramid leg independently against exchange minimums, but never simulates cumulative
slot exposure. The live engine applies `used + add_notional <= MAX_LEVERAGE * equity * share` before every add. Therefore
the UI can report `status=ok`, `entry_executable_pct=100` and `plan_executable_pct=100` while the advertised plan cannot
be built under its configured leverage cap.

Deterministic reproduction: $100 capital, share 1, max leverage 1, SOL $100, a one-level equal-size DCA, permissive
exchange minimums. The entry is leverage-capped to 1 SOL / $100 and passes. Preflight also passes the 1 SOL safety order
at $99 and returns a green 100% full plan. Live cumulative exposure would be $199 against a $100 cap, so `_add_gate`
must reject it. Increasing capital does not resolve a structurally over-cap plan because both quantities and the cap scale
together.

Fix: simulate cumulative exposure for every planned leg using the same per-slot leverage-cap semantics as `_add_gate`.
Distinguish exchange-minimum failure from leverage-plan failure. A plan blocked by leverage must never be green and must
not recommend a misleading higher-capital number; advise lowering planned add size/risk or raising the allowed leverage
instead. Add DCA and multi-pyramid regressions, including the reproduction above and a plan that stays below the cap.

The disclosed hedge-mode partial-close runtime question (D3) remains a separate testnet/mainnet-safety ticket; D6/D7 are
non-blocking follow-ups. No installer, runtime, settings or order action occurred in this review.

**Verdict: CHANGES REQUESTED — one contained P1.**

## Round 3 — accepted

Reviewed exact fix head `e224a8bb96b6be2942244c84caa6c095bc7e057f` on 2026-10-07 (Africa/Cairo).

The cumulative slot-leverage gate now follows the live engine's ordering and formula. DCA and pyramid plans are evaluated
as ordered sequences; a structural leverage failure is separated from exchange minimums, later unreachable legs are
identified, and the UI no longer recommends more capital for a ratio that remains over-cap at every capital level.

Independent evidence:

- focused BT02 suite: **52/52 passed**;
- GitHub fast and CodeQL gates: passed on the exact implementation head;
- the original $100 / 1x / equal-size DCA reproduction now reports `partial`, plan 0%, and no capital recommendation;
- quick UI harness on the implementation head rendered all three viewports and the Strategies tab without JavaScript or
  server errors. Its only failure was the unrelated Trades calendar click (162/163); the same 162/163 failure reproduced
  twice on this head and once on unchanged `origin/master`, so BT02 did not introduce it.

Non-blocking follow-ups remain: D3 hedge-mode partial-close runtime proof; D6/D7 cleanup; and repair the pre-existing T02
calendar-day harness check. A later `not_reached` add is currently carried in `add_undersized` for detail display, so its
aggregate warning wording can be tightened with D7; it does not affect feasibility status or capital advice.

**Verdict: ACCEPTED for protected merge.** No installer, bot restart, credential, or order action was required.
