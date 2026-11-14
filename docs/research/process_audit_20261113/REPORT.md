# Process audit — 13 November 2026

ROADMAP FIT: independent process audit and owner-approved catalog/sieve research. DEFERRED: runtime account changes, activation, mainnet, PR merges/closures and optional strategy variants.

## 1. Runtime traceability

Master verified during this audit: 9cb4341f2d94c21434492515b8e85f2b42c148ce. Accounts run deployed copies, not automatically this head. Data folders below are under F:/ZackBot2_data. Start times are Cairo.

| Instance | Port | Data folder | Process start | Source or build proof |
|---|---|---|---|---|
| Account 1 | 8765 | cohort_launch_20261108/runtime/ZackBot | 11 Nov 23:56:43/44 | v3.2 build 20261103-004246; executable SHA256 F373EBA5C39A60A1B18E71FBCB3991051DA6C21AEA610DEE0775C16501B5CC26; Git commit unproven |
| Account 2 | 8766 | OLD3-account2/runtime/ZackBot | 12 Nov 07:56:08 | v3.2 build dev; 48 top-level Python sources match 640292a20527ab42d523f359462b30042d3066fe |
| Account 3 | 8767 | ACCOUNT3-breakeven/runtime/ZackBot | 12 Nov 10:19:24 | v3.2 build dev; 49 top-level Python sources match source-equivalent 7b5beecb, also 0547b96 |
| Account 4 | 8768 | EXPLORATION-account4/runtime/ZackBot | 12 Nov 14:33:58 | v3.2 build dev; 48 top-level Python sources match 640292a, plus local H5 and stop-probe helpers |
| Overview | 8769 | dashboard-hub | 12 Nov 08:28:50 | hub.py and index.html hash-pinned; no separate trading book |

