import importlib.util
from pathlib import Path
import pytest

PATH = Path(__file__).resolve().parents[1] / 'tools' / 'research' / 'lane2_lead_replay.py'
spec = importlib.util.spec_from_file_location('lane2_lead_replay', PATH)
replay = importlib.util.module_from_spec(spec)
spec.loader.exec_module(replay)


def test_proxy_hook_records_charge_before_unchanged_deduction(tmp_path):
    target = tmp_path / 'backtest.py'
    target.write_text("def run(p, c, FPB, eq, fill_log, s, i):\n                eq -= p['qty'] * c * FPB; p['realized'] -= p['qty'] * c * FPB\n                return eq\n", encoding='utf-8')
    replay.instrument(target)
    ns = {}
    exec(target.read_text(encoding='utf-8'), ns)
    p = {'qty': 2., 'realized': 0., 'i': 3}
    log = []
    assert ns['run'](p, 100., .00005, 1000., log, 'ABC', 4) == 999.99
    assert p['realized'] == -.01
    assert log == [{'kind':'__proxy__', 'sym':'ABC', 'pos_i':3, 'i':4, 'qty':2., 'ref':100., 'cash':.01}]


def test_instrument_fails_closed_if_source_changed(tmp_path):
    target = tmp_path / 'backtest.py'
    target.write_text('pass\n', encoding='utf-8')
    with pytest.raises(RuntimeError, match='instrumentation point changed'):
        replay.instrument(target)
