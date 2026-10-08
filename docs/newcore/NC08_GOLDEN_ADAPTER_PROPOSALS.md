# NC-08 golden adapter: two proposals for a Codex ruling

**State:** PROPOSAL (doc only). No case, expectation, ledger record or tier changes with this file.
**Evidence:** `tests/golden/goldenlib/adapters/newcore_sim.py` and `tests/newcore_management/test_golden_newcore.py` on
`nc-management-driver`. The adapter runs each golden case through `ManagementDriver` + `step()` on a zb-path/1 path venue.
**Time:** 08 Oct 2026, Africa/Cairo.

## 1. G-TP1-ZERO-L-01: printed precision of the expectation

| | pnl | R |
|---|---|---|
| Case expectation (as printed) | `19.385932` | `1.9385932` |
| Exact (Decimal; the NC-07 core, and the hand formula in the case notes) | `19.38593201` | `1.938593201` |
| Difference | 1e-8 | 1e-9 |

`compare` allows `EPS = 1e-9` plus a per-trade `tol` that names the adapter. The NEWCORE result is exact, so it fails only
because the expectation was printed to 6 dp. The legacy adapters are `not_applicable` on this case, so they are not
affected.

The case notes already give the formula: `pnl = 5 x (103.999196 - 100.02) - 5 x (100.02 + 103.999196) x 0.0005`, which
evaluates to exactly 19.38593201.

**Options**
- **A (recommended): a `correction` record** that prints the exact values, `pnl "19.38593201"` and `R "1.938593201"`.
  This is a contract change: MANIFEST plus an appended CORRECTIONS record with a rationale. No behaviour changes, and
  the expectation becomes exact again.
- **B: a per-trade `tol`** `{R: 1e-9, pnl: 1e-8, adapters: [newcore_sim], why: "expectation printed to 6 dp"}`. This
  keeps the printed digits but adds a tolerance whose only cause is rounding.

## 2. risk_cap mapping for legacy_replay-sized cases (`budget_plus_costs`)

The NEWCORE plan cap is **cost-inclusive**, per Codex ruling 1. `planned_risk` is the entry plus the reserved add, filled
with adverse slippage, plus the stop exit with slippage, plus the taker fee on every fill.

The golden cases size with `account.sizing: legacy_replay`. That sizing makes the **price** risk to the stop equal to
`risk_usd = equity x risk x share` (10 USDT in the pack) and leaves costs outside it. A legacy-sized plan is therefore
always slightly above a cap of exactly 10 USDT.

| Cap policy | Effect on the pack (evidence in `test_golden_newcore.py`) |
|---|---|
| `budget`: cap = risk_usd | Every costed single-entry case is **refused** at the plan: the entry alone, with fees and slippage, carries 10.03 to 10.61 USDT of risk. The one-add cases **lose their add** (planned 10.51 / 10.53 > 10; `PlanNote add_over_cap`). The two-add historical cases fit (planned 4.62) |
| **`budget_plus_costs` (recommended)**: cap = risk_usd + (planned_risk - price_risk) | 14 cases PASS, long and short: stop, gap stop / tp, time, dca slip, one-add dca. G-GAP-DCA-L/S-01 reproduce the recorded `NC07-ONE-ADD-CAP` observed lists exactly. TP1-ZERO differs only as in section 1 |
| NEWCORE sizing net of costs (NC-06) | The cap stays risk_usd and the quantities shrink by the cost buffer. Every expectation changes: these would be new NEWCORE cases, not the legacy contract |

**Recommendation.** For the **golden adapter only**, map `risk_cap = risk_usd + reserved costs`, where the reserved
costs are `planned_risk(plan) - sum(q x |fill - stop|)`. This keeps the legacy meaning of a case's budget ("1R = price
risk to the stop") while NEWCORE still enforces its cost-inclusive cap on the actual fills (the add re-check, Codex
ruling 1).

Live sizing (NC-06) should size net of costs so that the live cap is risk_usd itself. That belongs to a separate NC-06
contract and is not part of this ruling.

**Asks for Codex**
1. Rule A or B for G-TP1-ZERO-L-01.
2. Accept `budget_plus_costs` as the NC-08 golden adapter's cap mapping, or rule otherwise.
3. Once ruled, the adapter can be registered in `adapters.CAPS` / `adapters.get`. This needs the
   `set(CAPS) == set(LEGACY)` assertion in `test_golden.py` to change. After that, the passing cases can move their
   `newcore_sim` status from `pending_adapter` to `required`, each through its own correction record.
