# Candidate 80 / Positive-jump excitation preregistration

Status: **FROZEN / NOT EVALUATED / RESEARCH ONLY**; frozen 3 November 2026 Cairo before results.  
Key: `positive_jump_excitation_long_1h`; 1h long only.

## Duplicate and correlation identity

Before freezing, the complete Candidate 1–79 append-only ledger/risk register, current tree, all local/remote refs and
tags, Git history, preregistrations, results and research tools were searched for self-exciting/Hawkes jump-arrival and
decayed jump-event-intensity rules. No prior candidate uses this statistic. C60 measures autocorrelation of squared
returns, C66 uses realized-variance jump share, and transient-jump candidates react to individual dislocations. C80
instead measures the direction-specific arrival intensity of repeated positive jumps. Trend correlation is disclosed;
the family identity and entry statistic are distinct. One long cell; no grid. This is a deterministic exponentially
decayed event rate, not an estimated Hawkes model.

## Frozen causal rule and costs

At each completed 1h decision bar, define an event when log return is at least `0.75 * ATR / close`. Compute the event
rate using pandas EWM with 6h half-life, `adjust=False`, and completed bars through `t`. Enter long at the next open only
when intensity is >=0.20, the decision-hour log return and completed 24h return are positive, and close is above EMA20.
Exit when intensity is <=0.10 or the 24h return is non-positive, after 24 completed bars, or earlier at the 2 ATR stop/2R
target. A 2 ATR stop must be >=5x modeled 0.14% taker-plus-slippage round-trip cost. Costs are 5 bp taker +2 bp adverse
slippage per fill plus checksum-verified actual signed funding.

CORE-20 development `2024-10-14T00:00:00Z..2025-04-01T00:00:00Z`. Stage-A PASS requires pre-cost gross>0, mean net
R>0, PF>1.05, >=100 closes, >=8 symbols, >=20 closes in each BTC regime, and max drawdown <=15R. Candidate 80 increases
the numbered family to 80; any later promotion must use family-wise alpha <=`0.05/80` and chronological OOS. PAPER
eligibility remains BLOCK regardless of the development result. No rescue, folds, holdout, PAPER or runtime action is
authorized; the post-2026-09-28 holdout stays sealed.

ROADMAP FIT: materially distinct default-OFF event-arrival-excitation family.  
DEFERRED: short cells, other event definitions/decays/thresholds, fitted Hawkes models, folds, holdout and activation.
