# ZackBot owner overview

*Last refreshed: 2026-10-05 23:15 Cairo (Africa/Cairo) by Codex, on branch `t04-ci`. This is the plain-language owner view. `ROADMAP.md` and `docs/reviews/` hold the detailed scope and evidence.*

## Where we are now

| Item | Current state |
|---|---|
| Environment | **Binance Futures testnet only.** Mainnet stays impossible until the roadmap and the final release audit are complete and you approve it. |
| Is ZackBot running? | Yes, on your PC (testnet). Last confirmed healthy at the end of T03: engine and exchange ok, 4 positions, all protected, 0 orphans. Nothing in T04 has stopped or touched it. |
| Installed application | **Unchanged** since build `20261005-204405` (T03). T04 does not install anything. |
| Latest accepted commit | `master` `8b6641f` (T03 accepted + this overview) |
| Current ticket | **T04: automatic checks (CI): fast / full / release verification** |
| Stage | **Changes requested.** Codex reviewed handoff `b884a79` and confirmed three release-gate defects. T04 is not ready for the long Windows full run or acceptance. |
| What Claude is doing | Fix the Windows fixture, testnet-before-drill gate and skip-to-PASS behavior; then post **FIXED FOR CODEX** with a new SHA and evidence |
| What Codex is reviewing | The exact remote handoff `b884a790845fc90d15a69133313950c90378fb4f`; review: `docs/reviews/T04_review_gpt.md` |
| Your action | **None.** Do not run `verify.bat full` yet; the automated review loop has returned the findings to Claude. |

## Latest test results

| Where | Result |
|---|---|
| GitHub, run #1 (push of `706fd33`) | **verify fast PASS** (3 min 20 s) |
| GitHub, pull request | `verify fast` PASS for `b884a79`; **verify full FAILED** with 168/171 UI checks (two mobile overflows and one calendar-flow timeout). Unit tests and both strict replays passed. |
| Claude's cloud sandbox (Linux) | `verify fast` PASS; `verify full` PASS (215 tests passed, 1 Windows-only skip; both strict replays PASS; UI 171/171) |
| Your Windows PC | Codex targeted T04 tests: **1 failed, 23 passed**. Confirmed cross-platform newline bug in the new manifest test. The long T04 run is deferred. |

All results above are simulated or automated test environments with a fake exchange. None placed real orders.

## What just changed

### T04 — automatic checks: in review

- One command, `verify.py`, with three levels: **fast** (every push), **full** (every pull request into `master`), **release** (on your PC: adds the rollback drill and a read-only check of the running bot).
- Each run saves one summary file: commit, build, dataset checksum, library versions, test counts, replay numbers, UI result, exe checksum, Cairo times.
- No change to trading, the engine, the exchange code, backtests or the panel.

### T03 — installer rollback safety: accepted

- Failed-launch drill proved the previous version is restored byte-for-byte and restarted. Normal install proved. 207/207 Windows tests. Positions stayed protected throughout.

## Bugs

| Status | Priority | Item | Notes |
|---|---|---|---|
| Open | High | Binance testnet refuses leverage changes on SOL/XRP (`-1000`) | Signals are skipped safely; no unsafe order. Fix: **T03a**, next after T04. |
| Open | Medium | Panel can feel frozen during a long engine cycle | Planned in T06–T09 (core extraction). |
| Open | Low | Risk tab "Compounding" tile clips at about 390 px wide | Next UI/mobile ticket. |
| Open (T04 review) | High | `verify release` drills before proving PAPER/testnet and does not require PAPER to pass reconciliation | Must be fixed before any release drill. |
| Open (T04 review) | High | Required replay and UI gates can be skipped while full/release reports PASS | Must be made fail-closed or moved to an explicitly partial diagnostic mode. |
| Open (T04 review) | High | New manifest test fails on Windows because fixture bytes differ from its expected hash | Blocks the owner's Windows full verification. |
| Open (T04 review) | High | GitHub full verification failed: mobile Signals overflow 22 px, Research overflow 12 px, and Trades calendar click timed out | Diagnose from the uploaded evidence; fix without weakening the UI requirements. |
| Open (T04 review) | Medium | Lightweight secret check misses nested private filenames and all JSON contents | Strengthen in T04 or explicitly carry into T04b with planted tests. |
| Fixed (T04 docs) | Low | T04 review document named commit ids from before the branch was rebuilt | Corrected on `t04-ci`. |

