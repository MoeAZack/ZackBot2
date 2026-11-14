# P5 IVS-1H: implied-volatility shock reversal (Claude, 13 Nov 2026). PROPOSAL ONLY

No counts, outcomes, signals or bulk fetch. The source check used S3 listings plus one sample file
(BTCBVOLUSDT 2025-03-10). The BDI lane was closed DATA_INADEQUATE; this replaces it.

## Information source (new to the project)
- **Binance options BVOL index**, the exchange's 30-day implied-volatility index derived from its options market:
  data.binance.vision `option/daily/BVOLIndex/{BTCBVOLUSDT,ETHBVOLUSDT}/<SYM>-BVOLIndex-YYYY-MM-DD.zip`.
- Schema `calc_time(ms),symbol,base_asset,quote_asset,index_value`, at a ~1 s cadence (86,396 rows in the sample day).
- Coverage of the window plus 7 warmup days (2024-10-04..2026-09-27, 724 days): files run from 2024-09-01 to 2026-11-12.
  **21 days are missing on BOTH symbols, from 2025-12-15 (contiguous, through about 2026-01-04).** Those days are
  ineligible and will not be filled.
- Size: ~312 MB (BTC) + ~318 MB (ETH) compressed, 1,448 files.
- A repo/ledger search finds no implied-volatility or options source in any study. This is a different market (options),
  not a transform of futures/spot prices or flows.

## Rule, one setting (to be frozen by Integration)
- Hourly IV level V_t = the last BVOL value with calc_time in [t, t+1h).
  - The hour is valid only if it has >= 3,000 observations and the last observation is within 60 s of the hour end.
  - x_t = ln(V_t / V_{t-1}), which needs both hours valid.
  - z_t is taken against the prior 168 consecutive valid x, excluding t, ddof 1.
- **Shock hour:** BTC z_t >= 3, the market-wide fear signal. ETH BVOL is used only as a descriptive cross-check, never selected.
- **Long:** in a shock hour, any original9 coin whose futures c_t - o_t <= -ATR14_t (price units) gets a long, entered at the
  t+1h open with the original-t ATR.
- **Short mirror:** BTC z_t >= 3 with a futures c_t - o_t >= +ATR14_t gets a short (an IV spike on a sharp rally, read as
  chasing). Both sides are preregistered and neither is selected later.
- LEVEL events. Tie order and max1 follow the shared simulator. There is no other filter.
- Shared execution, costs, orders, paths, window, split and lead gates are those of FROZEN_RULING @1a1effaa, and futures
  liquidity follows the common rules.

## Mechanism and family dependence
- Fear spikes in options (IV jumps alongside sharp selling) mark forced de-risking. The hypothesis is that the price impact
  partly reverts once hedging demand passes; the classic analogue is buying after volatility-index spikes.
- The trigger relies on options information. The price condition overlaps ATR-shock snapback (4h, REJECTED) and the
  displacement reversals (MPD/TJD/PFX). Without the IV condition this would be a known-failed price rule, so the IV
  filter is the hypothesis. That has to be stated and judged.

## Fee hurdle and frequency risk
- Costs are recomputed per lot as before. The earlier tapes needed roughly +0.09R gross at 15 bp. That figure is
  descriptive, not a gate.
- **Frequency is the main risk.** A BTC z >= 3 hour combined with a >= 1 ATR coin move may give fewer than 3 closes a week
  and fail the frequency gate. This is stated in advance; no relaxing it later.

## Timing and live availability
- The archive's calc_time is event time, not publication time. The historical test uses the hour's last value at the
  hour close and enters at the t+1h open, the standard closed-bar model.
- Live use would need the BVOL value at the hour close from the options API/stream. The exact endpoint, latency and
  history were not verified here and would need their own evidence before any use. **No collector is requested.**

## Acquisition (only after a GO)
- 1,448 official daily ZIPs plus CHECKSUMs, ~630 MB, ≤4 workers, stop on 403/418/429 or a hash failure.
- Stream-decompressed into hourly aggregates. Raw ZIP hashes are pinned; the full 1 s CSVs are not duplicated.
- A coverage audit (per-hour observation counts, monotonic calc_time, gaps) runs before any signal count.

No accounts, keys, workers, schedulers or activation.
