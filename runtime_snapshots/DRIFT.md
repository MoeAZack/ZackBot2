# Captured-source drift from master

Compared against `42ef787f6b6e8c9363705b76df13cdb487f840f0`. Scope: top-level source files and newcore/ml_shadow, plus observed launchers. Every captured differing file and master-only file within that scope is listed below. Nature is byte-level; risk is a conservative triage category, not a semantic audit. Credential-screened files are unassessed (see manifest exclusions); no complete-drift claim is made.

| Account | File | Nature | Risk |
|---|---|---|---|
| acct1 | `source/app.py` | modified; +0/-12 lines | HIGH: configuration, lifecycle and API orchestration |
| acct1 | `source/backtest.py` | modified; +2/-9 lines | MEDIUM: economic evidence/parity |
| acct1 | `source/binance_client.py` | modified; +19/-107 lines | HIGH: exchange adapter and order semantics |
| acct1 | `source/build_info.py` | runtime-only; +1/-0 lines | MEDIUM: provenance/launch/UI behavior needs review |
| acct1 | `source/engine.py` | modified; +11/-74 lines | HIGH: order/protection and strategy execution |
| acct1 | `source/installer_check.ps1` | modified; +0/-0 lines | MEDIUM: provenance/launch/UI behavior needs review |
| acct1 | `source/lab.py` | modified; +2/-9 lines | MEDIUM: provenance/launch/UI behavior needs review |
| acct1 | `source/newcore/ports/__init__.py` | modified; +0/-1 lines | MEDIUM: provenance/launch/UI behavior needs review |
| acct1 | `source/panel.html` | modified; +0/-2 lines | MEDIUM: operator controls and presentation |
| acct1 | `source/strategies.py` | modified; +0/-823 lines | HIGH: signal generation and strategy economics |
| acct1 | `source/strategy_shadow.py` | modified; +1/-1 lines | MEDIUM: shadow research/evidence |
| acct1 | `source/build_app.bat` | master-only; absent from loose source | MEDIUM: deployment coverage differs; may be build/test-only |
| acct1 | `source/candidate_activation.py` | master-only; absent from loose source | MEDIUM: deployment coverage differs; may be build/test-only |
| acct1 | `source/h5_hourly_long.py` | master-only; absent from loose source | MEDIUM: deployment coverage differs; may be build/test-only |
| acct1 | `source/installer.ps1` | master-only; absent from loose source | MEDIUM: deployment coverage differs; may be build/test-only |
| acct1 | `source/newcore/account_context.py` | master-only; absent from loose source | MEDIUM: deployment coverage differs; may be build/test-only |
| acct1 | `source/newcore/base_clean.py` | master-only; absent from loose source | MEDIUM: deployment coverage differs; may be build/test-only |
| acct1 | `source/newcore/loss_state.py` | master-only; absent from loose source | MEDIUM: deployment coverage differs; may be build/test-only |
| acct1 | `source/newcore/management.py` | master-only; absent from loose source | MEDIUM: deployment coverage differs; may be build/test-only |
| acct1 | `source/newcore/observability.py` | master-only; absent from loose source | MEDIUM: deployment coverage differs; may be build/test-only |
| acct1 | `source/newcore/ports/rate_blocks.py` | master-only; absent from loose source | MEDIUM: deployment coverage differs; may be build/test-only |
| acct1 | `source/newcore/reconciliation.py` | master-only; absent from loose source | MEDIUM: deployment coverage differs; may be build/test-only |
| acct1 | `source/newcore/risk.py` | master-only; absent from loose source | MEDIUM: deployment coverage differs; may be build/test-only |
| acct1 | `source/newcore/scheduler.py` | master-only; absent from loose source | MEDIUM: deployment coverage differs; may be build/test-only |
| acct1 | `source/newcore/simulator.py` | master-only; absent from loose source | MEDIUM: deployment coverage differs; may be build/test-only |
| acct1 | `source/newcore/testnet_harness.py` | master-only; absent from loose source | MEDIUM: deployment coverage differs; may be build/test-only |
| acct1 | `source/newcore/venue/__init__.py` | master-only; absent from loose source | MEDIUM: deployment coverage differs; may be build/test-only |
| acct1 | `source/newcore/venue/binance_usdm_testnet.py` | master-only; absent from loose source | MEDIUM: deployment coverage differs; may be build/test-only |
| acct1 | `source/newcore/venue/http_sender.py` | master-only; absent from loose source | MEDIUM: deployment coverage differs; may be build/test-only |
| acct1 | `source/paper_cohort_cli.py` | master-only; absent from loose source | MEDIUM: deployment coverage differs; may be build/test-only |
| acct1 | `source/prot.py` | master-only; absent from loose source | MEDIUM: deployment coverage differs; may be build/test-only |
| acct1 | `source/rollback_drill.bat` | master-only; absent from loose source | MEDIUM: deployment coverage differs; may be build/test-only |
| acct1 | `source/setup_git.bat` | master-only; absent from loose source | MEDIUM: deployment coverage differs; may be build/test-only |
| acct2 | `source/app.py` | modified; +0/-4 lines | HIGH: configuration, lifecycle and API orchestration |
| acct2 | `source/backtest.py` | modified; +2/-9 lines | MEDIUM: economic evidence/parity |
| acct2 | `source/binance_client.py` | modified; +19/-107 lines | HIGH: exchange adapter and order semantics |
| acct2 | `source/engine.py` | modified; +4/-57 lines | HIGH: order/protection and strategy execution |
| acct2 | `source/panel.before-account-confirm.html` | runtime-only; +2097/-0 lines | MEDIUM: provenance/launch/UI behavior needs review |
| acct2 | `source/panel.html` | modified; +28/-1 lines | MEDIUM: operator controls and presentation |
| acct2 | `source/strategy_shadow.py` | modified; +1/-1 lines | MEDIUM: shadow research/evidence |
| acct2 | `launcher/start_old3.py` | runtime-only; +54/-0 lines | MEDIUM: provenance/launch/UI behavior needs review |
| acct2 | `source/build_app.bat` | master-only; absent from loose source | MEDIUM: deployment coverage differs; may be build/test-only |
| acct2 | `source/h5_hourly_long.py` | master-only; absent from loose source | MEDIUM: deployment coverage differs; may be build/test-only |
| acct2 | `source/installer.ps1` | master-only; absent from loose source | MEDIUM: deployment coverage differs; may be build/test-only |
| acct2 | `source/installer_check.ps1` | master-only; absent from loose source | MEDIUM: deployment coverage differs; may be build/test-only |
| acct2 | `source/prot.py` | master-only; absent from loose source | MEDIUM: deployment coverage differs; may be build/test-only |
| acct2 | `source/rollback_drill.bat` | master-only; absent from loose source | MEDIUM: deployment coverage differs; may be build/test-only |
| acct2 | `source/run_checks.bat` | master-only; absent from loose source | MEDIUM: deployment coverage differs; may be build/test-only |
| acct2 | `source/run_ui_baseline.bat` | master-only; absent from loose source | MEDIUM: deployment coverage differs; may be build/test-only |
| acct2 | `source/setup_git.bat` | master-only; absent from loose source | MEDIUM: deployment coverage differs; may be build/test-only |
| acct2 | `source/verify.bat` | master-only; absent from loose source | MEDIUM: deployment coverage differs; may be build/test-only |
| acct2 | `source/verify_release.bat` | master-only; absent from loose source | MEDIUM: deployment coverage differs; may be build/test-only |
| acct3 | `source/binance_client.py` | modified; +19/-107 lines | HIGH: exchange adapter and order semantics |
| acct3 | `source/engine.py` | modified; +3/-47 lines | HIGH: order/protection and strategy execution |
| acct3 | `source/panel.html` | modified; +0/-0 lines | MEDIUM: operator controls and presentation |
| acct3 | `launcher/start_account3.py` | runtime-only; +58/-0 lines | MEDIUM: provenance/launch/UI behavior needs review |
| acct3 | `source/build_app.bat` | master-only; absent from loose source | MEDIUM: deployment coverage differs; may be build/test-only |
| acct3 | `source/h5_hourly_long.py` | master-only; absent from loose source | MEDIUM: deployment coverage differs; may be build/test-only |
| acct3 | `source/installer.ps1` | master-only; absent from loose source | MEDIUM: deployment coverage differs; may be build/test-only |
| acct3 | `source/installer_check.ps1` | master-only; absent from loose source | MEDIUM: deployment coverage differs; may be build/test-only |
| acct3 | `source/rollback_drill.bat` | master-only; absent from loose source | MEDIUM: deployment coverage differs; may be build/test-only |
| acct3 | `source/run_checks.bat` | master-only; absent from loose source | MEDIUM: deployment coverage differs; may be build/test-only |
| acct3 | `source/run_ui_baseline.bat` | master-only; absent from loose source | MEDIUM: deployment coverage differs; may be build/test-only |
| acct3 | `source/setup_git.bat` | master-only; absent from loose source | MEDIUM: deployment coverage differs; may be build/test-only |
| acct3 | `source/verify.bat` | master-only; absent from loose source | MEDIUM: deployment coverage differs; may be build/test-only |
| acct3 | `source/verify_release.bat` | master-only; absent from loose source | MEDIUM: deployment coverage differs; may be build/test-only |
