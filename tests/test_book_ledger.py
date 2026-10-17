"""BOOK-LEDGER-01 / RUN-PACK-01: observe-only per-book NAV, immutable cohort, sanitized checksum-verifiable pack."""
import copy, json, os, tempfile

import pytest

import book_ledger as BL
import engine as E
import paper_books as PB
import run_pack as RP
from test_safety import mk_engine, SL, SG

T0 = '2026-10-17T13:19:00+00:00'
SETS = {'MAX_LEVERAGE': 5}
SNAP = {'settings_at_start.json': SETS, 'settings_at_end.json': SETS}


def coh(**kw):
    return RP.new_cohort(kw.pop('started', T0), 'code', RP.sha256_doc(SETS)[:16], BL.book_version(), 'fee', 2000.0, **kw)


def row(book, pnl, fees=0.5, closed='2026-10-17T15:00:00+00:00', **kw):
    return dict(book=book, pnl=pnl, pnl_gross=pnl + fees, fees=fees, closed=closed, **kw)


def lot(book, sym, side, avg, qty, realized=0.0, fees=0.0, opened='2026-10-17T14:00:00+00:00', **kw):
    return dict(book=book, symbol=sym, side=side, avg=avg, qty=qty, realized=realized, fees=fees, opened=opened, **kw)


# ------------------------------------------------------------------ ledger maths
def test_nav_is_start_plus_closed_plus_open_booked_plus_mark_to_market():
    c = coh()
    d = BL.compute(c, [row('B1_FAST', 30, cohort_id=c['id'])],
                   [lot('B1_FAST', 'BTCUSDT', 'LONG', 100, 2, realized=5, fees=1, cohort_id=c['id']),
                    lot('B3_TREND', 'ETHUSDT', 'SHORT', 50, 4, cohort_id=c['id'])],
                   {'BTCUSDT': 110, 'ETHUSDT': 45}, 2000)
    b1, b3 = d['books']['B1_FAST'], d['books']['B3_TREND']
    assert b1['nav'] == 2000 + 30 + (5 - 1) + 20 and b1['realized_net'] == 30 and b1['unrealized'] == 20
    assert b3['nav'] == 2020 and b3['unrealized'] == 20                 # short gains when the mark falls
    assert b1['roi'] == pytest.approx(0.027) and b1['peak'] == 2054 and b1['dd'] == 0
    assert d['books']['B2_RANGE']['nav'] == 2000 and d['shared_account'] is True


def test_one_book_loss_never_moves_the_other_four():
    c = coh()
    base_h = [row(k, 10, cohort_id=c['id']) for k in PB.IDS]
    base_l = [lot(k, 'BTCUSDT', 'LONG', 100, 1, cohort_id=c['id']) for k in PB.IDS]
    a = BL.compute(c, base_h, base_l, {'BTCUSDT': 101}, 2000)
    h2 = base_h + [row('B2_RANGE', -1500, cohort_id=c['id'])]
    l2 = [dict(x, qty=50) if x['book'] == 'B2_RANGE' else x for x in base_l]
    b = BL.compute(c, h2, l2, {'BTCUSDT': 60}, 2000)      # B2 loses big; every book's lot sees the same (lower) mark
    assert b['books']['B2_RANGE']['nav'] < a['books']['B2_RANGE']['nav'] - 1500
    for k in PB.IDS:
        if k == 'B2_RANGE': continue
        assert b['books'][k]['realized_net'] == a['books'][k]['realized_net']
        assert b['books'][k]['open'] == a['books'][k]['open'] and b['books'][k]['symbols'] == a['books'][k]['symbols']
    a2 = BL.compute(c, h2, l2, {'BTCUSDT': 101}, 2000)    # same marks: only B2 differs from the baseline
    for k in PB.IDS:
        if k != 'B2_RANGE': assert a2['books'][k] == a['books'][k]


def test_overlap_same_symbol_two_books_stays_separately_attributed():
    c = coh()
    d = BL.compute(c, (), [lot('B1_FAST', 'SOLUSDT', 'LONG', 100, 1, cohort_id=c['id']),
                           lot('B4_BREAKOUT', 'SOLUSDT', 'SHORT', 102, 3, cohort_id=c['id'])], {'SOLUSDT': 101}, 2000)
    assert d['books']['B1_FAST']['unrealized'] == 1 and d['books']['B4_BREAKOUT']['unrealized'] == 3
    assert d['overlap'] == {'SOLUSDT': ['B1_FAST', 'B4_BREAKOUT']}


