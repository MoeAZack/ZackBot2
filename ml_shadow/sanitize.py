"""Allow-listed persisted row schemas. JsonlLedger.append writes ONLY sanitize_row output.

Each row kind has a fixed field list; anything else (including nested keys inside `features`) is dropped. Numeric
causal features keep float/None only (non-finite -> None, strings -> None). Every persisted string - ids, names,
reason, exit_reason, timestamps, categorical features - goes through redact_text. Generated ids are kept only in their
exact format (decision_id otherwise rejects the row, corr_id becomes None). Unknown kinds raise (nothing is written).
"""
import math
import re

import numpy as np

from .common import SCHEMA_VERSION, STATUSES, canonical_json, redact_text, sha256_hex
from .features import NUMERIC, CATEGORICAL

_HEX32, _CORR = re.compile(r'^[0-9a-f]{32}$'), re.compile(r'^c_[0-9a-f]{16}$')
_SHORT = 64


def _txt(v, cap=_SHORT):
    return None if v is None else redact_text(v, cap)


def _fmt(v, rx):
    """Generated ids keep their exact format or become None (an opaque caller string is never persisted)."""
    return v if isinstance(v, str) and rx.fullmatch(v) else None   # fullmatch: `$` admits a trailing newline


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
        'status': r.get('status') if r.get('status') in STATUSES else ('taken' if r.get('taken') else 'skipped'),
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


# ------------------------------------------------------------------ ML-PAIR-01 (contracts/ml_train_01 section 1.3)
_HEX64, _HEX40 = re.compile(r'^[0-9a-f]{64}$'), re.compile(r'^[0-9a-f]{40}$')
_BUILD = re.compile(r'^[A-Za-z0-9._-]{1,40}$')
_NAME = re.compile(r'^[a-z0-9_]+(\.[a-z0-9_]+)*$')     # schema / model ids: dotted names, never free text
PAIR_DECISION_FIELDS = ('ts_utc', 'candle_time', 'sleeve', 'strategy', 'family', 'symbol', 'side', 'tf', 'status', 'reason',
                        'rotation_group', 'features', 'missing', 'feature_hash', 'schema')
PAIR_FIELDS = ('kind', 'schema', 'pair_key', 'source', 'decision_id', 'corr_id', 'decision', 'label', 'costs', 'ids',
               'mgmt', 'epoch_hash', 'provenance', 'record_hash')     # + paired_at (metadata, set at append)
PAIR_SOURCES = ('live',)                  # backfill_v1 rows are ML-BACKFILL-01, not written by this build
MGMT_FIELDS = ('effective_max_bars', 'runner', 'overlay_id', 'overlay_hash', 'mgmt_hash', 'canonical_mgmt_hash', 'tf',
               'horizon_h')


def _h64(v, need=False):
    v = _fmt(v, _HEX64)
    if v is None and need: raise ValueError('a 64-hex hash is required')
    return v


def _bool(v):
    return v if isinstance(v, bool) else None


def _mgmt_fields(r):
    return {'effective_max_bars': _num(r.get('effective_max_bars')), 'runner': _bool(r.get('runner')),
            'overlay_id': _txt(r.get('overlay_id'), 32), 'overlay_hash': _h64(r.get('overlay_hash')),
            'mgmt_hash': _h64(r.get('mgmt_hash')), 'canonical_mgmt_hash': _h64(r.get('canonical_mgmt_hash')),
            'tf': _txt(r.get('tf'), 8), 'horizon_h': _num(r.get('horizon_h'))}


def _mgmt(r):
    """The lot's effective management snapshot at open, stored beside the durable decision (section 1.7)."""
    out = {'kind': 'mgmt', 'schema': _fmt(r.get('schema'), _NAME), 'decision_id': _id(r.get('decision_id'))}
    out.update(_mgmt_fields(r))
    return out


