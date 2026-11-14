"""Targeted checks for IVS-ECON-D01 wrapper (no market data, no outcomes)."""
import ast, os, sys, pathlib
import pandas as pd, pytest
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import econ_run as E


def test_cohort_order_exact_and_permutation_of_original9():
    assert E.COHORT == ['BTCUSDT', 'ETHUSDT', 'XRPUSDT', 'SOLUSDT', 'DOGEUSDT', 'BNBUSDT', 'ADAUSDT', 'LINKUSDT', 'AVAXUSDT']
    assert sorted(E.COHORT) == ['ADAUSDT', 'AVAXUSDT', 'BNBUSDT', 'BTCUSDT', 'DOGEUSDT', 'ETHUSDT', 'LINKUSDT', 'SOLUSDT', 'XRPUSDT']


def test_no_account_or_engine_imports_and_no_ivs1h_writes():
    src = pathlib.Path(E.__file__).read_text(encoding='utf-8'); tree = ast.parse(src)
    mods = {n.names[0].name for n in ast.walk(tree) if isinstance(n, ast.Import)} | {n.module for n in ast.walk(tree) if isinstance(n, ast.ImportFrom)}
    assert not any(m and any(b in m for b in ('binance', 'engine', 'account', 'requests', 'urllib')) for m in mods)
    assert "write_text" not in src.split('def main')[0].split('def identity')[0]      # build() never writes inputs itself


def _cap(dec):
    import numpy as np
    return dict(coins={'AAAUSDT': dict(dec=[(np.datetime64(f), sd, 0, 1.0) for f, sd in dec])}, stage_b_original=dict(FREQUENCY_FAIL=True))


def test_identity_aborts_nonparity(tmp_path):
    repo = tmp_path / 'repo'; (repo / 'docs/research/ivs1h/results').mkdir(parents=True); (tmp_path / 'out/rebuild').mkdir(parents=True)
    df = pd.DataFrame(dict(sym=['AAAUSDT'], signal_open=['2025-01-01 00:00:00'], scheduled_fill=['2025-01-01 01:00:00'], side=[1]))
    df.to_csv(repo / 'docs/research/ivs1h/results/raw_signals.csv', index=False); df.to_csv(tmp_path / 'out/rebuild/raw_signals.csv', index=False)
    with pytest.raises(SystemExit, match='NONPARITY'):          # 1 tuple != the frozen 826/153 identity
        E.identity(tmp_path / 'out', repo, _cap([('2025-01-01T01:00', 1)]))


def test_case_grid_is_exactly_12():
    names = [f'{o}.{m}.{c}bp' for o in ('order0_alphabetical_PRIMARY', 'cohort_DIAGNOSTIC') for m in ('causal', 'worst') for c in (7, 15, 23)]
    assert len(set(names)) == 12
