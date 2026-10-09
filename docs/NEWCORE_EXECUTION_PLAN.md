# ZackBot NEWCORE execution plan

**Control plan v3.1 — 08 Oct 2026, Africa/Cairo**

This is the shared execution document for the owner, Claude Code, Cowork and Codex. It converts the historical audit,
the product roadmap and the replacement-first decision into one ordered build plan.

The binding feature, control and final-UI requirements are maintained in
[`OWNER_PRODUCT_CONTRACT.md`](OWNER_PRODUCT_CONTRACT.md). Every affected ticket and acceptance decision must preserve
its traceability; this execution plan controls sequencing, not whether an owner requirement is remembered.

The objective is not to preserve the current installed bot. The objective is a clean, testable and operationally safe
new system that is measurably better than the legacy implementation. Legacy code is evidence: reuse a proven contract,
formula, fixture or regression test when it deserves to survive; do not port structure or compatibility merely because
it already exists.

## 1. Target and boundaries

The target product is one engine that can:

- trade long, short, trend, range and bounded scalp strategies through one risk gateway;
- run the same decision and management logic in backtest, replay, paper/testnet and live execution;
- isolate several accounts and exchanges, including Binance lead/copy portfolios;
- explain every decision, order, hold and close;
- support Simple, Guided and Pro interfaces plus secure mobile monitoring/control;
- expand to TradingView, news, gold and TradFi without bypassing core risk or execution contracts.
- only after the Binance live milestone and explicit owner activation, consider separate Vantage, Bybit and MT5 venue
  adapters; they cannot enter the active queue or delay the native Binance API engine and strategy path.

### Owner priority: engine-first mainnet candidate

The primary product milestone is a **mainnet-candidate engine**, not a finished SaaS shell. Before optional product
features take priority, the engine must have:

- one optimized live/backtest/replay core;
- credible long and short coverage across trend, range and short-horizon conditions;
- mechanically bounded risk from conservative through explicitly high-risk modes;
- controlled-capital, direction-bias, risk and acceptable-drawdown controls;
- crypto plus gold and TradFi-capable asset/venue contracts, with each venue proven in paper/test mode;
- exchange-truth execution, durable ownership, protection, accounting and explainability;
- enough operational safety to run unattended: encrypted secrets, watchdog, kill switch, alerts and recovery.

PWA, copy-product polish, broad remote controls, social features and secondary integrations follow this milestone. This
does not grant permission to trade mainnet early; it means the engine is designed and evidenced to mainnet standards
while the remaining product is built around it.

**Delivery ruling (owner, 09 Oct 2026):** get the minimum trustworthy Binance long/short engine onto testnet as soon as
possible, then use testnet findings to drive deeper safety/security, recovery coverage, final UI and full settings work.
Before first useful testnet execution, require only the narrow floor that makes evidence trustworthy: environment/account
binding, bounded exposure, idempotency, confirmed protection or HOLD, reconciliation/restart ownership, emergency exit
and telemetry. Optional hardening, speculative edge matrices and release polish cannot block the vertical slice. Every
mainnet/live gate remains mandatory after testnet and before explicit live activation.

Development remains PAPER/Binance Futures testnet until the release gate explicitly changes it. Testnet balances and
positions are disposable. Mainnet credentials, funds and deployment remain a separate owner-approved boundary.

## 2. Replacement-first rules

1. **Contracts survive; architecture does not automatically survive.** Capture exchange safety, state transitions,
   sizing rules and causal evidence. Rewrite or delete weak legacy implementations.
2. **One implementation path.** Live, replay and backtest may use different adapters, but not different trading logic.
3. **No compatibility shims by default.** A shim needs a named consumer, expiry ticket and regression test.
4. **No new feature enters the legacy engine merely to be ported later.** New behavior starts in NEWCORE unless it is a
   narrow safety fix needed to capture a contract.
5. **Delete after proof.** Legacy code is removed after its replacement passes contract, replay and fault tests.
6. **Risk only moves downward automatically.** Models, regimes and news may veto or reduce risk, never raise it above
   the user's mechanical ceiling.
7. **Protection outranks entry.** Protect > close > reduce > reconcile > add > enter > research.
8. **Engine completeness outranks feature count.** A new interface/integration cannot move ahead of a missing strategy,
   risk, execution or venue contract needed by the mainnet-candidate engine.
9. **Optimization is continuous, not a later rewrite.** Every ticket removes touched-file duplication/dead code and
   proves performance budgets, while broad unrelated cleanup stays out of the critical build path.
