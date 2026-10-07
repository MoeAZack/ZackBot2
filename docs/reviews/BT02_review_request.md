# BT02 (issue #14): exchange-filter feasibility in backtests. Review request

Local branch `bt02-exchange-filters`, base `fbl-bt01-v2` (938086f, BT01 incl. the Codex round-1 fix). Not pushed.

## The defect

`backtest.py` sized fractional quantities and only applied a notional floor (`MIN_NOTIONAL`: BTC 50, ETH 20, LINK 20,
others 5). It did not round to the exchange `stepSize`, did not check `minQty`, did not check adds at all, and skipped
an undersized entry silently. The live engine floors to the step and refuses anything below `minQty` / `minNotional`
(it never rounds up - that would exceed the declared risk). So the $500 backtests count trades the testnet engine cannot
place: BTC at 0.001 minimum (60-120 USDT) is the main gap.

## The fix: one pure shared function

`feasibility.py` (top level - no `core/` package on this base; no I/O, no clock, no network; a test enforces that):

| Function | Used by |
|---|---|
| `round_step(x, step)` | engine `_rd` (moved verbatim) and `size_check` |
| `rules_from_exchange_info(info)` | engine `connect` (same parser: PERPETUAL + TRADING, MARKET_LOT_SIZE, MIN_NOTIONAL default 5), snapshot builder |
| `risk_qty(mgmt, risk_usd, px, atr, sd)` | engine `open_lot`, `backtest.open_pos`, preflight (DCA: `risk / sum(w_k * |level_k - stop|)`, = risk / (24.5 ATR) for n=3/step 1/scale 1.5/stop 2) |
| **`size_check(qty_raw, qty, px, rule)`** | engine `open_lot` + `_add_qty`, `backtest.run` entries + DCA safety orders + pyramid adds, replay, app preflight |
| `snapshot_state`, `diff_snapshots`, `required_qty`, `slot_order_qtys`, `preflight` | app backtest result, `/api/preflight`, panel |

`size_check` floors `qty` to the step, then refuses `q < minQty or q * px < minNotional` - the old engine expression -
and returns `code` `leverage_cap` (the unrounded risk size would pass, only the leverage cap made it too small: the old
engine rule) or `below_min_qty` / `below_min_notional` (text: "size below Binance minimum (raise capital or risk)").
It never returns a quantity above its input; a rule of `None` gives `ok=None` / `code='unknown'`.

### Live engine: zero behaviour change

- `connect`: `self.rules = F.rules_from_exchange_info(info)` + `self.rules_meta` (source, environment, fetch time).
- `open_lot`: sizing via `F.risk_qty`, the min-size gate via `F.size_check`; same skip log line and the same two
  `last_skip` texts (kept as literals in engine.py because the T05a trade-audit reason scan reads them from that file;
  a test pins them to `F.REASON_*`).
- `_add_qty`: `F.size_check(q, q, px, r)` instead of the inline floor + minimum check.
- Proof: `tests/test_bt02_exchange_filters.py` keeps the pre-BT02 engine code verbatim and compares on a dense grid
  (25 rules x 1,500 random inputs incl. exact boundaries: same skip decision, same quantity, same text, for the entry
  and the add gate); `round_step` vs the old `_rd` bit for bit on 20,000 inputs; `connect` parser vs the old one; engine
  `open_lot` / `_add_qty` through the FakeX (no order sent below the minimum, quantity floored). All engine suites pass
  unchanged (below). Only difference: a `stepSize` of 0 (never sent by Binance) used to raise in `_rd`; `size_check`
  skips rounding for a falsy step - unreachable from `connect`.

### Backtester

`backtest.run(..., exchange_rules=None)`: a snapshot (or `{symbol: rule}`):
- entries: risk size -> leverage cap -> `size_check` -> placed with the floored quantity, or **skipped with its reason**
  (no synthetic fill, no fee);
- DCA safety orders and pyramid adds: the engine's `_add_qty` check (refused add = not retried in that candle, as other
  add gates; counted once per position and order level);
- accounting in `cv.attrs['feasibility']`: attempts, executed, skipped by code, `rule_blocked` (sized fine, refused by
  an enforced risk rule - not a feasibility skip), by symbol, by slot, add skips, the first 500 skips with time / slot /
  coin / qty / notional / rule, `executable_pct` = executed / (executed + skipped), `unknown_symbols`.
- **`exchange_rules=None` (default) = the legacy floor**, unchanged bit for bit: every symbol gets
  `legacy_rule(s) = dict(step=None, min_qty=0, min_notional=MIN_NOTIONAL.get(s, 5))` for entries, and adds are not
  checked (as before). Verified against the `fbl-bt01-v2` backtester on real data: balanced 4h top 40 (1,110 trades),
  boost 4h top 40 (852), calm 4h core 8 (265), active 1h core 8 (507): trades DataFrame and every curve point identical.
- A symbol missing from a given snapshot also gets the legacy floor and is listed in `unknown_symbols` (the documented
  "no-op when no rule exists": synthetic test symbols keep their pre-BT02 behaviour, so causality parity is unaffected).
- Not changed (follow-up if wanted): partial closes (tp1 / ladder / runner part) are not floored to the step in the
  backtest; the engine floors them and closes the whole lot rather than leave dust.

`replay.run_replay(..., exchange_rules=None)`: with rules, the simulated exchange serves exactly these filters to the
engine (`F.exchange_info_from_rules`) and the backtester gets the same rules. Default unchanged (step 1e-5).

### Versioned exchange-rule snapshots

- `exchange_rules.py`: `build exchangeInfo.json --env testnet|mainnet` (file saved from `/fapi/v1/exchangeInfo`),
  `fetch --env ...` (public endpoint, no key), `diff old.json new.json`. Each build bumps `version`, records `source`,
  `environment`, `fetched_at`, `verified=true` and prints the rule changes vs the previous file.
