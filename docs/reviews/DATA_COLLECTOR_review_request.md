# Market-data collector: review request (local branch `market-collector`, base a0dfee7 = master code)

Owner decision (2026-10-07): **start collecting market data now.** Binance serves only about 30 days of
`openInterestHist` and the long/short / taker ratios, so every day without a collector loses a day of history for good.
The cloud sandbox and the Claude VM cannot reach Binance. Only the installed ZackBot on Windows and Codex's Windows
environment can. So there are two deliverables, sharing one implementation:

1. `tools/collect_market_data.py` + `tools/collect_market_data.bat`: a standalone tool Codex can run **today**
   (stdlib + requests only; pandas is not even needed).
2. `market_collector.py`: an observe-only background thread inside the app, so the installed bot keeps collecting.

## What Codex should run on Windows today

From the repo folder (`C:\Dev\ZackBot2`, branch `market-collector` checked out or files copied in):

```bat
rem 1) one-shot back-fill + append (40 coins of the app universe; first run about 20-40 min, re-runs about 1-2 min)
tools\collect_market_data.bat --once
type data_market\collector.log
python -c "import json;m=json.load(open('data_market/manifest.json',encoding='utf-8'));print(json.dumps(m['last_run'],indent=1));print(len(m['series']),'series')"

rem 2) keep it running every 4 hours, even when ZackBot is closed
schtasks /create /tn "ZackBot market data" /sc hourly /mo 4 /st 00:05 /f /tr "\"C:\Dev\ZackBot2\tools\collect_market_data.bat\" --once"
schtasks /query /tn "ZackBot market data"
schtasks /run /tn "ZackBot market data"

rem 3) BT02: testnet exchangeInfo snapshot (public, no key)
tools\collect_market_data.bat --testnet
rem    -> data_market\exchange_info\testnet_exchangeInfo_latest.json (+ a timestamped copy)
rem    On the BT02 branch (exchange_rules.py present) this also builds data\exchange_rules_testnet.json directly.
rem    On master it prints the exact command to build it later on the BT02 branch, e.g.:
python exchange_rules.py build data_market\exchange_info\testnet_exchangeInfo_latest.json --env testnet --fetched-at <UTC time it printed> --source "https://testnet.binancefuture.com/fapi/v1/exchangeInfo"
```

