"""AUD-05 r3 (Codex re-review at 458ec50):
P1-1 a missing settings.json on an initialized install fails closed (install marker read before BOTH safety files).
P1-2 persisted grids are validated (cells, quantities against the grid's own budget, lot refs, pending op) and every grid
     order carries a client id owned before the send; a lost / crashed first order is settled from its order record.
P1-3 one ownership predicate; an account change never rebinds ownership (marker preserved, paused until the owner confirms).
P1-4 damaged legacy settings never leave the Telegram token (or its key name) in main / backup / temp / evidence files.
P2-5 install.json: exact version + account structure; invalid -> damaged, initialized, fail closed, not rewritten."""
import glob, hashlib, json, os
import pytest
import test_safety as TS
import test_grid as TG
from test_safety import mk_engine

E, BC = TS.E, TS.BC
TOKEN = '123456789:AAHsecretTelegramTokenValue_0123456'


@pytest.fixture(autouse=True)
def quiet(monkeypatch):
    monkeypatch.setattr(E.Engine, 'notify', lambda self, t: None)
    monkeypatch.setattr(E, '_sleep', lambda s: None)


def restart(tmp, fake):
    E.Futures = lambda *a, **k: fake
    e = E.Engine(dict(MODE='paper', API_KEY='k' * 16, API_SECRET='s' * 16), tmp)
    e.data = e.trade; e.connect(); e.marks = e.trade.marks()
    return e


def inc(e, key):
    return (e.health.get('incidents') or {}).get(key) or {}


# ------------------------------------------------------------------ P1-1
def test_deleted_settings_on_an_initialized_install_fail_closed():
    e, tmp = mk_engine(); e.S['MAX_LEVERAGE'] = 3; e.save_settings(); e.save_settings()
    for p in (e.F['settings'], e.F['settings'] + '.bak'): os.remove(p)
    e2 = restart(tmp, e.trade)
    assert e2.S['ENTRIES_PAUSED'] is True and e2.integrity['settings']['status'] == 'failed_closed'
    assert 'initialized installation' in inc(e2, 'integrity|settings')['msg']
    assert json.load(open(e2.F['settings']))['ENTRIES_PAUSED'] is True


def test_genuine_first_run_writes_settings_state_and_marker_unpaused(tmp_path):
    e = restart(str(tmp_path), TS.FakeX())
    assert e.S['ENTRIES_PAUSED'] is False and not e.integrity
    for k in ('settings', 'state', 'install'): assert os.path.exists(e.F[k]), k
    E.validate_install(json.load(open(e.F['install'])))                 # a marker of the exact supported schema
    e2 = restart(str(tmp_path), e.trade)
    assert e2.S['ENTRIES_PAUSED'] is False and not e2.integrity


def test_legacy_install_without_marker_and_without_settings_is_a_first_run_for_settings():
    e, tmp = mk_engine(); TS.opened(e); e.save_state()
    for p in (e.F['install'], e.F['settings'], e.F['settings'] + '.bak'): os.remove(p)
    e2 = restart(tmp, e.trade)
    assert e2.S['ENTRIES_PAUSED'] is False and len(e2.state['lots']) == 1 and os.path.exists(e2.F['install'])


# ------------------------------------------------------------------ P1-2 grids
class GridCidX(TG.FakeX):
    def __init__(self, *a, **k):
        super().__init__(*a, **k); self.orders = {}; self.crash = None; self.opens = []

    def open(self, s, ps, q, cid=None):
        if self.crash == 'before': raise Crash('died before the send')
        self.opens.append((s, ps, float(q), cid))
        r = super().open(s, ps, q)
        self.orders[cid] = dict(status='FILLED', executedQty=str(q), avgPrice=str(self.mark[s]), clientOrderId=cid)
        if self.crash == 'after': raise Crash('died after the send')
        return dict(r, status='FILLED', clientOrderId=cid)

    def get_order(self, s, cid):
        return self.orders.get(cid)


class Crash(BaseException):
    pass


def grid_engine(tmp=None, fx=None):
    e, gm, tmp = TG.mk(tmp, fx=fx or GridCidX())
    return e, gm, tmp


