# Candidate 111 VHF trend-onset Stage-A result

Verdict: **REJECT / BLOCK**. Research-only and default-OFF; no PAPER, runtime, fold, or holdout action.

- Branch: `codex/vhf-c111`.
- Frozen head: `d1846dc03ea22942f776d4a734c5d0b6772fa45e`; tree `b2b870f90d26cd6d9f57465e55f21a11723adf4e`.
- Preregistration SHA-256: `a11a8afaa0768c98d143d5557bd983ecd5ab621cb91e7f6e412ccbb70eb03bd6`.
- Artifact: `C:\Dev\ZackBot_cowork_data\vhf_c111\vhf-long-dev.json`.
- Artifact SHA-256: `7fc40016aa3c00b0bd14af7bc85e196d5f0c8e82b4d5bd7db60965578bf49f52`.
- Canonical packet SHA-256 (independently reproduced): `c9bd2ed12e3c3f30c9394c4edde21347b5d927d367bf0b1ce86adfe9172e5c99`.
- Funding manifest digest: `54912d9d2bf6e6fc45bc75c0553f0bd867e877972beeb3452785937379baf1e7`.

The frozen CORE-20 development run closed 461 long trades over all 18 symbols: 296 above and 165 below the BTC 4h
EMA200 regime boundary. Pre-cost gross was +$82.863672. Fees were $93.762105 and actual signed funding was $9.890629,
leaving dollar net P&L -$20.789063, net +0.673467R (mean +0.001461R), PF 0.982102, and max drawdown 37.780966R.
The opposite signs of dollar P&L and summed R arise from the runner's per-trade risk normalization and are both preserved.

Passed gates: pre-cost gross positive, mean net R positive, sample >=100, symbols >=8, and >=20 closes in each regime.
Failed gates: PF >1.05 and max drawdown <=15R. Stage-A PASS is false and isolated PAPER eligibility remains BLOCK.
The directional path-efficiency onset is near flat but fails payoff and risk materially; no window, threshold, filter, or
exit alteration can rescue this cell. No parameter tuning is authorized.

Focused tests: 6/6 before freeze and 6/6 after result recording. The sealed holdout was not accessed.
