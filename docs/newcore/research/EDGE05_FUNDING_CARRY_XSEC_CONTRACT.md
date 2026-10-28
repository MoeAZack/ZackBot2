# EDGE-05 — `funding_carry_xsec.v1` preregistration

Status: **DRAFT - UNSIGNED - NOT FROZEN** (28 October 2026 Cairo, Build lane, from Codex roadmap 6455796158 item 4).
This page is the human-readable twin. The **authoritative** preregistration is
`research_evidence/prereg/funding_carry_xsec.v1.json`; where they differ, the JSON wins. Tests and runners read only
that JSON, never this page (the CI reuse guard: executed code must not depend on `docs/`). No backtest, return, P&L or R has been computed for this family, and nothing on or after
2026-09-28T00:00Z has been read.

Signing: the JSON lands DRAFT on this branch (a new file under the append-only `prereg/` dir). Integration freezes the
numbers and flips `status`/`signed` in it before merge, then creates `research_evidence/ledger/funding_carry_xsec.jsonl`
plus one `REGISTRY.jsonl` line, all before any market result. No ledger or REGISTRY line exists while it is DRAFT.

## Hypothesis

Perpetuals whose recently settled funding is extreme relative to the cross-section earn a positive after-cost return
over the next ~24h when held against the crowd: short the highest, long the lowest, net of the funding actually paid
or received. This is a frequent-portfolio, longer-hold candidate, distinct from the 4h trend families.

## 1. Universe

STATIC-40: `research_evidence/universe/static-top40-minhist365-contract-20261011.json` (sha256 `d3460e6d…5b031`), all
40 symbols. It is frozen and current-universe / survivor-biased, the same artifact EDGE04 uses. CORE-10-ELIGIBLE-9 and
fixed-18 are too narrow for k = 5 per side.

A symbol is eligible at decision D only if its first settled funding event and its first 4h kline are each at least
30 days before D, its funding sequence in the window [D-24h, D) passes `costs.check_funding_sequence`, and the 1d bars it needs
for the R unit exist. It needs **at least 15 eligible symbols**. On a day with fewer, no cohort opens; that day is
reported, not counted as INCOMPLETE. There is no back-fill before a symbol's listing. If a 4h bar, mark bar or funding
event is missing inside an open episode, that episode is INCOMPLETE. It is never dropped silently.

## 2. Signal

**F24(s, D) = the sum of settled `fundingRate` over every funding event whose nominal time T satisfies
T + 300s in (D-24h, D], i.e. D-24h-300s < T <= D-300s.** Nominal times are whole hours, so this is exactly
**T in [D-24h, D)** (Cowork 6459484792 B1: the window is defined on availability).

- **Interval-agnostic:** the sum covers exactly 3 events on the 8h grid, 6 on the 4h grid and 24 on the 1h grid. The
  event at T = D is not yet available, so it is never in the window and never required.
- **Sequence check:** `costs.check_funding_sequence(symbol, rows, start_ms=D-24h, end_ms=D)`. The rows run from the phase
  anchor (the event at or immediately before D-24h) through the last event with T + 300s <= D. The covered interval is
  [D-24h, D), so the event at D is not covered and is not passed. Equivalently (Codex 6459518132):
  start_exclusive = D-24h-300s, end_inclusive = D-300s, with the latest nominal event at or before the lower boundary as
  the cadence phase anchor. A missing, duplicated or off-grid event inside the
  window makes the symbol ineligible at D.
- **Why 24h:** the spec holds each position for about 24h, so the look-back equals the hold. A 24h sum is the only
  window that normalises the 1h, 4h and 8h cadences without a free parameter. It is not tuned, and no alternative
  window is evaluated.
- **Settled only:** an event counts only if its archived calc_time is no later than T + 60s and T + **300s**
  (the availability lag) is no later than D.
- **Missing data:** if an expected event in the window is missing, duplicated or off-grid, the symbol is ineligible
  at D.

## 3. Selection

Rank the eligible symbols by F24, breaking ties by symbol name.

- **Short book:** the **k = 5** highest.
- **Long book:** the **k = 5** lowest.
- k = 5 is the existing EDGE04 H2 rotation constant. It is fixed and was not searched.
- There is no sign filter or threshold.
- The two books are separate primaries, with **at most 5 positions** each and equal risk per position.
- A book holds at most one position per symbol.
- No symbol can sit in both books, because 15 > 2k.

