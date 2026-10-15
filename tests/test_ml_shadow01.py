"""ML-SHADOW-01 M1-M3: decision ledger, close labels, offline trainer, shadow scorer. Synthetic data only, no network."""
import json
import os

import numpy as np
import pandas as pd
import pytest

from ml_shadow import (JsonlLedger, FEATURE_NAMES, build_decision_record, decision_id, features_at, record_decision,
                       record_label, score, train, walk_forward_splits, cairo_day)
from ml_shadow.common import SECRET_NAME_RE
from ml_shadow.trainer import join


def candles(n=120, seed=0, qv=True, start='2026-01-01'):
    rng = np.random.default_rng(seed)
    c = 100 * np.exp(np.cumsum(rng.normal(0, 0.01, n)))
    o = np.r_[c[0], c[:-1]]
    h = np.maximum(o, c) * (1 + rng.uniform(0, 0.005, n))
    l = np.minimum(o, c) * (1 - rng.uniform(0, 0.005, n))
    v = rng.uniform(100, 1000, n)
    d = pd.DataFrame({'t': pd.date_range(start, periods=n, freq='4h'), 'o': o, 'h': h, 'l': l, 'c': c, 'v': v})
    if qv:
        d['qv'] = v * c
    return d


def kw(**over):
    base = dict(sleeve='core', strategy='trend_ema_mom.v1', family='trend', symbol='BTCUSDT', side='long', tf='4h',
                candle_time='2026-01-10 08:00', taken=True, reason='ok', rotation_group='G1')
    base.update(over)
    return base


# ---------- M1 features ----------
def test_future_perturbation_never_changes_features():
    df = candles()
    ctx = {'btc_df': candles(seed=9), 'funding': 0.0001}
    for i in (0, 10, 30, 60, 100):
        ref = features_at(df, i, ctx)
        bad = df.copy()
        bad.loc[i + 1:, ['o', 'h', 'l', 'c', 'v', 'qv']] *= 7.0
        btc = ctx['btc_df'].copy()
        btc.loc[btc['t'] > df['t'][i], ['c']] *= 0.1           # future market candles too
        assert features_at(bad, i, {'btc_df': btc, 'funding': 0.0001}) == ref
        assert features_at(df.iloc[:i + 1].reset_index(drop=True), i, ctx) == ref


def test_missing_features_are_explicit_null_with_mask():
    df = candles(n=10, qv=False)
    out = features_at(df, 5, {})
    f, miss = out['features'], out['missing']
    assert set(f) == set(FEATURE_NAMES)
    for k in ('ema_slope', 'atr_pct', 'qv_ratio', 'funding', 'btc_dir', 'breadth', 'rank', 'regime'):
        assert f[k] is None and k in miss
    assert miss == sorted(k for k, v in f.items() if v is None)
    full = features_at(candles(), 100, {'funding': 0.0002, 'btc_dir': 1, 'breadth': 0.6, 'rank': 3,
                                        'exposure_count': 2, 'corr_count': 1})
    assert full['missing'] == [] and full['features']['session'] in ('ASIA', 'EU', 'NY', 'LATE')


def test_decision_id_deterministic_and_dedupe_on_restart(tmp_path):
    p = tmp_path / 'ledger.jsonl'
    df = candles()
    led = JsonlLedger(p)
    a = record_decision(led, df=df, i=80, **kw())
    assert a and a['decision_id'] == decision_id('core', 'trend_ema_mom.v1', 'btcusdt', 'LONG', '4h',
                                                 pd.Timestamp('2026-01-10 08:00', tz='UTC'))
    assert record_decision(led, df=df, i=80, **kw()) is None
    led2 = JsonlLedger(p)                                        # restart
    assert record_decision(led2, df=df, i=80, **kw()) is None
    assert record_decision(led2, df=df, i=81, **kw(candle_time='2026-01-10 12:00', taken=False, reason='cap')) is not None
    rows = list(led2.rows())
    assert len(rows) == 2 and len({r['decision_id'] for r in rows}) == 2
    assert all(r['feature_hash'] and r['schema'] for r in rows)
    assert rows[1]['corr_id'] is None and rows[1]['taken'] is False and rows[1]['reason'] == 'cap'


def test_rotation_is_bounded(tmp_path):
    p = tmp_path / 'ledger.jsonl'
    led = JsonlLedger(p, max_bytes=3000, max_files=3)
    feats = features_at(candles(), 80, {})
    for k in range(200):
        record_decision(led, features=feats, **kw(candle_time=pd.Timestamp('2026-01-01') + pd.Timedelta(hours=4 * k)))
    files = sorted(os.listdir(tmp_path))
    assert files == ['ledger.jsonl', 'ledger.jsonl.1', 'ledger.jsonl.2']
    assert all(os.path.getsize(tmp_path / f) <= 3000 for f in files)
    assert len(list(led.rows())) < 200                         # oldest rows dropped, bound holds


