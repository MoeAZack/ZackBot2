# ZackBot v3.2: response to the independent v3.1 audit

*2026-10-05, Africa/Cairo. Written by Claude for the reviewer who audited v3.1 ("ZackBot v3.1 — Independent Source Audit").*

This document goes through each finding: what was verified, what changed, and how it is tested. It also reports one new defect that the requested extra verification uncovered, and corrects the v3.1 report.

---

## Summary

**All 11 findings were confirmed and acted on.**
- The three P1 release blockers are fixed and covered by regression tests.
- Of the eight P2 items:
  - six are fixed;
  - one (engine/backtest duplication) is partly addressed: drift is now measured on every check, and the shared-core refactor is scheduled;
  - one (Windows build verification) can only be completed on the owner's PC.

**New: a lookahead in the backtester.**
- Acting on "one reference scenario is not broad equivalence proof", I added a second engine-vs-backtest replay scenario (trailing stops, pyramiding, shorts).
- It failed by **32 points**: engine −26.5%, backtest +5.9%.
- Two causes:
  - the replay harness itself (see §3);
  - a **real backtester lookahead**: trailing stops used the ATR of the candle being walked, which includes that candle's future high/low.
- After both fixes, the gap is **1.4 points**. All research tables were regenerated.

**Agreed on strategy language.** Steady Mix and Active DCA are **testnet candidates, not validated strategies**. The headline numbers are not mainnet expectations. The UI and report wording has been changed to match.

**Verification of this build (cloud sandbox, Linux, Python 3.13, pandas 3.0.6):**
- 174 tests pass.
- Both replay gates pass:
  - default scenario: +56.1% vs +52.1%;
  - scenario 2: −9.7% vs −11.1%.
- UI harness: all tabs and new cards render, no JS errors.
- **Not yet run on Windows:** the build script, the test suite and the exe self-test. The owner's first `build_app.bat` run is that test, and it is designed to fail without touching the running bot.

---

## 1. Release-blocking findings (P1)

### P1-1: adds bypassed the portfolio risk rules. **Confirmed, fixed.**

**What changed**
- `Engine._add_block(lot, qty, price)` is now the single gate for every quantity added to an open lot (DCA safety orders and pyramid adds). It blocks the add when any of these hold:
  - daily loss halt active;
  - entries paused;
  - stop not confirmed (`stop_dirty`);
  - manual lot;
  - slot removed, or slot id reused by another strategy;
  - slot leverage cap exceeded;
  - enforced portfolio rules: coin cap and open-risk cap (with the proposed notional and added stop risk), and funding filter.
- **Added stop risk:**
  - for DCA it is 0, because unfilled safety orders are already counted in open risk;
  - for pyramids it is `q × (price − stop)`.
- **Deliberately not applied to adds:**
  - the correlated-trades cap: an add does not open a new correlated position;
  - the BTC breaker: it pauses *new* trades. Blocking DCA orders mid-plan during a crash changes the basket's planned average. Open to discussion.
- **Recording:** a blocked add is logged once per reason, stored on the lot (`add_blocked`), added to the missed list (`kind='add_blocked'`), counted in health, and shown on the position card ("Adds paused: …").
- **Warn-mode rules** never block; they log once.
- **The backtester got the same gate**, so live and backtest stay in parity: `rule_block(..., add=True)` on both add paths, plus "no adds during a daily halt".

**Tests** (`tests/test_safety.py`)
- coin cap blocks a pyramid add, which proceeds again once the rule is off;
- open-risk cap blocks a pyramid add;
- warn mode never blocks;
- coin cap blocks a DCA safety order;
- no adds while paused or halted.

**Side effect on the research numbers:** the 2-year Balanced result went from $5,974 to $5,925 and Boost from $23,792 to $23,598. Pyramid adds no longer happen on halted days.

### P1-2: orphaned or reused slots could add without a leverage cap. **Confirmed, fixed.**

- Each lot now stores the slot's `share` at entry. Plans carry it through maker and trailing entries.
- The add cap uses `min(current share, share at entry)` × leverage × capital.
- **A missing slot, or a slot id now used by a different strategy:** no more adds. Stops, exits and exit signals continue through the existing orphan handling.
- **Legacy lots** (no stored share) fall back to the current slot's share.
- **Tests:** a removed slot gets no adds but keeps its stop; a reused id with a different strategy and a larger share gets no adds; a shrunk slot blocks on the share recorded at entry.

