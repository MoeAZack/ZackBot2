# STRAT-01: frozen research strategies as a NEWCORE EntrySource

Status: PAPER / TESTNET only. Stacked on RT-02 (PR #296). Mainnet is out of scope.

## What it does

`newcore_strategies.StrategyEntrySource` implements `newcore.runtime.ports.EntrySource`. On every runtime `cycle`:

1. For each `StrategySlot`, it waits for the candle that closed most recently at the wall clock. It decides only within
   `max_signal_age_ms` (default 15 minutes, at most half a candle) after that close, so a restart never enters late.
2. It asks the `CandleFeed` for `warmup_bars` (default 1500) closed candles of each universe symbol. A window is used
   only if it is gap-free, has finite positive OHLC with consistent high/low, and ends exactly at the expected close.
   A bad BTC window, or any bad window for a cross-sectional strategy (`rotation_long_h2`), cancels the slot for that
   candle. A bad window for another symbol skips only that symbol.
3. It runs the unchanged research path: `strategies.indicators` -> `strategies.build_context` -> `strategies.signals`,
   and reads the signal on the last closed candle only. A parity test checks that this matches the full-history
   research signal on real data for exp_range (L/S), exp_vwaprev (L/S) and rotation_long_h2.
4. Each signal becomes an `EntryCandidate`:
   - key: `decision_key(name, version, tf, symbol, side, candle_close_ms, ENTRY)`;
   - request: entry = decision close; stop = `stop_atr x ATR` of the decision candle (the strategy's own ATR series
     when it supplies one, like `backtest._sig_atr`), rounded to the tick away from entry; risk = `equity x
     base_risk_fraction x risk_mult`; leverage as configured. The request id is a hash of the account id and the key.
   - snapshots (account, portfolio, policy, filters, followers) come from the injected `RiskContextProvider`. This module
     never invents equity, exposures or filters. A missing, failing or mismatched context gives no candidate.
5. At most `max_entries_per_candle` (default 1) candidates per slot, per account, per candle are chosen, always the
   first ones in universe order. A chosen key is re-offered every `reoffer_ms` (default 60 s) while its candle is
   inside the age window, so a transient refusal (sink busy, stale NC-06 snapshot) can still succeed. The journaling
   sink's `claim_signal` is the durable once-only gate: a key already taken is refused there as `signal_consumed`.
6. An incomplete evaluation (a late or broken window for any symbol) is not cached. It is retried every `retry_ms`
   (default 30 s) until it is complete or the window closes. A complete evaluation runs once per candle.

`candidates()` never raises. Every refusal or contained error is counted in `source.reasons` (for example
`range4h_long:candles_unusable`, `range4h_long:risk_context_missing`, `vw:signal_error`).

Candle times must be datetimes (naive = UTC; aware ones are converted to UTC). Numeric times are refused because
pandas would read epoch milliseconds as nanoseconds. OHLCV values may be strings, Decimals or floats and are converted
to float64 before the research path runs.

Supported keys (explicit allowlist, `SUPPORTED`): rotation_long_h2, exp_range, exp_vwaprev, evening_star_short_1h,
starc_band_breakout_long_1h, kalman_innovation_recovery_long_1h. Rank/breadth keys and keys that read mark-price or
order-flow context are refused (`strategy_unsupported`), because `build_context` does not supply their inputs or they
need a complete universe that this module does not guarantee.

## Hard bounds

| Bound | Value |
|---|---|
| risk_mult | 0 < m <= 1 (the 3 Nov DD-policy cap is never raised) |
| base_risk_fraction | 0 < f <= 1% of equity |
| leverage | 1x to 3x (owner testnet bound) |
| warmup_bars | 400 to 5000 |
| max_entries_per_candle | 1 to 5 |

## Factory for the runtime

```python
from newcore_strategies import build_strategy_entries, CsvCandleFeed
source, refused = build_strategy_entries(feed=..., risk=..., wall_clock=..., base_risk_fraction=Decimal('0.005'),
                                         leverage=Decimal('3'))
```

`refused` lists preset slots that this checkout cannot build. On #296's base, C101 reports `strategy_unavailable`: its
signal function lives on the research branch `codex/kalman-innovation-c101` and is never approximated here. A row whose
key failed the 5 Nov sealed holdout is refused as `holdout_failed` (see below).

## Preset `TESTNET_SET_2026_11_05`

| Slot | Strategy | TF | Side | risk_mult | Basis |
|---|---|---|---|---:|---|
| c101_kalman_long | kalman_innovation_recovery_long_1h | 1h | LONG | 1.0 | standard DD 9.69R; weak holdout PASS |
| range4h_long/short | exp_range | 4h | L/S | 0.5 | forward PAPER evidence only |
| vwaprev4h_long/short | exp_vwaprev | 4h | L/S | 0.5 | forward PAPER evidence only |

Removed after the one-shot sealed holdout (5 Nov 2026, window 2026-09-28 to 2026-11-05, prereg sha256
`d3f93330...`, issue #219): C24 `rotation_long_h2` (-15.70R, PF 0.05), C213 `evening_star_short_1h` (-6.05R, PF 0.90)
and C164 `starc_band_breakout_long_1h` (-20.98R, PF 0.38). Under "losing after costs = no PAPER" they get no testnet
slot; `HOLDOUT_FAILED_2026_11_05` makes `build_slots` refuse them as `holdout_failed` even if a row is passed in.
C101 passed on 16 trades, but its development edge depends on signal exits filling at the trigger candle's close, so
it is not a mainnet candidate until that fill is modeled at the next open.

Universe: CORE-20 from `research_evidence/universe/static-top40-minhist365-contract-20261011.json`.

## Not covered (known gaps)

- **Exits other than the protective stop are not wired.** Signal exits (`lx`/`sx`), take-profit and max-bars
  from each strategy's `mgmt` need an NC-07 management source. Until then live behavior differs from the backtest.
- The risk context provider (equity, exposures, reservations, filters and the #295 loss fields) belongs to the runtime
  owner. The correlation cap (<=2R same direction) and the daily loss breaker are NC-06 policy, not this module.
- `exp_vwaprev` needs a quote-volume column (`qv`); without it the slot fails closed.
- Evaluation runs synchronously in `cycle`, once per candle close: about 0.3 s per slot, and about 1.2 s for
  `exp_vwaprev` over 17 symbols x 1500 bars (measured in the sandbox).
- `CsvCandleFeed` is for PAPER/replay (ISO or epoch-millisecond `t`). A testnet kline feed is a separate ticket. It
  must decide that a candle has closed from exchange time or the kline's closed flag, not the local clock alone.
- Stop anchor: live uses the decision close +/- `stop_atr x ATR`, while the backtest anchors on the next-open fill. This
  makes a small difference in R sizing.
- The 1h research keys (C101, and the failed C164/C213) are allow-listed but absent on this base, so their last-bar parity is untested
  here. Add them to the parity test when their branches merge.
