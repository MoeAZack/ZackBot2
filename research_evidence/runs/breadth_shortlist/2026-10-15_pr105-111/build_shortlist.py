"""Build the breadth shortlist packet from the COMMITTED packets of review PRs #105-#111 only (no candle is read, nothing is
recomputed from market data). Every number is copied from a packet's stats and cites the packet path, the PR head it was
read from and its packet_sha256.

  py -3.14 build_shortlist.py <out_dir>        (run inside a clone that has fetched the PR heads below)
"""
import hashlib
import json
import os
import subprocess
import sys

R = 'research_evidence/runs/breadth_candidate/'
SOURCES = {   # universe/tf/mgmt -> (PR, head, dir, file pattern)
    ('CORE-10', '4h', 'defaults'): [('#105', '9086e74891d5fd5ade46a1b7d4b63aac9f949edf', R + '2026-10-15_core10_7690426_batch1'),
                                    ('#106', '4f12e271d4f126e6e938ce14f1d2da318d239506', R + '2026-10-15_core10_7690426_batch2')],
    ('CORE-10', '4h', 'profit_capture'): [('#107', 'e9872eb1346fc1326b5379a55bc55f142b95dfa5', R + '2026-10-15_core10_339ba68_batch3')],
    ('CORE-20', '4h', 'defaults'): [('#110', 'c808794abede82f4432ef53bf31f71f6201ef3fc', R + '2026-10-15_core20_c8c7b92_batch6')],
    ('CORE-20', '4h', 'profit_capture'): [('#110', 'c808794abede82f4432ef53bf31f71f6201ef3fc', R + '2026-10-15_core20_c8c7b92_batch6')],
    ('CORE-10', '15m', 'defaults'): [('#109', 'ee51985c031fe60a480e121ce3d562273c781d04', R + '2026-10-15_core10_15m_339ba68_batch4/rerun_c8c7b92')],
    ('CORE-10', '15m', 'profit_capture'): [('#109', 'ee51985c031fe60a480e121ce3d562273c781d04', R + '2026-10-15_core10_15m_339ba68_batch4/rerun_c8c7b92')],
    ('CORE-20', '15m', 'defaults'): [('#111', '1a122b1d0c010fc4a48c7f107924299abcd9dc90', R + '2026-10-15_core20_15m_c8c7b92_batch7')],
    ('CORE-20', '15m', 'profit_capture'): [('#111', '1a122b1d0c010fc4a48c7f107924299abcd9dc90', R + '2026-10-15_core20_15m_c8c7b92_batch7')],
}
# engine.py @ master 87f8465: the PAPER breadth rotation (quoted line refs)
PAPER_REF = {'file': 'engine.py', 'commit': '87f8465',
             'BREADTH_BOTH': "L350: BREADTH_BOTH = frozenset({'ema_st', 'ema_mom', 'donchian_ens', 'breakout_pyramid', 'squeeze_tp', 'pullback_rsi'})",
             'rule': "L348-349 comment (Codex ruling 3): long-default strategies whose signal function already emits both sides run 'both' in PAPER; "
                     "every other strategy keeps its registry sides",
             'slots': "L397-411: breadth_sleeves(), L411: sides='both' if k in BREADTH_BOTH else st['sides'] (one slot per registry key, "
                      "breadth_groups L382, L386 keys = sorted(S.STRATEGIES))"}
BREADTH_BOTH = {'ema_st', 'ema_mom', 'donchian_ens', 'breakout_pyramid', 'squeeze_tp', 'pullback_rsi'}
PROVISIONAL = {('breakout_pyramid', 'short'), ('squeeze_tp', 'short'), ('donchian_ens', 'short')}
BELOW = {('pullback_rsi', 'short'), ('ema_mom', 'short'), ('ema_st', 'short')}
RULES = {
    'shortlist-candidate': 'registered side AND defaults net R > 0 in BOTH CORE-10 and CORE-20 (the strategy\'s own timeframe; '
                           '15m keys use pre-bust stats). "Positive" = net R (sum of per-trade R) > 0; no other criterion.',
    'provisional (unregistered; needs new prereg, no backdating)': 'Codex ruling 6199976344: exactly breakout_pyramid short, '
                           'squeeze_tp short, donchian_ens short.',
    'below-line (unregistered, more evidence needed)': 'Codex ruling 6199976344: exactly pullback_rsi short, ema_mom short, ema_st short.',
    'negative': 'registered side that does not meet the shortlist-candidate rule.',
    'blocked': 'unregistered side not named by the Codex ruling (includes every side whose signal never fires: "not emitted").',
}


def git(*a):
    return subprocess.run(['git', *a], capture_output=True, check=True).stdout


def load(head, path):
    raw = git('show', f'{head}:{path}')
    return json.loads(raw), hashlib.sha256(raw).hexdigest()


