# DATA-01 / DATA-01a / RES-01 / EDGE-00: draft contracts (PRE-STAGE, Claude Code)

Status: **draft for Codex acceptance, no runtime code.** Plan rows D1 (DATA-01), D1a (DATA-01a), D2 (RES-01) and D12
(EDGE-00) of `docs/NEWCORE_EXECUTION_PLAN.md` (control plan v3.1, 08 Oct 2026, Africa/Cairo). Claude Code implements
these only after Codex accepts this contract (plan section "Strategy research starts in parallel", Claude Code row).
Nothing here promotes, enables or tunes a strategy; STRAT-00 owns the taxonomy and the promotion gates, and this document
only supplies the data and measurement machinery those gates are evaluated with.

Sources read on 2026-10-08 (read through `git show`; only `origin/master` is checked out):

| Ref | Head | What it holds for this draft |
|---|---|---|
| `origin/master` | 2c3e54d | `market_data.py` + `market_collector.py` + `tools/collect_market_data.py` (the collector), `DATA_MANIFEST.json`, `data/`, `data1h/`, `data_long/`, legacy research scripts and `research/` outputs |
| `origin/nc-s1-slice` (also `origin/nc-rec02` 8e51504) | 7f35c5c | `newcore/runner/evidence.py` (episode-clustered bootstrap), `docs/newcore/slice/M3_long_candidate_evidence.md`, `M3_short_parity.md` |
| `origin/nc-strategy-ema-mom` | d21d113 | `newcore/strategy/research/ema_mom_after_cost.py` (after-cost re-run, the M3 input) |
| `origin/roadmap-strategy-expansion` | 48dcbe0 | `docs/STRATEGY_AND_RISK_EXPANSION.md` (metrics catalogue, validation ladder; not merged) |

Labels used below: **[read]** = verified in the repo; **[venue doc]** = Binance's published behaviour, to be re-checked
by the first DATA-01 run (the run records what it actually saw); **[proposal]** = a decision Codex accepts or changes.

---

## 1. Recommendations (read this first)

1. **Make "available at" a first-class column, not a convention.** Every stored row carries `available_ms`, the earliest
   time a live bot could have known it (a 4h candle opened at t is available at t + 4h; a funding rate at its
   `fundingTime`; a 30-day ratio row conservatively at `timestamp + period`). The harness view `at(t)` filters on
   `available_ms <= t` and nothing else. This one rule kills the most common lookahead bug (same-bar signal + fill)
   and the subtle ones (OI / ratio rows stamped at the start of their period). [proposal]
2. **Use the public Binance archive (`data.binance.vision`) as the primary historical source, the REST collector for
   the live tail.** The archive publishes monthly/daily files with a `.CHECKSUM` (SHA-256) per file, includes symbols
   that have since been delisted (the survivorship fix) and, for USD-M futures, carries `metrics` files with open
   interest and the long/short / taker ratios beyond the REST 30-day limit [venue doc, verify on first run]. The REST
   collector (already on master) stays the source for the last ~30 days and for exchangeInfo / fundingInfo snapshots.
   Where both exist, the overlap is cross-checked (gap report class `X1`).
3. **Freeze data by manifest digest, never by folder.** A research result cites exactly one
   `zb-data-manifest/1` digest. New data is a new manifest; a manifest is never edited. `DATA_MANIFEST.json` on master
   is the seed (it already records SHA-256, rows, first/last) but lacks source query, access time, availability rule,
   gaps and survivorship scope; it is grandfathered as `legacy-unverified`.
4. **Survivorship: a point-in-time universe rule, decided before any result is seen.** The candidate universe at time t
   is computed only from data available at t (listing status, onboard date, trailing quote volume). Delisted symbols stay
   in the sample until delisting with a modelled forced exit. Every existing result on `data_long` core 8 is labelled
   **SURVIVOR-ONLY** and cannot pass EDGE-00 on its own. [proposal]
5. **Funding is data, not an assumption.** The M3 pack and `ema_mom_after_cost.py` charge a flat 0.005% per 4h bar on
   both sides **[read]**. RES-01 charges the actual signed funding rate at each `fundingTime` on the mark-price notional
   held at that instant (longs pay positive rates, shorts receive them), with the flat model kept only as a stress row.
6. **One shared harness, the engine's own semantics.** RES-01 drives strategies through the same NEWCORE strategy
   `Evaluation` and (for managed candidates) the pure NC-07 management core, so research and runtime cannot diverge on
   fills, stop ordering, rounding or management. The legacy scripts are retired as evidence sources.
7. **Episode-clustered statistics are the default, not an option.** The M3 evidence already shows why: base kill-OFF has
   a per-trade CI of [+0.113, +1.011] R but an episode-clustered CI of [-0.041, +1.295] R **[read]**. RES-01 reports
   both, and every gate reads the clustered one.
8. **Holdout discipline is enforced by the harness, not by good intentions.** The harness refuses to evaluate a sealed
   holdout window unless a `zb-prereg/1` file for that candidate is committed in git *before* the run, and it writes an
   append-only holdout access log. A second holdout look for the same pre-registration is refused (a new pre-registration
   with a new, later holdout is required).
