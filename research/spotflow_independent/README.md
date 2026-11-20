# SPOTFLOW-D01 independent oracle (Claude Evidence lane)

Written only from the #219 6830883380 contract text; no Codex candidate code imported or copied. Frozen before any data or
outcome run. `spotflow_oracle.py` = kline reader (ms/us normalisation, header optional, conflicting duplicates refused), flow
I = 2*tbq/qv - 1 (invalid if qv <= 0 or tbq outside [0, qv]), canonical-futures ret3/Wilder ATR, PIT eligibility for both
venues (prior 30 complete UTC days >= $10m median AND >= 720 consecutive bars), level signals with BTC skip and window, and an
independent causal-1h lot (next open, $8 risk, 3 ATR stop, 2R, 12 h, gap, terminal; 5/13/21 bp fees, 2 bp slip, 1bp/8h funding).
`test_spotflow_oracle.py`: 11 synthetic tests (pass, incl. CP1252). Mutation: 17 key-line mutants; equivalent survivors are
BTC-skip `<` vs `<=` (exact 0.015 is not reachable in float), the 720-bar run (redundant with the 30-complete-day rule once a gap
makes a day incomplete), the 2-vs-3 consecutive-bar check (t-3 missing already makes ret3 NaN) and the entry-bar gap check.
Parity note for Codex: the 0.2 and 0.015 levels are float-fragile (2*0.6-1 = 0.19999999999999996), so tuples within 1e-9 of a
threshold must be listed separately rather than counted as mismatches or matches.
Use when the manifest arrives: oracle.signals(...) per coin -> compare (sym, t, side) tuples; oracle_lot(...) on selected lots
-> compare t_out/why/qty/gross/fees/fund/pnl.
