# Multi-position transfer review and EX13 receipt — 13 November 2026

ROADMAP FIT: owner-approved strategy-economics review. Multi-position evidence is a disclosed second-tier sensitivity study, not a replacement for max-one rule v1. Result head **45fe30bcc8ea7b4ae1e5d3d66f7f3d1d73523b3f**, variant freeze a2c5dd60. Sources: exp_trend at 9cb4341f2d94c21434492515b8e85f2b42c148ce; CP-Y2 at 646ab3fa1be92614998905e6ab3497ab03a7d382. The native backtest/feasibility/strategies files at these two source heads are identical; CP-Y2 is separately registered by research/newstrat_cp3.py.

## 1. Independent signals, admission and cash

The independent code imports neither Claude's runner nor its signal/backtest implementation. It reuses Codex's earlier independently written input/indicator construction, implements the breadth formula directly, then simulates positions, pending-order admission, shared notional room, quantity flooring, gaps, stop/target paths, half exits, breakeven, time exits and funding. Signal tuples include symbol, decision-candle open UTC, side and eligibility.

Trend matches 1,542 raw tuples / 1,320 eligible signals at each position limit. CP-Y2 matches 1,779 raw tuples / 1,504 eligible signals at both limits. All signal sets have zero missing/extra tuples. Daily breadth uses BTC plus the 31 available-history coin series, EMA20 with its frozen 20-observation minimum, the frozen 25%/75% five-day first-cross conditions and each traded coin's EMA21. D01 entry eligibility is applied afterwards; it is not a liquidity-filtered breadth denominator.

| Case | Primary closed trades matched | Worst closed trades matched | Mismatches | Primary net15, order 0 |
|---|---:|---:|---:|---:|
| exp_trend max1 | 323 / 323 | 323 / 323 | 0 | +$304.455825 |
| exp_trend max3 | 750 / 750 | 750 / 750 | 0 | +$513.848557 |
| exp_trend max5 | 977 / 977 | 977 / 977 | 0 | +$667.350712 |
| CP-Y2 max3 | 112 / 112 | 113 / 113 | 0 | +$163.950018 |
| CP-Y2 max5 | 183 / 183 | 185 / 185 | 0 | +$166.917282 |

The independently selected entry/exit sequence and reasons match in all ten order-0 cases: 4,693 closed-trade comparisons. Independently reconstructed quantities, fills, fees and funding produce the submitted P&L and turnover, including CP-Y2's 46 primary partial exits at max3 and 73 at max5. Maximum P&L difference is about $3.55e-15, turnover $5.68e-14. The submitted trade CSVs do not contain separate per-fill fee/funding columns; the independent detailed fill/cash records provide that decomposition, and the comparison is against their resulting P&L/turnover identities.

The summary medians of all six submitted variants at both capacities and both paths were separately recomputed from their per-order summaries: 24 arithmetic checks pass. This does not independently simulate the other 39 admission orders. LF/CRLF is normalized for content identity; result head and full evidence hashes remain recorded.

Trend ends flat in the independently simulated cases. CP-Y2 primary leaves 3/5 open lots and its worst path 2/3. Their cumulative partial cash, remaining marks and estimated liquidation P&L are included in independent_mp_review.json. These are not just unrealized P&L: an open lot may already have realized half. Marks use each timeframe's last complete alpha candle; daily and 4h endpoints differ, so do not silently combine their terminal marks as if simultaneous. Excluding open lots from completed-trade gates does not authorize omitting them from portfolio equity.

## 2. Evidence tier and economics

**ACCEPT as a weaker second tier.** Preserve `POST_HOC_VARIANT_MP3_PASS` / `POST_HOC_VARIANT_MP5_PASS` or FAIL and add `TIER2_SELECTED_CONSUMED_SYMBOL_DIAGNOSTIC / APPROX_FIXED_RISK`. Do not label these rule-v1 passes, independent new strategies, untouched validation or qualified durable profitability. Six survivors were selected from an earlier broad screen; capacities 3 and 5 were chosen after max-one outcomes. Keep all variants and failures in the ledger. Do not search new capacities or favorable coin subsets after these results.

| Variant | Submitted median meanR15 | meanR23 | ex-best R15 | Order-0 closes/week | Closed DD in R, order 0 |
|---|---:|---:|---:|---:|---:|
| Trend max3 | +0.095451 | +0.070626 | +0.075167 | 3.50 | 30.26 |
| Trend max5 | +0.084110 | +0.059115 | +0.065758 | 4.56 | 36.46 |
| CP-Y2 max3 | +0.160681 | +0.150766 | +0.119337 | 0.52 | 8.02 |
| CP-Y2 max5 | +0.164417 | +0.155336 | +0.138515 | 0.86 | 20.10 |

