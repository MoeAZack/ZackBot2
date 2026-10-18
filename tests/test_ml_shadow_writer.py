"""#104: ML-shadow ledger writes run off the engine cycle lock, and the lot -> decision link is persisted by the open
path's own save_state(). Fakes only - no network, no real keys, no real data folder."""
import builtins, json, os, sys, threading, time

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from test_ml_shadow01_hook import (run_fixture, ledger_rows, maker_setup, one_cycle, frame, sig, SLA)   # noqa: E402
from test_safety import mk_engine                          # noqa: E402
import engine as E                                         # noqa: E402
import ml_shadow as MLS                                    # noqa: E402
from ml_shadow import writer as MLW                        # noqa: E402

KW = dict(sleeve='A', strategy='ema_mom', family='Trend', symbol='BTCUSDT', side='LONG', tf='4h', features={'features': {}})


def dec_row(t, status='taken'):
    return MLS.build_decision_record(candle_time=t, taken=status == 'taken', status=status, **KW)


def lab_row(did):
    return MLS.build_label_record(decision_id=did, net_r=1.0, net_pnl=1.0, exit_reason='x')


def ledgers(tmp):
    return lambda: (MLS.JsonlLedger(str(tmp / 'd.jsonl')), MLS.JsonlLedger(str(tmp / 'l.jsonl')))


def rows(p):
    return [json.loads(x) for x in open(p, encoding='utf-8')] if os.path.exists(p) else []


def disk_state(e):
    with open(e.F['state'], encoding='utf-8') as f: return json.load(f)


# ------------------------------------------------------------------ slow disk / queue
def test_blocked_disk_never_delays_the_cycle_and_no_ml_io_on_engine_thread(tmp_path, monkeypatch):
    gate, real = threading.Event(), MLS.JsonlLedger.append
    def slow(self, row):
        gate.wait(30); return real(self, row)
    monkeypatch.setattr(MLS.JsonlLedger, 'append', slow)
    eng_thread, ml_io = threading.get_ident(), []
    real_open = builtins.open
    def spy(f, *a, **k):
        if 'ml_shadow' in str(f) and threading.get_ident() == eng_thread: ml_io.append(str(f))
        return real_open(f, *a, **k)
    monkeypatch.setattr(builtins, 'open', spy)
    t0 = time.monotonic()
    e, on = run_fixture(tmp_path / 'on', monkeypatch, True)
    took = time.monotonic() - t0
    monkeypatch.setattr(builtins, 'open', real_open)
    assert ml_io == []                                    # every ML ledger read/write is on the writer thread
    assert took < 10                                      # the cycles finished while the writer was stuck (gate: 30 s)
    assert e._ml_w.queued() + e._ml_w.q.unfinished_tasks > 0 and rows(e.ml_paths()['decisions']) == []
    gate.set()
    assert e.ml_flush(10)
    assert {r['status'] for r in rows(e.ml_paths()['decisions'])} >= {'taken', 'skipped'}
    _, off = run_fixture(tmp_path / 'off', monkeypatch, False)
    assert on == off                                      # still identical trading


def test_full_queue_drops_and_counts_never_blocks(tmp_path):
    gate = threading.Event()
    def opener():
        gate.wait(30); return ledgers(tmp_path)()
    ctr = {}
    w = MLW.LedgerWriter(opener, ctr, maxsize=2)
    t0 = time.monotonic()
    got = [w.submit('decision', dec_row(f'2026-10-0{k} 00:00:00')) for k in range(1, 7)]
    assert time.monotonic() - t0 < 1.0
    assert got.count(True) <= 3 and ctr['dropped'] == got.count(False) >= 3   # 2 queued (+1 possibly taken by the thread)
    gate.set()
    assert w.close(10) and ctr['decisions'] == got.count(True) and ctr['lost'] == 0
    assert len(rows(tmp_path / 'd.jsonl')) == got.count(True)