## 4. Execution

| Item | Rule |
|---|---|
| Decision | 00:00 UTC daily (D); one cohort per book per day |
| Entry | Market at the first 4h open at or after D + lag, i.e. **04:00 UTC** |
| Exit | Market at the 4h open **24h after entry**, or earlier by the protective stop |
| Funding held | Every event with entry <= T < exit (`costs.py` rule); exactly 3 on an 8h grid |
| Entry-bar stop | The 04:00-08:00 UTC entry bar is stop-checked from its 04:00 open with the same conservative rule: gap/open first, then stop-first over its full range (Cowork N2, Codex item 3) |
| R unit / stop | 2.0 x TR-SMA14 of the 15 closed 1d bars ending at D; on 4h bars the gap open is checked first, then stop-first |
| Roll | A symbol re-selected next day is closed and re-opened as a new episode, with both fills charged |
| Account | 10,000 USDT per book; 1% risk per position; max 3.0x gross per book (if a cohort would exceed it, every position is scaled down equally) |
| Filters | Historical stepSize, minQty and minNotional; quantities are floored and never rounded up. All fills are taker |

## 5. Costs

- **Fees:** 0.05% taker on every fill.
- **Slippage:** slip-v1 on 4h bars with the slip-cal-v1 crypto coefficient. While SLIP-C-UNFITTED applies, the
  fallback is 0.05% per fill.
- **Funding:** the actual signed settled rate x the funding-mark-proxy-v1 notional at each event while the position
  is held.
- **Net formula:** **net = pnl_gross + pnl_fill_adj - fees_actual + funding_actual** (`actual_costs.measured`).

Stress rows reprice the same episodes, and stress never inflates received funding:

| Row | Fees, slippage | Funding paid | Funding received | Role |
|---|---|---|---|---|
| base_x1 | x1 | x1 | x1 | gate |
| all_variable_costs_x1.5 | x1.5 | x1.5 | / 1.5 | gate (> 0) |
| all_variable_costs_x2 | x2 | x2 | / 2 | promotion gate (> 0) |
| funding_zero | x1 | 0 | 0 | report-only (carry vs price) |
| funding_mark_adverse | x1 | costs.py row | costs.py row | report-only |
| entry_latency_1bar | x1 | x1 | x1 | report-only, entry and exit +4h |

## 6. Splits

The splits are the EDGE04 / R4 boundaries:

- **Calibration:** 2020-02-03 to 2021-01-04, used for warm-up and eligibility only.
- **Train:** 2021-01-04 to 2021-12-20.
- **Walk-forward:** 5 folds ending exclusively at **2026-09-28T00:00Z**.

Each split starts with a fresh book. The lookback is 180 4h bars (30 days) and the horizon is 6 bars (24h). The purge
is 180 bars and the embargo is 1 bar (`splits.py`).

Everything on or after 2026-09-28 is sealed. The proposed holdout runs from 2026-11-02 to 2027-05-03, needs at least
500 episodes per side, and is revealed once. Integration freezes these dates.

## 7. Metrics and pass rules (per side; proposed for Integration to freeze)

| Gate | Threshold |
|---|---|
| Data | At most 2% INCOMPLETE episodes per WF fold, and no unresolved accounting row. Otherwise the side is INCOMPLETE |
| Activity | At least 1000 closed episodes per side across WF, and at least 200 cohort days in every fold |
| Fold stability | Mean net R > 0 in **at least 4 of 5** WF folds |
| Expectancy | Combined WF mean net R > 0, PF > **1.10**, and a 95% LCB > 0 (moving-block bootstrap of daily cohort means, block 30, 10,000 resamples, seed 20261028) |
| Baselines | Above the 95th percentile of the B0 book means, and above B1 |
| Stress | x1.5 mean net R > 0 (PASS gate); x2 > 0 (required for promotion) |
| Drawdown and tail | Tiered (below). On every path: x1.5 DD <= **65%**, longest underwater **<= 365 days**, worst cohort >= **-7.5R** |
| Concentration | No symbol > **25%** of absolute net R; top 3 <= **50%** |
| Capacity | Median position notional <= **0.1%** of the prior-24h quote volume; turnover reported |
| Multiplicity | Holm alpha 0.05 across P-SHORT and P-LONG |

