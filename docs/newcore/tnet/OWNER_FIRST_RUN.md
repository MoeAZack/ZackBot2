# NEWCORE testnet: owner's first run

> **DO NOT RUN until Claude Code says Cowork's re-check and Codex's review both PASSED** and gives you the exact SHA.

**What this is:** these steps check the new bot against the Binance Futures **testnet** only.

**The first run is exactly: steps 1-4 (key check, dry runs, read-only smoke, probes P1 and P2) and step 5a.**
Position management stays **OFF**, REC-02 is **not used**, T04-algo is optional and run separately, and the
management brackets (5b) are **deferred** to a later run.

- **Testnet only.** Every command talks only to `testnet.binancefuture.com`. That address is hard-coded in the code and cannot be changed by any option.
- **Mainnet is impossible.** These tools cannot reach mainnet. Mainnet keys are refused when you store them.
- **Disposable funds.** Testnet money is not real. Your MT5 account, Bybit and the old ZackBot are never touched.
- **Nothing to type secretly.** Your key is never typed on a command line and never printed. It stays encrypted in Windows (DPAPI) under `%LOCALAPPDATA%\ZackBotNC\secrets`.

Your account and config (both already set up and not secret):

- AccountId: `acct_be532e3bdaafa0a098818433891ce15a`
- Binding digest: `7d9bfb85502ca3be`
- Config: `%LOCALAPPDATA%\ZackBotNC\config\testnet.toml` (symbols BTCUSDT and ETHUSDT). The tools only read it, never change it.

Before you start, check that the testnet account is in **Hedge mode** and holds **at least 100 USDT** of testnet funds.

The testnet API key you store should have **read** and **futures trading** permission only. **Withdrawals must stay disabled**, and so must every other permission (transfers, margin, options): the tools never need them. If the testnet site offers an IP restriction, you may set it to your PC's address. Never use a mainnet key (it is refused anyway).

---

## 0. Get the exact code (once)

Open **PowerShell**. Leave `C:\Dev\ZackBot2_keys` alone: that is your key-tools copy. Make a separate, clean copy:

```powershell
cd C:\Dev
git clone https://github.com/MoeAZack/ZackBot2.git ZackBot2_tnet
cd C:\Dev\ZackBot2_tnet
git checkout --detach <SHA from Claude Code>
python --version
```

`python --version` must print 3.11 or newer. If `python` is not found, use `py -3` in every command below instead.

**Run every command below from `C:\Dev\ZackBot2_tnet`.**

## 1. Key check (a few seconds)

```powershell
python tools\newcore_keys.py status --account-id acct_be532e3bdaafa0a098818433891ce15a
```

Expected: the key is stored, with `binding : 7d9bfb85502ca3be`. Exit 0. (This tool has its own codes: 4 = no usable key, 3 = mainnet key refused.)
If it says no key: store it with `python tools\newcore_keys.py set --env testnet --account-id acct_be532e3bdaafa0a098818433891ce15a` (hidden prompts), then repeat the check.

## 2. Dry runs (instant; no network, no key used)

```powershell
python tools\newcore_smoke.py --account-id acct_be532e3bdaafa0a098818433891ce15a --dry-run
python tools\newcore_tnet.py --probe P1 --probe P2 --dry-run
python tools\newcore_tnet_runner.py --target testnet --dry-run
```

Expected: each prints a PLAN and exits 0. The last two show `config ... account acct_be53... binding 7d9bfb85502ca3be`.