def test_foreign_cohort_unbooked_and_unknown_rows_are_excluded_and_counted():
    c, old = coh(), coh(started='2026-10-10T00:00:00+00:00')
    hist = [row('B1_FAST', 5, cohort_id=c['id']), row('B1_FAST', 999, cohort_id=old['id']),
            row('B1_FAST', 7, closed='2026-10-16T00:00:00+00:00'),               # unstamped, before the cohort
            row('B1_FAST', 3),                                                    # unstamped, inside the window: legacy
            row(None, 50), row('B9_X', 50, cohort_id=c['id'])]
    d = BL.compute(c, hist, (), {}, 2000)
    assert d['books']['B1_FAST']['realized_net'] == 8 and d['books']['B1_FAST']['closed'] == 2
    assert d['excluded'] == dict(foreign=2, unbooked=1, unknown_book=1, legacy_unstamped=1)


def test_missing_mark_is_flagged_stale_not_invented():
    c = coh()
    d = BL.compute(c, (), [lot('B1_FAST', 'XRPUSDT', 'LONG', 1, 100, cohort_id=c['id'])], {}, 2000)
    assert d['books']['B1_FAST']['stale'] is True and d['books']['B1_FAST']['unrealized'] == 0


def test_actual_costs_reported_with_missingness_never_replace_estimates():
    c = coh()
    d = BL.compute(c, [row('B1_FAST', 10, fees=2, cohort_id=c['id'], fees_actual=1.5),
                       row('B1_FAST', 10, fees=2, cohort_id=c['id'])], (), {}, 2000)
    b = d['books']['B1_FAST']
    assert b['fees_est'] == 4 and b['fees_actual'] == 1.5 and b['fees_actual_missing'] == 1
    assert b['funding_actual'] is None and b['funding_missing'] == 2 and b['nav'] == 2020   # nav on the estimate basis
    assert 'estimated' in d['basis']


def test_peak_dd_and_would_freeze_are_sticky_within_and_reset_across_cohorts():
    c = coh()
    up = BL.compute(c, [row('B1_FAST', 500, fees=0, cohort_id=c['id'])], (), {}, 2000)
    assert up['books']['B1_FAST']['peak'] == 2500
    down = BL.compute(c, [row('B1_FAST', 500, fees=0, cohort_id=c['id']), row('B1_FAST', -3000, fees=0, cohort_id=c['id'])],
                      (), {}, 2000, prev=up, t='t1')
    b = down['books']['B1_FAST']
    assert b['peak'] == 2500 and b['nav'] == -500 and b['dd'] == pytest.approx(1.2) and b['state'] == 'WOULD_FREEZE'
    back = BL.compute(c, [row('B1_FAST', 100, fees=0, cohort_id=c['id'])], (), {}, 2000, prev=down, t='t2')
    assert back['books']['B1_FAST']['state'] == 'WOULD_FREEZE' and back['books']['B1_FAST']['would_freeze_at'] == 't1'
    other = coh(seq=1)
    fresh = BL.compute(other, (), (), {}, 2000, prev=down)
    assert fresh['books']['B1_FAST']['state'] == 'ACTIVE' and fresh['books']['B1_FAST']['peak'] == 2000


def test_parity_with_paper_books_summary_and_detects_divergence():
    c = coh()
    hist = [row('B1_FAST', 3, cohort_id=c['id']), row('B4_BREAKOUT', -4, cohort_id=c['id'])]
    d = BL.compute(c, hist, (), {}, 2000)
    assert BL.parity(d, c, hist) == {}
    bad = copy.deepcopy(d); bad['books']['B1_FAST']['realized_net'] += 1
    assert set(BL.parity(bad, c, hist)) == {'B1_FAST'}


def test_compute_is_deterministic_for_restart():
    c = coh()
    args = ([row('B1_FAST', 3, cohort_id=c['id'])], [lot('B3_TREND', 'BTCUSDT', 'LONG', 100, 1, cohort_id=c['id'])], {'BTCUSDT': 99})
    a = BL.compute(c, *args, 2000, t='x')
    b = BL.compute(c, *copy.deepcopy(args), 2000, prev=json.loads(json.dumps(a)), t='x')
    assert a == b


# ------------------------------------------------------------------ cohort record
def test_cohort_id_is_a_hash_of_its_fields_and_edits_are_detected():
    c = coh()
    assert c['id'].startswith('CH-20261017T131900Z-') and RP.validate_cohort(c) == []
    assert coh() == c                                                     # same inputs -> same id (restart)
    assert coh(seq=1)['id'] != c['id']
    for k, v in (('book_cap', 5000.0), ('code_sha', 'other'), ('mode', 'LIVE'), ('started_utc', '2026-10-18T00:00:00+00:00')):
        assert RP.validate_cohort(dict(c, **{k: v}))
    assert RP.drift(c, code_sha='code', book_version=c['book_version']) == []
    assert RP.drift(c, code_sha='new', settings_sha=c['settings_sha']) == ['code_sha']


