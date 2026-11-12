# RSR-1H proposal: residual shock reversal on 1h (research only, NOT frozen, awaiting Codex GO)

Why this, now: every single-coin 1h TA family failed after costs (CP3/CP4/CP5, Codex H1-H6), and the cross-asset ideas with the most
gross edge (X2, H5) are trend/impulse-continuation. RBR (4h, 7-day rank, short only) was gross-negative. The untested gap is a
SHORT-horizon, MARKET-NEUTRALISED, both-sides reversal: a coin moves hard against the market for idiosyncratic reasons (liquidation
of one coin, news overreaction) and partly mean-reverts within hours. It is not a fixed pair (Codex lane), not funding/OI/positioning
(Codex lanes), not a market-wide flush (excluded by a market filter), not volume impulse (H5), not ratio breakout (X2), not rank breadth (RBR).

Rule (one setting, no search):
- Universe: canonical 40 (manifest 99af9bfb), D01 PIT liquidity per coin at the decision bar; BTC tradable.
- m_t = equal-weight mean 1h log return of the coins eligible at t. beta = OLS beta of the coin on m over the prior 168 h (shifted, >=120 obs).
  e_t = r_t - beta * m_t. sigma = std of e over the prior 168 h (shifted).
- Shock z = (e_{t-2} + e_{t-1} + e_t) / (sigma * sqrt 3). Long if z <= -3.0; short if z >= +3.0.
- Exclude market-wide moves: skip if |sum of m over the same 3 h| >= 1.5%.
- Entry next 1h open. Stop 3.0 x ATR1h(14) from the fill, target 2R, no partial, time exit at the first close >= entry + 12 h.
Execution: X2-WIDE harness conventions unchanged (fixed $8, max 1 global, 40 tie orders, 7/15/23 bp + funding proxy, causal 1h and
worst 4h paths, Cairo -$120 halt, current filters, terminal MTM). Decisions 2022-08-26 .. 2026-10-04, split 2025-01-01, both consumedDEV.
Gates (X2-WIDE set): closes >= 100 and >= 30 per period; R15 > 0 in both periods; R23 > 0; ex-best-coin R15 > 0; p10 R15 > 0; worst-path R15 > 0.
Report gross decomposition, by side, by coin, and frequency. Freeze contract/source/runner/tests before any count.
Risks stated up front: 1h stop of 3 ATR may leave costs at 10-20% of R; max-1 global caps frequency; all 40 coins consumed by earlier work.
