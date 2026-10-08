"""`python -m newcore.run`: the runnable entry point (M2 testnet smoke). PAPER / TESTNET only.

    python -m newcore.run run    --config run.toml [--once | --cycles N] [--enable-candidate]
    python -m newcore.run replay --config run.toml [--symbols A,B] [--compare research.csv] [--enable-candidate]
                                 [--memory-journal]

run      fake: FakeVenue over data_long, one candle per cycle; its state is saved next to the journal after every cycle,
              so a later run (or a restart after a stop) resumes from the FileJournal + that state.
         testnet: the venue / bars / account reads come from the configured factory hook (testnet_hook.py); the cycle
              runs `delay_s` after each candle close of the wall clock.
         Each cycle prints one HEALTH line (Cairo time first) and rewrites trades.csv + incidents.jsonl (reports.py).
         --once = one cycle; SIGINT / SIGTERM (SIGBREAK on Windows) = finish the current cycle, write, exit 0.
replay   each symbol through the S1 Runner over data_long (start..end), a fresh FileJournal per symbol under
         <journal dir>/replay-<symbol> (or in memory), reports per symbol; --compare prints the match against the
         research trade list (the 224 / 224 check over the core 8).
The strategy (trend_ema_mom.v1) is DISABLED unless the config says enabled = true AND --enable-candidate is given.
Exit codes: 0 ok, 2 config refused, 3 store cannot write at boot, 4 store HOLD verdict, 5 store ABORT-RO.
"""
from __future__ import annotations

import argparse
import json
import os
import signal
import sys
import threading
import time
from decimal import Decimal

from newcore.adapters import CostModel, CsvBarSource, FakeVenue, MemoryJournal, load_rules
from newcore.adapters.csv_bars import TF_MS, parse_utc_ms
from newcore.ports.venue import ReadKind
from newcore.domain import (Account, AccountBinding, BindingConfirmation, BindingState, Environment, Venue,
                            confirmation_phrase)
from newcore.risk import BookPolicy
from newcore.store import Verdict, create_journal, recover_journal
from newcore.strategy import Params

from . import config as C
from .compare import compare
from .intrabar import play_candle
from .managed import ManagedBookRunner, ManagedRunner, ManagementConfig, RangeFixturePlans
from .replay import run_replay
from .reports import health_line, write_reports
from .runner import RunnerConfig
from .signals import EmaMomSignals, NoSignals
from .sizing import SizingPolicy

BASE_COSTS = CostModel(taker_fee=Decimal('0.0005'), slip=Decimal('0.0002'), funding_per_bar=Decimal('0.00005'))
STATE_FILE = 'fake_venue.json'
EXIT_CONFIG, EXIT_STORE_DOWN, EXIT_STORE_HOLD, EXIT_ABORT_RO = 2, 3, 4, 5


class StoreRefused(Exception):
    def __init__(self, code, text):
        super().__init__(text)
        self.code = code


class StopFlag:
    """Clean shutdown: set by SIGINT / SIGTERM (or a test); the loop finishes its cycle, writes and exits."""

    def __init__(self):
        self._e = threading.Event()

    def set(self, *_):
        self._e.set()

    def is_set(self):
        return self._e.is_set()

    def wait(self, seconds):
        return self._e.wait(seconds)

    def install(self):
        for name in ('SIGINT', 'SIGTERM', 'SIGBREAK'):
            sig = getattr(signal, name, None)
            if sig is not None:
                try:
                    signal.signal(sig, self.set)
                except (ValueError, OSError):                       # not the main thread: the caller owns signals
                    pass


# ---------------------------------------------------------------------------------------------------- building
def account(cfg):
    env = Environment.SIM if cfg.mode == 'PAPER' else Environment.TESTNET
    binding = AccountBinding(venue=Venue.BINANCE_USDM, environment=env, settlement_asset='USDT',
                             key_digest=cfg.key_digest, exchange_uid=None)
    conf = BindingConfirmation(account_id=cfg.account_id, old_key_digest=None, new_key_digest=cfg.key_digest,
                               typed_phrase=confirmation_phrase(cfg.account_id, cfg.key_digest),
                               confirmed_at_ms=946_684_800_000)
    return Account(account_id=cfg.account_id, label='newcore-run', hedge_mode=True, binding=binding,
                   binding_state=BindingState.CONFIRMED, proposed_binding=None, confirmation=conf)


