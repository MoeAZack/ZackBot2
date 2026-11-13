# FWD-BOOKS row 2 exact-artifact review

PASS at artifact head 68f2c34a5886ba12b0e1ee828811b85259ee0e1e. Comparison with accepted A1 4a349fe9 lists only the two published row directories: no evaluator/manifest changes.

Boundary 13 Nov 06:00 Cairo (04:00 UTC), original T 13 Nov 00:00 Cairo (12 Nov 22:00 UTC).
Row SHA256 9c2eeebd4bf0581094f13e57c61f02af4525b0df3e4c3ea62f6199063dedfba5.
Receipts SHA256 02ac2b46c9f53886decad18618c5faa1f8ef2746d9f6a478828415ab5df29b05.
All 18 input byte hashes match. Re-loading via accepted A1 and evaluating at the exact boundary reproduces every economic/coverage/normalized-input field and all 24 empty receipt lists exactly. Complete coverage, no order violations, correct primary/diagnostic labels, no admissions/closes, no NONPARITY/guard changes, $4000 cash and equity, zero cash growth/open mark/fees/funding in all 24 cases.

H5: 6 scored hourly candles per coin, nine coins. Separately written seven-condition OHLCV/ATR/EMA calculation confirms no signals on all 54 coin-candles. EMA: 1 scored 4h candle per coin; all nine have no qualifying EMA20/EMA50 crossover plus close-above-EMA200, independently calculating those necessary entry conditions. Hence no Supertrend result can create an entry. This is a genuine EMA observation, not baseline_flat. Detailed per-coin predicates saved in JSON.

Cadence accepted: one Claude-invoked cumulative research row after the 02:00 Cairo (00:00 UTC) daily refresh, planned about 02:25 Cairo, through the 20 Nov review. No requirement to publish every 4h. Continue all frozen cases and process every intervening hourly/4h candle and guard event, with full receipts, not one daily signal sample. Preserve published artifacts and input provenance; any changed historical prefix or missing coverage must be flagged rather than silently rewritten or filled. Earlier-boundary recomputation is conditional on preserved input prefixes; hashes alone cannot recreate lost/revised raw data. No new worker/scheduler. Material failures or NONPARITY found during a run are reported promptly, not held for a later daily summary.

This is still zero-trade research evidence, not verified profitability or activation. No account/key/worker/cursor/scheduler/runtime changes. Separate H5 worker stall is not repaired by this review.
