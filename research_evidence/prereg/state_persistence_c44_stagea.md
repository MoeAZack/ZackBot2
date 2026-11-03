# Candidate 44 / empirical bull-state persistence preregistration

Status: **FROZEN / NOT EVALUATED / RESEARCH ONLY**; frozen 3 November 2026 Cairo before results.  
Key: `state_persistence_long_1h`; 1h long only.

## Queue and duplicate identity

This is the next distinct preregisterable hypothesis in `SCOUT-REGIME-01`: a simple empirical transition diagnostic,
not an HMM. Before freezing, the tested-candidate ledger, current tree, all local/remote refs and tags, Git history,
preregistrations and results were searched for Markov trading, transition matrices/probabilities, bull-to-bull state
persistence and conditional state continuation. Hits were the scouting discussion and unrelated rank transitions; no
tested strategy or packet matched. RAS-1H already tested efficiency/volatility mechanism switching and is excluded.
C40 uses lag-1 linear return autocorrelation; C42 uses binary sign entropy. Neither estimates conditional persistence
of a frozen three-state normalized-return process. No C41-C43 threshold, signal or management rule is reused as rescue.

## Frozen causal rule and costs

At each completed bar classify a three-state process from the six-hour close change divided by current ATR14: bull at
>=+1 ATR, bear at <=-1 ATR, neutral otherwise. Over the 120 completed transitions ending one bar before the decision,
estimate `P(next bull | current bull)` as prior bull-to-bull count / prior bull-origin count; require >=20 bull origins.
Enter next open when the decision state is bull, the estimate is >=0.60, close >EMA50, quote volume >= its prior 48-bar
median, and a 2 ATR stop is >=5x modeled round-trip cost. Exit when state ceases to be bull or persistence falls below
0.50, at 2 ATR stop, 2R target or 12 bars. Costs: 5 bp taker +2 bp adverse slippage per fill plus actual verified signed
funding. This conditional frequency is a descriptive filter, not a probability of profit or proof of the Markov property.

CORE-20 development `2024-10-14T00:00:00Z..2025-04-01T00:00:00Z`. Reject if pre-cost gross<=0, mean net R<=0,
PF<=1.05, <100 closes, <8 symbols, DD>15R, or either BTC regime <20. Inspect all gates. This task ends after the single
development evaluation regardless of result: no rescue, folds, holdout, PAPER or runtime action.

Multiplicity: one frozen cell. Default-OFF/research-only. Survivorship-biased current-universe evidence.

ROADMAP FIT: simple transition diagnostic from the existing ranked queue after excluding the spent RAS mechanism.  
DEFERRED: alternative states/windows, short mirror, HMM, folds, holdout, Stage B and all activation.
