"""NC-02a FileJournal against the shared JournalPort contract suite (STEP0_INTERFACE.md r3 section 3), on the real
file system (tmp dirs only).

- `reopen` is a process restart: recover_journal from the files (the old object is left alone, as the suite reuses it).
- `fail_next_write` makes the journal's next durable write fail once. It runs in three flavours, because they leave
  different bytes behind: the write raises before anything lands; the write lands and the fsync raises (a complete
  frame is in the file but not durable); half the frame lands and the write raises (a torn frame). In every flavour the
  journal must seal those bytes off so that the files equal the in-process state and the retry lands exactly once.
"""
import itertools
import os

import pytest

from journal_contract import JournalContract
from nc_events import ACCT, PF
from newcore.store import Verdict, create_journal, recover_journal


class _FailOnce:
    """Seam wrapper: the first `write` / `fsync` (by flavour) raises ENOSPC; everything else passes through."""

    def __init__(self, inner, flavour):
        self.inner, self.flavour, self.armed = inner, flavour, True

    def __getattr__(self, name):
        return getattr(self.inner, name)

    def write(self, h, data):
        if self.armed and self.flavour in ('write', 'partial'):
            self.armed = False
            if self.flavour == 'partial':
                self.inner.write(h, data[:len(data) // 2])
            raise OSError(28, 'injected: no space left on device')
        return self.inner.write(h, data)

    def fsync(self, h):
        if self.armed and self.flavour == 'fsync':
            self.armed = False
            raise OSError(28, 'injected: no space left on device')
        return self.inner.fsync(h)


class _FileJournalContract(JournalContract):
    flavour = 'write'

    @pytest.fixture
    def make_journal(self, tmp_path):
        n = itertools.count()
        os.mkdir(tmp_path / 'accounts')

        def make():
            return create_journal(str(tmp_path / 'accounts' / f'a{next(n)}'), ACCT, PF)
        return make

    @pytest.fixture
    def reopen(self):
        def again(j):
            r = recover_journal(j.account_dir, ACCT, PF)
            assert r.verdict is Verdict.CLEAN, r.findings              # never a torn tail: failures were sealed off
            assert list(r.journal.read()) == list(j.read())
            return r.journal
        return again

    @pytest.fixture
    def fail_next_write(self):
        def arm(j):
            j._fs = _FailOnce(j._fs, self.flavour)
        return arm


class TestFileJournalContractWriteFails(_FileJournalContract):
    flavour = 'write'


class TestFileJournalContractFsyncFails(_FileJournalContract):
    flavour = 'fsync'


class TestFileJournalContractPartialWrite(_FileJournalContract):
    flavour = 'partial'
