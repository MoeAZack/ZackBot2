# exp_structure arm A, BTCUSDT only (development window, research only, non-promotable, no edge claim)

This packet comes from the frozen runner `tools/research/run_breadth_candidate.py`, unchanged: `--mgmt defaults`, CORE-10 account settings, `--symbols BTCUSDT`. It was exported by `tools/research/structure_btc_packet.py` at commit `1fb5a71`. Runner packet sha256 (prefix): long `9b88d403ba4c…`, short `bf5b5725a153…`; the full values are in `decomposition.json`.

- **Window:** 2024-10-14T00:00Z to 2026-09-28T00:00Z, end-exclusive. Indicator warm-up starts 2024-10-11T17:00Z. Nothing on or after the 2026-09-28 sealed cutoff is used.
- **Coverage (read this before the BTC rows):** the BTC packet is account-sized, and the account busts and then stops trading. LONG trades actually span 2024-10-14 to 2025-06-26, and SHORT trades 2024-10-14 to 2025-08-16, so about 8-10 months, partly after the bust; this is **not** a two-year BTC result. The full-window evidence is the CORE-10 fixed-R view in `decomposition.json` (about 14.4k trades per side, no bust). It shows the same shape but has no per-trade CSV here.
- **Account:** $500 start, 1% risk per trade, max leverage 10, 8% daily halt.
- **Strategy management:** stop 1.0 ATR, target 2R, 24-candle time cap, plus the strategy's own exit signal.
- **Both sides bust:** long at 2025-02-16T10:00Z (trade #437 of 718), short at 2025-05-18T03:30Z (#526 of 719). After the bust the runner keeps trading the dead account, so per-trade R stays meaningful but $ amounts shrink.

## Files

| file | committed | contents |
|---|---|---|
| `README.md` | yes | this file |
| `trades_long.csv` | yes | one row per trade; LONG only (`side` = 1 in all 718 rows) |
| `trades_short.csv` | yes | one row per trade; SHORT only (`side` = −1 in all 719 rows) |
| `decomposition.json` | yes | cost decomposition in R (table below) |
| `btc_15m_slice.csv` | **no**, raw market data kept local | the exact 15m BTCUSDT rows the book used (2024-10-11T17:00Z to 2026-09-27T23:45Z; columns t (open, ms), utc, o, h, l, c, v). sha256 `79877777612c0070fcc8cafe2da9b65cb84e30320d55fd644d4ba20cc81cd18e`, 5,339,404 bytes |
| runner packet JSONs | **no** | regenerate them with the commands below |

These files are LF, enforced by `.gitattributes`.

## Timestamps

`t_in` and `t_out` are 15m candle open times (UTC).

- **Entry:** the signal fires on the close of candle `t_in − 15m`, and the trade fills at the open of candle `t_in`.
- **Exit:** the trade exits inside candle `t_out` (stop or target), or at its close (signal or time exit).

## Cost model (backtest.py, frozen)

| parameter | value |
|---|---|
| `FEE` | 0.0005 (taker, charged on every fill) |
| `SLIP` | 0.0002 (2 bps, adverse, on every fill) |
| funding per 15m candle | 5e-5 / 16 = 3.125e-6 × notional at the candle close (always a cost) |

`side` is +1 for a long and −1 for a short.

```
entry_fill = entry_open × (1 + side × SLIP)
stop_dist  = 1.0 × ATR(previous closed candle)
stop       = entry_fill − side × stop_dist
qty        = risk_usd / stop_dist          (capped by the leverage limit: notional ≤ 10 × equity)
exit_level = stop | entry_fill + side × 2 × stop_dist | candle close (signal / time) | open (gap through stop)
exit_fill  = exit_level × (1 − side × SLIP)
gross_before_slip = side × (exit_level − entry_open) × qty
slip_usd   = side × (entry_fill − entry_open) × qty + side × (exit_level − exit_fill) × qty
gross      = gross_before_slip − slip_usd = side × (exit_fill − entry_fill) × qty
fee_usd    = FEE × qty × (entry_fill + exit_fill)
funding    = qty × 3.125e-6 × Σ close[c]  over the candles c = t_in … t_out (inclusive)
net        = gross − fee_usd − funding
R          = net / risk_usd
```

- `funding` is the runner's 6-decimal $ value; `funding_recomputed` is the formula above.
- `net` is the runner's value; `net_recomputed` is the formula above. The two agree within 5e-7 $, which is the packet's rounding.

## Cost decomposition (R)

Every $ term is divided by that trade's intended `risk_usd`, using the exact recomputed terms. The identity `gross_before_slip − slip − fee − funding = net` is asserted per trade (max gap 9e-16 R). `net` matches the runner's R per trade within 5e-7.

The `core10_fixed_r` rows are the CORE-10 fixed-R, no-bust view of the same strategy: 9 symbols, runner `--start 1e9 --risk 1e-6`.

| view / side | trades | gross before slip R | slippage R | fee R | funding R | net R | median stop (bps of entry) |
|---|---|---|---|---|---|---|---|
| BTCUSDT long | 718 | −7.18 | 106.64 | 266.60 | 4.00 | −384.42 | 28.9 |
| BTCUSDT short | 719 | −5.16 | 107.14 | 267.86 | 3.41 | −383.57 | 30.3 |
| CORE-10 fixed-R long | 14366 | −159.30 | 1274.02 | 3185.06 | 45.32 | −4663.70 | 55.4 |
| CORE-10 fixed-R short | 14456 | −188.43 | 1311.30 | 3278.25 | 47.13 | −4825.10 | 54.3 |

Per-trade means (R):

| view / side | gross before slip | slippage | fee | funding | net |
|---|---|---|---|---|---|
| BTCUSDT long | −0.0100 | 0.1485 | 0.3713 | 0.0056 | −0.5354 |
| BTCUSDT short | −0.0072 | 0.1490 | 0.3725 | 0.0047 | −0.5335 |
| CORE-10 fixed-R long | −0.0111 | 0.0887 | 0.2217 | 0.0032 | −0.3246 |
| CORE-10 fixed-R short | −0.0130 | 0.0907 | 0.2268 | 0.0033 | −0.3338 |

At ideal fills with no costs, the price move alone is already slightly negative. Costs then add about 0.31R per trade on the CORE-10 fixed-R view and about 0.53R on BTC. Stops of 30–55 bps make 2 × 2 bps of slippage plus 2 × 5 bps of fees a large share of 1R.

## Worked example: the first long trade (2024-10-14)

The signal came on the 07:30 candle close. The entry is at the 07:45 open; the target was hit inside the 08:00 candle.

| step | calculation | result |
|---|---|---|
| entry fill | 64298.5 × 1.0002 | 64311.3597 |
| stop distance (1 ATR) | | 229.2711 |
| stop | 64311.3597 − 229.2711 | 64082.0886 |
| risk | 1% of $500 | $5.00 |
| qty | 5.00 / 229.2711 | 0.0218082 BTC |
| target level | 64311.3597 + 2 × 229.2711 | 64769.9018 |
| exit fill | 64769.9018 × 0.9998 | 64756.9478 |
| gross before slippage | (64769.9018 − 64298.5) × 0.0218082 | $10.28045 |
| slippage | 0.28045 + 0.28250 | $0.56295 |
| gross | 10.28045 − 0.56295 | $9.71750 |
| fees | 0.0005 × 0.0218082 × (64311.3597 + 64756.9478) = 0.70126 + 0.70612 | $1.40738 |
| funding | 0.0218082 × 3.125e-6 × (64599.6 + 64470.0) | $0.00880 |
| **net** | 9.71750 − 1.40738 − 0.00880 | **$8.30132** |
| R | 8.30132 / 5.00 | 1.6603 |

## Regenerate (local caches; outputs are research only)

```
py -3.14 tools/research/structure_btc_packet.py --cache <candles_15m_research> --regime-cache <candles_4h_research> \
   --window 2024-10-14T00:00:00Z/2026-09-28T00:00:00Z --side long|short --dir <out> [--slice]
py -3.14 tools/research/structure_btc_packet.py ... --dir <out> --decompose <out>/decomposition.json
```
