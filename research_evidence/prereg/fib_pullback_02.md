# FIB-PULLBACK-02 preregistration

Status: **FROZEN / NOT EVALUATED / RESEARCH ONLY**  
Frozen: 29 October 2026, Cairo (before any market-data evaluation of this key)  
Authority: Codex, ZackBot2 #219 comment 6486879287 (frozen economic hypothesis, one parameter set)  
Review corrections before any evaluation: Codex, PR #236 comment 6487245023 (stop wording, E2 label, breadth gate,
atomic packet publication); no rule or parameter changed  
Strategy key: `fib_pullback_02` (new key; `fib_pullback` v1 and its recorded rejection are unchanged)  
Timeframe: 1h  
Order authority: none; excluded from PAPER rotation, settings, runtime sleeves and live execution

## Hypothesis

FIB-PULLBACK-01 (4h, 3/3 swings, 4 ATR impulse, 0.5 reclaim by any bullish/bearish close) was rejected with negative
gross expectancy in both cells (long PF 0.7008 / -5.867R; short PF 0.5405 / -16.854R). Its entry fired on the first
close through 0.5, with no evidence that the pullback had ended or that participation had returned.

The economic change in v2 is **entry selectivity by structure and participation confirmation**: after a confirmed
trend-aligned impulse and a retracement into the 0.382-0.618 zone, a continuation entry is taken only when the
reclaiming candle also closes beyond the prior candle's extreme, has a with-trend real body of at least 0.35 ATR and
trades at least its trailing 20-candle median volume. The hypothesis is that this confirmation removes the
still-falling (long) / still-rising (short) pullbacks that made v1 lose before costs, and that on 1h the setup is
frequent enough to measure, with positive net expectancy after Binance fees, slippage and funding. Long and short are
separate cells. Fibonacci levels alone are not presumed predictive, and no Fibonacci ratio is searched.

## Frozen rule (one parameter set; `strategies.FIB02`)

- Candles: 1h, closed candles only. ATR is the shared `strategies.indicators` ATR(14) (Wilder), EMA50/EMA200 the shared
  EMAs, volume the kline base volume `v`.
- Swings: confirmed 2-left / 2-right pivots. A pivot high at candle j is a candle whose high equals the maximum of the
  candles j-2 .. j+2; it becomes usable only on candle j+2 (after the second candle to its right has closed). Pivot lows
  mirrored. Historical signals never move when later candles are appended.
- Long impulse: the latest confirmed swing low precedes the latest confirmed swing high, span = high - low. Short is the
  mirror (latest high precedes latest low). Retracement levels are measured from the impulse end: long
  `L(r) = high - r x span`; short `L(r) = low + r x span`.
- Validity, checked on every candle: span >= 3.0 x ATR(14) of that candle, and EMA50 > EMA200 (long) / EMA50 < EMA200
  (short).
- Arming: a valid candle whose range overlaps the 0.382-0.618 zone (long: low <= L(0.382) and high >= L(0.618); short
  mirrored) and that does not close at/through L(0.786) arms the impulse **for the following candles**. The entry
  candle cannot arm itself. A new confirmed impulse resets the arm.
- Entry decision on a closed candle i, all required (long; short mirrored):
  1. armed before candle i, impulse valid on candle i, close above L(0.786);
  2. reclaims 0.5 in the trend direction: close[i-1] <= L(0.5) < close[i] (short: close[i-1] >= L(0.5) > close[i]);
  3. breaks the prior candle: close[i] > high[i-1] (short: close[i] < low[i-1]);
  4. real body in the trend direction: close[i] - open[i] >= 0.35 x ATR(14)[i] (short: open[i] - close[i]);
  5. participation: volume[i] >= median(volume[i-20 .. i-1]) (the 20 candles before it; undefined -> no entry).
- One entry per confirmed impulse and side. The order fills at the **next candle's open** (backtester next-open entry
  with its normal adverse slippage).
- Invalidation: L(0.786) is the **decision-time invalidation and the sizing anchor**. A close at/through it disarms
  the impulse. The emitted stop distance is the decision close's distance to L(0.786) (`atr` signal column;
  management `stop_atr = 1.0`), which sizes the position. The backtester fills at the next candle's open with
  slippage and rests its stop that distance from the fill, so the actual stop shifts by the entry gap and slippage
  and is **not guaranteed to sit exactly at L(0.786)**. The exit signal is retained: a close at/through L(0.786) or
  the EMA50/EMA200 alignment flipping. The core backtester is not changed for this record.
- Management, frozen and identical to v1 in bar units: 50% at +1R, breakeven at +1R, full target +2R, maximum 30
  candles (30 h on 1h). Existing backtester stop-first intrabar ordering is unchanged.
- Long/short are produced by one mirrored function; an ambiguous candle with both entries takes neither (`_one_side`).

## Runner and explicit equivalents

The run reuses the shared cost-aware candidate runner that produced the v1 result,
`tools/research/run_breadth_candidate.run` (byte-identical; frozen and sha256-pinned), through the thin wrapper
`tools/research/run_fib02_candidate.py`, which fixes the key to `fib_pullback_02`, the management to the frozen
defaults (no override), one side per call and `--tier` only (no symbol list). Two runner constraints force a
difference and are preregistered as explicit equivalents, scoped to the call:

