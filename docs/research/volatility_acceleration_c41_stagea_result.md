# Candidate 41 / volatility acceleration long — Stage-A result

Verdict: **HISTORICAL STAGE-A REJECTION; RECLASSIFIED TO HIGH-DD EXTENDED VALIDATION**. Research-only/default-OFF;
no PAPER/runtime authority yet.

Owner ruling on 3 November 2026 removed drawdown as a stand-alone economic rejection. The frozen result below is not
rewritten: it failed the preregistered 15R gate. Under the prospective risk policy it advances to chronological,
independently reproduced validation with its 43.682092R drawdown disclosed and a maximum future initial PAPER risk
multiplier of 0.343x. See `PROFIT_AND_DRAWDOWN_PROMOTION_POLICY.md`.

- Frozen head: `65977175976a8291787d5734043c75fb361bcf0c`.
- Duplicate proof: before freezing, current tree and all branches/remotes/tags/history were searched for ATR ratio,
  fast/slow ATR, volatility acceleration, range term structure and volatility-regime crossover code. No tested
  strategy/prereg/result/packet matched; existing range/squeeze/shock cells use structurally different mechanisms.
- Artifact: `C:\Dev\ZackBot_cowork_data\volacc1h_c41\volatility-acceleration-long-dev.json`.
- Canonical SHA-256: `a530ab55e1ab12607bec6ae85323d699139e0d2490f251ec6b8984de2615f69b`.
- File SHA-256: `9ec03d496e58381b515d3a8c451d211549a5a7230e4c39cdecfd211062a0ff6c`.
- CORE-20 Stage A: 736 closes / 18 symbols; BTC regimes 542 above / 194 below EMA200.
- Gross $261.524235; fees $154.953793; funding $11.039582; net $95.530860 = 24.915752R.
- Mean 0.033853R; PF 1.052772; max DD 43.682092R; win rate 43.8859%.
- PASS: gross, mean R, PF >1.05, sample, breadth, and both regime counts.
- FAIL: max DD <=15R. This hard gate alone rejects the candidate; thresholds were not changed.

The sealed holdout remained unopened and the no-fold/no-rescue contract stopped evaluation here.