Account 1 starts C:/Users/princ/AppData/Local/ZackBot/app/ZackBot.exe with its external data-folder override. Accounts 2–4 use start_old3.py, start_account3.py and start_account4.py in their respective instance roots. Overview source is dashboard-hub/hub.py. The watchdog script is F:/ZackBot2_data/ZackBot-PAPER-watchdog.ps1. These local deployment files were outside master. Draft PR [341](https://github.com/MoeAZack/ZackBot2/pull/341), head 021cfa9bae1a7516956404c2a6b5b1a1fb88618b, captures 13 deployed operational sources, hashes, redacted task XML and retirement evidence under ops/windows/deployed_paper_snapshot_20261113/. No settings, installs, keys, session tokens or logs are included.

Source-file agreement is not a complete build receipt: dependencies, resources and loaded process bytes are not proven by it. Account 1's exact Git commit remains unknown; the executable hash/build is the reliable identity. Two overview processes were observed; neither was stopped. All five listeners responded to read-only HTTP requests; protected routes returned 401 without credentials. Public ping provided the build labels above.

ZackBot-PAPER-Instance-Watchdog is Ready. Its exported definition includes BootTrigger, LogonTrigger, S4U and restart-on-failure every minute, count 10. Its existing launcher checks listening port and session-owner PID before starting a missing instance. Script and data are on local F: storage. No reboot or task edit was performed. This audit does not establish successful recovery before Windows sign-in; S4U also lacks ordinary network credentials, although public internet endpoints do not inherently require those credentials. A real reboot proof remains deferred while account 1 has positions.

## 2. Account health

Latest read-only local snapshot: 13 November **10:23:37 Cairo (08:23:37 UTC)**. All four account processes were present, and all four recorded the 10:00 Cairo 4h cycle. Account 4 also recorded the 10:15 15m and 10:01 1h cycles. Cycle fields are bar/cycle timestamps, not process heartbeats.

| Account | Open lots | Protective-stop evidence |
|---|---|---|
| 1 | XRP short 356.5 | 1.1567, algo a:1000000279710713, bot confirmation 64 seconds old |
| 2 | Flat | No open lot requiring a stop |
| 3 | BNB short 0.98; LINK short 35.26 | 644.58 and 10.443, confirmations 77 and 67 seconds old |
| 4 | ADA long 1232; ADA long 1334; XRP short 173.4 | 0.1637, 0.1642 and 1.0549, confirmations 32, 32 and 69 seconds old |

All six open lots have distinct recorded stop IDs, miss=0 and dirty=false. These are cached bot confirmations, **not an independent authenticated exchange-order quantity/coverage audit**. Do not infer quantity-sized protection or correct allocation of the two ADA long stops from this snapshot alone. No key was read or printed. Settings/install/cohort file hashes for all accounts match the pre-audit capture; natural state changes and account 4's new ADA lot are from the running bot. Account 1 was not modified or restarted.

## 3. H5 hourly runner

At 12 November 22:00 Cairo (20:00 UTC), ADA's input fetch failed with an SSL error while eight other mutable input files had already been appended. The runner writes fetched inputs before posting its row and advancing its cursor. Its next attempt compared old cursor hashes with those appended inputs and repeatedly raised cursor_data_changed. This does not by itself prove historical candle revisions.

The obsolete standalone H5 runner was retired, as authorized by the audit request. A RETIRED.json marker was added, and only the research-job guard was taught to honor it. The exact H5 process and research guard were stopped; the guard was restarted and now reports H5 RETIRED without respawning it. Account processes, FWD workers and scheduler definitions were not changed. Earlier H5 rows, source, freeze, cursor and heartbeat hashes are preserved. The sole authoritative H5 forward record is Claude FWD-BOOKS H5, original freeze 8f83494b plus A1 4a349fe9; published rows remain under docs/research/fwd_books/rows. Its publication is manually Claude-invoked, not a newly automated service.

## 4. Promotion bar

At historical row 1794542400000, 13 November 06:00 Cairo, REG:momentum_burst_scalp had 11 closed trades and +$54.5877 at 15 bp. Four other arms were negative: exp_scalp -$195.7437, exp_structure -$248.7697, failed_breakout_scalp -$241.5427 and quick_bank_runner -$245.3877. These are simulated forward-arm results, not account P&L, and use the frozen funding stress proxy.

I agree: no arm is proposed for promotion below 30 closes. Ten closes is an early monitoring flag only. Thirty is necessary, not sufficient: cost robustness, concentration, multiple-testing selection, protection and independent evidence still apply. The frozen FWD-01 file was not changed. Eleven positive closes among 63 arms are not established edge.

## 5. Trial count and one catalog

The global total of distinct tested hypotheses remains UNVERIFIED. Neither the highest C number, 63 arms nor the 403 inventory records is that total. Completed experiments, configurations, aliases and adapter code must be reconciled before counting. The proposed single master register is docs/research/strategy_catalog.json in this draft PR. Branch discovery covers all 329 enumerated heads, including C-numbered work, H1–H6 and all 63 FWD-01 arms; unresolved historical metadata is explicit. No complete validated global list previously existed. See ../catalog_sieve_20261113/README.md for exact scope, pass rule, data authority and candidate-specific coin contamination.

## 6. Open PR disposition

No PR was merged or closed. Recommendations, with exact heads and evidence, are in PR_DISPOSITION.txt. #293 and #318 remain merge candidates only after their current failures are fixed and independently reviewed on the exact integration head. #293 fails a date-sensitive Telegram test; #318 fails the frozen-ancestor fixture, and its old pass is for a different head. #319, #320 and #322 should close as superseded by #330/master. Other clear closure candidates are #108, #98 and #240; #214 first needs its failure evidence preserved. #294/#296 should close only after their containing #329 is accepted. #297 is not included in #329 and must not be swept away. Age or a research-only label is not sufficient to call other PRs dead.

## 7. Writing and next action

GitHub updates use normal sentences and spaces. Enabling audit artifacts are not reported as profitable-strategy output. The active strategy-economic path is the owner-approved catalog sieve: Claude freezes first EMA/C101 case manifests before outcomes; Codex independently checks their signal tuples and samples cash records once posted. Daily FWD-BOOKS rows do not block this work. No account, activation or mainnet change follows from this report.