9. **EDGE-00 compares against matched baselines, not zero.** A candidate must beat a matched random-entry baseline
   (same symbols, sides, stop distances, holding-time distribution, costs) by an episode-clustered margin, survive 2x
   costs and the point-in-time universe, and not be one symbol / one year. Proposed numeric thresholds are in section 7.
10. **DATA-01a scope is driven by the candidates, not collected speculatively.** 1m/5m klines and aggTrades/bookTicker
    are pulled only for the timeframes a pre-registered candidate needs (STR-03 Quick Bank scalp is the first consumer:
    intrabar ordering and spread matter there). Our own latency comes from TNET-01/testnet logs, not from a dataset.

---

## 2. What we already have, and its gaps

### 2.1 Market data

| Asset | What it is **[read]** | Gaps against DATA-01 |
|---|---|---|
| `data_long/` (4h core 8: 10,499 rows each, 2021-12-19 08:00 -> 2026-10-04 00:00 UTC; 1h core 8: 35,999 rows, 2022-08-26 06:00 -> 2026-10-04 04:00) | Copy of the app's last-price candle cache (`app.py` cache, columns `t,o,h,l,c,v`, UTC open time), taken 2026-10-04 | survivor-only universe; no close time, quote volume, trades or taker volume; no access time or query record; the last bar's closedness is not provable from the file; end bars differ (00:00 vs 04:00) |
| `data/` (40 coins 4h, 4,500 rows, 2024-09-14 -> 2026-10-04) and `data1h/` (core 8 1h, 4,499 rows, 2026-03-30 -> 2026-10-04) | fapi klines downloaded in the dev sandbox 2026-10-04 | survivor-only; **all 8 `data1h/` files miss the 2026-08-02 16:00 UTC bar** (15:00 -> 17:00; verified on BTCUSDT) and nothing flagged it |
| `DATA_MANIFEST.json` | 64 entries `{sha256, rows, first, last}` plus a note naming the survivorship bias | no source/query, no access time, no availability rule, no gap list; `data/exchange_rules_testnet.json` is not in it |
| `market_data.py` + `market_collector.py` + `tools/collect_market_data.py` (on master, Codex-reviewed; final acceptance was waiting on an in-app PAPER check) | keyless REST collector: OI hist, global L/S account, top L/S position, taker ratio (1h/4h, **30 days only**), funding rate (full), mark klines 1h/4h (full, closed only), fundingInfo change log, exchangeInfo snapshots (sha256 + `fetched_utc`, new file only on change); idempotent merges, atomic writes, 418/429 handling | output is git-ignored and lives on the Windows machines only; no per-request query log, no per-series content hash; gap detection only for a 30-day series that fell behind its window; no last-price / index / premium klines; no `topLongShortAccountRatio`; OI / ratio history exists only from the day collection started |
| `exchange_rules.py` / `feasibility.py` | rule snapshots with provenance (`direct_fetch` / `file_import`), stale after 30 days | the only freshness rule on market data; not point-in-time history |

### 2.2 Research code and outputs

| Asset | Method **[read]** | Status |
|---|---|---|
| `backtest.py` | next-open entries, signal/time exits at the close, intrabar path O-L-H-C (green) / O-H-L-C (red), doji worst-case, gap fills at the open; fee 0.05% taker, slip 0.02%, maker 0.02%; funding a flat 0.005% per bar on both sides | the engine-parity reference for legacy; funding is an assumption; maker fill model optimistic |
| `lab.py` | random/grid optimise with 75/25 holdout, rolling walk-forward, trade shuffle / bootstrap Monte Carlo, 5-day block bootstrap of daily returns, truncation-invariance lookahead check | reusable ideas; the 5-day block understates clustered drawdowns (v3.2 response) |
| `research_long*.py`, `research_grid.py`, `research_combos.py`, `research_refresh.py` and `research/*.json,csv` | presets chosen on overlapping data; walk-forward re-optimises risk only; `long2_wf.json` shows walk-forward **below the untuned baseline** for balanced / aggressive | **retire as evidence**: survivor-only, no untouched holdout, flat funding, not tied to a code SHA, predate FBL-BT01 (DCA "materially worse") and BT02; `long2_mixes.json` has no generator script |
| `replay.py`, `trade_audit.py` | engine-vs-backtest gates (>= 98% matched, median dR <= 0.05); causal audit with highs/lows labelled upper bounds | method reusable for RES-01's engine-parity and hindsight-ceiling rules |
| `ema_mom_after_cost.py` (`nc-strategy-ema-mom`, also on `nc-rec02`) | after-cost re-run, cost stress rows, per-trade + **episode-clustered** bootstrap (10k, seed 20261008), in-sample 2022-2024 vs holdout 2025-2026 | the statistical core RES-01 should generalise; funding flat; holdout not sealed (the rule was chosen with the whole window seen) |
| M3 evidence pack (`docs/newcore/slice/M3_*.md`, `newcore/runner/evidence.py`, `parity.py`, `mirror.py` on `nc-s1-slice` / `nc-rec02`) | `trend_ema_mom.v1` long through the S4 Runner + FakeVenue + FileJournal on `data_long` core 8; kill-ON canary: 22 trades, mean -0.465 R, episode CI [-0.860, -0.152]; kill-OFF: 205 trades / 93 episodes, +0.525 R, episode CI [-0.041, +1.295] (includes 0); short side: mirror parity exact at zero costs (224/224) | honest and correctly labelled "not a promotion"; in-sample w.r.t. the rule choice, survivor-only, flat funding: the RES-01 acceptance case (section 8, step 5) |
| `tests/golden` | 30 synthetic-market cases; costs block `model:"legacy"`, `funding_per_bar "0"` | no real market fixture; funding never exercised |
| `T09a_design_draft.md` (`claude-proposals-2026-10-07`) | proposes Deflated Sharpe, PBO/CSCV, Holm-Bonferroni, an append-only trial registry, pre-registered grids | proposal only; nothing implemented. Section 6.2 adopts the trial ledger + Holm; DSR / PBO are open question 11 |

