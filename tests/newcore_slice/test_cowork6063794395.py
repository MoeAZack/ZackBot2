"""Cowork's re-run on ef731da (PR #37, issuecomment 6063794395), the items still open on facd4b6 - repros first:

p3   a position AND its stop closed outside the bot: the lot stayed open and the runner still tried to protect it.
     Now the protect phase reads the side flat at the venue (after re-reading the lot's own orders, so a fill of ours in
     flight is booked first): a durable 'external close suspected' owner item, HOLD, the lot leaves management and
     NOTHING is sent for it.
G3   the guard left our stray stop resting on a flat venue: it now cancels OUR stops (zbn1, reduce-only) on a side the
     venue shows flat - never while exposed, never a foreign order.
LOW  strategy contract, runner side: a signal for any candle but the one just closed is ignored (incident); a CLOSE
     re-emitted every bar while the exit holds creates one close intent per lot."""
import json
from decimal import Decimal as D

import pytest

from newcore.domain import Action, EntriesMode, Purpose, ReasonCode
from newcore.runner import InjectedSignals, Signal
from newcore.runner import app as A
from newcore.runner import config as C
from mg_helpers import MgWorld, path, signals as mg_signals
from slice_helpers import H4, World, flat_bars
from test_run_cli import cfg_file, run

pytestmark = pytest.mark.usefixtures('journal_kind')
SYM = 'SOLUSDT'
T0 = flat_bars(1)[0].open_ms
SIDES = ('LONG', 'SHORT')


def _sends(w):
    return [e for e in w.port.effects if e in ("stop", "market_reduce")]          # every attempt, refused or not


# ------------------------------------------------------------------------------------------------------- p3
@pytest.mark.parametrize('side', SIDES)
@pytest.mark.parametrize('managed', (False, True))
def test_p3_an_external_flat_is_an_owner_item_and_nothing_is_sent(side, managed):
    if managed:
        w = MgWorld(path(30, {}, side), mg_signals(side), strict=False)
    else:
        sig = InjectedSignals({(SYM, T0 + 6 * H4): (('enter', side),)}, stop_atr=D('2'))
        w = World(flat_bars(30), sig, strict=False)
    w.run(7)
    lot, = w.runner.fold.open_lots()
    w.venue.external_cancel(lot.live_stop.intent.client_order_id)  # the stop AND the position, outside the bot
    w.venue._positions.pop((SYM, side), None)
    n0 = len(_sends(w))
    w.run(20)
    r = w.runner
    assert len(_sends(w)) == n0                                    # nothing sent for the lot, ever
    assert r.fold.mode is EntriesMode.HOLD
    items = [d for d in r.fold.decisions.values() if d.action is Action.WAIT and 'external close' in d.detail]
    assert len(items) == 1 and lot.lot_id not in getattr(r, 'mg', {})
    w.restart()
    w.run(22)
    assert len(_sends(w)) == n0


# ------------------------------------------------------------------------------------------------------- G3
def test_g3_the_guard_cancels_our_stray_stop_on_a_flat_venue_never_a_foreign_one(tmp_path):
    cfg = cfg_file(tmp_path)
    assert run(['run', '--config', cfg, '--cycles', '27', '--enable-candidate'])[0] == 0      # an open BTC lot
    d = tmp_path / 'nc' / C.load(cfg).account_id
    st = json.loads((d / A.STATE_FILE).read_text())
    for p in st['positions']:
        p[2] = '0'                                                     # flattened outside the bot ...
    ours, = [o for o in st['orders'] if o['type'] == 'STOP_MARKET' and o['status'] == 'NEW']   # ... our stop stays
    foreign = dict(ours, client_id='manual-stop-1', eoid='999999', seq=ours['seq'] + 100)
    st['orders'].append(foreign)
    st['seq'] = foreign['seq']
    (d / A.STATE_FILE).write_text(json.dumps(st))
    seg = sorted((d / 'journal').iterdir())[-1]
    raw = bytearray(seg.read_bytes())
    raw[len(raw) // 2] ^= 0xFF
    seg.write_bytes(bytes(raw))
    code, out = run(['run', '--config', cfg, '--cycles', '5', '--enable-candidate'])
    assert code == A.EXIT_STORE_HOLD and 'GUARD' in out
    st2 = json.loads((d / A.STATE_FILE).read_text())
    status = {o['client_id']: o['status'] for o in st2['orders']}
    assert status[ours['client_id']] == 'CANCELED' and status['manual-stop-1'] == 'NEW'


# ------------------------------------------------------------------------------------------------------- LOW
class StaleSignals(InjectedSignals):
    """A strategy that (wrongly) re-emits the signal of an OLDER candle."""

    def decide(self, symbol, bars, as_of_ms):
        out = super().decide(symbol, bars, as_of_ms)
        return tuple(Signal(action=s.action, side=s.side, candle_close_ms=s.candle_close_ms - H4,
                            stop_distance=s.stop_distance, reason=s.reason) for s in out)


def test_a_signal_for_an_older_candle_is_ignored():
    sig = StaleSignals({(SYM, T0 + 6 * H4): (('enter', 'LONG'),)}, stop_atr=D('2'))
    w = World(flat_bars(20), sig)
    w.run(8)
    r = w.runner
    assert not [d for d in r.fold.decisions.values() if d.action is Action.ENTER] and not r.fold.open_lots()
    assert any('stale signal' in t for _, t in r.incidents)


@pytest.mark.parametrize('side', SIDES)
def test_a_close_re_emitted_every_bar_makes_one_close_intent(side):
    s = {(SYM, T0 + 6 * H4): (('enter', side),)}
    for b in range(8, 14):
        s[(SYM, T0 + (b + 1) * H4)] = (('close', side),)              # the exit holds: CLOSE every bar
    w = World(flat_bars(20), InjectedSignals(s, stop_atr=D('2')))
    w.run(15)
    r = w.runner
    closes = [iv for iv in r.fold.intents.values() if iv.purpose is Purpose.CLOSE]
    assert len(closes) == 1 and closes[0].intent.reason is ReasonCode.EXIT_SIGNAL
    assert len(r.trades()) == 1 and not r.fold.open_lots()
