# EDGE-02 replacement strategy scout

Status: **Codex-frozen development screen v1 — 26 October 2026, Cairo.**

This work is independent of the PR #208 packet audit and may run in parallel. It changes no PAPER/testnet settings and
authorizes no strategy for orders. Every candidate remains research/shadow-only until it independently passes the gates
below and a later bounded PAPER cohort.

## Why this is NOW

The current cohort has 22 registered strategies, but only six may seek entries in the active rotation group, ten wait for
another rotation group, and six are parked. The five parked fast strategies supplied 131 of 154 cohort closes (85%) while
losing $532.61 after costs. Turning them back on would restore turnover by restoring a known loss source. EDGE-02 therefore
replaces, rather than re-enables, that fast-strategy capacity.

Corrected exact-head checks also reject two apparent shortcuts:

- `dca_dip` on 1h: 2,034 trades, PF 0.80, -58.1%, -67.6% drawdown;
- high-frequency Active 1% mix: 3,695 trades (17.4/week), PF 1.12, but -61.7% walk-forward drawdown.

The regime-gated aggressive 4h mix is a useful control (PF 1.96, 3.4/week, -30.9% development drawdown; walk-forward
-31.5% drawdown), but it is not frequent enough to replace the parked fast group.

## Frozen candidate queue

Long and short are separate hypotheses and may not rescue each other by pooling.

1. `structure_cf.v1` — original confirmed-structure entry and management, with causal side-specific regime, cost/ATR,
   volume and re-entry-lock gates. This tests whether positive gross structure edge survives lower turnover.
2. `rsi2_reclaim.v1` — 15m trend pullback using RSI2 exhaustion, prior-bar reclaim, local EMA trend, BTC regime and a
   cost floor. It is distinct from EMA-touch scalp and QBank.
3. `squeeze_release.v1` — 15m compression-to-Donchian release with volume, local trend, BTC regime and cost gates. It is
   distinct from single-candle momentum burst and the 4h breakout sleeve.
4. `btc_residual_mr.v1` — 15m beta-residual mean reversion against BTC, range-only, using shifted rolling estimates and
   symmetric z-score exits/invalidation. It is distinct from outright-price range/VWAP strategies.
5. Control only: existing `pullback_rsi.short` with `when=bear`, unchanged signal and management, to verify whether its
   older positive equal-cost result survives current costs and regime qualification.

No DCA is included. No threshold may be selected after seeing results.

### Candidate 4 frozen mechanics addendum — before any result

`btc_residual_mr.v1` uses the strict synchronized 15m intersection of BTC and each eligible non-BTC CORE-10 symbol.
At closed bar `t`, estimate a **zero-intercept** beta from the previous 1,920 paired log-return observations ending at
`t-1`, requiring at least 1,536 valid pairs. Define `e_t = r_symbol,t - beta_t * r_BTC,t`, sum the latest 32 residuals
into `Q_t`, then z-score `Q_t` against the previous 1,920 `Q` observations ending at `t-1`.

Only the last completed 4h BTC `range` regime may admit entries. Long enters on the first upward cross through `-2`;
short is the mirror downward cross through `+2`. Fill is no earlier than the next open. Both sides use a 1.5 ATR14
stop, a 16-bar cap, a five-times round-trip-cost admission floor and an eight-full-bar same-side re-entry lock. Exit at
the directional zero crossing or adverse invalidation only: long at `z <= -3.5`, short at `z >= +3.5`. Diagnostics are
entry z 1.5/2/2.5 only and cannot replace the primary. BTC is a benchmark, never a target symbol. HYPE is excluded from
any headline period for which its synchronized history is incomplete.

## Stage A — bounded causal screen

- Universe: CORE-10 first. CORE-20 is allowed only for a cell already net-positive on CORE-10.
- Primary cells: candidates 1–4 long and short; candidate 1 frozen baseline and cost-only ablation on each side; candidate
  5 short only: 13 primary/comparator cells total.
- Decisions use closed bars and fill no earlier than the next executable price.
- Costs include fees, spread/slippage, funding and exchange feasibility.
- Chronology: train `2024-10-14..2024-12-30`; seven non-overlapping forward slices through `2026-09-28`. Parameters are
  frozen after train and never fitted per fold. `SplitPlan` stores the non-overlapping train/forward slices; it must not be
  mislabelled as overlapping expanding estimation.
- Diagnostics only: RSI2 5/10/15; squeeze percentile .15/.20/.25; residual entry z 1.5/2/2.5; cost multiple 3/4/5. The
  primary and its immediate neighbours must retain sign; diagnostics cannot replace the primary.

Per-side screen gate:

- at least 60 episodes and at least four of seven positive forward slices;
- combined forward mean net R > 0 and PF > 1.05;
- positive pre-cost gross edge and no symbol above 35% of absolute contribution;
- for candidates 1–4, at least five closed trades/week across CORE-10;
- failure parks that family/side; no post-result rescue or opposite-side pooling.

## Stage B — execution-grade screen

Only Stage-A survivors enter the existing 1m-backed `scalp_exec_15m` path. Use the frozen Binance UM archive manifest,
actual signed funding, mainnet filters, ordered 1m path and CORE execution symbols. Test base, 1.5x cost, 2x fee/slippage
and 5x gap-slippage rows at 0 and 60 seconds latency.

Required per side: unresolved ambiguity <= 5%; base episode-clustered 95% lower confidence bound > 0; 1.5x-cost mean > 0;
and no latency sign flip. Compare against a matched symbol/side/holding-horizon random-entry control.

## Fresh holdout and promotion boundary

These hypotheses were designed after the existing 19 October holdout began, so they may not use that holdout for a new
claim. Preregister before `2026-11-02T00:00:00Z`; seal `2026-11-02T00:00:00Z..2027-11-01T00:00:00Z`; reveal once with at
least 30 episodes per side. Before that, a survivor may enter shadow and then a separately approved bounded PAPER cohort,
but it is not a mainnet candidate.

## Roadmap fit and deferrals

ROADMAP FIT: Wave D / `EDGE-01B`, frequent profitable strategy coverage after the completed negative EDGE-01 baselines.

DEFERRED: re-enabling failed fast strategies, QBank, unbounded parameter search, live/mainnet activation, additional venues,
TradFi/gold and UI work. The PR #208/#209 accounting and installation gate continues independently.
