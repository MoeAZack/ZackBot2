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
- Holdout: sealed. This contract does not open or schedule any holdout; whether a survivor needs one is **deferred to Integration**. The breadth_static40 registration is untouched.

## Decision chain: entry-only -> pyramiding
| Stage | Exit | Role |
|---|---|---|
| S1 | plain 2 ATR stop, exit after 42 bars | **H1** entry-only, gated |
| S3 | live mgmt (trail + pyramid), 180-bar research cap | **H2** pyramiding, gated, paired over S1 |
| S1-T | plain stop + 3R target, 42 bars | report-only |
| S2 | stop + trail, no adds | report-only (splits S3 - S1 into trail vs adds) |

- One entry set E (from S1) is replayed under every stage, so stages differ only after the fill.
- **H1 (S1)** needs all of the following, otherwise the family is **FALSIFIED**:
  - base mean net R > 0 with 95% LCB > 0, and mean net R > 0 at 1.5x cost;
  - positive in all 3 slices;
  - positive with the best symbol removed, within concentration limits;
  - above the 95th percentile of 1000 random-entry books;
  - the activity gate passed.
- **H2** is tested only after H1 passes. The paired increment S3 - S1 must have base LCB > 0, be positive at 1.5x cost and positive in all 3 slices. S3 itself must pass the H1 gates except the random-entry one.
- The deepest credited stage is the only one replayed as a book (10k USDT, 1% risk, max 4 open, 3x gross, taker).

## Costs and gates
- Taker 0.05% per fill (including adds), slip-v1 (0.05% fallback), actual signed funding. Base and 1.5x are reported and gated; 2x is reported.
- Activity: at least 300 episodes in total, at least 75 per slice, and at least 2 per 30 days in each slice. At least 15 of the 17 symbols must be active overall, and at least 8 per slice. Slice 1 predates SUI, ENA, WLD and 1000PEPE.
- Book DD: ordinary (DD <= 35%, closed-R DD < 40R) or profitability rescue (DD <= 55%, Calmar >= 0.75, net R / closed-R DD >= 2.0). A rescue-only pass is a HIGH-RISK SLEEVE. Underwater must be <= 365 days.
- Concentration: symbol share <= 25%, top 3 <= 50%. The worst episode must be >= -3R. Sizing must still be feasible after scaling to the portfolio DD budget, with no causality or accounting defect.

## Budget
2 gated variants (S1, S3), 2 report-only diagnostics (S1-T, S2) and 2 baseline rows (B0 gated, B2 report-only) give 6
ledger trial records. The fixed sequence H1 -> H2 at one-sided alpha 0.05 keeps family-wise error <= 0.05. Nothing is tuned.

## Deferred
There is no runner yet. It must follow the EDGE05 runner pattern and reuse `backtest.py` path semantics plus `costs.py`.
Running it needs Integration's authorization and EDGE04 to free RAM. No holdout, no PAPER settings, no live change.