def test_writer_survives_disk_errors_and_counts_them(tmp_path, monkeypatch):
    real, calls = MLS.JsonlLedger.append, []
    def flaky(self, row):
        calls.append(1)
        if len(calls) <= 3: raise [OSError('EIO'), PermissionError('denied'), OSError(28, 'disk full')][len(calls) - 1]
        return real(self, row)
    monkeypatch.setattr(MLS.JsonlLedger, 'append', flaky)
    ctr = {}
    w = MLW.LedgerWriter(ledgers(tmp_path), ctr)
    for k in range(1, 6): assert w.submit('decision', dec_row(f'2026-10-0{k} 00:00:00'))
    assert w.close(10)
    assert ctr['write_errors'] == 3 and ctr['decisions'] == 2 and len(rows(tmp_path / 'd.jsonl')) == 2


def test_open_failure_is_retried_on_the_next_row(tmp_path):
    n = []
    def opener():
        n.append(1)
        if len(n) == 1: raise OSError('locked')
        return ledgers(tmp_path)()
    ctr = {}
    w = MLW.LedgerWriter(opener, ctr)
    assert w.submit('decision', dec_row('2026-10-01 00:00:00')) and w.flush(5)
    assert w.submit('decision', dec_row('2026-10-02 00:00:00'))
    assert w.close(10) and ctr['decisions'] >= 1 and len(rows(tmp_path / 'd.jsonl')) == ctr['decisions']


def test_failed_open_never_reports_disk_ids_absent(tmp_path):
    d = dec_row('2026-10-01 00:00:00')
    w = MLW.LedgerWriter(ledgers(tmp_path), {}); w.submit('decision', d); assert w.close(10)
    n = []
    def opener():
        n.append(1)
        if len(n) <= 2: raise OSError('locked by a scanner')
        return ledgers(tmp_path)()
    w2 = MLW.LedgerWriter(opener, {})
    w2.submit('label', lab_row('0' * 32)); assert w2.flush(5)          # start + retry both failed (row counted as write error)
    assert w2.known(d['decision_id']) is None                           # unknown, not 'absent': a pending fill still links
    w2.submit('decision', dec_row('2026-10-02 00:00:00')); assert w2.flush(5)
    assert w2.known(d['decision_id']) is True
    assert w2.close(10)


def test_engine_restart_closes_the_old_writer_first(tmp_path):
    e, _ = mk_engine(str(tmp_path))
    e.S['ML_SHADOW'] = True
    gate = threading.Event()
    real = e._ml_ledgers
    def slow():
        gate.wait(0.3); return real()
    e._ml_ledgers = slow
    w = e._ml_writer()
    for k in range(1, 4): w.submit('decision', dec_row(f'2026-10-0{k} 00:00:00'))
    assert e.ml_close(10) and not w.alive() and w not in MLW._WRITERS
    assert len(ledger_rows(e, 'decisions')) == 3                        # drained before the next engine opens the files
    assert not w.submit('decision', dec_row('2026-10-05 00:00:00'))    # a stopped writer accepts nothing (counted)
    assert e.health['ml_shadow']['dropped'] == 1 and e.health['ml_shadow']['lost'] == 0


def test_app_start_engine_closes_the_previous_ml_writer():
    import inspect, app as A
    src = inspect.getsource(A.App.start_engine)
    i, j = src.index('old.lock.acquire()'), src.index('Engine(cfg, DATA)')
    assert 'old.ml_close()' in src[i:j]                                 # under the old lock, before the new engine exists


def test_shutdown_flushes_queued_rows(tmp_path):
    ctr = {}
    w = MLW.LedgerWriter(ledgers(tmp_path), ctr)
    d = [dec_row(f'2026-10-0{k} 00:00:00') for k in range(1, 6)]
    for r in d: w.submit('decision', r)
    w.submit('label', lab_row(d[0]['decision_id']))
    assert w.close(10) and not w.alive()
    assert len(rows(tmp_path / 'd.jsonl')) == 5 and len(rows(tmp_path / 'l.jsonl')) == 1 and ctr['lost'] == 0
    assert not w.submit('decision', dec_row('2026-10-09 00:00:00')) and ctr['dropped'] == 1   # closed: refused, counted


