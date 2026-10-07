# ZackBot historical regression & architecture audit (v3.2-rc2 → master)

- **Audited head:** `origin/master` @ `ef6abc0` (FBL-BT01), compared with tag `v3.2-rc2` (61 commits, 75 files, +17.2k/−0.6k lines).
- **Branch:** `audit/historical-code-audit`. Read-only audit: no product code was changed on any branch.
- **Date:** 07 Oct 2026, Africa/Cairo. **Author:** Claude Code (integration owner).
- **Method:** six evidence-gathering passes run in parallel (appendices A–F). The integration owner spot-checked the
  highest-severity claims against the code; see "Verification by the integrator" below.
- **Status:** **PROPOSED, not accepted.** Codex reviews severity, removes duplicates and places tickets on the roadmap.
  Cowork validates only the Windows/runtime/UI claims listed in §6. No broad fix starts until the agents agree (owner
  instruction).

Active work this audit does **not** touch (still moving separately): PR #18 BT02 (exchange filters, at `e224a8b`, in
Codex review) and PR #19 market-data collector (at `9baa092`, Codex code-pass plus isolated runtime evidence).

## 1. Executive summary

1. **No regressions.** All 212 prior findings are tracked in the ledger (Appendix A2, plus `HISTORICAL_CODE_AUDIT_ledger.json`).
   | Status | Count |
   |---|---|
   | Fixed | 71 |
   | Partially fixed | 16 |
   | Open | 114 |
   | Needs runtime evidence | 9 |
   | Obsolete | 2 |
   | Regressed | **0** |

   Every Codex-accepted fix (T03–T05b, FBL-BT01) still holds on master.
2. **The open risk is the order/bookkeeping core, unchanged since v3.2-rc2.** It is not new code. The serious items
   (§2, C01–C09) sit in `engine.py` order and management paths that the T-tickets never touched. The pre-mainnet engine
   fixes from issue #13 (ENG01 stop verification, ENG02 fill truth, TRATE, DCA-off) exist **only on unmerged
   `cowork/*` reference branches**.
3. **One P0, reproduced:** `trades.csv` is written synchronously *inside* the fill/partial-close/add paths. If the
   write fails (for example Excel holding the file open), the post-fill bookkeeping is skipped. The same TP1 or
   pyramid add then fires again every pass: the repro showed 5 buys for a 1-add plan, with the exchange stop still
   covering only the first size. The integrator confirmed the code path.
4. **Truthfulness gap visible to owner and followers:** the DCA profile cards and the Research table still show
   pre-FBL-BT01 numbers. For example "Active DCA $500 → $4,637", where the corrected backtester gives about $210 and PF
   0.80. DCA is still on by default on master.