10. **The legacy engine is mechanically frozen and disposable.** Audit failures become regression fixtures and NEWCORE
    contracts, not a second legacy repair program. Legacy code changes only when a defect prevents trustworthy data/
    contract collection, blocks NEWCORE validation, or can affect anything outside the confirmed testnet boundary. Every
    exception needs Codex scope, reports the `engine.py` line-count/top-level-definition delta and adds no product behavior.

## 3. Team operating model

The owner is the ultimate product authority. Codex is the binding roadmap, scope, prioritization and integration owner:
it maintains the `NOW / NEXT / LATER / POST-LIVE` queue and redirects work that does not advance the active milestone.
Claude Code implements and Cowork independently validates/researches within that queue. Both continue autonomously from
the next ready non-conflicting item, but material scope expansion or reordering requires a Codex ruling. Every handoff
states `ROADMAP FIT:` and `DEFERRED:` so multi-agent capacity accelerates the plan instead of creating distractions.

| Role | Primary responsibility | Must not do |
|---|---|---|
| **Claude Code — implementation owner** | Product code, migrations, focused regression tests and touched-file cleanup; publishes exact-SHA handoffs. | Self-accept, silently broaden a ticket, or preserve legacy code without a named contract. |
| **Cowork — research/adversarial-evidence owner** | Independent Linux/sandbox validation; strategy research; backtest-design review; frozen-data runs that fit its limits; assumption, leakage and overfit attacks; scouting briefs. | Change the implementation under review, claim Windows/network/testnet evidence it did not run, or report a timed-out test as passed. |
| **Codex — integration/roadmap owner** | Ticket boundaries, reproduction, architecture review, severity, ordering, exact-head acceptance, protected merge and owner summary. | Build a competing implementation while Claude owns the ticket or merge without independent evidence. |
| **Owner — product authority** | Product priorities, capital/risk intent and eventual mainnet/release decision. | Routine copy/paste, test approvals or agent coordination. |

### Standard ticket loop

1. Codex publishes scope, dependencies, acceptance contract and exact base SHA.
2. Claude implements on one non-protected branch and runs focused tests.
3. Claude posts `READY FOR CODEX` and `HANDOFF TO COWORK` with the exact SHA.
4. Cowork validates independent claims and posts exact-head evidence.
5. Codex reviews the diff, reproduces important risks and returns prioritized findings.
6. Revisions use fast/focused checks. The expensive full suite runs once on the accepted exact head.
7. Codex advances `full-ready`, verifies protection, merges and assigns the next ticket.
8. Every handoff or review marker ends with a `LANES:` line naming the current Build, Evidence and Integration work. An
   idle lane must name its next task.

GitHub commits and PR comments are the message bus. Five-minute polling is only a connection fallback.

### Progress acceleration rules

The order below is a dependency map, not a rule that every item must wait for an entire wave to finish. A ticket may
start as soon as its **direct** dependencies and file ownership are clear.

Three lanes should remain active whenever useful work exists:

| Lane | Owner | Work that can run concurrently |
|---|---|---|
| **Build** | Claude Code | The active product ticket. Claude may use independent Code subagents/worktrees for non-overlapping modules/tests, with one integration owner and no parallel edits to shared state. |
| **Evidence** | Cowork | Black-box Linux/sandbox tests for the active head; prepare the next ticket's adversarial harness; frozen-data backtests within its runtime limit; strategy research and scouting. Never edit the implementation being reviewed. Windows, native UI, installer and connected testnet evidence comes from Claude Code/Codex Windows runs or an available owner-PC link. |
| **Integration** | Codex | Review the current exact head; reproduce risks; draft the next acceptance contract/ADR; maintain status, dependencies and merge gates. Never build a competing product patch. |

Acceleration controls:

1. **Never idle on a wait.** Whenever a background agent or job finishes, a handoff marker is posted, or a review or CI
   wait starts, each agent confirms its lane has a running task. If not, it immediately starts the next useful,
   non-conflicting ready-queue item: next-ticket preparation, repros, contract drafts, harnesses, test-only slices or review.
2. **Submit as soon as ready.** Post handoffs, evidence, drafts and findings as soon as they exist, in pieces rather than
   batching them.
3. **Pre-stage the other agents.** Claude posts draft contracts, repros and interfaces ahead of each ticket; Codex
   publishes the next contract and records file/state independence promptly; Cowork builds the next head's adversarial
   harness before it lands.
4. **Auto-ping.** Each agent proactively tells the other two on GitHub what it thinks should happen next or can safely run
   in parallel. Write agent names without `@` so the GitHub Codex bot is not triggered accidentally.