### P1-3: the installer could relaunch a stale exe after a failed build. **Confirmed, fixed.** `build_app.bat` was rewritten.

**New build sequence**

| Step | What happens |
|---|---|
| Copy | `robocopy /MIR` into a **fresh** staging folder (deleted first); `build_info.py` is stamped with a build id |
| Environment | Pinned build environment only. **No fallback to global packages.** `requirements-dev.txt` adds pytest |
| Library check | Aborts on failure |
| Tests | Runs the safety tests; aborts on failure |
| Build | PyInstaller into `staging\dist`; aborts on a non-zero exit or a missing exe. A fresh folder means an old exe cannot satisfy the check |
| Self-test | `ZackBot.exe --selftest out.json` runs the **new** exe, which checks: imports of every lazily loaded module, `panel.html` and `research/` present in the bundle, Africa/Cairo time-zone data, presets and strategies present. The script requires `ok == true` **and** the build id equal to the stamped one |
| Install | **Only now** is the running bot stopped. The old exe is kept as `ZackBot.prev.exe`; the copy is retried 5 times; if it fails, the previous exe is restored and restarted |
| After install | Source mirrored to `%LOCALAPPDATA%\ZackBot\src`; shortcuts point at the exe only. The `pythonw app.py` fallback is gone |

- **On any failure** the window shows the reason and pauses. The bot that is running stays as it was.
- **Build id:** the running build id is shown in Settings, `/api/status`, `/api/meta` and `/api/ping`.

**Caveat:** written and syntax-reviewed in the sandbox, but never executed on Windows. Expect the first real run to be the test.

---

## 2. Other findings (P2)

| # | Finding | Verdict | What was done |
|---|---|---|---|
| P2-1 | "154 pass" not reproducible: `load_data` missing, no pytest requirement | **Confirmed** | Test uses `pytest.importorskip('load_data')`; `load_data.py` now shipped; `requirements-dev.txt`; `run_checks.bat` / `run_checks.sh`. New `tests/conftest.py` forces a throw-away `LOCALAPPDATA` for every test: `test_grid.py` used `setdefault`, which on Windows would have pointed at the **real** data folder |
| P2-2 | UI harness and research not reproducible | **Confirmed** | Shipped: `test_app_ui.py` (output path via `$ZB_OUT`), `test_engine_sim.py` (now a pass/fail gate), `replay_scenario2.json`, all research scripts, `research_refresh.py` (regenerates all five 2-year tables), and `DATA_MANIFEST.json` (sha256, rows and date range for all 64 candle files, plus source and survivorship note). The candle data is copied to the PC next to the source. The reconstructed winner-variant definitions are documented in the script; the unaffected rows reproduce the old numbers exactly |
| P2-3 | Partial nested `dca` / `pyramid` overrides → `KeyError` | **Confirmed** | `strategies.merge_mgmt()` merges one level deep (generic defaults ← strategy defaults ← slot override); used by the engine, backtester and lab. Lots saved by older versions are repaired on load. Tests cover the engine, legacy state and the backtester |
| P2-4 | UI time follows the device, not Cairo | **Confirmed** | All clock times are formatted in Africa/Cairo and the trading-day keys use the Cairo date; the footer says "Cairo time". Chart tick *positions* still follow the device clock, but their labels are Cairo times |
| P2-5 | Blank target looks broken | **Confirmed** | `Engine.exit_plan(lot)` returns a title, an ordered list of steps and the next trigger (see below). Shown on every position card (expandable "how this trade closes"), in the chart modal and in Telegram `/positions` |
| P2-6 | AI review claimed news/delisting knowledge and always said 4h | **Confirmed** | Prompt v2 states it has **no** news, listing or order-book data, uses the real timeframe and side, and labels returns by candle count. The log records model, prompt version, timeframe, latency and error. Test added |
| P2-7 | Engine and backtester are separate implementations | **Agreed** | Not refactored yet; it is first in the roadmap ("shared core"). Drift is now measured: two replay scenarios with a ±15-point gate and an exact position/stop reconciliation check. Scenario 2 immediately found the defect in §3 |
| — | Windows end-to-end | Open | Needs the owner's PC: `build_app.bat` (tests + self-test), then `run_checks.bat` |

**Exit-plan contents (P2-5)**
- *Title*, one of:
  - "No fixed target — lets the winner run";
  - "Basket target X";
  - "Fixed target X";
  - "Manual trade: stop only".
