"""TNET-02 CLI: identity refusals before anything else, plan output, proof conversion, real run refused."""
from __future__ import annotations

import json

import pytest

from newcore.domain.account import confirmation_phrase
from newcore.testnet_harness import run_suite
from tools import tnet02_run as cli

from .support import ACCOUNT_ID, BUILD_SHA, DIGEST, manifest, rig


def _account_file(tmp_path, **override):
    raw = {"account_id": ACCOUNT_ID, "label": "Dedicated TNET-02", "key_digest": DIGEST, "hedge_mode": True,
           "settlement_asset": "USDT", "confirmed_at_ms": 1_900_000_000_000,
           "typed_phrase": confirmation_phrase(ACCOUNT_ID, DIGEST)}
    raw.update(override)
    path = tmp_path / "account.json"
    path.write_text(json.dumps(raw), encoding="utf-8")
    return path


@pytest.fixture
def identity(monkeypatch):
    state = {"head": BUILD_SHA, "dirty": ""}

    def git(*args):
        return state["head"] if args[0] == "rev-parse" else state["dirty"]
    monkeypatch.setattr(cli, "_git", git)
    return state


def test_plan_prints_frozen_matrix(tmp_path, identity, capsys):
    code = cli.main(["plan", "--expected-head", BUILD_SHA, "--account-file", str(_account_file(tmp_path)),
                     "--symbols", "BTCUSDT,ETHUSDT", "--started-ms", "2000000000000"])
    out = capsys.readouterr().out
    assert code == cli.EXIT_OK
    assert out.count("\nCASE ") == 12 and "TESTNET" in out and "long_target" in out and "ip_ban_418" in out


@pytest.mark.parametrize("head, dirty, code", [("c" * 40, "", "head_mismatch"), (BUILD_SHA, " M x.py", "worktree_dirty")])
def test_plan_refuses_wrong_or_dirty_build(tmp_path, identity, capsys, head, dirty, code):
    identity["head"], identity["dirty"] = head, dirty
    assert cli.main(["plan", "--expected-head", BUILD_SHA, "--account-file", str(_account_file(tmp_path)),
                     "--symbols", "BTCUSDT"]) == cli.EXIT_REFUSED
    assert capsys.readouterr().err.strip() == f"REFUSED: {code}"


def test_plan_refuses_bad_account_or_symbols(tmp_path, identity, capsys):
    bad = _account_file(tmp_path, typed_phrase="BIND wrong")
    assert cli.main(["plan", "--expected-head", BUILD_SHA, "--account-file", str(bad), "--symbols", "BTCUSDT"]) == 3
    good = _account_file(tmp_path)
    assert cli.main(["plan", "--expected-head", BUILD_SHA, "--account-file", str(good),
                     "--symbols", "BTCUSDT,ETHUSDT,SOLUSDT"]) == 3
    assert "manifest_validation_binding_symbols" in capsys.readouterr().err


def test_real_run_is_refused_in_this_build(capsys):
    assert cli.main(["run"]) == cli.EXIT_REFUSED
    assert "real_run_not_wired_in_this_build" in capsys.readouterr().err


def test_proof_command(tmp_path, capsys):
    out: list[bytes] = []
    run_suite(manifest(), rig(tmp_path).adapter, out.append)
    report = tmp_path / "report.json"
    report.write_bytes(out[0])
    acct = str(_account_file(tmp_path))
    assert cli.main(["proof", "--expected-head", BUILD_SHA, "--account-file", acct, "--report", str(report),
                     "--host-id", "tnet_x"]) == 0
    printed = json.loads(capsys.readouterr().out)
    assert printed["passed"] is True and printed["kind"] == "real_tnet_exact_head"
    assert cli.main(["proof", "--expected-head", "c" * 40, "--account-file", acct, "--report", str(report),
                     "--host-id", "tnet_x"]) == 3
    assert "gate_head_mismatch" in capsys.readouterr().err
    other = tmp_path / "other"
    other.mkdir()
    foreign = str(_account_file(other, account_id="acct_" + "f" * 32,
                                typed_phrase=confirmation_phrase("acct_" + "f" * 32, DIGEST)))
    assert cli.main(["proof", "--expected-head", BUILD_SHA, "--account-file", foreign, "--report", str(report),
                     "--host-id", "tnet_x"]) == 3
    assert "gate_account_mismatch" in capsys.readouterr().err
