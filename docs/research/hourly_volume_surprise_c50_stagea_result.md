# Candidate 50 / intraday-seasonality-normalized volume surprise — Stage-A result

Verdict: **REJECTED IN DEVELOPMENT / BLOCK**. Research-only and default-OFF; no rescue, folds, sealed holdout,
PAPER, runtime, master or PR #277 action.

- Frozen head: `eee90ec43b363a3a93ec3b8dc884ad24a197a1fc`.
- Frozen preregistration SHA-256: `30d80d052fb446197ec8735bd163a7e5815e5d846ef7db2bfedf3032a4f21971`.
- Duplicate/correlation proof: the complete Candidate 1–49 ledger plus tree, refs/tags/history, preregistrations,
  results and tools contained no same-UTC-hour volume-normalization trial. Generic rolling volume, VPI/OBV and independent
  aggTrades mechanisms differ; momentum/volume beta is the disclosed nearest correlation.
- Artifact: `C:\Dev\ZackBot_cowork_data\hourvol_c50\hourly-volume-surprise-long-dev.json`.
- Canonical packet SHA-256: `3ec6b00a89c616c9b88d4c4f9fa88c6c515017745cd03b525b1bcc6a78aa103c`.
- Artifact file SHA-256: `e0e2d9dd4c03f33ed5cc8c3c6f84d72f5c6ca0be0c9a843ed63e7611cbf0166c`.
- 780 trades / 18 symbols from 1,777 raw signals; regimes 541 above / 239 below BTC 4h EMA200.
- Pre-cost gross +$55.843163; fees $127.612526; funding $9.063231; net -$80.832594 = -9.904442R.
- Mean -0.012698R; PF 0.953566; max DD 51.666404R; win rate 35.6410%.
- PASS pre-cost gross, sample (`>=100`), breadth (`>=8`) and both regime counts (`>=20`). FAIL mean net R and PF.
  Drawdown is classified **high** under the prospective profit/DD policy and cannot rescue failed after-cost gates.
- Multiplicity ledger is now 50 numbered candidates; any later promotion would require familywise alpha `0.001` plus
  prospective chronological OOS evidence.

This append-only result binds the frozen commit and clean tree. Isolated PAPER eligibility remains **BLOCK**.
