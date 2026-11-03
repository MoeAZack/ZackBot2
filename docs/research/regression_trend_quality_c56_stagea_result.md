# Candidate 56 / regression trend quality — Stage-A result

Verdict: **REJECTED IN DEVELOPMENT / BLOCK**. Research-only and default-OFF; no rescue, folds, sealed holdout,
PAPER, runtime, master or PR #277 action.

- Frozen head: `aca290986d9d21db81f98445d8a9acc2b0c716e9`.
- Frozen preregistration SHA-256: `2946cbea21b72f55940ccca50bbb6d57f148bcf25aa1952953fc8196e78775fc`.
- Duplicate/correlation proof: the complete Candidate 1–55 ledger plus tree, refs/tags/history, preregistrations,
  results and tools contained no rolling log-price regression slope t-statistic rule. EMA/momentum, autocorrelation,
  sign entropy and path-efficiency states are the disclosed nearest trend exposures but use different mechanisms.
- Artifact: `C:\Dev\ZackBot_cowork_data\regression_trend_c56\regression-trend-long-dev.json`.
- Canonical packet SHA-256: `9adb076665e2c7fe389cd44e57c3f519a54d7190d5b450b669320fa8388e29b1`.
- Artifact file SHA-256: `8de53c3fb59f25a870d719cee01c47911403ac152decbe4d1d78ad9ed395435f`.
- 844 trades / 18 symbols from 25,103 raw signals; regimes 546 above / 298 below BTC 4h EMA200.
- Pre-cost gross $278.729872; fees $210.537193; funding $27.059050; net $41.133629 = +23.581423R.
- Mean +0.027940R; PF 1.015611; max DD 50.081058R; win rate 43.7204%.
- PASS pre-cost gross, mean net R, sample, breadth and both regime counts. FAIL PF>1.05. Drawdown is **extreme /
  shadow-only** and cannot rescue the failed economic gate.
- Multiplicity ledger is now 56 numbered candidates; future familywise alpha is `0.05 / 56 = 0.0008928571428571429`.

This append-only result binds the frozen commit and clean tree. Isolated PAPER eligibility remains **BLOCK**.
