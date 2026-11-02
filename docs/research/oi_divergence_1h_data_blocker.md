# OI-price divergence/reclaim 1h — Stage-A data blocker

Status: **DATA BLOCKED / NO STRATEGY EVALUATION / NO PAPER AUTHORITY**  
Checked: 2 November 2026, Cairo  
Bounded development request: 20 frozen symbols, `2024-10-14..2025-04-01` (end-exclusive)

The local audit found no authoritative `oi_metrics_manifest.json` and no `binance_um_metrics_v1` archive store under
`C:\Dev\ZackBot2_data`. The existing OI work provides a signed, fail-closed manifest admission predicate and a causal
row loader, but no downloaded OI values. Therefore an OI-price signal cannot be preregistered and evaluated honestly
yet. No price-derived proxy is substituted.

`tools/research/oi_divergence_stagea_readiness.py` is the only new harness. It is network-free and hard-bounded to the
development interval. It requires all 20 symbols to pass the signed manifest and disk checks, opens rows only through
the causal loader, verifies exactly 288 five-minute rows per day per symbol, and verifies `available_at = period_end +
5 minutes`. It never opens the sealed holdout and grants no strategy, PAPER, runtime, or mainnet authority.

The next admissible action is an authoritative, checksum-verified bulk acquisition for exactly the required development
window, followed by this readiness command. Candidate rules remain unwritten until the data gate reports `READY`.

```powershell
python tools/research/oi_divergence_stagea_readiness.py `
  --manifest C:\Dev\ZackBot2_data\binance_um_metrics_v1\oi_metrics_manifest.json `
  --store C:\Dev\ZackBot2_data\binance_um_metrics_v1 `
  --out C:\Dev\ZackBot_cowork_data\oidiv1h_readiness\readiness.json
```

ROADMAP FIT: candidate 11 changes information source after ten rejected candidates.  
DEFERRED: strategy rules, Stage-A returns, folds, holdout, PAPER/testnet, runtime, mainnet, and any proxy substitution.
