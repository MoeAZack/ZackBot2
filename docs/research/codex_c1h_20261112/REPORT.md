# C1H results — 12 Nov2026 12:50 Cairo

**84/84 cases completed, zero run errors, full bar coverage, no liquidations or unknown-symbol fallback. No candidate passes the frozen economic screen. No account changed.**

Same frozen source7b5beecb; six long/short hourly configurations, explicit executed-fill/quantity reconciliation. First six adverse15bp results posted12:48 Cairo. The nine-coin full matrix and eight-coin longer history completed12:50:15 Cairo in122seconds with2low-priority workers.

## Nine-coin hourly results

Mean net R per closed trade; costs per EXECUTED SIDE. Gross excludes fees/slippage/funding. Costed runs include stated both-side .01%/8h funding stress proxy. No native funding claim.

|Candidate|Gross|7bp normal|15bp normal|15bp adverse|23bp normal|Closes/week at15 adverse|
|---|---:|---:|---:|---:|---:|---:|
|c1h_h1_ema_pullback|+0.008|-0.087|-0.175|-0.321|-0.272|9.38|
|c1h_h2_compression_breakout|+0.069|-0.031|-0.128|-0.275|-0.213|9.54|
|c1h_h3_keltner_reclaim|+0.018|-0.069|-0.163|-0.257|-0.260|3.56|
|c1h_h4_rsi2_reset|-0.027|-0.104|-0.190|-0.307|-0.280|10.73|
|c1h_h5_volume_impulse|+0.071|-0.004|-0.077|-0.183|-0.151|6.80|
|c1h_h6_btc_relative|+0.058|-0.011|-0.084|-0.224|-0.153|8.54|

Every configuration also loses at7bp with normal fills on the longer8coin record. Increasing frequency alone did not establish alpha. H5 volume impulse is closest to zero at7bp, still negative on both datasets and materially negative at15bp; it is not selected or promoted.

The shared protection rule is a possible contributor, not proof of the cause. A separately frozen matched FIXED-STOP exit-control matrix began12:52 Cairo, using exactly the same entry signals/base stop distances/targets/timeouts with prot disabled. All84 additional controls are retained as diagnostic variants in consumedDEV, not relabelled independent discoveries or holdout validation. Original failures remain recorded.

## Limits and disposition

All historical windows are consumedDEV and a survivor universe. Earlier/later2025 grouping is descriptive (by trade exit time), not a newly untouched test. Nine-coin hourly input uses only complete4x15m groups; longer8coin dataset lacksADA. Equity-based sizing compounds rather than exactly mirroring account4 fixed$8 sizing; account-fit gates remain required for any eventual candidate. These tests are solo sleeves; aggregate frequency and shared-account collision behavior cannot be inferred by adding rates. Funding stress is not observed native funding. Marked DD includes open positions; cumulative closed-R drawdown differs. No hypothesis was tuned or discarded from the ledger after observing results.

**Decision: reject all six original protected configurations for account4 activation.** Continue the separately frozen management-control diagnostic and Claude separate preregistered hourly candidates. No activation date can be honestly promised before an economic-positive candidate exists and passes independent causal/execution/account-fit/protection checks. No wait for20Nov imposed.

Source freeze51cb51aa245d9bd3dd059e7bd16d8c197bad1036. Full raw case outputs retained locallyF:/ZackBot2_data/C1H-20261112/results; summaryCSV and exact-byte frozen code archive accompany this report.