def pick(p):
    """Pre-bust stats for a busted packet (the account is dead after the bust), else the full-run stats."""
    b = p.get('bust') or {}
    if b.get('busted'):
        return p['stats']['pre_bust']['aggregate'], 'pre-bust'
    return p['stats']['aggregate'], 'full (no bust)' if b else 'full (bust not recorded; runner before c8c7b92)'


def cell(pr, head, path, p, fsha):
    a, basis = pick(p)
    return {'trades': a['trades'], 'pf': a['pf'], 'net_r': a['r_sum'], 'net_pnl': a['pnl'], 'max_dd_r': a.get('max_dd_r'),
            'max_dd_equity': a.get('max_dd_equity'), 'basis': basis, 'status': p['status'],
            'bust': {k: v for k, v in (p.get('bust') or {}).items() if k in ('busted', 'time', 'trade_number', 'trades_after')},
            'source': {'pr': pr, 'head': head, 'path': path, 'packet_sha256': p['packet_sha256'], 'file_sha256': fsha,
                       'runner_commit': p['commit']}}


def main(out):
    import strategies as S                                    # the strategy registry (runtime keys and their sides)
    cells, incomplete = {}, {}
    for (uni, tf, mg), srcs in SOURCES.items():
        for pr, head, d in srcs:
            names = git('ls-tree', '--name-only', f'{head}:{d}').decode().split()
            for n in names:
                if not n.endswith(f'_{mg}.json'):
                    continue
                path = f'{d}/{n}'
                p, fsha = load(head, path)
                assert p['universe']['tier'] == uni and p['window'].get('tf', '4h') == tf and p['strategy']['mgmt_mode'] == mg
                cells[(p['strategy']['key'], p['side'], uni, mg)] = cell(pr, head, path, p, fsha)
                incomplete.setdefault(f'{uni} {tf}', sorted(x['symbol'] for x in p['universe']['incomplete']))
                win = p['window']
    rows = []
    for key in sorted(S.STRATEGIES):
        st = S.STRATEGIES[key]
        for side in ('long', 'short'):
            reg = st['sides'] in ('both', side)
            paper = ('both' if key in BREADTH_BOTH else st['sides'])
            g = lambda u, m: cells.get((key, side, u, m))
            d10, d20 = g('CORE-10', 'defaults'), g('CORE-20', 'defaults')
            if (key, side) in PROVISIONAL:
                status = 'provisional (unregistered; needs new prereg, no backdating)'
            elif (key, side) in BELOW:
                status = 'below-line (unregistered, more evidence needed)'
            elif not reg:
                status = 'blocked'
            elif d10 and d20 and (d10['net_r'] or 0) > 0 and (d20['net_r'] or 0) > 0:
                status = 'shortlist-candidate'
            else:
                status = 'negative'
            rows.append({'key': key, 'side': side, 'tf': st.get('tf', '4h'),
                         'registered_sides': st['sides'], 'side_registered': reg,
                         'paper_experimental_sides': paper, 'side_in_paper': paper in ('both', side),
                         'status': status,
                         'core10_defaults': d10, 'core20_defaults': d20,
                         'core10_profit_capture': g('CORE-10', 'profit_capture'),
                         'core20_profit_capture': g('CORE-20', 'profit_capture'),
                         'timeframe_note': '15m: PRE-BUST ONLY (every 15m slice busts)' if st.get('tf') == '15m' else None})
    pkt = {'format': 'zb-breadth-shortlist/1', 'non_promotable': True, 'evidence_class': 'candidate-runtime',
           'claim': 'no edge claim; development window only; registry/PAPER labels per Codex 6199976344',
           'window': {k: win[k] for k in ('start', 'end', 'end_exclusive', 'sealed_cutoff')},
           'registered_sides_source': 'strategies.STRATEGIES[key][\'sides\'] (the strategy registry; equals the registry_sides '
                                      'field each packet records). The append-only research ledger '
                                      'research_evidence/ledger/REGISTRY.jsonl registers research families '
                                      '(range_bb_mr, short_breakdown, trend_ema_mom, trend_ema_mom_short, res01_infra), not '
                                      'these runtime keys, so no row below holds a holdout-eligible preregistration.',
           'paper_experimental_sides_source': PAPER_REF, 'status_rules': RULES,
           'sources': {f'{u} {t} {m}': [dict(pr=a, head=b, dir=c) for a, b, c in v] for (u, t, m), v in SOURCES.items()},
           'superseded': {'#108': 'CORE-20 4h on 13 of 20 symbols; superseded by #110 (full coverage, same runner family)'},
           'incomplete': incomplete, 'rows': rows}
    pkt['packet_sha256'] = hashlib.sha256(json.dumps(pkt, sort_keys=True, separators=(',', ':')).encode()).hexdigest()
    os.makedirs(out, exist_ok=True)
    with open(os.path.join(out, 'shortlist.json'), 'w', encoding='utf-8', newline='\n') as f:
        f.write(json.dumps(pkt, sort_keys=True, indent=1) + '\n')
    return pkt


