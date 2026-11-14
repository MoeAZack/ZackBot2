# Strategy queue checkpoint — 14 November 2026

This supersedes outdated running/wait states in NEXT_PARALLEL_20261114.md while retaining its frozen research contracts. No account or scheduler changed.

- LONGSCOUT V1–V5: Claude reports 32 new candidate rules, no full passes. This is not a claim that Codex independently replayed all32. V5's two positive-mean cases received independent signal, eligibility, cash-sample and gate checks; both FAIL, no causality defect found within that bounded review. High-beta's README omits the additionally failed order-zero per-half gate; result JSON is correct.
- TREND-FRESH134-D01: completed max1 and max3. Both FAIL the frozen gates; no widening account4 EX13. Data/evidence in trend_fresh134_d01_20261114.
- BREADTH-FRESH134-D01 #348: completed max1 and max3, both FAIL. Codex independently reproduced all7,895 signals, all40 primary medians and37 order0 trades, plus10 cash samples. Secondary max3 not independently replayed. No wider canary.
- DAILY-TF-01 #343: PARKED in the active queue after the fresh economics failed. Preserve the bounded findings and earlier extra31 evidence; no further implementation, deployment or account changes. Claude acknowledged stopping the fix work.
- Build next: LONGSCOUT-V6, six4h rules at source acc8d0eea5fbc964d45d582923732e2bbb5ba322, cases freeze eb5f615c78660490e76cad97b8f09c2fffbe1e89. Regime flip L/S, dual-regime hold long, time-series momentum L/S, Heikin-Ashi trend L/S, lower-high breakdown short, RSI bull-range long. Universe165, consumed-calendar labels, unchanged rule-v1 costs/sample/ex-best/halves gates and separate positive subset requirement. No post-hoc tuning/rescue.

Targets, Cairo on14November: next batch results around11:00 (Claude estimates30–40minutes after the V6 launch); integration decision checkpoint14:00; earliest protected account4 canary target18:00 **only if** a candidate clears economics, independent signal/cash, account-fit and protection gates. These are work/deployment targets, not a claim or promise of profitable future trades. At this checkpoint no new replacement is qualified. A failed batch advances to the next distinct frozen batch; it does not wait for daily forward rows or trigger unrelated engine work.

Existing account4 EX13 exp_trend on31coins/max3 and EX04 H5 onoriginal9/max1 were observed enabled in local settings only. They were not changed or claimed exchange-verified healthy by this checkpoint. Account1 remains untouched.
