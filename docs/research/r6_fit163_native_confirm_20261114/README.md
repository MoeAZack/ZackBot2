# R6-TNET163-FIT-D01 — Claude native confirmation

**Verdict: FIT_PASS confirmed independently.** Label: native confirmation of a DEPENDENT deployment-fit variant on CONSUMED-calendar data. Not mainnet approval, not proof of future profit, not an activation.

## What ran
- Wrapper `research/extra31_screen/r6_fit163_confirm.py`, frozen at **2dfcfdae** before any outcome (sha256 a5d41aea…).
- Path: `longscout_v2_run` → `longscout_run.worker` → `sieve_v1_panel.worker` → **native `backtest.run` of source acc8d0ee**, r6_regime_flip_4h unchanged (case table longscout_v6_cases.json, eb5f615c). No import of Codex's fit runner (7a81c7fd) or its review code.
- Symbols: Codex freeze 7a81c7fd `freeze.json` (sha256 50f6e767…) verbatim: 163 = full165 minus ICPUSDT, TLMUSDT (both fresh coins), so subsets are fresh132 + extra31. Wrapper aborts on any symbol missing a filter or any list mismatch; nothing dropped silently.
- Filters: the only swap — exported source's `data/exchange_rules_testnet.json` replaced with frozen `rules.json` (sha256 a34e7950…). Order 0: 151 attempts, 151 executed, 0 rule-blocked.
- Data: pinned D01 manifests fresh fb0c0572…, extra 99af9bfb…; every CSV hash-checked. Staged from moe's PC to the cloud; run in the cloud sandbox (pandas 3.0.5, numpy 2.5.3).
- Unchanged: warmup 220, T0/split, $8 risk at 10m equity, $3,333.33 cap, max 1, the 40 fixed orders, 7/15/23 bp, funding, rule-v1 gates incl. order0 ≥30 per half, frozen subset gate.

## Result (median of 40 orders, path scenario)
| | Claude native | Codex fit (7a81c7fd) |
|---|---|---|
| closes n | 150.5 | 150.5 |
| mean R15 | +0.328632 | +0.328632 |
| mean R23 | +0.310232 | +0.310232 |
| early / later R15 | +0.405188 / +0.291998 | same |
| ex-best-coin R15 | +0.187201 | +0.187201 |
| fresh132 / extra31 R15 | +0.244731 / +0.616750 | same |
| order 0 halves | 86 / 64 | 86 / 64 |

- All 40 orders × 6 stats: max abs diff **0** vs Codex `fit163/per_order_stats.json`; subset means max diff 2.2e-16.
- Order 0: all 150 closed trades identical (sym, side, entry/exit bar, exit reason), pnl/pnl15/pnl23/turnover max diff 0.
- Worst-path diagnostic also passes all gates (medians equal to path).
- Control: the same environment re-ran the original 165 case; all 40 orders equal the published 10486f35 result exactly (WIDE165_PASS).

## Risk disclosure (not gates; no post-hoc gate added)
- Spread across orders: mean R15 p10 +0.129, min +0.030, max +0.793.
- Order 0: sum +24.72 R15 (long +15.13, short +9.59), closed maxDD 18.15 R15 (~$145 at $8), 0.70 closes/week.
- **Tail concentration is material and slightly worse than full165:** ex-top-3 coins median −0.024 R15 (18/40 orders positive); ex-top-5 median −0.111 (8/40). Order 0 top coins: GRT +15.0, TRB +8.0, ARB +7.0, 1000BONK +5.6, BEL +5.5 R15.
- One pass in 38 attempts; consumed calendar.

## Not checked
- Terminal open-position marks (Codex reports them separately; not recomputed here).
- Live runtime path (D01 1h continuity / qv in the PAPER module, sizing receipt) — that is the R6-PAPER-01 lane.
- No adversarial-review agent or mutation check: research evidence only, no engine code changed.

No account, key, position, runtime, scheduler or trading action. Not merged.