def started(e, gm):
    with e.lock: gm.start('G1', 'BTCUSDT')
    g = TG.grid_of(e)
    return g, max(c['a'] for c in g['cells'] if c['k'] == 'L')


def test_grid_first_order_carries_a_client_id_owned_on_disk_before_the_send():
    e, gm, tmp = grid_engine(); g, top = started(e, gm)
    seen = []
    real = e.trade.open
    def spy(s, ps, q, cid=None):
        seen.append((cid, json.load(open(e.F['state']))['grids'][g['key']]['op'])); return real(s, ps, q, cid=cid)
    e.trade.open = spy
    TG.tick(e, gm, top - 0.05)
    assert seen and seen[0][0] and seen[0][1]['cid'] == seen[0][0], 'the op with its cid was durable before the send'
    TG.consistent(e)


@pytest.mark.parametrize('when', ['before', 'after'])
def test_grid_crash_around_the_first_order_is_settled_from_its_order_record(when):
    e, gm, tmp = grid_engine(); g, top = started(e, gm)
    e.trade.crash = when
    with pytest.raises(Crash):
        TG.tick(e, gm, top - 0.05)
    op = json.load(open(e.F['state']))['grids'][g['key']]['op']
    assert op and op['kind'] == 'open' and op['cid']
    e.trade.crash = None
    e2, gm2, _ = grid_engine(tmp, fx=e.trade)
    g2 = TG.grid_of(e2)
    if when == 'before':
        g2['op']['t'] -= 60                                             # past the 20 s 'never reached Binance' window
    TG.tick(e2, gm2, top - 0.05)
    TG.tick(e2, gm2, top - 0.05)
    opens = [o for o in e.trade.opens if o[0] == 'BTCUSDT']
    assert len(opens) == 1, opens                                       # exactly one first order on the exchange, never doubled
    lot = TG.lot_of(e2, 'LONG')
    assert lot and lot['qty'] == pytest.approx(e.trade.pos[('BTCUSDT', 'LONG')]) and not e2.untracked
    TG.consistent(e2)


def test_grid_position_fallback_adopts_only_an_exact_match():
    e, gm, tmp = grid_engine(fx=TG.FakeX()); g, top = started(e, gm)    # no order records (nosource)
    TG.ambiguous_once(e, 'open', fill=True)
    TG.tick(e, gm, top - 0.05)
    e.trade.pos[('BTCUSDT', 'LONG')] += g['op']['qty'] * 0.4             # someone else's size on the same side
    TG.tick(e, gm, top - 0.05)
    assert TG.lot_of(e, 'LONG') is None, 'aggregate size that does not match the order exactly is never adopted'


def _grid_state(tmp_huge=False):
    e, gm, tmp = grid_engine(); g, top = started(e, gm)
    TG.tick(e, gm, top - 0.05)                                          # one long cell filled: a lot + an active grid
    e.save_state(); e.save_state()
    return e, gm, tmp, g


def _g(s): return next(iter(s['grids'].values()))


