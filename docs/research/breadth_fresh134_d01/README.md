# BREADTH-FRESH134-D01 results (research only)

Freeze: 7d61cfce (research/extra31_screen/breadth_fresh134_v1.json sha256 5d508f99..., wrapper d666254f..., sieve_v1_panel.py 6da5a849...).
Windows run on the owner's PC, fresh clone with core.autocrlf=false; manifest fb0c0572... and all 135 CSV hashes checked before any signal; 7 wrapper tests passed there.
Label: CONSUMED_CALENDAR_SYMBOL_TRANSFER_DIAGNOSTIC (consumed calendar window; transfer of an existing candidate; no untouched-market claim).

| run | status | median n | meanR15 | meanR23 | early | late | ex-best |
|---|---|---|---|---|---|---|---|
| max1 (primary, rule v1) | FAIL | 36 | +0.0091 | +0.0012 | +0.054 | -0.013 | -0.082 |
| max3 (secondary) | FAIL | 106 | +0.0234 | +0.0151 | +0.069 | -0.024 | -0.026 |

Both fail the late half and ex-best-coin gates; max1 also fails the trade count and 30-per-half gates. Signals: 7,895 raw, 4,192 eligible.
Order 0 (alphabetical) is dominated by 1000BONKUSDT (max1 +6.16R of +6.37R; max3 +7.42R of +6.33R). Worst-path diagnostic meanR23 is negative in both.
Decision proposed: FAIL, no tuning; max3 does not substitute for max1.

Result json sha256: max1 8d8dfe1a2b428755cd754cc21353ee16fb5b8976608f45a77543df2d7326afc7, max3 5b3906201c1a9692ea57ec341026db23cc8ca1f59da0d1e101dfce14f505811e.
No account, key, runtime, scheduler or installer action.
