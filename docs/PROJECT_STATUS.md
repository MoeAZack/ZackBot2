# ZackBot owner overview

*Last refreshed: 2026-10-06 10:48 Cairo (Africa/Cairo) by Codex, on branch `t04d-faster-ci`. This is the plain-language owner view. `ROADMAP.md` and `docs/reviews/` hold the detailed scope and evidence.*

## Where we are now

| Item | Current state |
|---|---|
| Environment | **Binance Futures testnet only.** Mainnet stays impossible until the roadmap and the final release audit are complete and you approve it. |
| Is ZackBot running? | Yes, on your PC (testnet), build `20261006-064354`: engine and exchange ok, four positions, all protected, zero errors/untracked/orphans after the T03b install and rollback drill. |
| Installed application | **T03b installed and rollback-proven.** SHA-256 `203B155474942709B6AD40C3517BA32D058721664D245191C900068CBC4A60A6`; the drill restored the same build and hash exactly. |
| Latest accepted commit | `master` `566ed56` (T05 accepted and merged) |
| Current ticket | **T04d: faster, fail-closed CI review loop** |
| Stage | **Round-four fix requested: the full run passed, but GitHub did not count the dispatch-only `verify full` in PR protection, so the protected merge remains blocked.** |
| What Claude is doing | Replace the final-full trigger with a deliberate PR-associated trigger that satisfies the live required-check rollup without restoring duplicate or rejected full runs. T03c PR #9 remains queued. |
| What Codex is reviewing | T04d's final GitHub protection integration. No executable or runtime action is involved. |
| Your action | **None.** |

## Latest test results

| Where | Result |
|---|---|
| GitHub, run #1 (push of `706fd33`) | **verify fast PASS** (3 min 20 s) |
| GitHub, pull request | Head `8043c7e`, run #32: **verify fast PASS; verify full PASS**. |
| Claude's cloud sandbox (Linux) | `verify fast` PASS; `verify full` PASS (215 tests passed, 1 Windows-only skip; both strict replays PASS; UI 171/171) |
| Your Windows PC | Exact head `8043c7e`: targeted tests **32 passed**; complete `verify.bat full` **PASS** — 224/224 tests, both strict replays, UI 171/171, staged executable build/self-check. Installed bot not touched. |
| T04b, GitHub PR #2 | Head `ab4a7f1`: **verify fast PASS** (1m44s), **verify full PASS** (9m34s), zero annotations. Windows targeted CI tests: **16 passed**. |
| T03a, Windows official pytest | Final head `fc29214`: focused safety/verification set **94 passed** in 5.50s. |
| T03a, GitHub PR #4 | Reviewed head `fc29214` and review head `35cd02b`: **verify fast PASS; verify full PASS**, including all tests, both strict replays and UI harness. |
| T03a installer gate | Installer tests **17 passed**; normal install succeeded once with no warnings; source mirror and shortcuts verified. |
| T03a testnet canary | SOL 0.25% risk / about 18.90 USDT planned notional: safely skipped because current leverage 20× exceeded cap 10×; no order or SOL position; refusal telemetry correct. |
| T03b, GitHub PR #5 | Review/status head `b8dadb2`: **verify fast PASS; verify full PASS**, zero annotations; PR clean and mergeable before the runtime gate. |
| T03b, Windows review | PowerShell 5.1 installer tests **53 passed**; bare pytest **269 passed**; non-installing buildcheck **PASS** with eight successful structured steps. Installed executable hash unchanged. |
| T03b round 1 fixes, Windows | Focused PowerShell 5.1 installer suite **58 passed**. Both high-severity recovery fixes reproduced correctly; no runtime operation was performed. |
| T03b round 2 fixes, Windows | **62 passed, 1 failed.** Installed-file evidence is correct; the new embedded-quote launcher test reproduces marker execution and returns 0. Buildcheck was not rerun while focused tests are red. |
| T03b round 3 fixes, Windows | Focused installer/verification set **80 passed**; real non-installing buildcheck **PASS** with eight successful steps; accepted installed exe unchanged. GitHub fast passed; full was still running at 06:10 Cairo. |
| T03b normal install | Build `20261006-064354`, 14/14 structured steps, no warnings; mirror 120/120 exact; both shortcuts correct; PAPER health clean with four protected lots. |
| T03b rollback drill | Deliberately failed build `20261006-064800`; restored `20261006-064354` with the exact pre-drill hash and authenticated proof; PAPER health remained clean. |
| T05 focused Windows review | Accepted implementation head `e58f0c2`: **46/46 passed** in 11.04 s; deterministic close-vs-admission race test passed. |
| T05 GitHub | Final exact-head fast/full gates passed and T05 merged into protected `master` at `566ed56` at 09:35 Cairo. |
| T04d round-three Windows review | Exact implementation head `b6d4e81`: **51/51 passed** in 14.88 s, including a fresh `core.autocrlf=true` clone and byte-exact manifest proof. |
| T04d GitHub | Stable head `b3e63db`: single fast passed in 5m24s; all parallel full slices and strict aggregator passed in about seven minutes. **GitHub PR protection still shows only fast and blocks merge**, so the trigger needs one more fix. |

