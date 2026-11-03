# Candidate 84 / Directional-change overshoot — Stage-A result

Verdict: **REJECTED IN DEVELOPMENT / BLOCK**. Research-only and default-OFF; no rescue, folds, sealed holdout,
PAPER, runtime, master or open-PR action.

- Frozen head: `f816e3f6b58b36da2fe74a24a5c28d87cc963d37` (tree `4b80799a943a52e65469be8143590baf2e1d76a2`).
- Frozen preregistration SHA-256: `d1e2134f23843849a7620711fbac558abaacb9fa1e7246172c7a9490b8c16b1f`.
- Duplicate/correlation proof: the complete Candidate 1–83 ledger plus tree, refs/tags/history, preregistrations,
  results and tools contained no event-time directional-change rule. C75 turning-point counts and fixed-window breakout
  rules are related price-path disclosures but use materially different state and trigger definitions.
- Artifact: `C:\Dev\ZackBot_cowork_data\directional_change_c84\directional-change-long-dev.json`.
- Canonical packet SHA-256: `95aba6e628fe7582abe06ca832dc88acdc064329b6d43514bfe5c138e20adb6f`.
- Artifact file SHA-256: `3b96d4e87380d5de4204dd33a778de21ffd21d3d76276e00257d4ac3ff417057`.
- Funding-manifest digest: `54912d9d2bf6e6fc45bc75c0553f0bd867e877972beeb3452785937379baf1e7`.
- 1,151 trades / 18 symbols; regimes 749 above / 402 below BTC 4h EMA200.
- Pre-cost gross -$18.206413; fees $196.782712; funding $12.134562; dollar net -$227.123686;
  risk net -51.625568R.
- Mean -0.044853R; PF 0.873847; max DD 87.544675R; win rate 32.4935%.
- PASS sample, breadth and both regime-count gates. FAIL pre-cost gross, mean net R, PF and max-DD <=15R gates.
- Multiplicity ledger is now 84 numbered candidates; future familywise alpha is `0.05 / 84`.

The canonical packet hash was independently recomputed and matched. The frozen event-time overshoot hypothesis is
rejected as specified; no tuning or alternate threshold was attempted. Isolated PAPER eligibility remains **BLOCK**.
