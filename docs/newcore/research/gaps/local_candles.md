# Local candle data: gap report (`zb-data-gaps/1`)

Roots: `data`, `data1h`, `data_long`. Manifest: `DATA_MANIFEST.json`. 64 files, 587,976 rows. **40 errors, 8 warnings**, 8 missing bars.

Times are UTC and Africa/Cairo (IANA zone, DST-aware). Not checked: G5 venue maintenance windows (needs venue_sessions); F1 funding cadence (no funding data in these roots).

| class | findings |
|---|---|
| G1 | 8 |
| S1 | 8 |
| X1 | 8 |
| X2 | 24 |

## Files

| file | rows | first (UTC) | last (UTC) | findings |
|---|---|---|---|---|
| `data/1000PEPEUSDT_4h.csv` | 4,500 | 2024-09-14 04:00:00 | 2026-10-04 00:00:00 | clean |
| `data/AAVEUSDT_4h.csv` | 4,500 | 2024-09-14 04:00:00 | 2026-10-04 00:00:00 | clean |
| `data/ADAUSDT_4h.csv` | 4,500 | 2024-09-14 04:00:00 | 2026-10-04 00:00:00 | clean |
| `data/APTUSDT_4h.csv` | 4,500 | 2024-09-14 04:00:00 | 2026-10-04 00:00:00 | clean |
| `data/ARBUSDT_4h.csv` | 4,500 | 2024-09-14 04:00:00 | 2026-10-04 00:00:00 | clean |
| `data/ARKUSDT_4h.csv` | 4,500 | 2024-09-14 04:00:00 | 2026-10-04 00:00:00 | clean |
| `data/AVAXUSDT_4h.csv` | 4,500 | 2024-09-14 04:00:00 | 2026-10-04 00:00:00 | X1 x1, X2 x2 |
| `data/BCHUSDT_4h.csv` | 4,500 | 2024-09-14 04:00:00 | 2026-10-04 00:00:00 | clean |
| `data/BNBUSDT_4h.csv` | 4,500 | 2024-09-14 04:00:00 | 2026-10-04 00:00:00 | X1 x1, X2 x2 |
| `data/BTCUSDT_4h.csv` | 4,500 | 2024-09-14 04:00:00 | 2026-10-04 00:00:00 | X1 x1, X2 x2 |
| `data/DOGEUSDT_4h.csv` | 4,500 | 2024-09-14 04:00:00 | 2026-10-04 00:00:00 | X1 x1, X2 x2 |
| `data/DOTUSDT_4h.csv` | 4,500 | 2024-09-14 04:00:00 | 2026-10-04 00:00:00 | clean |
| `data/ENAUSDT_4h.csv` | 4,500 | 2024-09-14 04:00:00 | 2026-10-04 00:00:00 | clean |
| `data/ENJUSDT_4h.csv` | 4,500 | 2024-09-14 04:00:00 | 2026-10-04 00:00:00 | clean |
| `data/ETCUSDT_4h.csv` | 4,500 | 2024-09-14 04:00:00 | 2026-10-04 00:00:00 | clean |
| `data/ETHUSDT_4h.csv` | 4,500 | 2024-09-14 04:00:00 | 2026-10-04 00:00:00 | X1 x1, X2 x2 |
| `data/FETUSDT_4h.csv` | 4,500 | 2024-09-14 04:00:00 | 2026-10-04 00:00:00 | clean |
| `data/FILUSDT_4h.csv` | 4,500 | 2024-09-14 04:00:00 | 2026-10-04 00:00:00 | clean |
| `data/GALAUSDT_4h.csv` | 4,500 | 2024-09-14 04:00:00 | 2026-10-04 00:00:00 | clean |
| `data/INJUSDT_4h.csv` | 4,500 | 2024-09-14 04:00:00 | 2026-10-04 00:00:00 | clean |
| `data/LINKUSDT_4h.csv` | 4,500 | 2024-09-14 04:00:00 | 2026-10-04 00:00:00 | X1 x1, X2 x2 |
| `data/LTCUSDT_4h.csv` | 4,500 | 2024-09-14 04:00:00 | 2026-10-04 00:00:00 | clean |
| `data/MANAUSDT_4h.csv` | 4,500 | 2024-09-14 04:00:00 | 2026-10-04 00:00:00 | clean |
| `data/MOVRUSDT_4h.csv` | 4,500 | 2024-09-14 04:00:00 | 2026-10-04 00:00:00 | clean |
| `data/NEARUSDT_4h.csv` | 4,500 | 2024-09-14 04:00:00 | 2026-10-04 00:00:00 | clean |
| `data/ONDOUSDT_4h.csv` | 4,500 | 2024-09-14 04:00:00 | 2026-10-04 00:00:00 | clean |
| `data/ONEUSDT_4h.csv` | 4,500 | 2024-09-14 04:00:00 | 2026-10-04 00:00:00 | clean |
| `data/QNTUSDT_4h.csv` | 4,500 | 2024-09-14 04:00:00 | 2026-10-04 00:00:00 | clean |
| `data/SANDUSDT_4h.csv` | 4,500 | 2024-09-14 04:00:00 | 2026-10-04 00:00:00 | clean |
| `data/SOLUSDT_4h.csv` | 4,500 | 2024-09-14 04:00:00 | 2026-10-04 00:00:00 | X1 x1, X2 x2 |
| `data/STRKUSDT_4h.csv` | 4,500 | 2024-09-14 04:00:00 | 2026-10-04 00:00:00 | clean |
| `data/SUIUSDT_4h.csv` | 4,500 | 2024-09-14 04:00:00 | 2026-10-04 00:00:00 | clean |
| `data/SUPERUSDT_4h.csv` | 4,500 | 2024-09-14 04:00:00 | 2026-10-04 00:00:00 | clean |
| `data/TAOUSDT_4h.csv` | 4,500 | 2024-09-14 04:00:00 | 2026-10-04 00:00:00 | clean |
| `data/UNIUSDT_4h.csv` | 4,500 | 2024-09-14 04:00:00 | 2026-10-04 00:00:00 | clean |
| `data/WLDUSDT_4h.csv` | 4,500 | 2024-09-14 04:00:00 | 2026-10-04 00:00:00 | clean |
| `data/XLMUSDT_4h.csv` | 4,500 | 2024-09-14 04:00:00 | 2026-10-04 00:00:00 | clean |
| `data/XRPUSDT_4h.csv` | 4,500 | 2024-09-14 04:00:00 | 2026-10-04 00:00:00 | X1 x1, X2 x2 |
| `data/ZECUSDT_4h.csv` | 4,500 | 2024-09-14 04:00:00 | 2026-10-04 00:00:00 | clean |
| `data/ZROUSDT_4h.csv` | 4,500 | 2024-09-14 04:00:00 | 2026-10-04 00:00:00 | clean |
| `data1h/AVAXUSDT_1h.csv` | 4,499 | 2026-03-30 16:00:00 | 2026-10-04 03:00:00 | G1 x1, S1 x1 |
| `data1h/BNBUSDT_1h.csv` | 4,499 | 2026-03-30 16:00:00 | 2026-10-04 03:00:00 | G1 x1, S1 x1 |
| `data1h/BTCUSDT_1h.csv` | 4,499 | 2026-03-30 16:00:00 | 2026-10-04 03:00:00 | G1 x1, S1 x1 |
| `data1h/DOGEUSDT_1h.csv` | 4,499 | 2026-03-30 16:00:00 | 2026-10-04 03:00:00 | G1 x1, S1 x1 |
| `data1h/ETHUSDT_1h.csv` | 4,499 | 2026-03-30 16:00:00 | 2026-10-04 03:00:00 | G1 x1, S1 x1 |
| `data1h/LINKUSDT_1h.csv` | 4,499 | 2026-03-30 16:00:00 | 2026-10-04 03:00:00 | G1 x1, S1 x1 |
| `data1h/SOLUSDT_1h.csv` | 4,499 | 2026-03-30 16:00:00 | 2026-10-04 03:00:00 | G1 x1, S1 x1 |
| `data1h/XRPUSDT_1h.csv` | 4,499 | 2026-03-30 16:00:00 | 2026-10-04 03:00:00 | G1 x1, S1 x1 |
| `data_long/1h/AVAXUSDT_1h.csv` | 35,999 | 2022-08-26 06:00:00 | 2026-10-04 04:00:00 | clean |
| `data_long/1h/BNBUSDT_1h.csv` | 35,999 | 2022-08-26 06:00:00 | 2026-10-04 04:00:00 | clean |
| `data_long/1h/BTCUSDT_1h.csv` | 35,999 | 2022-08-26 06:00:00 | 2026-10-04 04:00:00 | clean |
| `data_long/1h/DOGEUSDT_1h.csv` | 35,999 | 2022-08-26 06:00:00 | 2026-10-04 04:00:00 | clean |
| `data_long/1h/ETHUSDT_1h.csv` | 35,999 | 2022-08-26 06:00:00 | 2026-10-04 04:00:00 | clean |
| `data_long/1h/LINKUSDT_1h.csv` | 35,999 | 2022-08-26 06:00:00 | 2026-10-04 04:00:00 | clean |
| `data_long/1h/SOLUSDT_1h.csv` | 35,999 | 2022-08-26 06:00:00 | 2026-10-04 04:00:00 | clean |
| `data_long/1h/XRPUSDT_1h.csv` | 35,999 | 2022-08-26 06:00:00 | 2026-10-04 04:00:00 | clean |
| `data_long/4h/AVAXUSDT_4h.csv` | 10,499 | 2021-12-19 08:00:00 | 2026-10-04 00:00:00 | X2 x1 |
| `data_long/4h/BNBUSDT_4h.csv` | 10,499 | 2021-12-19 08:00:00 | 2026-10-04 00:00:00 | X2 x1 |
| `data_long/4h/BTCUSDT_4h.csv` | 10,499 | 2021-12-19 08:00:00 | 2026-10-04 00:00:00 | X2 x1 |
| `data_long/4h/DOGEUSDT_4h.csv` | 10,499 | 2021-12-19 08:00:00 | 2026-10-04 00:00:00 | X2 x1 |
| `data_long/4h/ETHUSDT_4h.csv` | 10,499 | 2021-12-19 08:00:00 | 2026-10-04 00:00:00 | X2 x1 |
| `data_long/4h/LINKUSDT_4h.csv` | 10,499 | 2021-12-19 08:00:00 | 2026-10-04 00:00:00 | X2 x1 |
| `data_long/4h/SOLUSDT_4h.csv` | 10,499 | 2021-12-19 08:00:00 | 2026-10-04 00:00:00 | X2 x1 |
| `data_long/4h/XRPUSDT_4h.csv` | 10,499 | 2021-12-19 08:00:00 | 2026-10-04 00:00:00 | X2 x1 |

