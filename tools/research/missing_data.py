"""Missing-data policy `zb-missing-data/1` (M1): the ONE fail-closed predicate every evidence consumer shares.

`check(doc)` passes only an explicitly COMPLETE document with no contradicting marker; `check_tree(obj)` additionally
walks every nested dict / list (report payloads). Callers wrap `MissingDataError` in their own error type. No imports
beyond the standard library, so the runner, the report gate and the readiness gate all use the same rule."""
from __future__ import annotations

POLICY = 'zb-missing-data/1'
COMPLETE, INCOMPLETE = 'COMPLETE', 'INCOMPLETE'


class MissingDataError(ValueError):
    pass


def _markers(doc: dict) -> bool:
    """Any sign of unaccountable data: incomplete rows, excluded symbols, a censor, incomplete counts or verdict."""
    return bool(doc.get('incomplete') or doc.get('excluded_symbols') or 'censor' in doc or 'incomplete_rows' in doc
                or doc.get('verdict') == INCOMPLETE)


def check(doc, what: str = 'result') -> None:
    """Fail closed unless `doc` is explicitly COMPLETE: a missing status, INCOMPLETE, secondary halt rows without a
    primary, or a COMPLETE that still carries incomplete rows / excluded symbols / a censor / an INCOMPLETE verdict
    or count (a contradiction) is refused. Recurses into `rows`."""
    if not isinstance(doc, dict) or 'status' not in doc:
        raise MissingDataError(f'{what}: no explicit COMPLETE / INCOMPLETE status (fail closed)')
    rows = doc.get('incomplete') or []
    if any(r.get('secondary') for r in rows) and not any(r.get('secondary') is False for r in rows):
        raise MissingDataError(f'{what}: halted lots without a primary cause row (contradiction, fail closed)')
    if doc['status'] != COMPLETE:
        raise MissingDataError(f'{what}: status {doc["status"]!r} is not evidence (missing-data policy {POLICY})')
    if _markers(doc):
        raise MissingDataError(f'{what}: COMPLETE contradicts its incomplete rows / excluded symbols / censor '
                               '(fail closed)')
    for k, v in (doc.get('rows') or {}).items():
        if isinstance(v, dict):
            check(v, f'{what} row {k}')


def check_tree(obj, what: str = 'result') -> None:
    """`check` on the top-level document, then every nested dict: one carrying a status must itself pass `check`;
    one carrying a missing-data marker without a status is refused."""
    check(obj, what)
    _walk(obj, what, top=True)


def _walk(obj, what, top=False):
    if isinstance(obj, dict):
        if not top:
            if 'status' in obj:
                check(obj, what)
            elif _markers(obj):
                raise MissingDataError(f'{what}: missing-data markers without an explicit status (fail closed)')
        for k, v in obj.items():
            _walk(v, f'{what}.{k}')
    elif isinstance(obj, list):
        for i, v in enumerate(obj):
            _walk(v, f'{what}[{i}]')
