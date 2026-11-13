# EXTRA31 / SIEVE-V1 independent review — 13 November 2026

ROADMAP FIT: owner-approved strategy catalog and cost-aware transfer sieve. Research only. No account, key, position, worker, scheduler, strategy setting or core-engine change.

Reviewed result head: **0ca354406a618a51751497a2aa8cb5cb2ea3405e**, PR #340. Native source for the two reproduced leads: **9cb4341f2d94c21434492515b8e85f2b42c148ce**. First screen freeze a4d4ee68/run 929b6ecd; pair freeze 2c7cfce8; panel freeze 44efcec2. Rule v1: 487843b3. No new selection thresholds were introduced in this review.

## Findings and acceptance

**PASS for the submitted signal definitions, order-0 admission and trade arithmetic. Accept the results as selection-dependent, approximately fixed-risk research diagnostics. Do not certify exact fixed-$8 sizing or a real $4,000 account from this implementation.** The first-pass filter is separately disclosed; its 291 registered lines are not 291 independent hypotheses. The all-31 screen preceded rule-v1 case freezes and selected the later panel cases. The original screen and rule-v1 results remain separate in the record.

### Independent reconstruction

The independent reviewer does not import Claude's research runner, native strategy implementation, Book or backtest.run. It reads their frozen rules as specifications, then independently loads the pinned hourly CSVs, aggregates complete UTC 4h bars, computes real-history EMA/ATR/momentum/extremes, applies D01 liquidity and warmup, and constructs entry/exit signals. It independently simulates alphabetical order-0 admission and both declared management paths. No artificial pre-listing candle is created.

| Case | Raw signal tuples matched | Eligible entry tuples | Missing / extra |
|---|---:|---:|---:|
| exp_trend | 1,542 / 1,542 | 1,320 | 0 / 0 |
| bear_breakdown | 5,931 / 5,931 | 4,855 | 0 / 0 |

Tuples compare symbol, candle-open UTC, side and eligibility. A candle-open timestamp labels the closed decision candle; it is not the execution instant. Independent decision-to-next-open timing matches the native tape.

| Order-0 simulation | Closed lots | Entry/exit sequence and reasons | Net at 15 bp |
|---|---:|---|---:|
| exp_trend primary | 323 | Exact match | +$304.455825 |
| exp_trend worst diagnostic | 323 | Exact match | +$304.455825 |
| bear_breakdown primary | 197 | Exact match | +$135.225126 |
| bear_breakdown worst diagnostic | 221 | Exact match | -$53.945823 |

All 520 primary-path closed lots were independently sized, filled, charged fees/funding and managed; the independent admission extension covers 1,064 lots across the four tapes. Maximum net-P&L difference is about $1.42e-14. Both primary entry sets are entirely backed by independently reconstructed eligible signals. Exit times and reasons match, including the bear trailing-stop path. No terminal open lot remains in these four order-0 cases. All 31 traded symbols have rules in the pinned exchange snapshot; no legacy-rule fallback was needed here. The snapshot is current testnet data, not historical exchange-filter authority.

The submitted 40-order summary medians for exp_trend, bear_breakdown and C101 were independently recomputed from the submitted per-order summaries and match. **That is an arithmetic check, not an independent rerun of the other 39 symbol orders.** Neither the entire 291-line first screen nor other survivor signals have been independently reproduced by this review.

### File identity and data

All seven supplied CSV identities match the submitted CRLF hash (or supplied prefix) after converting the committed LF content to CRLF. Both full LF and reconstructed CRLF hashes are recorded in independent_review.json. A newline difference is not a content failure. In particular, summary.csv's CRLF hash is 2f58c9d955554194a34365994554bf6a68eb69598a8d61be4575799d895baba9. The C101 request supplies only a hash prefix; no full external hash comparison is claimed for that file.

The canonical manifest is 99af9bfb4a9b29ede80f8f3ff54541912d74d03b3b93d984432832c934bdfa95. All 32 inputs used here (BTC plus extra31) match their individual manifest hashes and have ordered, unique, consecutive hourly timestamps. Four-hour bars use complete UTC groups. Indicator history is calculated per coin's real history. The clock-label correction remains 26 August 2022 08:00 Cairo (06:00 UTC); no UTC window change.

## Implementation ruling

1. **Using backtest.run:** acceptable for native registered-management and max-one admission in this research screen. The independent order-0 reconstruction confirms the two leads' behavior. It is not an independent implementation by itself; the separate code in this evidence supplies that check.
2. **$10m device:** it is approximately, not exactly, fixed sizing. Native quantity and notional cap scale with realized equity: risk is $8 × equity / $10m and cap is $3,333.33 × equity / $10m. After rounding, 19 exp_trend lots and 12 bear lots differ from exact fixed-$8/cap sizing. Repricing the SAME admitted primary tapes with exact-fixed rounded quantities changes net15 from $304.455825 to $304.458999 and from $135.225126 to $135.225906. These tiny conditional changes do not explain the edge; they are not proof that exact sizing leaves every admission unchanged. Keep the original outputs as provenance and report this implementation variance. Do not silently relabel approximate sizing as exact rule compliance.
3. **Costs and account rules:** 15/23 bp are ex-post additions on recorded reference turnover; the underlying equity, sizing and admissions were simulated at base 7 bp. Funding is the disclosed 1 bp / 8h cost proxy on both sides, charged per held alpha bar, including entry/stop bars; it is not native signed funding. No risk_rules were supplied, and the default 8% daily halt is measured against $10m. Thus the result is not $4,000 margin, marked daily-halt, open-risk or liquidation evidence. The frozen rule did not add a new account-level halt gate; these are limits on integration, not retroactive screen thresholds.
4. **Terminal positions:** excluding open lots from the completed-trade sample is consistent with that gate. Portfolio return/equity and drawdown still need terminal marks, entry fees and funding disclosed. The four independently simulated order-0 lead cases are flat; no statement about other orders or EMA's terminal positions follows from that.
5. **Cross-sectional population:** the frozen panel uses BTC plus all case coins with available history at each timestamp. D01 eligibility masks entry signals afterwards; it does not remove illiquid/unseasoned but price-present coins from ranks or breadth. This is acceptable as the disclosed available-history context diagnostic, not an eligible-only rank universe. Bear's BTC filter is unaffected by the other coins, and exp_trend does not use the ranks. Do not swap to liquidity-filtered ranks after seeing outcomes. Rotation's fixed n_symbols enters its short conditions/exits; the reviewed registered rotation case is long-only, so that field does not cause its long-path failure. A changed context universe is a distinct test and must not rescue failed cases.

