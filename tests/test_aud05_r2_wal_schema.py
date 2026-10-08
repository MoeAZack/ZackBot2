"""AUD-05 r2 (Codex review on PR #30 at 931636a):
P1-A write-ahead ownership: every risk-adding order (entry incl. maker/fallback, add, provisional stop) is recorded durably
     with its client id BEFORE it is sent; a runtime safety-file write failure latches `state_untrusted` (no new risk,
     protection keeps running) and pauses entries.
P1-B secrets never enter the settings persistence boundary.
P2-A a missing state.json on an initialized installation is not a first run.
P2-B versioned schemas, validated before use; canonical serialisation."""
import errno, glob, json, math, os, threading
import pytest
import test_safety as TS
from test_safety import mk_engine, SL, SG

E, BC = TS.E, TS.BC
TOKEN = '123456789:AAHsecretTelegramTokenValue_0123'


class CidX(TS.FakeX):
    """A fake exchange that keeps every order under the client id the engine chose (Binance's order record)."""
    def __init__(self, *a, **k):
        super().__init__(*a, **k); self.orders = {}; self.stop_cids = {}; self.crash = None

    def open(self, s, ps, q, cid=None):
        r = super().open(s, ps, q)
        self.orders[cid] = dict(status='FILLED', executedQty=str(q), avgPrice=str(self.mark[s]), clientOrderId=cid)
        if self.crash == 'after_open': raise Crash('process died after the order was sent')
        return dict(r, status='FILLED', clientOrderId=cid)

    def get_order(self, s, cid):
        return self.orders.get(cid)

    def stop(self, s, ps, q, p, cid=None, acid=None):
        if self.crash == 'before_stop': raise Crash('process died before the stop was sent')
        tag = super().stop(s, ps, q, p)
        self.stop_cids[f'c:{cid}'] = tag
        if self.crash == 'after_stop': raise Crash('process died after the stop was sent')
        return tag

    def stop_status(self, s, tag, retry=None):
        t = self.stop_cids.get(tag, tag)
        return 'NEW' if t in self.stops else None


class Crash(BaseException):
    """A process death in the middle of an order path (not an Exception: nothing in the engine may catch it)."""


def mk(**S):
    TS.FakeX, real = CidX, TS.FakeX
    try: e, tmp = mk_engine(**S)
    finally: TS.FakeX = real
    return e, tmp


def restart(tmp, fake):
    E.Futures = lambda *a, **k: fake
    e = E.Engine(dict(MODE='paper', API_KEY='k' * 16, API_SECRET='s' * 16), tmp)
    e.data = e.trade; e.connect(); e.marks = e.trade.marks()
    return e


@pytest.fixture(autouse=True)
def quiet(monkeypatch):
    monkeypatch.setattr(E.Engine, 'notify', lambda self, t: None)
    monkeypatch.setattr(E, '_sleep', lambda s: None)


def fail_writes(monkeypatch, name, after=0, exc=None):
    """Writes of <name> (state.json / settings.json) fail with ENOSPC after `after` good ones."""
    real, n = E._write_durable, [0]
    def w(path, obj, strict=False):
        if os.path.basename(path) == name:
            n[0] += 1
            if n[0] > after: raise exc or OSError(errno.ENOSPC, 'No space left on device')
        return real(path, obj, strict)
    monkeypatch.setattr(E, '_write_durable', w)
    return n


def disk_state(e):
    return json.load(open(e.F['state']))


# ------------------------------------------------------------------ P1-A: write-ahead entry
@pytest.mark.parametrize('how', ['enospc', 'replace_permission'])
def test_entry_is_not_sent_when_its_write_ahead_record_cannot_be_saved(monkeypatch, how):
    """Codex repro: valid empty state, then state writes fail -> open_lot must not reach Binance."""
    e, tmp = mk(); e.save_state()
    if how == 'enospc': fail_writes(monkeypatch, 'state.json')
    else:
        real = os.replace
        def rep(src, dst):
            if os.path.basename(dst) == 'state.json': raise PermissionError(13, 'held by another process')
            return real(src, dst)
        monkeypatch.setattr(E.os, 'replace', rep)
    assert not e.open_lot(SL, 'BTCUSDT', 'LONG', SG, None, e.equity())
    assert 'open' not in e.trade.calls and not e.trade.pos.get(('BTCUSDT', 'LONG')), 'nothing sent'
    assert e.state_untrusted.get('state') and e.S['ENTRIES_PAUSED'] is True
    assert e.health['incidents']['persist|state']['open']
    assert json.load(open(e.F['settings']))['ENTRIES_PAUSED'] is True, 'the pause is durable (settings still writable)'
    assert e.state['unconfirmed_entries'] == {}


