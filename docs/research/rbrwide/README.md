# RBR-WIDE results (research only)

**Run:**
- Frozen head: 2429b3db.
- Hashes: runner d23f5eb7, contract 6681e766, rule source ddaee353 (strategies.py at 646ecc79), harness 8251f9b3, input manifest 99af9bfb.
- Full result: `rbrwide_result.json`.

**Verdict:** every arm fails. The rule is the unchanged rank_breadth_reversal_4h, short side only.

Figures are causal medians across the 40 trade orders, in R per trade at 15 bp per side.

| Arm | Role | R@15 | Gross (order 0) | Verdict |
|---|---|---|---|---|
| complement23 | primary | -0.140 | -0.058R | FAIL |
| core17_control | control | -0.096 | -0.034R | FAIL |
| union40 | diagnostic | see JSON | | FAIL |

**Gap with Stage-A:** in the Stage-A window (2024-10-14..2025-04-01), the control subset is negative. The prior Stage-A result was +0.096R on 76 closes. The gap is unexplained and has not been verified.

Known differences from Stage-A:
- 17 coins instead of 20 (HYPE, LSK and PUMP are missing).
- Ranks and breadth are computed over those 17 coins.
- 1 global position at a time.
- Fixed $8 risk per trade.
- Funding uses a stress proxy, not actual signed funding.
- 4h bars are built from 1h data.

No PAPER-review eligibility. No re-specification.
