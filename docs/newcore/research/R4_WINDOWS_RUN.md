# R4 official run on the owner's Windows PC (draft, not run)

Status: DRAFT. Nothing below may run until Codex clears it. Official runs are on the owner's Windows PC
(Codex 6094669453); Linux runs are complementary only. Progress is published as operational state only
(Codex 6094685201): no PnL, score, return or comparison until the run is complete and sealed.

## Placeholders (fill from accepted, reviewed heads only)

| Placeholder | Meaning |
|---|---|
| `<MASTER_SHA>` | accepted `origin/master` head containing #51 (e23379f), #52 and #53 |
| `<R3_SHA>` | Codex-cleared #52 head pinned in `research_evidence/prereg/r4_gate.json` |
| `<R4_SHA>` | Codex-cleared #53 head (prereg + harness + progress writer) |
| `<LEGACY_STORE>` | local root holding `data_long/` (legacy-unverified-v1 sources) |
| `<CAIRO_DATE>` | Cairo date of the run, `YYYY-MM-DD` |

Activation gate: exact reviewed SHA pins plus ancestry in accepted master (`run_r4.py check`, G1-G4).

## 0. Checkout (PowerShell)

```powershell
$env:PATH = [System.Environment]::GetEnvironmentVariable("PATH","Machine")+";"+[System.Environment]::GetEnvironmentVariable("PATH","User")
git clone https://github.com/MoeAZack/ZackBot2.git C:\Dev\ZackBot2_r4run
Set-Location C:\Dev\ZackBot2_r4run
git fetch origin
git checkout --detach <MASTER_SHA>
git merge-base --is-ancestor <R3_SHA> HEAD; if ($LASTEXITCODE) { throw "R3 head not in master" }
git merge-base --is-ancestor <R4_SHA> HEAD; if ($LASTEXITCODE) { throw "R4 head not in master" }
git status --porcelain                      # must print nothing
python --version                            # record; must match the reviewed environment
python -m pip install -r requirements.txt   # pinned deps only; no network after this step
```

## 1. Gate and registration

```powershell
python tools/research/run_r4.py check
# Exit 3 = CLOSED: stop and route the printed G1-G4 failures; do not edit pins locally.
```

If G3 reports "not registered" and Codex has cleared the prereg:

```powershell
python tools/research/run_r4.py register --author claude-code --cairo-date <CAIRO_DATE>
# then commit the ledger lines through a reviewed PR, re-checkout the new accepted master, re-run check
```

## 2. M3 reproduction (uncapped, legacy-only flat funding adapter)

```powershell
$out = "research_evidence\runs\r4\m3"
python tools/research/run_r4.py m3 --store <LEGACY_STORE> --cost both --out-dir $out `
    --author claude-code --cairo-date <CAIRO_DATE>
Get-ChildItem $out
Get-FileHash $out\* -Algorithm SHA256
```

- Writes `m3_repro_base.json`, `m3_repro_2x.json` and `progress-<run_id>.jsonl`.
- While running, only the progress file may be mirrored (signed checkpoint: commit, dataset identity, stage,
  integrity codes, artifact hashes).
- Acceptance: trade-for-trade match, |dR| <= 1e-9, same count, for both rows. Anything else = NOT REPRODUCED and
  step 3 does not start.

## 3. First time-correct long/short evaluation (development only)

Lands only after M3 is REPRODUCED and reviewed (R4b); today `run_r4.py pit` refuses with exit 4.
Expected shape (subject to the R4b contract):

```powershell
$out = "research_evidence\runs\r4\pit-dev"
python tools/research/run_r4.py pit --out-dir $out --author claude-code --cairo-date <CAIRO_DATE>
```

- Uncapped primary first; the 180-bar time cap is its own preregistered variant.
- Actual timestamped funding; gold-spot and tokenized-gold use their own R3 cost rows; slip coefficients
  unfitted, so every result is labelled SLIP-C-UNFITTED / provisional (PARK at best).
- Split reported as "development"; the holdout window is never opened by this command.

## 4. Seal and hand over

```powershell
python -c "import sys; sys.path.insert(0,'tools/research'); import progress as P, glob; [print(f, len(P.verify(f))) for f in glob.glob(r'research_evidence\runs\r4\**\progress-*.jsonl', recursive=True)]"
```

Every progress file must verify and end in `run_sealed` (or `run_aborted` with integrity codes). Hand over the
progress file plus artifact SHA-256s; metrics are posted only after the seal.