# ------------------------------------------------------------------ pack
def test_pack_round_trip_verify_and_tamper_detection():
    root = tempfile.mkdtemp(); c = coh()
    src = os.path.join(root, 'src.jsonl')
    with open(src, 'w') as f: f.write('{"a":1}\n{"a":2}\n')
    path, sha = RP.write_pack(os.path.join(root, 'out'), c, dict(SNAP, **{'history.json': [1, 2, 3]}),
                              {'fills.jsonl': src, 'none.jsonl': None})
    v = RP.verify_pack(path)
    assert v['ok'] and v['pack_sha'] == sha and v['cohort_id'] == c['id']
    man = json.load(open(os.path.join(path, 'MANIFEST.json')))
    assert {x['file']: x['rows'] for x in man['files'] if x['file'] in ('history.json', 'fills.jsonl')} == {'history.json': 3, 'fills.jsonl': 2}
    assert man['absent'] == ['none.jsonl'] and v['clean']
    with open(os.path.join(path, 'history.json'), 'ab') as f: f.write(b' ')
    assert not RP.verify_pack(path)['ok']
    with open(os.path.join(path, 'extra.txt'), 'w') as f: f.write('x')
    assert any('unlisted' in p for p in RP.verify_pack(path)['problems'])


def test_pack_never_overwrites_and_leaves_no_temp_on_refusal():
    root = tempfile.mkdtemp(); c = coh()
    RP.write_pack(root, c, {'a.json': {}})
    with pytest.raises(RP.PackRefused, match='never overwritten'):
        RP.write_pack(root, c, {'a.json': {'x': 1}})
    with pytest.raises(RP.PackRefused):
        RP.write_pack(root, coh(seq=2), {'a.json': {'api_key': 'x'}})
    assert sorted(os.listdir(root)) == [c['id']]


@pytest.mark.parametrize('doc', [{'API_SECRET': 'x'}, {'nested': [{'TELEGRAM_TOKEN': 'x'}]}, {'TELEGRAM_PIN': '1234'},
                                 {'note': '123456789:AAHdqTcvCH1vGWJxfSeofSAs0K5PALDsawq'}, {'k': 'mysecretvalue123'}])
def test_pack_refuses_credentials_by_key_value_or_shape(doc):
    with pytest.raises(RP.PackRefused) as ex:
        RP.write_pack(tempfile.mkdtemp(), coh(), {'x.json': doc}, forbid=['mysecretvalue123'])
    assert 'mysecretvalue123' not in str(ex.value) and 'AAHdq' not in str(ex.value)


def test_sanitize_settings_is_allowlist_and_drops_secret_names():
    s = RP.sanitize_settings(dict(A=1, B=2, TELEGRAM_PIN='9', API_KEY='k'), ['A', 'TELEGRAM_PIN', 'API_KEY'])
    assert s == {'A': 1}


# ------------------------------------------------------------------ engine wiring (PAPER, observe only)
def books_engine(**kw):
    e, tmp = mk_engine(CAPITAL_CAP=10000.0, PAPER_BOOKS=True, PAPER_BOOK_CAP=2000.0, **kw)
    e.S['CAP_SINCE'] = '2026-10-17T13:19:00+00:00'
    e._cohort_init()
    return e, tmp


def test_engine_creates_cohort_once_stamps_lot_and_history_and_never_rewrites():
    e, _ = books_engine()
    cid = e.state['cohort']['id']
    assert RP.validate_cohort(e.state['cohort']) == []
    e.S['MAX_LEVERAGE'] = 3; e._cohort_init()
    assert e.state['cohort']['id'] == cid and e.cohort_drift == ['settings_sha']
    assert e.open_lot(SL, 'BTCUSDT', 'LONG', SG, None, e.equity())
    k, l = next(iter(e.state['lots'].items()))
    assert l['cohort_id'] == cid and l['book'] == 'B3_TREND'
    e.close_lot(k, 'manual')
    assert e.history[-1]['cohort_id'] == cid and e.history[-1]['book'] == 'B3_TREND'


def test_ledger_never_changes_sizing():
    def size(on):
        e, _ = books_engine()
        if on:
            e.book_ledger_tick(); e.state['book_ledger']['books']['B3_TREND']['state'] = 'WOULD_FREEZE'
        assert e.open_lot(SL, 'BTCUSDT', 'LONG', SG, None, e.equity())
        l = next(iter(e.state['lots'].values()))
        return l['qty'], l['risk_usd'], l['eq_at_entry']
    assert size(False) == size(True)


