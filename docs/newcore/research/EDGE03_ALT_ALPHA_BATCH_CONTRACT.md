# EDGE-03 alternative-alpha finite batch

Status: **Codex preregistration draft v1 — 26 October 2026, Cairo. No result has been read.**

EDGE-03 is a separate research-only batch for two mechanisms already named on the strategy research board. It does not
replace or enlarge EDGE-02, change PAPER/testnet, authorize orders, or open a holdout. A result from this batch can reject
or prioritize a side for an execution-grade follow-up; it cannot promote a strategy.

## Purpose, trial count and multiplicity

The owner wants more frequent trading only inside an after-cost profitability envelope. This batch tests whether a
slower independent return source can add useful, regime-diverse opportunities without reviving the failed fast sleeves.

There are exactly four primary hypotheses:

1. `xsec_rs_4h.v1.long`
2. `xsec_rs_4h.v1.short`
3. `funding_extreme_reclaim.v1.long`
4. `funding_extreme_reclaim.v1.short`

Each primary hypothesis has one mechanically paired 1.5x-cost stress row, for eight Stage-A result rows total. The stress
row is not a second parameter choice. Long and short never pool or rescue one another. These four hypotheses append to the
existing EDGE-01/EDGE-02 family trial ledger; the multiplicity clock is not reset by the new ticket or strategy names.
The prior-trial inventory is known to be incomplete, so Stage A makes no family-wide significance or discovery claim.
Any p-values or intervals must be reported both raw and Holm-adjusted across these four new primary hypotheses, alongside
the cumulative trial count that can be reconstructed. Missing older trials are disclosed, not silently treated as zero.

No threshold, lookback, symbol subset, exit or side may be changed after results. There are no neighbour, management or
parameter diagnostics in this batch. A failed or sparse cell is recorded and parked; any revised version is a new finite
batch with a new id and counts as another trial.

## Data boundary and eligibility

- Decision data are Binance USD-M 4h candles. Funding and open-interest observations must be Binance USD-M records with
  source timestamps, retrieval provenance, completeness and content hashes frozen before a funding cell starts.
- Headline universe is the frozen CORE-10 list: BTC, ETH, ZEC, SOL, XRP, NEAR, HYPE, DOGE, UNI and SUI, quoted in USDT.
  The full 14 October 2024 through 28 September 2026 candle window is currently `CORE-10-ELIGIBLE-9`: HYPE begins later
  and is excluded from the headline. HYPE may appear only as a separately labelled available-period sensitivity after
  the four primary results; it cannot change a verdict or winner.
- CORE-10 is a present-day static list and therefore survivor-biased. In particular, cross-sectional ranks among the nine
  survivors are not a point-in-time investable-universe claim. Stage A is a bounded screen, not evidence that the same
  ranks were available from an unbiased historical universe.
- A cross-sectional decision requires at least eight of the nine headline symbols to have every required completed bar.
  It is skipped otherwise; ranks are never compressed onto a smaller convenient subset.
- The current research cache has no accepted, joined, point-in-time funding-plus-open-interest manifest for this window.
  Therefore both `funding_extreme_reclaim.v1` sides are **DATA-BLOCKED** until that artifact exists. Candle returns,
  volume, futures premium, present-day API values or forward-filled gaps are forbidden proxies. The window may not be
  shortened after looking at coverage. If full-window OI cannot be obtained, the primary funding cells end `INCOMPLETE`;
  a later available-period study requires a new preregistration and cannot inherit this trial's gate.
- A timestamp is eligible only when all inputs existed at the decision time. Revisions or records first retrieved after
  the simulated decision are not backfilled as if known. Missing data produce `UNKNOWN/SKIP`, never a neutral value.

## Common clock, accounting and execution rules

- Every indicator uses completed 4h bars only. A signal fixed at bar close `t` can fill no earlier than the next 4h open.
- OHLC path ambiguity is adverse: if a stop and target are both touched in one 4h bar, the stop fills first. A gap through
  a stop fills at the first executable price, not at the stop price. An entry whose next open is already beyond its stop
  is refused and counted.
