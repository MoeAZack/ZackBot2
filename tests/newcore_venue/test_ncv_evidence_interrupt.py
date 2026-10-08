"""Codex P2 (#13, 6064226836): a Ctrl+C at ANY filesystem boundary of the evidence commit (open, write, fsync,
rename) of the report or the cassette: the files are still written complete (SIGINT deferred, one retry from the
in-memory result), no *.tmp file is left, and the run ends exit 6. Fake HTTP, DUMMY keys."""
import builtins
import os

import pytest

from fake_binance import FakeBinance
from test_ncv_tnet_harness import env, run, tool  # noqa: F401  (env is a fixture)

import newcore.venue.cassette as C
import newcore.venue.tnet as T


class Once:
    def __init__(self):
        self.fired = False

    def fire(self):
        if not self.fired:
            self.fired = True
            raise KeyboardInterrupt


def arm(monkeypatch, module, boundary, nth=1):
    """Raise KeyboardInterrupt once at `boundary` of the nth evidence file `module` writes."""
    once, seen = Once(), {'n': 0}
    real_open, real_fsync, real_replace = builtins.open, os.fsync, os.replace

    def is_tmp(p):
        return '.tmp' in str(p)

    def fake_open(path, *a, **k):
        fh = real_open(path, *a, **k)
        if is_tmp(path):
            seen['n'] += 1
            if seen['n'] == nth:
                if boundary == 'open':
                    fh.close()
                    once.fire()
                if boundary == 'write':
                    real_write = fh.write

                    def write(text):
                        once.fire()
                        return real_write(text)
                    fh.write = write
                if boundary in ('fsync', 'rename'):
                    seen['armed'] = boundary
        return fh

    def fake_fsync(fd):
        if seen.get('armed') == 'fsync':
            seen['armed'] = None
            once.fire()
        return real_fsync(fd)

    def fake_replace(src, dst):
        if seen.get('armed') == 'rename' and is_tmp(src):
            seen['armed'] = None
            once.fire()
        return real_replace(src, dst)
    monkeypatch.setattr(module, 'open', fake_open, raising=False)
    monkeypatch.setattr(os, 'fsync', fake_fsync)
    monkeypatch.setattr(os, 'replace', fake_replace)
    return once


def files(d):
    return sorted(os.listdir(d)) if os.path.isdir(d) else []


@pytest.mark.parametrize('boundary', ['open', 'write', 'fsync', 'rename'])
@pytest.mark.parametrize('what', ['report', 'cassette'])
def test_ctrl_c_at_an_evidence_write_boundary_still_commits_everything(env, monkeypatch, what, boundary):  # noqa: F811
    once = arm(monkeypatch, T if what == 'report' else C, boundary)
    fb = FakeBinance()
    rc, out = run(env, ['--probe', 'P1'], http=fb)
    assert once.fired, 'the boundary was reached'
    assert rc == 6 and 'they are complete' in out, out
    reps, cas = files(env['reports']), files(env['cassettes'])
    assert [n for n in reps if n.endswith('.json')] and [n for n in reps if n.endswith('.md')] and cas
    assert not [n for n in reps + cas if '.tmp' in n]
    assert fb.flat()


def test_a_second_interrupt_inside_the_retry_propagates_typed_with_no_tmp(env, monkeypatch):  # noqa: F811
    mod = tool()
    calls = []

    def always(*a, **k):
        calls.append(1)
        raise KeyboardInterrupt
    monkeypatch.setattr(mod, 'tnet_report', always)
    rc, out = run(env, ['--probe', 'P1'], http=FakeBinance(), mod=mod)
    assert rc == 6 and len(calls) == 2 and 'INTERRUPTED' in out
    assert not [n for n in files(env['reports']) if '.tmp' in n]


def test_commit_evidence_defers_a_real_sigint():
    import signal
    flag = T.EvidenceInterrupt()

    def write():
        signal.raise_signal(signal.SIGINT)                        # arrives mid-commit: held, not raised
        return 'done'
    assert T.commit_evidence(write, flag) == 'done' and flag.hit
    assert signal.getsignal(signal.SIGINT) is signal.default_int_handler