Automated test-suite results use a fake exchange. The final T03a gate used the real Binance Futures testnet account; it correctly sent no entry order because leverage was above the cap.

## What just changed

### T04 — automatic checks: accepted and merged

- One command, `verify.py`, with three levels: **fast** (every push), **full** (every pull request into `master`), **release** (on your PC: adds the rollback drill and a read-only check of the running bot).
- Each run saves one summary file: commit, build, dataset checksum, library versions, test counts, replay numbers, UI result, exe checksum, Cairo times.
- No change to trading, the engine, the exchange code, backtests or the panel.

### T03 — installer rollback safety: accepted

- Failed-launch drill proved the previous version is restored byte-for-byte and restarted. Normal install proved. 207/207 Windows tests. Positions stayed protected throughout.

### T03a — leverage-refusal fallback: accepted and installed

- If Binance refuses to change leverage twice, ZackBot reads the coin's current leverage and proceeds only when it is already at or below the stricter user/coin cap.
- Unknown or above-cap leverage still skips the signal without placing an entry order.
- Per-coin refusal, proceeded and skipped counts appear in the panel.
- Build `20261006-030952` is running. The bounded SOL canary exercised the real refusal path and correctly placed no order at 20× current leverage versus the 10× cap. Existing positions and stops stayed healthy.

### T03b — installer PowerShell rewrite: accepted

- The 281-line CMD installer was replaced by a small launcher plus structured PowerShell functions and JSON evidence.
- Windows PowerShell 5.1 tests, the complete Python suite, GitHub CI and a real non-installing executable build all passed.
- Claude fixed both high-severity recovery paths, exact evidence fields and the launcher boundary; Codex confirmed them on Windows.
- The owner approved one normal install and one rollback drill. Both passed once, with structured evidence and no warnings.
- The drill restored build `20261006-064354` byte-for-byte and proved it running. All four testnet lots remained protected and health stayed clean.
- Accepted and merged to protected `master` as `d68d6ef`.

### T05 — fill telemetry: accepted and merged

- Records requested versus executed quantity, expected versus actual price, adverse slippage, wait time, maker attempts and exact fallback state.
- One process-wide, non-blocking writer owns the JSONL file; slow or broken telemetry cannot delay lot persistence or exchange-side protection.
- Restart/rotation, malformed records, missing quantities, fallback truthfulness and writer lifecycle are covered by 46 focused tests.
- Codex found and Claude fixed the final close-vs-admission race. The final exact-head checks passed and protected `master` now contains T05 at `566ed56`.

### T04d — faster review CI: protection-integration fix pending

- Feature-branch commits now run one PR fast check rather than duplicate push and PR checks.
- Rejected review iterations do not run the expensive full suite. Codex explicitly dispatches one parallel full gate only after review is clean.
- The full gate is tied to one exact approved SHA and refuses stale or moved heads.
- Fresh Git-for-Windows checkouts reproduce the byte-hashed datasets even when `core.autocrlf=true`.
- The reviewed implementation passed 51 focused Windows tests. The stable head passed its single fast check and all exact-head full slices.
- A live protection check exposed one remaining issue: a successful dispatch-only `verify full` is attached to the commit but omitted from PR #7's required-check rollup. GitHub correctly blocked the normal merge. Claude must move the deliberate final trigger onto a PR-associated event and prove it live.

## Bugs