- `data/exchange_rules_testnet.json`: **PLACEHOLDER, `verified: false`, `fetched_at: null`, note "unverified, refresh
  from exchangeInfo"**. The sandbox cannot reach Binance and the repo has no exchangeInfo capture; the only filter data
  in the repo are `backtest.MIN_NOTIONAL` (used for min_notional) and the engine test fakes' 0.001 step / minQty (used
  for every symbol). Real testnet steps for SOL / AVAX / DOGE / XRP etc. may be coarser - I did not fill in remembered
  values. No mainnet snapshot is shipped: mainnet state = `unavailable`.
- `snapshot_state` -> `ok` only for a valid, verified, fresh (<= 30 days) snapshot of the asked environment; otherwise
  `unavailable` / `invalid` / `wrong_environment` / `unverified` / `stale`, all shown as **unknown - never a green pass**.
- The app prefers the connected engine's own exchangeInfo rules (this environment, read at connect: state `ok`) over
  the file; testnet and mainnet are never mixed (`mainnet` if the engine is live, else `testnet`).

### Preflight / UI

- `GET /api/preflight`: every profile + the current slots at the current bot capital, through `F.preflight` (the same
  `risk_qty` + `size_check`): status `ok` | `partial` | `infeasible` | `unknown`, executable % of coin/slot pairs,
  undersized pairs with reason, **estimated minimum capital** (quantity is linear in capital; confirmed by re-running
  `size_check` at that capital) and **minimum risk %** at the current capital, later orders (pyramid add / safety order)
  too small, and a plain warning ("This profile cannot place any trade at 500 USDT ..." / "4 of 12 coin/slot pairs are
  below the Binance minimum order size ... Estimated capital for every pair to be tradable: about 6,504 USDT").
  Prices: the engine's candle cache, else the candle files shipped with the app; ATR = median ATR/price of the last 180
  candles x last close.
- Panel: profile cards show the status tag (green only for `ok` with verified rules), executable %, the warning and the
  top undersized coins with their minimum capital / risk. Backtest results show "Executable signals: X% (N placed, M
  skipped as below the Binance minimum order size)" and the most-skipped coins, flagged as an estimate when the rules
  are not verified.
- App backtests (`run_backtest_job`) now run with the rules (`exchange_rules: 'off'` in the request turns it off) and
  return `feasibility` (per timeframe + totals).
- The installer bundles `data/exchange_rules_testnet.json` (`--add-data`), the exe selftest checks it.

## Tests

`tests/test_bt02_exchange_filters.py` (29): engine parity (above); BTC 50 / ETH 20 / altcoin 5 minimums; BTC 0.001
minQty binding before its 50 notional; step floor never up (5,000 random cases: result <= capped <= risk size);
leverage-cap code; no rule -> unknown; `required_qty`; DCA sizing formula; snapshot states (unavailable, invalid, zero
step, unverified, stale, no time, wrong environment, ok); shipped snapshot is labelled unverified and is never `ok`;
builder from an exchangeInfo file, versioning, rule diff; backtest skips BTC / ETH with reason and trades SOL with no
synthetic fill; step rounding scales the P&L exactly by floor(q)/q; rule change between snapshots flips a coin from
placed to skipped; legacy default + missing symbol unknown; pyramid add below the minimum skipped (and fires at 5
USDT); preflight partial with min capital / min risk confirmed on both sides; infeasible warning; never green on unknown
rules / missing rule / no prices; undersized pyramid add flagged; risk-rule refusals counted apart from skips; module purity; replay rule round trip; app backtest
reports rules-mode feasibility; app preflight calls `F.preflight` per profile and is all-unknown on the placeholder.
All pass, also under the CP1252 shim.

Suites (minipytest, each < 2 min): test_bt02_exchange_filters 29, test_bt_intrabar_path 23, test_lab 27,
test_v31_engine 45, test_safety 78, test_fills 46, test_grid 14, test_leverage_auto 185, test_trade_audit 137, test_ci 36
passed; test_verify 16 passed + the known env-only `test_summary_has_the_required_provenance_fields`.

Causality (one case per process, 2 parallel, frozen copy of the branch): all 12 cases (6 modes x engine causality +
backtest-vs-engine parity) and the same-candle-information leak case PASS.

Strict replays (24 steps, `ZB_REPLAY_STRICT=1`, frozen copy; default replay = engine step 1e-5 / legacy backtest floor):

| Replay | BT01 round 1 | BT02 | Strict |
|---|---|---|---|
| 1: MOM pyramid + DCA + Squeeze | engine +54.9 % / bt +53.0 %, matched 133/133, median 0.015, p95 0.11, return gap 1.97 pp, DD gap 0.22 pp | identical (every number) | PASS |
| 2: breakout + bear shorts + Donchian | engine -8.9 % / bt -7.1 %, matched 300/301, median 0.019, p95 0.072, return gap 1.8 pp, DD gap 1.33 pp | matched 300/301, median 0.019, p95 0.072, return gap 1.8 pp, DD gap 1.33 pp (identical to BT01) | PASS |

No tolerance was changed. The strict replays do not pass exchange rules, so BT02 cannot move them except through the
engine refactor (which is decision-identical).

Mutation check (20 mutants of the key lines, BT02 test file only): 19 killed. Survivor: the preflight's minimum-capital
confirmation loop replaced by `break` - a float safety net that never triggers on the tested inputs (the linear estimate
is already exact there). Killed: minNotional / minQty ignored, floor -> ceil, leverage-cap `and` -> `or`, no step
rounding (feasibility / backtest), unverified or stale snapshot accepted, preflight green on unknown rules, no ceil in
`required_qty`, backtest synthetic fill below minimum, pyramid add unchecked, snapshot ignored, skips not counted, engine
skip ignored, engine add minimum ignored, engine skip text swapped, app backtest without rules, app preflight state
forced ok.

## Presets at their advertised capital ($500), BT01 vs BT01 + BT02

