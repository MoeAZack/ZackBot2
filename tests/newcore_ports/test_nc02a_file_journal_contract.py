"""NC-02a FileJournal against the shared JournalPort contract suite (STEP0_INTERFACE.md r2 section 3), on the real
file system (tmp dirs only). `reopen` is a process restart: close the handle, recover_journal from the files."""
import itertools
import os

import pytest

from journal_contract import JournalContract
from nc_events import ACCT, PF
from newcore.store import Verdict, create_journal, recover_journal


class TestFileJournalContract(JournalContract):
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
            j.close()
            r = recover_journal(j.account_dir, ACCT, PF)
            assert r.verdict is Verdict.CLEAN, r.findings
            assert list(r.journal.read()) == list(j.read())
            return r.journal
        return again
