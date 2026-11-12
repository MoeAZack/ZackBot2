# BASKET-OLD3-H5 independent result review
Source freeze347c4b71, result6538f3c8, trial85e12a28. Exact frozen basket blob ec48c6b562371159e90e51868ae494841c005529 independently matches GitHub347c4b71.

Native24tapes regenerated with frozen native engine/strategy source. Independent cash repricer verifies9741closed lot instances (9783total,42open,363partials) with maxerror1.4211e-14USD; published tape counts all match. HOTpartials64path/57worst eachcost; actual entryrisk max7.9999951<=8. Source methods/signals shared; independent accounting, not independent strategy implementation. Raw native lots4.8MB and detailed identity2.2MB retained on F with hashes in summary, not duplicated inGit. Exact source bytes zipped to preserve run hashes across line endings.

Separately authored seed0 ledger imports neither author replay nor target_cash_events. All18matchedrows (3books x3costs x2paths) reproduce net/terminal equity/cash/open marks/fills/refusals/phantom hours/funding/periods/DD/exposure within9.1e-13 numericerror. Combined15bp per-lot status/IDs and cash agree on all1574path /1687worst records, zero mismatch. Seed0 combined is additive to two solo books within8.7e-12USD. No aggregate-cap/daily-halt refusals. Caveat: standalone per-coin native candidate tapes still share missing counterfactual signals; accounting parity does not eliminate material phantom selection bias.

15bp SEED0 (not40-order medians): OLD3 +6.914709% path/-3.955081% worst; H5+3.779244% both; Combined+10.693953% path/-0.175836% worst. Author40-order label NOT_A_LEAD stands. No activation/account changes or parameter tuning.

Next recommended bounded work: native admission-aware fixed8 H5(max1) and EMA-long(max2) control runs, same frozen signals/management/data/common-flat-start, no phantom lots; both retained regardless outcomes. This is dependent diagnostic evidence, not two new discoveries. No new combined basket until both controls are assessed.
