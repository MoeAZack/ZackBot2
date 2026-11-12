# Independent duplicate coin/side review — 12 Nov 2026

## Decision summary

Research only, default OFF. No account, setting, position or key changed. Existing FWD-01 and matched protection/control studies continue separately. **Do not deploy the proposed cap on this report alone.** A 2x entry-risk cap is a reasonable risk-guardrail candidate for the 20 Nov review, with no demonstrated alpha improvement. It preserves a second lot and prevents a third full-sized stack, but does not fix correlated exposure across different coins or a strategy that loses after costs.

The strongest result is the scalp cost failure. Recommend **not retaining the six 15m sleeves as profit-seeking active strategies at promotion**; keep their signals/shadow evidence, or explicitly bounded execution-only experiments if still needed. This is a research recommendation, NOT an instruction carried out on account4. No live changes made. Seven measured live closes are too small a sample to overturn two years of adverse cost evidence.

## What was and was not reproduced

1. **2x cap: modest model impact, not proof of equality/noise.** In the primary adverse-path 4h fixed-risk cases, B2's full-window difference from stacking ranges from -2.23 to +1.39 percentage points across 7/15/23bp. At15bp, calendar-year differences reach3.01points. No statistical equivalence test establishes 'within noise'. Refuse-all has much larger, inconsistent changes. OLD3 B2 equals baseline by construction: that book has only two long sleeves and one short sleeve, so its entry-only two-sleeve budget does not normally bind.
2. **XRP concentration: independently confirmed.** EMA-long XRP entry6Nov2024 14:00 Cairo, exit10Dec2024 02:00 Cairo, net **+70.864R** at15bp. In the primary overlay baseline misses it, refusal admits it. Direct contribution **$1,133.82 /28.35 percentage points**, about **86.5%** of the refusal-vs-baseline spread. Removing this one opportunity reduces refusal from+5.35% to-20.17%; baseline remains-27.42%. Removing all2024 entry opportunities leaves baseline-24.25%, refusal-22.65%. These are separate conditional-tape counterfactuals, not a fresh alpha qualification.
3. **Joining lots: directionally better for account4 4h, not universal confirmation.** The independent means differ materially from the quoted values; OLD3's two-year joining mean is worse in this implementation. Conditioning on another open position is not a randomized confirmation test.
4. **15m costs: independently confirmed and somewhat worse at the high stress.** Gross candidate mean R is close to zero. At7bp/side all six are negative; at23bp several are below-1R/trade. The12-sleeve overlay reaches about-92% under every policy and breaches10% CLOSED-equity drawdown on16Oct2024 (baseline15bp:06:30 Cairo). The10% limit is only reported, NOT enforced in this research; actual enforced stopping would not follow that full loss path.

## Primary independent returns at15bp per executed side

These are CLOSED-trade cash returns from the fixed candidate tape, funding excluded, adverse `pessimistic='worst'`, conservative end-of-exit-bar timestamps. They **do not reproduce all of Claude's absolute headline returns**.

| book | baseline | refuse | cap1p5 | cap2 |
| --- | --- | --- | --- | --- |
| A4_4H_2Y | -7.39 | -13.68 | -7.34 | -7.01 |
| A4_4H_LONG | -32.77 | -30.9 | -33.09 | -32.68 |
| A4_ALL12_2Y | -92.24 | -92.15 | -92.0 | -92.09 |
| NEW3_2Y | -13.61 | -6.44 | -12.86 | -14.1 |
| NEW3_LONG | 5.96 | 5.42 | 4.57 | 6.9 |
| OLD3_2Y | -6.72 | -3.37 | -5.02 | -6.72 |
| OLD3_LONG | -27.42 | 5.35 | -24.2 | -27.42 |

## Exact-number disagreements and timing/path sensitivity

