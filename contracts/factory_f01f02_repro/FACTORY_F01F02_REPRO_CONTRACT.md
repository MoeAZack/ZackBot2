# FACTORY-F01F02-REPRO: independent reproduction contract (F01 `ema_mom` / F02 `exp_trend` long, fixed-18)

**Status: SIGNED — Codex ruling 6489310660 (Q1–Q8).**
`factory_f01f02_repro_contract.v1.json` is authoritative; the JSON wins any conflict.

Assignment: Codex 6488285235. Evidence: Codex 6468259657 (#219), 6442094571 and 6443422894 (#13).

## What this is

This contract lets a lane other than Build (Evidence / Cowork) re-derive the two Strategy Factory development packets
from first principles and get a mechanical verdict for each check: REPRODUCED, DIVERGED or INCONCLUSIVE. The
reproducer must not import Build's strategy, backtest or runner code. Build drafted this contract only. It does not
rerun or reinterpret the evidence.

## Frozen inputs

- **Window:** 2024-10-14T00:00Z to 2026-09-28T00:00Z, end-exclusive. Timeframe 4h, with a 220-bar warm-up.
- **Slices (frozen):** S1 runs to 2025-06-16, S2 to 2026-02-16 and S3 to 2026-09-28. Each slice is an independent run
  with its own warm-up and a fresh $500 account.
- **Universe (fixed-18):** CORE-20 of `static-top40-minhist365-contract-20261011`, minus HYPEUSDT and PUMPUSDT, which
  are INCOMPLETE.
- **Data manifest:** the JSON's ordered per-file sha256 and row counts are the committed manifest. A canonical digest
  covers them: `dec91dfb9f64e871c462ab43816b7878cca0941d811c214cc81ea49f2d2c68bd`.
  - Every input is verified before any computation. A missing or mismatched input makes the run INCONCLUSIVE.
  - REPRODUCED requires the exact pinned bytes. A row-equal re-download may be used for diagnosis only; the run stays
    INCONCLUSIVE.
- **Costs:** 5 bp taker fee per fill and 2 bp adverse slippage per fill.
- **Funding:** a legacy flat 0.5 bp per 4h bar per open position.
- **Exchange floor:** the legacy floor, minimum notional only.
- **Account:** $500 start, 1% risk, at most 4 positions, 10x leverage and an 8% Cairo-day halt.
- **Code:** `b5957d58` is the packet-provenance commit and does not need to be merged. The citation base is master
  `f189ff7`.
  - The JSON pins the sha256 of the runner, its helper, `pit_run.py`, `strategies.py`, `backtest.py` and
    `feasibility.py` at that commit.
  - Any mismatch makes the run INCONCLUSIVE.
- **Packet of record:** `corrected_core20` is binding. The `--symbols` packet is report-only and never substitutes for
  the target trade rows or hashes.

## Expected values (full window)

| Cell | Key / mgmt | Trades | Net R | Mean R | PF (USD) | Max DD (R) | Slices |
|---|---|---:|---:|---:|---:|---:|---|
| F01 (primary) | `ema_mom`, stop 2.5 ATR | 160 | +101.091624 | 0.631823 | 2.773052 | 7.551214 | + / + / + |
| F02 | `exp_trend`, stop 2.5 ATR, target 3R, 180 bars | 171 | +56.692730 | 0.331536 | 1.996836 | 7.619501 | + / + / + |

**Overlap (amended by ruling 6491343078):** on the round-trip key (symbol, t_in, t_out, side), 131 shared and Jaccard = 131 / (160 + 171 - 131) = 0.655. On the entry-stream key (symbol, t_in, side), recorded separately, 160 shared out of a union of 171 (Jaccard 160/171 = 0.935673). Every F01 entry is also an F02 entry.
- The overlap headline (131 / 0.655) uses the round-trip key (symbol, t_in, t_out, side); the entry-stream key (symbol, t_in, side) is reported separately (160 / 171). Both count closed trades only; positions still open at END are excluded.
- Entry fill price, the exit reason and R are reproduced separately but are not part of either overlap key.
- The two cells are one correlated family with F01 as primary. Never give both full independent risk.

The overlap has a code-defined cause. `exp_trend` is `_one_side(ema_mom)`, so on the long side both cells emit the
same entry signals (228 raw each). Their trade lists differ only through management and slot occupancy.

## Semantics, tolerances and verdict

The JSON `semantics` block pins each of the following with file:function citations at `f189ff7`. Every item is marked
**code-defined**, because no preregistration fixes it:
- indicators;
- the entry signal and the signal exit;
- next-open fills;
- slot priority;
- sizing and the leverage cap;
- the per-bar order;
- the intrabar path with stop-first;
- the time exit;
- exit accounting;
- the stats definitions.

The drawdown order is (i_out, i_in), then fixed-18 symbol order as the final tie-break.

A result is DIVERGED, with the first divergent trade reported, if any of these fails:
- the per-cell trade set, keyed (symbol, t_in, side), must match the packet exactly (this is the trade-set check, not the overlap key);
- t_out and the exit reason must match exactly;
- trade and raw-signal counts must match exactly;
- the win rate must match exactly;
- per-trade R and mean R must be within 1e-6, and net R within 1e-4;
- PF, max DD (R), max equity DD and net P&L must be within 1e-4;
- every slice sign must match;
- the overlap must match exactly.

Symbols and slice additivity are report-only.

A result is INCONCLUSIVE when an input, code or packet hash mismatches, only re-downloaded data is available, or the run
cannot complete. The overall verdict is REPRODUCED only when every check is REPRODUCED.

## Forbidden

- Importing strategies, backtest, feasibility, engine or app, or any runner module.
- Any read at or after 2026-09-28T00:00Z.
- Network access beyond re-downloading public klines for diagnosis.
- PAPER registration or runtime wiring.
- Full-risk dual allocation.
- Writing order or trade ids.
- Credentials or mainnet.
- Tuning.

## Resolved questions (Codex 6489310660)

Q1–Q8 are closed. The rulings are in the JSON `resolved_questions`:
- Q1: the overlap key.
- Q2: code identity.
- Q3: the data manifest.
- Q4: the packet of record.
- Q5: the slices.
- Q6: the DD tie order.
- Q7: data recovery.
- Q8: the secondary checks.

No open questions remain.
