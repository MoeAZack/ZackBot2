# Lane 5 independent CX search

Published 2026-11-16T17:17:16.241888+02:00 (Africa/Cairo). 4/8 registered trials completed, 0 screen passes.
Baseline: 281 reported trials / 11 screen passes. Completed cumulative count: 285,
excluding concurrent lanes. Independent passes increase the multiple-testing count.

| trial | status | median n | R15 | fresh134 | extra31 |
|---|---|---:|---:|---:|---:|
| cx001_entropy_collapse_long_4h | FAIL | 62.0 | -0.088815 | -0.066928 | -0.159121 |
| cx002_entropy_collapse_short_4h | FAIL | 88.0 | -0.161658 | -0.101030 | -0.352198 |
| cx003_cusum_long_4h | FAIL | 104.0 | -0.040927 | -0.102058 | +0.143486 |
| cx004_cusum_short_4h | FAIL | 47.0 | +0.100963 | +0.048271 | +0.346438 |


R15 and subset returns are mean per-trade R at 15 bp per side, medians across 40 seeded coin orders.
Full ledger CSV/JSON includes R23, both calendar halves, ex-best coin, order-0 half counts and every failed gate.
Round directories retain full results, signals, order-0 path/worst tapes, all-order tapes and export receipts.
Original CX1 evidence and its no-follow-up statement describe its original bounded assignment;
the owner subsequently explicitly authorized CX2-CX4. CX1 remains unchanged.

## New families and no-tuning provenance

CX2: sequential, reset-on-threshold standardized CUSUM. Prior V6 momentum and V21 autocorrelation do not
implement sequential reset states. CX3: within-day squared-return energy concentration, unlike V89 volume
concentration across 30 days, V47 session scheduling or V90 return/volume shock size. CX4: previous return
versus next absolute return coupling, unlike V85 contemporaneous return/volume correlation, V21 linear
autocorrelation or V84 marginal semivariance. None repeats CX1 ordinal entropy.
Each round committed rules first, then cases. All six new trials were pushed before any of their outcomes.
freeze_provenance.json verifies immutable Git blobs; working JSON line endings may be CRLF.
No rule, parameter, gate, universe or management change followed an outcome. No failed trial was replaced.

## Validation and limits

Unchanged LONGSCOUT V2 worker, costs, funding proxy, exchange rules, eligibility, $8 risk,
$3,333.33 cap, one position, 40 seeded orders, seven rule-v1 gates and both subset gates.
Every case runs 40 path and 40 worst-path scenarios. The existing source-transport fallback reads the exact
same frozen export list via cat-file, validates every blob ID and retains byte/hash receipts.
It does not change execution. Two original manifests and 175 source CSV hashes were checked before the new batch;
166 distinct loaded hashes were checked again after runs and match original CX1 loaded data.

Original CX1 has 8 targeted native Windows/CP1252 tests; CX2-CX4 add 12 tests covering independent formulas,
gaps, flat inputs, exact thresholds, regime gates, availability and 36 full-output synthetic prefix checks.
18 independent real-data signal comparisons (six new rules on three coins) and 79 real-data prefix comparisons
pass with zero mismatches. These are sampled causality checks, not an exhaustive proof.
Every completed case's saved trades are independently reconciled against all medians and acceptance gates;
this is arithmetic verification, not an independent admission/cash replay.
The original data calendar is consumed; historical exchange-filter coverage, proxy funding, capped sizing,
survivorship and selection multiplicity remain limitations. No fresh holdout or forward profit is claimed.

Reproduce each case with LONGSCOUT_CASES=longscout_cxN_cases.json and the existing longscout_cx_transport.py,
using the frozen source data paths shown in each execution_receipt.json. Reconcile with longscout_cx_audit.py.
Final focused test receipt is validation.json when present.

ROADMAP FIT: owner-authorized sustained lane-5 independent strategy economics under frozen LONGSCOUT gates.
DEFERRED: tuning, new data, full independent execution replay, forward/PAPER activation and unrelated work.
No accounts, keys, runtime, settings, schedulers, watchdogs, orders, other clones, merges or ready-state actions.
LANES: Build = six new rules frozen; Evidence = sequential research runs and completed-case audits;
Integration = same draft PR #356, Claude disposition only.