def test_no_cohort_or_stamp_when_books_off_or_live():
    e, _ = mk_engine(PAPER_BOOKS=False); e._cohort_init()
    assert e.state.get('cohort') is None and e.book_ledger_status() is None and e.book_ledger_tick() is False
    e2, _ = mk_engine(PAPER_BOOKS=True); e2.live = True; e2._cohort_init()
    assert e2.state.get('cohort') is None
    assert e2.open_lot(SL, 'BTCUSDT', 'LONG', SG, None, e2.equity())
    assert 'cohort_id' not in next(iter(e2.state['lots'].values()))


def test_tick_writes_once_per_bucket_and_restart_is_exact(monkeypatch):
    e, tmp = books_engine()
    assert e.open_lot(SL, 'BTCUSDT', 'LONG', SG, None, e.equity())
    monkeypatch.setattr(E.time, 'time', lambda: 1_800_000_000.0)
    assert e.book_ledger_tick() is True and e.book_ledger_tick() is False
    lines = open(e.F['book_ledger']).read().splitlines()
    assert len(lines) == 1
    before = e.book_ledger_status()
    e2 = E.Engine(dict(MODE='paper', API_KEY='k' * 16, API_SECRET='s' * 16), tmp)      # restart on the same folder
    e2.S.update(PAPER_BOOKS=True); e2.marks = e.marks
    assert e2.state['cohort'] == e.state['cohort']
    assert e2.book_ledger_tick() is False                                  # same bucket persisted: no duplicate line
    after = e2.book_ledger_status()
    for d in (before, after): d.pop('t')
    assert after == before


def test_damaged_cohort_record_stops_stamping_and_ledger():
    e, _ = books_engine()
    e.state['cohort']['book_cap'] = 9999.0
    e._cohort_init()
    assert e.book_ledger_status() is None
    assert e.open_lot(SL, 'BTCUSDT', 'LONG', SG, None, e.equity())
    assert 'cohort_id' not in next(iter(e.state['lots'].values()))


def test_boundary_refuses_unless_flat_and_outside_runtime():
    e, tmp = books_engine()
    assert e.open_lot(SL, 'BTCUSDT', 'LONG', SG, None, e.equity())
    with pytest.raises(RuntimeError, match='not flat'): e.cohort_boundary(tempfile.mkdtemp())
    k = next(iter(e.state['lots'])); e.close_lot(k, 'manual')
    with pytest.raises(RuntimeError, match='outside the runtime'): e.cohort_boundary(os.path.join(tmp, 'packs'))
    e.live = True
    with pytest.raises(RuntimeError, match='PAPER only'): e.cohort_boundary(tempfile.mkdtemp())


def test_boundary_packs_verifies_replays_and_cannot_mix_cohorts():
    e, _ = books_engine()
    old = e.state['cohort']['id']
    assert e.open_lot(SL, 'BTCUSDT', 'LONG', SG, None, e.equity())
    e.close_lot(next(iter(e.state['lots'])), 'manual')
    e.history.append(dict(book='B1_FAST', pnl=-50.0, pnl_gross=-49.0, fees=1.0, closed=E.now_utc().isoformat(timespec='seconds')))
    old_b1 = e.book_ledger_status()['books']['B1_FAST']['realized_net']
    out = e.cohort_boundary(tempfile.mkdtemp(), note='drill')
    assert out['closed'] == old and out['new'] != old and e.state['cohort']['seq'] == 1
    v = RP.verify_pack(out['pack'])
    assert v['ok'] and v['pack_sha'] == out['pack_sha'] and RP.replay_check(out['pack']) == {}
    packed = json.load(open(os.path.join(out['pack'], 'book_ledger_final.json')))
    assert packed['books']['B1_FAST']['realized_net'] == old_b1 == -50.0
    raw = b''.join(open(os.path.join(out['pack'], f), 'rb').read() for f in os.listdir(out['pack']))
    assert b'k' * 16 not in raw and b's' * 16 not in raw
    new = e.book_ledger_status()                                           # the new cohort starts clean
    assert all(b['closed'] == 0 and b['nav'] == 2000 for b in new['books'].values())
    assert e.cohort_rows == [] and len(e.history) == 2                     # trading history untouched by the boundary
    assert e.state['cohort_archive'][-1]['pack_sha'] == out['pack_sha']


# ------------------------------------------------------------------ review r1 regressions
def test_boundary_flushes_writers_and_packs_rotated_runtime_files():
    e, tmp = books_engine()
    calls = []
    for w in (e._fillw, e._auditw, e._enr): w.flush = (lambda n: (lambda *a, **k: calls.append(n)))(type(w).__name__)
    with open(e.F['fills'] + '.1', 'w') as f: f.write('{"kind":"exit"}\n')
    out = e.cohort_boundary(tempfile.mkdtemp())
    assert len(calls) == 3
    names = os.listdir(out['pack'])
    assert 'fills.1.jsonl' in names and 'scope.json' in names and RP.verify_pack(out['pack'])['ok']


