"""Codex P2 (#13, 6064226836), runner CLI: a Ctrl+C at ANY filesystem boundary (open, write, fsync, rename) of the
report, the per-scenario detail or a scenario cassette / sidecar: every file is still written complete (SIGINT
deferred, one retry from the in-memory result), no *.tmp file is left, exit 6, account flat."""
import importlib.util
import io
import os

import pytest

from test_ncv_evidence_interrupt import arm
from tnet_support import ACCOUNT, DIGEST, Store, World

import newcore.tnet.recording as REC
import newcore.venue.tnet as T

REPO = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


def cli():
    sp = importlib.util.spec_from_file_location('cli_ev', os.path.join(REPO, 'tools', 'newcore_tnet_runner.py'))
    mod = importlib.util.module_from_spec(sp)
    sp.loader.exec_module(mod)
    return mod


def files(d):
    return sorted(os.listdir(d)) if os.path.isdir(d) else []


@pytest.mark.parametrize('boundary', ['open', 'write', 'fsync', 'rename'])
@pytest.mark.parametrize('what', ['report', 'detail', 'cassette', 'sidecar'])
def test_ctrl_c_at_an_evidence_write_boundary_still_commits_everything(tmp_path, monkeypatch, what, boundary):
    mod = cli()
    module, nth = {'report': (T, 1), 'detail': (mod, 1), 'cassette': (REC, 2), 'sidecar': (REC, 3)}[what]
    once = arm(monkeypatch, module, boundary, nth=nth)     # REC writes: 1 preflight, 2 cassette, 3 its sidecar
    w = World()
    out = io.StringIO()
    rc = mod.main(['--target', 'testnet', '--account-id', ACCOUNT, '--key-digest', DIGEST, '--run-nonce', 'ev',
                   '--only', 'T10-floor', '--cassette-dir', str(tmp_path / 'c'), '--report-dir', str(tmp_path / 'r')],
                  http=w.fb, sleep=w.sleep, local_clock=w.clock, store=Store(), out=out, monotonic=w.monotonic)
    text = out.getvalue()
    assert once.fired, 'the boundary was reached'
    assert rc == 6, text
    reps, cas = files(tmp_path / 'r'), files(tmp_path / 'c')
    assert [n for n in reps if n.endswith('.scenarios.json')] and [n for n in reps if n.endswith('.md')]
    assert {'tnet-ev-preflight.json', 'tnet-ev-t10-floor.json', 'tnet-ev-t10-floor.meta.json'} <= set(cas)
    assert not [n for n in reps + cas if '.tmp' in n]
    assert w.fb.flat()
