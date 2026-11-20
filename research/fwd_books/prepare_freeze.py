"""Write the frozen FWD-BOOKS manifest once (refuses to overwrite). T = first strictly future unopened UTC 1h candle after
the freeze instant; after publishing, the push time must be < T or the freeze is redone with the next T (never backdated)."""
import json, os, sys, time
from datetime import datetime, timezone
from zoneinfo import ZoneInfo
import pandas as pd
HERE = os.path.dirname(os.path.abspath(__file__)); sys.path.insert(0, HERE)
import books as FB
REPO = os.path.abspath(os.path.join(HERE, '..', '..'))
SOURCES = ['research/fwd_books/books.py', 'research/fwd_books/run_books.py', 'research/fwd_books/test_books.py',
           'research/admission_controls/controls.py', 'research/basket_old3_h5/basket.py',
           'research/basket_old3_h5/h5_hourly_long_pr338.py', 'backtest.py', 'strategies.py', 'feasibility.py', 'prot.py']


def main(out):
    p = os.path.join(out, 'freeze.json')
    if os.path.exists(p): raise SystemExit('already frozen; refuse overwrite')
    now_ms = int(time.time() * 1000); T_ms = FB.cutoff_after(now_ms); T = pd.Timestamp(T_ms, unit='ms')
    m = dict(id='FWD-BOOKS-20261112', contract='Codex FORWARD EVIDENCE GO, PR #337 6836143114; owner GO #219 6836306574',
             definitions='research/admission_controls @29d76073 unchanged (imports basket_old3_h5 @347c4b71 unchanged)',
             freeze_utc=datetime.fromtimestamp(now_ms / 1000, timezone.utc).isoformat(), T_ms=T_ms, T_utc=str(T),
             T_cairo=datetime.fromtimestamp(T_ms / 1000, ZoneInfo('Africa/Cairo')).isoformat(),
             ema_first_scored_4h_utc=str(T.ceil('4h')),
             books=[dict(name='H5', key='h5_volume_impulse_long_1h', side='long', tf='1h', max_pos=1),
                    dict(name='EMA', key='ema_st', side='long', tf='4h', max_pos=2)],
             capital_usd=4000, sizing='fixed $8 pre-fee risk per entry, $3,333.33 notional cap per fill (basket.fixed_sizing)',
             coins='original9 ' + ','.join(FB.K.COINS), priority=dict(primary='alpha (alphabetical)', diagnostic='cohort order; never selected'),
             costs_bp_per_side=[7, 15, 23], cost_model='2 bp slip in fill prices + (c-2) bp fee per executed side',
             funding='native per-bar 1bp/8h scaled per TF; target ledger hourly 1bp/8h proxy; both reported closed/open/all',
             paths=['path', 'worst'], guards=dict(daily_halt='3% of Cairo-day start, MARKED equity (native engine rule)',
                                                  open_risk_cap='2.5% enforce'),
             nonparity='any target-ledger refusal of a native lot => row status NONPARITY, reported, never relabelled',
             warmup_bars=FB.K.WARMUP, W0_1h_utc='2026-10-30 16:00:00', W0_4h_utc='2024-10-11 20:00:00',
             canonical=dict(path='F:/ZackBot2_data/C1H-VENUE-RECHECK-20261112', manifest_sha256='c11ed2566727c38d0d9236d7290a01aabb4a53a0f3b15388ea8fed7ba8909351',
                            rules_sha256='d1ef2a9c60db9858774d68903648c334ca807a66c23dc85cce13fd8add96d2af', use='4h warmup history before 2026-09-28 and exchange rules'),
             forward_inputs=dict(path='F:/ZackBot2_data/FWD-01/data/<COIN>-1h.json and <COIN>-4h.json',
                                 producer='existing FWD-01 research runner (freeze head 4933cf28); read only, not modified',
                                 rule='4h = canonical to_4h first, FWD-01 native 4h klines after; overlap must match prices exactly and volumes within 1e-6'),
             rows='one immutable row per evaluated boundary; each row re-runs the native engine from W0 over closed bars < boundary',
             incomplete='missing, partial or out-of-order input => INCOMPLETE with exact gaps; never zero-filled',
             research_only='hypothetical bar replay; not exchange fills, stop evidence or live parity; no accounts/keys/workers/schedulers',
             source_sha256={s: FB.sha(os.path.join(REPO, s)) for s in SOURCES})
    with open(p, 'x', encoding='utf-8') as f: json.dump(m, f, indent=1)
    with open(os.path.join(out, 'freeze.sha256'), 'x', encoding='utf-8') as f: f.write(FB.sha(p))
    print(json.dumps(dict(T_utc=m['T_utc'], T_cairo=m['T_cairo'], freeze_sha256=FB.sha(p))))


if __name__ == '__main__':
    main(sys.argv[1])