GRID_MUTATIONS = {
    'cell_qty_million': lambda s: _g(s)['cells'][0].update(q=1_000_000.0),
    'cell_qty_nan': lambda s: _g(s)['cells'][0].update(q=float('nan')),
    'cell_bounds': lambda s: _g(s)['cells'][0].update(a=_g(s)['cells'][0]['b'] + 1),
    'cell_kind': lambda s: _g(s)['cells'][0].update(k='X'),
    'cell_filled': lambda s: _g(s)['cells'][0].update(f='yes'),
    'cells_type': lambda s: _g(s).update(cells={}),
    'lines_len': lambda s: _g(s)['lines'].pop(),
    'status': lambda s: _g(s).update(status='running'),
    'mode': lambda s: _g(s).update(mode='both'),
    'capital': lambda s: _g(s).update(capital=0),
    'capital_frac': lambda s: _g(s)['cfg'].update(capital_frac=1000),
    'key': lambda s: _g(s).update(key='G9|ETHUSDT'),
    'lo_hi': lambda s: _g(s).update(lo=_g(s)['hi'] * 2),
    'lots_side': lambda s: _g(s)['lots'].update(BOTH='x'),
    'lots_ref_other_side': lambda s: _g(s)['lots'].update(SHORT=_g(s)['lots']['LONG']),
    'op_kind': lambda s: _g(s).update(op=dict(id='abc123', side='LONG', kind='buy', cells=[0], qty=0.001, px=100.0, t=1.0)),
    'op_cell_index': lambda s: _g(s).update(op=dict(id='abc123', side='LONG', kind='add', cells=[99], qty=0.001, px=100.0, t=1.0)),
    'op_qty': lambda s: _g(s).update(op=dict(id='abc123', side='LONG', kind='add', cells=[0], qty=1e6, px=100.0, t=1.0)),
    'op_cid': lambda s: _g(s).update(op=dict(id='abc123', side='LONG', kind='add', cells=[0], qty=0.001, px=100.0, t=1.0, cid='x y')),
    'history': lambda s: s.update(grid_history={}),
}


@pytest.mark.parametrize('name', sorted(GRID_MUTATIONS))
def test_every_grid_record_is_validated_before_use(name):
    e, gm, tmp, g = _grid_state()
    doc = json.load(open(e.F['state'])); GRID_MUTATIONS[name](doc)
    with open(e.F['state'], 'w') as f: json.dump(doc, f)
    e2, gm2, _ = grid_engine(tmp, fx=e.trade)
    assert e2.integrity['state']['status'] == 'restored_from_backup', name
    assert 'invalid' in inc(e2, 'integrity|state')['msg'] and e2.S['ENTRIES_PAUSED'] is True


def test_a_tampered_million_btc_cell_never_becomes_an_order():
    """Codex repro: a syntactically valid active grid with cell qty 1,000,000 was accepted and the next pass sent it."""
    e, gm, tmp, g = _grid_state()
    os.remove(e.F['state'] + '.bak')
    doc = json.load(open(e.F['state']))
    for c in _g(doc)['cells']: c['q'] = 1_000_000.0
    with open(e.F['state'], 'w') as f: json.dump(doc, f)
    n = len(e.trade.opens)
    e2, gm2, _ = grid_engine(tmp, fx=e.trade)
    for px in (90.0, 95.0, 99.0): TG.tick(e2, gm2, px)
    assert e2.state.get('grids', {}) == {} and e2.S['ENTRIES_PAUSED'] is True
    assert all(q < 1000 for (_, _, q, _) in e.trade.opens[n:]) and len(e.trade.opens) == n


# ------------------------------------------------------------------ P1-3 ownership + account change
def test_ownership_predicate_covers_every_family():
    assert E.ownership_present({}) == [] and E.ownership_present(dict(lots={}, grids={}, orphans=[])) == []
    for f in E.OWNERSHIP_FAMILIES:
        assert E.ownership_present({f: {'x': 1} if f != 'orphans' else [['BTCUSDT', 'o:1']]}) == [f]


def _other_account(e):
    doc = json.load(open(e.F['install'])); doc['account']['key'] = 'f' * 16
    with open(e.F['install'], 'w') as f: json.dump(doc, f)
    return open(e.F['install'], 'rb').read()


def _family_state(family):
    if family == 'grids':
        e, gm, tmp = grid_engine(); started(e, gm)                     # an active grid that holds nothing yet (flat)
        e.save_state(); return e, tmp
    e, tmp = mk_engine()
    if family == 'lots': TS.opened(e)
    elif family == 'unconfirmed_entries':
        plan = dict(sl=dict(id='T'), sym='BTCUSDT', side='LONG', qty=1.0, stop_dist=5.0, px=100.0)
        e._remember_unconfirmed(plan, 'zbintent0000000000000000')
    elif family == 'resting_entries':
        e.state['resting_entries']['ME|T|ETHUSDT|LONG'] = dict(symbol='ETHUSDT', side='LONG', qty=1.0, filled=0.0, cost=0.0, n=1,
                                                               status='open', cid='zm0123456789abcdef012345', plan=dict(px=50.0))
    elif family == 'pending_entries':
        e.state['pending_entries']['T|ETHUSDT'] = dict(sleeve='T', symbol='ETHUSDT', side='LONG', ext=50.0, atr=1.0, dev=0.5,
                                                       until=9e9, sg={}, tf='4h', created=1.0)
    elif family == 'orphans': e.state['orphans'] = [['BTCUSDT', 'o:99']]
    e.save_state()
    return e, tmp


