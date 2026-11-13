# CLOCK-1021 supported-window Windows tests

This closes the missing complete Windows failure-ID comparison from `befd418f`; it does not turn the earlier timed-out full gate into a pass.

Exact head: `6e259ea5acf2379fe09bff933d7c5b05cfaa269e`. Exact baseline: `9cb4341f2d94c21434492515b8e85f2b42c148ce`.

| Checkout | Passed | Failed | Errors | Skipped | Expected failures | Collected |
|---|---:|---:|---:|---:|---:|---:|
| Head | 11095 | 22 | 0 | 41 | 32 | 11190 |
| Master | 11056 | 22 | 0 | 41 | 32 | 11151 |

The complete `(classname, name, failure/error)` multisets are identical: 22 shared failures, zero head-only and zero base-only failures/errors. Exactly 39 test IDs were added, all in the new clock test module; all 39 pass. No IDs were removed. Collection-only checks at both exact commits prove complete unique test-ID coverage in each native JUnit report. There were no worker crashes or internal pytest errors.

Both commands were `python -m pytest -q -p no:cacheprovider -rfE -n 6 --junitxml=<private output> tests`, run serially with separate app-data roots and a 14400-second deadline. Head completed at 14 November 01:14:21 Cairo (1272.06 s); master at 01:33:14 Cairo (1133.22 s). Both completed naturally with pytest exit 1 due to their identical failures. Both source checkouts remained clean.

The failing families are the existing Windows research-runner path tests (`test_bpyr_runner` and `test_fib_pullback_02`). Head failure messages include 21 cross-drive path errors (C: temporary files versus the F: checkout) and one wrapper return-code assertion. The ID parity is proven directly by the two completed JUnit files, not inferred from that diagnosis or from Claude's Linux counts.

Raw JUnit, logs and collection lists are retained locally under `F:\ZackBot2_data\CLOCK-1021-REVIEW-20261113\supported_window_20261114`; their SHA-256 values are published. XML ordering and LF/CRLF are irrelevant to the identity comparison.

Buildcheck is a separate remaining step, running unmodified with a four-hour deadline on each checkout, serially. It is not marked passed by these unit-test results. No source, account, key, position, runtime or scheduler change, installation or merge occurred.