5. End every marker comment with a `LANES:` line naming what Build, Evidence and Integration are doing. An idle lane must
   name its next task so a stall is immediately visible.
6. Keep a **ready queue of the next three bounded tickets** so completion never waits for scope writing.
7. While Claude fixes review findings, Cowork validates unaffected contracts and Codex prepares the next direct dependency.
8. Use a **fast path** for docs-only, test-only, labels and isolated pure functions: focused checks plus normal review; do
   not request the full matrix unless branch protection or risk class requires it.
9. Run fast/focused tests on each revision; run full CI/replay/UI once on the accepted exact head.
10. Queue long tests and continue non-conflicting work. Do not poll or sleep when another useful task is ready.
11. Data collection, scouting, research-harness preparation and UI design may run beside core work when they consume a
   frozen interface/dataset and cannot change the active contract.
12. Two implementation branches may proceed only when Codex records that their files, state, migrations and acceptance
   evidence are independent. Otherwise keep one product-code integration branch.
13. A proven P0/P1 uses the **expedite lane**: pause conflicting merges, publish a minimal contract/repro, fix and validate;
   unrelated evidence/scouting work continues in parallel.
14. Time-box speculative work. If a scout or optimization cannot state a falsifiable experiment and stop condition, park it.
15. Track lead time, review rounds, repeated CI minutes and escaped regressions. Optimize the workflow when coordination
    overhead grows, never by deleting a safety gate.

These acceleration rules do not weaken the safety gates: no self-acceptance, no parallel edits to the same files or
shared state, one integration owner per branch, one full suite on the accepted exact head, and explicit protection for
mainnet, funds and credentials.

### Continuous code-health budget

Code quality is part of each ticket's acceptance, not a separate months-long refactor:

- touched files receive dead-code, duplicate-path, naming/type and stale-comment cleanup in the same ticket;
- every new TODO/deprecation/shim needs an issue, owner component and deletion condition;
- dependency direction, cyclomatic hot spots, module size, cycle latency, allocation/memory and request counts are tracked;
- after every three implementation tickets, Codex runs a short debt scan and may reserve the next small cleanup slot;
- cleanup that changes behavior needs its own contract test; mechanical cleanup uses the fast path;
- debt stops the line only when it threatens ownership, protection, causal truth, performance budgets or the next core
  boundary. Everything else is removed opportunistically without stalling delivery.

## 4. Ticket states

- **BACKLOG:** ordered but waiting on dependencies.
- **READY:** acceptance contract and base SHA published.
- **BUILDING:** Claude owns implementation.
- **REVIEW:** exact head is with Codex and Cowork.
- **GATED:** review is clean; final evidence is running.
- **MERGED:** protected master contains the accepted change.
- **BLOCKED:** a named external dependency prevents progress.
- **DROPPED/SUPERSEDED:** deliberately not ported, with the reason recorded.

## 5. Ordered execution spine

### Wave A — reusable safety contracts

Purpose: extract the invariants NEWCORE must honor without polishing the old application.

| Order | Ticket | State at plan creation | Exit evidence |
|---:|---|---|---|
| A0 | AUD-00 single instance/account ownership | MERGED | One process per data folder/account. |
| A1 | AUD-01 non-repeating post-fill bookkeeping | MERGED | Logging/telemetry failure cannot repeat an exchange action. |
| A2 | AUD-02 isolated management stages/close ownership | MERGED | A rejected stage cannot starve protection; pending/resting quantities belong to the correct lot. |
| A3 | AUD-03 exchange-truth fills/ambiguous entries | MERGED | Executed quantities/prices, bounded CID resolution and provisional protection. |
| A4 | AUD-04 routine stop verification | MERGED | Fresh exchange confirmation, closing-side/quantity validation and duplicate-safe repair. |
| **A5** | **AUD-05 durable state/restart truth** | **MERGED** | Write-ahead ownership, atomic versioned state, fail-closed missing/corrupt state, no plaintext secrets, restart/fault/concurrency tests. |
| A6 | AUD-06 truth policy | MERGED / legacy frozen | Truth labels landed; NEWCORE ships every unverified strategy disabled until new evidence exists. No compatibility cycle is spent on the legacy UI. |

**Wave A exit:** crash recovery and exchange ownership are explicit, durable and independently reproducible. New code
does not depend on implicit fields in the legacy `Engine` object.

### Checkpoint A — bugs and scouting

- Proven P0/P1 exchange, ownership, stop, close, secret or data-loss defects interrupt immediately.
- P2 items attach to the earliest owning NEWCORE component.
- P3 cleanup waits for the component deletion pass.
- Scouting advances only if it prevents foundational rework or supplies a missing contract.

