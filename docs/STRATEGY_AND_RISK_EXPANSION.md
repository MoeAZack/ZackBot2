# Strategy, scalping and risk-control expansion

*Approved by the owner on 2026-10-06, Africa/Cairo. Planning contract; no trading behaviour changes in this document.*

## Decision

ZackBot will grow from a small long-biased strategy set into a broad, independently tested catalog covering trend, breakout, pullback, momentum, range, mean-reversion, DCA, grid, scalping and dedicated short models. Automation may recommend or select only among strategies the user has allowed. The user always retains manual strategy, side, timeframe, allocation and capital control.

"All strategies" means broad coverage of useful strategy families seen across established trading bots, not unreviewed duplication of every public configuration. Every candidate has its own evidence, status and safe default.

## Why this was added

The 2026-10-06 code and research review confirmed three gaps:

1. Standard EMA trend slots have no fixed target, and explicit runner mode can suppress fixed targets, time exits and some profitable signal exits. Existing summaries do not attribute banked profit versus runner-only profit.
2. The enabled library has no validated range/mean-reversion sleeve. Grid/COMBO is disabled and experimental; 0 of 32 tested setups made money.
3. Every built-in preset is long-only. The one dedicated short strategy is excluded because its long-history and portfolio evidence is weak. Several signal functions calculate short candidates, but the registry masks them in normal presets.

These are confirmed product/design facts. Whether runners reduced the realised result of the owner's current accounts is still a hypothesis until T05a can attribute the actual fills.

## Three independent controls

### 1. Interface detail

| View | Purpose |
|---|---|
| Simple | Plain-language status, bot capital, risk and drawdown sliders, recommended mix and clear exit plans |
| Guided | Strategy families, scores, allocations and explanations with advanced fields collapsed |
| Pro | Full signal, timeframe, side, management, regime and execution controls |

Changing the view never changes risk, strategy settings or open trades.

### 2. Risk grade

These are research bands to calibrate, not promised returns or final production limits.

| Grade | Indicative risk per trade | Indicative total open risk | Intent |
|---|---:|---:|---|
| Amateur | 0.25–0.50% | 2–4% | Highly filtered, small losses, few simultaneous positions |
| Conservative | 0.50–1.00% | 4–7% | Lower volatility with modest opportunity coverage |
| Intermediate | 1.00–2.00% | 7–12% | Balanced frequency, return and drawdown |
| Advanced | 2.00–3.00% | 12–18% | Aggressive sizing and more simultaneous risk |
| Expert | 3.00–4.00% | 18–25% | High volatility and substantial expected drawdowns |
| Maniac | 4.00–5.00% | 25–35% (hard ceiling 35%) | Extreme; Research/Testnet by default until a later explicit mainnet policy gate |

The calculation uses loss at the exchange-side stop, total open and correlated risk, gross exposure, margin use, liquidation distance, planned DCA/pyramid commitments, daily loss and current drawdown. Leverage alone is never treated as risk. No automatic component may exceed the user's controlled capital, loss tolerance or mechanical caps.

The user-entered **controlled capital** remains the sizing base and allocation ceiling even when the exchange wallet contains more money.

### 3. Control authority

| Mode | Behaviour |
|---|---|
| Manual | Trade only the exact strategy configuration selected by the user |
| Recommend | Calculate and explain proposed changes; the user approves them. Experimental candidates may be listed only with an explicit **Experimental** label and are never proposed as the default |
| Automatic | Select only from the user's strategy/side/coin/timeframe allowlist, inside every capital and risk cap |

Mode changes affect new decisions only and never reinterpret an open trade plan.

## Candidate catalog

### Trend and swing

- EMA/Supertrend and momentum-confirmed EMA;
- Donchian and volatility breakouts;
- breakout followed by retest;
- trend pullback and squeeze breakout;
- pyramiding continuation;
- cross-sectional momentum rotation;
- separately validated bull and bear trend models.

### Range and mean reversion

- Bollinger-band reversal;
- VWAP deviation and reclaim;
- RSI/range-bound reversal;
- support/resistance rejection;
- neutral range engine;
- symmetric range longs and shorts;
- redesigned grid experiments with hard range-break protection.

The first candidate should use a per-symbol range definition, reversal confirmation, liquidity/spread filters, a mid-band or VWAP target, a hard stop outside the range, a short time limit, one attempt per range and a cooldown. It starts with no DCA and no runner.

### Scalping candidates

- VWAP reclaim;
- Bollinger mean reversion;
- volume-confirmed momentum burst;
- breakout/retest;
- liquidity-sweep reversal;
- micro pullback inside a strong trend;
- range-edge long and short;
- bearish rejection;
- fast trailing breakout.

