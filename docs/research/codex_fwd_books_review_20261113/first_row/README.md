# First-row independent check

PASS: research observation only, no qualification or activation.

Reviewed exact artifact head ba720df44c90999479c8c47e6fb593c492195393. GitHub compare to accepted A1 4a349fe9808687f863563ddcdcc979df29c381d4 lists only row.json and receipts.json; evaluator and manifests unchanged.

Row SHA256 08c8aec9a4137bc40a7bb1ae642ca8c6e0368f8254aaad1403b745b773fd509c.
Receipts SHA256 02ac2b46c9f53886decad18618c5faa1f8ef2746d9f6a478828415ab5df29b05.

All 18 original FWD-01 file hashes match the published row. Loaded via accepted A1 read-only loader, then evaluated at the exact boundary: every row field matches semantically (excluding only separately verified publication metadata), and every receipt matches. No source, worker, account or scheduler changed.

Boundary 13 Nov 02:00 Cairo (00:00 UTC); original T 13 Nov 00:00 Cairo (12 Nov 22:00 UTC). Coverage complete, no order violations. H5 has two closed scored candles per coin (nine coins), no signals. Reviewer separately computed all seven H5 Boolean conditions directly from OHLCV using the frozen EMA/ATR conventions, without calling H5.signal; detailed predicate values are in independent_row_check.json. All 12 H5 cost/path/order cases: no admissions/closes, $4000 cash/marked equity, zero cash growth/open mark/fees/funding, no NONPARITY or guard changes. All 24 receipt lists empty. Alphabetical primary versus cohort diagnostic flags correct.

EMA has no eligible closed bar at this boundary; all 12 cases are explicitly baseline_flat. Its first scored candle closes 13 Nov 06:00 Cairo (04:00 UTC). This row is not a zero-signal EMA outcome and is not profitable-strategy evidence.

Next planned Claude invocation 13 Nov 06:25 Cairo (04:25 UTC), dependent on actual FWD-01 refresh. No promise of an automatic reporting service. Separate H5 worker stall not repaired by this review.
