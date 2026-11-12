# Claude duplicate coin/side study: original files, errata and tie-order robustness (12 Nov 2026)

Research only, default OFF. No runtime code, settings, account or key touched. Model source: master `7b5beecb` (`backtest.run` unchanged).
This folder answers Codex's request on #219 (6820613035) for the original files behind `REPORT_original.md`.

## Files
| File | What |
|---|---|
| `REPORT_original.md`, `results_v1_original.csv`, `yearly_v1_original.csv` | The report and tables as first sent to moe. **Superseded; see errata.** |
| `gen.py` | Candidate-lot generator. Defaults reproduce the original tapes: `pessimistic='path'`, fee = cost per side, slip 0, funding 0, start 1e9, risk 1e-6, max_pos 9, warmup 220. Env `PESS=worst` and `OUTD=<dir>` were added afterwards for the worst-path rerun. 4h bars for the two-year window are resampled from the 15m files without dropping incomplete groups (Codex drops them; the effect is negligible, see below). |
| `overlay_v1_original.py` | The shared-account replay that produced the original numbers. |
| `overlay_v2_corrected.py`, `results_v2_corrected.csv` | The corrected replay and its tables. |
| `cmp.py` | Runs Codex's `overlay_sim.py` (evidence head `5121e791`) on Claude's tapes. |
| `tieorder.py`, `tieorder_path.csv`, `tieorder_worst.csv` | Every policy under 40 coin tie orders (seed 0 = alphabetical, as Codex), for normal-path and worst-path tapes. |

Inputs: 15m data from `C:\Dev\paper-backup-20261105\candles_15m_research` (9 coins, 2024-10-11 to 2026-09-27), and long 4h data from repo `data_long/4h`. Scripts expect them under `/tmp/claude-0/work/d15` and write tapes to `/tmp/claude-0/work/out[w]`. Adjust these paths to rerun.

## Errata: why the original numbers differ from Codex's
The candidate tapes agree. Codex's `overlay_sim.py` run on Claude's tapes reproduces Codex's path-mode numbers: A4 4h 2y baseline -1.6%, OLD3 2y +13.6%, OLD3 long baseline +31.1% (Codex +30.2%, which uses a slightly different long-4h file and end cut). The disagreement came from two defects in `overlay_v1_original.py`:
1. **Same-bar tie order (the main cause).** When several coins signal on the same bar, the order decides which coin a 1- or 2-slot sleeve takes. v1 used an unstable sort, so the coin order was arbitrary. Fixed in v2 to a stable sleeve-then-coin order. Changing only this moves A4 4h 2y from -9.5% to -1.6%, and OLD3 long from +13.8% to +31.1%.
2. **Daily 3% halt.** v1 counted only losses on lots opened that same day, checked the halt only at entries, and used a fixed UTC+2 day. v2 counts every close at its exit time, on the IANA Africa/Cairo day. On its own this changed little (A4 -9.5% stays -9.5%; OLD3 long 13.4% becomes 13.8%).

v2 now matches Codex's overlay exactly on the shared cases. **Codex's numbers are the right ones; Claude's original absolute returns were wrong.**

## What survives: 40 random coin orders, both candle-path models (15 bp, median difference vs stacking, percentage points)
Because the tie order alone moves returns by many points, one ordering is not evidence. `tieorder_*.csv` re-run every policy under 40 orders.

| Book | refuse vs stack | cap 2x vs stack | Read |
|---|---|---|---|
| Account 4, 4h sleeves (2y and 2021-26) | -0.1 to +2.6, wins 50-100% | **+0.4 to +1.3, wins 80-100%** | Cap 2x is a small, consistent plus with slightly lower DD; refuse mixed |
| Account 1 book (2y) | +2.4, wins 80% | +0.1, wins 55% | mixed |
| Account 1 book (2021-26) | **-4.9, wins 17%** | -0.8, wins 38% | refuse hurts on long history |
| OLD3 (2y) | +1.2 to +2.0, wins 88-100% | 0 (never binds: only 2 long sleeves) | refuse small plus |
| OLD3 (2021-26) | **+28 to +29, wins 100%**; **+7.9 (p10 +3.4, p90 +15.1), wins 100% with the +71R XRP lot removed** (path model) | 0 | refuse helps beyond the outlier |

Under worst-path tapes the account 1 tapes are identical (that book has no intrabar stop moves), and the signs above hold.

## Revised proposal for the 20 Nov review (default OFF, per-account setting)
- Accounts 1, 3 and 4: shared cap **2x one sleeve's entry risk per coin/side** ($32 / $32 / $16), as a risk guardrail. It is not an alpha fix.
- Account 2 (OLD3): **refuse duplicates** is the candidate. Its only overlap is ema_st long and hot_coin long on the same coin, and refusing beats stacking in every tie order, including with the XRP outlier removed. This still needs forward confirmation before it is enabled.
- Unchanged: the six 15m scalp sleeves lose after costs (Codex confirmed independently).

Limits (shared with Codex's report): fixed-candidate replay (a refused lot can hide later signals), funding excluded, realized-only DD, consumed DEV data, no holdout.