@pytest.mark.skipif(os.name != 'nt', reason='case-insensitive paths are a Windows property')
def test_boundary_outside_runtime_check_is_case_insensitive():
    e, tmp = books_engine()
    with pytest.raises(RuntimeError, match='outside the runtime'):
        e.cohort_boundary(os.path.join(tmp.swapcase(), 'packs'))


def test_lost_state_record_restored_from_sidecar_and_cap_since_move_is_drift():
    e, tmp = books_engine()
    cid = e.state['cohort']['id']
    e.state['cohort'] = None
    e.S['CAP_SINCE'] = '2026-10-20T00:00:00+00:00'                          # capital fresh start after the cohort began
    e._cohort_init()
    assert e.state['cohort']['id'] == cid and 'started_utc' in e.cohort_drift


def test_status_reads_copies_while_lots_change(monkeypatch):
    e, _ = books_engine()
    assert e.open_lot(SL, 'BTCUSDT', 'LONG', SG, None, e.equity())
    real = BL.compute
    def racing(c, hist, lots, *a, **k):
        it = iter(lots); first = next(it)
        e.state['lots']['added-mid-read'] = dict(first)                    # the loop thread opens a lot meanwhile
        return real(c, hist, [first] + list(it), *a, **k)
    monkeypatch.setattr(BL, 'compute', racing)
    assert e.book_ledger_status()['books']['B3_TREND']['open'] == 1


def test_settings_pack_drops_telegram_and_workspace_id_is_forbidden():
    e, _ = books_engine()
    e.S['TELEGRAM_CHAT'] = '123456'
    assert not any(k.startswith('TELEGRAM') for k in RP.sanitize_settings(e.S, e._settings_allow()))
    e.cfg['ANTHROPIC_WORKSPACE_ID'] = 'wrkspc-0123456789'
    e.history.append(dict(book=None, note='wrkspc-0123456789', closed=E.now_utc().isoformat(timespec='seconds')))
    with pytest.raises(RP.PackRefused, match='credential value'): e.cohort_boundary(tempfile.mkdtemp())


def test_tick_persists_bucket_before_line_so_a_failed_write_never_repeats(monkeypatch):
    e, _ = books_engine()
    monkeypatch.setattr(E.time, 'time', lambda: 1_800_000_000.0)
    real_open = open
    def boom(p, *a, **k):
        if str(p) == e.F['book_ledger']: raise OSError('disk full')
        return real_open(p, *a, **k)
    monkeypatch.setattr('builtins.open', boom)
    assert e.book_ledger_tick() is False
    monkeypatch.setattr('builtins.open', real_open)
    assert e.state['book_ledger']['last_bucket'] == 1_800_000_000 // 900 * 900 and e.book_ledger_tick() is False


def test_verify_pack_checks_folder_name_and_absent_files():
    root = tempfile.mkdtemp(); c = coh()
    path, _ = RP.write_pack(root, c, {'a.json': {}}, {'gone.jsonl': None})
    with open(os.path.join(path, 'gone.jsonl'), 'w') as f: f.write('')
    assert any('absent but present' in p for p in RP.verify_pack(path)['problems'])
    os.remove(os.path.join(path, 'gone.jsonl'))
    moved = os.path.join(root, 'renamed'); os.rename(path, moved)
    assert any('folder name' in p for p in RP.verify_pack(moved)['problems'])


# ------------------------------------------------------------------ review r2 regressions
def test_boundary_flushes_enricher_before_fills_and_records_undrained():
    e, _ = books_engine()
    order = []
    e._enr.flush = lambda *a, **k: order.append('enr') or True
    e._fillw.flush = lambda *a, **k: order.append('fills') or False          # fills writer timed out
    e._auditw.flush = lambda *a, **k: order.append('audit') or True
    out = e.cohort_boundary(tempfile.mkdtemp())
    assert order == ['enr', 'fills', 'audit']
    assert json.load(open(os.path.join(out['pack'], 'scope.json')))['undrained_writers'] == ['fills']


