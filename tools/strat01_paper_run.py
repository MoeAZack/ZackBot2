"""STRAT-01 PAPER launcher: run the NEWCORE PAPER runtime with the frozen-strategy EntrySource.

    python tools/strat01_paper_run.py --config cfg.json --marks marks.json --candles candles/ --equity 10000 \
        [--feed binance-public] [--max-steps N] [--stop-file STOP]

It composes, outside ``newcore/runtime`` (whose import boundary forbids strategy code):

* ``newcore_strategies.build_strategy_entries`` (preset ``TESTNET_SET_2026_11_05``) over a ``CsvCandleFeed``, with every
  slot narrowed to ``--symbols`` (default CORE-20's first 8): the PAPER runtime marks and reconciles at most 8 symbols
  (``newcore.runtime.evidence.MAX_SYMBOLS``).  Cross-sectional keys cannot be narrowed and are refused;
* a ``JournalRiskContext`` per account: the runtime's live read-only ``JournalView`` (the ``journal`` keyword), the PAPER equity
  given here, filters from the pinned exchangeInfo snapshot and the ``PaperPolicy`` ceilings;

and hands the factory to ``newcore.runtime.paper_run.run_paper(..., entries_factory=...)``.  The config must be a
``paper_sim`` config with ``entries_enabled: true``; accounts must already be initialised (``python -m newcore.runtime
init-paper``).

``--feed binance-public`` starts a background refresher that writes closed USD-M klines (``<SYMBOL>_<tf>.csv``) and the
marks file from Binance's PUBLIC market-data endpoints (no key, no account, no order endpoint), atomically, every
``--feed-every`` seconds.  Without it, something else must keep both files current.

PAPER only.  It refuses to start while a live/mainnet switch variable is present, never reads a credential and never
sends an order anywhere but the in-memory PAPER venue.
"""
from __future__ import annotations

import argparse
import csv
import json
import os
import sys
import threading
import time
import urllib.parse
import urllib.request
from collections import Counter
from decimal import Decimal, InvalidOperation

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

EXIT_OK, EXIT_REFUSED, EXIT_USAGE, EXIT_HELD = 0, 2, 64, 3
REFUSED_ENVIRONMENT = ('LIVE_CONFIRM', 'ZACKBOT_LIVE', 'ZACKBOT_MAINNET')
DEFAULT_EXCHANGE_INFO = os.path.join(ROOT, 'research_evidence', 'prereg',
                                     'binance-um-exchangeinfo-mainnet-raw-20261011T151658Z.json')
PUBLIC_BASE = 'https://fapi.binance.com'
PAGE, EXTRA = 1500, 200                 # klines per request (Binance max) and the older page
MAX_MARK_AGE_S = 300
DEFAULT_SYMBOLS = ('BTCUSDT', 'ETHUSDT', 'ZECUSDT', 'SOLUSDT', 'XRPUSDT', 'NEARUSDT', 'HYPEUSDT', 'DOGEUSDT')  # CORE_20[:8]


# ---------------------------------------------------------------------------------------------------- feed
def _atomic_write(path: str, text: str) -> None:
    tmp = path + '.tmp'
    with open(tmp, 'w', encoding='utf-8', newline='') as handle:
        handle.write(text)
        handle.flush()
        os.fsync(handle.fileno())
    os.replace(tmp, path)


