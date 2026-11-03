# Candidate 55 / wick-imbalance auction pressure — Stage-A result

Verdict: **REJECTED IN DEVELOPMENT / BLOCK**. Research-only and default-OFF; no rescue, folds, sealed holdout,
PAPER, runtime, master or PR #277 action.

- Frozen head: `8048ba5b701776c9ce9aa58c0779fb931e64274c`.
- Frozen preregistration SHA-256: `114ce86cc63e78d574fec6e543cb44d51dd6945a7d772ffc90d4e2e62d8b20a7`.
- Duplicate/correlation proof: the complete Candidate 1–54 ledger plus tree, refs/tags/history, preregistrations,
  results and tools contained no multi-hour normalized wick-pressure rule. Single-bar liquidity sweeps, outside bars and
  close-location breakouts are the disclosed nearest OHLC exposures but use different events and mechanisms.
- Artifact: `C:\Dev\ZackBot_cowork_data\wick_pressure_c55\wick-pressure-long-dev.json`.
- Canonical packet SHA-256: `cb5ad65d5e299e0565a7e909309145cdc893a12ca153d47ebf95617de7d806b5`.
- Artifact file SHA-256: `86751bbd7b135345b7bd81a04b08be314be4235ef82f9d1010e50c17f546ce6c`.
- 296 trades / 18 symbols from 787 raw signals; regimes 187 above / 109 below BTC 4h EMA200.
- Pre-cost gross -$107.699272; fees $66.737089; funding $3.786296; net -$178.222658 = -40.944296R.
- Mean -0.138325R; PF 0.719543; max DD 49.440131R; win rate 39.8649%.
- PASS sample, breadth and both regime counts. FAIL pre-cost gross, mean net R and PF. Drawdown is **extreme / shadow-
  only** and cannot rescue failed economics.
- Multiplicity ledger is now 55 numbered candidates; future familywise alpha is `0.05 / 55 = 0.0009090909090909091`.

This append-only result binds the frozen commit and clean tree. Isolated PAPER eligibility remains **BLOCK**.
