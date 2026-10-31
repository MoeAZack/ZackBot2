# CARRY-01 — BTC/ETH spot–perpetual cash-and-carry

Status: **IMPLEMENTED FOR RESEARCH / DEFAULT OFF / NOT PAPER-ACTIVATED**
Frozen by Codex: 31 October 2026 (Cairo)

## Hypothesis

Long spot and short the same quantity of its perpetual contract. Positive settled funding and favourable basis may
produce a low-directional-beta return, but only when the after-cost return beats cash. The source video is a hypothesis,
not evidence; its reported 9.5% CAGR and 1.2% drawdown are not imported into ZackBot's ledger.

Primary instruments are BTC and ETH, evaluated separately for USDT and USDC quote pairs. No rotating altcoin version is
admitted: the source itself reports that turnover and fees destroyed that variant.

## Frozen portfolio and execution assumptions

- 10,000 quote-currency equity; 75% spot, 25% futures collateral.
- Long spot and short the same base-asset quantity. Target futures leverage 3x.
- Rebalance both legs only when estimated futures leverage leaves 2x–5x; every close and reopen pays both-leg costs.
- Conservative defaults: spot taker 10 bp, perpetual taker 5 bp, and 5 bp slippage on each fill.
- Funding is credited only on an explicit settled funding row at that row's timestamp. It is never forward-filled.
- Public, keyless Binance production market-data endpoints are allowed for observation. There are no authenticated
  endpoints, API keys, order methods, exchange-account reads, or writes in `tools/research/carry01.py`.

## Shadow scanner gate

All checks must pass independently for each pair:

1. mark age is -10–120 seconds (bounded exchange/local clock skew) and at least 240 unique, strictly ordered events
   exist in the trailing 90 days;
2. current funding and trailing 7d, 30d and 90d settled funding APR are positive;
3. absolute spot/perpetual basis <= 50 bp;
4. the 90d funding APR, scaled to the 75% matched notional, minus a four-fill round-trip cost amortised over 90 days,
   exceeds a 4% cash benchmark plus a 2% safety margin.

This is an admission screen, not a profitability result. A current funding print is never annualised and presented as
evidence by itself.

## Historical evidence contract

Input is immutable JSONL with strictly increasing, unique `timestamp_ms`, `spot_price`, `perp_price`, and optional
`funding_rate`. Mark rows must be frequent enough to observe the leverage band; a sparse series cannot establish gap or
liquidation safety. Funding rows must be the point-in-time
settled series. The run records the exact input SHA-256.

Before promotion, the dataset and report must add:

- point-in-time spot and perpetual prices, funding schedule changes, exchange rules and delistings;
- train, walk-forward and untouched holdout partitions, purged at boundaries;
- missing-event/gap refusal, liquidation and margin stress, rate reversal, basis widening and exchange-failure scenarios;
- USDT versus USDC results, cash/T-bill benchmark, funding/price/cost attribution, trade/rebalance count, return, Sharpe,
  maximum drawdown, recovery time, capacity and concentration;
- fee/slippage stresses at 1.5x and 2x, plus funding zero and funding sign-reversal rows;
- a complete candidate ledger including failures to control selection and multiple-testing bias.

Liquidated paths have no return or benchmark verdict, and windows under 30 days are not annualised. The replay uses a
0.5% maintenance-margin proxy; venue-tier liquidation and intrabar gaps remain required pre-promotion stresses.

No holdout is opened or tuned in this slice. No result can enable PAPER without independent evidence, and PAPER cannot
enable mainnet.

## Commands

```text
python tools/research/carry01.py scan
python tools/research/carry01.py replay <frozen-input.jsonl>
```

ROADMAP FIT: continuous strategy conveyor; isolated market-neutral candidate research and USDT/USDC comparison.
DEFERRED: data acquisition/freeze, independent reproduction, PAPER canary, runtime/API/UI wiring, any mainnet action.
