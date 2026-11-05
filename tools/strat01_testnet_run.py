"""STRAT-01 on Binance USD-M TESTNET: the NEWCORE testnet runtime with the frozen-strategy EntrySource.

    python tools/strat01_testnet_run.py --config cfg.json --account-file acct.json --candles candles/ \
        --marks marks.json --equity 1000 [--symbols BTCUSDT,...] [--feed-origin https://demo-fapi.binance.com]

It composes the same entries factory as ``tools/strat01_paper_run.py`` (``make_factory(environment='testnet')``) and
hands it to ``tools/newcore_testnet_run.run_testnet`` (#296), which owns the venue, the keys, the binding check and the
runtime.  This file never reads a credential.

* ``--equity`` is the capital the strategies size from.  It is capped at $2,000 (the owner's testnet bound); the
  testnet venue rejects anything the real balance cannot margin.
* Candles, marks and symbol filters come from the testnet origin's PUBLIC market-data endpoints (klines,
  premiumIndex, exchangeInfo), so signal prices, stops and tick/step sizes match the venue the orders go to.  Only
  the two approved testnet origins are accepted.
* Every slot is narrowed to ``--symbols`` (at most 8, BTCUSDT included); the runtime's evidence reads those symbols.

Testnet only: a live/mainnet switch variable, a non-testnet config or ``entries_enabled: false`` is refused before any
request.  Mainnet is never reachable from here.
"""
from __future__ import annotations

import argparse
import json
import os
import sys
from decimal import Decimal
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent
for p in (str(ROOT), str(HERE)):
    if p not in sys.path:
        sys.path.insert(0, p)

import strat01_paper_run as P  # noqa: E402

EXIT_OK, EXIT_REFUSED, EXIT_HELD, EXIT_USAGE = 0, 2, 3, 64
TESTNET_ORIGINS = ('https://demo-fapi.binance.com', 'https://testnet.binancefuture.com')
MAX_TESTNET_CAPITAL = Decimal('2000')


def main(argv=None, *, environ=None, out=None, run_hooks=None, fetch=None, run_testnet=None) -> int:
    environ = os.environ if environ is None else environ
    out = sys.stdout if out is None else out
    p = argparse.ArgumentParser(prog='strat01_testnet_run')
    p.add_argument('--config', required=True)
    p.add_argument('--account-file', required=True)
    p.add_argument('--marks', required=True)
    p.add_argument('--candles', required=True)
    p.add_argument('--equity', type=P._dec_arg, required=True)
    p.add_argument('--symbols', default=','.join(P.DEFAULT_SYMBOLS))
    p.add_argument('--feed-origin', default=TESTNET_ORIGINS[0])
    p.add_argument('--feed-every', type=int, default=60)
    p.add_argument('--max-mark-age', type=int, default=P.MAX_MARK_AGE_S)
    p.add_argument('--base-risk', type=P._dec_arg, default=Decimal('0.005'))
    p.add_argument('--leverage', type=P._dec_arg, default=Decimal('3'))
    p.add_argument('--step-ms', type=int, default=500)
    p.add_argument('--max-steps', type=int, default=None)
    p.add_argument('--stop-file', default=None)
    p.add_argument('--status-every', type=int, default=120)
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

    refused_env = {n.casefold() for n in P.REFUSED_ENVIRONMENT}
    present = sorted(n for n in environ if n.casefold() in refused_env)
    if present:
        return refuse('live_switch_present', variables=present)
    if args.status_every < 1 or not 10 <= args.feed_every <= 3600 or not 30 <= args.max_mark_age <= 3600:
        return EXIT_USAGE
    if args.feed_origin not in TESTNET_ORIGINS:
        return refuse('feed_origin_not_testnet')
    if args.equity > MAX_TESTNET_CAPITAL:
        return refuse('equity_above_testnet_cap')
    from newcore.runtime.config import RuntimeConfigError, RuntimeEnvironment, config_from_mapping
    try:
        with open(args.config, encoding='utf-8') as handle:
            config = config_from_mapping(json.load(handle))
    except RuntimeConfigError as ex:
        return refuse(ex.code)
    except Exception:
        return refuse('config_unreadable')
    if config.environment is not RuntimeEnvironment.TESTNET:
        return refuse('testnet_only')
    if not config.entries_enabled:
        return refuse('entries_not_enabled')
    if not os.path.isdir(args.candles):
        return refuse('candle_dir_missing')
    symbols = tuple(args.symbols.split(','))
    feed = P.PublicFeed(candle_dir=args.candles, marks_path=args.marks, symbols=symbols, tfs=(),
                        base=args.feed_origin, every_s=args.feed_every, fetch=fetch)
    try:
        info = feed._fetch('/fapi/v1/exchangeInfo', None)          # the testnet's own filters
        if not isinstance(info, dict) or not isinstance(info.get('symbols'), list):
            raise ValueError('exchange_info_invalid')
    except Exception:
        return refuse('exchange_info_unavailable')
    sources = []
    try:
        factory, slots = P.make_factory(marks_path=args.marks, candle_dir=args.candles, equity=args.equity,
                                        exchange_info=info, base_risk_fraction=args.base_risk,
                                        leverage=args.leverage, symbols=symbols, sources=sources,
                                        max_mark_age_s=args.max_mark_age, environment='testnet')
    except Exception as ex:
        return refuse(getattr(ex, 'code', f'setup_error:{type(ex).__name__}'))
    feed.tfs = tuple(sorted({s.tf for s in slots}))
    feed.refresh()
    if feed.reasons.get('marks_ok', 0) < 1:
        return refuse('marks_unavailable', feed_reasons=dict(feed.reasons))
    feed.start()
    if run_testnet is None:
        import newcore_testnet_run as T
        run_testnet, error = T.run_testnet, T.TestnetRunError
    else:
        error = Exception
    emit({'status': 'starting', 'environment': 'testnet', 'slots': [s.name for s in slots],
          'symbols': list(symbols), 'equity': str(args.equity), 'base_risk': str(args.base_risk),
          'leverage': str(args.leverage), 'feed_origin': args.feed_origin})
    try:
        final = run_testnet(config, args.account_file, symbols=symbols, emit=emit, step_ms=args.step_ms,
                            max_steps=args.max_steps, stop_file=args.stop_file, status_every=args.status_every,
                            entries_factory=factory, environ=environ, **(run_hooks or {}))
    except error as ex:
        return refuse(getattr(ex, 'code', type(ex).__name__))
    finally:
        feed.stop()
    emit({'status': 'strategy_summary',
          'accounts': [{'account_id': s['account_id'], 'refused_slots': s['refused'],
                        'source_reasons': dict(s['source'].reasons), 'risk_reasons': dict(s['risk'].reasons)}
                       for s in sources],
          'feed_reasons': dict(feed.reasons)})
    return EXIT_OK if all(not a['holds'] for a in final['accounts']) else EXIT_HELD


if __name__ == '__main__':  # pragma: no cover
    raise SystemExit(main())