def test_missing_sidecar_is_rewritten_and_a_disagreeing_one_stops_stamping():
    e, tmp = books_engine()
    os.remove(e.F['cohort'])
    e._cohort_init()
    assert json.load(open(e.F['cohort']))['id'] == e.state['cohort']['id']
    with open(e.F['cohort'], 'w') as f: json.dump(coh(seq=7), f)          # state restored from an older backup, say
    e._cohort_init()
    assert e.book_ledger_status() is None
    assert e.open_lot(SL, 'BTCUSDT', 'LONG', SG, None, e.equity())
    assert 'cohort_id' not in next(iter(e.state['lots'].values()))
    e.close_lot(next(iter(e.state['lots'])), 'manual')
    with pytest.raises(RuntimeError, match='disputed'): e.cohort_boundary(tempfile.mkdtemp())


def test_unreadable_sidecar_is_a_dispute_and_is_never_overwritten():
    e, _ = books_engine()
    with open(e.F['cohort'], 'w') as f: f.write('{"id": "CH-trunc')               # truncated / damaged
    e._cohort_init()
    assert open(e.F['cohort']).read() == '{"id": "CH-trunc' and e.book_ledger_status() is None


# ------------------------------------------------------------------ Codex review (6235416696) regressions
def restart(e, tmp):
    e2 = E.Engine(dict(MODE='paper', API_KEY='k' * 16, API_SECRET='s' * 16), tmp)
    e2.S.update(PAPER_BOOKS=True, PAPER_BOOK_CAP=2000.0, CAP_SINCE=e.S['CAP_SINCE'], CAPITAL_CAP=e.S['CAPITAL_CAP'],
                UNIVERSE=e.S['UNIVERSE'], SYMBOLS_ON=e.S['SYMBOLS_ON'], MAX_LEVERAGE=e.S.get('MAX_LEVERAGE'))
    e2._cohort_init()
    return e2


@pytest.mark.parametrize('where', ['state', 'sidecar'])
def test_boundary_interrupted_after_pack_completes_on_restart_and_on_retry(monkeypatch, where):
    for how in ('restart', 'retry'):
        e, tmp = books_engine()
        old, dest = e.state['cohort']['id'], tempfile.mkdtemp()
        if where == 'state':
            real_w = e._save_wal
            calls = []
            def wal():                                                     # the intent save passes, the completion save fails
                calls.append(1)
                if len(calls) > 1: raise OSError('crash')
                return real_w()
            monkeypatch.setattr(e, '_save_wal', wal)
        else:
            real = e._save_aux
            monkeypatch.setattr(e, '_save_aux', lambda n, o: (_ for _ in ()).throw(OSError('crash')) if n == 'cohort' else real(n, o))
        with pytest.raises(OSError): e.cohort_boundary(dest)
        assert os.path.isdir(os.path.join(dest, old))                     # the pack was published before the crash
        if where == 'state': assert e.state['cohort']['id'] == old and e.state['cohort_closing']   # memory rolled back
        monkeypatch.undo()
        e2 = restart(e, tmp) if how == 'restart' else e
        if how == 'retry' and where == 'state': e2.cohort_boundary(dest)   # same call again: completes, never "exists"
        assert e2.state['cohort']['id'] != old and not e2.state.get('cohort_closing')
        assert [a['id'] for a in e2.state['cohort_archive']] == [old] and RP.verify_pack(os.path.join(dest, old))['ok']
        if how == 'restart':
            assert not getattr(e2, '_cohort_conflict', False) and json.load(open(e2.F['cohort']))['id'] == e2.state['cohort']['id']
            assert e2.book_ledger_status() is not None


def test_interrupted_boundary_with_a_tampered_pack_is_not_completed():
    e, tmp = books_engine()
    old, dest = e.state['cohort']['id'], tempfile.mkdtemp()
    e.state['cohort_closing'] = dict(old=dict(e.state['cohort'], ended_utc=E.now_utc().isoformat(timespec='seconds')), dest=dest,
                                     note='', drift=[])
    os.makedirs(os.path.join(dest, old)); open(os.path.join(dest, old, 'MANIFEST.json'), 'w').write('{}')
    with pytest.raises(RuntimeError, match='does not verify'): e.cohort_boundary(dest)
    assert e.state['cohort']['id'] == old and e.state['cohort_closing'] and e.book_ledger_status() is None


def test_evidence_retention_never_changes_trading_history_window():
    e, _ = books_engine(COMPOUND=True)
    cid = e.state['cohort']['id']
    for i in range(3000):
        r = row('B3_TREND', 100, fees=0, cohort_id=cid, id=f'r{i}'); e.history.append(r); e._cohort_row(r)
    assert e.open_lot(SL, 'BTCUSDT', 'LONG', SG, None, e.equity())
    e.close_lot(next(iter(e.state['lots'])), 'manual')
    assert len(e.history) == 3000 and e.history[0]['id'] == 'r1'          # baseline trading window, oldest row dropped
    base = sum(h.get('pnl') or 0 for h in e.history if h.get('closed', '') >= e.S['CAP_SINCE'])
    assert e.capital_info()['realized'] == round(base, 2)
    assert e.book_ledger_status()['books']['B3_TREND']['closed'] == 3001   # the ledger still sees every cohort trade


