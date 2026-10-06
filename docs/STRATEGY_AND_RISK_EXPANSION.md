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
| Maniac | 4.00–5.00% | 25–35%+ | Extreme; Research/Testnet by default until a later explicit mainnet policy gate |

The calculation uses loss at the exchange-side stop, total open and correlated risk, gross exposure, margin use, liquidation distance, planned DCA/pyramid commitments, daily loss and current drawdown. Leverage alone is never treated as risk. No automatic component may exceed the user's controlled capital, loss tolerance or mechanical caps.

The user-entered **controlled capital** remains the sizing base and allocation ceiling even when the exchange wallet contains more money.

### 3. Control authority

| Mode | Behaviour |
|---|---|
| Manual | Trade only the exact strategy configuration selected by the user |
| Recommend | Calculate and explain proposed changes; the user approves them |
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

The initial research timeframes are 15 minutes and 1 hour. Genuine 1–5 minute strategies require retained high-resolution data, order-book/spread inputs, realistic latency, partial-fill and queue modelling, and more demanding execution infrastructure. A selectable timeframe is not evidence that a scalp is production-ready.

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

- Add an explicit `runner_frac` and parent/child attribution.
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
- fees, funding and slippage attributable to the extra holding period;
- whether the result changes across holding horizon and regime.

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

Failed candidates remain labelled **experimental** in Research. They are not silently deleted, promoted or used by Automatic mode.

## Delivery mapping

| Ticket | Deliverable | Trading authority |
|---|---|---|
| T05a | Runner/exit attribution and side/regime opportunity funnel | Observe only |
| T06–T09 | Shared live/backtest contracts and pure transitions | No strategy behaviour change |
| T09a | Range, scalp, short and runner research harness/candidates | Research, then Shadow |
| T11–T12 | Durable event store and user-facing Why? records | Observe/explain |
| T13 | Reproducible scorecard and readiness | Read-only |
| T13a | Catalog, calculation system, risk grades, sliders and control modes | Feature Off until its own gates |

The implementation stays ticketed and reversible. This plan does not authorize a live/mainnet install, an exchange setting change, credentials, a new order, or promotion of any experimental strategy.
