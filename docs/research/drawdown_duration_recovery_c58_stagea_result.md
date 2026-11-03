# Candidate 58 / drawdown-duration recovery — Stage-A result

Verdict: **REJECTED IN DEVELOPMENT / BLOCK**. Research-only and default-OFF; no rescue, folds, sealed holdout,
PAPER, runtime, master or PR #277 action.

- Frozen head: `c2887996a45047ffeb38fbd7893fd5ce0b57ae64`.
- Frozen preregistration SHA-256: `62892907c90575611817d04417f5a1c788cc1b9200c7edea125a249b3f82ade6`.
- Duplicate/correlation proof: the complete Candidate 1–57 ledger plus tree, refs/tags/history, preregistrations,
  results and tools contained no drawdown-duration recovery signal. Existing underwater references are performance
  metrics; oscillator recoveries and candle exhaustion use different instantaneous or fixed-sequence mechanisms.
- Artifact: `C:\Dev\ZackBot_cowork_data\drawdown_recovery_c58\drawdown-recovery-long-dev.json`.
- Canonical packet SHA-256: `7b20b382c832c0bc6fe43596283266ad11737bd6efafd28ec3670721539b4f8f`.
- Artifact file SHA-256: `f3ccd8e196f574ba2467ff6d2e6d5498ca489a49acd401253f1219c357d535ae`.
- 1,065 trades / 18 symbols from 2,069 raw signals; regimes 569 above / 496 below BTC 4h EMA200.
- Pre-cost gross -$166.382298; fees $112.210878; funding $3.780666; net -$282.373842 = -76.975661R.
- Mean -0.072278R; PF 0.736016; max DD 84.900117R; win rate 20.6573%.
- PASS sample, breadth and both regime counts. FAIL pre-cost gross, mean net R and PF. Drawdown is **extreme /
  shadow-only** under the prospective policy and cannot rescue failed economics.
- Multiplicity ledger is now 58 numbered candidates; future familywise alpha is `0.05 / 58 = 0.0008620689655172414`.

This append-only result binds the frozen commit and clean tree. Isolated PAPER eligibility remains **BLOCK**.
