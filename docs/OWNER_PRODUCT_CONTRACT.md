# ZackBot owner product contract

**Binding owner requirements — 09 Oct 2026, Africa/Cairo**

This is the canonical, durable statement of the owner's product requirements. Claude Code, Cowork and Codex must read
it before planning, implementing, reviewing or accepting engine, strategy, risk, settings or UI work. Issue comments
and chat history may add detail, but a requirement is not considered safely captured until it is represented here.

These requirements remain binding until the owner explicitly changes them. Evidence may change labels, defaults,
warnings and automation authority; it does not remove an owner-requested feature from the catalog or silently remove
manual control.

## 1. Product authority and visibility

1. Every meaningful application feature and setting is visible in the final UI and owner-toggleable where technically
   safe. Simple, Guided and Pro modes change presentation, explanations and defaults; they do not erase owner control.
2. Every strategy family remains manually selectable, including unproven or parked families, with an honest evidence
   label and explicit warning. Only evidence-backed strategies may be recommended or automated by default.
3. Manual, Recommend and Automatic modes must be distinct and auditable. No mode may silently change an existing
   position when a setting changes.
4. The only non-toggleable rules are universal execution-integrity ceilings: no duplicate order, cross-account action,
   corrupt/unknown ownership treated as known, unprotected exposure, or unbounded account/portfolio risk.
5. The owner remains the final authority at the future mainnet boundary. This contract does not authorize mainnet.

## 2. Primary engine controls

The final engine UI exposes these primary controls globally and, where supported, per account, asset and strategy:

| Control | Required behavior |
| --- | --- |
| Controlled capital | The amount the bot may control even when the exchange balance is higher. |
| Follower minimum | Owner-entered follower capital with a feasibility preview for every intended order/strategy. |
| Direction Bias | Strong bearish through neutral to strong bullish; governs new-entry preference, never rewrites evidence. |
| Risk | Conservative through explicitly high risk; maps deterministically to dollar risk and hard ceilings. |
| Acceptable Drawdown | Limits strategy/basket/session/portfolio drawdown and previews the practical consequence. |
| Trade Horizon / Scalping Tempo | Swing/slow through intraday, fast, scalp and ultra-short; selects compatible validated strategy/timeframe cells. |
| Scalp Activity / Opportunity Density | Low through high; controls eligible re-entries, cooldown and concurrency, not hidden per-trade risk. |

A faster horizon may create a larger nominal lot when a validated stop is narrower, but it must not silently increase
dollars at risk. The Risk control owns the loss budget. Every change previews expected holding band, timeframe,
strategy/side, lot/notional, dollar risk, leverage/margin, stop/targets, fees/slippage, liquidation buffer, concurrency,
cooldown and follower feasibility. Changes apply to new entries unless the owner explicitly confirms an audited
management change.

## 3. Required strategy catalog

The catalog must cover both directions and differing risk/holding styles:

- independently calibrated trend-long and trend-short strategies;
- breakout and breakdown strategies;
- range/mean-reversion longs and shorts;
- short-horizon/Quick Bank scalps with early partial profit and a protected runner;
- an optional single, small, strictly bounded micro-DCA layer where separately validated;
- a rebuilt bounded DCA basket with hard basket stop, bounded depth/scale and total-risk accounting;
- manual entries governed by the same risk, protection, accounting and audit path;
- per-asset/per-timeframe regime preference and manual overrides;
- visible evidence status, risk/tail score, operational readiness and copy/follower feasibility for every family.

No strategy is promised to be profitable. Backtests and promotion decisions must include fees, spread, slippage,
funding/borrow, gaps, latency, capacity, sample size, long/short coverage, drawdown, tail loss and untouched holdout
evidence.

## 4. Confirmed-range scalp behavior

When an asset is in a confirmed range and the owner selects a fast horizon/high activity:

- favor independently calibrated lower-band long and upper-band short mean-reversion cells;
- allow more eligible re-entries and faster capital recycling within validated limits;
- take quick partial profit near the mean/first target, then close or keep a small protected runner toward the opposite
  boundary;
