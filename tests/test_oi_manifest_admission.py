"""Production parity tests for the signed OI-LOADER-01 admission gate."""
import copy
import os
import sys

import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, 'tools', 'research'))

import oi_manifest_admission as PROD  # noqa: E402
from test_oi_loader01_contract import (  # noqa: E402
    ALONE, BASE, P02, REQ, REQ2, TWO, content, edit, evaluate,
)


def _materialize(root, manifest):
    for item in manifest['files']:
        path = root.joinpath(*item['path'].split('/'))
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(content(item['path'], item['bytes']))


def test_stream_verifies_and_admits(tmp_path):
    _materialize(tmp_path, TWO)
    assert PROD.admit(TWO, ['BTCUSDT', 'ETHUSDT'], *REQ2, tmp_path) == (
        True, {'BTCUSDT': [], 'ETHUSDT': []})


def test_all_or_nothing_on_disk_failure(tmp_path):
    _materialize(tmp_path, TWO)
    path = tmp_path.joinpath(*P02.split('/'))
    path.write_bytes(b'z' * path.stat().st_size)
    ok, per = PROD.admit(TWO, ['BTCUSDT', 'ETHUSDT'], *REQ2, tmp_path)
    assert ok is False
    assert per == {'BTCUSDT': ['DISK:sha256'], 'ETHUSDT': []}


@pytest.mark.parametrize('cid', list(ALONE))
def test_matches_reference_conjunct_refusals(cid, tmp_path):
    candidate = edit(BASE, ALONE[cid])
    _materialize(tmp_path, candidate)
    assert PROD.evaluate_symbol(candidate, *REQ, tmp_path) == evaluate(candidate, *REQ)


def test_confined_path_refuses_escape(tmp_path):
    with pytest.raises(OSError):
        PROD.confined_path(tmp_path, '../outside.zip')


def test_manifest_object_is_not_mutated(tmp_path):
    candidate = copy.deepcopy(BASE)
    before = copy.deepcopy(candidate)
    _materialize(tmp_path, candidate)
    PROD.evaluate_symbol(candidate, *REQ, tmp_path)
    assert candidate == before
