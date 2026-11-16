"""Replay check: the LIVE engine vs simulated Binance, compared trade-by-trade with the backtester on the same candles.

    python test_engine_sim.py [start_bar] [slots.json | slots-json-string]

Exit code 1 if the HARD gate fails (return gap > 15 pp, a position mismatch, or a lot without its exchange stop).
The STRICT metrics (matched >= 98 %, median |dR| <= 0.05, p95 |dR| <= 0.25, return gap <= 3 pp, DD gap <= 2 pp) are printed as
warnings; set ZB_REPLAY_STRICT=1 to make them blocking too (the release target once engine and backtest share one core).
Writes dev_out/seed_history.json, seed_missed.json (UI harness seeds) and replay_trades.csv (matched trade table)."""
import json, os, sys
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import load_data, engine as E
from replay import run_replay, STRICT, LOOSE_RET_GAP

OUT = os.environ.get('ZB_OUT') or os.path.join(os.path.dirname(os.path.abspath(__file__)), 'dev_out')
os.makedirs(OUT, exist_ok=True)
SYMS = ['BTCUSDT', 'ETHUSDT', 'SOLUSDT', 'BNBUSDT', 'XRPUSDT', 'DOGEUSDT', 'LINKUSDT', 'AVAXUSDT']

if __name__ == '__main__':
    raw = load_data.load(syms=SYMS)
    N = len(raw['BTCUSDT'])
    t0 = int(sys.argv[1]) if len(sys.argv) > 1 else N - 900
    if len(sys.argv) > 2:
        sleeves = json.load(open(sys.argv[2])) if sys.argv[2].endswith('.json') else json.loads(sys.argv[2])
    else:
        sleeves = [E.sleeve('MOM', 'ema_mom', .4, .03, 4, 'core8', mgmt=E.PY), E.sleeve('DCA', 'dca_dip', .3, .03, 4, 'core8'),
                   E.sleeve('SQZ', 'squeeze_tp', .3, .03, 4, 'core8', sides='both')]
    r = run_replay(raw, sleeves, t0, steps=int(os.environ.get('ZB_SIM_STEPS', '6')))
    m = r['metrics']
    print(f"ENGINE  500 -> {r['engine_curve'].iloc[-1]:.0f} ({m['engine_ret']:+.1f}%)  maxDD {m['engine_dd']:.1f}%  trades {m['trades_engine']}")
    print(f"BACKTEST 500 -> {r['bt_curve'].iloc[-1]:.0f} ({m['bt_ret']:+.1f}%)  maxDD {m['bt_dd']:.1f}%  trades {m['trades_bt']}")
    print(f"TRADES matched {m['matched']} ({m['matched_pct']}%)  median |dR| {m['med_dr']}  p95 |dR| {m['p95_dr']}  "
          f"return gap {m['ret_gap']} pp  DD gap {m['dd_gap']} pp  position mismatches {r['mismatch']}  stops {m['stops']} lots {m['lots']}")
    json.dump(r['history'], open(OUT + '/seed_history.json', 'w'), default=str)
    json.dump(r['missed'], open(OUT + '/seed_missed.json', 'w'), default=str)
    r['matched'].to_csv(OUT + '/replay_trades.csv', index=False)
    strict = os.environ.get('ZB_REPLAY_STRICT') == '1'
    sf = m['strict_fail']
    print(f"STRICT targets {STRICT}: " + ('all met' if not sf else 'NOT met: ' + ', '.join(sf)))
    ok = m['hard_ok'] and (not strict or not sf)
    print(f"GATE hard (return gap <= {LOOSE_RET_GAP} pp, positions/stops reconcile){' + strict' if strict else ''} ->", 'PASS' if ok else 'FAIL')
    sys.exit(0 if ok else 1)