- permit the separately validated single micro-DCA only while range validity and total-risk rules remain true;
- stop new range entries and cancel pending risk-adding orders when the range is invalidated by breakout/volatility,
  volume, spread, slippage, latency, liquidity or an evidenced macro/news veto;
- never average through a confirmed breakout.

Session-level portfolio heat, loss, consecutive-loss, fee-drag and correlation ceilings apply across the entire scalp
sequence. Higher activity must improve after-cost expectancy or retained profit, not merely gross wins or win rate.

## 5. Range Scalp Basket

The UI includes an optional **Range Scalp Basket**. One basket is one durable, explicit asset/strategy/account scope; it
cannot silently combine unrelated positions, strategies, symbols, accounts or earlier exposure.

Required accounting and lifecycle:

1. Net basket P&L equals realized plus executable unrealized P&L minus fees, funding and conservative exit slippage.
2. Before profit-lock activation, a hard basket-loss/risk limit bounds downside.
3. After activation, retain `peak_net_profit` and a trailing floor derived from a minimum locked profit and allowed
   peak giveback.
4. Continue taking eligible scalps only while the range, risk floor, profit lock, liquidity/costs, session limits and
   protection remain valid.
5. When profit deteriorates through the floor or the range breaks, transition atomically to `DRAINING`: block new
   entries, cancel pending risk-adding orders and follow the selected `protect-and-drain` or `flatten basket` policy.
   DCA is forbidden after draining begins.
6. Basket identity, members, P&L/costs, peak, floor, regime version, state and stop reason survive restart. Unknown or
   corrupt state means HOLD, never a replacement overlapping basket.
7. Reset requires flat, reconciled state plus its configured cooldown/new range episode.

Owner controls: basket on/off, scope, profit-lock activation, peak giveback, minimum retained profit, maximum basket
loss/duration/trades/concurrency, cooldown and end policy. The live card shows net P&L after costs, peak, locked floor,
distance to stop, realized/unrealized P&L, costs, trade count, range confidence and exact continue/stop reason.

## 6. Regime and context requirements

- Each supported asset/timeframe receives its own trend/range/volatility regime; BTC, another crypto, gold and an index
  may be in different regimes simultaneously.
- Strategy handling supports Manual, Recommend and Automatic modes with hysteresis and reasoned decisions.
- Context candidates include DXY, US 2Y/10Y and curve, S&P/Nasdaq, oil, gold/other relevant minerals and timestamped
  geopolitical/news inputs. They begin observe-only and become veto/reduction inputs only after causal evidence.
- TradingView signals enter through the same risk gateway and never bypass strategy, ownership or protection rules.

## 7. Markets, accounts and product surface

The planned final product includes multiple accounts and exchanges, internal/copy-trading support, Binance lead/follower
feasibility, secure PWA/mobile monitoring and control, TradingView/news integration, crypto, exchange-traded and broker
gold, and paper-proven TradFi indices/stocks. Gold and TradFi keep separate venue/session/gap/borrow/corporate-action
contracts; they are not treated as 24/7 crypto.

Simple/Guided/Pro interfaces must remain coherent and responsive. Simple mode exposes the important outcome, risk and
controls without engine noise; Pro exposes the evidence, mechanics and advanced scopes. Accessibility, mobile behavior
and actionable error/recovery states are release acceptance requirements.

## 8. Post-Binance-live optional MetaTrader 5 execution bridge

**The current Binance execution path remains the native Binance API. MT5 does not replace, wrap or sit in front of
Binance, and it is not part of the present runnable-slice gate.**

Only after the Binance engine has crossed its live milestone and the owner explicitly opens the post-live expansion
phase may MetaTrader 5 become a separate optional ZackBot venue adapter for Vantage and for specific Bybit/TradFi
accounts whose chosen account product actually executes through MT5. A future native Bybit API adapter remains a
separate post-live option; do not assume every Bybit account uses MT5. For an MT5-connected account, ZackBot remains
the control plane for strategies, regimes, risk, baskets, settings, audit and owner decisions. An MT5 Expert
Advisor/gateway only executes approved ZackBot intents and returns that broker/account's truth to the app.

Required architecture and authority:

1. Use a versioned `VenuePort` adapter plus a minimal MQL5 Expert Advisor/gateway. The same domain intent and management
   actions used by replay/testnet feed MT5; strategies must not be duplicated or independently reimplemented in MQL5.
