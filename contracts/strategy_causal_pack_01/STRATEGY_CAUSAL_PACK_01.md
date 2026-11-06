# STRATEGY-CAUSAL-PACK-01

**Status: FROZEN — signed by Codex, 6 November 2026 09:55 Cairo.**

The JSON twin is authoritative. This pack gives three previously positive-after-cost but under-sampled short ideas one
new, causal identity each. Selection is disclosed and is not evidence. All old candidate verdicts remain rejected.

## Frozen candidates

| New identity | Source cell | Frame | Causal maximum hold |
|---|---|---:|---:|
| `pullback_rsi_short_h1_causal_v2` | `pullback_rsi_short_h1` | 4h | 60 bars |
| `exp_mom_short_c27_causal_v2` | `exp_mom_short_c27` | 4h | 180 bars |
| `volume_pressure_inventory_short_1h_causal_v2` | `volume_pressure_inventory_1h:short` | 1h | 24 bars |

The exp-momentum 180-bar cap is inherited from the pre-existing canonical short-momentum preregistration, not selected
from Candidate 27 outcomes. No grid, tuning, side pooling, universe change or fold selection is allowed.

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