Background job (`presets.sh`, one python process per run, 2-5 s each; frozen copy of the branch). Datasets: the research
jobs (`research_long.sleeves`, data_long core 8, as BT01's table) and the app universe (data/ 4h, `all` slots on the top
40). Rules: the shipped **placeholder** testnet snapshot - so the BT02 numbers are an estimate until a real
exchangeInfo snapshot is built.

BT02 preset rerun: BT01 only (no exchange rules) -> BT01 + BT02 (data/exchange_rules_testnet.json, PLACEHOLDER rules: unverified). $500 start (mixed 4h+1h profiles: two $250 sub-accounts, curves added). Executable % = entries placed / (placed + skipped by the exchange-filter check); skips counted per entry attempt.

| Dataset | Preset | TF | Window | Final equity BT01 -> BT01+BT02 | PF | Max DD | Trades | Executable % (BT01 legacy floor -> BT02 rules) | Entry skips (BT02) | Add skips (BT02) |
|---|---|---|---|---|---|---|---|---|---|---|
| data_long core 8 | original | 4h | 2022-01-25 to 2026-10-04 | $5,188 -> $4,959 | 2.04 -> 2.04 | -52.7% -> -55.2% | 476 -> 475 | 100.0 -> 99.6 | leverage_cap 2 | - |
| data_long core 8 | calm | 4h | 2022-01-25 to 2026-10-04 | $1,141 -> $1,155 | 2.13 -> 2.18 | -13.3% -> -13.4% | 592 -> 580 | 43.2 -> 42.1 | below_min_notional 625, below_min_qty 164, leverage_cap 11 | - |
| data_long core 8 | balanced | 4h | 2022-01-25 to 2026-10-04 | $3,066 -> $3,197 | 2.09 -> 2.16 | -33.9% -> -30.6% | 820 -> 809 | 64.5 -> 63.5 | below_min_notional 325, below_min_qty 125, leverage_cap 15 | below_min_notional 8, below_min_qty 2 |
| data_long core 8 | aggressive | 4h | 2022-01-25 to 2026-10-04 | $4,938 -> $5,103 | 1.96 -> 2.01 | -46.8% -> -45.2% | 927 -> 914 | 76.4 -> 74.6 | below_min_notional 203, below_min_qty 101, leverage_cap 8 | below_min_notional 5 |
| data_long core 8 | boost | 4h | 2022-01-25 to 2026-10-04 | $3,943 -> $3,845 | 1.79 -> 1.8 | -45.6% -> -44.6% | 765 -> 752 | 87.2 -> 85.3 | below_min_notional 83, below_min_qty 36, leverage_cap 11 | - |
| data_long core 8 | active | 1h | 2022-09-04 to 2026-10-04 | $1,294 -> $1,239 | 1.08 -> 1.08 | -61.4% -> -61.9% | 4044 -> 3975 | 85.8 -> 83.3 | below_min_notional 419, below_min_qty 343, leverage_cap 35 | below_min_notional 1, leverage_cap 1 |
| data_long core 8 | active_dca | 1h | 2022-09-04 to 2026-10-04 | $210 -> $224 | 0.8 -> 0.81 | -67.6% -> -65.6% | 2034 -> 1989 | 79.5 -> 77.1 | below_min_notional 266, below_min_qty 282, leverage_cap 43 | - |
| data_long core 8 | steady_mix | 4h+1h (2 x $250) | 2022-09-04 to 2026-10-04 | $1,622 -> $1,642 | 1.66 -> 1.69 | -22.7% -> -22.5% | 2257 -> 2235 | 57.2 -> 56.4 | below_min_notional 1188, below_min_qty 515, leverage_cap 24 | below_min_notional 8, below_min_qty 5 |
| data_long core 8 | boost_active | 4h+1h (2 x $250) | 2022-09-04 to 2026-10-04 | $3,207 -> $2,917 | 1.3 -> 1.3 | -44.8% -> -43.3% | 4403 -> 4305 | 74.9 -> 72.4 | below_min_notional 1077, below_min_qty 519, leverage_cap 49 | below_min_notional 10, below_min_qty 5, leverage_cap 3 |
| data/ 4h top 40 | original | 4h | 2024-10-20 to 2026-10-04 | $3,223 -> $3,215 | 2.31 -> 2.31 | -39.2% -> -39.1% | 200 -> 200 | 100.0 -> 100.0 | - | - |
| data/ 4h top 40 | calm | 4h | 2024-10-20 to 2026-10-04 | $1,379 -> $1,379 | 2.32 -> 2.35 | -17.0% -> -17.1% | 691 -> 682 | 39.1 -> 38.1 | below_min_notional 1046, below_min_qty 59, leverage_cap 6 | - |
| data/ 4h top 40 | balanced | 4h | 2024-10-20 to 2026-10-04 | $4,224 -> $4,260 | 2.02 -> 2.05 | -39.5% -> -38.6% | 1002 -> 998 | 89.6 -> 88.0 | below_min_notional 93, below_min_qty 39, leverage_cap 5 | - |
| data/ 4h top 40 | aggressive | 4h | 2024-10-20 to 2026-10-04 | $7,326 -> $7,353 | 1.88 -> 1.88 | -50.9% -> -50.9% | 1017 -> 1011 | 96.3 -> 95.1 | below_min_notional 22, below_min_qty 21, leverage_cap 9 | below_min_qty 2 |
| data/ 4h top 40 | boost | 4h | 2024-10-20 to 2026-10-04 | $8,805 -> $8,949 | 1.59 -> 1.6 | -65.0% -> -65.0% | 867 -> 866 | 95.8 -> 95.8 | below_min_notional 1, below_min_qty 4, leverage_cap 33 | - |

### Skipped entries per slot (BT02 rules)