### Wave B — truth and test seams before extraction

| Order | Ticket | Audit mapping | Exit evidence |
|---:|---|---|---|
| B1 | AUD-07 execution/backtest truth | C11–C13 | One causal policy for time exits, maker fills, add slippage, closed-candle sampling and Cairo day boundaries. |
| B2 | AUD-08 characterization/parity gate | C15 | Golden cases cover long/short, stop, target, partial, runner, DCA, pyramid, maker, gap, min-size, outage, restart and ambiguity. |
| B3 | AUD-12a dependency/test injection | C21, TEST-01/04/05 | Explicit clock/client/store interfaces; fakes implement the production protocol; order-independent tests. |
| B4 | PORT-01 AUD port-damage assessment | AUD-00–05 | In parallel with B1–B3, inventory new coupling, duplicate state, test-only branches, compatibility baggage and performance cost. P0/P1 interrupts; other findings map to the owning NEWCORE ticket. |
| B5 | NEWCORE architecture decision record | C14, C22, ARCH-06/07 | State/action model, module boundaries, account identity, reason codes and legacy deletion map approved. |
| B6 | REC-01 recovery regression contract | Fable Audit2 NEW-ENG-01/NEW-DATA-01 | NC-01 supplies UNKNOWN/HOLD, binding and result-evidence types; NC-02 consumes the frozen fixtures. A missing/corrupt primary and backup can never become an empty managed account; a future schema aborts without modifying either file; current-schema damage preserves forensic evidence and requires explicit reconciliation. No legacy repair is required. |
| B7 | OPS-UI-01 account/incident UX contract | Fable Audit2 NEW-APP-01/02 | Specify NC-04/NC-09 and the replacement UI: mode/account binding, state generation, last reconciliation and actionable incidents. Typed confirmation leaves entries paused. Do not retrofit the old panel. |
| B8 | ORD-REC-01 order/recovery regression contract | Fable Audit2 NEW-ENG-02/03/04/11 | NC-01 defines unified intent/result ownership and ambiguous not-found; NC-03/NC-05/NC-07 consume the lock, pending, false-not-found and resting-maker repros. Require no network retries under state locks, durable result phases, position-corroborated resolution and pause/halt/flatten drain/late-fill handling. Do not repair the legacy execution path. |

**Wave B exit:** stable contract tests exist for the replacement. Every behavior difference is an accepted correction,
not accidental replay drift.

### Wave C — NEWCORE foundation

These are new modules, not a slow rearrangement of `engine.py`.

| Order | Ticket | Deliverable | Required proof |
|---:|---|---|---|
| C1 | NC-01 domain model/reason codes | Contract: `docs/newcore/NC01_CONTRACT.md`. Typed Account, Portfolio, Position, Lot, unified OrderIntent, OrderResult, Protection and Decision records with UNKNOWN/HOLD ownership and stable reasons. | Schema/property/import-boundary/mutation tests; invalid states rejected; no legacy dependencies. |
| C2 | NC-02 state/event store | Account-keyed snapshots plus append-only intent/result events; atomic migration/recovery. | Crash matrix at every write boundary; deterministic replay; no secrets. |
| C3 | NC-03 exchange adapter/order state machine | Idempotent submit/query/cancel; classic/algo normalization; known/unknown/final outcomes. | Lost answer, late/partial fill, duplicate CID, restart and malformed-response tests. |
| C4 | NC-04 AccountContext/order budget | Isolated account workers; priority queue and shared weight/418/429 policy. | Two simulated accounts; one outage/ban never delays the other's protection. |
| C5 | NC-05 scheduler/lock-free read model | Monotonic scheduler owns cycle/manage/guards; network outside state locks; immutable UI snapshots. | Cycle/snapshot budgets, stale labeling, close/flatten responsiveness, deadlock tests. |
| C6 | NC-06 single risk gateway | Controlled capital, allocation, leverage, exposure, correlation, follower feasibility and mechanical ceilings. | Every entry source uses it; exchange-step/minimum properties; no automatic risk increase. |
| C7 | NC-07 management transition core | Pure stop, BE, target, partial, runner, trail, time exit and bounded DCA/pyramid actions. | Same inputs produce same live/sim actions; gap/path stress. |
| C8 | NC-08 simulator/replay | Live and backtest adapters translate the same action objects; versioned datasets. | Strict live/replay/backtest parity and causal fixtures. |
| C9 | NC-09 observability contract | Structured incidents, fill/slippage, MFE/MAE, peak give-back and why-held/closed events. | Audit failure cannot block protection; every decision has a stable reason. |
| C10 | REC-02 integrated exchange-truth reconciliation | One account-level fold compares durable intent/result state with Binance positions, open orders, fills and protection after startup, timeout, manual exchange changes and every uncertain answer. It adopts or quarantines external state; it never guesses an empty account. | Automated matrix for local-only and venue-only orders/positions, partial and late fills, missing/duplicate orders, manual close/add, stop filled while offline, stale answers, unknown status and restart. Every result ends flat, protected, or in an explicit fail-closed HOLD with actionable evidence. |
| C11 | TNET-01 automated testnet scenario harness | A bounded, repeatable harness drives the complete NEWCORE vertical slice against Binance Futures testnet and emits one redacted exact-build report. It owns setup, assertions and cleanup; testnet funds/P&L are disposable. | Complete long and short cycles; confirmed entry, stop, target, partial close, one bounded DCA, cancel/replace race, lost answer + reconciliation, refusal/minimum failure, and restart with an open position. Final exchange truth must be flat or explicitly protected and reconciled. |

