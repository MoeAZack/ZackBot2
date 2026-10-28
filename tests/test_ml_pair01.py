"""ML-PAIR-01 (contracts/ml_train_01 sections 1.1-1.8, ticket 7.1): durable labelable decisions + the append-only,
measured-only pairs ledger. Fakes only - no network, no real keys, never the real %LOCALAPPDATA%\\ZackBot."""
import json, os, sys, tempfile

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
os.environ['LOCALAPPDATA'] = tempfile.mkdtemp()            # never the real %LOCALAPPDATA%\ZackBot
import ml_shadow as MLS                                    # noqa: E402
import ml_shadow.pairs as MLP                              # noqa: E402
from ml_shadow import writer as MLW                        # noqa: E402
from ml_shadow.sanitize import sanitize_row                # noqa: E402

PKG = dict(build_id='20261028-055000', source_commit='a' * 40, bundle_hash='b' * 64)
DEV = dict(build_id='dev', source_commit=None, bundle_hash=None)
T0 = '2026-10-20T00:00:00Z'


def dec(symbol='BTCUSDT', status='taken', t='2026-10-20 04:00:00'):
    return MLS.build_decision_record(sleeve='A', strategy='exp_scalp', family='Scalp', symbol=symbol, side='LONG', tf='15m',
                                     candle_time=t, taken=status == 'taken', status=status, reason='', trade_id='A|X|1',
                                     features={'features': {'atr_pct': 0.01, 'regime': 'trend'}})


def label(did, exit_reason='take_profit', net_r=1.5):
    return MLS.build_label_record(decision_id=did, net_r=net_r, net_pnl=3.0, exit_reason=exit_reason,
                                  trade_id='A|BTCUSDT|LONG|1792054800', closed_at='2026-10-20T06:00:00Z')


def close(status='measured', complete=True, fees=0.1, mgmt_close_hash=None):
    c = dict(status=status, complete=complete, why=[] if complete else ['fees_actual_missing'], pnl_gross=3.2,
             pnl_fill_adj=-0.1, fees_actual=fees, funding_actual=0.0, risk_usd=2.0, gross=3.1)
    return dict(cost=c, mgmt_close_hash=mgmt_close_hash)


def store(d, code=PKG, ctr=None, now=lambda: '2026-10-21T00:00:00Z'):
    return MLP.PairStore(str(d), ctr if ctr is not None else {}, code=code, now=now)


def lines(path):
    return open(path, 'rb').read().splitlines() if os.path.exists(path) else []


def paired(tmp, code=PKG):
    s = store(tmp, code)
    d = dec(); s.record_decision(d)
    s.record_mgmt(MLP.mgmt_snapshot(d['decision_id'], {'max_bars': 16, 'be_r': 1}, {'max_bars': 16, 'be_r': 1}, tf='15m'))
    return s, d


# ------------------------------------------------------------------ cost identity + sanitize
def test_cost_rev_id_equals_ai_scan_definition():
    import ai_scan as A
    c = close()['cost']
    want = A.cost_revision_id({k: c[k] for k in A.COST_REV_FIELDS}, net_basis='measured', cost_model_id=MLP.COST_MODEL_ID)
    assert MLP.cost_revision_id(c) == want and MLP.COST_REV_FIELDS == A.COST_REV_FIELDS
    assert MLP.COST_REV_SCHEMA == A.COST_REV_SCHEMA
    with pytest.raises(MLP.PairError): MLP.cost_revision_id(dict(c, fees_actual=None))
    with pytest.raises(MLP.PairError): MLP.cost_revision_id(dict(c, risk_usd=0.0))


