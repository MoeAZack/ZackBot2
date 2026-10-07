# T05a: causal trade audit + opportunity funnel. Review request

| Item | Value |
|---|---|
| Base | **T05b as merged** (PR #11, squash of upstream head `20c00c8`; local equivalent = T05b fix head `874cfd8` = master `3eabdbc` [merged T03c] + T05b + read_outage injector + start-up outage reconnect `de9cfe5` + backoff exponent cap `874cfd8`). T05a is one commit on top (local `fb9bb88`). TESTNET only. |
| History | Rebuilt from `t05a-next` (`5cf8cf8`) by applying the T05a-only diff `c124a68..5cf8cf8`, first onto T05b v2 head `450f025` (local `345b19c`, see Evidence), then onto the T05b fix head `874cfd8`. |
| Cross-ticket adjustment | One T05b test file changes: `tests/test_t05b_startup_outage.py` compares lots without T05a's observe-only `ex` excursion record (new helper `_prot`), because the audit updates `lot['ex']` on every mark. The three lot-equality asserts (in `test_engine_started_during_a_read_outage_recovers_in_the_same_process` and `test_open_orders_refused_non_transient_is_explained_and_recovery_waits`) are otherwise unchanged: quantities, stop ids and every other lot field must still be identical, and no order/stop/cancel may be sent. |
| Class | **Observe only.** No change to targets, runners, DCA, orders, sizing, stops, reconciliation or strategy selection. |
| Contract | Codex pre-design contract, PR #8. Every item is mapped below. |
| Out of scope | UI/timeline (T12), behaviour experiments (T09a), persistence migration (T11). |

## Contract → implementation → tests

All tests are in `tests/test_trade_audit.py` unless a different file is named.

| # | Contract item | Implementation | Tests (mutation-killed where marked *) |
|---|---|---|---|
| 1 | Strictly observe only | The engine only stores `lot['ex']` (tracking) and `lot['ap']` (policies declared at entry). Events go to the writer queue. Every hook is wrapped in `try/except`. The decision code reads none of these fields. | `test_audit_never_changes_trading_even_when_every_audit_function_raises` (every TA function raises and the writer's `emit` raises: lots, stops, calls, positions, history and missed are identical); replay comparison against the base `450f025` (below) |
| 2 | Append-only, versioned `trade_audit.jsonl`, separate from fills | Same `FillWriter` contract as T05 (one process-wide writer, `put_nowait`, bounded queue, drops counted). The file is `trade_audit.jsonl`, separate from `fills.jsonl`. Every line has `v=3` and `kind ∈ EVENT_KINDS`. Rotation is at `AUDIT_ROTATE_LINES=20000` lines **or** `AUDIT_ROTATE_BYTES=16 MB`, whichever comes first (third pass). | `test_audit_files_contain_no_credentials…` (schema of every line), `test_rotation_keeps_checkpoints_and_the_trade_window`, `test_audit_writer_rotates_at_the_byte_cap…`*, `test_engine_audit_writer_has_a_byte_cap`* |
| 3 | Event types | `funnel` (candidate decisions; exactly one terminal `taken` / `not_taken` per candidate, interim `armed_trailing` / `order_placed`), `hold_eval` (one per open lot per closed candle), `excursion` (bounded checkpoints), `cohort` (add or partial close), `runner` (activation), `trade_audit` (final record), `coverage` (open lot upgraded to T05a). | `test_taken_and_armed_candidates_emit_funnel_events`, the third-pass funnel tests (below), `test_cycle_emits_one_hold_eval_per_open_lot_per_closed_candle`, `test_engine_emits_cohort_and_runner_events…`, `test_legacy_closed_trades…` |
| 4 | PRICE MFE/MAE separate from LIFECYCLE net-P&L peak/trough | `price_excursion` holds mark-sample MFE/MAE. `lifecycle_pnl` holds the net peak and the new trough: realized − fees + open P&L − exit fee, so quantity changes are included. Flat legacy fields are kept. | `test_price_excursion_and_lifecycle_pnl_are_separate`* (M14) |
| 5 | Causal alternatives vs labelled hindsight ceiling | `kind='causal_policy'` (predeclared) vs `kind='hindsight_ceiling'` (`info_cutoff='full future path (NOT available live)'`) vs `kind='what_if'` (caller-supplied parameters, never causal). | `test_close_record_giveback_and_labelled_counterfactuals`, `test_rules_are_never_reoptimised_after_the_close`* (M11) |
| 6 | RAW long/short before side masking | `compute_signals` keeps `_sig_raw[sleeve\|sym] = {le, se, sides, le_m, se_m}`. The masked `sigs` (the decision input) is unchanged. `funnel` decision `side_masked` records a raw signal that the slot's sides hide. `both_raw` keeps the unused side when both sides fire. | `test_raw_signals_are_kept_before_side_masking…`* (M5), `test_both_raw_sides_keep_the_unused_short…` |
| 7 | Policies predeclared before entry, with parameters and version; `info_cutoff < decision_at`; no optimisation after the close | `TA.declare()` is called in `_create_lot` before the first `save_state` (`lot['ap']`, `POLICY_VERSION`, `seq=-1`). It stores target, trailing (k, ATR, src, best_from), time cap (bars, cap_t) and runner. Online policies read only the declaration. Each decision carries `decision_at{t,seq}` and `info_cutoff{t,seq,what,basis}`. `_cutoff()` withholds the value unless `max(seq of every input) < decision seq` (live path). Inputs: the declaration (−1), the running best (an earlier observation), the state snapshot (management before this observation = n − 0.5). | `test_engine_declares_policies_at_entry_before_any_observation`, `test_every_live_causal_decision_has_info_cutoff_strictly_before_the_decision`, `test_cutoff_mutations_withhold_the_decision`* (M3, M18), existing future-data and post-decision-state property tests, `test_online_policies_are_causal…`* (M12) |
| 8 | Candle highs/lows are upper bounds unless lower-TF ordering proves them | `hold_eval`: checks at the close price are executable. Checks at the high/low are labelled `upper_bound` unless a mark sample inside that candle proves the touch (`proven by a mark sample in this candle`). `info_cutoff` = candle close, and the evaluation is withheld unless close < `decision_at`. | `test_hold_eval_labels_candle_extremes_and_enforces_the_cutoff`* (M8) |
| 9 | Runner attribution on the same remaining quantity; cohort ledger across adds/partials | `cohort_ledger()` is rebuilt from the lot's own fills (persisted, so it is restart-proof). Cohorts are FIFO. The first partial take-profit (`RUNNER_WHY`) that leaves quantity open activates the runner, and its quantity is exactly the open quantity at that point. `value_vs_activation = Σ sd·(exit − activation)·q − fee·q·(exit − activation)`, so the entry basis cancels. Adds made after activation are not part of the runner. `attribution().runner_value` now sums this value; it was "− target-policy delta" before. | `test_cohort_ledger_pyramid_partial_runner…`* (M6, M7), `test_cohort_ledger_dca_basket_runner_and_ladder…`, `test_engine_emits_cohort_and_runner_events…` |
| 10 | Bounded non-blocking writer, finite-or-null JSON, rotation and restart coverage, NO disk/network I/O in the mark loop | The `changed = True` on a new MFE/MAE is **removed**. `observe()` is memory only. `checkpoint()` returns at most one `excursion` event per lot per `CKPT_MIN_S=900 s`, plus bounded forced events (first observation, new snapshot, online decision, closed gap/outage; ≤ 1+48+1+2+8+8). The event goes to the writer queue. `lot['ex']` rides the existing save cadence. On restart, `_audit_start()` (engine start, not the mark loop) does 3 things: (1) flushes the shared writer for 1 s at most; (2) loads the newest checkpoint per open lot and continues from it when it is ahead of state.json; (3) opens a tracking gap from the last known observation. `TA.clean()` gives every event non-finite → null. | `test_trending_mark_loop_saves_state_exactly_as_often_as_without_the_audit`* (M1, M16), `test_mark_loop_opens_no_file_on_the_calling_thread`, `test_restart_continues_from_the_newest_checkpoint…`* (M10), `test_rate_limited_checkpoints_lose_only_what_the_gap_says`, `test_restart_from_a_checkpoint_is_equivalent_to_no_restart` (property, 80 random crashes), `test_rotation_keeps_checkpoints_and_the_trade_window`* (M13), `test_event_volume_per_lot_is_bounded`* (M9) |
| 11 | Audit failure may alert but never delays or alters fills, stops, closes, reconciliation or history | `_audit_emit` swallows errors, and the writer counts drops and write errors. History is persisted before and independently of the audit (`_finish`). | `test_audit_never_changes_trading_even_when_every_audit_function_raises` (now includes the new functions and a refusing writer) |
| 12 | Legacy closed trades `audit_available` false; upgraded open trades partial | `audit_summary().coverage = {closed, audit_available, audit_unavailable, partial, legacy, audit_rotated, audit_missing}`. A closed trade with no `trade_audit` record is unavailable; it is `legacy` if it closed before the audit first ran (`trade_audit.jsonl.since`, written once at engine start), `audit_rotated` if it closed later but before the oldest record in the window, `audit_missing` otherwise. An open lot without `ap` at start is declared late (`coverage='partial'`) and gets a `coverage` event. The final record then carries `coverage='partial'` and `coverage_why`. | `test_legacy_closed_trades_are_audit_unavailable_and_upgraded_open_lots_partial`* (M15), `test_coverage_tells_legacy_rotated_and_missing_apart`*, `test_engine_records_the_audit_start_once`* |
| 13 | Use T05b state for outage/missing samples | **Live path (what actually happens):** during an outage `manage()` / `cycle()` stop at the failing reconcile, so no lot is observed and no candle is evaluated. The next sample after recovery records the pause (> `SAMPLE_GAP_S=60 s`) as a missing-sample interval with cause `exchange_outage` when the T05b `exchange-down` incident's last failure (`_audit_ctx()` `down_last`) falls inside it, else `no_samples`; the first decision after it is flagged `after_gap`. **Defensive only:** `observe(missing=True)` / `hold_eval(missing=True)` (incident open or circuit in `outage` at the lot loop) are kept as guards, but a successful reconcile resets the circuit before the lot loop, so the live engine practically never takes them (third pass: the claim that it does was dropped). | `test_outage_samples_are_missing_not_used_and_gaps_are_attributed`* (M2, M17), `test_engine_uses_t05b_incident_state_for_missing_samples` (the live gap path), `test_hold_eval_labels…` |
| 14 | No credentials, Telegram ids, raw exchange responses or account fingerprints in audit files | `TA.clean()` drops keys matching `_DENY` (api key/secret, signature, token, chat/telegram, order/client ids, stop_id/tag, account/balance, raw/response, avgPrice/executedQty). It also strips URL query strings, replaces `{…}`/`[…]` blobs and redacts tokens of 32+ characters. Third pass: in every string except the lot id under `id`, IPv4/IPv6 addresses and `request ip: …` become `[ip]` and 8+ digit integers (order / client ids) become `[id]` (times and decimals are kept). Reason details are scrubbed **before** the 160-character cut. | `test_clean_makes_events_finite_or_null_and_drops_secret_keys`* (M4), `test_audit_files_contain_no_credentials_telegram_ids_or_raw_exchange_dumps` (real engine scenario; scans for key, secret, Telegram token/chat, signature, orderId, clientOrderId, stop_id, avgPrice, executedQty, stop tags, balances, NaN/Infinity), `test_audit_events_contain_no_ip_addresses_or_long_digit_ids`* (every string of every event: -2015 in `cycle()`, order ids, IPv6) |

**What audit files may contain:** symbol, side, sleeve id, lot id (`<sleeve>|<symbol>|<side>|<epoch>`), times, prices, quantities, P&L, reason codes and scrubbed reason details, and policy parameters.

**What they must not contain:** anything else from the exchange or the config.

## Evidence

- **save_state during 240 trending ticks** (trailing lot, a new MFE on every tick):
  - T05b-only: **49**
  - this T05a: **49**
  - previous T05a draft: **240**

  Measured with the same script against all three trees (`savecount.py`); the in-repo test compares against an audit-free engine.
- **Replay no-behaviour-change vs `t05b-next`** (`replay_cmp.py`: 6 modes × 2 seeds, 80 closed trades):
  - engine trades, curves, history, metrics, backtest trades and match are **identical**;
  - `missed` differs only by the added funnel fields `stage/code/detail`;
  - backtest code is untouched.
- **Mutations** (`t05a_mut.py`): **18/18 killed**. The table marks each one where it is used. M1 restores the per-tick save; M16 adds `save_state` to the fill hook.
  - `test_audit_window_is_slimmed_and_the_summary_is_fast` (pre-existing, `dt < 0.1 s`) also failed in some runs. That was CPU load from 4 parallel runs, so it is not counted as a killer.
- **Tests (third pass):**
  - `test_trade_audit.py`: 114 passed. `test_safety.py`: 78 passed. `test_grid.py`: 14 passed.
  - Full suite (per file): 702 passed, 2 skipped, 1 failed. The failure is `test_verify` provenance, which is env-only and also fails on the baseline.
- **Replay vs `t05b-next`, re-run after the third pass** (6 modes × 2 seeds, 80 closed trades): engine trades, curves, history, metrics, backtest trades and match are **identical**. `missed` is identical once the funnel fields `stage/code/detail` are dropped.
- **Rebased onto `450f025` (T05b v2 PR head):**
  - Only conflict: the `engine.py` import block. Resolved as T05b v2's `binance_client` import (with `testnet_faults, testnet_read_outage`) plus T05a's `import trade_audit as TA`. Everything else applied cleanly. `git diff 450f025 --stat` lists only the 7 T05a files.
  - Tests: `test_trade_audit.py` 114, `test_t05a_t05b_interaction.py` 2, `test_safety.py` 78, `test_grid.py` 14, `test_leverage_auto.py` 185, `test_outage.py` 42, all passed. Full suite (17 files, per file): 761 passed, 2 skipped, 1 failed. The failure is the env-only `test_verify` provenance test.
  - Replay vs `450f025` (`v3_rc.py`: 6 modes × 2 seeds, 80 closed trades): engine trades, curves, history, metrics, backtest trades, matched and mismatch are **identical**. `missed` differs in 8 of 12 runs only by the funnel fields `stage/code/detail`, and is identical once they are dropped.

- **On the T05b fix head `874cfd8` (this PR's base, = T05b as merged in PR #11):**
  - `git diff 874cfd8 --stat`: 8 files = the 7 T05a files + `tests/test_t05b_startup_outage.py` (the `_prot` adjustment
    in the header table). No other T05b file changes.
  - Tests (sandbox stand-in runner, not the official pytest/CI; per file): `test_trade_audit.py` 114,
    `test_t05a_t05b_interaction.py` 2, `test_t05b_startup_outage.py` 13, `test_outage.py` 42, `test_outage_final.py` 8,
    `test_t03c_t05b_interaction.py` 3, `test_safety.py` 78, `test_grid.py` 14, `test_leverage_auto.py` 185,
    `test_fills.py` 46, `test_v31_engine.py` 45, `test_telegram.py` 23, `test_testnet_faults.py` 47, `test_lab.py` 27,
    `test_ci.py` 36, all passed. `test_verify.py`: 16 passed, 1 failed (the env-only
    `test_summary_has_the_required_provenance_fields`). `test_installer.py` and `test_causality.py` hit the sandbox's
    110 s per-file cap (no result; CI is the gate for them).
  - Not re-run on `874cfd8`: the replay comparison, `savecount.py` and the mutation runs (all above were on `450f025`).

## Changes to tests from the previous draft (intentional)

- `info_cutoff` is now a dict `{t, seq, what, basis}`, and `decision_at` is new. Tests assert `causal_ok` and a strict cutoff instead of `decision_t == info_cutoff`.
- Policy parameters come from the declaration:
  - the ATR-fallback test declares the lot without `atr0`;
  - `cap_bars` / `trail_k` overrides are `kind='what_if'`.
- `runner_value` is now the same-quantity runner attribution.
- Restart tests were rewritten. The draft relied on `save_state` on every new extreme, which the contract forbids; the tests now use checkpoints plus a gap.
- The audit writer rotates every `AUDIT_ROTATE_LINES`. Its in-memory window keeps only slimmed `trade_audit` records, because events are not summarised.

## Known limits (documented, not hidden)

- Marks are sampled about every 8 s. Intrabar extremes between samples are not seen, and each record says so.
- Observations after the last checkpoint and before a crash are lost. They fall inside the reported gap. Checkpoints are rate-limited by design, which is the bounded-volume requirement.
- The attribution window is the `trade_audit` records within `.1` + the current file (each up to 20k lines / 16 MB). `/api/status` shows `in_window`; trades whose record rotated out are counted as `audit_rotated`, not `legacy`.
- If an open lot's newest checkpoint rotated out of both files before a restart, tracking continues from state.json and the record says so (`ck_lost`); the restart gap already covers what was lost.
- A stop fill is booked at the stop level, as in T05.

## Third internal adversarial pass

A verifier pass (7 repros, all failing on `eb90f7e`) found five defects and three cheap follow-ups. The repros are now in
`tests/test_trade_audit.py` under descriptive names, plus guard tests. Every fix is mutation-checked (`mut3.py`).
Result: **20/20 mutants killed**.

| # | Finding | Fix | Tests (mutants) |
|---|---|---|---|
| 1 | Grid adds (`grid_buy` / `grid_sell`, booked through `_add_qty`) were treated as closes. The cohort ledger consumed the entry cohort, the `cohort` event said `partial_close` and `_tracking_late` flagged the lot late. | `ENTRY_KINDS` gains `grid_buy` and `grid_sell`. A fill record has no direction field, and close `why`s are open-ended (`grid_<range reason>`, exit reasons), so the finite add set stays the whitelist. Two guards keep it honest. (a) An AST scan of `engine.py` + `grid.py` takes every literal `why` passed to `_add_qty` / `_apply_add` (if/else branches included) plus `_create_lot`'s `'entry'`. All of them must be in `ENTRY_KINDS`, and no literal close `why` may be. (b) At runtime, `cohort_ledger()` reports `qty_mismatch` (fills vs lot quantity) and the record lists it as a limitation, so an unknown add kind shows up. | `test_grid_add_is_a_cohort_not_a_close`, `…_event_is_an_add`, `…_does_not_mark_tracking_late`, `test_grid_sell_on_a_short_grid_lot_is_an_add_too`, `test_grid_take_profit_after_adds_is_still_a_close`, `test_every_add_fill_kind_the_engine_and_grid_book_is_an_entry_kind`, `test_engine_grid_add_books_an_add_cohort_event`, `test_an_unknown_add_kind_shows_up_as_a_quantity_mismatch` (M1, M8) |
| 2 | Maker entries were counted as `taken` AND as `not_taken`. With `ENTRY_ORDER=maker`, `open_lot()` returns True when the post-only order is placed, not when a lot exists. | `_audit_opened()`: after `open_lot()` returns True, the decision is `order_placed` if a resting entry exists, else `taken`. `_maker_finalize` emits the one terminal outcome: `taken` when a lot was created from maker fills or from the market fallback, else `not_taken`. That is either via `miss()` or directly when lot creation failed / the fallback raised. The `missed` list is unchanged. | `test_maker_unfilled_candidate_is_not_both_taken_and_not_taken`, `test_maker_filled_candidate_is_taken_once…`, `test_maker_partial_fill_with_market_fallback_is_taken_once`, `test_maker_unfilled_market_fallback_is_taken_once` (M2, M3c, M3d) |
| 3 | A trailing entry that filled was never recorded as `taken`. | `_trail_entries()` emits `taken` (or `order_placed` for a maker order) when the rebound opens. Cancel and expiry already ended in `miss()` → `not_taken`. A slot deleted while armed now ends in `not_taken` (`trailing/slot_removed`) and is no longer silently dropped. | `test_trailing_entry_that_fills_is_recorded_as_taken`, `test_trailing_entry_expired_or_slot_removed_has_one_not_taken` (M3, M3b) |
| 4 | The host's public IP leaked. Binance -2015 text (`request ip: x.x.x.x`) and order ids reached funnel `detail`. | `_safe_text` (all strings except the lot id under `id`) replaces `request ip: …`, IPv4 and IPv6 with `[ip]` and 8+ digit integers with `[id]`. IPv6 needs `::` or 8 groups, so ISO times never match. Decimals and lot-id epochs are kept. | `test_funnel_detail_does_not_leak_the_request_ip`, `test_redaction_keeps_times_prices_and_lot_ids`, `test_audit_events_contain_no_ip_addresses_or_long_digit_ids` (scans every string of every emitted event on real engine paths) (M4a–d) |
| 5 | Rotation by lines only. A DCA/grid checkpoint line is about 7 KB, so a 20k-line file could reach about 145 MB. | `FillWriter(rotate_bytes=…)`: the audit writer rotates at 20k lines **or** 16 MB, whichever comes first. The byte count is reloaded at start. The summary window (final records only, from `.1` + current) is unchanged. If an open lot's newest checkpoint rotated out of both files, a restart continues from state.json and sets `ex.ck_lost`, and the record lists it. No crash, and the restart gap covers the loss. | `test_excursion_checkpoint_line_size_is_bounded_by_the_byte_cap` (repro, now asserting the capped bound), `test_audit_writer_rotates_at_the_byte_cap_and_keeps_window_and_checkpoints`, `test_engine_audit_writer_has_a_byte_cap`, `test_restart_after_the_checkpoint_rotated_away_is_flagged_not_broken` (M5a–d) |
| a | The `missing=True` sample path is effectively dead live: a successful reconcile resets the circuit before the lot loop, and an outage makes `manage()` return before it. | Honest minimal option: the claim was **dropped** (contract row 13 now describes the live path, the post-outage gap attributed to `exchange_outage` via `down_last`). `missing=` stays as a defensive guard. No new wiring was added, because a start-of-`manage()` snapshot would mark the first good post-recovery sample as missing and add nothing. Latent bug fixed: `_audit_restored.discard(key)` now runs only once `observe()` really created `lot['ex']`, so a restored lot whose first pass is a missing sample keeps its late flag. | `test_restored_flag_is_kept_until_observe_really_starts_tracking` (M6) |
| b | The cycle-path hooks were not covered by the "audit raises" equivalence test. | A new scenario runs `cycle()` (market and maker: entries, maker fill + fallback, manage, exit-signal close with `hold_eval`) with **every** `trade_audit` function raising and the writer refusing. Lots, stops, exchange calls, positions, history, missed, pending and resting entries are identical. | `test_cycle_never_changes_trading_when_every_audit_function_raises` (M9: an unguarded hook) |
| c | Records that rotated away were labelled the same as legacy trades. | `coverage()` splits `audit_unavailable` into `legacy` / `audit_rotated` / `audit_missing`. It uses `trade_audit.jsonl.since` (written once at the first engine start with T05a) and the oldest record time in the window (`t` is now kept in the slim window record). | `test_coverage_tells_legacy_rotated_and_missing_apart`, `test_engine_records_the_audit_start_once` (M7a–c) |

**Found while fixing, outside T05a scope (not changed):** `engine._finish()` computes the history `exit` average from fills
whose kind is not `entry` / `pyramid_add` / `safety_order`. That means `entry_fallback`, `grid_buy` and `grid_sell` adds
are averaged into the exit price of `history.json`. This is pre-existing trading/history code, so the observe-only T05a
leaves it alone. It should be a separate fix.

## Owner scope 2026-10-07 (PR #8)

The owner's measurement request ("per trade record MFE/MAE in price, USD, %, and R; time-to-MFE; realized result; give-back;
executable MFE after fees/slippage; exit/hold reason at each decision point; whether BE, partial TP, trailing, time or regime
exit would have improved the result without look-ahead; segments; failure classes; counts, expectancy and sample audits in
the UI"). **Still observe only:** nothing below is read by a trading decision, order, stop, size, DCA, target, runner,
reconciliation or strategy selection. Every new engine hook is wrapped in `try/except` like the existing T05a hooks. No new
disk/network I/O and no `save_state` in the mark loop. Policy set `POLICY_VERSION` 1 → 2 (lots declared under v1 keep v1).

### Requirement → implementation → tests

All tests are in `tests/test_trade_audit.py`. Mutation ids (O1–O16) refer to `t05a_add/mut.py` (results below).

| Requirement | Implementation | Tests (mutants killed) |
|---|---|---|
| MFE/MAE in price, USD, %, R | `trade_metrics()` → record `metrics.mfe` / `metrics.mae`: `price` = sd·(mark − average entry **valid at that observation**, from the state snapshot `st[mfe_si]`), `usd` = price × the quantity open at that observation (gross of fees), `pct` = price / that average × 100, `r` = usd / `risk_usd`. Unknown snapshot → price/% vs `entry0`, USD/R withheld (`basis='entry0_state_unknown'`). Price excursion = mark samples only. | `test_metrics_usd_pct_r_and_time_to_mfe_long_and_short` (O11) |
| Time to MFE | `metrics.time_to_mfe_s` / `time_to_mae_s` / `time_to_peak_net_s` from `lot.opened` (`time_basis`; tracking start if the entry time is unknown). | same test |
| Realized result | `metrics.net_usd`, `metrics.net_r` (= record `net_pnl`, `r`). | same test |
| Give-back from MFE to exit | `metrics.giveback_usd` = lifecycle net peak − final net; `giveback_pct_of_mfe` = that / peak × 100 (peak > 0). The legacy flat `giveback*` fields stay. | same test |
| Maximum favourable profit actually executable after fees/slippage | `metrics.executable_mfe_usd` = the lifecycle net peak (whole-position close at a **recorded mark sample**: realized − fees paid + open P&L − estimated exit fee, quantity changes included) minus modelled adverse slippage at that mark: `peak − s·px·q·(1 − sd·fee)`, `s = EXEC_SLIP_BPS = 2 bps`. `observe()` now also stores the peak's mark and quantity (`ex.peak_px/peak_q/peak_n`, memory only). Candle extremes are never used (`executable_basis`). Lots tracked before this field → `null`, not guessed. | `test_executable_mfe_is_the_net_peak_at_a_mark_sample_minus_slippage_never_a_candle_extreme` (O10) |
| Exit/hold reason at each decision point | Existing: one `hold_eval` per open lot per closed candle (`action` hold / close_exit_signal / close_time_exit, `exit_signal` before the runner override, `runner_override`, target/stop/time-cap checks) + the record's `exit_reason`. New: `hold_eval.regime` = the closed candle's trend state (same cutoff as the candle). | `test_cycle_hold_records_the_candle_trend_and_triggers_the_regime_exit`, existing `test_cycle_emits_one_hold_eval…` |
| BE / partial TP / trailing / time / regime exit without look-ahead | Existing causal policies: target, trailing, time cap, runner (= partial TP: the runner quantity closed at its activation fill). **New, predeclared at entry (v2):** `breakeven_after_costs` (arms when a mark is +`BE_ARM_R`=1R from the declared entry; from the NEXT observation on closes at the first mark where net P&L after fees, with the state valid at that observation, is ≤ 0; online at every observation; info cutoff = arming observation seq < decision seq) and `regime_exit` (at a closed candle of the lot tf with trend `down` for a LONG / `up` for a SHORT, decided in `cycle()` only if candle close < now; filled at the first mark sample at/after that decision). Both are `causal_policy` with `decision_at`/`info_cutoff`; a v1 lot says "not declared". | `test_breakeven_after_costs_online_long_and_short_is_causal` (O8), `test_breakeven_path_fallback_needs_an_earlier_arming_point`, `test_regime_exit_trigger_is_causal_and_fills_at_the_next_mark` (O9) |
| Segment by strategy, side, symbol, timeframe, regime, DCA count, runner status | Record `segment` = {strategy (sleeve id), side, symbol, tf, regime (entry label), dca (`0/1/2/3+` safety-order fills), runner (`runner` / `no_runner`)}. `segments()` aggregates per dimension: n, wins, win rate, expectancy (mean net USD, mean net R), net sum, flag counts. Slim window keeps compact strings (`flags`, `flags_unknown`, `seg='regime\|dca\|runner'`); slim and full give identical results. | `test_segments_aggregate_counts_expectancy_flags_and_samples` (O12), `test_close_record_carries_metrics_flags_and_segment` |
| Failure classes | Record `flags` (list), `flags_unknown` (classes that could not be evaluated, never guessed), `flag_detail`. Definitions below. | per-flag tests below (O1–O7, O16) |
| Counts, expectancy and sample audits in the UI | `audit_summary()` adds `segments` (cached with the attribution) and `missed_short` (computed per call, ≤ 600 items). Exposed on `/api/status` → `health.audit` (existing) and new read-only `GET /api/audit_summary` (no engine lock; the writer lock is held only to copy the window). UI: collapsed `<details>` "Trade audit" card on the Trades tab (separate commit, droppable). | `test_audit_summary_endpoint_is_read_only_and_exposes_segments`, `test_panel_audit_card_is_additive_collapsed_and_escaped`, `test_panel_audit_card_renders_escaped` (node, `<img onerror>` payload) |

### Failure class definitions (deterministic)

"Peak" = lifecycle net peak (`ex.peak_pnl`: realized − fees paid + open P&L − estimated exit fee, at a mark sample).
"Green value" = `executable_mfe_usd` (peak minus modelled slippage), else the peak when no executable value is recorded
(`flag_detail.green_basis`). Final = the record's `net_pnl`.

| Flag | Definition | Unknown when | Tests |
|---|---|---|---|
| `never_green` | peak ≤ 0 (never above zero after fees) | no peak recorded | `test_failure_flags_green_red_giveback_never_green_both_sides` (O1) |
| `green_to_red` | green value > 0 and final < 0 | no peak | same (O3) |
| `gave_back_gt_50pct_mfe` | green value > 0 and (green value − final) > 0.5 × green value (exactly 50 % is not flagged) | no peak | same (O2) |
| `dca_into_trend` | ≥ 1 `safety_order` fill whose regime snapshot (lot tf, last CLOSED candle known at the fill) is against the position: LONG: close < EMA200 **or** EMA20 < EMA50; SHORT mirrored. Pyramid / grid adds do not count. | an add has no usable snapshot (not recorded, stale > 2 bars, cutoff after the fill, indicators missing) and no add is against | `test_dca_into_trend_long_short_and_unknown_regime_is_not_flagged` (O5, O6, O16), `test_engine_records_the_entry_and_dca_add_regime_causally_and_flags_the_close` |
| `long_in_bear_regime` / `short_in_bull_regime` | entry regime label `bear` for a LONG / `bull` for a SHORT. Label: `bear` = symbol close < EMA200 on the lot tf **and** BTCUSDT close < EMA200 on 4h (lot-tf BTC when 4h is not cached; `btc_basis` says which); `bull` mirrored; `mixed` when they differ (not flagged). | either snapshot not usable | `test_long_in_bear_and_short_in_bull_from_the_entry_regime` (O7) |
| `missed_short_opportunity` (funnel, not a trade flag) | a candidate (slot, coin, candle) where the RAW short signal fired, or a SHORT candidate, and no short was taken: `side_masked` (slot sides hide shorts), `not_taken` (a SHORT stopped by a gate; its stage/code is kept), `long_preferred` (both raw sides fired, the long was taken / tried). Counted once per candle; the funnel event carries `missed_short_opportunity`. | — | `test_missed_short_definition`, `test_engine_counts_missed_shorts_once_per_candle_and_summarises_them` (O13) |

### Regime snapshots: causal by construction

`cycle()` calls `_audit_regime_cache(tf, frames)` right after `compute_signals`: for every frame it already holds (closed
candles only, `candles()` drops the forming one) plus BTCUSDT 4h from the candle cache when present (no fetch), it stores the
last row's close / EMA20 / EMA50 / EMA200 with `cutoff_t` = that candle's close. At entry (`_create_lot`, stored in
`lot['ap']['ctx']['entry']`) and at an add fill (`_audit_fill`, `lot['ap']['ctx']['adds']`, ≤ 16) the engine only reads that
dict; `regime_at()` returns status `ok` only when `cutoff_t ≤ decision_at` and the snapshot is ≤ 2 bars old, otherwise
`unknown` with a reason. `ap` is excluded from the "audit never changes trading" lot comparisons, like before.
Tests: `test_regime_snapshot_and_causal_lookup` (O4, O14), `test_trend_against_is_symmetric_and_never_guesses`,
`test_engine_records_the_entry_and_dca_add_regime_causally_and_flags_the_close` (entry/add `info_cutoff ≤ decision_at`;
`candles()` and `klines` patched to raise, so no fetch happens; O15),
`test_engine_without_a_cached_snapshot_records_unknown_never_a_guess`.

### Causal vs hindsight

- **Causal** (`kind='causal_policy'`, `decision_at` + `info_cutoff`, withheld unless the cutoff is before the decision):
  target, trailing, time cap, runner, breakeven_after_costs, regime_exit; regime snapshots (`causal_ok`, status).
- **Hindsight** (describe the finished trade, never available live): `metrics` (labelled `hindsight`), the
  `hindsight_ceiling` counterfactual, the failure flags that use the peak / final result (`never_green`, `green_to_red`,
  `gave_back_gt_50pct_mfe`). `dca_into_trend` and the regime flags classify with causal snapshots but are assigned at the
  close.
- **Not implemented:** a hindsight N-bar outcome for missed shorts (no cheap causal-labelled source in the audit path;
  the Missed signals view already shows the move since the signal). Counts only.

### Limitations

- Mark samples ~8 s: MFE/MAE/peak/executable values miss intrabar extremes (records say so).
- `executable_mfe_usd` uses a modelled 2 bps slippage, not the fill telemetry's measured slippage.
- Regime snapshots exist only for symbol/tf pairs a cycle computed; manual / grid lots on other pairs get `unknown`.
  Missed-short counts are memory since engine start (bounded 600); `short_not_taken_in_missed_list` is from the persisted
  missed list.
- The regime exit needs a candle-close cycle; if the lot closes in that same cycle (exit signal) it is not evaluated.
- Record size grew by ~2.8 KB (test bound 6000 → 9000 B); files stay byte-capped and the window slimmed (7.3 MB / 5000).

### Evidence (on `t05a-v3` + owner-scope commits)

- **Replay no-behaviour-change vs `65f0114`** (`v3_rc.py`, 6 modes × 2 seeds, `ex`/`ap` stripped): `engine_trades`,
  `engine_curve`, `history`, `missed`, `metrics`, `bt_trades`, `bt_curve`, `matched`, `mismatch` are **identical** in all
  12 runs (backtest code untouched).
- **test_causality** (per case, 2 parallel): 6 × `test_engine_decisions_never_depend_on_unseen_prices`, 6 ×
  `test_backtest_matches_causal_engine_trade_by_trade` and `test_parity_catches_same_candle_information`: all passed.
- **Tests** (sandbox stand-in runner, per file): `test_trade_audit.py` 137 (114 + 23 new), `test_t05a_t05b_interaction.py`
  2, `test_t05b_startup_outage.py` 13, `test_outage.py` 42, `test_outage_final.py` 8, `test_t03c_t05b_interaction.py` 3,
  `test_safety.py` 78, `test_grid.py` 14, `test_leverage_auto.py` 185, `test_fills.py` 46, `test_v31_engine.py` 45,
  `test_telegram.py` 23, `test_testnet_faults.py` 47, `test_lab.py` 27, `test_ci.py` 36, all passed; `test_verify.py` 16
  passed, 1 failed (the env-only `test_summary_has_the_required_provenance_fields`, as on the base). The existing
  no-I/O-in-the-mark-loop tests (`test_trending_mark_loop_saves_state_exactly_as_often…` = 49/50 pinned,
  `test_mark_loop_opens_no_file_on_the_calling_thread`) and both "every audit function raises" equivalence tests pass
  with the new hooks (the cycle one patches every `trade_audit` function, including the new ones).
- **Mutations** (`t05a_add/mut.py`): **18/18 killed**. O1 never_green `<`, O2 give-back `>=`, O3 green_to_red without the
  green check, O4 regime cutoff after the decision accepted, O5 trend-against needs both conditions, O6 unknown add regime
  flagged, O7 bear label with OR, O8 BE arms and exits on the same observation, O9 regime trigger at the candle close
  itself, O10 slippage improves the executable MFE, O11 metrics on the final average, O12 a zero-net trade counted as a win,
  O13 / O13b / O13c missed-short counter (first reason overwritten / key without the candle / another candle's raw signal),
  O14 stale snapshot accepted, O15 a fetch at entry instead of the cached snapshot, O16 pyramid adds counted as DCA.
  O8, O12 and O13 survived the first run; `test_breakeven_never_arms_and_exits_on_the_same_observation`,
  `test_segments_breakeven_trade_is_not_a_win` and `test_engine_missed_short_counter_keys_per_candle_and_keeps_the_first_reason`
  were added and kill them.
- **UI:** there is no screenshot/DOM baseline harness in the repo (panel tests are source/node-render checks), so the card
  is additive inside a closed `<details>` on the Trades tab; it is its own commit and can be dropped (the API stays).