### 2.3 The gaps that matter most

1. No point-in-time universe (every number we have is survivor-only).
2. No historical funding in any research run (flat assumption everywhere).
3. No OI / long-short / taker history older than the collector's start (REST serves 30 days), so any positioning
   feature needs the archive `metrics` files or months of collection before it can be tested.
4. No sealed holdout: every legacy and M3 window was seen during rule choice; only post-2026-10-04 data is untouched.
5. No gap checker (the `data1h/` hole went unnoticed) and no availability rule (bar close vs open is a convention).
6. No pre-registration, trial ledger or multiple-testing correction.

---

## 3. DATA-01 / DATA-01a dataset schemas

### 3.1 Common conventions (all datasets) [proposal]

- **Storage.** One file per (venue, env, market, dataset, symbol, interval, calendar month UTC):
  `data_store/<venue>/<env>/<market>/<dataset>/<symbol>/<interval>/<YYYY-MM>.csv` (git-ignored; the manifest is
  versioned). UTF-8, LF, header row, sorted by `ts_ms`, no duplicates. Partitions are immutable once their month is
  closed and verified; the open month is rewritten atomically (the collector's `atomic_write_text` already does this
  **[read]**).
- **Numbers.** Prices, quantities, rates are stored as the **exact decimal strings the venue returned** (no float
  round-trip, no reformatting), so a content hash is stable and the NC-01 `Decimal` domain can load them losslessly.
  Integers (`ts_ms`, counts) as base-10 integers.
