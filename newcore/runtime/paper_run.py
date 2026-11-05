"""RT-02 PAPER run: wire the real NEWCORE runtime to the in-memory PAPER venue and drive it.

* ``init_paper_account`` creates an account's NC-02 journal (if absent) and journals a CONFIRMED **SIM** binding with
  the typed confirmation.  It refuses an account whose journal already carries a binding, so it can never rebind a
  testnet account to paper or the other way round.
* ``build_paper_ports`` gives each account a real NC-04 ``AccountContext`` over a ``PaperVenue`` (whose book is
  restored from ``<account_dir>/paper_book.json``), the journal projection, venue evidence and the journaling sink.
  Entries come from ``NoEntries``: no strategy source is wired yet.
* ``run_paper`` boots, then steps the runtime, ticks the venue (stops trigger against the marks file) and saves the
  book after every step, until ``max_steps``, a stop file or Ctrl-C; it then stops NC-05 with grace and closes.

PAPER only.  A testnet config is refused (``testnet_run_unavailable``) because the testnet open-orders read cannot
prove evidence yet; ``entries_enabled`` is refused (``entries_source_missing``) unless the caller passes an
``entries_factory`` (a launcher outside this package composes a strategy ``EntrySource``; the runtime package never
imports strategy code).  Nothing here reads a credential or
opens a socket.
"""
from __future__ import annotations

import hashlib
import inspect
import json
import os
import time
import uuid
from decimal import Decimal, InvalidOperation

from newcore.account_context import (ACCOUNT_ORDER_LIMIT_PATH, ACCOUNT_SOURCE, EXCHANGE_INFO_PATH, MARKET_DATA_SOURCE,
                                     SOURCE_RETRIEVED_ON, AccountBudget, AccountContext, LimitAuthority,
                                     RawLimitPayload)
from newcore.domain import BindingChanged, ReasonCode, make_id
from newcore.domain.account import (AccountBinding, BindingConfirmation, BindingState, Environment, Venue,
                                    confirmation_phrase)
from newcore.ports.values import check_symbol
from newcore.runtime.config import RuntimeEnvironment
from newcore.runtime.evidence import MAX_SYMBOLS, VenueEvidence
from newcore.runtime.journaling import JournalingActionSink
from newcore.runtime.paper import PaperVenue
from newcore.runtime.ports import AccountPorts
from newcore.runtime.projection import JournalProjection
from newcore.runtime.runtime import boot
from newcore.store import create_journal, recover_journal
from newcore.store.errors import JournalExists

BOOK_FILE = 'paper_book.json'
PAPER_KEY_DIGEST = hashlib.sha256(b'zackbot.newcore.paper').hexdigest()[:16]
STATUS_SCHEMA = 'zackbot.newcore.runtime.paper_status.v1'


class PaperRunError(ValueError):
    def __init__(self, code: str):
        self.code = code
        super().__init__(code)


def _mono_ms() -> int:
    return time.monotonic_ns() // 1_000_000


def _wall_ms() -> int:
    return time.time_ns() // 1_000_000


# ---------------------------------------------------------------------------------------------------- init
def paper_binding() -> AccountBinding:
    return AccountBinding(venue=Venue.BINANCE_USDM, environment=Environment.SIM, settlement_asset='USDT',
                          key_digest=PAPER_KEY_DIGEST, exchange_uid=None)