def _pair(r):
    """ml_shadow.pair.v1: every nested block rebuilt from a fixed allow-list; hashes / ids keep their exact format or
    the row is refused (ValueError, nothing written). record_hash is added by the ledger (content only)."""
    did = _id(r.get('decision_id'))
    pk = r.get('pair_key') if isinstance(r.get('pair_key'), (list, tuple)) else ()
    if len(pk) != 2 or pk[0] != did: raise ValueError('pair_key must be (decision_id, cost_rev_id)')
    ids = r.get('ids') if isinstance(r.get('ids'), dict) else {}
    crid = _h64(ids.get('cost_rev_id'), need=True)
    if pk[1] != crid: raise ValueError('pair_key cost_rev_id mismatch')
    if r.get('source') not in PAIR_SOURCES: raise ValueError('unknown pair source')
    d = _decision(dict(r.get('decision') or {}, decision_id=did))
    lab = r.get('label') if isinstance(r.get('label'), dict) else {}
    tbs = lab.get('target_before_stop')
    if lab.get('net_basis') != 'measured': raise ValueError('only measured closes are paired')
    c = r.get('costs') if isinstance(r.get('costs'), dict) else {}
    if c.get('complete') is not True: raise ValueError('only cost-complete closes are paired')
    code = ids.get('code_id') if isinstance(ids.get('code_id'), dict) else {}
    build, commit, bundle = (_fmt(code.get('build_id'), _BUILD), _fmt(code.get('source_commit'), _HEX40),
                             _h64(code.get('bundle_hash')))
    if build in (None, 'dev') or commit is None or bundle is None:            # Codex 6459654266 #6: defense in depth
        raise ValueError('a live pair needs the complete code identity')
    m = r.get('mgmt') if isinstance(r.get('mgmt'), dict) else {}
    mg = _mgmt_fields(m)
    mg.update(mgmt_close_hash=_h64(m.get('mgmt_close_hash')), mgmt_changed=_bool(m.get('mgmt_changed')))
    return {
        'kind': 'pair', 'schema': 'ml_shadow.pair.v1', 'pair_key': [did, crid], 'source': r['source'],
        'decision_id': did, 'corr_id': _fmt(r.get('corr_id'), _CORR),
        'decision': {k: d[k] for k in PAIR_DECISION_FIELDS},
        'label': {'closed_at': _txt(lab.get('closed_at')), 'net_r': _num(lab.get('net_r')), 'net_pnl': _num(lab.get('net_pnl')),
                  'exit_reason': _txt(lab.get('exit_reason'), 200), 'mfe_r': _num(lab.get('mfe_r')),
                  'mae_r': _num(lab.get('mae_r')), 'target_before_stop': tbs if isinstance(tbs, bool) else 'UNKNOWN',
                  'net_basis': 'measured'},
        'costs': {'gross': _num(c.get('gross')), 'fees_actual': _num(c.get('fees_actual')),
                  'funding_actual': _num(c.get('funding_actual')), 'fill_adj': _num(c.get('fill_adj')),
                  'risk_usd': _num(c.get('risk_usd')), 'complete': True,
                  'why': [_txt(x, 40) for x in (c.get('why') or ())][:16]},
        'ids': {'code_id': {'build_id': build, 'source_commit': commit, 'bundle_hash': bundle},
                'decision_schema': _fmt(ids.get('decision_schema'), _NAME), 'label_schema': _fmt(ids.get('label_schema'), _NAME),
                'feature_schema': _fmt(ids.get('feature_schema'), _NAME),
                'feature_names_sha256': _h64(ids.get('feature_names_sha256')),
                'cost_model_id': _fmt(ids.get('cost_model_id'), _NAME), 'cost_rev_id': crid},
        'mgmt': mg, 'epoch_hash': _h64(r.get('epoch_hash'), need=True), 'provenance': None,
    }


_KINDS = {'decision': _decision, 'label': _label, 'mgmt': _mgmt, 'pair': _pair}
FIELDS = {k: tuple(_KINDS[k]({'decision_id': '0' * 32}).keys()) for k in ('decision', 'label', 'mgmt')}
FIELDS['pair'] = PAIR_FIELDS


def sanitize_row(kind, row):
    if kind not in _KINDS or not isinstance(row, dict):
        raise ValueError('unknown row kind')
    return _KINDS[kind](row)
