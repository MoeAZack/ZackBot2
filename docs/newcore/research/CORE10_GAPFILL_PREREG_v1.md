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
| Store | `C:/Dev/ZackBot2_data/binance_um` | must reproduce the report above before fetch (step 0, rule below) |

Report and plan were produced on a clean tree at `f485467` with:

```
py -3.14 tools/research/rex_gap_inventory.py --store C:/Dev/ZackBot2_data/binance_um --tier CORE-10 \
   --window 2021-01-04T00:00:00Z/2026-09-28T00:00:00Z --out <S>/gap_report.json
py -3.14 tools/research/binance_archive_fill.py --report <S>/gap_report.json --out <S>/fetch_plan.json   # plan-only default
```

**Step-0 equality rule.** The report embeds `provenance.commit`, so its self-sha changes with the head it runs at.
Step 0 regenerates the report at the **protected master merge commit of this PR** and requires every field to equal the frozen file **except** `provenance.commit`
(must equal that merge commit, tree clean) and the derived `sha256`. Verified at `89fb082`: those two are the only differing fields.
The fill always uses the **frozen** report (§6), so the plan digest and the add-on's pinned report do not move.

## 2. Exact scope

- Tier **CORE-10** (BTC ETH ZEC SOL XRP NEAR HYPE DOGE UNI SUI, USDT-M).
- Window **2021-01-04T00:00:00Z → 2026-09-28T00:00:00Z** = `pit_run.REX_DEV_WINDOW`.
  **Confirmed (Codex 6306709619):** the sealed v1 evidence (`detail-370fb3b0b1170055.json`, candidate `range_vwap_rev.v1`) has exactly this Access window, manifest `54912d9d…` and notice `4cdf7d61…`.
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

- Fetch outputs: `C:/Dev/ZackBot2_data/core10_gapfill_splitcut_v1/` (`addon.json`, `zips/`). Never inside the repo, never the sealed v1 dir.
- Evaluation output: `research_evidence/runs/rex-core10-gapfill-splitcut-v1/` (new dir; refuse if it exists; no `--resume`).
- Run identity: `rex-core10-gapfill-splitcut-v1`, variant `range_vwap_rev.v1-splitcut`, commit = **protected master merge commit of this PR** (never an unmerged branch), clean tree. Non-promotable evidence.

## 5. Acceptance / refusal table

| Event | Outcome |
|---|---|
| Step-0 report differs from §1 in any field other than `provenance.commit` / `sha256` | **STOP**, no fetch; report the diff |
| HTTP 429 / 418 / 403, HTML body, redirect off `data.binance.vision` | **STOP** the whole fetch; post status; no retry loop |
| HTTP 404 on a planned file | hole stays **archive_missing** = genuine gap under MISSING_DATA_POLICY_v1 |
| `.CHECKSUM` mismatch or unreadable zip | file **unresolved**; its holes stay holes; never re-fetched in this run |
| Zip row outside its hole set / overlaps existing data | row ignored; existing store data wins |
| Add-on loader refusal (manifest, sealed end, empty add-on, digest) | **STOP before Access**; no evaluation |
| Any fill or result outside CORE-10 / the window | refused by tooling; treated as a defect |
| Post-fill holes > 0 | reported per reason; evaluation proceeds only under the policy's genuine-gap handling |

## 6. One-shot commands (run once, after Codex acceptance)

```
# step 0: reproduce the frozen report (equality rule in §1)
py -3.14 tools/research/rex_gap_inventory.py --store C:/Dev/ZackBot2_data/binance_um --tier CORE-10 \
   --window 2021-01-04T00:00:00Z/2026-09-28T00:00:00Z --out <S>/gap_report.json
# step 1: fetch (network)
py -3.14 tools/research/binance_archive_fill.py --report research_evidence/prereg/core10_gapfill_report.v1.json \
   --out C:/Dev/ZackBot2_data/core10_gapfill_splitcut_v1/addon.json --fetch --zips C:/Dev/ZackBot2_data/core10_gapfill_splitcut_v1/zips
# step 2: post addon sha256 + zip listing on the PR, then evaluate
py -3.14 tools/research/run_r4.py rex --window 2021-01-04T00:00:00Z/2026-09-28T00:00:00Z --tier CORE-10 \
   --variant range_vwap_rev.v1-splitcut --store C:/Dev/ZackBot2_data/binance_um --out-dir research_evidence/runs/rex-core10-gapfill-splitcut-v1 \
   --notice research_evidence/notices/zb-delist-notice-core-10-20261014.json \
   --notice-sha256 4cdf7d61c1e8c38397e04a685f94540bfaf612e8c176385f30f1a2bbd1a6cf68 \
   --addon C:/Dev/ZackBot2_data/core10_gapfill_splitcut_v1/addon.json --addon-report research_evidence/prereg/core10_gapfill_report.v1.json \
   --addon-sha256 <posted> --addon-zips C:/Dev/ZackBot2_data/core10_gapfill_splitcut_v1/zips \
   --author claude-code --cairo-date <date>
```

## 7. Rulings (Codex 6306709619, fixed before any fetch)

1. Window: confirmed equal to the sealed v1 run window (§2).
2. Variant: exactly `range_vwap_rev.v1-splitcut` (the accepted development-only causal cutoff from #91). No other variant is run.
3. Multiplicity: exactly one additional rex-family trial. It tests the accepted remedy; it is not a strategy-selection or promotion trial.
4. Execution commit: the protected master merge commit of this PR; all commands in §6 run there.
