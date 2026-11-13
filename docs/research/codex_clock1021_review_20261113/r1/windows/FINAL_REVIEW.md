# Final CLOCK-1021 Windows review

Exact review head: `6e259ea5acf2379fe09bff933d7c5b05cfaa269e`. Exact master baseline: `9cb4341f2d94c21434492515b8e85f2b42c148ce`.

Independent source/fault checks: PASS. Windows full-gate acceptance: held; consult the failed steps below. No merge, installation, account, key, position or scheduler change.

Both Windows full commands ran serially and unmodified, with private app-data roots and non-installing buildcheck. The original result records are preserved; supplemental UI retests do not overwrite them. Times in the receipts use Africa/Cairo.

| Checkout | Gate | Result | Seconds |
|---|---|---|---|
| head_r1 | static: every .py file compiles | passed | 6.4 |
| head_r1 | static: no private files or key-like strings in the tree | passed | 2.6 |
| head_r1 | dataset: every file matches DATA_MANIFEST.json | passed | 0.5 |
| head_r1 | tests (all) | FAILED | 3601.2 |
| head_r1 | replay 1: MOM pyramid + DCA + Squeeze | passed | 486.7 |
| head_r1 | replay 2: breakout + bear shorts + Donchian | passed | 1798.5 |
| head_r1 | UI harness (all tabs x 3 widths, flows, API failures) | FAILED | 62.2 |
| head_r1 | installer buildcheck | FAILED | 3771.5 |
| base | static: every .py file compiles | passed | 7.4 |
| base | static: no private files or key-like strings in the tree | passed | 2.9 |
| base | dataset: every file matches DATA_MANIFEST.json | passed | 0.7 |
| base | tests (all) | FAILED | 3600.1 |
| base | replay 1: MOM pyramid + DCA + Squeeze | passed | 446.9 |
| base | replay 2: breakout + bear shorts + Donchian | passed | 1781.6 |
| base | UI harness (all tabs x 3 widths, flows, API failures) | FAILED | 61.6 |
| base | installer buildcheck | FAILED | 3619.4 |

Failure-ID comparison status: **INCONCLUSIVE**. Head-only and base-only failure IDs are unavailable. Null values in comparison.json mean no complete comparison, never zero regressions.

The full test step hit its 3600-second limit without completed JUnit on the review head. The zero counters printed in that incomplete summary are placeholders, not passed/failed test totals. Claude's Linux 39F/124E comparison cannot be presented as a Windows result. Gate timeouts do not establish that every failure is pre-existing.

Completed additional Windows evidence: 39/39 clock regression tests pass; isolated golden pack 32 passed, 8 skipped, 32 expected failures. The head's original UI failure was a browser-library/cache mismatch; a private environment using pinned Playwright 1.56 passes 173/173 UI checks. Full acceptance still requires complete Windows all-test reports and a completed buildcheck under a supported execution window; no gate was skipped or weakened here.

Both build launchers' timeouts left PowerShell/test descendants retaining their output pipes. Only the identity-checked private review installer trees (head PowerShell PID 26004; baseline PID 17804) were terminated after their deadlines, to release the failed gates. No live bot process was targeted. The head full run returned at 13 November 22:08:58 Cairo; the baseline returned at 14 November 00:47:41 Cairo. Both underlying verify.py commands returned exit code 1.

The independent reproducer covers protective stop/close/algo retries with unchanged client IDs and windows 6000/10000/20000/30000 ms, one normal-window retry for market/maker entries, the four repaired alert/deadline cases, 37,296 fresh-offset non-1021 comparisons with zero differences, and unchanged `_create_lot`. The latency bound is clock-only: mixed network/rate-limit retries may take longer. The PR description qualification was accepted without moving the tested head.
