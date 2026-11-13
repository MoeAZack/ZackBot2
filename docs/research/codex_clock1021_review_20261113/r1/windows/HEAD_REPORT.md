# Windows review: exact head 6e259ea5

The independent source/fault checks pass, but the Windows full gate is **FAIL / acceptance held**. No merge, installation, account, scheduler, key or position change was made.

`verify.py full` ran unmodified at `6e259ea5acf2379fe09bff933d7c5b05cfaa269e`, from 13 November 2026 19:26:48 to 22:08:58 Africa/Cairo. All app-data roots were private review folders. Build mode was `buildcheck`, never install. The working tree was clean.

| Step | Result |
|---|---|
| Compile / secret scan / data manifest | PASS |
| All tests | 3600-second timeout; no completed JUnit and no valid counts |
| Strict replay 1 | PASS, 486.7 s |
| Strict replay 2 | PASS, 1798.5 s |
| Original UI | 46/47; browser-library/cache mismatch |
| UI-only environment retest | PASS, 173/173; private Playwright 1.56.0 as pinned by requirements-ui.txt |
| Build check | Timeout during staged safety tests; no exe build/self-test acceptance |

The Windows verifier killed its build-launcher command at the one-hour limit, but PowerShell/test descendants retained its output pipe. At 22:08 Cairo, only the identity-checked private review installer tree was terminated to release the expired gate. No running bot process was targeted. The full gate remains failed; the UI retest does not overwrite it.

Additional completed Windows checks: all 39 clock regression tests pass (3.29 s); the isolated golden pack completes with 32 passed, 8 skipped and 32 expected failures (92.04 s). These are focused results, not a substitute for the full gate.

The verifier's all-test summary contains zero counters because JUnit was never completed. Those zeros do NOT mean zero failures or zero regressions. Claude's Linux 39F/124E comparison is not a Windows result. Exact-master full comparison started serially at 22:08:58 Cairo and remains pending at this report.

The r1 offline evidence and latency qualification remain as previously published: same-ID protective retries, one entry retry, 37,296 fresh-offset non-1021 comparisons with zero differences, and source-identical `_create_lot`. Mixed transient errors may exceed the clock-only latency bound.
