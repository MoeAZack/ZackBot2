# R6-PAPER-01 second review

Exact head `c337bed948573fffe62b345812735c380501150d`. Read-only review, 14 November 2026. [Ruling and bounded follow-up](https://github.com/MoeAZack/ZackBot2/pull/350#issuecomment-6875715059).

- Native Windows targeted tests: **95 passed in 42.75 seconds**.
- The corrected, unmodified receipt against actual account-4 source and copied settings: **PASS, exit 0**. Allocated risk $8; price100/ATR2 => quantity1.333, notional133.30; ATR0.01 => quantity33.333, notional3333.30; no book. Actual source/settings/install hashes unchanged.
- The first review's panel/shape/risk and owned-reinstall cases are corrected. Both signal-time and post-D01 checks exist; the entry wrapper now rechecks before starting the native entry attempt. Its recorded timestamp is the wrapper's attempt time, not necessarily the later exchange-send or fill time; distinguish these in the report.

## One remaining deadline-context defect

`r6_r2_fallback_witness.py` starts with a fresh/unregistered module, a saved EX14 short lot and one unrelated missing rule. Install raises and correctly registers exits-only, but `_CTX.resolve` and `_CTX.lot_entry` are still None. A falling regime has no raw short exit; 121 decision bars after the entry the canonical deadline is due but exits-only still returns sx=False. The new 124-bar backstop eventually acts, but this is not the intended canonical deadline. Fresh failed install must initialize the read-only lot resolver/deadline context even though admission/fetch stay disabled. The already passing failed-reinstall test retains old context and misses this path. Add fresh-module tests for both sides and no early deadline; no engine rewrite or alpha change.

This is **CHANGES REQUIRED**, not a full-ready or deployment acceptance. Full-suite acceptance has not been claimed on a head still needing this correction.

## Public-only workload measurement

Measured at **12:15 Cairo, 14 November 2026** (10:15 UTC), using a separate public market-data client loaded from the deployed account-4 `binance_client.py`; no Engine, install, setting, account, credential, running-process or slot access. Same public origin used by account 4 for candles, no authenticated endpoints.

- Exact frozen 163 symbols all reported TRADING on that public source.
- **164 GET requests**: one exchangeInfo and 163 requests of 1,500 4h candles.
- **93.969 seconds** total; one pacer wait of **7.653 seconds**; raw R6 signal computation **1.170 seconds**.
- No missing public symbols, insufficient histories, duplicate timestamps, out-of-order timestamps or gaps in the required trailing 768 4h bars.
- Expected 4h request weight is 1,630, plus other shared-IP traffic. The theoretical maximum D01 addition is 815 weight, not measured in this batch. Every request had the required used-weight header; request-level receipts are in public_workload_probe.json.

Decision: the standalone public-source coverage/workload measurement is complete and supports a bounded canary preparation. It is **not** a measurement of delays inside the running engine lock, worst shared-IP competition, authenticated protection or a complete activation gate. The final code review, required suite and protection gates still stand. At a later authorized canary step, first observe the actual paused cycle and protection behavior before enabling entries. No slot or deployment is performed here.

No real key was read or used. Account 1 was not accessed. No account, scheduler, runtime, slot, merge or label changed.
