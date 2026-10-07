# DCA off (owner decision 2026-10-07): review request

**Owner decision:** "turn DCA off until we can find a better margin strategy for short-term regimes".
**Branch:** `dca-off`, based on master `ef6abc0` (independent of BT02; DCA presets already labelled unverified by FBL-BT01).
**Scope:** TESTNET only. No DCA engine or backtester logic is deleted. Research can still run DCA, but only when it asks for it.

Label shown everywhere: **`DCA off — paused pending a better short-term strategy (owner decision 2026-10-07)`**
(`strategies.DCA_OFF_LABEL`). Reason text for skipped signals and blocked adds: **`DCA paused (owner decision)`**.
Its audit funnel code is `filter/dca_paused`.

## What counts as DCA

`strategies.uses_dca(slot)` is true for the `dca_dip` strategy and for any slot whose effective management (`merge_mgmt`)
has a `dca` block. This is the same test `engine.open_lot` and `backtest.run` use when they build safety-order levels,
so a `dca` override on another strategy counts too.

Inventory of where DCA could run:

| Place | DCA? | Handling |
|---|---|---|
| `engine.PRESETS` calm, balanced, aggressive, active, boost_active, steady_mix, active_dca, boost | DCA / DCA1H slots | Presets are **not edited**. Each preset gets `dca_slots`. The slots stay in the profile but open nothing while DCA is off (entry gate). The panel shows the label. |
| `original` (default `PRESET`) | no | unchanged |
| Custom slots (`/api/sleeves`), `dca` mgmt on any strategy | possible | same entry gate |
| Governor auto profile switch (`apply_preset`) | can load a DCA profile | same entry gate, so its DCA slots stay inactive |
| "Take now" (`take_signal`), Telegram (no profile commands; take/close only go through the engine) | possible | `entry_block` gate |
| Trailing (armed) entries | possible | re-checked through `entry_block`, cancelled as `trailing entry cancelled: DCA paused (owner decision)` |
| GRID slots (`grid.py` live) | no DCA | unchanged |
| COMBO (`grid.py`, `research_grid.py`) | backtest-only research model | unchanged (research) |
| Manual trades | no DCA (stop_atr / tp_r only) | unchanged |

## Decisions

1. **One setting: `DCA_ENABLED`, default `False`** (`GLOBAL_DEFAULTS`, from `strategies.DCA_ENABLED_DEFAULT`).
   `load_settings` keeps it on only for an explicit JSON `true`. A missing key (every settings.json written before
   this change), `"yes"` or `1` all load as off. **So the installed testnet bot stops opening new DCA baskets as soon
   as it runs this build.** Profiles and slots are not rewritten. Switching DCA back on returns exactly the old setup.
2. **Entry gate.** `entry_block` returns the literal `'DCA paused (owner decision)'` for a DCA slot while DCA is off.
   This covers automatic entries, Take now, trailing entries and maker-fallback re-checks. The cycle stores the result as
   a normal not-taken missed record with `stage/code = filter/dca_paused` (new `_PREFIX` row in `trade_audit`, so the
   reason-literal scan test stays green).
3. **Open DCA baskets (the main decision).** A basket that is already open is still fully managed. Its stop order on
   Binance is untouched, the basket take-profit and runner `dca_frac` keep working, time and signal exits keep working,
   and the open risk and leverage reservation still count its unfilled levels (conservative, and correct if DCA is turned
   back on). What changes is that it gets **no new safety orders**. `_add_block` returns the same reason for a lot with
   `levels` while DCA is off. That reason is logged once (`add_blocked`, `safety_order blocked: DCA paused (owner
   decision)`) and shown in the exit plan as "adds blocked: ...". The result is smaller exposure, the same stop and a TP
   based on the current average.
   If DCA is switched back on, the next manage pass places every level the price has already crossed, at market. This
   is how any lifted add block (breaker, outage, leverage cap) already behaves. Documented here, not changed.
   Pyramid adds are not affected.
4. **Backtester.** `backtest.run(..., dca_enabled=True)`. The default stays `True` because this is the research API: it
   runs the slots it is given, which keeps every existing research script, `verify.py` replay 1 and all parity and
   causality tests as they were. With `dca_enabled=False`, a DCA slot takes no entries and is counted in
   `cv.attrs['blocked']['dca_paused']` and `cv.attrs['dca']`.
   **The defaults users run follow the setting.** The app's `/api/backtest`, the profile study and `/api/lab` pass
   `run_options.dca_enabled = engine DCA_ENABLED` unless the request sets it. `lab.validate_lab_request` defaults it
   to `DCA_ENABLED_DEFAULT` (off) when called without the app. Results carry `dca = {enabled, label, paused_signals,
   paused_slots}`.
