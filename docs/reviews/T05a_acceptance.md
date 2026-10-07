# T05a causal trade audit — acceptance

*Accepted 2026-10-07 08:03 Africa/Cairo by Codex; implementation head `4991154`, reviewed head `d6f6a4d`, final branch head pending this document commit.*

## Verdict

**Accepted for PAPER/testnet and ready to merge.** No open P1/P2 finding. T05a is measurement-only; mainnet remains out of scope.

## Evidence

- Exact-head GitHub gates passed: verify fast, CodeQL, tests, both strict replay/UI slices and aggregate verify full.
- Official Windows focused T05a/T05b set: **152/152 passed** after the explicit UTF-8 portability fix.
- Broader Windows audit/safety/fills/outage/leverage set: **503/503 passed in 109.04 s**.
- Guarded installer completed all eight stages; build `20261007-075404` self-tested, installed and authenticated as running.
- Runtime at 08:02 Cairo: PAPER, engine and exchange `ok`, zero open incidents or unprotected lots; all four pre-existing lots remained present and protected.
- The read-only `/api/audit_summary` endpoint responds without error, the escaped/collapsed Trade audit card is in the installed panel, and `trade_audit.jsonl` plus its start marker were created.
- Runtime audit evidence contains coverage, funnel, hold-evaluation and excursion checkpoint events. The summary window correctly remains at zero until a post-T05a trade closes.
- No setting, capital cap, strategy decision, existing position quantity or stop protection was changed by the acceptance run.

## Resolved finding

The first Windows run found three deterministic CP1252 test failures. The branch now uses explicit UTF-8 for both source reads, the JavaScript temporary file and subprocess text decoding; the corrected Windows sets pass.

