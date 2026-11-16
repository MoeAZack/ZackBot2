# CX1 independent scout: 0/2 pass

Completed 2026-11-16T17:04:33.359185+02:00 (Africa/Cairo). Research only; consumed 165-coin calendar.
Both trials are rejected. No rule, parameter, universe, sample threshold or gate was changed after outcomes.
All metrics below are medians across the same 40 seeded coin orders; R15 means mean per-trade R at 15 bp per side.

| trial | status | median n | R15 | R23 | early | late | ex-best coin | fresh134 | extra31 | order-0 early/late |
|---|---|---:|---:|---:|---:|---:|---:|---:|---:|---|
| cx001_entropy_collapse_long_4h | FAIL | 62.0 | -0.088815 | -0.107783 | -0.073183 | -0.131196 | -0.156728 | -0.066928 | -0.159121 | 45/17 |
| cx002_entropy_collapse_short_4h | FAIL | 88.0 | -0.161658 | -0.186723 | -0.035526 | -0.288850 | -0.208202 | -0.101030 | -0.352198 | 44/45 |


CX001 fails every frozen gate, including the later-half count (17 < 30).
CX002 passes only order-0 half counts; it fails all other rule-v1 and both subset return gates.
Fresh/extra median trade counts are 46/16 for CX001 and 68/20 for CX002.
Full-precision ledger, gates, per-order results, raw signals, both order-0 tapes and compressed all-order tapes are included.
The baseline is 281 reported trials / 11 screen passes. These two trials make 283 / 11, excluding simultaneous lanes.
Neither candidate may be rescued by tuning. A screen pass, had there been one, would still add to multiple testing.

## Provenance and validation

Rules `2f4c4dc47c258440f1a323793daddd183cad5ded` were committed/pushed first;
cases `d8efdf3676ca715d1d46dc5b1bba01c1379fd384` were committed/pushed second; only then were tests and economics run.
See PREREGISTRATION.md for the ordinal-permutation-entropy family and prior-ledger distinction.
The formula uses 60 three-return ordinal patterns, normalized Shannon entropy crossing down through .95,
20-day directional return, matching R1, complete consecutive days, and the next day's first 4h signal.
That decision bar is 02:00/03:00 Cairo by date (00:00 UTC); modeled next-bar fill is 06:00/07:00 Cairo (04:00 UTC).
Parameters and 2.5 ATR / 2R / 30-bar management are frozen for both sides.

Eight targeted native Windows/CP1252 tests pass: independent reference loops across three seeds and both directions,
removed-day gaps, 14 full-output prefix comparisons, entropy bounds/ties, exact threshold/direction,
forced-regime/day-availability checks, incomplete-day exclusion and case/registration equality.
Both cases completed 40 path + 40 worst-path scenarios: 160 scenario runs total.
Independent arithmetic on saved all-order tapes reproduces all medians, seven rule-v1 gates and both subset gates,
with zero mismatches. This arithmetic check is not an independent execution replay.
Both pinned manifests and 175 source CSV hashes were verified before execution; all 166 distinct loaded CSVs
were checked against those manifests again afterward. Hashes and environment are in provenance.json.
Frozen rule code is byte-identical; the cases working copy differs only by CRLF line endings from its Git blob.

The first attempt stalled during `git archive` source export and was stopped BEFORE simulation or outcomes.
The documented `longscout_cx_transport.py` fallback exports exactly the same path list with `git cat-file --batch`,
validating every Git blob ID and preserving exact bytes. Each case includes transport_receipt.json.
Only parent source transport is replaced; V2 main, worker, signals, backtest, order generator and gates stay unchanged.
No failed economic run or results-driven rerun occurred.

Reproduce in PowerShell from this clone (the two case keys are in ledger.csv):

```powershell
$env:LONGSCOUT_CASES='longscout_cx1_cases.json'
$env:PYTHONIOENCODING='cp1252'
python research/extra31_screen/longscout_cx_transport.py C:/Dev/ZackBot_research_cache/longscout_fresh_20261114 C:/Dev/ZackBot_research_cache/h5_wide_d01_20261112/clean .git/lane5-repro --case cx001_entropy_collapse_long_4h --orders 40
# Repeat for the separately frozen cx002_entropy_collapse_short_4h case.
```

Limitations: already-consumed calendar, survivorship/selection multiplicity, historical exchange-filter coverage,
proxy funding and capped large-equity sizing; no fresh holdout, independent full execution replay or forward claim.
These are enabling research results and two rejections, not profitable strategies.

ROADMAP FIT: owner-authorized lane-5 independent strategy economics under the frozen LONGSCOUT protocol.
DEFERRED: tuning, additional rounds, new data, forward/PAPER books, activation and unrelated work.
No account, key, runtime, settings, scheduler, watchdog, order, other clone, merge or mark-ready action.
LANES: Build = frozen two-case round complete; Evidence = 160 scenario runs and both tape audits complete;
Integration = draft PR handoff to Claude, no dependent activation or merge.