## Open risks

- **T04 release gates (high):** Codex found three confirmed defects in the first handoff. No installer, rollback drill or long Windows run is requested while they are open.
- **CI maintenance (low):** GitHub warns that three standard actions used by the checks target an old Node version, and that the Linux runner image changes to Ubuntu 26 on 2026-10-19. Nothing fails today. Proposed fix as a tiny follow-up (T04b): pin the runner image and update the three actions.
- No protected `master` yet: comes right after T04 is accepted.
- Installer logic is long Windows batch code: T03b.

## Code clean-up policy (your request, 2026-10-05)

Every ticket now ends with a **clean-up pass on the files it touched**: remove dead code, unused imports and stale comments, provided the tests prove nothing changed. Larger removals are listed here and become their own small tickets, so safety tickets stay focused.

**Clean-up backlog (first scan, 2026-10-05):**

| Item | Size | Proposal |
|---|---|---|
| Unused imports in `lab.py` and the research scripts | tiny | Remove in the next ticket that touches them |
| 7 legacy config warnings at start-up (A7) | small | Remove the old config source |
| Grid / COMBO bots (`grid.py`, about 1,000 lines): 0 of 32 tested setups made money; shipped disabled | large | **Your decision:** keep as an experiment, or remove from the app (research stays in Git history) |
| `ai_filter.py` (off by default) | small | Decide during the News/AI ticket (Phase 11) |
| `run_checks.bat/.sh` now only call `verify full` | tiny | Remove after T04 is merged |
| Superseded bundles and scripts in `C:\Dev\ZackBot2_git\_superseded_*` | local only | Can be archived once GitHub holds everything |
| `panel.html` is one 320 KB file | large | Split during the UI/PWA work, not before |

## Roadmap queue

| Order | Ticket | Why it comes here |
|---|---|---|
| Now | **T04 — automatic checks** | Stops later bugs reaching `master` |
| Next | **T03a — leverage-refusal fallback** | Fixes the confirmed SOL/XRP gap, under CI |
| Then | **T03b — installer in PowerShell** | Easier to maintain once behaviour is proven |
| Then | **T05 — fill telemetry** | Measures expected vs actual fills |
| Core | **T06–T09 — shared core** | One trading logic for live and backtest |
| Scale | **T10–T16** | Order budgets, trade records, "Why?", scorecards, VPS, multi-account, ML data |
| Final gate | Release audit + your explicit mainnet approval | |

## Scouting: ideas from other bots and from Claude/Codex (proposed, not started)

How scouting works: Claude and Codex look at what leading bots do (3Commas, Cryptohopper, Bitsgap, Pionex, Freqtrade, Passivbot, Hummingbot, Cornix) and at ideas from our own reviews. A worthwhile idea is attached to an existing ticket as a **subtopic**, or proposed as a new ticket. Nothing is built without a roadmap slot, its own tests and the normal review. Ideas we already have are not repeated.

### From other bots (first pass, 2026-10-05)

