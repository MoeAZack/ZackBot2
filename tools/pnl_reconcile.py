"""Read-only accounting-basis review; never imports the app or connects to an account.

Usage: python -B tools/pnl_reconcile.py <ledger directory> [--open-positions FILE]
Reads history.json, equity.json, settings.json, optional funding.jsonl, and an
optional sanitized open-positions aggregate.
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

MAX_SAMPLE_SKEW_SECONDS = 120
OPEN_REQUIRED = {'sample_timestamp', 'open_price_pnl', 'exchange_margin_balance'}
OPEN_OPTIONAL = {'open_realized_partials', 'open_fees', 'open_funding'}

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


def open_snapshot(data):
    if not isinstance(data, dict) or not OPEN_REQUIRED <= data.keys() <= OPEN_REQUIRED | OPEN_OPTIONAL:
        raise ValueError('invalid open-positions fields')
    amounts = {}
    for key in (OPEN_REQUIRED | OPEN_OPTIONAL) - {'sample_timestamp'}:
        value = data.get(key, 0)
        if type(value) not in (int, float):
            raise ValueError('open-positions amount must be a JSON number')
        amounts[key] = number(value)
    if amounts['open_fees'] < 0:
        raise ValueError('open fees must be nonnegative')
    return epoch(data['sample_timestamp']), amounts


def reconcile(history, equity, settings, funding=(), *, open_positions=None):
    """Compare closed accounting with marked equity without inventing open-lot evidence.

    The engine records min(exchange margin balance, base + measured closed P&L
    + capital adjustments + measured-basis open price P&L), with a zero floor.
    A marked tie-out requires explicit evidence for the open mark and cap.
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
    reasons = []
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
    status = 'UNRESOLVED_MARKED_COMPONENTS'
    tie_out = {}
    if open_positions is None:
        reasons[:0] = ['open_price_pnl_and_exchange_cap_not_in_equity_pairs',
                       'separately_read_files_are_not_an_atomic_runtime_snapshot']
    else:
        open_t, amounts = open_snapshot(open_positions)
        skew = abs(open_t - sample_t) if sample_t is not None else None
        if skew is not None and skew > MAX_SAMPLE_SKEW_SECONDS:
            reasons.append('open_positions_sample_outside_window')
        tie_out = dict(
            **amounts,
            open_sample_cairo=dt.datetime.fromtimestamp(open_t, ZoneInfo('Africa/Cairo')).isoformat(),
            sample_skew_seconds=rnd(skew), max_sample_skew_seconds=MAX_SAMPLE_SKEW_SECONDS,
            cap_effect=None, floor_effect=None, computed_equity=None,
            residual=None, tolerance=None, likely_component=None,
        )
        if not reasons:
            uncapped = round(number(base + closed + adjustments + amounts['open_price_pnl']), 2)
            capped = min(uncapped, amounts['exchange_margin_balance'])
            computed = round(max(0, capped), 2)
            residual = rnd(number(marked - computed))
            tolerance = max(1.50, 0.0005 * marked)
            status = 'TIED_OUT' if abs(residual) <= tolerance else 'GAP'
            likely_component = None
            if status == 'GAP':
                if capped <= 0:
                    likely_component = 'zero_floor_or_sample_alignment'
                elif amounts['exchange_margin_balance'] <= uncapped:
                    likely_component = 'exchange_margin_balance_or_sample_alignment'
                else:
                    likely_component = 'open_price_pnl_or_sample_alignment'
            tie_out.update(cap_effect=rnd(number(capped - uncapped)), floor_effect=rnd(-min(0, capped)),
                           computed_equity=computed, residual=residual, tolerance=rnd(tolerance),
                           likely_component=likely_component)
    return dict(
        status=status, reasons=reasons,
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
        **tie_out,
    )


def print_table(report):
    print('| Component | Value |\n|---|---:|')
    for label, key in (
        ('Booked closed P&L', 'booked_closed_pnl'), ('Measured closed net', 'complete_closed_net'),
        ('Open price P&L', 'open_price_pnl'), ('Exchange cap effect', 'cap_effect'),
        ('Zero floor effect', 'floor_effect'), ('Computed equity', 'computed_equity'),
        ('Recorded equity', 'marked_equity'), ('Residual (recorded minus computed)', 'residual'),
    ):
        value = report.get(key)
        print(f'| {label} | {"—" if value is None else f"{value:,.4f}"} |')
    print(f'| Status | {report["status"]} |')
    if report.get('likely_component'):
        print(f'| Likely component | {report["likely_component"]} |')
    if report['reasons']:
        print(f'| Evidence flags | {", ".join(report["reasons"])} |')


def main(argv=None):
    import argparse
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('directory', type=Path)
    parser.add_argument('--open-positions', type=Path)
    parser.add_argument('--format', choices=('json', 'table'), default='json')
    args = parser.parse_args(argv)
    try:
        root = args.directory
        load = lambda name: json.loads((root / name).read_bytes())
        f = root / 'funding.jsonl'
        funding = [json.loads(line) for line in f.read_bytes().splitlines() if line.strip()] if f.is_file() else []
        positions = None
        if args.open_positions is not None:
            positions = json.loads(args.open_positions.read_bytes())
            if positions is None:
                raise ValueError('open-positions input must be an object')
        report = reconcile(load('history.json'), load('equity.json'), load('settings.json'), funding,
                           open_positions=positions)
        output = json.dumps(report, indent=2, allow_nan=False)
    except (OSError, ValueError, TypeError, KeyError, IndexError, AttributeError, OverflowError):
        print(json.dumps(dict(status='INPUT_INVALID', reason='input files missing or malformed; no raw data disclosed')))
        return 2
    if args.format == 'table':
        print_table(report)
    else:
        print(output)
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