## Findings

- **X1** (error) `data/AVAXUSDT_4h.csv`: 2026-10-04 00:00:00 UTC (2026-10-04 03:00:00 +0300 Cairo) - 1 of 4500 shared bars differ from data_long/4h/AVAXUSDT_4h.csv (count 1). _report; the sources must be reconciled before either is used in a manifest_
- **X2** (error) `data/AVAXUSDT_4h.csv`: 2024-10-28 20:00:00 UTC (2024-10-28 23:00:00 +0300 Cairo) -> 2026-10-04 00:00:00 UTC (2026-10-04 03:00:00 +0300 Cairo) - 2 of 4500 bars differ from the aggregate of data_long/1h/AVAXUSDT_1h.csv (count 2). _the coarse bar is rejected (a partial / open candle or a bad resample); rebuild it from the finer bars_
- **X2** (error) `data/AVAXUSDT_4h.csv`: 2026-10-04 00:00:00 UTC (2026-10-04 03:00:00 +0300 Cairo) - 1 of 1124 bars differ from the aggregate of data1h/AVAXUSDT_1h.csv (count 1). _the coarse bar is rejected (a partial / open candle or a bad resample); rebuild it from the finer bars_
- **X1** (error) `data/BNBUSDT_4h.csv`: 2026-10-04 00:00:00 UTC (2026-10-04 03:00:00 +0300 Cairo) - 1 of 4500 shared bars differ from data_long/4h/BNBUSDT_4h.csv (count 1). _report; the sources must be reconciled before either is used in a manifest_
- **X2** (error) `data/BNBUSDT_4h.csv`: 2024-10-28 20:00:00 UTC (2024-10-28 23:00:00 +0300 Cairo) -> 2026-10-04 00:00:00 UTC (2026-10-04 03:00:00 +0300 Cairo) - 2 of 4500 bars differ from the aggregate of data_long/1h/BNBUSDT_1h.csv (count 2). _the coarse bar is rejected (a partial / open candle or a bad resample); rebuild it from the finer bars_
- **X2** (error) `data/BNBUSDT_4h.csv`: 2026-10-04 00:00:00 UTC (2026-10-04 03:00:00 +0300 Cairo) - 1 of 1124 bars differ from the aggregate of data1h/BNBUSDT_1h.csv (count 1). _the coarse bar is rejected (a partial / open candle or a bad resample); rebuild it from the finer bars_
- **X1** (error) `data/BTCUSDT_4h.csv`: 2026-10-04 00:00:00 UTC (2026-10-04 03:00:00 +0300 Cairo) - 1 of 4500 shared bars differ from data_long/4h/BTCUSDT_4h.csv (count 1). _report; the sources must be reconciled before either is used in a manifest_
- **X2** (error) `data/BTCUSDT_4h.csv`: 2024-10-28 20:00:00 UTC (2024-10-28 23:00:00 +0300 Cairo) -> 2026-10-04 00:00:00 UTC (2026-10-04 03:00:00 +0300 Cairo) - 2 of 4500 bars differ from the aggregate of data_long/1h/BTCUSDT_1h.csv (count 2). _the coarse bar is rejected (a partial / open candle or a bad resample); rebuild it from the finer bars_
- **X2** (error) `data/BTCUSDT_4h.csv`: 2026-10-04 00:00:00 UTC (2026-10-04 03:00:00 +0300 Cairo) - 1 of 1124 bars differ from the aggregate of data1h/BTCUSDT_1h.csv (count 1). _the coarse bar is rejected (a partial / open candle or a bad resample); rebuild it from the finer bars_
- **X1** (error) `data/DOGEUSDT_4h.csv`: 2026-10-04 00:00:00 UTC (2026-10-04 03:00:00 +0300 Cairo) - 1 of 4500 shared bars differ from data_long/4h/DOGEUSDT_4h.csv (count 1). _report; the sources must be reconciled before either is used in a manifest_
- **X2** (error) `data/DOGEUSDT_4h.csv`: 2024-10-28 20:00:00 UTC (2024-10-28 23:00:00 +0300 Cairo) -> 2026-10-04 00:00:00 UTC (2026-10-04 03:00:00 +0300 Cairo) - 2 of 4500 bars differ from the aggregate of data_long/1h/DOGEUSDT_1h.csv (count 2). _the coarse bar is rejected (a partial / open candle or a bad resample); rebuild it from the finer bars_
- **X2** (error) `data/DOGEUSDT_4h.csv`: 2026-10-04 00:00:00 UTC (2026-10-04 03:00:00 +0300 Cairo) - 1 of 1124 bars differ from the aggregate of data1h/DOGEUSDT_1h.csv (count 1). _the coarse bar is rejected (a partial / open candle or a bad resample); rebuild it from the finer bars_
- **X1** (error) `data/ETHUSDT_4h.csv`: 2026-10-04 00:00:00 UTC (2026-10-04 03:00:00 +0300 Cairo) - 1 of 4500 shared bars differ from data_long/4h/ETHUSDT_4h.csv (count 1). _report; the sources must be reconciled before either is used in a manifest_
- **X2** (error) `data/ETHUSDT_4h.csv`: 2024-10-28 20:00:00 UTC (2024-10-28 23:00:00 +0300 Cairo) -> 2026-10-04 00:00:00 UTC (2026-10-04 03:00:00 +0300 Cairo) - 2 of 4500 bars differ from the aggregate of data_long/1h/ETHUSDT_1h.csv (count 2). _the coarse bar is rejected (a partial / open candle or a bad resample); rebuild it from the finer bars_
- **X2** (error) `data/ETHUSDT_4h.csv`: 2026-10-04 00:00:00 UTC (2026-10-04 03:00:00 +0300 Cairo) - 1 of 1124 bars differ from the aggregate of data1h/ETHUSDT_1h.csv (count 1). _the coarse bar is rejected (a partial / open candle or a bad resample); rebuild it from the finer bars_
- **X1** (error) `data/LINKUSDT_4h.csv`: 2026-10-04 00:00:00 UTC (2026-10-04 03:00:00 +0300 Cairo) - 1 of 4500 shared bars differ from data_long/4h/LINKUSDT_4h.csv (count 1). _report; the sources must be reconciled before either is used in a manifest_
- **X2** (error) `data/LINKUSDT_4h.csv`: 2024-10-28 20:00:00 UTC (2024-10-28 23:00:00 +0300 Cairo) -> 2026-10-04 00:00:00 UTC (2026-10-04 03:00:00 +0300 Cairo) - 2 of 4500 bars differ from the aggregate of data_long/1h/LINKUSDT_1h.csv (count 2). _the coarse bar is rejected (a partial / open candle or a bad resample); rebuild it from the finer bars_
- **X2** (error) `data/LINKUSDT_4h.csv`: 2026-10-04 00:00:00 UTC (2026-10-04 03:00:00 +0300 Cairo) - 1 of 1124 bars differ from the aggregate of data1h/LINKUSDT_1h.csv (count 1). _the coarse bar is rejected (a partial / open candle or a bad resample); rebuild it from the finer bars_
- **X1** (error) `data/SOLUSDT_4h.csv`: 2026-10-04 00:00:00 UTC (2026-10-04 03:00:00 +0300 Cairo) - 1 of 4500 shared bars differ from data_long/4h/SOLUSDT_4h.csv (count 1). _report; the sources must be reconciled before either is used in a manifest_
- **X2** (error) `data/SOLUSDT_4h.csv`: 2024-10-28 20:00:00 UTC (2024-10-28 23:00:00 +0300 Cairo) -> 2026-10-04 00:00:00 UTC (2026-10-04 03:00:00 +0300 Cairo) - 2 of 4500 bars differ from the aggregate of data_long/1h/SOLUSDT_1h.csv (count 2). _the coarse bar is rejected (a partial / open candle or a bad resample); rebuild it from the finer bars_
- **X2** (error) `data/SOLUSDT_4h.csv`: 2026-10-04 00:00:00 UTC (2026-10-04 03:00:00 +0300 Cairo) - 1 of 1124 bars differ from the aggregate of data1h/SOLUSDT_1h.csv (count 1). _the coarse bar is rejected (a partial / open candle or a bad resample); rebuild it from the finer bars_
- **X1** (error) `data/XRPUSDT_4h.csv`: 2026-10-04 00:00:00 UTC (2026-10-04 03:00:00 +0300 Cairo) - 1 of 4500 shared bars differ from data_long/4h/XRPUSDT_4h.csv (count 1). _report; the sources must be reconciled before either is used in a manifest_
- **X2** (error) `data/XRPUSDT_4h.csv`: 2024-10-28 20:00:00 UTC (2024-10-28 23:00:00 +0300 Cairo) -> 2026-10-04 00:00:00 UTC (2026-10-04 03:00:00 +0300 Cairo) - 2 of 4500 bars differ from the aggregate of data_long/1h/XRPUSDT_1h.csv (count 2). _the coarse bar is rejected (a partial / open candle or a bad resample); rebuild it from the finer bars_
- **X2** (error) `data/XRPUSDT_4h.csv`: 2026-10-04 00:00:00 UTC (2026-10-04 03:00:00 +0300 Cairo) - 1 of 1124 bars differ from the aggregate of data1h/XRPUSDT_1h.csv (count 1). _the coarse bar is rejected (a partial / open candle or a bad resample); rebuild it from the finer bars_
- **G1** (error) `data1h/AVAXUSDT_1h.csv`: 2026-08-02 16:00:00 UTC (2026-08-02 19:00:00 +0300 Cairo) - 1 missing 1h bar(s) between 2026-08-02 15:00:00 UTC and 2026-08-02 17:00:00 UTC (count 1). _no fill; signals whose lookback crosses it are not evaluated; positions across it are gap_exposed_
- **S1** (warning) `data1h/AVAXUSDT_1h.csv`: 2026-10-04 03:00:00 UTC (2026-10-04 06:00:00 +0300 Cairo) -> 2026-10-04 04:00:00 UTC (2026-10-04 07:00:00 +0300 Cairo) - ends 1 1h bar(s) before the newest 1h file (2026-10-04 04:00:00 UTC) (count 1). _the window end is cut to the last fresh row_
- **G1** (error) `data1h/BNBUSDT_1h.csv`: 2026-08-02 16:00:00 UTC (2026-08-02 19:00:00 +0300 Cairo) - 1 missing 1h bar(s) between 2026-08-02 15:00:00 UTC and 2026-08-02 17:00:00 UTC (count 1). _no fill; signals whose lookback crosses it are not evaluated; positions across it are gap_exposed_
- **S1** (warning) `data1h/BNBUSDT_1h.csv`: 2026-10-04 03:00:00 UTC (2026-10-04 06:00:00 +0300 Cairo) -> 2026-10-04 04:00:00 UTC (2026-10-04 07:00:00 +0300 Cairo) - ends 1 1h bar(s) before the newest 1h file (2026-10-04 04:00:00 UTC) (count 1). _the window end is cut to the last fresh row_
- **G1** (error) `data1h/BTCUSDT_1h.csv`: 2026-08-02 16:00:00 UTC (2026-08-02 19:00:00 +0300 Cairo) - 1 missing 1h bar(s) between 2026-08-02 15:00:00 UTC and 2026-08-02 17:00:00 UTC (count 1). _no fill; signals whose lookback crosses it are not evaluated; positions across it are gap_exposed_
- **S1** (warning) `data1h/BTCUSDT_1h.csv`: 2026-10-04 03:00:00 UTC (2026-10-04 06:00:00 +0300 Cairo) -> 2026-10-04 04:00:00 UTC (2026-10-04 07:00:00 +0300 Cairo) - ends 1 1h bar(s) before the newest 1h file (2026-10-04 04:00:00 UTC) (count 1). _the window end is cut to the last fresh row_
- **G1** (error) `data1h/DOGEUSDT_1h.csv`: 2026-08-02 16:00:00 UTC (2026-08-02 19:00:00 +0300 Cairo) - 1 missing 1h bar(s) between 2026-08-02 15:00:00 UTC and 2026-08-02 17:00:00 UTC (count 1). _no fill; signals whose lookback crosses it are not evaluated; positions across it are gap_exposed_
- **S1** (warning) `data1h/DOGEUSDT_1h.csv`: 2026-10-04 03:00:00 UTC (2026-10-04 06:00:00 +0300 Cairo) -> 2026-10-04 04:00:00 UTC (2026-10-04 07:00:00 +0300 Cairo) - ends 1 1h bar(s) before the newest 1h file (2026-10-04 04:00:00 UTC) (count 1). _the window end is cut to the last fresh row_
- **G1** (error) `data1h/ETHUSDT_1h.csv`: 2026-08-02 16:00:00 UTC (2026-08-02 19:00:00 +0300 Cairo) - 1 missing 1h bar(s) between 2026-08-02 15:00:00 UTC and 2026-08-02 17:00:00 UTC (count 1). _no fill; signals whose lookback crosses it are not evaluated; positions across it are gap_exposed_
- **S1** (warning) `data1h/ETHUSDT_1h.csv`: 2026-10-04 03:00:00 UTC (2026-10-04 06:00:00 +0300 Cairo) -> 2026-10-04 04:00:00 UTC (2026-10-04 07:00:00 +0300 Cairo) - ends 1 1h bar(s) before the newest 1h file (2026-10-04 04:00:00 UTC) (count 1). _the window end is cut to the last fresh row_
- **G1** (error) `data1h/LINKUSDT_1h.csv`: 2026-08-02 16:00:00 UTC (2026-08-02 19:00:00 +0300 Cairo) - 1 missing 1h bar(s) between 2026-08-02 15:00:00 UTC and 2026-08-02 17:00:00 UTC (count 1). _no fill; signals whose lookback crosses it are not evaluated; positions across it are gap_exposed_
- **S1** (warning) `data1h/LINKUSDT_1h.csv`: 2026-10-04 03:00:00 UTC (2026-10-04 06:00:00 +0300 Cairo) -> 2026-10-04 04:00:00 UTC (2026-10-04 07:00:00 +0300 Cairo) - ends 1 1h bar(s) before the newest 1h file (2026-10-04 04:00:00 UTC) (count 1). _the window end is cut to the last fresh row_
- **G1** (error) `data1h/SOLUSDT_1h.csv`: 2026-08-02 16:00:00 UTC (2026-08-02 19:00:00 +0300 Cairo) - 1 missing 1h bar(s) between 2026-08-02 15:00:00 UTC and 2026-08-02 17:00:00 UTC (count 1). _no fill; signals whose lookback crosses it are not evaluated; positions across it are gap_exposed_
- **S1** (warning) `data1h/SOLUSDT_1h.csv`: 2026-10-04 03:00:00 UTC (2026-10-04 06:00:00 +0300 Cairo) -> 2026-10-04 04:00:00 UTC (2026-10-04 07:00:00 +0300 Cairo) - ends 1 1h bar(s) before the newest 1h file (2026-10-04 04:00:00 UTC) (count 1). _the window end is cut to the last fresh row_
- **G1** (error) `data1h/XRPUSDT_1h.csv`: 2026-08-02 16:00:00 UTC (2026-08-02 19:00:00 +0300 Cairo) - 1 missing 1h bar(s) between 2026-08-02 15:00:00 UTC and 2026-08-02 17:00:00 UTC (count 1). _no fill; signals whose lookback crosses it are not evaluated; positions across it are gap_exposed_
- **S1** (warning) `data1h/XRPUSDT_1h.csv`: 2026-10-04 03:00:00 UTC (2026-10-04 06:00:00 +0300 Cairo) -> 2026-10-04 04:00:00 UTC (2026-10-04 07:00:00 +0300 Cairo) - ends 1 1h bar(s) before the newest 1h file (2026-10-04 04:00:00 UTC) (count 1). _the window end is cut to the last fresh row_
- **X2** (error) `data_long/4h/AVAXUSDT_4h.csv`: 2023-11-10 12:00:00 UTC (2023-11-10 14:00:00 +0200 Cairo) -> 2024-10-28 20:00:00 UTC (2024-10-28 23:00:00 +0300 Cairo) - 2 of 8999 bars differ from the aggregate of data_long/1h/AVAXUSDT_1h.csv (count 2). _the coarse bar is rejected (a partial / open candle or a bad resample); rebuild it from the finer bars_
- **X2** (error) `data_long/4h/BNBUSDT_4h.csv`: 2023-11-10 08:00:00 UTC (2023-11-10 10:00:00 +0200 Cairo) -> 2024-10-28 20:00:00 UTC (2024-10-28 23:00:00 +0300 Cairo) - 3 of 8999 bars differ from the aggregate of data_long/1h/BNBUSDT_1h.csv (count 3). _the coarse bar is rejected (a partial / open candle or a bad resample); rebuild it from the finer bars_
- **X2** (error) `data_long/4h/BTCUSDT_4h.csv`: 2023-11-10 12:00:00 UTC (2023-11-10 14:00:00 +0200 Cairo) -> 2024-10-28 20:00:00 UTC (2024-10-28 23:00:00 +0300 Cairo) - 2 of 8999 bars differ from the aggregate of data_long/1h/BTCUSDT_1h.csv (count 2). _the coarse bar is rejected (a partial / open candle or a bad resample); rebuild it from the finer bars_
- **X2** (error) `data_long/4h/DOGEUSDT_4h.csv`: 2023-11-10 12:00:00 UTC (2023-11-10 14:00:00 +0200 Cairo) -> 2024-10-28 20:00:00 UTC (2024-10-28 23:00:00 +0300 Cairo) - 2 of 8999 bars differ from the aggregate of data_long/1h/DOGEUSDT_1h.csv (count 2). _the coarse bar is rejected (a partial / open candle or a bad resample); rebuild it from the finer bars_
- **X2** (error) `data_long/4h/ETHUSDT_4h.csv`: 2023-11-10 12:00:00 UTC (2023-11-10 14:00:00 +0200 Cairo) -> 2024-10-28 20:00:00 UTC (2024-10-28 23:00:00 +0300 Cairo) - 2 of 8999 bars differ from the aggregate of data_long/1h/ETHUSDT_1h.csv (count 2). _the coarse bar is rejected (a partial / open candle or a bad resample); rebuild it from the finer bars_
- **X2** (error) `data_long/4h/LINKUSDT_4h.csv`: 2023-11-10 12:00:00 UTC (2023-11-10 14:00:00 +0200 Cairo) -> 2024-10-28 20:00:00 UTC (2024-10-28 23:00:00 +0300 Cairo) - 2 of 8999 bars differ from the aggregate of data_long/1h/LINKUSDT_1h.csv (count 2). _the coarse bar is rejected (a partial / open candle or a bad resample); rebuild it from the finer bars_
- **X2** (error) `data_long/4h/SOLUSDT_4h.csv`: 2023-11-10 08:00:00 UTC (2023-11-10 10:00:00 +0200 Cairo) -> 2024-10-28 20:00:00 UTC (2024-10-28 23:00:00 +0300 Cairo) - 3 of 8999 bars differ from the aggregate of data_long/1h/SOLUSDT_1h.csv (count 3). _the coarse bar is rejected (a partial / open candle or a bad resample); rebuild it from the finer bars_
- **X2** (error) `data_long/4h/XRPUSDT_4h.csv`: 2023-11-10 12:00:00 UTC (2023-11-10 14:00:00 +0200 Cairo) -> 2024-10-28 20:00:00 UTC (2024-10-28 23:00:00 +0300 Cairo) - 2 of 8999 bars differ from the aggregate of data_long/1h/XRPUSDT_1h.csv (count 2). _the coarse bar is rejected (a partial / open candle or a bad resample); rebuild it from the finer bars_