def init_paper_account(spec, *, wall_ms: int, event_n: int | None = None) -> str:
    """Create the journal if needed and journal the SIM binding.  Returns 'bound'; raises PaperRunError."""
    try:
        create_journal(spec.account_dir, spec.account_id, spec.aggregate_id).close()
    except JournalExists:
        pass
    journal = recover_journal(spec.account_dir, spec.account_id, spec.aggregate_id).journal
    if journal is None:
        raise PaperRunError('journal_unusable')
    try:
        if any(isinstance(ev, BindingChanged) for ev in journal.read()):
            raise PaperRunError('already_bound')
        binding = paper_binding()
        confirmation = BindingConfirmation(account_id=spec.account_id, old_key_digest=None,
                                           new_key_digest=binding.key_digest,
                                           typed_phrase=confirmation_phrase(spec.account_id, binding.key_digest),
                                           confirmed_at_ms=wall_ms)
        journal.append(BindingChanged(
            event_id=make_id('evt', uuid.uuid4().int if event_n is None else event_n), account_id=spec.account_id,
            aggregate_id=spec.aggregate_id, sequence=journal.last_sequence() + 1, at_ms=wall_ms,
            reason=ReasonCode.BINDING_UNCONFIRMED, from_state=BindingState.UNCONFIRMED,
            to_state=BindingState.CONFIRMED, binding=binding, confirmation=confirmation, reconciliation_id=None))
        return 'bound'
    finally:
        journal.close()


# ---------------------------------------------------------------------------------------------------- marks
class FileMarks:
    """Mark prices from a utf-8 JSON file ``{"BTCUSDT": "100.5", ...}`` (strings only), re-read when it changes."""

    def __init__(self, path: str):
        self.path = path
        self._stamp = None
        self._marks: dict[str, Decimal] = {}
        self.refresh()
        if not self._marks:
            raise PaperRunError('marks_empty')

    def refresh(self) -> None:
        try:
            st = os.stat(self.path)
            stamp = (st.st_mtime_ns, st.st_size)
            if stamp == self._stamp:
                return
            with open(self.path, encoding='utf-8') as handle:
                raw = json.load(handle)
        except Exception:
            raise PaperRunError('marks_unreadable') from None
        if type(raw) is not dict or not 1 <= len(raw) <= MAX_SYMBOLS:
            raise PaperRunError('marks_invalid')
        marks = {}
        for symbol, value in raw.items():
            try:
                check_symbol(symbol, 'marks')
                price = Decimal(value) if type(value) is str else None
            except (ValueError, InvalidOperation):
                price = None
            if price is None or not price.is_finite() or not price > 0:
                raise PaperRunError('marks_invalid')
            marks[symbol] = price
        self._marks, self._stamp = marks, stamp

    @property
    def symbols(self) -> tuple[str, ...]:
        return tuple(sorted(self._marks))

    def __call__(self, symbol: str) -> Decimal:
        if symbol not in self._marks:
            raise ValueError('mark_missing')
        return self._marks[symbol]


# ---------------------------------------------------------------------------------------------------- ports
def paper_limits():
    """Binance USD-M's documented limits (NC-04 pinned sources); the PAPER venue itself never throttles."""
    exchange = RawLimitPayload(
        authority=LimitAuthority.EXCHANGE_INFO, method='GET', path=EXCHANGE_INFO_PATH,
        source_url=MARKET_DATA_SOURCE, retrieved_on=SOURCE_RETRIEVED_ON,
        payload={'rateLimits': [
            {'rateLimitType': 'REQUEST_WEIGHT', 'interval': 'MINUTE', 'intervalNum': 1, 'limit': 2400}]})
    account = RawLimitPayload(
        authority=LimitAuthority.USER_RATE_LIMIT, method='GET', path=ACCOUNT_ORDER_LIMIT_PATH,
        source_url=ACCOUNT_SOURCE, retrieved_on=SOURCE_RETRIEVED_ON,
        payload=[{'rateLimitType': 'ORDERS', 'interval': 'SECOND', 'intervalNum': 10, 'limit': 300},
                 {'rateLimitType': 'ORDERS', 'interval': 'MINUTE', 'intervalNum': 1, 'limit': 1200}])
    return exchange, account


class MemoryRateBlocks:
    """PAPER has no venue rate limits to remember across restarts."""

    def __init__(self):
        self.records = []

    def append_rate_block(self, record):
        self.records.append(record)

    def load_rate_blocks(self, account_id, egress_id):
        return tuple(x for x in self.records if (x.account_id, x.egress_id) == (account_id, egress_id))


def book_path(spec) -> str:
    return os.path.join(spec.account_dir, BOOK_FILE)