2. Communication is bidirectional: ZackBot sends idempotent order/cancel/modify/flatten intents; MT5 returns account,
   symbol, quote/session, order, deal/fill, position, commission, swap, rejection and terminal-health events.
3. Every message is authenticated, sequenced, replay-resistant and bound to environment, broker server, account,
   terminal instance and strategy/account scope. Credentials stay in the terminal/normal secret interface, never in
   Git, logs or mobile clients.
4. A durable mapping binds ZackBot intent/client IDs to MT5 request/order/deal/position tickets, magic number and
   comment metadata. Duplicate delivery is harmless. Lost/late/partial answers, reconnects and terminal restarts are
   reconciled against broker truth before new risk is allowed.
5. Unknown, stale, conflicting or disconnected state means HOLD: no new entries. Existing broker-side stops remain;
   protection/flatten actions receive priority when communication is healthy. An optional broker-side emergency limit
   may only reduce risk and cannot invent entries.
6. The ZackBot risk gateway remains authoritative, and the EA independently enforces a small immutable safety envelope
   (allowed account/environment, maximum order/position exposure, permitted symbols, no duplicate intent and reduce-
   only semantics where applicable). The EA may reject but never increase ZackBot size or risk.
7. Explicitly normalize MT5 hedging versus netting accounts, symbol aliases/suffixes, contract size, lot minimum/step,
   tick size/value, quote/profit/margin currencies and conversion, stop/freeze levels, fill policy, market execution,
   partial fills, sessions/holidays, rollover/swap, commissions, spread, gaps, borrow/short availability and corporate
   actions. Unsupported cells stay visible and disabled with the exact reason.
8. Market data provenance is explicit: broker/MT5 quotes may drive executable pricing and session truth, while research
   data remains separately versioned. The app displays source, freshness and divergence; it never silently mixes feeds.
9. The app—not MT5—is the owner UI. It exposes each MT5 account/terminal's connection, demo/live state, broker/server,
   permissions, positions, protection, pending intents, P&L/costs and incidents. Manual, Recommend and Automatic modes
   use the same owner controls and audit trail as crypto venues.

Acceptance proceeds through a deterministic fake bridge, recorded replay, MT5 Strategy Tester where applicable, and a
broker demo account before any live boundary. Required drills include duplicate/lost messages, out-of-order events,
partial fill, reject/requote, spread/gap, session close, terminal/EA/app restart, network partition, broker-side manual
trade, hedging/netting mismatch, symbol/rule change, stale quote and emergency flatten. Final truth must be flat or
explicitly protected and reconciled.

Roadmap placement: **after the Binance live milestone and explicit owner activation of post-live expansion**, VENUE-01
defines the generic multi-venue contract and MT5-01 separately builds/proves the optional bridge. GOLD-02 and
TRADFI-01 may consume it. Broker-specific plugins/configuration follow the shared bridge rather than forking the
strategy engine. Before that boundary, Vantage, Bybit and MT5 implementation are deferred and may not consume the
active Build, Evidence or Integration lanes needed for Binance readiness.

## 9. Traceability and final-UI acceptance

Every affected ticket/PR must state:

- which contract sections it implements, changes or leaves untouched;
- whether the behavior is absent, scaffolded, implemented, evidenced, automated or release-ready;
- tests/evidence for mapping, persistence, restart, preview, manual/recommend/automatic authority and failure behavior;
- any deliberately unsupported asset/strategy/account cell, visibly labeled in the UI;
- confirmation that user controls did not weaken universal integrity ceilings.

Before the engine/final UI is accepted, maintain a requirements matrix mapping every numbered section above to code,
UI surface, tests, evidence and current status. A feature hidden only in configuration or code does not satisfy a UI
requirement. A toggle without deterministic behavior, preview, persistence and an audit reason is incomplete.

## 10. Change control

New explicit owner instructions append or revise this file promptly. Do not silently weaken an earlier requirement to
fit an implementation. If two owner requirements conflict, record the conflict and obtain an explicit ruling at the
point where it materially changes behavior. Historical issue #13 remains the discussion log; this file is the canonical
product contract.
