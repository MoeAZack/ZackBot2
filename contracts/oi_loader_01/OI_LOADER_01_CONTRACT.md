# OI-LOADER-01 — OI metrics manifest admission predicate (research only)

**Status: DRAFT — unsigned.** Contract only: no loader code, no authority. The machine twin
`oi_loader_01_contract.v1.json` is authoritative; the JSON wins any conflict. Assignment: Codex 6489944476.
Background rulings: 6489828197 (N1/N2) and 6489428240 (J1–J3). Built from `origin/master` @ `f189ff7`.

## 1. Code identity

**Rebound (ruling 6493041460).** #225 merged as master `f8f03df` (f8f03df265dfd812becaa2a5bb9e9373490d4344).
Every citation now points at `f8f03df`: the OI fetcher `tools/research/oi_metrics_fetch.py`, `oi_metrics_probe.py`
(which holds `CUTOFF` and `parse_key`), `manifest.py` (`canonical`) and `tests/test_oi_metrics_fetch.py`. Each is pinned
by the sha256 of its git blob at `f8f03df` (JSON `code_identity.pinned_files`), so a moving master cannot break the
pin. The files are byte-identical to the drafted #225 head `f850323`. The contract stays DRAFT and unsigned until
Integration signs.

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

## 3. Manifest fields (`build_manifest` @ f8f03df)

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
`2026-09-28` (last day `2026-09-27`); past it is refused (`covers` @ f8f03df). The manifest `end` is inclusive, so its
window is `[start, end + 1 day)`. Refused as ambiguous: non-ISO or basic-form dates, time- or timezone-bearing values,
non-str bounds, impossible dates, end <= start, a null manifest start, and a request outside the manifest window.

## 5. Fail-closed refusal codes

Request: `REQ_DATE`, `REQ_ORDER`, `REQ_CUTOFF`, `REQ_SYMBOL`, `REQ_WINDOW`. Schema: `SCHEMA_NOT_OBJECT`,
`SCHEMA_UNKNOWN_FIELD`, `SCHEMA_MISSING_FIELD`, `SCHEMA_VERSION`, `SCHEMA_TYPE`, `SCHEMA_DATE`, `SCHEMA_SYMBOLS`,
`SCHEMA_TOTAL_BYTES`, `SCHEMA_DIGEST`, `SCHEMA_NONFINITE`, `SCHEMA_GROUPS`, `SCHEMA_COVERAGE_ENVELOPE`,
`SCHEMA_MALFORMED`. Any precondition failure refuses before the conjuncts are evaluated (P3/P4 are still reported); then
every failing conjunct (P1–P6) is reported. Disk: `DISK:missing`, `DISK:unreadable`, `DISK:size`, `DISK:sha256`.

**Cowork 6490219007 hardening, ruled by Integration 6490439004.** H1: every malformed value refuses with a
deterministic `SCHEMA_*` code and never raises. Types are validated before any hashing, membership test or date
arithmetic; a bool is refused wherever an int is required; dates are exact zero-padded ISO; NaN or ±Infinity anywhere
is refused at schema entry (`SCHEMA_NONFINITE`). The catch-all `SCHEMA_MALFORMED` covers only unexpected malformed
input during validation; a programmer error on a valid manifest propagates. H2: `bytes` is an int in
[1, 104857600] (100 MiB), no clamping (Q8). `groups` is **binding** (Q7): exactly the three keys, each a sorted
duplicate-free symbol list, the lists exactly partition `symbols`, and each symbol sits in the list matching
`coverage[symbol].group` (`SCHEMA_GROUPS`).

## 6. Integration ruling 6490314342 (Q1–Q6, resolved)

- **Q1** sub-windows allowed, never outside `[manifest.start, manifest.end + 1 day)` (`REQ_WINDOW`).
- **Q2** digest and `total_bytes` stay global fail-closed preconditions, separate from P6; identity covers every entry.
- **Q3** `disk_verification` is a **MANDATORY loader step**: before any row is read, every file admitted for the
  request must match its exact size and SHA-256 on disk; missing or mismatched refuses. The reference evaluator models
  it with an injected `read_bytes(path)`, called only after P1–P6 hold.
- **Q4** per-symbol evidence, **all-or-nothing** batch admission; a single-symbol request stands alone.
- **Q5** exact coverage envelope: `coverage.required_start == manifest.start` and
  `coverage.required_end_exclusive == manifest.end + 1 day` for every symbol (`SCHEMA_COVERAGE_ENVELOPE`), even when
  `covers()` passes.
- **Q6** only the `failures` / `stopped` container types are preconditions; non-empty / non-null refuses via P4 / P3
  before any file read; malformed nested content only adds `DIAG:audit_content`. Scope (6490439004): this relaxes no
  file, coverage, groups, date, numeric or digest check.

## 7. Forbidden in this PR

Loader implementation, archive or network access, bulk run, feature computation, ML use, runtime import, PAPER
authority, reading any zip or row, synthesizing a missing day.

## 8. Open questions for Integration

None open: Q1–Q6 resolved by 6490314342, Q7 and Q8 by 6490439004. #225 has merged and the code identity is
rebound to `f8f03df` (6493041460); signing waits only for independent evidence and Integration's signature.