5. **Backtest ≠ engine in ways that move results:**
   - the time exit is one candle off (engine N, backtest N+1; it affects every DCA preset's 60-candle exit);
   - maker entries can take a target before they are filled;
   - adds pay no slippage.

   The parity/causality gates do not exercise most of the options involved.
6. **Concurrency.** One engine lock is held across network calls, AI review and sleeps. Stop management and the
   Flatten/Close controls can stall for minutes in bad network conditions, while the panel shows stale numbers with
   green chips.
7. **Persistence.** A corrupt `settings.json` silently restarts with defaults and entries open, and is overwritten at
   once. A corrupt `state.json` starts with no lots while positions stay open. Saves have no fsync and no backup.

Severity totals after consolidation (§2):
| Severity | Groups | Note |
|---|---|---|
| P0 | 1 | |
| P1 | 12 | 7 of them block testnet progression |
| P2 | 13 | |
| P3 | 4 | |

## 2. Consolidated issues (duplicates across sections merged)

Each group lists its source finding IDs. The full evidence, fix and tests for each source finding are in the appendices
and in `HISTORICAL_CODE_AUDIT_ledger.json`. **CONF** = confirmed by reading or running the code; **SPEC** =
speculative (it needs the stated evidence).

| ID | Sev | Class | Issue | Source findings | Blocks | Cowork runtime |
|---|---|---|---|---|---|---|
| C01 | **P0** | CONF (repro) | A failed `trades.csv` / JSON write inside the fill, partial-close and add paths skips the post-fill bookkeeping. Partial closes and adds repeat every pass, the extra size is unstopped, and a reconcile failure freezes all management | ENG-B01, OBS-01 | testnet | yes (Excel lock on testnet) |
| C02 | P1 | CONF (repro) | One try-block per lot: a repeatedly failing add (e.g. -2019) starves trailing, breakeven, TP1, ladder and tp_r | ENG-B02 | testnet | no |
| C03 | P1 | CONF | Fill truth: an unanswered order of ≤ (lots+1) steps is treated as filled. Fill quantities are ignored on the success path. An unconfirmed entry that filled is never adopted (it can sit unstopped for up to about 8 h) | ENG-B03, ENG-B04, ENG-B08, HIST-03 | testnet | yes |
| C04 | P1 | CONF | Stop existence is not verified routinely (only a report-only check after an outage). `cowork/eng01` has a reference, but it conflicts with ENG02 provisional stops | ENG-B07, HIST-02 | testnet | yes |
| C05 | P1 (mainnet) | SPEC | Resending with the same client order id after `-2013` can double-fill a delayed MARKET order | ENG-B10, OPS-03 | mainnet | yes (testnet probe) |
| C06 | P1 | CONF (budget SPEC) | No shared request budget or ban back-off across order, critical-read, backtest and Lab traffic. A 418 can escalate. A transient stop failure on entry triggers an immediate market close | OPS-01, HIST-04, CON-02, ENG-B09 | mainnet (CON-02: testnet if Lab is used heavily) | yes |
| C07 | P1 | CONF | The engine lock is held across the whole cycle: candle fetches, account reads, AI review ≤ 60 s, orphan cancels. The loop sleeps 60 s after a failed cycle. The panel has no fetch timeout, so it shows stale data with green chips | OPS-02, CON-01, CON-03, UX-05, HIST-05 | testnet (UX/controls) | yes (timings) |
| C08 | P1 | CONF | Persistence: a corrupt settings file resets to defaults with entries open and is overwritten. A corrupt state file starts with no lots. No fsync or backup. `/api/settings` applies keys one by one, so memory and disk can diverge | ARCH-01, OPS-05, OPS-06, HIST-06 | testnet | no |
| C09 | P2 | CONF | The cycle exit, panel/Telegram close and flatten ignore `lot['pending']` and can close another slot's resting-maker fill or a sibling lot's size | ENG-B05, ENG-B06, HIST-07 | testnet (ENG-B06) | no |
| C10 | P1 | CONF | DCA truthfulness: preset cards and the Research table show invalidated numbers. DCA is on by default (the owner's DCA-off decision is not on master). The "old engine" badge shows on every backtest, and the backtester version was not bumped by FBL-BT01 | HIST-01, UX-01, UX-02 | testnet (copy followers) | no |
| C11 | P1 | CONF (run) | Time exit `max_bars` is off by one between engine and backtest; live timing jitters with the cycle phase | BT-C1 | testnet (DCA presets) | no |
| C12 | P1 (option off) | CONF (repro) | Maker entry fills walk from the candle open, so targets and trails can hit before the limit fill (same-candle lookahead) | BT-C2 (Fable BT-08) | neither while maker is off by default | no |
| C13 | P2 | CONF | Smaller fill-model and parity gaps: adds pay no slippage; BTC breaker / pump guard are sampled differently; armed trail entries don't count toward `max_pos`; replay harness asymmetries; shipped `data/` ends on a forming candle; Cairo-midnight halt day | BT-C3..C8 | neither | no |
| C14 | P1 | CONF | Duplicated rule implementations (engine vs backtest): sizing, Kelly, governor, risk rules, stop ratchet, add gate. They already differ (governor default auto vs off), and parity never exercises them | ARCH-02, ENG-B14 | testnet (results) | no |
| C15 | P2 | CONF | Parity/causality coverage gaps: no mode with a firing time exit, maker, trail-entry, ladder, ttp, regime, breaker, risk rules, Kelly/governor, 1h. Only 3 of 6 causality modes are in the fast gate; strict replays only in the full gate | BT-C9, HIST-12, TEST-03 | testnet (for result claims) | no |
| C16 | P2 | CONF | Accounting estimates (FEE_EST, flat funding) feed guards, Kelly and drift gates. Recorded `risk_usd` ignores the leverage cap. Capital is derived from a history capped at 3,000 trades | HIST-09, ENG-B12, UX-04 | mainnet | yes |
| C17 | P2 | CONF | Install/update safety: the installer hard-kills the bot with no PAPER/idle gate (only the drill has one). No build provenance; no hash pins or Python pin; plaintext config copies left behind | OPS-04, HIST-10, OPS-13, OPS-11 | mainnet | yes |
| C18 | P2 | CONF / runtime | Single-instance race (port check TOCTOU + SO_REUSEADDR) could run two engines on one account. Paper→live switch carries testnet state | OPS-08, OPS-07 | testnet (OPS-08) / mainnet (OPS-07) | yes (OPS-08) |
| C19 | P2 | CONF | Telegram authorises a chat, not a person, with an optional PIN. Sending is implemented 3 times, with an unbounded thread per message | OPS-10, ARCH-04 | mainnet | no |
| C20 | P2 | CONF | CI/process: a PR can edit its own workflows to satisfy required checks; pytest config hardening; docs drift on CI | OPS-09, TEST-07, ARCH-08 | neither (process) | no |
| C21 | P2 | CONF | Test quality: global monkeypatch seams leak across tests and leave fake-only production branches. No tests for Kelly, settings load/migration, or HTTP auth outside the UI harness. Source-text-pinned tests. No property/mutation tests on rounding, sizing, intrabar path, reconcile or circuit | TEST-01, TEST-02, TEST-04, TEST-05, TEST-06 | neither (prerequisite for C14) | no |
| C22 | P2 | CONF | Three slot validators with different rules: settings accept DCA values the engine then refuses; the Lab skips validation; the Kelly×governor ceiling reaches up to 8× slot risk (SPEC) | ARCH-03, ENG-B12 (part) | neither | no |
| C23 | P2 | CONF | Observability: the backtest worker can die silently; telemetry/audit health is invisible; audit expectancy is shown without coverage; full-file JSON rewrites per event inside the lock | OBS-02, OBS-03, OBS-04 | neither | no |
| C24 | P2 | CONF | UI: "30d"/"All" equity and "All trades" are silently truncated; some unescaped symbol/side interpolations; panel errors swallowed | UX-03, UX-06, UX-07 | neither | yes (UI) |
| C25 | P2 | CONF | T03c above-cap exception is always on; ordinary-maker leverage race; one-way account fails every order (not just shorts); partial TP rounding to 0 marked done | HIST-13, ENG-B11, ENG-B13 | mainnet | no |
| C26 | P2 | CONF | Ops minimum absent: heartbeat, kill switch, playbook, 24/7 coverage | HIST-11 | mainnet | yes |
| C27 | P3 | CONF | Docs/instructions stale or contradictory (see §6 owner decision); dead / test-only code and unexecuted research scripts; app main loop reaches into engine privates | HIST-15, ARCH-05, ARCH-06, ARCH-07 | neither | no |
| C28 | P3 | CONF | Small unsynchronised races and memory/payload growth (JOBS, caches) | CON-04, CON-05 | neither | no |
| C29 | P3 | CONF | Time-sync error path and minor client error paths | OPS-12 | neither | no |
| C30 | P3 | CONF | Research statistics details (OOS labelling, MC assumptions) | BT-C10 | neither | no |

Grouped ledger rows HIST-14 (partially-fixed hygiene) and HIST-16 (runtime-evidence list) are itemised in Appendix A.

## 3. Behaviour since v3.2-rc2, area by area (meaningful changes only)

| Area | v3.2-rc2 | Master now | Assessment |
|---|---|---|---|
| Order lifecycle & reconcile | Idempotent `_order` by client id; reconcile by stop-order id | **Unchanged**; T05b adds an outage circuit for non-critical reads and a report-only stop check after outages | Core risks C01–C05 and C09 pre-date rc2 and remain |
| Stops / targets / runners / partials | tp1, ladder (dust rule), runner part, trailing, breakeven | Unchanged in the engine; the backtest got one declared intrabar path (FBL-BT01) | Engine behaviour stable. Backtest partial-exit parity is being fixed in PR #18 |
| Long / short | Symmetric math; hedge mode | Unchanged; one-way accounts fail every order (C25) | No new asymmetry found |
| DCA & pyramiding | Engine adds via `_add_gate`/`_add_qty` | Unchanged in the engine. Backtest add fills follow the path (FBL-BT01), which invalidated the DCA preset numbers | C10, C11, C13 |
| Sizing / leverage / minimums | Risk sizing, floor to step, skip below minimum | T03a refusal fallback; T03c above-cap exception with account-wide proof (+454 lines, always on). Shared exchange-filter function in PR #18 | Fails closed. C25 notes always-on and a maker race |
| Backtest ↔ engine parity & causality | Replay harness, intrabar heuristics | One declared OHLC path; deterministic tests; causality test | Still off-by-one time exit, maker lookahead, coverage gaps (C11–C15) |
| Outage & retry | Retries with Retry-After | Shared circuit for reads; outage blocks entries and adds; startup reconnect | Orders and critical reads still have no ban budget (C06); lock held during retries (C07) |
| Security & credentials | DPAPI config, token cookie, host check | Unchanged plus a secret scan in verify; testnet fault flags inert on mainnet (verified) | HTTP auth sound; Telegram auth weak (C19); PR workflow-edit gap (C20) |
| Installer / rollback / update | CMD installer | PowerShell installer with structured records, drill mode, hash-verified rollback, PAPER-gated drill | The normal install still hard-kills without a PAPER/idle gate; no provenance (C17) |
| UI truthfulness / a11y / mobile | Preset cards with research numbers | Unverified tag on DCA presets; Playwright smoke harness; a11y roles/labels/focus OK; mobile layout OK | Stale DCA numbers, stale-data freeze, truncation (C10, C07, C24) |
| Logging / audits / telemetry | trades.csv, bot.log | Non-blocking fill telemetry (T05), causal trade audit (T05a), log scrubbing | trades.csv is still synchronous in order paths (C01); health invisible (C23) |
| Performance / locking | Single engine lock | More work under the lock (audit hooks, bounded); Lab and backtests in-process | C07, C28 |
| Config & dependencies | requirements files | Pinned CI runner and actions; runtime deps pinned without hashes; Python not pinned for the Windows build | C17 |
| Tests / CI | Ad-hoc tests | ~875 tests; fast/full gates; replay and causality gates; branch protection requires both gates | Fast gate misses the BT01 parity modes; no property/mutation tests; leaky monkeypatch seams (C15, C21) |

## 4. Structure: duplication, compatibility paths, dead code, coupling, risky past fixes

- **Duplicated implementations (risk-reducing to merge):**
  - sizing / Kelly / governor / risk rules / stop ratchet / add gate in `engine.py` vs `backtest.py` (C14);
  - three slot validators (C22);
  - Telegram sending ×3 (C19);
  - hand-written Cairo-time fallbacks. The backtester's uses +3 h year-round, which is wrong in winter (Appendix F).

  Integrator correction to ENG-B14: on the PR #18 branch, `feasibility.py` does not add a third copy of the exchange
  minimums. The engine's entry/add checks, the backtester and the preflight all call that one function.
- **Temporary compatibility paths:**
  - legacy config locations and migrations in `app.load_cfg`;
  - `except TypeError` fallbacks for older client signatures. These can downgrade critical reads (Appendix A/F) and run
    only against test fakes (TEST-01).
- **Dead / test-only code:** `research_get`, research scripts not wired anywhere, source-text-pinned tests guarding
  dead branches (ARCH-05, TEST-04).
- **Hidden coupling:**
  - the app main loop is the live scheduler and reaches into engine privates (`_kc`, `_lev`);
  - tests depend on module-global monkeypatching (ARCH-06, TEST-01);
  - the panel relies on string-matched messages in a few places (Appendix E).
- **Split / consolidate candidates (only with a concrete benefit):**
  - extract one shared `risk_model` (sizing + Kelly + governor + rules) used by engine and backtest, which removes C14;
  - inject the exchange client instead of module globals, which removes TEST-01 and makes property tests cheap;
  - an `engine.py` split only after injection (ARCH-07);
  - move backtest/Lab execution out of the trading process (C07/CON-03).
- **Past fixes that added complexity:**
  - T03c's always-on above-cap exception (+454 lines; C25);
  - T05b's `except TypeError` fallbacks;
  - FBL-BT01 changed backtest numbers without bumping the version, so old results can't be told apart (UX-02);
  - T04d made full-gate results label-triggered, so regressions surface late (C15).

## 5. Proposed roadmap tickets (dependencies and acceptance gates)

The bounded fixes are listed in order of risk. These are proposals for Codex to place. The `cowork/*` reference
branches are inputs: under the owner's rule, Claude Code re-implements them with tests rather than merging them.

| Ticket | Covers | Depends on | Acceptance gate | Blocks |
|---|---|---|---|---|
| **AUD-01 Order-path I/O isolation** | C01 | none (first) | `log_trade` / `save_json` can never raise into an order path. The `post` bookkeeping runs before any I/O. `stop_dirty` is set on any exception after an exchange call. Tests: fault-inject PermissionError in TP1, ladder, pyramid, DCA and reconcile; exactly one order per event; stop qty = position; other lots still managed. Cowork: testnet run with trades.csv open in Excel | testnet |
| AUD-02 Per-step isolation in `manage` + pending-aware closes | C02, C09 | AUD-01 | One step failing doesn't skip the others. Close / flatten respect `pending` and resting makers. Repro tests for ENG-B02, B05 and B06 | testnet |
| AUD-03 Fill truth | C03 (ref `cowork/eng02`) | AUD-01 | executedQty/status drive bookkeeping. An unconfirmed entry that filled is adopted with a stop within one pass. Pending resolution is decided by exchange evidence, not tolerance. Testnet canary via Cowork | testnet |
| AUD-04 Routine stop verification | C04 (ref `cowork/eng01`) | AUD-03 | Stops are checked every N passes, and re-placed or alerted. Interaction test with AUD-03 provisional stops (the known ENG01×ENG02 bug). Cowork: delete a testnet stop by hand and observe the repair | testnet |
| AUD-05 Persistence safety | C08 | none (parallel with AUD-01) | Corrupt settings or state: entries paused, backup restored or alert, never overwritten. fsync + `.bak`. Atomic `/api/settings` validate-then-apply. Tests with corrupt and truncated files | testnet |
| AUD-06 DCA truthfulness | C10 (ref `cowork/dca-off`) | PR #18 merged | DCA off by default (owner decision 2026-10-07). Preset cards / Research table show BT01+BT02 verified numbers or "not verified" with no headline number. Backtest version bumped. UI test asserts no invalidated numbers | testnet (followers) |
| AUD-07 Backtest parity fixes | C11, C12, C13 | PR #18 merged | Time exit matches the engine (N candles). Maker fill only after the limit is touched. Adds pay slippage. Breaker sampling uses 1h BTC. Trail entries count toward `max_pos`. Each with a replay test | testnet (result claims) |
| AUD-08 Parity coverage | C15 | AUD-07 | Replay/causality modes for time exit, maker, trail entry, ladder, ttp, regime, breaker, risk rules, Kelly/governor, 1h. The BT01 modes join the fast gate | testnet (result claims) |
| AUD-09 One risk model | C14, C22 | AUD-08, AUD-12 (injection) | Engine and backtest import one sizing/Kelly/governor/rules module. One slot validator. Property tests (Hypothesis) on sizing/rounding | neither |
| AUD-10 Lock scope & loop | C07 | AUD-01..04 (same code) | No network, AI or sleep under the engine lock. Panel fetch timeout and stale indicator. Failed-cycle back-off ≤ the management interval. Cowork: measure Flatten latency during a simulated outage | testnet (controls) |
| AUD-11 Request budget & bans | C06, C05 (ref `cowork/trate`) | AUD-10 | Shared weight budget across clients including backtest/Lab downloads. Global 418 ban-until. No market close on a transient stop error. Same-cid resend decided by testnet evidence (Cowork probe) | mainnet |
| AUD-12 Test seams & CI | C20, C21 | none (parallel) | Exchange client injected (no module-global fakes). HTTP auth tests in the fast gate. Workflow-file changes need owner review (CODEOWNERS). pytest hardening. Mutation run on rounding, intrabar path and circuit | neither (enables AUD-09) |
| AUD-13 Install / update / instance safety | C17, C18 | none | Install refuses unless PAPER/idle (or the owner confirms). Build provenance (commit, Python, hashes). Exclusive single-instance lock. Paper→live wipes testnet state. Cowork: PC install run | mainnet (C18 OPS-08: testnet) |
| AUD-14 Observability & UI truth | C16 (UI part), C23, C24 | AUD-05 | Worker death reported. Telemetry/audit health shown. Truncation labelled. All interpolations escaped. Panel errors surfaced | neither |
| AUD-15 Mainnet minimum | C16, C19, C25, C26 | AUD-11, AUD-13 | Exchange-truth accounting. Telegram user auth + PIN required. T03c setting. Heartbeat, kill switch, playbook | mainnet |
| AUD-16 Cleanup | C27–C30 | AUD-12 | Dead code removed with tests. Docs reconciled. Small races fixed | neither |

**Suggested order:** AUD-01 and AUD-05 now (small, high value), then AUD-02/03/04. AUD-06/07/08 follow once PR #18 is
merged. AUD-10/11 are next, then the mainnet bundle.

## 6. Coordination

- **Cowork — Windows/runtime/UI validation only:**
  - C01 Excel lock on testnet;
  - C03/C04 testnet canaries;
  - C05 same-cid resend probe;
  - C06 418/429 weight under a backtest download;
  - C07 lock / Flatten latency;
  - C17 installer kill timing;
  - C18 second-instance bind on Windows;
  - C24 truncation display;
  - UX-02 badge on screen.

  Appendix A's HIST-16 and each source block mark which claims need it.
- **Codex:** independently re-grade severity, merge or remove duplicates, decide ticket placement, accept or reject
  each consolidated issue. Nothing here is self-accepted.
- **Owner decisions surfaced:**
  - (a) **Instruction conflict.** The global CLAUDE.md says to pause before installers and bot restarts. The repo
    CLAUDE.md and the Codex AGENTS.md pre-approve PAPER/testnet installs and restarts. One rule should win.
  - (b) Whether DCA preset cards are hidden or relabelled until BT01+BT02 verified numbers exist (AUD-06).

## 7. Verification by the integrator (spot checks)

- **C01 / ENG-B01:** confirmed. `engine.log_trade` is a bare `open(..., 'a')` (`engine.py:720-725`), called inside
  `_apply_close` (`:1088-1089`) before it returns. The caller's `tp1` / `adds` / `next_add` bookkeeping
  (`:1303-1310`) runs only after the return.
- **C11 / BT-C1:** consistent. The engine compares wall-clock seconds since `opened` / TF_SEC ≥ max_bars
  (`engine.py:1672,1684`). The backtest compares `i - p['i'] >= max_bars` from the entry bar (`backtest.py:493`).
  The section author's replay reproduced N vs N+1.
- **ENG-B14:** corrected as noted in §4.
- **Other P1s:** carried as reported, with their Evidence class. Codex should re-verify the SPEC ones (C05, part of
  C06).

## 8. Files

- `docs/reviews/HISTORICAL_CODE_AUDIT.md`: this report, with appendices A–F as written by the evidence passes.
- `docs/reviews/HISTORICAL_CODE_AUDIT_ledger.json`: machine-readable ledger with `consolidated` (C01–C30 → source
  IDs), `findings` (85 blocks) and `prior_ledger` (212 rows).

---


# Appendix A — History timeline and prior-finding ledger

_Evidence pass A (read-only); integrated unchanged. Severity and status are proposals for Codex review._


Scope: `v3.2-rc2..origin/master` (61 commits, HEAD `ef6abc0`), every prior audit, review and triage item. Read-only;
line numbers are current master in `C:/Dev/ZackBot2_audit`. Machine-readable ledger: `A_ledger.json` (212 rows).

## A1 Timeline of behaviour changes since v3.2-rc2

| Theme | Commits | Behaviour change | Risk introduced |
|---|---|---|---|
| ROADMAP v4 / T01 | 1de4433..756303a | Docs only: master-account target, ticket order, decision rules | None in code; starts the status-doc drift (A-DOC-1/2) |
| T02 UI baseline | b1923a8, 0957949, 7275fda, 2fc8642 | Playwright harness (isolated port, 3 viewports, failure injection); `pytest.ini testpaths=tests`. Panel: 2-line change | Harness is a smoke suite: no rendered value is checked against the API, dialogs auto-accepted (FBL-WF-01) |
| T03 rollback drill | b72b78b..24ff38b, e9e16bf | `build_app.bat` gains drill mode, verified hash rollback, HMAC build ping; mirror/shortcuts only after a confirmed launch; `app.py` `--simulate-failed-launch` | Installer still hard-kills the bot (`installer.ps1:262,269`), stamps only a timestamp build id (`:342`) |
| T04/T04b CI + verify | 32f2925..b3f525c, fb955d3 | `verify.py fast/full/release`; fail-closed PAPER-only pre-drill gate (`verify.py:274-300`); no skip switches; any-depth secret scan; pinned runner/actions | Current-tree secret scan only (no history); PS 5.1 and exe build never run in GitHub CI (FBL-OPS-03) |
| T03a leverage fallback | f9778ca | After 2 refused leverage changes, read current leverage; enter only if ≤ min(cap, coin max); per-coin counters | Small; snapshot not atomic (CX-T03A-1) |
| T03b PowerShell installer | d68d6ef | CMD logic → `installer.ps1` (671 lines): structured record, stop-attempt vs stop-confirmed, failed-stop recovery, fixed entry points (`rollback_drill.bat`) | ~4k lines Windows-only deploy code+tests (FBL-OPS-09) |
| T05 fill telemetry | 9cd14eb..c48a13f, 566ed56 | Process-wide non-blocking `FillWriter` (`engine.py:41-177`): `put_nowait`, counted drops, truthful fallback states, `fills.jsonl` window | One daemon writer thread per path; observe-only. Trading path still books plan qty on zero fill (FBL-ENG-02) |
| T04d faster CI | cb5092b | Full gate only on `full-ready` label (`verify.yml:19-22`); PR runs fast only; `verify_ci.py` slice merger; master push runs `push fast` | A later commit has no full result until relabelled; label can be added by any writer (FBL-OPS-06) |
| T03c auto leverage | 3eabdbc | Above-cap leverage accepted under cross margin after an account-wide exposure/stop/bracket proof (`engine.py:1850-2043`); exceptional lots get no adds/maker remainder; `ZB_TESTNET_FAULTS` injector (exact TESTNET only) | +454 engine lines, always enabled (no setting, FBL-PROC-05); normal maker vs external leverage race open (CX-T03C-15) |
| T05b outage resilience | 3b86a03 | Circuit on non-critical GET reads; outage blocks entries and adds (`engine.py:1481`); startup reconnect with capped backoff (`app.py:471-513`); incidents coalesced; critical reads/orders never gated | Loop still `sleep(60)` after a cycle failure (`app.py:586`); `except TypeError` fallbacks can downgrade critical reads (FBL-TEST-01) |
| T05a causal audit | 207ef5e | `trade_audit.py` (1,599 lines): mark-sampled MFE/MAE, give-back, funnel, `/api/audit_summary`, Trades card; hooks inside `manage()` | More work inside `manage()` under the engine lock (bounded, memory-only); observe-only |
| CLAUDE.md | 7fe7cdc | Working agreement: testnet installs/merges without owner approval | Conflicts with the owner's global instruction (A-DOC-4) |
| FBL-BT01 intrabar path | ef6abc0 | One declared OHLC path (`backtest.py:116-133`); every intrabar event (DCA fill, stop, basket TP, pyramid, tp1, ladder) fired in path order (`:316-421`); doji = worst case; `replay.py` follows it; DCA presets get an `unverified` tag only | Large backtest-number change (DCA-1h PF 1.75→0.80 per #13); preset notes, research table and DCA defaults NOT updated (HIST-01) |

Not on master (relevant): `cowork/eng01`, `cowork/eng02`, `cowork/trate`, `cowork/dca-off`, `bt02-exchange-filters`. Every
pre-mainnet engine fix from #13 (ENG01/02/TRATE/DCA-off) exists only on unmerged branches.

## A2 Prior-finding ledger

Status counts (212 rows): fixed 71 · partially-fixed 16 · open 114 · needs-runtime-evidence 9 · obsolete 2 · regressed 0.
Open P1: 25 (all pre-mainnet; 3 also block testnet progression). Sources: v3.1 report (V31-), v3.2 response (V32-),
Codex reviews (CX-), Fable audit (FBL-), issue #13/#14/#15 (I13-), Fable scouting/range (FS-/FR-), this audit's doc
drift (A-DOC-). Fable "High" protection/accounting items are graded P1; Fable "Medium" UI/process items P2/P3.

| ID | Source | Issue | Sev | Status | Evidence | Blocks |
|---|---|---|---|---|---|---|
| V31-A1 | v3.1 report §3 | Algo-order cancel string code '200' treated as failure | P3 | fixed | binance_client.py:288-290 | neither |
| V31-A2 | v3.1 report §3 | Backtest tab/Lab ignored per-slot when/trail_entry/pump_guard and run options | P1 | fixed | app.py:276; lab.py:103 | neither |
| V31-A3 | v3.1 report §3 | pandas 3 datetime unit mismatch crashed merge_asof (regime filter every cycle) | P1 | fixed | strategies.py:273-274 | neither |
| V31-A4 | v3.1 report §3 | Active (1h) preset far riskier (-63% DD) than its 6-month test | P1 | partially-fixed | engine.py:300 (warning in note; preset still offered, numbers pre-BT01) | testnet progression |
| V31-A5 | v3.1 report §3 | Tight ladder (1h) recommended from a 6-month test | P2 | partially-fixed | panel.html:642 corrected; panel.html:665 still calls it 'a candidate' | neither |
| V31-A6 | v3.1 report §3 | Telegram chat id is the bot's own id; Telegram never worked | P2 | needs-runtime-evidence | panel.html:1940 adoption hint; PC config not readable here | mainnet release only |
| V31-A7 | v3.1 report §3 | 7 legacy 'invalid value ignored' config warnings at startup | P3 | open | app.py:127-135 (no migrate(); also FBL CH-02) | neither |
| V31-A8 | v3.1 report §3 | Worst-case intrabar mode identical to path mode | P3 | obsolete | backtest.py:116-133 (worst=True now reorders every candle) | neither |
| V31-A9 | v3.1 report §3 | v3.1 UI build cut off mid-way | P3 | fixed | test_app_ui.py (T02 harness, 171 checks) | neither |
| V31-R1 | v3.1 report §5.1 | Testnet != mainnet: fills, algo endpoints, order-lookup-on-timeout never fired live | P1 | needs-runtime-evidence | engine.py:41-177 (T05 telemetry now records fills); binance_client.py:321-348 | mainnet release only |
| V31-R2 | v3.1 report §5.2 | Maker fill model optimistic; resting maker entry has no stop | P1 | partially-fixed | T05 telemetry engine.py:41-177; backtest maker model unchanged backtest.py:13-14,439-449 | mainnet release only |
| V31-R3 | v3.1 report §5.3 | No 1m data; intrabar order is a heuristic; survivorship | P2 | partially-fixed | backtest.py:116-133 declared path (FBL-BT01); no detail candles | mainnet release only |
| V31-R4 | v3.1 report §5.4 | Presets selected in-sample | P1 | open | engine.py:300-310 (insample True everywhere) | mainnet release only |
| V31-R5 | v3.1 report §5.5 | Funding filter not backtested; flat funding | P2 | open | backtest.py:13 FUND_PER_BAR | mainnet release only |
| V31-R6 | v3.1 report §5.6 | Backtest tab BTC breaker uses 4h proxy (no 1h BTC) | P3 | open | backtest.py:135 btc1h param; app.py never passes btc1h | neither |
| V31-R7 | v3.1 report §5.7 | Bot on home PC; nothing manages positions while down | P1 | open | ROADMAP.md:367 (T14 not started) | mainnet release only |
| V31-R8 | v3.1 report §5.8/Q8 | No kill switch/dead-man heartbeat outside the PC | P1 | open | no heartbeat code; ROADMAP.md:367 | mainnet release only |
| V31-R9 | v3.1 report §5.9 | All grid configs lost; grids shipped as experiment | P2 | open | grid.py (988 lines) still wired into manage(); decision deferred (#13) | neither |
| V31-R10 | v3.1 report §5.10 | All profiles long-biased crypto beta | P2 | open | research/*.csv; no benchmark rows | neither |
| V32-P1-1 | v3.2 response §1 | Adds bypassed portfolio risk rules | P1 | fixed | engine.py:1474-1505; backtest.py:350-352,386 | neither |
| V32-P1-2 | v3.2 response §1 | Orphaned/reused slots could add without leverage cap | P1 | fixed | engine.py:1491-1496 | neither |
| V32-P1-3 | v3.2 response §1 | Installer could relaunch a stale exe after failed build | P1 | fixed | installer.ps1 (T03/T03b); docs/reviews/T03b_acceptance.md | neither |
| V32-P2-1 | v3.2 response §2 | Test run not reproducible (load_data, pytest deps) | P2 | fixed | requirements-dev.txt; tests/conftest.py; verify.py | neither |
| V32-P2-2 | v3.2 response §2 | UI harness/research not reproducible | P2 | fixed | test_app_ui.py; DATA_MANIFEST.json; verify.py manifest check | neither |
| V32-P2-3 | v3.2 response §2 | Partial nested dca/pyramid override -> KeyError | P2 | fixed | strategies.py:217 merge_mgmt | neither |
| V32-P2-4 | v3.2 response §2 | UI time follows device not Cairo | P3 | fixed | panel.html:766 | neither |
| V32-P2-5 | v3.2 response §2 | Blank target looked broken; no exit plan | P3 | fixed | engine.py:1423 exit_plan | neither |
| V32-P2-6 | v3.2 response §2 | AI review claimed news knowledge, always 4h | P2 | fixed | ai_filter.py:5-7,29 | neither |
| V32-P2-7 | v3.2 response §2 | Engine and backtester are separate implementations | P1 | open | backtest.py:316-421 vs engine.py:1227-1380; ROADMAP.md:359-362 T06-T09 not started | mainnet release only |
| V32-WIN | v3.2 response §2 | Windows build/test/self-test never run end to end | P2 | fixed | docs/reviews/T03_acceptance.md; T03b_acceptance.md | neither |
| V32-ATR | v3.2 response §3 | Backtester trailing stop used same-candle ATR (lookahead) | P1 | fixed | backtest.py:439,478 atr[i-1] | neither |
| V32-Q1 | v3.2 response §7 | Should BTC breaker pause DCA safety orders | P3 | fixed | engine.py:1507-1514; backtest.py:345-348 (per-lot policy) | neither |
| V32-Q2 | v3.2 response §7 | Replay gate tolerance too loose (15 pts) | P2 | fixed | replay.py STRICT matched%/median dR; tests/test_causality.py:97-99 | neither |
| V32-Q3 | v3.2 response §7 | Shared-core pure management function | P2 | open | same as V32-P2-7 | mainnet release only |
| V32-Q4 | v3.2 response §7 | Cheap CI check for intrabar leaks | P2 | partially-fixed | tests/test_bt_intrabar_path.py; tests/test_causality.py (synthetic only) | neither |
| V32-G8 | v3.2 response §6 | Testnet observation with fill/reconcile telemetry | P2 | fixed | engine.py:41-177 (T05); trade_audit.py (T05a) | neither |
| CX-T03-1 | T03_review_gpt P2 | Review summary pointed at obsolete states | P3 | fixed | docs/reviews/T03_review_request.md top section | neither |
| CX-T03-2 | T03_review_gpt P2 | Normal install success path unproven | P2 | fixed | docs/reviews/T03_acceptance.md | neither |
| CX-T03-3 | T03_review_gpt P3 | Protect master after CI exists | P3 | fixed | GitHub API: required [verify fast, verify full], enforce_admins=true, force_push=false | neither |
| CX-T03A-1 | T03a_review_gpt follow-up | lev_refusals panel snapshot not atomic | P3 | open | engine.py:2044-2070 (deferred to T06-T09) | neither |
| CX-T03A-2 | T03a_review_gpt follow-up | Move leverage read to PositionInfo V3/user stream | P3 | open | binance_client.py positionRisk v2 | mainnet release only |
| CX-T03B-1 | T03b_review_gpt P1 | Invalid drill left new build installed/running | P1 | fixed | installer.ps1 DRILL_INVALID rollback; T03b_acceptance.md | neither |
| CX-T03B-2 | T03b_review_gpt P1 | Failed stop not followed by verification/recovery | P1 | fixed | installer.ps1:118 bot_stop_confirmed + stop_recovery | neither |
| CX-T03B-3 | T03b_review_gpt P2 | Structured record overstated installed/stopped | P2 | fixed | installer.ps1:105-119 | neither |
| CX-T03B-4 | T03b_review_gpt P3 | Launcher re-expanded mode unquoted | P3 | fixed | build_app.bat:9-20 (no %1 expansion) | neither |
| CX-T03B-5 | T03b_review_gpt R1 P2 | installed_file_sha256 cached/stale | P2 | fixed | installer.ps1:105-119 | neither |
| CX-T03B-6 | T03b_review_gpt R1 P3 | Embedded quotes escaped launcher | P3 | fixed | build_app.bat:11 (scoped out as caller shell) | neither |
| CX-T03B-7 | T03b_review_gpt R2 P2 | Last-occurrence parsing could select install/drill | P2 | fixed | installer.ps1:616-667 Get-LauncherMode | neither |
| CX-T03B-8 | T03b_review_gpt R2 P3 | Injection test asserted impossible guarantee | P3 | fixed | tests/test_installer.py | neither |
| CX-T03C-1 | T03c_review_gpt R1 P1 | Exposure proof omitted account exposure/unprotected/in-flight state | P1 | fixed | engine.py:1850-2043 | neither |
| CX-T03C-2 | T03c_review_gpt R1 P1 | Margin balance combined with wrong loss/notional basis | P1 | fixed | engine.py:1850-2043 | neither |
| CX-T03C-3 | T03c_review_gpt R1 P1 | Adds/maker remainder bypassed exceptional gate | P1 | fixed | engine.py:1483,2199,2578-2638 | neither |
| CX-T03C-4 | T03c_review_gpt R1 P1 | 1/leverage_max not bracket-aware | P1 | fixed | engine.py:1819-1830; binance_client.py:472-475 | neither |
| CX-T03C-5 | T03c_review_gpt R1 P2 | NaN/invalid numerics fail open | P2 | fixed | binance_client.py _finite_number | neither |
| CX-T03C-6 | T03c_review_gpt R1 P2 | Decision telemetry lost real reason | P2 | fixed | engine.py:2044-2070 | neither |
| CX-T03C-7 | T03c_review_gpt R1 P3 | Cooldown still made venue calls | P3 | fixed | engine.py:1790-1795 (cooldown only; see ENG-13) | neither |
| CX-T03C-8 | T03c_review_gpt R2 | Stale exceptional maker admission | P1 | fixed | engine.py:2199 | neither |
| CX-T03C-9 | T03c_review_gpt R2 | Stop proof did not require STOP_MARKET/MARK_PRICE shape | P1 | fixed | engine.py:1850-2043 | neither |
| CX-T03C-10 | T03c_review_gpt R2 | Stale _lev cache after external leverage change | P1 | fixed | engine.py:1787-1789 | neither |
| CX-T03C-11 | T03c_review_gpt R2 | Fractional/bool leverage truncated to valid int | P2 | fixed | binance_client.py adapters | neither |
| CX-T03C-12 | T03c_review_gpt R2 | String 'false' flags read as true | P2 | fixed | engine.py exposure proof flag checks | neither |
| CX-T03C-13 | T03c_review_gpt R2/R3 | notionalCoef ignored / guessed scaling | P2 | fixed | binance_client.py:472-475 (non-1 coef rejects) | neither |
| CX-T03C-14 | T03c_review_gpt R3 | Legacy exceptional maker record re-priced on stale approval | P2 | fixed | engine.py:2578 | neither |
| CX-T03C-15 | T03c_review_gpt R3 follow-up | Ordinary maker order vs external leverage change race | P2 | open | engine.py:1787-1789 (pop only at next ensure) | mainnet release only |
| CX-T03C-16 | T03c_review_gpt runtime | Installer staging omitted manifest data -> staged tests failed | P2 | fixed | installer.ps1 (data staged for pytest, deleted before PyInstaller) | neither |
| CX-T03C-17 | T03c_review_gpt R5 | Two failed leverage requests counted as one | P3 | fixed | engine.py _lev_api_refused | neither |
| CX-T04-1 | T04_review_gpt P1 | Required full check red (mobile overflow, calendar timeout) | P1 | fixed | panel.html (74fc48c); verify.py seed handoff | neither |
| CX-T04-2 | T04_review_gpt P1 | Manifest test failed on Windows CRLF | P2 | fixed | tests/test_verify.py byte-exact fixture | neither |
| CX-T04-3 | T04_review_gpt P1 | Release drill could stop a LIVE bot | P1 | fixed | verify.py:274-300 | neither |
| CX-T04-4 | T04_review_gpt P1 | Skip switches let full/release report PASS | P1 | fixed | verify.py (no skip on named levels) | neither |
| CX-T04-5 | T04_review_gpt P2 | Secret scan: root names only, JSON skipped, no history | P2 | partially-fixed | verify.py:108-125 (any depth, JSON in); no git-history scan | mainnet release only |
| CX-T04-6 | T04_review_gpt R2 P1 | Pre-drill runtime gate fail-open on missing fields | P1 | fixed | verify.py:274-300 runtime_problems | neither |
| CX-T04-7 | T04_review_gpt non-blocking | Batch-file whitespace noise | P3 | obsolete | T03b replaced CMD logic | neither |
| CX-T04B-1 | T04b_review_gpt follow-up | No Dependabot for pinned actions | P3 | open | .github/ has no dependabot.yml | neither |
| CX-T04B-2 | T04b_review_gpt follow-up | No git-history secret / dependency scanning | P2 | open | verify.py:125 'Git-history scanning is T04b' (never added) | mainnet release only |
| CX-T04D-1 | T04d_review_gpt R4 | Full result not PR-associated | P2 | fixed | .github/workflows/verify.yml:19-22 (label full-ready) | neither |
| CX-T04D-2 | T04d_review_gpt R4 | Unrelated labels could satisfy full gate | P2 | fixed | .github/workflows/verify.yml | neither |
| CX-T04D-3 | T04d_review_gpt R4 | CodeQL py/redos in test regex | P3 | fixed | tests/test_ci.py line parser | neither |
| CX-T05-1 | T05_review_gpt P1 | Telemetry file I/O between fill and stop | P1 | fixed | engine.py:42-56 put_nowait writer | neither |
| CX-T05-2 | T05_review_gpt P2 | Failed writes created phantom stats | P2 | fixed | engine.py:60 ctr persisted/dropped | neither |
| CX-T05-3 | T05_review_gpt P2 | Partial maker record claimed fallback | P2 | fixed | engine.py maker fallback states | neither |
| CX-T05-4 | T05_review_gpt P2 | Rotation broke restart summary window | P2 | fixed | engine.py FillWriter window (.1 + current) | neither |
| CX-T05-5 | T05_review_gpt P2 | Missing executedQty recorded as full fill (telemetry) | P2 | fixed | telemetry 'unknown'; trading path still books plan qty (ENG-02) | neither |
| CX-T05-6 | T05_review_gpt R2 P1 | Timed-out handover started second writer | P1 | fixed | engine.py:41-177 process-wide writer | neither |
| CX-T05-7 | T05_review_gpt R2 P2 | fallback=true meant call returned, not filled | P2 | fixed | engine.py fallback_attempted/confirmed/pending | neither |
| CX-T05-8 | T05_review_gpt R2 P2 | Malformed JSONL line broke /api/status | P2 | fixed | engine.py invalid_records counter | neither |
| CX-T05-9 | T05_review_gpt R2 P2 | Writer threads leaked (tests/replay/ctor failure) | P2 | fixed | tests/conftest.py; replay.py close | neither |
| CX-T05-10 | T05_review_gpt R3 P2 | emit() could admit after close() | P2 | fixed | engine.py:42-56 single lock | neither |
| CX-T05A-1 | T05a_review_gpt P2 | Tests used Windows locale not UTF-8 | P2 | fixed | tests/test_trade_audit.py (encoding='utf-8') | neither |
| CX-T05B-1 | T05b_review_gpt P1 | Startup outage left engine permanently disconnected | P1 | fixed | app.py:471-513 | neither |
| CX-T05B-2 | T05b_review_gpt P2 | Backoff 2**n overflow after ~17h outage | P2 | fixed | app.py:495 | neither |
| CX-T05B-3 | T05b_review_gpt follow-up | Test connection uses uncircuit-gated time read | P3 | open | app.py:770-781 | neither |
| CX-BT01-1 | FBL_BT01_review_gpt P1 | Whole-candle stop precheck skipped earlier DCA fills | P1 | fixed | backtest.py:336-367 | neither |
| FBL-ENG-01 | Fable audit §3 / #13 | Stop existence not re-verified while position size matches | P1 | open | engine.py:1567-1570 (fix only on origin/cowork/eng01) | mainnet release only |
| FBL-ENG-02 | Fable audit §3 / #13 | executedQty/status ignored -> phantom lot/add/close | P1 | open | engine.py:2221; engine.py:1196-1220 (fix only on cowork/eng02) | mainnet release only |
| FBL-ENG-03 | Fable audit §3 / #13 | No order budget; transient stop failure -> market close | P1 | open | engine.py:2252-2256; binance_client.py:294 | mainnet release only |
| FBL-ENG-04 | Fable audit §3 | close_lot/flatten/cycle exit ignore pending -> duplicate close | P1 | open | engine.py:1136-1146 | mainnet release only |
| FBL-ENG-05 | Fable audit §3 | Stop P&L at stop price, flat fee, no funding | P1 | open | engine.py:269,1087,1220 FEE_EST | mainnet release only |
| FBL-ENG-06 | Fable audit §3 | Kelly x governor can raise slot risk up to 8x | P2 | open | engine.py:1126-1134,2133,2463-2471 | mainnet release only |
| FBL-ENG-07 | Fable audit §3 | Resting maker entries survive halt/pause/flatten | P1 | open | engine.py:2720-2737 (flatten never cancels makers) | mainnet release only |
| FBL-ENG-08 | Fable audit §3 | Cycle holds lock over fetches, 60s LLM, sleeps; sleep(60) before manage | P1 | open | engine.py:1652-1653; app.py:586; ai_filter.py:35 | mainnet release only |
| FBL-ENG-09 | Fable audit §3 | close_lot closes whole exchange qty incl. untracked | P2 | open | engine.py:1141-1143 | mainnet release only |
| FBL-ENG-10 | Fable audit §3 | Same-cid resend after -2013 may double-fill delayed POST | P2 | needs-runtime-evidence | binance_client.py:318-340 | mainnet release only |
| FBL-ENG-11 | Fable audit §3 | lot risk_usd stores uncapped risk | P2 | open | engine.py:2134,2155,2234 | neither |
| FBL-ENG-12 | Fable audit §3 | One-way mode: every order fails; hedge flag not re-checked | P3 | open | engine.py:534-537 | neither |
| FBL-ENG-13 | Fable audit §3 | marginType POST + read on every entry | P3 | partially-fixed | engine.py:1792-1796 (skipped only in cooldown) | neither |
| FBL-ENG-14 | Fable audit §3 | Unreadable state.json -> starts with no lots | P1 | open | engine.py:419 | mainnet release only |
| FBL-ENG-15 | Fable audit §3 | Partial TPs can round to zero, marked done | P3 | open | engine.py:1310 | neither |
| FBL-ENG-16 | Fable audit §3 | maxQty unchecked; MARKET_LOT_SIZE used for STOP/LIMIT | P3 | open | engine.py:527 | neither |
| FBL-ENG-17 | Fable audit §3 | Dead hasattr client fallbacks | P3 | open | engine.py:1819-1839 | neither |
| FBL-ENG-18 | Fable audit §3 | No client-side weight accounting | P3 | open | binance_client.py:264-311 | mainnet release only |
| FBL-ENG-19 | Fable audit §3 | Ambiguous initial stop -> safety close not cid poll | P3 | open | engine.py:2252-2256 | neither |
| FBL-ENG-20 | Fable audit §3 | Module globals block AccountContext | P3 | open | engine.py:389 INCIDENT_LOCK; app.py APP.engine | neither |
| FBL-ENG-21 | Fable audit §3 | Business-error add failures retried every 8s forever | P3 | open | engine.py:1196-1226 | neither |
| FBL-ENG-22 | Fable audit §3 | Paper mode manages on mainnet marks, stops on testnet marks | P2 | open | engine.py:405; app.py:597 | testnet progression |
| FBL-BT-01 | Fable audit §4 / #13 FBL-BT01 | DCA basket TP hit by pre-fill high on red candles | P1 | fixed | backtest.py:116-133,316-421 (ef6abc0) | neither |
| FBL-BT-02 | Fable audit §4 | Strict replay never covers 1h or DCA-dominant mix | P2 | partially-fixed | test_engine_sim.py:25 (4h only); tests/test_bt_intrabar_path.py:287 hand-built DCA replay | testnet progression |
| FBL-BT-03 | Fable audit §4 | Causality/parity use synthetic candles; 1 unmatched tolerated | P2 | partially-fixed | tests/test_causality.py:97-99,122 unchanged; new deterministic path tests | neither |
| FBL-BT-04 | Fable audit §4 | Trend edge concentrated in top-10 trades / 2 quarters | P1 | open | research/*.csv; no P&L-ex-top-10 in reports | mainnet release only |
| FBL-BT-05 | Fable audit §4 | 2y/40-coin tables survivorship-biased, bull-only | P1 | open | research_combos.py; panel.html:639 | mainnet release only |
| FBL-BT-06 | Fable audit §4 | Walk-forward tested only risk | P2 | open | research_long2.py | mainnet release only |
| FBL-BT-07 | Fable audit §4 | 5-day block MC mis-calibrated | P2 | open | lab.py block bootstrap | mainnet release only |
| FBL-BT-08 | Fable audit §4 | Maker model (rest a bar) far more generous than live GTX | P1 | open | backtest.py:439-449 | mainnet release only |
| FBL-BT-09 | Fable audit §4 | Live regime from 1,500-candle window differs from backtest | P3 | open | engine.py:664 | neither |
| FBL-BT-10 | Fable audit §4 | Pyramid add + stop same red candle under-counts loss | P2 | fixed | backtest.py:383-392 (path-ordered adds) | neither |
| FBL-BT-11 | Fable audit §4 | Worst-case rows byte-identical to baseline | P3 | needs-runtime-evidence | backtest.py:128 (worst=True now differs for DCA; not re-run) | neither |
| FBL-BT-12 | Fable audit §4 | Funding flat both sides; no history | P2 | open | backtest.py:13 | mainnet release only |
| FBL-BT-13 | Fable audit §4 | research_grid.py is a copy; grid result not reproducible | P3 | open | research_grid.py | neither |
| FBL-BT-14 | Fable audit §4 | Rotation 'both' rows duplicate 'long' | P3 | open | strategies.py rotation; research_refresh.py | neither |
| FBL-BT-15 | Fable audit §4 | Slot allocation alphabetical, not signal strength | P3 | open | backtest.py pend order; engine.cycle | neither |
| FBL-BT-16 | Fable audit §4 | BTC min notional 50 vs exchange 100 | P2 | open | backtest.py:17,268 (tracked as #14 BT02) | testnet progression |
| FBL-BT-17 | Fable audit §4 | Backtests compound; live COMPOUND=False | P3 | open | engine.py:323 | neither |
| FBL-BT-18 | Fable audit §4 | Lab hold-out reusable without consult count | P3 | open | lab.py | neither |
| FBL-BT-19 | Fable audit §4 | Kelly over 40 fat-tailed R is noise | P3 | open | backtest.py Kelly; engine.py:1126-1134 | neither |
| FBL-BT-20 | Fable audit §4 | Flat 0.5% maintenance; constant stop slippage | P3 | open | backtest.py:135,201 | neither |
| FBL-UI-01 | Fable audit §5 / #13 CONC01 | Flatten/Close/Move stop/Telegram wait on engine lock | P1 | open | engine.py:1653,2696,2723 | mainnet release only |
| FBL-UI-02 | Fable audit §5 | No data-as-of/busy state; no fetch timeout | P2 | open | panel.html (no AbortController) | neither |
| FBL-UI-03 | Fable audit §5 | 19 native confirm(); inconsistent danger confirms | P2 | open | panel.html (19 confirm( calls) | mainnet release only |
| FBL-UI-04 | Fable audit §5 | Too many controls; no tiering | P3 | open | panel.html | neither |
| FBL-UI-05 | Fable audit §5 | Exposure only on Risk tab; no 'why did it trade' | P3 | open | panel.html | neither |
| FBL-UI-06 | Fable audit §5 | Typography/contrast/reduced-motion | P3 | open | panel.html:9-85 | neither |
| FBL-UI-07 | Fable audit §5 | Mobile is reflowed desktop | P3 | open | panel.html | neither |
| FBL-UI-08 | Fable audit §5 | Nested interactive in role=switch; small targets | P3 | open | panel.html | neither |
| FBL-UI-09 | Fable audit §5 | Single 327KB file, no-store | P3 | open | panel.html (1,987 lines) | neither |
| FBL-UI-10 | Fable audit §5 | Client/server validation drift (manual stop distance) | P3 | open | panel.html; app.py; engine.py | neither |
| FBL-DATA-01 | Fable audit §5 / #13 DATA01 | Unreadable state.json silently starts empty; no .bak | P1 | open | engine.py:419 | mainnet release only |
| FBL-DATA-02 | Fable audit §5 | Atomic rename without fsync | P2 | open | engine.py:381-384 | mainnet release only |
| FBL-DATA-03 | Fable audit §5 | No SETTINGS_VERSION/migrations | P2 | open | no SETTINGS_VERSION in engine.py/app.py | neither |
| FBL-DATA-04 | Fable audit §5 | /api/settings applies keys one by one | P2 | open | app.py settings handler | neither |
| FBL-DATA-05 | Fable audit §5 | Job worker iterates JOBS.items() during inserts | P2 | needs-runtime-evidence | app.py:232 | neither |
| FBL-DATA-06 | Fable audit §5 | Whole-file rewrites on hot path under lock | P2 | open | engine.py:1525 (missed.json in add gate) | neither |
| FBL-DATA-07 | Fable audit §5 | Live candles re-downloaded every cycle | P3 | open | engine.py:664 | neither |
| FBL-DATA-08 | Fable audit §5 | Preview worker vs cycle race on signals/_kc | P3 | needs-runtime-evidence | app.py preview worker | neither |
| FBL-DATA-09 | Fable audit §5 | Module-global single-account state | P3 | open | app.py globals | neither |
| FBL-SEC-01 | Fable audit §5 | Session token plaintext in session.json / command line | P2 | open | app.py:1253 | mainnet release only |
| FBL-SEC-02 | Fable audit §5 | One-click Telegram chat adoption; PIN optional | P2 | open | panel.html:1940 | mainnet release only |
| FBL-SEC-03 | Fable audit §5 | AI veto fail-open, 60s under lock, brittle parsing | P3 | open | ai_filter.py:35 | neither |
| FBL-SEC-04 | Fable audit §5 | Imported plaintext config.env left on disk | P3 | open | app.py:105-111 | mainnet release only |
| FBL-SEC-05 | Fable audit §5 | CSP allows unsafe-inline | P3 | open | app.py:800-802 | neither |
| FBL-WF-01 | Fable audit §5 | UI harness is a smoke suite, no value checks | P2 | open | test_app_ui.py | neither |
| FBL-WF-02 | Fable audit §5 | Owner is manual relay; status doc mixes trail | P3 | partially-fixed | CLAUDE.md roles; ~/.codex/AGENTS.md pre-approval | neither |
| FBL-CODE-01 | Fable audit §5 | app.py nine concerns, 200-line handle() | P3 | open | app.py | neither |
| FBL-CODE-02 | Fable audit §5 | Stale telegram docstring; /close burns code | P3 | open | telegram_ctl.py:14-35 | neither |
| FBL-OPS-01 | Fable audit §6 / #13 OPS01 | Installed build cannot prove its commit; dirty tree stageable | P1 | open | installer.ps1:342 (BUILD_ID only) | mainnet release only |
| FBL-OPS-02 | Fable audit §6 / #13 OPS01 | Installer hard-kills bot; no drain | P1 | open | installer.ps1:262,269 | mainnet release only |
| FBL-OPS-03 | Fable audit §6 | Exe build and PS 5.1 installer never in CI | P2 | open | .github/workflows (ubuntu-24.04 only) | neither |
| FBL-OPS-04 | Fable audit §6 | Pre-T02 history/tags only on owner PC | P3 | fixed | GitHub tags v2.2..v3.2-rc2 present | neither |
| FBL-OPS-05 | Fable audit §6 | No Dependabot, no pip hashes | P2 | open | requirements*.txt pinned without hashes | mainnet release only |
| FBL-OPS-06 | Fable audit §6 | No identity separation; implementer can add full-ready | P2 | partially-fixed | enforce_admins=true; no CODEOWNERS; label not restricted | mainnet release only |
| FBL-OPS-07 | Fable audit §6 | Public repo exposes username/runtime details | P3 | open | GitHub private=false; docs/reviews/T03_acceptance.md | mainnet release only |
| FBL-OPS-08 | Fable audit §6 | Unsigned onefile exe + 60s ping -> false rollback on AV | P3 | needs-runtime-evidence | installer.ps1 ping wait | neither |
| FBL-OPS-09 | Fable audit §6 | ~4k lines Windows-only deploy code vs Linux VPS plan | P3 | open | installer.ps1; tests/test_installer.py | neither |
| FBL-TEST-01 | Fable audit §6 | Production except TypeError fallbacks for fakes | P2 | open | engine.py:1011,2712 | mainnet release only |
| FBL-TEST-02 | Fable audit §6 | Five independent fake exchanges | P2 | open | tests/test_safety.py:14; tests/test_grid.py:15; replay.py:64; test_app_ui.py:106 | neither |
| FBL-TEST-03 | Fable audit §6 | Strict replay = parity on idealised exchange | P2 | open | replay.py:63-98 | mainnet release only |
| FBL-TEST-04 | Fable audit §6 | Lock-in tests of YAML/source text | P3 | open | tests/test_ci.py | neither |
| FBL-TEST-05 | Fable audit §6 | Mutation proofs unreproducible | P3 | open | no mutation script in repo | neither |
| FBL-TEST-06 | Fable audit §6 | Test counts in docs unexplained | P3 | open | docs/PROJECT_STATUS.md | neither |
| FBL-TEST-07 | Fable audit §6 | Flakiness vectors, no duration tracking | P3 | needs-runtime-evidence | tests/test_fills.py; verify.py | neither |
| FBL-RM-01 | Fable audit §7 | Incident playbook, dead-man, key rotation, kill drill too late | P1 | open | ROADMAP.md (T14 Phase 4; no playbook) | mainnet release only |
| FBL-RM-02 | Fable audit §7 / #13 ACCT01 | Accounting estimated, /fapi/v1/income unused | P1 | open | engine.py:269 FEE_EST | mainnet release only |
| FBL-RM-03 | Fable audit §7 | Live > 6 months out while edge evidence in-sample | P2 | open | ROADMAP.md | neither |
| FBL-RM-04 | Fable audit §7 | T09a/T09b scope creep ahead of T10-T13 | P3 | partially-fixed | #13: T09a paused until BT01/BT02 | neither |
| FBL-RM-05 | Fable audit §7 | Big refactor before event record/accounting | P3 | open | ROADMAP.md:359-366 | neither |
| FBL-PROC-01 | Fable audit §7 | Hand-maintained status docs stale | P2 | open | ROADMAP.md:358; docs/PROJECT_STATUS.md:3-15 (see A-DOC-1/2) | neither |
| FBL-PROC-02 | Fable audit §7 | Effort skewed to tooling/docs | P3 | partially-fixed | ef6abc0 touches backtest.py; engine fixes still on branches | neither |
| FBL-PROC-03 | Fable audit §7 | Loop serialised through owner PC | P3 | partially-fixed | ~/.codex/AGENTS.md standing testnet authorization | neither |
| FBL-PROC-04 | Fable audit §7 | Commit hygiene, authorship as MoeAZack | P3 | open | git log (all commits MoeAZack) | neither |
| FBL-PROC-05 | Fable audit §7 / App.B#2 | T03c above-cap exception has no off switch (default off for mainnet) | P2 | open | engine.py:2044-2070,2199 (no setting) | mainnet release only |
| FBL-CH-01 | Fable audit §7 | Untyped, unlinted code; no ruff | P3 | open | verify.py (compile only) | neither |
| FBL-CH-02 | Fable audit §7 | Three config sources + legacy imports | P3 | open | app.py:105-135 | neither |
| FBL-B1 | Fable audit App.B#1 | Remove grid/COMBO from app | P3 | open | grid.py; superseded by range-sleeve 'retire, keep primitives' | neither |
| FBL-B3 | Fable audit App.B#3 | Freeze T09a/T09b until BT01 + edge go/no-go | P3 | fixed | #13 owner/Claude comments (paused) | neither |
| FBL-B4 | Fable audit App.B#4 | Two-lane process, separate Codex identity | P3 | partially-fixed | CLAUDE.md roles; Codex still posts as owner | neither |
| FBL-B5 | Fable audit App.B#5 | Linux VPS testnet pilot now | P3 | open | ROADMAP T14 not started | neither |
| FBL-B6 | Fable audit App.B#6 | Return repo to private | P2 | open | GitHub private=false | mainnet release only |
| I13-BT02 | #13 / #14 | Backtester lacks stepSize/minQty/minNotional; presets include unplaceable trades | P1 | open | backtest.py:17,268 vs engine.py:2158 live gate | testnet progression |
| I13-COPY | #13 / #15 | COPY100/COPY200 lead/follower profiles | P2 | open | issue #15 (not started) | mainnet release only |
| I13-F1 | #13 TRATE | 418 ban retried; 429 sleeps under engine lock | P1 | open | binance_client.py:179,294 (fix only on origin/cowork/trate) | mainnet release only |
| I13-F2 | #13 handoff note | Backtest keeps zero-qty position when tp1_frac=1.0 | P3 | needs-runtime-evidence | backtest.py:198-210 (frac 1.0 -> qty 0 -> True by reading) | neither |
| I13-ENG0102 | #13 Cowork handoff | ENG01 verifier cancels ENG02 provisional stops (branch-only) | P1 | open | origin/cowork/eng02 tests/test_eng01_eng02_interaction.py; not on master | testnet progression |
| I13-DCAOFF | #13 owner decision 2026-10-07 | DCA OFF decision not on master; DCA presets still selectable | P1 | open | engine.py:300-310 (no DCA_ENABLED); fix on origin/cowork/dca-off | testnet progression |
| I13-UNVER | #13 FBL-BT01 acceptance | DCA preset numbers invalid; only an 'unverified' tag | P1 | partially-fixed | engine.py:305,308,314-319; panel.html:639 table unlabelled | testnet progression |
| FS-ALL | Fable scouting 2026-10-07 | 25 ranked proposals, S1-S5 shorts, G1-G8 gold, TradFi engine prerequisites | P3 | open | triaged on PR #8 / #13; none implemented on master | neither |
| FR-ALL | Fable range sleeve 2026-10-07 | Range/quick-bank sleeve, 1m backtester, maker fills, gate ladder | P3 | open | triaged #13 (RS0 research first); no code | neither |
| A-DOC-1 | this audit | ROADMAP status: T05a 'Planned'; no T05b / FBL-BT01 / BT02 rows | P2 | open | ROADMAP.md:356-358 | neither |
| A-DOC-2 | this audit | PROJECT_STATUS says T05a merge pending / current ticket T05a | P2 | open | docs/PROJECT_STATUS.md:3-15 | neither |
| A-DOC-3 | this audit | Docs say verify full runs on every PR; it is label full-ready only | P3 | open | README.md:48; docs/reviews/README.md:19; docs/PROJECT_STATUS.md:66; .github/workflows/verify-push.yml:5 | neither |
| A-DOC-4 | this audit | Instruction conflict: global CLAUDE.md pauses for installer/drill/bot stop; repo CLAUDE.md + AGENTS.md pre-approve them on testnet | P2 | open | ~/.claude/CLAUDE.md 'ZackBot autonomy'; CLAUDE.md 'Safety boundaries'; ~/.codex/AGENTS.md | neither |
| A-DOC-5 | this audit | verify.py says history scanning is T04b (never added) | P3 | open | verify.py:125 | neither |
| A-DOC-6 | this audit | Fable reports untracked; #13 cites a different PDF name | P3 | open | C:/Dev/ZackBot2/docs/reviews/Fable_* untracked; #13 body | neither |
| A-DOC-7 | this audit | Review workflow names Txx_fix_report.md that is never produced | P3 | open | docs/reviews/README.md:8-14 | neither |

Every Codex P1/P2 item from T03-T05b and FBL-BT01 was spot-checked in code and is fixed; no regression of an accepted
item was found. Everything still open comes from the v3.1 risk list, the Fable audit or #13.

## Detailed findings (open P1, partially-fixed, needs-runtime-evidence)

### HIST-01 DCA presets still advertised with invalidated numbers; owner's "DCA OFF" decision not on master
- Severity: P1
- Status: partially-fixed (label only) / open (DCA-off)
- Evidence class: CONFIRMED
- Origin: #13 FBL-BT01 acceptance; #13 owner decision 2026-10-07; I13-UNVER, I13-DCAOFF, V31-A4
- Files: engine.py:300-310, 314-319; panel.html:639, 665, 1326; app.py:376-378
- Evidence: `ef6abc0` adds `UNVERIFIED_BT01` to `p['bt']` only. Notes still say Active DCA "$500 -> $4,637 ... positive every year incl. late 2022" (engine.py:308) and Steady mix "$4,015, max DD -15%" (:305). The corrected backtester gives DCA-1h $210 / PF 0.80 (#13). The static Research table (panel.html:639) repeats $4,637 and $4,015 with no label (the tag only renders in `btBadge`). panel.html:665 still calls the tight ladder "a candidate". No `DCA_ENABLED` setting exists on master; the fix is only on `origin/cowork/dca-off`. Codex reported the installed testnet bot runs `steady_mix`.
- Impact: the owner and followers see disproven returns; the running testnet bot keeps opening DCA baskets the owner has switched off.
- Fix: merge DCA-off (default off, open baskets keep stop/TP); replace DCA preset `bt`/notes and the panel.html:639 rows with BT01-corrected numbers, or drop them; delete the stale tight-ladder text.
- Tests: preset-note test that no profile with a `dca_dip` slot shows a pre-BT01 `end` value; UI harness checks the Research table label.
- Cowork runtime validation needed: yes (confirm the installed preset and that no new DCA entries open after the change).
- Blocks: testnet progression

### HIST-02 Protective-stop existence is still only inferred (FBL-ENG-01)
- Severity: P1 · Status: open · Evidence class: CONFIRMED · Origin: Fable ENG-01, #13 Priority 1
- Files: engine.py:1567-1570, 1383-1400
- Evidence: `if have >= expected - tol: continue` (1567) runs before `open_stop_tags(sym)` (1570). `_stops_reconfirmed` (1383) runs only after an outage. Same as at Fable's audit (3b86a03). The fix exists only on `origin/cowork/eng01`, and Cowork found that it cancels ENG02 provisional stops unless the interaction fix on `cowork/eng02` comes with it (I13-ENG0102).
- Impact: a stop that was cancelled externally or EXPIRED leaves the position unprotected indefinitely while the panel shows "protected".
- Fix: land ENG01 and the ENG02 interaction fix together, or ENG02 first, so ENG01 is never merged alone.
- Tests: fake exchange: external cancel / EXPIRED-on-trigger → re-placed within one manage pass; `test_eng01_eng02_interaction.py`.
- Cowork runtime validation needed: yes (testnet: cancel a stop in the UI, observe re-placement).
- Blocks: mainnet release only (testnet may continue per #13)

### HIST-03 Zero/partial fills booked as full plan quantity (FBL-ENG-02)
- Severity: P1 · Status: open · Evidence class: CONFIRMED · Origin: Fable ENG-02, #13
- Files: engine.py:2221; engine.py:1196-1226 (`_add_qty`); engine.py:1062-1090 (`_market_close`)
- Evidence: `qty = self._rd(filled, r['step']) if filled > 0 else plan['qty']`. T05 marks telemetry `unknown` (CX-T05-5), but trading state still books the plan quantity.
- Impact: phantom lots, adds or closes, with a stop on a position that does not exist and phantom P&L in Kelly and the daily halt.
- Fix: `cowork/eng02` (r1-r3), after review.
- Tests: EXPIRED/zero/partial MARKET answers on entry, add, partial TP, close and flatten.
- Cowork runtime validation needed: no (deterministic fakes suffice; testnet rarely returns EXPIRED)
- Blocks: mainnet release only

### HIST-04 No order budget; 418/429 handling; transient stop failure closes at market (FBL-ENG-03, I13-F1)
- Severity: P1 · Status: open · Evidence class: CONFIRMED · Origin: Fable ENG-03/ENG-18/ENG-19, #13 TRATE
- Files: engine.py:2252-2256; binance_client.py:179, 294
- Evidence: `STOP FAILED ... closing for safety` → `close_lot` at once, with no bounded retry for transient errors. 418/429 are treated as transient (`binance_client.py:179`), so an IP ban is retried. The TRATE fix exists only on `cowork/trate`.
- Impact: under a lead-key 20/10 s budget, a burst can round-trip entries (two taker fees) or deepen a ban.
- Fix: merge TRATE, then T10a (state machine + priority budget).
- Tests: burst of 20 writes/10 s; 418 is never retried; no sleep while the lock is held.
- Cowork runtime validation needed: no
- Blocks: mainnet release only

### HIST-05 Management and emergency controls serialised behind one lock (FBL-ENG-08 / UI-01)
- Severity: P1 · Status: open · Evidence class: CONFIRMED · Origin: Fable ENG-08, UI-01; #13 CONC01
- Files: engine.py:1652-1653 (`cycle` holds the lock), 2696, 2723 (move_stop/flatten take it); app.py:586 (`time.sleep(60)` after a cycle failure); ai_filter.py:35 (`timeout=60`)
- Evidence: unchanged since v3.2. T05a adds per-mark work in `manage()`, but it is bounded.
- Impact: Flatten, Close, trailing and DCA management can be delayed by minutes. Exchange stops still bound the loss.
- Fix: decide outside the lock, use a snapshot read model, pre-empt for flatten (T06-T09 seam).
- Tests: harness check that `/api/status` and `flatten` answer within 2 s during a slow cycle.
- Cowork runtime validation needed: yes (measure the lock-hold time per cycle on testnet)
- Blocks: mainnet release only

### HIST-06 Persistence: silent empty start on corrupt state, no fsync (FBL-DATA-01/02, ENG-14)
- Severity: P1 · Status: open · Evidence class: CONFIRMED · Origin: Fable DATA-01/02, #13 DATA01
- Files: engine.py:419 (`log.warning('state.json unreadable - starting empty')`); engine.py:381-384 (`save_json` with no fsync and no .bak)
- Impact: after a power loss the bot can forget every lot while positions and stops stay on Binance. Reconcile then reports untracked positions, and they are left unmanaged.
- Fix: fsync + `.bak` + refuse to trade (pause entries, alert) on unreadable non-empty state.
- Tests: corrupt or zero-length `state.json` with live fake positions → entries paused, alert raised, no empty start.
- Cowork runtime validation needed: no
- Blocks: mainnet release only

### HIST-07 Close paths ignore pending markers and resting makers (FBL-ENG-04/07/09)
- Severity: P1 · Status: open · Evidence class: CONFIRMED · Origin: Fable ENG-04, ENG-07, ENG-09
- Files: engine.py:1136-1146 (`close_lot`: no `pending` check; closes `_positions_critical()` qty incl. untracked); engine.py:2720-2737 (`flatten` never cancels resting maker entries)
- Impact: a duplicate close or a reversed position after a lost reply, and a maker entry that fills after Flatten.
- Fix: refuse close while `pending`; cancel makers on halt/pause/flatten; close only tracked qty.
- Tests: close/flatten while pending; flatten with a resting maker.
- Cowork runtime validation needed: no
- Blocks: mainnet release only

### HIST-08 Backtests include trades the exchange cannot place (I13-BT02 / FBL-BT-16)
- Severity: P1 · Status: open · Evidence class: CONFIRMED · Origin: #13 Codex finding, #14
- Files: backtest.py:17, 268 (min-notional table only, no stepSize/minQty); live gate engine.py:2158
- Impact: preset and COPY100/200 numbers overstate what small accounts can do. Codex observed broad DCA skips on testnet.
- Fix: `bt02-exchange-filters` (in review). Rerun every advertised preset.
- Tests: feasibility parity (backtest vs live gate) on a versioned exchangeInfo snapshot.
- Cowork runtime validation needed: yes (testnet exchangeInfo snapshot)
- Blocks: testnet progression (COPY calibration depends on it)

### HIST-09 Estimated accounting feeds guards, Kelly and drift gates (FBL-ENG-05/11, RM-02)
- Severity: P1 · Status: open · Evidence class: CONFIRMED · Origin: Fable ENG-05, ENG-11, RM-02; #13 ACCT01
- Files: engine.py:269 (`FEE_EST = 0.0005`), 1087, 1220; engine.py:2134 (`risk_usd = sleeve_eq * risk`, before the leverage cap trims qty at 2155) and 2234
- Impact: the daily halt, compounding, Kelly and R drift from exchange truth. The Phase-6 live-vs-backtest gate cannot be measured.
- Fix: reconcile closed lots to `/fapi/v1/income`; store the capped risk.
- Tests: leverage-cap-binding entry stores capped `risk_usd`; income reconciliation with a fake.
- Cowork runtime validation needed: yes (compare against testnet income)
- Blocks: mainnet release only

### HIST-10 Build provenance and hard-kill install (FBL-OPS-01/02)
- Severity: P1 · Status: open · Evidence class: CONFIRMED · Origin: Fable OPS-01/02; #13 OPS01
- Files: installer.ps1:342 (`build_info.py` = timestamp only), 262, 269 (`Process.Kill()`)
- Impact: an install cannot prove which commit is running (a dirty tree can be staged). The kill leaves a 1-2 min unmanaged window with no in-flight drain. Testnet installs are now pre-approved and frequent.
- Fix: commit/dirty/manifest in `build_info`; refuse dirty trees; `prepare-stop` API before the kill.
- Tests: installer unit test refusing a dirty tree; `/api/status` shows the commit.
- Cowork runtime validation needed: yes (one testnet install)
- Blocks: mainnet release only

### HIST-11 Ops minimum absent: heartbeat, kill switch, playbook, 24/7 (V31-R7/R8, FBL-RM-01)
- Severity: P1 · Status: open · Evidence class: CONFIRMED (absence) · Origin: v3.1 §5.7-5.8, Q8; Fable RM-01
- Files: ROADMAP.md:367 (T14 not started); no heartbeat or dead-man code in engine.py/app.py/telegram_ctl.py
- Impact: an unattended bot on a home PC has no external alarm.
- Fix: Telegram dead-man heartbeat, a written incident playbook, a testnet kill-switch drill.
- Tests: heartbeat-missed alert unit test.
- Cowork runtime validation needed: yes (kill-switch drill)
- Blocks: mainnet release only

### HIST-12 Replay/causality coverage only partly extended (FBL-BT-02/03, V32-Q4)
- Severity: P2 · Status: partially-fixed · Evidence class: CONFIRMED · Origin: Fable BT-02/03; v3.2 Q4
- Files: test_engine_sim.py:25 (strict gate still 4h, DCA at 30% share); tests/test_bt_intrabar_path.py:287 (hand-built DCA replay); tests/test_causality.py:97-99, 122 (tolerance unchanged)
- Impact: a 1h DCA-dominant mix (the one BT-01 broke) is still not in the strict gate.
- Fix: add a 1h DCA-dominant strict replay scenario on `data1h/`.
- Tests: the scenario itself, at 24 steps.
- Cowork runtime validation needed: no
- Blocks: neither

### HIST-13 T03c exception always on; ordinary-maker leverage race (FBL-PROC-05, CX-T03C-15, ENG-13)
- Severity: P2 · Status: open / partially-fixed · Evidence class: CONFIRMED · Origin: Fable PROC-05, App.B#2; T03c round 3
- Files: engine.py:2044-2070, 2199 (no setting gates the exposure path); engine.py:1787-1796 (marginType POST on every non-cooldown entry)
- Fix: an `ABOVE_CAP_EXCEPTION` setting, default off for mainnet; re-prove leverage on a maker fill.
- Tests: setting off → refusal path skips as in T03a.
- Cowork runtime validation needed: no
- Blocks: mainnet release only

### HIST-14 Partially-fixed hygiene items (grouped)
- Severity: P2 · Status: partially-fixed · Evidence class: CONFIRMED
- CX-T04-5 / CX-T04B-2: secret scan covers the current tree at any depth (verify.py:108-125), but there is no git-history or dependency scan, and verify.py:125 still says "Git-history scanning is T04b".
- FBL-OPS-06: `enforce_admins=true`, required [verify fast, verify full], no force-push (GitHub API). There is no CODEOWNERS, and any writer can add `full-ready`.
- V31-R2 / V31-R3: T05 measures real fills and BT01 declares one path, but the maker fill model (backtest.py:439-449) and the absence of detail candles are unchanged.
- FBL-WF-02, FBL-PROC-02/03, FBL-RM-04, FBL-B4: process improved (lanes, standing testnet authorization, T09a paused), but Codex and Claude still post as the owner.
- Blocks: mainnet release only (security scan, identity); otherwise neither

### HIST-15 Status docs stale and instructions contradict (A-DOC-1..7, FBL-PROC-01)
- Severity: P2 · Status: open · Evidence class: CONFIRMED · Origin: Fable PROC-01; this audit
- Files: ROADMAP.md:356-358 (T05a "⬜ Planned"; no T05b, FBL-BT01 or BT02 rows); docs/PROJECT_STATUS.md:3-15 (says T05a merge pending); README.md:48, docs/reviews/README.md:19, docs/PROJECT_STATUS.md:66 ("verify full on every PR"; actual: `full-ready` label, verify.yml:19-22); verify-push.yml:5 ("dispatched full")
- Contradiction: `~/.claude/CLAUDE.md` says to pause "when an installer or rollback drill must run, ZackBot would be stopped". Repo `CLAUDE.md` ("On testnet, don't ask the owner for approvals for installs, restarts...") and `~/.codex/AGENTS.md` pre-approve those actions.
- Impact: agents act on a stale queue, and approval behaviour differs between Claude sessions.
- Fix: owner reconciles the global rule; Codex regenerates the ROADMAP/status tables from the GitHub API; add a tripwire test for workflow names in docs.
- Cowork runtime validation needed: no · Blocks: neither

### HIST-16 Needs runtime evidence (grouped)
- V31-A6 Telegram chat id (the warning exists at panel.html:1940; the PC config was not readable here). V31-R1 testnet ≠ mainnet fills: T05 `fills.jsonl` now holds data, but no calibration exists. FBL-ENG-10 same-cid resend race. FBL-DATA-05/08 thread races (app.py:232 iterates `JOBS.items()` unsnapshotted). FBL-BT-11 worst-mode rows after BT01. FBL-OPS-08 AV false rollback. FBL-TEST-07 flakiness. I13-F2: by reading, `close(frac=1.0)` sets qty to 0 and returns True (backtest.py:198-210), so the reported zero-qty position was not reproduced; a run is needed.
- Severity: P2/P3 · Evidence class: SPECULATIVE until run · Blocks: neither (ENG-10: mainnet release only)

## Behaviour changes since v3.2-rc2 (summary)
- Trading path: T03a/T03c leverage fallback and above-cap exception; T05b outage circuit blocking entries/adds and the startup reconnect; T05 telemetry writer; T05a audit hooks in `manage()`. No change to signals, sizing, stops-on-entry or the exit logic.
- Backtester: FBL-BT01 path policy (DCA results drop sharply; trend sleeves change a little). `replay.py` follows the same path.
- Deployment/CI: PowerShell installer with verified rollback; fail-closed verify levels; label-gated full CI.
- UI: refusal/outage/audit cards, and the `unverified` tag on DCA presets.

## Not verified
- Runtime state of the installed testnet bot (preset, Telegram chat id, open lots). GitHub CI run history. The Windows-only tests.
- Contents of the `cowork/*` and `bt02-exchange-filters` branches beyond the #13 description (fixes assumed unmerged, confirmed absent on master).
- Research claims (BT-04/05/06/07): not re-run. Fable's reproduction numbers are taken from #13 (independently reproduced there by Claude).
- Line-level re-check of Fable Low items: status rests on spot greps, and some cited lines are approximate.


# Appendix B — Trading core

_Evidence pass B (read-only); integrated unchanged. Severity and status are proposals for Codex review._

## B Trading core: orders, exits, DCA/pyramid, sizing

Scope: `engine.py` (master ef6abc0) order lifecycle, reconcile, stops/exits, adds, sizing; `binance_client.py` order helpers; `grid.py` live paths. Baseline `v3.2-rc2`. The owner's Fable audit (`C:/Dev/ZackBot2/docs/reviews/Fable_audit_2026-10-07.md`, IDs "Fable ENG-xx") already covers much of this area. IDs below are `ENG-Bxx` so they don't collide with it. New findings come first. The status of the Fable items in this area follows at the end.

Reproductions ran in-process against `tests/test_safety.py::FakeX` with `python -B` and a scratchpad TMP. No repo files were written.

### ENG-B01 A failed `trades.csv` write after a fill repeats partial closes and pyramid/DCA adds every pass, and leaves the extra size without a stop
- Severity: P0
- Status: open (pre-existing in v3.2-rc2)
- Evidence class: CONFIRMED (reproduced)
- Origin: new (this audit)
- Files: engine.py:1081-1093 (`_apply_close`), 1214-1224 (`_apply_add`), 720-725 (`log_trade`), 1285-1288, 1303-1313, 1318-1329 (manage), 1581-1586 (reconcile stop path)
- Evidence: `_apply_close`/`_apply_add` mutate `lot['qty']`/`avg`/`fills` and only then call `log_trade()`, which is a synchronous `open(trades.csv,'a')`. The caller's bookkeeping runs after `_market_close`/`_add_qty` returns: `lot['tp1']=True`, `tps_done`, `lot['adds']+=1`, `next_add`, `lot['dca']+=1` and `_replace_stop(lot)`. So does the stop re-size. If `log_trade` raises (on Windows, Excel holds an exclusive lock on an open CSV; AV, OneDrive or a full disk can do the same), the exception skips all of it. The per-lot `except` at 1359 swallows it, and the next pass fires the same event again. The `post=` dict is applied only on the AmbiguousOrder path.
  Repro with `log_trade` raising PermissionError, 4 manage passes:
  - tp1 50% fired 4 times. The position went 1.0 → 0.063 and `tp1` stayed False.
  - A pyramid with `n=1` sent 5 opens. The position went 1.0 → 3.0 and `adds` stayed 0. **The exchange stop still covers 1.0**, so 2.0 is unprotected. `_add_block` does not catch this because `stop_dirty` was never set. Only the slot leverage cap ends the loop.
  - In `reconcile()`, `log_trade` runs before `_finish` (1581). A failure there raises out of reconcile, and `manage()` returns at 1245-1250 before managing ANY lot. All trailing stops and exits freeze while the file is locked.
- Impact: real money. Repeated taker closes of a winner, and unbounded unprotected averaging-up, triggered by an ordinary desktop action (the owner opening trades.csv in Excel).
- Fix: do the bookkeeping before the exchange call, or apply the `post` dict before any I/O. Wrap `log_trade` (and `save_json` for history/missed) in try/except that queues or logs and never raises into an order path, the same way T05 telemetry does. After any exception in an add or partial-close step, set `stop_dirty=True` so that adds stop and the stop is re-placed.
- Tests: fake `log_trade`/`open` raising PermissionError during tp1, ladder, pyramid add, DCA safety order and reconcile stop. Assert exactly one exchange order per event, the stop qty equals the position, and other lots are still managed.
- Cowork runtime validation needed: yes. Open trades.csv in Excel on a testnet box during a partial TP to confirm the lock behaviour.
- Blocks: testnet progression

### ENG-B02 A failing add starves all later exit management of that lot (trailing, BE, tp1, ladder, tp_r)
- Severity: P1
- Status: open (pre-existing; extends Fable ENG-21, which only covers "retried forever")
- Evidence class: CONFIRMED (reproduced)
- Origin: new angle on Fable ENG-21
- Files: engine.py:1277-1360 (one try block per lot), 1303-1308 (pyramid), 1282-1288 (DCA)
- Evidence: management for a lot runs in a fixed order: stop retry, DCA, pyramid, tp1, ladder, tp_r, `best`, stop ratchet. It all sits in ONE `try`. `ge(next_add)` stays true while price keeps running. A business rejection on the add (e.g. -2019 margin insufficient, -4164, -1111) is raised every pass from `_add_qty`, so nothing after it ever runs. Repro: a trail_atr=1.0 lot with pyramid, `open` failing, price 100 → 130. The stop stayed at 95.0 and `best` stayed at 100. Once the add succeeded, the stop jumped to 128.
- Impact: a pyramid winner that hits margin limits keeps its initial stop, and the whole run-up can be given back. The 6-failure Telegram alert fires, but nothing self-heals.
- Fix: give each management stage its own try/except, or run exits and the stop ratchet BEFORE adds. On a non-transient add rejection, set `add_blocked` with a cooldown (Fable ticket for ENG-21).
- Tests: pyramid plus trail with `open` raising -2019. Assert the stop is ratcheted on every pass.
- Cowork runtime validation needed: no
- Blocks: testnet progression

### ENG-B03 `_resolve_pending` reports any unanswered order of ≤ (lots+1) steps as filled, booking a phantom close/add and leaving size unprotected
- Severity: P1
- Status: open (pre-existing)
- Evidence class: CONFIRMED (reproduced)
- Origin: new (this audit)
- Files: engine.py:1095-1119, 1556-1560
- Evidence: `tol = step*(len(keys)+1)`. The "filled" test `abs(have - (expected ∓ qty)) <= tol` runs BEFORE the "never filled" test `abs(have - expected) <= tol`. When the pending qty is ≤ tol, both are true, so the order is always classified as filled, even when it never reached Binance. Repro: lot 1.0 BTC (step 0.001) with a pending tp1 close of 0.001 that did not fill. Reconcile set lot=0.999 and `tp1=True` and re-sized the stop to 0.999, while the exchange still held 1.0. Untracked stayed `{}` because the 0.001 excess is inside tol, so the gap is never reported. With ~$500 capital this is realistic: BTC tp1 halves are often 1-2 steps.
- Impact: a phantom partial TP enters history, Kelly and the guards, and the residual size has no stop. The same happens for a pending add (it books size that was never bought).
- Fix: when `qty <= tol`, don't decide from the position. Resolve from `get_order(cid)` (the AmbiguousOrder tag carries the cid), or wait for a second confirming read and the 20 s age. Never let the two tests overlap: require `abs(have-target) < abs(have-expected)`.
- Tests: pending close/add of 1 step with have==expected. Expect "not filled" after 20 s.
- Cowork runtime validation needed: no
- Blocks: testnet progression

### ENG-B04 An unconfirmed market entry that filled is never adopted, and while the bot is flat it isn't even looked for until 2 candle cycles later
- Severity: P1
- Status: open (pre-existing)
- Evidence class: CONFIRMED (code)
- Origin: new (Fable ENG-19 covers only the ambiguous stop)
- Files: engine.py:2207-2213 (`_market_entry`), 1549-1551, 1608-1618 (untracked needs 2 sightings), app.py:598 (manage only if lots/grids/pending/resting exist), engine.py:1657 (cycle reconcile)
- Evidence: on `AmbiguousOrder` (raised after ~10 s of failed cid lookups), `_market_entry` returns False and creates no lot and no stop ("reconcile will flag it"). If the bot has no other lot, `app.loop` skips `manage()`, so the only `reconcile()` comes from `cycle()`. The untracked alert needs `over_seen >= 2`, which on a 4h slot means up to ~8 h of an unprotected position before the first alert. Even then it is only reported, never adopted or stopped. `grid._resolve_open` (grid.py:783-797) shows the adopt pattern already exists for grids.
- Impact: an unprotected position for hours, discovered by a human.
- Fix: keep a `pending_entry` record with the cid and plan. Resolve it on the next manage pass with `get_order(cid)`, and on FILLED create the lot and stop (reuse `_create_lot`). Run `manage()` while any pending entry record exists.
- Tests: `open` raising AmbiguousOrder with the position present. Expect a lot plus a stop within one manage pass.
- Cowork runtime validation needed: no
- Blocks: testnet progression

### ENG-B05 The exit path, the panel/Telegram close and flatten can close another slot's resting-maker fill or a grid's inventory
- Severity: P2
- Status: open (a variant of Fable ENG-09)
- Evidence class: CONFIRMED (code)
- Origin: Fable ENG-09
- Files: engine.py:1136-1146, 1545-1547, 1759-1761
- Evidence: when no other *lot* exists on the same symbol/side, `close_lot` closes `positions()[(sym, side)]`. That includes untracked size and the filled part of another slot's working maker entry (entry_block de-duplicates maker entries only per slot, 1759-1761). Later, `_maker_finalize` creates a lot and stop for size that is already gone, and the next reconcile books it as a `resync` at mark.
- Fix: close `min(lot qty + dust, exchange qty − resting fills − untracked)`, and close the exchange quantity only when nothing else is attributable.
- Tests: slot A lot plus slot B partial maker fill on the same coin/side, then an exit on A.
- Cowork runtime validation needed: no
- Blocks: mainnet release only

### ENG-B06 The cycle exit, panel/Telegram close and flatten ignore `lot['pending']` (Fable ENG-04), and a multi-lot pending close can eat a neighbour's size
- Severity: P2
- Status: open
- Evidence class: CONFIRMED (code)
- Origin: Fable ENG-04
- Files: engine.py:1686-1688, 2725-2728, 1136-1146; app.py:1213-1215
- Evidence: `manage` skips pending lots (1269), but the cycle exit and the close handlers call `close_lot` directly. If the earlier pending close had filled and other lots share the side, `close_lot` sends `lot['qty']` (now too large). In hedge mode that size is taken from the sibling lot's position, and the sibling's stop then over-covers.
- Fix: `close_lot` refuses (or defers) while `pending`, as the Fable audit recommends.
- Tests: pending close plus exit signal with 2 lots on the side.
- Cowork runtime validation needed: no
- Blocks: testnet progression

### ENG-B07 No routine stop-existence check (Fable ENG-01); T05b added a report-only check after outages
- Severity: P1
- Status: partially-fixed (only after an outage, and it reports without re-placing)
- Evidence class: CONFIRMED
- Origin: Fable ENG-01
- Files: engine.py:1563-1567 (open orders read only when the position shrank), 1383-1410 (`_stops_reconfirmed`: report only), 1280 (re-place only if `stop_dirty` or no `stop_id`)
- Evidence: an externally cancelled or expired stop, with the position unchanged, is never noticed in normal running. Only the T03c exposure proof (1928-1929) would refuse a *new* above-cap entry.
- Fix: every N passes, for each held symbol, read `open_stop_tags`. If a `stop_id` is missing while the position matches, set `stop_dirty=True` (manage re-places it) and alert.
- Tests: fake stop removed with the position unchanged. Expect the stop re-placed within one pass.
- Cowork runtime validation needed: yes (testnet: cancel a stop in the Binance UI)
- Blocks: testnet progression

### ENG-B08 Fill quantities are ignored on the success path (Fable ENG-02)
- Severity: P1
- Status: open
- Evidence class: CONFIRMED
- Origin: Fable ENG-02
- Files: engine.py:2216-2221 (zero fill books the full plan), 1203-1211 (`_add_qty` books the requested `q`), 1072-1079 (`_market_close` books `qty`); binance_client.py:341-347 (FILLED is enforced only on the lookup path)
- Fix/Tests: as in the Fable ticket. FakeX.close returns no `executedQty`, so add EXPIRED/partial fakes.
- Cowork runtime validation needed: no
- Blocks: testnet progression

### ENG-B09 A transient stop failure on entry triggers an immediate market close (Fable ENG-03/19)
- Severity: P2
- Status: open
- Evidence class: CONFIRMED
- Files: engine.py:2253-2261, 1041-1045 (an AmbiguousOrder on the first stop makes `_replace_stop` return False, and `close_lot` follows while the stop may exist and is only queued in `orphans`)
- Fix: retry the stop for a bounded window on transient errors, and resolve an ambiguous stop by cid before closing.
- Cowork runtime validation needed: no
- Blocks: mainnet release only

### ENG-B10 Same-cid resend after `-2013` can double-fill (Fable ENG-10)
- Severity: P1
- Status: open (unchanged since v3.0: `git diff v3.2-rc2 -- binance_client.py` leaves `_order` untouched)
- Evidence class: SPECULATIVE (depends on Binance order-visibility lag)
- Files: binance_client.py:329-340
- Evidence: after a POST timeout, `get_order` runs at +1 s. If it returns -2013 (not visible yet), the order is re-POSTed with the same cid. A filled MARKET order's cid is not "open", so Binance does not reject the duplicate.
- Fix: before resending, require ≥ 2 not-found answers spread over ≥ 5 s, and for MARKET entries prefer AmbiguousOrder plus ENG-B04 adoption over a resend.
- Cowork runtime validation needed: yes (testnet latency injection)
- Blocks: mainnet release only

### ENG-B11 One-way account: every order fails (-4061), not just shorts (Fable ENG-12)
- Severity: P2
- Status: open
- Evidence class: CONFIRMED (code); the Binance code itself is SPECULATIVE
- Files: engine.py:533-540, 1709, 1748; binance_client.py:493-506, 2553 (`positionSide` is always sent)
- Fix: refuse all entries with a clear reason while `hedge` is False, and re-check hedge mode when the bot is flat.
- Blocks: neither

### ENG-B12 Recorded `risk_usd` is the uncapped plan risk (Fable ENG-11); Kelly×governor ceiling (Fable ENG-06)
- Severity: P2
- Status: open
- Evidence class: CONFIRMED
- Files: engine.py:2133-2134, 2156-2157, 2197, 2234 (`risk_usd` stored before the leverage-cap/step scaling; the risk-rule check correctly uses `risk_usd*qty/qty_raw` at 2163, so R, Kelly and the history R are understated when the cap binds), 1126-1134, 2463-2471 (Kelly up to 4× from app.py:1036, times governor 2× = 8× slot risk; all portfolio rules default to `warn`, 330-336)
- Fix: store `risk_usd*qty/qty_raw`, and clip `kelly*gov` at 1.0 for automatic changes.
- Blocks: mainnet release only

### ENG-B13 Partial TPs: rounding to zero is marked done (Fable ENG-15); the dust rule exists only for the ladder
- Severity: P3
- Status: open
- Evidence class: CONFIRMED (code)
- Files: engine.py:1066-1067 (qty → 0 returns 0.0), 1310-1311 (`tp1=True` anyway), 1292-1296 (basket_tp_part has no dust rule), 1321-1323 (only the ladder keeps no dust)
- Impact: with small lots, tp1 can bank nothing, or leave a remainder below `min_notional` that later stops/closes may refuse (SPECULATIVE in hedge mode).
- Fix: one shared `leaves_dust()` (the BT02 branch has one in `feasibility.py`) used for tp1, basket_tp_part and the ladder. When the partial rounds to 0, skip it without marking it done, or close all.
- Blocks: neither

### ENG-B14 Duplicated rule implementations (engine vs backtest vs grid; BT02 would add a third)
- Severity: P2
- Status: open
- Evidence class: CONFIRMED
- Origin: new (context)
- Files:
  - risk sizing and DCA basket sizing: engine.py:2132-2157 vs backtest.py:253-266
  - open-risk incl. unfilled DCA: engine.py:2296-2303 vs backtest.py:221
  - stop ratchet (BE/trail/ttp/runner): engine.py:1334-1358 vs backtest.py:295-313
  - add gates: engine.py:1474-1503 vs backtest.py:353-363, 399-402
  - basket runner reset: engine.py:1291-1300 vs backtest.py:390-395
  - grids use their own add gate: grid.py:654-661, which bypasses `_add_block`'s slot cap and risk rules
  - origin/bt02-exchange-filters `feasibility.py` re-implements step/min-qty/dust/size_check (`risk_qty`, `size_check`, `leaves_dust`) while the engine keeps inline `_rd` and min checks (engine.py:2157-2162, 1199, 1321-1323)
- Evidence: the copies already differ:
  - The engine moves a runner's stop to BE only if price is beyond BE (1298); the backtest tightens unconditionally (394).
  - The engine's DCA cap uses `used` at avg prices plus `last_eq*share`; the backtest uses `notional(sl,i)` plus `eq`.
  - The engine floors the stop to the tick (1037, 2228); the backtest does not.
- Fix: one pure `sizing`/`ratchet`/`add_gate` module imported by engine, backtest and feasibility (fits T09's decide/act split). Add parity tests per rule rather than only end-to-end replay.
- Cowork runtime validation needed: no
- Blocks: neither

### ENG-B15 Minor
- `_add_qty` books `px=mark` for a fill only when `avgPrice` is missing, and grid `_step` reads `lot['fills'][-1][3]`. Both are fine but depend on ENG-B08. (P3, CONFIRMED)
- `cycle()` has dead code `or (sl is None and False)` (engine.py:1684), so a lot whose slot was removed gets no time exit unless `_orphan_sleeves` matches. (P3, CONFIRMED)
- `_rd` floors the stop for both sides, so a LONG stop is ≤1 tick looser than declared risk. Negligible. (P3)
- `manage` skips the stop-restore retry for a lot whose symbol has no mark (1263-1264); a delisted or renamed symbol is never re-protected. (P3, SPECULATIVE)
- A stale old stop (cancel parked in `orphans`) plus a new lot on the same coin/side: the orphan can close the new lot's size if it triggers before the retry succeeds. (P3, SPECULATIVE; window ≈ one manage pass unless the cancel keeps failing)

### Prior-audit (Fable) items in this area, current status
- ENG-01 partially-fixed (B07).
- ENG-02, ENG-03, ENG-04 open (B08, B09, B06).
- ENG-06 and ENG-11 open (B12).
- ENG-07 open: the maker re-price at 2598-2599 has no `entry_block`, and halt/pause/flatten do not cancel resting entries.
- ENG-09 open (B05).
- ENG-10 open (B10).
- ENG-12 open (B11).
- ENG-13 open, slightly worse: T03c adds a `margin_state` read even on a cache hit (1786-1790).
- ENG-15 open (B13).
- ENG-21 open (B02 shows the worse consequence).

### Behaviour changes since v3.2-rc2 (trading core)
- **Leverage (T03a/T03c, 3eabdbc/f9778ca):**
  - A refused leverage change no longer always skips the entry. It proceeds if Binance already has the coin at ≤ cap (`within_cap`), or under CROSS margin if a full fresh-snapshot exposure proof passes (`exposure`, 1850-2032).
  - Exception entries go market-only and are flagged `lev_exception`. Adds, grid adds and the maker remainder are then paused until the leverage is back within the cap (2098-2118).
  - A 30-min refusal cooldown applies, and every entry re-reads `margin_state` even when cached.
  - Older resting maker records with `lev_exception` are cancelled without a fallback (2578).
  - The fail-closed design looks sound. Every unknown raises LevReject.
- **Outage (T05b, 3b86a03):**
  - New entries, adds and grid adds are blocked while the circuit is in `outage` (1482, 1745, grid.py:657).
  - Sizing reads for the close quantity, order confirmations and the manual-stop mark are `critical` and bypass the circuit.
  - A reconcile failure during an outage becomes one incident.
  - On recovery, stops are re-read, but only reported (B07).
  - `move_stop` now refuses when no fresh mark is available.
- **Telemetry and audit (T05/T05a):** observe-only hooks in the order paths. They are wrapped and never raise (737-766, 830-839). The trading logic is unchanged apart from the `_last_order` bookkeeping.
- **No change in this area:** `_replace_stop`, `_market_close`, `_add_qty` ordering, `reconcile` resize/stop logic, `_resolve_pending`, DCA/pyramid sizing and gates, exit ordering in `manage`, and `binance_client._order` are all unchanged, so B01-B06 and B10 were already in v3.2-rc2. FBL-BT01 (ef6abc0) changed only the backtester's intrabar path. The engine was not touched, which widens the B14 divergence surface.

### Not verified
- Binance behaviour I could not test offline:
  - Excel's lock on trades.csv on the owner's machine (B01 trigger frequency).
  - Order-visibility lag after a POST timeout (B10).
  - `-4061` in one-way mode (B11).
  - Whether the algo-order endpoint returns `-2021` for an immediately-triggering stop (`_replace_stop` only special-cases `-2021`).
  - Whether a hedge-mode stop larger than the remaining position is rejected at trigger after a partial close whose stop re-size failed.
  - Whether hedge-mode closes below `min_notional` are accepted.
- `TA.observe` adds tracking fields to every lot in `state.json`. I did not measure the state growth or the save latency per pass.
- Locking: all panel/Telegram mutators I found take `engine.lock` (app.py:1184-1215, 1045, 1100; telegram_ctl.py:416-592). `app.py:710` calls `capital_info()` without the lock (read-mostly). I did not audit the rest of app.py.


# Appendix C — Backtest/live parity and causality

_Evidence pass C (read-only); integrated unchanged. Severity and status are proposals for Codex review._

## C Backtest/live parity, causality, research validity

Scope read: backtest.py, replay.py, lab.py, strategies.py, load_data.py, research_refresh.py/research_long.py (headers),
DATA_MANIFEST.json + data/, data1h/, data_long/, tests/test_causality.py, tests/test_bt_intrabar_path.py, tests/test_lab.py,
test_engine_sim.py, verify.py replay gate; engine.py candle/cycle/manage/open_lot/regime/breaker paths; app.py run_backtest_job,
get_candles, lab_book. Cross-checked against the owner's Fable audit (BT-01..BT-20) so prior items are given a status, not
re-reported. Experiments run read-only with the audit venv (synthetic replays / backtests in memory; no files written).

### BT-C1 Time exit (`max_bars`) is off by one candle between engine and backtester; live timing is jittery
- Severity: P1
- Status: open
- Evidence class: CONFIRMED (ran)
- Origin: new (this audit)
- Files: engine.py:1672, 1684; backtest.py:493 (p['i'] = fill bar, set in open_pos backtest.py:273)
- Evidence: engine `bars = (now_utc() - opened)/TF_SEC; closing ... bars >= max_bars` -> with `opened` = the fill (start of
  candle i_in) the engine exits at the close of candle i_in+max_bars-1 (holds max_bars candles). Backtester exits when
  `i - p['i'] >= max_bars` at candle close -> close of candle i_in+max_bars (holds max_bars+1 candles). Synthetic replay
  (test_causality.synth seed 6, donchian_ens both, stop 6 ATR, no trail, max_bars=4): engine holds exactly 4.0 candles on
  45/45 trades; backtester `i_out - i_in == 4` (5 candles) on 43/43; matched 93.3 %, median |dR| 0.066, ret gap 3.3 pp ->
  fails 3 STRICT targets. Same config without max_bars: 100 % matched, median 0.018. In live the comparison uses wall-clock
  seconds: if the entry cycle ran later after the candle close than the exit cycle (order latency, AI review, slow cycle),
  `bars` = 3.999 and the exit slips to the next candle - so live is nondeterministic between the two conventions.
- Impact: every preset with a DCA slot (dca_dip max_bars=60) and pullback_rsi (60) exit a candle earlier live than in the
  backtests that justified them; any user-set small max_bars diverges strongly. Escaped CI because no replay/causality
  mode exercises a time exit that actually fires (see BT-C9).
- Fix: count closed candles, not seconds: store the entry candle open time on the lot and compare candle indices
  (`floor((now - entry_candle_open)/tf)`), then pick ONE convention (desc says "time exit after N candles") in both models.
- Tests: add a `max_bars` mode to test_causality.MODES (fast set) asserting STRICT parity; unit test with an entry cycle
  delayed by 30 s.
- Cowork runtime validation needed: no (deterministic in replay)
- Blocks: testnet progression (presets' DCA slot behaviour differs from its evidence)

### BT-C2 Maker entries are filled before the candle reaches them: same-candle lookahead on targets/trails
- Severity: P1 (option off by default; available in Lab/backtest run_options and research_refresh v3.1 tables)
- Status: open
- Evidence class: CONFIRMED (ran)
- Origin: new; extends Fable BT-08 (which covers fill *probability*, not path order)
- Files: backtest.py:440-450 (maker fill), 469 (`skip_i` only for trailing entries), 476-485 (same-candle walk from the open)
- Evidence: a maker long fills at the previous close `lim` when `l[i] < lim`, at `min(lim, o)`. The position is then walked
  from the candle OPEN along the full path. On a red candle with o > lim (o -> h -> l -> c) the fill can only have happened
  on the down leg, but the walk books targets on the earlier up leg. Reproduced: prev close 100, candle o=101 h=106 l=99.5
  c=99.8, tp_r 1 (target 102): `entry_order='maker'` books `tp` +0.95 R in the fill candle; the candle closes below the
  entry, so the honest outcome is an open trade at about -0.1 R. `best` is also raised to the pre-fill high, so trails ratchet
  off a price the position never saw.
- Impact: any maker-mode backtest, optimisation or research row (research_refresh v3.1 table) is biased upward on top of BT-08.
- Fix: for a maker fill with o beyond the limit, start the walk at the fill point (only path legs after the limit is
  touched), or mark it `skip_i` like trailing entries; keep BT-08's calibration work separate.
- Tests: extend test_bt_intrabar_path with the case above (expect no exit in the fill candle).
- Cowork runtime validation needed: no
- Blocks: neither (option off by default) - must be fixed before any maker preset or maker research is quoted

### BT-C3 DCA safety orders and pyramid adds pay no slippage in the backtester (entries/exits do)
- Severity: P2
- Status: open
- Evidence class: CONFIRMED
- Origin: new (this audit); test locks it in
- Files: backtest.py:361-362 (DCA add at `lvl`, fee only), 404-405 (pyramid add at `lvl`, fee only); compare 449 (entry
  `o*(1+side*SLIP)`), 201 (exits `px*(1-side*SLIP)`); replay.py:86-88 (sim applies SLIP to every engine `open`, adds included);
  tests/test_bt_intrabar_path.py:146 asserts the slip-free add price
- Evidence: engine adds are taker MARKET orders (engine.py:1196-1212 `_add_qty`), so they pay the same spread/impact as
  entries. A full 3-order DCA basket has ~6.6x the first order's notional (scale 1.5); all of it is slip-free in the backtest.
- Impact: DCA/pyramid sleeves are systematically flattered by 2 bp on most of their notional; contributes to replay ret gap
  for DCA mixes; Fable's "slippage optimistic" note is concrete here.
- Fix: `lvl_fill = lvl*(1+sd*SLIP)` for adds in both legs; update the intrabar test expectations.
- Tests: test_bt_intrabar_path add-price assertions.
- Cowork runtime validation needed: no
- Blocks: neither (affects preset evidence; re-validation after BT01 should include it)

### BT-C4 BTC circuit breaker / pump guard are sampled differently live vs backtest
- Severity: P2
- Status: open
- Evidence class: CONFIRMED (code)
- Origin: new (this audit)
- Files: backtest.py:71-86 (4h book without `btc1h` -> 4h move / sqrt(4) proxy), 510 (breaker checked once per bar close);
  app.py:278-279 and lab.py:835-836 never pass `btc1h`; engine.py:1249-1251 (`_breaker()` every manage pass),
  2279-2287 (`btc_move_1h` = last closed 1h candle, refreshed each minute)
- Evidence: live trips on ANY closed 1h BTC candle moving > pct (4 chances per 4h candle, plus pump-guard checks at entry
  time); the app/Lab backtest uses a random-walk proxy at the 4h close only, so a 6 % 1h spike inside a 4h candle that
  closes flat never trips it. Only research_long.py passes real 1h data.
- Impact: backtests of `risk_rules.btc_breaker` / `pump_guard.btc_1h_pct` understate pauses and `tighten` exits - the
  feature's measured benefit/cost is not what live does.
- Fix: have app/lab load BTC 1h from the candle cache and evaluate the breaker at each 1h close inside the walk (or label
  the result "proxy" in the UI).
- Tests: backtest unit test: 4h book + 1h spike inside a flat 4h candle -> breaker_until set.
- Cowork runtime validation needed: no
- Blocks: neither (enforce mode off by default)

### BT-C5 Trailing entries do not count toward `max_pos` in the backtester; the engine counts them
- Severity: P2
- Status: open
- Evidence class: CONFIRMED (code)
- Origin: new (this audit)
- Files: backtest.py:519-537 (arming ignores `tpend` count), 453-456 (max_pos checked only at fill); engine.py:1757-1762
  (`len(held) + len(working) >= max_pos`, working = pending trailing + resting maker entries)
- Evidence: with max_pos=4 and 4 trailing entries armed, the engine refuses a 5th signal; the backtester arms it and fills
  whichever rebounds first (slot order differs, BT-15 tie-break no longer applies).
- Impact: different coin selection for `trail_entry` slots; replay never covers trailing entries (BT-C9), so undetected.
- Fix: count `len(sl['pos']) + len(sl['tpend'])` (and pending maker) at arming time, same as entry_block.
- Tests: replay mode with trail_entry + max_pos 1-2.
- Cowork runtime validation needed: no
- Blocks: neither

### BT-C6 Daily-halt day of a signal on a bar that spans Cairo midnight
- Severity: P3
- Status: open
- Evidence class: CONFIRMED (code)
- Origin: new (this audit)
- Files: backtest.py:195-196, 430-432, 519; engine.py:369-371, 1625-1628
- Evidence: backtest day = Cairo date of the bar OPEN; halt reset happens at the first bar whose open is on a new day. The
  4h bar opening 20:00 UTC (23:00 Cairo summer / 22:00 winter) closes after Cairo midnight: live evaluates its signal on
  the new day (halt cleared), backtest still applies the previous day's halt. Day-start equity likewise uses c[i-1].
- Impact: a few entries per halted day differ; small.
- Fix: use the Cairo date of the bar CLOSE (`T + bar_sec`) for both reset and signal gating.
- Tests: unit test with a halt on day D and a signal on the 20:00 UTC bar.
- Cowork runtime validation needed: no
- Blocks: neither

### BT-C7 Shipped `data/` ends with a forming (partial) 4h candle on every coin
- Severity: P3
- Status: open
- Evidence class: CONFIRMED
- Origin: new (this audit)
- Files: data/*_4h.csv last row (e.g. data/BTCUSDT_4h.csv:4501); DATA_MANIFEST.json note ("klines ... closed")
- Evidence: data/BTCUSDT_4h.csv `2026-10-04 00:00` = o 84710.5 h 84839.3 c 84744.0 v 2217 (typical v 7-9k); the same candle in
  data_long/4h = h 84853.4 c 84799.9 v 4265. ETH/SOL show the same pattern (v 45.9k vs 124k; 369k vs 1.54M). app.get_candles
  filters forming candles (app.py:213) but the sandbox download did not.
- Impact: replay 1 (`N-900 .. N-1`) and all research tables end on a partial candle; one-bar effect, but the manifest's
  "closed candles" claim is false and the replay gate runs on it.
- Fix: drop the last row of data/ (and re-hash) or re-download with the closed filter; add a manifest check that the last
  open time + tf <= download time.
- Tests: verify.py manifest_check assertion on last-row closure.
- Cowork runtime validation needed: no
- Blocks: neither

### BT-C8 Replay harness asymmetries that blur the strict metrics
- Severity: P3
- Status: open
- Evidence class: CONFIRMED (code)
- Origin: new; related to Fable TEST-03
- Files: replay.py:114 (`CAPITAL_CAP = 0`), engine.py:576-578; replay.py:122 (open applied with `W.mark.update`, not `move()`)
- Evidence: (a) with CAPITAL_CAP 0 the engine sizes on `totalMarginBalance` (incl. open P&L), the backtester on realised
  equity - sizing diverges whenever lots are open; this feeds the ret/DD gap the gate measures. (b) The candle open is set
  without running the simulated stops, so a stop gapped at the open fills at the stop price on the first path step (sim),
  while the backtester fills at the open - optimistic sim, mismatched R on gaps.
- Impact: strict-gate noise; real bugs can hide in (or be blamed on) harness differences.
- Fix: set CAPITAL_CAP=start + COMPOUND=True in the replay (realised compounding like the backtester); call `move()` for the
  open.
- Tests: replay unit case with a gap through a stop.
- Cowork runtime validation needed: no
- Blocks: neither

### BT-C9 Parity/causality coverage: what is and is not gated
- Severity: P2
- Status: open (partially overlaps Fable BT-02/BT-03, still open)
- Evidence class: CONFIRMED
- Origin: Fable BT-02, BT-03; this audit
- Files: tests/test_causality.py:63-75, 99-103; test_engine_sim.py:25-26; replay_scenario2.json; verify.py:179-196, 232-237,
  364; .github/workflows/verify.yml
- Evidence: fast set (every PR) runs 3 of 6 modes (trailing, breakout pyramid, DCA); the other 3 + both real-data strict
  replays (24 steps, ZB_REPLAY_STRICT=1) run only in `verify full` (label-triggered). Strict thresholds are enforced there
  (good). Parity tolerance: one unmatched trade (`max(1, 2 %)`), med |dR| 0.05, p95 0.25, single seed per mode. NOT covered by
  any replay or causality mode: `max_bars` that fires (BT-C1 found by adding it), maker entries (BT-C2), trailing entries
  (BT-C5), TP ladder `tps`, `ttp`, `when` regime filter, pump guard, BTC breaker, enforce risk rules, governor, Kelly,
  `hours`, `vol_max_pct`, context strategies (rotation/hot_coin rank), shorts with DCA, the 1h timeframe (BT-02). The
  perturbation test only perturbs candle j and later; it cannot see cross-sectional context or regime_now (unused without
  `when`).
- Impact: every finding in this section except BT-C7 lives in an uncovered option; "strict replay PASS" covers 6 strategy
  configs on 4h only.
- Fix: one parametrised fast replay per option (small synthetic, 3 steps) + a 1h DCA strict replay (BT-02); seed sweep (3
  seeds) for the CORE modes.
- Tests: as above.
- Cowork runtime validation needed: no
- Blocks: testnet progression for any preset using those options

### BT-C10 Research/statistics details
- Severity: P3
- Status: open
- Evidence class: CONFIRMED (code)
- Origin: new; adjacent to Fable BT-06/07/18
- Files: backtest.py:581 (maxdd from bar-close curve), lab.py:343-353 (WF test windows), lab.py:375 vs panel.html:1887
- Evidence: (a) max DD is computed on candle-close marks; intrabar adverse extremes (which drive live stops/halt/liquidation)
  are invisible, so DD is understated, most for 4h DCA baskets. (b) Walk-forward test windows start flat and end at MTM:
  positions open at a window end are valued without exit fee/slippage and vanish; the next window restarts flat.
  (c) Panel labels WF "Efficiency" as "test score ÷ train score" but lab computes OOS CAGR / mean IS CAGR
  (`efficiency_score` is the score ratio). (d) Lab optimiser ranks raw in-sample scores with no trial-count deflation;
  hold-out shown for top_k only (Fable BT-18).
- Impact: optimistic DD and WF numbers; mislabelled efficiency tile.
- Fix: track intrabar MTM low per bar for DD; close open positions at window end with costs in WF; relabel tile; show
  number of trials next to the best score.
- Tests: lab unit tests for WF boundary close and label.
- Cowork runtime validation needed: no
- Blocks: neither

### Status of prior items in this area (Fable audit 2026-10-07, BT-xx)
- BT-01 DCA TP before safety fill: **fixed** in backtester by ef6abc0 (FBL-BT01, walk_path backtest.py:316-427, tests in
  test_bt_intrabar_path.py). DCA preset numbers still carry the `unverified` label (engine.py:314-319) - re-validation pending.
- BT-02 (no 1h/DCA strict replay): open (verify.py:179 unchanged).
- BT-03 (synthetic parity, 1 unmatched allowed): partially-fixed - deterministic intrabar unit tests added; tolerance unchanged.
- BT-08 maker generosity: open (backtest.py:440-447: whole-candle rest at prev close vs live GTX bid, 3 reprices / 40 s,
  engine.py:2543-2601); BT-C2 adds a path-order leak on top.
- BT-09 regime EMA200 seed (live 1,500 x 4h window): open (engine.py:2270-2277 uses `candles('BTCUSDT','4h')`).
- BT-10 pyramid add + stop on red candle: fixed by the path walk (add fires on o->h, stop on h->l; backtest.py:416-422).
- BT-12 flat funding both sides: open (backtest.py:13, 479; replay.py:147).
- BT-15 slot order = symbol order: open, consistent live/backtest.
- BT-16 BTC min notional 50: open; being handled on origin/bt02-exchange-filters (not duplicated here).
- BT-17 compounding vs live COMPOUND=False: open but disclosed in the panel (panel.html:505, 585).
- BT-05 survivorship: open, disclosed (DATA_MANIFEST note, ROADMAP.md:182); additionally app.run_backtest_job/lab_book drop
  any coin without full history for the period (app.py:262-263), so newly listed coins are excluded too.
- Known replay "exit ... failed: (empty)" (coarse step): root cause being handled on origin/fix/replay-qty-drift (bca5141:
  sub-step qty tolerance + replay.reduce raising a named error instead of a bare StopIteration, whose str() is empty). No
  extra evidence added.

### Verified OK (no finding)
- Signals/indicators on closed candles only: engine.candles drops rows with close time >= now (engine.py:664-667); replay's
  fake exchange serves the forming candle and the perturbation tests pass, so this path is exercised.
- Indicators/context causal: hh/ll shifted, bbw_pct shifted in squeeze, ranks from closed returns, regime via merge_asof on
  availability time (strategies.py:243-278); Lab lookahead_check covers indicators, ctx, signals, regime.
- Backtest management uses ATR of the last closed candle (backtest.py:478); engine `atr_now` refreshed only at cycle.
- Entry sizing, stop distance, DCA level/weight math and leverage cap match open_lot (backtest.py:249-280 vs engine.py:2131-2158).
- app.get_candles caches closed candles only (app.py:213).

### Behaviour changes since v3.2-rc2 (this area)
- backtest.py (ef6abc0, the only backtester change): one declared intrabar path (`path_points`; doji = worst case for the
  side) now orders every event: safety orders fill before the stop (larger, correct basket losses), basket TP / next pyramid
  level / raised stops only reachable by later legs, adds still fire before a stop in the stop-first candle, targets
  suppressed in that candle. DCA/pyramid results are lower than at rc2; stop-only strategies unchanged.
- replay.py: legs follow `path_points` (doji worst case by held side); fill/audit writer threads closed after a run.
- verify.py added (fast/full/release plans, strict replays at 24 steps gated in full). strategies.py, lab.py, load_data.py,
  research_*.py, data/ unchanged since rc2.

### Not verified
- Magnitude of BT-C1/BT-C3 on the real presets (needs a strict replay with DCA on data/ and data1h/; not run to save time).
- Whether research_refresh v3.1 maker rows changed materially after BT-C2.
- Engine Kelly history (net of fees, excludes funding) vs backtest Kelly history parity.
- Slow causality modes and both real-data strict replays were not re-run here. Fast set run on master (ef6abc0):
  `pytest -m "not slow" tests/test_bt_intrabar_path.py tests/test_lab.py tests/test_causality.py` -> 57 passed, 6 deselected (202 s).


# Appendix D — Outage/retry, security, installer, config

_Evidence pass D (read-only); integrated unchanged. Severity and status are proposals for Codex review._

## D Outage/retry, security, installer/update, config & dependencies

Scope read: `binance_client.py` (full), `app.py` (config, HTTP, loop, actions, main), `engine.py` (lock/outage/persistence paths),
`telegram_ctl.py`, `ai_filter.py`, `installer.ps1`, `installer_check.ps1`, `build_app.bat`, `rollback_drill.bat`, `setup_git.bat`,
`verify.py` (secret scan, release gate), `.github/workflows/*`, `requirements*.txt`; branch protection via `gh api` (read-only).
All line numbers are master @ ef6abc0. Several items overlap the owner's Fable audit (`Fable_audit_2026-10-07.md`); those are cited
as origin and given a current status instead of being re-argued.

### OPS-01 No shared back-off for writes / critical reads on 429/418 (ban escalation)
- Severity: P1 (mainnet) / P2 (testnet)
- Status: open
- Evidence class: CONFIRMED
- Origin: new (this audit); related Fable ENG-03 (no order budget)
- Files: binance_client.py:264-311 (`_req`), 138-159 (`ExchangeHealth.fail`), 313-348 (`_order`), 520-537 (`cancel`); engine.py:1231-1236 (orphan cancels)
- Evidence: the circuit only gates `read = method == 'GET' and not critical` (l.270). DELETE (cancels), POST, `get_order`
  (`critical=True`, l.315) and critical `positions/marks` always go to the network. On HTTP 429/418 a DELETE/critical GET is retried
  up to 4 times (l.275, 294-306), sleeping `Retry-After` capped at 30 s per attempt, and `_order`'s confirmation loop issues up to
  4 x `get_order` (each up to 4 attempts) + a resend. Nothing records "banned until T" across calls, and `X-MBX-USED-WEIGHT-*` /
  `X-MBX-ORDER-COUNT-*` headers are never read. 418 is treated as just another transient code (`is_transient`, l.179).
  Retry-After handling itself improved vs v3.2-rc2 (old: `float(r.headers.get('Retry-After'))` crashed on an HTTP-date; now
  `retry_after()` l.69-84 never raises).
- Impact: Binance escalates IP bans (minutes to days) when requests continue after 429/418. During a ban every protective action
  (stop move, close, flatten) fails; exchange stops stay, but nothing can be adjusted. With copy-trading followers this is a
  multi-account stall.
- Fix: one client-wide `banned_until` set from 418/429 Retry-After (and weight headers above ~90%), honoured by ALL methods
  (protective calls get priority after it expires, entries/adds refused until then); never retry a 418 inside `_req`.
- Tests: fake returning 429 then 418 with Retry-After; assert zero requests until the deadline, then protect-before-enter order.
- Cowork runtime validation needed: no (unit-testable with the existing fake clock)
- Blocks: mainnet release only

### OPS-02 Network I/O and sleeps while holding the engine lock; 60 s loop sleep stalls management
- Severity: P1
- Status: open (unchanged by T05b; durations newly quantified)
- Evidence class: CONFIRMED
- Origin: Fable UI-01 / ENG-08 (decide-then-act cycle)
- Files: engine.py:1227-1240 (`manage` holds `self.lock`, orphan cancels BEFORE reconcile), 1653 (`cycle` under lock), 2170-2176
  (AI review inside `open_lot`), ai_filter.py:35 (`timeout=60`); app.py:582-586 (`time.sleep(60)` after a failed cycle), 594-602;
  binance_client.py:329-348
- Evidence: per orphan, a black-holed network costs ~4 x 15 s timeout + ~6 s back-off (~65 s); under Retry-After 30 s, ~90 s; all
  inside `manage()` with the RLock held, before the reconcile that would detect the outage. An ambiguous order costs
  1+2+3+4 s sleeps + 4 `get_order` calls (each up to ~66 s) = up to ~4.5 min under the lock. The AI veto can hold it 60 s per signal.
  A cycle that raises (e.g. `equity()` in an outage) makes the single loop thread `sleep(60)` per failing TF, so `manage` (trailing,
  stop repair) does not run; same line exists in v3.2-rc2 (app.py:538 there). Panel `flatten/close/move_stop` and Telegram
  `/flatten` wait on the same lock. T05b tests use `_nosleep` (tests/test_outage.py:337-391), so no test bounds wall-clock time.
- Impact: protective management and emergency controls can be blocked for minutes exactly when the exchange misbehaves.
- Fix: move orphan cancels after a successful reconcile and give them a single attempt per pass (they are retried every 8 s anyway);
  run AI review and kline fetches outside the lock with <=15 s timeout; replace the 60 s sleep with a per-TF `retry_at` so the loop
  keeps calling `manage`; drop the lock around `_order` confirmation sleeps (record a pending marker first).
- Tests: wall-clock harness: `/api/status` and `flatten` answer in <2 s while a cycle is blocked on a slow fake; `manage` runs
  within 10 s after a cycle failure.
- Cowork runtime validation needed: yes (T05b-style read-outage canary measuring manage cadence on testnet)
- Blocks: testnet progression (unattended runs)

### OPS-03 Same-cid resend ~1 s after -2013 can double-fill a delayed POST
- Severity: P1 (mainnet)
- Status: open (byte-identical to v3.2-rc2 `_order`)
- Evidence class: SPECULATIVE (needs Binance delayed-visibility behaviour; confirm on testnet with an injected 503 after send)
- Origin: Fable ENG-10
- Files: binance_client.py:321-348
- Evidence: after a timeout/5xx the first `get_order` returning -2013 triggers `return self._req('POST', ...)` with the same
  `newClientOrderId`. Binance only guarantees cid uniqueness among OPEN orders; a MARKET order that is processed late and FILLED
  is not open, so the resend can be accepted. The code does not distinguish a pre-send failure (`ConnectTimeout`,
  `ConnectionError` before the body was written) from a post-send one (`ReadTimeout`, HTTP 503 "execution status unknown").
- Impact: a second full-size market position on a copied account.
- Fix: resend only after a pre-send failure; after a post-send failure poll `get_order` for a bounded window (e.g. 10-20 s) and
  then compare position size, raising `AmbiguousOrder` instead of resending.
- Tests: fake whose first POST times out and whose order becomes visible 3 s later; assert exactly one fill.
- Cowork runtime validation needed: yes (delayed visibility cannot be proven with fakes)
- Blocks: mainnet release only

### OPS-04 Installer hard-kills the bot; install mode has no PAPER/idle gate
- Severity: P2 (P1 once live)
- Status: open
- Evidence class: CONFIRMED
- Origin: Fable OPS-02 (hard-kill stop); the missing install-mode gate is new
- Files: installer.ps1:249-268 (`Stop-Bot` -> `Process.Kill()`), 439-447; verify.py:337-344 (PAPER/healthy gate only for the drill);
  app.py:1244-1248 (`quit`: save under lock, then `Timer(1.0, os._exit)` without the lock); engine.py:2240-2241 (lot saved only
  after the fill returns)
- Evidence: `build_app.bat` (install) kills ZackBot at any instant; only `verify.py release` checks "mode PAPER, healthy, all
  protected" before the drill. A kill between a MARKET fill (`_market_entry`, l.2209) and `self.state['lots'][key] = lot;
  self.save_state()` (l.2240) leaves a position the restarted engine reports as UNTRACKED with no bot stop
  (engine.py:1609-1618). The panel `quit` path has the same 1 s race because the loop thread can start a cycle after the save.
- Impact: rare but unbounded: an unprotected position after an update; on LIVE the installer would also silently stop a mainnet bot.
- Fix: a `prepare-stop` API (pause entries, take the engine lock, persist, exit) called by `Stop-Bot` before any kill; refuse
  `install` when the running bot reports LIVE unless an explicit flag is given; `quit` should `os._exit` while holding the lock.
- Tests: installer fake asserting prepare-stop is called and kill only after timeout; engine test that `quit` holds the lock.
- Cowork runtime validation needed: yes (Windows install on the PC)
- Blocks: mainnet release only (testnet: update risk accepted)

### OPS-05 Corrupt settings.json silently resets to defaults with entries OPEN; no fsync, no backup
- Severity: P1
- Status: open (unchanged since v3.2-rc2)
- Evidence class: CONFIRMED (code); corruption trigger = SPECULATIVE (power loss without fsync)
- Origin: Fable DATA-01..04 / DATA-02
- Files: engine.py:381-384 (`save_json`: no flush/fsync), 417-419 (state), 476-479 (settings), 322-323 (`GLOBAL_DEFAULTS`:
  `ENTRIES_PAUSED=False`, `CAPITAL_CAP=500`, `PRESET='original'`); app.py:155-157 (config tmp+replace, no fsync); telegram_ctl.py:236-241
- Evidence: `settings.json unreadable - defaults used` is only a warning; defaults re-enable entries with the stock preset and the
  next `save_settings()` overwrites the broken file. `state.json unreadable - starting empty` drops every lot (all positions become
  UNTRACKED). No `.bak`, no schema version.
- Impact: after a crash/power cut the bot may restart trading a strategy and risk the owner never chose, with followers copying.
- Fix: one `atomic_write()` (flush + fsync + replace + keep `.bak`); on an unreadable non-empty settings/state file: load `.bak`
  or set `error`, pause entries, alert, and never overwrite the bad file.
- Tests: write garbage / zero bytes to each file, start Engine, assert entries paused and the file preserved.
- Cowork runtime validation needed: no
- Blocks: testnet progression

### OPS-06 /api/settings applies keys one by one; a later invalid key leaves memory and disk diverged
- Severity: P2
- Status: open (same structure in v3.2-rc2 app.py:968+)
- Evidence class: CONFIRMED
- Origin: Fable (validate-then-apply, roadmap item 6)
- Files: app.py:1044-1093
- Evidence: inside `with e.lock`, each key mutates `e.S` immediately; `TELEGRAM_TOKEN` even calls `write_cfg` (l.1066-1067). A
  ValueError on a later key returns 400 before `e.save_settings()`, so e.g. `{"MAX_LEVERAGE":5,"ENTRY_ORDER":"x"}` changes
  leverage in the running engine (and resets `e._lev`) but not on disk; the panel shows an error.
- Impact: the running bot trades on settings the user believes were rejected; they vanish on restart.
- Fix: validate into a copy (`new = deepcopy(e.S)`), then swap + save once; side effects (write_cfg, tg.restart) after success.
- Tests: POST a valid + invalid key; assert `e.S` unchanged and 400.
- Cowork runtime validation needed: no
- Blocks: neither

### OPS-07 Paper<->live switch carries testnet state into mainnet
- Severity: P2
- Status: open
- Evidence class: CONFIRMED (code); impact on guards SPECULATIVE
- Origin: new (this audit)
- Files: app.py:1109-1124 (`/api/keys`: refuses only when `e.state['lots']`), engine.py:400 (same `state.json`/settings for both modes),
  1621-1636 (`check_guards` uses stored `peak_equity` / `day_start_equity`)
- Evidence: the switch is allowed with `grids`, `pending_entries`, `resting_entries` or `orphans` present; the new mainnet engine
  inherits testnet order tags and grid plans. Capital baselines (`peak_equity`, `day_start_equity`, `halted`, `CAP_*`) are not
  reset, so a testnet peak can trip the daily halt or PEAK_DD flatten logic on the first live guard check.
- Impact: confusing first live session; mainnet engine acting on testnet order ids/grids.
- Fix: refuse the switch unless lots, grids, pending/resting entries and orphans are all empty; per-mode state files
  (`state.paper.json` / `state.live.json`) or an explicit baseline reset on switch.
- Tests: switch with a resting entry -> refused; switch resets baselines.
- Cowork runtime validation needed: no
- Blocks: mainnet release only

### OPS-08 Single-instance guarantee is TOCTOU; server binds with SO_REUSEADDR on Windows
- Severity: P2
- Status: needs-runtime-evidence
- Evidence class: CONFIRMED (code: `http.server.HTTPServer.allow_reuse_address == True`, `socketserver.TCPServer.server_bind` sets
  `SO_REUSEADDR`, checked in Python 3.14.6); Windows double-bind behaviour SPECULATIVE
- Origin: new (this audit)
- Files: app.py:1271-1273 (`port_in_use`), 1324-1342 (`main`: session.json written, `App()` connects the engine and starts
  Telegram BEFORE `ThreadingHTTPServer(('127.0.0.1', PORT), H)`)
- Evidence: two near-simultaneous launches both pass `port_in_use()`, both construct an Engine (exchange reads, possible
  `set_hedge_mode`) and Telegram poller, and the second overwrites `session.json` (installer ping and `existing_instance_token`
  then trust the wrong token). On Windows, SO_REUSEADDR lets a second socket bind the same address, so the second instance may
  not fail at bind and both loops trade one account; a local process could also bind 8765 and receive the `?t=<token>` launch URL.
- Impact: duplicate engines = duplicate orders; Telegram 409 conflicts.
- Fix: `allow_reuse_address = False` + `SO_EXCLUSIVEADDRUSE`; bind the server first, then build App; a named mutex / locked
  pid file as the single-instance guard.
- Tests: unit test that the server class sets exclusive use; Windows test that a second bind fails.
- Cowork runtime validation needed: yes (Windows socket semantics)
- Blocks: testnet progression (cheap to fix)

### OPS-09 Required checks can be satisfied by a PR's own edited workflows
- Severity: P2
- Status: open
- Evidence class: CONFIRMED (protection settings); exploit path SPECULATIVE (not exercised)
- Origin: new (this audit)
- Files: .github/workflows/verify-fast.yml, verify.yml; `gh api repos/MoeAZack/ZackBot2/branches/master/protection`
- Evidence: repo is public; required contexts `verify fast`, `verify full` (app 15368 = Actions), `strict`, enforce_admins on,
  `required_approving_review_count: 0`, no CODEOWNERS. `pull_request` workflows run the workflow files from the PR, so a PR that
  edits `verify*.yml` (or `verify.py` / `verify_ci.py`) can publish green checks under the required names. The gates rely on the
  reviewing agent noticing. Positives: actions pinned by SHA, `permissions: contents: read`, no secrets used, secret scanning and
  push protection enabled; dependabot alerts disabled.
- Impact: an agent or compromised contributor can merge untested trading code.
- Fix: CODEOWNERS for `.github/**`, `verify*.py`, `installer*.ps1`, `*.bat` with one required owner review; or a repository
  ruleset "require workflow" pinned to the default branch; `verify_ci.py merge` fails when gate files differ from base unless an
  `infra-change` label is set by the owner.
- Tests: CI self-test that diffs gate files vs `origin/master`.
- Cowork runtime validation needed: no
- Blocks: neither (process risk; should precede any mainnet release)

### OPS-10 Telegram control authorises a chat, not a person; PIN optional
- Severity: P2
- Status: open (unchanged)
- Evidence class: CONFIRMED
- Origin: Fable SEC-02
- Files: telegram_ctl.py:276-283 (`_authorized` by chat id / `@username`), 511-533 (PIN only if set), 567-587; app.py:1068-1071
- Evidence: any member of a configured group/channel can `/pause`, and with the 6-char code sent to that same chat, `/flatten` or
  `/close`. 4-digit PIN with 5 tries / 15 min lock = ~480 guesses/day. Positives: offset saved before acting, stale updates
  dropped, rate limit, token stripped from errors, no remote stop.
- Fix: require a PIN when control is on; also check `from.id` against an allow-list; refuse negative (group) chat ids for control.
- Tests: group message from a non-owner `from.id` ignored.
- Cowork runtime validation needed: no
- Blocks: mainnet release only

### OPS-11 Plaintext config.env copies left behind after import
- Severity: P3
- Status: open
- Evidence class: CONFIRMED
- Origin: new (this audit)
- Files: app.py:105-112 (`config_path` copies `EXE_DIR\config.env` or `Documents\ZackBot\config.env`), 162-166 (only
  `DATA\src\config.env` is removed)
- Evidence: the imported plaintext file is left in place ("you can delete that old copy" is only logged). The new copy is
  re-encrypted with DPAPI on the next start.
- Fix: after a successful encrypted rewrite, delete (or overwrite) the imported source, or show a panel warning until it is gone.
- Tests: import fixture; assert the old file is gone after `write_cfg`.
- Cowork runtime validation needed: no
- Blocks: mainnet release only

### OPS-12 Time sync and minor client error paths
- Severity: P3
- Status: open
- Evidence class: CONFIRMED
- Origin: new (this audit)
- Files: binance_client.py:236-238, 292-293, 547-553; app.py:776, 786-790
- Evidence: `sync_time()` does `.json()['serverTime']` with no handling; called from `_req` on -1021, an error body (e.g. 418 JSON)
  raises `KeyError` out of `_req` (not BinanceError/RequestException), bypassing `_order`'s handlers, the circuit and
  `_exchange_down` (reported as a generic failure). Offset is only refreshed reactively (fine with recvWindow 6000).
  `api_restrictions()` (mainnet only) uses a raw `s.get`; its exception text with the signed query string reaches the panel
  unscrubbed (signature is not a secret, but inconsistent with `scrub`).
- Fix: wrap `sync_time` (raise BinanceError(-1021) on failure, record health); route `api_restrictions` through `_scrubbed`.
- Tests: -1021 followed by a non-JSON / error time response.
- Cowork runtime validation needed: no
- Blocks: neither

### OPS-13 Build reproducibility: pins without hashes, Python not pinned in the Windows build
- Severity: P3
- Status: open (provenance part = Fable OPS-01)
- Evidence class: CONFIRMED
- Origin: Fable OPS-01; rest new
- Files: requirements.txt, requirements-dev.txt, requirements-ui.txt; installer.ps1:245-252 (`Find-Python`), 343-347 (venv created
  once, reused), 324 (`build_info.py` = timestamp only)
- Evidence: exact `==` pins for the full tree (good) but no `--require-hashes`; the persistent `buildenv` is never recreated when
  `py` resolves to a new Python, and pip never removes stray packages; CI uses 3.14 on Ubuntu while the shipped exe is built with
  whatever `py` is on the PC. The exe selftest records version/build only (not Python, commit, or dependency hash).
- Fix: hash-locked requirements; recreate buildenv when `sys.version` or the requirements hash changes; write commit + dirty flag +
  Python + `pip freeze` hash into `build_info.py` and the selftest JSON; refuse dirty trees for `install`.
- Tests: installer fake asserting venv rebuild on version change.
- Cowork runtime validation needed: yes (Windows build)
- Blocks: mainnet release only

### Checked and found sound (no finding)
- `ZB_TESTNET_FAULTS` (`lev_refuse`, `read_outage`) is inert unless `base == TESTNET` exactly (binance_client.py:19, 36); only the
  trade client can be TESTNET, the data client is always MAINNET; tests cover mainnet, trailing-slash and `...com.evil` hosts
  (tests/test_testnet_faults.py:25, 94). Activation is logged loudly (engine.py:407-410).
- LIVE gating: `load_cfg` forces paper unless `LIVE_CONFIRM=YES_REAL_MONEY` (app.py:139-141); panel requires typed
  `CONFIRM`; Telegram cannot change mode.
- HTTP: loopback bind, exact Host (anti-rebinding), Origin allow-list, per-launch token via HttpOnly SameSite=Strict cookie or
  header with `compare_digest`, JSON-only + 1 MB cap, CSP/XFO/nosniff; backtest ids regex + realpath; icon names alnum-only.
  `/api/ping` is unauthenticated but only returns HMAC(token, nonce) and version.
- Secrets: config values regex-validated, DPAPI with verify-on-encrypt, unreadable values preserved; Scrub filter on all handlers;
  requests exceptions re-raised without chained cause and with query strings removed (binance_client.py:96-103).
- AI filter sends only market indicators/candles and the strategy name to api.anthropic.com (no account data); fails open to the
  rules (documented veto-only).
- Installer: fail-closed staging/tests/selftest before touching the bot, verified SHA-256 backup, HMAC-proved launch, verified
  rollback, mirror/shortcuts only after success, strict launcher argument parsing, .NET-only helper.

### Behaviour changes since v3.2-rc2 (this area)
- binance_client: T05b outage circuit (reads fail fast in outage, half-open probes, never gates orders/cancels/critical reads);
  `ExchangeUnavailable`; robust Retry-After (HTTP-date, capped); scrubbed exceptions; strict numeric parsing and new read models
  (`position_risk`, `open_orders_all`, `leverage_brackets` fail closed on notionalCoef != 1); `open_stop_tags` now raises on unknown
  algo state; T03c/T05b testnet fault injection.
- app: automatic backed-off start-up reconnect, exchange-down incident handling in the loop and guards, `--selftest` and
  `--simulate-failed-launch`.
- installer: CMD logic replaced by `installer.ps1` with verified backup, HMAC ping, rollback, drill mode and JSON step record.
- CI: three workflows added (none at rc2), required checks on master, `verify.py`/`verify_ci.py` gates incl. secret scan.
- Unchanged and still open: `_order` resend logic, settings/state load-failure behaviour, non-fsync writes, `/api/settings`
  partial apply, 60 s loop sleep, chat-based Telegram auth.

### Not verified
- Windows SO_REUSEADDR double-bind (OPS-08) and actual installer kill timing (OPS-04): need the PC.
- Binance delayed order visibility after 503 (OPS-03) and real 418 escalation behaviour (OPS-01).
- Whether the PyInstaller bundle contains anything beyond `panel.html`, `research/`, tzdata (did not build; `research/` is 528 KB
  of results/icons, no secrets found by inspection).
- `panel.html` client-side handling (XSS of exchange/Telegram strings) was not reviewed (outside this section).
- `verify_ci.py` reuse logic for docs-only pushes was only skimmed.


# Appendix E — UI, logging/telemetry, concurrency

_Evidence pass E (read-only); integrated unchanged. Severity and status are proposals for Codex review._

## E UI truthfulness & accessibility, logging/telemetry, concurrency & performance

Scope: panel.html + the app.py read models, log/telemetry writers, engine.lock / thread model. Master = ef6abc0. Read-only;
nothing launched. Items marked "Cowork runtime validation needed: yes" need a running testnet app to confirm.

### UX-01 DCA profile cards still quote backtest numbers that the corrected backtester contradicts; Research tab drops the label
- Severity: P1
- Status: open (partially mitigated by a label)
- Evidence class: CONFIRMED
- Origin: FBL-BT01 (docs/reviews/FBL_BT01_review_request.md "Presets labelled unverified", item 5 re-baselining deferred)
- Files: engine.py:300-319 (PRESETS notes/bt + UNVERIFIED_BT01 loop); panel.html:1326 (btBadge), 1328 (renderPresets), 1488 (renderResearch); app.py:377-378
- Evidence: the cards still render the old `bt` block ("$500 -> $4,637, max DD -26%") and note text, e.g. active_dca "positive every year incl. late 2022". The FBL-BT01 rerun on the same data gives active_dca $4,637 -> **$210**, PF 1.75 -> 0.80, DD -26% -> -68%, negative in every year 2022-2026; steady_mix 4h+1h $4,015 -> $1,517; boost_active $8,112 -> $3,256. The only signal is a small amber tag "unverified: backtest path fix pending re-validation" above the unchanged headline numbers. `/api/research` returns `unverified`, but panel.html never reads it (only reference to `unverified` is btBadge), so Research-tab rows with a DCA slot are shown with no caveat at all (the review request says so: "the Research tab does not render it").
- Impact: the owner (and anyone choosing a profile for copy-trading followers at $100-$200) sees a known-wrong, strongly positive expectation for a profile the fixed backtester shows losing. "Unverified" understates "measured as losing".
- Fix: until re-baselined, replace the `bt` headline for dca_dip profiles with the FBL-BT01 rerun numbers (or hide `bt`/note numbers and show "re-validation pending"), strip "positive every year" text; render `R.unverified` on Research rows whose strategy set contains dca_dip. Owner decision exists (origin/cowork/dca-off, "DCA off by default") - land that or this, not neither.
- Tests: test_app_ui check that no preset card with a dca_dip slot shows a `$500 -> ` figure differing from a stored post-BT01 value; unit test that renderResearch marks dca rows.
- Cowork runtime validation needed: no (static content)
- Blocks: testnet progression (profile choice for the forward test) and mainnet release

### UX-02 "Old engine" warning is shown on every backtest; FBL-BT01 did not bump the backtester version
- Severity: P2
- Status: open (since v3.1, 4aa1c0e; unchanged at v3.2-rc2)
- Evidence class: CONFIRMED (code); visual confirmation needs-runtime
- Origin: new (this audit)
- Files: panel.html:1449 (`old=b.engine!=='v3'`), 1466/1470 (showBT "Old engine (v2) result - re-run"); backtest.py:16 (`VERSION = 'v3.1'`); app.py:305, 361
- Evidence: results are saved with `engine=BT.VERSION='v3.1'`; the panel compares to the literal `'v3'`, so every current result carries "old engine" / "Made with the old engine - re-run for current numbers". Conversely FBL-BT01 (ef6abc0) changed fill ordering materially but left VERSION at 'v3.1', so pre-fix saved backtests and Lab results in %LOCALAPPDATA%\ZackBot\backtests are indistinguishable from post-fix ones.
- Impact: the stale-result warning is permanently on (ignored), while the one staleness that matters (pre-BT01 DCA results) is invisible.
- Fix: bump BT.VERSION (e.g. 'v3.2-bt01'), expose it in /api/meta, and compute `old = r.engine !== META.bt_version`; label pre-BT01 results containing dca/pyramid sleeves explicitly.
- Tests: unit test that a result saved by the current backtester is not flagged old; one saved with 'v3.1' is.
- Cowork runtime validation needed: yes (confirm badge on a fresh backtest)
- Blocks: neither

### UX-03 Equity chart "30d"/"All" and Trades "All" are silently truncated
- Severity: P2
- Status: open (same at v3.2-rc2: app.py:792/797 there)
- Evidence class: CONFIRMED
- Origin: new (this audit)
- Files: app.py:871 (`equity_hist[-3000:]`), 866 (`history[-1500:]`), 614-617 (record every 300 s); engine.py:715-718 (keeps 8000), 1736 (extra point per cycle); panel.html:434 (eqRange default 30d), 469 (trRange All), 1155
- Evidence: a point is recorded every 5 min plus one per cycle (>=288/day, ~384/day with a 15m slot). 3000 points = ~8-10 days; the engine keeps 8000 (~3-4 weeks). The default "30d" and "All" views therefore show at most ~10 days with no notice. Trades "All" and all stats/calendar are over the last 1500 trades only.
- Impact: misleading performance/drawdown picture once the bot has run >10 days (equity) or >1500 trades.
- Fix: downsample server-side for long ranges (e.g. hourly for >7d) and return the full kept range; label the window actually covered ("since <date>"); keep a daily equity series that is never truncated.
- Tests: unit test for /api/equity covering 30 days of synthetic points.
- Cowork runtime validation needed: no
- Blocks: neither

### UX-04 Bot capital / compounding sizing is derived from a history list capped at 3000 trades
- Severity: P2
- Status: open (pre-existing: v3.2-rc2 engine.py:590)
- Evidence class: CONFIRMED (code); timing SPECULATIVE
- Origin: new (this audit)
- Files: engine.py:593 (`realized = sum(... for h in self.history if closed >= since)`), 1175 (`self.history = self.history[-3000:]`), 585-586 (compound sizing), 584 (guard_eq)
- Evidence: realized P&L since CAP_SINCE is recomputed from `history`, which drops its oldest records past 3000. When >3000 trades close within one capital cycle, "Bot capital", compounding sizing equity and the guard equity (daily halt / DD flatten baselines) jump by the dropped trades' P&L.
- Impact: at ~15-30 trades/week this is 2-4 years out, sooner with high-frequency slots; silent change of sizing and of the numbers followers' sizing would mirror.
- Fix: keep a running `realized_since_cap` accumulator in settings/state (updated in _finish, reset on capital reset) instead of summing a truncated list; keep history truncation for display only.
- Tests: unit test with 3,100 synthetic closes asserting capital_info()['realized'] is unchanged by truncation.
- Cowork runtime validation needed: no
- Blocks: mainnet release only

### UX-05 Panel freezes on stale data with green chips while the engine lock is held; no fetch timeout
- Severity: P2
- Status: open (known: test_app_ui.py:221-225 records CYCLE_BLOCK_S); T05b made it more frequent during outages
- Evidence class: CONFIRMED (code); duration needs-runtime
- Origin: T02 review note; new for the UI consequence
- Files: app.py:634 (_lots_view `with e.lock`), 751 (candles_view), 508-515 (T05b _reconnect holds lock across connect); panel.html:783 (api() without AbortController), 1954-1966 (load/OFFLINE), 1059, 1072-1090
- Evidence: /api/status takes e.lock, which cycle() holds for the whole candle cycle (see CON-01). `load()` has no timeout and `LOADING` blocks further polls, so the page keeps the last D. Only `tick()` keeps updating "prices Xs ago"; the Engine/Exchange chips keep their old colour and the RUNNING pill stays green. OFFLINE is only set on a thrown error, and then only the pill changes - balances, P&L and positions stay rendered as if live. T05b `_reconnect` holds e.lock across `connect()` (exchange_info with 15 s timeout x up to 4 attempts), so during an outage - when the owner most needs the panel - /api/status can block for tens of seconds per retry.
- Impact: owner can act on stale positions/P&L; "ENGINE STOPPED" can also flash after a >60 s cycle because loop_ok is updated only at the end of a pass (app.py:622, 657).
- Fix: (1) read models without e.lock (copy lots under a short lock that cycle releases between phases, or publish an immutable snapshot from the loop); (2) api(): AbortController ~8 s; (3) when the last good status is >15 s old, grey the money strip/positions and show "data as of hh:mm"; (4) don't hold e.lock across network in _reconnect.
- Tests: UI harness: inject a 20 s /api/status delay and assert a stale banner appears and chips leave "ok".
- Cowork runtime validation needed: yes (measure /api/status latency during a 4h-boundary cycle and during a simulated outage via ZB_TESTNET_FAULTS)
- Blocks: testnet progression (operator visibility)

### UX-06 Unescaped interpolations of symbol/side strings
- Severity: P3
- Status: open
- Evidence class: CONFIRMED (code); exploitability SPECULATIVE (low)
- Origin: new (this audit)
- Files: panel.html:756 (`coin=s=>(s||'').replace('USDT','')` - no esc), 1158, 1168-1170 (activity feed `coin(h.symbol)`, `h.side.toLowerCase()`), 1199 (sideTag `${s}`), 1237/1243/1254, 1351/1385 (`t-${lib.style}`), 1491 (`x.tf`), 1222 (`<option value="${s}">${coin(s)}`)
- Evidence: 264 esc() calls cover nearly all free text (errors, notes, names, logs via hl(), toast uses textContent). The remaining raw values are symbols/sides/tf from history.json, missed.json, state.json and Binance. Server-side these are regex-validated on entry (SYM, sleeve id), but history/missed files are loaded from disk unvalidated (engine.py:458-460).
- Impact: low (local files, loopback-only, CSP blocks external script but allows 'unsafe-inline', so an injected inline handler would run).
- Fix: make `coin()` and `sideTag()` return escaped text; esc() the remaining tf/style values.
- Tests: UI harness seed with a history record whose symbol is `<img src=x onerror=...>USDT`; assert no execution.
- Cowork runtime validation needed: no
- Blocks: neither

### UX-07 Errors swallowed in the panel
- Severity: P3
- Status: open
- Evidence class: CONFIRMED
- Origin: new (this audit)
- Files: panel.html:1488 (renderResearch `catch(e){return}` -> blank tab), 1506 (loadLogs `catch(e){}`), 1445 (loadBT keeps old list), 1213 (`try{renderAudit()}catch(e){}`)
- Impact: a failing endpoint looks like "no data". Fix: render an inline "could not load (<error>) - retry" note. Tests: harness API-failure injection already exists (T02) - add these endpoints.
- Cowork runtime validation needed: no
- Blocks: neither

Accessibility / mobile (checked, no finding above P3): proper tablist/tabpanel with roving tabindex, 84 aria-labels,
aria-live regions, focus-visible rings, setHTML skips unchanged DOM, dialog() for flatten, confirm() on close / move stop /
take signal / grid stop / backtest delete / quit / live switch / capital mode. Viewport meta and 640/900/1250 px breakpoints;
T04 harness checks 390/820/1560 px for sideways scroll. Dark theme only. The server is loopback-only with exact Host
check, so there is no phone path to the panel; Telegram is the only mobile surface (a product fact, not a defect).
"Pause entries" has no confirm (intentional: it is the safe direction).

### OBS-01 trades.csv is written synchronously inside order paths; a locked file aborts bookkeeping after the exchange executed
- Severity: P2
- Status: open (pre-existing; T05/T05a made only fills/audit non-blocking)
- Evidence class: CONFIRMED (code path); Windows lock behaviour SPECULATIVE
- Origin: new (this audit)
- Files: engine.py:720-725 (log_trade `open(..., 'a')`), 1089 (_apply_close), 1221 (_apply_add), 1581 (stop fill), 2262 (entry, before save_state at 2265), 1136-1146 (close_lot -> _finish/save_state after _apply_close)
- Evidence: `_apply_close` mutates the lot (realized, fills, qty) then calls `log_trade`; if the open raises (Excel opens CSV with a deny-write share lock; also AV/backup tools), the exception leaves `_market_close` before `_finish()` and `save_state()`: the position is closed on Binance, the lot stays in memory with qty 0, no history record, stop not cancelled, state not saved, and the caller logs "exit failed". A later close attempt self-heals (qty 0 -> `_market_close` returns early -> `_finish`), but a restart in between replays the stale state.json. On entry (2262) the exception skips `notify` and `save_state()`, so a restart before the next save loses the lot (it becomes an untracked position).
- Impact: owner opening trades.csv in Excel while the bot trades can make an executed close/entry look failed, delay history/P&L and, across a restart, desynchronise state from the exchange.
- Fix: route trades.csv through the existing FillWriter (non-blocking, counted) or wrap log_trade in try/except that logs and counts; never let a CSV failure raise into an order path.
- Tests: unit test patching `open` for trades.csv to raise PermissionError during close_lot/open; assert lot finished, history saved, state saved.
- Cowork runtime validation needed: yes (testnet: open trades.csv in Excel, trigger a manual close)
- Blocks: testnet progression

### OBS-02 Backtest worker thread can die silently; nothing restarts or reports it
- Severity: P2
- Status: open (same code at v3.2-rc2)
- Evidence class: CONFIRMED (code path); race likelihood SPECULATIVE (low)
- Origin: new (this audit)
- Files: app.py:226-233 (job_worker), 1134/1144/1149/1167 (HTTP thread inserts into JOBS), 47 (windowed exe: no stdout/stderr), no threading.excepthook anywhere
- Evidence: the `finally:` block iterates `JOBS.items()` in a Python-level comprehension while HTTP threads insert jobs; "dictionary changed size during iteration" raised in `finally` escapes `while True` and ends the thread. Default threading.excepthook prints to sys.stderr, which is None in the windowed exe, so nothing reaches bot.log. All later jobs stay "queued", then enqueue fails with "too many backtests waiting". Also: pruning keeps only status done/error, so `cancelled` lab jobs (with partial results) are never pruned.
- Impact: Backtest/Lab silently stop working until restart; no trading impact.
- Fix: snapshot `list(JOBS.items())` and guard with a lock; wrap the whole loop body; install threading.excepthook that logs to bot.log; prune 'cancelled' too; show worker liveness in /api/status.
- Tests: unit test that an exception in the pruning step does not stop job_worker; excepthook logs.
- Cowork runtime validation needed: no
- Blocks: neither

### OBS-03 Telemetry and audit health are invisible in the panel; audit expectancy shown without coverage
- Severity: P2
- Status: open (new surface since v3.2-rc2: T05, T05a)
- Evidence class: CONFIRMED
- Origin: T05 / T05a
- Files: app.py:663 (health.fills), 669 (health.audit); engine.py:148-167 (summary: dropped/write_errors/state), 919-942; panel.html:1204-1212 (renderAudit), no reference to `fills`, `telemetry`, `dropped`, `write_errors`, `coverage`
- Evidence: /api/status ships the fill summary and audit telemetry counters every 5 s, but the panel never renders them; a writer stuck in 'closing', a full queue (records dropped) or disk write errors are visible only as per-record WARNING lines in bot.log. The audit card shows "N audited closed trades" and segment expectancy but not `coverage` (legacy / rotated / missing), so expectancy over a partial window reads as complete. The missed-list funnel covers only the last 600 records (engine.py:1193) with no stated window.
- Impact: forward-test evidence (T05/T05a are the measurement basis for later gates) can silently degrade.
- Fix: one "Telemetry" row in the Engine popover (accepted/persisted/dropped/write_errors/state for fills and audit, red when dropped or errors > 0); show coverage and window dates in the audit card; mark segments with n < 30 as "too few".
- Tests: harness: inject write_errors>0 and assert the popover shows it.
- Cowork runtime validation needed: no
- Blocks: testnet progression (evidence quality)

### OBS-04 Full-file JSON rewrites per event inside the lock
- Severity: P3
- Status: open (pre-existing)
- Evidence class: CONFIRMED
- Origin: new (this audit)
- Files: engine.py:1194 (miss -> save_json(missed, 600 recs, indent=2) per missed signal), 1525 (add_blocked), 1176 (history 3000 recs incl. fills per close), 381-384 (save_json: no fsync; os.replace can raise PermissionError on Windows if a reader holds the target)
- Impact: CPU/disk inside cycle under e.lock (many misses per cycle = many 100-500 KB rewrites); a failing replace raises into miss()/close paths. Fix: batch missed writes once per cycle; append-only JSONL for history; catch and count persistence errors outside order-critical state.json. Tests: count save_json calls per cycle in replay.
- Cowork runtime validation needed: no
- Blocks: neither

Logging / secrets (checked, no defect found): Scrub filter on every handler (app.py:33-48) replaces API key/secret,
Anthropic key, Telegram tokens (regex) in formatted messages; no `log.exception`/`exc_info` use (tracebacks are put in the
message and so are scrubbed); binance_client.scrub/_scrubbed strip signed query strings and chained urllib3 causes;
Telegram failures log only the exception type (engine.py:472, telegram_ctl.py:185-188, 216); TELEGRAM_PIN is stored hashed and
excluded from /api/status. bot.log rotates at 5 MB x 3; fills.jsonl rotates at 5 MB (one .1), trade_audit.jsonl at
20k lines/16 MB (one .1); incidents de-duplicated (INCIDENT_MAX 200). err() de-dup limits warning floods.

### CON-01 Engine lock held across the whole cycle including network and AI calls
- Severity: P2
- Status: open (pre-existing; T05b added `_reconnect` under the lock)
- Evidence class: CONFIRMED (code); durations needs-runtime
- Origin: T02 review (test_app_ui.py:221 "the panel cannot refresh while a cycle runs")
- Files: engine.py:1652-1662 (cycle: equity(), reconcile(), compute_signals -> candles() klines for every coin, all under `with self.lock`), 2167-2177 (AI review: Anthropic call, timeout 60 s, ai_filter.py:35, per candidate under the lock); app.py:508, 573-598 (cycle and manage on the same loop thread); telegram_ctl.py:365/416/449/463/479/494 (Telegram reads take e.lock)
- Evidence: up to 120 coins x klines(1500) per timeframe plus account/positions reads and optional sequential 60 s AI reviews run inside the lock. Every panel read model, /api/candles, close/flatten/settings writes and Telegram /status/positions wait. manage() (trailing, breakeven, maker polling, unprotected-stop retries) runs after the cycle on the same thread, so soft management pauses for the whole cycle; maker MAKER_WAIT_S windows stretch.
- Impact: exchange stops still protect, but soft exits and stop repairs are delayed; panel/Telegram unresponsive (UX-05).
- Fix: prefetch candles and run AI review outside the lock (they only read), take the lock only for the decision/order phase; publish a read-only snapshot for HTTP/Telegram; keep `_reconnect` network outside the lock.
- Tests: replay test asserting lock hold time per cycle < N s with a slow fake data client; harness CYCLE_BLOCK_S budget.
- Cowork runtime validation needed: yes (measure hold time at a 4h boundary with TOP40 and with AI_FILTER on)
- Blocks: testnet progression (before more coins / AI filter), mainnet release

### CON-02 Backtest/Lab downloads and per-cycle full kline refetches share the Binance IP weight budget with the engine, unthrottled
- Severity: P1 (mainnet) / P2 (paper)
- Status: open
- Evidence class: SPECULATIVE (code CONFIRMED; ban requires measured request rate)
- Origin: new (this audit)
- Files: app.py:183-209 (get_candles: back/forward fill loops of klines(limit=1500) with no pacing, public MAINNET client), 316-333 (lab_book), 1241 (top_by_volume); engine.py:405 (`self.data = Futures('', '', MAINNET)` even in paper), 657-672 (candles(): full klines(1500) refetch per coin per new candle); binance_client.py:275-294 (retries on 418/429, no client-side weight budget)
- Evidence: klines with limit 1500 cost weight 10. A 2200-day 15m/1h multi-coin backtest or Lab job issues thousands of sequential calls from the same IP the engine uses for marks/klines; nothing paces them. At a 4h boundary the engine also refetches 1500 candles for every coin on each due timeframe (3 x 40 x 10 = 1200 weight) where 2-3 new candles (weight 1) would do.
- Impact: 429 then 418 IP ban (minutes to days) blocks mark prices and signals even in paper (data client is mainnet), and in LIVE also order/stop management on the same host.
- Fix: a process-wide weight limiter shared by all Futures instances on the mainnet host (headers X-MBX-USED-WEIGHT-1M), backtest downloads yield when used weight > ~50%; incremental candle cache in Engine.candles (fetch since last open, limit<100).
- Tests: fake client counting weight: a 2-year 1h TOP40 backtest stays under 1200/min; engine cycle weight per boundary.
- Cowork runtime validation needed: yes (log X-MBX-USED-WEIGHT-1M during a long backtest download on paper)
- Blocks: mainnet release only

### CON-03 Backtests and Lab run in the trading process (GIL), single worker, no cancel for backtests/study
- Severity: P2
- Status: open
- Evidence class: CONFIRMED (structure); latency impact SPECULATIVE
- Origin: new (this audit)
- Files: app.py:222-238 (one daemon worker, Queue(8)), 1138-1159 (study = 7+ full backtests in one queue item), 1171-1174 (cancel only for lab jobs); lab.py:264-301 (optimize up to MAX_TRIALS runs), backtest.py (Python loops); no multiprocessing anywhere
- Evidence: CPU-bound Python backtests hold the GIL in slices alongside the loop thread, HTTP threads and preview worker. A study or 300-trial optimize can occupy the worker for a long time; queued jobs cannot start, backtests cannot be cancelled.
- Impact: slower manage passes / panel during research; owner may avoid research while the bot runs.
- Fix: run jobs in a subprocess (ProcessPoolExecutor(1) or a `python app.py --job` child) with cancel; add cancel for backtest/study.
- Tests: measure loop pass latency (loop_ok deltas) with a lab optimize running.
- Cowork runtime validation needed: yes (loop latency under a Lab run)
- Blocks: neither

### CON-04 Small unsynchronised shared-state races
- Severity: P3
- Status: open
- Evidence class: CONFIRMED (code); occurrence SPECULATIVE
- Origin: new (this audit)
- Files: app.py:711 (`{x: y for x, y in j.items()}` while the worker adds 'result'/'error' -> RuntimeError -> /api/status 500 -> OFFLINE flash), 880, 1140 (generator over JOBS.values()); app.py:525-543 + engine.py:700-705 (preview_worker runs compute_signals without the lock and overwrites `_sig_raw`, the T05a raw-signal source; a mismatched candle time makes the audit drop `raw`); app.py:412-435 (coin_icon: all waiting threads repeat the network fetch serially under _ICON_LOCK, no re-check of _ICON_FAIL; with Binance CDN unreachable each icon blocks a browser connection ~16 s and can starve the 6-per-host pool used by /api/status); app.py:1098-1099 vs 1179 (share-sum check outside the lock: concurrent sleeve + grid saves can exceed 100%)
- Fix: guard JOBS with a lock and copy under it; give preview its own raw-signal dict; re-check _ICON_FAIL after acquiring the lock; validate shares under e.lock.
- Tests: unit test for icon fail cache under concurrency; JOBS snapshot test.
- Cowork runtime validation needed: no
- Blocks: neither

### CON-05 Memory / payload growth
- Severity: P3
- Status: open
- Evidence class: CONFIRMED
- Origin: new (this audit)
- Files: engine.py:428/671 (`_kc` keyed by any regex-valid symbol requested via /api/candles or grid_preview, never evicted; each entry 1500 rows x indicator columns); app.py:232-233 (30 finished jobs keep full `result`); app.py:663/669 (fill_summary uncached O(5000) Python loop + audit summary in every 5 s /api/status, unused by the UI - OBS-03); origin/bt02-exchange-filters app.py:362-388 `_PF_FILES` (keyed by shipped file + mtime: bounded by bundled files, no lock, duplicate compute only - acceptable)
- Fix: LRU-bound `_kc` to the universe + held symbols; store only job metadata in JOBS (results are on disk); serve health.fills/audit only on /api/audit_summary or when the card is open.
- Tests: none needed beyond a size assertion on `_kc` after 200 distinct /api/candles calls.
- Cowork runtime validation needed: no
- Blocks: neither

### Behaviour changes since v3.2-rc2 (this area)
- T05 / T05a: process-wide non-blocking FillWriter for fills.jsonl and trade_audit.jsonl (bounded queue 1000, drop-and-count, single .1 rotation); new /api/audit_summary and a Trades-tab "Trade audit" card; /api/status health gained fills, audit, lev_refusals, exchange_circuit, confirmed, incidents (payload larger, computed every poll).
- T05b: err() de-duplicates open incidents (one line with count); startup connect retries in the loop thread while holding e.lock; safety bar shows exchange "outage" and positions-confirmed age; mark-price failures map to one exchange-down incident.
- T03a/T03c: "Leverage refusals" chip and dialog.
- FBL-BT01: DCA profile cards show an "unverified" tag; old numbers unchanged; Research tab unchanged (label not rendered); backtester VERSION unchanged.
- T04: narrow-screen segmented buttons wrap; setS() un-focuses a rejected checkbox so the saved state re-renders.
- No change to: engine.lock scope of cycle(), job worker, history/equity caps, trades.csv writing, log rotation/scrubbing.

### Not verified
- Actual /api/status block times during cycles, outage reconnects, AI-filter cycles (CON-01, UX-05).
- Whether Excel's lock on trades.csv triggers OBS-01 on this machine (expected yes for .csv opened in Excel).
- Binance weight consumption of a long backtest download (CON-02); loop latency during Lab runs (CON-03).
- UI harness (Playwright) was not run; the "old engine" badge (UX-02) and stale-panel behaviour (UX-05) not seen rendered.
- Telegram command paths beyond lock usage; grid.py UI numbers; lab.py result labelling beyond panel text (optimize/walk-forward texts do state hold-out / out-of-sample).
- Contrast ratios computed by eye from tokens (--mute #8f89b8 on #07051a is roughly 6:1), not measured.


# Appendix F — Architecture, duplication, tests & CI

_Evidence pass F (read-only); integrated unchanged. Severity and status are proposals for Codex review._

## F Architecture, duplication, dead code, tests & CI

Scope: current master `ef6abc0` (audit worktree). Read-only. 823 tests collected (`-m "not slow"`: 816; 7 slow).
Ordered by severity. Line numbers are current master.

### ARCH-01 Corrupt settings.json / state.json silently replaced by defaults (and persisted at once)
- Severity: P1
- Status: open
- Evidence class: CONFIRMED (code read)
- Origin: new (this audit)
- Files: engine.py:476-489 (`load_settings`), engine.py:416-419 (state), engine.py:427-428 (`CAP_SINCE` save), engine.py:381-384 (`save_json`)
- Evidence: `try: s.update(json.load(...)) except Exception: log.warning('settings.json unreadable - defaults used')`, then
  `SLEEVES` falls back to `PRESETS[s.get('PRESET','original')]`, and because `CAP_SINCE` is now empty the constructor calls
  `self.save_settings()` immediately, overwriting the corrupt file with defaults (owner's slots, risk rules, governor, coin
  switches, capital cycle all lost; no `.corrupt` copy kept). `state.json` unreadable -> `'starting empty'` (lots forgotten;
  the next `save_state` overwrites it). `save_json` writes tmp + `os.replace` without `flush/fsync`, so a power loss can leave
  a truncated file, which this fallback then hides. Unchanged since v3.0 (`git show v3.2-rc2:engine.py`).
- Impact: after a crash the bot restarts trading the *original* preset at default leverage/risk and a fresh capital cycle;
  open positions become "untracked" (alerted, entries on that coin blocked, but no trailing/exits managed by the bot).
- Fix: fail closed: on unreadable settings/state, rename to `*.corrupt-<ts>`, refuse to start the engine (panel shows the
  error), never auto-save over it; add `f.flush(); os.fsync()` in `save_json`; keep one `.bak` generation.
- Tests: write garbage to settings.json/state.json -> Engine refuses to start, file preserved, no save; fsync spy test.
- Cowork runtime validation needed: no
- Blocks: testnet progression

### ARCH-02 Engine and backtester duplicate sizing / Kelly / governor / risk rules, and parity never exercises them
- Severity: P1
- Status: open (v3.1 audit P2-7 "engine and backtester are separate implementations"; shared core T06-T09 still pending)
- Evidence class: CONFIRMED (duplication, parity scope) / SPECULATIVE (size of result drift)
- Origin: ZackBot_v3.2_Response_to_Audit.md:110 (P2-7)
- Files: sizing engine.py:2131-2160 vs backtest.py:249-269 vs backtest.py:553 (`martingale_risk`, third copy of the DCA
  weight formula, production-unused); Kelly engine.py:1126-1134 vs backtest.py:53-63 (identical code, separate history
  sources); governor engine.py:2413-2471 vs backtest.py:89-113; risk rules engine `_rules_eval` 2311 vs backtest `rule_block`
  ~220-247; BTC-1h move engine.py:2279 vs backtest.py:71-87 (4h book without `btc1h` uses a sqrt(4) proxy); replay.py:179-181.
- Evidence:
  - Governor default mode differs: backtest `governor.get('mode','auto')=='off'` (backtest.py:90) vs engine
    `G.get('mode','off')` (engine.py:2419); `tests/test_v31_engine.py:472` locks the backtest default in. Engine clips
    the product to `GOV_MULT_MAX`, backtest clips each rule to a literal `2.0`; drawdown basis differs (engine `guard_eq`
    / capital-cycle growth, backtest mark-to-market vs `start`). Lab passes `governor`/`risk_rules` raw (lab.py:820-823),
    not through `app.clean_governor`, so a malformed rule raises `KeyError` inside the job.
  - Sizing: backtest omits `step`/`min_qty` rounding (BT02 branch addresses it) and caps leverage on current close
    (`notional(sl,i)`) while the engine uses entry `avg` (engine.py:2154).
  - Parity harness passes only `when/trail_entry/pump_guard` to `B.run` (replay.py:179-181) - never `kelly`, `hours`,
    `vol_max_pct`, `risk_rules`, `governor`, `entry_order`. Its fake has `stepSize 0.00001` (replay.py:87) so rounding is
    never compared. Zero tests reference `kelly_mult` in either module (grep).
- Impact: Lab/backtest results for any slot using Kelly, governor, risk rules, maker entries or hours filters can differ
  from live with no gate noticing; the governor "suggest" vs "auto" semantics are only kept aligned by panel.html:1807.
- Fix (bounded, before the full shared core): move `kelly_mult`, `gov_step`, the DCA weight/qty formula and the leverage
  cap into one pure module used by engine, backtest and `martingale_risk`; make backtest governor default `off`; validate
  lab `run_options.governor/risk_rules` with the app cleaners; extend `replay.run_replay` to pass every slot option.
- Tests: property test "engine sizing == backtest sizing" over random (equity, share, risk, atr, dca, lev, step);
  replay scenario with kelly + governor + coin_cap enforced; unit tests for Kelly edge cases (all wins/all losses/min_trades).
- Cowork runtime validation needed: no
- Blocks: testnet progression (results feed preset choices)

### ARCH-03 Three slot validators with different rules; settings accept DCA values the engine then refuses forever
- Severity: P2
- Status: open
- Evidence class: CONFIRMED
- Origin: new (this audit)
- Files: app.py:922-926 `_num`, 929-951 `clean_mgmt`, 1012-1039 `validate_sleeve`, MG_SUB app.py:~915; lab.py:694-712;
  grid.py:128 `_num`; engine.py:2135-2139
- Evidence: `MG_SUB['dca']` allows `n` 0-8 and `scale` 0.1-3, but `open_lot` refuses unless `1 <= n <= 8 and 1.0 <= scale <= 3.0`
  -> a slot saved with `scale 0.5` is accepted by `/api/sleeves` and then skips every entry ("DCA settings out of range").
  Lab `_validate_sleeves` checks only share/risk/max_pos (max_pos 1-40 vs 1-20 live) and does not validate `mgmt`/`params`
  at all; `/api/backtest` uses `validate_sleeve` but `/api/lab` does not. `app._num` accepts bools/strings (`True`->1.0,
  `"0.02"`), `lab._num` rejects them, so the same slot passes one endpoint and fails the other. Kelly `max` accepted up to 4
  (app.py:1036) while the panel offers 1.5; with governor x2 a slot's risk can reach 8x its configured risk (SPECULATIVE:
  bounded only by the leverage cap/open-risk rule if enabled).
- Impact: silently dead slots; optimizer/Lab can explore configurations the live engine rejects; inconsistent errors.
- Fix: one `validate_slot()` (app's, tightened to the engine's DCA bounds) used by settings, backtest, lab, grid; one `_num`.
- Tests: table test - every slot accepted by the validator opens a lot in `mk_engine` (no `last_skip` from range checks);
  lab and backtest endpoints reject the same invalid inputs.
- Cowork runtime validation needed: no
- Blocks: neither

### TEST-01 Global monkeypatch seams leak across tests; production code carries branches that only test fakes take
- Severity: P2
- Status: open
- Evidence class: CONFIRMED
- Origin: new (this audit)
- Files: tests/test_safety.py:51-59 (`E.Futures = FakeX`, never restored), tests/test_grid.py:55; replay.py:107-112 (patches
  `E.Futures`, `E.now_utc`, `E.time`); engine.py:1008-1011 (`positions(critical=True)` `TypeError` fallback), 1015 & 1373
  (`self.trade.__dict__.get('_health')`), 1819-1842 ("older client" `leverage_max` / `current_leverage` paths)
- Evidence: tests/test_testnet_faults.py:147 and test_t05b_startup_outage.py:94 must undo it ("test_grid/test_safety replace
  it globally") -> results depend on test order. `FakeX` and replay's `Fake` lack `leverage_brackets`/`margin_state`/
  `critical=`, so the engine runs the "older client" branches the real `binance_client.Futures` never takes; the strict
  replays and most engine tests therefore do not exercise the bracket/margin-state path used in production. The
  `except TypeError` fallback would also hide a real `TypeError` raised inside `positions()` and retry a non-critical read.
- Impact: order-dependent tests; the parity gate validates a code path production does not run; a refactor/split of
  engine.py cannot move `Futures`/`time` without breaking every seam.
- Fix: inject `client_factory` and `clock` into `Engine.__init__` (default real); a conftest fixture builds `FakeX`; make
  `FakeX`/replay `Fake` implement the full client protocol (incl. `leverage_brackets`, `margin_state`, `critical`) and
  delete the "older client" and `TypeError` branches; add a protocol-conformance test (fake methods == real public methods).
- Tests: run the suite with `pytest -p random_order` (or reversed file order) - must be green.
- Cowork runtime validation needed: no
- Blocks: neither (prerequisite for the shared-core split)

### TEST-02 Critical paths with no tests (Kelly, settings load/migration, HTTP auth outside the UI harness)
- Severity: P2
- Status: open
- Evidence class: CONFIRMED (grep of tests/)
- Origin: new (this audit)
- Files: engine.py `kelly_mult` 1126, `load_settings` 476, `apply_preset` 503, `daily_summary` 1643, `_sweep_incidents` 979;
  backtest.py `kelly_mult` 53; app.py `H._authed/_host_ok/do_POST` 828-910
- Evidence: 0 test references to `kelly_mult`, `load_settings`, `save_settings`, `apply_preset`, `daily_summary`,
  `_sweep_incidents`. Token/Host/Origin/Content-Type/size checks are only exercised by `test_app_ui.py` (checks at lines
  167-171), which runs only in the full gate's `replay1-ui` slice (needs Playwright) - no PR-fast coverage of the panel's
  security boundary. The v1/v2 migrations (engine.py:412-415, 483-484; app.py:162, 461-467) are untested.
- Fix: plain pytest tests that drive `H` through `http.server` on an ephemeral port (or call `do_POST` on a stub) for 401/403/
  415/413/421; unit tests for Kelly and settings load/migration.
- Tests: as above.
- Cowork runtime validation needed: no
- Blocks: neither

### TEST-03 Fast gate (the only per-commit gate) excludes the parity modes BT01 changed; master never runs full
- Severity: P2
- Status: open
- Evidence class: CONFIRMED
- Origin: new (this audit)
- Files: tests/test_causality.py:73-75 (`CORE` vs slow), .github/workflows/verify-push.yml (fast only), verify.yml (label only)
- Evidence: slow = causality+parity for "trend long + pyramid", "partial TP + breakeven", "runner ladder" (6 tests) and the
  autocrlf clone test. FBL-BT01 (ef6abc0) changed pyramid/partial/ladder ordering, i.e. exactly the slow modes; both strict
  replays and the UI harness are full-only. Full runs only when Codex adds `full-ready`; master pushes run fast only.
  Mitigation verified: branch protection `strict: true` with required "verify fast"+"verify full"
  (`gh api .../required_status_checks`), so a merged head did pass full. Risk is in iteration: a PR can sit green on fast
  while a parity regression is only found at the final label.
- Fix: move one pyramid and one partial-TP parity case into the fast set with fewer steps (measure; causality cases are
  ~16 s each: PROJECT_STATUS.md:46 "isolated causality 13/13 passed in 3:35"), or add a nightly `verify full` on master.
- Cowork runtime validation needed: no
- Blocks: neither

### TEST-04 Tests pinned to source text and dead compatibility guards
- Severity: P2
- Status: open
- Evidence class: CONFIRMED
- Origin: new (this audit)
- Files: tests/test_fills.py:133-135, 305, 357-360; tests/test_trade_audit.py:298-300, 2382-2416; tests/test_leverage_auto.py:795-830;
  app.py:651, 663-669, 865 (`hasattr(e, 'fill_summary'|'audit_summary'|'exchange_state'|'_sweep_incidents')`)
- Evidence: tests assert literal code lines such as `"fills=e.fill_summary() if hasattr(e, 'fill_summary') else None" in src`
  - they lock in `hasattr` guards that are dead (app and engine always ship together in one exe) and fail on any harmless
  reformat. Two panel tests `return` (silently pass) when the card or `node` is missing (test_trade_audit.py:2400, 2410)
  instead of `pytest.skip`/fail; test_t05b_startup_outage.py:84 keys behaviour off `inspect.stack()[1].function == 'loop'`.
- Fix: replace text asserts with behavioural tests (call `App.snapshot()` with a stub engine); delete the `hasattr` guards;
  use `pytest.skip` for missing tools; remove the stack-name coupling.
- Cowork runtime validation needed: no
- Blocks: neither

### TEST-05 Test-to-test imports instead of fixtures; wall-clock assertions
- Severity: P3
- Status: open
- Evidence class: CONFIRMED
- Origin: new (this audit)
- Files: 56 `from test_safety import ...` lines (tests/test_trade_audit.py alone ~45), test_outage_final.py:8,
  test_t03c_t05b_interaction.py:7-8; tests/test_fills.py:187, 200, 315, 430 (`time.time() - t < 1/3`, `sleep(0.3)` ordering)
- Evidence: helper/fake definitions live in test modules (and module-level state such as `E.Futures`), so editing one test
  file breaks others; timing asserts (<1 s) can flake on a loaded runner (this audit's own run shared the CPU with other
  suites and ran several-fold slower).
- Fix: `tests/fakes.py` + conftest fixtures; replace timing asserts with event/queue assertions (or generous bounds).
- Cowork runtime validation needed: no
- Blocks: neither

### ARCH-04 Telegram sending implemented three times; engine.notify spawns an unbounded thread per message
- Severity: P3
- Status: open
- Evidence class: CONFIRMED
- Origin: new (this audit)
- Files: engine.py:463-473, app.py:1218-1226, telegram_ctl.py:41,126,207
- Evidence: `notify` builds the URL with the token and starts a new daemon thread per call, bypassing telegram_ctl's rate
  limiter (20/min) and back-off; a burst (e.g. many CLOSED messages during a flatten) can hit Telegram's 429 and drop alerts.
- Fix: route all sends through `TelegramControl.send` (one queue, one rate limit, token scrubbing in one place).
- Tests: burst of 50 notify() calls -> <=20/min sent, rest queued; token absent from logs.
- Cowork runtime validation needed: no
- Blocks: neither

### ARCH-05 Dead or test-only code and unexecuted research scripts
- Severity: P3
- Status: open
- Evidence class: CONFIRMED (grep: no production caller)
- Origin: new (this audit)
- Files: grid.py:329-614 (`run_grid`, `run_combo`: only tests/test_grid.py:162-173 call them); backtest.py:553
  (`martingale_risk`: only tests); app.py:795 (`research_get`: no caller); engine.py:345-378 (`_egypt_offset` fallback - the
  exe self-test app.py:1311-1312 already requires tzdata) and backtest.py:195-196 (`+3h` fallback, wrong in winter);
  engine.py:208/2578 `LEGACY_LEV_EXC_MAKER` (covers maker records saved only by T03c review-round builds); research_*.py,
  load_data.py (module-level scripts, compiled by `verify` but never run; `research_grid.py` is a single-strategy sweep
  writing `results_single.csv`, unchanged since v2.2, although README.md:67 calls it the "grid / COMBO study").
- Impact: ~400 lines maintained and reviewed without a caller; research tables and preset numbers cannot be reproduced
  by the documented commands after BT01 (`UNVERIFIED_BT01` label, engine.py:317, is the only signal).
- Fix: delete `research_get`, the tz fallbacks, `LEGACY_LEV_EXC_MAKER` once the testnet state has no such record; either
  wire `run_grid` into the Lab or move it with the research scripts under `research/` with a smoke test that imports and
  runs each on a tiny book; fix README.
- Cowork runtime validation needed: yes (confirm no resting maker record with `lev_exception` in the testnet state.json)
- Blocks: neither

### ARCH-06 App main loop is the live scheduler and reaches into engine privates
- Severity: P3
- Status: open
- Evidence class: CONFIRMED
- Origin: new (this audit)
- Files: app.py:560-620 (cycle / manage every 8 s / guards every 60 s / equity 300 s), app.py:547 (`e._kc`), 601-602
  (`e._manage_failed`), 1049 (`e._lev = {}`); grid.py uses `e._market_close/_finish/_add_qty/_replace_stop`
- Evidence: ordering of cycle -> manage -> guards lives in app.py; replay.py:118-146 re-implements a different ordering
  (no periodic guards, no catch-up). A non-exchange exception in `check_guards` is only `log.error`'d by the outer loop
  (app.py:612-614) - surfaced later only as "engine stopped" after 60 s.
- Fix: `Engine.tick(now)` owning the schedule; app and replay both call it; grid gets a small public lot API.
- Cowork runtime validation needed: no
- Blocks: neither

### ARCH-07 Engine.py split: only after injection (ARCH/TEST-01)
- Severity: P3
- Status: open
- Evidence class: CONFIRMED (structure)
- Origin: new (this audit)
- Files: engine.py (2,737 lines, 141 defs): FillWriter 31-212, leverage/exposure 1780-2120 (~340), maker flow 2528-2668,
  governor/risk rules 2296-2490
- Evidence/benefit: these blocks have their own state and tests (test_fills, test_leverage_auto, test_v31_engine) and are
  natural modules; but every seam today is `E.<global>` monkeypatching (TEST-01), so splitting first would silently break
  the replay/causality gates. Recommend: injection -> extract FillWriter and leverage/exposure -> shared pure sizing/
  management core (ARCH-02). No benefit from splitting app.py/panel.html now.
- Blocks: neither

### ARCH-08 Documentation drift on CI and test counts
- Severity: P3
- Status: open
- Evidence class: CONFIRMED
- Origin: new (this audit)
- Files: README.md:47-48, 52, 67, 70; docs/reviews/README.md:19-20; docs/PROJECT_STATUS.md:66
- Evidence: docs say fast runs "on every push" and full "on every pull request ... and on demand (Run workflow)". Actual:
  verify-fast on PRs, push-fast on master pushes only, full only on the `full-ready` label (no `workflow_dispatch`).
  README says "200+ tests" (823 now); README.md:70 omits BT01's doji = worst-case rule; README.md:67 (see ARCH-05).
- Fix: update README and docs/reviews/README.md (PROJECT_STATUS is Codex-owned - flag to Codex).
- Blocks: neither

### TEST-06 No property-based or automated mutation testing where it pays off
- Severity: P3
- Status: open
- Evidence class: CONFIRMED (no hypothesis/mutmut in requirements-dev.txt or tests)
- Origin: new (this audit); CLAUDE.md requires manual "mutation check on key lines" per PR
- Targets that would pay off: `engine._rd/_fmt` + sizing vs exchange filters (qty never above risk, never below min, step
  multiple); `backtest.path_points` / intrabar walk (invariants: stop never filled better than stop price, a level set at
  the adverse extreme never filled in the same candle); `reconcile` (random exchange/lot states -> no order sent when
  unknown, untracked only after two sightings); `ExchangeHealth` circuit (random fail/ok/time sequences -> state machine
  invariants); `trade_audit` ledger (sum of cohort pnl == lot realized). A scheduled mutmut run over sizing/path/reconcile
  lines would replace the ad-hoc per-PR mutation tables.
- Blocks: neither

### TEST-07 pytest config hardening
- Severity: P3
- Status: open
- Evidence class: CONFIRMED
- Files: pytest.ini (only `testpaths`), tests/conftest.py
- Evidence: no `--strict-markers`, no per-test timeout (threads in FillWriter/telegram tests -> a deadlock hangs until the
  30-90 min job timeout), no `filterwarnings=error`. Installer flow tests run on CI under PowerShell 7 (`pwsh` on
  ubuntu-24.04) while production is Windows PowerShell 5.1 (tests/test_installer.py:100-115, 365-367) - semantics differ
  (encoding defaults); 5.1 coverage exists only on the PC.
- Fix: add `--strict-markers`, `pytest-timeout` (e.g. 120 s), keep PS 5.1 runs in verify_release.
- Blocks: neither

### Module -> tests -> critical untested paths
| Module | Tests covering it | Critical untested paths |
|---|---|---|
| engine.py | test_safety, test_v31_engine, test_outage(+_final), test_leverage_auto, test_fills, test_trade_audit, test_t05b_*, test_t0x_interaction, test_testnet_faults, test_telegram, test_grid, test_causality, test_bt_intrabar_path; replays (full) | `kelly_mult`; `load_settings` corrupt/migration (ARCH-01); `apply_preset`; `daily_summary`; bracket/margin-state path under the replay fake (TEST-01) |
| app.py | test_safety (Scrub, cfg, clean_mgmt), test_outage*, test_t05b_startup_outage, test_installer (selftest); test_app_ui.py (full only) | HTTP auth/Host/Origin/size (fast gate), `clean_governor`/`clean_risk_rules`/`validate_sleeve` bounds, main-loop guard failure handling, v2 migrations |
| backtest.py | test_bt_intrabar_path, test_causality, test_v31_engine, test_safety, test_grid, test_lab; replays | Kelly, governor parity with engine, risk-rule parity, sizing rounding (BT02) |
| replay.py | test_causality (fast core 3 modes, slow 3), test_fills (source text), verify replays | slot options not forwarded (kelly/hours/vol/rules/maker) |
| lab.py | test_lab | lab request validation of `mgmt`/`params`, raw governor/risk_rules |
| strategies.py | test_lab (lookahead), test_safety, test_v31_engine, test_bt_intrabar_path | per-strategy signal golden tests (only indirectly via replays) |
| grid.py | test_grid, test_leverage_auto | parity GridManager vs run_grid (run_grid is test-only) |
| binance_client.py | test_safety, test_outage*, test_testnet_faults, test_leverage_auto, test_t05b_* | protocol conformance of fakes vs real client |
| telegram_ctl.py | test_telegram | engine.notify burst/rate limit (ARCH-04) |
| trade_audit.py | test_trade_audit (137 tests), test_t05a_t05b_interaction | policy counterfactuals vs engine exit rules drift (analytics only) |
| ai_filter.py | test_safety (1 ref) | response parsing / timeout fallback |
| verify.py / verify_ci.py | test_verify, test_ci | - (well covered) |
| installer.ps1 / build_app.bat | test_installer (pwsh 7 on CI, PS 5.1 on PC) | PS 5.1 semantics on CI |
| panel.html | test_app_ui.py (full), text/node checks in test_leverage_auto/test_trade_audit | everything in the fast gate |

### Behaviour changes since v3.2-rc2 (this area)
- CI moved from "full on every PR" to fast-per-commit + label-triggered parallel full (T04/T04d), with docs-only reuse on
  master pushes; branch protection requires both checks, strict.
- Tests grew from ~200 to 823; seven are `slow` and run only in full.
- New subsystems each added their own seams rather than shared ones: FillWriter registry (T05), outage circuit with
  `TypeError`/`__dict__` probes (T05b), leverage/exposure (T03c, ~340 lines + legacy maker path), trade audit (T05a,
  1,599 lines with its own exit-policy re-implementations), single intrabar path (BT01, consolidating backtest events - a
  real simplification) plus the temporary `UNVERIFIED_BT01` preset label.
- No change to the engine/backtest duplication (shared core still pending).

### Not verified
- Fast suite on this PC (Windows, venv): `pytest -m "not slow" tests` exit 0 (816 tests), but it shared the CPU with other
  agents' suites, so wall times are inflated; slowest were the 6 core causality/parity cases (16-34 s each) and the
  installer flow tests (4-29 s). Flakiness of the timing asserts (TEST-05) not observed in this single run.
  Slow set (7) and the full gate not run.
- Whether any testnet state.json contains a `lev_exception` maker record (ARCH-05) or has ever been corrupted (ARCH-01).
- Magnitude of backtest-vs-live drift for Kelly/governor/risk-rule slots (ARCH-02) - needs a replay with those options.
- Research scripts still run against the current backtest API (not executed).