def test_record_decision_never_raises(tmp_path):
    led = JsonlLedger(tmp_path / 'l.jsonl')
    assert record_decision(led, **kw()) is None                # no df/features -> swallowed, None


# ---------- M2 labels ----------
def test_labels_never_mutate_feature_rows_and_skipped_stay_unlabeled(tmp_path):
    led, lab = JsonlLedger(tmp_path / 'f.jsonl'), JsonlLedger(tmp_path / 'l.jsonl')
    feats = features_at(candles(), 80, {})
    taken = record_decision(led, features=feats, trade_id='T1', **kw())
    skipped = record_decision(led, features=feats, **kw(candle_time='2026-01-11', taken=False, reason='rotation'))
    before = (tmp_path / 'f.jsonl').read_bytes()
    lr = record_label(lab, led, decision_id=taken['decision_id'], net_r=1.4, net_pnl=12.5, exit_reason='target',
                      mfe_r=2.0, mae_r=-0.3, trade_id='T1')
    assert lr['target_before_stop'] == 'UNKNOWN' and lr['net_r'] == 1.4
    assert record_label(lab, led, decision_id=skipped['decision_id'], net_r=-1, net_pnl=-5, exit_reason='stop') is None
    assert record_label(lab, led, decision_id=taken['decision_id'], net_r=9, net_pnl=9, exit_reason='x') is None
    assert (tmp_path / 'f.jsonl').read_bytes() == before
    assert taken['corr_id'] == 'T1'
    assert [r['decision_id'] for r in lab.rows()] == [taken['decision_id']]
    assert len(join(led, lab)) == 1


# ---------- redaction ----------
def test_secret_named_fields_never_written(tmp_path):
    led = JsonlLedger(tmp_path / 'f.jsonl')
    ctx = {'api_key': 'AKIA-SECRET', 'secret': 's3cr3t', 'token': 'tok', 'password': 'pw', 'funding': 0.0001}
    feats = features_at(candles(), 80, ctx)
    feats['features'].update({'apiKey': 'x', 'nested_token': 'y'})   # even if a caller smuggles them in
    record_decision(led, features=feats, **kw())
    lab = JsonlLedger(tmp_path / 'l.jsonl')
    record_label(lab, decision_id='abc', net_r=1, net_pnl=1, exit_reason='t')
    for name in ('f.jsonl', 'l.jsonl'):
        text = (tmp_path / name).read_text()
        for bad in ('AKIA-SECRET', 's3cr3t', '"tok"', '"pw"'):
            assert bad not in text

        def walk(o):
            if isinstance(o, dict):
                for k, v in o.items():
                    assert not SECRET_NAME_RE.search(k), k
                    walk(v)
        for line in text.splitlines():
            walk(json.loads(line))


# ---------- M3 trainer ----------
def synth_dataset(n_trades, families=(('trend', 'LONG'),), days=60, seed=1, t0='2026-01-01'):
    rng = np.random.default_rng(seed)
    feats_rows, labels = [], []
    t0 = pd.Timestamp(t0, tz='UTC')
    for k in range(n_trades):
        fam, side = families[k % len(families)]
        ct = t0 + pd.Timedelta(minutes=int(k * days * 1440 / n_trades))
        x = float(rng.normal())
        f = {n: None for n in FEATURE_NAMES}
        f.update(ema_slope=x, atr_pct=abs(rng.normal(0.02, 0.005)), regime=['TREND_UP', 'RANGE'][k % 2], session='EU')
        rec = build_decision_record(features={'features': f, 'missing': []}, sleeve='core', strategy='s', family=fam,
                                    symbol=f'SYM{k % 7}', side=side, tf='4h', candle_time=ct, taken=True)
        win = rng.random() < 1 / (1 + np.exp(-1.5 * x))
        feats_rows.append(rec)
        labels.append({'decision_id': rec['decision_id'], 'net_r': 2.0 if win else -1.0, 'net_pnl': 1.0,
                       'exit_reason': 'x', 'closed_at': str(ct + pd.Timedelta(hours=8))})
    return feats_rows, labels


