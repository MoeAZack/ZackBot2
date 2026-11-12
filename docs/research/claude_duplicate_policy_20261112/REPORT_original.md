# Duplicate coin/side policy test (research only, 12 Nov 2026)

Source: master `7b5beecb` (`backtest.run` unchanged, used as is). Scripts: `gen.py` (candidate lots per sleeve) and `overlay.py` (one shared account). Raw tables: `results.csv`, `yearly.csv`. No running account, key or setting touched.

## Method
1. Each sleeve was run alone through `backtest.run` (one lot per coin, adverse intrabar path, fee = cost per side, slippage 0, **funding excluded**), giving every candidate lot with its entry time, exit time and net R.
2. `overlay.py` replays all sleeves of a book in one $4,000 account, in time order, with ties broken by the saved sleeve order. Account rules applied: max positions per sleeve, 2.5% open-risk cap, 3% Cairo-day loss halt (realized). 10% DD stop is reported (date first hit), not enforced, so policies stay comparable.
3. Netting: a Binance coin/side position is the sum of the lots; each lot keeps its own reduce-only stop for its own quantity, so economics equal separate lots. Budget for policy B counts each open lot's **entry** risk until it closes (conservative: a lot at breakeven still uses budget).

Policies: **baseline** = stack freely (today). **A** = refuse an entry when another sleeve holds that coin and side. **B** = shared budget per coin/side; a new lot gets the remaining budget (skipped if under 25% of normal size). Caps tested: 1.5x and 2x sleeve risk ($12/$16 for account 4, $24/$32 for accounts 1-3).

Data: 9 coins. 15m history 11 Oct 2024 to 27 Sep 2026 (from `C:\Dev\paper-backup-20261105\candles_15m_research`), with 4h bars built from it. Long 4h history 2021 to Sep 2026 (`data_long/4h`, 8 coins with no ADA, and no exp_vwaprev because it lacks quote volume). All consumed DEV data, no holdout.

## Results (full window, 15 bp per side)
| Book | Baseline | A refuse | B 1.5x | B 2x |
|---|---|---|---|---|
| Account 4, all 12 sleeves (2y) | -92% (10% DD hit 16 Oct 2024) | -92% | -92% | -92% |
| Account 4, six 4h sleeves (2y) | -9.5%, DD 14.5%, 11.5/wk | -12.4%, DD 15.7%, 10.5/wk | -9.0%, DD 12.5% | -9.1%, DD 14.3% |
| Account 4, 4h sleeves (2021-26) | -26.5%, DD 30.8% | -26.2%, DD 29.4% | -26.1%, DD 29.2% | -25.2%, DD 29.6% |
| Account 1 book (2y) | -18.1%, DD 33.7% | -14.2%, DD 32.7% | -17.6% | -17.0% |
| Account 1 book (2021-26) | -3.3%, DD 35.1% | -4.0%, DD 34.1% | -2.6% | -0.7% |
| Account 2 OLD3 (2y) | +5.2%, DD 18.3% | +8.3%, DD 16.5% | +6.4% | +5.2% |
| Account 2 OLD3 (2021-26) | +13.4%, DD 18.5% | +48.1%, DD 15.3% | +11.2% | +13.4% |

The OLD3 refuse gain from 2021 to 2026 comes from one trade: an ema_st XRP long in Nov-Dec 2024 (+71R). Refusing a duplicate left a slot free for it. Without 2024, refuse beats baseline by about 1 point a year.

Year by year (or six-month blocks), no policy wins consistently on accounts 1 and 4. Average yearly return differences are under 1 point, apart from that outlier.

## Are the overlapping lots worse? No.
Average net R at 15 bp for lots that join another sleeve's open lot on the same coin and side, against all other lots:
- Account 4, 4h sleeves: +0.155 vs -0.037 over 2y, and +0.225 vs -0.039 over 2021-26. A second sleeve agreeing acts as confirmation.
- OLD3: +0.236 vs +0.100 over 2y, and about equal over 2021-26.
- Account 1, 2021-26: -0.096 vs +0.025. This is the only book where overlapping lots are worse.

## Side finding (bigger than the policy question)
Account 4's six 15m scalp sleeves are about 0R before costs and lose 0.3 to 0.9R per trade after costs. Stops are tight, so 7 bp per side already costs about 0.35R. In replay the 12-sleeve book hits the 10% drawdown stop within days, whatever the policy. exp_vwaprev and exp_range are also negative at 15 bp or more.

## Proposal for the 20 Nov review
Use **B with a hard cap of 2x one sleeve's risk per coin/side** ($16 on account 4, $32 on accounts 1-3), counted on entry risk. In these tests it gives the same profit as today (within noise) and keeps the confirming second lot. It also stops three or more sleeves piling onto one coin. The worst stacks seen today were $24 on account 4 and $48 on account 1. Refuse (A) is not recommended as a general rule: it drops the confirming lots and gains nothing consistent. Keep it as an option only for OLD3, and only if forward data agrees. Default OFF until approved.
