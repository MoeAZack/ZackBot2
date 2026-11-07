# QBANK-01 — v0 baseline seal and finite entry matrix (preregistration DRAFT, #202)

Status: **DRAFT for Codex to freeze.** No cell has run and no QBank result has been read. Once Codex freezes the B1–B3
definitions below, the frozen copy is hashed, and only then are the adapter variants built. Any wording change after the
freeze is a new preregistration.

## 1. Failed development baseline: `quick_bank_runner.v0`

**Code** (git blob ids at the installed PAPER build `cc475a4`, #198 build 20261023-223855):

| file | blob |
| --- | --- |
| `strategies.py` (`sig_quick_bank_runner`, `QB`, `SCALP_RT_COST`) | `27e1beb233d3ff8acfa0a3556ce5da771aeabbfd` |
| `engine.py` (scalp slots, management) | `fdfa7e93700c4a53c363d96187b46ec3b9e1ee51` |
| `replay.py` | `591df2ac32f098fe481b85f51c8f861dbc969925` |
| `risk_fill.py` | `36e1b423d0c338508e4d8eb3f579dd7b4114f0af` |
| `feasibility.py` | `99f304140e55e39fcbf3a909cd53c7a9bd94645a` |

**Signal (15m, both sides).**
- Long: EMA20 > EMA50, close > EMA50, low ≤ EMA9, close > EMA9, and a bullish candle.
- Short: the exact mirror.
- Exit on an EMA20/50 flip.

**Management `QB`:**
- stop 1.0 ATR;
- 70% banked at 0.8R, with the stop moved to breakeven at 0.8R;
- the 30% runner targets 2R, with a 12-candle cap.

**Sleeve.** The code default is `engine.scalp_sleeves()`:

```
{"enabled": false, "id": "SC-QBANK", "key": "quick_bank_runner", "max_pos": 2, "mgmt": {}, "risk": 0.01,
 "share": 1/6, "sides": "both", "symbols": "core8", "tf": "15m"}   sha256 55aa665b…0cab (canonical JSON)
```

**Integration seals the as-run sleeve.** The code default above is not necessarily what ran. Integration seals the as-run
sleeve (any PAPER overrides, e.g. `enabled: true`) from the runtime, as it did for the EDGE01 frozen config, and publishes its
sha256. Build does not read the PAPER data dir. B0 runs that sealed sleeve, with `sides` set per cell.

**Forward rows.** The deduplicated measured forward rows (57 closes at 00:34 Cairo) are sealed by Evidence. They are a
development baseline only, never a holdout.

## 2. Run grid (identical for every cell)

- **Adapter and data:**
  - accepted SCALP-EXEC-15M adapter;
  - frozen `CORE-10-ELIGIBLE-9` manifest and symbols;
  - window `2024-10-14/2026-09-28`;
  - 1m-under-15m causal execution.
- **Costs:**
  - actual base costs, plus a 1.5× cost stress row;
  - latency 0 s primary, plus a 60 s stress row.
- **Sides:** long and short are **separate cells**.
- **Scheduling:** queued after the active range/exp reruns, one cell process per key and side. Partial packets are never read.
- **Gates:** EDGE-01A v1 development gates, unchanged. No sealed holdout read unless a cell passes development, walk-forward,
  stability, concentration and tail gates.

## 3. Finite entry matrix (no parameter sweep)

Each variant only *removes* entries from B0. The signal, sizing and management are byte-identical to v0. Every rejection is
counted by reason and reported with trades/week.

### B0: exact v0

As in section 1.

### B1: B0 + cost admission

Admit only when **expected edge ≥ 5 × round-trip cost** at the decision cutoff.

- **Expected edge** = the v0 plan's blended planned reward in price units, at the cutoff:
  `stop_dist × (tp1_frac × tp1_r + (1 − tp1_frac) × tp_r) = 1.0·ATR14 × (0.7·0.8 + 0.3·2.0) = 1.16·ATR14`, where ATR14 is
  computed on closed 15m candles.
- **Round-trip cost** = the adapter's actual base cost model at the cutoff price: taker fee on entry and exit, plus the
  calibrated slippage for that symbol. It is not the `SCALP_RT_COST` constant.
- **Rejection reason:** `b1_edge`.

### B2: B1 + regime alignment

Point-in-time 4h regime alignment of both the symbol and BTC.

- **Long** requires, on the **last 4h candle closed at or before the cutoff**, both the symbol close > its 4h EMA50 and the
  BTCUSDT close > its 4h EMA50. **Short** is the mirror (both below).
- **4h candles** are aggregated from complete 15m candles on the UTC 4h grid. An incomplete 4h candle is never used.
- **Rejection reasons:** `b2_symbol_regime`, `b2_btc_regime`.

### B3: B2 + re-entry cooldown

No new entry on the same symbol **and** side within **4** 15m candles after that symbol/side's previous lot fully closed.

- **Counting:** candle closes are counted from the final exit's candle.
- **Causality:** it uses only exits at or before the cutoff.
- **Rejection reason:** `b3_cooldown`.

### Proposed choices for Codex to confirm or replace before the freeze

1. **B1 edge.** Use the *blended planned reward* (1.16·ATR), or the more conservative *first-target distance* (0.8·ATR)?
2. **B2 regime.** Use the 4h EMA50 close test as the regime definition, or the existing engine `btc_down` (EMA200 on the signal
   timeframe) for the BTC leg?
3. **B3 cooldown anchor.** Measure the cooldown from the previous lot's *final exit* (as written), or from its *entry*?

## 4. Management comparison (only after an entry cell passes)

Run this only if a B-cell clears the development PF, expectancy and stress gates. It compares v0 early-bank/runner management
against a whole-position fixed-target/time-cap alternative on that cell. That alternative is defined and preregistered
separately before it runs. Exit tuning may not rescue an entry that is negative before costs.

## 5. Reported per cell

Reported for every cell, with long and short separate:
- trades/week and rejected signals by reason;
- PF and net R/trade;
- costs/R;
- max DD and CVaR95;
- regime and coin concentration;
- every loss below −1.5R, each explained.

All cells also carry the causal cutoff, entry-minute and stop-order mutations, exact runtime parity (B0 vs the v0 engine) and
actual-cost completeness.

## 6. Compute budget (for Integration's queue)

- **Grid size:** 4 variants × 2 sides = 8 primary cells (latency 0), plus 8 latency-60 stress cells. The 1.5× cost row is
  derived inside each packet.
- **Measured runtime:** this machine took about 9–10 h per cell for the #187 runs.
- **Proposed order:** B0 long + B0 short first, since together they are the exact baseline and runtime-parity proof. Then the
  B1–B3 matrix, two processes at a time. The latency-60 stress cells run only for cells that pass the primary development gates.

## Deferred

- AI authority;
- unrestricted tuning;
- USDC routing, mainnet and new venues;
- the strategy-level PAPER auto-park proposal (separate reviewed contract, #202 item 7).