- *Steps*, as they apply:
  - DCA progress;
  - take-profit ladder levels;
  - partial-TP level;
  - breakeven trigger;
  - trailing stop (ATR);
  - trailing TP;
  - runner rules;
  - the next pyramid level;
  - time exit;
  - "or the strategy's own exit signal";
  - the protecting stop;
  - any blocked add.
- *Next*: the nearest known trigger price.

---

## 3. New defect found during verification: same-candle ATR lookahead

**How it was found.** I added a second replay scenario: the live engine driven by a simulated exchange vs the backtester, with `breakout_pyramid`, `bear_breakdown` and `donchian_ens`.

**First result:** engine −26.5% vs backtest +5.9%. Isolated per slot:

| Slot | Engine | Backtest |
|---|---:|---:|
| breakout_pyramid | −28.6% | +15.8% |
| bear_breakdown | −26.3% | −11.8% |
| donchian_ens | −34.1% | −28.1% |

**Cause 1 — the harness (not the bot).**
- The replay fed every candle low→high, but the backtester assumes high→low on red candles.
- It also jumped straight to the extremes, so pyramid adds filled at the candle's high. The live bot polls every 8 s, so it fills near the add level.
- **Fix:**
  - green candles are walked O→L→H→C and red ones O→H→L→C (the same convention as the backtester);
  - each leg is walked in 6 steps (`ZB_SIM_STEPS`).

| Stage | breakout_pyramid gap | bear_breakdown gap |
|---|---:|---:|
| Before harness fix | −44.5 pp | −14.6 pp |
| After harness fix | −21.0 pp | −3.8 pp |

**Cause 2 — the backtester (a real lookahead).**
- In `backtest.run`, open positions in candle *i* used `atr = a['atr'][i]` for the trailing stop.
- That is the ATR **including candle *i*'s own high and low**, which the live engine cannot know while the candle is forming.
- On breakout candles ATR jumps, so the backtest trailed wider than possible and held winners longer.
- **Fix:** `a['atr'][i − 1]`.
- **Result:** breakout_pyramid gap −21.0 → +1.9 pp; full scenario 2 gap −32.3 → **+1.4 pp**.

**Effect on results.** It affected every slot with `trail_atr` (Donchian, Breakout, Squeeze, Bear breakdown, Hot-coin) and the winner-handling variants that add a trail. The effect goes both ways, because the previous candle's ATR can be narrower or wider.
- 2-year single strategies at 3% risk:
  - Breakout core8 both: $915 → $1,660;
  - Squeeze top40 long: $654 → $272.
- **The main 4h presets have no trailing stop and are unaffected.**

**The existing lookahead check missed it**, because it only compares indicators and signals computed on truncated vs full history. A new truncation-invariance test was also added for management across candles (6 strategy/management combos). It would not catch this *intra*-candle case either; the replay gate does.

**Other corrections to the v3.1 report**
- **Costs:**
  - the taker fee in the backtester is **0.05%**, not 0.04%;
  - slippage is **0.02%** per fill;
  - funding is **0.005% per 4h bar**.
- **Engine-vs-backtest replay:** the published "+62.3% vs +57.8%" was produced with the old harness. With the corrected harness: **+56.1% vs +52.1%**.

---

## 4. Updated numbers (fixed backtester)

### Long history, core 8 coins (4h from 2022-01-25, 1h from 2022-09-03)

| Profile | $500 → (v3.1 report) | $500 → (v3.2) | Max DD (v3.1 → v3.2) |
|---|---:|---:|---|
| Original / Calm / Balanced / Boost (4h) | unchanged | unchanged | unchanged |
| Aggressive | 6,921 | 6,916 | −45.2% |
| Active (DCA 1h + Breakout 1h) | 3,172 | **3,947** | −63.2% → **−49.9%** |
| Active + Tight ladder | 599 | 635 | −66.7% → −65.2% |
| Boost+Active mixed (from 2022-09) | 7,726 | 8,112 | −40.2% → −32.5% |
| Active DCA (1h) | 4,637 | 4,637 | −25.9% |
| **Steady mix** (½ Balanced bull-only + ½ DCA 1h, from 2022-09) | 4,015 | 4,015 | −14.7% |
| Balanced / Aggressive / Boost, trend slots bull-only | unchanged | unchanged | −22.1% / −30.0% / −31.0% |

