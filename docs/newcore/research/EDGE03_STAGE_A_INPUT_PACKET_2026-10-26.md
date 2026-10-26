# EDGE-03 Stage-A audited input packet (2026-10-26)

Status: **inputs prepared; strategy run remains prohibited pending independent
Codex acceptance**.

No signal, trade, return, selection, or holdout result was calculated.

## Immutable acquisition

- Root: `C:\Dev\ZackBotEvidence\edge03\20261026T161815Z`
- Public source: `https://fapi.binance.com` (unsigned requests; no credentials)
- Acquisition: `2026-10-26T16:18:15Z`–`16:18:42Z`
  (`19:18 Cairo`, Africa/Cairo)
- Acquisition manifest SHA-256:
  `9b45bd09adfdc1faef85328e1c8f5216907db9a62e662a3c2b561e2a13a058bc`

Only `ZECUSDT`, `NEARUSDT`, `UNIUSDT`, and `SUIUSDT` funding histories were
requested. Each has three immutable raw `/fapi/v1/fundingRate` response pages
and exactly 2,143 rows over the inclusive source interval
`2024-10-14T00:00:00Z`–`2026-09-28T00:00:00Z`. Every row preserves Binance's
exact `fundingTime`, `fundingRate`, and settlement `markPrice` fields.

One immutable `/fapi/v1/klines?interval=4h&limit=1` response was acquired for
each of the nine eligible symbols at `2026-09-28T00:00:00Z`. Preparation
propagates only element 1 (`open`); no terminal high, low, close, volume, or
later bar is present in a runner input.

The acquisition manifest records the source, endpoint and parameters for every
request, retrieval time, page boundaries, row counts, byte counts, and SHA-256
for every raw response.

## Offline audit and preparation

Canonical prepared root:
`C:\Dev\ZackBotEvidence\edge03\20261026T161815Z\prepared-final`

Every funding timestamp and decimal rate for all nine symbols matches the local
Binance monthly archive exactly. Every mark price is finite and positive. The
audit binds all 216 monthly ZIP inputs, the five pre-existing audited API CSVs,
the twelve new funding pages, the nine terminal responses, the nine candle
sources, both source manifests, and every prepared output by SHA-256.

Prepared coverage:

- candles: 9/9, exact half-open Stage-A bars plus one open-only terminal row;
- source funding: 9/9, 2,143 rows per symbol on the inclusive audit interval;
- runner funding: 2,142 crossed settlements per symbol on `(start, end]`;
- runner funding records: 19,278;
- terminal opens: 9/9;
- holdout read: false.

Canonical hashes:

| artifact | SHA-256 |
|---|---|
| `audit_manifest.json` | `5fbcc2142003039f23534b8531a279c8c77ac1c65b25124541b1477ace57cf46` |
| `candles_manifest.json` | `082db188756d5348414173ecd45b939cbca871c631dd0b7e5c0d02560fb23cee` |
| `funding_manifest.json` | `0108a45114ecb6ec1f4d00364e360a8af012c9df8b8d30aeb10b834d9fd16f12` |
| `funding_calendar.csv` | `f3633d3134a012fd3df7ddbdf5aa97c4d049c887ea094443fae648491cf862cc` |
| `funding_records.csv` | `702d192ea33cb13170ee723b68ecf0b0b30517279b728b1a825bff057f28ed6c` |

The earlier sibling directories `prepared` and `prepared-v2` are preserved
failed write-once attempts and are **not accepted inputs**. Only
`prepared-final`, identified by the hashes above, is canonical.

## Remaining gate

The previous data blocker is resolved. The only blocker is procedural: root
Codex must independently accept these exact input hashes before any Stage-A
strategy result is run. Any byte change requires a fresh no-overwrite packet
and renewed acceptance.
