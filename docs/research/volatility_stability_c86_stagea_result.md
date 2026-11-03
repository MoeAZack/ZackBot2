# Candidate 86 / Volatility-of-volatility stability — Stage-A result

Verdict: **REJECTED IN DEVELOPMENT / BLOCK**. Research-only and default-OFF; no rescue, folds, sealed holdout,
PAPER, runtime, master or open-PR action.

- Frozen head: `d79bc34022fa9de04bf31e6716f436d0f676f013` (tree `a4b3b5cf2f929af3e98542ead54204ef7f59a70a`).
- Frozen preregistration SHA-256: `cb6e68a404e70157354af30eabbee6d390e716ca66f5ba1ccb71b1d25d767392`.
- Duplicate/correlation proof: the complete Candidate 1–85 ledger plus tree, refs/tags/history, preregistrations,
  results and tools contained no volatility-of-volatility coefficient rule. C41 range acceleration and C60 squared-
  return serial dependence are related volatility disclosures but use materially different information variables.
- Artifact: `C:\Dev\ZackBot_cowork_data\volatility_stability_c86\volatility-stability-long-dev.json`.
- Canonical packet SHA-256: `5d3f74d5ff1955a1fb08bafac751ae175c84ec4d007a26d456c99ebb1fa77300`.
- Artifact file SHA-256: `1efd96a5eadc0fbfb8c34ac063de5c75df9c14781098cc30840d2ca977f7b6b9`.
- Funding-manifest digest: `54912d9d2bf6e6fc45bc75c0553f0bd867e877972beeb3452785937379baf1e7`.
- 1,222 trades / 18 symbols; regimes 769 above / 453 below BTC 4h EMA200.
- Pre-cost gross -$69.264890; fees $202.013674; funding $18.646114; dollar net -$289.924678;
  risk net -68.647633R.
- Mean -0.056176R; PF 0.874397; max DD 113.277286R; win rate 30.9329%.
- PASS sample, breadth and both regime-count gates. FAIL pre-cost gross, mean net R, PF and max-DD <=15R gates.
- Multiplicity ledger is now 86 numbered candidates; future familywise alpha is `0.05 / 86`.

The canonical packet hash was independently recomputed and matched. The frozen volatility-stability hypothesis is
rejected as specified; no tuning or alternate horizon was attempted. Isolated PAPER eligibility remains **BLOCK**.
