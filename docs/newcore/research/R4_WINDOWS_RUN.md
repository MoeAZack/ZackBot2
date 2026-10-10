# R4 official run on the owner's Windows PC (R4-0 rebuild; not run)

Status: nothing below may run on real data until Codex issues `R4 READY`. Official runs are on the owner's Windows
PC (Codex 6094669453); Linux runs are complementary only. Progress is published as operational state only
(Codex 6094685201): no PnL, score, return or comparison until the run is complete and sealed. The forward holdout
is never opened by any command here.

## Pins (research_evidence/prereg/r4_gate.json, `zb-r4-gate/2`)

| Pin | Exact SHA | Check |
|---|---|---|
| R1+R2+R3 (#51, #52) | `01564264431650a9fde53f27623928bb84702914` (master, 2026-10-10 10:59 Cairo) | merged into `origin/master`, in HEAD |
| R3 overlap integrity + `legacy-m3-repro-v1` (#56) | `33ee82f70039a30dd6bcd9cda6b3816156131638` | equals `origin/nc-r3-overlap-integrity`, descends from 0156426, merged into `origin/master`, in HEAD |
| Classification v2 | `PENDING` | G5 keeps the gate closed until a 64-hex digest is bound in a reviewed commit |

Today the gate is CLOSED by design: #56 is not yet merged into master (G4), the prereg is UNREGISTERED (G3) and
classification v2 is PENDING (G5). A fix round that moves a pin is re-pinned only in a reviewed commit.

| Placeholder | Meaning |
|---|---|
| `<MASTER_SHA>` | accepted `origin/master` head containing both pins and the reviewed #53 head |
| `<R4_SHA>` | Codex-cleared #53 head (prereg + M3 harness + progress + readiness) |
| `<CAIRO_DATE>` | Cairo date of the run, `YYYY-MM-DD` |

## 0. Checkout (PowerShell)

```powershell
$env:PATH = [System.Environment]::GetEnvironmentVariable("PATH","Machine")+";"+[System.Environment]::GetEnvironmentVariable("PATH","User")
git clone https://github.com/MoeAZack/ZackBot2.git C:\Dev\ZackBot2_r4run
Set-Location C:\Dev\ZackBot2_r4run
git fetch origin
git checkout --detach <MASTER_SHA>
git merge-base --is-ancestor 01564264431650a9fde53f27623928bb84702914 HEAD; if ($LASTEXITCODE) { throw "R1-R3 not in HEAD" }
git merge-base --is-ancestor 33ee82f70039a30dd6bcd9cda6b3816156131638 HEAD; if ($LASTEXITCODE) { throw "#56 not in HEAD" }
git merge-base --is-ancestor <R4_SHA> HEAD; if ($LASTEXITCODE) { throw "#53 not in HEAD" }
git status --porcelain                      # must print nothing
python --version                            # record; must match the reviewed execution environment
```

## 1. Gate, tests and the offline R4-0 report

```powershell
python tools/research/run_r4.py check
# Exit 3 = CLOSED: stop and route the printed G1-G5 failures; never edit pins locally.
python -m pytest -q -p no:cacheprovider tests/test_res01_manifest_ledger.py tests/test_res02_universe.py `
    tests/test_res03_core.py tests/test_res04_prep.py tests/test_res04_progress.py
python tools/research/r4_readiness.py --pytest --out research_evidence\runs\r4\R4-0_readiness.json
# exit 0 = READY; exit 1 = NOT READY (see not_passing). Only Codex issues "R4 READY".
```

The offline report recomputes the archive manifest digest (103,659 files), the universe digest and its manifest
binding, the `legacy-m3-repro-v1` digest (zero overlaps, repro-only), the prereg SHA-256, checks that the family
ledger holds no holdout record, and records the test counts. Items it cannot compute stay MISSING.

If G3 reports "not registered" and Codex has cleared the prereg (only after classification v2 is bound and the
prereg's universe digests are updated in a reviewed commit):

```powershell
python tools/research/run_r4.py register --author claude-code --cairo-date <CAIRO_DATE>
# then commit the 19 ledger lines through a reviewed PR, re-checkout the new accepted master, re-run check
```

## 2. M3 reproduction (R4-0 item 5; uncapped; reproduction-only flat funding)

```powershell
$out = "research_evidence\runs\r4\m3"
python tools/research/run_r4.py m3 --store C:\Dev\ZackBot2_r4run --cost both --out-dir $out `
    --author claude-code --cairo-date <CAIRO_DATE>
Get-ChildItem $out
Get-FileHash $out\* -Algorithm SHA256
```

- Data: `research_evidence/manifests/legacy-m3-repro-v1.json` (digest `a42c3692...8bb6`): the 8 `data_long/4h`
  core-8 files under the checkout root (`--store` = the checkout). REPRO-ONLY: a development window only.
- Runner: `pit.evaluate` with `tools/research/m3_eval.py` (sandboxed process, every decision re-run under future
  perturbation, a second fresh-process replay, attestation in `research_evidence/ledger/trend_ema_mom.jsonl`).
- Writes `m3_repro_base.json`, `m3_repro_2x.json` (atomic), `progress-<run>.json` (live snapshot, atomically
  replaced) and `final-<run>.json` (immutable sealed final). Only the progress snapshot / final may be mirrored
  while running.
- Acceptance: trade-for-trade match, |dR| <= 1e-9, same count, for both rows. Anything else = NOT REPRODUCED.
- Determinism (item 6): run it twice in fresh processes and once from a relocated checkout; the three
  `final-<run>.json` files must be identical apart from `wall`.

## 3. Remaining R4-0 evidence (not built by Build)

- Item 2: Windows re-verification of the archive manifest and universe against the store; classification v2.
- Item 4: independent accounting calculator over representative long + short trades (Codex may assign to Cowork).
- Item 7: development pilot on a small spent slice with independently calculated sample trades.

Merge them into the readiness report with `--evidence r4_0_inputs.json` (keys `m3_parity`, `accounting_sample`,
`pilot`, `items`, `final_files`).

## 4. First time-correct evaluation (development only)

Lands only after `R4 READY` and after M3 is REPRODUCED and reviewed (R4b); today `run_r4.py pit` refuses with exit 4.
The crypto top-40 book is the only book; the uncapped primary and the `trend_ema_mom.v1.cap180` secondary are both
reported with no adaptive choice; gold-spot and gold-tokenized are a deferred pilot with their own later prereg.
