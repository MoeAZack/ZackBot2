# NEWSTRAT-CP3: account-4 scalp replacements, ten candidates, no clean survivor (2026-11-12)

Source: Claude Projects. research_only, default-OFF, not added to `strategies.py`. Rules frozen and pushed before each
run: wave 3a (X1-X6) at `46ece2b7`, wave 3b (Y1-Y4) at `e69f76ed` (`research/newstrat_cp3.py`). Runner
`research/newstrat_cp3_run.py` = PR #334's runner with account-4 sizing. One pre-run design fix (Y1 trend filter
EMA50 -> EMA200, made after seeing only signal counts, about 2 per coin in 5 years) is recorded in the file.

**Setup.** Unchanged `BT.run` (master `7b5beecb`). 8 coins with multi-year files (BTC ETH XRP SOL DOGE BNB LINK AVAX; no
long ADA file). $4,000, risk 0.002 ($8/entry), **1 position per sleeve**, testnet exchange filters, funding 0.01%/8h per
bar. 4h/1d from 2021-12-19 to 2026-10-04. DEV < 2025-01-01, HOLDOUT >= 2025-01-01, no tuning. Costs 7 bp/side, stress
15/23 bp. Gate: >=30 closes per period; R@15 > 0 in both; R@23 > 0 overall; MaxDD <= 30R; median round-trip cost at 15 bp
<= 10% of R; R@15 > 0 without the best coin; worst-fill R@15 > 0 overall.

| ID | Candidate | tf | DEV n / R@15 | HOLD n / R@15 | R@23 all | worst-fill DEV / HOLD R@15 | cost/R | Verdict |
|---|---|---|---|---|---|---|---|---|
| X1 | Laggard short (bottom 2 by 28d) | 1d | 25 / +0.085 | 17 / -0.028 | +0.030 | same | 1.7% | REJECT (n, holdout) |
| X2 | **Alt/BTC ratio breakout L/S** | 4h | 265 / +0.002 | 143 / +0.114 | +0.011 | -0.017 / +0.029 | 5.3% | FAIL worst fills: forward only |
| X3 | Wide-range day continuation | 1d | 68 / +0.106 | 37 / -0.116 | +0.010 | +0.020 / -0.187 | 3.2% | REJECT (holdout) |
| X4 | Breakout then retest | 4h | 334 / -0.087 | 193 / -0.178 | -0.171 | | 8.6% | REJECT |
| X5 | NR7 daily breakout | 1d | 122 / -0.234 | 68 / -0.072 | -0.206 | | 5.1% | REJECT |
| X6 | 180-day extreme breakout | 1d | 21 / -0.023 | 14 / -0.115 | -0.070 | | 2.1% | REJECT |
| Y1 | Leader/laggard RSI pullback | 4h | 64 / -0.019 | 38 / +0.115 | -0.002 | | 5.6% | REJECT (dev) |
| Y2 | **Breadth thrust L/S** | 1d | 34 / +0.083 | 20 / +0.038 | +0.049 | +0.070 / -0.018 | 3.0% | FAIL n and best-coin: forward only |
| Y3 | Weekly relative momentum | 1d | 81 / -0.011 | 47 / +0.047 | -0.000 | | 1.8% | REJECT (dev) |
| Y4 | Volatility-feedback short | 1d | 17 / +0.113 | 9 / -0.126 | +0.019 | | 2.0% | REJECT (n, holdout) |

**Read.** Wide stops worked as intended: costs are 2-9% of R (the scalps paid about 35%). But no candidate shows a
reliable edge. X2 is the best: 408 trades, positive in both periods on normal fills, 15R max drawdown, but its DEV edge is
zero (+0.002R) and it turns slightly negative under worst fills. Y2 is positive on every cost level but has too few
trades (with one position per sleeve, the first coin, usually BTC, takes every thrust). Ten tries make one weak pass
likely by chance. Recommendation: no new account-4 sleeve; X2 and Y2 go to FWD-01 as forward-only arms.