def test_pair_row_schema_hash_excludes_paired_at_and_no_raw_ids(tmp_path):
    s, d = paired(tmp_path)
    assert s.pair(label(d['decision_id']), close(mgmt_close_hash=MLP.chash({'max_bars': 16, 'be_r': 1}))) == 'written'
    (row,) = [json.loads(x) for x in lines(s.p['pairs'])]
    assert set(row) == set(MLS.sanitize.PAIR_FIELDS) | {'paired_at'}
    assert row['schema'] == 'ml_shadow.pair.v1' and row['source'] == 'live' and row['provenance'] is None
    assert row['pair_key'] == [d['decision_id'], row['ids']['cost_rev_id']]
    assert row['record_hash'] == MLP.seal(row) == MLP.seal(dict(row, paired_at='2099-01-01T00:00:00Z'))
    assert row['label']['net_basis'] == 'measured' and row['costs']['complete'] is True
    assert row['ids']['code_id'] == PKG and row['ids']['cost_model_id'] == MLP.COST_MODEL_ID
    m = row['mgmt']
    assert m['effective_max_bars'] == 16 and m['horizon_h'] == 4.0 and m['runner'] is False and m['overlay_id'] is None
    assert m['mgmt_hash'] == m['canonical_mgmt_hash'] == m['mgmt_close_hash'] and m['mgmt_changed'] is False
    assert row['epoch_hash'] == MLP.read_epoch(s.p['epoch'])['epoch_hash']
    raw = open(s.p['pairs'], encoding='utf-8').read() + open(s.p['durable'], encoding='utf-8').read()
    assert 'A|BTCUSDT|LONG|1792054800' not in raw and 'A|X|1' not in raw          # raw trade ids never persisted


def test_mgmt_drift_and_missing_close_hash_are_recorded_not_inherited(tmp_path):
    s, d = paired(tmp_path)
    s.pair(label(d['decision_id']), close(fees=0.1, mgmt_close_hash=MLP.chash({'max_bars': 99})))
    s.pair(label(d['decision_id']), close(fees=0.2, mgmt_close_hash=None))
    a, b = [json.loads(x)['mgmt'] for x in lines(s.p['pairs'])]
    assert a['mgmt_changed'] is True and b['mgmt_close_hash'] is None and b['mgmt_changed'] is None


def test_sanitize_refuses_unmeasured_incomplete_or_foreign_source():
    good = dict(kind='pair', decision_id='d' * 32, pair_key=['d' * 32, 'c' * 64], source='live', decision={},
                label=dict(net_basis='measured'), costs=dict(complete=True), ids=dict(cost_rev_id='c' * 64, code_id=PKG),
                epoch_hash='e' * 64)
    assert sanitize_row('pair', good)['pair_key'] == ['d' * 32, 'c' * 64]
    for bad in (dict(label=dict(net_basis='acknowledged')), dict(costs=dict(complete=False)), dict(source='backfill_v1'),
                dict(pair_key=['d' * 32, 'f' * 64]), dict(epoch_hash=None),
                dict(ids=dict(cost_rev_id='c' * 64, code_id=dict(PKG, build_id='dev'))),
                dict(ids=dict(cost_rev_id='c' * 64, code_id=dict(PKG, source_commit=None))),
                dict(ids=dict(cost_rev_id='c' * 64, code_id=dict(PKG, bundle_hash='z' * 64))),
                dict(ids=dict(cost_rev_id='c' * 64))):
        with pytest.raises(ValueError): sanitize_row('pair', dict(good, **bad))


# ------------------------------------------------------------------ excluded closes / decisions
def test_skipped_acknowledged_booked_and_incomplete_closes_never_pair(tmp_path):
    s, d = paired(tmp_path)
    sk = dec(symbol='ETHUSDT', status='skipped')
    assert s.record_decision(sk) is None and s.decision(sk['decision_id']) is None      # skipped: never durable
    s.pair(label(sk['decision_id']), close())
    s.pair(label(d['decision_id']), close(status='acknowledged'))
    s.pair(label(d['decision_id']), close(status='booked'))
    s.pair(label(d['decision_id']), close(status='pending', complete=False))
    s.pair(label(d['decision_id']), close(status='measured', complete=False))
    s.pair(label(d['decision_id']), dict(close(), cost=dict(close()['cost'], risk_usd=None)))   # no cost identity
    assert lines(s.p['pairs']) == []
    c = s.ctr
    assert c['no_durable_decision'] == 1 and c['non_training_closes'] == {'acknowledged': 1, 'booked': 1}
    assert c['cost_incomplete'] == 3 and c['pairs'] == 0 and c['errors'] == 0