def test_evidence_rows_survive_restart_and_heal_from_history():
    e, tmp = books_engine()
    assert e.open_lot(SL, 'BTCUSDT', 'LONG', SG, None, e.equity())
    e.close_lot(next(iter(e.state['lots'])), 'manual')
    os.remove(e.F['cohort_rows'])                                          # a lost append: history still holds the row
    e.save_state()
    e2 = restart(e, tmp)
    assert [r['id'] for r in e2.cohort_rows] == [e.history[-1]['id']]
    assert e2.book_ledger_status()['books']['B3_TREND']['closed'] == 1


def test_boundary_refuses_drift_unless_accepted_and_seals_it_verifiably():
    e, _ = books_engine(MAX_LEVERAGE=5)
    e._cohort_init()
    e.state['cohort'] = None; e.state.pop('cohort_settings', None)
    os.remove(e.F['cohort']); e._cohort_init()                             # cohort started under MAX_LEVERAGE 5
    e.S['MAX_LEVERAGE'] = 3
    with pytest.raises(RuntimeError, match='settings_sha changed'): e.cohort_boundary(tempfile.mkdtemp())
    out = e.cohort_boundary(tempfile.mkdtemp(), accept_drift=True)
    v = RP.verify_pack(out['pack'])
    assert v['ok'] and not v['clean'] and 'settings_sha' in v['drift']
    assert json.load(open(os.path.join(out['pack'], 'settings_at_start.json')))['MAX_LEVERAGE'] == 5
    assert json.load(open(os.path.join(out['pack'], 'book_ledger_final.json')))['drift'] == v['drift']
    mp = os.path.join(out['pack'], 'MANIFEST.json')                        # strip the recorded drift: verify catches it
    man = json.load(open(mp)); man['meta'] = {}; json.dump(man, open(mp, 'w'))
    assert any('drift is not recorded' in p for p in RP.verify_pack(out['pack'])['problems'])


def test_boundary_refuses_an_active_grid():
    e, _ = books_engine()
    e.state['grids'] = {'G|BTCUSDT': dict(symbol='BTCUSDT')}
    with pytest.raises(RuntimeError, match=r'not flat \(grids\)'): e.cohort_boundary(tempfile.mkdtemp())


def test_retry_of_a_pending_boundary_reuses_its_end_time_and_destination(monkeypatch):
    e, _ = books_engine()
    old, dest = e.state['cohort']['id'], tempfile.mkdtemp()
    real_w, calls = e._save_wal, []
    monkeypatch.setattr(e, '_save_wal', lambda: calls.append(1) or (real_w() if len(calls) == 1 else (_ for _ in ()).throw(OSError('x'))))
    with pytest.raises(OSError): e.cohort_boundary(dest)
    monkeypatch.undo()
    ended = e.state['cohort_closing']['old']['ended_utc']
    with pytest.raises(RuntimeError, match='pending for'): e.cohort_boundary(tempfile.mkdtemp())
    later = E.now_utc() + E.timedelta(hours=1)
    monkeypatch.setattr(E, 'now_utc', lambda: later)                     # a fresh boundary would get a new end time
    out = e.cohort_boundary(dest)
    assert json.load(open(os.path.join(out['pack'], 'cohort.json')))['ended_utc'] == ended == e.state['cohort']['started_utc']


def test_completing_the_same_transition_twice_never_duplicates_the_archive():
    e, _ = books_engine()
    pend = dict(old=dict(e.state['cohort'], ended_utc=E.now_utc().isoformat(timespec='seconds')), dest=tempfile.mkdtemp(), note='', drift=[])
    e._complete_boundary(pend); e._complete_boundary(pend)
    assert [a['id'] for a in e.state['cohort_archive']] == [pend['old']['id']]


def test_missing_settings_snapshot_is_drift():
    e, _ = books_engine()
    e.state.pop('cohort_settings'); e._cohort_init()
    assert e.cohort_drift == ['settings_snapshot']
    with pytest.raises(RuntimeError, match='settings_snapshot'): e.cohort_boundary(tempfile.mkdtemp())


def test_verify_rejects_a_start_snapshot_that_is_not_the_cohorts():
    root = tempfile.mkdtemp(); c = coh()
    path, _ = RP.write_pack(root, c, {'settings_at_start.json': {'MAX_LEVERAGE': 9}, 'settings_at_end.json': SETS})
    assert any('settings_at_start does not match' in p for p in RP.verify_pack(path)['problems'])


