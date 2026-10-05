# T03: Acceptance evidence for the normal (success-path) install

*Collected 2026-10-05, about 20:50 Cairo, by Claude from the owner's PC (read-only). Branch `t03-rollback-drill` at `1d651c2`; implementation commit `24ff38b`. Install run by the owner, started 20:44:03.*

| # | Required evidence | Result | Source |
|---|---|---|---|
| 1 | `build.log`: checksum preflight, tests passed, `BUILD_DONE`, no `ROLLBACK` | ✅ Details below. | `%LOCALAPPDATA%\ZackBot\build.log` |
| 2 | Installed exe hash equals the logged new hash; `.prev` equals the pre-install exe | ✅ Details below. | `sha256sum` of both files |
| 3 | Source mirror carries the new build and matches the T03 source | ✅ `src\build_info.py` = `BUILD_ID = '20261005-204405'`. A recursive diff of the mirror against `C:\Dev\ZackBot2` shows **no differences**, excluding what the installer deliberately does not copy: `tests/`, data folders, `dev_out`, `build_app.bat`, `rollback_drill.bat`, `setup_git.bat`, `build_info.py`, local secrets and logs. | `diff -rq` |
| 4 | Shortcuts updated | ✅ Desktop `ZackBot.lnk` and Start-menu `Programs\ZackBot.lnk` were both rewritten at 20:47 Cairo, the install time. Target: `C:\Users\princ\AppData\Local\ZackBot\app\ZackBot.exe`. | `.lnk` targets and times |
| 5 | New build started; no errors; reconcile | ✅ `bot.log` 20:47:53: `ZackBot 3.2 \| mode=PAPER \| keys=yes`, control panel on 127.0.0.1:8765, `session.json` started 17:47:51 UTC. **0 WARNING/ERROR lines** since start. *The startup reconcile logs only problems; a clean pass is evidenced by the absence of warnings.* | `bot.log`, `session.json` |
| 6 | Every open lot still protected | ✅ 4 lots, each with an exchange `stop_id` and none marked `stop_dirty`. Details below. | `state.json` |
| 7 | Panel Settings shows the new build | Owner confirmed ("ready" after the check). | Owner |

**Item 1 details.** Build id `20261005-204405`:
- `checksum helper ok`;
- installer tests **194 passed, 7 skipped, 6 deselected**;
- new exe self-test `{"version":"3.2","build":"20261005-204405","ok":true}`;
- `backup ok sha256 67528ACA…B79B5D6`; `previous build 20261005-103320`;
- `ping ok: version 3.2 build 20261005-204405`;
- `BUILD_DONE build=20261005-204405 sha256=E0CC259F…EBB09CF`;
- no `ROLLBACK` and no `BUILD_WARNINGS` lines.

**Item 2 details.**

| File | SHA-256 | Equals |
|---|---|---|
| `app\ZackBot.exe` | `E0CC259F6CAE94B836CE472D36B822A419DE37CA914056CAE8F6CE360EBB09CF` | the `BUILD_DONE` hash |
| `app\ZackBot.prev.exe` | `67528ACA8CFB23D535EF6D926B22DE1A01FAEE2FC2F1AB9306D3068E9B79B5D6` | the pre-install exe, and the drill's restored exe |

**Item 6 details.**

| Lot | Slot | Stop |
|---|---|---|
| 1000PEPEUSDT LONG | MOM | 0.0041425 |
| HYPEUSDT LONG | MOM | 86.655 |
| DOGEUSDT LONG | MOM | 0.0924 |
| LINKUSDT LONG | DCA1H | 13.147 |

**Not observable from here:** the exchange-side view of the stops (the panel safety bar / `/api/status` health). The local engine state and the absence of warnings both agree it is healthy. The reviewer's live panel check after the drill (20:32) showed 0 unprotected and 0 orphans, and the same check is recommended once more for the record.

## Combined T03 evidence

| Path | Result |
|---|---|
| **Failure path** (rollback drill, 20:20) | New build failed on purpose; restore was hash-identical and HMAC-confirmed; the bot recovered |
| **Success path** (this install, 20:44–20:48) | New build installed, verified and confirmed; mirror and shortcuts updated only after confirmation; previous exe kept as a verified `.prev` |
| **Tests** | 207/207 on Windows; strict replays unchanged; no engine, trading or exchange code touched |

**Request:** final acceptance of T03. Once accepted:
1. the ROADMAP row gets ✅;
2. `master` is fast-forwarded to `t03-rollback-drill`;
3. T04 (CI, then branch protection) starts.