def test_pending_decision_is_durable_but_skipped_rows_stay_rotating_only(tmp_path):
    s = store(tmp_path)
    p = dec(status='pending')
    s.record_decision(p)
    assert s.decision(p['decision_id'])['status'] == 'pending'
    assert [json.loads(x)['status'] for x in lines(s.p['durable'])] == ['pending']


# ------------------------------------------------------------------ epoch
def test_epoch_created_once_by_installed_build_and_dev_never_pairs_without_it(tmp_path):
    s = store(tmp_path / 'dev', DEV)
    d = dec(); s.record_decision(d)
    assert not os.path.exists(s.p['epoch'])                                      # dev build: no epoch
    s.pair(label(d['decision_id']), close())
    assert s.ctr['identity_incomplete'] == 1 and s.ctr['identity_reasons'] == {'dev_build': 2}
    assert s.ctr['epoch_refused_identity'] == 1 and lines(s.p['pairs']) == []
    s2, d2 = paired(tmp_path / 'pkg')
    e1 = open(s2.p['epoch'], 'rb').read()
    s2.record_decision(dec(t='2026-10-21 00:00:00'))
    assert open(s2.p['epoch'], 'rb').read() == e1                                # never rewritten
    ep = MLP.read_epoch(s2.p['epoch'])
    assert ep['first_decision_candle'] == '2026-10-20T04:00:00Z' and ep['build_id'] == PKG['build_id']
    with pytest.raises(MLP.PairError): MLP.create_epoch(str(tmp_path / 'x.json'), PKG, '2026-10-22T00:00:00Z', T0)


def test_pre_epoch_decision_and_tampered_epoch_refuse(tmp_path):
    s, d = paired(tmp_path)
    old = dec(symbol='SOLUSDT', t='2026-10-19 00:00:00'); s.record_decision(old)
    s.pair(label(old['decision_id']), close())
    assert s.ctr['pre_epoch'] == 1
    ep = json.load(open(s.p['epoch'])); ep['first_decision_candle'] = '2026-10-01T00:00:00Z'
    open(s.p['epoch'], 'w').write(json.dumps(ep))
    s.pair(label(d['decision_id']), close())
    assert s.ctr['epoch_invalid'] == 1 and lines(s.p['pairs']) == []


# ------------------------------------------------------------------ idempotency / conflicts / torn lines
def test_restart_replay_is_a_no_op_even_with_a_new_paired_at(tmp_path):
    s, d = paired(tmp_path)
    assert s.pair(label(d['decision_id']), close()) == 'written'
    before = open(s.p['pairs'], 'rb').read()
    s2 = store(tmp_path, now=lambda: '2026-10-25T00:00:00Z')                    # restart: a new store, a later clock
    assert s2.pair(label(d['decision_id']), close()) == 'replay'
    assert open(s.p['pairs'], 'rb').read() == before and s2.ctr['pair_replays'] == 1
    assert s2.pair(label(d['decision_id']), close(fees=0.3)) == 'written'      # a new cost revision is a new pair key
    assert len(lines(s.p['pairs'])) == 2


def test_conflicting_replay_is_refused_and_nothing_written(tmp_path):
    s, d = paired(tmp_path)
    s.pair(label(d['decision_id']), close())
    before = open(s.p['pairs'], 'rb').read()
    s2 = store(tmp_path)
    assert s2.pair(label(d['decision_id'], exit_reason='stop_loss'), close()) is None
    assert s2.ctr['duplicate_pairs'] == 1 and open(s.p['pairs'], 'rb').read() == before


