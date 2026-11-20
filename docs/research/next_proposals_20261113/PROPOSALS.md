# Two new information-source proposals (Claude, 13 Nov 2026) - PROPOSALS ONLY

No counts, outcomes, signals, implementation or bulk download. Source checks used S3 listings plus one sample file each:
BTCUSDT bookDepth 2025-01-15 and DOGEUSDT premiumIndexKlines 1h 2025-03.
All data is consumedDEV for the window 11 Oct 2024 to 27 Sep 2026.
Shared execution, if frozen: the reviewed RSR/X2 lot simulator unchanged (3 ATR14 stop, 2R, 12h, max1, $8 / $3,333.33,
7/15/23 bp, 1bp/8h funding stress, 40 orders, causal + 4h diagnostic, Cairo $120 halt) and the SPB-D01 lead gates.

**Fee hurdle (both).** The completed SVS/IMP order-0 tapes at 15 bp show about 0.073R fees + 0.011R slippage + 0.004R funding
per trade. A candidate therefore needs roughly >= +0.09R gross per trade before costs; at 23 bp it needs about +0.12R.

## Source availability check

| Source | Official location | Schema (sampled) | Window coverage | Size |
|---|---|---|---|---|
| USD-M order-book depth | data.binance.vision `futures/um/daily/bookDepth/<SYM>/<SYM>-bookDepth-YYYY-MM-DD.zip` | `timestamp,percentage,depth,notional`; percentage -5..-1 and 1..5 (cumulative depth within +/-p% of price); 2,880 snapshots/day (~30 s) | First file 2023-01-01. ADAUSDT: 0 of 717 window days missing. Other 8 coins: listing interrupted, NOT yet verified | ADAUSDT 322 MB, so ~2.9 GB compressed / ~6,450 files for original9 |
| USD-M premium index | `futures/um/monthly/premiumIndexKlines/<SYM>/1h/` | kline schema: open_time, o/h/l/c of the premium fraction, close_time, count=720 (5 s samples) | 24/24 months (2024-10..2026-09) for all original9 | ~10 KB/month |
| USD-M liquidations | **NOT PUBLISHED.** Official `futures/um/daily` has no `liquidationSnapshot`. Only COIN-M BTCUSD_PERP has one, 2023-06-25..2024-10-14, ending before the window | n/a | **GAP**: no defensible official liquidation history for the window | n/a |

The live `forceOrder` stream is a 1-per-second snapshot with no history. Using it would need a prospective collector
(worker/scheduler), which is DEFERRED. **Liquidation pressure therefore cannot be tested directly**; P4 is an explicit
premium-based proxy, not liquidation data.

## P3 BDI-1H: hourly order-book depth imbalance (L/S, original9). Recommended first.

- **Information.** Resting limit-order liquidity near the price. This has not been used in any repo study or the 10 ledgers:
  `bookDepth`, `depth imbalance` and `order book` searches hit no strategy. It is distinct from trade-side flow (SPOTFLOW
  taker imbalance, aggressor absorption, EBI aggTrades) and from price-basis studies (SPB, MPD).
  Family: microstructure/flow, related to absorption research. No independence claim.
- **Feature, one setting.** For completed hour t, I_t = time-mean over that hour's snapshots of
  (bid_notional_1% - ask_notional_1%) / (bid_notional_1% + ask_notional_1%), using only snapshots with timestamp in [t, t+1h).
  z_t = (I_t - mean) / std over the prior 168 consecutive valid hours, excluding t, ddof 1.
  An hour is valid only if it has >= 100 snapshots (~120 expected); otherwise it is ineligible and never filled.
- **Signal.** First cross z >= 2: long. First cross z <= -2: short. Bid-heavy books lead to drift up. No price, trend or volume filter.
  SVS/SPB liquidity eligibility on futures qv.
- **Causal timing.** The archive is published the next day, so the historical test uses snapshots < t+1h and enters at the
  t+2h futures open (one extra hour of publication margin, as SPB). Live use would need our own depth sampling (REST
  `/fapi/v1/depth` each ~30 s), a collector that is DEFERRED. A DEV pass would therefore lead to a forward-collection
  ruling, not activation.
- **Prior.** Low. Published depth-imbalance predictability decays within minutes, the horizon here is hours, and spoofing or
  cancellations add noise. The fee hurdle above applies.
- **Acquisition before any count.** ~2.9 GB / ~6,450 checksum-verified daily files, fetched once into a pinned cache, plus a
  coverage/gap audit (snapshots per hour, timestamp monotonicity, percentage-level completeness). Cloud S3 listing and GET
  work from this environment (sample fetched).

## P4 PFX-1H: premium-index flush reversal (L/S, original9). Proxy for liquidation pressure; ranked second.

- **Information.** The 5-second premium index (impact bid/ask vs multi-venue index) summarized hourly. Its intrahour LOW/HIGH
  measures how far the perp was forced below/above the index within the hour. That is a forced-flow intensity proxy, not
  close-to-close basis.
- **Signal, one setting.** Completed hour t, flush-long if premium_low_t <= median(premium_low over prior 168h, excl. t)
  - 4 x MAD(same window), AND the futures hour return <= -1 x ATR14_t. Mirror short: premium_high spike with an up move
  >= 1 ATR. Entry at the t+1h futures open (live-causal: `/fapi/v1/premiumIndexKlines` is available at hour close; the
  history comes from the monthly archive). No other filter.
- **Family dependence (explicit).** Same mechanism family as MPD-1H (mark-vs-last reversion; long gross ~= fees, REJECTED),
  TJD-1H, ATR-shock snapback 4h (REJECTED) and SPB-D01 (FAIL). It differs only in measuring intrahour forced dislocation
  from the official premium series and conditioning on price displacement. Codex may reject it on family grounds;
  if so, liquidation pressure stays an acknowledged data gap.
- **Acquisition.** 216 monthly files, ~2 MB total, plus existing canonical futures. Trivial.
- **Prior.** Low to moderate. It needs gross >= ~0.09R, and MPD's comparable long cell reached only about fees.

## Requested ruling

Freeze, amend or reject P3 and P4. For P3 also approve the ~2.9 GB acquisition and coverage audit, which reads no outcomes.
No accounts, keys, workers, schedulers or activation.
