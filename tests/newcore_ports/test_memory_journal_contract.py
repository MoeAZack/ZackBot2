"""The step-0 JournalPort contract suite run unchanged against S1's MemoryJournal (newcore/adapters/memory_journal.py).
Lives here (not in tests/newcore_slice) so it imports the suite the same way the reference journal does."""
import pytest

from journal_contract import JournalContract
from nc_events import ACCT, PF
from newcore.adapters import MemoryJournal


class TestMemoryJournal(JournalContract):
    @pytest.fixture
    def make_journal(self):
        return lambda: MemoryJournal(ACCT, PF)

    @pytest.fixture
    def reopen(self):
        return lambda j: j.reopen()                       # a restart: the gate is rebuilt from the durable events
