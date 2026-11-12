# RBR identity and Stage-A provenance

Causal v4 was economically evaluated at protected head 6754a336652496952aaab82e42fbe9bd6171430a: 294 closed trades, -35.567R, mean -0.121R, PF 0.755, zero incomplete rows, FAILED_GATES. Full packet path, hashes, stress results, authorities and per-fold results are in identity_audit.json. [Integration result](https://github.com/MoeAZack/ZackBot2/issues/219#issuecomment-6699064340).

No economic run for the distinct causal v3 identity was located. That does not prove none exists. The v4 authority rebind is a distinct trial and must not be relabeled v3.

The original Stage-A packet requested 20 symbols but actually ran 9 complete symbols, with max_pos=4. It recorded gross $39.798985 after slippage, fees $7.166298, and funding -$2.481770 under its cost sign convention (a $2.481770 credit); net $35.114457 and 76 closes. Slippage is not separately reported. The packet reports per-file cache hashes and the manifest digest; these are copied into the JSON. The Stage-A/new-control difference remains unresolved without a matched ablation.
