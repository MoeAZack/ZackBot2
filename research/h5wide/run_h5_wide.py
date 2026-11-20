"""H5-WIDE: Codex's selected H5 volume-impulse LONG 1h, UNCHANGED, on 11 coins outside its selection universe.

FROZEN before any run (Claude Projects, 2026-11-12). Purpose: more trades for the account-4 basket without new rules.
- Signal and exits: the merged account-4 module h5_hourly_long.py from master 9cb4341f (copied byte-for-byte here,
  sha256 8573dd81b39558f55d29682223568d7f72d61e9b25eaa5f072f81dc94139f128, checked at run time): LONG only,
  3 ATR stop, 4R target, 48 bars, be_r 0, trail 0. RESEARCH dict used as is.
- Universe: 1000PEPE ENA HYPE LSK NEAR PUMP QNT SUI UNI WLD ZEC (USD-M perps with 1h files on the owner's PC,
  C:\\Dev\\ZackBot_research_cache\\candles_1h_fib02, 2024-10-04 .. 2026-09-27; HYPE from 2025-05-30, PUMP from 2025-07-10).
  None is in H5's 9-coin selection universe. Caveat: this file set was assembled for earlier research (fib02), so it is
  a survivor list, and the period is consumedDEV for other strategies (never for H5 on these coins).
- Account fit: $4,000, initial risk $8 (0.002, compounding as in BT.run), 1 position, testnet filters where the
  snapshot has the symbol, funding proxy 0.01%/8h both sides, costs 7/15/23 bp per executed side.
- Periods: early < 2025-10-01, later >= 2025-10-01 (about equal halves; diagnostic, not a holdout).
- Gate (same as CP4): >=30 closes each period; R@15 > 0 in both; R@23 >= 0 overall; median cost/R@15 <= 15%;
  R@15 > 0 without the best coin; worst-fill R@15 > 0 overall; turnover fallback 0. DD reported, not gated.
Usage: python research/h5wide/run_h5_wide.py <data_dir> <out_dir> [path|worst]
"""
import sys, os, hashlib, types
import pandas as pd
HERE = os.path.dirname(os.path.abspath(__file__)); ROOT = os.path.dirname(os.path.dirname(HERE))
sys.path.insert(0, ROOT); sys.path.insert(0, os.path.join(ROOT, 'research')); sys.path.insert(0, HERE)
SRC = os.path.join(HERE, 'h5_hourly_long_master.py')
assert hashlib.sha256(open(SRC, 'rb').read()).hexdigest() == '8573dd81b39558f55d29682223568d7f72d61e9b25eaa5f072f81dc94139f128'
import strategies as S, h5_hourly_long_master as H5, newstrat_cp4_run as R

COINS = ['1000PEPEUSDT', 'ENAUSDT', 'HYPEUSDT', 'LSKUSDT', 'NEARUSDT', 'PUMPUSDT', 'QNTUSDT', 'SUIUSDT', 'UNIUSDT',
         'WLDUSDT', 'ZECUSDT']
KEY = 'h5wide_codex_h5_long_1h'


def main(data_dir, out, scen):
    def load(tf):
        o = {}
        for s in COINS:
            d = pd.read_csv(os.path.join(data_dir, f'{s}_1h.csv'))
            d['t'] = pd.to_datetime(d.t, unit='ms'); o[s] = d[['t', 'o', 'h', 'l', 'c', 'v']]
        return o
    def register():
        S.STRATEGIES[KEY] = dict(H5.RESEARCH, name='[RESEARCH] H5-WIDE ' + H5.RESEARCH['name'])
    R.SYMS = COINS; R.load = load; R.SPLIT = pd.Timestamp('2025-10-01')
    R.N4 = types.SimpleNamespace(register=register)
    R.main(KEY, out, scen)


if __name__ == '__main__':
    main(sys.argv[1], sys.argv[2], sys.argv[3] if len(sys.argv) > 3 else 'path')
