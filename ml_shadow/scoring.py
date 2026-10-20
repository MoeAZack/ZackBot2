"""Tiny shadow scorer (M4 stub). Returns NO_SCORE for a missing/corrupt/incompatible model - never raises."""
import json

from .common import SCHEMA_VERSION, canonical_json, sha256_hex
from .trainer import MODEL_VERSION, design, predict_logistic

NO_SCORE = 'NO_SCORE'


def _no(reason):
    return {'status': NO_SCORE, 'reason': reason}


def score(model_path, features):
    try:
        with open(model_path, 'r', encoding='utf-8') as fh:
            blob = json.load(fh)
        body = blob['body']
        if sha256_hex(canonical_json(body)) != blob.get('sha256'):
            return _no('sha256 mismatch')
        if body.get('model_version') != MODEL_VERSION or body.get('schema') != SCHEMA_VERSION \
                or body.get('kind') != 'logistic':
            return _no('incompatible model')
        f = features.get('features', features) if isinstance(features, dict) else None
        if not isinstance(f, dict):
            return _no('features not a dict')
        X = design([f], body['spec'])
        if X.shape[1] != len(body['weights']):
            return _no('feature width mismatch')
        p = float(predict_logistic(X, body['weights'], body['bias'])[0])
        ev = body['ev']
        exp_r = p * ev['avg_win_r'] + (1 - p) * ev['avg_loss_r']
        th = body['thresholds']
        grade = 'strong' if p >= th['strong'] else 'weak' if p <= th['weak'] else 'neutral'
        return {'status': 'OK', 'grade': grade, 'p': p, 'expected_r': exp_r, 'model_version': body['model_version']}
    except Exception as e:                                   # noqa: BLE001 - missing/corrupt -> NO_SCORE, never raise
        return _no(type(e).__name__)
