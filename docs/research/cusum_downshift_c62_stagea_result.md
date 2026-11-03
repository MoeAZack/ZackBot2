# Candidate 62 / CUSUM negative-drift change detection — Stage-A result

Verdict: **REJECTED IN DEVELOPMENT / BLOCK**. Research-only and default-OFF; no rescue, folds, sealed holdout,
PAPER, runtime, master or PR #277 action.

- Frozen head: `e28800e5ca65ceb56b5a5ab77777cc98f43eddd3` (tree `d974a8d05f663454f1ba0c0a6aac5ba2463b934e`).
- Frozen preregistration SHA-256: `53237a770ca74bb5f949d02df0bed5a5e2b37b0e0d28de91f14e1343b486c274`.
- Duplicate/correlation proof: the complete Candidate 1–61 ledger plus tree, refs/tags/history, preregistrations,
  results and tools contained no resettable CUSUM/sequential-change signal. Momentum shorts, signed-return
  autocorrelation and volatility clustering are related disclosures but use fixed-window levels or dependence metrics.
- Artifact: `C:\Dev\ZackBot_cowork_data\cusum_c62\cusum-short-dev.json`.
- Canonical packet SHA-256: `0d8413064794ee1f588ec7315ff2a09f87d3511ffb7b36d8a13dc1d944eb969b`.
- Artifact file SHA-256: `9a84cfb3598d59be18fee6e7edf7884a7a5f6be541dbc51626b5c28363709365`.
- 607 trades / 18 symbols; regimes 352 above / 255 below BTC 4h EMA200.
- Pre-cost gross -$38.646808; fees $76.561113; funding -$2.815307; net -$112.392613 = -15.395840R.
- Mean -0.025364R; PF 0.878160; max DD 75.017194R; win rate 32.1252%.
- PASS sample, breadth and both regime counts. FAIL pre-cost gross, mean net R and PF. Drawdown is **extreme /
  shadow-only** under the prospective policy and cannot rescue failed economics.
- Multiplicity ledger is now 62 numbered candidates; future familywise alpha is
  `0.05 / 62 = 0.0008064516129032258`.

This append-only result binds the frozen commit and clean tree. Isolated PAPER eligibility remains **BLOCK**.
