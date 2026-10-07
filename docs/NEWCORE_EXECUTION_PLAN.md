# ZackBot NEWCORE execution plan

**Control plan v1 — 08 Oct 2026, Africa/Cairo**

This is the shared execution document for the owner, Claude Code, Cowork and Codex. It converts the historical audit,
the product roadmap and the replacement-first decision into one ordered build plan.

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

## 3. Team operating model

| Role | Primary responsibility | Must not do |
|---|---|---|
| **Claude Code — implementation owner** | Product code, migrations, focused regression tests and touched-file cleanup; publishes exact-SHA handoffs. | Self-accept, silently broaden a ticket, or preserve legacy code without a named contract. |
| **Cowork — validation/evidence owner** | Independent adversarial/runtime testing; Windows, UI, testnet and fault evidence; backtest/research runs; scouting briefs. | Change the implementation under review or claim an environment it did not test. |
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

GitHub commits and PR comments are the message bus. Five-minute polling is only a connection fallback.

### Progress acceleration rules

The order below is a dependency map, not a rule that every item must wait for an entire wave to finish. A ticket may
start as soon as its **direct** dependencies and file ownership are clear.

Three lanes should remain active whenever useful work exists:

| Lane | Owner | Work that can run concurrently |
|---|---|---|
| **Build** | Claude Code | The active product ticket. Claude may use independent Code subagents/worktrees for non-overlapping modules/tests, with one integration owner and no parallel edits to shared state. |
| **Evidence** | Cowork | Black-box tests for the active head; prepare the next ticket's adversarial harness; Windows/testnet runs; frozen-data backtests; scouting. Never edit the implementation being reviewed. |
| **Integration** | Codex | Review the current exact head; reproduce risks; draft the next acceptance contract/ADR; maintain status, dependencies and merge gates. Never build a competing product patch. |

Acceleration controls:

1. Keep a **ready queue of the next three bounded tickets** so completion never waits for scope writing.
2. While Claude fixes review findings, Cowork validates unaffected contracts and Codex prepares the next direct dependency.
3. Use a **fast path** for docs-only, test-only, labels and isolated pure functions: focused checks plus normal review; do
   not request the full matrix unless branch protection or risk class requires it.
4. Run fast/focused tests on each revision; run full CI/replay/UI once on the accepted exact head.
5. Queue long tests and continue non-conflicting work. Do not poll or sleep when another useful task is ready.
6. Data collection, scouting, research-harness preparation and UI design may run beside core work when they consume a
   frozen interface/dataset and cannot change the active contract.
7. Two implementation branches may proceed only when Codex records that their files, state, migrations and acceptance
   evidence are independent. Otherwise keep one product-code integration branch.
8. A proven P0/P1 uses the **expedite lane**: pause conflicting merges, publish a minimal contract/repro, fix and validate;
   unrelated evidence/scouting work continues in parallel.
9. Time-box speculative work. If a scout or optimization cannot state a falsifiable experiment and stop condition, park it.
10. Track lead time, review rounds, repeated CI minutes and escaped regressions. Optimize the workflow when coordination
    overhead grows, never by deleting a safety gate.

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
| **A5** | **AUD-05 durable state/restart truth** | **BUILDING — PR #30** | Write-ahead ownership, atomic versioned state, fail-closed missing/corrupt state, no plaintext secrets, restart/fault/concurrency tests. |
| A6 | AUD-06 truth policy | BACKLOG | Merge only narrow useful truth labels. NEWCORE ships every unverified strategy disabled until new evidence exists. Do not spend a broad compatibility cycle on the legacy UI. |

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
| B4 | NEWCORE architecture decision record | C14, C22, ARCH-06/07 | State/action model, module boundaries, account identity, reason codes and legacy deletion map approved. |

**Wave B exit:** stable contract tests exist for the replacement. Every behavior difference is an accepted correction,
not accidental replay drift.

### Wave C — NEWCORE foundation

These are new modules, not a slow rearrangement of `engine.py`.