def test_accepted_entry_whose_lot_save_fails_is_protected_latched_and_settled_after_restart(monkeypatch):
    """The write-ahead record is durable; the post-fill save fails: the stop is still placed, new risk is blocked, and a
    restart books the lot from Binance's order record."""
    e, tmp = mk(); e.save_state()
    fail_writes(monkeypatch, 'state.json', after=1)                   # only the write-ahead record lands
    assert e.open_lot(SL, 'BTCUSDT', 'LONG', SG, None, e.equity())
    k = next(iter(e.state['lots'])); q = e.state['lots'][k]['qty']
    assert e.state['lots'][k]['stop_id'] in e.trade.stops, 'protection still placed'
    assert e.state_untrusted and e.entry_block(SL, 'ETHUSDT', 'LONG').startswith('data file not saved')
    assert list(disk_state(e)['unconfirmed_entries'].values())[0]['cid'], 'disk holds the intent with its client id'
    monkeypatch.undo(); monkeypatch.setattr(E, '_sleep', lambda s: None); monkeypatch.setattr(E.Engine, 'notify', lambda s, t: None)
    e2 = restart(tmp, e.trade)
    e2.reconcile(e2.equity())
    lots = list(e2.state['lots'].values())
    assert len(lots) == 1 and lots[0]['qty'] == pytest.approx(q) and not e2.state['unconfirmed_entries']
    e2.reconcile(e2.equity()); assert not e2.untracked


@pytest.mark.parametrize('when', ['before_send', 'after_send'])
def test_crash_around_the_send_is_settled_from_the_order_record(monkeypatch, when):
    e, tmp = mk(); e.save_state()
    if when == 'after_send': e.trade.crash = 'after_open'
    else:
        def died(*a, **k): raise Crash('process died before the send')
        monkeypatch.setattr(e.trade, 'open', died)
    with pytest.raises(Crash):
        e.open_lot(SL, 'BTCUSDT', 'LONG', SG, None, e.equity())
    assert len(disk_state(e)['unconfirmed_entries']) == 1, 'the intent was durable before the send'
    monkeypatch.undo(); monkeypatch.setattr(E, '_sleep', lambda s: None); monkeypatch.setattr(E.Engine, 'notify', lambda s, t: None)
    e.trade.crash = None
    e2 = restart(tmp, e.trade)
    for u in e2.state['unconfirmed_entries'].values(): u['t'] -= 60           # past UNCONF_DROP_S
    e2.reconcile(e2.equity())
    if when == 'after_send':
        assert len(e2.state['lots']) == 1 and next(iter(e2.state['lots'].values()))['qty'] == pytest.approx(e.trade.pos[('BTCUSDT', 'LONG')])
    else:
        assert e2.state['lots'] == {} and not e2.state['unconfirmed_entries'] and not e.trade.pos.get(('BTCUSDT', 'LONG'))


def test_ambiguous_entry_answer_keeps_the_write_ahead_record(monkeypatch):
    e, tmp = mk(); e.save_state()
    real = e.trade.open
    def lost(s, ps, q, cid=None):
        real(s, ps, q, cid=cid); raise BC.AmbiguousOrder('timeout', f'c:{cid}')
    monkeypatch.setattr(e.trade, 'open', lost)
    assert not e.open_lot(SL, 'BTCUSDT', 'LONG', SG, None, e.equity())
    ue = list(disk_state(e)['unconfirmed_entries'].values())
    assert len(ue) == 1 and ue[0]['cid'] in e.trade.orders
    e.reconcile(e.equity())
    assert len(e.state['lots']) == 1 and not e.state['unconfirmed_entries']


def test_refused_entry_drops_its_write_ahead_record(monkeypatch):
    e, tmp = mk()
    e.trade.fail.add('open')
    with pytest.raises(BC.BinanceError):
        e.open_lot(SL, 'BTCUSDT', 'LONG', SG, None, e.equity())
    assert e.state['unconfirmed_entries'] == {} and disk_state(e)['unconfirmed_entries'] == {}


