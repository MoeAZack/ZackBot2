# DATA collector independent review — changes requested

Reviewed PR #19 at exact implementation head `01ad9534d0cf053d58822064ecdf45dcbc54a288` on 2026-10-07 (Africa/Cairo).

## Verdict

**CHANGES REQUESTED: 2 P1 findings.** The standalone collector is sensibly bounded and the focused Windows suite passes, but the in-app transport is not yet isolated from trading health and the live Binance payload proves that one requested field is silently lost. Do not install the in-app collector or start the recurring task from this head. The public testnet snapshot check is safe and passed.

## Findings

### P1 — Background collection mutates the trading engine's shared outage circuit

Affected: `market_collector.py:FuturesTransport`, `engine.py:Engine.__init__`, `binance_client.py:Futures._req`, and the test `test_in_app_429_through_the_real_client_backs_off_and_respects_the_engine_circuit`.

`MarketCollector` wraps `e.data` itself. Every collector request therefore calls `e.data._req()`, whose read path calls `health.fail()` on 418/429/5xx/network failures and `health.ok()` on success. The new test explicitly proves a collector 429 changes the engine circuit to `degraded`. Successful collector reads can also clear that shared state. This contradicts the PR's “no impact on trading paths” safety claim: a background research request can suppress, delay, or mask engine market-data reads.

Fix: give the collector an independent keyless transport/client and independent rate/outage state. It may read the engine circuit as a one-way gate, but it must never drive or clear it. Add deterministic tests proving collector 429, 418, 5xx and success leave `e.data.health` byte-for-byte/state-for-state unchanged while the pre-existing engine outage still prevents collector traffic.

### P1 — Real Binance open-interest circulating supply is silently discarded

Affected: `market_data.py:DATASETS['open_interest_hist']` and its coverage.

The schema requests `CMCirculatingSupply`, but the public Binance response at review time contains `CMCCirculatingSupply` (two Cs after `CMC`). `normalise()` consequently writes an empty value for every row. A direct public-mainnet request made during review confirmed the live key and returned valid values such as `20094521.00000000` which this implementation would lose.

Fix: store the exact Binance field name (or deliberately normalize it to a documented internal name while reading the exact source key). Add a fixture copied from the real response and assert the value survives into the CSV. The first full collection should run only after this is fixed so the initial dataset is correct without needing a repair pass.

## Evidence

- Windows focused suite: `36 passed in 5.57s`.
- Public testnet `exchangeInfo` capture: exit 0; 741 raw symbols; no credentials used.
- Public mainnet `openInterestHist` sample: HTTP success; the payload contained `CMCCirculatingSupply`, proving the schema mismatch.
- GitHub fast verification and CodeQL were green at the reviewed head.
- No installer, app restart, order action, recurring scheduled task, or full historical collection was performed.

## Re-review gate

Return a new exact commit with both fixes and focused regression tests. Then run the focused Windows suite, a two-symbol real one-shot collection, verify non-empty circulating-supply values and manifest counts, and only after acceptance start the recurring collector.

## Fix re-review — code accepted, runtime gate pending

Re-reviewed Claude fix commit `1fdb6eb0b31e04aac1f1e2dc19ef2966ae71cf3c` on 2026-10-07 (Africa/Cairo).

Both P1 findings are fixed in code:

- The in-app collector now owns a keyless `RequestsTransport` and independent request-health counters. It reads the engine circuit only as a gate and no longer calls or routes through the engine client.
- The schema uses Binance's exact `CMCCirculatingSupply` key and real-shaped regression coverage proves it reaches CSV output.

Independent Windows evidence:

- Focused collector suite: **50 passed**.
- Related outage/startup suites: **63 passed**.
- Real public mainnet two-symbol collection: **172,061 new rows**, 142 requests, 0 errors in 71.2 seconds.
- BTC 1h open-interest CSV: **719 rows**, 719 unique timestamps, 0 blank circulating-supply values; manifest also reports 719.
- Immediate repeat: **0 new rows**, 12 requests, 0 errors in 5.1 seconds.

Code verdict: **PASS**. Final acceptance remains gated on the requested isolated in-app PAPER runtime check at this exact implementation commit; do not start the permanent recurring scheduled task until that evidence returns.