def klines_csv(rows, now_ms: int) -> str:
    """Binance kline rows -> CSV text (t,o,h,l,c,v,qv; t = UTC open time ISO), CLOSED candles only."""
    import io
    buf = io.StringIO()
    w = csv.writer(buf, lineterminator='\n')
    w.writerow(['t', 'o', 'h', 'l', 'c', 'v', 'qv'])
    for r in rows:
        if int(r[6]) >= now_ms:                     # close time not yet passed: still forming
            continue
        t = time.strftime('%Y-%m-%d %H:%M:%S', time.gmtime(int(r[0]) // 1000))
        w.writerow([t, r[1], r[2], r[3], r[4], r[5], r[7]])
    return buf.getvalue()


def marks_json(rows, symbols) -> str:
    want = set(symbols)
    out = {}
    for r in rows:
        if r.get('symbol') in want:
            try:
                p = Decimal(str(r['markPrice']))
            except (InvalidOperation, KeyError):
                continue
            if p.is_finite() and p > 0:
                out[r['symbol']] = str(p)
    if not out:
        raise ValueError('marks_empty')
    return json.dumps(out, sort_keys=True)


class PublicFeed:
    """Refreshes candles and marks from Binance public market data in a daemon thread.  Errors are counted, never
    raised: stale candles are refused by the strategy source's freshness check."""

    def __init__(self, *, candle_dir, marks_path, symbols, tfs, base=PUBLIC_BASE, every_s=60,
                 fetch=None, clock=lambda: int(time.time() * 1000)):
        self.candle_dir, self.marks_path, self.symbols, self.tfs = candle_dir, marks_path, tuple(symbols), tuple(tfs)
        self.base, self.every_s, self.clock = base, every_s, clock
        self._fetch = fetch or self._http
        self.reasons: Counter = Counter()
        self._stop = threading.Event()
        self._thread = None

    def _http(self, path, params):
        url = self.base + path + ('?' + urllib.parse.urlencode(params) if params else '')
        req = urllib.request.Request(url, headers={'User-Agent': 'zackbot-strat01-paper'})
        with urllib.request.urlopen(req, timeout=15) as resp:
            return json.loads(resp.read().decode('utf-8'))

    def refresh_marks(self) -> None:
        try:
            _atomic_write(self.marks_path, marks_json(self._fetch('/fapi/v1/premiumIndex', None), self.symbols))
            self.reasons['marks_ok'] += 1
        except Exception as ex:
            self.reasons[f'marks_error:{type(ex).__name__}'] += 1

    def refresh_candles(self) -> None:
        """Two pages per file: Binance caps one request at 1500 candles and one of those is still forming, so a
        single page could never fill the strategy source's 1500-bar warm-up."""
        now = self.clock()
        for tf in self.tfs:
            for sym in self.symbols:
                try:
                    rows = self._fetch('/fapi/v1/klines', {'symbol': sym, 'interval': tf, 'limit': PAGE})
                    if rows:
                        older = self._fetch('/fapi/v1/klines', {'symbol': sym, 'interval': tf, 'limit': EXTRA,
                                                                'endTime': int(rows[0][0]) - 1})
                        rows = [r for r in older if int(r[0]) < int(rows[0][0])] + list(rows)
                    _atomic_write(os.path.join(self.candle_dir, f'{sym}_{tf}.csv'), klines_csv(rows, now))
                    self.reasons['candles_ok'] += 1
                except Exception as ex:
                    self.reasons[f'candles_error:{type(ex).__name__}'] += 1

    def refresh(self) -> None:
        self.refresh_marks()
        self.refresh_candles()

    def start(self) -> None:
        def loop():
            while not self._stop.wait(self.every_s):
                self.refresh()
        self._thread = threading.Thread(target=loop, name='strat01-feed', daemon=True)
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()


# ---------------------------------------------------------------------------------------------------- factory
def paper_rows(rows, symbols):
    """The preset rows narrowed to ``symbols`` (the PAPER runtime marks and reconciles at most MAX_SYMBOLS).  A
    cross-sectional strategy needs its whole universe, so narrowing one is refused."""
    from newcore.runtime.evidence import MAX_SYMBOLS
    from newcore_strategies.entry_source import CROSS_SECTIONAL, StrategyConfigError
    symbols = tuple(symbols)
    if not 1 <= len(symbols) <= MAX_SYMBOLS or len(set(symbols)) != len(symbols) or 'BTCUSDT' not in symbols:
        raise StrategyConfigError('paper_symbols_invalid')
    out = []
    for row in rows:
        if row['key'] in CROSS_SECTIONAL and tuple(row['symbols']) != symbols:
            raise StrategyConfigError('cross_sectional_universe_narrowed')
        out.append({**row, 'symbols': list(symbols)})
    return tuple(out)


def make_factory(*, marks_path, candle_dir, equity, exchange_info, base_risk_fraction, leverage, symbols,
                 rows=None, policy=None, sources=None, max_mark_age_s=MAX_MARK_AGE_S, now_s=time.time,
                 environment='paper'):
    """``entries_factory(spec, *, wall_clock, journal)`` for ``run_paper`` (or ``run_testnet`` with
    ``environment='testnet'``).  Built sources are appended to ``sources``."""
    from newcore.runtime.paper_run import FileMarks
    from newcore_strategies import CsvCandleFeed, build_strategy_entries
    from newcore_strategies.entry_source import StrategyConfigError
    from newcore_strategies.journal_risk import JournalRiskContext, PaperPolicy, filters_from_exchange_info
    from newcore_strategies.presets import TESTNET_SET_2026_11_05, build_slots
    rows = paper_rows(TESTNET_SET_2026_11_05 if rows is None else rows, symbols)
    policy = PaperPolicy() if policy is None else policy
    slots, _ = build_slots(rows)
    if not slots:
        raise StrategyConfigError('no_slots_available')
    filters = filters_from_exchange_info(exchange_info, symbols, max_notional=equity * policy.max_gross_multiple,
                                         max_leverage=policy.max_leverage)
    missing = [x for x in symbols if x not in filters]
    if missing:
        raise StrategyConfigError('filters_missing:' + ','.join(missing))

    def factory(spec, *, wall_clock, journal=None):
        # ``journal``: paper_run's callable returning the live read-only JournalView (None before boot).  Without it
        # the risk context has no journal and offers nothing.
        journal = journal if callable(journal) else (lambda: None)
        marks = FileMarks(marks_path)

        def mark(symbol):
            # The marks file's age is physical time: a feed that stopped must not price the book as fresh.
            if now_s() - os.stat(marks_path).st_mtime > max_mark_age_s:
                raise ValueError('marks_stale')
            marks.refresh()
            return marks(symbol)

        risk = JournalRiskContext(
            account_id=spec.account_id, marks=mark, equity=equity, filters=filters, slots=slots, policy=policy,
            leverage=leverage, environment=environment,
            journal_reader=journal)
        source, refused = build_strategy_entries(feed=CsvCandleFeed(candle_dir), risk=risk, wall_clock=wall_clock,
                                                 base_risk_fraction=base_risk_fraction, leverage=leverage,
                                                 rows=rows)
        if sources is not None:
            sources.append({'account_id': spec.account_id, 'source': source, 'risk': risk, 'refused': refused})
        return source
    return factory, slots


# ---------------------------------------------------------------------------------------------------- cli
def _dec_arg(text):
    try:
        d = Decimal(text)
    except InvalidOperation:
        raise argparse.ArgumentTypeError('not a decimal') from None
    if not d.is_finite() or d <= 0:
        raise argparse.ArgumentTypeError('must be > 0')
    return d


def main(argv=None, *, environ=None, out=None, run_hooks=None, fetch=None) -> int:
    environ = os.environ if environ is None else environ
    out = sys.stdout if out is None else out
    p = argparse.ArgumentParser(prog='strat01_paper_run')
    p.add_argument('--config', required=True)
    p.add_argument('--marks', required=True)
    p.add_argument('--candles', required=True)
    p.add_argument('--equity', type=_dec_arg, required=True)
    p.add_argument('--exchange-info', default=DEFAULT_EXCHANGE_INFO)
    p.add_argument('--symbols', default=','.join(DEFAULT_SYMBOLS),
                   help='comma list, at most 8 (the PAPER runtime marks at most 8), must include BTCUSDT')
    p.add_argument('--base-risk', type=_dec_arg, default=Decimal('0.005'))
    p.add_argument('--leverage', type=_dec_arg, default=Decimal('3'))
    p.add_argument('--feed', choices=('none', 'binance-public'), default='none')
    p.add_argument('--feed-every', type=int, default=60)
    p.add_argument('--max-mark-age', type=int, default=MAX_MARK_AGE_S, help='seconds; older marks give no entries')
    p.add_argument('--step-ms', type=int, default=250)
    p.add_argument('--max-steps', type=int, default=None)
    p.add_argument('--stop-file', default=None)
    p.add_argument('--status-every', type=int, default=240)
    try:
        args = p.parse_args(argv)
    except SystemExit:
        return EXIT_USAGE

    def emit(line):
        out.write(json.dumps(line, sort_keys=True, default=str) + '\n')
        out.flush()

    def refuse(code, **extra):
        emit({'status': 'refused', 'code': code, **extra})
        return EXIT_REFUSED

    refused_env = {n.casefold() for n in REFUSED_ENVIRONMENT}
    present = sorted(n for n in environ if n.casefold() in refused_env)
    if present:
        return refuse('live_switch_present', variables=present)
    if args.status_every < 1 or not 10 <= args.feed_every <= 3600 or not 30 <= args.max_mark_age <= 3600:
        return EXIT_USAGE
    from newcore.runtime import paper_run
    from newcore.runtime.config import RuntimeConfigError, RuntimeEnvironment, config_from_mapping
    from newcore_strategies.journal_risk import RiskSetupError, load_exchange_info
    try:
        with open(args.config, encoding='utf-8') as handle:
            config = config_from_mapping(json.load(handle))
    except RuntimeConfigError as ex:
        return refuse(ex.code)
    except Exception:
        return refuse('config_unreadable')
    if config.environment is not RuntimeEnvironment.PAPER_SIM:
        return refuse('paper_only')
    if not config.entries_enabled:
        return refuse('entries_not_enabled')
    if not os.path.isdir(args.candles):
        return refuse('candle_dir_missing')
    try:
        info = load_exchange_info(args.exchange_info)
    except (OSError, ValueError, RiskSetupError):
        return refuse('exchange_info_unreadable')
    sources = []
    try:
        factory, slots = make_factory(marks_path=args.marks, candle_dir=args.candles, equity=args.equity,
                                      exchange_info=info, base_risk_fraction=args.base_risk,
                                      leverage=args.leverage, symbols=tuple(args.symbols.split(',')),
                                      sources=sources, max_mark_age_s=args.max_mark_age)
    except Exception as ex:
        return refuse(getattr(ex, 'code', f'setup_error:{type(ex).__name__}'))
    feed = None
    if args.feed == 'binance-public':
        symbols = sorted({x for s in slots for x in s.symbols})
        feed = PublicFeed(candle_dir=args.candles, marks_path=args.marks, symbols=symbols,
                          tfs=sorted({s.tf for s in slots}), every_s=args.feed_every, fetch=fetch)
        feed.refresh()                                  # first fill before the runtime reads the marks file
        feed.start()
    emit({'status': 'starting', 'slots': [s.name for s in slots], 'equity': str(args.equity),
          'base_risk': str(args.base_risk), 'leverage': str(args.leverage), 'feed': args.feed})
    try:
        final = paper_run.run_paper(config, marks_path=args.marks, emit=emit, step_ms=args.step_ms,
                                    max_steps=args.max_steps, stop_file=args.stop_file,
                                    status_every=args.status_every, entries_factory=factory, **(run_hooks or {}))
    except paper_run.PaperRunError as ex:
        return refuse(ex.code)
    finally:
        if feed is not None:
            feed.stop()
    emit({'status': 'strategy_summary',
          'accounts': [{'account_id': s['account_id'], 'refused_slots': s['refused'],
                        'source_reasons': dict(s['source'].reasons), 'risk_reasons': dict(s['risk'].reasons)}
                       for s in sources],
          'feed_reasons': None if feed is None else dict(feed.reasons)})
    return EXIT_OK if all(not a['holds'] for a in final['accounts']) else EXIT_HELD


if __name__ == '__main__':  # pragma: no cover
    raise SystemExit(main())
