"""R6-TNET163-FIT-D01 native confirmation (Claude lane). RESEARCH ONLY: no network, account, key, runtime or scheduler.

Independent of Codex's fit runner (7a81c7fd): this re-runs r6_regime_flip_4h UNCHANGED through Claude's own rule-v1 path
(longscout_v2_run -> longscout_run.worker -> sieve_v1_panel.worker -> native backtest.run of source acc8d0ee) on the
frozen venue-covered 163-coin list, with the frozen current-testnet filters swapped in for the exported source's
data/exchange_rules_testnet.json. Nothing else changes: same D01 data, warmup 220, T0/split, $8 risk at 10m equity,
$3,333.33 cap, max 1, the 40 fixed orders, 7/15/23 bp, funding and the rule-v1 gates (incl. order0 >= 30 per half), plus
the frozen subset gate (fresh132 and extra31 each median mean R15 > 0).
Codex's freeze.json / rules.json are copied verbatim into r6_fit163/ and pinned by sha256 below.
  LONGSCOUT_CASES=longscout_v6_cases.json python research/extra31_screen/r6_fit163_confirm.py <fresh_dir> <extra_clean_dir> <out_dir>
"""
import sys, os, json, subprocess, shutil
HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
os.environ.setdefault('LONGSCOUT_CASES', 'longscout_v6_cases.json')
import run_screen as RS, longscout_v2_run as V2

CASE = 'r6_regime_flip_4h'
FIT = os.path.join(HERE, 'r6_fit163')
FREEZE_SHA = '50f6e767df1c5111b30cb96f94b76566731552cd4febc311b96711eb059c53d9'   # 7a81c7fd freeze.json
RULES_SHA = 'a34e79503f4b96f855fa7f7181340ddbee181335e702c5e7a064c475e91a4140'    # 7f42f2cf/7a81c7fd rules.json
SOURCE = 'acc8d0eea5fbc964d45d582923732e2bbb5ba322'


def main():
    fresh_dir, extra_dir, out_root = sys.argv[1:4]
    if os.path.basename(V2.CASES_PATH) != 'longscout_v6_cases.json': raise SystemExit('LONGSCOUT_CASES must be longscout_v6_cases.json')
    case = V2.CASES['cases'][CASE]
    if case['commit'] != SOURCE: raise SystemExit('case source differs from acc8d0ee')
    fz_p, ru_p = os.path.join(FIT, 'freeze.json'), os.path.join(FIT, 'rules.json')
    if RS.sha(fz_p) != FREEZE_SHA or RS.sha(ru_p) != RULES_SHA: raise SystemExit('fit freeze/rules sha differs from the pinned Codex freeze')
    fz = json.load(open(fz_p, encoding='utf-8')); rules = json.load(open(ru_p, encoding='utf-8'))
    coins = fz['symbols']
    if fz['rules_sha256'] != RULES_SHA or fz['candidate_source'] != SOURCE: raise SystemExit('freeze does not name these rules/source')
    if len(coins) != 163 or coins != sorted(set(coins)): raise SystemExit('frozen list is not 163 sorted unique symbols')
    missing_rules = [s for s in coins if s not in rules['symbols']]
    if missing_rules: raise SystemExit(f'no current filter for {missing_rules}')   # visible, never silently dropped
    # data: same pinned manifests and per-file checks as the V2 runner
    if RS.sha(os.path.join(fresh_dir, 'input_manifest.json')) != V2.FRESH_MANIFEST: raise SystemExit('fresh manifest differs')
    RS.check_data(extra_dir, True, RS.EXTRA31)
    m = json.load(open(os.path.join(fresh_dir, 'input_manifest.json'), encoding='utf-8'))
    fresh134 = json.load(open(os.path.join(fresh_dir, 'selected.json'), encoding='utf-8'))['selected']
    full165 = sorted(fresh134 + RS.EXTRA31)
    if sorted(set(full165) - set(coins)) != fz['excluded_unavailable'] or not set(coins) <= set(full165):
        raise SystemExit('frozen 163 is not exactly full165 minus the frozen exclusions')
    fresh = sorted(set(fresh134) & set(coins))
    os.makedirs(out_root, exist_ok=True)
    data = os.path.join(out_root, '_data'); os.makedirs(data, exist_ok=True)
    for s in ['BTCUSDT'] + fresh:
        p = os.path.join(fresh_dir, 'clean', f'{s}_1h.csv')
        if RS.sha(p) != m['symbols'][s]['sha256']: raise SystemExit(f'{s}: sha differs from manifest')
        shutil.copy(p, data)
    for s in RS.EXTRA31: shutil.copy(os.path.join(extra_dir, f'{s}_1h.csv'), data)
    out = os.path.join(out_root, CASE); os.makedirs(out, exist_ok=True)
    src = RS.export(case['source'], out)
    hist_rules = os.path.join(src, 'data', 'exchange_rules_testnet.json')
    hist_sha = RS.sha(hist_rules)
    shutil.copyfile(ru_p, hist_rules)                     # the only swap: current testnet filters
    if RS.sha(hist_rules) != RULES_SHA: raise SystemExit('rules swap failed')
    subprocess.run([sys.executable, os.path.join(HERE, 'longscout_v2_run.py'), data, out, out, '--case', CASE, '--orders', '40',
                    '--worker', src, '--universe', ','.join(coins)], check=True, env=dict(os.environ))
    rp = os.path.join(out, f'{CASE}_result.json'); r = json.load(open(rp, encoding='utf-8'))
    if r['coins'] != coins: raise SystemExit('worker traded a different coin list')
    sg = V2.subset_gate(out, CASE, fresh)
    sg['fresh132'] = sg.pop('fresh134')
    status = 'FIT_PASS' if (r['status'] == 'TRANSFER_SCREEN_PASS' and sg['pass']) else 'FIT_FAIL'
    r['fit163'] = dict(trial=fz['id'], status=status, label='NATIVE_CONFIRMATION of a DEPENDENT_DEPLOYMENT_FIT_VARIANT; CONSUMED_CALENDAR',
                       subset_gate=sg, n_coins=len(coins), excluded_unavailable=fz['excluded_unavailable'],
                       freeze_sha256=FREEZE_SHA, rules_sha256=RULES_SHA, replaced_rules_sha256=hist_sha,
                       wrapper_sha256=RS.sha(os.path.abspath(__file__)), v2_runner_sha256=RS.sha(os.path.join(HERE, 'longscout_v2_run.py')),
                       cases_sha256=RS.sha(V2.CASES_PATH))
    json.dump(r, open(rp, 'w', encoding='utf-8'), indent=1, default=str)
    print('FIT163', status, r['gates'], {k: (round(v, 6) if isinstance(v, float) else v) for k, v in r['medians'].items()}, sg, flush=True)


if __name__ == '__main__':
    main()
