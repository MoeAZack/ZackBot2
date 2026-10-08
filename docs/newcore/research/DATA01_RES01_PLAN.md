# DATA-01 / RES-01 scoping plan (draft for Codex ruling, Claude Code, 09 Oct 2026 Cairo)

Scope: owner alignment on issue 13 (comment 6070651161) makes strategy evidence the primary lane once the three safety
blockers (two S1 P1s, #47 target-orphan) close. This plan is the short, buildable cut of the full contract draft
`docs/newcore/research/DATA01_RES01_DRAFT.md` on `prep/data01-res01` (ed778f3); schema detail lives there. **No new
framework:** everything below extends code that already exists on master or on the `nc-*` branches.

## 1. Frozen point-in-time dataset (DATA-01 v1)

| Item | Decision |
|---|---|
| Venue / symbols | Binance USD-M perps. Universe `pit-top40-qv30d-v1`: weekly, top 40 by trailing 30-day quote volume, listed >= 30 days, **delisted symbols kept until delisting with a forced exit**. Core 8 (BTC ETH SOL BNB XRP DOGE AVAX LINK) reported separately as the legacy comparison. |
| Timeframes | 4h and 1h (trend, short, range). 15m + 1m only for the Quick Bank candidate (DATA-01a, pulled for that candidate only). |
| Series | last-price klines, mark klines, signed funding history (`fundingTime`), exchangeInfo/rules snapshots. OI / long-short / taker only if a pre-registration needs them. |
| Sources | `data.binance.vision` monthly/daily archives with per-file SHA-256 `.CHECKSUM` (primary, includes delisted symbols); existing REST collector (`market_data.py`, `market_collector.py`) for the post-archive tail and rules snapshots. Overlap cross-checked (gap class X1). |
| Window / freeze | 2021-12-19 -> freeze date (first build). Everything after the freeze date is forward-only data. |
| Point-in-time rule | every row carries `available_ms` (bar open + interval; funding at `fundingTime`); the harness view filters on `available_ms <= t` only. |
| Manifest | `zb-data-manifest/1`: per file source URL/query, archive checksum, our SHA-256, rows, first/last, `available_ms` rule, Cairo access time, gap-report digest; manifest digest = SHA-256 of the canonical JSON. A result cites exactly one manifest digest; manifests are never edited. |
| Gaps | `tools/research/gap_check.py` (`prep/data01-gapcheck`, 4f3cb49) already emits `zb-data-gaps/1`; it runs on every manifest and its report is committed. |
| Legacy | `data_long/`, `data/`, `data1h/` and `DATA_MANIFEST.json` import as one `legacy-unverified` survivor-only manifest, used only to reproduce M3. All `research/*` outputs retired as evidence. |

## 2. Cost, funding, slippage and filter model

- **Fees:** VIP0 taker 0.05% per side for market entries/exits; maker 0.02% only for resting targets that the engine
  actually places as limits (no maker entries in v1). Tier recorded in the run.
- **Funding:** signed actual rate x mark notional at each `fundingTime` the position spans (longs pay positive, shorts
  receive). The legacy flat 0.005%/bar stays only as a reproduction row.
- **Slippage:** `max(2 bps, k x ATR-proxy)` per side; gap/stop fills at the bar open when price opens through the stop.
- **Intrabar:** stop-first when stop and target touch the same bar unless 1m data covers it (share reported).
- **Filters:** tick, step, min qty, min notional from the rules snapshot valid at decision time; a size that rounds
  below minimum is a counted refusal (reuse `exchange_rules.py` / `feasibility.py`).
- **Stress rows (always reported):** fees+slippage x2, funding x2 (sign kept), slippage x5 on gap/stop fills,
  flat-funding legacy row.

## 3. One shared causal harness (RES-01)

Built inside the existing NEWCORE research path, not beside it:

- **Strategy input:** the NEWCORE strategy `Evaluation` (`newcore/strategy/*`, `nc-strategy-ema-mom`), pure,
  closed-candle, called with a PIT view `Dataset.view(t)`. Each candidate is one adapter: `evaluate(view, symbol, side)
  -> Signal | None` plus a management preset.
- **Execution + management:** the S4 Runner path (`newcore/runner/*` with FakeVenue/FileJournal on `nc-s1-slice`) and
  the pure NC-07 core (`newcore/management/{core,sim,presets}.py`) for TP1 / break-even / runner / bounded DCA. Long,
  short, range and scalp run through the same fills, stop ordering and rounding as runtime.
- **Statistics:** generalise `newcore/runner/evidence.py` (episode-clustered bootstrap) and
  `newcore/strategy/research/ema_mom_after_cost.py` (cost rows, seeded 10k bootstrap); reuse `lab.py`'s truncation
  lookahead check and `replay.py`'s parity gate as tests.
- **New modules (thin):** `data.py` (manifest load + PIT view), `costs.py`, `splits.py` (walk-forward + sealed holdout +
  access log), `report.py` (`zb-research-run/1`). Research code is never imported by runtime (AST import test).
- **Causality tests:** truncation re-run at 200 seeded decision points must reproduce every decision; entry is always
  the next bar's open; a deterministic clean-checkout run on a committed fixture manifest reproduces the run digest.

## 4. Preregistration (`zb-prereg/1`, committed before any run)

`candidate_id`, mechanism / hypothesis (why the edge should exist, which side, which regime), universe rule, manifest
digest, timeframe, the one primary parameter set, the neighbour grid (+/- one step each), primary endpoint (episode-
clustered mean net R), rejection criteria (the EDGE-00 REJECT/PARK rows that apply), split: anchored walk-forward
6-month folds on the in-sample window + sealed holdout = last 12 months of the manifest + all post-freeze forward data,
stop condition, author, Cairo date. The harness refuses a holdout run without a prior committed prereg with matching
digest/parameters, and refuses a second holdout look (append-only `holdout_access.jsonl`). Every configuration run is
appended to `trials/<candidate>.jsonl`; `n_trials` and a Holm correction appear in the report.

Note: the 2025-2026 window was already seen for `trend_ema_mom.v1` (M3). For that candidate only post-freeze forward
data counts as untouched holdout; the report must say so.

## 5. Report metrics (`zb-research-run/1`, per side, per cost row)

1. After-cost expectancy: mean net R and net % equity; profit factor; win rate; avg win / avg loss.
2. Drawdown: max mark-to-market DD at bar closes; Monte Carlo (episode bootstrap, 10k) DD at p5/p50/p95; longest underwater.
3. MFE / MAE per trade (in R) and give-back = (MFE - realised R) / MFE, distribution and median.
4. Counts: trades, episodes (overlapping holding windows on any symbol), refusals by reason, exposure time.
5. CIs: 95% episode-clustered bootstrap (gating) and per-trade bootstrap (reported only); seed recorded.
6. Concentration: net PnL share by symbol, calendar year, PIT regime (trend / range / high-vol) and top-5 episodes.
7. Neighbour stability: share of +/- one-step neighbours keeping the endpoint sign; neighbour heat table.
8. Stress: every row in section 2; survivor-only vs PIT universe delta.
9. Baselines: B0 matched random entries (1,000 seeded), B1 hold, B2 20-bar momentum, B3 cash.
10. Follower feasibility at 100 / 200 / 500 USDT: share of trades clearing min qty/notional, R error from step rounding.

## 6. First four candidates

| Family | Candidate | Exists | Must be built |
|---|---|---|---|
| Long trend | `trend_ema_mom.v1` (4h) | Strategy + after-cost script (`nc-strategy-ema-mom`), S4 Runner run and M3 evidence pack (`nc-s1-slice`); M3 result: PARK pending DATA-01 | Re-run on PIT manifest with real funding; reproduce M3 to the cent on the legacy manifest first (harness acceptance case) |
| Asymmetric short | `short_breakdown.v1` (4h), from legacy `bear_breakdown` (BTC downtrend + 20-bar-low break, exit on 10-bar-high or BTC regime flip) | Legacy signal in `strategies.py`; mirror parity machinery (`runner/mirror.py`) | NEWCORE `Evaluation` adapter with its own calibration (not the ema_mom mirror); funding receipt matters most here |
| Range / MR | `RANGE-BB-MR.v1` (4h) | Management preset in `newcore/management/presets.py` (stop 2 ATR, one add at 1 ATR, TP1 50% at 1 ATR, BE, 12-candle time exit); legacy `regime()` ADX/BB-width filter in `strategies.py` | Entry adapter (BB-band touch inside PIT range regime) + regime label in the PIT view |
| Quick Bank scalp | `quick_bank.v1` (15m, 1m intrabar) | NC-07 TP1 + break-even + runner + bounded one-add DCA primitives | Entry adapter, DATA-01a 15m/1m slice for the universe subset, spread/latency stress; green-after-cost measured honestly |

## 7. Slice order (one PR each, READY FOR CODEX + HANDOFF TO COWORK per slice)

| # | Slice | Rough size |
|---|---|---|
| R0 | Merge gap checker (`prep/data01-gapcheck`) + this plan's ruling | done / review only |
| R1 | DATA-01 loader: archive fetch with checksum, normaliser, manifest builder, `load()` + fixture manifest | ~500 LOC + tests |
| R2 | PIT universe + delisting exits; legacy manifest import | ~250 LOC + tests |
| R3 | RES-01 core: PIT view, cost model with real funding, splits/holdout guard, prereg check, report, truncation + import tests | ~700 LOC + tests |
| R4 | Acceptance case: M3 reproduced on legacy manifest, then first PIT run of `trend_ema_mom.v1` | ~150 LOC + report |
| R5 | Short and range adapters + preregs, runs | ~300 LOC + 2 reports |
| R6 | DATA-01a 15m/1m slice + `quick_bank.v1` adapter, prereg, run | ~400 LOC + report |
| R7 | EDGE-00 comparison table across the four; promote / park / reject recommendations for Codex | ~200 LOC + report |

Open for Codex: archive as primary source; universe rule; holdout length per timeframe; stop-first default; EDGE-00
thresholds (draft section 7). Cowork: leakage/causality/survivorship review of sections 1, 3 and 4.
