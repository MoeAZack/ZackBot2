# Independent S16 and R17 review

14 November 2026. Research only. No frozen alpha, account, key, runtime, scheduler, installer, label, merge or PAPER action changed. R6-PAPER-01 remains the higher-priority canary preparation; [its exact-head review](https://github.com/MoeAZack/ZackBot2/pull/350#issuecomment-6875463622) requests bounded fixes before deployment acceptance.

## S16: implementation reproduced; record only for now

Head `d9bf7bf75bd274e47e72dc4fc33bf10d32802895`. Rule freeze `b9ac5a4064b0c6cff2738503aadf5fec069973c8`; cases `fab1040e9efe0cc00edeee4d6f3290ec4d49b9fe`.

An independent reconstruction of the rule, daily availability, ATR, liquidity and native-capacity cash path imports neither the submitted alpha nor native execution engine. All 165 input files match their pinned hashes; their complete 4h aggregations have no gaps.

- **7,731 / 7,731** published raw signal tuples match; 3,758 eligible; zero missing or extra.
- **223 / 223** order-zero closed trades match symbol, side, entry/exit indices and exit reason. Maximum P&L15 difference `8.9e-14` dollars; maximum turnover difference `2.3e-13`.
- Reproduced order-zero total **+10.70686954 R15**, mean **+0.04801287 R15**, 113 early / 110 later trades, closed-equity drawdown **8.81775002R15**. These are the frozen proxy costs, not observed exchange profitability or marked-account drawdown.
- Thirty-six historical prefix checks have zero differences. The inside-day information becomes available on the 20:00 UTC 4h bar as it closes at midnight UTC, not at that bar's open. The previous calendar day is required to construct an inside day.
- Synthetic caveat: when the next day is incomplete, the helper drops it and backward-maps the older inside-day flag/level. This is stale context, not future information. It has **no observed impact on the pinned, gap-free inputs**. Keep this caveat in the record and require an explicit fail-closed data/admission guard before any later live proposal; do not silently edit the frozen alpha now.

Scope: the requested order-zero economics and full signal set were independently rebuilt. The submitted 40-order medians, worst-path and ex-top3/ex-top5 figures were not independently re-simulated by this S16 check. Keep them attributed to Claude.

Decision: preserve **WIDE165_PASS** as the disclosed, selected consumed-calendar screen result; **no PAPER / no advancement now**. The thin fresh-subset margin and disclosed negative ex-top3 result do not demonstrate a robust replacement after 83 candidates. These are descriptive reasons to prioritize, not newly imposed backtest gates. Do not tune or erase the pass, and do not call it established profitability.

## R17: full economic screen independently reproduced; related to R6, not identical

Head `b6f82edb0be9a134aefb74ec1b58366d269628f4`. Rule freeze `6e3f37531aeedf2316cb82e492dfe0955d86885e`; cases `0f8d474cecc163e9968705e26e79b42167befba9`.

Codex adapted its own previously accepted independent cash simulator with a separately written EMA50-envelope signal and 2.5 ATR stop. It imports no submitted strategy, runner or native execution engine. All **166** original input files (BTC timeline plus the 165 traded coins) match their pinned hashes.

- **30,486 / 30,486** raw tuples match; **17,983 eligible**, zero missing / extra.
- All **40** symbol-order paths independently replayed and matched the supplied count and six primary economic statistics to numerical tolerance.
- All **269 / 269** order-zero closed trades match; maximum P&L15 difference `1.42e-14` dollars. Quantity, price, fees, funding, floors, capacity and cash were calculated independently.
- Reproduced medians: 280.5 closes, R15 **+0.11739546**, R23 **+0.09711219**, early **+0.05849093**, later **+0.17559026**, ex-best **+0.06317043**, fresh134 **+0.01446932**, extra31 **+0.31434346**.
- All 40 orders retain an open lot at the terminal boundary. Separately estimated terminal liquidation marks at 15bp range from **-$2.92 to +$15.02**; closed-trade statistics exclude them. This is not a completed live account return.

### R6 / R17 overlap

Both rules were rebuilt from the same pinned inputs. Of 3,722 eligible R6 and 17,983 eligible R17 tuples, **246** share the exact symbol, decision candle and side. Jaccard is **1.146%** (246 / 21,459). Thus the two alphas are not the same entry algorithm; R17 can enter repeatedly inside a regime while R6 enters its first state transition, and their stop/exit behavior differs.

Descriptive comparison of the separately reconciled closed-lot journals across 40 orders:

- Median **6.16% of R17 entries** occur while R6 holds that same coin/side.
- Median **6.83% of R17 held bars** coincide with R6 holding the same coin/side.
- When both books hold a lot, their sides agree **70.69%** of the time, including positions on different coins.

Holding intervals include entry/exit bars; terminal open lots are excluded. These figures describe standalone journals, not a jointly simulated account, covariance test or proof of portfolio diversification. The 70.69% statistic alone is not a P&L correlation estimate.

Decision: **accept the independently reproduced WIDE165_PASS as a dependent research lead; no PAPER.** R6 and R17 are related regime/trend exposure, but rejecting R17 as literally identical would be inaccurate. Keep R17 behind R6 rather than deploy both now. The small fresh134 edge, negative disclosed ex-top3 and repeated consumed-calendar selection mean it is not yet an independently diversified profitable replacement. Preserve the frozen rule. A later proposal would need actual-cost/venue fit and a preregistered paired forward comparison showing incremental economics and shared-risk behavior; no such work or activation is authorized by this review.

## Earlier failure labels and next priority

The earlier V7–V12 label check reconciles **28 FAIL cases**, 1,120 orderings and 250,357 supplied path-trade rows. It is independent count/cost/half/ex-best arithmetic on the supplied tapes, not another signal/execution reproduction. Keep all failures in the trial ledger.

Do not make another price-pattern round the prerequisite for R6. NOW is the bounded R6 fixes, exact-head checks and workload/protection proof. Existing capacity research is independent. The next distinct economics question is realized signed funding sensitivity versus the fixed proxy, followed by a frozen future comparison; preserve the current alpha and do not turn these consumed-calendar leads into live-profit claims.

ROADMAP FIT: strategy-economic verification and bounded R6 PAPER preparation. DEFERRED: S16/R17 activation, new parameters, general engine work, new schedulers and mainnet.
