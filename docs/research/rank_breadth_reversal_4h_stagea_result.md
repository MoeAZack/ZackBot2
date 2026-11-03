# RBR-4H development result

Status: **REJECTED / PARKED / NO PAPER AUTHORITY**

The frozen `rank_breadth_reversal_4h` cells were evaluated once on CORE-20 development at exact head
`646ecc79612351d090fe36c02b6e4b2fdba2753d`. Long fails the economic and 60-trade gates. Short is aggregate-positive,
but only nine closes occurred in the BTC-below-EMA200 regime versus the preregistered minimum of 15, so it is not a
development survivor and no chronological fold may be opened. No threshold, side, universe or management rule changed.

| Side | Trades | Symbols | Gross P&L | Fees | Actual funding | Net P&L | Net R | Mean R | PF | Max DD | Regime counts | Verdict |
|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---|---|
| Long | 59 | 8 | -$4.81 | $4.90 | +$0.53 | -$10.24 | -1.70R | -0.029R | 0.912 | 6.34R | above 28 / below 31 | reject: economic + frequency |
| Short | 76 | 8 | +$39.80 | $7.17 | -$2.48 | +$35.11 | +7.33R | +0.096R | 1.239 | 7.96R | above 67 / below 9 | reject: regime sample |

For short, both observed regime aggregates were positive, but nine observations cannot satisfy the frozen coverage claim.
Opening walk-forward folds anyway, lowering the 15-trade regime floor, or re-labelling the candidate as regime-specific
would be post-result rescue. A future independently preregistered regime-specific family would consume a new trial.

Packets under `C:\Dev\ZackBot_cowork_data\rbr4h_stagea\`:

- long: `282d9f62d37b00d326027bcf0e01aeaebaa9068df489cb3e24d6f06eb48b2f1f`
- short: `ff5cc80468610804236a06ac57cbaa737936509ba04d73c41335ede65bf0c28f`

ROADMAP FIT: low-turnover cross-sectional reversal diversifier.  
DEFERRED: folds, holdout, Stage B, PAPER/testnet, runtime, mainnet, and rescue.
