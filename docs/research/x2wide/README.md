# X2-WIDE results (research only)

**Final result:** `fast/x2wide_result.json`, run at frozen head a525ed69.
- Runner sha256: 8251f9b3.
- Contract sha256: 66dfcef3.
- Signal module sha256: 929cb91e.
- Input manifest sha256: 99af9bfb.

**Partial result:** the files in this directory (not in `fast/`) come from the first frozen head, de27e871 (runner 2b506eb5).
- That run was stopped once the original8 arm had finished, because it was slow.
- Its original8 output matches `fast/` exactly: identical JSON for both paths, all costs and all 40 orders, and byte-identical order-0 trade CSVs.
- `x2wide_result.json` here is the snapshot taken when it was stopped. Any extra31 rows in it are incomplete; ignore them.

**Verdict:** every arm fails the preregistered gates (Codex #219 6827389390). R@15 is the causal 40-order median.

| Arm | R@15 | Result | Reason |
|---|---|---|---|
| original8 | -0.003 | FAIL | |
| extra31 | +0.001 | FAIL | R23 < 0, early < 0, ex-best < 0, p10 < 0, worst path < 0 |
| union39 | +0.023 | FAIL | R23 -0.004, p10 -0.015 |

No PAPER-review eligibility. Any side-specific reading would be a re-specification made after seeing the results.
