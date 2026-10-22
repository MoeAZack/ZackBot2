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

## EDGE-01A preregistered numeric gate v1

These values are frozen before the first EDGE-01A baseline result. A result must pass every applicable hard gate; a strong
score elsewhere cannot compensate. The gate may be made stricter after results are known, but not relaxed for this trial.

| Dimension | Development and anchored walk-forward gate | Untouched holdout / PAPER transfer gate |
|---|---|---|
| Activity | At least 300 closed trades total, 100 per enabled side, and median at least 15 trades/week across CORE-10 | At least 100 complete closes over at least 14 Cairo days before a PAPER promotion verdict |
| Normal costs | PF >= 1.25 and net expectancy >= +0.08R/trade | PF >= 1.15 and net expectancy >= +0.05R/trade |
| Cost stress | PF >= 1.10 and positive expectancy at 1.5x the frozen fee/spread/slippage profile | Positive measured expectancy after actual fees, funding and fill adjustment |
| Statistical margin | Trade-block bootstrap 95% lower bound of mean net R must be above zero | Direction of measured result must agree with holdout; no substitution of booked P&L |
| Time stability | At least 70% of anchored test windows and 6 of 8 available quarters positive | No accounting-incomplete period used for a verdict |
| Concentration | Removing the best symbol remains profitable; no symbol supplies over 35% of positive net R | Report symbol/regime shares; a single-symbol rescue is a failure |
| Tail/risk | Max drawdown <= 12R; expected shortfall and every loss beyond -1.5R explained and stress-tested | No unprotected lot, risk-budget breach or unresolved ownership/fill mismatch |
| Parity | Exact runtime decisions; zero unexplained live/replay decision mismatches | 100% actual-cost completeness for ranked closes |

Long and short are separate promotion cells. A side that fails is disabled even when the combined strategy passes. If the
activity floor fails, the candidate may be economically sound but does not meet the owner's frequent-trading objective and
is not promoted into this catalog slot.

The finite repair matrix is deliberately small:

1. frozen baseline rules and exits;
2. 5x expected-edge-to-round-trip-cost admission gate;
3. item 2 plus spread/liquidity veto;
4. item 2 plus the strategy-specific regime veto (trend alignment for `exp_scalp`, confirmed range for `range_scalp`);
5. best still-eligible entry rule with whole-position fixed target/time cap;
6. the same entry rule with early bank plus protected runner.

No Cartesian parameter sweep is allowed. A repair that fails a development hard gate stops; it does not consume the
holdout. The selected successor receives a new version id before shadow or PAPER use.

## SCALP-EXEC-15M-01 integration rulings

These rulings are frozen before the execution-grade cells run.

- The forward-readiness baseline is the exact protected master `9523a65c8fdb77c7f9df143eede56f485b4fc076` and its
  engine/strategy blobs. Historical PAPER rows from older builds remain separate observed evidence; the new replay must
  not claim to reproduce an older build. It reports how often base and stressed adverse entry costs would trigger the
  current 10% measured-fill trim/unwind boundary.
- The as-run sleeve input is
  `EDGE01_FROZEN_BASELINE_CONFIG_2026-10-23.json`, SHA-256
  `3027cb8a048fcaae89154fb842fdadc5ccdf66e68f9b50d13afb7de0919320de`. The 8-hour value is the PAPER breadth-rotation
  group period, not a session filter. The two `enabled: true` values record the state before the 02:25 Cairo entry
  freeze; no other sleeve field changed during that freeze.
- Primary development coverage is 14 October 2024 through 28 September 2026 UTC on the frozen CORE-10 universe artifact.
  Manifest `54912d9d2bf6e6fc45bc75c0553f0bd867e877972beeb3452785937379baf1e7` contains full-window 1m, funding and 1h
  mark-price coverage for nine symbols. `HYPEUSDT` starts only in May 2025, so the strict full-window headline is
  CORE-10-ELIGIBLE-9. Every packet reports coverage and exclusions. A separate available-period HYPE sensitivity is
  reported but cannot enter the headline gate or choose a winner.
- Fifteen-minute decisions use only completed candles. Stops, targets and exits computed at completed bar `t` become
  effective at the first minute of bar `t+1`; they are never moved from bar `t` extremes and then tested inside bar `t`.
  The entry minute is always walked, including gap and same-minute stop/target cases.
- Signal-causality, prior-bar stop activation, entry-minute stop, gap-through-stop and same-minute-stop-first mutations
  are required. Symbols excluded for incomplete data also receive an available-period sensitivity so survivor selection
  is visible.
- Repriced zero-cost decisions are diagnostic only. The adapter must also run the account path with actual base-row costs
  and the frozen 1.5x cost stress applied at fill time, then report trade-set, sizing, occupancy and per-trade-R divergence.
  A material divergence makes the repriced rows non-comparable and blocks promotion.
- Latency is not tunable: zero seconds is the primary row and 60 seconds is the single preregistered stress row. Every
  strategy x side x management x cost x latency result appends to the immutable trial ledger. The earlier four 4h screens
  and six simplified baseline packets count in the family-wide multiplicity record.
- No measured EXEC-CAL profile is frozen yet. Use `slip-cal-v1`, mark it `SLIP-C-UNFITTED`, and keep the packets
  non-promotable for slippage-transfer claims. Do not compare that modeled slippage with observed PAPER slippage as if it
  were calibrated.

ROADMAP FIT: Binance strategy evidence, truthful PAPER forward validation and numeric mainnet-candidate gates.
DEFERRED: mainnet activation, new venues, unrestricted parameter mining, ML order authority and cosmetic UI work.
