# FLAG-PCT: percent bull/bear flag, 4h and daily: REJECTED (2026-11-12)

Source: Claude Projects. Idea from the public `day-trader-skill` repo's `bull_flag` scan (US stocks, long only), ported
literally and mirrored for shorts. Rules and evaluation frozen in `bf11d726` (`research/flagpct.py`,
`research/flagpct_run.py` = NEWSTRAT-CP runner and gate, unchanged) before any run. Master `640292a2` `BT.run`.
Note: the impulse is measured from the close 16 bars back to the close just before the 5-bar flag (6 bars back); the
docstring's "5 / 15 bars ago" is shorthand for that.

| ID | tf | DEV n / R@15 | HOLDOUT n / R@15 | R@7 all | R@23 all | MaxDD path / worst | Verdict |
|---|---|---|---|---|---|---|---|
| FP1 | 4h | 96 / -0.148 | 37 / -0.124 | -0.118 | -0.164 | 18R / 21R | REJECTED |
| FP2 | 1d | 47 / -0.040 | 24 / -0.175 | -0.071 | -0.100 | 12R / 12R | REJECTED |

Both lose even at 7 bp (no gross edge) and both sides lose overall on 4h. FP2 daily longs were positive on DEV
(+$39) but negative on HOLDOUT (-$75); FP2 holdout also has under 30 closes. With CP-N5 (1h flag, rejected), flag
continuation is now negative on 1h, 4h and 1d. Do not retune; a changed hypothesis is a new candidate.
Not registered in `strategies.py`. Raw results: `docs/research/flagpct/*.result.json`.