def fmt(c):
    if not c:
        return 'n/a'
    if not c['trades']:
        return '0 tr (not emitted)'
    pf = '-' if c['pf'] is None else f"{c['pf']:.2f}"
    dd = '-' if c['max_dd_r'] is None else f"{c['max_dd_r']:.1f}"
    tag = ' [pre-bust]' if c['basis'] == 'pre-bust' else ''
    return f"{c['trades']} / {pf} / {c['net_r']:+.1f} / {dd}{tag}"


def write_md(pkt, out):
    L = ['# Breadth shortlist packet: review PRs #105-#111 (REVIEW ONLY, no edge claim)', '',
         'Built only from the committed packets of #105-#111 by `build_shortlist.py` (`git show <head>:<path>`). No candle was read and '
         'no number was recomputed. Development window ' f"{pkt['window']['start']} -> {pkt['window']['end']} (end exclusive, sealed "
         f"cutoff {pkt['window']['sealed_cutoff']}). No holdout, no tuning. `packet_sha256` = `{pkt['packet_sha256']}`.", '',
         '## Status rules (only these; no other criterion)', '']
    L += [f'- **{k}**: {v}' for k, v in pkt['status_rules'].items()]
    L += ['', '## Columns', '',
          '- `registered_sides`: formal eligibility, ' + pkt['registered_sides_source'],
          f"- `paper_experimental_sides`: {PAPER_REF['file']} @ {PAPER_REF['commit']}: {PAPER_REF['BREADTH_BOTH']}. {PAPER_REF['rule']}. "
          f"{PAPER_REF['slots']}. PAPER experiment != formal registration (Codex 6199976344).",
          '- Cells: `trades / PF / net R / maxDD R`. 4h keys: full-run stats (no 4h slice busts). **15m keys (exp_scalp, exp_structure): '
          'PRE-BUST ONLY**; every 15m slice busts (equity <= 10% of start) and only the trades before the bust are counted.',
          '- `n/a` = no packet exists for that mgmt (profit_capture exists only for the six keys in Codex 6198970276).', '',
          '| key | side | tf | registered_sides | paper_experimental_sides | CORE-10 defaults | CORE-20 defaults | '
          'CORE-10 profit_capture | CORE-20 profit_capture | status |', '|' + '---|' * 10]
    for r in pkt['rows']:
        L.append(f"| {r['key']} | {r['side']} | {r['tf']} | {r['registered_sides']} | {r['paper_experimental_sides']} | "
                 f"{fmt(r['core10_defaults'])} | {fmt(r['core20_defaults'])} | {fmt(r['core10_profit_capture'])} | "
                 f"{fmt(r['core20_profit_capture'])} | {r['status']} |")
    L += ['', '## Completeness (INCOMPLETE symbols, excluded and never filled)', '']
    L += [f'- {k}: {", ".join(v) or "none"}' for k, v in sorted(pkt['incomplete'].items())]
    L += [f'- Superseded: {k}: {v}' for k, v in pkt['superseded'].items()]
    L += ['', '## 15m bust points (pre-bust headline)', '']
    for r in pkt['rows']:
        for u in ('core10', 'core20'):
            for m in ('defaults', 'profit_capture'):
                c = r[f'{u}_{m}']
                if c and c['bust'].get('busted'):
                    b = c['bust']
                    L.append(f"- {r['key']} {r['side']} {u.upper().replace('CORE', 'CORE-')} {m}: bust {b['time']} at trade "
                             f"{b['trade_number']}, {b['trades_after']} trades after (excluded)")
    L += ['', '## Sources (every cell cites its packet; full per-cell citation with packet_sha256 in shortlist.json)', '',
          '| cell | PR | head | path | packet_sha256 |', '|---|---|---|---|---|']
    for r in pkt['rows']:
        for u in ('core10', 'core20'):
            for m in ('defaults', 'profit_capture'):
                c = r[f'{u}_{m}']
                if c:
                    s = c['source']
                    L.append(f"| {r['key']} {r['side']} {u} {m} | {s['pr']} | {s['head'][:7]} | `{s['path']}` | `{s['packet_sha256']}` |")
    L += ['', 'No edge claim. Results are development-window backtests only; nothing here is promotable or holdout evidence.', '']
    with open(os.path.join(out, 'shortlist.md'), 'w', encoding='utf-8', newline='\n') as f:
        f.write('\n'.join(L))


if __name__ == '__main__':
    sys.path.insert(0, os.getcwd())
    write_md(main(sys.argv[1]), sys.argv[1])