def load_book(venue: PaperVenue, spec) -> None:
    path = book_path(spec)
    if not os.path.exists(path):
        return
    try:
        with open(path, encoding='utf-8') as handle:
            raw = json.load(handle)
        venue.load_state(raw)
    except Exception:
        raise PaperRunError('paper_book_invalid') from None


def save_book(venue: PaperVenue, spec) -> None:
    path = book_path(spec)
    tmp = path + '.tmp'
    with open(tmp, 'w', encoding='utf-8') as handle:
        json.dump(venue.export_state(), handle, sort_keys=True)
        handle.flush()
        os.fsync(handle.fileno())
    os.replace(tmp, path)


class JournalView:
    """Read-only access to an account's live NC-02 journal for an entries factory (no append, no close).

    It is valid once the runtime has booted (``None`` before that) and must be read from the account's own NC-05
    runner thread, which is where the cycle hook calls ``EntrySource.candidates``.
    """

    __slots__ = ('_journal',)

    def __init__(self, journal):
        self._journal = journal

    @property
    def account_id(self):
        return self._journal.account_id

    def read(self):
        return self._journal.read()

    def state(self):
        return self._journal.state()

    def last_sequence(self):
        return self._journal.last_sequence()


def _factory_takes_journal(factory) -> bool:
    try:
        params = inspect.signature(factory).parameters.values()
    except (TypeError, ValueError):
        return False
    return any(p.name == 'journal' or p.kind is inspect.Parameter.VAR_KEYWORD for p in params)


def build_paper_ports(config, marks: FileMarks, *, monotonic_clock, wall_clock, entries_factory=None, views=None):
    """Returns (ports by account id, venues by account id)."""
    if config.environment is not RuntimeEnvironment.PAPER_SIM:
        raise PaperRunError('testnet_run_unavailable')
    ports, venues = {}, {}
    for spec in config.accounts:
        venue = PaperVenue(clock=wall_clock, marks=marks)
        load_book(venue, spec)
        exchange, account = paper_limits()
        budget = AccountBudget(account_id=spec.account_id, egress_id=spec.egress_id, exchange_info=exchange,
                               account_order_limits=account, monotonic_clock=monotonic_clock, wall_clock=wall_clock,
                               journal=MemoryRateBlocks())
        context = AccountContext(account_id=spec.account_id, egress_id=spec.egress_id, venue=venue, budget=budget,
                                 monotonic_clock=monotonic_clock)
        ports[spec.account_id] = AccountPorts(context=context, state_source=JournalProjection(label='paper'),
                                              evidence=VenueEvidence(symbols=marks.symbols),
                                              actions=JournalingActionSink(),
                                              **({} if entries_factory is None else
                                                 {'entries': _entries(entries_factory, spec, wall_clock, views)}))
        venues[spec.account_id] = venue
    return ports, venues


def _entries(factory, spec, wall_clock, views):
    """Call the factory; one that accepts ``journal`` gets a callable returning the account's ``JournalView``."""
    if views is None or not _factory_takes_journal(factory):
        return factory(spec, wall_clock=wall_clock)
    return factory(spec, wall_clock=wall_clock, journal=lambda: views.get(spec.account_id))


# ---------------------------------------------------------------------------------------------------- run
def status_line(snap, step: int, state: str) -> dict:
    return {'schema': STATUS_SCHEMA, 'status': state, 'step': step, 'environment': snap.environment.value,
            'entries_enabled': snap.entries_enabled, 'scheduler': snap.scheduler.state.value,
            'accounts': [{'account_id': a.account_id, 'phase': a.phase.value, 'holds': list(a.holds),
                          'plan': a.last_plan_status, 'restore_proven': a.restore_proven} for a in snap.accounts]}


