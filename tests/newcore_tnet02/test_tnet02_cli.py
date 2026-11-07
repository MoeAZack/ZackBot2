"""TNET-02 CLI: identity refusals before anything else, plan output, proof conversion, real run refused."""
from __future__ import annotations

import json
import time

import pytest

from newcore.domain.account import confirmation_phrase
from newcore.testnet_harness import run_suite
from tools import tnet02_run as cli

from .support import ACCOUNT_ID, BUILD_SHA, DIGEST, manifest, rig
from newcore.venue.binance_usdm_testnet import TESTNET_BASE_URL as ORIGIN


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


LIVE_KEY = "TNET02LIVEKEYMARKER0000000000000a"
LIVE_SECRET = "TNET02LIVESECRETMARKER00000000000b"


def _live_account(tmp_path):
    from newcore.tnet02.wiring import key_digest
    digest = key_digest(LIVE_KEY)
    return _account_file(tmp_path, key_digest=digest, typed_phrase=confirmation_phrase(ACCOUNT_ID, digest))


def _run_args(tmp_path, account, *extra):
    journals, out = tmp_path / "journals", tmp_path / "out"
    journals.mkdir(exist_ok=True)
    out.mkdir(exist_ok=True)
    return ["run", "--expected-head", BUILD_SHA, "--account-file", str(account), "--symbols", "BTCUSDT",
            "--origin", ORIGIN, "--journal-dir", str(journals), "--out-dir", str(out), "--host-id", "tnet_x", *extra]


@pytest.fixture
def no_network(monkeypatch):
    import newcore.venue as venue_pkg

    def explode(*_a, **_k):
        raise AssertionError("network sender constructed")
    monkeypatch.setattr(venue_pkg, "TestnetHttpSender", explode)


def test_run_refuses_without_credentials_before_any_network(tmp_path, identity, capsys, monkeypatch, no_network):
    monkeypatch.delenv("ZACKBOT_TNET02_API_KEY", raising=False)
    monkeypatch.delenv("ZACKBOT_TNET02_API_SECRET", raising=False)
    assert cli.main(_run_args(tmp_path, _live_account(tmp_path))) == cli.EXIT_REFUSED
    assert capsys.readouterr().err.strip() == "REFUSED: credentials_api_key_missing"


def test_run_refuses_a_key_that_is_not_the_confirmed_one(tmp_path, identity, capsys, monkeypatch, no_network):
    monkeypatch.setenv("ZACKBOT_TNET02_API_KEY", "SomeOtherTestnetKey0000000000000")
    monkeypatch.setenv("ZACKBOT_TNET02_API_SECRET", LIVE_SECRET)
    assert cli.main(_run_args(tmp_path, _live_account(tmp_path))) == cli.EXIT_REFUSED
    err = capsys.readouterr().err
    assert err.strip() == "REFUSED: credentials_digest_mismatch" and LIVE_SECRET not in err


def test_run_refuses_wrong_execute_ack_and_dirty_build(tmp_path, identity, capsys, monkeypatch, no_network):
    monkeypatch.setenv("ZACKBOT_TNET02_API_KEY", LIVE_KEY)
    monkeypatch.setenv("ZACKBOT_TNET02_API_SECRET", LIVE_SECRET)
    assert cli.main(_run_args(tmp_path, _live_account(tmp_path), "--execute", "yes")) == cli.EXIT_REFUSED
    assert capsys.readouterr().err.strip() == "REFUSED: execute_ack"
    identity["dirty"] = " M x.py"
    assert cli.main(_run_args(tmp_path, _live_account(tmp_path), "--execute", "TESTNET")) == cli.EXIT_REFUSED
    assert capsys.readouterr().err.strip() == "REFUSED: worktree_dirty"


def test_run_preflight_reads_only_and_reports_isolation(tmp_path, identity, capsys, monkeypatch):
    import newcore.venue as venue_pkg
    from .fake_binance import FakeBinance, FakeClock, RealtimeClock

    fake = FakeBinance(RealtimeClock())  # CLI signs in server time
    monkeypatch.setattr(venue_pkg, "TestnetHttpSender", lambda origin: fake)
    monkeypatch.setenv("ZACKBOT_TNET02_API_KEY", LIVE_KEY)
    monkeypatch.setenv("ZACKBOT_TNET02_API_SECRET", LIVE_SECRET)
    assert cli.main(_run_args(tmp_path, _live_account(tmp_path))) == cli.EXIT_OK
    out = capsys.readouterr()
    assert "isolated=true" in out.out and "no order was sent" in out.out
    assert {m for m, _p, _q in fake.requests} == {"GET"}
    assert LIVE_KEY not in out.out + out.err and LIVE_SECRET not in out.out + out.err
    assert "/fapi/v1/exchangeInfo" in [p for _m, p, _q in fake.requests]
    fake.positions[("ETHUSDT", "SHORT")] = __import__("decimal").Decimal("1")
    assert cli.main(_run_args(tmp_path, _live_account(tmp_path))) == cli.EXIT_REFUSED
    assert "isolated=false" in capsys.readouterr().out


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