def test_torn_and_edited_lines_are_skipped_counted_and_closed_off(tmp_path):
    s, d = paired(tmp_path)
    s.pair(label(d['decision_id']), close())
    good = lines(s.p['pairs'])[0]
    edited = json.loads(good); edited['label']['net_r'] = 9.9
    with open(s.p['pairs'], 'ab') as fh:
        fh.write(json.dumps(edited).encode() + b'\n' + b'{"kind":"pair","pair_k')        # edited line + torn tail
    with open(s.p['durable'], 'ab') as fh: fh.write(b'{"kind":"deci')
    s2 = store(tmp_path)
    assert s2.ctr['skipped_lines'] == 3
    assert s2.pair(label(d['decision_id']), close(fees=0.4)) == 'written'
    d2 = dec(symbol='XRPUSDT'); s2.record_decision(d2)
    assert s2.decision(d2['decision_id']) is not None
    rows, skipped = MLP.read_pairs(str(tmp_path))
    assert len(rows) == 2 and skipped == 2 and lines(s.p['pairs'])[-1].startswith(b'{')
    assert MLP.SealedJsonl(s.p['durable'], MLP._durable_key).skipped == 1


# ------------------------------------------------------------------ writer: rotation survival, fail-safe
def opener(tmp, max_bytes=2_000_000):
    led = []
    def f():
        if not led:
            led.extend([MLS.JsonlLedger(str(tmp / 'decisions.jsonl'), max_bytes, 2),
                        MLS.JsonlLedger(str(tmp / 'labels.jsonl'), max_bytes, 2)])
        return tuple(led)
    return f


def test_label_and_pair_survive_decision_ledger_rotation(tmp_path):
    ctr = {}
    w = MLW.LedgerWriter(opener(tmp_path, max_bytes=1500), ctr, open_pairs=lambda: store(tmp_path, ctr=ctr))
    d = dec(); w.submit('decision', d)
    w.submit('mgmt', MLP.mgmt_snapshot(d['decision_id'], {'max_bars': 16}, {'max_bars': 16}, tf='15m'))
    for k in range(12): w.submit('decision', dec(symbol=f'C{k}USDT', status='skipped'))   # rotate the taken row out
    assert w.flush(10)
    rot = MLS.JsonlLedger(str(tmp_path / 'decisions.jsonl'), 1500, 2)
    assert d['decision_id'] not in {r['decision_id'] for r in rot.rows()}       # really rotated away
    w.submit('label', label(d['decision_id']), close())
    assert w.close(10)
    assert ctr['missing_decision'] == 0 and ctr['labels'] == 1                  # the durable copy kept the label
    assert ctr['pairs']['pairs'] == 1 and ctr['pairs']['durable_decisions'] == 1
    assert len(lines(MLP.paths(str(tmp_path))['durable'])) == 2                  # the decision + its mgmt snapshot


def test_pair_store_failures_never_block_decisions_or_labels(tmp_path):
    ctr = {}
    def boom(): raise OSError('disk full')
    w = MLW.LedgerWriter(opener(tmp_path), ctr, open_pairs=boom)
    d = dec(); w.submit('decision', d); w.submit('label', label(d['decision_id']), close())
    assert w.close(10)
    assert ctr['decisions'] == 1 and ctr['labels'] == 1 and ctr['pairs']['open_errors'] >= 1
    s = store(tmp_path / 'p')
    s.durable.append = lambda rec: (_ for _ in ()).throw(OSError('x'))
    assert s.record_decision(dec()) is None and s.ctr['errors'] == 1             # counted, never raised


def test_code_identity_binds_build_json_to_the_running_build(tmp_path, monkeypatch):
    mod = type(sys)('build_info'); mod.BUILD_ID = '20261028-055000'
    monkeypatch.setitem(sys.modules, 'build_info', mod)
    (tmp_path / 'build.json').write_text(json.dumps(dict(build_id='20261028-055000', source_commit='c' * 40)))
    c = MLP.code_identity(str(tmp_path))
    assert c['build_id'] == '20261028-055000' and c['source_commit'] == 'c' * 40 and len(c['bundle_hash']) == 64
    (tmp_path / 'build.json').write_text(json.dumps(dict(build_id='20261027-000000', source_commit='c' * 40)))
    assert MLP.code_identity(str(tmp_path))['source_commit'] is None             # another build's record
    monkeypatch.delitem(sys.modules, 'build_info')
    monkeypatch.setattr(sys, 'path', [p for p in sys.path if not os.path.exists(os.path.join(p, 'build_info.py'))])
    assert MLP.code_identity(str(tmp_path))['build_id'] == 'dev'
    assert MLP.bundle_hash(str(tmp_path)) is None                                # modules unreadable: incomplete


