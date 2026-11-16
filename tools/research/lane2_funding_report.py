"""Build compact complete-matrix evidence from lane2 fixed-book funding deltas."""
import argparse
import csv
import gzip
import json
import math
from pathlib import Path
from statistics import median

ROOT = Path(__file__).resolve().parents[2]
DOC = ROOT / 'docs/research/lane2_funding_audit_20261116'
LEADS = ['r6_regime_flip_4h', 's54_double_top_4h', 'l24_breakaway_day_4h',
         's26_weekly_close_breakdown_4h', 'l40_dual_momentum_4h',
         's51_momentum_accel_4h', 's76_uptrend_line_break_4h', 'r74_prior_month_break_4h']


def csv_write(path, rows):
    columns = list(dict.fromkeys(k for row in rows for k in row))
    with path.open('w', encoding='utf-8', newline='') as stream:
        writer = csv.DictWriter(stream, columns)
        writer.writeheader(); writer.writerows(rows)


def expected_jobs():
    leads = {f'{s}:{order}:{scen}|cost{cost}' for s in LEADS for order in range(40)
             for scen in ('path', 'worst') for cost in (7, 15, 23)}
    prot = set()
    for family in ('path4h', 'worst4h', 'h1'):
        with (DOC / 'inputs' / f'prot_{family}_rows.csv').open(newline='') as stream:
            for row in csv.DictReader(stream):
                scope = row['mode'] if family == 'h1' else row['period']
                prot.add(f"{family}|{scope}|{row['strategy']}|{row['variant']}|{row['cost_bp']}")
    return leads | prot


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('--jobs', nargs='+', required=True, help='one or more job_deltas.json files')
    p.add_argument('--out', type=Path, default=DOC / 'completed')
    p.add_argument('--allow-partial', action='store_true', help='diagnostic only, never a completed report')
    a = p.parse_args(); a.out.mkdir(parents=True, exist_ok=True)
    jobs = {}
    for source in a.jobs:
        for row in json.loads(Path(source).read_text()):
            key = row['job_id']
            if key in jobs:
                raise ValueError('duplicate funding job: ' + key)
            jobs[key] = row
    wanted = expected_jobs()
    missing, unexpected = sorted(wanted - jobs.keys()), sorted(jobs.keys() - wanted)
    incomplete = [k for k, v in jobs.items() if v['status'] != 'COMPLETE_MODELLED_CLOCK']
    inventory = dict(expected=len(wanted), actual=len(jobs), missing=missing,
                     unexpected=unexpected, incomplete=incomplete,
                     complete=not (missing or unexpected or incomplete))
    (a.out / 'matrix_inventory.json').write_bytes((json.dumps(inventory, indent=2) + '\n').encode())
    if not inventory['complete'] and not a.allow_partial:
        raise ValueError(f'incomplete matrix: missing={len(missing)}, unexpected={len(unexpected)}, incomplete={len(incomplete)}')

    lead_rows = []
    for strategy in LEADS:
        for cost in (7, 15, 23):
            for scenario in ('path', 'worst'):
                selected = [v for v in jobs.values() if v['strategy'] == strategy and
                            v['cost_bp'] == cost and v.get('scenario') == scenario]
                if not selected: continue
                if any(v['status'] != 'COMPLETE_MODELLED_CLOCK' for v in selected):
                    lead_rows.append(dict(strategy=strategy, cost_bp=cost, scenario=scenario,
                                          orders=len(selected), status='INCOMPLETE'))
                    continue
                old = median(v['baseline_sumR'] / v['n'] for v in selected)
                fee = median(v['fee_slippage_only_sumR'] / v['n'] for v in selected)
                new = median(v['sum_recosted_R'] / v['n'] for v in selected)
                zero = next(v for v in selected if v['order'] == 0)
                lead_rows.append(dict(strategy=strategy, cost_bp=cost, scenario=scenario,
                    orders=len(selected), status='COMPLETE_MODELLED_CLOCK',
                    old_proxy_funded_median_meanR=old, fee_slippage_only_median_meanR=fee,
                    recosted_median_meanR=new, change_of_medians=new-old,
                    funding_change_vs_fee_only_medians=new-fee,
                    median_order_funding_delta_meanR=median(v['sum_native_funding_R']/v['n'] for v in selected),
                    proxy_bound_low_median_meanR=median((v['baseline_sumR']+v['sum_proxy_low_delta_R'])/v['n'] for v in selected),
                    proxy_bound_high_median_meanR=median((v['baseline_sumR']+v['sum_proxy_high_delta_R'])/v['n'] for v in selected),
                    late_timing_median_meanR=median((v['baseline_sumR']+v['sum_late_delta_R'])/v['n'] for v in selected),
                    equal_boundary_before_fill_median_meanR=median((v['baseline_sumR']+v['sum_boundary_before_fill_delta_R'])/v['n'] for v in selected),
                    order0_n=zero['n'], order0_old_sumR=zero['baseline_sumR'],
                    order0_fee_only_sumR=zero['fee_slippage_only_sumR'],
                    order0_new_sumR=zero['sum_recosted_R'],
                    order0_native_funding_R=zero['sum_native_funding_R'],
                    proxy_touched=any(v['proxy_trade_count'] for v in selected),
                    LUNC_proxy_touched=any(v['LUNC_proxy_trade_count'] for v in selected),
                    proxy_trade_instances=sum(v['proxy_trade_count'] for v in selected),
                    LUNC_proxy_trade_instances=sum(v['LUNC_proxy_trade_count'] for v in selected)))
    csv_write(a.out / 'lead_summary.csv', lead_rows)
    prot_rows = []
    for v in jobs.values():
        if v['strategy'] in LEADS: continue
        r = dict(strategy=v['strategy'], family=v['family'], scope=v['scope'], variant=v['variant'],
                 cost_bp=v['cost_bp'], n=v['n'], status=v['status'], old_sumR=v['baseline_sumR'],
                 fee_only_sumR=v['fee_slippage_only_sumR'], new_sumR=v['sum_recosted_R'],
                 delta_vs_old_R=v['sum_delta_R'], funding_delta_vs_fee_only_R=v['sum_native_funding_R'],
                 proxy_trade_instances=v['proxy_trade_count'], LUNC_proxy_trade_instances=v['LUNC_proxy_trade_count'])
        for label, key in (('proxy_bound_low_sumR', 'sum_proxy_low_delta_R'),
                           ('proxy_bound_high_sumR', 'sum_proxy_high_delta_R'),
                           ('late_timing_sumR', 'sum_late_delta_R'),
                           ('equal_boundary_before_fill_sumR', 'sum_boundary_before_fill_delta_R')):
            r[label] = v['baseline_sumR'] + v[key] if v[key] is not None else None
        prot_rows.append(r)
    prot_rows.sort(key=lambda r: (r['family'], r['scope'], r['cost_bp'], r['strategy'], r['variant']))
    csv_write(a.out / 'prot_all_variants.csv', prot_rows)
    paired = []
    for base in prot_rows:
        if base['variant'] != 'BASE': continue
        alternative = next((r for r in prot_rows if r['strategy'] == base['strategy'] and
                            r['family'] == base['family'] and r['scope'] == base['scope'] and
                            r['cost_bp'] == base['cost_bp'] and r['variant'] == 'P1_own'), None)
        if alternative is None: continue
        complete = base['status'] == alternative['status'] == 'COMPLETE_MODELLED_CLOCK'
        paired.append(dict(strategy=base['strategy'], family=base['family'], scope=base['scope'], cost_bp=base['cost_bp'],
            old_P1_minus_BASE_R=alternative['old_sumR']-base['old_sumR'],
            new_P1_minus_BASE_R=alternative['new_sumR']-base['new_sumR'] if complete else None,
            fee_only_P1_minus_BASE_R=alternative['fee_only_sumR']-base['fee_only_sumR'],
            proxy_bound_low_P1_minus_BASE_R=alternative['proxy_bound_low_sumR']-base['proxy_bound_high_sumR'] if complete else None,
            proxy_bound_high_P1_minus_BASE_R=alternative['proxy_bound_high_sumR']-base['proxy_bound_low_sumR'] if complete else None,
            late_timing_P1_minus_BASE_R=alternative['late_timing_sumR']-base['late_timing_sumR'] if complete else None,
            proxy_touched=bool(base['proxy_trade_instances'] or alternative['proxy_trade_instances']),
            LUNC_proxy_touched=bool(base['LUNC_proxy_trade_instances'] or alternative['LUNC_proxy_trade_instances']),
            status='COMPLETE_MODELLED_CLOCK' if complete else 'INCOMPLETE'))
    csv_write(a.out / 'prot_BASE_vs_P1.csv', paired)
    compact = sorted(jobs.values(), key=lambda x: x['job_id'])
    with gzip.open(a.out / 'all_job_deltas.json.gz', 'wt', encoding='utf-8', newline='\n') as stream:
        json.dump(compact, stream, allow_nan=False, separators=(',', ':'))
    print(json.dumps(dict(inventory=inventory, lead_summary_rows=len(lead_rows),
                          prot_rows=len(prot_rows), paired_rows=len(paired))))


if __name__ == '__main__': main()