def open_journal(account_dir, cfg):
    """FileJournal of the account: created on the first run, recovered (CLEAN / REPAIRED) on every later one."""
    jd = os.path.join(account_dir, 'journal')
    if not os.path.isdir(jd) or not os.listdir(jd):
        return create_journal(account_dir, cfg.account_id, cfg.portfolio_id)
    r = recover_journal(account_dir, cfg.account_id, cfg.portfolio_id)
    if r.verdict in (Verdict.CLEAN, Verdict.REPAIRED):
        return r.journal
    if r.verdict is Verdict.DURABILITY_UNAVAILABLE:
        raise StoreRefused(EXIT_STORE_DOWN, f'store cannot write at boot ({r.findings}): hard HOLD needs the '
                                            'emergency set with a read-only journal view (NC-02a gap)')
    if r.verdict is Verdict.ABORT_RO:
        raise StoreRefused(EXIT_ABORT_RO, f'journal of an unknown / future format: ABORT-RO ({r.findings})')
    raise StoreRefused(EXIT_STORE_HOLD, f'journal {r.verdict}: HOLD, nothing is run ({r.findings})')


def signals_for(cfg, enabled):
    tf_ms = TF_MS[cfg.tf]
    if not enabled:
        return NoSignals(tf_label=cfg.tf)
    return EmaMomSignals(tf_ms, cfg.tf, params=Params(enable_short=cfg.mirrored_short))


def sides_for(cfg):
    return ('LONG', 'SHORT') if cfg.mirrored_short else ('LONG',)


def policy_for(cfg):
    return BookPolicy(risk_pct=cfg.risk_pct, max_positions=cfg.max_positions, max_leverage=cfg.max_leverage,
                      cap_gap_buffer=cfg.cap_gap_buffer, daily_loss_pct=cfg.daily_loss_pct,
                      kill_drawdown_pct=cfg.kill_drawdown_pct)


def management_for(cfg):
    """[management]: OFF by default (the runner is then exactly the unmanaged one). The only plan is RANGE-BB-MR.v1, a
    disabled mechanics fixture costed like the replay venue."""
    from newcore.management import CostModel as PlanCosts
    plans = RangeFixturePlans(costs=PlanCosts(taker_fee=BASE_COSTS.taker_fee, slip=BASE_COSTS.slip),
                              cap_mult=cfg.mg_cap_mult)
    return ManagementConfig(enabled=cfg.mg_enabled, plans=plans)


def data_source(cfg, symbols):
    return CsvBarSource.from_data_long(cfg.data_root, list(symbols), cfg.tf)


def rules_for(cfg, symbols):
    return load_rules(os.path.join(cfg.data_root, 'data', 'exchange_rules_testnet.json'), list(symbols))