@pytest.mark.parametrize('family', list(E.OWNERSHIP_FAMILIES) if hasattr(E, 'OWNERSHIP_FAMILIES') else
                         ['lots', 'unconfirmed_entries', 'resting_entries', 'pending_entries', 'grids', 'orphans'])
def test_account_change_never_rebinds_any_ownership_family(family):
    e, tmp = _family_state(family)
    before = _other_account(e)
    e2 = restart(tmp, e.trade)
    assert e2.S['ENTRIES_PAUSED'] is True, family
    assert open(e.F['install'], 'rb').read() == before, 'the original marker is preserved'
    assert family in inc(e2, 'integrity|install-account')['msg']
    e3 = restart(tmp, e.trade)
    assert e3.S['ENTRIES_PAUSED'] is True and open(e.F['install'], 'rb').read() == before, 'still unresolved after a restart'


def test_account_change_with_no_ownership_records_the_new_account():
    e, tmp = mk_engine()
    _other_account(e)
    e2 = restart(tmp, e.trade)
    assert E.ownership_present(e2.state) == []
    assert e2.S['ENTRIES_PAUSED'] is False and json.load(open(e.F['install']))['account'] == E.account_fingerprint(e2.cfg, e2.trade.base if hasattr(e2.trade, 'base') else None)


def test_owner_confirms_the_account_explicitly():
    e, tmp = mk_engine(); TS.opened(e); e.save_state()
    _other_account(e)
    e2 = restart(tmp, e.trade)
    assert e2.install_mismatch and e2.S['ENTRIES_PAUSED'] is True
    assert 'confirmed' in e2.confirm_install()
    assert json.load(open(e.F['install']))['account']['key'] != 'f' * 16 and glob.glob(e.F['install'] + '.corrupt-*')
    e3 = restart(tmp, e.trade)
    assert not e3.install_mismatch and not inc(e3, 'integrity|install-account')


# ------------------------------------------------------------------ P1-4 secrets in damaged legacy settings
MALFORMED = {
    'truncated_after_token': ('{"TELEGRAM_TOKEN": "%s", "MAX_LEV' % TOKEN).encode(),
    'truncated_inside_token': ('{"MAX_LEVERAGE": 5, "TELEGRAM_TOKEN": "%s' % TOKEN[:24]).encode(),
    'garbage_from_first_byte': b'\x00\xff\x13 junk TELEGRAM_TOKEN=' + TOKEN.encode() + b' more junk',
    'bare_token_value': b'][' + TOKEN.encode() + b'}{',
}


def _scan(tmp):
    bad = []
    for p in glob.glob(os.path.join(tmp, '*')):
        if not os.path.isfile(p): continue
        raw = open(p, 'rb').read()
        if TOKEN[:20].encode() in raw or TOKEN[:24].encode() in raw or b'TELEGRAM_TOKEN' in raw: bad.append(os.path.basename(p))
    return bad


@pytest.mark.parametrize('kind', sorted(MALFORMED))
@pytest.mark.parametrize('with_app', [True, False])
def test_damaged_legacy_settings_never_keep_the_token(kind, with_app, monkeypatch, tmp_path):
    import app as A
    tmp = str(tmp_path)
    for p in ('settings.json', 'settings.json.bak'):
        with open(os.path.join(tmp, p), 'wb') as f: f.write(MALFORMED[kind])
    if with_app:
        cfg, writes = {}, []
        monkeypatch.setattr(A, 'write_cfg', lambda u: writes.append(u))
        A.migrate_settings_secrets(cfg, tmp)
        if kind == 'truncated_after_token': assert writes == [{'TELEGRAM_TOKEN': TOKEN}], 'recoverable token migrated'
    e = restart(tmp, TS.FakeX())
    e.save_settings(); e.save_settings()
    assert e.S['ENTRIES_PAUSED'] is True and glob.glob(os.path.join(tmp, 'settings.json.corrupt-*'))
    assert _scan(tmp) == [], _scan(tmp)