- **Time.** All timestamps are integer ms since the Unix epoch, UTC. Two mandatory time columns:
  `ts_ms` (the venue's own key: kline open time, `fundingTime`, ratio `timestamp`) and `available_ms` (section 1, R1).
  Cairo appears only in manifests and reports (access time, day boundaries), resolved per timestamp through the IANA
  zone `Africa/Cairo`, never a fixed offset.
- **Closed rows only.** A kline whose `close_time >= access time` is never stored (the collector already filters mark
  klines this way **[read]**; trade klines from the archive are closed by construction).
- **Identity.** `symbol` is the venue symbol (`BTCUSDT`), `market` is `um_futures` (USD-M perpetual) for the current
  scope; `env` is `mainnet` (historical research never uses testnet market data: testnet prices are not a market).

### 3.2 DATA-01 datasets

`available_ms` rules are [proposal]; field lists are [venue doc] unless marked [read].

| Dataset id | Source (archive / REST) | Key | Columns (beyond `ts_ms`, `available_ms`) | `available_ms` | History |
|---|---|---|---|---|---|
| `klines` | archive `futures/um/monthly/klines/<S>/<I>/` ; REST `/fapi/v1/klines` | open time | `open, high, low, close, volume, close_time_ms, quote_volume, trades, taker_buy_base, taker_buy_quote` | `close_time_ms + 1` | full from listing |
| `mark_klines` | archive `markPriceKlines` ; REST `/fapi/v1/markPriceKlines` **[read: collector 1h/4h]** | open time | `open, high, low, close, close_time_ms` | `close_time_ms + 1` | full |
| `index_klines` | archive `indexPriceKlines` ; REST `/fapi/v1/indexPriceKlines` | open time | `open, high, low, close, close_time_ms` | `close_time_ms + 1` | full |
| `premium_klines` | archive `premiumIndexKlines` ; REST `/fapi/v1/premiumIndexKlines` | open time | `open, high, low, close, close_time_ms` | `close_time_ms + 1` | full |
| `funding_rate` | archive `fundingRate` (monthly) ; REST `/fapi/v1/fundingRate` **[read: collector]** | `fundingTime` | `funding_rate, mark_price` (mark may be empty in old rows) | `ts_ms` | full |
| `funding_info` | REST `/fapi/v1/fundingInfo` **[read: change log with first-seen time]** | first-seen | `interval_h, cap, floor, first_seen_ms` | `first_seen_ms` (we cannot know when it changed before we first saw it) | from first collection only |
| `open_interest` | archive `metrics` (daily, 5m) ; REST `/futures/data/openInterestHist` **[read: 30 days]** | `timestamp` | `sum_open_interest, sum_open_interest_value` | `ts_ms + period_ms` | archive: from ~late 2021 [verify]; REST: 30 d |
| `ls_ratio_global_account` | archive `metrics` ; REST `globalLongShortAccountRatio` **[read]** | `timestamp` | `long_short_ratio, long_account, short_account` | `ts_ms + period_ms` | as above |
| `ls_ratio_top_account` | archive `metrics` ; REST `topLongShortAccountRatio` | `timestamp` | same | `ts_ms + period_ms` | as above |
| `ls_ratio_top_position` | archive `metrics` ; REST `topLongShortPositionRatio` **[read]** | `timestamp` | same | `ts_ms + period_ms` | as above |
| `taker_volume` | archive `metrics` ; REST `takerlongshortRatio` **[read]** | `timestamp` | `buy_sell_ratio, buy_vol, sell_vol` | `ts_ms + period_ms` | as above |
| `exchange_info` | REST `/fapi/v1/exchangeInfo` raw JSON **[read: new file only on change]** | access time | raw JSON + parsed `symbol, status, onboard_ms, delivery_ms, tick, step, min_qty, min_notional, contract_type` | access time | from first collection only |
| `universe` (derived) | built from `exchange_info` snapshots + archive listing + `onboardDate`/`deliveryDate` | rebalance time | `symbol, member, reason, trailing_quote_vol_30d` | rebalance time | section 5 |

Notes:
- The `metrics` archive mixes several series in one daily 5m file; the loader splits it into the five dataset ids above
  so each has one schema. Where `metrics` and the REST series overlap, values are compared (gap class `X1`).
- Aggregation (5m to 1h/4h) for ratios and OI is defined once in the loader: OI = last value in the period; ratios =
  last value; taker volumes = sum. The aggregated row's `available_ms` = the last constituent's `available_ms`.
- `funding_info` and `exchange_info` have **no history before our first collection**. Before that, funding interval is
  inferred from consecutive `fundingTime` gaps (recorded as `inferred`), and contract rules (tick/step/min notional)
  are the earliest snapshot we hold, labelled `rules_backfilled` in every result that relies on them.

### 3.3 DATA-01a execution-grade datasets

Pulled per pre-registered candidate (R10), never wholesale.

| Dataset id | Source | Key | Columns | Use |
|---|---|---|---|---|
| `klines_1m`, `klines_5m` | archive klines 1m/5m | open time | as `klines` | intrabar ordering of stop vs target inside a higher-timeframe bar; scalp candidates |
| `agg_trades` | archive `aggTrades` (daily) | `agg_id` | `price, qty, first_id, last_id, ts_ms, is_buyer_maker` | fill realism for scalps, slippage calibration on stop triggers |
| `book_ticker` | archive `bookTicker` (daily, where published) [verify availability window] | `update_id` | `best_bid, best_bid_qty, best_ask, best_ask_qty, ts_ms` | spread distribution by hour/regime; spread veto calibration |
| `book_depth` | archive `bookDepth` (daily, where published) [verify] | `ts_ms, percentage` | `depth, notional` | market-impact bound for follower sizes |
| `rules_history` | `exchange_info` snapshots | access time | tick/step/min notional/max qty/status per symbol | point-in-time exchange filters in the harness |
| `venue_sessions` | venue announcements (manual, cited) + `exchange_info` `status` changes | event time | `symbol, event, start_ms, end_ms, source_url` | maintenance windows, TradFi-perp session rules (XAUUSDT), delistings |
| `latency` | our own TNET-01 / testnet run logs (`observed_at_ms` vs send time) | run id | `endpoint, route, p50_ms, p95_ms, p99_ms, n` | latency stress in RES-01; never a venue dataset |

DATA-01a manifest entries additionally carry: `source_version` (archive path + checksum file contents, or API version
`fapi/v1`), `revalidate_by` (a Cairo date after which the entry must be re-fetched and re-hashed before it is used again,
default 90 days for REST snapshots, never for checksummed closed archive months), and `lawful_basis` (public archive /
public REST, no key; nothing behind a login is stored).

---

## 4. Provenance and manifest format

### 4.1 `zb-data-manifest/1` [proposal]

One JSON document, canonical form (sorted keys, LF, UTF-8, no floats: all decimals as strings). Its `digest` is the
SHA-256 of the canonical document with `digest` removed; results cite `manifest_digest`.

```json
{
  "format": "zb-data-manifest/1",
  "name": "um-core-2021-2026-v1",
  "created_cairo": "2026-10-09T14:05:00+03:00",
  "created_utc_ms": 1791543900000,
  "tool": {"repo": "MoeAZack/ZackBot2", "git_sha": "<40 hex>", "entry": "python -m newcore.data.build ..."},
  "scope": {"venue": "binance", "env": "mainnet", "market": "um_futures",
            "window_utc_ms": [1609459200000, 1790812800000],
            "universe_rule": "pit-top40-qv30d-v1", "survivorship": "point_in_time"},
  "entries": [
    {
      "dataset": "klines", "symbol": "BTCUSDT", "interval": "4h", "partition": "2024-03",
      "path": "data_store/binance/mainnet/um_futures/klines/BTCUSDT/4h/2024-03.csv",
      "source": {"kind": "archive",
                 "url": "https://data.binance.vision/data/futures/um/monthly/klines/BTCUSDT/4h/BTCUSDT-4h-2024-03.zip",
                 "checksum_url": "<url>.CHECKSUM", "checksum_sha256": "<64 hex as published>"},
      "query": null,
      "accessed_utc_ms": 1791541200000, "accessed_cairo": "2026-10-09T13:20:00+03:00",
      "raw_sha256": "<sha256 of the downloaded zip>",
      "content_sha256": "<sha256 of the normalised csv>",
      "rows": 186, "first_ts_ms": 1709251200000, "last_ts_ms": 1711915200000,
      "available_rule": "close_time_plus_1",
      "gaps": "gap_report#BTCUSDT/klines/4h", "status": "verified",
      "revalidate_by_cairo": null, "lawful_basis": "public_archive_no_key"
    },
    {
      "dataset": "open_interest", "symbol": "BTCUSDT", "interval": "1h", "partition": "2026-10",
      "source": {"kind": "rest", "base": "https://fapi.binance.com", "path": "/futures/data/openInterestHist"},
      "query": {"pages": [{"params": {"symbol": "BTCUSDT", "period": "1h", "startTime": 1790812800000,
                                       "endTime": 1791536400000, "limit": 500},
                           "accessed_utc_ms": 1791541200000, "http_status": 200, "rows": 202,
                           "response_sha256": "<sha256 of the raw response body>"}]},
      "accessed_utc_ms": 1791541200000, "accessed_cairo": "2026-10-09T13:20:00+03:00",
      "content_sha256": "<...>", "rows": 202, "first_ts_ms": 1790812800000, "last_ts_ms": 1791536400000,
      "available_rule": "ts_plus_period", "gaps": "gap_report#BTCUSDT/open_interest/1h",
      "status": "verified", "revalidate_by_cairo": "2027-01-07", "lawful_basis": "public_rest_no_key"
    }
  ],
  "gap_report_sha256": "<sha256 of the zb-data-gaps/1 document>",
  "digest": "<sha256 of this document without 'digest'>"
}
```

Rules:
- `status` is one of `verified` (checksum / overlap checks passed), `unverified` (no external checksum, e.g. REST
  only, used with the label), `legacy-unverified` (imported from `DATA_MANIFEST.json`, provenance not reconstructible),
  `rejected` (failed a check; kept for audit, never loaded).
- Raw downloads (zip / response bodies) are archived beside the normalised files under `data_store/_raw/` with the same
  hash, so a normalised file can be re-derived and re-hashed from the raw bytes on a clean checkout.
- **Freezing** = committing the manifest JSON under `docs/newcore/research/manifests/<name>.json` plus its gap report.
  The data files themselves are not committed (size); the hashes make any copy verifiable.
- Re-fetching a closed archive month must reproduce `raw_sha256`; a mismatch makes the entry `rejected` and is a
  gap-report item (`P1`), never a silent overwrite.

### 4.2 Loading contract

`load(manifest_path) -> Dataset` verifies every entry's `content_sha256` before returning anything and refuses the whole
manifest on a mismatch, a `rejected` entry inside the window, or a missing partition. The harness has no other way to
read market data (no direct CSV paths), so an unmanifested file cannot leak into a result.

---

## 5. Gap report and survivorship policy

### 5.1 `zb-data-gaps/1` [proposal]

One record per finding: `{class, dataset, symbol, interval, from_ts_ms, to_ts_ms, count, detail, disposition}`.

| Class | Detection | Disposition in research |
|---|---|---|
| `G1` missing interval | expected key grid (interval step from listing to delisting / window end) minus present keys | no fill. Signals cannot be evaluated on a bar whose lookback crosses `G1`; open positions crossing it are priced at the next available open and flagged `gap_exposed` |
| `G2` duplicate key | same `ts_ms` twice with different values | `rejected` partition unless the archive and REST agree on one value |
| `G3` OHLC invariant | `low <= min(open, close)`, `high >= max(open, close)`, `volume >= 0`, `close_time = open + interval - 1` | `rejected` partition |
| `G4` zero-volume / flat bars | `volume = 0` or `high = low` for a liquid symbol | kept, flagged; a strategy may not enter on a flagged bar |
| `G5` venue maintenance | known windows from `venue_sessions` | treated as `G1` with reason `maintenance` |
| `S1` stale tail | `last available_ms` older than `access - 2 x interval` | the manifest window end is cut to the last fresh row |
| `F1` funding cadence | a `fundingTime` step different from the current `funding_info` interval | kept; interval recorded as `inferred`; funding cost uses the actual timestamps, never an assumed cadence |
| `X1` cross-source mismatch | archive vs REST overlap; trade vs mark close beyond a band (default 2% for 4h) | report; mark/trade divergence is data, not an error, unless the archive and REST disagree on the same series |
| `P1` provenance | checksum mismatch, re-fetch hash change, unmanifested file | `rejected` |

Every result report prints the count of `G1/G4/G5` bars inside its window and the trades flagged `gap_exposed`.

### 5.2 Survivorship policy [proposal]

- **Point-in-time universe rule** (`pit-top40-qv30d-v1`, Codex to accept or replace): at each Monday 00:00 UTC, the
  universe is the 40 `TRADING` USD-M perpetual USDT symbols with the highest trailing 30-day quote volume computed from
  `klines` available at that instant, excluding symbols listed less than 30 days before. Membership changes only at the
  weekly rebalance; an open position in a symbol that left the universe is managed to its normal exit (no forced exit).
- **Delistings.** A symbol is tradable until its delisting announcement time (from `venue_sessions` / `exchange_info`
  status); after the announcement no new entries; a position still open at the final settlement is closed at the last
  available trade price with a stress slippage (default 2%) and flagged `delist_exit`.
- **Labelling.** Every result carries `survivorship: point_in_time | survivor_only`. Results on today's survivors (all of
  `data_long`, `data/`, `data1h/`) are `survivor_only`. EDGE-00 requires a `point_in_time` run; a `survivor_only` run
  may only be shown next to it to quantify the bias.
