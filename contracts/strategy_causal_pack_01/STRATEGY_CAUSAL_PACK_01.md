# STRATEGY-CAUSAL-PACK-01

**Status: FROZEN v3 — signed by Codex, 6 November 2026 13:28 Cairo.**

The JSON twin is authoritative. This pack gives three previously positive-after-cost but under-sampled short ideas one
new, causal identity each. Selection is disclosed and is not evidence. All old candidate verdicts remain rejected.

## Frozen candidates

| New identity | Source cell | Frame | Causal maximum hold |
|---|---|---:|---:|
| `pullback_rsi_short_h1_causal_v3` | `pullback_rsi_short_h1` | 4h | 60 bars |
| `exp_mom_short_c27_causal_v3` | `exp_mom_short_c27` | 4h | 180 bars |
| `volume_pressure_inventory_short_1h_causal_v3` | `volume_pressure_inventory_1h:short` | 1h | 24 bars |

The 1h VPI packet is explicitly `DATA_INCOMPLETE_PRE_RUN`: the frozen archive manifest contains no required 1h kline
archive for this universe. It therefore reads no candidate market rows and receives no economic verdict. It remains one
of the three registered packets and remains in the multiplicity ledger; missing data cannot erase a trial.

The exp-momentum 180-bar cap is inherited from the pre-existing canonical short-momentum preregistration, not selected
from Candidate 27 outcomes. The earlier v2 identities are retired before evaluation because their 11 October filter
snapshot was future-dated relative to the folds. The v3 universe amendment below is metadata-only and was frozen before
any candidate market row was read. No grid, tuning, side pooling, later universe change or fold selection is allowed.

## Causal exchange-rule universe

Each fold freezes the latest checksum-bound Binance USD-M exchangeInfo response observed strictly before that fold
starts: the 13 December 2024 response for wf1 and wf2, and the 17 February 2026 response for wf3. The responses are
raw Binance API payloads preserved by the Internet Archive and pinned by `exchange_filter_authority.v2.json`.
For every PIT week, walk the existing crypto volume ranking in order and take the first 20 symbols that the fold
snapshot records as TRADING USDT COIN perpetuals with complete positive price, market-quantity and minimum-notional
filters. The hash includes the PIT cell already active at fold start and every later Monday before fold end. Fewer than 20 makes the candidate incomplete before any market row is read. No later snapshot, invented rule,
current-survivor screen or outcome-driven deletion is allowed. The fold snapshot stays fixed for the whole fold; current
PAPER/testnet admission must independently pass current exchangeInfo parity.

At every completed decision bar `d`, effective membership is the ordered PIT CORE-20 intersected with symbols whose
checksum-verified causal closes at `d` and `d-42` are both finite and available by `d`. Cross-sectional ranks, breadth,
and their denominator use only that effective set, without imputation, forward fill, synthetic values, or replacement
survivors. The original PIT order is preserved after intersection and alone controls simultaneous-entry priority.
Later archive coverage, listing state, or delisting knowledge may never suppress an entry or rewrite that decision.

A missing row for a symbol outside effective membership, or for a flat/non-signalling symbol when that row is never
needed by an entered episode, does not invalidate the candidate. Once a signal is admitted, however, every bar, next
open, open-position mark, applicable exchange filter, and funding settlement intersecting the actual exposure must be
causal, checksum-bound, and complete through exit. A missing next open, open-position bar or mark, or intersecting
funding settlement makes the whole candidate incomplete. No last-mark, synthetic, zero, or delisting close is allowed;
a terminal/delist exit requires checksum-bound point-in-time authority known before its exit decision, otherwise the
candidate is incomplete. This sparse rule cannot delete an entered episode or create a reduced-sample pass.

## Causal evidence