# ------------------------------------------------------------------ P1-A: adds, provisional stop, maker, grid
def test_add_is_not_sent_when_its_write_ahead_record_cannot_be_saved(monkeypatch):
    e, tmp = mk(); k = TS.opened(e); lot = e.state['lots'][k]
    n0 = e.trade.calls.count('open')
    fail_writes(monkeypatch, 'state.json')
    with pytest.raises(RuntimeError, match='write-ahead|not sent'):
        e._add_qty(lot, lot['qty'] / 2, 100.0, 'pyramid_add')
    assert e.trade.calls.count('open') == n0 and 'pending' not in lot and e.state_untrusted


def test_add_write_ahead_record_is_on_disk_before_the_send(monkeypatch):
    e, tmp = mk(); k = TS.opened(e); lot = e.state['lots'][k]
    seen = []
    real = e.trade.open
    def spy(s, ps, q, cid=None):
        seen.append(disk_state(e)['lots'][k].get('pending')); return real(s, ps, q, cid=cid)
    monkeypatch.setattr(e.trade, 'open', spy)
    assert e._add_qty(lot, 0.5, 100.0, 'pyramid_add')
    assert seen and seen[0]['kind'] == 'add' and seen[0]['cid'] and 'pending' not in lot


def test_latched_engine_blocks_every_risk_path_but_keeps_closing_and_stops(monkeypatch):
    e, tmp = mk(); k = TS.opened(e)
    fail_writes(monkeypatch, 'state.json'); e.save_state()
    assert e.state_untrusted
    e.S['ENTRIES_PAUSED'] = False                                      # even if someone resumes entries
    blk = e.entry_block(SL, 'ETHUSDT', 'LONG')
    assert blk and blk.startswith('data file not saved')
    assert e._add_block(e.state['lots'][k], 0.1, 100.0).startswith('data file not saved')
    assert e.grids._can_add(dict(sym='ETHUSDT', lots={}, cells=[]), 'LONG') is False
    rec = dict(symbol='ETHUSDT', side='LONG', qty=1.0, filled=0.0, n=0, status='between')
    with pytest.raises(RuntimeError, match='not sent'):
        e._maker_place(rec)
    e.close_lot(k, 'manual', 100.0)                                    # protection paths keep running
    assert k not in e.state['lots'] and not e.trade.pos.get(('BTCUSDT', 'LONG'))
    monkeypatch.undo(); monkeypatch.setattr(E.Engine, 'notify', lambda s, t: None)
    assert e.save_state() is True and not e.state_untrusted, 'cleared only by a successful full save'


def _unconfirmed_with_size(e):
    """An entry whose answer was lost while its size is on Binance (AUD-03b) - its provisional stop is due."""
    plan = dict(sl=dict(id='T', key='ema_mom', tf='4h', name='T', share=0.5), sym='BTCUSDT', side='LONG', qty=1.0, stop_dist=5.0,
                atr=2.0, g={}, risk_usd=5.0, eq=500.0, manual=False, reason='signal', px=100.0, sg=dict(time='t', close=100.0))
    e._remember_unconfirmed(plan, 'zbunknown0000000000000000')
    e.trade.pos[('BTCUSDT', 'LONG')] = 1.0


def test_provisional_stop_is_not_sent_without_its_write_ahead_record(monkeypatch):
    e, tmp = mk(); _unconfirmed_with_size(e)
    n0 = e.trade.calls.count('stop')
    fail_writes(monkeypatch, 'state.json')
    e.reconcile(e.equity())
    u = next(iter(e.state['unconfirmed_entries'].values()))
    assert e.trade.calls.count('stop') == n0 and not u.get('prov') and not u.get('prov_pending')


def test_provisional_stop_sent_then_crash_is_owned_after_restart(monkeypatch):
    e, tmp = mk(); _unconfirmed_with_size(e)
    e.trade.crash = 'after_stop'
    with pytest.raises(Crash):
        e.reconcile(e.equity())
    pend = next(iter(disk_state(e)['unconfirmed_entries'].values()))['prov_pending']
    assert pend['tag'].startswith('c:') and pend['alt'].startswith('ac:'), 'both client ids owned before the send'
    e.trade.crash = None
    e2 = restart(tmp, e.trade)
    nstops = len(e.trade.stops)
    e2.reconcile(e2.equity())
    u = next(iter(e2.state['unconfirmed_entries'].values()))
    assert u['prov'] == pend['tag'] and len(e.trade.stops) == nstops, 'the stop already on Binance is owned, never doubled'