- **Bias measurement.** For each candidate RES-01 reports the difference between its `survivor_only` and
  `point_in_time` results; a long-biased trend rule whose edge shrinks materially is reported as such.

---

## 6. RES-01 research harness interface

### 6.1 Shape [proposal]

```
newcore/research/            (research only; never imported by the runtime - enforced by an AST import test)
  data.py        load(manifest) -> Dataset; Dataset.view(t_ms) -> PIT view (available_ms <= t only)
  universe.py    pit universe at t (section 5.2)
  costs.py       CostModel: fees (venue tier, maker/taker), slippage model, funding from data, stress multipliers
  execution.py   fills on the engine's semantics: next-open entries, intrabar policy, gaps, exchange filters at t
  run.py         run(prereg, config) -> RunResult (trades, equity, ledger, refusals), deterministic, no clock, no network
  stats.py       per-trade + episode-clustered bootstrap, block bootstrap, Monte Carlo, multiple-testing ledger
  splits.py      anchored walk-forward folds, sealed holdout, access log
  report.py      zb-research-run/1 JSON + Markdown report
```

- **Strategy input** is the NEWCORE strategy `Evaluation` (as `newcore/strategy` on `nc-strategy-ema-mom` already
  defines it **[read]**): pure, closed-candle, called with a PIT view. A strategy that reads anything not in the view is
  a contract violation caught by a **causal truncation test**: re-running on data truncated at every decision time must
  reproduce every decision (one random sample of 200 decision points per run, seeded).
