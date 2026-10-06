# T03c acceptance

*Accepted 2026-10-06 23:40 Africa/Cairo*

- Accepted Git head: `ee564c101ac6866acaaac4febcd0fddef3b8c199`.
- Review: no remaining P1/P2 finding; Codex focused Windows gate 291/291.
- GitHub: fast, CodeQL, tests, both strict replays, UI and exact-head merged full summary passed.
- Installed testnet build: `20261006-233104`; installed SHA-256 matches the verified new executable exactly.
- Runtime: deterministic SOL refusal canary recorded two failed requests, one accepted exposure decision, a real protected
  fill, 0.13x effective leverage and 0.15% worst-case margin ratio.
- Restart: exceptional lot and exchange stop were adopted with health clean.
- Cleanup: injector removed, normal SOL path restored the configured leverage, canary closed, no orphan or untracked state.
- Final account state: PAPER/testnet, engine and exchange OK, three original protected lots, zero errors/unprotected/
  untracked/orphans.

T03c is accepted for merge through protected `master`. Mainnet remains prohibited pending the final release audit and the
owner's explicit mainnet approval.
