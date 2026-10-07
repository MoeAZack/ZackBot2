"""In-app market-data collector (observe-only): a daemon thread that every ~4 h (jittered) runs market_data.Collector
over its OWN keyless public MAINNET client (market_data.RequestsTransport: separate requests.Session, separate rate-limit
/ ban / failure state). It never sends a request through engine.data and never calls its outage circuit's ok()/fail():
a collector 429, 418, 5xx or network error cannot degrade the engine's market-data reads, and a collector success can
never clear an engine outage. The engine circuit is only READ, as a one-way gate (not 'ok' -> no collector traffic).

Contract (tests/test_market_collector.py):
  - never takes engine.lock, never touches orders, positions, stops or engine state; reads only e.S (MARKET_COLLECTOR,
    UNIVERSE) and e.data.health.state
  - writes only under its own directory (%LOCALAPPDATA%/ZackBot/market_data), every file atomically (market_data)
  - bounded: one run at a time (a run still going -> the next one is skipped), a per-run time limit, pacing below the
    engine's own request budget; a run aborts while the engine's exchange circuit is not healthy, on an IP ban (418)
    or when the setting is switched off
  - one log line per run; status() is shown in /api/status -> health.market_collector (incl. its own request health)
  - MARKET_COLLECTOR (default ON) switches it off
"""
import logging, os, random, threading, time

import market_data as MD

log = logging.getLogger('zackbot')


def public_transport():
    """The collector's own keyless MAINNET transport (no API key header, its own HTTP session)."""
    return MD.RequestsTransport(MD.MAINNET)


class RequestHealth:
    """The collector's own request outcome counters - independent of the engine's ExchangeHealth (never shared)."""
    def __init__(self):
        self._lk = threading.Lock()
        self.ok = self.failed = 0
        self.last_ok = self.last_failure = None

    def record(self, ok, what=''):
        with self._lk:
            if ok: self.ok += 1; self.last_ok = MD.utc_iso(MD.now_ms())
            else: self.failed += 1; self.last_failure = dict(t=MD.utc_iso(MD.now_ms()), what=str(what)[:160])

    def snapshot(self):
        with self._lk:
            return dict(ok=self.ok, failed=self.failed, last_ok=self.last_ok, last_failure=self.last_failure)


class _Tracked:
    """Wraps the collector transport: every answer / error is recorded in the collector's RequestHealth only."""
    def __init__(self, t, health):
        self.t, self.health = t, health

    @property
    def used_weight(self):
        return getattr(self.t, 'used_weight', None)

    def get(self, path, params):
        try:
            out = self.t.get(path, params)
        except MD.Refused:
            self.health.record(True); raise              # Binance answered (business refusal): reachable
        except Exception as ex:
            self.health.record(False, f'{path}: {type(ex).__name__} {str(ex)[:100]}'); raise
        self.health.record(True)
        return out