# ------------------------------------------------------------------ wired at the real close point (fake engine)
def test_engine_pairs_at_the_measured_close_and_trading_is_unchanged(tmp_path, monkeypatch):
    import test_ml_shadow01_hook as H
    monkeypatch.setattr(MLP, 'code_identity', lambda data_dir, base=None: dict(PKG))
    _, off = H.run_fixture(tmp_path / 'off', monkeypatch, False)
    e, on = H.run_fixture(tmp_path / 'on', monkeypatch, True)
    assert on == off                                                             # orders, state, lots, history, skips
    assert e.ml_flush(5.0)
    p = MLP.paths(e.dir)
    assert lines(p['pairs']) == []                                               # closes still pending: nothing paired
    assert e.health['ml_shadow']['pairs']['cost_incomplete'] == 0               # pending closes wait, no label yet
    H.settle(e)                                                                  # the real revision -> measured hook
    assert e.ml_flush(5.0)
    rows, skipped = MLP.read_pairs(e.dir)
    assert len(rows) == 2 and skipped == 0
    dur = [json.loads(x) for x in lines(p['durable'])]
    assert {r['kind'] for r in dur} == {'decision', 'mgmt'}
    assert all(r['status'] in ('taken', 'pending') for r in dur if r['kind'] == 'decision')
    for r in rows:
        assert r['mgmt']['mgmt_hash'] and r['mgmt']['mgmt_close_hash'] == r['mgmt']['mgmt_hash']
        assert r['ids']['code_id'] == PKG and r['label']['net_basis'] == 'measured'
    assert e.health['ml_shadow']['pairs']['pairs'] == 2 and e.health['ml_shadow']['pairs']['errors'] == 0
    raw = open(p['pairs'], encoding='utf-8').read()
    assert not any(k in raw for k in e.state.get('measured_wait', {})) and '|BTCUSDT|' not in raw


# ------------------------------------------------------------------ Codex 6459654266: complete code identity
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def test_bundle_module_list_is_every_pair_source_and_the_installer_packages_each():
    import glob as G
    want = ['engine.py', 'actual_costs.py'] + sorted('ml_shadow/' + os.path.basename(p)
                                                     for p in G.glob(os.path.join(ROOT, 'ml_shadow', '*.py')))
    assert list(MLP.BUNDLE_MODULES) == want                                      # a new module must join the identity
    src = open(os.path.join(ROOT, 'installer.ps1'), encoding='utf-8').read()
    build = src[src.index("@('-m', 'PyInstaller'"):]
    build = build[:build.index("Stop-Install 'PyInstaller failed")]
    for m in MLP.BUNDLE_MODULES:
        if '/' in m:
            d, f = m.split('/')
            line = "'--add-data', ([IO.Path]::Combine($src, '%s', '%s') + ';%s')" % (d, f, d)
        else:
            line = "'--add-data', ([IO.Path]::Combine($src, '%s') + ';.')" % m
        assert line in build, f'{m} is not packaged where bundle_hash reads it'


def stage(tmp, skip=()):
    import shutil
    for m in MLP.BUNDLE_MODULES:
        if m in skip: continue
        dst = os.path.join(str(tmp), *m.split('/'))
        os.makedirs(os.path.dirname(dst), exist_ok=True)
        shutil.copyfile(os.path.join(ROOT, *m.split('/')), dst)
    return str(tmp)


def build(monkeypatch, data, rec, bid='20261028-055000'):
    mod = type(sys)('build_info'); mod.BUILD_ID = bid
    monkeypatch.setitem(sys.modules, 'build_info', mod)
    if rec is not None: (data / 'build.json').write_text(json.dumps(rec))


def run_identity(data, code):
    s = store(data, code)
    d = dec(); s.record_decision(d)
    s.record_mgmt(MLP.mgmt_snapshot(d['decision_id'], {'max_bars': 16}, {'max_bars': 16}, tf='15m'))
    return s, s.pair(label(d['decision_id']), close())


