# NEWCORE vertical slice (VS-01) build plan — PRE-STAGE, Claude Code

Status: read-only build plan for Codex (Integration) and Cowork (Evidence). 08 Oct 2026, Africa/Cairo. Implements nothing.
This is not a contract: every "Recommend" is input for Codex's ruling.

**Inputs read:**
- `origin/master` @ `1a24e70`: NEWCORE plan v3.1, strategies.py, backtest.py, binance_client.py, research/*, DATA_MANIFEST.
- NC-01 contract: `origin/pr36:docs/newcore/NC01_CONTRACT.md` (r2).
- NC-01 code: local `nc-01-domain` @ `571c5c4` (C:\Dev\ZackBot2_aud05, 13 commits ahead of master, 30 files).
- NC-02 design: `origin/prep/nc02-negative-fixtures:docs/newcore/nc02_prep/impl/nc02_design.md` and the A01–A24 acceptance draft r4.
- Golden pack: `origin/golden-short-mirrors` @ `eb8c82b` (28 cases + TIERS.json).
- Strategy evidence: research/*.csv/json on master, plus `origin/claude-proposals-2026-10-07:docs/proposals/T09a_design_draft.md` §7.2–7.3.

**Owner rule applied to every item below:** a new abstraction must prevent a confirmed serious failure or directly unblock
the runnable slice; otherwise it is deferred.

---

## 0. The slice in one picture

```
 Bars (frozen CSV | venue klines, closed candles only)
   └─> strategy.signal(bars)            pure: le / se / lx / sx at candle close
        └─> risk.admit(...)              pure: qty (floor to step) or SKIP(reason)
             └─> Decision{ENTER, PLANNED intent}
                  └─> journal.append(IntentRecorded) -> DURABLE        (NC-02a: fsync before send)
                       └─> venue.submit(intent) -> OrderResult          (NC-03: Fake | BinanceTestnet)
                            └─> journal.append(ResultObserved) -> apply -> Lot (fill ledger)
                                 └─> PROTECT intent (STOP_MARKET reduce-only) -> durable -> submit -> WORKING
 every candle close / cycle:
   reconcile(portfolio, venue)  -> MANAGE | HOLD(items)
   manage(lot, bars, params)    -> stop / target / partial / trail / time / signal exit decisions (NC-07, pure)
   close FINAL result           -> lot removed -> outcome projection (R, pnl, fees, exit code) -> trades.csv
```

There is one `Runner` loop. Replay, backtest and testnet differ only in the injected `Clock`, `BarSource` and `VenuePort`.

---

## 1. Minimal module set (smallest viable version of each)

| Module | Package | Smallest viable version for VS-01 | Explicitly deferred |
|---|---|---|---|
| NC-01 domain | `newcore/domain/` | **Taken as is** (see 1.1). | — |
| NC-02a store | `newcore/store/` | Durable journal plus fold-based recovery (see 1.2). | Snapshots and compaction, anchors, evidence envelope, migration, GC (NC-02b). |
| NC-03 venue | `newcore/venue/` | One `VenuePort` with two adapters: `FakeVenue` and `BinanceTestnetVenue` (see 1.3). | Maker/post-only, trailing entry, websocket user stream, request-weight budget (NC-04), one-way mode. |
| Market data | `newcore/data/` | `BarSource` over frozen CSVs (sha256 checked against DATA_MANIFEST) and over venue klines (forming candle dropped). Golden `market` spec builder. | Funding/OI/mark history (DATA-01), 1m/5m execution datasets (DATA-01a). |
| Strategy | `newcore/strategy/` | One pure function `ema_mom(bars, params) -> {le, se, lx, sx}` plus indicators: EMA, Wilder ATR, returns (see §2). | Registry, regimes, the other legacy strategies. |
| NC-06 risk v0 | `newcore/risk/` | One `admit()` gateway (see 1.4). | Correlation, allocation, follower feasibility, grades/bias controls, Kelly/governor. |
| NC-07 manage v0 | `newcore/manage/` | Pure transitions: initial stop, target, tp1 partial + breakeven, ATR trail, time exit, signal exit (see 1.5). | Ladder, runner/TTP, DCA/pyramid (these arrive with the range/scalp slice), maker reprice. |
| Reconcile v0 | `newcore/reconcile/` | Boot and per-cycle match of owned lots/intents against venue positions and open orders. Resolve UNKNOWN by client id. Routine stop verification. Mismatch → HOLD item. | Owner per-item UI (CLI only), multi-account. |
| NC-08 runner | `newcore/sim/`, `newcore/run.py` | One `Runner` with `ReplayClock`+`FakeVenue` (backtest/replay) or `WallClock`+`BinanceTestnetVenue` (testnet). Golden adapters `newcore_sim` and `newcore_live_sim`. | Versioned dataset registry beyond DATA_MANIFEST; Monte Carlo inside the runner (it lives in the report script). |
| Outcome/obs v0 | `newcore/report/` | Derived projections from the journal: `trades.csv` (one row per lot: sym, side, i_in, i_out, exit code, R, pnl, fees, funding, MFE/MAE) and a JSONL decision/incident log. | Full NC-09 incident model, UI, Telegram, PWA. |
| Scheduler | (inside Runner) | Single-threaded loop; network calls never under a lock; a priority order `PROTECT > CLOSE > REDUCE > ENTER` taken from `Decision.priority`. | NC-05 lock-free read model, NC-04 multi-account workers. |

### 1.1 NC-01 domain: taken as is

These parts are used unchanged:
- `OrderIntent`/`OrderResult` lifecycle, `may_send`/`may_apply`.
- `Lot` fill ledger (`fills`, `risk_distance`, `risk_usd`, `tp1_done`).
- `Protection` and `protection_status`.
- `InstrumentRules.quantize_qty(DOWN)`/`check_intent`.
- `Decision` with reason, authority and priority.
- The `EntriesMode`/`permitted` table.
- `Portfolio` trust (`UNKNOWN`/`KNOWN`/`KNOWN_EMPTY`) and the canonical codec.

How the slice fits the domain with no new fields:

- **Targets, tp1, trail-crossed and time/signal exits are bot-side MARKET `REDUCE`/`CLOSE` intents.** NC-01 has no
  take-profit order type, and the goldens define `TP_FULL` and `STOP_CROSSED` as bot market closes anyway.
- **The only exchange-resident order is the protective `STOP_MARKET`.** It is reduce-only, mark-price, with a
  deterministic client id.
- **Trail best-price is derived from closed bars since the fill candle,** not stored. It is restart-safe because the
  klines are exchange truth.
- **The time exit is derived from `opened_at_ms` plus the timeframe.** The C11 rule counts from the fill candle.
- **The closed-trade outcome and funding are projections,** built from the lot's fill ledger and the exchange income
  history. No new NC-01 record is needed (decision O5).

### 1.2 NC-02a: minimum store (journal intent and result, and recover)

**Keep from `nc02_design.md`**, with the same frame format so NC-02b adds to it without format churn:

- `FsSeam` for all I/O (the crash/fault injection point), plus the AST test that no other `open`/`os.*` call exists.
- File frame §3.1 (`ZBNC` header, framed records with CRC32) and event segments §3.4 with `lsn` and the `prev` hash
  chain.
- Append protocol §4.2 E0–E3:
  1. validate with the reader schema (A18);
  2. write;
  3. fsync;
  4. only then release the caller to SEND (for an intent) or APPLY (for a result).
- Recovery is `fold(genesis, events)` → `Portfolio`. It is deterministic and pure; replaying twice gives identical
  canonical bytes.
- With no snapshots, recovery always replays from genesis. At slice volumes (well under 10k events per month) this is
  fine.
- **Boot rules kept:**
  - A01: a future or unknown frame or format version means ABORT-RO with zero writes.
  - A torn tail stops the fold at the last good record. It is seal-and-roll (D3) without the evidence copy, because the
    writer never writes secrets.
  - Mid-segment damage means HOLD with ownership UNKNOWN (A06, A17).
  - A09: INIT only against a flat exchange snapshot gives `KNOWN_EMPTY` with a `FLAT_SNAPSHOT` proof. A non-flat
    exchange gives HOLD-INIT.
  - A19: results come only from FINAL evidence; a bare not-found never resolves anything.
  - A05/A08: HOLD is durable, and leaving it needs a reconciliation record built from a fresh exchange match.
- **A21 + A23 + minimal A24.** A store write failure blocks every send except the emergency reduce-only stop with a
  deterministic client id, placed and confirmed before the old one is cancelled. The account then goes to hard HOLD
  until restart.
  - **Why this is in the slice:** without it, a disk-full between fill and stop leaves exposure unprotected. That is a
    confirmed serious failure class.
- One account directory and one HEAD (dual-slot `HEAD.a/.b`, design D1). The HEAD names the segments and the
  `writer_seq`.

**Deferred to NC-02b (still required before ENG-GATE-01):**
- snapshots and compaction (§3.3, §4.1), generations and the high-water anchor outside `data\` (A03, D2);
- the DPAPI evidence envelope (A04, D5);
- migrations (A13), GC (D16), by-binding index and multi-account directories, OneDrive/network-path refusal;
- the full rollback matrix.

### 1.3 NC-03: one interface, two adapters

`VenuePort` is synchronous. It is called outside state locks and never retries internally; the Runner owns retry policy.

```
rules(symbol) -> InstrumentRules              closed_bars(symbol, tf, since_ms) -> bars   (or a separate BarSource)
submit(intent) -> OrderResult                 KNOWN | UNKNOWN (lost answer) | FINAL (refused / filled)
query(intent) -> OrderResult                  by deterministic client id (classic, then algo id for PROTECT)
cancel(intent) -> OrderResult
position(symbol, side) -> PositionRead        open_orders(symbol) -> owned/foreign order list
equity() -> Decimal                           fills(intent) -> [(qty, price, fee, at_ms)]   (fee truth for the ledger)
```

**`FakeVenue`** (deterministic, bar-driven):
- Market orders fill at the next open × (1 ± slip). Taker fee is charged on notional.
- `STOP_MARKET` rests on the book:
  - It fills on the zb-path/1 intrabar walk (green candle o→l→h→c, red o→h→l→c, doji = adverse-last).
  - It fills at the open when the candle gaps through it.
- Mark price = the current path point.
- Fault injector with `lost_response(nth, truth)`, `exchange_outage(from_ms, to_ms)`, `restart(at_ms, down_ms)`, `reject`
  and `partial_fill`. These are the golden fault kinds plus two.
- Positions are hedge-mode (LONG/SHORT).

**`BinanceTestnetVenue`** (thin new REST client, about 250 lines):
- HMAC-SHA256 signing, `recvWindow`, server-time sync.
- Endpoints:
  - `POST /fapi/v1/order` (MARKET, STOP_MARKET `workingType=MARK_PRICE`, `positionSide`, `newClientOrderId`);
  - algo fallback `POST /fapi/v1/algoOrder` on -4120/-1116/-1102/-4136 (from `binance_client.stop`);
  - `GET /fapi/v1/order?origClientOrderId`, `DELETE /fapi/v1/order` and `/fapi/v1/algoOrder`;
  - `GET /fapi/v2/positionRisk`, `/fapi/v1/userTrades`, `/fapi/v1/income` (funding), `/fapi/v1/exchangeInfo`,
    `/fapi/v1/klines`.
- Base URL allow-list: `https://testnet.binancefuture.com` only. The constructor refuses any `AccountBinding` whose
  environment is not TESTNET (NC-01 invariant 13).
- A timeout returns `UNKNOWN`, never "failed".
- No retry of a send. Re-query by client id instead.

**Shared rules:**
- Client ids are deterministic:
  - order: `zb-<base32(sha256(account_id|intent_id|attempt))[:24]>`;
  - protect: `zb-es-<...[:20]>`, from `protect_key` (nc02_design §7).
- One shared contract-test suite runs against `FakeVenue` and against recorded testnet cassettes (request/response
  JSON). The fake cannot drift from the real venue without a red test.

### 1.4 NC-06 risk gateway v0 (`admit`)

The decision points, in order:

1. `entries_mode == ACTIVE` and binding CONFIRMED; otherwise SKIP with the mode's reason.
2. Raw size: `risk_usd = equity × risk_pct × share` and `qty_raw = risk_usd / stop_distance`.
   - `stop_distance = k × ATR(closed bar)`.
   - The sizing mode `legacy_replay` reproduces the golden `account.sizing`.
3. Leverage cap: `qty ≤ (max_lev × equity − open_notional) / px`. This caps the size; it does not skip the trade.
4. Quantize DOWN to step. SKIP if below `min_qty` or `min_notional`. Never round up (BT02/C25).
5. Mechanical ceilings:
   - `risk_pct ≤ hard_max` (canary 1%, golden 2–10% only inside the golden adapter);
   - `max_pos` per slot;
   - one lot per symbol/side;
   - max concurrent lots.
6. Halts:
   - daily-loss halt on the **Cairo trading day of the decision time** (the candle close; G-DAY-CAIRO, default 8%,
     canary 3%);
   - an account drawdown kill from the peak → `HALTED` plus FLATTEN.

Everything is pure. Every SKIP carries a `risk_gateway.*`/`capacity.*` reason code.

### 1.5 NC-07 management v0 (pure `manage(lot, bars_since_fill, mark, params) -> [Decision]`)

| Behaviour | Rule | Exit code |
|---|---|---|
| Initial stop | `k × ATR` from the fill price. Exchange STOP_MARKET placed right after the fill result is applied. If placement fails, close at market. | `STOP_HIT` / `STOP_FAILED` |
| Target | `tp_r × R`. Bot market close when the path or mark crosses it; a gapped open closes at the open. | `TP_FULL` |
| Partial | tp1 `frac` at `r1`, floored to step. **A part that floors to zero sends nothing and marks nothing** (C25). Then the stop moves to breakeven. | `TP_PARTIAL` (event trace) |
| Trail | Chandelier `best − trail_atr × ATR`, ratchet only, at candle close. Replace = place new, confirm, cancel old. If the level is already crossed: market close. | `STOP_CROSSED` |
| Time exit | N candles counted from the **fill candle**, decided at candle close (C11). | `TIME_EXIT` |
| Signal exit | `lx`/`sx` at candle close. | `SIGNAL_EXIT` |

For VS-01's `ema_mom` only these are active: initial stop, signal exit and (optional, separately measured) trail. The
rest exist so the golden pack runs. They are needed again by the range/scalp slice.

---

## 2. The first trend strategy: `ema_mom` 4h (long candidate; short = mechanics proof, not an edge claim)

### 2.1 Why this one

Source: Claude's read-only evidence sweep of master research/* and the T09a draft. All numbers are in-sample, and the
path is given for each one. Cowork has not yet re-run them independently.

**`ema_mom` long, 4h, core 8** is the best-evidenced simple rule:

| Source | Window / setup | Trades | PF | Return | Max DD | Notes |
|---|---|---|---|---|---|---|
| `research/long_all.csv` (`strat:ema_mom`) | 2022-01 → 2026-10, 2% risk | 205 | 2.26 | +323% | −33% | Win rate 31%. Years 2022 −18, 2023 +93, 2024 +25, 2025 +28, 2026 +68. |
| `results_single.csv`, core 8 | 2 years, 1% risk | — | 2.95 | — | −11.5% | Halves +29 / +28. |
| `results_single.csv`, top 40 coins | 2 years, 1% risk | 294 | 2.37 | — | — | Breadth holds. |
| T09a §7.3 rerun | 4.8 years | — | — | $500 → $2,134 | −34% | Mean +0.49R. |

- `ema_st` has a higher return but fails breadth: top 40 PF 1.27, DD −47%.
- The breakout rules have DD of 50–67%.

The rule is also the **simplest to build**: three EMAs, two return filters, one ATR stop and one signal exit. There are
no partials, trail, pyramid or DCA, so the first runnable slice needs the fewest management transitions.

**Short side:**
- `ema_mom`'s `se` mirror already exists and is mechanically symmetric. It exercises the short path end to end (sizing,
  sell entry, buy-stop, short exit, funding sign, reconciliation) with no new signal code.
- Its **evidence is negative**: T09a §7.2 gives −0.09R/trade ungated and −0.15R with a bear gate.
- **No existing short trend rule is acceptable.** Ungated: `donchian_ens` +0.08R at DD −53%, `breakout_pyramid` +0.22R
  at DD −67%, `bear_breakdown` PF 1.02. All of these turn negative under a bear gate.
- So VS-01 proves the **short path's mechanics** with `ema_mom`-short. A real short candidate (T09a S2b "EMA rejection")
  is a separate STR-01b research item on the same harness. No forced symmetry, per plan D3.

### 2.2 Exact rule spec (VS-01 `trend_ema_mom` v1)

**Data:** closed 4h candles (t, o, h, l, c), UTC, Binance USDT-M perpetual klines.
- Universe: core 8 (BTC, ETH, SOL, BNB, XRP, DOGE, AVAX, LINK).
- Warmup: 220 bars.
- The forming candle is never used.

**Indicators** (computed on closes up to and including bar i):
- `EMA_n(c)`, n = 20, 50, 200. Standard EMA, `adjust=False`, seeded like `strategies.ema`. Parity is checked against
  legacy to 1e-9.
- `ATR14`: Wilder (`ewm alpha=1/14, adjust=False`) of the true range `max(h−l, |h−c₋₁|, |l−c₋₁|)`.
- `ret7d = c[i] / c[i−42] − 1` and `ret30d = c[i] / c[i−180] − 1`.
  - These lookbacks are **defined in days** (7d, 30d) and converted to bars by the timeframe. On 4h that is exactly the
    legacy 42/180.
  - **Why:** the legacy bar-count is why the rule collapsed on 1h (PF 0.71–0.94).
- `xup(a, b)[i] = a[i] > b[i] and a[i−1] ≤ b[i−1]`.

**Signals** (evaluated at the close of bar i):
- `le = xup(EMA20, EMA50) and c > EMA200 and ret7d > 0 and ret30d > 0`
- `se = xup(EMA50, EMA20) and c < EMA200 and ret7d < 0 and ret30d < 0`
- `lx = EMA20 < EMA50 or not (ret7d > 0 and ret30d > 0)`
- `sx = EMA20 > EMA50 or not (ret7d < 0 and ret30d < 0)`

**Entry:** a market order at the open of bar i+1. The replay fills it at `o[i+1] × (1 ± slip)`. On testnet it is sent
in the first cycle after the close of bar i.
- Skip if a lot already exists for that symbol/side, or if `admit()` refuses.

**Stop:** `2.5 × ATR14[i]` from the **fill price**. Exchange STOP_MARKET, mark-price, reduce-only, placed immediately
after the fill.
- R = stop distance.

**Exit:** `lx`/`sx` at a candle close → bot market close (`SIGNAL_EXIT`), or the exchange stop fill (`STOP_HIT`).
- No target, trail, partial or time exit in v1.

**Sizing:**
- Gate/backtest: 1% risk per trade, `max_pos` 4 per side, max leverage 3× effective.
- Golden adapter: case-declared sizing.

**Variants measured separately, never blended into v1's numbers:**
- **v1-bull:** long only while BTC's daily close is above its daily EMA200 (legacy `when='bull'`). This roughly halves
  drawdown (`long2_runs.csv` `bull:*`).
- **v1-trail:** an added 3 ATR chandelier (T09a §7.3 shows it costs expectancy; kept only as a mechanics check).

**Data needed:**
- Replay/backtest: `data_long/4h/{8 symbols}_4h.csv`, 2021-12-19 → 2026-10-04, 10,499 rows each, sha256-pinned in
  `DATA_MANIFEST.json`. Daily BTC is resampled from 4h, using UTC-day closes, for v1-bull.
- Testnet: `/fapi/v1/klines` 4h, at least 220 + 180 closed bars per symbol at boot, plus exchangeInfo rules and
  `/fapi/v1/income` funding.
- Missing today: point-in-time historical funding (DATA-01). Until it exists, use the legacy flat funding cost (charged
  on both sides, conservative) plus a ±funding stress.

---

## 3. Build order (each step runnable and testable on its own)

**Prerequisites (outside VS-01):**
- NC-01 accepted and frozen. PR #36 contract plus `nc-01-domain` @ `571c5c4` under review.
- NC-02a subset ruled by Codex (decision O2).
- VS-01 is built on a new branch from the NC-01 merge. Its NC-02a module may land in the same branch or as its own PR
  first; either way it is one integration owner.

| Step | Deliverable | Runs end to end as | Tests / proof |
|---|---|---|---|
| **S1 walking skeleton** | `data` (CSV + golden market builder), `strategy` (injected golden signals **and** `ema_mom`), `risk.admit` (size, floor, leverage cap), `Runner`, `FakeVenue` (market + resting stop + gap), `MemoryJournal` implementing the **same `JournalPort`** as NC-02a, `manage` (initial stop + signal exit), `trades.csv` projection. | `python -m newcore.run replay --case G-STOP-L-01` and `python -m newcore.run backtest --strategy ema_mom --symbols BTCUSDT --tf 4h` on `data_long`. Both produce journal events and trades. | Goldens G-STOP-L-01, G-STOP-S-01, G-GAP-STOP-L-01, G-GAP-STOP-S-01 via adapter `newcore_sim`. Invariant checked every cycle: **no lot without a WORKING protective stop of equal qty after the cycle that applied its fill**. A replay run twice gives byte-identical journals. |
| **S2 management** | `manage`: target, tp1 + breakeven (C25 floor-to-zero), ATR trail with replace-then-cancel and the STOP_CROSSED close, time exit from the fill candle. Instrument rules from the case. | Same CLI, every golden in tier B. | G-GAP-TP-L/S-01, G-TIME-L-01, G-TIME-S-01, G-TIME-L-02/S-02 (45 s entry-cycle delay through `newcore_live_sim` timing), G-STOP-CROSSED-L/S-01, G-TP1-ZERO-L-01. Pure-function table tests per transition (long and short). |
| **S3 durability + faults** | NC-02a file journal swapped in for `MemoryJournal` (the same port). Boot recovery = fold. Reconcile v0. FakeVenue faults (lost response, outage, restart). Crash-point enumerator kills the Runner at every journal boundary. | `replay --case G-RESTART-TIME-L-01 --store <tmp>` and `--crash-at <boundary>`. | G-RESTART-TIME-L-01, G-AMBIG-ENTRY-L-01, G-OUTAGE-STOP-L-01. Crash matrix: after each boundary, restart reproduces the fault-free trace with no duplicate order and no unprotected lot past the grace cycle. Store-failure drill (A21/A23 hard HOLD). Torn-tail and future-version boot tests. |
| **S4 full backtest + risk v0** | Multi-symbol Runner, daily Cairo halt, drawdown kill, `max_pos`, cost model with funding, the report script (stats, bootstrap, per-year/per-symbol, IS/holdout split). | `backtest --strategy ema_mom --universe core8 --tf 4h --side long\|short\|both --from 2021-12-19 --to 2026-10-04`. | G-DAY-CAIRO-W-01, G-DAY-CAIRO-S-01 and both SHORT mirrors. Cross-check against legacy `backtest.run` with the same signals and mgmt: trade list equal or every difference explained. Report artefact with an exact-SHA header. |
| **S5 testnet adapter** | `BinanceTestnetVenue` plus the shared contract suite. Cassettes are recorded once on testnet (Windows run) and replayed in CI. | `python -m newcore.run testnet-smoke --symbol SOLUSDT --qty min`: open → stop WORKING verified → close → reconcile flat. | Contract suite green on the fake and on the cassettes. A MAINNET binding refused at construction (test). Live smoke: one long and one short round trip, with the journal ledger matching `userTrades` fees and realized PnL. |
| **S6 testnet canary** | `Runner` with `WallClock`, canary bounds (§5.4), and drills (§5.5). | `python -m newcore.run testnet --strategy ema_mom --profile canary`, plus a mechanics profile on 15m (same code). | Drill log with zero unprotected-exposure breaches, zero duplicates and zero unexplained reconciliation diffs. Daily status line (Cairo time). |
| **S7 gate report** | The engine-gate pack (§5) on the accepted head. | One command builds the report from the journals and backtests. | Codex/Cowork review. Numeric thresholds per decision O9. |

**Ordering notes:**
- S1 and S2 need no NC-02a code, only the `JournalPort` interface. They can start the day NC-01 merges.
- NC-02a can run in parallel on separate files (`newcore/store/`), with Codex recording the independence.
- S5's cassette recording can be prepared during S3/S4. Its live smoke waits on decision O1 (testnet account isolation).

---

## 4. Golden cases the slice must pass on its NEWCORE adapter

The pack is `origin/golden-short-mirrors` @ `eb8c82b`: 28 cases, 15 in the core tier. Every case currently has
`newcore_sim`/`newcore_live_sim` = `pending_adapter`. All are 4h, use `costs.model=legacy` (taker 0.0005, slip 0.0002,
funding 0) and `sizing=legacy_replay`. The NEWCORE adapter must reproduce that sizing exactly, or the case cannot be
flipped.

| Expressible at | Cases | What they force in the slice |
|---|---|---|
| **S1** (market entry, ATR stop, no faults) | G-STOP-L-01 (core), G-STOP-S-01 (core), G-GAP-STOP-L-01 (core), G-GAP-STOP-S-01 | FakeVenue resting stop on the zb-path/1 walk, gap fill at the open, fee/slip R math, short mirror. |
| **S2** (management) | G-GAP-TP-S-01 (core), G-GAP-TP-L-01, G-TIME-L-01 (core), G-TIME-L-02 (core), G-TIME-S-01, G-TIME-S-02, G-STOP-CROSSED-S-01 (core), G-STOP-CROSSED-L-01, G-TP1-ZERO-L-01 (core, needs `instruments`) | Target bot close, C11 time clock from the fill candle (the -02 cases with a 45 s slow entry cycle need `newcore_live_sim` timing), trail replace and STOP_CROSSED, C25 floor-to-zero partial. |
| **S3** (faults) | G-RESTART-TIME-L-01 (core), G-AMBIG-ENTRY-L-01 (core), G-OUTAGE-STOP-L-01 (core) | Durable journal plus fold; query by client id for a lost answer; the resting stop protects during an outage and the fill is booked from the exchange record at the exchange time. |
| **S4** (multi-symbol, halt, signal exit) | G-DAY-CAIRO-W-01 (core), G-DAY-CAIRO-S-01, G-DAY-CAIRO-W-SHORT-01, G-DAY-CAIRO-S-SHORT-01 | Cairo trading-day halt at decision time, two symbols, `SIGNAL_EXIT`. |
| **Range/scalp slice (not VS-01)** | G-GAP-DCA-L-01 (core), G-GAP-DCA-S-01, G-DCA-SLIP-L/S-01, G-PYR-SLIP-L-01 (core), G-PYR-SLIP-S-01, G-TRAILENTRY-MAXPOS-SHORT-01 (core), G-TRAILENTRY-MAXPOS-01 | Bounded DCA, pyramid add slippage, trailing entry. The NEWCORE adapter raises `NotExpressible` for these, so they stay `pending_adapter`. |

**Coverage:**
- VS-01 covers 12 of the 15 core cases and 20 of the 28 overall (S1 4, S2 9, S3 3, S4 4).
- Every short mirror except DCA, pyramid and trailing entry passes by S4. These are stop, gap-stop, gap-TP, both time
  cases, STOP_CROSSED and both Cairo-day cases. That is the slice's **mechanical** long/short parity proof.

**Procedure:**
- Flipping `applies_to.newcore_sim` from `pending_adapter` to `required` changes each case's contract sha, so each flip
  needs a CORRECTIONS.json record (decision O7).
- A `newcore_*` entry is added to `goldenlib/adapters/__init__.py` CAPS with `final={'lots'}` and `funding=True`.

---

## 5. Engine-gate measurement plan (VS-01 scope)

### 5.1 Metrics

**Per strategy × side, plus combined:**
- Net expectancy in R per trade after costs, with a 95% bootstrap CI.
- PF, win rate, average win/loss in R, trade count.
- CAGR, max DD on mark-to-market equity at each candle close, and DD duration.
- Monte Carlo (trade bootstrap) 95th-percentile DD.
- Worst trade in R (gap tail) and exposure time.

**Robustness:**
- Per-year and per-symbol contribution. No single symbol above 40% of net; no single year above 50%.
- Parameter neighbours: EMA lengths ±20% and stop 2.0/2.5/3.0 ATR must stay positive.
- Cost stress: 2× fee+slip, ±funding. The rule must stay positive at 2× costs.
- IS vs holdout separately.

**Safety (hard zero, measured in replay faults and on testnet):**
- Unprotected-exposure time: lot qty above the WORKING reduce-only stop qty for longer than the grace period. Grace is
  one cycle in replay and ≤ 10 s on testnet.
- Duplicate orders, phantom lots, orphan stops still open more than one cycle after their lot closed.
- Unexplained reconciliation diffs.
- Ledger mismatch versus exchange `userTrades` + `income` above 1e-8 USDT.

**Parity:**
- Replay and backtest use the same code path, so their trades are identical.
- Testnet vs a replay of the same testnet klines: 100% signal agreement. The fill-slippage distribution is recorded and
  fed back into the cost model.

### 5.2 Data window

- `data_long/4h`, core 8, 2021-12-19 → 2026-10-04 (about 4.8 years). Warmup 220 bars.
- **IS:** 2022-01 → 2024-12. **Holdout:** 2025-01 → 2026-10-04.
- **Caveat:** the legacy research (long_all, T09a) already used the whole window, and presets were chosen on the last 6
  months. No local data is untouched. The only truly untouched sample is forward testnet/shadow data from 2026-10-04 on.
  That should be stated in the report, not hidden.
- Survivorship bias: the universe is today's survivors. A top-40 breadth check uses `data/` (2024-09 → 2026-10).

### 5.3 Cost model

- **Fees:** taker 0.05% per side, matching Binance USDT-M VIP0 and the golden `legacy` model. There are no maker fills
  in VS-01.
- **Slippage:** 0.02% per side on the entry and on bot closes. A stop fill is at stop × (1 ± slip), or at the open when
  gapped.
- **Funding:**
  - Baseline is the legacy 0.00005 per 4h bar, charged on both sides (conservative).
  - Stress: 0.0001 per bar.
  - When DATA-01 lands, use point-in-time Binance `fundingRate` with the correct sign. This matters most for shorts.
- **Stress run:** 2× fee+slip, and the gap-only stop worst case (the doji/worst path policy).

### 5.4 Testnet canary bounds (aggressive but bounded; testnet only)

| Bound | Strategy canary (`ema_mom` 4h) | Mechanics canary (same code, 15m, to get volume) |
|---|---|---|
| Risk per trade | 1% of testnet equity | 0.25%, or min qty |
| Concurrent lots | ≤ 4 (≤ 1 per symbol/side) | ≤ 2 |
| Effective leverage | ≤ 3× | ≤ 2× |
| Daily loss halt (Cairo day) | 3% | 2% |
| Canary DD kill | 10% from peak → FLATTEN + HALTED | 5% |
| Symbols | core 8 | BTCUSDT, SOLUSDT |
| Sides | long + short (short labelled "negative evidence, mechanics only") | both |
| Duration | ≥ 14 days, plus drills | ≥ 3 days or ≥ 50 round trips |

- The mechanics canary proves lifecycle volume only. Its PnL is never reported as edge.
- Mainnet is impossible by construction: the venue allow-list plus the TESTNET-only binding check.

### 5.5 Restart and timeout drills

Each drill is run first in replay (FakeVenue fault injector + crash enumerator) and then on testnet. On testnet, the
injection is a `FsSeam`/HTTP seam in the adapter; the bot is never killed from Claude's own shell for a long run.

| Drill | Injection | Pass |
|---|---|---|
| D1 crash after intent durable, before send | kill at E3 | Restart: query by cid finds nothing, the intent ends NOT_SENT or is re-sent under the same cid. One entry at most. |
| D2 lost entry answer | the HTTP response is dropped after the request reached the venue | UNKNOWN → query → FINAL. Exactly one lot. The stop follows the resolved qty (G-AMBIG). |
| D3 crash between fill and stop | kill after ResultObserved, before PROTECT is sent | Restart: reconcile sees the lot without a stop, places a deterministic-cid stop. Unprotected window ≤ restart time. Logged. |
| D4 crash mid trail replace | kill after the new stop is WORKING, before the old one is cancelled | Restart cancels the old stop only. Coverage never exceeds the lot qty. No gap in coverage. |
| D5 exchange outage ≥ 1 candle | the network seam is blocked | The resting stop protects. After recovery, the fill is booked from the exchange record with the exchange time (G-OUTAGE). No decisions taken during the outage. |
| D6 restart mid trade | stop/start | The holding clock and lot are restored from the fold; outcome identical (G-RESTART). |
| D7 store unwritable | journal path made read-only | A21: no sends. A23: emergency stop only if coverage is missing. Hard HOLD. Restart with a writable store goes through the A08 reconcile. |
| D8 torn tail / future version | truncate the last record / bump `frame_version` | Torn tail: fold to the last good record. Future version: ABORT-RO with zero bytes changed. |

---

## 6. Risks and open decisions for Codex (recommendation first)

| # | Decision / risk | Recommend |
|---|---|---|
| **O1** | **Testnet account isolation.** The legacy bot runs on the same testnet account with 4 protected positions. NEWCORE INIT would see a non-flat account, give HOLD-INIT, and could never trade (A09/A22). | A **second testnet account/API key** for NEWCORE. The owner creates it, because credentials are protected. Fallback: stop the legacy bot and flatten testnet (pre-approved testnet action, but it ends the legacy evidence lane). Never symbol-scoped sharing. |
| **O2** | **What "freeze NC-02" means.** The full `nc02_design.md` (snapshots, anchors, DPAPI envelope, migration, GC) vs the slice subset. | Accept **NC-02a** (§1.2) as the frozen store for VS-01, with the same frame format. **NC-02b** (the deferred items) is a named prerequisite of ENG-GATE-01, not of VS-01. |
| **O3** | **Strategy evidence status.** `ema_mom` long is strong but in-sample (no deflation across about 450+ variants). Its short mirror is negative. No short trend rule is acceptable today. | VS-01 claims engine mechanics plus after-cost **replication**, not a new edge. Long = candidate, ships disabled until the gate and forward data. Short = mechanics proof only. STR-01b (T09a S2b EMA-rejection short, independently calibrated with real funding) is the next research item. |
| **O4** | **Targets/trail/partials as bot-side market closes** (no exchange take-profit; NC-01 has no TP order type). | Bot-side for VS-01; it matches the golden `TP_FULL`/`STOP_CROSSED`. Revisit exchange-side TP only if testnet latency data shows give-back. |
| **O5** | **Outcome and funding records.** NC-01 has no closed-trade or funding record. | Derived projections from the journal plus exchange `userTrades`/`income`. No new NC-01 record (the decision rule). Revisit only if NC-09 needs durable outcomes. |
| **O6** | **Venue client.** A new thin REST client vs importing legacy `binance_client.Futures`. | A new thin client inside `newcore/venue/`, keeping the import boundary clean. Port the legacy error-code knowledge (-4120 algo fallback, -2011/-2013 already gone) as contract tests. |
| **O7** | **Golden adapter flips.** `pending_adapter` → `required` changes the contract sha. | The VS-01 PR flips cases per step with CORRECTIONS.json records. Codex accepts each batch. Unflippable cases stay `pending_adapter`, never `not_applicable`. |
| **O8** | **`ret` lookbacks in days vs bars.** | Days (7d/30d), identical on 4h. This stops the silent 1h degradation. |
| **O9** | **Numeric gate thresholds for VS-01** (ENG-GATE-01 is not numeric yet). | Proposed: long net expectancy CI lower bound > 0 at base costs and point estimate > 0 at 2× costs. Max DD ≤ 35% at 1% risk on the full window. ≥ 100 trades per promoted side. All §5.1 safety counters = 0. Replay/backtest parity exact. Every golden flipped for its step green. |
| **O10** | **Position mode.** | Hedge mode (`positionSide` LONG/SHORT), matching NC-01 `Side` and legacy. Verify on the new testnet account at S5. |

**Top risks:**
1. O1 blocks S5/S6 until a clean testnet account exists.
2. Golden `legacy_replay` sizing and fee-on-notional R math must match to 1e-9, or the flips fail. This is cheap to
   catch at S1.
3. A 4h strategy gives few testnet trades (likely only a handful per week across core 8). Lifecycle volume comes from
   the 15m mechanics canary, which must never be read as edge.
4. No point-in-time funding data, so the short-side economics are unknown until DATA-01.

LANES (proposed): Build — Claude: S1 skeleton the day NC-01 merges, with NC-02a on its own files in parallel if Codex
records independence. Evidence — Cowork: independent re-run of `ema_mom` long/short on `data_long` with the §5.3 cost
model, plus the S2b short calibration harness. Integration — Codex: rule O1–O10, publish the VS-01 contract and record
NC-02a/VS-01 file independence.