Expected: exit code 0 (1 = finished with some series errors, listed in `manifest.json -> last_run.last_errors`;
3 = stopped by an IP ban / a rate limit that did not clear / another run holding the lock). Please attach
`data_market\manifest.json`, `collector.log` and the BT02 snapshot result to the PR comment. Commit the snapshot only if
BT02 wants it versioned; `data_market\` itself is git-ignored.

After the app is rebuilt with this branch (`build_app.bat`): `/api/status -> health.market_collector` shows `enabled`,
`runs`, `last_run` (`rows_added`, `errors`, `stopped`), `next_run`, `last_skip`, `banned_until`. Data goes to
`%LOCALAPPDATA%\ZackBot\market_data` (the scheduled task uses `data_market\`, so they never write the same folder).

## Datasets

| Dataset folder | Endpoint | Periods | History |
|---|---|---|---|
| `open_interest_hist` | `/futures/data/openInterestHist` | 1h, 4h | last 30 days (Binance limit) |
| `global_ls_account` | `/futures/data/globalLongShortAccountRatio` | 1h, 4h | last 30 days |
| `top_ls_position` | `/futures/data/topLongShortPositionRatio` | 1h, 4h | last 30 days |
| `taker_ls_ratio` | `/futures/data/takerlongshortRatio` | 1h, 4h | last 30 days |
| `funding_rate` | `/fapi/v1/fundingRate` | - | full, from the symbol's `onboardDate`, 1000 per page |
| `mark_klines` | `/fapi/v1/markPriceKlines` | 1h, 4h | full, 1500 per page, closed candles only |
| `funding_info/ALL.csv` | `/fapi/v1/fundingInfo` | - | change log: (symbol, interval h, cap, floor) with first-seen time |
| `exchange_info/` | `/fapi/v1/exchangeInfo` | - | raw JSON; `<env>_exchangeInfo_latest.json` + a timestamped copy only when the content changed |

One CSV per (dataset, symbol, period), UTF-8, plus a `time_utc` column. `manifest.json` has per series: rows, first/last
timestamp (ms and UTC), last run, source base, file, and any recorded gaps. `last_run` holds the run summary.

## Design (market_data.py)

- **Idempotent.** Each series is merged by timestamp (same ts: the newer answer wins) and the whole file is rewritten
  atomically (`<file>.<pid>.tmp` + fsync + `os.replace`). A crash leaves the old complete file and no temp file.
  Re-running adds 0 rows and rewrites nothing.
- **30-day series.** The window starts at the first period boundary after `now - (30 d - 1 h)`. It is requested in chunks
  of at most `limit` periods with both `startTime` and `endTime`, so the result never depends on how Binance orders rows
  in a longer range. If the last stored row is older than the window (the collector was off for more than 30 days), a
  **gap** is recorded in the manifest and the run errors. It is never hidden.
- **Full-history series.** Paged forward from the last stored row (or onboard) with `startTime` only. Progress is
  checkpointed every 20 pages and also when a run is stopped, so an interrupted back-fill resumes. `MAX_PAGES=400` per
  series and run.
- **Priority.** The 30-day series for all coins go first, then funding, then the long kline back-fill.
- **Rate limits.** Pacing: at least `min_gap` between requests (0.25 s standalone, 0.5 s in-app). Klines (weight 10) are
  paced to a weight budget (1200/min standalone, 400/min in-app; the IP limit is 2400). fundingRate/fundingInfo are at
  least 0.7 s apart (shared 500 / 5 min). `/futures/data` is at least 0.35 s apart. If `X-MBX-USED-WEIGHT-1M` > 1800,
  the tool waits for the next minute. **429**: Retry-After honoured (else 2, 4, 8, 16 s), bounded at 4 retries, then the
  run stops. **418**: the run stops immediately, nothing is retried, and `ban.json` blocks every later run until the ban
  ends (at least 120 s). 5xx/network: bounded retries; 3 unreachable series in a row stop the run. A 4xx refusal skips
  that series only.
- **Never a key.** `RequestsTransport` drops any `X-MBX-APIKEY` header. The in-app transport uses the keyless MAINNET
  `engine.data` client. Only GET, never signed.
- **Single writer.** `<out>/.collector.lock` (O_EXCL; a lock older than 6 h is taken over).

## In-app collector (market_collector.py, app.py, engine.py)

- `APP.collector = MC.MarketCollector(lambda: APP.engine, DATA/market_data)` starts in `main()` after `App()`. It is a
  daemon thread `market-collector`. First run after ~3 min (+-10 %), then every 4 h (+-10 %).
- Transport: `engine.data._req('GET', path, params, retry=False)`. It is one attempt through the engine's own client and
  T05b circuit; the collector does the back-off. `binance_client._req` now attaches `http_status` / `retry_after` to the
  busy-path `BinanceError` (two attributes, no behaviour change), so a 418 ban can be told apart from a 429.
  `ExchangeUnavailable` (circuit open) stops the run.
- Gate before every request: off switch, app stopping, engine circuit. When the circuit is `degraded`, the collector
  waits up to 60 s for the engine's next good read, then stops the run. When it is `outage`, the run stops with no
  request sent. **The collector never adds load while the engine's exchange path is unhealthy.**
- Bounded: its own non-blocking run lock (an overlapping run is skipped), 3 h run limit (a back-fill continues next run),
  and the lock file against the standalone tool.
- **Never takes `engine.lock`.** It reads only `e.S['MARKET_COLLECTOR']`, `e.S['UNIVERSE']` and `e.data`. It never
  touches orders, lots, state or settings files, and writes only under its own folder.
- One log line per run (`market data: +N rows, 40 coins, R requests, E errors, Ts`). A crash in a run is one warning line;
  the thread survives.
- Setting `MARKET_COLLECTOR` defaults to **True** (owner asked to start collecting). `load_settings` turns a non-bool into
  True. `/api/settings` accepts only a real JSON bool (`"no"` or `0` raise `ValueError`, and nothing changes). Anything
  but `True` counts as off in the collector. Switching off mid-run stops the run before its next request.
- `/api/status -> health.market_collector` gives the status (None for an App without a collector).
- Packaging: PyInstaller follows the imports (`app -> market_collector -> market_data`). `selftest` imports both
  modules. `installer.ps1` staging excludes `data_market` (`/XD`), and `.gitignore` has `data_market/`.

## Tests: `tests/test_market_collector.py` (36, no network, fake clock and fake Binance)

Pagination (gapless, ascending, from onboard, open candle never stored, page sizes 1500/1000); re-run adds 0 and leaves
files byte-identical; later runs resume right after the last closed candle; dedupe with newer-wins; 30-day window
(every request inside it, chunks <= 500, no hour lost to the limit); gap after 40 days recorded; 429 Retry-After +
exponential back-off; 429 that never clears stops after 1 + 3 tries; 418 stops at once, `ban.json`, later runs send
nothing until it ends; weight pacing; unreachable stops after 3 series; refused series skipped; manifest fields;
exchangeInfo new file only on change (raw JSON = `exchange_rules.py build` input); fundingInfo change log; atomic writes
(failed `os.replace`, crash with half the bytes written: old file intact, no temp left); interrupted back-fill resumes
from its checkpoint; stale temp files swept; lock single-writer + stale takeover; every `open(` in the three files names UTF-8; non-ASCII round
trip; CLI `--once/--symbols/--out` + exit codes 0/3 + `--every` validation; default universe = engine TOP40; `--testnet`
fetches only exchangeInfo and calls `exchange_rules.build_file(path, 'testnet', source, fetched_at)` (or prints the
command); `RequestsTransport` status mapping and no API key; **in-app thread through a real `binance_client.Futures`
(`_once` faked) with `engine.lock` replaced by a guard that records any acquire from the collector thread: none**; 418
and 429 through the real client; circuit outage means zero requests; degraded waits for the engine's next good read;
off switch (before and mid-run, non-bool = off); overlapping run and foreign lock skipped; one log line on failure;
setting default/validation through `app.handle`; status in `App.health`; start-up order in `main()`; AST check that the
collector never touches `.lock`, `.trade`, `.state`, orders; installer exclusion.

Sandbox results (mini runner; `pytest` is not installed here):

| File | Result |
|---|---|
| test_market_collector | 36 passed (also under the CP1252 simulator) |
| test_safety / test_outage / test_outage_final / test_t05b_startup_outage | 78 / 42 / 8 / 13 passed |
| test_trade_audit / test_telegram / test_ci | 137 / 23 / 36 passed |
| test_installer | 62 passed, 2 skipped (run alone, ~4 min in this sandbox) |
| test_verify | 16 passed, 1 known env-only failure (`pytest` not installed: provenance lists no pytest version) |

Also: an end-to-end run of the standalone tool over real HTTP (local fake server, real `requests`, one injected 429):
2 coins + 1 unknown coin -> 24 series, 13 115 rows, 35 requests, the unknown coin reported and skipped, the 429
recovered; a re-run added 0 rows.

## Limits / open points

- Binance's real pagination behaviour on `/futures/data` with both start and end is avoided by design (chunks <= limit).
  It still needs to be confirmed on the first real Windows run: check `rows` for `open_interest_hist/*/1h` is about 720.
- The first in-app back-fill of full mark-kline history at 400 weight/min takes about 1 h for 40 coins. It runs in the
  background and resumes across runs.
- No panel toggle yet (the setting is available through `/api/settings`). A small switch on the
  Settings tab can follow if wanted.
- Parquet is not used. CSV keeps the tool dependency-free and diff-able, and the files are small (about 30 MB for
  40 coins of full 1h mark klines).

## Round 2 — fixes for `DATA_COLLECTOR_review_gpt.md` (2 × P1), 07 Oct 2026 (Cairo)

The "In-app collector -> Transport" paragraph above describes round 1 and is **superseded** by this section.

### P1 — collector no longer touches the engine's outage circuit
- `market_collector.py`: `FuturesTransport` (which sent every request through `engine.data._req`) is removed. The in-app
  collector builds its **own** keyless `market_data.RequestsTransport(MAINNET)` once per app run (own `requests.Session`,
  no `X-MBX-APIKEY`) and keeps its own `RequestHealth` counters (ok / failed / last failure), shown in
  `health.market_collector.request_health`. Back-off, 429 Retry-After, the 418 `ban.json` and 5xx/network retries are
  the collector's own (`market_data.Collector`), exactly as in the standalone tool.
- The engine circuit is read only, as a one-way gate (`gate()` reads `e.data.health.state`). Not `ok` stops the run or
  waits up to 60 s for the engine's own next good read. Nothing in the module calls `ok()`, `fail()`, `admit_read()` or
  `_req()` (AST-checked in a test). `market_collector.py` no longer imports `binance_client`.
- `binance_client.py` is reverted to master byte-for-byte (the `http_status` / `retry_after` attributes were only for
  the removed transport). The trading client is unchanged by this PR.

### P1 — `CMCCirculatingSupply`
- `DATASETS['open_interest_hist'].cols` now uses Binance's exact key `CMCCirculatingSupply`. No collection ran from the
  round-1 head, so no data needs repairing.

### Tests (tests/test_market_collector.py, 50 pass; was 36)
- `test_collector_outcomes_never_change_the_engine_circuit[ok|e429|e418|e5xx|network × fresh|just-recovered]` — a real
  `binance_client.Futures` is the engine client, wired as a tripwire (any request through it fails the test). Every
  `ExchangeHealth` field must be identical before and after the collector run, and the collector's own
  `request_health` records the failure.
- `test_engine_circuit_outage_means_no_requests` (outage: no traffic, circuit unchanged) and
  `test_engine_circuit_degraded_is_a_one_way_gate` (degraded by an engine read: the collector waits and stops, or resumes
  only after the ENGINE's own success).
- `test_in_app_429_backs_off_on_its_own_state`, `test_in_app_418_stops_and_is_reported` (also: nothing is sent during
  the ban), `test_default_transport_is_its_own_keyless_mainnet_session` (no engine-client calls in the module source).
- `test_every_column_exists_in_the_real_open_interest_payload` and
  `test_real_open_interest_payload_reaches_the_csv_with_circulating_supply` — a fixture copied verbatim from the public
  `openInterestHist` answer (BTCUSDT 1h, fetched 07 Oct 2026) must reach the CSV with no blank column.
- Mutation check: with the round-1 `market_collector.py` / `market_data.py` / `binance_client.py` put back, all 13
  isolation tests fail. They catch the defect.

### Real public run (standalone tool, this PC, round-2 code, 07 Oct 2026 12:45 Cairo)
`python tools/collect_market_data.py --once --symbols BTCUSDT,ETHUSDT --out <scratch>` -> exit 0, 142 requests,
172 059 rows, 0 errors, 70.8 s. `open_interest_hist/*/1h` = 719 rows each (the expected ~720), `/4h` = 180.
`CMCCirculatingSupply` has **0 blank values** (BTC 20094521.00000000, ETH 122110434.69485405). Funding from 2019-09-10
(BTC) / 2019-11-27 (ETH). Mark klines start 2019-12-23 for both coins, which looks like where Binance's markPriceKlines
history begins (recorded, not a collector gap). No API key, no order endpoint, scratch folder only; the data is not
committed.