- **E1 timeframe.** The frozen runner's cached-timeframe table holds 4h and 15m only. For the call, 1h is added;
  every other step is the runner's own timeframe-generic path: a bounded 1h research cache from
  `tools/research/collect_klines_research.py --interval 1h` (Binance USD-M trade klines, never past the sealed
  cutoff), completeness on the 1h grid, 220-candle warm-up inside the runner's 4h warm-up guard, funding scaled to
  the timeframe, and the BTC regime split from a 4h BTCUSDT cache.
- **E2 exchange floors.** The frozen runner uses the legacy notional-only floor (`exchange_rules=None`; v1's packets
  were `non_promotable` partly for this). For the call, the backtester receives the committed, directly fetched
  mainnet snapshot `data/exchange_rules_mainnet.json` (step size, minimum quantity, minimum notional). An entry below
  the floor is skipped, never rounded up. Skips are reported. The snapshot postdates the window; exchange rules are
  not market data and only tighten execution. E2 is labelled, in this record and in every packet, a
  **prospective / current-rule feasibility check, not point-in-time historical exchange rules**; it cannot by itself
  make a packet promotable (packets stay `non_promotable`).
- **Packet publication.** The shared runner writes a sibling staging file; the wrapper validates it, adds the FIB-02
  envelope, recomputes `packet_sha256` and only then publishes the requested path atomically (temp file, fsync,
  replace). The final path holds nothing or the complete wrapped packet. A staging file left by an interrupted run
  is never reused or silently deleted: the run refuses until it is removed.

## Evaluation plan (development only)

1. Phase A unit gates (this record's commit): synthetic long/short mirror symmetry, delayed pivot availability and
   prefix causality, each entry condition gating independently (reclaim, prior-candle break, body, volume, zone
   arming, 0.786 invalidation, impulse, EMA alignment), one entry per impulse, runtime exclusion (not in PAPER
   rotation, dropped by the engine load guard with `integrity|research-only-sleeve`, refused by `validate_sleeve`
   without `allow_research`), v1 unchanged, the wrapper's scoped equivalents, and atomic packet publication under an
   injected post-run / pre-publish failure. No market data is read in Phase A.
2. Phase B development run, on a separate go-ahead:
   - Universe: the entire declared Binance tier the shared runner accepts, **CORE-20** of the owner-approved frozen
     static universe `static-top40-minhist365-contract-20261011` (`research_evidence/universe/`), the CORE-10 tier v1
     used plus ranks 11-20. Completeness is the runner's rule: a symbol with any missing 1h candle in
     [warm-up start, END) is `INCOMPLETE`, excluded from every aggregate and listed; nothing is filled. From listing
     dates, HYPEUSDT and PUMPUSDT are expected INCOMPLETE; the collector manifest decides.
   - Window: 2024-10-14T00:00:00Z to **2026-09-28T00:00:00Z exclusive** (the sealed boundary), the same development
     window as v1 for a like-for-like comparison. 17,136 decision candles per symbol plus a 220-candle warm-up. No
     candle at or after the sealed boundary is read; the holdout is not read.
   - Costs: next-open fills, 5 bp taker fee per fill, 2 bp adverse slippage per fill, funding 0.125 bp per 1h candle
     (the runner's 0.5 bp per 4h, scaled), current mainnet exchange floors (E2). Account and sleeve: the runner's app
     defaults ($500 start, 1% risk, max 4 positions, max leverage 10, 8% daily halt).
   - Cells: `fib_pullback_02` long and `fib_pullback_02` short, one packet each.
   - Reported per cell: requested / run / INCOMPLETE symbols with reasons, raw signals, floor skips, closed trades,
     gross and net P&L and R, fees, funding, mean R, win rate, PF, max R and equity drawdown, BTC-EMA200 regime split,
     per-symbol rows, and the comparison with v1 (trades, mean R, PF, net R per cell). Per-symbol and regime rows are
     descriptive only.
3. No parameter grid, no Fibonacci-ratio or confirmation-threshold search, no post-hoc symbol, regime or session
   filter, no management change, no holdout read and no PAPER slot.

## Decision rule

- If a cell's development **net expectancy (mean net R per closed trade) is <= 0**, that cell is rejected immediately
  and recorded; no walk-forward, holdout or exit tuning follows for this version.
- A cell with net expectancy > 0 is only a **development survivor**, not a pass. The breadth gate for the next step
  is **at least 100 closed trades across at least 8 symbols with trades** (packet `fib02.breadth`:
  `closed_trades`, `symbols_with_trades`, `breadth_ok`). A positive cell that misses it is
  **INCONCLUSIVE / INSUFFICIENT BREADTH**, never a PAPER pass.
- A positive cell that meets the breadth gate is handed off for independent Evidence review (causality,
  multiplicity, cost/floor, regime spread and frequency/breadth checks) and an Integration anchored walk-forward
  ruling. The holdout stays sealed until that ruling.
- Any revision is a new preregistered version; this record is never tuned or relabelled.

## Multiplicity

Two cells (long, short), one parameter set, one window, one tier. This is the second Fibonacci-pullback hypothesis
(v1 rejected); both count in the strategy-factory trial ledger and in any later multiplicity adjustment.

## Boundaries

No PAPER slot, runtime setting, cohort, order, mainnet path, exchange credential, TradingView alert or ML authority is
changed by FIB-PULLBACK-02. A future PAPER canary requires a separately reviewed promotion record.

ROADMAP FIT: Binance strategy discovery; frequent, cost-aware Fibonacci continuation candidate.
DEFERRED: Phase B run (separate go-ahead), walk-forward until a development pass, sealed holdout, PAPER promotion,
ML/order authority, mainnet and non-Binance venues.
