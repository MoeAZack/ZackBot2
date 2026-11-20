"""Driver for FWD-BOOKS rows (research only; no scheduler, no account access, reads existing files only). Usage:
python -I run_books.py <freeze.json> <cohort9_1h dir + manifest/rules parent> <FWD-01 data dir> <out dir> [--coverage-only]
Verifies the frozen manifest and every pinned hash, then writes one immutable row at the latest complete boundary."""
import json, os, sys, hashlib
import pandas as pd
HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import books as FB
K = FB.K
REPO = os.path.abspath(os.path.join(HERE, '..', '..'))


def load_inputs(m, canon_root, fwd_dir):
    if FB.sha(os.path.join(canon_root, 'input_manifest.json')) != m['canonical']['manifest_sha256']: raise SystemExit('manifest hash')
    if FB.sha(os.path.join(canon_root, 'exchange_rules.json')) != m['canonical']['rules_sha256']: raise SystemExit('rules hash')
    man = json.load(open(os.path.join(canon_root, 'input_manifest.json'), encoding='utf-8'))['cohort9_1h']
    data = os.path.join(canon_root, 'inputs', 'cohort9_1h')
    for s in K.COINS:
        if FB.sha(os.path.join(data, f'{s}.csv')) != man[s]['input_sha256']: raise SystemExit(f'{s} canonical hash')
    rules = json.load(open(os.path.join(canon_root, 'exchange_rules.json'), encoding='utf-8'))
    canon = K.load_1h(data)                                         # truncated at the frozen historical END
    raw1, raw4, files, violations = {}, {}, {}, {}
    for s in K.COINS:
        p1, p4 = os.path.join(fwd_dir, f'{s}-1h.json'), os.path.join(fwd_dir, f'{s}-4h.json')
        b1, b4 = open(p1, 'rb').read(), open(p4, 'rb').read()
        files[f'{s}-1h.json'] = hashlib.sha256(b1).hexdigest(); files[f'{s}-4h.json'] = hashlib.sha256(b4).hexdigest()
        raw1[s] = FB.klines_frame(json.loads(b1), FB.H1)
        f4 = FB.klines_frame(json.loads(b4), FB.H4)
        bad = FB.order_problem(f4)
        if bad: violations[f'4h|{s}'] = bad; raw4[s] = f4; continue      # left unmerged; reported INCOMPLETE below
        raw4[s] = FB.merge_sources([K.to_4h(canon[s]), f4])
    return raw1, raw4, rules, files, violations


def verify_freeze(path):
    m = json.load(open(path, encoding='utf-8'))
    exp = open(path.replace('.json', '.sha256'), encoding='utf-8').read().strip()
    if FB.sha(path) != exp: raise SystemExit('frozen manifest changed')
    amend = os.path.join(os.path.dirname(path), 'freeze_amendment_a1.json')
    if os.path.exists(amend):                                       # A1: input-order guard; T and economics unchanged
        a = json.load(open(amend, encoding='utf-8'))
        if FB.sha(amend) != open(amend.replace('.json', '.sha256'), encoding='utf-8').read().strip(): raise SystemExit('amendment changed')
        if a['amends_freeze_sha256'] != exp or a['T_utc'] != m['T_utc']: raise SystemExit('amendment does not match freeze')
        m = dict(m, source_sha256=dict(m['source_sha256'], **a['source_sha256']), amendment=a['id'])
    for rel, h in m['source_sha256'].items():
        if FB.sha(os.path.join(REPO, rel)) != h: raise SystemExit(f'frozen source changed: {rel}')
    return m


def latest_boundary(raw1):
    return min(d.t.max() for d in raw1.values()) + FB.H1


def main(freeze, canon_root, fwd_dir, out, coverage_only=False):
    m = verify_freeze(freeze)
    T = pd.Timestamp(m['T_utc']); W1, W4 = pd.Timestamp(m['W0_1h_utc']), pd.Timestamp(m['W0_4h_utc'])
    raw1, raw4, rules, files, violations = load_inputs(m, canon_root, fwd_dir)
    b = latest_boundary(raw1)
    os.makedirs(out, exist_ok=True)
    if violations:                                                  # never sorted away; precise INCOMPLETE record
        res = dict(kind='row' if b > T else 'coverage', boundary=str(b), T=str(T), status='INCOMPLETE',
                   order_violations=violations, input_files_sha256=files)
        name = f'incomplete_{int(b.value // 10**6)}.json'
        with open(os.path.join(out, name), 'w', encoding='utf-8') as f: json.dump(res, f, indent=1)
        print('INCOMPLETE order_violation', json.dumps(violations)); return res
    if coverage_only or b <= T:
        c1 = FB.coverage(raw1, W1, T, b, FB.H1, T); c4 = FB.coverage(raw4, W4, T, b.floor('4h'), FB.H4, T.ceil('4h'))
        res = dict(kind='coverage', inputs_through=str(b), T=str(T), status_1h=c1[0], gaps_1h=c1[1], status_4h=c4[0], gaps_4h=c4[1],
                   warmup_1h={s: int(((d.t >= W1) & (d.t < T)).sum()) for s, d in raw1.items()},
                   warmup_4h={s: int(((d.t >= W4) & (d.t < T.ceil('4h'))).sum()) for s, d in raw4.items()}, input_files_sha256=files)
        with open(os.path.join(out, f'coverage_{int(b.value // 10**6)}.json'), 'w', encoding='utf-8') as f: json.dump(res, f, indent=1)
        print(json.dumps({k: res[k] for k in ('inputs_through', 'status_1h', 'status_4h')})); return res
    row, rec = FB.evaluate(raw1, raw4, rules, W1, W4, T, b)
    row.update(input_files_sha256=files, freeze_sha256=FB.sha(freeze))
    d = os.path.join(out, str(int(b.value // 10**6))); os.makedirs(d, exist_ok=True)
    with open(os.path.join(d, 'row.json'), 'x', encoding='utf-8') as f: json.dump(row, f, indent=1, default=str)
    with open(os.path.join(d, 'receipts.json'), 'x', encoding='utf-8') as f: json.dump(rec, f, default=str)
    print(row['status'], row['boundary']); return row


if __name__ == '__main__':
    a = [x for x in sys.argv[1:] if not x.startswith('--')]
    main(*a[:4], coverage_only='--coverage-only' in sys.argv)