class MarketCollector:
    EVERY_S = 4 * 3600
    JITTER = 0.1                    # +-10 % on every interval
    FIRST_DELAY_S = 180             # let the engine connect / reconcile first
    MAX_RUN_S = 3 * 3600            # a long first back-fill continues on the next run (series resume from their last row)
    WEIGHT_PER_MIN = 400            # well under Binance's 2400 / min IP budget, leaving room for the engine's own reads
    DEGRADED_WAIT_S = 60            # circuit 'degraded': wait this long for the engine's next good read, then stop the run

    def __init__(self, get_engine, out, every_s=None, first_delay_s=None, clock=time.time, transport_factory=public_transport):
        self.get_engine, self.out = get_engine, out
        self.every_s = self.EVERY_S if every_s is None else every_s
        self.first_delay_s = self.FIRST_DELAY_S if first_delay_s is None else first_delay_s
        self.clock, self.transport_factory = clock, transport_factory
        self.req_health = RequestHealth()          # the collector's own - the engine's circuit is only ever read
        self._transport = None                     # created on the first run, then reused (one keep-alive session)
        self._lock_token = None                    # the folder lock this collector holds while a run is going
        self._stop = threading.Event()
        self._run_lock = threading.Lock()          # the collector's own lock - never the engine's
        self._st_lock = threading.Lock()
        self.thread = None
        self._st = dict(running=False, runs=0, skipped=0, rows_total=0, last_run=None, last_skip=None, next_run=None, error=None)

    # -------------------------------------------------------------- switches
    def enabled(self, e=None):
        e = self.get_engine() if e is None else e
        if e is None: return False
        v = (getattr(e, 'S', None) or {}).get('MARKET_COLLECTOR', True)
        return v is True

    def gate(self):
        """Checked by market_data before every request: a reason string stops the run. Reads the engine circuit's state
        only (one-way): nothing here calls its ok() / fail() / admit_read()."""
        if self._stop.is_set(): return 'app stopping'
        e = self.get_engine()
        if not self.enabled(e): return 'MARKET_COLLECTOR switched off'
        h = getattr(getattr(e, 'data', None), 'health', None)
        st = getattr(h, 'state', 'ok')
        if st == 'degraded':
            t_end = time.monotonic() + self.DEGRADED_WAIT_S
            while st == 'degraded' and time.monotonic() < t_end and not self._stop.is_set():
                self._stop.wait(2.0); st = getattr(h, 'state', 'ok')
        if st != 'ok': return f'engine exchange circuit {st} - not adding load'
        return None

    def _sleep(self, s):
        self._stop.wait(max(0.0, float(s)))

    # -------------------------------------------------------------- one run
    def run_once(self):
        """One bounded collection run. Returns its summary, or None when skipped."""
        if not self._run_lock.acquire(blocking=False):
            self._skip('previous run still going'); return None
        try:
            e = self.get_engine()
            if e is None: self._skip('engine not started'); return None
            if not self.enabled(e): self._skip('MARKET_COLLECTOR off'); return None
            token = MD.acquire_lock(self.out, clock=self.clock)
            if not token:
                self._skip('another collector (standalone tool) is writing this folder'); return None
            self._lock_token = token
            with self._st_lock: self._st['running'] = True
            try:
                symbols = [s for s in list((e.S or {}).get('UNIVERSE') or MD.CORE8) if isinstance(s, str)]
                if self._transport is None: self._transport = self.transport_factory()
                c = MD.Collector(_Tracked(self._transport, self.req_health), self.out, source=MD.MAINNET, sleep=self._sleep,
                                 clock=self.clock, weight_per_min=self.WEIGHT_PER_MIN, min_gap=0.5, gate=self.gate,
                                 max_run_s=self.MAX_RUN_S)
                s = c.run(symbols, run_kind='in-app')
            finally:
                MD.release_lock(self.out, token); self._lock_token = None
                with self._st_lock: self._st['running'] = False
            with self._st_lock:
                self._st['runs'] += 1; self._st['rows_total'] += s['rows_added']; self._st['error'] = None
                self._st['last_run'] = {k: s.get(k) for k in ('started', 'finished', 'seconds', 'requests', 'rows_added', 'errors',
                                                             'last_errors', 'stopped', 'symbols')}
            log.info(f"market data: +{s['rows_added']} rows, {s['symbols']} coins, {s['requests']} requests, {s['errors']} errors, "
                     f"{s['seconds']}s" + (f" - stopped: {s['stopped']}" if s['stopped'] else ''))
            return s
        except Exception as ex:                              # never let the thread die; one line, no traceback spam
            with self._st_lock: self._st['error'] = f'{type(ex).__name__}: {str(ex)[:160]}'
            log.warning(f'market data: run failed - {type(ex).__name__}: {str(ex)[:160]}')
            return None
        finally:
            self._run_lock.release()

    def _skip(self, why):
        with self._st_lock:
            self._st['skipped'] += 1; self._st['last_skip'] = dict(t=MD.utc_iso(MD.now_ms(self.clock)), why=why)

    # -------------------------------------------------------------- thread
    def _jit(self, s):
        return s * random.uniform(1 - self.JITTER, 1 + self.JITTER)

    def _loop(self):
        wait = self._jit(self.first_delay_s)
        while True:
            with self._st_lock: self._st['next_run'] = MD.utc_iso((self.clock() + wait) * 1000)
            if self._stop.wait(wait): return
            self.run_once()
            wait = self._jit(self.every_s)

    def start(self):
        if self.thread is not None and self.thread.is_alive(): return self.thread
        self.thread = threading.Thread(target=self._loop, name='market-collector', daemon=True)
        self.thread.start()
        log.info(f"market data collector: every {self.every_s / 3600:g} h -> {self.out}"
                 + ('' if self.enabled() else ' (MARKET_COLLECTOR is off: runs are skipped)'))
        return self.thread

    def stop(self):
        self._stop.set()

    def shutdown(self):
        """App quitting (os._exit follows): stop, and release the folder lock if a run holds it, so the next start does
        not skip collection. Files are written atomically, so a run cut off here leaves complete files only."""
        self._stop.set()
        tok = self._lock_token
        if tok: MD.release_lock(self.out, tok)

    def status(self):
        with self._st_lock: st = dict(self._st)
        try: st['enabled'] = self.enabled()
        except Exception: st['enabled'] = None
        st['out'] = self.out
        st['request_health'] = self.req_health.snapshot()
        ban = MD.read_json(os.path.join(self.out, MD.BAN_FILE)) or {}
        try: st['banned_until'] = ban.get('until_utc') if float(ban.get('until') or 0) > self.clock() else None
        except (TypeError, ValueError): st['banned_until'] = None
        return st