| Idea (seen in) | What it would add to ZackBot | Proposed place | Value |
|---|---|---|---|
| **Stop-loss streak guard** (Freqtrade *StoplossGuard*) | Pause new entries after N stop-outs within X hours, per coin or overall | Subtopic of the risk-rule work in **T10** (risk/execution, backtested first) | High |
| **Cooldown after exit** (Freqtrade *CooldownPeriod*) | No re-entry on the same coin for a set time after a close; stops whipsaw re-entries | Same as above, **T10** | Medium |
| **Weak-coin lock** (Freqtrade *LowProfitPairs*) | Temporarily drop a coin whose recent trades keep losing | Research first, then **T13** scorecard | Medium |
| **Delisting and new-listing filters** (Freqtrade *DelistFilter*, *AgeFilter*) | Never open on a coin Binance is about to delist; skip coins with too little history | Subtopic of **T03a** follow-up (safety, small) or **T06** | High |
| **Spread / volatility filters** (Freqtrade *SpreadFilter*, *VolatilityFilter*) | Skip coins whose spread or volatility makes stops and maker fills unreliable | Subtopic of **T05** (needs the fill data) | Medium |
| **Total wallet exposure limit** (Passivbot *TWEL*) | One hard cap on total position size versus capital, across all slots | Subtopic of **T10**; required before lead portfolios (Phase 7) | High |
| **1-minute-candle backtests** (Passivbot) | Exact intrabar order instead of the open/low/high/close guess | Subtopic of **T09** (parity) | Medium |
| **One exit object: stop + target + time limit** (Hummingbot *triple barrier*) | Cleaner, testable trade management | Design input for **T08** | Medium |
| **Signal producer / consumer** (Freqtrade) | One engine publishes intents, other accounts consume them | Design input for **T15** (multi-account) | High (later) |
| **Indicator warm-up check** (Freqtrade *recursive-analysis*) | Proves indicators give the same value with short and long history | Subtopic of **T04b** (CI) | Medium |
| **Telegram/TradingView signal intake** (Cornix, 3Commas) | Already planned | Phase 11, unchanged | — |
| **Evolutionary parameter optimiser** (Passivbot) | Our walk-forward showed re-tuning hurts | **Not recommended** | Low |
| **"Unstucking" losing DCA baskets** (Passivbot) | Closes stuck baskets in small loss slices | Research only (tail-risk test first) | Low |

### From Claude/Codex reviews

| Idea | Proposed place |
|---|---|
| Pin the CI runner image + update GitHub actions (avoids a break on 2026-10-19) | New tiny ticket **T04b** right after T04 |
| Dependency and secret scanning in CI | Subtopic of **T04b** |
| Windows self-hosted runner for the real installer/exe checks (Codex) | After **T03b** |
| Leverage refusal counter per coin + daily Telegram summary | Subtopic of **T03a** |
| Structured (JSON) installer logs | Subtopic of **T03b** |
| Record the order-book spread at signal time | Subtopic of **T05** |
| Performance budgets (cycle time, panel response, memory) as tests (Codex) | Subtopic of **T06** |
| Point-in-time coin list incl. delisted coins (LUNA, FTT) | Subtopic of **T13** |
| Generate this page from the evidence files so it can't go stale (Codex) | Subtopic of **T12** |
| Event-triggered PR handling instead of polling (Codex) | Process, when the GitHub trigger is proven |

## Decisions for you (no rush)

1. **Grid / COMBO:** keep (disabled, experimental) or remove from the app?
2. **Duplicate `DCA1H2` slot** in the canary profile: keep or remove? (Unchanged from T03.)
3. **T04b:** OK to add the small CI-maintenance ticket right after T04?
4. **Scouting:** any idea above you want moved up, or dropped?

## How Claude and Codex coordinate

GitHub is the message channel. Claude pushes a ticket's commit and posts **READY FOR CODEX** on the pull request; Codex reviews the exact commit and replies there; Claude fixes and posts **FIXED FOR CODEX**; this repeats until Codex accepts. Then this page is refreshed, the pull request is merged under the branch rules, and the next ticket starts. You do not need to carry messages.

## Safety boundaries (unchanged)

Testnet only. No mainnet keys, no live orders, no withdrawal permissions. You are asked first before any installer run, rollback drill, bot stop/restart, anything that could touch testnet orders, or anything needing credentials.
