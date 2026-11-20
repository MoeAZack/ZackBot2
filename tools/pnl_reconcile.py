"""Read-only accounting-basis review; never imports the app or connects to an account.

Usage: python -B tools/pnl_reconcile.py <ledger directory>
Reads only history.json, equity.json, settings.json and optional funding.jsonl.
Outputs aggregates only: no row/order/account identifiers, settings or paths.
"""
import collections
import datetime as dt
import json
import math
from pathlib import Path
import sys
from zoneinfo import ZoneInfo

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import actual_costs as AC

# Persisted partial-reason strings are untrusted. Never echo arbitrary ledger
# text (which may contain identifiers or credentials) in an aggregate report.
SAFE_MEASUREMENT_REASONS = frozenset({
    'fill_px_partial', 'fill_px_pending', 'fill_px_lost', 'fill_px_missing',
    'fill_px_unmeasured', 'fill_ref_missing', 'fill_ref_unmatched',
    'fills_unreadable', 'pnl_unreadable', 'pnl_fill_adj_missing',
    'pnl_fill_adj_unreadable', 'fees_actual_missing', 'funding_unpriced',
    'funding_unattributed', 'funding_unread', 'ambiguous_opposite_side_funding',
    'unreadable_funding', 'row_conflict',
})


def safe_reason(reason):
    return reason if reason in SAFE_MEASUREMENT_REASONS else 'unknown_measurement_reason'


def number(value):
    if isinstance(value, bool):
        raise ValueError('boolean amount')
    value = float(value)
    if not math.isfinite(value):
        raise ValueError('nonfinite amount')
    return value


def epoch(value):
    stamp = dt.datetime.fromisoformat(value.replace('Z', '+00:00'))
    if stamp.tzinfo is None:
        raise ValueError('timestamp needs timezone')
    return stamp.timestamp()


def reconcile(history, equity, settings, funding=()):
    """Compare closed accounting with marked equity without inventing open-lot evidence.

    The engine records min(exchange margin balance, base + measured closed P&L
    + capital adjustments + measured-basis open price P&L). Its legacy equity
    pairs contain no open component or exchange-cap evidence. A residual is
    therefore unresolved, never a demonstrated ledger error or a measured UPNL.
    Closed-row measured net already includes its attributed native funding.
    """
    since = settings.get('CAP_SINCE') or ''
    rows = [r for r in history if r.get('closed', '') >= since]
    views = [AC.row_view(r) for r in rows]
    pending = sum(v['status'] == 'pending' for v in views)
    latest = equity[-1] if equity else None
    sample_t = number(latest[0]) if latest else None
    marked = number(latest[1]) if latest else None
    base = number(settings.get('CAPITAL_CAP') or 0)
    adjustments = sum(number(a['amount']) for a in settings.get('CAP_ADJ', []))
    total = lambda key: sum(number(r.get(key) or 0) for r in rows)
    known_net = sum(v['pnl'] or 0 for v in views)
    closed = None if pending else known_net
    later_closes = sum(sample_t is not None and epoch(r['closed']) > sample_t for r in rows)
    # funding.jsonl is attribution evidence, NOT another cash addition. The same
    # payment may already be in history or belong to an open or unowned lot.
    seen, funding_total, funding_assets, conflicts = {}, 0.0, set(), 0
    for f in funding:
        key = f.get('key')
        if not key:
            raise ValueError('funding payment missing deduplication key')
        if key in seen:
            if f != seen[key]:
                conflicts += 1
            continue
        seen[key] = f
        if since and number(f['time_ms']) / 1000 < epoch(since):
            continue
        funding_total += number(f['amount'])
        funding_assets.add(f.get('asset'))
    closed_capital = base + adjustments + closed if closed is not None and base > 0 else None
    reasons = ['open_price_pnl_and_exchange_cap_not_in_equity_pairs',
               'separately_read_files_are_not_an_atomic_runtime_snapshot']
    if pending:
        reasons.append('pending_closed_measurements_checkpoint_not_supplied')
    if later_closes:
        reasons.append('history_has_closes_after_equity_sample')
    if base <= 0:
        reasons.append('whole_account_capital_basis')
    if marked is None:
        reasons.append('equity_sample_missing')
    if conflicts:
        reasons.append('conflicting_funding_replays')
    if len(funding_assets) > 1 or None in funding_assets:
        reasons.append('funding_assets_not_comparable')
    rnd = lambda v: round(v, 8) if v is not None else None
    return dict(
        status='UNRESOLVED_MARKED_COMPONENTS', reasons=reasons,
        equity_sample_cairo=dt.datetime.fromtimestamp(sample_t, ZoneInfo('Africa/Cairo')).isoformat() if latest else None,
        closed_rows=len(rows), measurement_status=dict(collections.Counter(v['status'] for v in views)),
        measurement_reasons=dict(collections.Counter(safe_reason(w) for v in views for w in v['why'])),
        closes_after_equity_sample=later_closes, base=rnd(base), adjustments=rnd(adjustments),
        booked_closed_pnl=rnd(total('pnl')), booked_gross=rnd(total('pnl_gross')),
        fill_price_correction=rnd(total('pnl_fill_adj')), booked_fees=rnd(total('fees')),
        actual_fees=rnd(total('fees_actual')), funding_already_in_closed_rows=rnd(total('funding_actual')),
        known_closed_net=rnd(known_net), complete_closed_net=rnd(closed),
        closed_capital=rnd(closed_capital), marked_equity=rnd(marked),
        residual_not_proven_open_pnl=rnd(marked - closed_capital) if marked is not None and closed_capital is not None else None,
        funding_evidence_total=rnd(funding_total) if len(funding_assets) <= 1 and None not in funding_assets else None,
        funding_unique_payments=len(seen), funding_conflicts=conflicts,
        funding_evidence_added_to_closed_capital=False,
        equity_basis='guard_marked_capital_capped_by_exchange_margin_balance',
        closed_basis='actual_costs.row_view; measured net includes native attributed funding; legacy rows remain booked',
    )


def main(argv=None):
    import argparse
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('directory', type=Path)
    args = parser.parse_args(argv)
    try:
        root = args.directory
        load = lambda name: json.loads((root / name).read_bytes())
        f = root / 'funding.jsonl'
        funding = [json.loads(line) for line in f.read_bytes().splitlines() if line.strip()] if f.is_file() else []
        report = reconcile(load('history.json'), load('equity.json'), load('settings.json'), funding)
    except (OSError, ValueError, TypeError, KeyError, IndexError, AttributeError, OverflowError):
        print(json.dumps(dict(status='INPUT_INVALID', reason='ledger files missing or malformed; no raw data disclosed')))
        return 2
    print(json.dumps(report, indent=2, allow_nan=False))
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
