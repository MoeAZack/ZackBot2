# Candidate 51 / realized-kurtosis tail risk — Stage-A result

Verdict: **REJECTED IN DEVELOPMENT / BLOCK**. Research-only and default-OFF; no rescue, folds, sealed holdout,
PAPER, runtime, master or PR #277 action.

- Frozen head: `6801610ceff66ddff70c1fa7f05455459bd77bd0`.
- Frozen preregistration SHA-256: `3827e457cc54d9f58a4193cf145474c9136c43511bef1dfbd525f6a0158f1e19`.
- Duplicate/correlation proof: the complete Candidate 1–50 ledger plus tree, refs/tags/history, preregistrations,
  results and tools contained no kurtosis/fourth-moment trial. Realized skew, jumps, variance acceleration and candle
  exhaustion differ; higher-moment/volatility correlation is the disclosed nearest exposure.
- Artifact: `C:\Dev\ZackBot_cowork_data\kurtosis_c51\realized-kurtosis-short-dev.json`.
- Canonical packet SHA-256: `91a090aa87e16ce9d8a5b0d3f033c1df1a1be10252f49a5c0da3713e76da4f73`.
- Artifact file SHA-256: `15d956d3758f93dcc17b33ca62a420561aec4b3fd9bf307258f849c61bc8e914`.
- 299 trades / 18 symbols from 621 raw signals; regimes 217 above / 82 below BTC 4h EMA200.
- Pre-cost gross -$69.434600; fees $35.474413; funding -$4.750572; net -$100.158440 = -18.712960R.
- Mean -0.062585R; PF 0.829008; max DD 34.137450R; win rate 41.8060%.
- PASS sample, breadth and both regime counts. FAIL pre-cost gross, mean net R and PF. Drawdown is **high** under the
  prospective policy and cannot rescue failed economics.
- Multiplicity ledger is now 51 numbered candidates; future familywise alpha is `0.05 / 51 = 0.000980392156862745`.

This append-only result binds the frozen commit and clean tree. Isolated PAPER eligibility remains **BLOCK**.