# ------------------------------------------------------------------ P1-B: secrets
def test_no_secret_in_the_settings_defaults():
    assert 'TELEGRAM_TOKEN' not in E.GLOBAL_DEFAULTS


def _all_files_text(tmp):
    out = {}
    for p in glob.glob(os.path.join(tmp, '*')):
        if os.path.isfile(p): out[os.path.basename(p)] = open(p, 'rb').read().decode('utf-8', 'replace')
    return out


def test_legacy_token_is_migrated_before_the_engine_and_never_written_by_saves_or_recovery(monkeypatch, tmp_path):
    import app as A
    tmp = str(tmp_path)
    legacy = dict(TELEGRAM_TOKEN=TOKEN, TELEGRAM_CHAT='12345', MAX_LEVERAGE=7)
    for p in ('settings.json', 'settings.json.bak'):
        with open(os.path.join(tmp, p), 'w') as f: json.dump(legacy, f)
    cfg, writes = {}, []
    monkeypatch.setattr(A, 'write_cfg', lambda u: writes.append(u))
    A.migrate_settings_secrets(cfg, tmp)
    assert writes == [{'TELEGRAM_TOKEN': TOKEN}] and cfg['TELEGRAM_TOKEN'] == TOKEN
    e = restart(tmp, CidX())
    e.S['MAX_LEVERAGE'] = 8; e.save_settings(); e.save_settings()
    with open(e.F['settings'], 'w') as f: f.write('{"broken')                       # recovery: damaged -> .bak
    e2 = restart(tmp, e.trade); e2.save_settings()
    texts = _all_files_text(tmp)
    assert any(n.startswith('settings.json.corrupt-') for n in texts) and 'settings.json.bak' in texts
    for n, t in texts.items():
        assert TOKEN not in t and 'TELEGRAM_TOKEN' not in t, n


def test_engine_alone_never_writes_a_legacy_token(tmp_path):
    tmp = str(tmp_path)
    with open(os.path.join(tmp, 'settings.json'), 'w') as f: json.dump(dict(TELEGRAM_TOKEN=TOKEN, ENTRIES_PAUSED=True), f)
    e = restart(tmp, CidX())                                           # init saves (CAP_SINCE, pause ...) happen here
    assert 'TELEGRAM_TOKEN' not in e.S
    e.save_settings(); e.save_settings()
    assert TOKEN not in open(e.F['settings']).read() and TOKEN not in open(e.F['settings'] + '.bak').read()


def test_strict_serialisation_is_canonical_and_rejects_unsupported_values(tmp_path):
    p = str(tmp_path / 'state.json')
    E.save_json(p, dict(b=1, a=2), strict=True); one = open(p, 'rb').read()
    E.save_json(p, dict(a=2, b=1), strict=True); assert open(p, 'rb').read() == one
    for bad in (dict(a={1, 2}), dict(a=float('nan')), dict(a=E.now_utc())):
        with pytest.raises((TypeError, ValueError)):
            E.save_json(p, bad, strict=True)
        assert open(p, 'rb').read() == one and not glob.glob(str(tmp_path / '*.tmp'))


# ------------------------------------------------------------------ P2-A: install marker
def test_first_run_initializes_once():
    e, tmp = mk()
    assert os.path.exists(e.F['install']) and os.path.exists(e.F['state']) and e.S['ENTRIES_PAUSED'] is False
    acct = json.load(open(e.F['install']))['account']
    assert acct['mode'] == 'paper' and len(acct['key']) == 16 and 'k' * 16 not in json.dumps(acct)
    e2 = restart(tmp, e.trade)
    assert e2.S['ENTRIES_PAUSED'] is False and not e2.integrity


def test_deleted_state_on_an_initialized_install_fails_closed():
    e, tmp = mk(); TS.opened(e); e.save_state()
    for p in (e.F['state'], e.F['state'] + '.bak'): os.remove(p)
    e2 = restart(tmp, e.trade)
    assert e2.S['ENTRIES_PAUSED'] is True and e2.integrity['state']['status'] == 'failed_closed'
    assert 'initialized installation' in e2.health['incidents']['integrity|state']['msg']
    e2.reconcile(e2.equity()); e2.reconcile(e2.equity())
    assert 'BTCUSDT|LONG' in e2.untracked, 'exchange truth reported'


