# NEWSTRAT-CP: eight new candidates, two survivors (2026-11-12)

Source: Claude Projects. All research_only, default-OFF. Definitions frozen before any run: wave 1 at `822c446d`
(`research/newstrat_cp.py`), wave 2 at `b75fa28b` (`research/newstrat_cp2.py`); evaluation `research/newstrat_cp_run.py`.

**Setup.** Unchanged `BT.run` (master `640292a2`). 8 cohort coins with multi-year files (BTC ETH XRP SOL DOGE BNB LINK
AVAX; no long ADA file). $4,000, risk 0.004 ($16/entry), 2 positions, max_lev 10, daily halt 8%, testnet exchange
filters, funding 0.01%/8h scaled per bar. 1h from 2022-08-26, 4h/1d from 2021-12-19, to 2026-10-04. DEV < 2025-01-01,
HOLDOUT >= 2025-01-01, single run, no tuning. Costs 7 bp/side base, stresses +8/+16 bp on executed notional
(15/23 bp per side). Gate: >=30 closes per period, R@15bp > 0 in both, R@23bp >= -0.05 overall, MaxDD <= 30R.

| ID | Candidate | tf | DEV n / R@15 | HOLDOUT n / R@15 | R@23 all | MaxDD path / worst | Verdict |
|---|---|---|---|---|---|---|---|
| N1 | FVG continuation (ICT) | 1h | 575 / -0.18 | 387 / -0.27 | -0.34 | 86R | REJECTED |
| N2 | Liquidity-sweep reversal (ICT) | 1h | 2268 / -0.38 | 1520 / -0.42 | -0.56 | 242R | REJECTED |
| N3 | Turtle 20/10 L/S | 1d | 77 / +0.44 | 48 / -0.09 | +0.22 | 21R | REJECTED (holdout) |
| N4 | Asia-range session breakout | 1h | 2039 / -0.22 | 1562 / -0.25 | -0.36 | 201R | REJECTED |
| N5 | Flag continuation | 1h | 295 / -0.14 | 206 / -0.09 | -0.19 | 29R | REJECTED |
| N6 | **BTC-lead alt catch-up L/S** | 4h | 250 / +0.055 | 155 / +0.056 | +0.015 | 17R / 20R | **PASS, PAPER canary** |
| N7 | Capitulation / blow-off reversal | 1d | 60 / -0.16 | 29 / -0.13 | -0.16 | 14R | REJECTED |
| N8 | Squeeze breakdown short | 4h | 95 / +0.027 | 61 / +0.094 | +0.005 | 9R / 11R | PASS path, FAIL worst: forward ledger only |

**Robustness from the per-trade ledgers** (`docs/research/newstrat_cp/evidence.json`, by
`research/newstrat_cp_evidence.py`; R per trade at 15/23 bp = (pnl - turnover x 8/16 bp) / risk):

| N6 BTC-lead | path R@15 | worst R@15 | path R@23 | worst R@23 |
|---|---|---|---|---|
| all 405 / 412 trades | +0.055 | +0.008 | +0.015 | -0.033 |
| without SOL (best coin) | -0.004 | -0.049 | -0.046 | -0.091 |
| without top 5 trades | +0.032 | -0.016 | -0.009 | -0.057 |
| positive half-years (15 bp) | 5 / 10 | 6 / 10 | | |
| positive coins (15 bp) | 5 / 7 | 3 / 7 | | |

**Honest read: N6 passes the frozen gate but its edge is carried by SOL**; without SOL it is break-even at 15 bp and
negative under worst-path fills. It is a weak candidate. Recommendation: FWD-01 arm now; a $16 testnet canary only as a
labelled low-confidence experiment after OLD3, never scaled on this evidence.

**N6 detail.** Worst-path fills still pass (DEV +0.006R, HOLDOUT +0.011R at 15 bp; -0.033R at 23 bp). Years
2022..2026 sumR +3.4 / -1.7 / +21.9 / +12.2 / +2.9; 5 of 7 coins positive (SOL +26R, ETH +11R, XRP -7R); top five
trades are 26% of total R. DEV profit was longs, HOLDOUT profit was shorts. About 1.7 trades/week across the book, below
the 3/week sleeve activity target. Edge is thin: about +0.05R per trade at 15 bp.

**N8 robustness.** Without SOL: -0.021R path / -0.082R worst at 15 bp; without the top five trades -0.010R. Worst-path DEV R@15 is -0.077 (fails); top five trades are 62% of total R; 2026 is -6R. Forward
ledger arm only.

**Lessons.** The 1h ideas were negative even at 7 bp: no gross edge, not only costs. Wide-stop 4h ideas that ride
cross-asset information did best.

Registry: **not added to strategies.py.** `strategies.py` is byte-pinned by
`tests/test_causal_v4_runner_rebind.py`, and with PAPER on hold no live registry entry is needed. N6/N8 live only in
`research/newstrat_cp2.py` (frozen keys `cp_n6_btc_lead_catchup_4h`, `cp_n8_squeeze_breakdown_short_4h`) for FWD-01
replay. Promoting either to the engine later is its own ticket: two registry entries plus `btc_brk_up`/`btc_brk_dn` in
`build_context`, and a pin update that Codex has to approve. Tests: `tests/test_newstrat_cp.py` (causality, research_only,
absence from the shipped registry).