def test_trainer_insufficient_below_thresholds():
    f, l = synth_dataset(999)
    assert train(f, l)['status'] == 'INSUFFICIENT_DATA'
    f, l = synth_dataset(1100, families=[(str(k), 'LONG') for k in range(12)])   # ~92 per bucket
    out = train(f, l)
    assert out['status'] == 'INSUFFICIENT_DATA' and 'bucket' in out['reason']
    skipped = [dict(r, taken=False) for r in synth_dataset(1200)[0]]
    assert train(skipped, synth_dataset(1200)[1])['status'] == 'INSUFFICIENT_DATA'


def test_walk_forward_no_overlap_and_embargo():
    f, l = synth_dataset(600)
    ot = [r['candle_time'] for r in f]
    ct = [x['closed_at'] for x in l]
    ct[50] = str(pd.Timestamp('2026-03-30', tz='UTC'))          # a long episode that straddles every boundary
    splits = walk_forward_splits(ot, ct, n_folds=4, embargo_days=2)
    assert len(splits) == 4
    otu = pd.to_datetime(ot, utc=True)
    ctu = pd.to_datetime(ct, utc=True)
    for s in splits:
        tr, te = s['train'], s['test']
        assert not set(tr) & set(te)
        assert otu[tr].max() < otu[te].min()
        assert ctu[tr].max() < s['cutoff'] == s['test_start'] - pd.Timedelta(days=2)
        assert otu[te].min() >= s['test_start']
        assert 50 not in tr
        assert {cairo_day(t) for t in otu[tr]}.isdisjoint({cairo_day(t) for t in otu[te]})
        assert s['test_start'].tz_convert('Africa/Cairo').hour == 0


def test_train_ok_artefact_and_score(tmp_path):
    f, l = synth_dataset(1300, families=(('trend', 'LONG'), ('trend', 'SHORT')))
    mp = str(tmp_path / 'model.json')
    out = train(f, l, out_path=mp)
    assert out['status'] == 'OK' and out['n'] == 1300 and len(out['sha256']) == 64
    rep = out['report']
    for k in ('calibration', 'ranking', 'precision_by_bucket', 'stability_by_regime'):
        assert rep['logistic'][k]
    assert rep['gbt'].get('status') == 'MODEL_UNAVAILABLE' or rep['gbt']['ranking']
    assert rep['logistic']['ranking'][-1]['mean_net_r'] > rep['logistic']['ranking'][0]['mean_net_r']
    good = score(mp, {'ema_slope': 2.0, 'atr_pct': 0.02, 'regime': 'RANGE', 'session': 'EU'})
    bad = score(mp, {'ema_slope': -2.0, 'atr_pct': 0.02, 'regime': 'RANGE', 'session': 'EU'})
    assert good['status'] == 'OK' and good['grade'] == 'strong' and bad['grade'] == 'weak'
    assert good['p'] > bad['p'] and good['expected_r'] > bad['expected_r']


def test_corrupt_missing_incompatible_model_no_score(tmp_path):
    f, l = synth_dataset(1300, families=(('trend', 'LONG'), ('trend', 'SHORT')))
    mp = tmp_path / 'model.json'
    train(f, l, out_path=str(mp))
    feats = {'ema_slope': 1.0}
    assert score(str(mp), feats)['status'] == 'OK'
    assert score(str(tmp_path / 'nope.json'), feats)['status'] == 'NO_SCORE'
    blob = json.loads(mp.read_text())
    blob['body']['bias'] += 1.0                                   # tampered -> sha mismatch
    (tmp_path / 't.json').write_text(json.dumps(blob))
    assert score(str(tmp_path / 't.json'), feats)['status'] == 'NO_SCORE'
    (tmp_path / 'c.json').write_text(mp.read_text()[:-40])        # truncated
    assert score(str(tmp_path / 'c.json'), feats)['status'] == 'NO_SCORE'
    blob = json.loads(mp.read_text())
    blob['body']['model_version'] = 'other.v9'
    from ml_shadow.common import canonical_json, sha256_hex
    blob['sha256'] = sha256_hex(canonical_json(blob['body']))
    (tmp_path / 'i.json').write_text(json.dumps(blob))
    assert score(str(tmp_path / 'i.json'), feats)['status'] == 'NO_SCORE'
    assert score(str(mp), 'not-a-dict')['status'] == 'NO_SCORE'


def test_package_is_standalone_observation_only():
    import ml_shadow, sys
    root = os.path.dirname(ml_shadow.__file__)
    src = ''.join(open(os.path.join(root, n), encoding='utf-8').read() for n in os.listdir(root) if n.endswith('.py'))
    for mod in ('engine', 'app', 'strategies', 'backtest', 'binance_client', 'requests', 'urllib', 'socket'):
        assert f'import {mod}' not in src and f'from {mod} ' not in src
    assert 'LOCALAPPDATA' not in src