def test_legacy_install_without_marker_is_upgraded_not_paused():
    e, tmp = mk(); TS.opened(e); e.save_state()
    os.remove(e.F['install'])
    e2 = restart(tmp, e.trade)
    assert e2.S['ENTRIES_PAUSED'] is False and len(e2.state['lots']) == 1 and os.path.exists(e2.F['install'])


def test_other_account_with_open_records_pauses():
    e, tmp = mk(); TS.opened(e); e.save_state()
    doc = json.load(open(e.F['install'])); doc['account']['key'] = 'ffffffffffffffff'
    with open(e.F['install'], 'w') as f: json.dump(doc, f)
    e2 = restart(tmp, e.trade)
    assert e2.S['ENTRIES_PAUSED'] is True and e2.health['incidents']['integrity|install-account']['open']


# ------------------------------------------------------------------ P2-B: schemas
def _valid_state(e):
    k = TS.opened(e)
    _unconfirmed_with_size(e); e.trade.pos[('BTCUSDT', 'LONG')] += 0                # keep
    e.state['resting_entries']['ME|T|ETHUSDT|LONG'] = dict(key='ME|T|ETHUSDT|LONG', sleeve='T', symbol='ETHUSDT', side='LONG',
                                                           qty=1.0, filled=0.0, cost=0.0, n=1, t0=1.0, status='open',
                                                           cid='zm0123456789abcdef012345', plan=dict(px=50.0))
    e.state['pending_entries']['T|ETHUSDT'] = dict(sleeve='T', symbol='ETHUSDT', side='LONG', ext=50.0, atr=1.0, dev=0.5,
                                                   until=9e9, sg={}, tf='4h', created=1.0)
    e.state['orphans'] = [['BTCUSDT', 'o:99']]
    e.state['short_seen'] = {'BTCUSDT|LONG': 1}; e.state['over_seen'] = {'ETHUSDT|LONG': 1}
    e.save_state(); e.save_state()                                     # .bak = the same valid document
    return k


def _first(d): return next(iter(d.values()))


MUTATIONS = {
    'lot.side': lambda s: _first(s['lots']).update(side='BOTH'),
    'lot.qty_nan': lambda s: _first(s['lots']).update(qty=float('nan')),
    'lot.qty_str': lambda s: _first(s['lots']).update(qty='1.0'),
    'lot.avg_zero': lambda s: _first(s['lots']).update(avg=0),
    'lot.symbol': lambda s: _first(s['lots']).update(symbol='btc/usdt'),
    'lot.stop_id': lambda s: _first(s['lots']).update(stop_id='12345'),
    'lot.stop_miss': lambda s: _first(s['lots']).update(stop_miss=-1),
    'lot.stop_miss_why': lambda s: _first(s['lots']).update(stop_miss_why='gone'),
    'lot.pending_kind': lambda s: _first(s['lots']).update(pending=dict(kind='open', qty=1.0, t=1.0, cid='zbx')),
    'lot.pending_qty': lambda s: _first(s['lots']).update(pending=dict(kind='add', qty=None, t=1.0, cid='zbx')),
    'lot.pending_cid': lambda s: _first(s['lots']).update(pending=dict(kind='add', qty=1.0, t=1.0, cid='bad cid!')),
    'ue.side': lambda s: _first(s['unconfirmed_entries']).update(side='long'),
    'ue.qty': lambda s: _first(s['unconfirmed_entries']).update(qty=0),
    'ue.prov': lambda s: _first(s['unconfirmed_entries']).update(prov='stop-1'),
    'ue.prov_pending': lambda s: _first(s['unconfirmed_entries']).update(prov_pending=dict(tag='c:zb1', qty=1.0, stop=-5, t=1.0)),
    'ue.prov_pending_tag': lambda s: _first(s['unconfirmed_entries']).update(prov_pending=dict(tag='zb1', qty=1.0, stop=95.0, t=1.0)),
    'ue.plan': lambda s: _first(s['unconfirmed_entries']).update(plan=None),
    'resting.status': lambda s: _first(s['resting_entries']).update(status='working'),
    'resting.cid': lambda s: _first(s['resting_entries']).update(cid=None),
    'resting.filled': lambda s: _first(s['resting_entries']).update(filled=-1.0),
    'trail.until': lambda s: _first(s['pending_entries']).update(until='later'),
    'trail.side': lambda s: _first(s['pending_entries']).update(side='UP'),
    'orphans.shape': lambda s: s.update(orphans=[['BTCUSDT']]),
    'orphans.tag': lambda s: s.update(orphans=[['BTCUSDT', 'nope']]),
    'short_seen': lambda s: s.update(short_seen={'BTCUSDT|LONG': 'x'}),
    'over_seen': lambda s: s.update(over_seen={'BTCUSDT|LONG': -2}),
    'lots.type': lambda s: s.update(lots=[]),
    'halted': lambda s: s.update(halted='no'),
    'version_newer': lambda s: s.update(schema_version=99),
}