def run_paper(config, *, marks_path: str, emit, step_ms: int = 250, max_steps: int | None = None,
              stop_file: str | None = None, status_every: int = 20, sleep=time.sleep,
              monotonic_clock=_mono_ms, wall_clock=_wall_ms, boot_id: str | None = None,
              entries_factory=None) -> dict:
    """Run until max_steps, the stop file appears, or KeyboardInterrupt.  Returns the final status line."""
    if config.environment is not RuntimeEnvironment.PAPER_SIM:
        raise PaperRunError('testnet_run_unavailable')
    if config.entries_enabled and entries_factory is None:
        raise PaperRunError('entries_source_missing')
    if type(step_ms) is not int or not 10 <= step_ms <= 60_000:
        raise PaperRunError('step_ms_invalid')
    if max_steps is not None and (type(max_steps) is not int or max_steps < 1):
        raise PaperRunError('max_steps_invalid')
    if any(not os.path.isdir(spec.account_dir) for spec in config.accounts):
        raise PaperRunError('account_not_initialised')      # run never creates an account: init-paper does
    marks = FileMarks(marks_path)
    if entries_factory is not None and not callable(entries_factory):
        raise PaperRunError('entries_factory_invalid')
    views: dict[str, JournalView] = {}
    ports, venues = build_paper_ports(config, marks, monotonic_clock=monotonic_clock, wall_clock=wall_clock,
                                      entries_factory=entries_factory, views=views)
    specs = {s.account_id: s for s in config.accounts}
    runtime = boot(config, ports, boot_id=boot_id or f'paper-{uuid.uuid4().hex[:12]}',
                   monotonic_clock=monotonic_clock, wall_clock=wall_clock)
    attach_views(runtime, views)

    def each_step():
        marks.refresh()

    def after_step():
        for account_id, venue in venues.items():
            venue.tick()
            save_book(venue, specs[account_id])

    def on_close():
        for account_id, venue in venues.items():
            save_book(venue, specs[account_id])

    return drive(runtime, config, emit=emit, step_ms=step_ms, max_steps=max_steps, stop_file=stop_file,
                 status_every=status_every, sleep=sleep, monotonic_clock=monotonic_clock, before_step=each_step,
                 after_step=after_step, on_close=on_close)


def attach_views(runtime, views: dict) -> None:
    """Fill the entries factories' journal views once the runtime has booted (see ``JournalView``)."""
    for account_id, row in runtime._accounts.items():
        if row.journal is not None:
            views[account_id] = JournalView(row.journal)


def drive(runtime, config, *, emit, step_ms: int, max_steps: int | None, stop_file: str | None, status_every: int,
          sleep, monotonic_clock, before_step=None, after_step=None, on_close=None) -> dict:
    """Step a booted runtime until max_steps, the stop file or Ctrl-C; then stop NC-05 with grace and close.

    Shared by the PAPER run and the testnet launcher (``tools/newcore_testnet_run.py``).  Returns the final status.
    """
    step, snap, reason = 0, runtime.snapshot(), 'max_steps'
    emit(status_line(snap, step, 'booted'))
    try:
        while max_steps is None or step < max_steps:
            if stop_file is not None and os.path.exists(stop_file):
                reason = 'stop_file'
                break
            if before_step is not None:
                before_step()
            snap = runtime.step()
            if after_step is not None:
                after_step()
            step += 1
            if step % status_every == 0:
                emit(status_line(snap, step, 'running'))
            sleep(step_ms / 1000)
    except KeyboardInterrupt:
        reason = 'interrupted'
    finally:
        try:
            runtime.stop(monotonic_clock() + config.stop_grace_ms)
            for _ in range(max(1, (config.stop_grace_ms * 2) // step_ms)):
                snap = runtime.step()
                if snap.scheduler.state.value == 'stopped':
                    break
                sleep(step_ms / 1000)
        finally:
            runtime.close()
            if on_close is not None:
                on_close()
    final = status_line(snap, step, 'stopped')
    final['reason'] = reason
    emit(final)
    return final


__all__ = ['BOOK_FILE', 'FileMarks', 'JournalView', 'PAPER_KEY_DIGEST', 'PaperRunError', 'attach_views',
           'build_paper_ports', 'drive', 'init_paper_account', 'load_book', 'paper_binding', 'run_paper', 'save_book']