### Checkpoint C — BASE-CLEAN and deletion

1. Run the full legacy-versus-NEWCORE contract pack.
2. Mark every old path **delete**, **research-only**, **temporary adapter** or **still required**.
3. Remove duplicate formulas, dead compatibility guards and obsolete tests.
4. Run static/dependency/secret scans, property tests and mutation checks on state/order/sizing.
5. Measure cycle latency, UI snapshot latency, memory and request budgets.
6. Do not port Grid/COMBO, the old AI filter or unverified DCA unless later research earns promotion.

**Wave C exit:** NEWCORE runs the complete vertical slice without invoking the legacy engine; REC-02 proves exchange
truth after ambiguity/restart; TNET-01 proves at least one complete long and one complete short testnet lifecycle; and
two isolated simulated accounts survive independently. Wave D runtime promotion is blocked until this gate passes.

### Wave D — engine strategies, regimes and risk controls

This is the primary product wave. Optional application features remain behind it.

#### Strategy research starts in parallel before Wave D implementation

`STRAT-00` is a research-only lane and may start while Waves A-C build the trustworthy engine. It cannot promote a
strategy, enable one in PAPER/testnet, or change runtime behavior until the Wave C REC-02/TNET-01 gate passes. Its
purpose is to make the later implementation faster and harder to fool:

| Owner | Starts now | Output |
|---|---|---|
| **Codex** | Define the strategy taxonomy, economic hypotheses, risk contracts, comparison metrics, rejection rules and promotion gates. Review trading merit and integrate the final experiment order. | Versioned strategy/evidence matrix and acceptance contract. |
| **Cowork** | Independently inventory current evidence; research candidate long/short, trend/range, breakout/breakdown and short-horizon families; attack leakage, selection bias, costs, fills, funding and regime assumptions; review proposed backtests. | Source/evidence ledger, contradiction report, falsifiable experiments and adversarial backtest checklist. Unknown/timed-out work is explicitly unverified. |
| **Claude Code** | Keep the current safety/core implementation line moving. Once DATA-01/RES-01 contracts are accepted, implement the data adapters, shared research harness and one bounded candidate at a time. | Reproducible code and exact-SHA result artifacts; never self-accepts trading merit. |

Research runs are split into small deterministic shards when Cowork's time limit is lower than the full study. Claude
Code/Codex run long Windows jobs and native UI/testnet checks. All agents compare the same dataset manifest, configuration,
seed, cost model and output schema; results from different environments are complementary, not silently pooled.

Every candidate begins with a written mechanism and a stop condition. It is rejected or parked if it cannot survive
after-cost baselines, causal truncation, anchored walk-forward testing, untouched holdout, parameter-neighbour checks,
symbol/year concentration checks and gap/funding/slippage stress. More strategies are not automatically better.

