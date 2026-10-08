"""TNET-01 runner-driven scenario harness (plan docs/newcore/REC02_TNET01_BUILD_PLAN.md section 6).

The Runner itself (newcore/runner) is driven through T01-T04 and T09-T12 - the scenarios that need no management
(T05 partial close, T06 DCA, T07 cancel/replace race and T08 manual change wait for the ManagementDriver wiring):

    rspec.py      format zb-newcore-tnet-runner/1 (JSON), strict validation; bundled specs in specs/*.json
    signals.py    ScriptedSignals: ENTER / CLOSE on the next CLOSED 1m candle, keyed, name 'tnet_' + 8-hex run nonce
    seams.py      BoundedPort (order / notional caps + ledger), PortFaults (FakeVenue), HttpFaults (testnet HTTP seam)
    targets.py    FakeTarget (FakeVenue + MemoryJournal, CI) and TestnetTarget (newcore.venue.factory:build_testnet)
    driver.py     run_scenario / run_suite: one entry point for both targets; verdicts, final exchange truth (T12),
                  guarded cleanup, exit codes (plan 6.6)

Nothing here opens a network connection by itself: TestnetTarget takes the `http` callable (the CLI passes the real
TestnetHttpSender, the tests a fake Binance).
"""
