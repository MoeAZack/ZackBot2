# DAILY-TF-01 independent review

Head 7cc838c6e0be16a5d7e4be4934573d2b7bfbf503; base 9cb4341f2d94c21434492515b8e85f2b42c148ce; master comparison 42ef787f. Decision: changes required before activation. Read-only source review; no runtime, account, scheduler, full suite, merge, or activation action.

1. panel_of compares only panel timestamps with each other and the traded frame. A uniformly stale complete panel passes. Engine.candles filters unclosed rows but returns stale rows, so a delayed or manual cycle can act on an old thrust. Require the expected last closed UTC daily open at decision time; test a complete uniformly stale signal-bearing panel. Pass a cutoff explicitly to preserve historical parity evaluation.
2. validate_sleeve accepts any nonempty subset of the panel, but cycle loads only sleeve symbols plus BTC and lots. Unless another sleeve happens to supply all panel coins, a subset produces no signals. Load the full panel independently from trade eligibility, or require the full list; test actual cycle wiring and missing exchange symbols.
3. Registration is default-off and has no production caller. Ordinary restart cannot select this strategy without separate testnet-bound registration and packaging integration. This is a remaining activation gate.

Parity: breadth, transition, EMA21 filter, params and management agree with 646ab3fa research/newstrat_cp3.py for an equivalent complete panel. Historical gaps can alter the denominator and EMA; data continuity and authority require proof before canary. Existing tests copy the reference formula and use resampled local candles. No native tests were run in this review.

Repin decision: ACCEPT narrowly for this head, contingent on final-head gates. strategies.py only adds shared panel_c to build_context; existing strategy functions and context expressions are unchanged. This adds resource cost and a potential failure surface, so targeted non-breadth context regression and the full suite on the accepted merged head remain required.

Scheduling: TF_SEC includes 86400; app loop derives active timeframes and schedules epoch-aligned daily boundaries with 15-second delay; candle code filters unclosed exchange rows. Fresh complete panel and scheduler catch-up need tests. merge-tree against 42ef787f shows no textual conflict markers; engine.py changed on both sides and the merged behavior needs review and exact-head tests.

Activation gates: fix and independently test panel defects; verify testnet origin, startup registration, packaging and restart; verify panel availability and historical continuity; realistic costs, funding and exchange rules; independent economic results and selection ledger; bounded protected cohort and rollback; exact merged-head full suite. No profitability claim.
