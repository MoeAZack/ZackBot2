# T03a: Leverage-refusal fallback. Review request

## Current state

| Item | Value |
|---|---|
| Branch | `t03a-leverage-fallback`, from `master` 7e0c245 (T04 merged) |
| Head for review | `2ad1c74` (see the PR for the full SHA) |
| Commits | 11e1cb2 engine fallback, 31b7389 client read, 9c030ad status, a889327 panel chip/dialog, 2ad1c74 tests |
| Files | engine.py (+26/-2), binance_client.py (+7), app.py (1 line), panel.html (+6/-3), tests/test_safety.py (+71) |
| Class | **E - risk/execution (order path).** Lands only under CI, with strict replays unchanged |
| Behaviour change | Only when Binance **refuses** a leverage change. Unchanged: successful leverage changes, sizing, stops, every other entry check |

## The problem (testnet, since v3.0)

Binance testnet answers `-1000` to every leverage change on SOLUSDT/XRPUSDT so far. Today the entry is then skipped
(fail-closed), so those coins never trade even when the coin's leverage is already safe.

## The change

`Engine._ensure_leverage` (after the existing single retry):
1. read the coin's **current** leverage, read-only (`GET /fapi/v2/positionRisk?symbol=`; new `Futures.current_leverage`);
2. **proceed only if** `1 <= current <= cap`, where cap = min(setting, the coin's Binance maximum);
3. otherwise skip exactly as today (`could not set leverage/margin on Binance: leverage 10x refused (...); current leverage 20x is above the 10x cap` / `current leverage unknown`);
4. a refused change is **not cached**: the next entry tries to set the leverage again;
5. every refusal is counted per coin (`count`, `proceeded`, `skipped`, `current`, `cap`, `last_error`, `last_time`) in
   `Engine.lev_refusals`, exposed as `health.lev_refusals` in `/api/status`, shown as a **"Leverage refusals: N"** chip
   in the status bar with a per-coin details dialog.

Why a lower current leverage is safe: sizing is risk-based and capped by `MAX_LEVERAGE x capital` regardless of the
exchange setting; a lower exchange leverage only needs more margin (Binance rejects the order if margin is short - the
existing failed-entry path) and puts liquidation further away. A higher one is never accepted.

## Tests (`tests/test_safety.py`, 8 new)

- refused + current 5x <= cap 10x: entry proceeds, exchange stop placed, refusal counted, not cached
- refused + current exactly at cap: proceeds
- refused + current 20x > cap: skipped, **no order sent**, counted as skipped
- refused + current unknown (no row / read error): skipped, no order sent
- cap = min(setting, coin max): coin max 8x, current 10x -> skipped
- per-coin counters across coins; manual trade raises with "leverage" in the message
- successful change: no refusal recorded, current leverage never read
- client: `current_leverage` reads `/v2/positionRisk` with the symbol, returns max of the rows, `None` for no rows

**Mutation proof (sandbox):** accepting any known leverage (`ok = cur is not None`) -> 3 tests fail; removing the fallback (old code) -> 7 tests fail.

## Evidence so far

Claude sandbox (Linux, Python 3.13, pandas 3.0.5). This used a **stand-in test runner, NOT official pytest** (PyPI is blocked there).
It ran on the exact pushed tree (`git archive t03a-leverage-fallback`):
- `tests/test_safety.py` + `tests/test_verify.py`: 92 passed, 1 failed. The failure is `test_summary_has_the_required_provenance_fields`, which needs the real pytest package installed. It is environment-only and also fails on master in that sandbox.
- `verify.secret_scan` on the tree: no findings.
- Strict replays with the T03a engine: replay 1 PASS (100% match, 0.017, 2.81pp); replay 2 PASS (99.7%, 0.019, 2.17pp). Replays never hit a leverage refusal, so this shows that nothing else moved.
- Mutation proof: as listed above.

Pending (official): GitHub `verify fast` + `verify full` on the PR, and the owner's targeted Windows pytest run.

Not run, and not touched: no installer, no drill, ZackBot not stopped, no orders, no account calls. The installed app is untouched.

## Questions for Codex

1. Is `1 <= current <= min(setting, coin max)` the right acceptance rule? Or should a refused change additionally require `current <= setting` only (ignoring a coin max lower than current)? Our view: the min is stricter and correct.
2. `positionRisk` in hedge mode returns LONG and SHORT rows. We take the max, which is conservative. Do you agree?
3. Not caching a refused change means one extra signed call per entry attempt on a refusing coin. Acceptable at our entry rate?

## Testnet canary plan (needs owner approval at that time)

Install the build, watch the SOL/XRP signals: each refusal must show in the chip with "went ahead" only when Binance
reports a leverage <= cap; every resulting lot must be protected; reconciliation clean. Rollback: previous build via the
T03 installer rollback.
