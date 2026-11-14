# EBI proposal: event-burst impact reversal on aggTrades (research only; NO thresholds, counts, outcomes or build before Codex GO)

Question: when a short, one-sided burst of aggressive futures flow moves price a lot PER UNIT of volume (thin liquidity, high
impact), does part of that move give back within minutes, more than enough to pay taker costs? The mirror case (large one-sided
volume absorbed with little price move) is NOT traded; it is reported as a control only.

Why it is new versus the exhausted families:
- AOF-1H / LTI-1H / BOL-1H / TJD-1H / SPOTFLOW used HOURLY kline aggregates (taker-buy share, large-trade proxies, hourly jumps).
  None measured price impact per unit of signed volume at event scale; hourly bars cannot separate a 30-second burst from an hour of drift.
- Here the unit is an event burst built from aggTrades (100 ms same-price/same-side aggregates), the feature is realised impact
  efficiency (move / signed notional) relative to the coin's own recent distribution, and the horizon is minutes, not hours.
- It is still in the broad reversal family (RBR, RSR, snapback): not claimed statistically independent. No hourly field is reused.

Mechanics (shape only; every number below marked [C] is to be frozen in the Codex contract, not chosen from data):
- Burst: rolling [C]-second windows of signed aggTrade notional (taker side from the is_buyer_maker flag); a burst is a window whose
  |signed notional| and |price change| both exceed coin-own rolling quantiles [C] computed from strictly prior data.
- Impact efficiency = |price change| / |signed notional| of the burst, ranked against the prior [C]-day distribution.
- Trade: fade the burst direction only in the top-impact bucket [C]; one setting, both sides, BTC and ETH only.
- Exit: fixed horizon [C] minutes or stop at [C] x burst size; no partial/BE.

Execution assumptions (explicit, conservative):
- Latency: decision at burst-window close + 1 s; entry at the first aggTrade price at or after that time, then +2 bp adverse.
- Spread: BTC/ETH USD-M top-of-book spread assumed ~0.1-0.5 bp; we cannot observe the book from aggTrades, so it is absorbed into the
  2 bp slippage and a stress case adds +3 bp per side.
- Fees: taker 5 bp per side base; stress 13 / 21 bp per side (as X2-WIDE). Round trip base cost ~14 bp, so the hypothesis needs a
  mean give-back well above 14 bp; bursts smaller than that are economically irrelevant by construction.
- Funding: 1bp/8h proxy (minor at minute horizons). Max 1 global position, $8 risk, current filters.

Data limits stated up front: aggTrades are 100 ms aggregates, not individual orders or queue data; book absorption cannot be proven;
the 14,723 raw trade-ID range gaps in BTC Sep 2026 must be characterised (or the affected windows suppressed) before any count.
One month of BTC/ETH is a feasibility sample only; an economic test needs a frozen multi-month window chosen before outcomes.
