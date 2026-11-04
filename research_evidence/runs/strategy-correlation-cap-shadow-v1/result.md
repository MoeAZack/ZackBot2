# Shadow strategy correlation/risk cap result

Snapshot: 2026-11-04T13:58:27.217Z (Cairo: 2026-11-04T15:58:27.217000+02:00)
Cohort: `CH-20261020T100344Z-6b4aba0e`; 188 closed and 8 open positions.
Artifact body SHA-256: `f579c27a5622affadbb8a531a5a348d3265ff72e3a6e62a7db6a88d553ad726c`.

All values are descriptive shadow evidence. No variant is selected and no PAPER/runtime setting changed.
The recent burst has 3 candidate entries and 1 pre-existing open exp_vwaprev position(s) at admission.
The complete exp_vwaprev sample has only 19 positions, below a 30-trade inference floor; no p-value, promotion, or best-cell claim is made.

## Recent exp_vwaprev cluster

Window: 2026-11-03T18:01:28+02:00 to 2026-11-04T04:55:37+02:00 Cairo (2026-11-03T16:01:28Z to 2026-11-04T02:55:37Z UTC).

| Variant | Status | Candidates | Accepted | Blocked | Net R | PF | DD R | Closed-only diagnostic net / PF / DD | Turnover | Position / risk blocks |
| --- | --- | ---: | ---: | ---: | ---: | ---: | ---: | --- | --- | --- |
| baseline | complete | 3 | 3 | 0 | -3.128609 | 0.000000 | 3.128609 | -3.128609 / 0.000000 / 3.128609 | USDT 17556.798450 | 0 / 0 |
| cap_1_r2 | complete | 3 | 0 | 3 | 0.000000 | None | 0.000000 | 0.000000 / None / 0.000000 | 0 | 3 / 0 |
| cap_2_r2 | complete | 3 | 1 | 2 | -1.063178 | 0.000000 | 1.063178 | -1.063178 / 0.000000 / 1.063178 | USDT 6989.313320 | 2 / 0 |
| cap_3_r2 | complete | 3 | 1 | 2 | -1.063178 | 0.000000 | 1.063178 | -1.063178 / 0.000000 / 1.063178 | USDT 6989.313320 | 0 / 2 |

## Full exp_vwaprev cohort

Window: 2026-10-20T19:01:19+03:00 to 2026-11-04T04:55:37+02:00 Cairo (2026-10-20T16:01:19Z to 2026-11-04T02:55:37Z UTC).

| Variant | Status | Candidates | Accepted | Blocked | Net R | PF | DD R | Closed-only diagnostic net / PF / DD | Turnover | Position / risk blocks |
| --- | --- | ---: | ---: | ---: | ---: | ---: | ---: | --- | --- | --- |
| baseline | complete | 19 | 19 | 0 | 3.857494 | 1.942334 | 4.093551 | 3.857494 / 1.942334 / 4.093551 | USDT 71803.775093 | 0 / 0 |
| cap_1_r2 | complete | 19 | 9 | 10 | 2.129361 | 3.002826 | 1.063178 | 2.129361 / 3.002826 / 1.063178 | USDT 33207.385079 | 10 / 0 |
| cap_2_r2 | complete | 19 | 15 | 4 | 5.136930 | 3.492879 | 2.060642 | 5.136930 / 3.492879 / 2.060642 | USDT 56926.603723 | 4 / 0 |
| cap_3_r2 | complete | 19 | 15 | 4 | 5.136930 | 3.492879 | 2.060642 | 5.136930 / 3.492879 / 2.060642 | USDT 56926.603723 | 0 / 4 |

## Whole cohort to snapshot

Window: 2026-10-20T13:30:39+03:00 to 2026-11-04T14:01:14+02:00 Cairo (2026-10-20T10:30:39Z to 2026-11-04T12:01:14Z UTC).

| Variant | Status | Candidates | Accepted | Blocked | Net R | PF | DD R | Closed-only diagnostic net / PF / DD | Turnover | Position / risk blocks |
| --- | --- | ---: | ---: | ---: | ---: | ---: | ---: | --- | --- | --- |
| baseline | insufficient | 196 | 196 | 0 | None | None | None | -25.201914 / 0.677798 / 46.086858 | USDT 893251.781009 | 0 / 0 |
| cap_1_r2 | insufficient | 196 | 116 | 80 | None | None | None | -7.472966 / 0.825368 / 22.689034 | USDT 480926.517028 | 80 / 0 |
| cap_2_r2 | insufficient | 196 | 148 | 48 | None | None | None | -13.994727 / 0.754870 / 34.642713 | USDT 612254.863332 | 24 / 24 |
| cap_3_r2 | insufficient | 196 | 148 | 48 | None | None | None | -13.994727 / 0.754870 / 34.642713 | USDT 612254.863332 | 0 / 48 |

Insufficiency: `right_censored_at_window`, `right_censored_open`, `risk_changes_after_entry`.

## Whole cohort latest flat prefix

Window: 2026-10-20T13:30:39+03:00 to 2026-10-30T07:28:29+02:00 Cairo (2026-10-20T10:30:39Z to 2026-10-30T05:28:29Z UTC).

| Variant | Status | Candidates | Accepted | Blocked | Net R | PF | DD R | Closed-only diagnostic net / PF / DD | Turnover | Position / risk blocks |
| --- | --- | ---: | ---: | ---: | ---: | ---: | ---: | --- | --- | --- |
| baseline | insufficient | 172 | 172 | 0 | None | None | None | -25.453908 / 0.652133 / 46.086858 | USDT 836290.076569 | 0 / 0 |
| cap_1_r2 | insufficient | 172 | 103 | 69 | None | None | None | -7.621047 / 0.813102 / 22.689034 | USDT 454294.791518 | 69 / 0 |
| cap_2_r2 | insufficient | 172 | 129 | 43 | None | None | None | -15.597025 / 0.711581 / 34.642713 | USDT 568051.675532 | 19 / 24 |
| cap_3_r2 | insufficient | 172 | 129 | 43 | None | None | None | -15.597025 / 0.711581 / 34.642713 | USDT 568051.675532 | 0 / 43 |

Insufficiency: `risk_changes_after_entry`.

## Boundary

Blocked entries earn zero and never cause a future trade to be substituted. Recorded costs, funding, fills and exits are unchanged for retained positions. The whole current cohort is not a completed performance sample while open positions remain, so its scalar performance fields are intentionally null.