| Order | Ticket | Deliverable | Required proof |
|---:|---|---|---|
| C1 | NC-01 domain model/reason codes | Typed Account, Portfolio, Position, Lot, OrderIntent, OrderResult, Protection and Decision records. | Schema/property tests; invalid states rejected. |
| C2 | NC-02 state/event store | Account-keyed snapshots plus append-only intent/result events; atomic migration/recovery. | Crash matrix at every write boundary; deterministic replay; no secrets. |
| C3 | NC-03 exchange adapter/order state machine | Idempotent submit/query/cancel; classic/algo normalization; known/unknown/final outcomes. | Lost answer, late/partial fill, duplicate CID, restart and malformed-response tests. |
| C4 | NC-04 AccountContext/order budget | Isolated account workers; priority queue and shared weight/418/429 policy. | Two simulated accounts; one outage/ban never delays the other's protection. |
| C5 | NC-05 scheduler/lock-free read model | Monotonic scheduler owns cycle/manage/guards; network outside state locks; immutable UI snapshots. | Cycle/snapshot budgets, stale labeling, close/flatten responsiveness, deadlock tests. |
| C6 | NC-06 single risk gateway | Controlled capital, allocation, leverage, exposure, correlation, follower feasibility and mechanical ceilings. | Every entry source uses it; exchange-step/minimum properties; no automatic risk increase. |
| C7 | NC-07 management transition core | Pure stop, BE, target, partial, runner, trail, time exit and bounded DCA/pyramid actions. | Same inputs produce same live/sim actions; gap/path stress. |
| C8 | NC-08 simulator/replay | Live and backtest adapters translate the same action objects; versioned datasets. | Strict live/replay/backtest parity and causal fixtures. |
| C9 | NC-09 observability contract | Structured incidents, fill/slippage, MFE/MAE, peak give-back and why-held/closed events. | Audit failure cannot block protection; every decision has a stable reason. |

### Checkpoint C — BASE-CLEAN and deletion

1. Run the full legacy-versus-NEWCORE contract pack.
2. Mark every old path **delete**, **research-only**, **temporary adapter** or **still required**.
3. Remove duplicate formulas, dead compatibility guards and obsolete tests.
4. Run static/dependency/secret scans, property tests and mutation checks on state/order/sizing.
5. Measure cycle latency, UI snapshot latency, memory and request budgets.
6. Do not port Grid/COMBO, the old AI filter or unverified DCA unless later research earns promotion.

**Wave C exit:** NEWCORE runs a deterministic strategy on paper/testnet, recovers from crashes and manages two isolated
simulated accounts without invoking the legacy engine.

### Wave D — evidence platform and strategies

| Order | Ticket | Deliverable | Promotion gate |
|---:|---|---|---|
| D1 | DATA-01 market-data integrity | Point-in-time candles, funding, mark, OI, taker and long/short data with freshness/provenance. | Frozen manifest, gap report, survivorship policy. |
| D2 | RES-01 research harness | Walk-forward/holdout, Monte Carlo, sensitivity, regimes, costs and follower feasibility. | Reproducible clean-checkout report. |
| D3 | STR-01 trend long + short | Independently calibrated long and short trend strategies. | Adequate samples, untouched holdout; no forced symmetry. |
| D4 | STR-02 range/mean reversion | Sideways-market entries/exits with volatility/spread guards. | Regime advantage after costs and gaps. |
| D5 | STR-03 Quick Bank scalp | Early TP, protected runner and optional one small bounded DCA layer. | Green-after-cost measured honestly; tail/gap and follower-minimum stress; no “always positive” promise. |
| D6 | STR-04 DCA basket | Hard basket stop, bounded depth/scale and total-risk accounting. | Rebuilt causal results; otherwise remains disabled/dropped. |
| D7 | REG-01 per-asset regime engine | Asset/timeframe trend/range/volatility and strategy preference; Manual/Recommend/Automatic. | Point-in-time stability, hysteresis and shadow evidence. |
| D8 | MACRO-01 context | DXY, US 2Y/10Y/curve, S&P/Nasdaq, oil, gold/minerals and timestamped geopolitics/news. | Observe first; veto/reduce only after evidence. |

