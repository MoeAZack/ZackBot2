"""Allow-listed persisted row schemas. JsonlLedger.append writes ONLY sanitize_row output.

Each row kind has a fixed field list; anything else (including nested keys inside `features`) is dropped. Numeric
causal features keep float/None only (non-finite -> None, strings -> None). Every persisted string - ids, names,
reason, exit_reason, timestamps, categorical features - goes through redact_text. Generated ids are kept only in their
exact format (decision_id otherwise rejects the row, corr_id becomes None). Unknown kinds raise (nothing is written).
"""
import math
import re

import numpy as np

from .common import SCHEMA_VERSION, canonical_json, redact_text, sha256_hex
from .features import NUMERIC, CATEGORICAL

_HEX32, _CORR = re.compile(r'^[0-9a-f]{32}$'), re.compile(r'^c_[0-9a-f]{16}$')
_SHORT = 64


def _txt(v, cap=_SHORT):
    return None if v is None else redact_text(v, cap)


def _fmt(v, rx):
    """Generated ids keep their exact format or become None (an opaque caller string is never persisted)."""
    return v if isinstance(v, str) and rx.match(v) else None


def _id(v):
    v = _fmt(v, _HEX32)
    if v is None:
        raise ValueError('decision_id must be a generated 32-hex id')   # fail closed: nothing written
    return v


def _num(v):
    if isinstance(v, (bool, np.bool_)) or not isinstance(v, (int, float, np.integer, np.floating)):
        return None
    v = float(v)
    return v if math.isfinite(v) else None


def _features(f):
    f = f if isinstance(f, dict) else {}
    out = {k: _num(f.get(k)) for k in NUMERIC}
    out.update({k: _txt(f.get(k), 32) for k in CATEGORICAL})
    return out


def _decision(r):
    feats = _features(r.get('features'))
    return {
        'kind': 'decision', 'schema': SCHEMA_VERSION,
        'decision_id': _id(r.get('decision_id')), 'corr_id': _fmt(r.get('corr_id'), _CORR),
        'ts_utc': _txt(r.get('ts_utc')), 'candle_time': _txt(r.get('candle_time')),
        'sleeve': _txt(r.get('sleeve')), 'strategy': _txt(r.get('strategy')), 'family': _txt(r.get('family')),
        'symbol': _txt(r.get('symbol')), 'side': _txt(r.get('side')), 'tf': _txt(r.get('tf')),
        'taken': bool(r.get('taken')), 'reason': _txt(r.get('reason'), 200),
        'rotation_group': _txt(r.get('rotation_group')), 'features': feats,
        'missing': sorted(k for k, v in feats.items() if v is None),
        'feature_hash': sha256_hex(canonical_json(feats)),          # always over the PERSISTED features
    }


def _label(r):
    tbs = r.get('target_before_stop')
    return {
        'kind': 'label', 'schema': SCHEMA_VERSION,
        'decision_id': _id(r.get('decision_id')), 'corr_id': _fmt(r.get('corr_id'), _CORR),
        'closed_at': _txt(r.get('closed_at')), 'net_r': _num(r.get('net_r')), 'net_pnl': _num(r.get('net_pnl')),
        'exit_reason': _txt(r.get('exit_reason'), 200), 'mfe_r': _num(r.get('mfe_r')), 'mae_r': _num(r.get('mae_r')),
        'target_before_stop': tbs if isinstance(tbs, bool) else 'UNKNOWN',
    }


_KINDS = {'decision': _decision, 'label': _label}
FIELDS = {k: tuple(fn({'decision_id': '0' * 32}).keys()) for k, fn in _KINDS.items()}


def sanitize_row(kind, row):
    if kind not in _KINDS or not isinstance(row, dict):
        raise ValueError('unknown row kind')
    return _KINDS[kind](row)