The initial research timeframes are 15 minutes and 1 hour under the normal validation ladder. Genuine sub-15-minute strategies (especially 1–5 minute) additionally require retained high-resolution data, order-book/spread inputs, realistic latency, partial-fill and queue modelling, and more demanding execution infrastructure. A selectable timeframe is not evidence that a scalp is production-ready.

### Quick Bank scalp management

Research a fee-aware **Quick Bank** plan alongside the scalp entries. It is a trade-management family, not a promise that a trade or account will finish green:

- place an early take-profit for an explicit fraction of the parent trade, then either close the rest or leave a small, separately measured runner only when its regime gate remains valid;
- test the early target, bank fraction and runner fraction in R/ATR terms rather than hard-coding one percentage for every coin and timeframe;
- offer a strict **net profit lock** variant: only after the bank fill is confirmed, the worst modeled result from realized profit plus the remaining stop must be positive after fees, funding and slippage. If exchange sizing or stop constraints cannot prove that condition, it must say `not locked`;
- allow at most one optional micro-DCA before the first target and before the original thesis is invalidated. The initial entry is pre-sized to reserve that add, and the add must never widen the stop or increase the trade's precommitted maximum loss;
- disable the add after TP1, during abnormal spread/volatility, a news/risk veto, a portfolio halt, stale data, or when either resulting order would violate exchange or copy-follower minimums;
- resize and verify the exchange-side protective stop after every confirmed partial fill or add. Ambiguous fills fail closed and reconcile before another action;
- translate exits through venue-specific position-side/close-only rules and verify that every child order can only reduce the intended position;
- retain one parent trade id with child ids for the initial lot, add, bank fill and runner so neither win rate nor trade count is inflated.

The research matrix compares: (A) base exit with no quick bank or add; (B) quick bank only; (C) quick bank plus conditional runner; (D) quick bank plus bounded micro-DCA; and (E) quick bank plus both bounded micro-DCA and conditional runner. Candidate starting ranges such as a 0.35–0.75R first target, 50–80% bank and 0.25–0.50× add are search bounds only, not production defaults. Promotion depends on out-of-sample net expectancy and tail risk, not the percentage of green trades.

Manual control remains available. Simple mode may expose `Protect profit early` and `Allow one rescue add`; Pro mode exposes the target/bank fraction, runner policy, add trigger/size and time limit. Risk grades cap or disable the add and runner independently, and changing a setting never rewrites an open trade's fixed plan.

An optional **green-session guard** is tested separately from the entry and exit logic. It measures the account's realized daily result after all costs. Once a user-set daily profit threshold is reached it may reduce Quick Bank risk, disable its DCA, or pause new Quick Bank entries; a second session give-back floor may pause them if too much of that realized gain is later lost. It never widens stops, adds to an existing trade, closes unrelated strategies merely to preserve a green display, or promises that the day cannot turn negative. The UI states whether the guard is `building`, `protecting`, `paused after give-back`, or off.

### Short-specific candidates

- bear breakdown followed by failed retest;
- lower-high continuation;
- upper-range rejection;
- failed bullish breakout;
- overextended momentum reversal;
- bottom-ranked relative-momentum short in a confirmed bearish regime;
- market-neutral long/short selection.

Shorts are not forced to equal longs. They earn allocation through evidence. Short squeezes, funding and asymmetric tail risk require their own thresholds and normally a lower initial risk budget.

## Runner policy

"Runner" must mean an explicit remaining fraction, not an implicit replacement for every other exit.

- T09a may add an explicit `runner_frac` and parent/child attribution after the observe-only evidence and shared-core work; T05a does not change lots, exits or order behaviour.
- At the planned target, bank the main quantity and separately track the remaining child quantity.
- Preserve a runner-specific maximum holding time and hard stop.
- Record target touch, banked P&L, runner-only P&L, MFE/MAE, peak open profit, protected profit and give-back.
- Range, mean-reversion and DCA strategies default to a complete planned exit.
- Short-term trend runners are small and time-limited unless longer evidence proves otherwise.
- Wider 4-hour trend runners may remain candidates because fixed ladders historically cut off rare large winners.

The UI must state the plan directly, for example: `80% at 1.5R; 20% runner; +0.7R protected; maximum 8 bars remaining`.

## Metrics and calculation system

Every strategy/timeframe/side/regime combination receives separate outputs:

### Return quality

- net return and CAGR;
- expectancy and average/median R;
- profit factor;
- Sharpe, Sortino and Calmar;
- win rate and trades per week;
- average and tail holding time.

### Risk and tail behaviour

