# Candidate 53 / learned hour-of-day return drift — Stage-A result

Verdict: **REJECTED IN DEVELOPMENT / BLOCK**. Research-only and default-OFF; no rescue, folds, sealed holdout,
PAPER, runtime, master or PR #277 action.

- Frozen head: `024c0438346d69047d411e6f5ff18ba31738eccb`.
- Frozen preregistration SHA-256: `f8930cc57a0f2d6d90cd9ac060424831bfe24dcd4e30f7d6cb7def362c07c73c`.
- Duplicate/correlation proof: the complete Candidate 1–52 ledger plus tree, refs/tags/history, preregistrations,
  results and tools contained no learned same-hour return-drift rule. Fixed session/weekend events and C50 volume
  seasonality differ; calendar/session correlation is the disclosed nearest exposure.
- Artifact: `C:\Dev\ZackBot_cowork_data\hourdrift_c53\hour-of-day-drift-long-dev.json`.
- Canonical packet SHA-256: `e5df2f2bab870c1e37d16b152ba9c40ce6125d237cbf44aaaa8b4217fae74f24`.
- Artifact file SHA-256: `70c7b32c8284207e2ffa5d06c192e7eb40a949f28c65685fbf131d4e4c9faf28`.
- 4,543 trades / 18 symbols from 11,001 raw signals; regimes 2,915 above / 1,628 below BTC 4h EMA200.
- Pre-cost gross -$217.044914; fees $271.674789; funding $7.422004; net -$496.141706 = -366.451916R.
- Mean -0.080663R; PF 0.722884; max DD 368.510026R; win rate 42.2408%.
- PASS sample, breadth and both regime counts. FAIL pre-cost gross, mean net R and PF. Drawdown is **extreme / shadow-
  only** and cannot rescue failed economics.
- Multiplicity ledger is now 53 numbered candidates; future familywise alpha is `0.05 / 53 = 0.0009433962264150943`.

This append-only result binds the frozen commit and clean tree. Isolated PAPER eligibility remains **BLOCK**.