def test_redaction_keeps_the_rest_of_the_evidence():
    raw = b'{"MAX_LEVERAGE": 5, "TELEGRAM_TOKEN": "' + TOKEN.encode() + b'", "UNIV'
    red = E.redact_secrets(raw)
    assert b'TELEGRAM_TOKEN' not in red and TOKEN.encode() not in red and b'"MAX_LEVERAGE": 5' in red and red.endswith(b'"UNIV')


# ------------------------------------------------------------------ P2-5 install marker schema
INSTALL_MUTATIONS = {
    'version_99': lambda d: d.update(schema_version=99),
    'version_0': lambda d: d.update(schema_version=0),
    'version_str': lambda d: d.update(schema_version='1'),
    'version_bool': lambda d: d.update(schema_version=True),
    'created_missing': lambda d: d.pop('created'),
    'account_missing': lambda d: d.pop('account'),
    'mode': lambda d: d['account'].update(mode='testnet'),
    'base': lambda d: d['account'].update(base=5),
    'key_not_hex': lambda d: d['account'].update(key='zzzzzzzzzzzzzzzz'),
    'key_length': lambda d: d['account'].update(key='a' * 17),
    'key_type': lambda d: d['account'].update(key=None),
}


@pytest.mark.parametrize('name', sorted(INSTALL_MUTATIONS))
def test_invalid_install_marker_is_damaged_initialized_and_never_rewritten(name):
    e, tmp = mk_engine()
    doc = json.load(open(e.F['install'])); INSTALL_MUTATIONS[name](doc)
    with open(e.F['install'], 'w') as f: json.dump(doc, f)
    raw = open(e.F['install'], 'rb').read()
    e2 = restart(tmp, e.trade)
    assert e2.S['ENTRIES_PAUSED'] is True and inc(e2, 'integrity|install')['open']
    ev = glob.glob(e.F['install'] + '.corrupt-*')
    assert len(ev) == 1 and open(ev[0], 'rb').read() == raw and not os.path.exists(e.F['install']), 'evidence kept, not rewritten'
    os.remove(e.F['state']); os.remove(e.F['state'] + '.bak') if os.path.exists(e.F['state'] + '.bak') else None
    e3 = restart(tmp, e.trade)
    assert e3.S['ENTRIES_PAUSED'] is True and e3.integrity['state']['status'] == 'failed_closed', 'still initialized'


def test_install_digest_is_the_documented_pbkdf2_vector():
    want = hashlib.pbkdf2_hmac('sha256', b'k' * 16, b'zackbot-install-marker-v1', 200_000).hex()[:16]
    e, tmp = mk_engine()
    assert json.load(open(e.F['install']))['account']['key'] == want
    E.validate_install(json.load(open(e.F['install'])))


# ------------------------------------------------------------------ write-ahead client ids reach the real client only
def test_send_passes_client_ids_only_to_functions_that_declare_them(monkeypatch):
    """The offline UI harness replaces binance_client.Futures with a fake whose open() takes no cid: the engine must decide
    by signature, never by class (458ec50 sent cid= to it and every manual trade in the harness failed)."""
    e, _ = mk_engine()
    real_cls = BC.Futures
    c = real_cls.__new__(real_cls); got = {}
    c._order = lambda params: got.update(params) or {}
    e._send(c.open, 'BTCUSDT', 'LONG', '0.001', cid='zbX')
    assert got['newClientOrderId'] == 'zbX'
    class Harness:
        def open(self, s, ps, q): return 'ok'
    monkeypatch.setattr(BC, 'Futures', Harness)
    assert e._send(Harness().open, 'BTCUSDT', 'LONG', 1, cid='zbX') == 'ok'
