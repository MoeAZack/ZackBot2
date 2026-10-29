# OI-LOADER-01 — OI metrics manifest admission predicate (research only)

**Status: DRAFT — unsigned.** Contract only: no loader code, no authority. The machine twin
`oi_loader_01_contract.v1.json` is authoritative; the JSON wins any conflict. Assignment: Codex 6489944476.
Background rulings: 6489828197 (N1/N2) and 6489428240 (J1–J3). Built from `origin/master` @ `f189ff7`.

## 1. Code identity

Every citation is to PR #225 head `f850323` (`tools/research/oi_metrics_fetch.py`, `oi_metrics_probe.py`,
`tests/test_oi_metrics_fetch.py`), which is **not on master**. `rebind_required_before_signing`: the contract stays
DRAFT until #225 lands and this branch is refreshed; then every citation is rebound to the master code identity and the
pinned values are re-checked before Integration signs.

## 2. Admission predicate (ALL must hold)

| id | conjunct |
|----|----------|
| P1 | `authoritative is true` |
| P2 | `complete is true` |
| P3 | `stopped is null` |
| P4 | `failures == []` |
| P5 | `covers(manifest_coverage, required_start, required_end_exclusive)` |
| P6 | every required day has exactly one verified file entry with valid path/date/symbol/hash/size identity for the requested symbol and window |

`authoritative`, `covers()`, `missing_days == []` and `strict_complete` are each **never sufficient alone**. Cowork's
N1/N2 partial-run shape (authoritative, complete false, one failure, `covers()` true) is refused by P2, P4 and P6.

## 3. Manifest fields (`build_manifest` @ f850323)

Pinned: `format` = `zb-oi-metrics-fetch/1`, `loader_version` = `zb-oi-metrics-raw-zip/1`, `source_class`,
`data_root`, `series`, `mode` = `bulk`, `cutoff_exclusive` = `2026-09-28`, `gate_months` = 36, `available_rule`.
File entries are exactly `path`, `symbol`, `date`, `bytes`, `sha256`, `source` (size is `bytes`). Coverage entries,
gaps, month runs and `stopped` have fixed field sets. `digest` and `total_bytes` are re-checked.

Identity: `path` = `daily/metrics/{symbol}/{symbol}-metrics-{date}.zip`; `source` =
`https://data.binance.vision/data/futures/um/{path}`; `date` strict `YYYY-MM-DD` inside the manifest window and before
the cutoff; `symbol` in `symbols`; `sha256` 64 lowercase hex; `bytes` an int > 0. Every entry is checked, and no two
entries share a path.

## 4. Dates and cutoff

The request is `[required_start, required_end_exclusive)`. `required_end_exclusive` may **equal** the sealed cutoff
`2026-09-28` (last day `2026-09-27`); past it is refused (`covers` @ f850323). The manifest `end` is inclusive, so its
window is `[start, end + 1 day)`. Refused as ambiguous: non-ISO or basic-form dates, time- or timezone-bearing values,
non-str bounds, impossible dates, end <= start, a null manifest start, and a request outside the manifest window.

## 5. Fail-closed refusal codes

Request: `REQ_DATE`, `REQ_ORDER`, `REQ_CUTOFF`, `REQ_SYMBOL`, `REQ_WINDOW`. Schema: `SCHEMA_NOT_OBJECT`,
`SCHEMA_UNKNOWN_FIELD`, `SCHEMA_MISSING_FIELD`, `SCHEMA_VERSION`, `SCHEMA_TYPE`, `SCHEMA_DATE`, `SCHEMA_SYMBOLS`,
`SCHEMA_TOTAL_BYTES`, `SCHEMA_DIGEST`. Any precondition failure refuses before the conjuncts are evaluated; then every
failing conjunct (P1–P6) is reported.

## 6. Forbidden in this PR

Loader implementation, archive or network access, bulk run, feature computation, ML use, runtime import, PAPER
authority, reading any zip or row, synthesizing a missing day.

## 7. Open questions for Integration

Q1 sub-window requests · Q2 digest/total_bytes as preconditions · Q3 on-disk re-hash at load time · Q4 per-symbol vs
all-or-nothing · Q5 coverage required-interval match · Q6 failures/stopped content schema. Proposals are in the JSON.