| Status | Priority | Item | Notes |
|---|---|---|---|
| Fixed and accepted (T03a) | High | Binance testnet refuses leverage changes on SOL/XRP (`-1000`) | Fallback reads current leverage and remains fail-closed; real testnet canary skipped safely at 20× > cap 10× with no order. |
| Open | Medium | Panel can feel frozen during a long engine cycle | Planned in T06–T09 (core extraction). |
| Open | Low | Risk tab "Compounding" tile clips at about 390 px wide | Next UI/mobile ticket. |
| Fixed and accepted (T04) | High | Release drill could run without first proving an exact healthy PAPER status | Now fail-closed before and after the drill; 23 planted unsafe shapes prove the drill is never called. |
| Fixed and accepted (T04) | High | Required replay and UI gates could be skipped while full/release reported PASS | Skip switches removed from named levels; regression tested. |
| Fixed and accepted (T04) | High | Windows manifest fixture mismatch | Byte-exact fixture; Windows full suite passed. |
| Fixed and accepted (T04) | High | GitHub UI baseline had mobile overflow and missing clean-checkout history | Responsive wrap and deterministic replay seed handoff; GitHub and Windows UI 171/171. |
| Fixed and accepted (T04) | Medium | Lightweight secret check missed nested private filenames and JSON contents | Current-tree scan strengthened with planted tests; Git-history scan remains T04b. |
| Fixed (T04 docs) | Low | T04 review document named commit ids from before the branch was rebuilt | Corrected on `t04-ci`. |
| Fixed and accepted (T03b) | High | Invalid rollback drill left the deliberately untrusted new build installed and running | Restores and HMAC-verifies the old build, then retains a failed invalid-drill verdict; the real drill passed. |
| Fixed and accepted (T03b) | High | Failed stop attempt did not verify or recover the previous runtime | Proves the old runtime or performs one hash-guarded restart; confirmed across answering, restart and unrecoverable cases. |
| Fixed and accepted (T03b) | Medium | Installed-file evidence could be stale | Re-reads presence/hash at every verdict and verifies failed first-install deletion; confirmed on Windows. |
| Fixed and accepted (T03b) | Medium | Raw launcher parser could select install/drill from malformed extra arguments | Anchors on the first exact launcher token and refuses the three reproduced ambiguity cases. |
| Fixed and accepted (T03b) | Low | Launcher test claimed it could neutralize hostile outer CMD syntax | Tests the real boundary: fixed supported invocations and refusal of ordinary unknown/extra tokens. |
| Fixed and accepted (T05) | High | Optional fill telemetry performed synchronous file I/O before protection | Order paths now use bounded non-blocking admission to a dedicated process-wide writer. |
| Fixed and accepted (T05) | High | A timed-out writer handover could overlap two writers on one file | Settings restarts share one writer; a stuck closer stays registered and drop-only until proven stopped. |
| Fixed and accepted (T05) | Medium | Fallback, malformed-history and unknown-quantity telemetry could be misleading or break status | Exact fallback states, defensive normalization and explicit unknown outcomes are covered by focused tests. |
| Fixed and accepted (T05) | Medium | `emit()` could accept after `close()` stopped the writer | Admission and closing are atomic under one lock; deterministic race test passes. |
| Fixed; final gate pending (T04d) | High | Every rejected PR iteration ran the full suite | Full verification is now explicit and exact-head only; ordinary PR updates run fast only. |
| Fixed; final gate pending (T04d) | Medium | Feature heads started duplicate push and PR fast runs | Feature branches now have one PR fast producer; push-fast is master-only. |
| Fixed; final gate pending (T04d) | High | Fresh Windows checkouts changed hashed dataset bytes to CRLF | Manifest datasets and manifest JSON are pinned to LF and tested in a fresh autocrlf-enabled clone. |

## Open risks

- **CI security follow-up (low):** add Dependabot for the immutable action pins plus Git-history secret and dependency scanning.
- **Repository visibility:** GitHub currently reports the repository as **public**. The owner previously accepted that testnet-development risk; it must return to private before any mainnet credential or live-release work.
- T04d has no remaining code finding. It changes CI/review infrastructure only and requires no installer, bot stop, or testnet canary.

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
| Current | **T04d — faster CI** | Review-clean; final stable-head fast/full evidence and protected merge remain |
| Next execution | **T03c — automatic leverage handling** | Queued PR #9; review starts after T04d merges |
| Resilience | **T05b — exchange-outage handling** | Backoff/circuit state, incident coalescing and reconciliation after transient Binance failures |
| Next measurement | **T05a — causal trade audit** | Peak profit/MFE, give-back, hold/close reasons and causal earlier-exit comparisons |
| Core | **T06–T09 — shared core** | One trading logic for live and backtest |
| Strategy research | **T09a** | Range, short and scalp families; Quick Bank TP/runner/micro-DCA components; research/shadow first |
| Regime and macro | **T09b** | Per-asset/timeframe regimes plus USD, US bonds/rates, equities, commodities and optional TradingView evidence |
| Scale and control | **T10–T13a** | Order budgets, trade records, scorecards, bounded risk grades and Manual/Recommend/Automatic authority |
| Product expansion | **T14–T16 and Phases 8–12** | VPS, multi-account/copy, PWA, TradingView/news, gold and TradFi after the safety foundation |
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
3. **Scouting:** any idea above you want moved up, or dropped?

## How Claude and Codex coordinate

GitHub is the message channel. Claude pushes a ticket's commit and posts **READY FOR CODEX** on the pull request; Codex reviews the exact commit and replies there; Claude fixes and posts **FIXED FOR CODEX**; this repeats until Codex accepts. Then this page is refreshed, the pull request is merged under the branch rules, and the next ticket starts. You do not need to carry messages.

## Safety boundaries (unchanged)

Testnet only. No mainnet keys, no live orders, no withdrawal permissions. Existing testnet positions are disposable test data and do not block bounded automated tests. You are still asked first before an installer run, rollback drill, deliberate bot stop/restart, credential change, repository visibility change, or anything that could reach mainnet.
