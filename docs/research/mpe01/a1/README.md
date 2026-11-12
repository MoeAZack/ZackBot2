# MPE-01 amendment A1: zero-impact proof

A1 frozen at 97fbe9679083cf1dfb4bdf35e480320cbc0e6a7b (amendment_a1.json 62a63494, run_mpe01_a1.py 01d91535, tests 4a581e49, 20 tests).
Single change: fill bar opening beyond the stop exits at that adverse open (label stop_gap_fillbar).
Full rerun (2 paths x maker 4 + taker 3 cost cases x 40 orders, plus the portfolio-free signal-level tables):
- all 24 CSV outputs byte-identical to the c5572270 run (result head ac2304e1);
- mpe01_a1_result.json equals mpe01_result.json in every path summary, all 40-order statistics, signal counts and signal-level tables
  (only runner/amendment hashes and the new counter differ); mpe01_a1_result.json sha256 ffdb2708...;
- fillbar_open_gap_exits = 0 in every maker summary; with all 40-order stats identical and the 1,319-signal table identical, no eligible
  fill is affected. This matches Codex's independent count of zero (PR #337 6830146743).
The economics in ../README.md therefore stand unchanged under A1. Still exploratory; no promotion or activation.
