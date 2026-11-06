# RANK-BREADTH-REVERSAL-4H-SHORT-CAUSAL-V2

**Status: FROZEN — signed by Codex, 6 November 2026 11:50 Cairo.**

The JSON twin is authoritative. This preregistration gives the unchanged short cell of
`sig_rank_breadth_reversal_4h` one new causal identity. It is research-only, default-OFF and PAPER/testnet-only. It
grants no runtime or mainnet authority. No market, archive or holdout data may be read before this contract is merged.

## Prior result is contaminated context

The earlier development result — **76 closes, +7.33R, PF 1.239, 7.96R maximum drawdown** — remains rejected because
the BTC-below regime had only nine closes. Those numbers explain why this new trial was selected; they are not fresh
evidence and satisfy none of its gates. They cannot seed tuning, choose a fold or be pooled with the new run. The old
trial and this identity both remain in the complete multiplicity ledger, with raw and Holm-adjusted diagnostics.

## Frozen signal and evidence

The strategy remains short-only on 4h closed candles. At each decision candle, membership comes from the
checksum-bound weekly point-in-time CORE-20 snapshot available then. `rank7` ranks causal 42-bar returns only among
eligible members with both closes; `breadth7` is the positive-return fraction over that same set. A short entry requires
at least eight ranked members, the top `ceil(20% × n)` rank, breadth at least 0.65, RSI2 crossing below 70 on a red
candle, volume at least 80% of the prior-42-volume median and the unchanged 5× cost-distance screen. The signal exit is
the source rule: rank falls to the lower half or breadth drops below 0.45. All parameters and management are unchanged.

The exact authority is the committed `pit-top40-qv30d-v4.json` artifact (byte SHA-256 `d7000458…3229`, canonical
digest `485c7522…d093`), bound to manifest digest `54912d9d…f1e7`, classification digest `48a9d32c…6845` and the
`crypto` book. CORE-20 is literally the first 20 ordered crypto members of the latest Monday-00:00-UTC row available at
the decision close. The artifact order is exact 30-day quote-volume descending with symbol-ascending ties; this order
also decides simultaneous admissions. Before any market row is opened, the runner verifies the committed universe,
manifest and classes byte hashes/digests, schema, 348 ordered weeks and every ordered member/qv row. Any mismatch or a
week with fewer than 20 crypto members makes the candidate incomplete; there is no static/current/union fallback.

Entries and signal/time exits fill at the next tradable open. The 18-bar cap therefore exits at `e+18`, never at a
same-candle close or synthetic final mark. Folds use a conservative 200-bar local warm-up for the causal BTC EMA200
regime report and an 18-bar end purge: the first eligible decision is index 199, first entry 200 and last entry `N-19`.
Every episode closes inside its fold. Missing membership, bars, next opens,
exchange rules, marks or funding makes the whole candidate incomplete.

Stops, TP1, breakeven and the full target use the frozen `STRATEGY_CAUSAL_FBL_BT01_V1` event order. The entry bar
participates; a gap through the stop fills at the open, and a stop present in the candle beats a favourable level.
Otherwise levels fire in path order. TP1 closes a step-floored half at 1R; breakeven can tighten only after the completed
favourable leg; the remaining quantity targets 2R. DCA, pyramiding and same-leg retroactive management are forbidden.

The source `reentry_lock=True` is binding. A signal/time exit is associated with the decision candle that scheduled its
next-open fill; an intrabar exit is associated with its fill candle. The same symbol/side cannot take an entry decision
from that associated candle, while the following completed candle is eligible. Times and reasons are persisted as UTC
candle-open milliseconds; malformed state makes the candidate incomplete rather than unlocked.

Exit ordering is explicit. Existing positions complete the decision candle's full intrabar path before its close
decision is computed. At the following open, due signal/time exits execute before any new entry or fill-bar intrabar
event; only afterward do re-entry checks and CORE-20-ordered admissions run, followed by exact-time funding and the fill
bar path. A due next-open exit therefore wins over classifying the same open as a gap stop.

