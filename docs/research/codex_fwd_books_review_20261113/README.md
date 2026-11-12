# FWD-BOOKS independent review — 13 Nov Cairo

Reviewed source freeze 8f83494b; manifest SHA256 389c466e3eca3fa99d0a477c7a87306536179602921b21d4bd46b1a01b395824.

## Verdict
Freeze/cutoff/input authority PASS. Research-only on-demand reporting is accepted with explicit four-hour collection lag; this is not an autonomous hourly reporting service and no account activation is accepted. Evaluator's out-of-order-input guard requires the bounded correction below before unconditional acceptance. Keep original T, rules and economic parameters; publish a hash-pinned amendment for guard changes, not a fresh selection window.

T = 13 Nov 00:00 Cairo (12 Nov 22:00 UTC), strictly after freeze 12 Nov 23:36:44.869 Cairo (21:36:44.869 UTC). All ten frozen source hashes match. Canonical manifest c11ed2566727c38d0d9236d7290a01aabb4a53a0f3b15388ea8fed7ba8909351 and rules d1ef2a9c60db9858774d68903648c334ca807a66c23dc85cce13fd8add96d2af match the independently checked input authority.

Across nine coins: 29 canonical/native 4h overlaps each (261 total), 79 complete FWD 1h-to-4h groups each (711 total), zero disagreements. Snapshot is complete ONLY through 12 Nov 22:00 Cairo (20:00 UTC), not through T. All cached inputs ordered and duplicate-free. Raw source file hashes saved in data_audit.json; mutable cache contents were not copied. Warmup 316 hourly / 4572 four-hour bars from the separately checked preflight.

Six frozen synthetic tests pass. Additional reviewer-selected boundary probes: H5 signal at T-1h and EMA at T-2h produce zero native admissions and zero diagnostic post-T signals. The suspected pre-T signal leak is NOT reproduced: native run selects signal indices >= t0 and schedules fills on the next bar. No cutoff defect is asserted.

## Confirmed guard defect
Reversed hourly frames and same-source duplicate bars both yield coverage COMPLETE (independent_repro.json). klines_frame does not validate monotonic uniqueness, coverage uses sets, and merge_sources sorts. This contradicts freeze.incomplete. Current data is clean, so no demonstrated economic corruption. Claude: add strict per-source ascending/unique timestamps before merge/evaluation, map violations to INCOMPLETE with exact symbol/time, add reversed/duplicate hostile cases through public loader/evaluator, and preserve legitimate matching overlap across the two sources. Do not change economic definitions, T, accounts or workers.

## Reporting decision
Accept delayed on-demand research snapshots only. run_books.py has no scheduled caller; four-hour input refresh does not constitute hourly automatic reporting. First planned evaluation is after 13 Nov 02:00 Cairo (00:00 UTC) refresh. EMA's first eligible 4h bar opens then and closes 06:00 Cairo (04:00 UTC); earlier EMA rows must be labelled baseline/no eligible closed bar, not an economic observation. First post-T data/row is not yet independently verified. Do not add a scheduler or change workers under this review. Report the invocation/next-row responsibility explicitly; absence of a caller remains an operational gap for autonomous daily evidence.

## H5 runner diagnosis (read only)
H5-HOURLY-FWD-20261112 is stalled, not healthy. Cursor is still boundary 12 Nov 22:00 Cairo (20:00 UTC), previous 21:00 Cairo row hash remains valid. ADA file ends at 20:00 Cairo (18:00 UTC) and matches old cursor hash; eight others end 21:00 Cairo (19:00 UTC) and no longer match old cursor hashes. The attempted 22:00 Cairo row is INCOMPLETE, with ADA SSLError and missing_symbol_data. posts.jsonl has no record for that row; last recorded post is 21:00 Cairo boundary.

Source explains the lock: concurrent fetches overwrite shared data files before evaluate/post/cursor commit. After partial fetch or failed publication, validate_cursor compares the next attempt against old whole-file hashes and raises cursor_data_changed before retrying. This evidence is consistent with a failed attempt, not proof of historical candle revision. Exact original publication failure is not retained in the current heartbeat; do not invent it. Heartbeat at 12 Nov 23:58:59 Cairo was ERROR cursor_data_changed.

Bounded repair proposal (NOT deployed): stage immutable per-boundary inputs and result, make publication idempotent by marker reconciliation, then atomically commit a cursor referencing that boundary snapshot. Reconcile the orphan incomplete attempt explicitly. Do not blindly reset hashes/cursor, silently rewrite published evidence, or report missing periods as zero. Isolated reproduction should prove recovery from partial symbol fetch and post/cursor interruption before deployment.

No accounts, credentials, workers, cursors, schedulers, runtime settings or activation changed. Only isolated research copies/tests and this review evidence were written. No strategy has become qualified from this review.
