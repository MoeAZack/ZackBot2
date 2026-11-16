"""Offline red-team: recorded stop must actually cover the current lot."""
import sys
from pathlib import Path
import pytest
import engine as E

from test_lane3_recovery_redteam import no_network  # noqa: F401 - autouse fixture

sys.path.insert(0, str(Path(__file__).parent))
from test_aud04_stop_verify import mk, lot_open


@pytest.fixture(autouse=True)
def restore_fake_factory(monkeypatch):
    monkeypatch.setattr(E, 'Futures', E.Futures)


def test_recorded_stop_with_insufficient_quantity_is_not_confirmed(tmp_path):
    engine, clock, _ = mk(tmp=str(tmp_path))
    key = lot_open(engine)
    lot = engine.state['lots'][key]
    tag = lot['stop_id']
    symbol, side, quantity, price = engine.trade.stops[tag]
    # The exchange still lists the recorded stop, but it covers only half of the lot.
    engine.trade.stops[tag] = (symbol, side, quantity / 2, price)
    lot['stop_confirmed_t'] = None
    engine._verify_symbol(symbol, 'red-team')
    assert not engine.stop_view(lot)['protected']
