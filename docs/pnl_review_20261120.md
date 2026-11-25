# P&L review — 20 November 2026 Cairo

Scope: read-only PAPER/testnet diagnosis; proposed diagnostics only. No account 1 access, runtime installation, configuration/scheduler/watchdog/account/order changes, stops/restarts, merge, mark-ready or mainnet action. The R6 canary state, LEADFWD checkout and task were not accessed. A4 inspection was limited to requested equity/history/funding files, scalar capital settings, and source code; no A4 state snapshot or canary actions.

## Decision

**NO-GO for mainnet.** The supplied small-sample gains do not qualify a pilot. The A2 refusal is an intentional ownership safety fence, and the P&L comparison used booked closed returns against measured marked equity. No defect in equity arithmetic has been established by these files.

## A2 OLD3

Read-only A2 state/history and audit inspection reproduced all 54 `true_book / book_incomplete` rejections. There are zero lots or working entries, no pending row revisions/unowned funding, and a successful funding read. One manual close has no book or cohort stamp and closes within the current cohort window. `_tb_rows` deliberately includes it; `true_books.compute` produces `unowned_account_activity` with one row for every book. The deployed `true_books.py` matches repository master.

This is not evidence that the completeness calculation should be relaxed. Keep new-risk refusal. Do not delete/restamp the row, ignore unowned activity, disable TRUE_BOOKS/PAPER_BOOKS or infer book ownership from hypothetical FWD-01 profit. A diagnostic patch exposes safe reason names behind the generic refusal. Resolving ownership/capital attribution in the running cohort remains a separate reviewed operator action requiring the owner's approval under this handoff. The FWD-01 result is a separate hypothetical model, not realized missed profit or authorization to catch up orders.

## P&L accounting basis

The deployed A3/A4 `actual_costs.py` matches this source; AST comparisons also match `capital_info`, `bot_unrealized`, `equity` and `record_equity`.

For measured closed rows:

`net = pnl_gross + pnl_fill_adj - fees_actual + funding_actual`

`history[].pnl` is the booked figure. `equity.json` records guard marked capital, not a closed-only ledger balance:

`max(0, min(exchange margin balance, base + measured closed net + adjustments + open price P&L))`

Pending measured closes instead hold capital at the previous reconciled checkpoint. Native funding already attributed into closed rows must not be added again from `funding.jsonl`. The raw funding stream can also include open-lot payments. The displayed guard-capital formula is not a complete independent NAV audit of open realized partials, open fees or funding.

