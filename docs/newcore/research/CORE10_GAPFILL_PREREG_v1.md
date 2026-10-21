# CORE-10 bounded gap-fill evidence run: preregistration v1

Status: **DRAFT, plan only.** No fetch and no evaluation is authorized until Codex accepts this file at an exact head.
Basis: master `f485467ab82a2238def19da8025c426b7f73524a` (remedy stack #88 → #91 → #92 merged), MISSING_DATA_POLICY_v1.md.

## 1. Frozen inputs (committed in this PR)

| Item | Path | Pin |
|---|---|---|
| Source manifest | `research_evidence/manifests/binance-um-archive-v1.json.gz` | manifest_digest `54912d9d2bf6e6fc45bc75c0553f0bd867e877972beeb3452785937379baf1e7` |
| Hole/window report (timestamp-only) | `research_evidence/prereg/core10_gapfill_report.v1.json` | file sha256 `cd93ce28b8736c83257e3b9766aa5f866b30874327a6ab31af8a6a53b10968fa`; self `sha256` `7d3bdc9ce4b99de20fb3ad346ca4aa97c0541f981fc2b09d5d6c7b7752d442bd` |
| URL plan | `research_evidence/prereg/core10_gapfill_plan.v1.json` | file sha256 `d7336ab59a0d6d4f987da3f090db2d3c4032c3d11414211d6c0db2f44c337a84`; `digest` `fb9e5744b3d4f684f753fd8ac0bd2f121eb2709c7dca5735fb5a9951582215e3`; `inventory_digest` = report self-sha |
| Delist notice | `research_evidence/notices/zb-delist-notice-core-10-20261014.json` | artifact `digest` field `4cdf7d61c1e8c38397e04a685f94540bfaf612e8c176385f30f1a2bbd1a6cf68` (what `--notice-sha256` expects; not the file sha256) |
| Store | `C:/Dev/ZackBot2_data/binance_um` | must reproduce the report above before fetch (step 0, rule below) |

Report and plan were produced on a clean tree at `f485467` with:

```
py -3.14 tools/research/rex_gap_inventory.py --store C:/Dev/ZackBot2_data/binance_um --tier CORE-10 \
   --window 2021-01-04T00:00:00Z/2026-09-28T00:00:00Z --out <S>/gap_report.json
py -3.14 tools/research/binance_archive_fill.py --report <S>/gap_report.json --out <S>/fetch_plan.json   # plan-only default
```

**Step-0 equality rule.** The report embeds `provenance.commit`, so its self-sha changes with the head it runs at.
Step 0 regenerates the report at the **protected master merge commit of this PR** and requires every field to equal the frozen file **except** `provenance.commit`
(must equal that merge commit, tree clean) and the derived `sha256`.
The fill always uses the **frozen** report (§6), so the plan digest and the add-on's pinned report do not move.

## 2. Exact scope

- Tier **CORE-10** (BTC ETH ZEC SOL XRP NEAR HYPE DOGE UNI SUI, USDT-M).
- Window **2021-01-04T00:00:00Z → 2026-09-28T00:00:00Z** = `pit_run.REX_DEV_WINDOW`.
  **Confirmed (Codex 6306709619):** the sealed v1 evidence (`detail-370fb3b0b1170055.json`, candidate `range_vwap_rev.v1`) has exactly this Access window, manifest `54912d9d…` and notice `4cdf7d61…`.
- Holes: **182** (294 missing bar opens). By series/split: markPriceKlines train 90 / walk-forward 84; klines walk-forward 8.
  Missing opens by reason: funding_mark 174 (train 90, wf[0] 27, wf[1] 24, wf[4] 33); gap 120 (all wf[0], the 8 klines holes). `series_end` 0, `off_grid` 0.
- Requests: **77** daily archive files = **154 HTTP requests** (zip + `.CHECKSUM` each) (57 markPriceKlines, 20 klines), periods 2021-07-01 → 2026-06-29, no monthly.
  Per symbol: NEAR/SOL/XRP/ZEC 13, BTC 9, UNI 8, DOGE/ETH 3, HYPE/SUI 1.
- Holdout: no planned file's period ends after `SEALED_END` (enforced by `candidates()` and re-checked by the add-on loader).
- Host: `https://data.binance.vision` only; `min_gap_s` 1.0, sequential.

## 3. Checksums

Archive sha256 values are not knowable before fetch. Rule: each zip is verified against its own `.CHECKSUM` at fetch time;
the zip and `.CHECKSUM` are kept in `--zips`; the add-on loader re-proves every filled row from those zips.
The add-on file's sha256 and the zips dir listing (name + sha256 per file) are **posted on the PR before any evaluation**,
and that posted value is the one passed as `--addon-sha256`.
`.CHECKSUM` is served by the same host as the zip, so it proves corruption integrity, not independent provenance.

## 4. Output and run identity

- Fetch outputs: `C:/Dev/ZackBot2_data/core10_gapfill_splitcut_v1/` (`addon.json`, `zips/`). Never inside the repo, never the sealed v1 dir.
  **One-shot:** step 1 may start only if this dir does not exist (the tool itself overwrites `--out` and `--zips` silently, so the
  guard in §6 enforces it). After any step-1 start, including exit 3 or an interruption, the dir is frozen and step 1 is never re-run in this run.
- Evaluation output: `research_evidence/runs/rex-core10-gapfill-splitcut-v1/` (artifacts land in `research_evidence/runs/rex-core10-gapfill-splitcut-v1/CORE-10/`). Step 2 may start only if this dir does not exist;
  `pit_run._state` treats an existing dir without progress as new, so the §6 guard enforces it, not the tool.
- **No `--resume` in this run.** An exception seals an aborted final (resume refuses); a hard kill followed by resume would
  re-open Access and append a second data_access + second trial set to the ledger. So any interruption of step 2 **ends this
  registered run**. A retry needs a separately accepted run record, which must state that the ledger already holds the first attempt's records.
- Run identity: `rex-core10-gapfill-splitcut-v1`, variant `range_vwap_rev.v1-splitcut`, commit = **protected master merge commit of this PR** (never an unmerged branch), clean tree. Non-promotable evidence.

## 5. Acceptance / refusal table

| Event | Outcome |
|---|---|
| Step-0 report differs from §1 in any field other than `provenance.commit` / `sha256` | **STOP**, no fetch; report the diff |
| HTTP 429 / 418 / 403, HTML body, redirect off `data.binance.vision` | **STOP** the whole fetch; post status; no retry |
| `addon.stopped` is not null (step 1 exits 3), even if the partial add-on is loadable | **no evaluation**; post the stopped state; this registered run ends |
| Verified HTTP 404 on a planned file | hole stays **archive_missing** = genuine gap under MISSING_DATA_POLICY_v1 |
| Any other unresolved outcome: HTTP 5xx, timeout/network error, bad zip, schema or `.CHECKSUM` failure | holes **unresolved**; **no evaluation and no retry in this run**; a future attempt needs a separately accepted run record |
| Zip row outside its hole's missing set | never enters the add-on (`extract()` keeps only missing opens) |
| Base store changed and a planned open now exists | `check_base` refuses the **whole add-on**: **STOP before Access**; no evaluation |
| Add-on loader refusal (manifest, sealed end, empty add-on, digest) | **STOP before Access**; no evaluation |
| Any fill or result outside CORE-10 / the window | refused by tooling; treated as a defect |
| Only verified-404 gaps remain unfilled | evaluation may proceed only if the add-on is non-empty and the loader accepts it; incompleteness reported per reason |

## 6. One-shot commands (run once, after Codex acceptance)

```
# step 0: reproduce the frozen report (equality rule in §1)
py -3.14 tools/research/rex_gap_inventory.py --store C:/Dev/ZackBot2_data/binance_um --tier CORE-10 \
   --window 2021-01-04T00:00:00Z/2026-09-28T00:00:00Z --out <S>/gap_report.json
# step 1: fetch (network), one-shot guard first
test ! -e C:/Dev/ZackBot2_data/core10_gapfill_splitcut_v1 || { echo 'fetch dir exists: STOP'; exit 1; }
py -3.14 tools/research/binance_archive_fill.py --report research_evidence/prereg/core10_gapfill_report.v1.json \
   --out C:/Dev/ZackBot2_data/core10_gapfill_splitcut_v1/addon.json --fetch --zips C:/Dev/ZackBot2_data/core10_gapfill_splitcut_v1/zips
# step 2a: pre-evaluation gate (must print GATE PASS; anything else = no evaluation, run ends)
py -3.14 -c "import json,sys; a=json.load(open(sys.argv[1])); bad=[a['stopped'] is not None, not a['rows'], any(h['status']=='unresolved' for h in a['holes']), any(f['status'] in ('error','rejected_checksum') for f in a['files'])]; print('GATE FAIL' if any(bad) else 'GATE PASS', bad)" C:/Dev/ZackBot2_data/core10_gapfill_splitcut_v1/addon.json
# step 2b: post addon sha256 + zip listing on the PR, then evaluate (one-shot guard first, no --resume)
test ! -e research_evidence/runs/rex-core10-gapfill-splitcut-v1 || { echo 'evidence dir exists: STOP'; exit 1; }
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
   This is the multiplicity accounting, not the ledger record count: one run writes 1 data_access + 20 trial records
   (2 sides x (primary + 8 neighbours + baseline B2) = 18 variant + 2 baseline), and together they count as this one trial.
4. Execution commit: the protected master merge commit of this PR; all commands in §6 run there.
