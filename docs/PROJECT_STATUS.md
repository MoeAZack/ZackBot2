# ZackBot owner overview

*Last refreshed: 2026-10-05 21:41 Cairo (Africa/Cairo). This is the plain-language owner view. `ROADMAP.md` and `docs/reviews/` retain the detailed scope and evidence.*

## Where we are now

| Item | Current state |
|---|---|
| Environment | **Binance Futures testnet only.** Mainnet activation is prohibited until the roadmap and final release audit are complete. |
| Released test build | ZackBot 3.2, build `20261005-204405` |
| Runtime health | Engine and exchange healthy; four tracked positions protected; zero unprotected positions and zero orphans at the final T03 check |
| Git | Private GitHub repository; `master` at accepted T03; clean local/remote state |
| Current ticket | **T04 — continuous integration and repeatable fast/full/release verification** |
| Owner action | None currently |

## What just changed

### T03 — installer rollback safety: accepted

- Added a deliberate failed-launch drill.
- Proved the previous executable is restored byte-for-byte and restarted successfully.
- Added authenticated build checks, checksum preflight, safer backup/rollback ordering, and Windows integration tests.
- Proved the normal installation path, source mirror, and shortcuts.
- Windows result: **207/207 tests passed**.
- Current testnet positions remained protected throughout the drill and installation.

### T02 — Windows browser/UI baseline: accepted

- Full Playwright harness: **171/171 checks passed**.
- Added isolation, cleanup, stricter state assertions, and safer pytest collection.

## Open bugs and engineering risks

| Priority | Item | Current behavior | Planned work |
|---|---|---|---|
| High | Binance testnet leverage refusal on SOL/XRP (`-1000`) | The signal is safely skipped when leverage/margin cannot be set. No unsafe order is placed. | **T03a:** read current leverage and proceed only when it is already within the configured cap; otherwise keep skipping. Add refusal counters and strict execution tests. |
| Medium | No automated CI or protected `master` yet | Tests are comprehensive but currently launched locally. | **T04:** fast/full/release pipelines, machine-readable evidence, then required checks and branch protection. |
| Medium | Installer logic is large Windows batch code | Tested and working, but harder to maintain and diagnose than structured code. | **T03b:** move logic into PowerShell functions; retain tiny double-click batch launchers. |
| Medium | Panel status can wait behind the engine cycle lock | During a long cycle, the panel may temporarily feel frozen even though the engine is working. | **T06–T09:** shared read model and core extraction. |
| Low | Risk tab “Compounding” tile clips at approximately 390 px | Cosmetic mobile-width defect only. | Include in the next bounded UI/mobile ticket. |
| Decision | Duplicate `DCA1H2` slot remains in the canary profile | The current canary runs, but the intended slot set needs a deliberate decision before release. | Resolve during profile/readiness work; do not silently remove it. |

## Roadmap queue

| Order | Ticket/topic | Why it comes here |
|---|---|---|
| Now | **T04 — CI fast/full/release checks** | Prevents later execution and refactor bugs from reaching `master`. |
| Next | **T03a — leverage-refusal fallback** | Fixes a confirmed testnet execution gap under CI protection. |
| Then | **T03b — installer PowerShell migration** | Makes deployment code easier to maintain after its behavior is proven. |
| Then | **T05 — fill telemetry** | Measures expected versus actual fills, partials, delays, and fallbacks. |
| Core | **T06–T09 — shared-core extraction** | Contracts, reason codes, account context, costs, sizing, management levels, and deterministic trade-state transitions. |
| Scale | **T10–T16** | Order budgets, event storage, “Why?” records, scorecards, VPS operation, multi-account/copy shadowing, and ML dataset tooling. |
| Product expansion | News, TradingView, mobile/PWA, gold, stocks, exchanges, copy trading, user modes, richer backtests and engines | Starts only after the safety/core sequence is accepted. Each becomes a bounded ticket with its own evidence. |
| Final gate | Release audit and explicit mainnet promotion | Mainnet stays impossible until this gate is approved by the owner. |

## Ideas being scouted, not yet committed

- Replace the 10-minute GitHub polling bridge with event-triggered pull-request activity when the connected GitHub task trigger is available and proven reliable.
- Use a Windows self-hosted CI runner for the real installer preflight and packaged-executable checks while keeping destructive drills on the guarded testnet machine.
- Generate this overview from ticket evidence to detect stale roadmap summaries automatically.
- Add dependency/security scanning, secret scanning, and signed release provenance before external sharing.
- Add performance budgets for cycle duration, panel response time, backtest duration, and memory so regressions become test failures.

Scouted ideas do **not** enter implementation automatically. Codex compares value, risk, dependencies, and roadmap placement; worthwhile items are proposed as named tickets.

## How Claude and Codex coordinate

1. Claude implements one ticket and pushes its review request and evidence.
2. Codex detects it, reviews the complete diff, runs independent checks, and pushes findings.
3. Claude detects the review commit automatically, fixes findings, and pushes new evidence.
4. Codex accepts only when no findings remain and required testnet/runtime proof is complete.
5. The ticket is fast-forwarded to `master`, this overview is refreshed, and the next ticket begins.

## What you will receive

Every meaningful update in this chat will include:

- **Current ticket and stage** — implementation, review, fix, runtime proof, or accepted.
- **Changelog** — what actually changed in the product.
- **Bugs** — newly found, fixed, still open, and their severity.
- **Evidence** — tests, replay/UI results, build identifiers, and runtime safety state.
- **Roadmap** — what remains and why the order changed, if it changed.
- **Scouting** — promising new topics and whether they belong now, later, or not at all.
- **Your action** — normally “none”; decisions are surfaced plainly when truly needed.
