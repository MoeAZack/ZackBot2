"""TNET-02 tests never touch the real per-user origin pin store."""
from __future__ import annotations

import pytest


@pytest.fixture(autouse=True)
def _isolated_machine_pin_store(tmp_path_factory, monkeypatch):
    from tools import tnet02_run

    monkeypatch.setattr(tnet02_run, "MACHINE_PIN_STORE",
                        tmp_path_factory.mktemp("machine") / ".zackbot" / "tnet02_origin_pins.json")