def test_staged_real_bundle_hash_gives_a_complete_identity_that_pairs(tmp_path, monkeypatch):
    import hashlib
    base = stage(tmp_path / 'bundle')
    want = MLP.chash([[m, hashlib.sha256(open(os.path.join(ROOT, *m.split('/')), 'rb').read()).hexdigest()]
                      for m in sorted(MLP.BUNDLE_MODULES)])
    assert MLP.bundle_hash(base) == want == MLP.bundle_hash(ROOT)
    (tmp_path / 'data').mkdir()
    build(monkeypatch, tmp_path / 'data', dict(build_id='20261028-055000', source_commit='c' * 40))
    code = MLP.code_identity(str(tmp_path / 'data'), base)
    assert code == dict(build_id='20261028-055000', source_commit='c' * 40, bundle_hash=want)
    s, res = run_identity(tmp_path / 'data', code)
    assert res == 'written' and MLP.read_epoch(s.p['epoch'])['bundle_hash'] == want
    assert json.loads(lines(s.p['pairs'])[0])['ids']['code_id'] == code


def test_one_missing_packaged_module_means_no_epoch_and_no_pair(tmp_path, monkeypatch):
    base = stage(tmp_path / 'bundle', skip=('ml_shadow/writer.py',))
    assert MLP.bundle_hash(base) is None
    (tmp_path / 'data').mkdir()
    build(monkeypatch, tmp_path / 'data', dict(build_id='20261028-055000', source_commit='c' * 40))
    s, res = run_identity(tmp_path / 'data', MLP.code_identity(str(tmp_path / 'data'), base))
    assert res is None and not os.path.exists(s.p['epoch']) and lines(s.p['pairs']) == []
    assert s.ctr['identity_incomplete'] == 1 and s.ctr['epoch_refused_identity'] == 1
    assert s.ctr['identity_reasons'] == {'bundle_hash': 2} and s.ctr['identity_last'] == 'bundle_hash'
    assert s.decision(json.loads(lines(s.p['durable'])[0])['decision_id']) is not None   # diagnostic rows stay


@pytest.mark.parametrize('rec', [dict(build_id='20261027-000000', source_commit='c' * 40),   # another build's record
                                 dict(build_id='20261028-055000'),                            # no source commit
                                 dict(build_id='20261028-055000', source_commit='C' * 40),    # not lowercase 40-hex
                                 dict(build_id='20261028-055000', source_commit=''),
                                 None])                                                       # no build.json at all
def test_mismatched_or_incomplete_build_json_means_no_epoch_and_no_pair(tmp_path, monkeypatch, rec):
    base = stage(tmp_path / 'bundle')
    (tmp_path / 'data').mkdir()
    build(monkeypatch, tmp_path / 'data', rec)
    code = MLP.code_identity(str(tmp_path / 'data'), base)
    assert code['source_commit'] is None and MLP.identity_gap(code) == 'source_commit'
    s, res = run_identity(tmp_path / 'data', code)
    assert res is None and not os.path.exists(s.p['epoch']) and lines(s.p['pairs']) == []
    assert s.ctr['identity_reasons'] == {'source_commit': 2}


def test_an_epoch_with_an_incomplete_identity_never_verifies(tmp_path):
    with pytest.raises(MLP.PairError): MLP.create_epoch(str(tmp_path / 'e.json'), dict(PKG, bundle_hash=None), T0, T0)
    body = dict(schema=MLP.EPOCH_SCHEMA, build_id='dev', source_commit=None, bundle_hash=None,
                first_decision_candle=T0, created_at=T0)
    body['epoch_hash'] = MLP.chash(body)
    (tmp_path / 'e.json').write_text(json.dumps(body))
    with pytest.raises(MLP.PairError): MLP.read_epoch(str(tmp_path / 'e.json'))


def test_measured_cost_model_id_is_defined_once_in_actual_costs():
    import actual_costs as AC, ai_scan as A
    assert MLP.COST_MODEL_ID is AC.MEASURED_COST_MODEL_ID and A.MEASURED_COST_MODEL_ID is AC.MEASURED_COST_MODEL_ID
    for f in ('ml_shadow/pairs.py', 'ai_scan.py'):
        assert "'zackbot.actual_costs.measured.v1'" not in open(os.path.join(ROOT, f), encoding='utf-8').read()


