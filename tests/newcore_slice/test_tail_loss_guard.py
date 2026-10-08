"""Codex ruling 13, tail-loss contract: when the store refuses the journal at boot (DAMAGED / UNREADABLE: an acknowledged
tail or the middle of a segment cannot be trusted), the run does not trade and does not simply stop: it runs as a
GUARD - hard HOLD from boot over an in-memory journal that is never persisted - reconciles exchange truth and
emergency-protects the exposure our own orders prove (the zbn1 namespace), then exits 4 for recovery / adoption by the
owner. Nothing is decided, inferred ("never sent") or written to the damaged journal."""
import json
from decimal import Decimal as D

from newcore.runner import app as A
from newcore.runner import config as C
from newcore.runner import ids
from test_run_cli import cfg_file, run


def _state(tmp_path, cfg):
    acct = C.load(cfg).account_id
    return tmp_path / 'nc' / acct, acct


def test_a_damaged_journal_boots_a_guard_that_covers_proven_exposure_and_trades_nothing(tmp_path):
    cfg = cfg_file(tmp_path)
    assert run(['run', '--config', cfg, '--cycles', '27', '--enable-candidate'])[0] == 0      # a BTC LONG lot is open
    d, acct = _state(tmp_path, cfg)
    st = json.loads((d / A.STATE_FILE).read_text())
    pos = [p for p in st['positions'] if p[1] == 'LONG' and D(p[2]) > 0]
    assert pos, 'the fixture run must end with an open lot'
    stops = [o for o in st['orders'] if o['type'] == 'STOP_MARKET' and o['status'] == 'NEW']
    assert len(stops) == 1
    lot_qty = D(pos[0][2])
    pos[0][2] = str(lot_qty + D('3'))                                   # exposure the journal cannot own: +3
    (d / A.STATE_FILE).write_text(json.dumps(st))
    seg = sorted((d / 'journal').iterdir())[-1]
    raw = bytearray(seg.read_bytes())
    raw[len(raw) // 2] ^= 0xFF                                          # damage in the middle of the segment
    seg.write_bytes(bytes(raw))
    before = {p.name: p.read_bytes() for p in (d / 'journal').iterdir()}
    code, out = run(['run', '--config', cfg, '--cycles', '5', '--enable-candidate'])
    assert code == A.EXIT_STORE_HOLD and 'GUARD' in out and 'hold=durability_unavailable' in out
    assert {p.name: p.read_bytes() for p in (d / 'journal').iterdir()} == before          # never written
    st2 = json.loads((d / A.STATE_FILE).read_text())
    em = [o for o in st2['orders'] if ids.is_emergency_client_id(o['client_id']) and o['status'] == 'NEW']
    assert [(D(o['qty']), D(o['stop'])) for o in em] == [(D('3'), D(stops[0]['stop']))]   # the gap, at our level
    assert not [o for o in st2['orders'] if o['type'] == 'MARKET' and o not in st['orders']]   # nothing traded
    assert len(st2['fills']) == len(st['fills'])