class Session:
    """One account: its journal, venue, bars, Runner and the cycle clock."""

    def __init__(self, cfg, enabled):
        self.cfg = cfg
        self.tf_ms = TF_MS[cfg.tf]
        self.account_dir = os.path.join(cfg.journal_dir, cfg.account_id)
        os.makedirs(cfg.journal_dir, exist_ok=True)
        self.journal = open_journal(self.account_dir, cfg)
        if cfg.venue_kind == 'fake':
            self.bars = data_source(cfg, cfg.symbols)
            candles = {s: self.bars.all_bars(s) for s in cfg.symbols}
            self.last_close = min(b[-1].close_ms for b in candles.values())
            path = os.path.join(self.account_dir, STATE_FILE)
            if os.path.exists(path):
                with open(path, encoding='utf-8') as fh:
                    self.venue = FakeVenue.from_state(candles, self.tf_ms, json.load(fh), costs=BASE_COSTS)
            else:
                start = parse_utc_ms(cfg.start) if cfg.start else max(b[0].open_ms for b in candles.values())
                self.venue = FakeVenue(candles, self.tf_ms, costs=BASE_COSTS, equity=cfg.equity, start_ms=start)
            reads, port, venue_rules = self.venue, self.venue, None
            self.reads = reads
        else:
            from .testnet_hook import build
            port, self.bars, reads, venue_rules = build(cfg)
            self.reads = reads
            self.venue, self.last_close = None, None
        rcfg = RunnerConfig(account=account(cfg), portfolio_id=cfg.portfolio_id, symbols=cfg.symbols,
                            tf_ms=self.tf_ms, timeframe=cfg.tf, rules=venue_rules or rules_for(cfg, cfg.symbols),
                            sizing=SizingPolicy(cfg.risk_pct, cfg.max_leverage, cfg.cap_gap_buffer),
                            sides=sides_for(cfg), strict=False,
                            raw_qty=cfg.tnet_raw_qty if cfg.tnet_enabled else None)
        self.runner = ManagedBookRunner(rcfg, policy=policy_for(cfg), journal=self.journal, venue=port,
                                        bars=self.bars, signals=signals_for(cfg, enabled), account_reads=reads,
                                        management=management_for(cfg))

    def next_close(self, wall_ms=None):
        """The candle close of the next cycle, or None when the fake data is exhausted."""
        if self.venue is not None:
            t = self.venue.now_ms + self.tf_ms
            return t if t <= self.last_close else None
        now = int(time.time() * 1000) if wall_ms is None else wall_ms
        return now - now % self.tf_ms

    def cycle(self, t):
        if self.venue is not None:
            if self.cfg.mg_enabled:                               # management: intra-candle marks (zb-path/1)
                play_candle(self.venue, self.runner, t)
            else:
                self.venue.advance_to(t)
            self.runner.cycle(t, decide=t < self.last_close)
        else:
            self.runner.cycle(t)

    def poll_marks(self, wall_ms=None):
        """Testnet, management enabled: between candle closes, read the owned orders and offer each symbol's mark
        price to the drivers (a target / add triggers at the mark, not at the next close). The mark read is the
        adapter's `mark_price(symbol)` (duck-typed; interface item for the TestnetVenue lane)."""
        if not self.cfg.mg_enabled or self.venue is not None:
            return 0
        read = getattr(self.reads, 'mark_price', None)
        if read is None:
            return 0
        now = int(time.time() * 1000) if wall_ms is None else wall_ms
        self.runner.intrabar_sync(now)
        n = 0
        for sym in self.cfg.symbols:
            r = read(sym)
            if r.kind is ReadKind.OK:
                n += self.runner.mark(sym, r.value[0], now)
        return n

    def save(self):
        if self.venue is not None:
            path = os.path.join(self.account_dir, STATE_FILE)
            tmp = path + '.tmp'
            with open(tmp, 'w', encoding='utf-8') as fh:
                json.dump(self.venue.to_state(), fh, sort_keys=True, separators=(',', ':'))
            os.replace(tmp, path)
        write_reports(self.runner, self.cfg.output_dir)

    def close(self):
        close = getattr(self.journal, 'close', None)
        if close is not None:
            close()


# ---------------------------------------------------------------------------------------------------- commands
def cmd_run(cfg, args, out, stop):
    enabled = cfg.enabled and args.enable_candidate
    if cfg.enabled and not args.enable_candidate:
        print('strategy enabled in the config but --enable-candidate not given: DISABLED (no entries)', file=out)
    s = Session(cfg, enabled)
    print(f'RUN mode={cfg.mode} venue={cfg.venue_kind} account={cfg.account_id} journal={s.account_dir} '
          f'strategy={"ON" if enabled else "off"} symbols={",".join(cfg.symbols)}', file=out)
    limit = 1 if args.once else args.cycles
    done = 0
    last = None
    polled = 0.0
    try:
        while limit is None or done < limit:
            t = s.next_close()
            if t is None:
                print('DATA END: no candle left to play', file=out)
                break
            if s.venue is None:                                     # testnet: once per candle close + delay
                if t == last or (time.time() * 1000) < t + cfg.delay_s * 1000:
                    if cfg.mark_poll_s and time.time() - polled >= cfg.mark_poll_s:
                        s.poll_marks()
                        polled = time.time()
                    if stop.wait(1.0):
                        break
                    continue
            s.cycle(t)
            last = t
            done += 1
            s.save()
            print(health_line(s.runner), file=out, flush=True)
            if stop.is_set():
                print('STOP: clean shutdown after the cycle', file=out)
                break
            if cfg.cadence_s and stop.wait(cfg.cadence_s):
                print('STOP: clean shutdown after the cycle', file=out)
                break
    finally:
        s.save()
        s.close()
    return 0