- Wilder ATR(14) and EMA use `adjust=False`; they require a complete warm-up and are never seeded with future history.
- One candidate may own at most one position per symbol. It cannot pyramid. Positions from different candidate cells are
  evaluated independently at Stage A, so a combined portfolio result is not reported as a gate.
- Position size makes the entry-to-initial-stop distance equal to 1R before fees, subject to historical tick, step and
  minimum-notional rules when those records are available. An infeasible order is skipped, never rounded into more risk.
- Base costs are 5 bp taker fee plus 2 bp adverse spread/slippage on every entry and exit fill. The paired stress applies
  1.5 times those two components. Actual signed funding at each crossed funding timestamp is additional in both rows.
  A missing funding charge makes that episode accounting-incomplete and excludes the cell from a pass.
- Results report signals, skips by reason, entries, closes, trades/week, occupancy, gross and net R, costs/R, PF, maximum
  drawdown, expected shortfall, worst trade, fold, symbol and regime splits, and absolute-contribution concentration.

## Frozen candidate A — `xsec_rs_4h.v1`

Mechanism: persistent relative strength should continue over the next day when the selected asset also agrees with its
absolute trend. This is cross-sectional continuation, not a market-direction or single-symbol momentum claim.

At the completed 4h bar whose close timestamp is 00:00 UTC each day:

1. For every eligible symbol compute 180-bar log return `ln(close[t] / close[t-180])` and the sample standard deviation
   of the 180 intervening 4h log returns. Score is log return divided by that standard deviation. Zero/non-finite
   deviation makes the symbol ineligible.
2. Rank descending by score, breaking exact ties by symbol name ascending. Compute EMA(50) on that symbol's close.
3. Long selects the first two ranks whose score is positive and close is strictly above EMA(50). Short selects the last
   two ranks whose score is negative and close is strictly below EMA(50). Fewer than two qualifiers means only the
   available qualifiers trade; no lower-ranked substitute may violate the sign/trend rule.
4. Enter each selection at the next 4h open. Initial stop is 2.0 ATR(14) from entry. There is no profit target or trailing
   stop. Exit at the earlier of the stop or the open following the sixth completed 4h holding bar (24-hour cap).
5. A symbol that closes at the time cap cannot reopen at that same price; one complete 4h bar must pass before another
   entry in the same side/candidate. Candidate sides are evaluated separately.

The two selected ranks, 180-bar score, EMA(50), 2.0-ATR stop and six-bar cap are primary rules, not a grid.

## Frozen candidate B — `funding_extreme_reclaim.v1`

Mechanism: an extreme one-sided funding payment combined with contracting open interest marks a crowd unwind; a causal
reclaim of local trend may capture the subsequent mean reversion. Funding alone is not an entry.

At each settled Binance funding event (normally 00:00, 08:00 and 16:00 UTC):

1. Use only that symbol's prior 270 settled funding observations, excluding the current event, with at least 180 valid
   observations. Quantiles are type-7 linear quantiles. The current settled rate is a long extreme only when it is at or
   below the causal 5th percentile and `<= -0.0001`; it is a short extreme only when it is at or above the causal 95th
   percentile and `>= +0.0001`.
2. Use the last open-interest snapshots at or before the event and at or before eight hours earlier, each no more than
   five minutes stale. Require `(OI_now / OI_8h_ago) - 1 <= -0.02`. Missing, non-positive or stale OI is `UNKNOWN/SKIP`.
3. Arm that symbol/side for exactly the next two completed 4h bars. Long triggers on the first close that crosses from
   at/below to strictly above EMA(20); short triggers on the first close that crosses from at/above to strictly below
   EMA(20). The prior close and both EMA values must have been computable at their respective close times.