Each strategy has separate performance, tail-risk, evidence-readiness and operational/copy-readiness scores. No blended
score may hide a failed hard gate.

### Wave E — risk automation and owner product

| Order | Ticket | Deliverable |
|---:|---|---|
| E1 | GOV-01 bounded risk grades | Conservative through high-risk/“Maniac,” mapped to explicit trade risk, leverage, exposure, drawdown and allowed components. |
| E2 | GOV-02 allocator | Scores may recommend, disable or reduce; they cannot exceed user ceilings. Manual/Recommend/Automatic remain available. |
| E3 | COPY-01 capital/follower feasibility | Owner enters controlled capital and follower minimum; UI predicts whether complete plans clear venue/follower minimums at $100/$200/$500 etc. |
| E4 | UX-01 Simple / Guided / Pro | Simple: capital, Risk Level, Loss Tolerance. Guided: reasons/recommendations. Pro: full detail. Same engine/state. |
| E5 | UX-02 trade audit | MFE, give-back, why held, causal earlier-exit result, close target/stop and protection freshness. |

### Wave F — operations, accounts and remote control

| Order | Ticket | Deliverable / gate |
|---:|---|---|
| F1 | OPS-01 VPS/service | Supervised Linux service, static IP, encrypted secrets, dead-man alerts, backup/restore and rollback. |
| F2 | ACCT-01 multi-account | Separate credentials, state, limits, health and emergency control per account/exchange. |
| F3 | COPY-02 intent copying | Copy risk intent, not quantity; each follower sizes/protects/reconciles independently. Off/Shadow/Testnet/Live. |
| F4 | PWA-01 mobile monitor | Secure HTTPS read model, stale/offline state, notifications and installable PWA. |
| F5 | PWA-02 remote controls | Pause/ack by default; flatten/risk/keys need re-auth, MFA, idempotency, expiry and audit. |
| F6 | TV-01 TradingView | Signed/versioned webhook intents through the shared risk gateway. |
| F7 | NEWS-01 news provider | Source, time, expiry and affected assets; observe then veto/reduce only. |

### Wave G — venues and release

| Order | Ticket | Deliverable / gate |
|---:|---|---|
| G1 | GOLD-01 tokenized gold | PAXG, then XAUT, using crypto adapters and separate liquidity evidence. |
| G2 | TRADFI-01 broker abstraction | XAUUSD, indices and stocks in paper mode with sessions, gaps, corporate actions and borrow rules. |
| G3 | REL-01 hardening | AUD-13/14/15: provenance, signed artifacts, workflow trust, accounting truth, remote auth, kill switch, incident playbook. |
| G4 | REL-02 own-account canary | Mainnet only after explicit owner approval; stepwise capital, no withdrawals, IP-restricted keys. |
| G5 | REL-03 lead portfolios | Private/no-followers first; copy suitability and incident gates before public launch. |

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
- **Your action:** normally none; only material product/mainnet decisions.

This document owns near-term order. `ROADMAP.md` remains the long-term product vision. If they disagree, this plan
controls until Codex merges an explicit amendment.

## 10. Immediate queue

1. Complete AUD-05 PR #30. Require write-ahead ownership, runtime persistence latch, secret migration, initialized-
   account marker and versioned semantic schemas.
2. Resolve AUD-06a narrowly: merge truthful labels if useful or supersede with NEWCORE default-off policy.
3. Open AUD-07 execution/backtest truth.
4. Open AUD-08 characterization/parity.
5. Add test injection and approve the NEWCORE architecture decision record.
6. Begin NC-01 only after Wave B exits. No strategy expansion before the NEWCORE contract spine exists.

## 11. Decisions already made

- Replacement-first: the installed bot and testnet positions do not constrain design.
- GitHub remains public during testnet at accepted owner risk; make it private and secret-scan before mainnet/release.
- Claude implements; Cowork validates/runs research; Codex reviews, sequences and merges.
- Routine PAPER/testnet tests, restarts, installs, canaries and merges do not wait for owner approval.
- Mainnet, real funds, credentials, visibility changes and destructive/irreversible actions remain protected.