| Dataset | Preset | TF | Slot | attempts | skipped | executable % |
|---|---|---|---|---|---|---|
| long | original | 4h | A | 273 | 1 | 99.6 |
| long | original | 4h | B | 205 | 1 | 99.5 |
| long | calm | 4h | DCA | 871 | 755 | 13.3 |
| long | calm | 4h | MOM | 224 | 20 | 91.1 |
| long | calm | 4h | ST | 286 | 25 | 91.3 |
| long | balanced | 4h | DCA | 776 | 453 | 41.6 |
| long | balanced | 4h | MOM | 224 | 5 | 97.8 |
| long | balanced | 4h | ST | 275 | 7 | 97.5 |
| long | aggressive | 4h | DCA | 729 | 305 | 58.2 |
| long | aggressive | 4h | MOM | 224 | 3 | 98.7 |
| long | aggressive | 4h | ST | 274 | 4 | 98.5 |
| long | boost | 4h | DCA | 658 | 127 | 80.7 |
| long | boost | 4h | MOM | 224 | 3 | 98.7 |
| long | active | 1h | BRK1H | 2136 | 11 | 99.5 |
| long | active | 1h | DCA1H | 2638 | 786 | 70.2 |
| long | active_dca | 1h | DCA1H | 2580 | 591 | 77.1 |
| long | steady_mix | mix | DCA | 817 | 584 | 28.5 |
| long | steady_mix | mix | DCA1H | 2802 | 1134 | 59.5 |
| long | steady_mix | mix | MOM | 162 | 5 | 96.9 |
| long | steady_mix | mix | ST | 182 | 4 | 97.8 |
| long | boost_active | mix | BRK1H | 2142 | 20 | 99.1 |
| long | boost_active | mix | DCA | 699 | 241 | 65.5 |
| long | boost_active | mix | DCA1H | 2887 | 1379 | 52.2 |
| long | boost_active | mix | MOM | 224 | 5 | 97.8 |
| top40 | original | 4h | A | 113 | 0 | 100.0 |
| top40 | original | 4h | B | 88 | 0 | 100.0 |
| top40 | calm | 4h | DCA | 1351 | 1102 | 18.4 |
| top40 | calm | 4h | MOM | 327 | 4 | 98.8 |
| top40 | calm | 4h | ST | 116 | 5 | 95.7 |
| top40 | balanced | 4h | DCA | 696 | 135 | 80.6 |
| top40 | balanced | 4h | MOM | 327 | 1 | 99.7 |
| top40 | balanced | 4h | ST | 114 | 1 | 99.1 |
| top40 | aggressive | 4h | DCA | 621 | 43 | 93.1 |
| top40 | aggressive | 4h | MOM | 331 | 9 | 97.3 |
| top40 | aggressive | 4h | ST | 113 | 0 | 100.0 |
| top40 | boost | 4h | DCA | 566 | 5 | 99.1 |
| top40 | boost | 4h | MOM | 339 | 33 | 90.3 |

### Skipped entries per symbol (BT02 rules)

| Dataset | Preset | TF | 1000PEPE | AAVE | ADA | APT | ARB | ARK | AVAX | BCH | BNB | BTC | DOGE | DOT | ENA | ENJ | ETC | ETH | FET | FIL | GALA | INJ | LINK | LTC | MANA | MOVR | NEAR | ONDO | ONE | QNT | SAND | SOL | STRK | SUI | SUPER | TAO | UNI | WLD | XLM | XRP | ZEC | ZRO |
|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|
| long | original | 4h |  |  |  |  |  |  | 0/53 |  | 0/69 | 2/69 | 0/36 |  |  |  |  | 0/63 |  |  |  |  | 0/58 |  |  |  |  |  |  |  |  | 0/63 |  |  |  |  |  |  |  | 0/67 |  |  |
| long | calm | 4h |  |  |  |  |  |  | 87/155 |  | 63/185 | 167/195 | 68/122 |  |  |  |  | 121/185 |  |  |  |  | 117/182 |  |  |  |  |  |  |  |  | 95/177 |  |  |  |  |  |  |  | 82/180 |  |  |
| long | balanced | 4h |  |  |  |  |  |  | 40/137 |  | 11/171 | 135/193 | 28/110 |  |  |  |  | 90/177 |  |  |  |  | 90/169 |  |  |  |  |  |  |  |  | 48/162 |  |  |  |  |  |  |  | 23/156 |  |  |
| long | aggressive | 4h |  |  |  |  |  |  | 18/126 |  | 2/168 | 108/186 | 12/104 |  |  |  |  | 59/166 |  |  |  |  | 77/166 |  |  |  |  |  |  |  |  | 27/158 |  |  |  |  |  |  |  | 9/153 |  |  |
| long | boost | 4h |  |  |  |  |  |  | 0/88 |  | 0/123 | 58/132 | 0/79 |  |  |  |  | 25/118 |  |  |  |  | 46/126 |  |  |  |  |  |  |  |  | 0/111 |  |  |  |  |  |  |  | 1/105 |  |  |
| long | active | 1h |  |  |  |  |  |  | 3/475 |  | 0/714 | 390/728 | 2/486 |  |  |  |  | 137/662 |  |  |  |  | 260/604 |  |  |  |  |  |  |  |  | 3/622 |  |  |  |  |  |  |  | 2/483 |  |  |
| long | active_dca | 1h |  |  |  |  |  |  | 0/256 |  | 0/381 | 359/403 | 0/281 |  |  |  |  | 85/349 |  |  |  |  | 147/312 |  |  |  |  |  |  |  |  | 0/323 |  |  |  |  |  |  |  | 0/275 |  |  |
| long | steady_mix | mix |  |  |  |  |  |  | 67/410 |  | 37/543 | 557/598 | 53/390 |  |  |  |  | 410/567 |  |  |  |  | 464/542 |  |  |  |  |  |  |  |  | 73/488 |  |  |  |  |  |  |  | 66/425 |  |  |
| long | boost_active | mix |  |  |  |  |  |  | 54/608 |  | 8/855 | 536/884 | 60/593 |  |  |  |  | 409/830 |  |  |  |  | 457/802 |  |  |  |  |  |  |  |  | 83/768 |  |  |  |  |  |  |  | 38/612 |  |  |
| top40 | original | 4h |  |  |  |  |  |  | 0/29 |  | 0/32 | 0/30 | 0/12 |  |  |  |  | 0/27 |  |  |  |  | 0/23 |  |  |  |  |  |  |  |  | 0/21 |  |  |  |  |  |  |  | 0/27 |  |  |
| top40 | calm | 4h | 24/43 | 33/48 | 21/34 | 27/37 | 24/31 | 23/31 | 23/58 | 16/51 | 10/75 | 59/79 | 24/44 | 21/31 | 26/43 | 24/31 | 15/27 | 48/75 | 29/34 | 22/29 | 30/35 | 27/35 | 41/66 | 27/50 | 32/41 | 25/31 | 34/48 | 29/42 | 23/31 | 18/38 | 32/37 | 19/56 | 25/28 | 33/57 | 19/32 | 32/43 | 39/56 | 37/44 | 21/49 | 13/62 | 50/66 | 36/46 |
| top40 | balanced | 4h | 2/25 | 2/33 | 0/12 | 5/23 | 0/13 | 6/17 | 0/40 | 2/38 | 1/64 | 39/68 | 3/31 | 0/11 | 2/31 | 2/17 | 0/7 | 18/62 | 1/15 | 0/9 | 2/9 | 0/15 | 14/48 | 0/28 | 0/14 | 2/18 | 0/26 | 2/25 | 1/20 | 0/26 | 1/15 | 1/45 | 2/14 | 7/41 | 6/21 | 1/28 | 3/44 | 6/25 | 0/30 | 0/51 | 6/50 | 0/28 |
| top40 | aggressive | 4h | 1/24 | 4/32 | 0/12 | 0/19 | 0/12 | 0/13 | 0/38 | 1/34 | 0/62 | 21/64 | 0/30 | 0/9 | 0/29 | 0/14 | 0/8 | 6/57 | 1/15 | 0/8 | 0/8 | 0/14 | 4/40 | 0/26 | 0/13 | 0/17 | 0/23 | 0/25 | 0/20 | 0/26 | 0/13 | 0/44 | 0/12 | 1/41 | 3/18 | 0/24 | 0/43 | 3/24 | 1/30 | 1/49 | 4/48 | 1/27 |
| top40 | boost | 4h | 2/21 | 2/31 | 2/14 | 1/19 | 0/13 | 0/13 | 2/24 | 2/34 | 2/38 | 4/40 | 0/20 | 0/10 | 2/27 | 0/15 | 0/7 | 1/36 | 2/16 | 0/7 | 0/10 | 2/16 | 1/25 | 0/26 | 1/14 | 0/16 | 0/22 | 0/25 | 1/18 | 1/25 | 0/13 | 0/29 | 0/13 | 1/39 | 2/16 | 1/24 | 2/42 | 0/22 | 1/30 | 1/25 | 0/44 | 2/26 |

