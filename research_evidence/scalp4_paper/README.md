# SCALP-4 PAPER closed trades — sanitized packet (snapshot 2026-10-17 05:27 Cairo / 02:27 UTC)

The 23 closed SC-* trades in PAPER `history.json`, with MFE and giveback from `trade_audit.jsonl` (`peak_r`, `giveback_r`; 23 of 23 matched). No order or account identifiers, quantities, prices or dollar amounts.

- `net_r`: realised R after costs, from the history record `r`.
- `mfe_r`: peak open net P&L / risk, from the audit `peak_r` and floored at 0. This is a mark-based net figure, so `net_r` can exceed it by rounding. One row (QBANK SHORT take_profit) shows 0.83 against 0.825.
- `giveback_r`: the audit value, peak − final computed on the UNFLOORED peak. Where `mfe_r` shows 0.0 (the peak was slightly negative), `giveback_r` is therefore smaller than `0 − net_r`, e.g. net −1.21, giveback 1.148 (Cowork 6228439512).
- `regime`: the research runner's label (`run_breadth_candidate.regime_at`), BTCUSDT close vs 4h EMA200 on the last 4h candle closed at or before the entry. `regime_src_close` is that candle's close time, so it is always ≤ `entry_time`.
  - BTC 4h series = the app candle cache (`candles/BTCUSDT_4h.csv`, which ends 2026-10-15T00:00Z) plus public Binance USDⓈ-M `/fapi/v1/klines` 4h (600 bars, sha256 `6a087007…c627`). Duplicates go to the fresh klines; a no-gap assert guards the series.
  - Note: the runtime slot gate (`strategies.regime`) uses a different definition (daily vs 200-day EMA).
- `rows.py` is the exact extractor: `py -3.14 -I rows.py <ZackBot data dir> out.csv btc4h.csv`.
