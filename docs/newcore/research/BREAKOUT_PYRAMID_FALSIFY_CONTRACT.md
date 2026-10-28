# breakout_pyramid falsification contract (v1, DRAFT)

Authoritative: `research_evidence/prereg/breakout_pyramid_falsify.v1.json`. This page is a summary; where they differ, the JSON wins.

Status: DRAFT, unsigned, not evaluated. Aligned to Integration ruling Codex 6468454634. Integration freezes and signs; no ledger line exists yet.

## Why
Integration triage (development packet, legacy cost assumptions) flagged `breakout_pyramid` long on the fixed 17-symbol
book as a high-drawdown probation candidate. That result is triage, not evidence. The ruling: isolate entry edge before
pyramiding gets any credit.

## Identity (pinned at 612b537)
- `strategies.py` `STRATEGIES['breakout_pyramid']`, `sig_breakout_pyramid`, long only, 4h.
- Entry: close > prior 20-bar high and EMA50 > EMA200. No signal exit.
- Live mgmt: stop 2.0 ATR, chandelier trail 3.0 ATR, pyramid n=2, step 1.0R, frac 0.5.
- Bound by sha256 of the function / `indicators` / `ema` source (LF), plus the merged mgmt dict; tests check both against the live code.

## Fills (causal)
The signal is computed on the closed bar t, using the channel high shifted by one bar. Entry is a taker fill at the
**open of bar t+1**. Time exits fill at the next open. Stops and adds fill intrabar at their level, in `backtest.py`
path order: gap first, then conservative stop-first.

## Universe
- Primary: **FIXED-17** = `engine.CAND_FIXED18` minus LSKUSDT, pinned explicitly with a sha256 of the sorted list.
- **Selection-bias caveat:** Integration chose this book, and the fixed-18 list was itself picked after factory
  results. A pass here is weaker than a pass on a pre-existing list. STATIC-40 is reported alongside as a report-only
  bias check; it can never pass or rescue anything.

## Windows
- Development window 2021-01-04 to **2026-09-28** (exclusive), after a 2020 warm-up. It is split into three
  chronological slices: 2021-01-04 to 2022-12-19, 2022-12-19 to 2024-12-16, and 2024-12-16 to 2026-09-28.
- The 5 EDGE04 folds are reported but never gated.
- Episodes belong to the slice of their entry fill and run past internal slice boundaries to their exit.
- **Last-entry cutoff 2026-08-29T00:00Z** (180 x 4h bars before the sealed boundary). No stage and no book admits an entry from then on. An episode still open at 2026-09-28T00:00Z is INCOMPLETE: excluded from every gate statistic, counted and reported, and counted in the 2% data gate. No price on or after the boundary is read to close it.
- Holdout: sealed. This contract does not open or schedule any holdout; whether a survivor needs one is **deferred to Integration**. The breadth_static40 registration is untouched.

## Decision chain: entry-only -> trail -> pyramiding
| Stage | Exit | Role |
|---|---|---|
| S1 | plain 2 ATR stop, exit after 42 bars | **H1** entry-only, gated |
| S2 | 2 ATR stop + 3 ATR trail, no adds, no time cap | **H2** gated |
| S3 | S2 + pyramid = live default management exactly, no time cap | **H3** gated, paired over S2 |
| S1-T | plain stop + 3R target, 42 bars | report-only |

- **Paired unit replay:** one entry set E (from S1) is replayed at quantity 1 under every stage, so stages differ only after the fill. This is an independent paired analysis. Because E re-arms on S1 exits, S2/S3 unit episodes on one symbol may overlap in time, which is allowed only in this analysis.
- **H1 (S1)** needs all of the following, otherwise the family is **FALSIFIED**:
  - base mean net R > 0 with 95% LCB > 0, and mean net R > 0 at 1.5x cost;
  - positive in all 3 slices;
  - positive with the best symbol removed, within concentration limits;
  - above the 95th percentile of 1000 random-entry books;
  - the activity gate passed.
- **H2 (S2)** is tested only after H1 passes. S2 itself must be positive at base (LCB > 0) and at 1.5x, in all 3 slices, with the best symbol removed and within concentration limits.
- **H3 (pyramiding)** is tested only after H1 and H2 pass. S2 and S3 differ only by the adds, so the paired increment S3 - S2 isolates them. It must have base LCB > 0, be positive at 1.5x and positive in all 3 slices, and S3 must also pass the H2 test. S2 - S1 is report-only.

## Book (separate from E)
- Only the deepest credited stage is replayed as a book. It is built from that stage's raw signals and exits; E is never fed into it.
- One open episode per symbol. A signal on a symbol that is already open is skipped and counted.
- Each 4h bar runs four causal phases, in this order:
  1. **Open:** only events knowable at the open: gap stops (filled at the open) and scheduled next-open exits. Symbols go in name order, ascending.
  2. **Admit:** entries pending from the previous close are admitted, symbol ascending, using the open-time state after phase 1 only (`capacity_for_open_entries: open_state_after_phase_1_only`). An entry needs fewer than 4 open lots and gross notional <= 3x equity; otherwise it is skipped and counted, never scaled down.
  3. **Intrabar:** each open position's path is walked, symbol ascending, including positions admitted in phase 2. This follows the `backtest.py` `path_points` convention (green candle open, low, high, close; red candle open, high, low, close) and the FBL-BT01 `walk_path` rules. Adds, stops and trail updates happen only here. An add that would breach the gross cap is skipped and counted.
  4. **Close:** signals are computed on the closed bar and become pending for the next open.
- No intrabar event can free or consume capacity for the same bar's open entries. Where `backtest.py`'s cross-symbol loop differs, the contract wins.
- Account: 10k USDT, 1.0% risk per entry, historical exchange filters, every fill taker.

## Costs and gates
- Taker 0.05% per fill (including adds), slip-v1 (0.05% fallback), actual signed funding. Base and 1.5x are reported and gated; 2x is reported.
- Activity: at least 300 episodes in total, at least 75 per slice, and at least 2 per 30 days in each slice. At least 15 of the 17 symbols must be active overall, and at least 8 per slice. Slice 1 predates SUI, ENA, WLD and 1000PEPE.
- Book DD: ordinary (DD <= 35%, closed-R DD < 40R) or profitability rescue (DD <= 55%, Calmar >= 0.75, net R / closed-R DD >= 2.0). A rescue-only pass is a HIGH-RISK SLEEVE. Underwater must be <= 365 days.
- Concentration: symbol share <= 25%, top 3 <= 50%. The worst episode must be >= -3R, with no causality or accounting defect.
- **Sizing is report-only, never a gate.** The book is reported at fixed 1.0%, 0.5% and 0.25% risk per trade. The deployment risk budget is deferred to the later PAPER admission contract.
- The bootstrap is frozen in the JSON: moving blocks of 8 weeks over weekly mean net R, 10000 resamples, seed 20261028.

## Budget
3 gated variants (S1, S2, S3), 1 report-only diagnostic (S1-T) and 2 baseline rows (B0 gated, B2 report-only) give 6
ledger trial records. The fixed sequence H1 -> H2 -> H3 at one-sided alpha 0.05 each keeps family-wise error <= 0.05. Nothing is tuned.

## Deferred
There is no runner yet. It must follow the EDGE05 runner pattern and reuse `backtest.py` path semantics plus `costs.py`.
Running it needs Integration's authorization and EDGE04 to free RAM. No holdout, no PAPER settings, no live change.
