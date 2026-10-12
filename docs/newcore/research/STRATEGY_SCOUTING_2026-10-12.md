# ZackBot strategy scouting — 12 October 2026, Cairo

Status: research proposal prepared during the owner-requested Claude pause. The review automation remains paused. This document does not start implementation, alter frozen strategy contracts, register trials, or open a holdout. Reconcile with the current canonical roadmap when work resumes. No candidate below has been backtested by this scouting exercise.

## Decision

Finish the already-approved STRAT-02 short runner and its synthetic evidence. Next research priorities should be a range strategy, a simple two-sided breakout, and exit-policy comparisons. Keep Binance CORE-10 first, CORE-20 second, static-40 only where already authorized; label current-universe historical results as survivor-biased. Gold remains a separate later group; stocks, other venues and broad macro ingestion do not enter this slice.

The existing STRAT-01 PARK result must remain intact. New exits or filters are new registered variants, never silent changes to its frozen result. Names below are scouting IDs, not replacements for existing ticket numbers.

## Requested video: findings and corrections

Source: Lewis Jackson Investing, “I Re-Built A Quant Trading Strategy With Fable 5,” https://www.youtube.com/watch?v=Z-hU97WO30I . Reviewed the exported English auto-generated transcript across the 21:11 video, not an independent reproduction of its generated code or performance.

Useful concepts: measurable bull/bear/range states; state transition counts; awareness of overlapping lookbacks; fitting only on prior information; presenting direction and uncertainty clearly. These fit the owner's requested regime toggle and bullish/bearish controls.

Specific problems in the explanation:

- Around 3:55: the Markov property is an assumption of a model, not a proven law of markets. A three-label state can discard useful information.
- Around 7:24–8:02: daily 20-day windows overlap heavily. Sampling every 20 days changes the forecast horizon to 20 days and reduces the sample; it does not automatically validate a tomorrow forecast. Retain explicit observation interval, label window and forecast horizon. Compare against a simple persistence baseline and account for serial dependence.
- Around 8:22–9:44: P(bull) minus P(bear) is a directional score, not a probability of profit. An 80% bull, 10% bear distribution gives a +0.70 score, while P(bull) is still 0.80. A rolling bull label can persist even on a losing next day.
- Around 10:25–11:15: an h-step Markov forecast uses the matrix power P^h, not a single probability raised to h. The latter omits paths through other states. Probabilities need not decay toward zero at long horizons.
- Around 13:32–15:17: an HMM is not assumption-free. State count, observations, distribution and fit procedure still matter. Agreement between two models using the same prices is not independent confirmation. Online decisions must use only observations available then, not full-sequence hindsight decoding.
- The 23x/60x examples do not establish an executable edge. This review did not obtain the full code, frozen data, cost assumptions and trial history behind those figures.

Recommendation: a small descriptive regime layer first; optional Markov/HMM challenger later. Compare OFF, descriptive tagging and a prespecified filter. Measure net expectancy, drawdown, turnover, rejected profitable trades and missed opportunities. Do not let a confidence score directly increase leverage. Forecast calibration and net expected return must be evaluated separately.

Mathematical references: https://python.quantecon.org/finite_markov.html and https://hmmlearn.readthedocs.io/en/stable/tutorial.html .

## Ranked research queue

| Proposed item | Mechanical hypothesis to freeze before testing | Why / primary failure mode |
|---|---|---|
| SCOUT-RANGE-01: range reversion | Closed-bar excursion beyond a rolling VWAP or price band, followed by a close back inside; symmetric long/short; mean target, hard stop, time exit; one open trade per symbol | Directly addresses missing range trades. Persistent trends and fees can erase the edge. Pick one band definition first rather than mixing indicators. |
| SCOUT-EXIT-01: exit comparison | Same frozen entries and initial risk; compare whole-position fixed target, partial target plus runner, and trailing exit with time limit | Addresses profitable trades giving back gains. A larger winning percentage is insufficient if average losses or lost trend profits grow. Start with a small declared comparison, not a large search. |
| SCOUT-BREAKOUT-01: Donchian / volatility breakout | Close beyond a channel computed from prior bars only; symmetric short breakdown; next executable fill; ATR stop and declared trailing/time exit | Simple alternative to EMA crossover, including falling markets. Whipsaws, gaps and chase costs are the main threats. |
| SCOUT-PULLBACK-01: trend continuation | Higher-timeframe direction from completed candles; lower-timeframe pullback and reclaim; reverse rules for shorts; invalidation stop | Potentially better entry location than chasing a move, but may duplicate existing momentum exposure. Test incremental portfolio benefit. |
| SCOUT-SCALP-01: quick-bank sleeve | Best supported range hypothesis on a prespecified shorter horizon; net-of-cost target, hard maximum holding period, bounded attempts and turnover | Only advance if executable spread/slippage leave a margin. Shorter holding time does not imply a larger appropriate position. |
| SCOUT-BASKET-01: range session accounting | Track realized plus unrealized P&L less all fees/funding; basket peak giveback threshold, loss ceiling and range invalidation; individual stops persist | Implements the owner's basket request after the underlying scalp has evidence. Prevent resetting the basket to hide losses; define flatten versus stop-new-entry behavior explicitly. |
| SCOUT-REGIME-01: optional state model | Price direction normalized by volatility, volatility percentile and trend efficiency; later compare a simple transition model/HMM | First a diagnostic. Promote only if it improves out-of-sample decisions beyond the unfiltered and simple-filter baselines. |
| SCOUT-FUNDING-01: crowding/context | Timestamp-correct funding as a prespecified context variable for an existing strategy | Later research; high funding alone does not establish an imminent reversal. Historical availability must be verified. |

