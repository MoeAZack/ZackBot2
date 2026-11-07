# ATR-SHOCK-SNAPBACK-4H-CAUSAL-V3

Status: **FROZEN / UNEVALUATED / RESEARCH ONLY**
Authority: `atr_shock_snapback_4h_causal_v3_contract.v1.json`; this note cannot weaken it.

Candidate 13 keeps the exact `1a24edf4` Stage-A hypothesis, parameters, two separate long/short cells, 4h timeframe,
12-bar management cap and same-candle re-entry lock. The only identity change binds the cells to checksum-verified weekly
PIT CORE-20 membership, causal fold-start Binance filters, next-open discretionary fills, the frozen event timeline,
realistic fee/slippage/funding accounting and one isolated marked-equity book per side. There is no tuning, symbol
selection, side pooling, outcome reuse or authority to read the viewed holdout.

Sparse missing data is harmless only while flat and non-signalling. Indicators restart after every canonical gap and no
input may be imputed or learned from future coverage. Once a signal schedules an entry, every next open, filter, fill,
bar, mark, funding settlement, protection input and exit must be causal and checksum-bound through close; otherwise the
entire side is `INCOMPLETE_CANDIDATE`, never a skipped trade or reduced denominator.

All discretionary entry, mean-touch/trend-invalidation and time exits fill at the next tradable open. At an open, the
fixed order is earlier funding, marks/Cairo roll, due exits, re-entry locks, PIT-ordered entries, exact-time funding, then
fill-bar intrabar protection. Every fill pays 5 bp taker fee and 2 bp adverse slippage; actual signed Binance funding is
settled over `entry <= t < exit`. Full 1x/1.5x/2x books must be replayed rather than repricing one trade list.

The append-only family ledger has four trials: prior Stage-A long and short plus causal-v3 long and short. Unavailable
prior p-values conservatively enter Holm as 1.0. The contract freezes UTC exit-day sign-flip and clustered bootstrap
units, PCG64 seeds, resample counts, tie rules and empty-sample behavior; these remain diagnostics, not promotion gates.

Episode R uses actual filled initial stop risk after leverage and exchange-step flooring. Exact-time cashflows are summed
atomically for fold-local R drawdown, marked-equity drawdown is separately disclosed, and symbol concentration uses
absolute per-symbol net-R contribution. Entry sizing freezes marked equity, gross notional and leverage-cap formulas.
Min-quantity/notional failures skip rather than round up, max-quantity failures refuse rather than clip, and a zero-step
TP1 sends nothing and remains retryable without dust conversion.

Each side independently needs positive aggregate net and mean R, PF above 1.05, positive pre-cost gross expectancy, at
least 160 closes over 8 symbols, 2/3 positive folds, a UTC monthly median of at least 5 including zero-close months,
drawdown at most 15R, at least 15 closes in each frozen BTC/EMA200 regime with neither regime mean below -0.05R, symbol
absolute contribution at most 35%, positive net R at 1.5x costs, complete reports, causal parity and independent review.
Inference is diagnostic only. All four trials remain in the full ledger; failure is parked without rescue.

Nothing at or after `2026-09-28T00:00:00Z` may be read. This contract grants no runtime or mainnet authority.