def test_shutdown_with_blocked_writer_honours_timeout_and_counts_lost(tmp_path):
    gate = threading.Event()
    def opener():
        gate.wait(30); return ledgers(tmp_path)()
    ctr = {}
    w = MLW.LedgerWriter(opener, ctr)
    for k in range(1, 4): w.submit('decision', dec_row(f'2026-10-0{k} 00:00:00'))
    t0 = time.monotonic()
    assert w.close(0.5) is False and time.monotonic() - t0 < 3
    assert ctr['lost'] == 3 and ctr['decisions'] == 0       # never claimed as written
    gate.set()


def test_label_for_unknown_or_skipped_decision_refused_and_counted(tmp_path):
    ctr = {}
    w = MLW.LedgerWriter(ledgers(tmp_path), ctr)
    sk, tk = dec_row('2026-10-01 00:00:00', 'skipped'), dec_row('2026-10-02 00:00:00')
    w.submit('decision', sk); w.submit('decision', tk)
    for did in ('0' * 32, sk['decision_id'], tk['decision_id']): w.submit('label', lab_row(did))
    assert w.close(10)
    assert ctr['missing_decision'] == 1 and ctr['refused_skipped'] == 1 and ctr['labels'] == 1
    assert [l['decision_id'] for l in rows(tmp_path / 'l.jsonl')] == [tk['decision_id']]


def test_dropped_decision_never_label_eligible_but_lot_keeps_id(tmp_path):
    gate = threading.Event()
    def opener():
        gate.wait(30); return ledgers(tmp_path)()
    ctr = {}
    w = MLW.LedgerWriter(opener, ctr, maxsize=1)
    first, dropped = dec_row('2026-10-01 00:00:00'), dec_row('2026-10-02 00:00:00')
    assert w.submit('decision', first)
    time.sleep(0.3)                                         # the thread holds `first`; the queue (size 1) is free
    w.submit('decision', dec_row('2026-10-03 00:00:00'))    # fills the queue
    assert not w.submit('decision', dropped) and ctr['dropped'] >= 1
    assert w.known(dropped['decision_id']) is None          # still loading: a pending fill would keep the id on its lot
    gate.set(); assert w.flush(10)
    assert w.known(dropped['decision_id']) is False         # loaded: a dropped decision is known absent
    assert w.submit('label', lab_row(dropped['decision_id'])) and w.flush(5)
    assert w.submit('label', lab_row(first['decision_id']))
    assert w.close(10)
    assert ctr['missing_decision'] == 1 and ctr['labels'] == 1
    assert [l['decision_id'] for l in rows(tmp_path / 'l.jsonl')] == [first['decision_id']]


def test_restart_replay_never_duplicates_rows(tmp_path):
    d = dec_row('2026-10-01 00:00:00')
    for _ in range(2):                                      # two processes, same rows replayed
        w = MLW.LedgerWriter(ledgers(tmp_path), {})
        w.submit('decision', d); w.submit('decision', d); w.submit('label', lab_row(d['decision_id']))
        assert w.close(10)
    assert len(rows(tmp_path / 'd.jsonl')) == 1 and len(rows(tmp_path / 'l.jsonl')) == 1


def test_torn_tail_never_swallows_the_next_row(tmp_path):
    with open(tmp_path / 'd.jsonl', 'w', encoding='utf-8') as f: f.write('{"decision_id": "torn-partial", "kin')
    w = MLW.LedgerWriter(ledgers(tmp_path), {})
    a, b = dec_row('2026-10-01 00:00:00'), dec_row('2026-10-02 00:00:00')
    w.submit('decision', a); w.submit('decision', b); assert w.close(10)
    lines = open(tmp_path / 'd.jsonl', encoding='utf-8').read().split('\n')
    assert lines[0] == '{"decision_id": "torn-partial", "kin' and lines[-1] == ''
    assert [json.loads(x)['decision_id'] for x in lines[1:-1]] == [a['decision_id'], b['decision_id']]


