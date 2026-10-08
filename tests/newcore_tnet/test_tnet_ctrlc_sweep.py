"""Cowork 6063794395: Ctrl+C at EVERY request of a runner-target run (boot, preflight, cycles, final truth, teardown,
report) ends typed: exit 6 with the report written, and the account flat with no NEWCORE order (an interrupt inside
the teardown re-runs it once). Interrupts at the filesystem boundaries of the evidence writes themselves are covered by
test_tnet_evidence_interrupt.py."""
import importlib.util
import io
import os

import pytest

from fake_binance import FakeBinance
from tnet_support import ACCOUNT, DIGEST, Store, World

REPO = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


def cli():
    sp = importlib.util.spec_from_file_location('cli_sweep', os.path.join(REPO, 'tools', 'newcore_tnet_runner.py'))
    mod = importlib.util.module_from_spec(sp)
    sp.loader.exec_module(mod)
    return mod


MOD = cli()


class CtrlCAt(FakeBinance):
    def __init__(self, k, **kw):
        super().__init__(**kw)
        self.k = k

    def __call__(self, request):
        if len(self.requests) == self.k:
            self.requests.append(request)
            raise KeyboardInterrupt
        return super().__call__(request)


def run(fb, w, tmp_path, nonce):
    out = io.StringIO()
    rc = MOD.main(['--target', 'testnet', '--account-id', ACCOUNT, '--key-digest', DIGEST, '--run-nonce', nonce,
                   '--cassette-dir', str(tmp_path / 'c'), '--report-dir', str(tmp_path / 'r'), '--only', 'T01-long'],
                  http=fb, sleep=w.sleep, local_clock=w.clock, store=Store(), out=out, monotonic=w.monotonic)
    return rc, out.getvalue()


def _requests():
    import tempfile
    from pathlib import Path
    w = World()
    run(w.fb, w, Path(tempfile.mkdtemp()), 'base')
    return len(w.fb.requests)


N = _requests()


@pytest.mark.parametrize('k', range(N))
def test_ctrl_c_at_every_request_is_exit_6_with_a_report_and_a_flat_account(tmp_path, k):
    w = World()
    w.fb = CtrlCAt(k)
    w.fb.now = w.t
    rc, out = run(w.fb, w, tmp_path, f'sw{k}')
    assert rc == 6, out
    assert any(n.endswith('.json') for n in os.listdir(tmp_path / 'r')), out
    assert w.fb.flat() and not w.fb.open_cids()
    assert 'Traceback' not in out