| book | policy | Claude_claim_return_pct | independent_worst_exitclose_pct | independent_path_exitclose_pct | independent_path_exitopen_pct |
| --- | --- | --- | --- | --- | --- |
| A4_ALL12_2Y | baseline | -92.0 | -92.24 | nan | nan |
| A4_4H_2Y | baseline | -9.5 | -7.39 | -1.6 | -3.6 |
| A4_4H_LONG | baseline | -26.5 | -32.77 | -18.97 | -16.15 |
| NEW3_2Y | baseline | -18.1 | -13.61 | -13.61 | -1.24 |
| NEW3_LONG | baseline | -3.3 | 5.96 | 5.96 | 19.88 |
| OLD3_2Y | baseline | 5.2 | -6.72 | 13.56 | 12.76 |
| OLD3_LONG | baseline | 13.4 | -27.42 | 30.2 | 31.43 |
| OLD3_LONG | refuse | 48.1 | 5.35 | 61.34 | 55.75 |


Normal-OHLC `path` and adverse `worst` are different backtester settings. Entry/exit-bar timestamps also change which slot/risk budget is free. The original gen/overlay/raw tables live in Claude's container and were not available directly on the PC; the bundle was requested on#219. Without those exact generator settings, I cannot attribute every numeric difference to one cause or endorse the quoted+13.4%/+48.1% as independently reproduced. All alternative results are retained; no winning assumption was selected.

## Joining vs other admitted lots,15bp

| book | joined_other_count | joined_other_average_R | nonjoined_other_average_R |
| --- | --- | --- | --- |
| A4_4H_2Y | 376 | 0.0095 | -0.0583 |
| A4_4H_LONG | 88 | 0.0761 | -0.1047 |
| NEW3_2Y | 103 | -0.1416 | -0.0365 |
| NEW3_LONG | 260 | -0.0065 | 0.013 |
| OLD3_2Y | 52 | -0.1816 | -0.0175 |
| OLD3_LONG | 148 | -0.0742 | -0.0531 |


Means are net trade R under each candidate model, before the separate funding stress. Counts are conditional on the baseline overlay's admission decisions. They do not establish causality or promotion readiness.

## Six15m sleeves: standalone candidate mean R

Costs below are **bp per executed entry/exit side/fill**, not roundtrip. A15bp case charges15bp on entry and15bp on a full exit; partial fills are charged on their executed notional. Gross=zero modeled fees/slippage/funding. No venue-maker savings or ideal fills are assumed.

| strategy | 0 | 7 | 15 | 23 |
| --- | --- | --- | --- | --- |
| exp_scalp | 0.003 | -0.3511 | -0.7559 | -1.1606 |
| exp_structure | -0.004 | -0.3564 | -0.7591 | -1.1618 |
| failed_breakout_scalp | -0.0073 | -0.2887 | -0.6103 | -0.9319 |
| momentum_burst_scalp | 0.0276 | -0.1515 | -0.3563 | -0.561 |
| quick_bank_runner | -0.0578 | -0.4092 | -0.8107 | -1.2123 |
| range_scalp | -0.0201 | -0.3345 | -0.6938 | -1.0531 |


The similar-92% full-book terminal results have a mechanical component: fixed$8 risk ceases to fit a2.5% entry-risk limit once equity falls below about$320 (-92%). Thus equal terminal losses are not evidence that early paths or policies are equivalent. Compare first-drawdown dates and unit economics, not only that floor.

## Funding stress, separately from the funding-excluded comparison

Both sides charged3bp per8h settlement on fixed entry notional, booked at close. This deliberately conservative proxy does not reduce notional after partial exits and is not native historical funding. It changes cash/daily-halt timing relative to periodic settlement. No qualification may rely on it as measured funding.

| book | baseline | refuse | cap1p5 | cap2 |
| --- | --- | --- | --- | --- |
| A4_4H_2Y | -16.23 | -21.95 | -14.7 | -15.84 |
| A4_4H_LONG | -47.63 | -45.23 | -47.34 | -47.45 |
| NEW3_2Y | -26.37 | -19.42 | -22.81 | -26.25 |
| NEW3_LONG | -34.56 | -26.61 | -31.46 | -34.11 |
| OLD3_2Y | -16.46 | -12.74 | -14.37 | -16.46 |
| OLD3_LONG | -54.52 | -17.17 | -48.09 | -54.52 |