@pytest.mark.parametrize('name', sorted(MUTATIONS))
def test_every_ownership_record_is_validated_before_use(name):
    e, tmp = mk(); _valid_state(e)
    doc = disk_state(e); MUTATIONS[name](doc)
    with open(e.F['state'], 'w') as f: json.dump(doc, f)                            # syntactically valid JSON
    e2 = restart(tmp, e.trade)
    assert e2.integrity['state']['status'] == 'restored_from_backup', name
    assert 'invalid' in e2.health['incidents']['integrity|state']['msg'] and len(glob.glob(e.F['state'] + '.corrupt-*')) == 1
    assert e2.S['ENTRIES_PAUSED'] is True


def test_valid_state_round_trips_and_legacy_v0_is_migrated():
    e, tmp = mk(); _valid_state(e)
    doc = disk_state(e); assert doc['schema_version'] == E.STATE_SCHEMA
    doc.pop('schema_version')
    with open(e.F['state'], 'w') as f: json.dump(doc, f)                            # an unversioned (pre-r2) file
    e2 = restart(tmp, e.trade)
    assert not e2.integrity and e2.S['ENTRIES_PAUSED'] is False and len(e2.state['lots']) == 1
    e2.save_state(); assert disk_state(e2)['schema_version'] == E.STATE_SCHEMA


@pytest.mark.parametrize('bad', [dict(MAX_LEVERAGE='x'), dict(ENTRIES_PAUSED='no'), dict(UNIVERSE=5), dict(UNIVERSE=['btc usdt']),
                                 dict(SYMBOLS_ON={'BTCUSDT': 1}), dict(SLEEVES=[dict(id='A', key='k', share=float('nan'), risk=0.01)]),
                                 dict(CAPITAL_CAP=-1), dict(ENTRY_ORDER='limit'), dict(schema_version=7)])
def test_invalid_settings_are_quarantined_like_corrupt(bad):
    e, tmp = mk(); e.S['MAX_LEVERAGE'] = 7; e.save_settings(); e.save_settings()
    doc = json.load(open(e.F['settings'])); doc.update(bad)
    with open(e.F['settings'], 'w') as f: json.dump(doc, f)
    e2 = restart(tmp, e.trade)
    assert e2.integrity['settings']['status'] == 'restored_from_backup' and e2.S['MAX_LEVERAGE'] == 7 and e2.S['ENTRIES_PAUSED']


# ------------------------------------------------------------------ concurrency / re-entrancy
def test_commit_settings_races_the_engine_thread_without_divergence():
    e, tmp = mk(); TS.opened(e)
    errs, stop = [], threading.Event()
    def engine_thread():
        try:
            while not stop.is_set():
                with e.lock:
                    e.manage(e.trade.marks()); e.save_state(); e.save_settings()
        except Exception as ex: errs.append(ex)
    t = threading.Thread(target=engine_thread); t.start()
    try:
        for i in range(40):
            with e.lock:
                ns = json.loads(json.dumps(e.S)); ns['MAX_LEVERAGE'] = 2 + i % 20
                e.commit_settings(ns)
    finally:
        stop.set(); t.join(10)
    assert not errs and not t.is_alive()
    disk = json.load(open(e.F['settings']))
    assert disk['MAX_LEVERAGE'] == e.S['MAX_LEVERAGE'] == 2 + 39 % 20 and not e.state_untrusted


def test_nested_saves_under_the_lock_do_not_deadlock():
    e, tmp = mk()
    done = threading.Event()
    def nested():
        with e.lock:
            with e.lock:
                ns = dict(e.S, MAX_LEVERAGE=4)
                e.commit_settings(ns)                                  # commit_settings takes the (re-entrant) lock again
                e._save_wal()
                e._persist_pause()
        done.set()
    t = threading.Thread(target=nested); t.start(); t.join(10)
    assert done.is_set() and e.S['MAX_LEVERAGE'] == 4 and json.load(open(e.F['settings']))['ENTRIES_PAUSED'] is True