| Order | Ticket | Deliverable | Promotion gate |
|---:|---|---|---|
| D1 | DATA-01 market-data integrity | Point-in-time candles, funding, mark, OI, taker and long/short data with freshness/provenance. | Frozen manifest, gap report, survivorship policy. |
| D1a | DATA-01a execution-grade datasets | 1m/5m plus trade/book/spread/latency inputs where required; point-in-time listing/rules, funding/index/mark and venue-session histories. | Venue-specific manifest, exact query/window, source version, Cairo access time, lawful content hash/archive reference and revalidation date. |
| D2 | RES-01 research harness | Walk-forward/holdout, Monte Carlo, sensitivity, regimes, costs, follower feasibility, point-in-time candidate board and same-window benchmarks. | Reproducible clean-checkout report; separate opportunity/entry/hold/regime/evidence/operational dimensions and explicit stand-down state. |
| D3 | STR-01 trend long + short | Independently calibrated long and short trend strategies. | Adequate samples, untouched holdout; no forced symmetry. |
| D4 | STR-02 range/mean reversion | Sideways-market entries/exits with volatility/spread guards. | Regime advantage after costs and gaps. |
| D5 | STR-03 Quick Bank scalp | Early TP, protected runner and optional one small bounded DCA layer. | Green-after-cost measured honestly; tail/gap and follower-minimum stress; no “always positive” promise. |
| D6 | STR-04 DCA basket | Legacy DCA-1h curves are historical/unverified and excluded from the candidate catalog. Any replacement starts as a new bounded candidate with hard basket stop, bounded depth/scale and total-risk accounting. | Rebuilt causal results on execution-grade data; otherwise remains disabled/dropped. A single micro-DCA scalp experiment is separate and optional. |
| D7 | REG-01 per-asset regime engine | Asset/timeframe trend/range/volatility and strategy preference; Manual/Recommend/Automatic. | Point-in-time stability, hysteresis and shadow evidence. |
| D8 | MACRO-01 context | DXY, US 2Y/10Y/curve, S&P/Nasdaq, oil, gold/minerals and timestamped geopolitics/news. | Observe first; veto/reduce only after evidence. |
| D9 | GOV-01 bounded risk grades | Conservative through high-risk/“Maniac,” mapped to explicit admission thresholds, target opportunity frequency, trade risk, leverage, exposure, concurrency, cooldown, DCA permission and portfolio drawdown ceilings. | Profiles are separately backtested and expose expected/actual trade frequency plus rejection reasons. High-risk modes admit more valid setups without bypassing integrity, protection, reconciliation or account hard-loss boundaries. |
| D10 | GOV-02 allocator/strategy matrix | Recommend, disable or reduce strategies by asset/regime; never exceed user ceilings. | Manual, Recommend and Automatic produce auditable decisions and never force a trade. |
| D11 | CTRL-01 engine controls | Controlled capital plus Direction Bias, Risk and Acceptable Drawdown controls, globally and optionally per asset/strategy. | Deterministic mappings, previewed consequences and no silent changes to existing positions. |
| D12 | EDGE-00 evidence checkpoint | Compare promoted candidates against simple after-cost baselines and each other before adding more strategy families. Keep backtest, paper, testnet and forward ledgers separate, with reason-coded exclusions and optional proof-of-prior commitments. | Freeze winners/losers by regime, concentration, tail risk and operational feasibility; park candidates with no distinct edge. |

Each strategy has separate performance, tail-risk, evidence-readiness and operational/copy-readiness scores. No blended
score may hide a failed hard gate.

Here, a hard gate means an integrity, venue-capability, protection, ownership/reconciliation or account-loss invariant;
it does not mean every ranking dimension must be high. Market-quality thresholds and activity settings vary by the
selected risk profile. Starvation diagnostics must prove when stacked filters are suppressing nearly all entries.

#### Required strategy-coverage matrix

The goal is useful coverage, not an unbounded pile of bots. Research must either fill or explicitly reject each relevant
cell:

| Market condition | Long | Short | Neutral/no-trade |
|---|---|---|---|
| Persistent trend | Momentum, breakout, pullback | Momentum, breakdown, rally-fade pullback | Exhaustion/late-entry veto |
| Range | Lower-band/mean reversion | Upper-band/mean reversion | Volatility/spread veto |
| Volatility expansion | Breakout with gap control | Breakdown with gap control | Whipsaw/circuit-breaker state |
| Short horizon/scalp | Quick Bank TP + protected runner | Mirrored short scalp with independent calibration | Fees/spread/latency veto |
| Adverse move management | Optional single bounded micro-DCA where proven | Same, independently proven | Hard stop and account-level loss halt |

The Direction Bias control ranges from strongly bearish through neutral to strongly bullish. It changes permitted
directional allocation/risk caps; it never invents a signal. The Risk control maps to tested mechanical ceilings rather
than directly multiplying leverage. Acceptable Drawdown sets portfolio throttling/halt behavior, not a loss target.

### Wave E — gold, TradFi and mainnet-candidate engine gate

Before E6 can pass, the minimum OPS-01 mechanisms must already exist: supervised service, encrypted secrets,
watchdog/dead-man, kill switch, graceful stop, backup/restore and incident playbook. NC-02/NC-08 must also reconcile
exchange fees, funding and realized PnL and prove a deterministic ledger rebuild. Full prolonged operational validation
remains in Wave F; tax and profit-share reporting do not block the engine candidate.