Read-only samples around **15:17 Cairo** (not the earlier handoff's exact snapshot):

| Component | A3 | A4 |
|---|---:|---:|
| Closed rows, all measured | 6 | 19 |
| Booked closed P&L | +63.3330 | -8.7186 |
| Booked gross | +65.1263 | +16.0422 |
| Measured fill-price correction | -1.80287291 | -45.85078687 |
| Actual fees | 1.43509724 | 19.81011865 |
| Native funding already in closes | +0.24954216 | -0.68859210 |
| Measured closed net (row-view rounded) | **+62.1378** | **-50.3074** |
| Base + measured closes, zero adjustments | 4062.1378 | 3949.6926 |
| Latest recorded marked equity | 4072.83 | 3958.34 |
| Recorded equity minus closed capital | +10.6922 | +8.6474 |

A3 sample is 15:17:55 Cairo. A4 sample is 15:17:53 Cairo. These differences compare recorded equity with a closed-only subtotal. Use the open mark and margin balance captured at the equity sample to determine how much each contributes.

The large A4 discrepancy is substantially explained by using booked instead of measured closes. In particular, its fill correction is -45.85, offset partly by lower actual commissions. A3's measured correction also changes its closed total. Do not relabel the supplied booked gains as measured results, or double-count closed funding.

## Run the marked-equity tie-out

Run once per account with staged ledger inputs and a sanitized open-positions JSON. The table shows booked and measured closed P&L, the open price mark, cap and floor effects, computed and recorded equity, residual, and status. Omit `--format table` for the JSON report.

```powershell
python -B tools/pnl_reconcile.py "<ledger directory>" --open-positions "<open-positions.json>" --format table
```

The open-positions file is a single object with the following schema. Amounts must be finite JSON numbers in the same quote currency as recorded equity. Optional amounts default to zero. The sample timestamp must be an ISO 8601 string with an explicit UTC offset or `Z`. Extra fields are rejected.

| Field | Required | Meaning |
|---|---|---|
| `sample_timestamp` | Yes | Time the open price P&L and margin balance were observed. |
| `open_price_pnl` | Yes | Signed aggregate price P&L on quantities still open, using measured entries and sample-time marks. |
| `exchange_margin_balance` | Yes | Signed total exchange margin balance at that sample. |
| `open_realized_partials` | No | Signed realized price P&L on partial exits for lots still open. |
| `open_fees` | No | Nonnegative fees attributed to lots still open. |
| `open_funding` | No | Signed funding attributed to lots still open. |

Sample JSON with synthetic values:

```json
{
  "sample_timestamp": "2026-11-20T15:17:55+02:00",
  "open_price_pnl": 10.6922,
  "exchange_margin_balance": 4100,
  "open_realized_partials": 0,
  "open_fees": 0,
  "open_funding": 0
}
```

The calculation matches the positive-base guard-equity code, including cent rounding before the margin cap and again when equity is recorded. The cap and zero-floor effects are signed changes applied in that order. Open realized partials, fees, and funding appear as audit context in JSON. The guard-equity formula adds only the open price mark. `funding.jsonl` is deduplicated attribution evidence. Measured closed net already includes closed-row funding.

- `TIED_OUT` means the samples are within 120 seconds and the absolute residual is within the larger of $1.50 and 0.05% of recorded equity. Residual is recorded equity minus computed equity.
- `GAP` means the inputs support the calculation and the residual exceeds that tolerance. The report points to the cap, floor, or open price mark to investigate first, along with sample alignment.
- `UNRESOLVED_MARKED_COMPONENTS` means evidence is missing, stale, or incompatible with the calculation. Examples include pending closes with no checkpoint, closes after the equity sample, and a whole-account capital basis. The command keeps its original aggregate diagnostics when `--open-positions` is omitted.

Synthetic fixtures using the published 20 November aggregates produce `TIED_OUT` for A3 with a $0.00 residual and A4 with a +$1.00 residual. The synthetic open marks and cap values demonstrate the calculation. A historical tie-out requires a contemporaneous aggregate from the original sample.

## Amended pilot gate

These are minimum review criteria, not automatic mainnet authorization:

1. Freeze the exact strategy/build, symbols, side(s), timeframe, sizing/capacity, entry/exit/protection rules, cost model, regime classifier and acceptance thresholds before new evaluation. Count all attempts; do not call previously consumed data a fresh holdout or tune after observing it.
2. At least 30 independent closed lots of that exact strategy, with two weeks of causally identified non-bear observations relevant to the pilot. Do not count partial exits as separate trades or borrow other strategies' samples. For a two-sided pilot, require adequate side/regime samples separately; a bear-only short win is not evidence of a long edge. Thirty closes is a minimum sample, not statistical proof.
3. Positive net expectancy at conservative 15 bp **per executed side**, with adverse-fill modeling and signed native funding, plus positive overall economics under the frozen 23 bp stress gate. Report actual measured fills/costs separately; replace the fee component when repricing, rather than adding a full modeled fee on top of actual commissions. Include open/terminal marks and exchange filters; a funding proxy alone does not satisfy the native-funding gate.
4. Top three winning lots contribute less than 50% of total positive net profit, with the denominator defined as total net P&L including losing closes; report the exact concentration and a remove-top-three sensitivity. Reject an undefined denominator or nonpositive net, rather than adjusting the criterion afterward.
5. Marked drawdown stays inside both the preregistered pilot risk budget and the relevant backtest envelope on comparable capital/cost/mark definitions. Historical maximum DD is not a guaranteed future bound. Enforce bounded risk, protection, observation and rollback criteria independently of profitability.
6. PAPER/testnet versus matched frozen FWD-01 causal signal, admission, sizing and exit reconciliation, with every difference explained. Match universe/capacity and input boundaries first. Model fills, actual testnet fills and funding proxies are distinct; quantify their differences rather than requiring fictitious identical cash. No mainnet query or activation is part of this review.
7. Zero open accounting/ownership/protection/execution blockers, independent evidence, exact-head acceptance gates and explicit owner mainnet authorization. Existing MAINNET NO-GO remains.

ROADMAP FIT: correct the economic evidence and expose a confirmed admission blocker without changing protection or account behavior.
DEFERRED: runtime attribution repair/install, historical validation with actual same-time aggregates, strategy retuning, activation and mainnet.
