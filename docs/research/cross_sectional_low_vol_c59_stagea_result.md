# Candidate 59 / cross-sectional low-volatility factor — Stage-A result

Verdict: **REJECTED IN DEVELOPMENT / BLOCK**. Research-only and default-OFF; no rescue, folds, sealed holdout,
PAPER, runtime, master or PR #277 action.

- Frozen head: `eb86fdefd05da77ed497f688f3f1ee4d0aa57fb0` (tree `a8a15187f0752193ad75f6ad71e8de40a750712a`).
- Frozen preregistration SHA-256: `b23b0c9f6efabdd09cd21dc94ca638f848cf8cad6a96ead7117b6b9e94bb7021`.
- Duplicate/correlation proof: the complete Candidate 1–58 ledger plus tree, refs/tags/history, preregistrations,
  results and tools contained no strategy ranking the universe by realized volatility. Return ranks, own-symbol
  volatility acceleration and volatility state selectors are related disclosures but materially different mechanisms.
- Artifact: `C:\Dev\ZackBot_cowork_data\low_vol_factor_c59\low-vol-long-dev.json`.
- Canonical packet SHA-256: `8d266622c141dc4b0bfd7b97be03803b8803ef47a71939eca11d08627f56818f`.
- Artifact file SHA-256: `c8e8ece0340d244c6b56710617d70fe13ff2fbc6a1d89a3f62d48ae221fd0e7a`.
- 1,113 trades / 12 symbols; regimes 652 above / 461 below BTC 4h EMA200.
- Pre-cost gross -$65.489907; fees $259.556212; funding $36.690327; net -$361.736445 = -88.888561R.
- Mean -0.079864R; PF 0.872548; max DD 120.671464R; win rate 39.5328%.
- PASS sample, breadth and both regime counts. FAIL pre-cost gross, mean net R and PF. Drawdown is **extreme /
  shadow-only** under the prospective policy and cannot rescue failed economics.
- Multiplicity ledger is now 59 numbered candidates; future familywise alpha is
  `0.05 / 59 = 0.0008474576271186442`.

This append-only result binds the frozen commit and clean tree. Isolated PAPER eligibility remains **BLOCK**.