Reading it:
- The legacy floor already skipped many entries silently (calm: 780 of 1,372 attempts on core 8); BT02 makes them
  visible and adds BTC's 0.001 minQty (60-120 USDT): at $500 BTC is skipped in 70-93 % of its attempts in the
  DCA-carrying profiles (calm 167/195, balanced 135/193, active_dca 359/403, steady_mix 557/598).
- Attempts are counted per signal candle: a skipped coin whose signal repeats on the next candle is counted again.
- Final equity moves little and in both directions (-$290 boost_active to +$165 aggressive on core 8); the
  executable % is the number to watch: the DCA slots of calm (13 %), steady_mix (28 % / 60 %), balanced (42 %) are
  structurally undersized at $500.

### Preflight at $500 (shipped candles 2026-10-04, placeholder rules -> status `unknown`, estimate in brackets)

| Preset | Status (estimate) | Executable pairs | Undersized pairs (worst 4: coin slot tf -> min capital / min risk at $500) | Capital for every pair |
|---|---|---|---|---|
| original | unknown (ok) | 16/16 (100.0%) | - | $147 |
| calm | unknown (partial) | 46/88 (52.3%) | BTC DCA 4h -> $6,504 / 13.01%, LINK DCA 4h -> $3,544 / 7.09%, ONE DCA 4h -> $2,575 / 5.15%, ETH DCA 4h -> $2,259 / 4.52% +38 more | $6,504 |
| balanced | unknown (partial) | 60/88 (68.2%) | BTC DCA 4h -> $3,252 / 13.01%, LINK DCA 4h -> $1,772 / 7.09%, ONE DCA 4h -> $1,287 / 5.15%, ETH DCA 4h -> $1,130 / 4.52% +24 more | $3,252 |
| aggressive | unknown (partial) | 77/88 (87.5%) | BTC DCA 4h -> $2,168 / 13.01%, LINK DCA 4h -> $1,181 / 7.09%, ONE DCA 4h -> $858 / 5.15%, ETH DCA 4h -> $753 / 4.52% +7 more | $2,168 |
| active | unknown (partial) | 14/16 (87.5%) | BTC DCA1H 1h -> $1,068 / 4.27%, LINK DCA1H 1h -> $620 / 2.48% | $1,068 |
| boost_active | unknown (partial) | 87/96 (90.6%) | BTC DCA1H 1h -> $2,137 / 8.55%, BTC DCA 4h -> $1,734 / 17.34%, LINK DCA1H 1h -> $1,239 / 4.96%, LINK DCA 4h -> $945 / 9.45% +5 more | $2,137 |
| steady_mix | unknown (partial) | 52/96 (54.2%) | BTC DCA 4h -> $6,504 / 26.02%, LINK DCA 4h -> $3,544 / 14.17%, ONE DCA 4h -> $2,575 / 10.3%, ETH DCA 4h -> $2,259 / 9.04% +40 more | $6,504 |
| active_dca | unknown (partial) | 7/8 (87.5%) | BTC DCA1H 1h -> $534 / 2.14% | $534 |
| boost | unknown (partial) | 79/80 (98.8%) | BTC DCA 4h -> $867 / 8.67% | $867 |

