# ZackBot roadmap

*Plan v4, 2026-10-05 (Cairo).*

> **Execution control:** the replacement-first build order, three-agent responsibilities, bug/scouting interruption rules
> and current next steps are maintained in [`docs/NEWCORE_EXECUTION_PLAN.md`](docs/NEWCORE_EXECUTION_PLAN.md). This file
> remains the long-term product vision; the execution plan controls the near-term sequence when the two differ.
> The owner's primary milestone is the mainnet-candidate **engine**: long/short strategy coverage, bounded risk/bias/
> drawdown controls and crypto/gold/TradFi venue readiness precede optional product features.
> Binding owner feature, settings and final-UI requirements live in
> [`docs/OWNER_PRODUCT_CONTRACT.md`](docs/OWNER_PRODUCT_CONTRACT.md) and must be traced through implementation and
> acceptance rather than reconstructed from chat history.

This plan merges:
- the companion "ZackBot Master Roadmap & Build Vision" (32 pages, 2026-10-05);
- Claude's review of it;
- Binance's current lead-trading rules.

The PDF holds the long-form rationale. This file is the working plan that lives with the code.

## Target

The first product target is a testnet-proven engine whose **controlled bot allocation** can start around **$500**, with
copy-feasibility previews for followers at **$100, $200 and $500**. These are sizing and feasibility targets, not a
promise that every strategy or Binance order will fit every balance.

The later production-capital target remains live trading from an account funded at **$2,000+**, run as one engine across
**several Binance accounts**:
- own trading accounts;
- **at least two master (lead) portfolios** that others copy.

The controlled bot allocation is distinct from total account or lead-portfolio capital: unused account equity is not
automatically risked. Everything below is built for that end state now, so nothing has to be rebuilt later. Each phase
still ships behind safe defaults and its own gate.

## North star

A feature is done only when it is:
- causal;
- reproducible;
- backtested;
- parity-checked against the live engine;
- explainable;
- controllable through a toggle with a safe default;
- observed on testnet or a canary;
- versioned and reversible.

---

## A. Binance facts that shape the design

Checked 2026-10-05 against Binance FAQ pages. Re-verify before each live step, because these rules change.

| Fact | Consequence for ZackBot |
|---|---|
| A lead portfolio is its own **Copy Trading account**, separate from the normal Futures account. | The engine must treat every portfolio as a separate account: own keys, balance, state and reconciliation. |
| **Copy trading is not supported for sub-accounts.** | The current testnet sub-account setup cannot become a master. Lead portfolios live under a **main** account. |
| Per Binance account: **1 public + 1 private** lead portfolio. | "A couple of masters" = one public + one private on one account, or more main accounts, each owned and verified by its real owner. |
| Minimum **500 USDT** to create a portfolio. The withdrawable amount is the balance minus 500. | Each master needs its own dedicated capital. The bot-capital cap must never treat the locked 500 as free profit. |
| Up to **2 API keys per lead portfolio**. Futures, **USDT only**. **No Multi-Assets mode.** **IP whitelist** recommended. | Keys are per portfolio. Settings must refuse Multi-Assets. A **static IP is needed, which means a VPS.** |
| Lead API order limit: **20 requests per 10 seconds**. | Our stop-first replacement, DCA adds and partial TPs can burst. Each account needs an **order budget with priority**: protection first, then exits, then adds and entries. |
| A portfolio can't be closed with open positions; a 72-hour cooldown may apply after a loss. | Mistakes on a public master are sticky. A master must never be the place to experiment. |
| Followers copy into their own accounts. The public minimum copy amount defaults to 10 USDT. Lead earns up to 30% profit share. | Many small orders (DCA steps, pyramids, partial TPs) can fall below **followers'** minimum order sizes. Master profiles must be **copy-friendly**. |
| No documented testnet for lead portfolios. | The last rehearsal is a **private lead portfolio at minimum size, with no copiers**. |