4. Enter at the next 4h open. Initial stop is 1.5 ATR(14); fixed target is 2.0 ATR(14). Exit at stop, target, or the open
   after six completed holding bars, whichever occurs first. Adverse intrabar ordering follows the common rule.
5. An arm expires after two bars, is consumed by one trigger, and is not extended by a second extreme. After any close,
   that symbol/side has a three-funding-event (24-hour) cooldown. No overlapping arm or position is allowed.

The 270-event history, 5th/95th percentiles, absolute 1 bp funding floor, 2% OI contraction, two-bar reclaim window,
EMA(20), stop, target, time cap and cooldown are frozen. No one-factor candle-only fallback is this strategy.

## Stage A — chronology and side-separated gate

The untouched holdout remains sealed and is not used or replaced in EDGE-03. Stage A uses only these non-overlapping
development slices, represented as half-open UTC intervals:

| Slice | Interval |
|---|---|
| Train / warm-up and integrity | `2024-10-14T00:00:00Z` to `2024-12-30T00:00:00Z` |
| WF0 | `2024-12-30T00:00:00Z` to `2025-03-31T00:00:00Z` |
| WF1 | `2025-03-31T00:00:00Z` to `2025-06-30T00:00:00Z` |
| WF2 | `2025-06-30T00:00:00Z` to `2025-09-29T00:00:00Z` |
| WF3 | `2025-09-29T00:00:00Z` to `2025-12-29T00:00:00Z` |
| WF4 | `2025-12-29T00:00:00Z` to `2026-03-30T00:00:00Z` |
| WF5 | `2026-03-30T00:00:00Z` to `2026-06-29T00:00:00Z` |
| WF6 | `2026-06-29T00:00:00Z` to `2026-09-28T00:00:00Z` |

Train is not used to select parameters; it supplies warm-up, schema and feasibility checks. For each of the four sides,
all applicable hard gates below must pass independently:

- at least 60 complete forward episodes and at least four of seven forward slices net-positive;
- combined forward mean net R `> 0`, PF `> 1.05`, and positive pre-cost gross expectancy;
- paired 1.5x-cost mean net R `> 0`; no base-to-stress sign flip;
- maximum drawdown `<= 12R`, with every loss worse than `-1.5R` explained from the ordered path and costs;
- no symbol supplies more than 35% of absolute net-R contribution, and removing the best symbol leaves positive net R;
- `xsec_rs_4h.v1` median activity at least five closes/week per side across CORE-10-ELIGIBLE-9;
- `funding_extreme_reclaim.v1` median activity at least one close/week per side. A side below five/week may survive only
  as an explicitly low-frequency diversifier; it does not satisfy the frequent-strategy catalog slot;
- zero unexplained causality, eligibility, cost or accounting mismatch.

Failure of data sufficiency, episode count or any economic/risk gate parks only that side. A combined-side or combined-
candidate portfolio cannot convert a failed side into a survivor.

## Stage B and boundary after this batch

Only a Stage-A side that passes unchanged may receive a separate Stage-B execution contract. Stage B must use
execution-grade sub-4h paths, historical exchange filters, ordered stop/target resolution, actual signed funding, frozen
latency and gap stresses, and an appropriate matched random-entry or rank-permutation control. Stage B parameters and
gates must be frozen before its data are read.

EDGE-03 does not inspect a fresh or existing holdout. It does not create a shadow/PAPER allocation. Any Stage-B survivor
still needs an independently reviewed promotion preregistration and a genuinely untouched future sample; that later
contract may not call these development slices a holdout.

ROADMAP FIT: Wave D / EDGE-01B bounded scouting: cross-sectional relative-strength continuation and funding/open-interest
dislocation mean reversion, after the failed fast baselines and without delaying engine/testnet safety work.

DEFERRED: implementation, data acquisition, Stage-A runs, Stage B, holdout access, PAPER/testnet changes, QBank, USDC
routing, ML order authority, mainnet/live activation, new venues, TradFi/gold and UI work.
