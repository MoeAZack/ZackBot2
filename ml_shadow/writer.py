"""#104: the ML-shadow ledgers are written OFF the engine's cycle lock.

The engine builds each decision / label row on its own thread (causal: features are read from the candle it decided on)
and hands it to submit(), which is ONE non-blocking put. A single daemon thread appends the rows to the bounded JSONL
ledgers in FIFO order (a decision always reaches disk before a label that follows it). A full queue drops the row and
counts it - the engine never waits for a slow or failing disk.

Decision ids: ids accepted into the queue are known at once (in memory); ids already on disk are loaded by the writer
thread when it starts. known(id) answers True / False, or None while that load has not finished (the label stage still
checks the decision ledger on disk, so an id that has no decision row never gets a label: it is refused and counted as
missing_decision - explicit incomplete evidence, never reconstructed or relinked (Codex 6254070622).
"""
import atexit, logging, queue, threading, time

from .ledger import labelable

log = logging.getLogger('zackbot.ml_shadow')
QUEUE_MAX = 1024


class LedgerWriter:
    def __init__(self, open_ledgers, counters=None, maxsize=QUEUE_MAX):
        """open_ledgers() -> (decision_ledger, label_ledger); called once, on the writer thread (it reads the files).
        counters: a dict whose keys decisions / labels / missing_decision / refused_skipped / write_errors this thread
        increments (the engine health block); dropped by submit(), lost by close()."""
        self.open_ledgers = open_ledgers
        self.ctr = counters if counters is not None else {}
        for k in ('decisions', 'labels', 'missing_decision', 'refused_skipped', 'write_errors', 'dropped', 'lost'):
            self.ctr.setdefault(k, 0)
        self.q = queue.Queue(maxsize)
        self.lock, self.stop_ev = threading.Lock(), threading.Event()
        self.closing, self.thread, self.loaded = False, None, False
        self.ids = set()

    # ---------------------------------------------------------------- engine side (non-blocking)
    def submit(self, kind, row):
        """Queue one built row ('decision' | 'label'). True if accepted; False (counted as dropped) when the queue is
        full or the writer is closing. Never blocks, never raises."""
        try:
            with self.lock:
                if self.closing:
                    self.ctr['dropped'] += 1; return False
                if self.thread is None:
                    self.thread = threading.Thread(target=self._run, name='ml-shadow-writer', daemon=True)
                    self.thread.start()
                try:
                    self.q.put_nowait((kind, row))
                except queue.Full:
                    self.ctr['dropped'] += 1; return False
                if kind == 'decision': self.ids.add(row.get('decision_id'))
                return True
        except Exception:                                   # noqa: BLE001 - an observer never breaks the engine
            return False

    def known(self, did):
        """True if this decision id is queued or on disk; False if not; None while the on-disk ids are still loading."""
        with self.lock:
            if did in self.ids: return True
            return False if self.loaded else None

    # ---------------------------------------------------------------- writer thread
    def _write(self, led, kind, row):
        dec, lab = led
        if kind == 'decision':
            if dec.append(row) is not None: self.ctr['decisions'] += 1
            return
        did = row.get('decision_id')
        src = next((r for r in dec.rows() if r.get('decision_id') == did), None)
        if src is None:                                    # dropped / never written / rotated out: incomplete evidence
            self.ctr['missing_decision'] += 1; return
        if not labelable(src):                             # a skipped signal never gets a label
            self.ctr['refused_skipped'] += 1; return
        if lab.append(row) is not None: self.ctr['labels'] += 1

    def _open(self):
        """Open the ledgers and merge the decision ids already on disk. None (retried on the next row) on failure -
        and then the ids stay 'not loaded' (known() -> None), never 'absent'."""
        try:
            led = self.open_ledgers()
            on_disk = {r.get('decision_id') for r in led[0].rows()}
            with self.lock: self.ids |= on_disk; self.loaded = True
            return led
        except Exception as e:                              # noqa: BLE001
            log.warning('ml_shadow: ledgers not opened (%s)', type(e).__name__)
            return None

    def _run(self):
        led = self._open()
        while True:
            try: kind, row = self.q.get(timeout=0.2)
            except queue.Empty:
                if self.stop_ev.is_set(): return
                continue
            try:
                if led is None: led = self._open()
                if led is None: raise OSError('ledgers unavailable')
                self._write(led, kind, row)
            except Exception as e:                          # noqa: BLE001
                self.ctr['write_errors'] += 1
                log.warning('ml_shadow: row not written (%s)', type(e).__name__)   # type only: no data in logs
            finally:
                self.q.task_done()

    # ---------------------------------------------------------------- flush / close
    def flush(self, timeout=2.0):
        """Wait (bounded) until every accepted row is written or failed. True if the queue drained."""
        end = time.monotonic() + timeout
        while self.q.unfinished_tasks and time.monotonic() < end: time.sleep(0.01)
        return not self.q.unfinished_tasks

    def alive(self):
        return self.thread is not None and self.thread.is_alive()

    def close(self, timeout=2.0):
        """Stop accepting, flush what is queued (bounded), stop and join. True only if no writer thread remains."""
        end = time.monotonic() + timeout
        with self.lock: self.closing = True
        self.flush(max(0.0, end - time.monotonic()))
        self.stop_ev.set()
        if self.thread is not None: self.thread.join(max(0.0, end - time.monotonic()) + 0.3)
        left = self.q.unfinished_tasks
        if left: self.ctr['lost'] += left                  # accepted but never written: lost, never claimed as written
        return not self.alive()

    def queued(self):
        return self.q.qsize()


_WRITERS, _WRITERS_LOCK = [], threading.Lock()


def register(w):
    with _WRITERS_LOCK: _WRITERS.append(w)
    return w


def unregister(w):
    with _WRITERS_LOCK:
        if w in _WRITERS: _WRITERS.remove(w)
    return True


def close_all(timeout=2.0):
    """Process exit: flush and stop every writer (each bounded by timeout). Returns how many could not be stopped."""
    with _WRITERS_LOCK: ws = list(_WRITERS); _WRITERS.clear()
    return sum(0 if w.close(timeout) else 1 for w in ws)


atexit.register(close_all, 2.0)
