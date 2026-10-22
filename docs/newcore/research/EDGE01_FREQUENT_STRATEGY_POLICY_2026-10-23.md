# EDGE-01 frequent-strategy evidence decision — 23 October 2026, Cairo

## Owner objective

Seek frequent trading while maintaining real after-cost profitability. Risk is allowed inside explicit loss and drawdown
limits. A failing or barely break-even strategy is not useful and cannot be carried toward mainnet to increase activity.
Continue scouting when existing candidates fail; preserve every failed trial so the same idea is not rediscovered or
silently tuned against known outcomes.

## Trigger evidence

The append-only retained PAPER ledger was deduplicated by trade id at 01:46 Cairo on 23 October 2026.

| Frozen baseline | Closed trades | Wins | Booked net | Best available adjusted net | Booked R | Decision |
|---|---:|---:|---:|---:|---:|---|
| `exp_scalp.v0` | 43 | 24 | -$235.94 | -$233.99 | -5.56R | PARK / stop new entries |
| `range_scalp.v0` (`SC-RANGE`) | 30 | 11 | -$90.17 | -$91.73 | -4.12R | PARK / stop new entries |

The adjusted all-time figures are reconstructions because 31 `exp_scalp` and 27 `SC-RANGE` rows predate fill-price
adjustment recording. The audit-grade recent subset is also negative: `exp_scalp` 11 trades, -$22.75 / -0.55R;
`SC-RANGE` 3 trades, -$69.29 / -3.44R. This limitation strengthens the refusal to promote; it does not justify replacing
missing execution evidence with optimistic estimates.

Four separate 4h development candidates were already screened on CORE-10-ELIGIBLE-9 from 14 October 2024 through
28 September 2026. All were correctly parked: liquidity sweep/FVG +1.40R (PF 1.03), volatility breakout -13.80R
(PF 0.90), trend pullback -20.92R (PF 0.84), and range-extreme reversion +2.08R (PF 1.02). The marginal positives failed
cost and/or quarter-consistency gates. They are research evidence, not replacements ready for PAPER.

## Immediate queue

1. Disable new `exp_scalp` and `SC-RANGE` PAPER entries; keep every existing lot managed and exchange-protected.
2. Seal their exact code, settings, ledger rows and completeness caveats as failed baseline artifacts.
3. `EDGE-01A`: run the exact two versions on execution-grade sub-15m data and the shared live/replay policy.
4. Register a small repair matrix before results: cost/edge gate; spread/liquidity veto; volatility/regime veto; turnover
   control; fixed versus early-bank/runner/time-cap exits. Long and short are independent cells.
5. Run development, anchored walk-forward, sensitivity/concentration and stressed-cost checks. Open the untouched holdout
   once for a candidate that already passes every development gate.
6. Advance only qualified versions to shadow comparison and then a bounded PAPER cohort. Forward evidence measures
   execution transfer; it does not rescue failed historical economics.
7. In parallel, `EDGE-01B` continues bounded scouting of short-horizon range, failed-breakout, momentum-burst, pullback
   and breakout hypotheses. One preregistered hypothesis/trial budget at a time; failed candidates are parked promptly.

## Required reporting

Every cohort and backtest board reports frequency together with net expectancy: trades/week, eligible versus rejected
signals, exposure/occupancy, net R/trade, PF, costs/R, drawdown, worst trade, long/short and regime splits, concentration,
walk-forward stability, holdout status and accounting completeness. Gross profit, win rate or trade count alone can never
produce a green verdict.

ROADMAP FIT: Binance strategy evidence, truthful PAPER forward validation and numeric mainnet-candidate gates.
DEFERRED: mainnet activation, new venues, unrestricted parameter mining, ML order authority and cosmetic UI work.
