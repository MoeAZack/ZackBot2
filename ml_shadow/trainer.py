"""M3 offline trainer - fail closed, walk-forward by Cairo day with an embargo, never random splits.

train(ledger, labels) -> {'status': 'INSUFFICIENT_DATA' | 'OK', ...}
  * joins TAKEN decisions to labels by decision_id (skipped signals have no labels and never train);
  * needs >= MIN_TOTAL labeled trades AND evaluates only (family, side) buckets with >= MIN_BUCKET of them;
  * walk-forward: contiguous blocks of Cairo days, expanding train window, embargo before each test block, and any
    training trade still open at (test start - embargo) is purged - so no symbol episode straddles a split;
  * models: numpy logistic baseline (always) and gradient-boosted trees only if scikit-learn is already installed
    (else 'MODEL_UNAVAILABLE');
  * report: calibration bins, net-R ranking by score bucket, precision by bucket, stability by regime;
  * artefact: JSON {'body': {...}, 'sha256': sha256(canonical body)} with a model_version.
Offline only: nothing here is imported by the engine and nothing feeds back into trading.
"""
import json
import os
from datetime import datetime, timezone
from zoneinfo import ZoneInfo

import numpy as np
import pandas as pd

from .common import SCHEMA_VERSION, canonical_json, sha256_hex
from .features import NUMERIC, CATEGORICAL

MIN_TOTAL, MIN_BUCKET = 1000, 100
MODEL_VERSION = 'ml_shadow.logit.v1'
CAIRO = ZoneInfo('Africa/Cairo')


def _rows(x):
    if x is None:
        return []
    return list(x.rows()) if hasattr(x, 'rows') else list(x)


def _utc(ts):
    t = pd.Timestamp(ts)
    return t.tz_localize('UTC') if t.tzinfo is None else t.tz_convert('UTC')


def cairo_day(ts):
    return _utc(ts).tz_convert(CAIRO).date()


def _cairo_midnight_utc(day):
    return pd.Timestamp(datetime(day.year, day.month, day.day)).tz_localize(CAIRO).tz_convert('UTC')


def join(ledger, labels):
    """[(feature_row, label_row)] for TAKEN decisions with a numeric net_r label (first label per id wins)."""
    lab = {}
    for r in _rows(labels):
        if r.get('net_r') is not None and r.get('decision_id') not in lab:
            lab[r['decision_id']] = r
    out, seen = [], set()
    for r in _rows(ledger):
        did = r.get('decision_id')
        if r.get('taken') and did in lab and did not in seen:
            seen.add(did)
            out.append((r, lab[did]))
    return out


def walk_forward_splits(open_times, close_times, n_folds=4, embargo_days=1):
    """List of {'train', 'test', 'test_start', 'cutoff'} index splits over Cairo-day blocks (time order, no shuffle).
    Train rows: opened AND closed before cutoff = test_start - embargo_days (rows with unknown close are purged).
    Test rows: opened on the block's Cairo days."""
    ot = [_utc(t) for t in open_times]
    ct = [_utc(t) if t is not None else None for t in close_times]
    days = [cairo_day(t) for t in ot]
    uniq = sorted(set(days))
    if len(uniq) < n_folds + 1:
        return []
    blocks = np.array_split(np.arange(len(uniq)), n_folds + 1)
    out = []
    for k in range(1, n_folds + 1):
        test_days = {uniq[j] for j in blocks[k]}
        start = _cairo_midnight_utc(min(test_days))
        cutoff = start - pd.Timedelta(days=embargo_days)
        train = [j for j in range(len(ot)) if ot[j] < cutoff and ct[j] is not None and ct[j] < cutoff]
        test = [j for j in range(len(ot)) if days[j] in test_days]
        if train and test:
            out.append({'train': np.array(train), 'test': np.array(test), 'test_start': start, 'cutoff': cutoff})
    return out


