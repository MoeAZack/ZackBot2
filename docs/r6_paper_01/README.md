# R6-PAPER-01: r6_regime_flip_4h as a default-OFF PAPER strategy for account 4

Status: **code only, NOT DEPLOYED.** No slot was added (live or disabled), no account, key, runtime, scheduler or
order action was taken. The first canary, when Codex accepts it, stays at max_pos 1.

Evidence this builds on: candidate source `acc8d0ee` (research/longscout_v6.py, R1 from research/longscout_v1.py),
Codex independent screen pass (#347 comment 6874885569), Codex deployment-fit freeze `7a81c7fd`
(docs/research/regime_flip_testnet_fit_20261114, freeze.json sha256 `50f6e767…`, 163 symbols), native fit
confirmation PR #349. These are results on data that has already been used, not proof of future profit.

## Files

| File | What it is |
|---|---|
| `r6_regime_flip.py` | Strategy. Import registers nothing. Frozen R1 core is copied verbatim; D01 admission; fail-closed live gate; `register_for_paper` only for mode `paper` at an exact testnet HTTPS origin; `unregister` cleans up. |
| `r6_paper_overlay.py` | Account-4 overlay, called from a launcher **after** `engine.connect()`. Changes no deployed file. |
| `tools/r6_sizing_receipt.py` | Read-only receipt. Runs the real `open_lot` of a given source tree on a copy of a real settings.json with a fake exchange. |
| `tests/test_r6_regime_flip.py` | 61 targeted tests. |

## Rule (frozen, unchanged)

- **R1** works on completed daily closes built from complete 4h bins (6 of 6). `sma` is the 100-day mean and `slope = sma - sma[20 days earlier]`.
  - Rising: close > sma and slope > 0.
  - Falling: close < sma and slope < 0.
  - Unknown before 120 closes.
  - A 4h row sees a day once that day has closed. Flips therefore happen only on the 20:00 UTC row (tested).
- **Entry:** long on the first rising row, short on the first falling row. The engine enters on the next bar.
- **Exit:** the regime leaves the state, checked at the row close. The initial stop is 3 ATR (ATR of the decision bar).
- **Time exit:** the research exits when `i - i_entry >= 120`, that is at the close of bar entry+120.
  - The engine instead counts wall-clock bars since the lot opened. That moment is a little after the entry bar's open.
  - With `max_bars: 120`, the engine would exit **one bar early** (at the close of entry+119) whenever the later cycle runs
    as late as the entry cycle did. The adversarial review found this, and the test reproduces it.
  - The PAPER entry therefore uses `max_bars: 120.5`. That lands on the close of entry+120 for any cycle delay under 2 hours (tested at 120.0, 120.4, 120.6 and 121.0 bars).
  - This is the same rule, expressed in the engine's units.
- **Nothing else:** no take-profit, break-even, trailing, pyramid, DCA or runner. `merge_mgmt` gives exactly `{stop_atr: 3.0, max_bars: 120.5}`.

## Admission (Codex fit D01, frozen 163)

- **Symbols:** only the frozen 163 symbols (sha256 of the newline-joined list `7a28fc2d…`). An entry on any other symbol is refused.
- **D01 liquidity,** checked at the decision bar's last 1h candle (t+3h). The data is closed 1h public candles fetched
  for `[decision day − 31 d, decision open + 4 h)` in one request (≤ 768 candles, weight 5). Both conditions must hold:
  - a contiguous 1h run of at least 720 candles;
  - the median of the prior 30 complete (24-candle) UTC days' quote volume is at least 10,000,000.

  The code is the fit runner's formula. It is tested against Codex's lines verbatim on 60 random gapped series × 12 decision times.
- **Fail closed.** An entry is emitted only when every check below passes. Otherwise the signal is false and the reason
  goes to `LAST_GATE` and the log.
  - The row is the freshly closed decision row (by wall clock), and the check runs within 20 minutes of its close. There is no late
    entry from a manual run or a start-up catch-up, because the research fills only at the next open.
  - The account settings still pass the overlay guard, re-checked at every decision: PAPER, exact origin, no book mode,
    market entries, no rotation preset, and EX14 present and on. A switched-off slot spends no 1h fetches.
  - The 4h frame is gap-free over the last 768 rows.
  - The symbol is resolved and is in the 163.
  - The 1h data was fetched and passes D01.
  - The bar's fetch budget is not used up. The budget is 163 per decision bar, so every coin can be checked even on a burst
    day. In the repo data, 26 of 40 coins flipped on 2025-01-12. Fetches go through the pacer.
- **Exits are never gated.**
- **Symbol resolution.** The strategy receives only the DataFrame. The overlay resolves its symbol by object identity
  with `engine._kc`, the engine's own closed-candle cache, which `compute_signals` passes straight through. The end-to-end
  test runs the real `engine.candles` → `compute_signals` → `strategies.signals` path.

## Two hazards found (why no slot goes in early)

1. **Panel lockout.** `app.clean_symbols` refuses lists longer than 80, and `/api/sleeves` re-validates every slot on
   each save. With a 163-coin EX14 in settings, every panel slot save would fail, including the documented rollback
   `EX13 enabled:false`.
   - The overlay therefore wraps `validate_sleeve` and `clean_symbols` in-process. They accept **exactly** the frozen list,
     and only while validating slot EX14 of this strategy.
   - The tests show the stock panel refuses the list. They also show these are still refused: 162 coins plus ICP, the
     first 81 coins, the list in reversed order, the list on another slot id, and the list with another strategy.
   - `restore_panel` puts the originals back.
2. **Data cost of a switched-off slot.** `Engine.cycle` and the panel preview fetch candles for every slot's coins,
   enabled or not (tested). EX14 adds 163 4h requests of weight 10 each, about 1,630 weight per 4h close, on the IP that all
   four accounts share. Binance allows 2,400 per minute.
   - The overlay's pacer wraps only account 4's public data client, and is installed only when EX14 is added. Once
     Binance reports `X-MBX-USED-WEIGHT-1M ≥ 1200`, the next request waits for the next minute.
   - The order and stop clients are untouched.
   - Cost: the 4h cycle of account 4 runs about 1–2 minutes longer, under the engine lock.
   - **The data behaviour still needs a live measurement before any slot is added** (see the open items).

## Sizing receipt

The receipt runs the real engine `open_lot` with CAPITAL_CAP 4000, MAX_LEVERAGE 10, slot share 1/12, risk 0.024, and
PAPER_BOOKS and TRUE_BOOKS off:

- risk is **$8.00**;
- with ATR 2 at price 100: quantity 1.333, notional $133.30;
- with ATR 0.01: the cap binds at notional **$3,333.30** (10 × $333.33, floored to the step);
- no book is attached.

The overlay refuses to install in book mode, because book mode would size EX14 from B5_DISCOVERY (shared with H5) instead of the slot basis.
To prove the basis on account 4's actual settings and engine image, run this read-only command on the PC:

```
python tools/r6_sizing_receipt.py --source F:\ZackBot2_data\EXPLORATION-account4\source --settings F:\ZackBot2_data\EXPLORATION-account4\runtime\ZackBot\settings.json
```

The tool copies the settings to a temp folder and drops any key, secret or token fields. It uses a fake exchange and sends nothing.

## Missing-key fail-safe

The engine calls `strategies.signals(slot.key)` for every slot, enabled or not, and does not catch the error. If a saved
EX14 slot (or lot) refers to `r6_regime_flip_4h` while the key is not registered, **every 4h slot stops getting signals and
exits**, and the panel cannot remove the slot either. The adversarial review found this. Three protections:

- If `install()` fails while a slot or lot refers to the key, it registers an **exits-only** entry first. That entry never
  enters and keeps the frozen exits. The test covers save, unregister, a reloaded engine, a failed install and the exits-only entry.
- `uninstall()` refuses while anything refers to the key.
- Rollback order: switch off, wait until flat, remove the slot, then uninstall.

## Live vs research differences (disclosed, not tuned)

1. **Bar construction.** Live 4h bars are the API's 4h klines. The research built 4h bars from 1h candles (4 of 4).
   - D01 guarantees complete 1h data for the last ~31 days, and the gate requires a gap-free 4h frame over 128 days.
   - A 1h hole inside an old 4h bar (more than 31 days back) could still make R1 differ in a rare case.
2. **No catch-up entries.** If the engine misses the decision bar's 20-minute window (PC asleep, slow fetch), the entry is skipped. The research would have entered.
3. **Fills.** The entry fills at market just after the close (research: next open + 2 bp). Funding is the real rate.
4. **Time exit.** It uses the lot's wall-clock age; see the 120.5 note above.
5. **Overlap with EX13.** EX14 can hold the same coin as EX13 (exp_trend, 31 coins that are also in the 163). The engine
   blocks the same coin only within a slot, and the duplicate coin/side policy (#339) is OFF. Research ignored other
   slots. **Codex decision needed:** accept this, or require EX14 to skip coins another slot holds.
6. **Possible risk scaling.** CTRL direction bias, governor or Kelly multipliers can only scale risk down. That keeps it at $8 or less.

## Canary steps (staged, NOT run; Codex exact-head and protection acceptance first)

1. **Back up the source.** Back up `source\` (account 4 keeps its custom engine image; only the two new modules are copied in).
2. **Update the launcher.** Add to the account-4 launcher after `connect()` and before the loop starts:
   `import app, r6_paper_overlay as OV; OV.install(e, app)`.
   - This registers the strategy only: no slot and no pacer.
   - Then measure data behaviour for one 4h close: weight headers and cycle duration.
3. **Add the slot at canary time.** Run `OV.add_sleeve(e, enabled=False)`, check the receipt, then run `OV.set_enabled(e, True)`.
   - The enabled shares are now 8/12, so they become 9/12.
4. **Rollback.** Run `OV.set_enabled(e, False)`: new entries stop, and an open lot keeps its stop and exits.
   - When the slot is flat, run `OV.remove_sleeve(e)` to stop the extra downloads.
   - Then run `OV.uninstall(e, app)`, remove the launcher lines and restore the source backup.
   - **Never** remove the launcher lines while the slot or a lot still exists. A restart would then raise KeyError for every slot.
     The exits-only fail-safe covers only a failed `install()`, not a missing call.

## Tests (sandbox Linux, non-official)

- `tests/test_r6_regime_flip.py`: 61 passed. They also pass under a CP1252 default-encoding shim (76 with `test_h5_hourly_long.py`).
- Parity and causality:
  - The rule equals the verbatim research copy and Codex's formulation on all 40 repo 4h coins.
  - Prefix causality holds at 6 cut points.
- Mutation check, after the review fixes: **35 of 35 key-line mutants killed.**
  - Two earlier mutants are equivalent and were dropped:
    - `RUN_MIN_HOURS 719`: 30 complete prior days plus a gap-free current day already imply a run of at least 721.
    - R1 warm-up `<118`: the slope is already NaN there.
- Adversarial review: a separate agent with runnable repros. All findings were fixed, and each has a regression test:
  - missing-key KeyError for all slots;
  - time exit one bar early;
  - late entries up to 4h after the close;
  - settings checked only at install;
  - pacer installed before any slot;
  - fetch budget too small for burst days;
  - panel exception not tied to EX14;
  - six weak tests.
- Not run: Windows, the full official gates, the receipt on the PC's real settings, any live data measurement.
