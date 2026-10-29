# FACTORY-F01F02-REPRO: independent reproduction contract (F01 `ema_mom` / F02 `exp_trend` long, fixed-18)

**Status: DRAFT — unsigned.** `factory_f01f02_repro_contract.v1.json` is authoritative; the JSON wins any conflict.

Assignment: Codex 6488285235. Evidence: Codex 6468259657 (#219), 6442094571 and 6443422894 (#13).

## What this is

This contract lets a lane other than Build (Evidence / Cowork) re-derive the two Strategy Factory development packets
from first principles and get a mechanical verdict for each check: REPRODUCED, DIVERGED or INCONCLUSIVE. The
reproducer must not import Build's strategy, backtest or runner code. Build drafted this contract only. It does not
rerun or reinterpret the evidence.

## Frozen inputs

- **Window:** 2024-10-14T00:00Z to 2026-09-28T00:00Z, end-exclusive. Timeframe 4h, with a 220-bar warm-up.
- **Slices:** S1 runs to 2025-06-16, S2 to 2026-02-16 and S3 to 2026-09-28. Each slice is an independent run with
  its own warm-up and a fresh $500 account.
- **Universe (fixed-18):** CORE-20 of `static-top40-minhist365-contract-20261011`, minus HYPEUSDT and PUMPUSDT, which
  are INCOMPLETE.
- **Data:** each of the 20 4h cache files is pinned by sha256 in the JSON.
- **Costs:** 5 bp taker fee per fill and 2 bp adverse slippage per fill.
- **Funding:** a legacy flat 0.5 bp per 4h bar per open position.
- **Exchange floor:** the legacy floor, minimum notional only.
- **Account:** $500 start, 1% risk, at most 4 positions, 10x leverage and an 8% Cairo-day halt.
- **Code:** the packets record `b5957d58`, which is not on master. The runner `tools/research/run_breadth_candidate.py`
  (sha256 `dad619aa…`) and its helper are byte-identical on master `184ec2e`. All the semantics the reproducer needs are
  unchanged there.

## Expected values (full window)

| Cell | Key / mgmt | Trades | Net R | Mean R | PF (USD) | Max DD (R) | Slices |
|---|---|---:|---:|---:|---:|---:|---|
| F01 (primary) | `ema_mom`, stop 2.5 ATR | 160 | +101.091624 | 0.631823 | 2.773052 | 7.551214 | + / + / + |
| F02 | `exp_trend`, stop 2.5 ATR, target 3R, 180 bars | 171 | +56.692730 | 0.331536 | 1.996836 | 7.619501 | + / + / + |

**Overlap:** 131 exact shared fills, and Jaccard = 131 / (160 + 171 - 131) = 0.655. The two cells are one correlated
family with F01 as primary. Never give both full independent risk.

The overlap has a code-defined cause. `exp_trend` is `_one_side(ema_mom)`, so on the long side both cells emit the
same entry signals (228 raw each). Their trade lists differ only through management and slot occupancy.

The proposed fill key is (symbol, t_in, side). That key is not defined in code; see Q1.

## Semantics, tolerances and verdict

The JSON `semantics` block pins each of the following with file:function citations. Every item is marked
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

A result is DIVERGED, with the first divergent trade reported, if any of these fails:
- the (symbol, t_in, side) trade set must match exactly;
- t_out and the exit reason must match exactly;
- trade and raw-signal counts must match exactly;
- per-trade R and mean R must be within 1e-6, and net R within 1e-4;
- PF, max DD (R) and max equity DD must be within 1e-4;
- every slice sign must match;
- the overlap must match exactly.

A result is INCONCLUSIVE when an input hash mismatches or the run cannot complete. The overall verdict is
REPRODUCED only when every check is REPRODUCED.

## Forbidden

- Importing strategies, backtest, feasibility, engine or app, or any runner module.
- Any read at or after 2026-09-28T00:00Z.
- Network access beyond re-downloading public klines that are then checked against the pins.
- PAPER registration or runtime wiring.
- Full-risk dual allocation.
- Writing order or trade ids.
- Credentials or mainnet.
- Tuning.

## Open questions for Integration

Q1–Q8 are in the JSON:
- Q1: the overlap key.
- Q2: the packet commit is not on master.
- Q3: no committed data manifest exists.
- Q4: which full-window packet is the packet of record.
- Q5: the uncommitted slice split.
- Q6: the DD tie order.
- Q7: whether a re-download is acceptable.
- Q8: whether the secondary values are binding.
