"""M2 close labels: a SEPARATE append-only file keyed by decision_id. Feature rows are never touched."""
import logging
from datetime import datetime, timezone

from .common import SCHEMA_VERSION, corr_id_of
from .sanitize import sanitize_row
from .ledger import labelable

log = logging.getLogger('zackbot.ml_shadow')
UNKNOWN = 'UNKNOWN'


def _f(x):
    try:
        return float(x)
    except (TypeError, ValueError):
        return None


def build_label_record(*, decision_id, net_r, net_pnl, exit_reason, mfe_r=None, mae_r=None, target_before_stop=None,
                       trade_id=None, closed_at=None):
    tbs = target_before_stop if isinstance(target_before_stop, bool) else UNKNOWN
    return sanitize_row('label', {
        'kind': 'label', 'schema': SCHEMA_VERSION, 'decision_id': str(decision_id), 'corr_id': corr_id_of(trade_id),
        'closed_at': closed_at or datetime.now(timezone.utc).strftime('%Y-%m-%dT%H:%M:%SZ'),
        'net_r': _f(net_r), 'net_pnl': _f(net_pnl), 'exit_reason': exit_reason or UNKNOWN,
        'mfe_r': _f(mfe_r), 'mae_r': _f(mae_r), 'target_before_stop': tbs,
    })


def record_label(label_ledger, feature_ledger=None, **kw):
    """CLOSE HOOK. Appends one label per decision_id (dedupe). If feature_ledger is given, refuses labels for unknown
    or skipped decisions (skipped signals stay unlabeled; pending ones are accepted - they filled). Never raises; returns the row or None."""
    try:
        rec = build_label_record(**kw)
        if feature_ledger is not None:
            row = next((r for r in feature_ledger.rows() if r.get('decision_id') == rec['decision_id']), None)
            if row is None or not labelable(row):            # taken or pending-then-filled; never skipped
                return None
        return label_ledger.append(rec)
    except Exception as e:                                   # noqa: BLE001
        log.warning('ml_shadow: record_label failed (%s, ignored)', type(e).__name__)      # type only: no data in logs
        return None