def test_partial_write_never_swallows_the_next_row(tmp_path, monkeypatch):
    import ml_shadow.ledger as LM
    real_open, fail = builtins.open, [True]
    class Short:                                            # writes half the row, then the disk gives up
        def __init__(self, fh): self.fh = fh
        def __enter__(self): return self
        def __exit__(self, *a): self.fh.close()
        def write(self, s):
            self.fh.write(s[:len(s) // 2]); self.fh.flush(); raise OSError(28, 'No space left on device')
        def flush(self): self.fh.flush()
    def fake_open(p, mode='r', *a, **k):
        fh = real_open(p, mode, *a, **k)
        if 'a' in mode and fail[0]:
            fail[0] = False; return Short(fh)
        return fh
    ctr = {}
    w = MLW.LedgerWriter(ledgers(tmp_path), ctr)
    a, b, c = (dec_row(f'2026-10-0{k} 00:00:00') for k in (1, 2, 3))
    assert w.submit('decision', a) and w.flush(5)            # one good append: the tail is known clean
    fail[0] = True
    monkeypatch.setattr(LM, 'open', fake_open, raising=False)
    assert w.submit('decision', b) and w.flush(5)            # fragment written, then the write raises
    assert w.submit('decision', c) and w.close(10)
    assert ctr['write_errors'] == 1 and ctr['decisions'] == 2
    lines = open(tmp_path / 'd.jsonl', encoding='utf-8').read().split('\n')
    assert lines[-1] == '' and len(lines) == 4                 # a, the fragment, c
    assert json.loads(lines[0])['decision_id'] == a['decision_id'] and json.loads(lines[2])['decision_id'] == c['decision_id']
    with pytest.raises(ValueError): json.loads(lines[1])      # the failed row stays invalid evidence, never claimed written


def test_known_ids_seeded_from_disk_with_torn_last_line(tmp_path):
    d = dec_row('2026-10-01 00:00:00')
    w = MLW.LedgerWriter(ledgers(tmp_path), {}); w.submit('decision', d); assert w.close(10)
    with open(tmp_path / 'd.jsonl', 'a', encoding='utf-8') as f: f.write('{"kind": "decision", "decisi')
    w2 = MLW.LedgerWriter(ledgers(tmp_path), {})
    assert w2.known(d['decision_id']) is None               # nothing loaded before the writer starts
    w2.submit('decision', dec_row('2026-10-02 00:00:00')); assert w2.flush(5)
    assert w2.known(d['decision_id']) is True and w2.known('f' * 32) is False
    assert w2.close(10)


# ------------------------------------------------------------------ crash window: the link is in the first save
def _snapshot_at_stop(e, box):
    real = e._replace_stop
    def spy(lot, *a, **k):                                  # runs right after _create_lot's save_state()
        box.append(disk_state(e)); return real(lot, *a, **k)
    e._replace_stop = spy


def test_direct_entry_link_is_in_the_first_state_save(tmp_path):
    e, _ = mk_engine(str(tmp_path))
    e.S['ML_SHADOW'] = True; e.S['SLEEVES'] = [SLA]
    box = []
    _snapshot_at_stop(e, box)
    one_cycle(e, {'BTCUSDT': sig('2026-10-04 00:00:00', le=True)}, {'BTCUSDT': frame(100.0)})
    assert box, 'the open path did not run'
    (k, lot), = box[0]['lots'].items()                      # what a crash right after that save leaves on disk
    want = MLS.decision_id('A', 'ema_mom', 'BTCUSDT', 'LONG', SLA['tf'], '2026-10-04 00:00:00')
    assert lot['ml_decision_id'] == want
    dec = ledger_rows(e, 'decisions')
    assert [(r['decision_id'], r['status']) for r in dec] == [(want, 'taken')]
    assert e.ml_close(10)                                   # the old process is gone (its writer drained + stopped)
    e2, _ = mk_engine(str(tmp_path))                        # restart from that state: the close labels exactly once
    e2.trade.pos.update(e.trade.pos); e2.trade.stops.update(e.trade.stops)
    e2.S['ML_SHADOW'] = True
    e2.close_lot(k, 'exit_signal')
    assert [l['decision_id'] for l in ledger_rows(e2, 'labels')] == [want]


def test_pending_fill_link_is_in_the_first_state_save(tmp_path, monkeypatch):
    box = []
    real = E.Engine._replace_stop
    def spy(self, lot, *a, **k):
        box.append(disk_state(self)); return real(self, lot, *a, **k)
    monkeypatch.setattr(E.Engine, '_replace_stop', spy)
    e, k, lot = maker_setup(tmp_path)
    assert box and box[-1]['lots'][k]['ml_decision_id'] == lot['ml_decision_id']
    assert ledger_rows(e, 'decisions')[0]['decision_id'] == lot['ml_decision_id']


def test_pending_fill_not_linked_when_decision_known_absent(tmp_path):
    e, _ = mk_engine(str(tmp_path))
    e.S['ML_SHADOW'] = True
    w = e._ml_writer(); w.submit('decision', dec_row('2026-10-01 00:00:00')); assert w.flush(5)
    plan = dict(sl=dict(id='A', key='ema_mom', tf='4h'), sym='BTCUSDT', side='LONG', sg=dict(time='2026-10-02 00:00:00'))
    lot = {}
    e._ml_link(lot, plan)
    assert 'ml_decision_id' not in lot                      # loaded ledger, id absent: never silently relinked
    e._ml_direct = ('A', 'ETHUSDT', 'LONG', '2026-10-02 00:00:00'); e._ml_link(lot, plan)
    assert 'ml_decision_id' not in lot                      # another decision's open never vouches for this lot
    e._ml_direct = ('A', 'BTCUSDT', 'LONG', '2026-10-02 00:00:00'); e._ml_link(lot, plan); e._ml_direct = None
    assert lot['ml_decision_id'] == MLS.decision_id('A', 'ema_mom', 'BTCUSDT', 'LONG', '4h', '2026-10-02 00:00:00')
    lot2 = {}; e.S['ML_SHADOW'] = False; e._ml_link(lot2, plan)
    assert lot2 == {}                                       # OFF: no field at all


# ------------------------------------------------------------------ Codex activation gate: one live writer per ledger folder
def test_timed_out_close_never_lets_two_writers_share_the_files(tmp_path):
    gate = threading.Event()
    e, _ = mk_engine(str(tmp_path))
    e.S['ML_SHADOW'] = True
    real = e._ml_ledgers
    def stuck():
        gate.wait(30); return real()
    e._ml_ledgers = stuck
    old = e._ml_writer()
    old.submit('decision', dec_row('2026-10-01 00:00:00'))
    assert e.ml_close(0.3) is False and old.alive()          # close timed out: the old daemon is still running
    e2, _ = mk_engine(str(tmp_path))                         # the app builds the next engine on the same data folder
    e2.S['ML_SHADOW'] = True
    w2 = e2._ml_writer()
    assert not w2.submit('decision', dec_row('2026-10-02 00:00:00'))
    h2 = e2.health['ml_shadow']
    assert h2['blocked_prior_writer'] == 1 and h2['dropped'] == 1 and not w2.alive()   # never started, counted
    gate.set(); old.thread.join(10)
    assert not old.alive()
    assert w2.submit('decision', dec_row('2026-10-03 00:00:00')) and e2.ml_flush(5)    # takes over once the old one is gone
    ids = [r['decision_id'] for r in ledger_rows(e2, 'decisions')]
    assert ids == [dec_row('2026-10-01 00:00:00')['decision_id'], dec_row('2026-10-03 00:00:00')['decision_id']]
    assert e2.ml_close(10)


def test_writers_of_different_folders_never_block_each_other(tmp_path):
    a = MLW.LedgerWriter(ledgers(tmp_path / 'a'), {}, key=str(tmp_path / 'a'))
    (tmp_path / 'b').mkdir(); (tmp_path / 'a').mkdir(exist_ok=True)
    b = MLW.LedgerWriter(ledgers(tmp_path / 'b'), {}, key=str(tmp_path / 'b'))
    assert a.submit('decision', dec_row('2026-10-01 00:00:00')) and b.submit('decision', dec_row('2026-10-01 00:00:00'))
    assert a.close(10) and b.close(10)