Codex's reference was ~3,700 USDT for an unchanged preset at current testnet filters; with the placeholder rules (BTC
min 50 / 0.001 step) the worst pair (BTC in calm / steady_mix's 4h DCA slot) needs ~6,500 USDT, balanced ~3,250,
aggressive ~2,170, boost ~870. The difference is the rules and the ATR basis; a real exchangeInfo snapshot settles it.

## For Codex to decide

1. **Placeholder snapshot**: ship as is (labelled unverified, all preflights `unknown` until the app is connected or a
   real snapshot is built), or have Moe run `python exchange_rules.py fetch --env testnet` on his PC and commit the
   verified file before merge.
2. **Backtest default**: `backtest.run` keeps the legacy floor when no rules are passed (research scripts, lab, tests
   unchanged); the app's backtests pass the rules. Should `lab.py` jobs (optimizer / walk-forward) also pass them?
3. **Partial closes** (tp1 / ladder / runner part) are not floored to the step in the backtest yet (the engine does, and
   closes the whole lot rather than leave dust). Follow-up ticket or in BT02?
4. **Preset numbers / labels**: the profile cards still quote the old research numbers; replace them with the BT01+BT02
   numbers after a verified snapshot, or add a "needs about X USDT" line from the preflight now?
5. **Attempt counting**: per signal candle (repeated signals counted again) vs per distinct signal episode.
6. **Preflight ATR basis**: median of the last 180 candles (stable) vs the latest ATR (what the engine sizes with).

## Round 2: fixes for `BT02_review_gpt.md` (3 × P1, 2 × P2), 07 Oct 2026 (Cairo)

Implementation fixes reviewed head `ce5c40d` / review commit `31fa7bc`. Sections above describe round 1. Where they
conflict with this section (placeholder data, preflight math, ATR basis, partial closes), **this section supersedes them**.

### P1: untrusted rules can no longer change the canonical backtest
- `app.run_backtest_job`: rules are passed to `BT.run` **only when `snapshot_state == 'ok'`** for the selected environment
  (`xapply`). Every other state (unverified / stale / invalid / wrong_environment / unavailable) runs the legacy floor.
  The result says so: `feasibility.rules_applied=false`, `promotable=false`, plus `rules_state` and `rules_detail`.
  The panel shows "Binance minimum-order rules were **not applied** (...). Not a verified number."
  An applied result records `rules_version` / `rules_fetched_at`.
- The placeholder is **gone**. `data/exchange_rules_testnet.json` is now a direct public fetch (07 Oct 2026 09:52 UTC,
  12:52 Cairo; 570 symbols, version 2, `provenance: direct_fetch`, `raw_sha256 65c6bd689c30…`,
  `source_url https://testnet.binancefuture.com/fapi/v1/exchangeInfo`). Its values match Codex's independent capture:
  BTC step / minQty 0.0001, min notional 50; ETH 20; LINK 5; DOGE and 1000PEPE step 1. Testnet's own `serverTime` in the
  answer read 03:42 UTC; it is recorded as `server_time` but not used. `fetched_at` is the fetching PC's UTC clock.
  The file expires after 30 days (state `stale`, so backtests fall back to legacy and say so). Refresh with
  `python exchange_rules.py fetch --env testnet`.
- Lab jobs (decision 2) never apply exchange rules. Their results now carry
  `execution: {exchange_rules: 'not applied (legacy 5 USDT floor)', execution_realistic: false, promotable: false}`, and
  the Lab panel shows an "Exploratory ... not execution-realistic" note.
- Tests: `test_app_backtest_applies_only_verified_fresh_rules_of_this_environment` runs `run_backtest_job` 7 times.
  Each non-ok state gives curve, stats and by-symbol P&L **identical** to `exchange_rules='off'`. The `ok` state applies
  the rules and differs. `test_shipped_testnet_snapshot_is_a_verified_direct_fetch_and_mainnet_is_absent` pins the values
  above, plus `ok` → `stale` after 31 days and `wrong_environment` for mainnet.

### P1: preflight covers every planned order
- `feasibility.slot_order_legs` lists every order the engine would plan for one coin/slot:
  - the entry;
  - each DCA safety order `q0 × scale^k` at DCA level k (`q0` = the entry floored to the step, the engine's lot `q0`);
  - each of the `n` pyramid adds `q0 × frac`, k × step_r × R from the entry.

  Each add is checked at its own price, on the side where its notional is smallest, so the check is never optimistic
  for either direction.
- `preflight` returns:
  - `entry_executable_pct` and `plan_executable_pct` (`executable_pct` is now the plan %);
  - `status` 'ok' **only when every order of every pair passes** ('partial' when any entry or later order fails);
  - `add_undersized` per failing order (leg, notional, code);
  - `min_capital_all` over every order, with `min_capital_binding` (symbol / slot / leg). It is a linear estimate per
    leg on unfloored sizes, confirmed with the real check.
- Panel: the green "Tradable at your capital" tag comes only from status 'ok'. The badge shows
  "x% … can place every planned order (y% can open)" and a "Later orders too small: …" line.
- Codex's repro (SOL, $500, 0.03%, pyramid 0.5): it was `ok` / 100% / min 500. It is now `partial`, entry 100%, plan 0%,
  `min_capital_all ≈ 1,000` "set by pyramid add 1".
- Tests: `test_preflight_flags_undersized_pyramid_add` (inverted: it asserted the defect), plus:
  - `test_preflight_dca_safety_order_sets_the_minimum_capital` (binding = safety order 3);
  - `test_panel_never_shows_tradable_when_a_later_order_fails`;
  - pyramid n=2 → two legs.

  At `min_capital_all × 1.001` every leg passes; at ×0.99 one fails.

### P1: backtest partial exits match the engine
- `backtest.close`:
  - a **partial** (frac < 1) is floored to the step, as in `engine._market_close`;
  - a partial that floors to 0 sends nothing;
  - the remainder is floored to the step, as in `_apply_close`.

  A full close (frac = 1) and the legacy rule (step None) are bit-identical to before.
- Ladder: the engine's no-dust rule is now one shared predicate, `feasibility.leaves_dust(rest, px, rule)`, used by
  both `engine.py` (ladder) and `backtest.py` (ladder, real rules only; legacy keeps no dust rule).
  TP1 / basket runner part get step flooring and no dust rule, exactly like the engine.
- Binance reduce-only exemption: **not modelled and not assumed**. This is an engine-parity rule. Whether Binance would
  accept a reduce-only close of a sub-minimum remainder is a separate question, to be checked on testnet if it matters.
- Tests (long and short):
  - TP1 at a step boundary: step 1 vs 0.5. The P&L differs by exactly 0.5 × (stop − tp1) after slip and fee:
    −2·(1−s)(1−f) long, −2·(1+s)(1+f) short.
  - TP1 that floors to 0: identical to never reaching TP1.
  - Ladder remainder below the minimum: whole lot closed at the ladder candle.
  - The engine uses the shared predicate.
  - **Engine vs backtest through `replay.run_replay`** with step-1 rules (`test_partial_exits_match_the_engine_through_replay`;
    TP1 in the fast tier, ladder marked slow): every trade matches, with return gap ≤ 0.25%. Measured on the pre-fix
    backtest: TP1 gap 1.07%, and the ladder matched only 1 of 2 trades (gap 1.51%). After the fix: 0.05% and 0.06%,
    all matched.
- Observation (pre-existing, not caused by this change, identical on the old code): with step 1.0 rules some replay
  scenarios (`squeeze_tp`, seed 23) log `exit … failed: (empty) - stop stays in place` with engine/simulator quantity
  mismatches. This looks like a `StopIteration` in the simulator's `reduce()`. It is filed as a separate task, not
  widened into BT02.

### P2: a file import cannot self-certify
- `exchange_rules.build_file` is **unverified by default** (`provenance: file_import`). The capture time is
  `--fetched-at`, else the file's own `serverTime`, else none (state `stale`). It is never "now".
- **No trust override** (changed after the Codex comparison note, taking Cowork's stricter gate). `build --trust` is
  refused with exit 2 and a pointer to `fetch`. `build_file` has no trust parameter.
- `snapshot_state` returns `ok` only for trusted provenance:
  - `direct_fetch` whose `source_url` is exactly that environment's exchangeInfo URL (`feasibility.TRUSTED_SOURCES`);
  - `engine`, the live connection, built in memory by `app.exchange_rules_now`.

  Anything else is `unverified`, whatever `verified` says, with a detail telling you to run `fetch`. A file on disk that
  claims `engine` is demoted by `exchange_rules.load`.
- Only `fetch` produces `direct_fetch`. Every snapshot records `raw_sha256` (the exact bytes), `source_url` and
  `server_time`.
- Tests:
  - `test_file_import_never_self_certifies`: a mainnet-URL file imported as testnet with a fresh `--fetched-at` stays
    unverified; no time means no `fetched_at`; `--trust` is refused.
  - `test_only_a_direct_fetch_of_this_environment_or_the_live_engine_is_trusted`: wrong/foreign/empty `source_url`,
    `file_import`, missing provenance and an `engine` claim on disk are all `unverified`.
  - `test_app_live_engine_rules_are_trusted_and_labelled` and `test_direct_fetch_is_verified_with_provenance`.

### P2: current-capital preflight uses the latest ATR
- `preflight_market` returns `px` and `atr` of the **latest closed candle** (the engine's signal `atr`), plus
  `atr_median` (180-candle median × last close), `t` and `basis`.
- `App.preflight` headline = latest ATR. Each preset also gets a `planning` block (median ATR, labelled). The response
  carries `market_basis`, `market_asof` and `rules_fetched_at`, all shown in the badge tooltip.
- Test: `test_preflight_market_uses_the_latest_closed_candle_atr_like_the_engine`. Latest ATR is 10× the median: the
  headline is `infeasible` while the median-based planning check says `ok`.

### Preset feasibility on the VERIFIED testnet rules (replaces the placeholder table above)
Shipped candle files, last row dropped and forming candles cut, latest closed candle 2026-10-03 20:00 to 2026-10-04
02:00 UTC. Max leverage 10, every planned order, adds priced on each slot's own trading side.

- "entries" = the first order can be placed; "full plan" = the entry **and** every DCA / pyramid add can be placed.
- **Status in the app is `unknown` for all of these**: the shipped prices are over 2 candles old, so it is never green on
  them. The figures are the kept estimate. The live app uses the engine's fresh candles for pairs it polls.

| Profile | $100: entries / full plan | $200: entries / full plan | $500: entries / full plan | Capital for every planned order (binding order) |
|---|---|---|---|---|
| original | 100% / 100% | 100% / 100% | 100% / 100% | $75 (entry, BTC A 4h) |
| calm | 17.0% / 17.0% | 39.8% / 39.8% | 52.3% / 52.3% | $16,504 (entry, QNT DCA 4h) |
| balanced | 39.8% / 18.2% | 52.3% / 43.2% | 64.8% / 64.8% | $8,252 (entry, QNT DCA 4h) |
| aggressive | 47.7% / 34.1% | 54.5% / 50.0% | 78.4% / 78.4% | $5,502 (entry, QNT DCA 4h) |
| active | 81.2% / 81.2% | 87.5% / 87.5% | 100% / 100% | $283 (entry, BTC DCA1H 1h) |
| boost_active | 46.9% / 40.6% | 58.3% / 57.3% | 84.4% / 83.3% | $4,401 (entry, QNT DCA 4h) |
| steady_mix | 20.8% / 5.2% | 42.7% / 22.9% | 56.2% / 53.1% | $16,504 (entry, QNT DCA 4h) |
| active_dca | 75.0% / 75.0% | 100% / 100% | 100% / 100% | $142 (entry, BTC DCA1H 1h) |
| boost | 53.8% / 52.5% | 76.2% / 76.2% | 97.5% / 97.5% | $2,201 (entry, QNT DCA 4h) |

These are exchange-minimum feasibility estimates only, not profitability. Per decision 4, preset cards stay unchanged
until BT01+BT02 reruns on an accepted verified snapshot. For the $100–$200 follower range, only `original` is fully
tradable at both amounts. `active_dca` is fully tradable from $200 and `active` from $500. The DCA-heavy profiles cannot
build most of their planned positions.

### Round 2b: comparison with Cowork's parallel version (`cowork/bt02-r2@aa83084`) and what was merged
The owner chose compare-then-pick. Codex's comparison note picked this branch as the base, with Cowork's stricter trust
gate (above). An independent read-only comparison and cross-tests (my partial-exit and replay-parity tests also pass on
Cowork's code) found these defects in round 2a, now fixed here:

- **Pyramid / DCA add pricing (A1).** Adds were always priced on the short side (below the entry). The engine places
  long pyramid adds **above** the entry (`next_add = fill + step_r × R`), and every pyramid strategy is long-only.
  `slot_order_legs` now prices each add on the side(s) the slot trades (`sides`, from the slot or its strategy;
  `both` = the smaller notional), and the app passes each slot's `sides`.
  - Effect, volatile coin (SOL $150, ATR 12): long-only plan needs **$180** (binding "pyramid add 1"); the old mirrored
    pricing said **$540**.
  - Test: `test_adds_are_priced_on_the_side_the_slot_trades`.
- **Add failure labels (A2).** An add failing only because of step rounding was labelled `leverage_cap`. Adds now always
  report `below_min_qty` / `below_min_notional`.
- **Rounding (A3).** `min_capital` is rounded **up** to $1 and `min_risk_pct` up to 0.01, so a quoted minimum is never
  still too small.
- **Unchanged field meaning (A7).** `ok` keeps its round-1 meaning (pairs whose first order passes, row and top level).
  The full plan is the new `plan_ok`; status and `plan_executable_pct` use it.
- **Forming candle (A4, from Cowork).** `preflight_market` drops the shipped CSV's last row (its completion is unknown
  when written) and cuts any candle not closed by `now`, the engine's rule. The engine cache is already closed-only.
- **Old prices never green (Cowork D2).** A pair whose latest closed candle is more than 2 candles late is marked
  `stale`. Preflight status becomes `unknown`, keeping the estimate and saying "use old prices (latest candle …)".
- **Partial rules coverage (Cowork D1).** A backtest with rules applied but some symbols missing from the snapshot
  (legacy floor for them) now has `execution_realistic=false` and `promotable=false`, and the panel lists those coins.
  This matters because testnet lacks some top-40 coins.
- **Rules-off note (A6).** The panel now says "not applied (switched off for this run)" when the request turned rules off.

Tests: `test_bt02_exchange_filters.py` **48 passed**, including the new tests named above,
`test_preflight_never_green_on_old_prices_and_cuts_the_forming_candle` and
`test_backtest_with_symbols_missing_from_trusted_rules_is_not_promotable`.

Not taken from Cowork:
- deleting the shipped snapshot (Codex: keep the direct fetch);
- its rename of the engine close helpers (behaviour is identical; the shared `leaves_dust` covers the ladder rule).

Cowork's open items are left for later tickets:
- D3: hedge-mode partial close without `reduceOnly`, retried each pass if Binance refuses. Needs a testnet check, i.e.
  Cowork runtime work.
- D5: preflight ignores the leverage-cap add block.
- D6: `exchange_rules:'off'` is not reachable through `/api/backtest`.
- D7: gitignore and doc nits.

### Not done here (deliberately)
- Distinct-signal-episode counting (decision 5, "if useful"): not added. Attempts stay per signal candle.
- COPY100 / COPY200 and the DCA-default PRs remain untouched until BT02 is accepted.

### Round 2c: Codex round-2 P1 — the cumulative slot leverage gate
Preflight now walks the plan as the engine executes it:
- **The gate.** Every add passes the engine's cumulative slot-leverage gate, i.e. `Engine._add_block`'s own formula:
  notional already held in the slot + this add's `q × px` must not exceed `MAX_LEVERAGE × equity × share`. The check
  runs *before* the exchange minimum, as the engine calls `_add_gate` before `_add_qty`.
- **Failure codes.** An add refused by the gate is `leverage_plan`, listed in the new `add_over_leverage` (kept apart
  from `add_undersized`, i.e. exchange minimums).
- **Sequence stop.** The engine stops a sequence at the first skipped add (DCA `break`; pyramid add k+1 needs add k).
  Every later add of that sequence is therefore `not_reached`.
- **No misleading capital figure.** A leverage-plan failure is structural: the cap and every order size scale with
  capital. So `min_capital` / `min_capital_all` are `None` for that pair, with no "about $X" recommendation. The warning
  says it cannot place every planned order at **any** capital: lower the risk %, the DCA/pyramid size, or raise max
  leverage.
- **Panel.** The badge adds "Over the slot leverage cap (more capital does not help): …".
- **Assumption, stated in the code.** This coin's position is the slot's only open lot. With several lots open the slot
  cap is shared, so fewer adds fit.

Codex repro ($100, 1x, entry = the whole cap, 1-SOL DCA at $99): `partial`, entry 100%, plan 0%,
`safety order 1: … 199.00 > 100.00 USDT`, `min_capital_all = None`. It is still refused at $1,000 and $100,000.

Tests (`test_bt02_exchange_filters.py`, **52 passed**):
- `test_dca_over_the_cumulative_slot_cap_is_never_green_and_no_capital_is_recommended`;
- `test_dca_within_the_cumulative_slot_cap_passes`;
- `test_multi_pyramid_stops_at_the_first_add_over_the_cap_and_later_adds_are_not_reached`, which passes at 10x;
- `test_check_legs_uses_the_engine_add_gate_formula`: `>` not `>=`, the cap checked before the minimum, and the engine
  source pinned.