The only evidence windows are the three unopened folds: 1 April–30 September 2025, 1 October 2025–31 March 2026 and
1 April–28 September 2026, all end-exclusive. Nothing at or after **28 September 2026 03:00 Cairo (00:00 UTC)** may be
read. The already viewed `[2026-09-28, 2026-11-05)` holdout remains sealed from this trial.

## Frozen economics and account order

The candidate uses an isolated 500 USDT marked-equity book. Each sequential next-open attempt risks 1% of current
marked equity, with at most four open positions and 10× gross marked notional. Existing positions use the last verified
1h mark available at the open; same-open admissions use their unslipped reference open. Simultaneous entries follow the
point-in-time CORE-20 order. Earlier fills and costs reduce the capacity visible to later requests. Equity compounds and no
caller may override sizing or event order.

At a bar open, funding strictly before the open settles first; marks and a new Cairo-day baseline are then frozen; due
signal/time exits run before entries. The 8% Africa/Cairo daily loss latch is checked before the first entry and after
every cashflow. Exact-time funding settles after all same-open attempts, then intrabar protection runs. A latch blocks
only later entries in that Cairo day; exits and protection never stop.

Each fill pays a 5 bp fee and 2 bp adverse slippage, with point-in-time tick/quantity/minimum/maximum rules. Actual
Binance USD-M funding uses causal marks and entry-inclusive, exit-exclusive settlement. Price P&L, slippage, fees and
funding are each counted once. Report 1×, 1.5× and 2× variable-cost stress; credits never increase under stress.

## Verdict and diagnostic math

Each trade's immutable R denominator is its actual initial stop risk after the leverage cap and exchange-step floor:
`initial filled quantity × 1.5 × entry ATR`. Net R is the stable sum of fully closed trade R; mean R divides by the
closed count; PF divides positive trade R by the absolute negative sum. Symbol concentration divides the largest
absolute symbol net R by the sum of all absolute symbol net R. A zero denominator fails.

Promotion drawdown uses normalized cashflows, including entry costs, funding, partials and final realization. Exact-time
ties are summed atomically so symbol iteration cannot change the curve. Each fold starts at zero; the gate uses the
largest fold drawdown, never a curve carried between independent 500 USDT books. Causal marked-equity cash/percentage
drawdown is also mandatory disclosure.

The 1×, 1.5× and 2× rows are three full replays from 500 USDT, not post-hoc repricing: costs can change compounding,
sizing, floors, leverage, daily halts, occupancy and subsequent trades. The source's 5× distance screen always retains
its base `5 bp + 2 bp` constants so stress does not mutate the signal identity. Base gates use 1×; only the positive
1.5× gate reads the 1.5× replay; 2× is required disclosure.

Raw inference is a one-sided 100,000-draw sign-flip over UTC exit-day net-R clusters (PCG64 seed 20261106115043). The
ordinary 95% mean-R interval is a 10,000-draw exit-day cluster percentile bootstrap (seed 20261106115044). Holm uses the
three-entry frozen RBR family ledger (`cafb8b56…3448`, canonical digest `8f6613af…1ba5`); the two contaminated trials get
conservative p=1 because they have no preregistered raw p. Raw, bootstrap and Holm outputs are mandatory diagnostics but
never promotion gates.

## Promotion

Promotion requires positive aggregate net R and mean R, PF above 1.05, at least 120 closes across eight symbols, at
least two positive folds, and a median of at least four closes across **all UTC months intersecting the folds, including
zero-close months**. Maximum drawdown must be at most 15R. Each causal BTC-above/on and BTC-below EMA200 regime needs at
least 15 closes and mean R no worse than -0.05; no symbol may exceed 35% of absolute contribution. The candidate must
remain positive at 1.5× costs, pass causal parity and independent review, and publish every required report plus the full
candidate/multiplicity ledger. Missing evidence never passes.

ROADMAP FIT: next default-OFF causal diversifier trial on the continuous strategy conveyor.
DEFERRED: market reads until merge; viewed holdout; parameter rescue; PAPER activation; runtime and mainnet.