These are positive after the disclosed modeled fees/slippage/funding proxy, not native signed funding. Closed DD is not marked-account DD and is reported rather than used as a new veto. Higher trend capacity increases trading but lowers per-trade expectancy. CP-Y2 is an intermittent daily complement, not a frequent replacement alone.

CP-Y2's 112/183 primary trades arise from only 46/48 distinct entry dates; early/later counts of dates are 27/19 and 29/19. Multiple same-event coins are correlated observations, not new independent market episodes. This does not retroactively alter the frozen 30-closes-per-half gate, but it weakens confidence from the larger trade count. Trend has 679/823 distinct primary entry times at max3/max5.

The earlier $10m implementation limitation still applies. Both the risk budget and cap drift with native realized equity; 15/23 bp are added after base-cost admission. The stated 5 × $8 initial-stop budget is an approximation before drift, costs and gap risk. Native notional room is shared across the standalone sleeve's open positions using previous-close valuation; it is not an invariant ceiling on market value after price moves. The existing exact-fixed companion contract remains the smallest next research correction. No core-engine change is required or performed by this review.

## 3. Bear-breakdown ruling

Keep its primary numerical results, since the frozen worst path was a diagnostic. **Reject using these results to justify an additional or expanded bear allocation.** At max1, worst median R23 is -0.011009 and early/ex-best R15 are negative. At max3 and max5 even worst median R15 is negative, -0.017214 / -0.026033, with R23 -0.047607 / -0.055659. This is path sensitivity at every capacity, not a lack of portfolio slots that the variant solved.

Label `PRIMARY_PASS / PATH_FRAGILE / NO_NEW_ALLOCATION`. Existing account 2 OLD3 is not changed. No post-hoc trailing-parameter rescue or new capacity scan. Actual sub-bar/forward execution evidence would be needed to resolve this risk without selecting the favorable path.

## 4. Read-only runtime feasibility

Runtime copies for accounts 2 and 4 are source-equivalent to 640292a20527ab42d523f359462b30042d3066fe, with account4's separately documented H5 launcher registration. Sources and launcher provenance are captured in draft #341.

**Trend on account4: technically supported by existing strategy and 4h code.** No new alpha code merge is required. It needs its own explicit 31-symbol sleeve, both sides, unchanged management, max3 and an allocation/risk target. Explicit symbol lists bypass the global `all` selection but still require connected exchange rules and symbol switches. Candle collection takes the union of sleeve symbols. Before the later owner instruction, no such sleeve existed; other sleeves had explicit original9 lists. Existing preset is custom, capital cap $4,000, max leverage 10, PAPER_BOOKS/TRUE_BOOKS false.

At a $4,000 basis, share 1/12 × risk .024 gives $8 planned entry risk; the sleeve's leverage room is approximately $3,333.33 across its positions. Actual risk is subject to equity, control multipliers and quantity flooring, not guaranteed exact dollars. This is the **EX13 sleeve's** notional allocation; other sleeves' notional remains in the account. The standalone experiment's one-sleeve account-wide $3,333.33 cap must not be described as a new global cap on the whole existing account. Existing marked daily loss/open-risk rules and live entry filters still apply. Runtime requires at least 250 closed candles, versus the research warmup/eligibility convention. Market fills and live refusals will differ from ideal next-open research fills.

**CP-Y2 on account2: not currently runnable as a configuration-only addition.** Its code is on unmerged research PR #337 at 646ab3fa, not the deployed registry. Required bounded integration before any account2 activation:

- Isolate the exact CP-Y2 rule, parameters and management; do not merge the entire research branch or enable all research candidates. Normal panel validation explicitly refuses research_only strategies, and engine load drops them. Any PAPER admission wrapper must be deliberate and independently reviewed, not a bypass of that guard.
- Add tested `1d` support through existing runtime timeframe dispatch, candle/cache handling, slot validation, closed-UTC-day timing, exit/time-cap handling and UI. Current engine TF_SEC and app validation accept only 15m, 1h and 4h. No new Windows scheduled task is necessary.
- Supply an immutable, causal per-cycle BTC+case daily panel with correct late-history handling. The research module uses a global PANEL set by the research runner; normal runtime compute_signals does not populate it. Avoid cross-timeframe concurrent mutation while OLD3 continues on 4h.
- Account2 uses PAPER_BOOKS and TRUE_BOOKS with PAPER_BOOK_CAP 800. An unmapped CP-Y2 book is refused. Define and test separate book attribution, sizing, notional and protection accounting; do not disable or disturb OLD3's existing book switches.
- Reconcile live 250-candle preflight with daily research warmup 60, then prove signals, partials/BE, stops and restart handling on the accepted candidate integration head. No account2 setting or process was changed here.

