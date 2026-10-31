"""Static-40 summary: one row per slice, next to the CORE-10 / CORE-20 rows of the #112 shortlist (read via git show)."""
import hashlib
import json
import os
import subprocess
import sys

D = sys.argv[1]
SL_HEAD = 'a867d3d4c70f529ba20bafe9d423c3ae1d623a5e'
SL_PATH = 'research_evidence/runs/breadth_shortlist/2026-10-15_pr105-111/shortlist.json'
raw = subprocess.run(['git', 'show', f'{SL_HEAD}:{SL_PATH}'], capture_output=True, check=True).stdout
sl = json.loads(raw)
rows112 = {(r['key'], r['side']): r for r in sl['rows']}
ORDER = [('breakout_pyramid', 'long'), ('donchian_ens', 'long'), ('ema_mom', 'long'), ('ema_st', 'long'), ('exp_breakout', 'long'),
         ('exp_trend', 'long'), ('hot_coin', 'long'), ('pullback_rsi', 'long'), ('rotation', 'long'), ('squeeze_tp', 'long'),
         ('breakout_pyramid', 'short'), ('donchian_ens', 'short'), ('squeeze_tp', 'short')]
BOTH = {'ema_st', 'ema_mom', 'donchian_ens', 'breakout_pyramid', 'squeeze_tp', 'pullback_rsi'}


def f(c):
    if not c:
        return 'n/a'
    return f"{c['trades']} / {c['pf']:.2f} / {c['net_r']:+.1f} / {c['max_dd_r']:.1f}"


L, inc, book = [], None, None
tab = ['| key | side | registered_sides | paper_experimental_sides | label | Static-40 trades | PF | net R | net $ | maxDD R | maxDD eq | basis | '
       'CORE-10 (#112) tr/PF/R/DD | CORE-20 (#112) tr/PF/R/DD |', '|' + '---|' * 14]
for k, s in ORDER:
    fn = f'{k}_{s}_defaults.json'
    p = json.load(open(os.path.join(D, fn), encoding='utf-8'))
    assert p['strategy']['key'] == k and p['side'] == s and p['strategy']['mgmt_mode'] == 'defaults' and p['commit'].startswith('c8c7b92')
    b = p['bust']
    a = p['stats']['pre_bust']['aggregate'] if b.get('busted') else p['stats']['aggregate']
    basis = f"pre-bust (bust {b['time']} at trade {b['trade_number']})" if b.get('busted') else 'full (no bust)'
    reg = p['strategy']['registry_sides']
    paper = 'both' if k in BOTH else reg
    label = 'registered' if reg in ('both', s) else 'PAPER experiment (provisional; unregistered, needs new prereg)'
    r112 = rows112[(k, s)]
    inc = inc or [x['symbol'] for x in p['universe']['incomplete']]
    book = book or p['universe']['book']
    tab.append(f"| {k} | {s} | {reg} | {paper} | {label} | {a['trades']} | {a['pf']:.2f} | {a['r_sum']:+.2f} | {a['pnl']:+.2f} | "
               f"{a['max_dd_r']:.2f} | {a['max_dd_equity']:.3f} | {basis} | {f(r112['core10_defaults'])} | {f(r112['core20_defaults'])} |")
L += ['# Breadth candidates on Static-40, defaults only (REVIEW ONLY, no edge claim)', '',
      'Runner `tools/research/run_breadth_candidate.py` @ c8c7b92, unchanged. Universe: the committed `symbols[]` of '
      '`research_evidence/universe/static-top40-minhist365-contract-20261011.json` (40, rank order), passed with `--symbols`; freeze in #113. '
      'Window 2024-10-14T00:00:00Z -> 2026-09-28T00:00:00Z (end exclusive, sealed cutoff), 220-bar warm-up. Costs: fee 0.0005, slip 0.0002, '
      'funding 0.00005 per 4h bar. No tuning, no profit_capture, no 15m, no holdout read.', '',
      f'INCOMPLETE (excluded, never filled): {", ".join(inc)} ({len(inc)} of 40). Book: {len(book)} symbols.', '',
      'rotation ranks within the run universe (here the 33-symbol book), so its Static-40 row is not a superset of the CORE-10/20 rows.', '',
      '`registered_sides` = strategies.STRATEGIES[key][\'sides\'] (formal eligibility). `paper_experimental_sides` = engine.py BREADTH_BOTH '
      '(L350 @ 87f8465) -> \'both\' in the PAPER rotation. The 3 shorts are PAPER experiments: provisional, unregistered, need a new append-only '
      'prereg (no backdating) per Codex 6199976344. Stats are pre-bust when a slice busts (equity <= 10% of start); otherwise full-run.', '',
      f'CORE-10 / CORE-20 cells are copied from #112 `{SL_PATH}` @ {SL_HEAD[:7]} (packet_sha256 `{sl["packet_sha256"]}`).', '']
L += tab
L += ['', 'No edge claim. Development-window backtest only; nothing here is promotable or holdout evidence.', '']
open(os.path.join(D, 'summary.md'), 'w', encoding='utf-8', newline='\n').write('\n'.join(L))
print('\n'.join(tab))
print(inc, len(book))
