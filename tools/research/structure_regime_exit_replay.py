"""Research-only replay: run_breadth_candidate.py + the declared causal opposite-regime exit (trade_audit.RULE_REGIME).

Arm B of the exp_structure exit comparison (development window only, non-promotable, no edge claim). Everything is the
frozen runner (tools/research/run_breadth_candidate.py, imported, unchanged): window guard, universe, caches, costs,
account, backtest.Book + backtest.run, FeeProbe, stats, packet. The ONLY addition is the regime exit:

  trade_audit.regime_snapshot trend of a CLOSED candle of the lot tf (book frame c / e20 / e50 / e200, the same columns
  the engine passes): above200 = c > e200, up20 = e20 > e50; trend 'down' = (not above200) and (not up20), 'up' = both,
  anything non-finite = 'unknown' (never triggers). LONG exits on 'down', SHORT exits on 'up'.

It is injected as an extra exit-signal source: the book's raw lx / sx columns are OR-ed with that trend, so backtest.run
evaluates it at step 6 (signal / time exits) of candle i, i.e. after candle i has closed, and fills it at that candle's
close with the runner's exit slippage and taker fee - the same fill convention as the strategy's own exit signal, and
the first price at/after the decision (no look-ahead: candle i's trend uses only candles <= i). The entry columns
(le / se) are untouched. With regime_exit=False the class is a no-op and the run is the frozen runner's arm A.

Output: the runner's packet (unchanged format) plus a sidecar <out>.regime_exit.json: the rule, per trade whether the
exit was regime-only (regime trend against and own exit signal false), the next-open vs close fill sensitivity (fraction of price),
and this script's sha256.

  py -3.14 tools/research/structure_regime_exit_replay.py <run_breadth_candidate args...> [--no-regime-exit]
"""
import hashlib
import json
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(os.path.dirname(HERE))
for _p in (ROOT, HERE):
    if _p not in sys.path:
        sys.path.insert(0, _p)

import numpy as np                                                                          # noqa: E402

import backtest as BT                                                                       # noqa: E402
import run_breadth_candidate as R                                                           # noqa: E402
import trade_audit as TA                                                                    # noqa: E402

RULE = TA.RULE_REGIME
LAST_BOOK = None
_RUNNER_RUN = R.run       # the frozen runner's run(); main() routes R.main's call through run() below
TREND = 'trade_audit.regime_snapshot: down = not(c > e200) and not(e20 > e50); up = c > e200 and e20 > e50; unknown never exits'


def regime_trend(d):
    """(down, up) bool arrays per closed candle of frame d, exactly trade_audit.regime_snapshot's trend."""
    c, e20, e50, e200 = (d[k].to_numpy(float) for k in ('c', 'e20', 'e50', 'e200'))
    ok = np.isfinite(c) & np.isfinite(e20) & np.isfinite(e50) & np.isfinite(e200)
    a, u = c > e200, e20 > e50
    return ok & ~a & ~u, ok & a & u


def make_book(regime_exit: bool):
    class RegimeExitBook(BT.Book):
        """backtest.Book whose raw_sig adds the regime exit to lx (LONG: trend down) / sx (SHORT: trend up)."""
        last = None
        enabled = regime_exit

        def __init__(self, raw):
            super().__init__(raw)
            type(self).last = self
            self.own = {}

        def raw_sig(self, key, sym, params=None):
            k = (key, sym, str(params))
            fresh = k not in self._sig
            g = super().raw_sig(key, sym, params)
            if fresh:
                self.own[k] = {'lx': g['lx'].copy(), 'sx': g['sx'].copy()}
                if self.enabled:
                    down, up = regime_trend(self.d[sym])
                    g = dict(g, lx=g['lx'] | down, sx=g['sx'] | up)
                    self._sig[k] = g
            return g
    return RegimeExitBook


def _sha(path):
    with open(path, 'rb') as f:
        return hashlib.sha256(f.read()).hexdigest()


def run(*, regime_exit=True, **kw):
    """R.run with backtest.Book swapped for the regime-exit book for this call only. Returns (packet, sidecar)."""
    cls = make_book(regime_exit)
    old = BT.Book
    BT.Book = cls
    try:
        pkt = _RUNNER_RUN(**kw)
    finally:
        BT.Book = old
    book = cls.last
    global LAST_BOOK
    LAST_BOOK = book
    side = kw['side']
    bt = R._ms(book.t)
    idx = {R._utc(t): j for j, t in enumerate(bt)}
    rows, n_rx = [], 0
    for tr in pkt['trades']:
        s, i_out = tr['symbol'], idx[tr['t_out']]
        own = book.own[(kw['key'], s, 'None')]['lx' if side == 'long' else 'sx'][i_out]
        down, up = regime_trend(book.d[s])
        against = bool((down if side == 'long' else up)[i_out])
        rx = regime_exit and tr['why'] == 'signal' and against and not bool(own)
        n_rx += rx
        rec = dict(symbol=s, t_in=tr['t_in'], t_out=tr['t_out'], why=tr['why'], regime_only_exit=bool(rx),
                   trend_against_at_exit=against)
        if rx and i_out + 1 < len(bt):
            a = book.arr[s]
            sd = 1 if side == 'long' else -1
            # sensitivity: next candle open vs this candle close, as a fraction of the close (+ = next open better)
            rec['next_open_vs_close'] = float(sd * (a['o'][i_out + 1] - a['c'][i_out]) / a['c'][i_out])
        rows.append(rec)
    sens = [r['next_open_vs_close'] for r in rows if 'next_open_vs_close' in r]
    car = {'format': 'zb-structure-regime-exit/1', 'non_promotable': True, 'regime_exit': bool(regime_exit),
            'rule': RULE, 'trend': TREND, 'fill': 'close of the triggering closed candle (lot tf), runner exit slippage + '
            'taker fee; same convention as the own exit signal', 'script_sha256': _sha(os.path.abspath(__file__)),
            'packet_sha256': pkt['packet_sha256'], 'regime_only_exits': int(n_rx),
            'next_open_vs_close_mean': float(np.mean(sens)) if sens else None,
            'next_open_vs_close_abs_mean': float(np.mean(np.abs(sens))) if sens else None,
            'next_open_eq_close_share': float(np.mean(np.array(sens) == 0.0)) if sens else None,
            'trades': rows}
    with open(kw['out'] + '.regime_exit.json', 'w') as f:
        json.dump(car, f, sort_keys=True, indent=1)
    return pkt, car


def main(argv=None) -> int:
    argv = list(sys.argv[1:] if argv is None else argv)
    on = '--no-regime-exit' not in argv
    argv = [a for a in argv if a != '--no-regime-exit']
    holder = {}

    def fake_run(**kw):
        holder['pkt'], holder['side'] = run(regime_exit=on, **kw)
        return holder['pkt']
    real = R.run
    R.run = fake_run
    try:
        rc = R.main(argv)
    finally:
        R.run = real
    if rc == 0:
        print(f"regime_exit={on}: {holder['side']['regime_only_exits']} regime-only exits")
    return rc


if __name__ == '__main__':
    sys.exit(main())