**Exchange-filter coverage:** all31 are present in the pinned research snapshot. An independent keyless public testnet `/fapi/v1/exchangeInfo` read at **11:27:11 Cairo (09:27:11 UTC)** confirmed **31/31 TRADING perpetuals**, all with LOT_SIZE, MARKET_LOT_SIZE, PRICE_FILTER and MIN_NOTIONAL records. The account4 panel's connected rules also contain all31. Detailed fields and the public response hash are in exchange_coverage.json. This proves filter availability, not that every ATR-sized order clears current minimum/maximum quantity or that an account's margin/leverage/hedge state is ready.

## 5. Later owner-approved EX13 change

After the feasibility review, the owner explicitly authorized account4-only activation. Codex sent the authenticated panel calls at **11:40:24 Cairo (09:40:24 UTC)**. This was Codex's action; Claude staged but did not send it. No restart occurred: process/session owner PID remained 35112.

EX13 = exp_trend, 4h, both, share 1/12, risk .024, max3, enabled, explicit31, empty management override (registered stop2.5 ATR / target3R / max180 unchanged). UNIVERSE became 40 and all31 switches are on. The other12 sleeves are structurally identical, including enabled flags, scopes and management. The 2.5% enforced open-risk cap and CAPITAL_CAP 4000 are unchanged. Only SLEEVES, SYMBOLS_ON and UNIVERSE changed.

Settings SHA256 before: c2c7335a76eb485b251a5223ad021c830ee77baab5c67576235e60be13f88904.

Settings SHA256 after: 1d487380997429fb24c363299915a4d22fc736457c96805f63db3015d47cc11a.

The normal panel run_cycle action requested the first cycle. Its successful 4h receipt is **11:40:46 Cairo (09:40:46 UTC)**. All31 had valid close/ATR and signals for the last closed 4h candle; no candle-fetch or cycle error occurred. That cycle had zero EX13 entry signals and opened no EX13 lot. Label: **wider-coin test of an existing arm**, not a new strategy. Accounts1–3 settings/install/cohort hashes remain unchanged. No exchange API key was read or printed, and no scheduler was changed. The existing local panel session was reused internally without exposing its token.

The owner subsequently asked for a read-only check after **14:00 Cairo (12:00 UTC)**. That observation is pending until the actual close; the earlier manual cycle must not be represented as the natural-close proof. EX13 remains enabled and is not reverted. A bounded read-only observation in the current task waits for the actual cycle; no scheduled task or runtime watcher was installed.

## 6. ADA stop-failure diagnosis, read-only

The event predates EX13. EX01 ADA long opened at **10:02:54 Cairo (08:02:54 UTC)**. At 10:03:25.980 the stop request failed with **-1021: Timestamp for this request is outside of the recvWindow**. The following `STOP FAILED ... closing for safety` message records intent. At 10:03:47.060 the safety close also failed with -1021; the lot was retained as unprotected/awaiting protection and retried. At **10:05:40.246 Cairo (08:05:40 UTC)** the log confirms `ADAUSDT [EX01] stop restored`. That is about 2m14s from the first stop-failure log, or 2m46s after the recorded lot open. The generic phrase `previous stop kept` is not proof that this new lot had a previous protective stop.

At the read-only **11:47 Cairo** snapshot the EX01 lot still held 1,232 ADA long with stop 0.1637; the older EX02 lot held 1,334 with stop 0.1642. Both were `protected=true`, `stop_state=confirmed`, age about 62 seconds in the bot's exchange verifier. EX01 had no ADA closed-history row. It was not successfully safety-closed. This is current verifier evidence, not a separately retrieved raw exchange-order response by Codex. The earlier log also flags an unresolved measured-entry-price/risk accounting gap after restoration; do not silently equate restored protection with complete fill economics.

The failure can recur. Both stop placement and emergency closing depend on signed timestamps. The client normally resynchronizes on -1021 and retries with a fresh signature, but a non-retried POST has only two attempts. Persistent timing error or request delay can reject both actions. sync_time uses serverTime minus the local arrival time; it does not prove the server-time response was fresh or compensate network delay with a midpoint. No Windows time-service/kernel adjustment event was found in the bounded incident window. The logs establish timestamp rejection, not whether the original cause was OS clock drift, delay or a stale sync response. No clock, client, stop, position or setting repair was made. Any fix needs a separately reported bounded change preserving order-idempotency; this report does not authorize one under the owner's latest read-only instruction.

DEFERRED: additional capacity/parameter searches, CP-Y2 integration, account2 changes, timestamp repairs, new schedulers and mainnet.
LANES: Integration records independent economics and the owner-approved EX13 receipt; Evidence observes the requested natural close and retains the ADA diagnosis; Build's exact-fixed companion remains a separate bounded research task. No daily-row dependency.