## Live account4 snapshot — read only

As of **2026-11-12T11:54:55.735050+02:00**: **7 measured strategy closes, net$-15.04, sumR-1.87**. Manual stop drills excluded. This tiny sample does not prove a durable edge or independently refute the historical finding. Raw operational snapshot retained locally and excluded from publication. No account mutation or key access.

## Method, provenance and limits

- Accepted model source **7b5beecb3a964d4ce36aa7187a734093ffe6818a**. backtest.run unchanged. Independent generator/overlay; no Claude gen.py/overlay.py imports.
- Nine-coin15m common grid11Oct2024 17:00UTC–27Sep2026 23:45UTC; native quote volume aggregated only over complete16-candle4h groups. Eight-coin long4h19Dec2021 08:00UTC–27Sep2026 20:00UTC, noADA/noVWAP where qv unavailable. All consumed DEV, fixed survivor universe, no sealed holdout or tuning.
- Standalone candidate generation uses large paper capital and tiny risk to avoid bankrupt-strategy tape truncation; full-curve coverage verified. These are economic R tapes with legacy floors, not current exchange-filter/minimum/margin qualification.
- **Important fixed-tape limitation:** each standalone generator already enforces one own position per coin. If the overlay refuses that trade, any later signals hidden by the standalone holding interval are absent. This is a replay of preselected potential trades, **not a full policy-dependent signal/exit resimulation**. Candidate lots still open at the end are recorded in generation metadata but absent from the closed-trade overlay. Do not call these results live causal parity or qualified strategy economics.
- Primary overlay:fixed$8/account4 and$16/others; explicit max positions1/2 per sleeve;2.5% open ENTRY-risk;3% realized Cairo-day halt through IANA Africa/Cairo;10% closed-DD date only. Budget held until full lot close, even at breakeven. B resizes and refuses below25% normal size. Ties:exit first, saved sleeve order, then symbol. End-of-exit-bar primary; open-of-exit-bar sensitivity uses+1microsecond for same-bar round trips so entry precedes its own close.
- Whole-window continuous cash path; no annual reset or stitched equity curves. Yearly tables include no-trade years as zero and flag partial first/last years. DD excludes intrabar/unrealized equity and can miss earlier/larger drawdowns.
- **208 candidate tape files and model/raw-input hashes validated. 328 fresh policy cases have code/tape/contract/parameter fingerprints with stale-cache refusal.** Independent bounded cross-review recomputed all328 fingerprints:0mismatches. Eleven overlay tests passed, including25% sizing, distinct EX04/EX05 risk/caps, Cairo DST and funding stress. N6's independent signal review is a separate artifact.

## 20 Nov review recommendation

Keep B2 as a default-OFF risk guardrail proposal, not a profitability cure. Do not generally refuse a second confirming sleeve based solely on these averages or one XRP winner. Compare observed duplicate episodes, joint loss clusters, entry-risk reservations, and incremental after-cost P&L with the forward records before acceptance. Account1 is a reference and remains unchanged. The six15m sleeves are a red economic flag; no scale/mainnet promotion, with shadow/execution-only disposition proposed for review. Native funding and full policy-dependent execution evidence remain outstanding.

Reproduction bundle:preregistration.json,generate_tapes.py,overlay/overlay_sim.py,run_overlays_hashed.py,run_overlays_path_hashed.py,validated_run_audit.json,tape_provenance_validation.json,claim_comparison.csv,results-validated-*.csv,yearly_all_validated.csv,outlier_attribution.json. Raw candidate/admitted/curve CSVs retained locally underF:\ZackBot2_data\DUP-COIN-SIDE-INDEPENDENT-20261112. No runtime policy is enabled by these files.
