# Profit and drawdown promotion policy

Owner ruling, 3 November 2026 (Africa/Cairo): a profitable strategy is not rejected solely because its historical
drawdown exceeds a fixed ceiling. Drawdown remains a first-class risk metric and controls classification, sizing and
canary limits. This ruling is prospective; it does not rewrite a frozen preregistration or convert a development result
into out-of-sample evidence.

## Separate decisions

1. **Code integration:** every distinct, causally implemented candidate may live in the repository as
   `research_only` and default-OFF, including rejected candidates. This preserves the complete tested-candidate ledger.
2. **Economic survival:** gross and net P&L after fees, adverse slippage and funding must be positive, mean net R must
   be positive, and PF must exceed the frozen economic floor. A strategy that loses after costs receives no PAPER risk.
3. **Evidence maturity:** trade count, breadth, regimes, chronological walk-forward/OOS, parameter sensitivity and
   independent reproduction determine whether a positive observation can advance. Thin observations remain in the
   evidence-building tier; their code is integrated but they are not activated.
4. **Risk classification:** max drawdown no longer acts as a binary economic rejection. Report it beside net R, mean R,
   PF, trade count, win rate and regime/breadth coverage.

## Drawdown labels and initial PAPER scaling

| Development max DD | Label | Maximum initial risk multiplier |
|---:|---|---:|
| <=15R | standard | 1.00x |
| >15R to 30R | elevated | `15 / DD` |
| >30R to 60R | high | `15 / DD` |
| >60R | extreme | shadow only until stronger OOS evidence |

The multiplier is an upper bound, not a promise to trade. A canary also needs causal parity, realistic costs,
chronological OOS/walk-forward survival, independent evidence, exchange protection, isolated accounting and rollback.
PAPER activation is bounded and observable. Mainnet remains separately gated and requires explicit owner approval.

## Current reclassification

- `rotation_long_h2` (candidate 24): **elevated-DD extended validation**. +30.85R, mean +0.177R, PF 1.464,
  max DD 18.60R, 174 trades / 9 symbols. Initial PAPER cap after all remaining gates: 0.806x base risk.
- `volatility_acceleration_long_1h` (candidate 41): **high-DD extended validation**. +24.915752R after costs,
  mean +0.033853R, PF 1.052772, max DD 43.682092R, 736 trades / 18 symbols, win 43.8859%.
  Initial PAPER cap after all remaining gates: 0.343x base risk.

Their original Stage-A documents remain immutable evidence of the gates frozen at the time. This policy changes the
next action, not the historical result. Both now proceed to independently reproduced chronological validation; neither
is yet PAPER-active. Positive but thin candidates remain evidence-building. Negative-after-cost candidates remain OFF.

ROADMAP FIT: continuous strategy conveyor / cost-aware validation / isolated PAPER preparation.

DEFERRED: any mainnet activation, hindsight parameter rescue, opening sealed holdouts before the registered validation
plan, or allocating capital to a negative-after-cost candidate.