| Order | Ticket | Deliverable |
|---:|---|---|
| E1 | VENUE-01 asset/venue contracts | Sessions, calendars, tick/lot/notional rules, fees, funding/borrow, gaps, order types and market-data freshness are adapter inputs—not strategy assumptions. |
| E2 | GOLD-01 exchange-traded gold | Validate Binance XAUUSDT TradFi perpetual first where account/region availability permits, including funding, index, sessions and liquidity; test PAXG/XAUT only as explicit fallback venues. Gold candidates remain separate from crypto. |
| E3 | GOLD-02 broker gold | Paper XAUUSD with sessions, rollover, spreads, weekend gaps and broker execution semantics. |
| E4 | TRADFI-01 indices/stocks | Paper S&P/Nasdaq instruments then liquid stocks; corporate actions, sessions, borrow/short availability and gap risk explicit. |
| E5 | RISK-01 cross-asset portfolio | Correlation, concentration, currency, session/gap, liquidity and total wallet exposure across crypto/gold/TradFi. |
| E6 | ENG-GATE-01 numeric mainnet-candidate engine | Frozen strategy catalog, risk-control mappings and venue limitations; versioned numeric pass/fail limits for ownership/protection ambiguity, replay/parity, restart/fault recovery, data freshness/gaps, management latency, request-budget headroom, and strategy holdout/tail/sample evidence. Thresholds are baselined from ZackBot evidence, not copied from generic audit targets. Passing means “engineering candidate,” not permission to use real funds. |

**Wave E exit:** the core engine can run both directions, choose or reject strategies by regime, respect owner risk/bias/
drawdown controls and exercise crypto, gold and TradFi adapters in paper/test environments. Unsupported cells are visible,
not silently filled with weak strategies.

### Wave F — prolonged validation and live boundary

| Order | Ticket | Deliverable / gate |
|---:|---|---|
| F1 | OPS-01 completion/drills | The minimum runtime mechanisms are already required before E6; here they undergo prolonged unattended, restore, rollback, kill-switch and incident-response drills. |
| F2 | VAL-01 prolonged paper/testnet program | Regime coverage, long/short opportunities, fill/slippage calibration, recovery drills and resource/request budgets across crypto/gold/TradFi. |
| F3 | VAL-02 shadow/mainnet rehearsal | Mainnet market data and shadow decisions with no orders; compare predicted venue behavior and operational health. |
| F4 | REL-01 own-account canary | Only after explicit owner approval: smallest bounded capital, IP-restricted keys, no withdrawals and staged scale. |

### Wave G — secondary product expansion

| Order | Ticket | Deliverable / gate |
|---:|---|---|
| G1 | UX-01 Simple / Guided / Pro polish | Simple surfaces capital, bias, risk and drawdown; Guided explains; Pro shows engine detail. Core controls already exist from D11. |
| G2 | UX-02 trade audit views | MFE, give-back, why held, causal earlier-exit result, close target/stop and protection freshness. |
| G3 | ACCT-01 multi-account | Separate credentials, state, limits, health and emergency control per account/exchange. |
| G4 | COPY-01 follower feasibility/copy intent | Predict $100/$200/$500 plan feasibility; copy risk intent, not quantity; protect/reconcile each follower independently. |
| G5 | PWA-01/02 mobile | Secure monitor first; dangerous controls require re-auth, MFA, idempotency, expiry and audit. |
| G6 | TV-01 TradingView | Strict versioned signal/result schemas; HMAC + timestamp/nonce; durable `signal_id` dedupe; strategy/version and trade-family attribution; explicit units; dry-run; stable reason codes; all intents through the same risk gateway. Runtime intake begins only after the Binance vertical slice. |
| G7 | NEWS-01 news authority | Timestamped source/expiry; observe then veto/reduce only. |
| G8 | REL-02 shareable/lead product | Signed release, private lead/no-followers rehearsal, then copy-suitability/incident gates before public launch. |

## 6. Bug interruption lane

| Class | Meaning | Scheduling rule |
|---|---|---|
| **P0** | Active data/fund loss, repeated orders, secret exposure or protection destruction | Stop the current merge; assign immediately. |
| **P1-foundation** | Ownership, state, stop/close, risk gateway, isolation or causal-truth defect | Fix before the owning wave exits. |
| **P1-release** | Mainnet/release blocker without PAPER exposure | Record now; schedule in Wave G unless architecture needs it earlier. |
| **P2** | Bounded correctness/resilience issue | Attach to the next ticket touching the owner component. |
| **P3** | Cleanup, polish or speculative improvement | Cleanup/scouting backlog; never interrupts safety work. |