Monte Carlo for Active, daily blocks:
- median max DD −63.5% → −60.6%;
- P(falling below half the start) 22.7% → **18.2%**.

**Active's conclusion stands:** its 1h breakout slot doubles the drawdown of DCA-only (−50% vs −26%).

### 2-year tables (40 coins)

| | Before | After |
|---|---:|---:|
| Balanced | 5,974 | 5,925 |
| Aggressive | 11,886 | 11,917 |
| Boost | 23,792 | 23,598 |
| Active (6 months) | 961 / −24% | 935 / −26% |
| Boost+Active (6 months) | 1,196 / −32% | 1,575 / −38% |

The Boost+Active 6-month figure is a side-by-side 4h + 1h simulation.

### v3.1 options on Balanced / Boost (2 years, `research/results_v31.csv`)

| Option | Before | After |
|---|---:|---:|
| TP ladder | 1,866 | 1,866 |
| Trailing TP | 1,354 | 1,354 |
| Maker entries | 6,181 | 6,131 |
| Pump guard | 4,794 | 4,755 |
| Bull-only | 3,722 | 3,722 |
| Risk rules enforce | – | 5,790 (−28.0%) |
| Governor halve | 15,547 | 15,486 |
| Governor × 0.33 | 10,718 | 10,622 |
| Martingale recovery | 14,807 / −69% | 17,688 / −67%, worst month −33% |

**The verdicts are unchanged.** Every row is now regenerated by `research_refresh.py v31`.

### Engine replay gates

| Scenario | Before | After |
|---|---|---|
| Default (MOM pyramid + DCA + Squeeze both-sides) | +62.3% vs +57.8% (old harness) | **+56.1% vs +52.1%**, positions/stops match |
| Scenario 2 (Breakout pyramid + Bear shorts + Donchian both-sides) | −26.5% vs +5.9% | **−9.7% vs −11.1%**, positions/stops match |

---

## 5. Strategy interpretation: agreed

All six caveats the reviewer listed are accepted:
- survivorship bias (8 surviving large caps, no LUNA/FTT);
- the 1h history starts after most of the 2022 collapse;
- presets selected on overlapping data;
- an optimistic maker-fill model;
- block-bootstrap Monte Carlo understating clustered DCA drawdowns;
- funding modelled only as an average.

**Wording now used:** Steady Mix and Active DCA are *leading testnet candidates*. The mainnet expectation is "unknown until live testnet fills are compared with the backtest".

**Planned before any mainnet decision (roadmap):**
- a point-in-time universe including delisted coins;
- replays of the LUNA and FTX weeks;
- synthetic gap shocks;
- longer bootstrap blocks;
- parameter perturbation;
- fill calibration from logged testnet orders.

---

## 6. Release-gate status (the reviewer's 8 items)

| # | Gate | Status |
|---|---|---|
| 1 | Portfolio rules on adds | Done + tests |
| 2 | Immutable cap basis / orphan adds | Done + tests |
| 3 | Fail-closed, version-verified build | Done; **first Windows run pending** |
| 4 | Clean full test run, test deps | Done (174 pass); Windows run happens inside the build |
| 5 | Regression tests: add risks, partial settings | Done |
| 6 | UI harness shipped; exe reports version | Done (`--selftest`, build id in Settings and API) |
| 7 | Exit-plan wording, Cairo time | Done |
| 8 | Testnet observation with fill/reconcile telemetry | Ongoing; per-fill logging for fill-model calibration is on the roadmap |

---

## 7. Open questions for the reviewer

1. **BTC breaker and DCA adds.** Should an active BTC circuit breaker also pause DCA safety orders, as it pauses new trades? Pausing leaves a smaller basket than planned. That means less loss if the crash continues, but a worse average if it reverses.
2. **Replay gate tolerance.** It is 15 points of total return over ~150 days, plus exact position reconciliation. Would a per-trade metric be stricter and still robust? For example: matched-trade share ≥ 90%, and the median per-trade R difference ≤ 0.1.
3. **Shared core.** For the shared-core refactor, would you extract the management step as a pure function of (lot state, candle-path point, settings) → (actions, new state)? It would be called by both the engine's mark loop and the backtester's intrabar walk.
4. **Intrabar leaks in CI.** What is the cheapest additional CI check that would have caught the same-candle ATR leak without a full replay? One idea: perturb only the unfinished candle's high/low beyond the path point and assert no decision changes before that point.