- **Management** for managed candidates (TP1 / runner / bounded DCA / break-even) runs through the pure NC-07 core
  (`newcore.management.step` + `sim`), so the research curve and the testnet behaviour share one implementation.
- **Execution policy** [proposal]:
  - entries at the next bar's open after the signal bar closes (never the signal bar's close);
  - stop and target touched in the same bar: resolved with `klines_1m` when the candidate has DATA-01a coverage,
    otherwise **stop first** (conservative), and the share of trades resolved this way is reported;
  - a bar that opens through the stop fills at the open (gap fill), with slippage;
  - exchange filters (tick, step, min notional) as of the decision time from `rules_history`; a quantity that rounds below
    the minimum is a refusal, counted in the funnel;
  - no partial fills on market orders in research (a known optimism, bounded by the slippage stress).
- **Cost model** [proposal]: taker fee per side from the venue's fee tier (default VIP0 taker 0.05%, maker 0.02%,
  recorded in the run); slippage = `max(base_bps, k x spread_or_atr_proxy)` with base 2 bps per side; funding = signed
  `funding_rate x mark_notional` at every `fundingTime` the position is open across, from `funding_rate`
  (mark notional from `mark_klines` at that instant). Stress rows: fees + slippage x2, funding x2 (sign kept), slippage x5
  on gap and stop fills only.
- **Sizing**: the NC-06 risk gateway contract when it lands; until then the fixed-fractional model of
  `ema_mom_after_cost.py` (1% of closed equity per entry, gross cap) with the cap and refusals reported.
- **Follower feasibility**: for accounts of 100 / 200 / 500 USDT, the share of the candidate's trades whose risk-sized
  quantity clears `min_qty` and `min_notional` at that time, and the R error introduced by step rounding.

### 6.2 Statistics [proposal]

- **Unit**: net R per closed trade (R = initial risk incl. entry costs, as the M3 pack defines it), plus net % equity.
- **Episodes**: trades whose holding windows overlap on *any* symbol form one episode (the M3 definition **[read]**).
  Primary CI = 95% percentile bootstrap over episodes (10,000 resamples, seed recorded); the per-trade CI is reported
  but never gates.
- **Equity**: max drawdown on mark-to-market equity at every bar close; Monte Carlo drawdown bands from an episode
  bootstrap of the trade sequence (5th / 50th / 95th percentile max DD over 10,000 paths).
