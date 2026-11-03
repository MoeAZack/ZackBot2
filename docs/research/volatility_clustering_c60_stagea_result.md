# Candidate 60 / volatility-clustering downside continuation — Stage-A result

Verdict: **REJECTED IN DEVELOPMENT / BLOCK**. Research-only and default-OFF; no rescue, folds, sealed holdout,
PAPER, runtime, master or PR #277 action.

- Frozen head: `fb58655e573e7a2827737ddeff268c14bfac1aad` (tree `c72fda91b00d989424faa5e3637c0309520bc445`).
- Frozen preregistration SHA-256: `1a217213528f861609b5e2adc945521115b76f117761007f54dbcc18d4adc2e1`.
- Duplicate/correlation proof: the complete Candidate 1–59 ledger plus tree, refs/tags/history, preregistrations,
  results and tools contained no squared-return serial-dependence signal. Signed-return autocorrelation, volatility
  acceleration, kurtosis and semivariance are related disclosures but use materially different information variables.
- Artifact: `C:\Dev\ZackBot_cowork_data\vol_clustering_c60\vol-clustering-short-dev.json`.
- Canonical packet SHA-256: `cdb2dfda12129aa7d23f7324c4bb79e73d5946923451d5464a3793a09c5a8ca5`.
- Artifact file SHA-256: `f32e42378ac3430c8d1402f2756622d5c03975a2015640901a07092d48fe90f7`.
- 722 trades / 18 symbols; regimes 361 above / 361 below BTC 4h EMA200.
- Pre-cost gross -$61.298291; fees $100.749093; funding -$2.538888; net -$159.508496 = -31.180156R.
- Mean -0.043186R; PF 0.844230; max DD 47.397577R; win rate 29.3629%.
- PASS sample, breadth and both regime counts. FAIL pre-cost gross, mean net R and PF. Drawdown is **extreme /
  shadow-only** under the prospective policy and cannot rescue failed economics.
- Multiplicity ledger is now 60 numbered candidates; future familywise alpha is
  `0.05 / 60 = 0.0008333333333333334`.

This append-only result binds the frozen commit and clean tree. Isolated PAPER eligibility remains **BLOCK**.