# ------------------------------------------------------------------ review r4 regressions
def test_restart_completing_a_boundary_packs_the_closing_cohorts_rows(monkeypatch):
    e, tmp = books_engine()
    assert e.open_lot(SL, 'BTCUSDT', 'LONG', SG, None, e.equity())
    e.close_lot(next(iter(e.state['lots'])), 'manual')
    tid, dest = e.history[-1]['id'], tempfile.mkdtemp()
    monkeypatch.setattr(RP, 'write_pack', lambda *a, **k: (_ for _ in ()).throw(OSError('crash before the pack')))
    with pytest.raises(OSError): e.cohort_boundary(dest)
    monkeypatch.undo()
    e2 = restart(e, tmp)                                                   # resumes with an empty in-memory store
    old = e2.state['cohort_archive'][0]
    packed = json.load(open(os.path.join(old['pack'], 'history.json')))
    assert [r['id'] for r in packed] == [tid] and RP.replay_check(old['pack']) == {}


def test_pending_boundary_is_never_completed_unless_flat(monkeypatch):
    e, _ = books_engine()
    dest = tempfile.mkdtemp()
    monkeypatch.setattr(RP, 'write_pack', lambda *a, **k: (_ for _ in ()).throw(RP.PackRefused('scan')))
    with pytest.raises(RP.PackRefused): e.cohort_boundary(dest)
    monkeypatch.undo()
    assert e.open_lot(SL, 'BTCUSDT', 'LONG', SG, None, e.equity())        # trading went on while it was pending
    with pytest.raises(RuntimeError, match='pending but not flat'): e.cohort_boundary(dest)


def test_pending_boundary_can_be_aborted_only_before_its_pack_exists(monkeypatch):
    e, _ = books_engine()
    cid, dest = e.state['cohort']['id'], tempfile.mkdtemp()
    monkeypatch.setattr(RP, 'write_pack', lambda *a, **k: (_ for _ in ()).throw(OSError('gone')))
    with pytest.raises(OSError): e.cohort_boundary(dest)
    monkeypatch.undo()
    assert e.book_ledger_status() is None and e.cohort_boundary_abort() is True
    assert e.state['cohort']['id'] == cid and e.book_ledger_status() is not None and e.cohort_boundary_abort() is False
    real_w, calls = e._save_wal, []
    monkeypatch.setattr(e, '_save_wal', lambda: calls.append(1) or (real_w() if len(calls) == 1 else (_ for _ in ()).throw(OSError('x'))))
    with pytest.raises(OSError): e.cohort_boundary(dest)                  # pack published, completion save failed
    monkeypatch.undo()
    with pytest.raises(RuntimeError, match='already published'): e.cohort_boundary_abort()


def test_end_settings_are_frozen_when_the_boundary_is_requested(monkeypatch):
    e, tmp = books_engine()
    monkeypatch.setattr(RP, 'write_pack', lambda *a, **k: (_ for _ in ()).throw(OSError('crash')))
    dest = tempfile.mkdtemp()
    with pytest.raises(OSError): e.cohort_boundary(dest)
    monkeypatch.undo()
    e.S['MAX_LEVERAGE'] = 2                                                # changed between the crash and the retry
    out = e.cohort_boundary(dest)
    v = RP.verify_pack(out['pack'])
    assert v['ok'] and v['clean']


def test_abort_reloads_the_open_cohorts_evidence_rows(monkeypatch):
    e, _ = books_engine()
    assert e.open_lot(SL, 'BTCUSDT', 'LONG', SG, None, e.equity())
    e.close_lot(next(iter(e.state['lots'])), 'manual')
    monkeypatch.setattr(RP, 'write_pack', lambda *a, **k: (_ for _ in ()).throw(OSError('gone')))
    with pytest.raises(OSError): e.cohort_boundary(tempfile.mkdtemp())
    monkeypatch.undo()
    e.cohort_rows = []                                                     # as after a restart that could not complete it
    assert e.cohort_boundary_abort() and e.book_ledger_status()['books']['B3_TREND']['closed'] == 1


def test_failed_abort_keeps_the_intent_in_memory(monkeypatch):
    e, _ = books_engine()
    monkeypatch.setattr(RP, 'write_pack', lambda *a, **k: (_ for _ in ()).throw(OSError('gone')))
    with pytest.raises(OSError): e.cohort_boundary(tempfile.mkdtemp())
    monkeypatch.setattr(e, '_save_wal', lambda: (_ for _ in ()).throw(OSError('disk')))
    with pytest.raises(OSError): e.cohort_boundary_abort()
    assert e.state['cohort_closing'] and e.book_ledger_status() is None