The last two use your `testnet.toml` (step 0's config path). It must contain `mode = "TESTNET"`, and yours already
does. If either prints a `REFUSED: --config` line, stop and send the output; do not edit the
file yourself. The tools only read it.

## 3. Read-only smoke (under 1 minute; sends NO orders)

```powershell
python tools\newcore_smoke.py --account-id acct_be532e3bdaafa0a098818433891ce15a
```

Expected: every read OK, flat, hedge mode, exit 0. Exit 1 means warnings only.

## 4. Probes P1 and P2 (about 2-5 minutes; minimum-size orders)

```powershell
python tools\newcore_tnet.py --probe P1
python tools\newcore_tnet.py --probe P2
```

Expected: `preflight OK`, then `P1: conclusive` (and `P2: conclusive`), `CLEANUP CLEAN`, a `report:` line, and exit 0.

## 5. Runner scenarios: the first run is 5a ONLY

**What Ctrl+C does.** You can stop any step with Ctrl+C.

- **Before any order was sent** (start-up checks): it simply stops with exit 6. There may be no report, because nothing happened.
- **After an order was sent:** the cleanup still runs (a second Ctrl+C cannot stop it). The report is written in full, and the exit code is 6, or 8 if something may be left.
- **While a report or cassette file is being saved:** the file is finished first, then the run stops. No further scenario starts and no further order is sent. Exit 6 (or 8).
- **During `--cleanup`** (step 6): if you press Ctrl+C while it is still listing the account, nothing has been sent and nothing was cleaned. Run `--cleanup` again. If you press it while the cleanup itself runs, the cleanup finishes first and its `CLEANUP` line tells you the result: `CLEANUP CLEAN` means nothing is left (exit 6), `NOT CLEAN` means run `--cleanup` again (exit 8).

**5a. Core scenarios (about 35-45 minutes). This, with the probes, is the whole first run.**

- Management: **OFF**. None of these scenarios uses position management.
- REC-02: **not used**.

```powershell
python tools\newcore_tnet_runner.py --target testnet --only T01-long --only T02-short --only T03 --only T09 --only T10-floor --only T10-refused --only T11 --only T12-flat --only T12-protected
```

Expected: one `PASS` line per scenario, a `report:` line, and exit 0.

**Always copy the command exactly, with every `--only`.** Running `--target testnet` without `--only` would also run
T04-algo and the management brackets (5b), which are NOT part of the first run.

**Optional, separate, only when Claude Code says so: T04-algo stop-exit bracket (30 minutes up to about 1.5 hours).**

This one is transport-only: no management. It waits for the market to hit a tight stop and tries up to 3 times. `INCONCLUSIVE` only means the market did not move far enough.

```powershell
python tools\newcore_tnet_runner.py --target testnet --only T04-algo
```

**5b. Management brackets (T05 / T06 / T07): NOT part of the first run.** They turn position management on, and they come in a later run after the REC-02 work. Do not run them now.

## 6. Only if something was left: cleanup

```powershell
python tools\newcore_tnet.py --cleanup
```

This cancels NEWCORE orders only and lists any positions it finds. It never touches orders you placed yourself.

If it lists NEWCORE positions that are still open, close them with:

```powershell
python tools\newcore_tnet.py --cleanup --close-positions
```

This closes every position on BTCUSDT and ETHUSDT. If you hold a position of your OWN there that must stay, name it
with its exact quantity (`SYMBOL:SIDE:QTY`); only the rest is closed:

```powershell
python tools\newcore_tnet.py --cleanup --close-positions --adopt-foreign BTCUSDT:LONG:0.002
```

Without the `:QTY` part, `--close-positions` refuses to run when a position is adopted, and a QTY larger than the
open position is refused too. Nothing is sent in either case.

---

## Exit codes (smoke, newcore_tnet, newcore_tnet_runner)

| Code | Meaning | What you do |
|---|---|---|
| 0 | Passed, account clean | Send the report paths |
| 1 | Inconclusive or warnings; safe | Send the report paths |
| 2 | Wrong command or option; nothing was sent | Check the command, or send the output |
| 3 | No usable key, or the key does not match the binding | Step 1 |
| 4 | Preflight refused (not flat / not hedge mode / foreign orders / low balance) or a read failed; nothing was sent (also when Ctrl+C was pressed at the same time) | Fix it in the testnet UI, or send the output |
| 5 | The report or cassette could not be written | Send the output |
| 6 | Stopped by time limit or Ctrl+C. If orders had been sent, the cleanup ran and the report was written. If it was stopped before anything was sent (start-up checks, or `--cleanup` while it was still listing the account), nothing was sent and nothing was cleaned | Send the output and report paths (if any). After a stopped `--cleanup`, run it again |
| 7 | A scenario FAILED; the cleanup ran (also when Ctrl+C was pressed after that scenario had finished: the failure is what counts) | Send the report paths |
| 8 | **Something MAY be left on the testnet account** | See below |

**On exit 8:**

1. Open the testnet site, go to Positions and Open Orders, and look for the client ids that were printed (they start with `zbn1`).
2. Run `python tools\newcore_tnet.py --cleanup`.
3. If NEWCORE positions are still open, run `python tools\newcore_tnet.py --cleanup --close-positions`. This closes every position on BTCUSDT and ETHUSDT. To keep a position of your OWN (not NEWCORE), add it with its quantity: `--adopt-foreign SYMBOL:SIDE:QTY`, for example `--adopt-foreign BTCUSDT:LONG:0.002` keeps 0.002 BTC long and closes only the rest.
4. Repeat step 2 until it exits 0.

**What to look for on a scenario line:**

- `PASS` / `FAIL` / `INCONCLUSIVE` is the verdict.
- `degraded=SYMBOL:SIDE:how` means the bot could not place its normal protective stop and used a fallback: either a stop placed further from the current price, or a reduce-only market close. It only happens when the bot cannot trust its own records (a "hard hold"), which these tests do not set up, so it should not appear. If it does, send that report even when the line says PASS.

## Run notes: what this build does NOT do yet

- **The first run is steps 1-4 plus 5a, nothing else.** T04-algo is optional and separate; the management brackets
  (5b) and REC-02 come in a later run.
- **Position management is OFF** in every first-run scenario: no targets, partial exits, adds or trailing.
- **Funding in hedge mode:** Binance's funding rows do not say which side (LONG or SHORT) they belong to. If both sides
  of one symbol were open at once, the bot would count all of that symbol's funding against the side it asks about.
  The first-run scenarios open one side per symbol at a time, so this does not change any verdict.
- **Old journals are refused:** a journal written by a NEWCORE build from before the record freeze is not migrated.
  The bot refuses it and only guards its own proven exposure. The first run starts every scenario with a fresh
  journal, so you will not see this.
- **No fresh mark price:** if the mark price cannot be read, or is older than 30 seconds by Binance's clock, the
  bot does not use a stale price for a fallback stop. It closes with a reduce-only market order instead, and the line
  shows `degraded=` (see above).
- **Restart long after a lost entry (NEW A):** if the last records of the bot's journal are lost right after an entry
  (for example a crash or a power cut) and it is restarted more than 60 candles later, it may not be able to prove that
  entry is its own. The position may then be left without a stop,
  in a loud HOLD with an incident; the bot does not guess. Nothing in the first run restarts that late. If you ever
  see such a HOLD, close the position in the testnet UI and send the output.
- **More S1 limits may be listed here** before Claude Code gives you the SHA: the S1 work is still closing items.
  Use the version of this file at that SHA.

## What to send back

- The full console output of each step. Keys are never printed in it.
- The report paths printed after `report:`. They are under `%LOCALAPPDATA%\ZackBotNC\reports\` (`.json`, `.md` and `.scenarios.json`).
- The cassette folder `%LOCALAPPDATA%\ZackBotNC\cassettes\`. These files are sanitized; still keep them local until Claude Code asks for them.

**Never send** your API key, your secret, or anything from `%LOCALAPPDATA%\ZackBotNC\secrets`.
