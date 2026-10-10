"""The window walk shared by fills(start, end) and trades(symbol, side, from_ms) never returns a FALSE complete.

Cowork 6069451524: `seen` spans windows but only in-window rows were emitted, so a row first met on window 1's fromId
continuation (timed in window 2) was later skipped in window 2 as an "overlap" and dropped, while the read said
OK / complete. Here: that repro, and a seeded stateful fuzz against a simulated userTrades server (plain, duplicate-
injecting, overlap-replaying) - an OK answer must be EXACTLY the true rows; UNKNOWN is always allowed.
Fake HTTP only, DUMMY keys, no network."""
import json
import random
import zlib
from urllib.parse import parse_qsl

import pytest

from newcore.ports import venue as P
from newcore.venue import testnet_venue as TV
from newcore.venue.credentials import StaticCredentials
from newcore.venue.transport import BinanceTestnetTransport, PositionMode

from ncv_support import DUMMY_KEY, DUMMY_SECRET, raw
from test_ncv_venue_reads import NOW_MS, ok, trade, venue

DAY = 24 * 3600 * 1000
WEEK = TV.FILL_WINDOW_MS + 1


def test_cowork_repro_a_continuation_row_of_the_next_window_is_emitted_not_dropped(monkeypatch):
    monkeypatch.setattr(TV, 'USER_TRADES_LIMIT', 2)
    start = NOW_MS - 9 * DAY
    w1 = start + TV.FILL_WINDOW_MS                                     # window 1 = [start, w1], window 2 = [w1+1, now]
    t3 = trade(3, t=w1 + 1000)                                         # in window 2
    v, http = venue(ok([trade(1, t=start + 1), trade(2, t=start + 2)]), ok([t3]), ok([t3]))
    out = v.fills('SOLUSDT', start_ms=start, end_ms=NOW_MS)
    assert len(http.requests) == 3
    assert out.kind is P.ReadKind.OK and [f.trade_id for f in out.value] == ['1', '2', '3']
    assert out.detail == 'complete pages=3 dups=0'                     # a re-fetch across windows is not a dup
    v, _ = venue(ok([trade(1, t=start + 1), trade(2, t=start + 2)]), ok([t3]), ok([t3]))
    out = v.trades('SOLUSDT', 'LONG', start)
    assert out.kind is P.ReadKind.OK and [f.trade_id for f in out.value] == ['1', '2', '3']


# ---------------------------------------------------------------------------------------------- stateful fuzz
class Server:
    """A userTrades endpoint over a fixed trade list (ids ascending, time non-decreasing): bounded queries answer the
    rows inside [startTime, endTime]; fromId queries the rows with id >= fromId; both cut at `limit` RAW rows.
    mode 'dup' sometimes repeats a row inside a page; mode 'replay' sometimes starts a fromId page with the row
    before fromId (an overlap with the previous page)."""

    def __init__(self, rows, mode, rng):
        self.rows, self.mode, self.rng, self.requests = rows, mode, rng, []

    def __call__(self, request):
        self.requests.append(request)
        q = dict(parse_qsl(request.query))
        lim = int(q['limit'])
        if 'fromId' in q:
            page = [r for r in self.rows if r['id'] >= int(q['fromId'])]
            if self.mode == 'replay' and self.rng.random() < 0.5:
                before = [r for r in self.rows if r['id'] == int(q['fromId']) - 1]
                page = before + page
        else:
            page = [r for r in self.rows if int(q['startTime']) <= r['time'] <= int(q['endTime'])]
        page = page[:lim]
        if self.mode == 'dup' and page and self.rng.random() < 0.4:
            i = self.rng.randrange(len(page))
            page = (page[:i + 1] + [page[i]] + page[i + 1:])[:lim]
        return raw(200, json.dumps(page).encode(), {'X-MBX-USED-WEIGHT-1M': '1'})


def make_rows(rng, start):
    n = rng.randrange(0, 40)
    lo, hi = start - 2 * DAY, NOW_MS + DAY
    times = sorted(rng.choice([rng.randrange(lo, hi), start + rng.randrange(0, 3) * WEEK - rng.randrange(0, 3)])
                   for _ in range(n))
    return [dict(trade(i + 1, t=t, ps=rng.choice(['LONG', 'SHORT'])), id=i + 1) for i, t in enumerate(times)]


def run_case(seed, mode, read):
    rng = random.Random(seed)
    start = NOW_MS - rng.randrange(1, 22) * DAY + rng.randrange(0, DAY)
    rows = make_rows(rng, start)
    TV.USER_TRADES_LIMIT = rng.choice([1, 2, 3, 5])
    server = Server(rows, mode, rng)
    t = BinanceTestnetTransport(environment='testnet', http=server, clock=lambda: NOW_MS,
                                position_mode=PositionMode.HEDGE, credentials=StaticCredentials(DUMMY_KEY, DUMMY_SECRET))
    v = TV.TestnetVenue(t, lambda: NOW_MS)
    if read == 'fills':
        out = v.fills('SOLUSDT', start_ms=start, end_ms=NOW_MS)
        truth = [str(r['id']) for r in rows if start <= r['time'] <= NOW_MS]
    else:
        side = rng.choice(['LONG', 'SHORT'])
        out = v.trades('SOLUSDT', side, start)
        truth = [str(r['id']) for r in rows if start <= r['time'] <= NOW_MS and r['positionSide'] == side]
    if out.kind is not P.ReadKind.OK:
        return 'unknown'
    got = [f.trade_id for f in out.value]
    return 'ok' if sorted(got, key=int) == truth else f'FALSE COMPLETE seed {seed}: got {got} truth {truth}'


@pytest.mark.parametrize('read', ['fills', 'trades'])
@pytest.mark.parametrize('mode', ['plain', 'dup', 'replay'])
def test_the_window_walk_never_returns_a_false_complete(monkeypatch, mode, read):
    monkeypatch.setattr(TV, 'USER_TRADES_LIMIT', TV.USER_TRADES_LIMIT)          # restored after the test
    monkeypatch.setattr(TV, 'FILL_WINDOW_PAGES', 10_000)                         # the bound is not under test here
    monkeypatch.setattr(TV, 'TRADES_MAX_PAGES', 10_000)
    base = zlib.crc32(f'{mode}:{read}'.encode()) * 1000                      # stable across processes
    results = [run_case(base + i, mode, read) for i in range(300)]
    bad = [r for r in results if r.startswith('FALSE')]
    assert bad == []
    assert results.count('ok') >= 270                       # a real server answer is complete almost always
