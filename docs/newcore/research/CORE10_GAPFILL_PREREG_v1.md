# CORE-10 bounded gap-fill evidence run: preregistration v1

Status: **DRAFT, plan only.** No fetch and no evaluation is authorized until Codex accepts this file at an exact head.
Basis: master `f485467ab82a2238def19da8025c426b7f73524a` (remedy stack #88 → #91 → #92 merged), MISSING_DATA_POLICY_v1.md.

## 1. Frozen inputs (committed in this PR)

| Item | Path | Pin |
|---|---|---|
| Source manifest | `research_evidence/manifests/binance-um-archive-v1.json.gz` | manifest_digest `54912d9d2bf6e6fc45bc75c0553f0bd867e877972beeb3452785937379baf1e7` |
| Hole/window report (timestamp-only) | `research_evidence/prereg/core10_gapfill_report.v1.json` | file sha256 `cd93ce28b8736c83257e3b9766aa5f866b30874327a6ab31af8a6a53b10968fa`; self `sha256` `7d3bdc9ce4b99de20fb3ad346ca4aa97c0541f981fc2b09d5d6c7b7752d442bd` |
| URL plan | `research_evidence/prereg/core10_gapfill_plan.v1.json` | file sha256 `d7336ab59a0d6d4f987da3f090db2d3c4032c3d11414211d6c0db2f44c337a84`; `digest` `fb9e5744b3d4f684f753fd8ac0bd2f121eb2709c7dca5735fb5a9951582215e3`; `inventory_digest` = report self-sha |
| Delist notice | `research_evidence/notices/zb-delist-notice-core-10-20261014.json` | `4cdf7d61c1e8c38397e04a685f94540bfaf612e8c176385f30f1a2bbd1a6cf68` |
| Store | `C:/Dev/ZackBot2_data/binance_um` | must reproduce the report above byte-for-byte before fetch (step 0) |

Report and plan were produced on a clean tree at `f485467` with:

```
py -3.14 tools/research/rex_gap_inventory.py --store C:/Dev/ZackBot2_data/binance_um --tier CORE-10 \
   --window 2021-01-04T00:00:00Z/2026-09-28T00:00:00Z --out <S>/gap_report.json
py -3.14 tools/research/binance_archive_fill.py --report <S>/gap_report.json --out <S>/fetch_plan.json   # plan-only default
```

## 2. Exact scope

- Tier **CORE-10** (BTC ETH ZEC SOL XRP NEAR HYPE DOGE UNI SUI, USDT-M).
- Window **2021-01-04T00:00:00Z → 2026-09-28T00:00:00Z** = `pit_run.REX_DEV_WINDOW`.
  **OPEN (Codex 6306617660):** the sealed v1 rex run's window/ledger is not on master; this assumes it equals `REX_DEV_WINDOW`. If it differs, the report and plan are regenerated and every digest above changes.
- Holes: **182** (294 missing bar opens). By series/split: markPriceKlines train 90 / walk-forward 84; klines walk-forward 8.
  Missing opens by reason: funding_mark 174 (train 90, wf[0] 27, wf[1] 24, wf[4] 33); gap 120 (all wf[0], the 8 klines holes). `series_end` 0, `off_grid` 0.
- Requests: **77** daily archive files (57 markPriceKlines, 20 klines), periods 2021-07-01 → 2026-06-29, no monthly.
  Per symbol: NEAR/SOL/XRP/ZEC 13, BTC 9, UNI 8, DOGE/ETH 3, HYPE/SUI 1.
- Holdout: no planned file's period ends after `SEALED_END` (enforced by `candidates()` and re-checked by the add-on loader).
- Host: `https://data.binance.vision` only; `min_gap_s` 1.0, sequential.

## 3. Checksums

Archive sha256 values are not knowable before fetch. Rule: each zip is verified against its own `.CHECKSUM` at fetch time;
the zip and `.CHECKSUM` are kept in `--zips`; the add-on loader re-proves every filled row from those zips.
The add-on file's sha256 and the zips dir listing (name + sha256 per file) are **posted on the PR before any evaluation**,
and that posted value is the one passed as `--addon-sha256`.

## 4. Output and run identity

- Fetch outputs: `C:/Dev/ZackBot2_data/core10_gapfill_v1/` (`addon.json`, `zips/`). Never inside the repo, never the sealed v1 dir.
- Evaluation output: `research_evidence/runs/rex-core10-gapfill-v1/` (new dir; refuse if it exists; no `--resume`).
- Run identity: `rex-core10-gapfill-v1`, commit = accepted head of this PR, clean tree.

## 5. Acceptance / refusal table

| Event | Outcome |
|---|---|
| Step-0 report digest differs from §1 | **STOP**, no fetch; report the diff |
| HTTP 429 / 418 / 403, HTML body, redirect off `data.binance.vision` | **STOP** the whole fetch; post status; no retry loop |
| HTTP 404 on a planned file | hole stays **archive_missing** = genuine gap under MISSING_DATA_POLICY_v1 |
| `.CHECKSUM` mismatch or unreadable zip | file **unresolved**; its holes stay holes; never re-fetched in this run |
| Zip row outside its hole set / overlaps existing data | row ignored; existing store data wins |
| Add-on loader refusal (manifest, sealed end, empty add-on, digest) | **STOP before Access**; no evaluation |
| Any fill or result outside CORE-10 / the window | refused by tooling; treated as a defect |
| Post-fill holes > 0 | reported per reason; evaluation proceeds only under the policy's genuine-gap handling |

## 6. One-shot commands (run once, after Codex acceptance)

```
# step 0: reproduce the frozen report (must match §1)
py -3.14 tools/research/rex_gap_inventory.py --store C:/Dev/ZackBot2_data/binance_um --tier CORE-10 \
   --window 2021-01-04T00:00:00Z/2026-09-28T00:00:00Z --out <S>/gap_report.json
# step 1: fetch (network)
py -3.14 tools/research/binance_archive_fill.py --report research_evidence/prereg/core10_gapfill_report.v1.json \
   --out C:/Dev/ZackBot2_data/core10_gapfill_v1/addon.json --fetch --zips C:/Dev/ZackBot2_data/core10_gapfill_v1/zips
# step 2: post addon sha256 + zip listing on the PR, then evaluate
py -3.14 tools/research/run_r4.py rex --window 2021-01-04T00:00:00Z/2026-09-28T00:00:00Z --tier CORE-10 \
   --store C:/Dev/ZackBot2_data/binance_um --out-dir research_evidence/runs/rex-core10-gapfill-v1 \
   --notice research_evidence/notices/zb-delist-notice-core-10-20261014.json \
   --notice-sha256 4cdf7d61c1e8c38397e04a685f94540bfaf612e8c176385f30f1a2bbd1a6cf68 \
   --addon C:/Dev/ZackBot2_data/core10_gapfill_v1/addon.json --addon-report research_evidence/prereg/core10_gapfill_report.v1.json \
   --addon-sha256 <posted> --addon-zips C:/Dev/ZackBot2_data/core10_gapfill_v1/zips \
   --author claude-code --cairo-date <date>
```

## 7. Open rulings (Codex)

1. Window: confirm `REX_DEV_WINDOW` equals the sealed v1 run window (§2).
2. Variant: `range_vwap_rev.v1` (above) vs `--variant range_vwap_rev.v1-splitcut` with the add-on. One variant only, chosen before fetch.
3. Trial multiplicity: this run counts as one additional trial against the rex family; the result is evidence for the remedy, not a new strategy selection.