# ---------- Cowork F1: Egypt DST boundaries never raise ----------
@pytest.mark.parametrize('day', ['2026-04-24', '2027-04-30', '2026-10-29', '2026-10-30'])
def test_dst_boundary_test_block_never_raises(day):
    d0 = pd.Timestamp(day, tz='UTC')
    ot = [d0 + pd.Timedelta(days=k - 1, hours=12) for k in range(5)]   # 5 Cairo days, test block 1 starts on `day`
    ct = [t + pd.Timedelta(hours=2) for t in ot]
    splits = walk_forward_splits(ot, ct, n_folds=4, embargo_days=0)
    assert splits and str(cairo_day(ot[1])) == day
    first = splits[0]
    assert first['test_start'] <= ot[1] and first['test_start'] > ot[0]
    f, l = synth_dataset(1200, families=(('trend', 'LONG'), ('trend', 'SHORT')), days=6,
                         t0=str((d0 - pd.Timedelta(days=1)).date()))
    assert train(f, l, embargo_days=0)['status'] in ('OK', 'INSUFFICIENT_DATA')


# ---------- Cowork F2: one time normaliser, ints = epoch ms ----------
def test_time_forms_give_same_decision_id_and_session(tmp_path):
    ts = pd.Timestamp('2026-01-10 14:00', tz='UTC')
    forms = [ts, ts.tz_localize(None), '2026-01-10T14:00:00Z', '2026-01-10 16:00:00+02:00', int(ts.value // 10**6)]
    ids = {decision_id('core', 's', 'BTCUSDT', 'LONG', '4h', t) for t in forms}
    assert len(ids) == 1
    df = candles(n=60, start='2026-01-01 06:00')
    i = 59
    ms = df.copy()
    ms['t'] = (pd.to_datetime(df['t']).astype('datetime64[ns]').astype('int64') // 10**6).astype('int64')
    iso = df.copy()
    iso['t'] = pd.to_datetime(df['t']).dt.strftime('%Y-%m-%dT%H:%M:%SZ')
    ref = features_at(df, i, {})
    assert features_at(ms, i, {}) == ref == features_at(iso, i, {})
    led = JsonlLedger(tmp_path / 'f.jsonl')
    assert record_decision(led, df=df, i=i, **kw(candle_time=df['t'][i])) is not None
    assert record_decision(led, df=ms, i=i, **kw(candle_time=int(ms['t'][i]))) is None        # deduped, not doubled
    assert record_decision(led, df=iso, i=i, **kw(candle_time=iso['t'][i])) is None
    for bad in (1.7e12, None, True, object(), 'not-a-time'):
        assert record_decision(led, df=df, i=i, **kw(candle_time=bad)) is None                # never raises
    assert len(list(led.rows())) == 1


# ---------- Cowork note: free-text reason redaction + cap ----------
def test_reason_text_redacted_and_capped(tmp_path):
    led, lab = JsonlLedger(tmp_path / 'f.jsonl'), JsonlLedger(tmp_path / 'l.jsonl')
    secret = 'sk_live_' + 'Q' * 40
    reason = f'cap hit api_key=ABC123 secret: s3cr3t Bearer eyJhbGciOi token={secret} raw {secret} ' + 'z ' * 300
    rec = record_decision(led, features=features_at(candles(), 80, {}), **kw(reason=reason))
    lr = record_label(lab, decision_id=rec['decision_id'], net_r=1, net_pnl=1, exit_reason=reason)
    for r in (rec['reason'], lr['exit_reason']):
        assert len(r) <= 200 and r.startswith('cap hit ')
    text = (tmp_path / 'f.jsonl').read_text() + (tmp_path / 'l.jsonl').read_text()
    for bad in ('ABC123', 's3cr3t', 'eyJhbGciOi', 'QQQQQQQQQQ'):
        assert bad not in text


def test_redact_text_bearer_and_sign_cowork_f3():
    """Cowork 6191480901 F3: a short Bearer token after 'Authorization:' and sign=/sig= values must not survive."""
    from ml_shadow.common import redact_text
    for raw, leak in (('Authorization: Bearer abc.def', 'abc.def'), ('bearer xyz12', 'xyz12'),
                      ('sign=abcd1234', 'abcd1234'), ('sig: 9f9f', '9f9f'), ('x-signature=deadbeef', 'deadbeef')):
        out = redact_text(raw)
        assert leak not in out and '[REDACTED]' in out, (raw, out)
    assert redact_text('stop hit after 3 bars') == 'stop hit after 3 bars'