# ---------- design matrix ----------
def fit_spec(feats):
    spec = {'numeric': list(NUMERIC), 'categorical': list(CATEGORICAL), 'mean': {}, 'std': {}, 'vocab': {}}
    for k in NUMERIC:
        vals = np.array([f[k] for f in feats if f.get(k) is not None], float)
        spec['mean'][k] = float(vals.mean()) if len(vals) else 0.0
        sd = float(vals.std()) if len(vals) else 0.0
        spec['std'][k] = sd if sd > 1e-12 else 1.0
    for k in CATEGORICAL:
        spec['vocab'][k] = sorted({str(f[k]) for f in feats if f.get(k) is not None})
    return spec


def design(feats, spec):
    rows = []
    for f in feats:
        x = []
        for k in spec['numeric']:
            v = f.get(k)
            x.append(0.0 if v is None else (float(v) - spec['mean'][k]) / spec['std'][k])
        x += [1.0 if f.get(k) is None else 0.0 for k in spec['numeric']]          # missing-mask indicators
        for k in spec['categorical']:
            v = None if f.get(k) is None else str(f[k])
            x += [1.0 if v == c else 0.0 for c in spec['vocab'][k]]
        rows.append(x)
    return np.clip(np.array(rows, float), -10, 10)


def fit_logistic(X, y, l2=1.0, iters=600, lr=0.2):
    w, b, n = np.zeros(X.shape[1]), 0.0, len(y)
    for _ in range(iters):
        p = 1 / (1 + np.exp(-(X @ w + b)))
        g = p - y
        w -= lr * (X.T @ g / n + l2 * w / n)
        b -= lr * float(g.mean())
    return w, b


def predict_logistic(X, w, b):
    return 1 / (1 + np.exp(-(X @ np.asarray(w, float) + b)))


def _gbt():
    try:
        from sklearn.ensemble import HistGradientBoostingClassifier   # only if already installed - never pip install
        return HistGradientBoostingClassifier
    except Exception:                                                  # noqa: BLE001
        return None


# ---------- report ----------
def _report(p, y, r, fam_side, regime):
    rep = {'calibration': [], 'ranking': [], 'precision_by_bucket': {}, 'stability_by_regime': {}}
    edges = np.linspace(0, 1, 11)
    for a, b in zip(edges[:-1], edges[1:]):
        m = (p >= a) & ((p < b) if b < 1 else (p <= b))
        if m.any():
            rep['calibration'].append({'bin': [round(float(a), 2), round(float(b), 2)], 'n': int(m.sum()),
                                       'mean_p': float(p[m].mean()), 'win_rate': float(y[m].mean())})
    q = np.quantile(p, [0.2, 0.4, 0.6, 0.8])
    qi = np.searchsorted(q, p, side='right')
    for k in range(5):
        m = qi == k
        if m.any():
            rep['ranking'].append({'score_bucket': k + 1, 'n': int(m.sum()), 'mean_net_r': float(r[m].mean()),
                                   'precision': float(y[m].mean())})
    fs = np.array(fam_side, dtype=object)
    for bkt in sorted(set(fam_side)):
        m = fs == bkt
        flag = m & (p >= 0.5)
        rep['precision_by_bucket'][bkt] = {'n': int(m.sum()), 'n_flagged': int(flag.sum()),
                                           'precision': float(y[flag].mean()) if flag.any() else None,
                                           'base_rate': float(y[m].mean()),
                                           'mean_net_r_flagged': float(r[flag].mean()) if flag.any() else None}
    med = float(np.median(p))
    rg = np.array(regime, dtype=object)
    for g in sorted(set(regime)):
        m = rg == g
        hi, lo = m & (p >= med), m & (p < med)
        rep['stability_by_regime'][g] = {'n': int(m.sum()), 'win_rate': float(y[m].mean()),
                                         'mean_net_r_top': float(r[hi].mean()) if hi.any() else None,
                                         'mean_net_r_bottom': float(r[lo].mean()) if lo.any() else None}
    return rep