Every bug record includes exact SHA, reproduction, affected contract, severity rationale, owner component, regression,
fix SHA and verification.

## 7. Scouting lane

Cowork may continuously scout exchanges, open-source bots, research and competitors. A proposal includes the user
problem/source, causal evidence, expected after-cost benefit, failure/tail risk, dependencies, earliest wave, authority
ladder and a small experiment that can disprove it.

Codex scores foundation impact, value, evidence, risk, dependency cost and reversibility. A proposal moves earlier only
when it closes a safety/architecture gap or prevents expensive rework. Popularity is not evidence.

Reserved bug/scouting checkpoints occur after Waves A, C, D and F so discoveries have space without making the main
sequence permanently fluid.

## 8. Evidence matrix

| Change | Minimum evidence before merge |
|---|---|
| Docs/process | Link/consistency check; no stale ticket or SHA claims. |
| Core/refactor | Characterization, property tests and strict replay; zero unexplained drift. |
| Persistence/order/risk | Fault injection, ambiguous/partial/late cases, restart matrix, focused Windows tests and Cowork adversarial validation. |
| Strategy/signal | Point-in-time data, walk-forward, untouched holdout, sensitivity, costs, shadow mode and sample limits. |
| UI/PWA | API contract, desktop/mobile/accessibility Playwright, auth/CSRF/Host tests, stale/offline behavior. |
| Operations/release | Provenance, secret/history scan, restore/rollback, watchdog/kill-switch and explicit mainnet approval. |

Fast CI runs during review. Full CI/replay/UI runs once after exact-head acceptance.

## 9. Owner dashboard

`docs/PROJECT_STATUS.md` is the concise owner view and is updated after every merge or roadmap change:

- **Current:** active ticket, owner and exact SHA;
- **Changed:** plain-language accepted changes;
- **Bugs:** found/fixed/open by severity;
- **Evidence:** focused, Cowork, Windows and CI results;
- **Roadmap:** next three tickets/dependencies;
- **Scouting:** promoted, parked and rejected ideas;
- **Lanes:** the current Build, Evidence and Integration task, including the next task for any temporarily idle lane;
- **Your action:** normally none; only material product/mainnet decisions.

This document owns near-term order. `ROADMAP.md` remains the long-term product vision. If they disagree, this plan
controls until Codex merges an explicit amendment.

## 10. Immediate queue

1. Close the active AUD-07 product/C12 handoff as reusable evidence: merge only contract/test material that directly
   protects NEWCORE and does not grow the legacy product; otherwise supersede it without further legacy repair rounds.
2. Convert Fable Audit2 B6–B8 into frozen failing fixtures and replacement acceptance contracts. Implement the fixes in
   NC-02/03/04/05/07/09 and the replacement UI, not in `engine.py` or the old panel.
3. Complete AUD-08 characterization/parity and dependency injection, expanding the golden matrix before NC-01.
4. Approve B5 architecture plus numeric ENG-GATE-01 and the source-provenance contract.
5. Build NC-01 through NC-09, then complete REC-02 integrated reconciliation and the TNET-01 automated long/short
   testnet harness. Legacy `engine.py` remains frozen except for the narrow boundary exceptions in rule 10.
6. Run DATA-01/DATA-01a and STRAT-00 research in parallel, but do not promote or enable a strategy until REC-02 and
   TNET-01 pass; the legacy DCA-1h evidence is retired, not tuned into
   acceptance. Run EDGE-00 before expanding the candidate count.
7. Validate Binance XAUUSDT venue suitability first, with regional/account availability explicit; keep PAXG/XAUT and
   broker XAUUSD as separate fallback/venue studies.
8. Require the minimum OPS-01 mechanisms and numeric ENG-GATE-01 before declaring the engine a mainnet candidate.

## 11. Decisions already made

- Replacement-first: the installed bot and testnet positions do not constrain design.
- Engine-first: two-way strategies, risk controls, gold/TradFi support and mainnet-grade evidence precede optional
  product features.
- GitHub remains public during testnet at accepted owner risk; make it private and secret-scan before mainnet/release.
- Claude implements; Cowork validates/runs research; Codex reviews, sequences and merges.
- Routine PAPER/testnet tests, restarts, installs, canaries and merges do not wait for owner approval.
- Mainnet, real funds, credentials, visibility changes and destructive/irreversible actions remain protected.