Entries and every signal exit fill at the next tradable open after the completed decision candle. With fold-local bars
`0..N-1`, a decision at `d` enters at `e=d+1`; the time cap exits at the open of `e+H`. An entry is eligible only when
`e >= L` and `e+H < N`, so the last eligible entry is `N-H-1`. Every actual exit fill must have index below `N`.
Stops and targets use the JSON-encoded `STRATEGY_CAUSAL_FBL_BT01_V1` rule: green candles walk O-L-H-C, red candles
O-H-L-C, and dojis take the side-worst path. The entry bar participates. A gap through a stop fills at the open first;
if the bar contains the opening stop and a favourable level, the stop wins. Otherwise levels fire in path order. Partial,
breakeven and trail changes occur once and only affect later path legs. All three candidates forbid DCA and pyramiding.
A missing next open or unresolved bar makes the whole candidate incomplete, never a last-mark or synthetic close and
never a reduced passing sample.

Management uses the adverse executable entry and the decision candle's last causal ATR. Initial `R` is `stop_atr × ATR`
and the stop is `entry − side × R`. Targets are `entry + side × tp_r × R`; TP1 uses the same formula with `tp1_r`
and floors its requested fraction to the venue step. A zero partial sends nothing and is not marked complete; TP1 does
not auto-close dust. Breakeven tightens to entry only after its favourable leg. A chandelier trail uses the best price
minus/plus `trail_atr ×` the prior closed bar's engine ATR, tightening only after a completed leg and never loosening.
Trigger levels remain exact formula values; executable fills alone receive the frozen adverse tick rounding.

The only evidence windows are the three untouched folds: 1 April–30 September 2025, 1 October 2025–31 March 2026,
and 1 April–28 September 2026, all end-exclusive. Every candidate observes its declared warm-up and holding-horizon
purge. Nothing at or after 28 September 2026 may be read. The already viewed holdout is permanently excluded.

Each candidate runs in its own frozen causal marked-equity book: 500 USDT starting equity, 1% of marked equity risked
at each next-open entry attempt, at most four open positions, and gross marked notional capped at 10x marked equity.
This is an identity-defining deviation from the legacy source's realized-cash sizing. Existing positions use the last
verified 1h mark close available at the open; same-open admissions use their unslipped reference open. Settled cash
includes all prior realized PnL, fees, slippage, funding, and partials exactly once. Simultaneous entries follow the
checksum-bound causal filter-eligible CORE-20 order, so later symbols see earlier fills and entry costs.

At each bar open, funding strictly before the open is settled, marks and any new Cairo-day baseline are frozen, and due
signal/time exits execute before entry terms are calculated. The 8% Cairo-day threshold is checked before the first
entry and after every cashflow; once latched it blocks only later entries that day. Same-timestamp funding is settled
after all same-open entries (so new positions receive it), then intrabar protection runs. Exits and protection are never
blocked. Equity compounds inside each isolated candidate book, and callers cannot override any value or ordering.

Each fill pays 5 bp taker fee plus 2 bp adverse slippage, rounded against the strategy to the frozen exchange tick.
Actual checksum-verified Binance funding is included for settlements from entry inclusive to exit exclusive: positive
funding is a receipt for shorts and a debit for longs. Gross price P&L uses unslipped reference prices; adverse slippage
is the reference-to-executable cash difference subtracted exactly once, and fees use executable notional. Net P&L is
reference-price P&L plus signed funding, less fees and that one slippage charge.
At stressed costs, debits are multiplied but funding credits stay at 1×. Missing funding, marks, next opens, or required
reports makes the whole candidate incomplete. Raw and Holm-adjusted inference is reported only as a selection-bias
diagnostic; this economic-screen pack does not claim confirmatory error control or use inference as a promotion gate.

## Promotion

A candidate needs positive aggregate net and mean R, PF above 1.05, at least 200 closes over at least eight symbols,
at least two positive folds, at least eight median monthly closes, max drawdown at most 15R, acceptable regime and
symbol concentration, positive 1.5× cost stress, causal parity and independent review. Zero incomplete rows are allowed;
missing evidence never passes and may not be removed to create a smaller passing sample.

Qualifiers remain default-OFF. The first two or three independently accepted candidates enter isolated PAPER/testnet
slots with RISK-LOSS-01 and protected execution. This contract grants no mainnet authority.
