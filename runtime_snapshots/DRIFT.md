# On-disk source drift from master

Baseline `42ef787f6b6e8c9363705b76df13cdb487f840f0`. Scope: top-level source + newcore/ml_shadow and captured launchers/overlays. Counts compare original on-disk bytes before redaction; no secret-bearing diff text is emitted. Risk is conservative triage, not semantic acceptance.

| Account | File | Nature | Risk |
|---|---|---|---|
| acct1 | `source/app.py` | modified; +0/-12; 15 redactions | HIGH: engine/exchange/configuration boundary |
| acct1 | `source/backtest.py` | modified; +2/-9; 0 redactions | MEDIUM: launch/UI/research/provenance; review before deployment |
| acct1 | `source/binance_client.py` | modified; +19/-107; 1 redactions | HIGH: engine/exchange/configuration boundary |
| acct1 | `source/build_info.py` | runtime-only; +1/-0; 0 redactions | MEDIUM: launch/UI/research/provenance; review before deployment |
| acct1 | `source/engine.py` | modified; +11/-74; 36 redactions | HIGH: engine/exchange/configuration boundary |
| acct1 | `source/installer_check.ps1` | modified; +0/-0; 0 redactions | MEDIUM: launch/UI/research/provenance; review before deployment |
| acct1 | `source/lab.py` | modified; +2/-9; 9 redactions | MEDIUM: launch/UI/research/provenance; review before deployment |
| acct1 | `source/newcore/ports/__init__.py` | modified; +0/-1; 0 redactions | MEDIUM: launch/UI/research/provenance; review before deployment |
| acct1 | `source/panel.html` | modified; +0/-2; 0 redactions | MEDIUM: launch/UI/research/provenance; review before deployment |
| acct1 | `source/strategies.py` | modified; +0/-823; 7 redactions | HIGH: engine/exchange/configuration boundary |
| acct1 | `source/strategy_shadow.py` | modified; +1/-1; 4 redactions | MEDIUM: launch/UI/research/provenance; review before deployment |
| acct1 | `source/telegram_ctl.py` | modified; +1/-2; 2 redactions | MEDIUM: launch/UI/research/provenance; review before deployment |
| acct1 | `source/build_app.bat` | master-only; absent from loose source | MEDIUM: deployment coverage; may be build/test-only |
| acct1 | `source/candidate_activation.py` | master-only; absent from loose source | MEDIUM: deployment coverage; may be build/test-only |
| acct1 | `source/h5_hourly_long.py` | master-only; absent from loose source | MEDIUM: deployment coverage; may be build/test-only |
| acct1 | `source/installer.ps1` | master-only; absent from loose source | MEDIUM: deployment coverage; may be build/test-only |
| acct1 | `source/newcore/account_context.py` | master-only; absent from loose source | MEDIUM: deployment coverage; may be build/test-only |
| acct1 | `source/newcore/base_clean.py` | master-only; absent from loose source | MEDIUM: deployment coverage; may be build/test-only |
| acct1 | `source/newcore/loss_state.py` | master-only; absent from loose source | MEDIUM: deployment coverage; may be build/test-only |
| acct1 | `source/newcore/management.py` | master-only; absent from loose source | MEDIUM: deployment coverage; may be build/test-only |
| acct1 | `source/newcore/observability.py` | master-only; absent from loose source | MEDIUM: deployment coverage; may be build/test-only |
| acct1 | `source/newcore/ports/rate_blocks.py` | master-only; absent from loose source | MEDIUM: deployment coverage; may be build/test-only |
| acct1 | `source/newcore/reconciliation.py` | master-only; absent from loose source | MEDIUM: deployment coverage; may be build/test-only |
| acct1 | `source/newcore/risk.py` | master-only; absent from loose source | MEDIUM: deployment coverage; may be build/test-only |
| acct1 | `source/newcore/scheduler.py` | master-only; absent from loose source | MEDIUM: deployment coverage; may be build/test-only |
| acct1 | `source/newcore/simulator.py` | master-only; absent from loose source | MEDIUM: deployment coverage; may be build/test-only |
| acct1 | `source/newcore/testnet_harness.py` | master-only; absent from loose source | MEDIUM: deployment coverage; may be build/test-only |
| acct1 | `source/newcore/venue/__init__.py` | master-only; absent from loose source | MEDIUM: deployment coverage; may be build/test-only |
| acct1 | `source/newcore/venue/binance_usdm_testnet.py` | master-only; absent from loose source | MEDIUM: deployment coverage; may be build/test-only |
| acct1 | `source/newcore/venue/http_sender.py` | master-only; absent from loose source | MEDIUM: deployment coverage; may be build/test-only |
| acct1 | `source/paper_cohort_cli.py` | master-only; absent from loose source | MEDIUM: deployment coverage; may be build/test-only |
| acct1 | `source/prot.py` | master-only; absent from loose source | MEDIUM: deployment coverage; may be build/test-only |
| acct1 | `source/rollback_drill.bat` | master-only; absent from loose source | MEDIUM: deployment coverage; may be build/test-only |
| acct1 | `source/setup_git.bat` | master-only; absent from loose source | MEDIUM: deployment coverage; may be build/test-only |
| acct2 | `source/app.py` | modified; +0/-4; 15 redactions | HIGH: engine/exchange/configuration boundary |
| acct2 | `source/backtest.py` | modified; +2/-9; 0 redactions | MEDIUM: launch/UI/research/provenance; review before deployment |
| acct2 | `source/binance_client.py` | modified; +19/-107; 1 redactions | HIGH: engine/exchange/configuration boundary |
| acct2 | `source/engine.py` | modified; +4/-57; 36 redactions | HIGH: engine/exchange/configuration boundary |
| acct2 | `source/panel.before-account-confirm.html` | runtime-only; +2097/-0; 0 redactions | MEDIUM: launch/UI/research/provenance; review before deployment |
| acct2 | `source/panel.html` | modified; +28/-1; 0 redactions | MEDIUM: launch/UI/research/provenance; review before deployment |
| acct2 | `source/strategy_shadow.py` | modified; +1/-1; 4 redactions | MEDIUM: launch/UI/research/provenance; review before deployment |
| acct2 | `launcher/start_old3.py` | runtime-only; +54/-0; 0 redactions | MEDIUM: launch/UI/research/provenance; review before deployment |
| acct2 | `source/build_app.bat` | master-only; absent from loose source | MEDIUM: deployment coverage; may be build/test-only |
| acct2 | `source/h5_hourly_long.py` | master-only; absent from loose source | MEDIUM: deployment coverage; may be build/test-only |
| acct2 | `source/installer.ps1` | master-only; absent from loose source | MEDIUM: deployment coverage; may be build/test-only |
| acct2 | `source/installer_check.ps1` | master-only; absent from loose source | MEDIUM: deployment coverage; may be build/test-only |
| acct2 | `source/prot.py` | master-only; absent from loose source | MEDIUM: deployment coverage; may be build/test-only |
| acct2 | `source/rollback_drill.bat` | master-only; absent from loose source | MEDIUM: deployment coverage; may be build/test-only |
| acct2 | `source/run_checks.bat` | master-only; absent from loose source | MEDIUM: deployment coverage; may be build/test-only |
| acct2 | `source/run_ui_baseline.bat` | master-only; absent from loose source | MEDIUM: deployment coverage; may be build/test-only |
| acct2 | `source/setup_git.bat` | master-only; absent from loose source | MEDIUM: deployment coverage; may be build/test-only |
| acct2 | `source/verify.bat` | master-only; absent from loose source | MEDIUM: deployment coverage; may be build/test-only |
| acct2 | `source/verify_release.bat` | master-only; absent from loose source | MEDIUM: deployment coverage; may be build/test-only |
| acct3 | `source/binance_client.py` | modified; +19/-107; 1 redactions | HIGH: engine/exchange/configuration boundary |
| acct3 | `source/engine.py` | modified; +3/-47; 36 redactions | HIGH: engine/exchange/configuration boundary |
| acct3 | `source/panel.html` | modified; +0/-0; 0 redactions | MEDIUM: launch/UI/research/provenance; review before deployment |
| acct3 | `launcher/start_account3.py` | runtime-only; +58/-0; 1 redactions | MEDIUM: launch/UI/research/provenance; review before deployment |
| acct3 | `source/build_app.bat` | master-only; absent from loose source | MEDIUM: deployment coverage; may be build/test-only |
| acct3 | `source/h5_hourly_long.py` | master-only; absent from loose source | MEDIUM: deployment coverage; may be build/test-only |
| acct3 | `source/installer.ps1` | master-only; absent from loose source | MEDIUM: deployment coverage; may be build/test-only |
| acct3 | `source/installer_check.ps1` | master-only; absent from loose source | MEDIUM: deployment coverage; may be build/test-only |
| acct3 | `source/rollback_drill.bat` | master-only; absent from loose source | MEDIUM: deployment coverage; may be build/test-only |
| acct3 | `source/run_checks.bat` | master-only; absent from loose source | MEDIUM: deployment coverage; may be build/test-only |
| acct3 | `source/run_ui_baseline.bat` | master-only; absent from loose source | MEDIUM: deployment coverage; may be build/test-only |
| acct3 | `source/setup_git.bat` | master-only; absent from loose source | MEDIUM: deployment coverage; may be build/test-only |
| acct3 | `source/verify.bat` | master-only; absent from loose source | MEDIUM: deployment coverage; may be build/test-only |
| acct3 | `source/verify_release.bat` | master-only; absent from loose source | MEDIUM: deployment coverage; may be build/test-only |
| acct4 | `overlay/r6_account4_runtime_gate.py` | runtime-only; +97/-0; 0 redactions | HIGH: engine/exchange/configuration boundary |
| acct4 | `overlay/r6_panel_client.py` | runtime-only; +28/-0; 1 redactions | MEDIUM: launch/UI/research/provenance; review before deployment |
| acct4 | `overlay/r6_readonly_monitor.py` | runtime-only; +173/-0; 3 redactions | MEDIUM: launch/UI/research/provenance; review before deployment |
| acct4 | `source/app.py` | modified; +0/-4; 15 redactions | HIGH: engine/exchange/configuration boundary |
| acct4 | `source/backtest.py` | modified; +2/-9; 0 redactions | MEDIUM: launch/UI/research/provenance; review before deployment |
| acct4 | `source/engine.py` | modified; +1/-10; 36 redactions | HIGH: engine/exchange/configuration boundary |
| acct4 | `source/ml_shadow/ml_shadow/common.py` | runtime-only; +79/-0; 0 redactions | MEDIUM: launch/UI/research/provenance; review before deployment |
| acct4 | `source/ml_shadow/ml_shadow/context.py` | runtime-only; +134/-0; 0 redactions | MEDIUM: launch/UI/research/provenance; review before deployment |
| acct4 | `source/ml_shadow/ml_shadow/features.py` | runtime-only; +151/-0; 0 redactions | MEDIUM: launch/UI/research/provenance; review before deployment |
| acct4 | `source/ml_shadow/ml_shadow/labels.py` | runtime-only; +43/-0; 0 redactions | MEDIUM: launch/UI/research/provenance; review before deployment |
| acct4 | `source/ml_shadow/ml_shadow/ledger.py` | runtime-only; +164/-0; 0 redactions | MEDIUM: launch/UI/research/provenance; review before deployment |
| acct4 | `source/ml_shadow/ml_shadow/pairs.py` | runtime-only; +433/-0; 6 redactions | MEDIUM: launch/UI/research/provenance; review before deployment |
| acct4 | `source/ml_shadow/ml_shadow/sanitize.py` | runtime-only; +167/-0; 1 redactions | MEDIUM: launch/UI/research/provenance; review before deployment |
| acct4 | `source/ml_shadow/ml_shadow/scoring.py` | runtime-only; +37/-0; 0 redactions | MEDIUM: launch/UI/research/provenance; review before deployment |
| acct4 | `source/ml_shadow/ml_shadow/trainer.py` | runtime-only; +239/-0; 0 redactions | MEDIUM: launch/UI/research/provenance; review before deployment |
| acct4 | `source/panel.html` | modified; +1/-1; 0 redactions | MEDIUM: launch/UI/research/provenance; review before deployment |
| acct4 | `source/r6_paper_overlay.py` | runtime-only; +570/-0; 0 redactions | HIGH: engine/exchange/configuration boundary |
| acct4 | `source/r6_regime_flip.py` | runtime-only; +358/-0; 7 redactions | MEDIUM: launch/UI/research/provenance; review before deployment |
| acct4 | `source/strategy_shadow.py` | modified; +1/-1; 4 redactions | MEDIUM: launch/UI/research/provenance; review before deployment |
| acct4 | `launcher/start_account4.py` | runtime-only; +69/-0; 1 redactions | MEDIUM: launch/UI/research/provenance; review before deployment |
| acct4 | `source/build_app.bat` | master-only; absent from loose source | MEDIUM: deployment coverage; may be build/test-only |
| acct4 | `source/h5_hourly_long.py` | master-only; absent from loose source | MEDIUM: deployment coverage; may be build/test-only |
| acct4 | `source/installer.ps1` | master-only; absent from loose source | MEDIUM: deployment coverage; may be build/test-only |
| acct4 | `source/installer_check.ps1` | master-only; absent from loose source | MEDIUM: deployment coverage; may be build/test-only |
| acct4 | `source/prot.py` | master-only; absent from loose source | MEDIUM: deployment coverage; may be build/test-only |
| acct4 | `source/rollback_drill.bat` | master-only; absent from loose source | MEDIUM: deployment coverage; may be build/test-only |
| acct4 | `source/run_checks.bat` | master-only; absent from loose source | MEDIUM: deployment coverage; may be build/test-only |
| acct4 | `source/run_ui_baseline.bat` | master-only; absent from loose source | MEDIUM: deployment coverage; may be build/test-only |
| acct4 | `source/setup_git.bat` | master-only; absent from loose source | MEDIUM: deployment coverage; may be build/test-only |
| acct4 | `source/verify.bat` | master-only; absent from loose source | MEDIUM: deployment coverage; may be build/test-only |
| acct4 | `source/verify_release.bat` | master-only; absent from loose source | MEDIUM: deployment coverage; may be build/test-only |