5. **Replay / parity harness.** `replay.run_replay(..., dca_enabled=True)` sets the engine's `DCA_ENABLED` and the
   backtester's `dca_enabled` together. It is research, so DCA runs explicitly on both sides. The synthetic
   "DCA basket" causality mode therefore still trades DCA. Its existing "no trades = meaningless" guards
   (`seen > 0`, `trades_engine >= 2`) would fail if it didn't, so the test is not weakened.
6. **UI (A-class, additive).**
   - Dashboard controls get a switch, "DCA trades (safety orders)". The note under it shows the label while DCA is off.
     Turning it on needs a `confirm()` and posts `{DCA_ENABLED: true}`. The API accepts only a JSON boolean.
   - Profile cards show the label tag ("this profile opens no trades" when every slot is DCA, otherwise "its DCA slots
     stay inactive") and mark each DCA slot "inactive (DCA off)".
   - The backtest and Lab profile selectors add "· DCA off (...)".
   - Run options get "Include DCA slots (research)", which sends `dca_enabled: true`.
   - Backtest results show a note naming the inactive slots and how many signals were skipped. Lab results show a tag.
   - `/api/meta` presets carry `dca_slots` and `dca_off` (the label while off), plus a top-level `dca`. The research
     table gets a `dca_off` label next to the BT01 `unverified` label.

## Files

- `strategies.py`: `DCA_ENABLED_DEFAULT`, `DCA_OFF_LABEL`, `DCA_PAUSED_REASON`, `uses_dca()`
- `engine.py`: default, strict-bool load, preset `dca_slots`, entry gate, add gate (two one-line gates)
- `trade_audit.py`: reason code `filter/dca_paused`
- `backtest.py`: `dca_enabled` option, signal-time skip, `cv.attrs['dca']`
- `lab.py`: `dca_enabled` run option (validated bool, default off), result `dca` block
- `app.py`: settings toggle (bool only), meta labels, backtest / study / lab defaults from the setting, result `dca`, research label
- `replay.py`: explicit `dca_enabled` (default True) on both sides
- `panel.html`: switch, preset labels, selector suffix, research run option, result notes
- Tests: new `tests/test_dca_off.py` (23). DCA-mechanics tests opt in explicitly with `e.S['DCA_ENABLED'] = True`:
  `test_safety` (4 tests plus the `_dca_lot` helper), `test_v31_engine::test_engine_refuses_dca_without_stop`,
  `test_trade_audit::test_engine_records_the_entry_and_dca_add_regime_causally_and_flags_the_close`.
  No assertion was changed.

## Evidence

- **New tests (`tests/test_dca_off.py`, 23):**
  - defaults and label
  - `uses_dca`
  - migration of an old settings.json (missing / true / false / "yes" / 1)
  - cycle with FakeX: DCA slot skipped with the missed record and `filter/dca_paused`; the non-DCA slot still trades
  - every entry route gated (open_lot, take_signal, mgmt-dca on another strategy)
  - a trailing entry armed while on and triggered while off is cancelled with the DCA reason
  - reason scan includes the literal
  - open basket while off: no safety orders, qty / TP / stop / stop_id unchanged, logged once, exit plan line, basket TP still closes it
  - open basket while off: the Binance stop order is unchanged and a stop fill is reconciled as `stop`
  - explicit enable gives the old path (weights 1.5 / 2.25 / 3.375, levels, TP re-computed after each fill, basket_tp)
  - re-enable resumes the levels
  - backtest of DCA presets off vs on
  - backtest off equals the run without the DCA slot (trades and curve), and default equals explicit on
  - Lab default off, explicit on, bool validation, result label
  - app settings toggle is bool-only and persisted
  - meta labels / dca_slots
  - backtest and lab requests follow the setting unless asked (non-dict `run_options` still rejected)
  - app backtest job on Boost: label plus no `dca_dip` trades vs DCA trades when enabled
  - panel strings
  - Also passes under the CP1252 shim.
- **Parity with base** (scratch scripts, base master `ef6abc0` vs this branch): default backtests of balanced / boost /
  steady_mix (trade-table hash and final equity) are **byte-identical**. A DCA engine scenario with DCA_ENABLED on
  (TP path and stop path: lot state per mark, fills, history, exchange stops, exchange calls) was byte-identical against
  the earlier base; the engine DCA path is unchanged by the rebase and the opt-in DCA-mechanics tests pass on master.
- **Mutation check: 14/14 killed.**
  - entry gate, add gate, strict-bool load, default off, `uses_dca` mgmt branch, backtester gate, reason-code row
  - lab default, app settings bool check, app backtest default, app lab default, meta label (all by `test_dca_off`)
  - replay engine flag, replay backtester flag (both by `test_bt_intrabar_path`)
- Suites (run file by file) and causality (one-case driver, max 2 parallel): see the PR comment / report.

## Questions for the reviewer

1. Should open baskets keep adding safety orders until they close (finish the plan) instead of freezing? This build
   freezes them: less exposure, same stop.
2. Should the shipped `research/*.csv` DCA rows be hidden rather than labelled?
