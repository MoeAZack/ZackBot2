"""ML-SHADOW-01: observation-only decision ledger, close labels, offline trainer and a shadow scorer.

Nothing in this package may change signals, eligibility, sizing, stops, exits, rotation or orders. The engine may later
call ONE hook, `record_decision(...)`, before it decides on a candidate signal, and `record_label(...)` after a trade
closes. Both swallow their own errors (an observer must never break the engine) and return None on failure/dedupe.
"""
from .common import SCHEMA_VERSION, redact, canonical_json, sha256_hex
from .ledger import JsonlLedger, decision_id, record_decision, build_decision_record
from .features import features_at, FEATURE_NAMES
from .labels import record_label, build_label_record
from .trainer import train, walk_forward_splits, cairo_day
from .scoring import score

__all__ = ['SCHEMA_VERSION', 'redact', 'canonical_json', 'sha256_hex', 'JsonlLedger', 'decision_id', 'record_decision',
           'build_decision_record', 'features_at', 'FEATURE_NAMES', 'record_label', 'build_label_record', 'train',
           'walk_forward_splits', 'cairo_day', 'score']
from .writer import LedgerWriter                           # noqa: E402  (#104: ledger writes off the engine cycle lock)
__all__.append('LedgerWriter')