- maximum drawdown and worst month;
- ulcer index and time under water;
- worst trade and losing streak;
- MAE distribution, liquidation distance and gap loss;
- correlated portfolio contribution;
- Monte Carlo drawdown bands and risk of ruin.

### Robustness and evidence readiness

- untouched holdout and rolling walk-forward results;
- parameter sensitivity/stability;
- bull, bear, range and high-volatility breadth;
- point-in-time universe and delisted-coin coverage;
- minimum independent trade sample;
- data-manifest and code-version identity.

### Execution quality

- fees and funding;
- expected versus actual fill;
- spread and slippage;
- wait, fallback and partial-fill rates;
- rejected/ambiguous/reconciled orders;
- order count and follower-minimum suitability.
- Quick Bank target/fill rate, time to bank, percentage of trades green after all costs, and percentage that satisfy the stricter net profit lock;
- positive-session frequency, realized daily profit at guard activation, profit retained at day end, peak-to-close session give-back, and the opportunity cost of trades skipped by the green-session guard;
- micro-DCA trigger/dependency rate, incremental P&L and added MAE/tail loss;
- runner incremental P&L and give-back versus closing the same remainder at TP1;
- partial-fill, cancellation, replacement, reconciliation and minimum-notional failure rates for every child order.

### Current fit

- global and per-symbol regime;
- volatility and liquidity;
- direction and timeframe fit;
- execution conditions and data freshness.

Current fit is context, not a promise. A score alone has no trading authority.

## Opportunity funnel and attribution

T05a records counts and reason codes from raw candidate to final outcome:

`raw signal → side mask → regime → slot capacity → risk gateway → execution → opened → managed → closed`

It must answer, by strategy/timeframe/side/regime:

- how many long and short candidates existed;
- where each candidate was rejected;
- how much the planned target banked;
- whether the runner added or subtracted value versus closing the same remainder at the target;
- the most favorable price and timestamp reached before the actual close, with peak net open P&L, peak R and MFE;
- actual close P&L versus peak, including absolute and percentage/R give-back;
- why the engine continued holding at each meaningful exit evaluation: the active policy, signal/regime state, target/trailing/time conditions and any execution constraint;
- whether a **predeclared causal exit rule**, using only information available at that moment, would have closed earlier for more net profit after fees, funding and modeled slippage;
- the absolute best observed exit as a separately labelled **hindsight ceiling**, never presented as a decision the live bot could necessarily have made;
- fees, funding and slippage attributable to the extra holding period;
- whether the result changes across holding horizon and regime.

T05a measures the behaviour that exists today from current fill/trade records and counterfactual closes of the same remaining quantity at the planned target and other predeclared exit policies. It does not add `runner_frac`, split lots or alter exits. Its append-only observation records live as JSONL beside T05 fill telemetry; T11 later migrates both into the versioned SQLite event store.

The audit must never silently use future candles to call an earlier exit "available." For every comparison it records `kind=hindsight_ceiling` or `kind=causal_policy`, the market-data resolution, decision timestamp, information cutoff, exit rule, modeled execution cost and confidence/limitations. With candle-only data, an intrabar high/low is an upper bound unless lower-timeframe ordering proves the exit could have occurred.

## Validation ladder

No candidate reaches Automatic live selection without all applicable stages:

1. causal indicator and warm-up tests;
2. same shared-core state transitions as live trading;
3. fees, funding, spread, slippage and partial fills;
4. high-resolution intrabar treatment appropriate to its timeframe;
5. walk-forward and untouched holdout;
6. sensitivity and regime/year breakdown;
7. portfolio/correlation test rather than standalone return only;
8. shadow signals with no orders;
9. bounded Testnet canary;
10. own-account live canary after the later explicit mainnet gate.

Failed candidates remain labelled **experimental** in Research. They are not silently deleted, promoted or used by Automatic mode. Recommend mode may list them only under an explicit **Experimental** label and must never propose one as the default.

## Delivery mapping

| Ticket | Deliverable | Trading authority |
|---|---|---|
| T05a | Runner/exit attribution and side/regime opportunity funnel | Observe only |
| T06–T09 | Shared live/backtest contracts and pure transitions | No strategy behaviour change |
| T09a | Range, scalp, short and runner research harness/candidates, including Quick Bank + bounded micro-DCA variants | Research, then Shadow |
| T11–T12 | Durable event store and user-facing Why? records | Observe/explain |
| T13 | Reproducible scorecard and readiness | Read-only |
| T13a | Catalog, calculation system, risk grades, sliders and control modes | Feature Off until its own gates |

The implementation stays ticketed and reversible. This plan does not authorize a live/mainnet install, an exchange setting change, credentials, a new order, or promotion of any experimental strategy.