def test_engine_pairing_failure_never_changes_trading(tmp_path, monkeypatch):
    import test_ml_shadow01_hook as H
    _, off = H.run_fixture(tmp_path / 'off', monkeypatch, False)
    def boom(*a, **k): raise RuntimeError('pair boom')
    monkeypatch.setattr(MLP, 'mgmt_snapshot', boom)
    monkeypatch.setattr(MLP, 'cost_inputs', boom)
    monkeypatch.setattr(MLP.PairStore, '__init__', boom)
    e, on = H.run_fixture(tmp_path / 'on', monkeypatch, True)
    H.settle(e)
    assert on == off and e.ml_flush(5.0)
    assert e.health['ml_shadow']['labels'] == 2                                  # labels still written


@pytest.mark.parametrize('field, bad', [('source_commit', 'a' * 40 + '\n'), ('bundle_hash', 'b' * 64 + '\n'),
                                        ('build_id', '2026\n'), ('source_commit', 'A' * 40)])
def test_identity_gate_is_a_full_match_and_rejects_a_trailing_newline(field, bad):
    """Cowork 6460324223 N1: `^...$` with re.match admits a trailing newline; the gate and the sanitizer use fullmatch."""
    good = {'build_id': '2026.10.28', 'source_commit': 'a' * 40, 'bundle_hash': 'b' * 64}
    assert MLP.identity_gap(good) is None
    assert MLP.identity_gap(dict(good, **{field: bad})) is not None
    from ml_shadow.sanitize import _fmt, _HEX40, _HEX64, _BUILD
    rx = {'source_commit': _HEX40, 'bundle_hash': _HEX64, 'build_id': _BUILD}[field]
    assert _fmt(bad, rx) is None and _fmt(good[field], rx) == good[field]


# ------------------------------------------------------------------ Codex 6460424043 N2 / N3
def test_two_store_duplicate_pair_reads_as_one_training_row(tmp_path):
    """N2: two writers overlapping a restart can each append the same pair; the reader keeps the first, once."""
    s1, d = paired(tmp_path)
    s2 = store(tmp_path)                                    # constructed before s1 writes: it has not seen the pair
    assert s1.pair(label(d['decision_id']), close()) == 'written'
    s2.pair(label(d['decision_id']), close())
    first = lines(s1.p['pairs'])[0]
    with open(s1.p['pairs'], 'ab') as fh: fh.write(first + b'\n')       # force the duplicate whatever s2 did
    assert len(lines(s1.p['pairs'])) >= 2
    st = {}
    rows, skipped = MLP.read_pairs(str(tmp_path), stats=st)
    assert len(rows) == 1 and skipped == 0 and json.loads(first)['record_hash'] == rows[0]['record_hash']
    assert st['duplicates'] == len(lines(s1.p['pairs'])) - 1 and st['conflicts'] == 0
    assert MLP.read_pairs(str(tmp_path))[0] == rows                    # stats is optional


@pytest.mark.parametrize('drop, bad', [('created_at', None), ('first_decision_candle', None),
                                        (None, ('created_at', ['2026'])), (None, ('created_at', 'not-a-time'))])
def test_hash_valid_epoch_with_a_missing_or_malformed_field_is_epoch_invalid(tmp_path, drop, bad):
    """N3: a re-hashed epoch missing created_at (or with a malformed time) is a PairError, counted as epoch_invalid."""
    s, d = paired(tmp_path)
    ep = json.load(open(s.p['epoch']))
    ep.pop('epoch_hash')
    if drop: ep.pop(drop)
    if bad: ep[bad[0]] = bad[1]
    ep['epoch_hash'] = MLP.chash(ep)
    open(s.p['epoch'], 'w').write(json.dumps(ep))
    with pytest.raises(MLP.PairError, match='epoch field missing or malformed'): MLP.read_epoch(s.p['epoch'])
    s.pair(label(d['decision_id']), close())
    assert s.ctr['epoch_invalid'] == 1 and lines(s.p['pairs']) == []
