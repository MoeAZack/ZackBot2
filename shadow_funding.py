"""TRUE-BOOK-02: keyless Binance USD-M TESTNET funding timeline for the strategy shadow (observe only).

Codex 6246971221 #4: shadow funding comes from the collected TESTNET historical funding timeline (fundingTime, fundingRate,
markPrice) - never account income, never mainnet. A daemon thread pages /fapi/v1/fundingRate forward per symbol over its
OWN keyless testnet transport (market_data.RequestsTransport(TESTNET): separate session, rate-limit and failure state) into
<data>/strategy_shadow/funding_testnet/funding_rate/<SYM>.csv (market_data storage: rows keyed by fundingTime, written
atomically, earlier rows kept). It never takes engine.lock, never calls the engine's exchange client, never touches the
ShadowBook: the engine feeds new rows to the observer on its own thread (Engine._shadow_funding_feed), so the shadow
ledger keeps a single writer. A run is skipped unless the engine is PAPER with STRATEGY_SHADOW on, and stops while the
engine's exchange circuit is not healthy (read one-way, like market_collector).
"""
import logging, os, random, threading, time

import market_data as MD
from market_collector import RequestHealth, _Tracked

log = logging.getLogger('zackbot')
SUBDIR = os.path.join('strategy_shadow', 'funding_testnet')
LOOKBACK_MS = 3 * 24 * 3600 * 1000           # first collection without a cohort start: the last 3 days


def out_dir(data_dir):
    return os.path.join(data_dir, SUBDIR)


def series_file(data_dir, sym):
    return MD.series_path(out_dir(data_dir), 'funding_rate', sym, None)


def read_funding(data_dir, sym, after_ms=None):
    """[(fundingTime ms, rate str|None, markPrice str|None)] sorted, strictly after after_ms."""
    rows = MD.read_rows(series_file(data_dir, sym), 'fundingTime')
    return [(t, (r.get('fundingRate') or None), (r.get('markPrice') or None)) for t, r in sorted(rows.items())
            if after_ms is None or t > after_ms]


class ShadowFunding:
    EVERY_S = 1800
    JITTER = 0.1
    FIRST_DELAY_S = 240
    MAX_RUN_S = 600
    WEIGHT_PER_MIN = 120                      # tiny: the engine shares the IP budget

    def __init__(self, get_engine, data_dir, clock=time.time, transport_factory=lambda: MD.RequestsTransport(MD.TESTNET)):
        self.get_engine, self.out = get_engine, out_dir(data_dir)
        self.clock, self.transport_factory = clock, transport_factory
        self.req_health = RequestHealth()
        self._transport = None
        self._stop = threading.Event()
        self._run_lock = threading.Lock()
        self._st_lock = threading.Lock()
        self.thread = None
        self._st = dict(runs=0, skipped=0, rows_total=0, last_run=None, last_skip=None, error=None)

    def enabled(self, e=None):
        e = self.get_engine() if e is None else e
        return bool(e is not None and getattr(e, 'live', True) is False and (getattr(e, 'S', None) or {}).get('STRATEGY_SHADOW') is True)

    def gate(self):
        if self._stop.is_set(): return 'app stopping'
        e = self.get_engine()
        if not self.enabled(e): return 'STRATEGY_SHADOW off or not PAPER'
        st = getattr(getattr(getattr(e, 'data', None), 'health', None), 'state', 'ok')
        return None if st == 'ok' else f'engine exchange circuit {st} - not adding load'

    def _start_ms(self, e):
        coh = (getattr(e, 'state', None) or {}).get('cohort') or {}
        try:
            from datetime import datetime
            return int(datetime.fromisoformat(str(coh['started_utc'])).timestamp() * 1000)
        except (KeyError, TypeError, ValueError):
            return MD.now_ms(self.clock) - LOOKBACK_MS

    def run_once(self):
        if not self._run_lock.acquire(blocking=False):
            self._skip('previous run still going'); return None
        try:
            e = self.get_engine()
            why = self.gate()
            if why: self._skip(why); return None
            symbols = sorted({s for s in list((e.S or {}).get('UNIVERSE') or ()) if isinstance(s, str)})
            if self._transport is None: self._transport = self.transport_factory()
            c = MD.Collector(_Tracked(self._transport, self.req_health), self.out, source=MD.TESTNET, sleep=self._stop.wait,
                             clock=self.clock, weight_per_min=self.WEIGHT_PER_MIN, min_gap=0.7, gate=self.gate,
                             max_run_s=self.MAX_RUN_S)
            c.t0 = self.clock()                              # the run-time limit counts from here
            start, added, errs = self._start_ms(e), 0, 0
            for sym in symbols:
                if self.gate(): break
                try: added += c.collect_forward('funding_rate', sym, None, start)
                except (MD.Transient, MD.Refused, MD.RateLimited, OSError) as ex:   # OSError: CSV held open by a reader
                    errs += 1; log.debug(f'shadow funding {sym}: {ex}')
                except (MD.Banned, MD.StopRun):
                    break
            MD.atomic_write_json(os.path.join(self.out, 'manifest.json'), c.manifest)   # provenance: source=TESTNET per series
            with self._st_lock:
                self._st['runs'] += 1; self._st['rows_total'] += added; self._st['error'] = None
                self._st['last_run'] = dict(t=MD.utc_iso(MD.now_ms(self.clock)), symbols=len(symbols), rows_added=added, errors=errs)
            return dict(rows_added=added, errors=errs, symbols=len(symbols))
        except Exception as ex:                              # never let the thread die
            with self._st_lock: self._st['error'] = f'{type(ex).__name__}: {str(ex)[:160]}'
            log.warning(f'shadow funding: run failed - {type(ex).__name__}: {str(ex)[:160]}')
            return None
        finally:
            self._run_lock.release()

    def _skip(self, why):
        with self._st_lock:
            self._st['skipped'] += 1; self._st['last_skip'] = dict(t=MD.utc_iso(MD.now_ms(self.clock)), why=why)

    def _loop(self):
        wait = self.FIRST_DELAY_S * random.uniform(1 - self.JITTER, 1 + self.JITTER)
        while not self._stop.wait(wait):
            self.run_once()
            wait = self.EVERY_S * random.uniform(1 - self.JITTER, 1 + self.JITTER)

    def start(self):
        if self.thread is not None and self.thread.is_alive(): return self.thread
        self.thread = threading.Thread(target=self._loop, name='shadow-funding', daemon=True)
        self.thread.start()
        return self.thread

    def stop(self):
        self._stop.set()

    def status(self):
        with self._st_lock: st = dict(self._st)
        st['out'] = self.out
        st['request_health'] = self.req_health.snapshot()
        return st
