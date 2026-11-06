"""TNET-02 tests never touch real per-user machine state."""
from __future__ import annotations

import pytest


@pytest.fixture(autouse=True)
def _isolated_machine_pin_store(tmp_path_factory, monkeypatch):
    from tools import tnet02_run
    from newcore.tnet02.loss_store import FileLossHighWater

    monkeypatch.setattr(tnet02_run, "MACHINE_PIN_STORE",
                        tmp_path_factory.mktemp("machine") / ".zackbot" / "tnet02_origin_pins.json")
    monkeypatch.setattr(tnet02_run, "MACHINE_LOSS_STATE_ROOT",
                        tmp_path_factory.mktemp("loss-machine") / ".zackbot" / "tnet02_loss_state")
    high_water = tmp_path_factory.mktemp("loss-high-water") / "record.json"
    monkeypatch.setattr(tnet02_run, "_machine_high_water", lambda: FileLossHighWater(high_water))
