# ZackBot owner overview

*Last refreshed: 2026-10-05 22:45 Cairo (Africa/Cairo) by Claude, on branch `t04-ci`. This is the plain-language owner view. `ROADMAP.md` and `docs/reviews/` hold the detailed scope and evidence.*

## Where we are now

| Item | Current state |
|---|---|
| Environment | **Binance Futures testnet only.** Mainnet stays impossible until the roadmap and the final release audit are complete and you approve it. |
| Is ZackBot running? | Yes, on your PC (testnet). Last confirmed healthy at the end of T03: engine and exchange ok, 4 positions, all protected, 0 orphans. Nothing in T04 has stopped or touched it. |
| Installed application | **Unchanged** since build `20261005-204405` (T03). T04 does not install anything. |
| Latest accepted commit | `master` `8b6641f` (T03 accepted + this overview) |
| Current ticket | **T04: automatic checks (CI): fast / full / release verification** |
| Stage | **Review.** Code is pushed (`706fd33`); a pull request into `master` is open; waiting for the GitHub `verify full` run and Codex's review |
| What Claude is doing | Opened the pull request, corrected the review document, posting **READY FOR CODEX** once the checks finish |
| What Codex is reviewing | T04 on branch `t04-ci` (the exact commit is named in the pull-request comment) |
| Your action | None right now. Later (I will ask first): run `verify.bat full` on the PC, then protect `master` on GitHub |

## Latest test results

| Where | Result |
|---|---|
| GitHub, run #1 (push of `706fd33`) | **verify fast PASS** (3 min 20 s) |
| GitHub, pull request | `verify fast` + `verify full`: running, results go in the pull-request comment |
| Claude's cloud sandbox (Linux) | `verify fast` PASS; `verify full` PASS (215 tests passed, 1 Windows-only skip; both strict replays PASS; UI 171/171) |
| Your Windows PC | Last full Windows run: T03, 207/207 tests. The T04 Windows run (`verify.bat full`) is still to do |

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
| Fixed (T04 docs) | Low | T04 review document named commit ids from before the branch was rebuilt | Corrected on `t04-ci`. |

## Open risks

- **CI maintenance (new, low):** GitHub warns that three standard actions used by the checks target an old Node version, and that the Linux runner image changes to Ubuntu 26 on 2026-10-19. Nothing fails today. Proposed fix as a tiny follow-up (T04b): pin the runner image and update the three actions.
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

## Scouting: new topics (proposed, not started)

Scouted topics are attached to an existing ticket as a **subtopic** when they fit there, or proposed as a new ticket. Nothing starts without a roadmap slot.

| Topic | Value | Proposed place |
|---|---|---|
| Pin CI runner image + update GitHub actions | Avoids a surprise CI break on 2026-10-19 | New tiny ticket **T04b**, right after T04 |
| Dependency and secret scanning in CI (e.g. `pip-audit`, history secret scan) | Catches vulnerable libraries and leaked keys automatically | Subtopic of T04b |
| Leverage "refusal counter" shown per coin with a daily Telegram summary | Owner sees silent skips | Subtopic of **T03a** |
| Structured logs (JSON lines) for the installer | Faster diagnosis of a failed install | Subtopic of **T03b** |
| Record order-book spread at signal time | Makes the maker-fill model checkable | Subtopic of **T05** |
| Performance budgets (cycle time, panel response, memory) as tests | Turns slowdowns into test failures | Subtopic of **T06** |
| Point-in-time coin list with delisted coins (LUNA, FTT) | Removes survivorship bias from research | Subtopic of **T13** (scorecard) |
| Weekly automatic owner report generated from evidence files | Status page can never go stale | Subtopic of **T12** |

## Decisions for you (no rush)

1. **Grid / COMBO:** keep (disabled, experimental) or remove from the app?
2. **Duplicate `DCA1H2` slot** in the canary profile: keep or remove? (Unchanged from T03.)
3. **T04b:** OK to add the small CI-maintenance ticket right after T04?

## How Claude and Codex coordinate

GitHub is the message channel. Claude pushes a ticket's commit and posts **READY FOR CODEX** on the pull request; Codex reviews the exact commit and replies there; Claude fixes and posts **FIXED FOR CODEX**; this repeats until Codex accepts. Then this page is refreshed, the pull request is merged under the branch rules, and the next ticket starts. You do not need to carry messages.

## Safety boundaries (unchanged)

Testnet only. No mainnet keys, no live orders, no withdrawal permissions. You are asked first before any installer run, rollback drill, bot stop/restart, anything that could touch testnet orders, or anything needing credentials.
