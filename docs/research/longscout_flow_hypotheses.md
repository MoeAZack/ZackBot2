# New public flow fields: prospective hypothesis plan

Fixed before full feature acquisition and before any candidate outcome. This
plan adds information, not a parameter rescue for an earlier losing rule. The
price calendar is still consumed; new feature access does not make it untouched.
Candidate source/cases will be frozen and separately counted before execution.
No trial may be omitted, and neither rule can run unless all166 symbol/day
comparisons in the new data release pass. No failed coin may be silently dropped.

Public-only daily USD-M klines provide trade count and taker-buy quote volume;
the existing hourly input cache does not retain those columns. Three BTC days
were probed for feasibility, matching the frozen daily OHLC/base/quote aggregates.
That probe is not an economic test. Official field documentation:
https://developers.binance.com/en/docs/catalog/core-trading-derivatives-trading-usd-s-m-futures/api/rest-api/market-data

All dates/symbols come solely from the already pinned166-file input audit. Use
only fully completed UTC days with all24 original hourly bars. Save raw public
responses and compact feature CSVs in the new dedicated flow folder, reconcile
every candle/volume to the old cache, and record raw/CSV hashes and all failures.
The acquisition helper is single-threaded, paced>=1s/request, capped500requests,
stops on418/429 and at23Nov16:00 Africa/Cairo, and never accesses an account or
uses authentication. It refuses an existing output. No shared input is modified.

## Planned V98 daily candidates

I_j=2*taker_buy_quote_volume_j/quote_volume_j-1. Counts must be positive and
quote volume positive for a candidate day. Require22 complete consecutive days
with valid flow inputs; coin regime R1 uses only the completed daily close.
First full statistical condition includes readiness; apply R1 afterward.

L98 passive absorption long: I_j<=-0.20 despite C_j/O_j-1>=+0.01; prior completed
day did not satisfy the full condition; coin R1 rising or sideways. The proposed
mechanism is price holding up under aggressive sales, not a claim that observed
volume identifies investors. Daily signal at day close, next-day-open modeled
entry; stop2 dailyATR, target2R,7daily bars maximum.

R98 trade-fragmentation range: A_j=quote_volume_j/trade_count_j; A_j<=0.5 times
the median A of prior20 days; quote_volume_j>=2 times prior20-day median volume;
abs(C_j/C_j-1-1)>=0.05. First full condition, coin R1 sideways; fade the day's
direction (negative day->long, positive->short). Same daily2ATR/2R/7bar management.
Average trade size is a fragmentation proxy, not proof of retail ownership.

Both defaultOFF/research-only, unchanged40orders/rule-v1/fresh134+extra31 gates,
15bp fees PLUS2bp modeled slip/side, native fixed funding and frozen exchange
feasibility. No activation, accounts/runtime/scheduler/LEADFWD/merge/mark-ready.
DD labels remain Standard<=15R,Elevated<=30R,High<=60R,Extreme>60R.

These are plans, not passes or executed trials. If data cannot reconcile in full,
record the precise data blocker and continue the untouched OHLCV search queue.