- **Multiple testing**: every configuration evaluated against a dataset is appended to a trial ledger
  (`docs/newcore/research/trials/<candidate>.jsonl`, append-only). The report states `n_trials` and applies a
  Holm correction to the primary endpoint across the candidate's pre-registered variants; untracked trials are a
  contract violation.
- **Sensitivity**: every pre-registered parameter at +/- one grid step (all neighbours); the primary endpoint must keep
  its sign in at least 80% of neighbours.
- **Regimes**: PIT regime labels (trend / range / high-vol from data available at entry); results per regime and side.
- **Concentration**: share of net PnL by symbol, by calendar year and by top-5 episodes.

### 6.3 Splits and holdout discipline [proposal]

- **Anchored walk-forward** over the in-sample window: folds of 6 months, fit on all data before the fold, evaluate on
  the fold; parameters are chosen only from the pre-registered grid.
- **Sealed holdout**: the last 12 months of the manifest window (and, separately, all forward data after the freeze
  date). The harness's holdout split refuses to run unless (a) a `zb-prereg/1` for the candidate exists in git with a
  commit time earlier than the run, (b) the pre-registration's `manifest_digest` and parameters equal the run's, and
  (c) the access log has no earlier holdout run for that pre-registration. Each run appends
  `{prereg_id, run_digest, utc_ms, cairo, git_sha}` to `docs/newcore/research/holdout_access.jsonl`.
- `zb-prereg/1` holds: candidate id and mechanism (economic hypothesis), side(s), universe rule, manifest digest,
  timeframe, parameter values (one primary set) and the grid for sensitivity, primary endpoint and the EDGE-00
  thresholds it will be judged by, holdout window, stop condition (what result parks / rejects it), author, Cairo date.

### 6.4 `zb-research-run/1` output [proposal]

`{format, run_digest, prereg_id, candidate, git_sha, manifest_digest, config_sha256, seed, split, survivorship,
cost_rows[], metrics{...per cost row}, episodes, trades_csv_sha256, equity_csv_sha256, trial_index, warnings[]}`.
"Reproducible clean-checkout report" (plan D2) = given the git SHA and a data store matching the manifest, a clean
checkout regenerates the identical `run_digest` (CI runs the harness on a tiny committed fixture manifest to prove it).

---

## 7. EDGE-00 stop / go criteria [proposal; thresholds are Codex's call]

EDGE-00 is run per candidate (and per side: no forced symmetry) on a `point_in_time` manifest after its pre-registered
holdout run. Baselines, all with the identical cost model, universe, sizing and holding-time distribution:

- **B0 random**: random entries on the same symbols / sides / days, same stop distance in ATR, holding time drawn from
  the candidate's own distribution (1,000 seeded replications);
- **B1 hold**: equal-risk buy-and-hold (long candidates) or short-and-hold (short candidates) of the universe;
- **B2 simple trend**: sign of the 20-bar return with a 2.5 ATR stop (the cheapest "is it just beta / momentum" check);
- **B3 cash**: no trade (the gate against negative expectancy).

| Gate | GO requires (all) | PARK if | REJECT if |
|---|---|---|---|
| E1 sample | >= 60 episodes total and >= 20 in the holdout | fewer | - |
| E2 expectancy | episode-clustered 95% CI of mean net R (base costs) has lower bound > 0 over walk-forward + holdout combined | lower bound <= 0 but holdout mean > 0 | holdout mean <= 0 |
| E3 holdout consistency | holdout mean >= 50% of the walk-forward mean | below 50% | - |
| E4 vs random | episode-clustered CI of (candidate - B0 median) excludes 0 | includes 0 | candidate below B0 median |
| E5 vs simple | candidate mean R >= B2 mean R at equal risk | below | - |
| E6 cost stress | mean net R > 0 at 2x fees+slippage and at 2x funding | <= 0 under one stress | <= 0 under base x 1.5 |
| E7 concentration | no symbol > 35% and no calendar year > 50% of net PnL; top-5 episodes < 50% | otherwise | - |
| E8 sensitivity | sign kept in >= 80% of parameter neighbours | otherwise | - |
| E9 survivorship | point-in-time result keeps E2's sign | sign flips vs survivor-only | - |
| E10 tail | Monte Carlo 95th percentile max DD within the GOV-01 grade's drawdown ceiling | above | - |
| E11 operational | follower feasibility >= 90% of trades at 200 USDT; gap-exposed trades reported | below | - |

