"""Intra-candle marks for management (M4): the replay / fake clock between two candle closes.

A live runner sees the mark price between candle closes (testnet: polled; Runner.mark), so a bot-side target / add is
triggered AT its level, not at the next candle close. For FakeVenue replay, `play_candle` reproduces that along the
golden zb-path/1 path (newcore.management.sim / the golden newcore_sim adapter):

  - the path: green open -> low -> high -> close, red open -> high -> low -> close, doji the worst case for the position;
  - at each point: a level already THROUGH at the point is hit at the point (a gap at the open fills at the open);
    otherwise the levels the path reaches before the next point, nearest first; at one price the order is stop, TP1,
    TP2, add (protect first);
  - stop-first for the target / stop ambiguity: when a resting stop of that side lies inside the candle's range at the
    open, no target triggers in that candle (adds still do);
  - a stop is triggered on the venue (venue.trigger_stop) and the runner reads it at once (Runner.intrabar_sync), so a
    later level in the same candle sees the position the stop left; a target / add is offered to the runner at its
    level (Runner.mark): the driver fires it and the venue fills the market order at that mark;
  - orders placed after a fill (a raised / break-even stop) rest from that point on.
The candle-close cycle (Runner.cycle) runs after it, unchanged. Used only with management enabled: with management off
the clock is venue.advance_to + cycle, byte for byte as before.
"""
from __future__ import annotations

from newcore.adapters.fake_venue import path_points

PRIORITY = {'stop': 0, 'tp1': 1, 'tp2': 2, 'add': 3}
MAX_EVENTS = 64


def _through(price, falls, x):
    return x <= price if falls else x >= price


def play_candle(venue, runner, t_ms):
    """Walk the candle closing at t_ms on every symbol, feeding the runner its marks; then close the candle."""
    resumed = venue.open_candle()                                 # a restart mid-candle resumes the walk:
    bars = resumed or venue.begin_candle(t_ms)
    if resumed:                                                   # first what the dead process decided (journaled
        runner.intrabar_sync(min(b.open_ms for b in bars.values()))   # drafts) goes out at the mark it died at
    for sym in sorted(bars):                                      # an exception (a crash) leaves it open
        _walk(venue, runner, sym, bars[sym])
    venue.end_candle(t_ms)


def _walk(venue, runner, sym, bar):
    at = bar.open_ms
    side = runner.mark_side(sym) or 'LONG'
    pts = path_points(bar.open, bar.high, bar.low, bar.close, side)
    blocked = {sd for _, sd, sp in venue.stop_levels(sym) if bar.low <= sp <= bar.high}
    tried = set()                                     # (leg, price) offered at a point that did not fire

    def levels():
        out = [('stop', sp, sd == 'LONG', cid) for cid, sd, sp in venue.stop_levels(sym)]
        for leg, price, falls, lot_side in runner.mark_levels(sym):
            if leg in ('tp1', 'tp2') and lot_side in blocked:
                continue
            if (leg, price) not in tried:
                out.append((leg, price, falls, None))
        return out

    def hit(kind, price, cid, px):
        if kind == 'stop':
            venue.trigger_stop(cid, px)
            runner.intrabar_sync(at)
            return
        venue.set_mark(sym, px)
        if not runner.mark(sym, px, at):
            tried.add((kind, price))

    x = pts[0]
    venue.set_mark(sym, x)
    n = 0
    for y in pts:
        while True:
            n += 1
            if n > MAX_EVENTS:
                raise RuntimeError(f'{sym} {bar.open_ms}: the intra-candle walk did not settle')
            lv = levels()
            through = [(PRIORITY[k], k, p, cid) for k, p, falls, cid in lv if _through(p, falls, x)]
            if through:
                _, k, p, cid = min(through, key=lambda v: v[0])
                hit(k, p, cid, x)
                continue
            cand = [(abs(p - x), PRIORITY[k], k, p, cid) for k, p, falls, cid in lv
                    if (falls and y <= p < x) or (not falls and x < p <= y)]
            if not cand:
                break
            _, _, k, p, cid = min(cand)
            x = p
            venue.set_mark(sym, x)
            hit(k, p, cid, p)
        x = y
        venue.set_mark(sym, x)
        tried.clear()