def train(ledger, labels, out_path=None, n_folds=4, embargo_days=1, min_total=MIN_TOTAL, min_bucket=MIN_BUCKET):
    pairs = join(ledger, labels)
    if len(pairs) < min_total:
        return {'status': 'INSUFFICIENT_DATA', 'reason': f'{len(pairs)} labeled trades < {min_total}', 'n': len(pairs)}
    counts = {}
    for f, _ in pairs:
        b = (f.get('family'), f.get('side'))
        counts[b] = counts.get(b, 0) + 1
    eligible = {b for b, c in counts.items() if c >= min_bucket}
    if not eligible:
        return {'status': 'INSUFFICIENT_DATA', 'reason': f'no family/side bucket with >= {min_bucket}',
                'buckets': {f'{a}/{b}': c for (a, b), c in counts.items()}}
    pairs = [(f, lb) for f, lb in pairs if (f.get('family'), f.get('side')) in eligible]
    pairs.sort(key=lambda fl: (_utc(fl[0]['candle_time']), fl[0]['decision_id']))
    feats = [f['features'] for f, _ in pairs]
    r = np.array([float(lb['net_r']) for _, lb in pairs])
    y = (r > 0).astype(float)
    fam_side = [f"{f.get('family')}/{f.get('side')}" for f, _ in pairs]
    regime = [str(f['features'].get('regime')) for f, _ in pairs]
    splits = walk_forward_splits([f['candle_time'] for f, _ in pairs], [lb.get('closed_at') for _, lb in pairs],
                                 n_folds, embargo_days)
    if not splits:
        return {'status': 'INSUFFICIENT_DATA', 'reason': 'not enough Cairo days for walk-forward folds'}

    oof_idx, oof_p, gbt_p, folds = [], [], [], []
    G = _gbt()
    for s in splits:
        tr, te = s['train'], s['test']
        if len(set(y[tr].tolist())) < 2:
            continue
        spec = fit_spec([feats[j] for j in tr])
        Xtr, Xte = design([feats[j] for j in tr], spec), design([feats[j] for j in te], spec)
        w, b = fit_logistic(Xtr, y[tr])
        oof_idx += te.tolist()
        oof_p += predict_logistic(Xte, w, b).tolist()
        if G is not None:
            gbt_p += G(max_iter=150, learning_rate=0.05).fit(Xtr, y[tr]).predict_proba(Xte)[:, 1].tolist()
        folds.append({'n_train': int(len(tr)), 'n_test': int(len(te)), 'test_start': str(s['test_start']),
                      'cutoff': str(s['cutoff'])})
    if not oof_idx:
        return {'status': 'INSUFFICIENT_DATA', 'reason': 'no fold with both classes'}
    oi, op = np.array(oof_idx), np.array(oof_p)
    sub_fs, sub_rg = [fam_side[j] for j in oi], [regime[j] for j in oi]
    report = {'folds': folds, 'buckets': sorted(f'{a}/{b}' for a, b in eligible),
              'logistic': _report(op, y[oi], r[oi], sub_fs, sub_rg)}
    report['gbt'] = (_report(np.array(gbt_p), y[oi], r[oi], sub_fs, sub_rg) if G is not None
                     else {'status': 'MODEL_UNAVAILABLE', 'reason': 'scikit-learn not installed'})

    spec = fit_spec(feats)
    w, b = fit_logistic(design(feats, spec), y)
    wins, losses = r[r > 0], r[r <= 0]
    body = {'model_version': MODEL_VERSION, 'schema': SCHEMA_VERSION, 'kind': 'logistic', 'spec': spec,
            'weights': [float(v) for v in w], 'bias': float(b),
            'thresholds': {'weak': float(np.quantile(op, 1 / 3)), 'strong': float(np.quantile(op, 2 / 3))},
            'ev': {'avg_win_r': float(wins.mean()) if len(wins) else 0.0,
                   'avg_loss_r': float(losses.mean()) if len(losses) else 0.0},
            'buckets': report['buckets'], 'n_train': len(pairs),
            'trained_at': datetime.now(timezone.utc).strftime('%Y-%m-%dT%H:%M:%SZ')}
    digest = sha256_hex(canonical_json(body))
    if out_path:
        os.makedirs(os.path.dirname(os.path.abspath(out_path)), exist_ok=True)
        with open(out_path, 'w', encoding='utf-8') as fh:
            json.dump({'body': body, 'sha256': digest}, fh, sort_keys=True)
    return {'status': 'OK', 'model_version': MODEL_VERSION, 'sha256': digest, 'model_path': out_path,
            'n': len(pairs), 'report': report}
