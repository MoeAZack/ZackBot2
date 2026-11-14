# WINPATH-01 independent Windows review

Final reviewed head: `615e14b394400a2bbe188d3b4d7dee5991e7e1d6` (#346 / #345), based on merged CLOCK-1021 master `3d8a87a418c248b27403e0927d2fd05a7e5cd689`. All five Windows-fix file blobs are identical to corrected reviewed head `aa7246ae7bbcc9ba3474b73cff8e3d25102225a9`.

## Native full test result

Requested command: `python -m pytest -q -p no:cacheprovider -rfE -n 6 --junitxml=<private>.xml tests`. Real normal Windows C: temp and F: checkout; isolated app-data; no drive-simulation plugin. Completed naturally at 14 November 2026 06:43:43 Cairo, exit 0, 1330.95 s, source clean.

| Windows source | Passed | Failed | Errors | Skipped | Expected failures | Unique cases |
|---|---:|---:|---:|---:|---:|---:|
| Original pinned 9cb4341f | 11056 | 22 | 0 | 41 | 32 | 11151 |
| Tested CLOCK-1021 baseline 6e259ea5 | 11095 | 22 | 0 | 41 | 32 | 11190 |
| Final WINPATH head 615e14b3 | 11149 | 0 | 0 | 41 | 32 | 11222 |

All 22 baseline failure IDs are fixed; zero new failed/errored IDs; zero baseline test IDs removed; 32 new path/identity test IDs added. Master 3d8a87a4 has the identical Git tree to tested baseline 6e259ea5 (verified at its normal merge); this is tree-equivalence provenance, not a claim that a second baseline run occurred. Full ID comparisons and original-baseline comparisons are in comparison.json.

## Focused review

At original cd0b5bf9, native focused tests passed 215/215, but the new helper was absent from CODE_MODULES despite the explicit per-module byte-identity contract. An isolated real-Git reproduction showed that the helper was absent from module hashes; dirty-tree/commit binding still caught initial edits, so this was a P2 identity-completeness issue, not a demonstrated new authorization bypass. Broader post-preflight rehash and existing filesystem-alias issues were deferred.

Corrected aa7246ae adds the helper to CODE_MODULES plus a mutation-sensitive identity regression. Native focused tests then passed 216/216 in 112.45 s. No protected records, economic parameters, strategy rules or runtime settings changed. The final merged-base head retains the same five reviewed file blobs.

## Remaining gate

Unmodified Windows `build_app.bat buildcheck` is running with its own private app-data and a 14400-second deadline. It is NOT marked passed by unit-test results; acceptance requires real BUILDCHECK_OK and executable self-test. The exact-head normal GitHub full gate runs in parallel. Final results will be recorded separately.

No installation, account, key, position, runtime or scheduler action. Raw native XML/logs are retained under `F:\ZackBot2_data\WINPATH-01-REVIEW-20261114-evidence\final_615e14b3`; their hashes are published.
