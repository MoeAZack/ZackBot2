# IVS-1H independent verification

Reviewed result 544a33d29d83ea988631e0d918e1697d4d63e932 and source/data freeze ef2c4cc17805a539984430885434029addab828e09f1178d7481c745d7e85673. All 32 pins match.

The independent raw audit fetched all 703 official ZIPs and checksums into an F-drive cache. All checksums matched. It reconstructed 60,600,092 rows into all 17,376 calendar hours and matched every hourly table field and source hash with zero mismatches. There are 16,837 valid hours, 504 missing-day hours and 35 other invalid hours. The 31 stale flags overlap the 35 low-count hours; they are not an additional category. Raw archives are not committed. The per-file first_us metadata was not separately compared; per-file last time, row/hour counts, hashes, bad-row and unit counters were reconciled. The hourly data SHA is b9f16e54324e771e45dbdae2b23c05beef8f61db74d086b7d8e329ec928339f6.

The separate Stage B implementation imports no author signal or runner. It matches all 826 coin signal tuples, 230 global IV-shock hours and 153 distinct scheduled fill times. No missing or extra tuples; stop distances differ by at most 4.55e-13. Calendar weeks are 102.3214285714, so the maximum possible completed-fill frequency is 1.4952879581 per week. FREQUENCY_FAIL is confirmed. The upper bound being above 100 does not mean 100 trades were actually completed; no portfolio simulation occurred.

The observed base_asset is BTCBVOL. The original ruling's BTC literal was an Integration metadata error. Accept the disclosed schema clarification and preserve the published strings, original freeze and result. No data or signal rerun is required for this label.

Economics were NOT evaluated by the original IVS run or this verification. A separate dependent 12-case economic diagnostic is authorized under 0547b96f49788d49d562f1c599b96d6ab2e536f8, retaining the original frequency failure and prohibiting retrospective PASS or activation. The subsequent owner catalog-and-sieve plan has priority over optional new proposals. No account, key, position, strategy setting, worker or scheduler was changed by the IVS verification.
