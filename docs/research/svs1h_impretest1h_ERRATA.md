# SVS-1H / IMP-RETEST-1H labelling errata (Codex review 6845908799, evidence 67f3cf36). No rerun; result files unchanged.

- IMP state events `trigger=12136` are FULL-HISTORY triggers = 5,621 before the window + 6,515 in the window.
  The 6,515 in-window triggers = 907 liquidity-ineligible (setup consumed, no decision) + 5,608 executable decisions.
  `raw_signals.csv` therefore has 6,515 rows, not 5,608.
- Closes: the across-order MEDIAN completed closes is 1,037. Order 0 has 1,041 admitted lots = 1,040 closed + 1 TERMINAL_OPEN.
  Medians (across 40 orders) and order-0 values are distinct and must not be mixed.
- Mean net R15: SVS median -0.0563 / order0 -0.0591; IMP median -0.10915 / order0 -0.1288.
- The per-trade cost decomposition (gross_after_slip / slippage / fees / funding) is ORDER 0 ONLY. It must not be combined
  arithmetically with median R, and slippage is already inside gross_after_slip (do not subtract it twice).
- SPB 28 Oct 2024 20:00-21:00 UTC annotation: native zero-volume placeholder bars and the canonical-vs-native disagreement are
  observed; the exchange-outage cause is NOT independently established.
