"""BREADTH-FRESH134-D01: freeze integrity and input checks (synthetic files; no network, no repo data, no outcomes)."""
import os, sys, json, hashlib
import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path[:0] = [os.path.join(ROOT, 'research', 'extra31_screen')]
import breadth_fresh134_run as W
import run_screen as RS

FZ = W.load_freeze()
PANEL = json.load(open(os.path.join(ROOT, 'research', 'extra31_screen', 'sieve_v1_panel_cases.json'), encoding='utf-8'))
MP = json.load(open(os.path.join(ROOT, 'research', 'extra31_screen', 'sieve_v1_mp_variant.json'), encoding='utf-8'))


def test_freeze_universe_is_the_locked_134_without_btc_or_used_coins():
    coins = FZ['universe']['traded']
    assert len(coins) == 134 == len(set(coins)) and coins == sorted(coins)
    assert RS.BTC not in coins
    assert not set(coins) & set(RS.EXTRA31)
    assert FZ['data']['input_manifest_sha256'] == 'fb0c0572b6c2bc5f569c64d89bd0ee2ca1dd9e3cf96b1ed8bd1110c341ed2f3e'


def test_candidate_is_the_unchanged_frozen_panel_case():
    case = PANEL['cases'][W.CASE]
    assert case['commit'] == FZ['candidate']['commit'] == '646ab3fa1be92614998905e6ab3497ab03a7d382'
    assert case['source'] == FZ['candidate']['source'] == 'cp345'
    assert RS.CONTRACT['sources']['cp345']['commit'] == case['commit']
    assert case['params'] == dict(lo=0.25, hi=0.75, win=5) and case['side'] == 'both' and case['tf'] == '1d'
    assert case['management'] == dict(stop_atr=2.5, tp_r=3.0, max_bars=20, tp1_r=1.0, tp1_frac=0.5, be_r=1.0)
    # max3 must be a value the unchanged panel runner accepts for this case; max5 is not offered by the wrapper
    assert 3 in MP['max_pos'] and W.CASE in MP['cases']
    assert set(FZ['runs']) == {'primary', 'secondary'} and FZ['runs']['primary']['max_pos'] == 1 and FZ['runs']['secondary']['max_pos'] == 3


def _sha(b):
    return hashlib.sha256(b).hexdigest()


@pytest.fixture
def fresh(tmp_path, monkeypatch):
    coins = FZ['universe']['traded']
    (tmp_path / 'clean').mkdir()
    syms = {}
    for s in [RS.BTC] + coins:
        b = f't,o,h,l,c,v,qv\n0,1,1,1,1,1,{s}\n'.encode('utf-8')
        (tmp_path / 'clean' / f'{s}_1h.csv').write_bytes(b)
        syms[s] = dict(sha256=_sha(b))
    (tmp_path / 'selected.json').write_text(json.dumps(dict(selected=list(reversed(coins)))), encoding='utf-8')
    man = json.dumps(dict(symbols=syms)).encode('utf-8')
    (tmp_path / 'input_manifest.json').write_bytes(man)
    fz = json.loads(json.dumps(FZ)); fz['data']['input_manifest_sha256'] = _sha(man)
    return tmp_path, fz


def test_inputs_pass_when_everything_matches(fresh):
    d, fz = fresh
    coins, msha, got = W.check_inputs(str(d), fz)
    assert coins == FZ['universe']['traded'] and len(got) == 135 and msha == fz['data']['input_manifest_sha256']


def test_inputs_refuse_a_different_manifest(fresh):
    d, fz = fresh
    with pytest.raises(SystemExit, match='manifest sha256'):
        W.check_inputs(str(d), FZ)            # real frozen sha != synthetic manifest


def test_inputs_refuse_a_changed_csv(fresh):
    d, fz = fresh
    p = d / 'clean' / f'{FZ["universe"]["traded"][5]}_1h.csv'
    p.write_bytes(p.read_bytes() + b'1,1,1,1,1,1,1\n')
    with pytest.raises(SystemExit, match='csv sha256'):
        W.check_inputs(str(d), fz)


def test_inputs_refuse_a_different_selection(fresh):
    d, fz = fresh
    sel = FZ['universe']['traded'][:-1] + ['ETHUSDT']
    (d / 'selected.json').write_text(json.dumps(dict(selected=sel)), encoding='utf-8')
    with pytest.raises(SystemExit, match='selected.json'):
        W.check_inputs(str(d), fz)


def test_inputs_refuse_a_missing_or_extra_manifest_symbol(fresh):
    d, fz = fresh
    m = json.loads((d / 'input_manifest.json').read_text(encoding='utf-8'))
    m['symbols'].pop(RS.BTC)
    b = json.dumps(m).encode('utf-8'); (d / 'input_manifest.json').write_bytes(b)
    fz['data']['input_manifest_sha256'] = _sha(b)
    with pytest.raises(SystemExit, match='manifest symbols'):
        W.check_inputs(str(d), fz)
