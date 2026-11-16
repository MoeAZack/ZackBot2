# Re-costing conventions frozen before funding economics

Owner instruction, 16 November 2026: continue #358 with an explicitly labelled nearest
public one-minute mark-price candle proxy for the four original missing LUNC settlement
marks, report timestamp gap/source/value, flag affected results and publish sensitivity.
The full-range retrieval found additional missing marks. Their candles may be acquired
as public source facts, but economic substitution beyond the original four remains
pending the owner's answer to the extension question. No omission or zero substitution.

Authorization amendment, 16 November 2026, before economic re-costing: the owner answered
"Yes, apply it to additional needed LUNC marks", then "Yes, extend the labelled proxy to
all required missing marks". The same disclosure, affected-result flags and OHLC bounds
therefore apply to every required missing settlement mark, across all symbols. This
supersedes the pending-extension restriction above; no strategy or timing rule changes.

All computation stays in the isolated lane-2 clone. Research only; no runtime, accounts,
keys, settings, schedulers, watchdogs, orders, installations, canary actions or mainnet.
No strategy rules, thresholds, universe orders, trade paths or historical sizing change.

1. Reconstruct pinned baseline fills and already-charged proxy funding, and require
   published trade/P&L parity before using a book. Lead snapshots are pinned at
   `4189908d08c559421bdfd270c02de3f8dd6d97e9`; each case retains its frozen source.
   PROT uses `ecaff93d272deca7fa80eb9decc1620c23286ed0`, whose executable source/results
   match the earlier `53753968` snapshot. Historical quantities and per-trade risk stay fixed.
2. Re-cost signed native funding cash `-side * quantity * mark * rate`. First undo
   the old proxy funding: `new_net = baseline_net + old_proxy_paid + native_cash`.
   Report both the delta against the published proxy-funded baseline and the native
   funding delta against reconstructed fee/slippage-only P&L.
3. Primary eligibility convention: ordinary taker entries and gap fills at candle open;
   signal/time exits at candle's last millisecond; other intrabar fills at candle open.
   Fills at the same timestamp execute before the funding event. Retain exact returned
   funding timestamps, including millisecond jitter. These are modelled candle fills,
   not measured exchange execution/eligibility. Never present them as actual paid funding.
4. Timing sensitivity: move uncertain intrabar fills to the candle's last millisecond;
   separately evaluate funding before equal-timestamp fills. Known-open entries/gap fills
   and known-close signal/time exits keep their primary time. The alternative scenarios
   quantify specified clock conventions; they are not a proof of every possible path.
5. For an approved missing mark, use the nearest public minute candle's open (for these
   boundary events this is their containing-minute open). Record symbol/event timestamp,
   candle timestamp, signed gap in milliseconds, value, full OHLC, source URL, archive or
   REST response hash, retrieval time and proxy label. Native settlement marks take precedence.
   A missing rate or missing candle remains INCOMPLETE; it is never filled with zero.
6. Proxy sensitivity: hold rates, quantities and primary eligibility fixed; replace every
   approved proxy value with its containing candle's low/high, taking the lower/higher
   cash contribution after applying signed exposure/rate. Sum these per-event extremes.
   This bounds the disclosed OHLC substitution, not an unobserved settlement price outside
   the candle range, and is separate from execution-timing sensitivity. Flag every job,
   strategy and order that touches a proxy, with a separate LUNC flag.
7. Retrieve the full required public event ranges with ascending, duplicate-free,
   exhaustively paginated REST responses. Keep hashes and query/range provenance.
   Audit returned cadence discontinuities rather than manufacturing a fixed 8h schedule;
   unresolved held-interval event/coverage gaps make affected results INCOMPLETE.
8. Preserve all 40 historical lead orders and both path scenarios, three cost levels,
   and the PROT published comparison matrix where reconstructed. Aggregate using the
   original per-order/period/mode statistics. Do not tune or rescue candidates on these
   results. Supporting multiple-testing/selection caveats remain unchanged.

ROADMAP FIT: cost-aware economics for the eight named leads and PROT-CMP-01.
DEFERRED: reselection/tuning, native-equity-feedback re-sizing, any strategy activation,
merge/readiness decisions and all operational work.
