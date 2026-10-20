# FEE-FAST-01 replay artifacts (read-only research, not runtime code)

The fee-edge gate replay at 3x vs 5x, run on master `d5e3491` (#13 comments 6288223867 and 6290026125).
`SHA256SUMS` covers the original files; `episodes.csv.gz` gunzips to the `episodes.csv` listed there.

- `feerep.py`: the replay script. It uses `backtest.Book` / `backtest.run`. Inputs:
  - the Wave-2 USDT 15m pack (6 coins, local);
  - repo `data/*_4h.csv`.
  
  Holdout: rows with an open time on or after 2026-09-28T00:00Z are dropped while parsing.
- `episodes.csv.gz`: one row per closed backtest entry, with columns strategy, book, tf, side, stop_pct, gross, net. No exchange ids.
- `table.json`: the per strategy × side summary posted on #13.
- `err.log`: the data window and per-strategy entry counts.

**Independent check without the 15m pack:** recompute the table from `episodes.csv`. A trade is refused at multiple M when `stop_pct <= M * 0.0014`. The band is refused at 5 but kept at 3. For the band and for the kept-at-5 group, compare n, mean gross, mean net and t.