Sources:
- [Binance: API key for a lead trading portfolio](https://www.binance.com/en/support/faq/how-to-create-an-api-key-for-a-futures-lead-trading-portfolio-2bec848b904b422197ce121d0925f20b)
- [Binance: Futures copy trading FAQ](https://www.binance.com/en/support/faq/frequently-asked-questions-on-binance-futures-copy-trading-6ed0995daf0b42d5816beaf1e31ca09d)
- [Binance: How to lead trade](https://www.binance.com/en/support/faq/how-to-lead-trade-on-binance-futures-6acfb4c1f50c4db1b9e915181ff31a4c)

---

## B. Decision rules

These apply to every phase.

1. **Engine and protection come first.** Stop and close work always outranks the UI, audits, research, ML and news.
2. **Every behaviour-changing feature needs:**
   - a policy (mode, scope and safe default);
   - full-config validation;
   - an audit trail;
   - a rollback path.
3. **One shared risk gateway.** Strategies, manual trades, TradingView, news, ML and copy logic are all *inputs* to it. None may bypass it.
4. **No automatic system may raise risk above the user's mechanical limit.** ML and the governor may only reduce risk.
5. **Account isolation.** One account's failure, rate limit or bad state can never delay another account's stop management.
6. **Masters are production.** Nothing reaches a lead portfolio without passing through an own live canary first.
7. **Do not relax replay thresholds to make a feature pass.** Fix the shared logic or the simulator instead.

**Required evidence by change class:**

| Class | Examples | Minimum evidence |
|---|---|---|
| A — Presentation | Layout, copy, hidden or visible fields | API compatibility, Playwright screenshots, no console errors |
| B — Settings/policy | Toggles, limits | Validation, migration, round-trip, audit, behaviour tests |
| C — Signal | Strategy, TradingView, news veto | Causality, walk-forward, point-in-time inputs, shadow mode |
| D — Management | Stops, targets, DCA, pyramids, runners | Shared-core tests, strict replay, gap stress, testnet fills |
| E — Risk/execution | Sizing, leverage, adapters, copy sizing, order budget | Invariants, reconciliation, failure injection, canary |
| F — Model | Scoring or ML with authority | Frozen dataset, walk-forward, calibration, shadow/challenger, drift and rollback |

---

## C. Phases

### Phase 0 — Verified baseline (now)

**Status on 2026-10-05:**

| # | Item | Status |
|---|---|---|
| 0.1 | Git from the rc2 bundle | ✅ Done. History in `%LOCALAPPDATA%\ZackBot\history.git`, identical to tag v3.2-rc2; use `..\ZackBot2_git\zbgit.bat`. |
| 0.2 | Branches: `master` protected, one branch per bounded change | ✅ Done. Pull request, fast/full checks and linear history required; force-push and deletion blocked. |
| 0.3 | Playwright on Windows | ✅ Done (T02): `run_ui_baseline.bat`, 171/171 on Windows. |
| 0.4 | Installer rehearsal | ✅ Done (T03): failure-path rollback drill and normal install verified; current test build `20261005-204405`; hashes, authenticated restart, source mirror, shortcuts and protected positions confirmed. |
| 0.5 | CI | ✅ Done (T04/T04b): fast on pushes; full on pull requests; release gate on Windows; fixed Ubuntu 24.04 and immutable Node-24 action pins. |
| 0.6 | One testnet canary | ◐ Running v3.2. The slot set must equal a tested profile; the DCA1H2 duplicate is pending a decision. |
| 0.7 | Fill telemetry | ⬜ Log every maker/market fill: expected vs actual price, wait, partials, fallback. |

**Exit gate:**
- the source is in Git;
- all tests and strict replays pass from a clean environment;
- the UI harness passes on Windows;
- the install **and** rollback drill have been rehearsed;
- the canary shows healthy stop and reconcile telemetry for at least 7 days.

### Phase 1 — Account-aware shared core

This is the highest priority. Every later phase depends on it.

| Ticket | Work | Acceptance |
|---|---|---|
| 1.1 | Pure functions for rounding, costs, fees, slippage and funding | Zero replay change |
| 1.2 | Pure sizing and caps (bot allocation, slot share, stop distance, leverage cap) | Zero replay change |
| 1.3 | Pure management levels (stop, breakeven, target, DCA, pyramid, runner, trailing) | All tests and replays pass |
| 1.4 | **Trade state transition**: (lot, settings, closed-candle context, path event, portfolio/account context) → (new state, actions) | Live sim and backtest call the same function; strict parity holds |
| 1.5 | Execution translation: live → exchange orders; backtest → simulated fills | Unchanged behaviour |
| 1.6 | Unified **reason codes** for every decision and action | The same codes appear in live, backtest and audits |
| 1.7 | **`AccountContext` from day one**: keys, venue, allocation, rate budget, state dir and guards per account. Today runs as a single account through it. | Two simulated accounts in one process, fully isolated |
| 1.8 | **Order budget and priority queue** per account (e.g. 20 per 10 s for lead keys): protect > close > reduce > add > enter. Never drop a protection order. | Burst tests: stops always go first; adds are deferred, never lost silently |
| 1.9 | New replay scenarios: gap through stop, min-order rounding and dust, maker partial/no fill, risk-rule blocks, lost API answers, restart with open plans, grid/COMBO | All strict |

**Refactor rules:**
- characterise behaviour before moving it;
- move one behaviour family per commit;
- never mix a refactor with new strategy behaviour;
- pure functions never touch the clock, network or filesystem.

**Exit gate:**
- live and backtest share the core;
- the replay set has expanded and passes strict thresholds;
- research tables have been regenerated against a versioned manifest.

### Phase 2 — Trade Decision Record

Data foundation per account, stored in **SQLite**.

- **What gets recorded:** an append-only event store for considered, rejected, opened, modified and closed trades.
- **Contents of each record:**
  - point-in-time market snapshot;
  - risk plan and rule results;
  - execution (request, ack, fills, latency, slippage, fees);
  - lifecycle;
  - close reason and R;
  - causal MFE/MAE.
- **Integrity:** every event is hashed and chained to the previous one.
- **UI and exports:**
  - a "Why?" view on every trade;
  - JSON and Markdown exports with secrets removed;
  - counterfactual replays, clearly labelled as hindsight.
- **Failure rule:** if the audit store fails, the bot **alerts** but never blocks protection.
- **Exit gate:** every path emits reason codes, history survives restarts, and live, testnet and backtest share one event schema.

### Phase 3 — Strategy scorecard, readiness and copy suitability

This is the top product priority. It comes before ML.

**Four scores are kept separate:**
- performance;
- risk;
- evidence readiness;
- operational readiness.

A high score **never** changes settings automatically.

**Scorecard v1 weights:**

| Component | Weight |
|---|---:|
| Risk / tail | 25 |
| Robustness (walk-forward, holdout, sensitivity, regime breadth) | 25 |
| Return quality | 15 |
| Execution realism | 15 |
| Diversification | 10 |
| Data quality | 10 |

**Hard caps:**
- small sample;
- no untouched holdout;
- no point-in-time coin universe (survivorship);
- uncalibrated maker fills;
- any lookahead or state mismatch.

**Audit readiness:**
- the bot calculates it;
- the user only sets the notification threshold (50–90%, default 70%);
- DCA and pyramid adds never count as independent trades.

**New for masters: copy-suitability score.** It covers:
- order count per trade;
- share of orders under follower minimums at 10, 50 and 100 USDT copy sizes;
- max DD and worst month (a public curve can't hide);
- holding time;
- leverage-tag risk (Binance tags ≥ 20×).

**Exit gate:** scores reproduce from frozen manifests, and the UI cannot confuse readiness with profitability.

**High-risk DCA / martingale mode (owner decision 2026-10-07; later, not now).**
- What it is: an opt-in recovery mode (DCA ladder / martingale-style sizing).
- When it is built: only after the verified backtests with the future strategies are done, and only through the normal
  realism, cost, causality, risk and testnet gates.
- Safety rules:
  - Whenever it is enabled, the panel shows a persistent, prominent **HIGH RISK** bar.
  - It is never on by default.
  - Its backtest numbers stay labelled "unverified" until they pass the BT01+BT02 re-validation.
- Until then, the existing DCA profiles keep their old numbers but are labelled **UNVERIFIED** everywhere (AUD-06a).

### Phase 4 — 24/7 operations (new phase; required before any live master)

- **VPS:**
  - Linux;
  - **static IP** for the whitelisted keys;
  - runs the engine as a supervised service with auto-restart and a watchdog.
- **Secrets:**
  - an encrypted store, since DPAPI doesn't exist on Linux;
  - keys IP-restricted, withdrawals disabled, futures only.
- **Monitoring:**
  - heartbeat and **dead-man alerts** through Telegram and email;
  - stale-data detection;
  - daily health report.
- **Deployment:** build from a Git tag → self-test → verify the build id → staged rollout per account → one-command rollback.
- **Data:** backups for state, SQLite and settings, plus a restore drill.
- **Local panel:** stays available over an SSH tunnel or an outbound agent. **The loopback port is never exposed.**
- **Exit gate:**
  - 14 days on the VPS against testnet with zero missed management loops;
  - the restore drill and the rollback drill pass.

### Phase 5 — Multiple accounts and internal copy

- **Workers:**
  - one worker per account, each with its own rate limiter, state, reconciliation, guards and health;
  - portfolio-level oversight across all accounts;
  - per-account emergency controls.
- **Internal copy (own accounts):**
  - copy **risk intent, not quantity**: each follower sizes from its own allocation, limits and minimums;
  - every follower gets its own protective stop;
  - follower rejections are recorded;
  - late and partial fills are handled.
- **Modes per follower:** Off / Shadow / Testnet / Live.
- **Exit gate:**
  - two testnet accounts take the same intent at different valid sizes;
  - each reconciles independently;
  - each survives the other's outage.

### Phase 6 — Live canary on an own main account ($2,000)

- **Setup:**
  - a tested low-drawdown profile (currently Steady mix);
  - bot capital starts small (for example $500 of the $2,000);
  - **mainnet keys:** IP-locked, no withdrawals.
- **Measure:**
  - live vs backtest drift per trade (R, fills, fees, funding);
  - protection latency;
  - reconcile corrections.
- **Scale-up:** the cap rises in steps only after explicit owner approval at each step.
- **Gate to Phase 7:**
  - **≥ 8–12 weeks live**;
  - no unprotected or untracked positions;
  - drawdown inside the Monte Carlo band;
  - strict replay unchanged;
  - fill telemetry calibrated.

### Phase 7 — Master (lead) portfolios

1. **Private lead portfolio, 500 USDT minimum, no copiers.** This is a full production rehearsal on lead keys: order budget, IP whitelist, USDT-only, no Multi-Assets.
2. **Copy-friendly master profile:**
   - fewer and larger orders;
   - no DCA steps or partial TPs that fall below follower minimums;
   - leverage under the 20× tag;
   - risk sized for a public equity curve.
3. **Public portfolio.** Publication policy:
   - what is shown;
   - monthly report;
   - risk disclosure;
   - **no return promises**.
4. **Second master:**
   - either the private portfolio becomes a second strategy;
   - or a separate main account owned by its real holder.
   - Masters never share failure paths.
5. **Follower-impact monitoring:**
   - estimated follower slippage;
   - missed copies;
   - profit-share accounting.

- **Exit gate:**
  - the private master matches the own-account canary;
  - copy-suitability score is above threshold;
  - an incident playbook exists: what to do with open positions if the engine or VPS fails.

### Phase 8 — PWA and remote control

**Architecture:** the engine on the VPS connects **out** to a small HTTPS control service. Phones never reach the engine directly.

**Capability tiers:**

| Tier | What it allows | Protection |
|---|---|---|
| Monitor (default) | Viewing | — |
| Safe controls | Pause/resume, acknowledge alerts | — |
| Dangerous controls | Flatten, raise risk, keys, allocation | MFA, re-authentication, confirmation, cooldown |

**Resilience:**
- commands are idempotent and expire;
- devices can be revoked;
- the app shows stale data clearly;
- Telegram stays as the independent backup channel.

### Phase 9 — Simple / Guided / Pro, and direction preference

- **One engine with three views.** Mode changes never alter open trades.
- **Beginner controls:**
  - bot capital;
  - discrete Risk Level and Loss Tolerance settings;
  - Long/Short preference (new entries only; low value until a short strategy proves itself).

### Phase 10 — ML in shadow

**Can start in parallel after Phase 3 data exists.**

- **Separate models:** signal quality, regime, and execution quality.
- **Authority ladder:**
  1. research;
  2. shadow;
  3. veto-only;
  4. risk reduction (0–1×);
  5. never above 1×.
- **Model rules:**
  - logistic-regression baseline first;
  - champion/challenger;
  - drift monitoring with automatic loss of authority.

### Phase 11 — TradingView and real news

- **TradingView webhook:**
  - durable queue and idempotency;
  - age checks, allowlists and Pine version hash;
  - modes: Off / Observe / Testnet / Live.
- **News:** a real `NewsProvider` with source, freshness and expiry. It may warn or veto only. The existing LLM reviewer stays separate.

### Phase 12 — Gold, TradFi and shareable release

- **Gold:** PAXG spot first, then XAUT, then broker XAUUSD.
- **TradFi:** paper-only until sessions, gaps and corporate actions pass their tests.
- **Vantage / Bybit / MetaTrader 5 (post-Binance-live only):** keep Binance on its native API and finish its engine,
  strategies, evidence and live milestone first. Only after the owner explicitly opens post-live expansion may a
  fail-closed MT5 bridge serve Vantage or specific Bybit/TradFi account products, or a native Bybit API adapter begin.
  These adapters cannot enter the active queue or consume Binance-readiness lanes before that boundary.
- **Shareable release:**
  - signed installer and updates;
  - onboarding;
  - per-user isolation;
  - legal and disclosure text.

---

## D. First tickets

| ID | Ticket | Mode | Status |
|---|---|---|---|
| T01 | Git baseline | No behaviour change | ✅ Done 2026-10-05 (history in `%LOCALAPPDATA%\ZackBot\history.git`, tag v3.2-rc2) |
| T02 | Playwright on Windows | No behaviour change | ✅ Done 2026-10-05 (accepted after 3 reviews: Windows 171/171 UI checks, 192/192 tests, strict replays unchanged; branch `t02-ui-baseline` @ 2fc8642) |
| T03 | Installer rollback drill ("simulate failed launch" switch) | Deployment only | ✅ Done 2026-10-05 (failure-path drill passed; normal install build 20261005-204405 passed; mirror + shortcuts updated; 4/4 lots protected; 207/207 Windows tests; Codex final review has no open findings) |
| T04 | CI fast/full pipelines: `verify fast` (static checks, unit tests, installer preflight; < 3 min), `verify full` (all tests, strict replays, full UI harness, build + exe self-test), `verify release` (full + Windows rollback drill + testnet reconciliation). Each writes one machine-readable summary: commit, build id, dataset manifest hash, dependency versions, pass/fail/skip counts, replay metrics, UI evidence path, exe hash, Cairo start/finish | No behaviour change | ✅ Done 2026-10-06 (Windows full PASS; GitHub fast/full green; fail-closed release gate; protected `master`; PR #1 merged) |
| T04b | Deterministic CI runner/action maintenance: fixed Ubuntu 24.04 image and immutable Node-24 action pins | No behaviour change | ✅ Done 2026-10-06 (accepted; PR #2 merged as `ad1d584`). Follow-up: Dependabot + history/dependency scanning |
| T03a | Leverage-refusal fallback: when Binance refuses a leverage change (testnet `-1000` on every SOLUSDT/XRPUSDT attempt so far), read the coin's current leverage (read-only); enter only if it is at or below the cap, otherwise skip as today; count refusals per coin in the panel | E-class (order path): tests + strict replays + canary | ✅ Done 2026-10-06 (code review and CI green; build `20261006-030952` installed; bounded SOL testnet canary safely skipped at current 20× > cap 10×; no order; existing positions stayed protected) |
| T03b | Installer logic moved from CMD into a structured PowerShell script (functions, real error handling, testable); `build_app.bat` / `rollback_drill.bat` stay as tiny double-click launchers. Same steps, same fail-closed rules, drill re-run | Deployment only | ✅ Done 2026-10-06 (Windows tests/buildcheck, normal install and verified rollback drill passed; PR #5 merged as `d68d6ef`) |
| T04d | Faster CI: remove duplicate work, keep canonical required gates, and preserve evidence/provenance | No behaviour change | ✅ Done 2026-10-06 (PR #7 merged as `cb5092b`; exact-head labelled full gate and protection proof passed) |
| T05 | Fill telemetry (maker/market expected vs actual) | Observe | ✅ Done 2026-10-06 (PR #6 merged in protected master as `566ed56`) |
| T03c | Automatic leverage handling after a refusal: allow above-cap coin settings only under cross margin after a fresh account-wide exposure, stop and bracket proof; fail closed on every unknown | E-class (order path): adversarial tests + strict replays + owner-approved canary | **Accepted** at `ee564c1`; exact-head CI and the corrected two-refusal SOL testnet canary passed; build `20261006-233104` installed and cleanup healthy |
| T05a | Causal trade audit: MFE/MAE, peak profit and give-back, hold/close reasons, earlier-exit counterfactuals, runner/side/regime attribution | Observe/research | ⬜ Planned in PR #8; follows T05 |
| BT01 | Causal intrabar OHLC path shared by engine replay and backtest | Backtest realism | ✅ Done 2026-10-07 (PR #16 merged as `ef6abc0`) |
| BT02 | Binance step/minimum feasibility, full-plan leverage simulation and truthful capital guidance | Backtest/UI realism | ✅ Accepted 2026-10-07 at `e224a8b`; protected merge pending. Follow-ups: hedge partial-close runtime proof, small API/docs cleanup, and the pre-existing T02 calendar-day harness repair |
| T06 | Superseded by NEWCORE B5/NC-01 | Replacement-first; no legacy extraction sequence | ⛔ Superseded |
| T07 | Superseded by NEWCORE NC-06/NC-08 | Sizing/cost contracts move into the replacement | ⛔ Superseded |
| T08 | Superseded by NEWCORE NC-07 | Management transitions move into the replacement | ⛔ Superseded |
| T09 | Superseded by NEWCORE NC-07/NC-08 | State/replay parity moves into the replacement | ⛔ Superseded |
| T10 | Per-account order budget and priority queue | E-class, burst tests | ⬜ |
| T11 | SQLite trade-event store | Feature Off | ⬜ |
| T12 | Entry/close reason records + "Why?" view | Observe | ⬜ |
| T13 | Scorecard v1 + readiness + copy suitability | Read-only | ⬜ |
| T14 | VPS service, secrets, heartbeat, deploy/rollback | Ops | ⬜ |
| T15 | Multi-account workers + internal copy (shadow) | Off/Shadow | ⬜ |
| T16 | ML dataset builder (leakage checks, manifests) | Research | ⬜ |

**Current controlling order:** `docs/NEWCORE_EXECUTION_PLAN.md` supersedes the old T06–T09 extraction sequence. The legacy engine is mechanically frozen and disposable: confirmed failures become NEWCORE regression contracts, not a legacy repair/cleanup program. NEWCORE owns replacement, strategy/risk work and the numeric engine gate. GitHub is currently public by the owner's testnet-only decision and protected by required checks; it must return to private before mainnet credentials or release work. Keys, `config.env`, session tokens, account data, logs, market data, executables and evidence remain excluded.

**Ticket rules:** one ticket at a time, in this order. A ticket is marked ✅ (with the date) only when its acceptance checks pass **and** the other assistant's review has no open findings. ◐ = implemented, review or Windows run pending.

---

## E. Stop-work triggers

Work stops immediately on any of these:
- any position or state mismatch in strict replay;
- any sign of future-data leakage;
- any path that can remove protection before replacement or close is confirmed;
- any build that can't prove its version and commit;
- any audit, ML or UI work that delays management or emergency close;
- any account adapter that can't reconcile after a lost response;
- any rate-limit condition that could defer a protective order.

---

## F. Done (history)

### v3.2-rc2 (2026-10-05)

- **BTC breaker:** pauses pyramid adds; DCA safety orders follow a per-basket policy fixed at entry.
- **Replay metrics:** trade-level metrics with a strict release gate; both scenarios pass.
- **Causality tests:** engine perturbation test, synthetic parity, and a leak detector.
- **Installer:** verified backup, hash checks, post-launch HMAC ping, automatic rollback.
- **Engine:** short-history guard.
- **Git history:** set up with version tags.

### v3.2 (2026-10-05)

- **Add gate:** risk rules, cap snapshot, orphan block.
- **Installer:** fail-closed, with exe self-test.
- **Settings:** deep-merged management settings.
- **UI:** exit plans and Cairo time.
- **AI review:** honest AI prompt.
- **Backtester:** same-candle ATR lookahead fixed.
- **Reproducibility:** reproducible research.

### v3.1 (2026-10-04)

- Telegram control.
- Order and exit types.
- Grid/COMBO (experimental).
- Backtest Lab.
- Portfolio risk rules.
- Regime filters and governor.

### v3.0 (2026-10-04)

- **Order safety:** idempotent orders, stop-first replacement, pending markers and reconciliation.
- **Security:** DPAPI and panel security.
- **Backtester:** the path-based backtester.
- **UI:** the UI overhaul.