def cmd_replay(cfg, args, out, stop):
    if cfg.venue_kind != 'replay':
        raise C.ConfigError('replay needs venue.kind = "replay"')
    enabled = cfg.enabled and args.enable_candidate
    if not enabled:
        print('strategy disabled (needs enabled = true and --enable-candidate): the replay makes no trades', file=out)
    symbols = tuple(args.symbols.split(',')) if args.symbols else cfg.symbols
    total = [0, 0]
    for sym in symbols:
        src = data_source(cfg, [sym])
        bars = src.all_bars(sym)
        start = parse_utc_ms(cfg.start) if cfg.start else bars[0].open_ms
        end = parse_utc_ms(cfg.end) if cfg.end else bars[-1].close_ms
        venue = FakeVenue({sym: bars}, TF_MS[cfg.tf], costs=BASE_COSTS, equity=cfg.equity, start_ms=start)
        if args.memory_journal:
            journal = MemoryJournal(cfg.account_id, cfg.portfolio_id)
        else:
            os.makedirs(cfg.journal_dir, exist_ok=True)
            d = os.path.join(cfg.journal_dir, f'replay-{sym}')
            if os.path.exists(d):
                raise C.ConfigError(f'{d} exists: a replay writes a fresh journal (remove it or pick another dir)')
            journal = create_journal(d, cfg.account_id, cfg.portfolio_id)
        rcfg = RunnerConfig(account=account(cfg), portfolio_id=cfg.portfolio_id, symbols=(sym,), tf_ms=TF_MS[cfg.tf],
                            timeframe=cfg.tf, rules=rules_for(cfg, [sym]),
                            sizing=SizingPolicy(cfg.risk_pct, cfg.max_leverage, Decimal(0)), sides=sides_for(cfg))
        runner = ManagedRunner(rcfg, journal=journal, venue=venue, bars=src, signals=signals_for(cfg, enabled),
                               management=management_for(cfg))
        runner = run_replay(runner, venue, start_ms=start, end_ms=end, tf_ms=TF_MS[cfg.tf], intrabar=cfg.mg_enabled)
        write_reports(runner, os.path.join(cfg.output_dir, f'replay-{sym}'))
        s = runner.summary()
        print(f'REPLAY {sym}: {s.trades} trades, sum R {s.sum_r:.4f}, pnl {s.pnl:.2f}, '
              f'unprotected cycles {s.counters["unprotected_cycles"]}, mode {s.mode}', file=out, flush=True)
        if args.compare:
            c = compare(runner, sym, bars[0].open_ms, TF_MS[cfg.tf], args.compare)
            total[0] += c.matched
            total[1] += c.research
            print('COMPARE ' + c.line(), file=out, flush=True)
        if hasattr(journal, 'close'):
            journal.close()
        if stop.is_set():
            break
    if args.compare:
        print(f'COMPARE TOTAL {total[0]}/{total[1]}', file=out, flush=True)
        return 0 if total[0] == total[1] else 1
    return 0


def parser():
    p = argparse.ArgumentParser(prog='python -m newcore.run', description=__doc__.splitlines()[0])
    p.add_argument('command', nargs='?', default='run', choices=('run', 'replay'))
    p.add_argument('--config', required=True)
    p.add_argument('--enable-candidate', action='store_true', help='allow the candidate strategy to trade')
    g = p.add_mutually_exclusive_group()
    g.add_argument('--once', action='store_true', help='one cycle, then exit')
    g.add_argument('--cycles', type=int, default=None, help='at most N cycles')
    p.add_argument('--symbols', default='', help='replay: comma-separated subset')
    p.add_argument('--compare', default='', help='replay: research trade list CSV')
    p.add_argument('--memory-journal', action='store_true', help='replay: journal in memory')
    return p


def main(argv=None, *, out=None, stop=None):
    out = out or sys.stdout
    args = parser().parse_args(argv)
    try:
        cfg = C.load(args.config)
    except (C.ConfigError, OSError, ValueError) as ex:
        print(f'CONFIG REFUSED: {ex}', file=out)
        return EXIT_CONFIG
    if stop is None:
        stop = StopFlag()
        stop.install()
    try:
        return (cmd_run if args.command == 'run' else cmd_replay)(cfg, args, out, stop)
    except StoreRefused as ex:
        print(f'STORE: {ex}', file=out)
        return ex.code
    except C.ConfigError as ex:
        print(f'CONFIG REFUSED: {ex}', file=out)
        return EXIT_CONFIG
