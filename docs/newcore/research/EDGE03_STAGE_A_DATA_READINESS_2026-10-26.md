# EDGE-03 Stage-A local-data readiness (2026-10-26)

Status: **DATA-BLOCKED — do not run Stage-A**.

This is an inventory and acquisition contract only. No EDGE-03 signal, trade,
return, or selection result was calculated.

## Frozen scope

- Universe: `BTCUSDT, ETHUSDT, ZECUSDT, SOLUSDT, XRPUSDT, NEARUSDT,
  DOGEUSDT, UNIUSDT, SUIUSDT` (`CORE-10-ELIGIBLE-9`).
- Required Stage-A interval: `2024-10-14T00:00:00Z` through the sealed
  `2026-09-28T00:00:00Z` boundary.
- Completeness gate: at least 8 of the 9 eligible symbols, with no silent
  substitution of a price proxy.
- Funding event ordering remains `(entry_ms, exit_ms]`.

## Candle inventory

The audited local candle cache at
`C:\Dev\ZackBot_cowork_data\candles_4h_core_research` is complete for all nine
eligible symbols. Its source manifest is
`C:\Dev\ZackBot_cowork_data\klines_4h_core10_research.json`
(`zb-klines-research/1`). Each eligible file has 4,504 unique on-grid 4-hour
rows, from `2024-09-07T08:00:00Z` (the 220-bar warm-up) through
`2026-09-27T20:00:00Z`, with no missing bars or duplicates.

| symbol | file SHA-256 |
|---|---|
| BTCUSDT | `f97d65b5690b11c8809aada183f2174a538dbae8eb41248c5f5d0ee66e605cd9` |
| ETHUSDT | `4dcd3502009cb5f485404e9c82e39f953c95b2ec0a3a5635e13ac26a0338bb86` |
| ZECUSDT | `6c5599b32f38770916a4a9a752435eca0b51cf69c941fdd9469f03bad9a790b2` |
| SOLUSDT | `1ad0f6960f9cd8fbcdb4883f8a8d7abba02c355093e32f293a95265cca35ac0e` |
| XRPUSDT | `ada4e1fe0412c81ca656001ce6425e886fda0442a6289ce65ff8d86326d6202e` |
| NEARUSDT | `b3587ec1e9d262fb12ac692d52026c1b06cdaaa84da9ee547b50b2583ced82b4` |
| DOGEUSDT | `e6a2f2fcc88cd1b8d3aff63f9ae36a9bb33f75e41b1fd3872401f0cc9411c53a` |
| UNIUSDT | `c914f0580b1f47386c91563849ded9252a5b44d8fe849e5080299be4ec2187c9` |
| SUIUSDT | `1dd46be48c2994f26a24cf55ecd7746fcbc50bad7cb87ca0758537ac1746fb7b` |

The cache deliberately stops before the sealed boundary. The current runner
also needs the next-open value at exactly `2026-09-28T00:00:00Z`; this value is
not in this audited cache and must be supplied as a separately audited terminal
open. A full boundary candle must not be exposed to the strategy merely to
obtain its open.

## Funding inventory

### Public archive rates (9/9, but no mark price)

`C:\Dev\ZackBot2_data\binance_um\um\monthly\fundingRate` contains 24 monthly
ZIPs per eligible symbol for October 2024 through September 2026. Within the
frozen interval, every symbol has 2,143 unique rows, from
`1728864000000` through `1790553600000`, inclusive. All nine timestamp arrays
are identical (calendar SHA-256
`216ff3cd44e86528f36866379453f900dde33e386ca7db7b9d1b844146e24cbb`).
Millisecond offsets around nominal 8-hour boundaries are real archive values
and must be preserved exactly.

The archive schema is only:

```text
calc_time,funding_interval_hours,last_funding_rate
```

It has no settlement mark-price field. The adjacent Binance
`markPriceKlines/1h` archives are OHLC candles. Existing ZackBot research
correctly labels their close as `funding-mark proxy v1`; that proxy is not a
genuine settlement mark and is forbidden for this EDGE-03 packet.

### Audited API settlement rows (5/9)

The prior evidence acquisition at
`C:\Dev\ZackBotEvidence\quote_pair\20261018-v1\accounting_raw` has an audited
manifest recording source `https://fapi.binance.com`, acquisition
`2026-10-18T05:10:09Z`–`05:15:01Z`, zero errors, and zero gaps. Its funding CSV
schema includes `fundingTime,fundingRate,markPrice,time_utc`. Five eligible
USDT symbols have all 2,143 rows in the frozen interval, including the terminal
settlement, with a non-empty genuine `markPrice` on every row:

| symbol | exact rows | raw file SHA-256 |
|---|---:|---|
| BTCUSDT | 2,143 | `d8ad07197ce96a1dd6b09db6cec51a1b3f21369f0b5ae86abd8de56a125bbbda` |
| ETHUSDT | 2,143 | `43cd57bfd6f971f4fa135eb6a2186f4221d2a30754626ccb87aaac96320e0c27` |
| SOLUSDT | 2,143 | `69dada208e12bf2e0ec2910fcff739d3c2826a68cb9e92d4a45607a3b0c97d94` |
| XRPUSDT | 2,143 | `2e7d08a37d390c21dbcfd27447c66177b0fe6d978007cec75e95271d86710b4a` |
| DOGEUSDT | 2,143 | `f96976cb883d36c2c8b8008f0a9a120a73879b2dfcb619b13741af176a4b47d5` |

For each of these five files, every timestamp and funding rate matches its
monthly archive row exactly; there are zero missing archive keys, rate
mismatches, or non-positive marks.

No equivalent local audited funding-rate API file was found for `ZECUSDT`,
`NEARUSDT`, `UNIUSDT`, or `SUIUSDT`. Therefore exact settlement funding
coverage is **5/9**, below the required 8/9 gate. The hourly mark-price candle
archives cannot raise that count.

## Smallest unblock contract

1. Acquire only the four missing symbols from Binance USD-M
   `/fapi/v1/fundingRate`, bounded to the frozen interval. Preserve the API's
   exact `symbol`, `fundingTime`, `fundingRate`, and `markPrice` fields; do not
   join or interpolate any candle series.
2. Save immutable raw response pages plus a manifest containing request
   parameters, source URL/environment, retrieval timestamps, page boundaries,
   row counts, and SHA-256 for every raw and normalized artifact. Never include
   credentials.
3. Preserve each `fundingTime` exactly, including millisecond jitter. Require
   exact `(symbol, fundingTime, fundingRate)` parity with the local monthly
   archive, one finite positive `markPrice` per archive settlement, no extra
   rows, and coverage through `1790553600000` inclusive. Any mismatch remains
   a hard block.
4. Record `available_ms = fundingTime` only under the explicit Binance
   settlement-record semantic that this API row is the finalized value
   published for that settlement. Record the later acquisition time separately;
   it must not be confused with event availability.
5. Acquire and hash one terminal 4-hour **open** per eligible symbol at
   `1790553600000`. Prepare a terminal-open-only artifact so high/low/close from
   the sealed boundary bar cannot enter the runner.
6. Only after steps 1–5 pass, build bounded candle and funding manifests in a
   new, no-overwrite output directory. The builder must verify source hashes,
   exact universe, exact common settlement calendar, half-open candle slices,
   terminal-open isolation, finite values, and 9/9 funding completeness before
   emitting anything. Then run its synthetic tests; do not run market Stage-A
   until an independent gate accepts the packet inputs.

No manifest builder is added on this head because accepting the present local
inventory would either violate the 8/9 completeness gate or manufacture four
symbols' settlement marks from candle prices.