- **GO** = every gate passes: the candidate may proceed to shadow / testnet canary (still behind REC-02 / TNET-01 and
  Codex's promotion gate).
- **PARK** = no REJECT condition, at least one PARK condition: kept as *experimental*, no further tuning on the same
  holdout; it can return only with a new pre-registration on new forward data.
- **REJECT** = any REJECT condition: recorded with its numbers, never re-tuned into acceptance.
- **Before adding strategy families**: EDGE-00 also compares GO candidates with each other (episode-overlap and return
  correlation); a new candidate whose returns are > 0.7 correlated with an existing GO candidate must add a distinct edge
  (E4 against that candidate instead of B0) or is parked as redundant.

Worked example (illustration only, **not** an EDGE-00 run): the M3 `trend_ema_mom.v1` base kill-OFF row has 93
episodes, holdout 37 episodes, episode CI [-0.041, +1.295], holdout mean +0.587 R vs in-sample +0.487 R **[read]**. It
would fail E2 (lower bound below 0), pass E3, and cannot be judged on E4 / E9 / E6-funding at all because B0 is not
built, the universe is survivor-only and funding is a flat assumption: **PARK pending DATA-01**, not REJECT.

---

## 8. Implementation order after acceptance (Claude Code, one PR each)

1. **DATA-01a-loader**: archive downloader with `.CHECKSUM` verification, normaliser to the section 3 schemas,
   `zb-data-manifest/1` builder, `load()` with hash verification. Tests on a committed miniature archive fixture.
2. **DATA-01-gaps**: `zb-data-gaps/1` checker (all classes), run on the first real manifest; the report is committed.
3. **DATA-01-universe**: PIT universe builder + delisting handling; survivor-only vs PIT comparison on `data_long` core 8.
4. **RES-01-core**: PIT view, execution policy, cost model with real funding, stats (episode bootstrap, Monte Carlo),
   splits / holdout guard / pre-registration check, `zb-research-run/1`, causal truncation test, import-isolation test.
5. **RES-01-repro**: the M3 candidate re-run on the first PIT manifest as the harness's acceptance case (it must
   reproduce the M3 numbers on the survivor-only `data_long` manifest when funding is set to the flat model, to the
   cent, before any new number is believed).
6. **EDGE-00-report**: baselines B0-B3 and the gate table, first run on whatever candidates STRAT-00 has pre-registered.

Each step ends with READY FOR CODEX + HANDOFF TO COWORK markers; Cowork re-runs the shards in its environment against
the same manifest digest (results from different environments are complementary, never silently pooled).

---

## 9. Open questions for Codex

1. **Archive as primary source**: accept `data.binance.vision` (with its checksums) as the primary historical source and
   REST as the live tail? Cowork's sandbox cannot reach Binance REST **[read: collector review]**; can it reach the
   archive host? If not, Windows (Claude Code / Codex) fetches and Cowork verifies by hash.
2. **`available_ms` for 30-day ratio / OI rows**: accept the conservative `timestamp + period`, or does Codex have
   evidence the venue stamps them at the period end (which would make `available_ms = ts_ms`)?
3. **Universe rule**: accept `pit-top40-qv30d-v1` (weekly, trailing 30-day quote volume, 30-day listing age), or
   prefer a fixed list re-derived point-in-time each year?
4. **Holdout length**: last 12 months of the window + all post-freeze forward data. Too long for scalp candidates with
   many trades / too short for 4h trend with ~40 trades a year?
5. **EDGE-00 thresholds**: are 60 / 20 episodes, the 35% symbol / 50% year concentration caps, the 80% neighbour rule
   and the 0.7 redundancy correlation acceptable starting values, or should they come from ENG-GATE-01's baselining?
6. **Intrabar default**: stop-first when 1m data is absent: accept as the conservative default for every timeframe, or
   require DATA-01a 1m coverage for any candidate with a target closer than 1 ATR?
7. **Fee tier**: research at VIP0 taker for every order, or model maker fills for limit-entry candidates (with a fill
   probability from `agg_trades`)?
8. **Legacy evidence**: confirm that all `research/` outputs and `data/`, `data1h/` results are retired as evidence
   (kept for history), and that `data_long` is re-imported as a `legacy-unverified` manifest only to reproduce M3.
9. **Where the data store lives**: `C:\Dev\...\data_store` on the Windows machines plus a hash-verified copy for Cowork;
   or a release-asset / LFS archive of the raw zips (size: roughly tens of GB for 1m + aggTrades of 40 symbols over
   5 years; 4h/1h + funding + metrics is small) [estimate].
10. **TradFi perps (XAUUSDT)**: include in DATA-01 now (plan item 7 says validate Binance XAUUSDT first), or a
    separate DATA-01b once VENUE-01 defines its session rules?
11. **Beyond Holm**: add Deflated Sharpe / PBO (CSCV), as proposed in `T09a_design_draft.md`, to EDGE-00 now, or only
    once a candidate family has enough variants for them to be meaningful?
12. **Collector acceptance**: the REST collector's final acceptance was waiting on an in-app PAPER check. Is its
    `data_market/` output (from the start date) the DATA-01 live tail, or does DATA-01 run its own tail fetch so that
    every row has a per-request query record (section 4.1)?

LANES: Build (Claude Code) - this draft, then idle-ready for DATA-01a-loader on acceptance or any Cowork/Codex defect on
nc-management-driver / nc-tnet01-mgmt. Evidence (Cowork) - next: verify section 2's inventory and the M3 worked example,
check archive reachability (Q1). Integration (Codex) - next: rule on Q1-Q10 and the EDGE-00 thresholds.