**DD tiers (owner high-DD/profitability rule, Codex 6457763292).** A higher DD is not an automatic rejection when the
after-cost return, cost stress and recovery are credible. The DD gate passes when either tier holds and every all-path
ceiling above holds:

- **Ordinary:** base max equity DD **<= 35%**, and closed-R DD < **40R**.
- **Profitability rescue:** any base DD **<= 55%** (no lower bound), when annualized after-cost return / max DD
  **>= 0.75** and total net R / closed-R DD **>= 2.0**. Because the rescue has no lower bound, the gate is monotone:
  a lower-DD book can never fail where a higher-DD book with the same ratios passes (Codex 6459518132 N1).
- **FAIL on any path:** base DD > **55%**, or x1.5 DD > **65%**.

The other gates (worst cohort, folds, expectancy, PF, cost stress, concentration, multiplicity) are unchanged. Return,
max DD, return/DD (Calmar), PF, net expectancy, trade count, exposure time and the longest recovery are reported
together (Codex 6457388087). Per side and per fold, the report also gives the count and share of selected symbols
with positive, zero and negative F24 at selection, and the funding versus price contribution to net R, so a market-wide
funding-sign shift is visible (Codex ruling 2). No DD threshold may be relaxed after holdout P&L is seen. A passing book enters shadow with at most **25%** of the portfolio risk budget
and at most 5% open risk.

## 8. Nulls and baselines

- **B0:** 1000 seeded random books. Each day it draws k symbols from that day's eligible set and uses the same
  cadence, hold, stop, sizing and costs.
- **B1:** an equal-risk book long or short every eligible symbol (matching the side).
- **B2:** the sign-flipped selection, report-only.
- **D-LS:** the market-neutral sum of both books, report-only. It can never promote or rescue a primary.

The trial budget is 2 primaries + 1 diagnostic + 6 baseline rows = **9 ledger trial records**. No prior funding-carry
looks were found in `REGISTRY.jsonl`.

## 9. Data coverage (metadata only)

`tools/research/edge05_coverage.py` streams the manifest's file entries. It opens no archive and reads no rate or
price. Over the window 2020-02-03 to 2026-09-28 (end exclusive), the static-40 result is:

- **Status:** COMPLETE.
- **Funding:** 40/40 symbols have funding data, with 0 missing symbols and 0 month gaps.
- **Rows:** 224,064 funding rows in full months.
- **Straddle month:** the 2026-09 month file straddles the cutoff for all 40 symbols. Its rows are not counted.
- **Execution and cost series:** the 4h klines, 1d klines and 1h mark data are also complete for 40/40 symbols, with
  no gaps.
- **Symbols per month:** between 11 and 40 symbols have funding data each month. At least 15 have it from 2020-09,
  20 by 2021-01 and 22 by 2022-01.

## 10. Failure modes (fail closed)

- **Incomplete funding:** the symbol is ineligible at D. Inside a hold, the episode is INCOMPLETE. More than 2% per
  fold makes the side INCOMPLETE.
- **Missing symbol or bar:** reported, never forward-filled, and never allowed to shrink the universe silently below
  the 15-symbol minimum.
- **Holdout timestamp:** any access on or after 2026-09-28 during prep or development is refused. The coverage tool
  refuses before it reads.

## Open questions for Integration

1. Stress semantics: this draft scales received funding by 1/m. EDGE04's "1.5x all variable costs" may scale signed
   funding, which would favour a carry book.
2. Sign filter: pure rank, or require F24 > 0 for shorts and F24 < 0 for longs?
3. Holdout: a 26-week window with at least 500 episodes per side, or EDGE04's 52 weeks?
4. Roll: close and re-open every day (conservative, as drafted), or carry a re-selected symbol over?
5. The 04:00 UTC entry follows from 4h klines only; 1m klines exist if an earlier entry is wanted.

ROADMAP FIT: Binance strategy discovery, frequent portfolio, longer hold.
DEFERRED: any evaluation, holdout access, PAPER/runtime change, mainnet.
