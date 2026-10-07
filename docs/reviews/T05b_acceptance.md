# T05b exchange-outage resilience — acceptance

*Accepted 2026-10-07 04:55 Africa/Cairo by Codex; implementation head `20c00c8`, final reviewed head pending this document commit.*

## Verdict

**Accepted for PAPER/testnet and ready to merge.** No open P1/P2 finding. Mainnet remains out of scope.

## Evidence

- GitHub exact-head gates: verify fast, CodeQL, replay1/UI, replay2, tests and required `verify full` all passed.
- Windows: focused outage set **113/113**, broader safety set **390/390**, installer set **64/64**.
- Guarded installer completed all eight stages; build `20261007-044336` started and reconciled cleanly.
- Startup canary: exact TESTNET injector `read_outage:180`; one process/PID stayed alive, circuit reached outage, six bounded probes advanced to the 60-second cap, one coalesced `exchange-down` incident remained open, and no protection issue appeared.
- Same-process recovery at 04:53:42 Cairo: circuit `ok`, recoveries `1`, positions re-read, stops re-confirmed, incident closed once.
- DOGE crossed its configured safety-order level during the outage. No add occurred while blind; the safety order executed at 04:53:43, one second **after** recovery, proving exposure stayed blocked until reconciliation. Its quantity changed 282 → 705 and its stop was safely replaced; PEPE and HYPE stayed byte-for-byte equivalent at the lot/stop level.
- Final restart without the injector: PAPER, engine/exchange/circuit `ok`, zero errors, incidents, unprotected, untracked or orphaned positions; all three current lots have confirmed Binance stop IDs.

## Earlier findings

The original permanent-disconnect P1 and long-outage backoff-overflow P2 were both confirmed, fixed and regression-tested before this acceptance.
