# All-account overview: owner decision, 12 Nov 2026

Port: 8769, bound only to 127.0.0.1.

NOW: preserve the separate read-only overview. Finish the outstanding trading gates and first prospective FWD-01 row before selector work.
LATER, low priority: add All accounts / Account 1–4 selection within the existing ZackBot UI. Preserve charts, history and controls. Do not replace the original UI.

The overview and future selector may only read account data. All trade or settings actions remain in each account’s own existing controls. The only current POST route opens that account’s existing browser controls; it does not forward trading or settings requests. No restart, process attachment, code/settings changes or position changes to account 1. Never expose API keys, secrets or local authentication tokens.

Reads: GET /api/status on loopback ports 8765–8768; session.json for existing local session authentication; install.json mode and testnet-host verification. Displays only selected capital, realized/unrealized P&L, position/risk/protection, entry pause state, engine/exchange health and timestamps. Does not read config.env or exchange API keys. UI refresh interval 15 seconds while open. API output is an allowlist.

Own service/sign-in task: ZackBot-All-Accounts-Overview. It starts only the hub. Open-account buttons start only a browser window in the selected account’s existing isolated profile, never its bot process.
