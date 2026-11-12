# N6 raw-set correction — 12 Nov 2026

The earlier independent implementation omitted the frozen rule's alt close-versus-EMA50 filter: long close > EMA50, short close < EMA50. That made its raw set too broad. Frozen source research/newstrat_cp2.py at b75fa28bdd4786688659bee13f4047c92f91aa12 explicitly includes these predicates. This corrects the prior count; original evidence is preserved rather than overwritten.

After the same 220-candle warmup: old independent1260, frozen1142, intersection1142, old-only118, frozen-only0. Every old-only signal fails close-versus-EMA50. BTC first-cross and rolling-level edge agree after the rolling window exists; strict alt-range predicates do not explain the excess on these inputs.

The frozen implementation's full-file raw set has1156 signals, including14 before the comparable warmup. Comparing1260 after warmup with1156 before warmup gives104, but the like-for-like discrepancy is118.

Corrected independent raw set:1142. Against the original405-filled-trade ledger:405 matched,0missing,737extra raw signals. This is signal eligibility, NOT independent execution/portfolio or profitability parity. SHA-verified original candles and reference ledger; corrected entries use next-candle open timestamps. Difference CSV uses DECISION candle-open UTC; entry timestamp is4h later.

N6 remains FORWARD_ONLY. Current PR334 head2fba3ff06ac7c2e23823d91a963385c6c6f6010b is not accepted by this correction. No runtime or account changed.