The five other survivor cases may retain their already frozen all31 consumed-symbol diagnostic scope. This ruling does not retrospectively make them unselected hypotheses, declare their coins untouched, or authorize post-outcome subset tuning. Record failed cases without rerunning to find better coins.

## Disposition

**exp_trend:** retain as the strongest positive lead from this screen, not as newly independently qualified profitability. Submitted medians are n=321, meanR15 +0.122114, early/later +0.163920/+0.073355, ex-best +0.091492, meanR23 +0.097624. Both native path modes match for this management. Order 0 has 323 closes, +38.05698R, of which about +33.12R is short and +4.93R long; closed DD is 9.48R. Frequency is only 1.51 closes/week over this 31-coin run. It contributes to a slower basket, not the frequent hourly replacement by itself. Do not turn the side split into a new short-only variant after selection.

**bear_breakdown:** preserve its primary screen pass; the frozen worst diagnostic was not a pass gate, so do not rewrite the primary verdict as though it was. Nevertheless it is path-sensitive and not accepted for increased capital or new deployment from this result. Submitted worst medians: meanR15 +0.018261, early -0.020101, ex-best -0.009771, meanR23 -0.011009. Independently simulated order-0 worst loses $53.9458 at 15 bp. Label PRIMARY_SCREEN_PASS / PATH_FRAGILE / NO_NEW_ACTIVATION. Existing accounts are unchanged. Its frequency is about 0.92 closes/week. The native worst path is favourable-extreme-first for the held side, stressing tightened stops; it is not a universal worst conceivable market path or a separate stop-versus-target test.

**C101:** confirmed the recorded 18-coin development packet SHA256 867996d0e7556b1441d947bff75d4936a557f762956e23f24964a739da231f0c. Its eight extra31 intersections are exactly 1000PEPE, ENA, NEAR, QNT, SUI, UNI, WLD and ZEC; LSK is outside the cache. The signal, Kalman helper, parameters and registration/management are AST-identical between original C101 6e52bb61 and screened c254 f7a1265b. The 23-coin complement is confirmed relative to THIS recorded packet; it is not proof of every historical run, and the first-pass screen has now consumed all31. Retain that history limitation. Its reported rule-v1 result n=304, meanR15 -0.0680, 0/40 passing, closes this unchanged transfer candidate as FAILED/PARKED. No new tuning, coin exclusions or rerun is justified to rescue it. Its full signal/cash set was not independently rebuilt in this two-lead review; parking a negative does not imply accepting a positive strategy.

## Next bounded work before the 20 November review

The exact-fixed sizing companion below is authorized research work, not a rule change or new strategy. Claude should freeze its small research-only adapter and tests BEFORE new outcomes, retain all original outputs, and run the same two leads, source/rules/data/universe/management/40 orders/paths/cost conventions unchanged. Use exactly $8 and $3,333.33 at every entry, with native-rule quantity flooring, no parameter or side changes. Report changed quantity/admission counts against v1, all existing gate statistics, full closed cash and terminal open-lot marked/estimated-liquidation value. Demonstrate signal tuples are unchanged. Do not edit the shared core engine or touch runtime accounts to do this; if a native fixed-sizing hook is unavailable, use a bounded research-only simulator/adapter with explicit parity proof.

In parallel, prepare the account-relevance bridge: the existing original9 arm's observed live/forward signals and closes, same management/version, cost assumptions, long/short attribution, refusals and book capacity. The extra31 historical result is dependent supporting evidence, not new forward evidence for the original9. Because exp_trend already exists in the cohort, do not add it again or count it as a new strategy. Any actual $4,000 portfolio/allocation proposal also needs a separately frozen admission-aware comparison under its intended marked daily halt/open-risk/capital rules, exact cost scenarios, terminal equity and shared-book exposure. This can run without waiting for a daily row or changing any account.

For 20 November, use the independently verified signal/cash result as supporting historical economics with these scope labels. Do not claim a frequent profitable replacement is now qualified, or require waiting until 20 November to perform the next bounded research computation. No new candidate family, engine work or activation is authorized by this review.

DEFERRED: unrelated infrastructure, alpha tuning, optional new data acquisitions, account changes and mainnet.
LANES: Build freezes and runs exact-fixed companion cases; Evidence prepares the original9/current-cohort relevance bridge; Integration owns parity, the path-risk ruling and later portfolio acceptance. Neither lane waits for daily forward rows.
