# T05a causal trade audit — Codex review

*Updated 2026-10-07 07:31 Africa/Cairo; exact reviewed head `49911543948c0d6e0eecce0e196e8dd8450fcb37`.*

## Verdict

**Code review accepted — no remaining P1/P2 findings. Merge still requires the repository's full gate and the documented
measurement-only runtime acceptance.**

### Resolved P2 — three new tests used the Windows locale instead of UTF-8

The official Windows build environment ran the focused T05a/T05b set and produced **149 passed, 3 failed**. All failures
are deterministic encoding errors in `tests/test_trade_audit.py`:

- lines 299 and 2383 open UTF-8 `app.py` without `encoding='utf-8'`, so Windows CP1252 cannot decode the file;
- line 2419 creates a temporary JavaScript file without `encoding='utf-8'`, so CP1252 cannot encode the Unicode minus
  character copied from the panel source.

Use explicit UTF-8 for both source reads and the `NamedTemporaryFile`, add/retain a Windows-safe regression, and re-run
`test_trade_audit.py`, `test_t05a_t05b_interaction.py`, and `test_t05b_startup_outage.py` in the official Windows build
environment. Claude fixed all four Windows boundaries (the two source reads, temporary JavaScript file, and subprocess
text decoding) at `4991154`. Codex reproduced the corrected focused set in the official Windows build environment:
**152 passed in 62.99 s**.

## Substantive review

- The audit remains observe-only: no trading decision reads an audit field, and audit exceptions are contained.
- Mark-loop work is bounded and memory-only; checkpoint persistence uses the existing non-blocking writer contract.
- Excursion paths, state snapshots, gaps, missing samples, record/window sizes, and missed-short memory are bounded.
- MFE/MAE and give-back use recorded mark samples rather than candle extremes; unavailable causal inputs are labelled
  partial/unknown instead of guessed.
- Entry/add regime context is limited to already-closed cached candles. The declared policy set is stored before later
  evaluation, so the new comparisons do not select a rule with hindsight.
- The API and collapsed Trades card are read-only, escaped, and covered by the Windows rendering test.
- The increase of the maximum closed-audit-record test bound from 6 KB to 9 KB is supported by a 5,000-record window
  below the existing 10 MB limit. Files remain bounded by both line and byte rotation.

The UI card should remain in T05a: it exposes the measurement output without adding a control or trading path.

## Evidence

Windows evidence:

- Initial head `8f5577b`: **149 passed, 3 failed in 74.11 s**; all three failures were the resolved UTF-8 portability defect.
- Corrected head `4991154`, focused T05a/T05b set: **152 passed in 62.99 s**.
- Corrected head `4991154`, broader safety set (`trade_audit`, T05a/T05b interaction, startup outage, safety, fills,
  outage, leverage auto): **503 passed in 109.04 s**.
- GitHub fast verification and CodeQL: green at the exact corrected head.

No installer, bot restart, order, or running-app mutation occurred during this review.