All thresholds, timeframes and trial budgets above remain unspecified deliberately: these are candidates for contracts, not execution instructions. Each contract must define them before accessing evaluation outcomes. DCA remains separate, off initially; any later bounded add must fit a fixed total loss budget and prove an advantage over no-add. Never treat averaging down as a way to guarantee a positive account.

## Scouting sources and what to reuse

1. Freqtrade strategy collection: https://github.com/freqtrade/freqtrade-strategies . Educational examples, not verified profitability. The repository identifies GPL-3.0; assess licensing before importing code. Prefer independently written rules in our existing engine rather than adding another runtime.
2. Concrete source inspected: https://github.com/freqtrade/freqtrade-strategies/blob/main/user_data/strategies/futures/VolatilitySystem.py . It supports both directions and uses price movement versus ATR. It also declares stoploss=-1, position adjustment and 2x leverage. Borrow the signal hypothesis only; do not import those policy defaults. Completed higher-timeframe bar alignment needs explicit proof.
3. EMA/VWAP candidate: https://github.com/xenoxavier/freqtrade-ema-vwap . README describes an EMA9/daily VWAP long breakout. Treat its performance claims as unverified and its leverage assumptions as unrelated to our risk policy. Useful as a contrasting hypothesis, not as the range-reversion implementation.
4. Prior owner link: https://github.com/ArunKBhaskar/PineScript . Contains ICT displacement, equal highs/lows, liquidity and FVG scanners. These are discovery/visualization candidates; names and screenshots do not prove tradable alpha. Confirm pivot detection delay and higher-timeframe behavior before any Python translation. Defer behind simpler hypotheses.
5. YouTube scouting: Financial Wisdom's Donchian video https://www.youtube.com/watch?v=r7gzyEnPTwg is linked from the creator's strategy hub https://www.financialwisdomtv.com/post/trading-strategy-library-backtesting-hub . Reviewed the hub, not the full video transcript. Use the mechanically describable breakout idea; do not transfer equity-market performance claims to crypto.
6. Research basis: https://www.aqr.com/Insights/Research/Journal-Article/Time-Series-Momentum . Supports investigating trend as a family across the paper's markets/horizons; it does not validate crypto scalping or our implementation.
7. Method/data: https://www.freqtrade.io/en/stable/lookahead-analysis/ and https://github.com/binance/binance-public-data . Useful references for causal checks and exchange archives; continue using ZackBot's existing accepted harness and pinned data.

Sources were inspected for a bounded scouting pass, not audited or installed. No third-party strategy code executed. Record immutable source revision and license in any later implementation proposal.

## Smallest useful validation plan

1. Freeze one entry family and a small set of exit comparisons. Record every attempted variant, including failures. Keep the existing sealed holdout closed.
2. Unit/synthetic evidence covers both directions, tick rounding, stop/target ambiguity, gaps, funding signs, partial fills and closed-bar availability. Prove future data cannot change past decisions.
3. Run on the existing development data and CORE-10 first; CORE-20 only after integrity checks. Use the shared portfolio engine so position conflicts, capital and risk limits are real.
4. Charge both sides of fees, spread/slippage and funding; stress costs and do not assume a limit order fills merely because a candle touches it. Lower-resolution candles cannot prove scalp fills. An unresolved stop/target order needs a conservative convention or finer data.
5. Report net expectancy, drawdown, turnover, long/short splits, symbol concentration, holding time, exposure, baseline comparisons and uncertainty. Compare risk-matched alternatives, not gross profit alone.
6. Record maximum favorable/adverse excursion, peak net P&L and profit given back, together with the actual exit reason. The hindsight best exit is diagnostic only; a proposed earlier-exit rule must have been knowable at the time and earn its own test.
7. Check feasibility at $500 controlled master capital and hypothetical $100/$200 follower allocations, using current venue filters. Report minimum-order skips and partial-exit infeasibility. This is a sizing simulation, not a guarantee of Binance copy execution or follower outcomes.
8. Advance eligible candidates to paper/testnet for execution parity. Synthetic exchange fills test implementation, not real-market profitability. Keep experimental/manual controls distinguishable from evidence-qualified automated strategies.

## Prepared lane handoff for when the owner says ready

Build: finish STRAT-02; then draft the smallest RANGE and EXIT contracts using existing interfaces. Evidence: independently challenge causal entries, range invalidation, scalp costs and exit assumptions. Integration: reconcile these proposals with existing tickets, deduplicate, freeze trial budgets and order the gates. No need for a new general backtester, multi-venue framework or ML platform.

ROADMAP FIT: native Binance strategy coverage and validated exit behavior. DEFERRED: standalone HMM trading, broad parameter mining, martingale/grid recovery, order-book HFT, news/LLM-generated orders, new venue integrations and product polish.
